"""Atomic native draft boundary, source conflicts and unknown-result recovery."""

import copy
import datetime
import importlib.util
import json
import pathlib
import sys
import types
import unittest
import uuid
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).parents[1] / "jifeng_erp"


class Doc(types.SimpleNamespace):
    def check_permission(self, permission):
        if permission in getattr(self, "denied", ()):
            raise PermissionError("denied")


class Source(dict):
    __getattr__ = dict.__getitem__


class TestWageWorkbench(unittest.TestCase):
    def setUp(self):
        self.persisted = []
        self.deleted = []
        self.savepoint_rows = []
        self.native_inserts = 0
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
        frappe.whitelist = lambda **kwargs: (lambda function: function)
        frappe.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
        frappe.session = types.SimpleNamespace(user="Administrator")
        frappe.has_permission = Mock(return_value=True)
        frappe.PermissionError = PermissionError
        self.supplement = Source(name="SUP-1", employee="E-1", company="C-1", term_type="临时",
                                 wage_method="计件", modified="2026-10-01 08:00:00")
        frappe.db = types.SimpleNamespace(savepoint=Mock(side_effect=self.savepoint),
                                         rollback=Mock(side_effect=self.rollback), commit=Mock(),
                                         sql=Mock(side_effect=self.current_read),
                                         get_value=Mock(return_value=self.supplement))
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        scope = patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("workbench_payroll", ROOT / "payroll.py")
        payroll = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(payroll)
        self.payroll = payroll
        payroll_scope = patch.dict(sys.modules, {"jifeng_erp.payroll": payroll})
        payroll_scope.start()
        self.addCleanup(payroll_scope.stop)
        spec = importlib.util.spec_from_file_location("workbench_piece_workers", ROOT / "piece_workers.py")
        self.piece_workers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.piece_workers)
        piece_scope = patch.dict(sys.modules, {"jifeng_erp.piece_workers": self.piece_workers})
        piece_scope.start()
        self.addCleanup(piece_scope.stop)
        spec = importlib.util.spec_from_file_location("workbench_temporary_workers", ROOT / "temporary_workers.py")
        self.temporary_workers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.temporary_workers)
        temporary_scope = patch.dict(sys.modules, {"jifeng_erp.temporary_workers": self.temporary_workers})
        temporary_scope.start()
        self.addCleanup(temporary_scope.stop)
        spec = importlib.util.spec_from_file_location("wage_workbench_under_test", ROOT / "wage_workbench.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.frappe = frappe
        self.docs = {
            ("Company", "C-1"): Doc(),
            ("Employee", "E-1"): Doc(name="E-1", company="C-1", status="Active", employee_name="合成人员",
                modified="2026-10-01 07:00:00", meta=Doc()),
            ("Salary Component", "PAY"): Doc(type="Earning", disabled=0),
            ("Operation", "OP-1"): Doc(),
            ("UOM", "Nos"): Doc(),
            ("Job Card", "JC-1"): Doc(name="JC-1", company="C-1", docstatus=1, work_order="WO-1",
                operation="OP-1", operation_id="ROW-1", stock_uom="Nos", modified="2026-10-01 09:00:00.000001",
                posting_date="2026-10-01", is_corrective_job_card=0, time_logs=[Doc(employee="E-1")]),
            ("Work Order", "WO-1"): Doc(name="WO-1", company="C-1", docstatus=1, jf_flow_order="FLOW-1",
                modified="2026-10-01 09:00:00.000002", operations=[Doc(name="ROW-1", operation="OP-1")]),
            ("Jifeng Flow Order", "FLOW-1"): Doc(company="C-1"),
        }
        frappe.get_doc = Mock(side_effect=self.get_doc)
        frappe.get_list = Mock(side_effect=lambda *args, **kwargs: [
            Doc(name=doc.name) for doc in self.persisted
            if doc.workbench_request_key.startswith(kwargs["filters"]["workbench_request_key"][1][:-1])])
        frappe.get_all = Mock(side_effect=lambda doctype, **kwargs: self.deleted if doctype == "Deleted Document" else [
            doc.name for doc in self.persisted
            if doc.workbench_request_key.startswith(kwargs["filters"]["workbench_request_key"][1][:-1])])
        self.payload = {"request_id": str(uuid.uuid4()), "company": "C-1", "employee": "E-1",
                        "payroll_date": "2026-10-01", "salary_component": "PAY", "rows": [self.row()]}

    def row(self, job_card="JC-1"):
        return {"work_date": "2026-10-01", "operation": "OP-1", "job_card": job_card,
                "source_modified": "2026-10-01 09:00:00.000001" if job_card else "",
                "work_order_modified": "2026-10-01 09:00:00.000002" if job_card else "",
                "quantity": 2 if job_card else 0, "stock_uom": "Nos" if job_card else "",
                "piece_rate": 3 if job_card else 0, "hours": 1, "hourly_rate": 4,
                "extra_amount": 0, "notes": "合成单元测试"}

    def temporary_payload(self, *, timed=False):
        payload = copy.deepcopy(self.payload)
        if timed:
            payload["rows"] = [self.row(job_card="")]
        payload["temporary_selection"] = {"company": "C-1", "employee": "E-1",
            **self.temporary_workers._versions(self.docs[("Employee", "E-1")], self.supplement, "C-1")}
        return payload

    def test_temporary_selection_creates_native_draft_after_original_source_locks(self):
        payload = self.temporary_payload()
        ordered = Mock()
        ordered.attach_mock(self.frappe.get_doc, "document")
        ordered.attach_mock(self.frappe.db.get_value, "qualification")
        result = self.module.save_drafts(payload)
        self.assertFalse(result["reused"])
        self.assertEqual(self.native_inserts, 1)
        self.assertFalse(hasattr(self.persisted[0], "temporary_selection"), "selection is a new-request predicate, not stored historical classification")
        calls = ordered.mock_calls
        card = next(index for index, call in enumerate(calls) if call[0] == "document"
                    and call.args == ("Job Card", "JC-1") and call.kwargs.get("for_update"))
        work = next(index for index, call in enumerate(calls) if call[0] == "document"
                    and call.args == ("Work Order", "WO-1") and call.kwargs.get("for_update"))
        qualifiers = [index for index, call in enumerate(calls) if call[0] == "qualification" and call.kwargs.get("for_update")]
        self.assertTrue(qualifiers and all(index > card and index > work for index in qualifiers))
        self.frappe.db.commit.assert_not_called()

    def test_temporary_manual_timing_keeps_no_job_card_rule_and_checks_temporary_source(self):
        payload = self.temporary_payload(timed=True)
        self.module.save_drafts(payload)
        self.assertEqual(self.persisted[0].hours, 1)
        self.assertEqual(self.persisted[0].quantity, 0)
        self.assertFalse(getattr(self.persisted[0], "job_card", None))

    def test_changed_temporary_source_rejects_whole_new_batch_before_any_insert(self):
        payload = self.temporary_payload()
        payload["rows"].append(self.row(job_card=""))
        for field, value in (("term_type", "长期"), ("modified", "2026-10-02 08:00:00"),
                             ("name", "SUP-REPLACED"), ("employee", "E-2"), ("company", "C-2")):
            with self.subTest(field=field):
                previous = self.supplement[field]
                self.supplement[field] = value
                with self.assertRaisesRegex(ValueError, "重新查询"):
                    self.module.save_drafts(payload)
                self.assertEqual(self.native_inserts, 0)
                self.assertEqual(self.persisted, [])
                self.supplement[field] = previous
        self.docs[("Employee", "E-1")].modified = "2026-10-02 07:00:00"
        with self.assertRaisesRegex(ValueError, "重新查询"):
            self.module.save_drafts(payload)
        self.assertEqual(self.persisted, [])

    def test_saved_temporary_request_recovers_before_later_eligibility_and_create_changes(self):
        payload = self.temporary_payload()
        first = self.module.save_drafts(payload)
        person = self.docs[("Employee", "E-1")]
        person.status, person.company, person.modified = "Left", "C-2", "2026-10-02 07:00:00"
        self.supplement.update(term_type="长期", modified="2026-10-02 08:00:00")
        self.frappe.has_permission.return_value = False
        self.frappe.db.get_value.reset_mock()
        second = self.module.save_drafts(copy.deepcopy(payload))
        self.assertTrue(second["reused"])
        self.assertEqual(first["wages"], second["wages"])
        self.assertEqual(self.module.batch_status(payload["request_id"], "C-1", "E-1")["wages"], first["wages"])
        self.frappe.db.get_value.assert_not_called()
        self.assertEqual(self.native_inserts, 1)

    def test_selection_removal_or_replacement_cannot_change_saved_request_fingerprint(self):
        payload = self.temporary_payload()
        self.module.save_drafts(payload)
        for change in (lambda p: p.pop("temporary_selection"),
                       lambda p: p["temporary_selection"].update(supplement_version="0" * 64)):
            altered = copy.deepcopy(payload)
            change(altered)
            with self.assertRaisesRegex(ValueError, "内容已变化"):
                self.module.save_drafts(altered)
        self.assertEqual(self.native_inserts, 1)

    def test_invalid_explicit_selection_does_not_fall_back_to_ordinary_wages(self):
        for selection in (None, {}, {"company": "C-1", "employee": "E-1"},
                          {**self.temporary_payload()["temporary_selection"], "employee": "E-2"}):
            payload = self.temporary_payload(timed=True)
            payload["temporary_selection"] = selection
            with self.assertRaisesRegex(ValueError, "重新查询"):
                self.module.save_drafts(payload)
        self.assertFalse(self.persisted)
        self.assertEqual(self.native_inserts, 0)

    def test_history_reference_pair_preserves_exact_request_and_changes_reject_reuse(self):
        self.payload["rows"][0].update(piece_rate_reference="OLD-1",
                                      piece_rate_reference_version="2026-09-01 09:00:00.000001")
        result = self.module.save_drafts(self.payload)
        self.assertEqual(self.persisted[0].piece_rate_reference, "OLD-1")
        self.assertEqual(self.persisted[0].piece_rate_reference_version, "2026-09-01 09:00:00.000001")
        self.assertEqual(self.module.save_drafts(copy.deepcopy(self.payload))["wages"], result["wages"])
        self.payload["rows"][0]["piece_rate_reference_version"] = "2026-09-02 09:00:00"
        with self.assertRaisesRegex(ValueError, "内容已变化"):
            self.module.save_drafts(self.payload)

    def test_history_reference_payload_rejects_missing_half_or_unknown_fields(self):
        for values in ({"piece_rate_reference": "OLD-1"},
                       {"piece_rate_reference_version": "2026-09-01 09:00:00"},
                       {"piece_rate_reference": "OLD-1", "piece_rate_reference_version": "2026-09-01 09:00:00", "default_rate": 1}):
            payload = copy.deepcopy(self.payload)
            payload["rows"][0].update(values)
            with self.assertRaisesRegex(ValueError, "完整填写"):
                self.module.save_drafts(payload)
        self.assertFalse(self.persisted)

    def test_new_amendment_rejects_fresh_quote_then_clears_exact_original_copy(self):
        for doctype in ("Jifeng Wage Entry", "Jifeng Wage Allocation"):
            source = Doc(doctype=doctype, name="OLD", docstatus=2,
                         workbench_request_key=None, workbench_request_fingerprint=None,
                         employee="E-1", piece_rate=3, piece_rate_reference="H1",
                         piece_rate_reference_version="2026-09-01 09:00:00", wage_entry=None)
            source.employees = [Doc(**{field: getattr(source, field) for field in
                ("employee", "piece_rate", "piece_rate_reference", "piece_rate_reference_version", "wage_entry")})]
            self.frappe.get_doc = Mock(return_value=source)
            new = Doc(doctype=doctype, amended_from="OLD", flags=Doc(),
                      workbench_request_key=None, workbench_request_fingerprint=None,
                      piece_rate=3, piece_rate_reference="H2", piece_rate_reference_version="2026-09-02 09:00:00")
            new.employees = [Doc(employee="E-1", wage_entry=None, piece_rate=3,
                piece_rate_reference="H2", piece_rate_reference_version="2026-09-02 09:00:00")]
            action = self.module.prepare_wage_amendment if doctype == "Jifeng Wage Entry" else self.module.prepare_allocation_amendment
            with self.assertRaisesRegex(ValueError, "先保存修改草稿"):
                action(new)
            target = new if doctype == "Jifeng Wage Entry" else new.employees[0]
            target.piece_rate_reference, target.piece_rate_reference_version = source.piece_rate_reference, source.piece_rate_reference_version
            action(new)
            self.assertIsNone(target.piece_rate_reference)
            self.assertIsNone(target.piece_rate_reference_version)
            self.assertEqual(target.piece_rate, 3)
            self.assertEqual(source.piece_rate_reference, "H1")

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            doc = Doc(**doctype, flags=types.SimpleNamespace(), docstatus=0)
            if hasattr(self, "wage_meta"):
                doc.meta = self.wage_meta
            doc.get_doc_before_save = lambda: None
            doc.insert = lambda: self.insert(doc)
            return doc
        if doctype == "Jifeng Wage Entry":
            return next(doc for doc in self.persisted if doc.name == name)
        return self.docs[(doctype, name)]

    def savepoint(self, point):
        self.savepoint_rows = list(self.persisted)

    def current_read(self, query, values, **kwargs):
        if "tabDeleted Document" in query:
            return self.deleted
        return [Doc(name=doc.name) for doc in self.persisted if doc.workbench_request_key.startswith(values[0][:-1])]

    def rollback(self, **kwargs):
        self.persisted[:] = self.savepoint_rows

    def insert(self, doc):
        self.native_inserts += 1
        self.module.guard_request_identity(doc)
        if self.native_inserts == getattr(self, "fail_at", -1):
            raise ValueError("native controller rejects last row")
        doc.name = "W-" + str(self.native_inserts)
        self.persisted.append(doc)
        return doc

    def test_explicit_source_returns_version_and_checks_membership(self):
        source = self.module.get_entry_source("JC-1", "C-1", "E-1")
        self.assertEqual(source["source_modified"], self.row()["source_modified"])
        self.assertEqual(source["operation"], "OP-1")
        self.docs[("Job Card", "JC-1")].time_logs = [Doc(employee="E-2")]
        with self.assertRaisesRegex(ValueError, "没有这位员工"):
            self.module.get_entry_source("JC-1", "C-1", "E-1")

    def test_sources_must_be_readable_and_current_company_submitted(self):
        card = self.docs[("Job Card", "JC-1")]
        card.denied = ["read"]
        with self.assertRaises(PermissionError):
            self.module.save_drafts(self.payload)
        card.denied = []
        for field, value in (("company", "C-2"), ("docstatus", 0)):
            before = getattr(card, field)
            setattr(card, field, value)
            with self.assertRaisesRegex(ValueError, "已提交"):
                self.module.save_drafts(self.payload)
            setattr(card, field, before)
        self.assertEqual(self.persisted, [])

    def test_native_insert_and_request_identity_are_used_without_commit(self):
        result = self.module.save_drafts(self.payload)
        self.assertEqual(self.native_inserts, 1)
        self.assertFalse(result["reused"])
        self.assertEqual(self.persisted[0].flow_order, "FLOW-1")
        self.assertEqual(self.persisted[0].job_card, "JC-1")
        self.frappe.db.commit.assert_not_called()

    def test_last_row_failure_rolls_back_first_native_insert(self):
        self.payload["rows"].append(self.row(job_card=""))
        self.fail_at = 2
        with self.assertRaisesRegex(ValueError, "last row"):
            self.module.save_drafts(self.payload)
        self.assertEqual(self.persisted, [])
        self.frappe.db.rollback.assert_called_once_with(save_point="jifeng_wage_workbench")

    def test_exact_repeated_post_returns_same_drafts_without_insert(self):
        first = self.module.save_drafts(self.payload)
        second = self.module.save_drafts(copy.deepcopy(self.payload))
        self.assertTrue(second["reused"])
        self.assertEqual(first["wages"], second["wages"])
        self.assertEqual(self.native_inserts, 1)

    def test_retry_cannot_replace_payload_or_create_after_original_cancel(self):
        self.module.save_drafts(self.payload)
        self.payload["rows"][0]["piece_rate"] = 9
        with self.assertRaisesRegex(ValueError, "内容已变化"):
            self.module.save_drafts(self.payload)
        self.payload["rows"][0]["piece_rate"] = 3
        self.persisted[0].docstatus = 2
        result = self.module.save_drafts(self.payload)
        self.assertEqual(result["wages"][0]["docstatus"], 2)
        self.assertEqual(self.native_inserts, 1)

    def test_unknown_result_check_locks_employee_before_reading_batch(self):
        self.module.save_drafts(self.payload)
        self.frappe.get_doc.reset_mock()
        status = self.module.batch_status(self.payload["request_id"], "C-1", "E-1")
        self.assertEqual(len(status["wages"]), 1)
        self.frappe.get_doc.assert_any_call("Employee", "E-1", for_update=True)
        self.assertTrue(all("FOR UPDATE" in call.args[0] for call in self.frappe.db.sql.call_args_list))

    def test_stale_card_or_work_order_rejected_before_inserts(self):
        for doctype in ("Job Card", "Work Order"):
            doc = self.docs[(doctype, "JC-1" if doctype == "Job Card" else "WO-1")]
            before = doc.modified
            doc.modified = before + "x"
            with self.assertRaisesRegex(ValueError, "来源已变化"):
                self.module.save_drafts(self.payload)
            doc.modified = before
        self.assertEqual(self.native_inserts, 0)

    def test_manual_timing_accepts_explicit_rates_without_job_card(self):
        self.payload["rows"] = [self.row(job_card="")]
        self.module.save_drafts(self.payload)
        self.assertEqual(self.persisted[0].hours, 1)
        self.assertFalse(getattr(self.persisted[0], "job_card", None))

    def manual_piece_payload(self):
        payload = copy.deepcopy(self.payload)
        row = self.row(job_card="")
        row.update(quantity=2.5, piece_rate=4, stock_uom="Nos", hours=0, hourly_rate=0)
        payload["rows"] = [row]
        return payload

    def test_ordinary_manager_can_save_manual_piece_with_explicit_unit_and_no_production_source(self):
        self.frappe.session.user = "HR-MANAGER"
        permitted = (*self.module.EDIT_FIELDS, "job_card", "work_order", "flow_order",
                     "type", "disabled", "status", "employee_name",
                     "workbench_request_key", "workbench_request_fingerprint")
        self.wage_meta = Doc(get_permitted_fieldnames=Mock(return_value=permitted))
        for doc in self.docs.values():
            doc.meta = self.wage_meta
        unit = self.docs[("UOM", "Nos")]
        unit.check_permission = Mock()
        payload = self.manual_piece_payload()
        first = self.module.save_drafts(payload)
        wage = self.persisted[0]
        self.assertFalse(first["reused"])
        self.assertEqual((wage.quantity, wage.piece_rate, wage.stock_uom), (2.5, 4, "Nos"))
        self.assertEqual(self.payroll.wage_amounts(wage.quantity, wage.piece_rate, wage.hours,
                         wage.hourly_rate, wage.extra_amount)[2], 10)
        self.assertTrue(all(not getattr(wage, field, None) for field in ("job_card", "work_order", "flow_order")))
        self.assertFalse(any(call.args[0] in ("Job Card", "Work Order", "Jifeng Flow Order")
                             for call in self.frappe.get_doc.call_args_list if isinstance(call.args[0], str)))
        unit.check_permission.assert_called_once_with("select")
        self.frappe.db.get_value.assert_any_call(self.piece_workers.SUPPLEMENT,
            {"company": "C-1", "employee": "E-1"}, ["name", "wage_method", "modified"],
            as_dict=True, for_update=True)
        recovered = self.module.batch_status(payload["request_id"], "C-1", "E-1")
        repeated = self.module.save_drafts(copy.deepcopy(payload))
        self.assertEqual(recovered["wages"], first["wages"])
        self.assertTrue(repeated["reused"])
        self.assertEqual(self.native_inserts, 1)
        self.frappe.db.commit.assert_not_called()

    def test_manual_piece_requires_unit_selection_permission_and_piece_worker(self):
        payload = self.manual_piece_payload()
        payload["rows"][0]["stock_uom"] = ""
        with self.assertRaisesRegex(ValueError, "手工计件.*单位"):
            self.module.save_drafts(payload)
        payload["rows"][0]["stock_uom"] = "Nos"
        self.docs[("UOM", "Nos")].denied = ("select",)
        with self.assertRaises(PermissionError):
            self.module.save_drafts(payload)
        self.docs[("UOM", "Nos")].denied = ()
        self.supplement["wage_method"] = "计时"
        with self.assertRaisesRegex(ValueError, "工资方式必须为计件"):
            self.module.save_drafts(payload)
        self.assertEqual(self.native_inserts, 0)

    def test_manual_piece_cannot_claim_source_versions_or_bypass_native_create_fields(self):
        payload = self.manual_piece_payload()
        for field in ("source_modified", "work_order_modified"):
            payload["rows"][0][field] = "2026-10-01 09:00:00"
            with self.assertRaisesRegex(ValueError, "不能携带来源核对信息"):
                self.module.save_drafts(payload)
            payload["rows"][0][field] = ""
        self.frappe.has_permission.return_value = False
        with self.assertRaisesRegex(ValueError, "没有新增"):
            self.module.save_drafts(payload)
        self.frappe.has_permission.return_value = True
        self.frappe.session.user = "HR-MANAGER"
        self.wage_meta = Doc(get_permitted_fieldnames=Mock(return_value=["company", "status", "employee_name", "type", "disabled"]))
        for doc in self.docs.values():
            doc.meta = self.wage_meta
        with self.assertRaisesRegex(ValueError, "所需字段"):
            self.module.save_drafts(payload)
        self.assertEqual(self.native_inserts, 0)

    def test_mixed_manual_and_job_card_batch_still_rolls_back_on_last_controller_failure(self):
        self.payload["rows"].extend(self.manual_piece_payload()["rows"])
        self.fail_at = 2
        with self.assertRaisesRegex(ValueError, "last row"):
            self.module.save_drafts(self.payload)
        self.assertEqual(self.persisted, [])
        self.frappe.db.rollback.assert_called_once_with(save_point="jifeng_wage_workbench")

    def test_duplicate_source_and_foreign_operation_or_unit_are_rejected(self):
        self.payload["rows"].append(self.row())
        with self.assertRaisesRegex(ValueError, "只能填写一次"):
            self.module.save_drafts(self.payload)
        self.payload["rows"].pop()
        self.payload["rows"][0]["stock_uom"] = "Kg"
        with self.assertRaisesRegex(ValueError, "计件单位"):
            self.module.save_drafts(self.payload)

    def test_company_selection_scope_suffices_without_company_private_read(self):
        self.docs[("Company", "C-1")].denied = ("read",)
        source = self.module.get_entry_source("JC-1", "C-1", "E-1")
        self.assertEqual(source["job_card"], "JC-1")
        self.assertTrue(self.module.save_drafts(self.payload)["wages"])
        self.docs[("Company", "C-1")].denied = ("read", "select")
        with self.assertRaises(PermissionError):
            self.module.get_entry_source("JC-1", "C-1", "E-1")

    def test_employee_must_be_active_company_scoped(self):
        employee = self.docs[("Employee", "E-1")]
        for field, value in (("company", "C-2"), ("status", "Left")):
            before = getattr(employee, field)
            setattr(employee, field, value)
            with self.assertRaisesRegex(ValueError, "在职员工"):
                self.module.save_drafts(self.payload)
            setattr(employee, field, before)

    def test_native_create_permission_is_checked_for_endpoint_and_each_row(self):
        self.frappe.has_permission.return_value = False
        with self.assertRaisesRegex(ValueError, "没有新增"):
            self.module.save_drafts(self.payload)
        self.frappe.has_permission.return_value = True
        original = self.get_doc
        def denied_doc(doctype, name=None, **kwargs):
            doc = original(doctype, name, **kwargs)
            if isinstance(doctype, dict):
                doc.denied = ["create"]
            return doc
        self.frappe.get_doc.side_effect = denied_doc
        with self.assertRaises(PermissionError):
            self.module.save_drafts(self.payload)
        self.assertEqual(self.native_inserts, 0)

    def test_field_level_permissions_are_not_bypassed_by_workbench(self):
        self.frappe.session.user = "HR-TEST"
        for doc in self.docs.values():
            doc.meta = types.SimpleNamespace(get_permitted_fieldnames=lambda **kwargs: [])
        with self.assertRaisesRegex(ValueError, "所需字段"):
            self.module.get_entry_source("JC-1", "C-1", "E-1")

    def source_field_permissions(self):
        self.frappe.session.user = "HR-TEST"
        self.frappe.share = Doc(get_shared=Mock(return_value=[]))
        columns = {"Employee": ("company", "status", "employee_name"),
                   "Job Card": self.module.SOURCE_FIELDS,
                   "Work Order": ("company", "jf_flow_order"),
                   "Jifeng Flow Order": ("company",)}
        for (doctype, _), doc in self.docs.items():
            doc.doctype = doctype
            tables = {name: Doc(fieldtype="Table", options=child, permlevel=0)
                      for (parent, name), child in self.module.SOURCE_TABLES.items() if parent == doctype}
            doc.meta = Doc(get_permitted_fieldnames=Mock(return_value=columns.get(doctype, ())),
                           get_permlevel_access=Mock(return_value=[0]),
                           get_field=Mock(side_effect=tables.get))
        children = {"Job Card Time Log": Doc(get_permitted_fieldnames=Mock(return_value=["employee"])),
                    "Work Order Operation": Doc(get_permitted_fieldnames=Mock(return_value=["operation"]))}
        self.frappe.get_meta = Mock(side_effect=children.__getitem__)

    def test_source_tables_do_not_need_to_appear_in_native_permitted_value_fields(self):
        self.source_field_permissions()
        source = self.module.get_entry_source("JC-1", "C-1", "E-1")
        self.assertEqual(source["stock_uom"], "Nos")
        self.assertNotIn("time_logs", self.module.SOURCE_FIELDS)
        self.frappe.get_meta.assert_any_call("Job Card Time Log")
        self.frappe.get_meta.assert_any_call("Work Order Operation")

    def test_parent_source_table_permission_level_is_required(self):
        self.source_field_permissions()
        for doctype, name, table in (("Job Card", "JC-1", "time_logs"), ("Work Order", "WO-1", "operations")):
            field = self.docs[(doctype, name)].meta.get_field(table)
            field.permlevel = 1
            with self.assertRaisesRegex(ValueError, "来源明细"):
                self.module.get_entry_source("JC-1", "C-1", "E-1")
            field.permlevel = 0

    def test_native_read_share_retains_level_zero_source_table_access(self):
        self.source_field_permissions()
        self.docs[("Job Card", "JC-1")].meta.get_permlevel_access.return_value = []
        self.frappe.share.get_shared.return_value = [Doc(name="JC-1")]
        self.module.get_entry_source("JC-1", "C-1", "E-1")
        self.frappe.share.get_shared.assert_called_once_with("Job Card", "HR-TEST", rights=["read"], limit=1)

    def test_native_amendment_requires_amend_source_read_and_cancelled_source(self):
        for doctype in ("Jifeng Wage Entry", "Jifeng Wage Allocation"):
            source = Doc(doctype=doctype, name="OLD", docstatus=2)
            self.frappe.get_doc = Mock(return_value=source)
            new = Doc(doctype=doctype, amended_from="OLD", denied=["amend"])
            with self.assertRaises(PermissionError):
                self.module.authorize_native_amendment(new)
            self.frappe.get_doc.assert_not_called()
            new.denied = []
            source.denied = ["read"]
            with self.assertRaises(PermissionError):
                self.module.authorize_native_amendment(new)
            source.denied = []
            source.docstatus = 1
            with self.assertRaisesRegex(ValueError, "已取消"):
                self.module.authorize_native_amendment(new)
            source.docstatus = 2
            self.assertIs(self.module.authorize_native_amendment(new), source)
            self.frappe.get_doc.assert_called_with(doctype, "OLD")

    def test_amendment_source_must_be_an_exact_string_identity(self):
        self.frappe.get_doc.reset_mock()
        for value in (False, 123, {"name": "OLD"}, ["OLD"], " OLD", "OLD "):
            with self.assertRaisesRegex(ValueError, "准确的原单编号"):
                self.module.authorize_native_amendment(Doc(doctype="Jifeng Wage Entry", amended_from=value))
        self.frappe.get_doc.assert_not_called()

    def test_native_wage_amendment_clears_only_exact_copied_request(self):
        self.module.save_drafts(self.payload)
        source = self.persisted[0]
        source.docstatus = 2
        approved_classification = {
            "personnel_type_at_approval": "临时", "personnel_type_snapshot_status": "已核实",
            "personnel_type_snapshot_time": "2026-10-07 10:00:00.000001",
            "personnel_type_snapshot_source": "a" * 64, "personnel_type_snapshot_version": "b" * 64,
        }
        for field, value in approved_classification.items():
            setattr(source, field, value)
        new = Doc(doctype=source.doctype, amended_from=source.name, flags=Doc(),
                  workbench_request_key=source.workbench_request_key,
                  workbench_request_fingerprint=source.workbench_request_fingerprint,
                  **approved_classification)
        original_key = source.workbench_request_key
        new.workbench_request_key = "forged"
        with self.assertRaisesRegex(ValueError, "来自原已取消单据"):
            self.module.prepare_wage_amendment(new)
        new.workbench_request_key = original_key
        self.module.authorize_native_restore(new)
        self.module.prepare_wage_amendment(new)
        self.module.guard_request_identity(new)
        self.assertIsNone(new.workbench_request_key)
        self.assertIsNone(new.workbench_request_fingerprint)
        self.assertEqual(source.workbench_request_key, original_key)
        for field, value in approved_classification.items():
            self.assertIsNone(getattr(new, field))
            self.assertEqual(getattr(source, field), value)

    def test_native_restore_token_preserves_an_amended_document_request(self):
        self.module.save_drafts(self.payload)
        source = self.persisted[0]
        source.docstatus = 2
        restored = Doc(doctype=source.doctype, name="RESTORED-AMENDMENT", amended_from=source.name,
                       flags=Doc(from_restore=True), workbench_request_key=str(uuid.uuid4()) + ":000",
                       workbench_request_fingerprint="native-restored-fingerprint")
        fields = ("name", *self.module.EDIT_FIELDS, "job_card", "work_order", "flow_order",
                  "workbench_request_key", "workbench_request_fingerprint")
        snapshot = {field: getattr(restored, field, None) for field in fields}
        snapshot["doctype"] = restored.doctype
        self.deleted = [Doc(data=json.dumps(snapshot))]
        self.module.authorize_native_restore(restored)
        self.module.prepare_wage_amendment(restored)
        self.module.guard_request_identity(restored)
        self.assertEqual(restored.workbench_request_key, snapshot["workbench_request_key"])

    def test_exact_personnel_restore_of_an_ordinary_amended_wage_preserves_original_snapshot(self):
        restored = Doc(doctype="Jifeng Wage Entry", name="RESTORED-ORDINARY-AMENDMENT", amended_from="OLD",
                       flags=Doc(personnel_classification_restore_token=self.module.PERSONNEL_RESTORE_TOKEN),
                       personnel_type_at_approval="临时", personnel_type_snapshot_status="已核实",
                       personnel_type_snapshot_time="2026-10-07 10:00:00.000001",
                       personnel_type_snapshot_source="a" * 64, personnel_type_snapshot_version="b" * 64)
        original = {field: getattr(restored, field) for field in self.module.PERSONNEL_SNAPSHOT_FIELDS}
        self.frappe.get_doc.reset_mock()
        self.module.prepare_wage_amendment(restored)
        self.assertEqual({field: getattr(restored, field) for field in self.module.PERSONNEL_SNAPSHOT_FIELDS}, original)
        self.frappe.get_doc.assert_not_called()

    def test_native_workbench_restore_compares_snapshot_datetime_without_false_format_conflict(self):
        self.module.save_drafts(self.payload)
        saved = self.persisted[0]
        saved.personnel_type_snapshot_time = datetime.datetime(2026, 10, 7, 10, 0, 0, 1)
        fields = ("name", *self.module.EDIT_FIELDS, "job_card", "work_order", "flow_order",
                  *self.module.PERSONNEL_SNAPSHOT_FIELDS, "workbench_request_key", "workbench_request_fingerprint")
        snapshot = {field: getattr(saved, field, None) for field in fields}
        snapshot["personnel_type_snapshot_time"] = "2026-10-07 10:00:00.000001"
        snapshot["doctype"] = "Jifeng Wage Entry"
        self.deleted = [Doc(data=json.dumps(snapshot))]
        saved.flags = Doc(from_restore=True)
        self.module.authorize_native_restore(saved)
        self.module.guard_request_identity(saved)
        saved.flags = Doc(from_restore=True)
        saved.personnel_type_snapshot_time += datetime.timedelta(microseconds=1)
        with self.assertRaisesRegex(ValueError, "原删除单据"):
            self.module.authorize_native_restore(saved)

    def test_native_allocation_amendment_clears_new_refs_and_preserves_old_history(self):
        source = Doc(doctype="Jifeng Wage Allocation", name="OLD", docstatus=2,
                     employees=[Doc(employee="E-1", wage_entry="OLD-WAGE")])
        self.frappe.get_doc = Mock(return_value=source)
        new = Doc(doctype=source.doctype, amended_from=source.name, flags=Doc(),
                  employees=[Doc(employee="E-1", wage_entry="FOREIGN-WAGE")])
        with self.assertRaisesRegex(ValueError, "来自原已取消单据"):
            self.module.prepare_allocation_amendment(new)
        new.employees[0].wage_entry = "OLD-WAGE"
        self.module.prepare_allocation_amendment(new)
        self.assertIsNone(new.employees[0].wage_entry)
        self.assertEqual(source.employees[0].wage_entry, "OLD-WAGE")

    def test_hidden_saved_rows_never_become_an_absent_batch(self):
        self.module.save_drafts(self.payload)
        self.frappe.get_list.side_effect = lambda *args, **kwargs: []
        with self.assertRaisesRegex(ValueError, "完整读取"):
            self.module.batch_status(self.payload["request_id"], "C-1", "E-1")
        with self.assertRaisesRegex(ValueError, "完整读取"):
            self.module.save_drafts(self.payload)
        self.assertEqual(self.native_inserts, 1)

    def test_deleted_draft_never_becomes_an_absent_request_or_new_insert(self):
        self.module.save_drafts(self.payload)
        saved = self.persisted.pop()
        self.deleted = [Doc(data=json.dumps({"doctype": "Jifeng Wage Entry",
            "workbench_request_key": saved.workbench_request_key}))]
        with self.assertRaisesRegex(ValueError, "已删除"):
            self.module.batch_status(self.payload["request_id"], "C-1", "E-1")
        with self.assertRaisesRegex(ValueError, "已删除"):
            self.module.save_drafts(self.payload)
        self.assertEqual(self.native_inserts, 1)

    def test_restored_live_draft_resolves_tombstone_without_new_insert(self):
        self.module.save_drafts(self.payload)
        saved = self.persisted[0]
        self.deleted = [Doc(data=json.dumps({"doctype": "Jifeng Wage Entry",
            "workbench_request_key": saved.workbench_request_key}))]
        self.assertTrue(self.module.save_drafts(self.payload)["reused"])
        self.assertEqual(self.native_inserts, 1)

    def test_restore_guard_requires_system_manager_and_exact_native_snapshot(self):
        self.module.save_drafts(self.payload)
        saved = self.persisted[0]
        fields = ("name", *self.module.EDIT_FIELDS, "job_card", "work_order", "flow_order",
                  "workbench_request_key", "workbench_request_fingerprint")
        snapshot = {field: getattr(saved, field, None) for field in fields}
        snapshot["doctype"] = "Jifeng Wage Entry"
        self.deleted = [Doc(data=json.dumps(snapshot))]
        saved.flags = Doc(from_restore=True)
        self.module.authorize_native_restore(saved)
        saved.name = "NATIVE-RENAMED"
        self.module.guard_request_identity(saved)
        saved.name = snapshot["name"]
        saved.flags = Doc(from_restore=True)
        saved.notes = "tampered"
        with self.assertRaisesRegex(ValueError, "原删除单据"):
            self.module.authorize_native_restore(saved)
        saved.notes = snapshot["notes"]
        saved.name = "MANUALLY-NEW-NAME"
        with self.assertRaisesRegex(ValueError, "原删除单据"):
            self.module.authorize_native_restore(saved)
        saved.name = snapshot["name"]
        saved.personnel_type_at_approval = "临时"
        with self.assertRaisesRegex(ValueError, "原删除单据"):
            self.module.authorize_native_restore(saved)
        saved.personnel_type_at_approval = None
        self.frappe.session.user = "HR-ACTOR"
        self.frappe.get_roles = Mock(return_value=["HR Manager"])
        with self.assertRaisesRegex(ValueError, "恢复权限"):
            self.module.authorize_native_restore(saved)

    def test_child_employee_and_work_operation_field_access_is_required(self):
        self.frappe.session.user = "HR-TEST"
        parent = Doc(doctype="Job Card", meta=Doc(
            get_field=lambda field: Doc(fieldtype="Table", permlevel=0,
                                       options=self.module.SOURCE_TABLES[(parent.doctype, field)]),
            get_permlevel_access=lambda **kwargs: [0]))
        self.frappe.get_meta = Mock(return_value=Doc(get_permitted_fieldnames=lambda **kwargs: []))
        with self.assertRaisesRegex(ValueError, "来源明细"):
            self.module._child_fields(parent, "time_logs", ("employee",))
        parent.doctype = "Work Order"
        with self.assertRaisesRegex(ValueError, "来源明细"):
            self.module._child_fields(parent, "operations", ("operation",))

    def test_corrective_read_source_is_rejected_before_draft_insert(self):
        self.docs[("Job Card", "JC-1")].is_corrective_job_card = 1
        with self.assertRaisesRegex(ValueError, "纠正报工"):
            self.module.get_entry_source("JC-1", "C-1", "E-1")
        self.assertEqual(self.native_inserts, 0)

    def test_request_guard_rejects_forged_token_and_identity_replacement(self):
        doc = Doc(workbench_request_key="forged", workbench_request_fingerprint="fake",
                  flags=types.SimpleNamespace(workbench_request_token="pretend"), get_doc_before_save=lambda: None)
        with self.assertRaisesRegex(ValueError, "正式工资操作台"):
            self.module.guard_request_identity(doc)
        self.module.save_drafts(self.payload)
        saved = self.persisted[0]
        before = copy.copy(saved)
        saved.get_doc_before_save = lambda: before
        saved.workbench_request_key = ""
        with self.assertRaisesRegex(ValueError, "不能移除"):
            self.module.guard_request_identity(saved)

    def test_unsupported_fields_limits_and_request_ids_are_rejected(self):
        for change in (lambda payload: payload.update(ignore_permissions=True),
                       lambda payload: payload.update(request_id="not-a-uuid"),
                       lambda payload: payload.update(rows=[]),
                       lambda payload: payload["rows"][0].update(wage_allocation="A-1")):
            payload = copy.deepcopy(self.payload)
            change(payload)
            with self.assertRaises(ValueError):
                self.module.save_drafts(payload)
        self.assertEqual(self.native_inserts, 0)


if __name__ == "__main__":
    unittest.main()
