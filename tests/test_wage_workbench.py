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
        frappe.db = types.SimpleNamespace(savepoint=Mock(side_effect=self.savepoint),
                                         rollback=Mock(side_effect=self.rollback), commit=Mock(),
                                         sql=Mock(side_effect=self.current_read))
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        scope = patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("workbench_payroll", ROOT / "payroll.py")
        payroll = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(payroll)
        payroll_scope = patch.dict(sys.modules, {"jifeng_erp.payroll": payroll})
        payroll_scope.start()
        self.addCleanup(payroll_scope.stop)
        spec = importlib.util.spec_from_file_location("wage_workbench_under_test", ROOT / "wage_workbench.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.frappe = frappe
        self.docs = {
            ("Company", "C-1"): Doc(),
            ("Employee", "E-1"): Doc(company="C-1", status="Active", employee_name="合成人员"),
            ("Salary Component", "PAY"): Doc(type="Earning", disabled=0),
            ("Operation", "OP-1"): Doc(),
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

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            doc = Doc(**doctype, flags=types.SimpleNamespace(), docstatus=0)
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

    def test_manual_timing_accepts_explicit_rates_but_no_manual_piece_source(self):
        self.payload["rows"] = [self.row(job_card="")]
        self.module.save_drafts(self.payload)
        self.assertEqual(self.persisted[0].hours, 1)
        self.payload["request_id"] = str(uuid.uuid4())
        self.payload["rows"][0]["quantity"] = 1
        with self.assertRaisesRegex(ValueError, "计件录入必须"):
            self.module.save_drafts(self.payload)

    def test_duplicate_source_and_foreign_operation_or_unit_are_rejected(self):
        self.payload["rows"].append(self.row())
        with self.assertRaisesRegex(ValueError, "只能填写一次"):
            self.module.save_drafts(self.payload)
        self.payload["rows"].pop()
        self.payload["rows"][0]["stock_uom"] = "Kg"
        with self.assertRaisesRegex(ValueError, "计件单位"):
            self.module.save_drafts(self.payload)

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
