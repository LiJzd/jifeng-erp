"""Model tests; the separate rollback runner verifies real native controllers."""

import ast
from copy import deepcopy
from datetime import date
from functools import wraps
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, fn):
        self._functions.append(fn)

    def reset(self):
        self._functions.clear()


class Meta:
    def __init__(self, fields, environment, doctype):
        self.fields, self.environment, self.doctype = fields, environment, doctype

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)

    def get_permlevel_access(self, permission_type):
        return self.environment.levels.get((self.doctype, permission_type), [0])


class NativeDocument(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.metas[self.doctype]

    def has_permission(self, permission):
        return ((self.doctype, permission) not in self.environment.denials
                and (self.doctype, self.name, permission) not in self.environment.document_denials
                and not (permission == "create" and (self.company in self.environment.denied_companies
                         or self.department in self.environment.denied_departments)))

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native " + self.doctype + " " + permission)

    def set(self, field, value):
        self[field] = value

    def is_new(self):
        return bool(self.get("__islocal"))

    def as_dict(self):
        return dict(self)

    def append(self, field, values):
        doctype = self.meta.get_field(field).options
        row = NativeDocument(doctype=doctype, name=None, parent=self.name,
                             parenttype=self.doctype, parentfield=field, **values)
        self.setdefault(field, []).append(row)
        return row

    def save(self):
        env = self.environment
        self.check_permission("create" if self.is_new() else "write")
        if self.doctype == "Jifeng Employee Supplement" and env.fail_supplement:
            raise ValueError("supplement native failure")
        if not self.name:
            self.name = "EMP-NEW" if self.doctype == "Employee" else self.employee
        if self.doctype == "Employee":
            self.employee_name = " ".join(filter(None, [self.first_name, self.middle_name, self.last_name]))
            if self.user_id:
                self.update_user()
                self.update_user_permissions()
                self.update_user_status()
            if env.approver_side_effect:
                env.users[self.leave_approver]["roles"].append("Unexpected Approver")
        env.tick += 1
        self.modified = "2026-10-01T01:00:" + str(env.tick).zfill(2)
        self["__islocal"] = 0
        for field in self.meta.fields:
            if field.fieldtype == "Table":
                for index, row in enumerate(self.get(field.fieldname) or []):
                    row.name = row.name or "ROW-NEW-" + str(index)
                    row.parent, row.parenttype, row.parentfield = self.name, self.doctype, field.fieldname
        env.documents[(self.doctype, self.name)] = deepcopy(dict(self))
        return self

    def update_user(self):
        self.environment.user_calls.append("profile")
        self.environment.users[self.user_id]["first_name"] = self.first_name

    def update_user_permissions(self):
        self.environment.user_calls.append("permissions")
        self.environment.user_permissions.append({"user": self.user_id, "allow": "Employee"})

    def update_user_status(self):
        self.environment.user_calls.append("status")
        self.environment.users[self.user_id]["enabled"] = int(self.status == "Active")


class TestPersonnelArchive(unittest.TestCase):
    def setUp(self):
        self.documents, self.users, self.user_permissions, self.auth = {}, {}, [], []
        self.denials, self.hidden, self.levels = set(), {}, {}
        self.document_denials, self.list_hidden = set(), set()
        self.denied_companies, self.default_company = set(), None
        self.denied_departments, self.default_department = set(), None
        self.fail_supplement = self.approver_side_effect = False
        self.user_calls, self.tick = [], 1
        self.metas = {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = PermissionError, RuntimeError
        self.frappe.DoesNotExistError = KeyError
        self.frappe.session = Record(user="hr-manager@example.invalid")
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.throw = lambda text, error=ValueError: (_ for _ in ()).throw(error(text))
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.share = types.SimpleNamespace(get_shared=lambda *a, **k: [])
        self.frappe.get_meta = lambda doctype: self.metas[doctype]
        self.frappe.has_permission = lambda doctype, permission: (doctype, permission) not in self.denials
        self.frappe.get_doc, self.frappe.new_doc = Mock(side_effect=self.get_doc), Mock(side_effect=self.new_doc)
        self.frappe.get_all = Mock(side_effect=lambda doctype, **kwargs: deepcopy(self.user_permissions))
        self.frappe.get_list = Mock(side_effect=self.list_records)
        self.snapshots = {}
        self.db = types.SimpleNamespace(get_value=Mock(side_effect=self.get_value),
            sql=Mock(side_effect=lambda *a, **k: deepcopy(self.auth)),
            savepoint=self.savepoint, rollback=self.rollback,
            **{name: Queue() for name in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        self.frappe.db = self.db
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: date.fromisoformat(str(value))
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        # Execute the actual production transaction decorator, not a mirrored
        # test implementation of savepoint/callback rollback.
        tree = ast.parse((ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_atomic")
        namespace = {"frappe": self.frappe, "wraps": wraps, "deepcopy": deepcopy, "uuid4": uuid4}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "native_atomic", "exec"), namespace)
        separation._atomic = namespace["_atomic"]
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
                                     "frappe.utils": utils, "jifeng_erp.personnel_separation": separation}):
            spec = importlib.util.spec_from_file_location("personnel_archive_under_test", ROOT / "jifeng_erp/personnel_archive.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.Employee = type("Employee", (self.module.ClassicPersonnelEmployeeMixin, NativeDocument), {})
        NativeDocument.environment = self
        self.employee_fields()
        for doctype, date_field in self.module.FORMAL_RECORDS.items():
            names = ["name", "employee", "docstatus", date_field]
            if doctype == "Employee Separation":
                names.append("jf_exit_status")
            self.metas[doctype] = Meta([Record(fieldname=name, fieldtype="Data") for name in names], self, doctype)
        for folder in ("jifeng_employee_supplement", "jifeng_legacy_work_history",
                       "jifeng_legacy_salary_history", "jifeng_legacy_promotion_history", "jifeng_legacy_education_history"):
            path = ROOT / "jifeng_erp/jifeng_erp/doctype" / folder / (folder + ".json")
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.metas[schema["name"]] = Meta([Record(field) for field in schema["fields"]], self, schema["name"])
        self.add("Company", "JF")
        self.add("Company", "OTHER")
        self.add("Department", "DEPT", company="JF")
        self.add("Department", "FOREIGN", company="OTHER")
        self.add("Gender", "Male")
        self.employee = self.add("Employee", "EMP-1", company="JF", status="Active", first_name="甲",
            employee_name="甲", user_id="linked@example.invalid", create_user_permission=1,
            create_user_automatically=0, date_of_birth="1990-01-01", date_of_joining="2020-01-01",
            ctc=99000, education=[{"qualification": "untouched"}], internal_work_history=[{"name": "INTERNAL"}],
            external_work_history=[{"name": "EXTERNAL"}], unknown_field="KEEP")
        self.supplement = self.add(self.module.SUPPLEMENT, "SUPP-OLD-NAME", employee="EMP-1", company="JF",
            term_type="长期", notes="原备注", unknown_field="KEEP-SUPP", work_history=[NativeDocument(
                doctype="Jifeng Legacy Work History", name="ROW-1", parent="SUPP-OLD-NAME",
                parenttype=self.module.SUPPLEMENT, parentfield="work_history", title="旧岗位", project="旧项目",
                unknown_field="KEEP-ROW")])
        self.users["linked@example.invalid"] = {"name": "linked@example.invalid", "enabled": 0,
            "first_name": "user-profile", "roles": ["Employee"], "role_profile_name": "PROFILE"}
        self.auth = [{"doctype": "User", "name": "linked@example.invalid", "password": "TEST-HASH"}]
        self.user_permissions = [{"name": "UP", "user": "linked@example.invalid", "allow": "Employee", "for_value": "EMP-1"}]

    def employee_fields(self):
        fields = []
        for name in self.module.EMPLOYEE_READ | self.module.LIFECYCLE:
            kind, options = "Data", ""
            if name in ("company", "department", "designation", "gender"):
                kind, options = "Link", {"company": "Company", "department": "Department", "designation": "Designation", "gender": "Gender"}[name]
            elif name in ("date_of_birth", "date_of_joining", "relieving_date"):
                kind = "Date"
            elif name == "image":
                kind = "Attach Image"
            fields.append(Record(fieldname=name, label=name, fieldtype=kind, options=options,
                                 read_only=name == "employee_name", reqd=name in ("first_name", "gender", "date_of_birth", "company")))
        self.metas["Employee"] = Meta(fields, self, "Employee")

    def add(self, doctype, name, **values):
        values.update(doctype=doctype, name=name, modified="2026-10-01T00:00:00", __islocal=0)
        self.documents[(doctype, name)] = values
        return values

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "User":
            return NativeDocument(deepcopy(self.users[name]))
        return (self.Employee if doctype == "Employee" else NativeDocument)(deepcopy(self.documents[(doctype, name)]))

    def new_doc(self, doctype):
        return (self.Employee if doctype == "Employee" else NativeDocument)(doctype=doctype, name=None,
            __islocal=1, status="Active", company=self.default_company, department=self.default_department,
            create_user_permission=1, create_user_automatically=0)

    def get_value(self, doctype, filters, field):
        if doctype == "File":
            for (kind, name), document in self.documents.items():
                if kind == "File" and all(document.get(key) == value for key, value in filters.items()):
                    return name
            return None
        return next((name for (kind, name), doc in self.documents.items()
                     if kind == doctype and all(doc.get(key) == value for key, value in filters.items())), None)

    def list_records(self, doctype, filters, fields, **kwargs):
        readable = self.columns(doctype, "read")
        return [Record({field: deepcopy(doc.get(field)) for field in fields if field in readable})
                for (kind, name), doc in self.documents.items() if kind == doctype
                and (doctype, name) not in self.list_hidden
                and all(doc.get(key) == value for key, value in filters.items())]

    def columns(self, doctype, permission_type, **kwargs):
        return {field.fieldname for field in self.metas[doctype].fields if field.fieldtype != "Table"} - self.hidden.get((doctype, permission_type), set())

    def state(self):
        return deepcopy((self.documents, self.users, self.user_permissions, self.auth))

    def savepoint(self, name):
        self.snapshots[name] = self.state()

    def rollback(self, save_point):
        self.documents, self.users, self.user_permissions, self.auth = deepcopy(self.snapshots[save_point])

    def save(self, **overrides):
        payload = {"name": "EMP-1", "employee_modified": self.documents[("Employee", "EMP-1")]["modified"],
            "supplement_modified": self.documents[(self.module.SUPPLEMENT, "SUPP-OLD-NAME")]["modified"], **overrides}
        return self.module.save_archive(payload)

    def formal_record(self, doctype="Employee Transfer", name="TRANSFER-1", **values):
        return self.add(doctype, name, employee="EMP-1", docstatus=1, **values)

    def test_formal_records_keep_exact_native_references_and_cancelled_history_separate(self):
        self.formal_record(transfer_date="2026-09-01")
        self.formal_record("Employee Promotion", "PROMOTION-1", promotion_date="2026-08-01")
        cancelled = self.formal_record("Employee Promotion", "PROMOTION-CANCEL", promotion_date="2026-07-01")
        cancelled["docstatus"] = 2
        draft = self.formal_record(name="TRANSFER-DRAFT", transfer_date="2026-10-01")
        draft["docstatus"] = 0
        before = self.state()
        result = self.module.read_archive("EMP-1")
        self.assertEqual([row["name"] for row in result["formal_records"]],
                         ["TRANSFER-DRAFT", "TRANSFER-1", "PROMOTION-1", "PROMOTION-CANCEL"])
        self.assertEqual([row["docstatus"] for row in result["formal_records"]], [0, 1, 1, 2])
        self.assertEqual(result["histories"]["work_history"][0]["name"], "ROW-1")
        self.assertEqual(result["employee"]["status"], "Active")
        self.assertEqual(self.state(), before, "history reading does not save Employee or legacy rows")
        for call in self.frappe.get_list.call_args_list:
            self.assertEqual(call.kwargs["filters"], {"employee": "EMP-1"})
            self.assertEqual(call.kwargs["limit_page_length"], 0)

    def test_formal_separation_phase_and_native_docstatus_are_both_kept(self):
        source = self.formal_record("Employee Separation", "EXIT-1", jf_last_working_day="2026-09-30",
                                    jf_exit_status="已办理")
        self.employee["status"] = "Left"
        result = self.module.read_archive("EMP-1")
        self.assertEqual(result["formal_records"][0]["exit_status"], "已办理")
        self.assertEqual(result["employee"]["status"], "Left")
        source["jf_exit_status"], source["docstatus"], self.employee["status"] = "已撤销", 2, "Active"
        result = self.module.read_archive("EMP-1")
        self.assertEqual(result["formal_records"][0]["docstatus"], 2)
        self.assertEqual(result["formal_records"][0]["exit_status"], "已撤销")
        self.assertEqual(result["employee"]["status"], "Active")

    def test_formal_history_applies_source_list_scope_and_current_document_read(self):
        self.formal_record(name="VISIBLE", transfer_date="2026-09-01")
        self.formal_record(name="LIST-HIDDEN", transfer_date="2026-09-01")
        self.formal_record(name="DOC-HIDDEN", transfer_date="2026-09-01")
        other = self.formal_record(name="OTHER-EMPLOYEE", transfer_date="2026-09-01")
        other["employee"] = "EMP-OTHER"
        self.list_hidden.add(("Employee Transfer", "LIST-HIDDEN"))
        self.document_denials.add(("Employee Transfer", "DOC-HIDDEN", "read"))
        result = self.module.read_archive("EMP-1")
        self.assertEqual([row["name"] for row in result["formal_records"]], ["VISIBLE"])
        self.assertNotIn(("Employee Transfer", "LIST-HIDDEN"), [call.args for call in self.frappe.get_doc.call_args_list])

    def test_formal_history_type_denial_does_not_block_the_readable_archive(self):
        self.formal_record(transfer_date="2026-09-01")
        self.denials.add(("Employee Transfer", "read"))
        result = self.module.read_archive("EMP-1")
        self.assertEqual(result["formal_records"], [])
        self.assertEqual(result["name"], "EMP-1")
        self.assertNotIn("Employee Transfer", [call.args[0] for call in self.frappe.get_list.call_args_list])

    def test_formal_history_field_projection_never_refills_unreadable_values(self):
        self.formal_record("Employee Separation", "EXIT-1", jf_last_working_day="2026-09-30",
                           jf_exit_status="已办理")
        self.hidden[("Employee Separation", "read")] = {"jf_last_working_day", "jf_exit_status"}
        result = self.module.read_archive("EMP-1")["formal_records"]
        self.assertEqual(result, [{"doctype": "Employee Separation", "name": "EXIT-1", "docstatus": 1}])
        self.hidden[("Employee Separation", "read")] = {"employee"}
        self.assertEqual(self.module.read_archive("EMP-1")["formal_records"], [])

    def test_formal_source_changes_between_list_and_load_do_not_show_stale_state_or_scope(self):
        source = self.formal_record(transfer_date="2026-09-01")
        native_list = self.list_records
        for changed in ({"employee": "EMP-OTHER"}, {"docstatus": 2}):
            source.update(employee="EMP-1", docstatus=1)
            def changed_after_list(doctype, **kwargs):
                rows = native_list(doctype, **kwargs)
                if doctype == "Employee Transfer":
                    source.update(changed)
                return rows
            self.frappe.get_list.side_effect = changed_after_list
            self.assertEqual(self.module.read_archive("EMP-1")["formal_records"], [])

    def test_formal_records_cannot_be_written_as_legacy_history_and_new_forms_do_not_query_sources(self):
        self.assertEqual(self.module.init_archive(company="JF")["formal_records"], [])
        self.frappe.get_list.assert_not_called()
        with self.assertRaisesRegex(ValueError, "不支持"):
            self.save(formal_records=[{"name": "FORGED", "docstatus": 2}])

    def test_schema_keeps_numeric_and_three_exact_old_tables(self):
        meta = self.metas[self.module.SUPPLEMENT]
        self.assertEqual(meta.get_field("legacy_base_salary").fieldtype, "Currency")
        self.assertEqual(meta.get_field("legacy_time_value").fieldtype, "Float")
        for field, (doctype, columns) in self.module.HISTORIES.items():
            self.assertEqual(meta.get_field(field).options, doctype)
            self.assertEqual(tuple(f.fieldname for f in self.metas[doctype].fields), columns)

    def test_init_defaults_empty_method_long_term_and_explicit_company(self):
        result = self.module.init_archive()
        self.assertIsNone(result["employee"]["company"])
        self.assertEqual(result["employee"]["status"], "Active")
        self.assertIsNone(result["supplement"]["wage_method"])
        self.assertEqual(result["supplement"]["term_type"], "长期")
        self.assertTrue(result["field_meta"]["employee"]["gender"]["reqd"])
        self.assertFalse(result["field_meta"]["histories"]["work_history"]["read_only"])

    def test_temporary_initializer_is_visible_unsaved_and_does_not_change_existing_archive(self):
        before = self.state()
        result = self.module.init_archive("JF", term_type="临时")
        self.assertEqual(result["supplement"]["term_type"], "临时")
        self.assertEqual(result["employee"]["company"], "JF")
        self.assertIsNone(result["name"])
        self.assertFalse(result["field_meta"]["supplement"]["term_type"]["read_only"])
        self.assertEqual(self.state(), before)

    def test_temporary_initializer_cannot_hide_a_denied_type_or_accept_unknown_type(self):
        for permission in ("read", "write"):
            with self.subTest(permission=permission):
                self.hidden[(self.module.SUPPLEMENT, permission)] = {"term_type"}
                with self.assertRaises(PermissionError):
                    self.module.init_archive(term_type="临时")
                self.hidden.clear()
        with self.assertRaisesRegex(ValueError, "人员类型"):
            self.module.init_archive(term_type="Full-time")

    def test_explicit_new_temporary_type_survives_normal_archive_save_without_account_changes(self):
        initialized = self.module.init_archive("JF", term_type="临时")
        account_before = deepcopy((self.users, self.user_permissions, self.auth))
        result = self.module.save_archive({"employee": {"company": "JF", "first_name": "临工",
            "gender": "Male", "date_of_birth": "1990-01-01", "date_of_joining": "2026-10-01"},
            "supplement": {"term_type": initialized["supplement"]["term_type"]}})
        self.assertEqual(result["supplement"]["term_type"], "临时")
        self.assertEqual(result["employee"]["status"], "Active")
        self.assertEqual(self.documents[(self.module.SUPPLEMENT, "SUPP-OLD-NAME")]["term_type"], "长期")
        self.assertEqual((self.users, self.user_permissions, self.auth), account_before)

    def test_new_company_is_resolved_before_native_document_create_permissions(self):
        self.default_company, self.denied_companies = "OTHER", {"OTHER"}
        self.assertTrue(self.module.init_archive()["can_write"])
        explicit = self.module.init_archive("JF")
        self.assertTrue(explicit["can_write"])
        self.assertEqual(explicit["employee"]["company"], "JF")
        with self.assertRaises(PermissionError):
            self.module.init_archive("OTHER")
        with self.assertRaises(PermissionError):
            self.module.save_archive({"employee": {"company": "OTHER", "first_name": "拒绝"}})
        result = self.module.save_archive({"employee": {"company": "JF", "first_name": "许可公司"}})
        self.assertEqual(result["employee"]["company"], "JF")
        self.assertEqual(self.documents[(self.module.SUPPLEMENT, result["supplement_name"])]["company"], "JF")

    def test_empty_init_still_requires_both_create_roles(self):
        for doctype in ("Employee", self.module.SUPPLEMENT):
            with self.subTest(doctype=doctype):
                self.denials = {(doctype, "create")}
                with self.assertRaises(PermissionError):
                    self.module.init_archive()

    def test_new_explicit_department_precedes_both_full_document_create_checks(self):
        self.default_department, self.denied_departments = "FOREIGN", {"FOREIGN"}
        result = self.module.save_archive({"employee": {"company": "JF", "department": "DEPT", "first_name": "许可部门"}})
        self.assertEqual(result["employee"]["department"], "DEPT")
        self.assertEqual(self.documents[(self.module.SUPPLEMENT, result["supplement_name"])]["department"], "DEPT")

    def test_missing_supplement_uses_existing_employee_company_before_create_check(self):
        del self.documents[(self.module.SUPPLEMENT, "SUPP-OLD-NAME")]
        self.default_company, self.denied_companies = "OTHER", {"OTHER"}
        result = self.module.read_archive("EMP-1")
        self.assertTrue(result["can_write"])
        self.assertIsNone(result["supplement_name"])

    def test_native_child_oracle_allows_only_parent_matched_modified_fields(self):
        source = ast.parse((ROOT / "jifeng_erp/personnel_archive_validation.py").read_text(encoding="utf-8"))
        definition = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                          and node.name == "_assert_native_history_preserved")
        namespace = {}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "native_child_oracle", "exec"), namespace)
        verify = namespace["_assert_native_history_preserved"]
        old = {field: [dict(name="ROW", doctype="Native Child", parent="EMP-1", parenttype="Employee",
            parentfield=field, idx=1, owner="original-owner", creation="original-time", unknown_business="KEEP",
            modified="old-time", modified_by="old-actor")] for field in
            ("education", "external_work_history", "internal_work_history")}
        second = deepcopy(old["education"][0])
        second.update(name="ROW-2", idx=2, unknown_business="KEEP-2")
        old["education"].append(second)
        new = deepcopy(old)
        new.update(modified="new-time", modified_by="new-actor")
        for field in old:
            for row in new[field]:
                row.update(modified=new["modified"], modified_by=new["modified_by"])
        verify(old, new)
        for fieldname in ("name", "doctype", "parent", "parenttype", "parentfield", "idx", "owner", "creation",
                          "unknown_business", "modified", "modified_by"):
            changed = deepcopy(new)
            changed["education"][0][fieldname] = "CHANGED"
            with self.subTest(field=fieldname), self.assertRaises(AssertionError):
                verify(old, changed)
        removed = deepcopy(new)
        removed["education"] = []
        with self.assertRaises(AssertionError):
            verify(old, removed)
        reordered = deepcopy(new)
        reordered["education"].reverse()
        with self.assertRaises(AssertionError):
            verify(old, reordered)

    def test_old_amounts_and_text_do_not_change_ctc_or_formal_records(self):
        result = self.save(supplement={"legacy_base_salary": "1800.25", "legacy_time_value": "12.5",
            "legacy_account": "00001234", "legacy_title": "旧职称", "legacy_city": "城市",
            "legacy_notes_text": "另一个备注", "legacy_marital_status": "原文"},
            histories={"salary_history": [{"project": "旧项目", "content": "内容", "wage": "2500.5"}],
                       "promotion_history": [{"project": "旧项目", "title": "旧升职"}]})
        self.assertEqual(result["supplement_name"], "SUPP-OLD-NAME")
        self.assertEqual(result["supplement"]["legacy_base_salary"], 1800.25)
        self.assertEqual(result["supplement"]["legacy_time_value"], 12.5)
        self.assertEqual(result["supplement"]["notes"], "原备注")
        self.assertEqual(self.documents[("Employee", "EMP-1")]["ctc"], 99000)
        self.assertEqual({kind for kind, _ in self.documents} & {"Employee Promotion", "Salary Structure Assignment"}, set())

    def test_old_education_fields_stable_identity_and_native_education_preserved(self):
        before = deepcopy(self.documents[("Employee", "EMP-1")]["education"])
        result = self.save(histories={"education_history": [{"project": "旧项目", "study_content": "学习内容",
            "from_date": "2000-01-01", "to_date": "2004-01-01", "school": "旧学校", "title": "原职务"}]})
        row = result["histories"]["education_history"][0]
        self.assertTrue(row["name"])
        updated = self.save(histories={"education_history": [{"name": row["name"], "school": "新学校"}]})
        after = updated["histories"]["education_history"][0]
        self.assertEqual(after["name"], row["name"])
        self.assertEqual(after["project"], "旧项目")
        self.assertEqual(after["study_content"], "学习内容")
        self.assertEqual(after["school"], "新学校")
        self.assertEqual(self.documents[("Employee", "EMP-1")]["education"], before)

    def test_native_linked_user_methods_are_skipped_only_for_exact_api_object(self):
        before = deepcopy((self.users, self.auth, self.user_permissions))
        self.save(employee={"first_name": "新姓名", "date_of_birth": "1991-01-02"})
        self.assertEqual((self.users, self.auth, self.user_permissions), before)
        self.assertEqual(self.user_calls, [])
        other = self.get_doc("Employee", "EMP-1")
        other.update_user_status()
        self.assertEqual(self.user_calls, ["status"])
        self.assertEqual(self.users[other.user_id]["enabled"], 1)

    def test_second_document_failure_rolls_back_employee_and_restores_callbacks(self):
        before = self.state()
        def old():
            pass
        self.db.after_commit.add(old)
        self.fail_supplement = True
        with self.assertRaisesRegex(ValueError, "supplement native failure"):
            self.save(employee={"first_name": "不应留下"})
        self.assertEqual(self.state(), before)
        self.assertEqual(self.db.after_commit._functions, [old])
        self.assertFalse(self.module.is_archive_save(self.get_doc("Employee", "EMP-1")))

    def test_unexpected_approver_role_change_rejects_and_rolls_back_not_repaired(self):
        self.employee["leave_approver"] = "linked@example.invalid"
        self.approver_side_effect = True
        before = self.state()
        with self.assertRaisesRegex(ValueError, "关联账号"):
            self.save(employee={"first_name": "失败"})
        self.assertEqual(self.state(), before)

    def test_unknown_native_fields_and_history_ids_survive_patch(self):
        before = deepcopy(self.employee)
        self.save(histories={"work_history": [{"name": "ROW-1", "work_content": "新增内容"}]})
        after = self.documents[("Employee", "EMP-1")]
        for field in ("unknown_field", "education", "internal_work_history", "external_work_history"):
            self.assertEqual(after[field], before[field])
        row = self.documents[(self.module.SUPPLEMENT, "SUPP-OLD-NAME")]["work_history"][0]
        self.assertEqual((row.name, row.project, row.unknown_field), ("ROW-1", "旧项目", "KEEP-ROW"))

    def test_explicit_history_order_is_saved_with_original_row_ids(self):
        row = deepcopy(self.supplement["work_history"][0])
        row.name, row.idx = "ROW-2", 2
        self.supplement["work_history"].append(row)
        self.save(histories={"work_history": [{"name": "ROW-2"}, {"name": "ROW-1"}]})
        rows = self.documents[(self.module.SUPPLEMENT, "SUPP-OLD-NAME")]["work_history"]
        self.assertEqual([(row.name, row.idx) for row in rows], [("ROW-2", 1), ("ROW-1", 2)])

    def test_native_employee_and_supplement_permissions_are_both_required(self):
        for doctype in ("Employee", self.module.SUPPLEMENT):
            self.denials = {(doctype, "write")}
            before = self.state()
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.save(employee={"first_name": "失败"})
            self.assertEqual(self.state(), before)

    def test_new_employee_cannot_be_written_with_supplement_create_denied(self):
        self.denials.add((self.module.SUPPLEMENT, "create"))
        before = self.state()
        with self.assertRaises(PermissionError):
            self.module.save_archive({"employee": {"company": "JF", "first_name": "新员工"}})
        self.assertEqual(self.state(), before)

    def test_stale_employee_or_supplement_timestamp_denies_before_save(self):
        for field in ("employee_modified", "supplement_modified"):
            before = self.state()
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.save(**{field: "stale"}, employee={"first_name": "失败"})
            self.assertEqual(self.state(), before)

    def test_company_transfer_and_foreign_department_are_not_archive_edits(self):
        for values in ({"company": "OTHER"}, {"department": "FOREIGN"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.save(employee=values)
        self.denials.add(("Company", "select"))
        with self.assertRaises(PermissionError):
            self.module.init_archive("JF")

    def test_read_denials_and_guest_do_not_return_private_supplement_fields(self):
        for doctype in ("Employee", self.module.SUPPLEMENT):
            self.denials = {(doctype, "read")}
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.module.read_archive("EMP-1")
        self.denials.clear()
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.module.read_archive("EMP-1")

    def test_field_read_is_omitted_and_field_write_is_read_only(self):
        self.hidden[(self.module.SUPPLEMENT, "read")] = {"identity_number"}
        self.hidden[(self.module.SUPPLEMENT, "write")] = {"legacy_base_salary"}
        result = self.module.read_archive("EMP-1")
        self.assertNotIn("identity_number", result["supplement"])
        self.assertNotIn("identity_number", result["field_meta"]["supplement"])
        self.assertTrue(result["field_meta"]["supplement"]["legacy_base_salary"]["read_only"])
        with self.assertRaises(PermissionError):
            self.save(supplement={"legacy_base_salary": 10})

    def test_history_parent_and_child_permissions_apply_to_output_and_patch(self):
        self.hidden[("Jifeng Legacy Work History", "read")] = {"project"}
        self.hidden[("Jifeng Legacy Work History", "write")] = {"title"}
        result = self.module.read_archive("EMP-1")
        self.assertNotIn("project", result["histories"]["work_history"][0])
        self.assertTrue(result["field_meta"]["histories"]["work_history"]["fields"]["title"]["read_only"])
        with self.assertRaises(PermissionError):
            self.save(histories={"work_history": [{"name": "ROW-1", "title": "失败"}]})
        self.levels[(self.module.SUPPLEMENT, "read")] = []
        self.assertNotIn("work_history", self.module.read_archive("EMP-1")["histories"])

    def test_wrong_duplicate_or_structured_child_ids_are_rejected(self):
        for rows in ([{"name": "OTHER-EMPLOYEE-ROW"}], [{"name": "ROW-1"}, {"name": "ROW-1"}], [{"name": {"bad": "id"}}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.save(histories={"work_history": rows})

    def test_unreadable_unknown_child_field_prevents_whole_row_deletion(self):
        meta = self.metas["Jifeng Legacy Work History"]
        meta.fields.append(Record(fieldname="unknown_field", fieldtype="Data"))
        self.hidden[(meta.doctype, "read")] = {"unknown_field"}
        with self.assertRaises(PermissionError):
            self.save(histories={"work_history": []})

    def test_only_current_private_file_and_file_read_are_accepted(self):
        self.add("File", "PHOTO", file_url="/private/files/photo.png", is_private=1,
            attached_to_doctype="Employee", attached_to_name="EMP-1", attached_to_field="image")
        self.save(employee={"image": "/private/files/photo.png"})
        self.denials.add(("File", "read"))
        self.documents[("Employee", "EMP-1")]["image"] = None
        with self.assertRaises(PermissionError):
            self.save(employee={"image": "/private/files/photo.png"})
        self.denials.clear()
        self.documents[("File", "PHOTO")]["attached_to_name"] = "OTHER-EMPLOYEE"
        with self.assertRaisesRegex(ValueError, "当前档案"):
            self.save(employee={"image": "/private/files/photo.png"})

    def test_client_cannot_forge_save_context_or_account_lifecycle(self):
        for field in ("user_id", "status", "relieving_date", "create_user_automatically", "leave_approver", "flags"):
            before = self.state()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.save(employee={field: "forged"})
            self.assertEqual(self.state(), before)
        with self.assertRaises(ValueError):
            self.save(marker="forged")

    def test_nan_boolean_bad_dates_and_unconfirmed_select_values_are_rejected(self):
        for values in ({"legacy_time_value": True}, {"legacy_base_salary": "NaN"}, {"term_type": "Full-time"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.save(supplement=values)
        with self.assertRaises(ValueError):
            self.save(employee={"date_of_birth": "not-a-date"})

    def test_new_archive_does_not_create_user_or_accept_missing_company(self):
        with self.assertRaisesRegex(ValueError, "公司"):
            self.module.save_archive({"employee": {"first_name": "新员工"}})
        users = deepcopy(self.users)
        result = self.module.save_archive({"employee": {"company": "JF", "first_name": "新员工",
            "gender": "Male", "date_of_birth": "1990-01-01", "date_of_joining": "2026-10-01"}})
        self.assertEqual(result["name"], "EMP-NEW")
        self.assertEqual(result["supplement"]["term_type"], "长期")
        self.assertEqual(self.users, users)
        self.assertEqual(self.documents[("Employee", "EMP-NEW")]["status"], "Active")


if __name__ == "__main__":
    unittest.main()
