from copy import deepcopy
from collections import deque
from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Doc(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def set(self, name, value):
        self[name] = value

    def is_new(self):
        return bool(self.get("__islocal"))

    def as_dict(self):
        return deepcopy({key: value for key, value in self.items() if key not in ("flags", "meta")})


class TestPersonnelSeparation(unittest.TestCase):
    def setUp(self):
        self.store, self.checkpoints = {}, {}
        self.denied, self.field_denied = {}, {}
        self.levels = {"read": {0}, "write": {0}}
        self.table_level = 0
        self.source_table_level = 0
        self.clock, self.employee_saves, self.source_hooks, self.user_calls = 0, 0, 0, []
        self.fail_source, self.extra_task = False, False
        self.native_validations = 0
        self.employee_fields = {"company", "employee_name", "department", "designation", "branch", "grade",
            "status", "date_of_joining", "relieving_date", "user_id", "employment_type", "reports_to", "ctc",
            "create_user_permission", "resignation_letter_date", "reason_for_leaving", "phone"}
        self.fields = {"Employee": self.employee_fields,
            "Employee Internal Work History": {"branch", "department", "designation", "from_date", "to_date"},
            "Project": {"company", "status"}, "Task": {"company", "project", "status", "completed_on"},
            "Employee Separation": {"employee", "company", "project", "boarding_begins_on", "resignation_letter_date",
                "jf_last_working_day", "jf_reason_for_leaving", "jf_exit_snapshot", "jf_exit_status", "amended_from"},
            "Employee Boarding Activity": {"name", "task", "activity_name", "role", "description"}}
        test = self
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError, self.frappe.ValidationError, self.frappe.DoesNotExistError = PermissionError, ValueError, KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda: lambda function: function
        self.frappe.session = Doc(user="real-role-user")
        self.frappe.local = types.SimpleNamespace()
        self.frappe.flags = Doc(currently_saving=[])
        self.frappe._ = lambda value: value
        self.frappe.share = types.SimpleNamespace(get_shared=Mock(return_value=[]))
        model, utils = types.ModuleType("frappe.model"), types.ModuleType("frappe.utils")
        self.get_permitted_fields = Mock(side_effect=lambda doctype, permission_type="read", **kwargs:
            sorted(self.fields.get(doctype, set()) - self.field_denied.get((doctype, permission_type), set())))
        model.get_permitted_fields = self.get_permitted_fields
        utils.getdate = lambda value=None: date.fromisoformat(str(value or "2026-09-30")[:10])
        utils.today = lambda: "2026-09-30"
        utils.now_datetime = lambda: datetime(2026, 9, 30, 12)

        def employee_field(name):
            return Doc(fieldtype="Table", options="Employee Internal Work History", permlevel=self.table_level) if name == "internal_work_history" else Doc(fieldtype="Data", permlevel=0)

        self.employee_meta = types.SimpleNamespace(get_field=employee_field,
            get_permlevel_access=Mock(side_effect=lambda permission_type, **kwargs: sorted(self.levels[permission_type])))
        self.source_meta = types.SimpleNamespace(get_field=lambda name:
            Doc(fieldtype="Table", options="Employee Boarding Activity", permlevel=self.source_table_level)
            if name == "activities" else Doc(fieldtype="Long Text" if name == "jf_exit_snapshot" else "Select", read_only=1,
                no_copy=1, allow_on_submit=1, hidden=1, print_hide=1),
            get_permlevel_access=Mock(side_effect=lambda permission_type, **kwargs: sorted(self.levels[permission_type])))
        self.activity_meta = types.SimpleNamespace(get_valid_columns=lambda: ["name", "parent", "parenttype", "parentfield",
            "idx", "task", "activity_name", "role", "description", "modified", "docstatus"])
        self.payroll_meta = types.SimpleNamespace(get_table_fields=lambda: [Doc(fieldname="detail", options="Payroll Child")])
        self.frappe.get_meta = Mock(side_effect=lambda doctype:
            self.employee_meta if doctype == "Employee" else self.source_meta if doctype == "Employee Separation"
            else self.activity_meta if doctype == "Employee Boarding Activity" else self.payroll_meta)

        def notify_update(doc):
            if not hasattr(self.frappe.local, "_realtime_log"):
                self.frappe.local._realtime_log = []
                self.frappe.db.after_commit.add(lambda: self.callback_calls.append("flush_realtime_log"))
                def clear_realtime_log():
                    if hasattr(self.frappe.local, "_realtime_log"):
                        del self.frappe.local._realtime_log
                self.frappe.db.after_rollback.add(clear_realtime_log)
            self.frappe.local._realtime_log.append(["doc_update", {"doctype": doc.doctype, "name": doc.name}, "room"])

        class NativeDoc(Doc):
            def has_permission(self, permission, user=None):
                return permission not in test.denied.get((self.doctype, self.name), set())

            def check_permission(self, permission):
                if not self.has_permission(permission):
                    raise PermissionError("native denied")

            def reload(self):
                fresh = get_doc(self.doctype, self.name)
                self.clear()
                self.update(fresh)
                return self

            def save(self):
                self.check_permission("write")
                test.frappe.flags.setdefault("currently_saving", []).append((self.doctype, self.name))
                test.clock += 1
                self.modified = "2026-09-30T12:00:" + str(test.clock).zfill(2)
                test.store[(self.doctype, self.name)] = Doc(self.as_dict())
                notify_update(self)
                if self.doctype != "Employee Separation":
                    test.frappe.flags.currently_saving.remove((self.doctype, self.name))
                return self

        class NativeEmployee(NativeDoc):
            def update_user(self):
                test.user_calls.append("profile")
                test.store[("User", self.user_id)].first_name = "NATIVE-PROFILE"

            def update_user_permissions(self):
                test.user_calls.append("permission")
                test.store[("User Permission", "native-rule")] = Doc(user=self.user_id)

            def update_user_status(self):
                test.user_calls.append("status")
                test.store[("User", self.user_id)].enabled = int(self.status == "Active")

            def save(self):
                if self.status == "Left" and not self.relieving_date:
                    raise ValueError("native missing relieving date")
                if self.status == "Left" and any(dt == "Employee" and row.reports_to == self.name and row.status == "Active"
                    for (dt, _), row in test.store.items()):
                    raise ValueError("native active subordinate")
                if self.user_id and any(dt == "Employee" and row.name != self.name and row.user_id == self.user_id
                    and row.status == "Active" for (dt, _), row in test.store.items()):
                    raise ValueError("native duplicate active user")
                super().save()
                test.employee_saves += 1
                if self.user_id:
                    self.update_user()
                    self.update_user_permissions()
                    self.update_user_status()
                return self

        class NativeSource(NativeDoc):
            def validate(self):
                test.native_validations += 1
                if self.amended_from:
                    for activity in self.activities:
                        activity.task = ""

            def on_submit(self):
                project = test.Project(doctype="Project", project_name="Employee Separation : " + self.employee,
                    company=self.company, flags=Doc(), meta=test.project_meta)
                project.before_insert()
                if any(dt == "Project" and row.project_name == project.project_name for (dt, _), row in test.store.items()):
                    raise ValueError("native unique project title")
                project.name = "NEW-PROJECT-" + self.name
                test.store[("Project", project.name)] = Doc(project.as_dict())
                self.project = project.name

            def save(self):
                if self.docstatus == 1:
                    self.check_permission("submit")
                self.before_validate()
                if self.docstatus == 1:
                    self.before_update_after_submit()
                else:
                    self.validate()
                super().save()
                test.source_hooks += 1
                if test.extra_task:
                    test.store[("Task", "UNEXPECTED")] = Doc(doctype="Task", name="UNEXPECTED", project=self.project,
                        company=self.company, status="Open", modified="unexpected")
                if test.fail_source:
                    for queue in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
                        getattr(test.frappe.db, queue).add(lambda queue=queue: test.callback_calls.append(queue))
                    def clear_failed_realtime():
                        if hasattr(test.frappe.local, "_realtime_log"):
                            del test.frappe.local._realtime_log
                    test.frappe.db.after_rollback.add(clear_failed_realtime)
                    raise ValueError("native source save failed")
                test.frappe.flags.currently_saving.remove((self.doctype, self.name))
                return self

            def on_cancel(self):
                raise AssertionError("unsafe native force delete must never run")

        def get_doc(doctype, name=None, **kwargs):
            data = deepcopy(self.store[(doctype, name)])
            if doctype == "Employee":
                return self.Employee(data, flags=Doc())
            if doctype == "Employee Separation":
                return self.Source(data, flags=Doc(), meta=self.source_meta)
            return NativeDoc(data, flags=Doc())

        def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
            def match(row):
                for key, expected in (filters or {}).items():
                    if isinstance(expected, list):
                        if expected[0] == "in" and row.get(key) not in expected[1]:
                            return False
                    elif row.get(key) != expected:
                        return False
                return True
            rows = [row for (dt, _), row in sorted(self.store.items()) if dt == doctype and match(row)]
            if pluck:
                return [row.get(pluck) for row in rows]
            return [Doc(deepcopy(row) if fields == ["*"] else {field: row.get(field) for field in fields or ("name",)}) for row in rows]

        def savepoint(name):
            self.checkpoints[name] = deepcopy(self.store)

        def rollback(save_point):
            self.store = deepcopy(self.checkpoints[save_point])

        self.frappe.db = types.SimpleNamespace(
            get_value=Mock(side_effect=lambda dt, name, fields, **kwargs:
                Doc({field: self.store[(dt, name)].get(field) for field in fields}) if isinstance(fields, list)
                else self.store[(dt, name)].get(fields)),
            exists=Mock(side_effect=lambda dt, filters:
                filters in ("Jifeng Wage Entry", "Additional Salary", "Salary Slip", "Salary Structure Assignment")
                if dt == "DocType" else bool(get_all(dt, filters=filters))),
            escape=lambda value: "'" + value.replace("'", "''") + "'",
            savepoint=Mock(side_effect=savepoint), rollback=Mock(side_effect=rollback),
            delete=Mock(side_effect=AssertionError("delete must never run")))
        class Callbacks:
            def __init__(self):
                self._functions = deque()

            def add(self, callback):
                self._functions.append(callback)

            def reset(self):
                self._functions.clear()

        for queue in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            setattr(self.frappe.db, queue, Callbacks())
        self.callback_calls = []
        self.frappe.get_doc, self.frappe.get_all = Mock(side_effect=get_doc), Mock(side_effect=get_all)
        self.frappe.get_list = Mock(return_value=[Doc(name="EMP")])
        path = Path(__file__).parents[1] / "jifeng_erp"
        modules = {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils}
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("jifeng_erp.personnel_changes", path / "personnel_changes.py")
            changes = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(changes)
            with patch.dict(sys.modules, {"jifeng_erp.personnel_changes": changes}):
                spec = importlib.util.spec_from_file_location("personnel_separation_under_test", path / "personnel_separation.py")
                self.module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.module)
        self.Employee = type("Employee", (self.module.SeparationEmployeeMixin, NativeEmployee), {})
        self.Source = type("Source", (self.module.EmployeeSeparationMixin, NativeSource), {})
        self.Project = type("Project", (self.module.SeparationProjectMixin, NativeDoc), {})
        self.project_meta = types.SimpleNamespace(get_field=lambda name: Doc(fieldtype="Data", length=140))
        self.store[("User", "linked@example.test")] = Doc(doctype="User", name="linked@example.test", enabled=1, first_name="ORIGINAL")
        history = Doc(name="HISTORY", parent="EMP", parenttype="Employee", parentfield="internal_work_history",
            idx=1, department="DEPT", designation="JOB", branch=None, from_date="2020-01-01", to_date=None)
        self.store[("Employee", "EMP")] = Doc(doctype="Employee", name="EMP", company="COMPANY", employee_name="Employee",
            status="Active", date_of_joining="2020-01-01", relieving_date=None, reason_for_leaving="OLD-REASON",
            resignation_letter_date="2026-09-01", user_id="linked@example.test", create_user_permission=1, department="DEPT",
            designation="JOB", grade=None, branch=None, reports_to=None, employment_type=None, ctc=1000,
            modified="2026-09-01T12:00:00", internal_work_history=[history])
        self.store[("Company", "COMPANY")] = Doc(doctype="Company", name="COMPANY")
        self.store[("Project", "PROJECT")] = Doc(doctype="Project", name="PROJECT", company="COMPANY", status="Completed",
            percent_complete=100, percent_complete_method="Task Completion", modified="2026-09-10T12:00:00")
        self.store[("Task", "TASK")] = Doc(doctype="Task", name="TASK", project="PROJECT", company="COMPANY", status="Completed",
            completed_on="2026-09-29", modified="2026-09-29T12:00:00")
        activity = Doc(doctype="Employee Boarding Activity", name="ACTIVITY", parent="SOURCE", parenttype="Employee Separation",
            parentfield="activities", task="TASK")
        self.store[("Employee Boarding Activity", "ACTIVITY")] = activity
        self.store[("Employee Separation", "SOURCE")] = Doc(doctype="Employee Separation", name="SOURCE", docstatus=1,
            employee="EMP", company="COMPANY", project="PROJECT", boarding_begins_on="2026-09-01",
            resignation_letter_date="2026-09-01", jf_last_working_day="2026-09-30", jf_reason_for_leaving="ACTUAL-REASON",
            jf_exit_status="待办理", jf_exit_snapshot=None, activities=[deepcopy(activity)])

    def source(self):
        return self.frappe.get_doc("Employee Separation", "SOURCE")

    def finalize(self):
        return self.module.finalize_separation("SOURCE")

    def reverse(self):
        return self.module.reverse_separation("SOURCE")

    def test_exact_finalization_and_reversal_preserve_global_accounts_and_boarding(self):
        before = deepcopy(self.store[("Employee", "EMP")])
        user = deepcopy(self.store[("User", "linked@example.test")])
        project, task = deepcopy(self.store[("Project", "PROJECT")]), deepcopy(self.store[("Task", "TASK")])
        self.assertEqual(self.finalize()["exit_status"], "已办理")
        after = self.store[("Employee", "EMP")]
        self.assertEqual((after.status, after.relieving_date, after.reason_for_leaving), ("Left", "2026-09-30", "ACTUAL-REASON"))
        self.assertEqual(after.internal_work_history, before.internal_work_history)
        self.assertEqual(self.reverse()["exit_status"], "已撤销")
        restored = self.store[("Employee", "EMP")]
        restored.pop("modified")
        before.pop("modified")
        self.assertEqual(restored, before)
        self.assertEqual(self.store[("User", "linked@example.test")], user)
        self.assertEqual(self.store[("Project", "PROJECT")], project)
        self.assertEqual(self.store[("Task", "TASK")], task)
        self.assertEqual(self.user_calls, [])
        self.assertEqual(self.source_hooks, 2)

    def test_active_disabled_account_is_never_enabled_by_finalization_or_reversal(self):
        self.store[("User", "linked@example.test")].enabled = 0
        self.finalize()
        self.reverse()
        self.assertEqual(self.store[("User", "linked@example.test")].enabled, 0)
        self.assertEqual(self.user_calls, [])

    def test_client_flags_cannot_forge_token_and_normal_employee_save_keeps_native_sync(self):
        employee = self.frappe.get_doc("Employee", "EMP")
        employee.flags.jf_exit_account_guard = True
        employee.save()
        self.assertEqual(self.user_calls, ["profile", "permission", "status"])

    def test_native_submission_gives_each_same_employee_source_an_independent_project_title(self):
        previous = object()
        self.frappe.local.jf_exit_project_context = previous
        original = deepcopy(self.store[("Project", "PROJECT")])
        titles = []
        for name in ("FIRST", "SECOND"):
            source = self.source()
            source.name, source.project = name, None
            source.on_submit()
            project = self.store[("Project", source.project)]
            self.assertIn(name, project.project_name)
            titles.append(project.project_name)
            self.assertIs(self.frappe.local.jf_exit_project_context, previous)
        self.assertNotEqual(*titles)
        self.assertEqual(self.store[("Project", "PROJECT")], original)

    def test_project_title_keeps_complete_source_id_at_field_limit_and_context_is_server_only(self):
        source = self.source()
        source.name, source.project = "S" * 139, None
        source.on_submit()
        title = self.store[("Project", source.project)].project_name
        self.assertIn(source.name, title)
        self.assertLessEqual(len(title), 140)
        self.frappe.local.jf_exit_project_context = {"token": True, "native_base": "Employee Separation : EMP",
            "company": "COMPANY", "source": "FORGED", "used": False}
        project = self.Project(doctype="Project", project_name="Employee Separation : EMP", company="COMPANY",
            meta=self.project_meta)
        project.before_insert()
        self.assertEqual(project.project_name, "Employee Separation : EMP")

    def test_native_submit_context_is_restored_on_failure_and_wrong_company_is_blocked_before_graph(self):
        previous = object()
        self.frappe.local.jf_exit_project_context = previous
        source = self.source()
        source.name = "S" * 141
        with self.assertRaisesRegex(ValueError, "标题长度"):
            source.on_submit()
        self.assertIs(self.frappe.local.jf_exit_project_context, previous)
        source = self.source()
        source.company = "FOREIGN"
        initial = deepcopy(self.store)
        with self.assertRaisesRegex(ValueError, "公司不一致"):
            source.before_validate()
        with self.assertRaisesRegex(ValueError, "公司不一致"):
            source.on_submit()
        self.assertEqual(self.store, initial)
        self.field_denied = {("Employee", "read"): {"company"}}
        with self.assertRaises(PermissionError):
            self.source().before_validate()

    def test_idempotent_actions_do_not_save_again_and_reversed_source_is_not_reused(self):
        self.finalize()
        self.finalize()
        self.assertEqual(self.employee_saves, 1)
        self.reverse()
        self.reverse()
        self.assertEqual(self.employee_saves, 2)
        with self.assertRaisesRegex(ValueError, "不能复用"):
            self.finalize()

    def test_last_day_is_explicit_and_differs_from_boarding_start(self):
        for value in (None, "2026-10-01", "2019-12-31"):
            self.store[("Employee Separation", "SOURCE")].jf_last_working_day = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.finalize()
        self.assertEqual(self.employee_saves, 0)

    def test_nonactive_and_legacy_left_are_not_claimed_as_new_finalizations(self):
        for status in ("Inactive", "Suspended", "Left"):
            self.store[("Employee", "EMP")].status = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.finalize()
        self.assertEqual(self.employee_saves, 0)

    def test_native_subordinate_and_duplicate_active_user_guards_remain(self):
        employee = deepcopy(self.store[("Employee", "EMP")])
        employee.name, employee.user_id, employee.reports_to = "OTHER", None, "EMP"
        self.store[("Employee", "OTHER")] = employee
        with self.assertRaisesRegex(ValueError, "native active subordinate"):
            self.finalize()
        self.store[("Employee", "OTHER")].reports_to = None
        self.store[("Employee", "OTHER")].user_id = "linked@example.test"
        with self.assertRaisesRegex(ValueError, "native duplicate active user"):
            self.finalize()
        self.assertEqual(self.employee_saves, 0)

    def test_cancelled_open_missing_completion_and_manual_percent_do_not_prove_completion(self):
        for status, completed in (("Cancelled", "2026-09-29"), ("Open", None), ("Completed", None), ("Completed", "2026-10-01")):
            self.store[("Task", "TASK")].update(status=status, completed_on=completed)
            self.store[("Project", "PROJECT")].percent_complete_method = "Manual"
            with self.subTest(status=status, completed=completed), self.assertRaisesRegex(ValueError, "实际完成"):
                self.finalize()
        self.assertEqual(self.employee_saves, 0)

    def test_extra_task_or_source_owner_and_wrong_company_are_rejected(self):
        initial = deepcopy(self.store)
        mutations = (
            lambda: self.store.update({("Task", "EXTRA"): Doc(doctype="Task", name="EXTRA", project="PROJECT")}),
            lambda: self.store.update({("Employee Separation", "OTHER"): Doc(doctype="Employee Separation", name="OTHER", project="PROJECT")}),
            lambda: self.store[("Task", "TASK")].update(company="OTHER"),
            lambda: self.store[("Project", "PROJECT")].update(company="OTHER"),
            lambda: self.store[("Employee", "EMP")].update(company="OTHER"),
        )
        for mutate in mutations:
            self.store = deepcopy(initial)
            mutate()
            with self.assertRaises(ValueError):
                self.finalize()
        self.assertEqual(self.employee_saves, 0)

    def test_source_read_write_employee_read_write_and_graph_read_are_required(self):
        for doctype, name, permission in (("Employee Separation", "SOURCE", "read"), ("Employee Separation", "SOURCE", "write"),
            ("Employee Separation", "SOURCE", "submit"),
            ("Employee", "EMP", "read"), ("Employee", "EMP", "write"), ("Project", "PROJECT", "read"), ("Task", "TASK", "read")):
            self.denied = {(doctype, name): {permission}}
            with self.subTest(doctype=doctype, permission=permission), self.assertRaises(PermissionError):
                self.finalize()
        self.assertEqual(self.employee_saves, 0)

    def test_physical_columns_parent_table_and_history_child_fields_are_separate(self):
        self.assertNotIn("internal_work_history", self.get_permitted_fields("Employee", permission_type="read"))
        self.table_level = 1
        with self.assertRaises(PermissionError):
            self.finalize()
        self.table_level = 0
        for doctype, field in (("Employee", "ctc"), ("Employee Internal Work History", "from_date"), ("Task", "status")):
            self.field_denied = {(doctype, "read"): {field}}
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.finalize()
        self.assertEqual(self.employee_saves, 0)

    def test_source_physical_columns_activities_table_child_and_server_write_permissions(self):
        self.assertNotIn("activities", self.get_permitted_fields("Employee Separation", permission_type="read"))
        self.source_table_level = 1
        with self.assertRaises(PermissionError):
            self.finalize()
        self.source_table_level = 0
        for doctype, permission, field in (
            ("Employee Separation", "read", "jf_last_working_day"),
            ("Employee Separation", "read", "jf_reason_for_leaving"),
            ("Employee Separation", "read", "employee"),
            ("Employee Boarding Activity", "read", "task"),
            ("Employee Separation", "write", "jf_exit_snapshot"),
            ("Employee Separation", "write", "jf_exit_status"),
        ):
            self.field_denied = {(doctype, permission): {field}}
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.finalize()
        self.field_denied = {("Employee Separation", "write"): {"jf_last_working_day", "jf_reason_for_leaving"}}
        self.finalize()  # The approved intent can be read without editing those fields.
        child_calls = [call.kwargs for call in self.get_permitted_fields.call_args_list
                       if call.args[0] == "Employee Boarding Activity"]
        self.assertTrue(child_calls)
        self.assertTrue(all(call.get("parenttype") == "Employee Separation" for call in child_calls))

    def test_rpc_names_are_strings_and_submitted_source_company_select_stays_native(self):
        for name in (None, "", "  ", {}, {"name": "SOURCE"}, 123, True):
            self.frappe.get_doc.reset_mock()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "非空字符串"):
                self.module.finalize_separation(name)
            self.frappe.get_doc.assert_not_called()
        self.store[("Employee Separation", "SOURCE")].docstatus = 0
        with self.assertRaisesRegex(ValueError, "已提交"):
            self.finalize()
        self.store[("Employee Separation", "SOURCE")].docstatus = 1
        self.denied = {("Company", "COMPANY"): {"select"}}
        with self.assertRaises(PermissionError):
            self.finalize()

    def test_server_state_injection_and_project_task_alias_swaps_are_rejected(self):
        for field, value in (("jf_exit_snapshot", "{}"), ("jf_exit_status", "已办理"), ("project", "FOREIGN")):
            source = self.source()
            source[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                source.before_validate()
        source = self.source()
        source.activities[0].task = "OTHER"
        with self.assertRaises(ValueError):
            source.before_validate()

    def test_new_source_cannot_accept_client_server_state_or_prelinked_project(self):
        for field, value in (("jf_exit_snapshot", "{}"), ("jf_exit_status", "已办理"), ("project", "UNBOUND-PROJECT")):
            source = self.source()
            source.update(__islocal=1, project=None)
            source[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                source.before_validate()

    def test_frozen_intent_cannot_be_changed_even_after_reversal(self):
        self.finalize()
        for reverse in (False, True):
            if reverse:
                self.reverse()
            source = self.source()
            source.jf_reason_for_leaving = "NEW"
            with self.subTest(reversed=reverse), self.assertRaisesRegex(ValueError, "迁移"):
                source.before_update_after_submit()

    def test_full_activity_semantics_are_frozen_but_native_lifecycle_fields_are_excluded(self):
        self.finalize()
        source = self.source()
        for field, value in (("activity_name", "LATER"), ("role", "PRIVATE-ROLE"), ("description", "NEW")):
            changed = self.source()
            changed.activities[0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "迁移"):
                changed.before_update_after_submit()
        source.activities[0].modified = "NATIVE-SAVE-TIME"
        source.activities[0].docstatus = 1
        source.before_update_after_submit()

    def test_unapproved_pre_exit_wages_block_but_future_draft_does_not(self):
        self.store[("Jifeng Wage Entry", "DRAFT")] = Doc(doctype="Jifeng Wage Entry", name="DRAFT", employee="EMP",
            docstatus=0, work_date="2026-09-29", payroll_date="2026-09-30")
        with self.assertRaisesRegex(ValueError, "未审核"):
            self.finalize()
        self.store[("Jifeng Wage Entry", "DRAFT")].work_date = "2026-10-01"
        self.finalize()

    def test_later_job_ctc_history_or_employee_save_conflicts_preserve_current_values(self):
        self.finalize()
        baseline = deepcopy(self.store)
        for field, value in (("designation", "LATER"), ("ctc", 9999), ("modified", "LATER-SAVE")):
            self.store = deepcopy(baseline)
            self.store[("Employee", "EMP")][field] = value
            changed = deepcopy(self.store)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "后续变化"):
                self.reverse()
            self.assertEqual(self.store, changed)
        self.store = deepcopy(baseline)
        self.store[("Employee", "EMP")].internal_work_history[0].to_date = "2026-09-30"
        with self.assertRaises(ValueError):
            self.reverse()

    def test_all_four_payroll_parents_and_children_protect_reversal_without_leaking_payload(self):
        self.finalize()
        baseline = deepcopy(self.store)
        for doctype in self.module.PAYROLL_TYPES:
            self.store = deepcopy(baseline)
            self.store[(doctype, "PRIVATE-PAYROLL-ID")] = Doc(doctype=doctype, name="PRIVATE-PAYROLL-ID", employee="EMP",
                docstatus=1, amount="PRIVATE-AMOUNT", modified="NEW")
            with self.subTest(doctype=doctype), self.assertRaises(ValueError) as error:
                self.reverse()
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertNotIn("PRIVATE", self.store[("Employee Separation", "SOURCE")].jf_exit_snapshot)
        self.store = deepcopy(baseline)
        self.store[("Salary Slip", "OLD")] = Doc(doctype="Salary Slip", name="OLD", employee="EMP", docstatus=1)
        original = self.module._payroll_hash("EMP")
        self.store[("Payroll Child", "CHILD")] = Doc(doctype="Payroll Child", name="CHILD", parent="OLD",
            parenttype="Salary Slip", parentfield="detail", amount=100)
        self.assertNotEqual(original, self.module._payroll_hash("EMP"))

    def test_graph_reopen_or_additional_task_blocks_reversal_without_auto_reactivating(self):
        self.finalize()
        self.store[("Task", "TASK")].status = "Open"
        with self.assertRaises(ValueError):
            self.reverse()
        self.assertEqual(self.store[("Employee", "EMP")].status, "Left")

    def test_source_save_error_or_unexpected_native_task_is_atomically_rolled_back(self):
        for failure, extra in ((True, False), (False, True)):
            self.fail_source, self.extra_task = failure, extra
            baseline = deepcopy(self.store)
            with self.subTest(failure=failure, extra=extra), self.assertRaises(ValueError):
                self.finalize()
            self.assertEqual(self.store, baseline)
        self.assertEqual(self.user_calls, [])
        self.frappe.db.rollback.assert_called()

    def test_rpc_failure_restores_preexisting_callbacks_and_runs_only_local_rollback_cleanup(self):
        before = {}
        for queue in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            def callback(queue=queue):
                self.callback_calls.append("OLD-" + queue)
            getattr(self.frappe.db, queue).add(callback)
            before[queue] = [callback]
        self.fail_source = True
        baseline = deepcopy(self.store)
        with self.assertRaisesRegex(ValueError, "source save failed"):
            self.finalize()
        self.assertEqual(self.store, baseline)
        self.assertEqual(self.callback_calls, ["before_rollback", "after_rollback"])
        for queue, expected in before.items():
            self.assertEqual(list(getattr(self.frappe.db, queue)._functions), expected)

    def test_rpc_failure_preserves_earlier_realtime_messages_and_saving_stack(self):
        prior_message = ["doc_update", {"doctype": "Other", "name": "EARLIER"}, "earlier-room"]
        self.frappe.local._realtime_log = [deepcopy(prior_message)]
        self.frappe.flags.currently_saving = [("Other", "EARLIER")]
        old_flush = Mock()
        self.frappe.db.after_commit.add(old_flush)
        # Existing rollback watcher can clear the old log. A new native cleanup
        # that also clears it must still leave the caller's old messages intact.
        def clear_log():
            if hasattr(self.frappe.local, "_realtime_log"):
                del self.frappe.local._realtime_log
        self.frappe.db.after_rollback.add(clear_log)
        old_rollback = list(self.frappe.db.after_rollback._functions)
        self.fail_source = True
        with self.assertRaisesRegex(ValueError, "native source save failed"):
            self.finalize()
        self.assertEqual(self.frappe.local._realtime_log, [prior_message])
        self.assertEqual(self.frappe.flags.currently_saving, [("Other", "EARLIER")])
        self.assertEqual(list(self.frappe.db.after_commit._functions), [old_flush])
        self.assertEqual(list(self.frappe.db.after_rollback._functions), old_rollback)
        old_flush.assert_not_called()

    def test_rpc_failure_removes_its_log_and_true_missing_dict_get_saving_key(self):
        self.frappe.flags.pop("currently_saving")
        self.assertTrue(hasattr(self.frappe.flags, "currently_saving"))
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.fail_source = True
        with self.assertRaisesRegex(ValueError, "native source save failed"):
            self.finalize()
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))
        self.assertEqual(list(self.frappe.db.after_commit._functions), [])

    def test_success_keeps_native_realtime_and_callbacks_and_only_removes_finished_saving_rows(self):
        self.frappe.flags.currently_saving = [("Other", "EARLIER")]
        self.finalize()
        self.assertEqual(self.frappe.flags.currently_saving, [("Other", "EARLIER")])
        self.assertEqual({event[1]["doctype"] for event in self.frappe.local._realtime_log},
                         {"Employee", "Employee Separation"})
        self.assertTrue(self.frappe.db.after_commit._functions)
        self.assertTrue(self.frappe.db.after_rollback._functions)

    def test_cleanup_error_preserves_original_failure_and_still_rolls_back_business(self):
        @self.module._atomic
        def fail():
            self.store[("Employee", "EMP")].status = "Left"
            def broken_cleanup():
                raise RuntimeError("cleanup failed")
            self.frappe.db.before_rollback.add(broken_cleanup)
            raise ValueError("original business failure")
        baseline = deepcopy(self.store)
        with self.assertRaisesRegex(ValueError, "original business failure") as caught:
            fail()
        self.assertEqual(self.store, baseline)
        self.assertIn("before_rollback:RuntimeError", caught.exception.__notes__[0])

    def test_source_read_rechecks_employee_and_graph_field_permissions_without_locks(self):
        self.finalize()
        source = self.source()
        self.frappe.get_doc.reset_mock()
        self.assertIs(self.module.separation_has_permission(source, "read", user="reader"), True)
        self.assertTrue(all("for_update" not in call.kwargs for call in self.frappe.get_doc.call_args_list))
        for doctype, field in (("Employee", "ctc"), ("Employee Internal Work History", "to_date"), ("Project", "status"),
            ("Task", "completed_on"), ("Employee Separation", "jf_reason_for_leaving"),
            ("Employee Separation", "jf_exit_snapshot"), ("Employee Boarding Activity", "task")):
            self.field_denied = {(doctype, "read"): {field}}
            with self.subTest(doctype=doctype):
                self.assertIs(self.module.separation_has_permission(source, "read", user="reader"), False)
                self.assertEqual(self.module.separation_query_conditions(user="reader"), "1=0")
        self.field_denied.clear()
        self.denied = {("Task", "TASK"): {"read"}}
        self.assertIs(self.module.separation_has_permission(source, "write", user="reader"), False)
        self.denied.clear()
        self.source_table_level = 1
        self.assertIs(self.module.separation_has_permission(source, "read", user="reader"), False)
        self.source_table_level = 0
        self.store[("Task", "TASK")].project = "FOREIGN"
        self.assertIs(self.module.separation_has_permission(source, "read", user="reader"), False)

    def test_snapshot_read_can_show_history_after_current_tasks_are_cancelled(self):
        self.finalize()
        self.reverse()
        self.store[("Task", "TASK")].status = "Cancelled"
        self.store[("Project", "PROJECT")].status = "Cancelled"
        self.assertIs(self.module.separation_has_permission(self.source(), "read"), True)
        self.assertIn("'SOURCE'", self.module.separation_query_conditions(user="reader"))

    def test_snapshot_schema_rejects_extra_fields_bad_hash_duplicate_or_mismatched_tasks(self):
        self.finalize()
        original = json.loads(self.source().jf_exit_snapshot)
        mutations = (lambda data: data["changed_fields"].append("ctc"),
            lambda data: data["changed_fields"].append(data["changed_fields"][0]),
            lambda data: data.update(payroll_hash="z" * 64),
            lambda data: data["tasks"][0].update(activity="FOREIGN"),
            lambda data: data["source"].update(employee=123),
            lambda data: data["source"].update(activities_hash="bad"),
            lambda data: data["project"].update(modified=123),
            lambda data: data["tasks"][0].update(modified=None),
            lambda data: data["before"]["fields"].update(bank_ac_no="PRIVATE"))
        for mutate in mutations:
            data = deepcopy(original)
            mutate(data)
            with self.assertRaises(ValueError):
                self.module._parse_snapshot(json.dumps(data))

    def test_safe_native_cancel_preserves_exact_project_task_and_links(self):
        source = self.source()
        source.before_cancel()
        source.docstatus = 2
        self.store[(source.doctype, source.name)].docstatus = 2
        source.on_cancel()
        self.assertEqual(self.store[("Task", "TASK")].status, "Cancelled")
        self.assertEqual(self.store[("Project", "PROJECT")].status, "Cancelled")
        self.assertEqual(source.project, "PROJECT")
        self.assertEqual(source.activities[0].task, "TASK")
        self.assertEqual(self.employee_saves, 0)
        self.frappe.db.delete.assert_not_called()

    def test_finalized_source_requires_reverse_before_native_cancel(self):
        self.finalize()
        with self.assertRaisesRegex(ValueError, "先撤销"):
            self.source().before_cancel()
        self.reverse()
        self.source().before_cancel()

    def test_safe_cancel_requires_native_cancel_and_project_task_write_and_exact_scope(self):
        for doctype, name, permission in (("Employee Separation", "SOURCE", "cancel"), ("Project", "PROJECT", "write"), ("Task", "TASK", "write")):
            self.denied = {(doctype, name): {permission}}
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.source().before_cancel()
        self.denied.clear()
        self.store[("Task", "EXTRA")] = Doc(doctype="Task", name="EXTRA", project="PROJECT")
        with self.assertRaises(ValueError):
            self.source().before_cancel()

    def test_reversed_source_cancel_checks_task_conflicts_and_direct_callback_token(self):
        self.finalize()
        self.reverse()
        self.store[("Task", "TASK")].modified = "LATER"
        with self.assertRaisesRegex(ValueError, "后续变化"):
            self.source().before_cancel()
        with self.assertRaisesRegex(ValueError, "服务器锁定"):
            self.source().on_cancel()

    def test_empty_source_cancel_never_selects_or_deletes_unassigned_tasks(self):
        self.store[("Employee Separation", "SOURCE")].update(project=None, activities=[])
        self.store.pop(("Employee Boarding Activity", "ACTIVITY"))
        self.store[("Task", "UNASSIGNED")] = Doc(doctype="Task", name="UNASSIGNED", project=None, status="Open")
        source = self.source()
        source.before_cancel()
        source.on_cancel()
        self.assertEqual(self.store[("Task", "UNASSIGNED")].status, "Open")
        self.frappe.db.delete.assert_not_called()

    def test_empty_activity_submission_is_rejected_before_creating_a_project(self):
        source = self.source()
        source.activities = []
        before = deepcopy(self.store)
        with self.assertRaisesRegex(ValueError, "先填写离职交接活动"):
            source.before_submit()
        self.assertEqual(self.store, before)

    def test_pending_empty_owned_project_can_cancel_without_touching_unrelated_tasks(self):
        self.store[("Employee Separation", "SOURCE")].activities = []
        self.store.pop(("Employee Boarding Activity", "ACTIVITY"))
        self.store.pop(("Task", "TASK"))
        self.store[("Task", "UNASSIGNED")] = Doc(doctype="Task", name="UNASSIGNED", project=None, status="Open")
        source = self.source()
        source.before_cancel()
        source.on_cancel()
        self.assertEqual(self.store[("Project", "PROJECT")].status, "Cancelled")
        self.assertEqual(self.store[("Task", "UNASSIGNED")].status, "Open")
        self.frappe.db.delete.assert_not_called()

    def test_empty_project_cancel_rejects_any_actual_task_and_cannot_finalize(self):
        self.store[("Employee Separation", "SOURCE")].activities = []
        self.store.pop(("Employee Boarding Activity", "ACTIVITY"))
        before = deepcopy(self.store)
        with self.assertRaises(ValueError):
            self.source().before_cancel()
        self.assertEqual(self.store, before)
        with self.assertRaises(ValueError):
            self.finalize()

    def test_submitted_pending_activity_cannot_clear_replace_or_remove_persisted_task(self):
        for mutation in (lambda source: setattr(source.activities[0], "task", ""),
                         lambda source: setattr(source.activities[0], "task", "OTHER"),
                         lambda source: setattr(source, "activities", [])):
            source = self.source()
            mutation(source)
            baseline = deepcopy(self.store)
            with self.assertRaisesRegex(ValueError, "不能移除、清空或更换"):
                source.save()
            self.assertEqual(self.store, baseline)

    def test_submitted_pending_activity_can_append_new_native_taskless_activity(self):
        source = self.source()
        source.activities.append(Doc(name="NEW-ACTIVITY", task=None))
        source.before_update_after_submit()
        self.assertEqual(source.activities[0].task, "TASK")
        self.assertIsNone(source.activities[1].task)
        self.assertEqual(self.native_validations, 0)

    def test_amended_submitted_lifecycle_preserves_exact_task_links_via_native_after_submit(self):
        original = deepcopy(self.store[("Employee Separation", "SOURCE")])
        original.update(name="ORIGINAL", docstatus=2, project="ORIGINAL-PROJECT", activities=[])
        self.store[("Employee Separation", "ORIGINAL")] = original
        self.store[("Employee Separation", "SOURCE")].amended_from = "ORIGINAL"
        baseline = deepcopy(original)
        self.finalize()
        snapshot = json.loads(self.store[("Employee Separation", "SOURCE")].jf_exit_snapshot)
        self.assertEqual(snapshot["source"]["amended_from"], "ORIGINAL")
        self.reverse()
        self.assertEqual(self.source().activities[0].task, "TASK")
        self.assertEqual(self.store[("Employee Separation", "ORIGINAL")], baseline)
        self.assertEqual(self.native_validations, 0)
        self.assertEqual(self.store[("Employee", "EMP")].status, "Active")

    def test_amended_draft_still_runs_native_task_reset_without_restoring_submitted_links(self):
        original = deepcopy(self.store[("Employee Separation", "SOURCE")])
        original.update(name="ORIGINAL", docstatus=2)
        self.store[("Employee Separation", "ORIGINAL")] = original
        self.store[("Employee Separation", "SOURCE")].update(docstatus=0, amended_from="ORIGINAL")
        source = self.source()
        source.__islocal = 1
        source.before_validate()
        source.validate()
        self.assertEqual(source.activities[0].task, "")
        self.assertEqual(self.native_validations, 1)

    def test_amended_origin_must_be_cancelled_readable_and_same_employee_company(self):
        original = deepcopy(self.store[("Employee Separation", "SOURCE")])
        original.update(name="ORIGINAL", docstatus=2)
        self.store[("Employee Separation", "SOURCE")].amended_from = "ORIGINAL"
        for changed in ({"docstatus": 1}, {"employee": "OTHER"}, {"company": "OTHER"}):
            self.store[("Employee Separation", "ORIGINAL")] = Doc(deepcopy(original), **changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "同员工、同公司"):
                self.source().before_update_after_submit()
        self.store[("Employee Separation", "ORIGINAL")] = original
        self.denied[("Employee Separation", "ORIGINAL")] = {"read"}
        with self.assertRaises(PermissionError):
            self.source().before_update_after_submit()
        self.assertEqual(self.native_validations, 0)

    def test_native_desk_amend_copy_is_normalized_without_reusing_snapshot_project_or_tasks(self):
        self.finalize()
        self.reverse()
        original = self.source()
        original.docstatus = 2
        self.store[("Employee Separation", "SOURCE")].docstatus = 2
        copy = self.Source(original.as_dict(), flags=Doc(), meta=self.source_meta)
        copy.update(name="COPY", __islocal=1, docstatus=0, amended_from="SOURCE")
        baseline = deepcopy(self.store)
        copy.before_validate()
        copy.validate()
        self.assertIsNone(copy.project)
        self.assertIsNone(copy.jf_exit_snapshot)
        self.assertEqual(copy.jf_exit_status, "待办理")
        self.assertEqual(copy.activities[0].task, "")
        self.assertEqual(self.store, baseline)
        self.assertEqual(self.native_validations, 1)

    def test_native_duplicate_resets_only_links_from_the_exact_readable_same_employee_source(self):
        copy = self.source()
        copy.update(name="COPY", __islocal=1, docstatus=0)
        baseline = deepcopy(self.store)
        copy.before_validate()
        self.assertIsNone(copy.project)
        self.assertEqual(copy.activities[0].task, "")
        self.assertEqual(self.store, baseline)
        copy = self.source()
        copy.update(name="COPY", __islocal=1, docstatus=0, employee="OTHER")
        with self.assertRaisesRegex(ValueError, "同员工、同公司"):
            copy.before_validate()

    def test_amend_copy_rejects_forged_snapshot_task_or_project_instead_of_silently_resetting(self):
        self.store[("Employee Separation", "SOURCE")].docstatus = 2
        for changed in ({"project": "OTHER"}, {"jf_exit_snapshot": "forged"}, {"jf_exit_status": "FORGED"}):
            copy = self.source()
            copy.update(name="COPY", __islocal=1, docstatus=0, amended_from="SOURCE", **changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                copy.before_validate()
        copy = self.source()
        copy.update(name="COPY", __islocal=1, docstatus=0, amended_from="SOURCE")
        copy.activities[0].task = "OTHER"
        with self.assertRaisesRegex(ValueError, "实际来源不一致"):
            copy.before_validate()


if __name__ == "__main__":
    unittest.main()
