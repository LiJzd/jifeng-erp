"""Private fixture identity, native reversal changes and unknown commit recovery."""

import datetime
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).parents[1] / "jifeng_erp"


class Doc(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    @property
    def meta(self):
        return types.SimpleNamespace(has_field=lambda field: field in self)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWageBrowserValidation(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = pathlib.Path(directory.name)
        self.frappe = types.ModuleType("frappe")
        self.frappe.get_site_path = Mock(return_value=str(self.directory / "private" / "jifeng_qa"))
        self.frappe.only_for = Mock()
        self.frappe.whitelist = lambda: lambda function: function
        self.frappe.throw = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
        self.frappe.session = types.SimpleNamespace(user="Administrator")
        self.frappe.db = types.SimpleNamespace(_disable_transaction_control=1, commit=Mock())
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.get_datetime = lambda value: datetime.datetime.fromisoformat(str(value))
        utils.get_first_day = Mock()
        utils.now = lambda: "2026-09-30 12:00:00"
        utils.today = lambda: "2026-09-30"
        diagnostics = types.ModuleType("jifeng_erp.diagnostics")
        diagnostics._begin_rollback_only = Mock()
        diagnostics._end_rollback_only = Mock()
        inventory = types.ModuleType("jifeng_erp.inventory_validation")
        inventory._ledger_snapshot = Mock(return_value={"Stock Ledger Entry": "stock", "GL Entry": "gl"})
        inventory_browser = types.ModuleType("jifeng_erp.inventory_browser_validation")
        inventory_browser.ZERO_BIN_FIELDS = ("actual_qty", "planned_qty", "projected_qty")
        delete_doc = types.ModuleType("frappe.model.delete_doc")
        delete_doc.get_linked_docs = Mock(return_value=[])
        delete_doc.get_dynamic_linked_docs = Mock(return_value=[])
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
            "jifeng_erp.diagnostics": diagnostics, "jifeng_erp.inventory_validation": inventory,
            "jifeng_erp.inventory_browser_validation": inventory_browser, "frappe.model.delete_doc": delete_doc})
        scope.start()
        self.addCleanup(scope.stop)
        payroll = load_module("browser_test_payroll", ROOT / "payroll.py")
        payroll_scope = patch.dict(sys.modules, {"jifeng_erp.payroll": payroll})
        payroll_scope.start()
        self.addCleanup(payroll_scope.stop)
        allocation = load_module("browser_test_allocation", ROOT / "wage_allocation.py")
        allocation_scope = patch.dict(sys.modules, {"jifeng_erp.wage_allocation": allocation})
        allocation_scope.start()
        self.addCleanup(allocation_scope.stop)
        self.module = load_module("wage_browser_validation_under_test", ROOT / "wage_browser_validation.py")
        self.links = delete_doc
        self.ledger = inventory._ledger_snapshot
        self.docs = {}
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.docs[(doctype, name)])
        self.frappe.db.exists = Mock(side_effect=lambda doctype, name: (doctype, name) in self.docs)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        prefix = "JF-WAGE-BROWSER-012345abcdef"
        self.data = {"fixture_type": "wage_browser", "fixture_version": 1, "prefix": prefix,
            "company": "QA", "item_group": "Products", "stock_uom": "Nos", "warehouse": "Shared - QA",
            "created_at": "2026-09-30 08:00:00", "created_by": "Administrator", "creator_roles": ["System Manager"],
            "browser_user": "Administrator", "browser_roles": ["System Manager"], "ready_at": "2026-09-30 08:30:00",
            "phase": "ready", "cleanup_complete": False, "settings_restored": True, "records": [], "bin_records": [],
            "deleted_document_log": [], "ui_stage_log": [], "payroll_date": "2026-09-30",
            "ledger_before": self.ledger(), **{kind: [] for kind in self.module.KINDS}}
        sources = [
            self.doc("Employee", "E-1", first_name=prefix + "-E1", company="QA", status="Active"),
            self.doc("Employee", "E-2", first_name=prefix + "-E2", company="QA", status="Active"),
            self.doc("Salary Component", prefix + "-PIECE", salary_component=prefix + "-PIECE", disabled=0),
            self.doc("Item", prefix + "-RAW", item_code=prefix + "-RAW", stock_uom="Nos", disabled=0),
            self.doc("Item", prefix + "-FG", item_code=prefix + "-FG", stock_uom="Nos", disabled=0),
            self.doc("Workstation", prefix + "-WS", workstation_name=prefix + "-WS", disabled=0),
            self.doc("Operation", prefix + "-OP", workstation=prefix + "-WS"),
            self.doc("BOM", "BOM-1", docstatus=1, company="QA", item=prefix + "-FG", is_active=1, is_default=1),
            self.doc("Jifeng Flow Order", "FLOW-1", company="QA", item_code=prefix + "-FG", work_order="WO-1"),
            self.doc("Work Order", "WO-1", docstatus=1, company="QA", qty=5, status="In Process",
                     operations=[Doc(name="WO-OP-1", parent="WO-1", parenttype="Work Order", operation=prefix + "-OP",
                                     workstation=prefix + "-WS", time_in_mins=1, completed_qty=5,
                                     actual_operation_time=120, actual_start_time="2026-09-30 09:00:00")]),
            self.doc("Job Card", "JC-1", docstatus=1, company="QA", work_order="WO-1", jf_flow_order="FLOW-1", operation=prefix + "-OP",
                     total_completed_qty=5, total_time_in_mins=120, transferred_qty=0, status="Completed",
                     time_logs=[Doc(name="LOG-1", parent="JC-1", parenttype="Job Card", employee="E-1",
                                    from_time="2026-09-30 09:00:00", to_time="2026-09-30 10:00:00",
                                    time_in_mins=60, completed_qty=0)]),
        ]
        for doc in sources:
            kind = next(kind for kind, doctype in self.module.KINDS.items() if doctype == doc.doctype)
            self.data[kind].append(doc.name)
            self.data["records"].append(self.module._record(self.data, doc))
        self.data["source_snapshot"] = dict(company="QA", job_card="JC-1", work_order="WO-1", flow_order="FLOW-1",
            operation=prefix + "-OP", operation_id="WO-OP-1", item_code=prefix + "-FG", stock_uom="Nos", currency="CNY",
            work_date="2026-09-30", source_completed_qty="5.000000", source_signature="source-signature")
        self.data["ui_contract"] = {"allocation_notes": prefix + " 工资浏览器验收（合成夹具）", "correct_shares": [
            {"employee": "E-1", "quantity": 2, "piece_rate": 3}, {"employee": "E-2", "quantity": 3, "piece_rate": 4}]}

    def doc(self, doctype, name, **fields):
        doc = Doc(doctype=doctype, name=name, owner="Administrator", creation="2026-09-30 09:00:00", docstatus=0)
        doc.update(fields)
        self.docs[(doctype, name)] = doc
        return doc

    def get_all(self, doctype, filters=None, **kwargs):
        if doctype == "Bin":
            return []
        docs = [doc for (kind, _), doc in self.docs.items() if kind == doctype]
        for field, value in (filters or {}).items():
            if isinstance(value, list):
                docs = [doc for doc in docs if doc.get(field) in value[1]]
            else:
                docs = [doc for doc in docs if doc.get(field) == value]
        return sorted(doc.name for doc in docs)

    def ui_documents(self):
        allocation = self.doc("Jifeng Wage Allocation", "A-1", **self.data["source_snapshot"], docstatus=1,
            payroll_date=self.data["payroll_date"], salary_component=self.data["salary_components"][0],
            allocated_qty=5, total_amount=18, notes=self.data["ui_contract"]["allocation_notes"], employees=[])
        for index, spec in enumerate(self.data["ui_contract"]["correct_shares"], 1):
            amount = spec["quantity"] * spec["piece_rate"]
            row = Doc(name=f"ROW-{index}", parent="A-1", parenttype="Jifeng Wage Allocation", **spec,
                hours=1, source_hours_complete=1, include_time_pay=0, wage_hours=0, hourly_rate=0, extra_amount=0,
                piece_amount=amount, time_amount=0, total_amount=amount, wage_entry=f"W-{index}")
            allocation.employees.append(row)
        wages = [self.doc("Jifeng Wage Entry", row.wage_entry, **self.module.wage_values(allocation, row),
                          notes=self.module.GENERATED_WAGE_NOTES, creation="2026-09-30 09:05:00")
                 for row in allocation.employees]
        return allocation, wages

    def register(self, allocation, wages):
        self.data["allocations"], self.data["wages"] = [allocation.name], [doc.name for doc in wages]
        self.data["records"].extend(self.module._record(self.data, doc) for doc in [allocation, *wages])

    def test_journal_rejects_changed_source_path_and_business_binding(self):
        self.module._journal(self.data)
        self.assertEqual(self.module._load(self.data)["job_cards"], ["JC-1"])
        for field, value in (("job_cards", ["REAL-CARD"]), ("employees", ["REAL-EMPLOYEE"]),
                             ("source_snapshot", {**self.data["source_snapshot"], "job_card": "REAL-CARD"})):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "身份或准备来源集合"):
                self.module._load({**self.data, field: value})
        with self.assertRaisesRegex(ValueError, "路径"):
            self.module._load({**self.data, "artifact_path": str(self.directory / "foreign.json")})

    def test_read_path_does_not_create_private_directory(self):
        self.module._path(self.data)
        self.assertFalse((self.directory / "private").exists())

    def test_inspect_observes_unregistered_ui_without_writing_or_adopting_it(self):
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        self.ui_documents()
        result = self.module.inspect(self.data)
        self.assertEqual(result["unregistered_ui"], {"allocations": ["A-1"], "wages": ["W-1", "W-2"]})
        self.assertEqual(result["registered_ui"], {"allocations": [], "wages": []})
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        self.frappe.db.commit.assert_not_called()

    def test_inspect_early_failed_prepare_without_job_card_keeps_read_only_evidence(self):
        self.data["phase"], self.data["job_cards"] = "prepare_failed_rolled_back", []
        self.data["records"] = [row for row in self.data["records"] if row["doctype"] != "Job Card"]
        self.docs.clear()
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        result = self.module.inspect(self.data)
        self.assertTrue(result["source_validation_errors"])
        self.assertTrue(all(not row["exists"] for row in result["state"]))
        self.assertEqual(result["unregistered_ui"], {"allocations": [], "wages": []})
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        self.frappe.db.commit.assert_not_called()

    def test_approved_stage_requires_explicit_native_generated_wage_ids(self):
        allocation, wages = self.ui_documents()
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        with self.assertRaisesRegex(AssertionError, "必须显式登记"):
            self.module.record_ui(self.data, allocation=allocation.name, stage="approved")
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        result = self.module.record_ui(self.data, allocation=allocation.name,
                                       wages=[wage.name for wage in wages], stage="approved")
        self.assertEqual(result["wages"], ["W-1", "W-2"])
        self.frappe.db.commit.assert_not_called()

    def test_old_prepare_output_uses_canonical_registered_ui_ids(self):
        original = deepcopy(self.data)
        allocation, wages = self.ui_documents()
        self.register(allocation, wages)
        self.module._journal(self.data)
        self.assertEqual(self.module._load(original)["wages"], ["W-1", "W-2"])
        self.assertEqual(original["wages"], [])

    def test_wage_binding_rejects_wrong_row_employee_amount_owner_and_notes(self):
        allocation, wages = self.ui_documents()
        self.module._validate_wage(self.data, wages[0], allocation)
        for field, value in (("wage_allocation_employee", "ROW-2"), ("employee", "E-2"),
                             ("quantity", 5), ("total_amount", 999), ("job_card", "FOREIGN-CARD"),
                             ("owner", "Other User"), ("notes", "Other Notes")):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.module._validate_wage(self.data, Doc({**wages[0], field: value}), allocation)

    def test_allocation_binding_rejects_foreign_source_marker_and_old_creation(self):
        allocation, _ = self.ui_documents()
        self.module._validate_allocation(self.data, allocation)
        for field, value in (("job_card", "FOREIGN-CARD"), ("source_completed_qty", 6),
                             ("source_signature", "forged"), ("notes", "other fixture"),
                             ("creation", "2026-09-30 08:00:00")):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.module._validate_allocation(self.data, Doc({**allocation, field: value}))

    def test_unregistered_wage_and_external_salary_reference_are_refused(self):
        allocation, wages = self.ui_documents()
        self.register(allocation, wages)
        self.module._validate_registered_ui(self.data)
        self.doc("Jifeng Wage Entry", "UNREGISTERED", job_card="JC-1")
        with self.assertRaisesRegex(AssertionError, "未登记"):
            self.module._validate_registered_ui(self.data)
        self.docs.pop(("Jifeng Wage Entry", "UNREGISTERED"))
        self.links.get_dynamic_linked_docs.side_effect = lambda doc: [
            {"reference_doctype": "Additional Salary", "reference_docname": "FOREIGN-AS"}
        ] if doc.doctype == "Jifeng Wage Entry" else []
        with self.assertRaisesRegex(AssertionError, "日志外单据"):
            self.module._validate_registered_ui(self.data)

    def test_native_cancel_derived_fields_do_not_change_immutable_source_identity(self):
        # Native Job Card cancellation clears WO actual completion/times;
        # WO cancellation changes status, and BOM clears active/default flags.
        allocation, wages = self.ui_documents()
        self.register(allocation, wages)
        for doc in self.docs.values():
            if doc.doctype in {"Job Card", "Work Order", "BOM", "Jifeng Wage Allocation", "Jifeng Wage Entry"}:
                doc.docstatus = 2
            if doc.doctype in {"Work Order", "Job Card"}:
                doc.status = "Cancelled"
            if doc.doctype in {"Item", "Salary Component", "Workstation"}:
                doc.disabled = 1
            if doc.doctype == "Employee":
                doc.status = "Inactive"
        self.docs[("BOM", "BOM-1")].update(is_active=0, is_default=0)
        operation = self.docs[("Work Order", "WO-1")].operations[0]
        operation.update(completed_qty=0, actual_operation_time=0, actual_start_time=None, actual_end_time=None)
        self.docs[("Job Card", "JC-1")].transferred_qty = 0
        self.data["cleanup_complete"] = True
        self.module._validate_sources(self.data)
        self.module._validate_registered_ui(self.data)
        operation.workstation = "FOREIGN-WORKSTATION"
        with self.assertRaisesRegex(AssertionError, "业务身份或内容变化"):
            self.module._validate_sources(self.data)

    def test_job_card_flow_and_department_links_remain_fixed_during_cleanup(self):
        card = self.docs[("Job Card", "JC-1")]
        for field, value in (("jf_flow_order", "FOREIGN-FLOW"), ("jf_department", "FOREIGN-DEPARTMENT")):
            previous = card.get(field)
            card[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(AssertionError, "业务身份或内容变化"):
                self.module._validate_sources(self.data)
            card[field] = previous

    def test_commit_exception_records_unknown_and_releases_transaction_barrier(self):
        self.frappe.db.commit.side_effect = RuntimeError("connection lost")
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            self.module._commit(self.data, "prepare")
        self.assertEqual(self.frappe.db._disable_transaction_control, 0)
        canonical = self.module._load(self.data)
        self.assertEqual(canonical["phase"], "prepare_commit_unknown")
        self.assertEqual(canonical["pending_state"], self.module._state(self.data))
        self.frappe.db.commit.assert_called_once()

    def test_reconcile_unknown_prepare_confirms_exact_committed_state_without_db_write(self):
        self.data.update(phase="prepare_commit_unknown", commit_phase="prepare", pending_state=self.module._state(self.data))
        self.module._journal(self.data)
        result = self.module.reconcile(self.data)
        self.assertEqual(result["phase"], "ready")
        self.frappe.db.commit.assert_not_called()

    def test_reconcile_unknown_prepare_missing_sources_does_not_repeat_create(self):
        self.data.update(phase="prepare_commit_unknown", commit_phase="prepare", pending_state=self.module._state(self.data))
        self.module._journal(self.data)
        self.docs.clear()
        self.assertEqual(self.module.reconcile(self.data)["phase"], "prepare_failed_rolled_back")
        self.frappe.db.commit.assert_not_called()

    def test_reconcile_unknown_partial_state_is_refused_without_rewriting_journal(self):
        self.data.update(phase="prepare_commit_unknown", commit_phase="prepare", pending_state=self.module._state(self.data))
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        self.docs.pop(("Job Card", "JC-1"))
        with self.assertRaisesRegex(ValueError, "提交结果"):
            self.module.reconcile(self.data)
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        self.frappe.db.commit.assert_not_called()

    def test_reconcile_unknown_cleanup_rollback_restores_previous_journal_allowlist(self):
        before = deepcopy(self.data)
        state = self.module._state(self.data)
        self.data.update(phase="cleanup_commit_unknown", commit_phase="cleanup", pending_state=[],
                         cleanup_before_state=state, cleanup_before_journal=before,
                         deleted_document_log=[{"doctype": "Jifeng Wage Entry", "name": "W-1"}])
        self.module._journal(self.data)
        result = self.module.reconcile(self.data)
        self.assertEqual(result["phase"], "ready")
        self.assertEqual(result["deleted_document_log"], [])
        self.assertFalse(result["cleanup_complete"])
        self.frappe.db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
