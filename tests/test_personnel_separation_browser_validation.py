"""Safety gates for the persistent fixture; native business gates run on site."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / "jifeng_erp"


class Doc(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class SeparationBrowserValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.frappe = types.ModuleType("frappe")
        self.frappe.only_for = Mock()
        self.frappe.session = Doc(user="Administrator")
        self.frappe.in_test = False
        self.frappe.get_site_path = lambda *parts: str(self.directory.joinpath(*parts))
        self.frappe.db = Mock()
        self.frappe.db._disable_transaction_control = 1
        self.frappe.get_doc = Mock()
        self.frappe.get_all = Mock()
        utils = types.ModuleType("frappe.utils")
        utils.now = lambda: "2026-09-30 12:00:00"
        utils.today = lambda: "2026-09-30"
        utils.cint = lambda value: int(value or 0)
        utils.add_days = Mock()
        diagnostics = types.ModuleType("jifeng_erp.diagnostics")
        diagnostics._begin_rollback_only = Mock()
        diagnostics._end_rollback_only = Mock()
        inventory = types.ModuleType("jifeng_erp.inventory_validation")
        inventory._ledger_snapshot = Mock(return_value={"GL Entry": "gl", "Stock Ledger Entry": "stock"})
        changes = types.ModuleType("jifeng_erp.personnel_changes_validation")
        changes._callbacks = Mock(return_value=["existing callback"])
        changes._restore_callbacks = Mock()
        changes._normal = lambda value: json.loads(json.dumps(value, default=str, sort_keys=True))
        import hashlib
        changes._digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
        changes._employee_contents = Mock(return_value={"Employee": [], "children": []})
        changes._tab_counts = Mock(return_value=({}, {"tabEmployee": 0, "tabVersion": 0}))
        separation = types.ModuleType("jifeng_erp.personnel_separation_validation")
        for name, value in {"LAST_DAY": "jf_last_working_day", "REASON": "jf_reason_for_leaving",
                            "SNAPSHOT": "jf_exit_snapshot", "SOURCE": "Employee Separation",
                            "STATUS": "jf_exit_status"}.items():
            setattr(separation, name, value)
        separation._activities_hash = Mock(return_value="activity-hash")
        separation._audit_digest = changes._digest
        separation._payroll_contents = Mock(return_value={"wages": []})
        separation._source_graph = Mock()
        separation._state = Mock()
        self.accounts = {"User": [
            {"name": "Administrator", "enabled": 1, "last_login": "before", "last_active": "before", "last_ip": "127.0.0.1"},
            {"name": "Other", "enabled": 1, "last_active": "before"}],
            "roles": [{"parent": "Administrator", "role": "System Manager"}], "auth": [{"password": "private"}],
            "User Permission": [{"name": "UP-1", "allow": "Employee", "for_value": "PRIVATE-EMPLOYEE"}]}
        separation._user_contents = Mock(side_effect=lambda: deepcopy(self.accounts))
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
            "jifeng_erp.diagnostics": diagnostics, "jifeng_erp.inventory_validation": inventory,
            "jifeng_erp.personnel_changes_validation": changes,
            "jifeng_erp.personnel_separation_validation": separation})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("separation_browser_under_test", ROOT / "personnel_separation_browser_validation.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.restore_callbacks = changes._restore_callbacks
        self.data = {field: None for field in self.module.IMMUTABLE}
        self.data.update(fixture_type="separation_browser", fixture_version=1, prefix="JF-SEP-BROWSER-012345abcdef",
            company="QA", browser_user="Administrator", created_at="2026-09-30 08:00:00", created_by="Administrator",
            employee="EMP-1", source="SEP-1", project="PROJ-1", tasks=["TASK-1", "TASK-2"], records=[],
            holiday_list="JF-SEP-BROWSER-012345abcdef-HOL", holiday_assignment="HLA-1",
            phase="ready", cleanup_complete=False, ui_stage_log=[], counts_before={"tabEmployee": 0, "tabVersion": 0},
            last_day="2026-09-27", reason="Synthetic reason", employee_modified_before="2026-09-30T08:00:00")
        self.data["protected_before"] = self.module._protected(self.data)
        self.before = {"fields": {"status": "Active", "relieving_date": None, "reason_for_leaving": None,
                        "company": "QA", "user_id": None, "ctc": 0}, "history": []}
        self.data["employee_before"] = self.before
        self.data["source_intent"] = {"name": "SEP-1", "employee": "EMP-1", "company": "QA", "project": "PROJ-1", "amended_from": None}
        self.data["graph_before"] = {"project": {"name": "PROJ-1", "status": "Completed"},
                                     "tasks": [{"task": "TASK-1", "status": "Completed"}, {"task": "TASK-2", "status": "Completed"}]}
        self.data["payroll_before"] = self.module._audit_digest({"wages": []})

    def observation(self, stage="finalized"):
        return {"stage": stage, "snapshot": {"state": stage}, "employee_state": self.before,
                "source_docstatus": 2 if stage == "cancelled" else 1,
                "graph": self.data["graph_before"], "protected": self.data["protected_before"], "owned_state": []}

    def payload(self, state="finalized"):
        after = {**self.before["fields"], "status": "Left", "relieving_date": self.data["last_day"],
                 "reason_for_leaving": self.data["reason"]}
        return {"version": 1, "state": state, "source": self.data["source_intent"],
            "before": {"fields": self.before["fields"], "history_hash": self.module._audit_digest([]),
                       "modified": self.data["employee_modified_before"]},
            "after": {"fields": after, "history_hash": self.module._audit_digest([]), "modified": "2026-09-30T09:00:00"},
            "changed_fields": ["reason_for_leaving", "relieving_date", "status"],
            "project": self.data["graph_before"]["project"], "tasks": self.data["graph_before"]["tasks"],
            "payroll_hash": self.data["payroll_before"], "finalized_by": "Administrator", "finalized_at": "2026-09-30T09:00:00",
            "reversal": None if state == "finalized" else {"by": "Administrator", "at": "2026-09-30T10:00:00"}}

    def test_path_rejects_traversal_and_foreign_journal_without_creating_directory(self):
        self.module._path(self.data)
        self.assertFalse((self.directory / "private").exists())
        for changed in ({"prefix": "../foreign"}, {"artifact_path": str(self.directory / "foreign.json")}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.module._path({**self.data, **changed})

    def test_old_prepare_result_keeps_canonical_stages_and_rejects_identity_replacement(self):
        old = deepcopy(self.data)
        self.data["phase"] = "ui_finalized"
        self.data["ui_stage_log"] = [{"stage": "finalized"}]
        self.module._journal(self.data)
        self.assertEqual(self.module._load(old)["phase"], "ui_finalized")
        for field, value in (("employee", "REAL-EMPLOYEE"), ("tasks", ["REAL-TASK"]),
                             ("protected_before", {"ledger": "forged"})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module._load({**old, field: value})

    def test_account_audit_allows_only_named_browser_login_metadata(self):
        initial = self.module._accounts("Administrator")
        self.accounts["User"][0].update(last_active="after", last_login="after", last_ip="10.0.0.1")
        after = self.module._accounts("Administrator")
        self.assertEqual(initial["hash"], after["hash"])
        self.assertNotEqual(initial["browser_login_metadata"], after["browser_login_metadata"])
        self.assertNotIn("private", json.dumps(after))
        self.assertNotIn("PRIVATE-EMPLOYEE", json.dumps(after))
        self.accounts["User"][1]["last_active"] = "after"
        self.assertNotEqual(initial["hash"], self.module._accounts("Administrator")["hash"])

    def test_account_enabled_roles_password_and_user_permissions_are_never_excluded(self):
        initial = self.module._accounts("Administrator")["hash"]
        for table, index, field, value in (("User", 0, "enabled", 0), ("roles", 0, "role", "HR User"),
                                           ("auth", 0, "password", "changed"), ("User Permission", 0, "for_value", "OTHER")):
            with self.subTest(table=table):
                old = self.accounts[table][index][field]
                self.accounts[table][index][field] = value
                self.assertNotEqual(initial, self.module._accounts("Administrator")["hash"])
                self.accounts[table][index][field] = old

    def test_complete_ledger_or_other_employee_change_is_refused(self):
        for field, value in (("ledger", {"GL Entry": "changed"}), ("other_employees_hash", "changed"),
                             ("accounts_hash", "changed")):
            with self.subTest(field=field), patch.object(self.module, "_protected", return_value={**self.data["protected_before"], field: value}):
                with self.assertRaises(AssertionError):
                    self.module._assert_protected(self.data)

    def test_snapshot_checks_exact_restore_fields_and_owned_source_graph(self):
        payload = self.payload()
        source = Doc(jf_exit_snapshot=json.dumps(payload))
        self.assertEqual(self.module._snapshot(self.data, source, "finalized"), payload)
        mutations = [lambda p: p["before"]["fields"].update(status="Left"),
                     lambda p: p["after"]["fields"].update(ctc=999),
                     lambda p: p.update(tasks=[{"task": "REAL-TASK"}]),
                     lambda p: p.update(payroll_hash="changed"),
                     lambda p: p.update(finalized_by="Other")]
        for mutation in mutations:
            altered = deepcopy(payload)
            mutation(altered)
            with self.subTest(payload=altered), self.assertRaises(AssertionError):
                self.module._snapshot(self.data, Doc(jf_exit_snapshot=json.dumps(altered)), "finalized")

    def test_reversal_snapshot_requires_actual_browser_actor_and_time(self):
        payload = self.payload("reversed")
        self.module._snapshot(self.data, Doc(jf_exit_snapshot=json.dumps(payload)), "reversed")
        payload["reversal"]["by"] = "Other"
        with self.assertRaises(AssertionError):
            self.module._snapshot(self.data, Doc(jf_exit_snapshot=json.dumps(payload)), "reversed")

    def test_inspect_cannot_write_commit_or_adopt_other_ui_identifiers(self):
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        with patch.object(self.module, "_owned_state", return_value=[]), patch.object(self.module, "_observe", return_value=self.observation()):
            result = self.module.inspect(self.data)
        self.assertEqual(result["stage"], "finalized")
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        self.frappe.db.commit.assert_not_called()

    def test_stage_registration_requires_actual_db_state_and_order(self):
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        with patch.object(self.module, "_observe", return_value=self.observation("ready")):
            with self.assertRaises(AssertionError):
                self.module.record_ui(self.data, "finalized")
        with patch.object(self.module, "_observe", return_value=self.observation("cancelled")):
            with self.assertRaises(AssertionError):
                self.module.record_ui(self.data, "cancelled")
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        with patch.object(self.module, "_observe", return_value=self.observation("finalized")):
            recorded = self.module.record_ui(self.data, "finalized")
        self.assertEqual(recorded["phase"], "ui_finalized")
        self.frappe.db.commit.assert_not_called()

    def test_commit_exception_is_unknown_once_with_exact_pending_state(self):
        self.frappe.db.commit.side_effect = RuntimeError("connection lost")
        owned = [{"name": "SEP-1", "docstatus": 1}]
        with patch.object(self.module, "_owned_state", return_value=owned):
            with self.assertRaisesRegex(RuntimeError, "connection lost"):
                self.module._commit(self.data, "prepare")
        canonical = self.module._load(self.data)
        self.assertEqual(canonical["phase"], "prepare_commit_unknown")
        self.assertEqual(canonical["pending_state"], owned)
        self.assertEqual(self.frappe.db._disable_transaction_control, 0)
        self.frappe.db.commit.assert_called_once()

    def test_unknown_state_prevents_cleanup_and_ui_registration(self):
        self.data["phase"] = "prepare_commit_unknown"
        self.module._journal(self.data)
        with self.assertRaises(AssertionError):
            self.module.cleanup(self.data)
        with self.assertRaises(AssertionError):
            self.module.record_ui(self.data, "finalized")
        self.frappe.get_doc.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_reconcile_partial_commit_is_refused_without_rewriting_journal(self):
        self.data.update(phase="prepare_commit_unknown", commit_phase="prepare",
                         pending_state=[{"name": "SEP-1", "exists": True}, {"name": "EMP-1", "exists": True}])
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        with patch.object(self.module, "_owned_state", return_value=[{"name": "SEP-1", "exists": True}, {"name": "EMP-1", "exists": False}]):
            with self.assertRaisesRegex(ValueError, "提交结果"):
                self.module.reconcile(self.data)
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        self.frappe.db.commit.assert_not_called()

    def test_cleanup_refuses_to_reverse_finalized_business_on_behalf_of_ui(self):
        self.module._journal(self.data)
        with patch.object(self.module, "_observe", return_value=self.observation("finalized")):
            with self.assertRaisesRegex(AssertionError, "须先原生撤销"):
                self.module.cleanup(self.data)
        self.frappe.get_doc.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_cleanup_completed_fixture_is_a_read_only_noop(self):
        self.data.update(phase="cancelled_history_retained", cleanup_complete=True)
        self.module._journal(self.data)
        original = self.module._path(self.data).read_bytes()
        with patch.object(self.module, "_observe", return_value=self.observation("cancelled")):
            result = self.module.cleanup(self.data)
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(self.module._path(self.data).read_bytes(), original)
        self.frappe.get_doc.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def cleanup_documents(self):
        source = Doc(doctype="Employee Separation", name="SEP-1", docstatus=1)
        employee = Doc(doctype="Employee", name="EMP-1", status="Active", user_id=None)
        assignment = Doc(doctype="Holiday List Assignment", name="HLA-1", docstatus=1)
        source.cancel = Mock(side_effect=lambda: source.update(docstatus=2))
        assignment.cancel = Mock(side_effect=lambda: assignment.update(docstatus=2))
        employee.save = Mock()
        self.frappe.get_doc.side_effect = lambda doctype, name: {
            "Employee Separation": source, "Employee": employee, "Holiday List Assignment": assignment}[doctype]
        self.assignment = assignment
        self.frappe.db._disable_transaction_control = 0
        self.module._begin_rollback_only.side_effect = lambda point: setattr(self.frappe.db, "_disable_transaction_control", 1)
        self.module._end_rollback_only.side_effect = lambda point: setattr(self.frappe.db, "_disable_transaction_control", 0)
        return source, employee

    def test_cleanup_uses_native_cancel_and_save_once_and_retains_links(self):
        source, employee = self.cleanup_documents()
        self.data["phase"] = "ui_reversed"
        self.module._journal(self.data)
        def observed(data):
            return self.observation("cancelled" if data["cleanup_complete"] else "reversed")
        retained = [{"doctype": "Employee Separation", "name": "SEP-1", "project": "PROJ-1"}]
        with patch.object(self.module, "_observe", side_effect=observed), patch.object(self.module, "_owned_state", return_value=retained):
            result = self.module.cleanup(self.data)
        source.cancel.assert_called_once_with()
        self.assignment.cancel.assert_called_once_with()
        self.assertEqual(self.assignment.docstatus, 2)
        employee.save.assert_called_once_with()
        self.assertEqual(employee.status, "Inactive")
        self.assertEqual(result["phase"], "cancelled_history_retained")
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(result["project"], "PROJ-1")
        self.assertEqual(result["tasks"], ["TASK-1", "TASK-2"])
        self.frappe.db.commit.assert_called_once_with()
        self.frappe.db.set_value.assert_not_called()

    def test_cleanup_native_save_failure_rolls_back_and_keeps_original_allowlist(self):
        source, employee = self.cleanup_documents()
        employee.save.side_effect = RuntimeError("native employee validation failed")
        self.module._journal(self.data)
        with patch.object(self.module, "_observe", return_value=self.observation("ready")), \
                patch.object(self.module, "_owned_state", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "native employee validation failed"):
                self.module.cleanup(self.data)
        canonical = self.module._load(self.data)
        self.assertEqual(canonical["phase"], "cleanup_failed_rolled_back")
        self.assertFalse(canonical["cleanup_complete"])
        self.assertEqual(canonical["tasks"], ["TASK-1", "TASK-2"])
        source.cancel.assert_called_once_with()
        self.module._end_rollback_only.assert_called_once()
        self.restore_callbacks.assert_called_once_with(["existing callback"])
        self.assertEqual(self.frappe.db._disable_transaction_control, 0)
        self.frappe.db.commit.assert_not_called()

    def test_prepare_missing_native_submit_permission_fails_before_creating_any_fixture(self):
        self.frappe.db.db_type = "mariadb"
        self.frappe.db.exists.return_value = True
        self.frappe.db.get_value.return_value = 1
        self.frappe.has_permission = Mock(side_effect=lambda doctype, permission, **kwargs:
                                        not (doctype == "Employee Separation" and permission == "submit"))
        with self.assertRaisesRegex(AssertionError, "原生操作权限"):
            self.module.prepare("QA")
        self.frappe.get_doc.assert_not_called()
        self.frappe.db.commit.assert_not_called()
        self.assertFalse((self.directory / "private").exists())

    def test_snapshot_intent_binds_amended_from_and_rejects_changed_origin(self):
        payload = self.payload()
        payload["source"]["amended_from"] = "CANCELLED-ORIGINAL"
        self.module._snapshot(self.data, Doc(jf_exit_snapshot=json.dumps(payload)), "finalized")
        altered = deepcopy(payload)
        altered["source"]["amended_from"] = "OTHER-ORIGINAL"
        with self.assertRaises(AssertionError):
            self.module._snapshot(self.data, Doc(jf_exit_snapshot=json.dumps(altered)), "finalized")

    def owned_holiday_documents(self):
        def doc(doctype, name, **values):
            return Doc(doctype=doctype, name=name, creation="2026-09-30 08:00:00", owner="Administrator", **values)
        employee = doc("Employee", "EMP-1", company="QA", first_name=self.data["prefix"],
                       employee_number=self.data["prefix"], user_id=None, date_of_joining="2026-07-02")
        source = doc("Employee Separation", "SEP-1", employee="EMP-1", company="QA", project="PROJ-1",
                     boarding_begins_on="2026-09-25", resignation_letter_date="2026-09-24", amended_from=None,
                     jf_last_working_day=self.data["last_day"], jf_reason_for_leaving=self.data["reason"],
                     notify_users_by_email=0, activities=[Doc(name="A-1", task="TASK-1"), Doc(name="A-2", task="TASK-2")])
        project = doc("Project", "PROJ-1", company="QA", project_name="SEP-1")
        tasks = [doc("Task", name, company="QA", project="PROJ-1", subject="synthetic") for name in self.data["tasks"]]
        holiday = doc("Holiday List", self.data["holiday_list"], holiday_list_name=self.data["holiday_list"],
                      from_date="2026-06-30", to_date="2026-10-30", holidays=[])
        assignment = doc("Holiday List Assignment", "HLA-1", applicable_for="Employee", assigned_to="EMP-1",
                         holiday_list=holiday.name, from_date="2026-07-02", employee_company="QA", docstatus=1)
        documents = [employee, source, project, *tasks, holiday, assignment]
        self.data["records"] = [self.module._record(document) for document in documents]
        self.data["source_intent"] = self.module._intent(source)
        lookup = {(document.doctype, document.name): document for document in documents}
        self.frappe.db.exists.side_effect = lambda doctype, name: False if doctype == "Employee Onboarding" else (doctype, name) in lookup
        self.frappe.db.get_value.return_value = holiday.name
        self.frappe.get_doc.side_effect = lambda doctype, name: lookup[(doctype, name)]
        self.frappe.get_all.side_effect = lambda doctype, **kwargs: ["SEP-1"] if doctype == "Employee Separation" else ["HLA-1"]
        self.module._source_graph.return_value = source, project, tasks, [{"name": "A-1"}, {"name": "A-2"}]
        return assignment

    def test_owned_holiday_config_is_employee_specific_exact_and_cancelled_at_cleanup(self):
        assignment = self.owned_holiday_documents()
        self.module._validate_owned(self.data)
        self.data["cleanup_complete"] = True
        with self.assertRaisesRegex(AssertionError, "生命周期"):
            self.module._validate_owned(self.data)
        assignment.docstatus = 2
        self.module._validate_owned(self.data)
        self.frappe.get_all.side_effect = lambda doctype, **kwargs: ["SEP-1"] if doctype == "Employee Separation" else ["HLA-1", "UNOWNED-HLA"]
        with self.assertRaisesRegex(AssertionError, "日志外"):
            self.module._validate_owned(self.data)

    def test_owned_holiday_assignment_cannot_bind_global_company_or_other_employee(self):
        assignment = self.owned_holiday_documents()
        for field, value in (("applicable_for", "Company"), ("assigned_to", "OTHER-EMPLOYEE")):
            previous = assignment[field]
            assignment[field] = value
            self.data["records"][-1] = self.module._record(assignment)
            with self.subTest(field=field), self.assertRaisesRegex(AssertionError, "员工专属"):
                self.module._validate_owned(self.data)
            assignment[field] = previous
            self.data["records"][-1] = self.module._record(assignment)


if __name__ == "__main__":
    unittest.main()
