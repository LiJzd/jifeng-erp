"""Native-scope HR vocabulary and atomic unknown-result recovery contracts."""

import ast
from copy import deepcopy
from datetime import datetime
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
DOCTYPE = "Jifeng HR Dictionary"
SCHEMA = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_hr_dictionary/jifeng_hr_dictionary.json").read_text(encoding="utf-8"))


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


class NativeDocument(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.meta

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, key, value):
        self[key] = value

    def has_permission(self, permission):
        env = self.environment
        return permission not in env.role_denials and self.name not in env.document_denials and self.company not in env.denied_companies

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native dictionary " + permission)

    def get_doc_before_save(self):
        values = self.environment.documents.get(self.name)
        return NativeDocument(deepcopy(values)) if values else None

    def save(self):
        env = self.environment
        self.check_permission("create" if self.is_new() else "write")
        env.module.validate_dictionary(self)
        if any(row["dictionary_key"] == self.dictionary_key and row["name"] != self.name for row in env.documents.values()):
            raise KeyError("native unique constraint")
        env.tick += 1
        self.name = self.name or "HR-D-" + str(env.tick)
        self.modified = datetime(2026, 10, 1, 10, 0, 0, env.tick)
        self["__islocal"] = 0
        env.documents[self.name] = deepcopy(dict(self))
        if env.fail_after_save:
            env.db.after_commit.add(lambda: None)
            env.frappe.local._realtime_log = ["unexpected dictionary event"]
            env.frappe.flags.currently_saving = ["unexpected dictionary context"]
            raise ValueError("native post-save failure")
        return self


class TestHRDictionary(unittest.TestCase):
    def setUp(self):
        self.documents, self.role_denials, self.document_denials, self.denied_companies = {}, set(), set(), set()
        self.hidden = {"read": set(), "write": set()}
        self.tick, self.fail_after_save, self.forced_list = 1, False, None
        self.meta = types.SimpleNamespace(get_field=lambda name: next((Record(field) for field in SCHEMA["fields"] if field["fieldname"] == name), None))
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="hr-manager@example.invalid")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = PermissionError, RuntimeError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.has_permission = Mock(side_effect=lambda dt, permission: permission not in self.role_denials)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.new_doc = Mock(side_effect=lambda dt: NativeDocument(doctype=DOCTYPE, name=None, company="DEFAULT-DENIED", inactive=0, __islocal=1))
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=AssertionError("unscoped list is forbidden"))
        self.snapshots = {}
        self.db = types.SimpleNamespace(savepoint=self.savepoint, rollback=self.rollback,
            **{key: Queue() for key in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        self.frappe.db = self.db
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            {row["fieldname"] for row in SCHEMA["fields"]} | {"name", "modified"}) - self.hidden[permission_type]
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse((ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_atomic")
        namespace = {"frappe": self.frappe, "wraps": wraps, "deepcopy": deepcopy, "uuid4": uuid4}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual_native_atomic", "exec"), namespace)
        separation._atomic = namespace["_atomic"]
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "jifeng_erp.personnel_separation": separation})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("hr_dictionary_under_test", ROOT / "jifeng_erp/hr_dictionary.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        NativeDocument.environment = self
        self.company_calls = []

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Company":
            self.company_calls.append(name)
            def permission(action):
                self.assertEqual(action, "select")
                if name in self.denied_companies:
                    raise PermissionError("company user permission")
            return Record(name=name, check_permission=permission)
        self.assertEqual(doctype, DOCTYPE, "dictionary must not load Employee or private Supplement")
        return NativeDocument(deepcopy(self.documents[name]))

    def get_list(self, doctype, filters, fields, **kwargs):
        self.assertEqual(doctype, DOCTYPE)
        if self.forced_list is not None:
            return self.forced_list
        result = []
        for name, row in self.documents.items():
            if name in self.document_denials or row["company"] in self.denied_companies:
                continue
            for key, value in filters.items():
                if isinstance(value, list) and value[0] == "like":
                    matches = value[1].strip("%") in row[key]
                else:
                    matches = row[key] == value
                if not matches:
                    break
            else:
                result.append(Record({field: row[field] for field in fields if field in row and field not in self.hidden["read"]}))
        return sorted(result, key=lambda row: row.get("value", row.name))

    def savepoint(self, point):
        self.snapshots[point] = deepcopy(self.documents)

    def rollback(self, save_point):
        self.documents = deepcopy(self.snapshots[save_point])

    def payload(self, **changes):
        return {"company": "C-1", "dictionary_type": "专长", "value": "装配", "inactive": 0, **changes}

    def saved(self, **changes):
        return self.module.save_dictionary(self.payload(**changes))

    def update(self, archive, **changes):
        return self.module.save_dictionary({field: archive[field] for field in ("name", "version", *self.module.FIELDS)} | changes)

    def test_actual_field_contract_is_three_raw_supplement_fields(self):
        self.assertEqual(self.module.TYPES, {"专长": "specialty", "文化": "education_level", "民族": "ethnicity"})
        supplement = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_employee_supplement/jifeng_employee_supplement.json").read_text(encoding="utf-8"))
        fields = {row["fieldname"]: row for row in supplement["fields"]}
        self.assertTrue(all(fields[field]["fieldtype"] == "Data" for field in self.module.TYPES.values()))

    def test_schema_preserves_native_tracking_unique_key_and_minimum_roles(self):
        self.assertTrue(SCHEMA["track_changes"])
        fields = {row["fieldname"]: row for row in SCHEMA["fields"]}
        self.assertTrue(fields["dictionary_key"]["unique"] and fields["dictionary_key"]["read_only"])
        self.assertTrue(all(fields[field]["set_only_once"] for field in self.module.IDENTITY))
        for role in SCHEMA["permissions"]:
            self.assertFalse(role.get("delete"))
            if role["role"] == "HR User":
                self.assertTrue(role["read"] and role["select"])
                self.assertFalse(role.get("create") or role.get("write"))

    def test_unscoped_init_clears_default_company_and_has_only_role_create_gate(self):
        self.denied_companies.add("DEFAULT-DENIED")
        archive = self.module.init_dictionary("文化")
        self.assertIsNone(archive["company"])
        self.assertIsNone(archive["name"])
        self.assertIsNone(archive["version"])
        self.assertEqual(archive["dictionary_type"], "文化")
        self.assertEqual(archive["inactive"], 0)
        self.assertTrue(archive["can_write"])
        self.assertEqual(self.company_calls, [])

    def test_read_only_hr_user_init_has_no_write_grant(self):
        self.role_denials.update(("create", "write"))
        self.assertFalse(self.module.init_dictionary("民族")["can_write"])
        self.assertTrue(all(field["read_only"] for field in self.module.init_dictionary("民族")["field_meta"].values()))

    def test_explicit_company_init_and_create_check_actual_company_permission(self):
        self.denied_companies.add("C-2")
        with self.assertRaises(PermissionError):
            self.module.init_dictionary("专长", "C-2")
        with self.assertRaises(PermissionError):
            self.saved(company="C-2")
        self.assertFalse(self.documents)

    def test_three_types_and_companies_are_separate_persistent_identities(self):
        archives = [self.saved(dictionary_type=type_) for type_ in self.module.TYPES]
        archives.append(self.saved(company="C-2"))
        self.assertEqual(len({archive["name"] for archive in archives}), 4)
        self.assertEqual(len({row["dictionary_key"] for row in self.documents.values()}), 4)
        self.assertEqual(len(self.module.list_dictionary("专长", "C-1")), 1)

    def test_trimmed_content_and_server_key_are_persistent_not_client_identity(self):
        archive = self.saved(value="  装配  ")
        self.assertEqual(archive["value"], "装配")
        self.assertEqual(self.documents[archive["name"]]["dictionary_key"], self.module._key("C-1", "专长", "装配"))
        self.assertNotIn("dictionary_key", archive)
        document = self.get_doc(DOCTYPE, archive["name"])
        document.dictionary_key = "client-forgery"
        document.save()
        self.assertEqual(document.dictionary_key, self.module._key("C-1", "专长", "装配"))

    def test_native_unique_constraint_rejects_same_identity_and_keeps_original(self):
        self.saved()
        before = deepcopy(self.documents)
        with self.assertRaisesRegex(ValueError, "词条已存在或身份冲突"):
            self.saved(value=" 装配 ")
        self.assertEqual(self.documents, before)

    def test_permission_hidden_unique_collision_has_no_identity_or_exception_context(self):
        archive = self.saved()
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.resolve_creation("C-1", "专长", "装配"), {"status": "not_found"})
        with self.assertRaises(ValueError) as error:
            self.saved()
        self.assertNotIn(archive["name"], str(error.exception))
        self.assertNotIn(self.documents[archive["name"]]["dictionary_key"], str(error.exception))
        self.assertIsNone(error.exception.__context__)

    def test_existing_identity_values_can_be_echoed_but_cannot_change(self):
        archive = self.saved()
        stopped = self.update(archive, inactive=1)
        self.assertEqual(stopped["value"], archive["value"])
        for field, value in (("company", "C-2"), ("dictionary_type", "文化"), ("value", "切割")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "公司、类型及内容不能修改"):
                self.update(stopped, **{field: value})
        self.assertEqual(self.documents[archive["name"]]["inactive"], 1)

    def test_native_forms_enforce_the_same_fixed_identity(self):
        archive = self.saved()
        for field, value in (("company", "C-2"), ("dictionary_type", "民族"), ("value", "别的原文")):
            document = self.get_doc(DOCTYPE, archive["name"])
            document.set(field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "公司、类型及内容不能修改"):
                document.save()

    def test_stopping_removes_only_new_choices_and_management_can_include_inactive(self):
        stopped = self.update(self.saved(), inactive=1)
        self.assertEqual(self.module.list_dictionary("专长", "C-1"), [])
        rows = self.module.list_dictionary("专长", "C-1", include_inactive="1")
        self.assertEqual(rows, [{"name": stopped["name"], "value": "装配", "inactive": 1}])
        self.assertEqual(self.module.read_dictionary(stopped["name"])["inactive"], 1)

    def test_management_list_still_needs_native_read_not_write_or_create(self):
        archive = self.saved()
        self.update(archive, inactive=1)
        self.role_denials.update(("create", "write"))
        self.assertEqual(len(self.module.list_dictionary("专长", "C-1", include_inactive=True)), 1)
        self.role_denials.add("read")
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("专长", "C-1", include_inactive=True)

    def test_native_scoped_list_then_full_document_read_scope_guard(self):
        archive = self.saved()
        self.forced_list = [Record(name=archive["name"], value="装配", inactive=0)]
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.list_dictionary("专长", "C-1"), [])
        self.document_denials.clear()
        self.documents[archive["name"]]["company"] = "C-2"
        self.assertEqual(self.module.list_dictionary("专长", "C-1"), [])
        self.frappe.get_all.assert_not_called()

    def test_query_projection_never_refills_omitted_native_field(self):
        archive = self.saved()
        self.forced_list = [Record(name=archive["name"], inactive=0)]
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("专长", "C-1")

    def test_standard_name_is_permitted_even_when_not_a_declared_docfield(self):
        self.assertNotIn("name", {row["fieldname"] for row in SCHEMA["fields"]})
        archive = self.saved()
        self.assertEqual(self.module.list_dictionary("专长", "C-1")[0]["name"], archive["name"])

    def test_field_read_write_denials_precede_queries_or_mutations(self):
        archive = self.saved()
        self.hidden["read"].add("value")
        self.frappe.get_list.reset_mock()
        for action in (lambda: self.module.list_dictionary("专长", "C-1"),
                       lambda: self.module.read_dictionary(archive["name"]),
                       lambda: self.module.resolve_creation("C-1", "专长", "装配")):
            with self.assertRaises(PermissionError):
                action()
        self.frappe.get_list.assert_not_called()
        self.hidden["read"].clear()
        self.hidden["write"].add("inactive")
        self.assertFalse(self.module.read_dictionary(archive["name"])["can_write"])
        with self.assertRaises(PermissionError):
            self.update(archive, inactive=1)

    def test_stale_version_rejects_and_save_response_version_round_trips(self):
        archive = self.saved()
        changed = self.update(archive, inactive=1)
        with self.assertRaises(RuntimeError):
            self.update(archive, inactive=0)
        response = self.update(changed, inactive=0)
        self.assertEqual(self.update(response, inactive=1)["inactive"], 1)

    def test_all_deletions_are_refused_including_administrator(self):
        for user in ("hr-manager@example.invalid", "Administrator"):
            self.frappe.session.user = user
            with self.subTest(user=user), self.assertRaisesRegex(ValueError, "停用保留历史，不能删除"):
                self.module.prevent_delete(None)

    def test_guest_cannot_init_list_read_resolve_or_save(self):
        archive = self.saved()
        self.frappe.session.user = "Guest"
        for action in (lambda: self.module.init_dictionary("民族"), lambda: self.module.list_dictionary("专长", "C-1"),
                       lambda: self.module.read_dictionary(archive["name"]), lambda: self.module.resolve_creation("C-1", "专长", "装配"),
                       lambda: self.saved()):
            with self.assertRaises(PermissionError):
                action()

    def test_resolve_unknown_creation_is_read_only_exact_and_does_not_grant_document_read(self):
        self.assertEqual(self.module.resolve_creation("C-1", "专长", "装配"), {"status": "not_found"})
        archive = self.saved()
        before = deepcopy(self.documents)
        found = self.module.resolve_creation("C-1", "专长", " 装配 ")
        self.assertEqual(found["status"], "found")
        self.assertEqual(found["archive"]["name"], archive["name"])
        self.assertEqual(self.documents, before)
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.resolve_creation("C-1", "专长", "装配"), {"status": "not_found"})
        self.forced_list = [Record(name=archive["name"])]
        with self.assertRaises(PermissionError):
            self.module.resolve_creation("C-1", "专长", "装配")

    def test_native_post_save_failure_rolls_back_doc_and_existing_callback_context(self):
        def initial():
            pass
        self.db.after_commit.add(initial)
        self.frappe.local._realtime_log = ["original event"]
        self.frappe.flags.currently_saving = ["original context"]
        self.fail_after_save = True
        with self.assertRaisesRegex(ValueError, "native post-save failure"):
            self.saved()
        self.assertEqual(self.documents, {})
        self.assertEqual(self.db.after_commit._functions, [initial])
        self.assertEqual(self.frappe.local._realtime_log, ["original event"])
        self.assertEqual(self.frappe.flags.currently_saving, ["original context"])

    def test_invalid_types_text_flags_and_client_keys_are_rejected(self):
        for changes in ({"dictionary_type": "Religion"}, {"value": " "}, {"value": "a" * 141}, {"inactive": "yes"},
                        {"inactive": 2}, {"inactive": 1.0}, {"dictionary_key": "fake"}, {"version": "old"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.save_dictionary(self.payload(**changes))
        self.assertFalse(self.documents)

    def test_read_wrong_dictionary_kind_is_rejected(self):
        archive = self.saved()
        with self.assertRaisesRegex(ValueError, "类型与当前人事词典不一致"):
            self.module.read_dictionary(archive["name"], "民族")


if __name__ == "__main__":
    unittest.main()
