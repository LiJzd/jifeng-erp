"""Mutable product words, permission projections and stable creation receipts."""

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
from uuid import UUID, uuid4


ROOT = Path(__file__).parents[1]
DOCTYPE = "Jifeng Product Dictionary"
SCHEMA = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_product_dictionary/jifeng_product_dictionary.json").read_text(encoding="utf-8"))


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

    def set(self, field, value):
        self[field] = value

    def has_permission(self, permission):
        env = self.environment
        return permission not in env.role_denials and self.name not in env.document_denials and self.company not in env.denied_companies

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native dictionary " + permission)

    def get_doc_before_save(self):
        row = self.environment.documents.get(self.name)
        return NativeDocument(deepcopy(row)) if row else None

    def save(self):
        env = self.environment
        new = self.is_new()
        self.check_permission("create" if new else "write")
        if new:
            env.module.before_insert_dictionary(self)
        env.module.validate_dictionary(self)
        if any(row["request_id"] == self.request_id and row["name"] != self.name for row in env.documents.values()):
            env.frappe.local.message_log = list(getattr(env.frappe.local, "message_log", None) or []) + [{"message": "private unique-index SQL"}]
            raise KeyError("private native identity")
        env.tick += 1
        self.name = self.name or "PROD-D-" + str(env.tick)
        self.modified = datetime(2026, 10, 1, 10, 0, 0, env.tick)
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.documents[self.name] = deepcopy(dict(self))
        if not new:
            env.versions.append((self.name, deepcopy(dict(self))))
        if env.fail_after_save:
            env.db.after_commit.add(lambda: None)
            env.frappe.local._realtime_log = ["unexpected event"]
            env.frappe.flags.currently_saving = ["unexpected context"]
            raise ValueError("native post-save failure")
        return self


class TestProductDictionary(unittest.TestCase):
    def setUp(self):
        self.documents, self.versions = {}, []
        self.role_denials, self.document_denials, self.denied_companies = set(), set(), set()
        self.hidden = {"read": set(), "write": set()}
        self.tick, self.fail_after_save, self.forced_list = 1, False, None
        self.meta = types.SimpleNamespace(get_field=lambda name: next((Record(field) for field in SCHEMA["fields"] if field["fieldname"] == name), None))
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="stock-manager@example.invalid")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = PermissionError, RuntimeError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.has_permission = Mock(side_effect=lambda dt, permission: permission not in self.role_denials)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.new_doc = Mock(side_effect=self.new_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=AssertionError("unscoped listing is forbidden"))
        self.snapshots = {}
        self.db = types.SimpleNamespace(savepoint=self.savepoint, rollback=self.rollback,
            **{key: Queue() for key in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        self.frappe.db = self.db
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            {row["fieldname"] for row in SCHEMA["fields"]} | {"name", "modified", "creation", "owner", "modified_by"}) - self.hidden[permission_type]
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse((ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_atomic")
        namespace = {"frappe": self.frappe, "wraps": wraps, "deepcopy": deepcopy, "uuid4": uuid4}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual_native_atomic", "exec"), namespace)
        separation._atomic = namespace["_atomic"]
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "jifeng_erp.personnel_separation": separation})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("product_dictionary_under_test", ROOT / "jifeng_erp/product_dictionary.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        NativeDocument.environment = self
        self.company_calls = []

    def new_doc(self, doctype):
        self.assertEqual(doctype, DOCTYPE)
        return NativeDocument(doctype=DOCTYPE, name=None, company="DEFAULT-DENIED", dictionary_type="风格", inactive=0, __islocal=1)

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Company":
            self.company_calls.append(name)
            def permission(action):
                self.assertEqual(action, "select")
                if name in self.denied_companies:
                    raise PermissionError("company user permission")
            return Record(name=name, check_permission=permission)
        self.assertEqual(doctype, DOCTYPE, "dictionary must not load or write Item/Profile/Employee")
        return NativeDocument(deepcopy(self.documents[name]))

    def get_list(self, doctype, filters, fields, **kwargs):
        self.assertEqual(doctype, DOCTYPE)
        if self.forced_list is not None:
            return self.forced_list
        def matches(row, field, value):
            return value[1].strip("%") in (row.get(field) or "") if isinstance(value, list) else row.get(field) == value
        result = []
        for name, row in self.documents.items():
            if name in self.document_denials or row["company"] in self.denied_companies:
                continue
            if not all(matches(row, field, value) for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(matches(row, field, [operator, value]) for field, operator, value in kwargs["or_filters"]):
                continue
            result.append(Record({field: row[field] for field in fields if field in row and field not in self.hidden["read"]}))
        return sorted(result, key=lambda row: row.get("value", row.name))

    def savepoint(self, point):
        self.snapshots[point] = deepcopy((self.documents, self.versions))

    def rollback(self, save_point):
        self.documents, self.versions = deepcopy(self.snapshots[save_point])

    def payload(self, **changes):
        return {"request_id": str(uuid4()), "company": "C-1", "dictionary_type": "风格", "legacy_number": "001",
                "value": " 典雅 ", "remarks": " 原始备注\n第二行 ", "abbreviation": " DY ", "inactive": 0, **changes}

    def saved(self, **changes):
        return self.module.save_dictionary(self.payload(**changes))

    def update(self, archive, **changes):
        return self.module.save_dictionary({field: archive[field] for field in ("name", "version", *self.module.FIELDS) if field in archive} | changes)

    def test_schema_is_three_raw_profile_words_with_only_request_uuid_unique(self):
        self.assertEqual(self.module.TYPES, {"风格": "style", "来源": "source", "品牌": "brand"})
        profile = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_product_profile/jifeng_product_profile.json").read_text(encoding="utf-8"))
        fields = {row["fieldname"]: row for row in profile["fields"]}
        self.assertTrue(all(fields[field]["fieldtype"] == "Data" for field in self.module.TYPES.values()))
        fields = {row["fieldname"]: row for row in SCHEMA["fields"]}
        self.assertEqual({name for name, field in fields.items() if field.get("unique")}, {"request_id"})
        self.assertTrue(SCHEMA["track_changes"])
        self.assertTrue(all(fields[field]["set_only_once"] for field in self.module.IDENTITY))
        self.assertFalse(fields["value"].get("set_only_once"))
        self.assertEqual({row["role"] for row in SCHEMA["permissions"]}, {"System Manager", "Stock Manager", "Manufacturing Manager"})
        self.assertTrue(all({key for key, value in row.items() if value == 1} == {"read", "select", "create", "write"} for row in SCHEMA["permissions"]))

    def test_init_clears_native_company_defaults_and_exposes_actual_field_meta(self):
        archive = self.module.init_dictionary("品牌")
        self.assertIsNone(archive["company"])
        self.assertIsNone(archive["name"])
        self.assertIsNone(archive["version"])
        self.assertEqual(archive["dictionary_type"], "品牌")
        self.assertTrue(archive["can_write"])
        self.assertEqual(self.company_calls, [])
        self.assertEqual(set(archive["field_meta"]), set(self.module.FIELDS))

    def test_optional_original_words_preserve_whitespace_without_deriving_number_or_pinyin(self):
        archive = self.saved()
        self.assertEqual((archive["legacy_number"], archive["value"], archive["remarks"], archive["abbreviation"]),
                         ("001", " 典雅 ", " 原始备注\n第二行 ", " DY "))
        empty = self.saved(legacy_number=None, remarks=None, abbreviation=None)
        self.assertIsNone(empty["legacy_number"])
        self.assertIsNone(empty["abbreviation"])
        self.assertNotIn("request_id", archive)
        self.assertNotIn("request_digest", archive)

    def test_same_number_and_text_are_allowed_across_same_scope_and_types(self):
        names = [self.saved(dictionary_type=kind)["name"] for kind in self.module.TYPES]
        names.append(self.saved()["name"])
        names.append(self.saved(company="C-2")["name"])
        self.assertEqual(len(set(names)), 5)

    def test_native_new_form_generates_server_uuid_and_digest(self):
        document = self.new_doc(DOCTYPE)
        document.company, document.value = "C-1", "原生新建"
        document.save()
        self.assertEqual(UUID(document.request_id).version, 4)
        self.assertEqual(document.request_digest, self.module._digest("C-1", "风格", self.module._values(document)))

    def test_native_new_form_rejects_server_metadata_or_json_token_injection(self):
        for field, value in (("request_id", str(uuid4())), ("request_digest", "a" * 64)):
            document = self.new_doc(DOCTYPE)
            document.company, document.value = "C-1", "原生新建"
            document.set(field, value)
            document.__dict__["_jf_dictionary_server"] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "只能由服务器生成"):
                document.save()
        document = self.new_doc(DOCTYPE)
        document.company, document.value = "C-1", "原生新建"
        document.request_id, document.request_digest = str(uuid4()), "a" * 64
        document.__dict__["_jf_dictionary_server"] = "client-token"
        with self.assertRaisesRegex(ValueError, "只能由服务器生成"):
            self.module.validate_dictionary(document)

    def test_rename_keeps_stable_name_receipt_and_generates_history(self):
        archive = self.saved()
        receipt = tuple(self.documents[archive["name"]][field] for field in self.module.REQUEST_FIELDS)
        changed = self.update(archive, value="改名原文", legacy_number="X", remarks="新备注", abbreviation="手工简拼")
        self.assertEqual(changed["name"], archive["name"])
        self.assertEqual(tuple(self.documents[archive["name"]][field] for field in self.module.REQUEST_FIELDS), receipt)
        self.assertEqual(len(self.versions), 1)
        self.assertFalse(changed["field_meta"]["value"]["read_only"])
        self.assertTrue(changed["field_meta"]["company"]["read_only"])
        self.assertIn("modified_by", changed)

    def test_native_saved_form_rename_is_allowed_and_identity_is_fixed(self):
        archive = self.saved()
        document = self.get_doc(DOCTYPE, archive["name"])
        document.value = "原生修改"
        document.save()
        self.assertEqual(self.documents[document.name]["value"], "原生修改")
        for field, value in (("company", "C-2"), ("dictionary_type", "来源"),
                             ("request_id", str(uuid4())), ("request_digest", "b" * 64)):
            document = self.get_doc(DOCTYPE, archive["name"])
            document.set(field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "不能修改"):
                document.save()

    def test_creation_retry_after_rename_reads_current_record_without_reverting_or_new_version(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        current = self.update(archive, value="后续已改名", remarks="后续备注")
        before = deepcopy((self.documents, self.versions))
        self.assertEqual(self.module.save_dictionary(payload), current)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "风格", "C-1")["archive"], current)
        self.assertEqual((self.documents, self.versions), before)
        with self.assertRaisesRegex(ValueError, "新建请求已使用"):
            self.module.save_dictionary(payload | {"value": "别的初始内容"})

    def test_hidden_request_collision_is_generic_and_preserves_old_ui_messages(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "风格", "C-1"), {"status": "not_found"})
        self.frappe.local.message_log = [{"message": "earlier independent message"}]
        before = deepcopy((self.documents, self.versions))
        with self.assertRaisesRegex(ValueError, "新建请求已使用") as caught:
            self.module.save_dictionary(payload)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(archive["name"], str(caught.exception))
        self.assertEqual(self.frappe.local.message_log, [{"message": "earlier independent message"}])
        self.assertEqual((self.documents, self.versions), before)

    def test_stale_write_has_lock_and_never_applies_original_words(self):
        archive = self.saved()
        changed = self.update(archive, value="最新")
        with self.assertRaises(RuntimeError):
            self.update(archive, value="过时覆盖")
        self.assertEqual(self.documents[archive["name"]]["value"], "最新")
        self.assertTrue(any(call.kwargs.get("for_update") for call in self.frappe.get_doc.call_args_list))
        self.assertEqual(self.update(changed, inactive=1)["inactive"], 1)

    def test_list_contains_version_and_manual_search_only_reads_permitted_number_or_abbreviation(self):
        archive = self.saved()
        self.assertEqual(self.module.list_dictionary("风格", "C-1", "DY")[0]["version"], archive["version"])
        self.assertEqual(self.module.list_dictionary("风格", "C-1", "001")[0]["name"], archive["name"])
        self.hidden["read"].add("abbreviation")
        self.assertEqual(self.module.list_dictionary("风格", "C-1", "DY"), [])
        self.assertTrue(all(row[0] != "abbreviation" for row in self.frappe.get_list.call_args.kwargs["or_filters"]))

    def test_optional_hidden_values_are_never_backfilled_from_full_document(self):
        archive = self.saved()
        self.hidden["read"].update(("remarks", "legacy_number", "abbreviation"))
        for response in (self.module.read_dictionary(archive["name"]), self.module.list_dictionary("风格", "C-1")[0]):
            self.assertTrue(set(self.hidden["read"]).isdisjoint(response))
        self.assertNotIn("remarks", self.module.read_dictionary(archive["name"])["field_meta"])
        before = deepcopy(self.documents)
        with self.assertRaises(PermissionError):
            self.module.save_dictionary({"name": archive["name"], "version": archive["version"], "company": "C-1",
                                         "dictionary_type": "风格", "remarks": "不可读字段盲写"})
        self.assertEqual(self.documents, before)

    def test_optional_projection_omission_stays_absent_but_missing_required_column_rejects(self):
        archive = self.saved()
        row = Record(self.documents[archive["name"]])
        row.pop("remarks")
        self.forced_list = [row]
        self.assertNotIn("remarks", self.module.read_dictionary(archive["name"]))
        self.assertNotIn("remarks", self.module.list_dictionary("风格", "C-1")[0])
        row.pop("value")
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(archive["name"])
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("风格", "C-1")

    def test_required_field_denial_blocks_queries_and_creation_receipt_read_permission_is_separate(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.hidden["read"].add("value")
        self.frappe.get_list.reset_mock()
        for action in (lambda: self.module.list_dictionary("风格", "C-1"), lambda: self.module.read_dictionary(archive["name"]),
                       lambda: self.module.resolve_creation(payload["request_id"], "风格", "C-1")):
            with self.assertRaises(PermissionError):
                action()
        self.frappe.get_list.assert_not_called()
        self.hidden["read"].clear()
        self.hidden["read"].add("request_digest")
        self.assertEqual(self.module.read_dictionary(archive["name"])["value"], archive["value"])
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "风格", "C-1")

    def test_native_form_cannot_modify_unreadable_or_unwritable_original_fields(self):
        archive = self.saved()
        for permission in ("read", "write"):
            self.hidden[permission].add("remarks")
            document = self.get_doc(DOCTYPE, archive["name"])
            document.remarks = "hidden replacement"
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                document.save()
            self.hidden[permission].clear()

    def test_partial_visible_edit_does_not_normalize_an_unchanged_hidden_empty_original(self):
        archive = self.saved()
        self.documents[archive["name"]]["remarks"] = ""
        self.hidden["read"].add("remarks")
        self.hidden["write"].add("remarks")
        self.module.save_dictionary({"name": archive["name"], "version": archive["version"], "company": "C-1",
                                     "dictionary_type": "风格", "value": "仅改可见名称"})
        self.assertEqual(self.documents[archive["name"]]["remarks"], "")

    def test_checking_actual_company_and_document_user_permissions_keeps_rows_scoped(self):
        archive = self.saved()
        self.denied_companies.add("C-2")
        for action in (lambda: self.module.init_dictionary("品牌", "C-2"), lambda: self.saved(company="C-2"),
                       lambda: self.module.list_dictionary("风格", "C-2")):
            with self.assertRaises(PermissionError):
                action()
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])
        with self.assertRaises(PermissionError):
            self.module.read_dictionary(archive["name"])
        self.frappe.get_all.assert_not_called()

    def test_scope_guard_filters_misleading_native_list_rows_without_backfill(self):
        archive = self.saved()
        self.forced_list = [Record(self.documents[archive["name"]])]
        self.document_denials.add(archive["name"])
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])
        self.document_denials.clear()
        self.documents[archive["name"]]["company"] = "C-2"
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])

    def test_inactive_is_filter_only_and_raw_history_remains_independent(self):
        archive = self.saved()
        independent_profile = {"style": archive["value"], "source": "未登记来源", "brand": "未登记品牌"}
        before = deepcopy(independent_profile)
        stopped = self.update(archive, value="新词条名", inactive=1)
        self.assertEqual(self.module.list_dictionary("风格", "C-1"), [])
        self.assertEqual(self.module.list_dictionary("风格", "C-1", include_inactive=True)[0]["value"], "新词条名")
        self.assertEqual(independent_profile, before)
        self.assertEqual(self.module.read_dictionary(stopped["name"])["inactive"], 1)
        self.frappe.get_doc.assert_any_call(DOCTYPE, archive["name"], for_update=True)

    def test_guest_and_no_manager_role_cannot_read_create_or_modify(self):
        payload = self.payload()
        archive = self.module.save_dictionary(payload)
        self.role_denials.update(("read", "create", "write"))
        actions = (lambda: self.module.init_dictionary("品牌"), lambda: self.module.list_dictionary("风格", "C-1"),
                   lambda: self.module.read_dictionary(archive["name"]), lambda: self.saved(),
                   lambda: self.module.resolve_creation(payload["request_id"], "风格", "C-1"))
        for action in actions:
            with self.assertRaises(PermissionError):
                action()
        self.role_denials.clear()
        self.frappe.session.user = "Guest"
        for action in actions:
            with self.assertRaises(PermissionError):
                action()

    def test_delete_is_explicitly_unimplemented_for_every_actor_not_a_permanent_identity_policy(self):
        for user in ("stock-manager@example.invalid", "Administrator"):
            self.frappe.session.user = user
            with self.assertRaisesRegex(ValueError, "删除尚未实现"):
                self.module.prevent_delete(None)

    def test_wrong_type_company_versions_and_server_payload_fields_reject(self):
        archive = self.saved()
        for changes in ({"company": "C-2"}, {"dictionary_type": "品牌"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "不能修改"):
                self.update(archive, **changes)
        with self.assertRaisesRegex(ValueError, "类型与当前"):
            self.module.read_dictionary(archive["name"], "来源")
        invalid = ({"dictionary_type": "类别"}, {"value": " "}, {"value": "a" * 141}, {"legacy_number": "a" * 141},
                   {"remarks": 1}, {"abbreviation": []}, {"inactive": "yes"}, {"inactive": 1.0}, {"request_id": str(uuid4()).upper()},
                   {"request_id": "not-a-uuid"}, {"request_digest": "client-forged"}, {"version": "old"})
        before = deepcopy(self.documents)
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.saved(**changes)
        self.assertEqual(self.documents, before)

    def test_post_save_failure_rolls_back_record_history_and_preserves_prior_callbacks_and_context(self):
        def initial():
            pass
        self.db.after_commit.add(initial)
        self.frappe.local._realtime_log = ["original event"]
        self.frappe.flags.currently_saving = ["original context"]
        archive = self.saved()
        before = deepcopy((self.documents, self.versions))
        self.fail_after_save = True
        with self.assertRaisesRegex(ValueError, "native post-save failure"):
            self.update(archive, value="failed rename")
        self.assertEqual((self.documents, self.versions), before)
        self.assertEqual(self.db.after_commit._functions, [initial])
        self.assertEqual(self.frappe.local._realtime_log, ["original event"])
        self.assertEqual(self.frappe.flags.currently_saving, ["original context"])

    def test_required_creation_write_permission_and_absent_context_are_preserved_on_failure(self):
        self.hidden["write"].add("value")
        self.assertFalse(self.module.init_dictionary("风格")["can_write"])
        self.hidden["write"].clear()
        self.fail_after_save = True
        with self.assertRaises(ValueError):
            self.saved()
        self.assertFalse(self.documents)
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))

    def test_success_keeps_preexisting_and_new_native_commit_callbacks(self):
        def existing():
            pass
        def native_notification():
            pass
        self.db.after_commit.add(existing)
        original = NativeDocument.save
        def notify(document):
            result = original(document)
            self.db.after_commit.add(native_notification)
            return result
        with patch.object(NativeDocument, "save", notify):
            archive = self.saved()
        self.assertIn(archive["name"], self.documents)
        self.assertEqual(self.db.after_commit._functions, [existing, native_notification])


if __name__ == "__main__":
    unittest.main()
