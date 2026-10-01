"""Product category native permissions, raw fields and durable creation receipts."""

import ast
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
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
DOCTYPES = {"category": "Jifeng Product Category", "detail": "Jifeng Product Category Detail"}
EDIT_FIELDS = {
    "category": ("legacy_code", "major_name", "owner_name", "region", "inactive"),
    "detail": ("minor_name", "material", "display_uom", "conversion_uom_text", "conversion_value", "inactive"),
}


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, callback):
        self._functions.append(callback)

    def reset(self):
        self._functions.clear()


class NativeDocument(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.metadata[self.doctype]

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, field, value):
        self[field] = value

    def has_permission(self, permission):
        env = self.environment
        return ((self.doctype, permission) not in env.role_denials
                and self.name not in env.document_denials
                and (self.name, permission) not in env.document_denials
                and self.company not in env.denied_companies)

    def check_permission(self, permission):
        self.environment.permission_calls.append((self.doctype, self.name, permission))
        if not self.has_permission(permission):
            raise PermissionError("native category " + permission)

    def get_doc_before_save(self):
        row = self.environment.documents.get(self.name)
        return NativeDocument(deepcopy(row)) if row else None

    def save(self):
        env = self.environment
        new = self.is_new()
        self.check_permission("create" if new else "write")
        if new:
            env.module.before_insert_record(self)
        env.module.validate_record(self)
        if any(row["doctype"] == self.doctype and row["request_id"] == self.request_id
               and row["name"] != self.name for row in env.documents.values()):
            env.frappe.local.message_log = list(getattr(env.frappe.local, "message_log", None) or []) + [
                {"message": "private category unique-index SQL"}]
            raise KeyError("private native category identity")
        env.tick += 1
        self.name = self.name or "CAT-" + str(env.tick)
        self.modified = datetime(2026, 10, 1, 10, 0, 0, env.tick)
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.documents[self.name] = deepcopy(dict(self))
        if not new:
            env.versions.append((self.doctype, self.name, deepcopy(dict(self))))
        if env.fail_after_save:
            for key in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
                getattr(env.db, key).add(lambda: None)
            env.frappe.local._realtime_log = ["unexpected category event"]
            env.frappe.flags.currently_saving = ["unexpected category context"]
            raise ValueError("native post-save failure")
        return self


class TestProductCategory(unittest.TestCase):
    def setUp(self):
        self.schemas = {
            kind: json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype" / dt.lower().replace(" ", "_") /
                              (dt.lower().replace(" ", "_") + ".json")).read_text(encoding="utf-8"))
            for kind, dt in DOCTYPES.items()
        }
        self.documents, self.versions, self.snapshots = {}, [], {}
        self.role_denials, self.document_denials, self.denied_companies = set(), set(), set()
        self.hidden = {permission: {dt: set() for dt in DOCTYPES.values()} for permission in ("read", "write")}
        self.tick, self.fail_after_save = 1, False
        self.forced_lists, self.company_calls, self.permission_calls = {}, [], []
        self.metadata = {dt: types.SimpleNamespace(get_field=lambda name, kind=kind: next(
            (Record(field) for field in self.schemas[kind]["fields"] if field["fieldname"] == name), None))
            for kind, dt in DOCTYPES.items()}
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="stock-manager@example.invalid")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = PermissionError, RuntimeError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.has_permission = Mock(side_effect=lambda dt, permission: (dt, permission) not in self.role_denials)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.new_doc = Mock(side_effect=self.new_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=AssertionError("unscoped listing is forbidden"))
        self.db = types.SimpleNamespace(savepoint=self.savepoint, rollback=self.rollback,
            get_value=Mock(side_effect=AssertionError("unpermissioned category lookup is forbidden")),
            **{key: Queue() for key in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        self.frappe.db = self.db
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            {row["fieldname"] for row in self.schemas[self.kind(dt)]["fields"]}
            | {"name", "modified", "creation", "owner", "modified_by"}) - self.hidden[permission_type][dt]
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse((ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_atomic")
        namespace = {"frappe": self.frappe, "wraps": wraps, "deepcopy": deepcopy, "uuid4": uuid4}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual_category_atomic", "exec"), namespace)
        separation._atomic = namespace["_atomic"]
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
                                        "jifeng_erp.personnel_separation": separation})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("product_category_under_test", ROOT / "jifeng_erp/product_category.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        NativeDocument.environment = self

    def kind(self, doctype):
        self.assertIn(doctype, DOCTYPES.values(), "category must not load or write Item/Profile/UOM/Employee")
        return next(kind for kind, dt in DOCTYPES.items() if dt == doctype)

    def new_doc(self, doctype):
        self.kind(doctype)
        return NativeDocument(doctype=doctype, name=None, company="DEFAULT-DENIED", category="DEFAULT-PARENT",
                              inactive=0, __islocal=1)

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Company":
            self.company_calls.append(name)
            def permission(action):
                self.assertEqual(action, "select")
                if name in self.denied_companies:
                    raise PermissionError("company user permission")
            return Record(name=name, check_permission=permission)
        self.kind(doctype)
        self.assertEqual(self.documents[name]["doctype"], doctype)
        return NativeDocument(deepcopy(self.documents[name]))

    def get_list(self, doctype, filters, fields, **kwargs):
        self.kind(doctype)
        if doctype in self.forced_lists:
            return deepcopy(self.forced_lists[doctype])
        def matches(row, field, value):
            if isinstance(value, (list, tuple)):
                operator, operand = value
                if operator == "like":
                    return str(operand).strip("%") in str(row.get(field) or "")
                if operator == "in":
                    return row.get(field) in operand
                self.assertIn(operator, ("=", "=="))
                return row.get(field) == operand
            return row.get(field) == value
        result = []
        for name, row in self.documents.items():
            if row["doctype"] != doctype or name in self.document_denials or (name, "read") in self.document_denials:
                continue
            if row["company"] in self.denied_companies:
                continue
            if not all(matches(row, field, value) for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(matches(row, field, [operator, value])
                    for field, operator, value in kwargs["or_filters"]):
                continue
            result.append(Record({field: row[field] for field in fields
                                  if field in row and field not in self.hidden["read"][doctype]}))
        return sorted(result, key=lambda row: row.get("major_name", row.get("minor_name", row.name)))

    def savepoint(self, point):
        self.snapshots[point] = deepcopy((self.documents, self.versions))

    def rollback(self, save_point):
        self.documents, self.versions = deepcopy(self.snapshots[save_point])

    def payload(self, record_type="category", category=None, **changes):
        fields = {"legacy_code": " 001 ", "major_name": " 大类原文 ", "owner_name": " 负责人原文 ",
                  "region": " 华南原文 ", "inactive": 0} if record_type == "category" else {
                  "minor_name": " 小类原文 ", "material": " 木材原文 ", "display_uom": " 件原文 ",
                  "conversion_uom_text": " 箱原文 ", "conversion_value": 2.5, "inactive": 0}
        return {"record_type": record_type, "request_id": str(uuid4()), "company": "C-1",
                **({"category": category} if record_type == "detail" else {}), **fields, **changes}

    def saved(self, record_type="category", category=None, **changes):
        return self.module.save_record(self.payload(record_type, category, **changes))

    def parent(self, **changes):
        return self.saved(**changes)

    def detail(self, parent=None, **changes):
        parent = parent or self.parent()
        return self.saved("detail", parent["name"], **changes)

    def update(self, archive, **changes):
        return self.module.save_record({"record_type": archive["record_type"], "name": archive["name"],
                                        "version": archive["version"], **changes})

    def hidden_fields(self, permission, kind, *fields):
        self.hidden[permission][DOCTYPES[kind]].update(fields)

    def test_schemas_are_two_normal_doctypes_with_mutable_raw_text_and_native_roles(self):
        self.assertEqual(self.module.DOCTYPES, DOCTYPES)
        self.assertEqual(self.module.EDIT_FIELDS, EDIT_FIELDS)
        for kind, schema in self.schemas.items():
            with self.subTest(kind=kind):
                fields = {row["fieldname"]: row for row in schema["fields"]}
                self.assertFalse(schema.get("istable"))
                self.assertFalse(schema.get("issingle"))
                self.assertTrue(schema["track_changes"])
                self.assertEqual({name for name, field in fields.items() if field.get("unique")}, {"request_id"})
                self.assertTrue(fields["company"]["set_only_once"])
                self.assertEqual((fields["company"]["fieldtype"], fields["company"]["options"]), ("Link", "Company"))
                self.assertTrue(all(not fields[field].get("set_only_once") for field in EDIT_FIELDS[kind]))
                self.assertTrue(all(fields[field]["fieldtype"] == "Data" for field in EDIT_FIELDS[kind]
                                    if field not in ("inactive", "conversion_value")))
                self.assertEqual(fields["inactive"]["fieldtype"], "Check")
                self.assertEqual({row["role"] for row in schema["permissions"]},
                                 {"System Manager", "Stock Manager", "Manufacturing Manager"})
                self.assertTrue(all({key for key, value in row.items() if value == 1}
                                    == {"read", "select", "create", "write"} for row in schema["permissions"]))
        detail_fields = {row["fieldname"]: row for row in self.schemas["detail"]["fields"]}
        self.assertTrue(detail_fields["category"]["set_only_once"])
        self.assertEqual((detail_fields["category"]["fieldtype"], detail_fields["category"]["options"]),
                         ("Link", DOCTYPES["category"]))
        self.assertEqual(detail_fields["conversion_value"]["fieldtype"], "Data")

    def test_init_category_clears_defaults_and_returns_actual_field_metadata(self):
        archive = self.module.init_record("category")
        self.assertIsNone(archive["company"])
        self.assertIsNone(archive["name"])
        self.assertIsNone(archive["version"])
        self.assertEqual(archive["record_type"], "category")
        self.assertEqual(archive["inactive"], 0)
        self.assertEqual(set(archive["field_meta"]), {"company", *EDIT_FIELDS["category"]})
        self.assertEqual(self.company_calls, [])
        self.assertTrue(archive["can_write"])
        self.assertFalse(archive["field_meta"]["major_name"]["read_only"])

    def test_new_category_requires_explicit_company_and_never_uses_native_default(self):
        payload = self.payload()
        for missing in (None, "", " C-1 "):
            with self.subTest(company=missing), self.assertRaises(ValueError):
                self.module.save_record(payload | {"company": missing})
        payload.pop("company")
        with self.assertRaises(ValueError):
            self.module.save_record(payload)
        self.assertFalse(self.documents)
        self.assertNotIn("DEFAULT-DENIED", self.company_calls)

    def test_init_and_new_detail_derive_company_from_readable_selectable_parent(self):
        parent = self.parent()
        archive = self.module.init_record("detail", category=parent["name"])
        self.assertEqual((archive["company"], archive["category"]), ("C-1", parent["name"]))
        self.assertEqual(set(archive["field_meta"]), {"company", "category", *EDIT_FIELDS["detail"]})
        payload = self.payload("detail", parent["name"])
        payload.pop("company")
        detail = self.module.save_record(payload)
        self.assertEqual((detail["company"], detail["category"]), ("C-1", parent["name"]))
        self.assertIn((DOCTYPES["category"], parent["name"], "select"), self.permission_calls)
        self.assertTrue(any(call.args == (DOCTYPES["category"], parent["name"])
                            and call.kwargs.get("for_update") for call in self.frappe.get_doc.call_args_list))
        with self.assertRaises(ValueError):
            self.saved("detail", parent["name"], company="C-2")
        with self.assertRaises(ValueError):
            self.module.init_record("detail", "C-2", parent["name"])
        with self.assertRaises(ValueError):
            self.module.init_record("detail")

    def test_parent_read_and_select_permissions_are_required_for_new_detail(self):
        parent = self.parent()
        before = deepcopy((self.documents, self.versions))
        for action in ("read", "select"):
            self.document_denials.add((parent["name"], action))
            with self.subTest(action=action), self.assertRaises(PermissionError):
                self.detail(parent)
            self.document_denials.clear()
        self.assertEqual((self.documents, self.versions), before)

    def test_existing_detail_reads_and_edits_require_parent_read_without_parent_write_or_select(self):
        parent = self.parent()
        detail = self.detail(parent)
        self.document_denials.update({(parent["name"], "write"), (parent["name"], "select")})
        self.permission_calls.clear()
        self.assertEqual(self.module.read_record(detail["name"], "detail")["minor_name"], detail["minor_name"])
        changed = self.update(detail, minor_name=" 只改明细原文 ")
        self.assertEqual(changed["minor_name"], " 只改明细原文 ")
        self.assertIn((DOCTYPES["category"], parent["name"], "read"), self.permission_calls)
        self.assertNotIn((DOCTYPES["category"], parent["name"], "write"), self.permission_calls)
        self.assertNotIn((DOCTYPES["category"], parent["name"], "select"), self.permission_calls)
        self.document_denials.add((parent["name"], "read"))
        with self.assertRaises(PermissionError):
            self.module.read_record(detail["name"], "detail")
        with self.assertRaises(PermissionError):
            self.update(changed, material="不能访问父分类")

    def test_raw_original_fields_and_duplicates_never_link_map_or_rewrite_sources(self):
        parent = self.parent()
        detail = self.detail(parent)
        for field in EDIT_FIELDS["category"]:
            self.assertEqual(parent[field], self.payload()[field])
        for field in EDIT_FIELDS["detail"]:
            original = self.payload("detail", parent["name"])[field]
            self.assertEqual(detail[field], str(Decimal(str(original))) if field == "conversion_value" else original)
        other_parent = self.parent()
        other_detail = self.detail(parent)
        self.assertNotEqual(other_parent["name"], parent["name"])
        self.assertNotEqual(other_detail["name"], detail["name"])
        for archive in (parent, detail):
            self.assertNotIn("request_id", archive)
            self.assertNotIn("request_digest", archive)
        self.frappe.get_all.assert_not_called()
        self.db.get_value.assert_not_called()

    def test_optional_text_and_conversion_values_allow_blank_zero_and_negative_without_derivation(self):
        parent = self.parent(legacy_code=None, owner_name="", region=None)
        self.assertIsNone(parent["legacy_code"])
        self.assertIsNone(parent["owner_name"])
        for value in (None, "", 0, -2.5, "0", "-2.5", 1.25):
            with self.subTest(value=value):
                detail = self.detail(parent, material=None, display_uom="", conversion_uom_text=None,
                                     conversion_value=value)
                self.assertIsNone(detail["material"])
                self.assertIsNone(detail["display_uom"])
                self.assertIsNone(detail["conversion_uom_text"])
                self.assertEqual(detail["conversion_value"], None if value in (None, "") else str(Decimal(str(value))))

    def test_conversion_value_rejects_bool_nan_infinity_and_non_numeric_inputs(self):
        parent = self.parent()
        before = deepcopy((self.documents, self.versions))
        for value in (True, False, float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-Infinity", [], {}, "单位"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.detail(parent, conversion_value=value)
        self.assertEqual((self.documents, self.versions), before)

    def test_nullable_conversion_storage_keeps_empty_distinct_from_zero_and_api_decimal_text(self):
        parent = self.parent()
        for value in (None, "", 0, -2.5, "1.25"):
            with self.subTest(value=value):
                archive = self.detail(parent, conversion_value=value)
                numeric_text = None if value in (None, "") else str(Decimal(str(value)))
                stored = self.documents[archive["name"]]["conversion_value"]
                self.assertEqual(stored, numeric_text)
                if numeric_text is not None:
                    self.assertIsInstance(stored, str)
                    self.assertIsInstance(archive["conversion_value"], str)
                self.assertEqual(archive["conversion_value"], numeric_text)
                self.assertEqual(self.module.read_record(archive["name"], "detail")["conversion_value"], numeric_text)
                listed = self.module.list_records("detail", "C-1", parent["name"])
                self.assertEqual(next(row for row in listed if row["name"] == archive["name"])["conversion_value"], numeric_text)
        archive = self.detail(parent, conversion_value=0)
        self.assertEqual(self.documents[archive["name"]]["conversion_value"], "0")
        archive = self.update(archive, conversion_value=None)
        self.assertIsNone(self.documents[archive["name"]]["conversion_value"])
        self.assertIsNone(archive["conversion_value"])
        document = self.get_doc(DOCTYPES["detail"], archive["name"])
        document.conversion_value = -3.5
        document.save()
        self.assertEqual(self.documents[archive["name"]]["conversion_value"], "-3.5")
        self.assertEqual(self.module.read_record(archive["name"], "detail")["conversion_value"], "-3.5")

    def test_decimal_text_preserves_precise_digits_large_finite_values_and_negative_zero(self):
        parent = self.parent()
        values = ("12345678901234567890.123456789012345678901234567890", "1E+400", "-0.0",
                  -0.0, "000.5000", Decimal("-0.000"))
        for value in values:
            with self.subTest(value=value):
                expected = str(Decimal(str(value)))
                archive = self.detail(parent, conversion_value=value)
                self.assertEqual(archive["conversion_value"], expected)
                self.assertEqual(self.documents[archive["name"]]["conversion_value"], expected)
                self.assertEqual(self.module.read_record(archive["name"], "detail")["conversion_value"], expected)
                listed = self.module.list_records("detail", "C-1", parent["name"])
                self.assertEqual(next(row for row in listed if row["name"] == archive["name"])["conversion_value"], expected)
        before = deepcopy((self.documents, self.versions))
        with self.assertRaises(ValueError):
            self.detail(parent, conversion_value="0." + "1" * 139)
        self.assertEqual((self.documents, self.versions), before)

    def test_check_field_accepts_only_native_bool_integer_or_zero_one_string(self):
        for value in (0, 1, False, True, "0", "1"):
            with self.subTest(value=value):
                self.assertEqual(self.parent(inactive=value)["inactive"], int(value))
        before = deepcopy(self.documents)
        for value in (1.0, 0.0, "yes", "", None, -1, 2, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parent(inactive=value)
        self.assertEqual(self.documents, before)

    def test_native_forms_generate_server_uuid_and_reject_client_receipt_metadata(self):
        for kind in DOCTYPES:
            parent = self.parent() if kind == "detail" else None
            document = self.new_doc(DOCTYPES[kind])
            document.company = "C-1"
            if parent:
                document.category = parent["name"]
            for field, value in self.payload(kind, parent["name"] if parent else None).items():
                if field in EDIT_FIELDS[kind]:
                    document.set(field, value)
            document.save()
            self.assertEqual(UUID(document.request_id).version, 4)
            self.assertEqual(len(document.request_digest), 64)
            self.assertTrue(any(value is self.module._SERVER for value in document.__dict__.values()))
            for field, value in (("request_id", str(uuid4())), ("request_digest", "a" * 64)):
                forged = self.new_doc(DOCTYPES[kind])
                forged.company = "C-1"
                if parent:
                    forged.category = parent["name"]
                forged.set("major_name" if kind == "category" else "minor_name", "原生新建")
                forged.set(field, value)
                with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                    forged.save()

    def test_native_json_token_and_boolean_object_marker_cannot_authorize_creation(self):
        valid = self.new_doc(DOCTYPES["category"])
        valid.company, valid.major_name = "C-1", "服务器初始化原文"
        self.module.before_insert_record(valid)
        markers = [key for key, value in valid.__dict__.items() if value is self.module._SERVER]
        self.assertTrue(markers)
        for where in ("json", "attribute"):
            forged = self.new_doc(DOCTYPES["category"])
            forged.update(valid)
            for marker in markers:
                if where == "json":
                    forged.set(marker, True)
                else:
                    forged.__dict__[marker] = True
            with self.subTest(where=where), self.assertRaises(ValueError):
                self.module.validate_record(forged)
        valid.major_name = "初始化后篡改原文"
        with self.assertRaises(ValueError):
            self.module.validate_record(valid)

    def test_identity_and_creation_receipt_are_immutable_in_api_and_native_forms(self):
        parent = self.parent()
        detail = self.detail(parent)
        other = self.parent()
        for archive in (parent, detail):
            kind = archive["record_type"]
            changes = [("company", "C-2"), ("request_id", str(uuid4())), ("request_digest", "b" * 64)]
            if kind == "detail":
                changes.append(("category", other["name"]))
            for field, value in changes:
                with self.subTest(api=kind, field=field), self.assertRaises(ValueError):
                    self.update(archive, **{field: value})
                document = self.get_doc(DOCTYPES[kind], archive["name"])
                document.set(field, value)
                with self.subTest(native=kind, field=field), self.assertRaises(ValueError):
                    document.save()

    def test_partial_update_keeps_identity_and_receipts_and_native_history(self):
        parent = self.parent()
        detail = self.detail(parent)
        for archive in (parent, detail):
            kind = archive["record_type"]
            field = "major_name" if kind == "category" else "minor_name"
            receipt = tuple(self.documents[archive["name"]][key] for key in ("request_id", "request_digest"))
            changed = self.update(archive, **{field: " 后续改名原文 "})
            self.assertEqual(changed["name"], archive["name"])
            self.assertEqual(changed["company"], archive["company"])
            self.assertEqual(changed[field], " 后续改名原文 ")
            self.assertTrue(changed["field_meta"]["company"]["read_only"])
            self.assertFalse(changed["field_meta"][field]["read_only"])
            if kind == "detail":
                self.assertEqual(changed["category"], detail["category"])
                self.assertTrue(changed["field_meta"]["category"]["read_only"])
            self.assertEqual(tuple(self.documents[archive["name"]][key] for key in ("request_id", "request_digest")), receipt)
        self.assertEqual(len(self.versions), 2)

    def test_stale_versions_lock_and_preserve_the_latest_original(self):
        parent = self.parent()
        for archive in (parent, self.detail(parent)):
            kind = archive["record_type"]
            field = "major_name" if kind == "category" else "minor_name"
            current = self.update(archive, **{field: "最新原文"})
            before = deepcopy((self.documents, self.versions))
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                self.update(archive, **{field: "过时覆盖"})
            self.assertEqual((self.documents, self.versions), before)
            self.assertEqual(self.documents[archive["name"]][field], current[field])
            self.assertTrue(any(call.args == (DOCTYPES[kind], archive["name"])
                                and call.kwargs.get("for_update") for call in self.frappe.get_doc.call_args_list))

    def test_same_uuid_creation_retry_after_rename_returns_current_record_without_writes(self):
        parent_payload = self.payload()
        parent = self.module.save_record(parent_payload)
        detail_payload = self.payload("detail", parent["name"])
        detail = self.module.save_record(detail_payload)
        for payload, archive in ((parent_payload, parent), (detail_payload, detail)):
            kind = archive["record_type"]
            field = "major_name" if kind == "category" else "minor_name"
            current = self.update(archive, **{field: "重试前已改名"})
            before = deepcopy((self.documents, self.versions))
            self.assertEqual(self.module.save_record(payload), current)
            self.assertEqual(self.module.resolve_creation(payload["request_id"], kind, "C-1",
                             parent["name"] if kind == "detail" else None), {"status": "found", "archive": current})
            self.assertEqual((self.documents, self.versions), before)

    def test_record_type_and_uuid_joint_identity_resolves_each_kind_and_rejects_same_kind_conflicts(self):
        request_id = str(uuid4())
        parent_payload = self.payload(request_id=request_id)
        parent = self.module.save_record(parent_payload)
        detail_payload = self.payload("detail", parent["name"], request_id=request_id)
        detail = self.module.save_record(detail_payload)
        self.assertNotEqual(parent["name"], detail["name"])
        self.assertEqual(self.documents[parent["name"]]["request_id"], request_id)
        self.assertEqual(self.documents[detail["name"]]["request_id"], request_id)
        self.assertEqual(self.module.resolve_creation(request_id, "category", "C-1"),
                         {"status": "found", "archive": parent})
        self.assertEqual(self.module.resolve_creation(request_id, "detail", "C-1", parent["name"]),
                         {"status": "found", "archive": detail})
        before = deepcopy((self.documents, self.versions))
        for payload, field in ((parent_payload, "major_name"), (detail_payload, "minor_name")):
            with self.subTest(kind=payload["record_type"]), self.assertRaisesRegex(ValueError, "新建请求已使用"):
                self.module.save_record(payload | {field: "相同类型的其他初始原文"})
        self.assertEqual((self.documents, self.versions), before)

    def test_uuid_receipt_digest_covers_every_initial_edit_field_and_scope(self):
        parent = self.parent()
        for kind in DOCTYPES:
            payload = self.payload(kind, parent["name"] if kind == "detail" else None)
            self.module.save_record(payload)
            before = deepcopy((self.documents, self.versions))
            for field in EDIT_FIELDS[kind]:
                value = 1 if field == "inactive" else -3 if field == "conversion_value" else "别的初始原文"
                with self.subTest(kind=kind, field=field), self.assertRaisesRegex(ValueError, "新建请求已使用"):
                    self.module.save_record(payload | {field: value})
            with self.subTest(kind=kind, scope="company"), self.assertRaisesRegex(ValueError, "新建请求已使用"):
                if kind == "detail":
                    other_parent = self.parent(company="C-2")
                    expected = deepcopy((self.documents, self.versions))
                    self.module.save_record(payload | {"company": "C-2", "category": other_parent["name"]})
                else:
                    expected = before
                    self.module.save_record(payload | {"company": "C-2"})
            self.assertEqual((self.documents, self.versions), expected)

    def test_parent_inactive_blocks_new_detail_but_keeps_existing_edit_and_original_uuid_replay(self):
        parent = self.parent()
        payload = self.payload("detail", parent["name"])
        detail = self.module.save_record(payload)
        self.update(parent, inactive=1)
        with self.assertRaises(ValueError):
            self.detail(parent)
        current = self.update(detail, minor_name="停用父分类后的历史原文", material="后续材质原文")
        self.assertFalse(current["can_select"])
        self.assertEqual(self.module.read_record(detail["name"], "detail"), current)
        before = deepcopy((self.documents, self.versions))
        self.assertEqual(self.module.save_record(payload), current)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "detail", "C-1", parent["name"]),
                         {"status": "found", "archive": current})
        self.assertEqual((self.documents, self.versions), before)
        self.assertEqual(self.module.list_records("detail", "C-1", parent["name"]), [])
        self.assertEqual(self.module.list_records("detail", "C-1", parent["name"], include_inactive=True)[0]["name"], detail["name"])

    def test_inactive_is_a_selection_filter_while_category_and_detail_history_stay_readable(self):
        parent = self.parent()
        detail = self.detail(parent)
        stopped = self.update(detail, inactive=1)
        self.assertFalse(stopped["can_select"])
        self.assertEqual(self.module.list_records("detail", "C-1", parent["name"]), [])
        self.assertEqual(self.module.list_records("detail", "C-1", parent["name"], include_inactive=True)[0]["name"], detail["name"])
        self.assertEqual(self.module.read_record(stopped["name"], "detail")["inactive"], 1)
        stopped_parent = self.update(parent, inactive=1)
        self.assertFalse(stopped_parent["can_select"])
        self.assertEqual(self.module.list_records("category", "C-1"), [])
        self.assertEqual(self.module.list_records("category", "C-1", include_inactive=True)[0]["name"], parent["name"])

    def test_company_user_permissions_and_document_permissions_scope_all_entry_points(self):
        parent = self.parent()
        self.denied_companies.add("C-2")
        actions = (lambda: self.module.init_record("category", "C-2"), lambda: self.parent(company="C-2"),
                   lambda: self.module.list_records("category", "C-2"))
        for action in actions:
            with self.assertRaises(PermissionError):
                action()
        self.document_denials.add(parent["name"])
        self.assertEqual(self.module.list_records("category", "C-1"), [])
        with self.assertRaises(PermissionError):
            self.module.read_record(parent["name"], "category")
        with self.assertRaises(PermissionError):
            self.detail(parent)
        self.frappe.get_all.assert_not_called()

    def test_guest_and_native_role_denials_block_category_and_detail_operations(self):
        parent_payload = self.payload()
        parent = self.module.save_record(parent_payload)
        detail = self.detail(parent)
        actions = (lambda: self.module.init_record("category"), lambda: self.module.list_records("category", "C-1"),
                   lambda: self.module.read_record(parent["name"], "category"), lambda: self.parent(),
                   lambda: self.module.init_record("detail", category=parent["name"]),
                   lambda: self.module.read_record(detail["name"], "detail"), lambda: self.detail(parent),
                   lambda: self.module.resolve_creation(parent_payload["request_id"], "category", "C-1"))
        self.role_denials.update((dt, action) for dt in DOCTYPES.values() for action in ("read", "create", "write"))
        for action in actions:
            with self.assertRaises(PermissionError):
                action()
        self.role_denials.clear()
        self.frappe.session.user = "Guest"
        for action in actions:
            with self.assertRaises(PermissionError):
                action()

    def test_unreadable_required_field_blocks_query_and_receipt_permission_is_separate(self):
        payload = self.payload()
        parent = self.module.save_record(payload)
        self.hidden_fields("read", "category", "major_name")
        self.frappe.get_list.reset_mock()
        actions = (lambda: self.module.list_records("category", "C-1"),
                   lambda: self.module.read_record(parent["name"], "category"),
                   lambda: self.module.resolve_creation(payload["request_id"], "category", "C-1"))
        for action in actions:
            with self.assertRaises(PermissionError):
                action()
        self.frappe.get_list.assert_not_called()
        self.hidden["read"][DOCTYPES["category"]].clear()
        self.hidden_fields("read", "category", "request_digest")
        self.assertEqual(self.module.read_record(parent["name"], "category")["major_name"], parent["major_name"])
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "category", "C-1")

    def test_optional_hidden_fields_are_not_backfilled_and_cannot_be_blind_written(self):
        parent = self.parent()
        for archive, optional in ((parent, "owner_name"), (self.detail(parent), "material")):
            kind = archive["record_type"]
            self.hidden_fields("read", kind, optional)
            read = self.module.read_record(archive["name"], kind)
            listed = self.module.list_records(kind, "C-1", parent["name"] if kind == "detail" else None)[0]
            self.assertNotIn(optional, read)
            self.assertNotIn(optional, read["field_meta"])
            self.assertNotIn(optional, listed)
            before = deepcopy((self.documents, self.versions))
            with self.assertRaises(PermissionError):
                self.update(archive, **{optional: "隐藏字段盲写"})
            self.assertEqual((self.documents, self.versions), before)

    def test_optional_projection_omission_stays_absent_and_missing_required_projection_rejects(self):
        parent = self.parent()
        row = Record(self.documents[parent["name"]])
        row.pop("owner_name")
        self.forced_lists[DOCTYPES["category"]] = [row]
        self.assertNotIn("owner_name", self.module.read_record(parent["name"], "category"))
        self.assertNotIn("owner_name", self.module.list_records("category", "C-1")[0])
        row.pop("major_name")
        self.forced_lists[DOCTYPES["category"]] = [row]
        with self.assertRaises(PermissionError):
            self.module.read_record(parent["name"], "category")
        with self.assertRaises(PermissionError):
            self.module.list_records("category", "C-1")

    def test_native_form_rejects_changed_hidden_field_but_preserves_unchanged_hidden_empty_original(self):
        parent = self.parent()
        for archive, optional in ((parent, "owner_name"), (self.detail(parent), "material")):
            kind = archive["record_type"]
            for permission in ("read", "write"):
                self.hidden_fields(permission, kind, optional)
                document = self.get_doc(DOCTYPES[kind], archive["name"])
                document.set(optional, "不可操作原文")
                with self.subTest(kind=kind, permission=permission), self.assertRaises(PermissionError):
                    document.save()
                self.hidden[permission][DOCTYPES[kind]].clear()
            self.documents[archive["name"]][optional] = ""
            self.hidden_fields("read", kind, optional)
            self.hidden_fields("write", kind, optional)
            field = "major_name" if kind == "category" else "minor_name"
            self.update(archive, **{field: "仅修改可见原文"})
            self.assertEqual(self.documents[archive["name"]][optional], "")

    def test_field_write_denial_and_native_read_only_metadata_are_enforced_before_mutation(self):
        parent = self.parent()
        for archive in (parent, self.detail(parent)):
            kind = archive["record_type"]
            field = "major_name" if kind == "category" else "minor_name"
            self.hidden_fields("write", kind, field)
            before = deepcopy((self.documents, self.versions))
            with self.assertRaises(PermissionError):
                self.update(archive, **{field: "无权限写入"})
            self.assertEqual((self.documents, self.versions), before)
            self.hidden["write"][DOCTYPES[kind]].clear()
            df = next(row for row in self.schemas[kind]["fields"] if row["fieldname"] == field)
            df["read_only"] = 1
            with self.assertRaises(PermissionError):
                self.update(archive, **{field: "元数据只读写入"})
            self.assertEqual((self.documents, self.versions), before)

    def test_native_list_rows_are_rechecked_against_actual_document_and_parent_scope(self):
        parent = self.parent()
        detail = self.detail(parent)
        for archive in (parent, detail):
            kind = archive["record_type"]
            self.forced_lists[DOCTYPES[kind]] = [Record(self.documents[archive["name"]])]
            self.document_denials.add(archive["name"])
            self.assertEqual(self.module.list_records(kind, "C-1", parent["name"] if kind == "detail" else None), [])
            self.document_denials.clear()
            self.documents[archive["name"]]["company"] = "C-2"
            self.assertEqual(self.module.list_records(kind, "C-1", parent["name"] if kind == "detail" else None), [])
            self.documents[archive["name"]]["company"] = "C-1"
        self.forced_lists.pop(DOCTYPES["category"], None)
        other = self.parent()
        self.documents[detail["name"]]["category"] = other["name"]
        self.assertEqual(self.module.list_records("detail", "C-1", parent["name"]), [])

    def test_search_uses_only_readable_raw_text_fields_and_returns_version(self):
        parent = self.parent()
        detail = self.detail(parent)
        self.assertEqual(self.module.list_records("category", "C-1", search="001")[0]["version"], parent["version"])
        self.assertEqual(self.module.list_records("detail", "C-1", parent["name"], search="木材")[0]["name"], detail["name"])
        self.hidden_fields("read", "detail", "material")
        self.assertEqual(self.module.list_records("detail", "C-1", parent["name"], search="木材"), [])
        detail_calls = [call for call in self.frappe.get_list.call_args_list if call.args[0] == DOCTYPES["detail"]]
        self.assertNotIn("material", {row[0] for row in detail_calls[-1].kwargs.get("or_filters", [])})

    def test_primary_key_rename_and_merge_are_denied_while_display_text_remains_editable(self):
        parent = self.parent()
        detail = self.detail(parent)
        parent = self.update(parent, major_name="修改显示大类", legacy_code="修改代码")
        detail = self.update(detail, minor_name="修改显示小类")
        for kind, archive in (("category", parent), ("detail", detail)):
            self.assertEqual(self.schemas[kind]["allow_rename"], 0)
            before = deepcopy((self.documents, self.versions))
            for merge in (False, True):
                with self.subTest(kind=kind, merge=merge), self.assertRaisesRegex(ValueError, "类别主键不能重命名"):
                    self.module.prevent_rename(self.get_doc(DOCTYPES[kind], archive["name"]),
                                               archive["name"], "OTHER-KEY", merge)
                self.assertEqual((self.documents, self.versions), before)

    def test_hidden_uuid_collision_is_generic_and_keeps_prior_messages_and_data(self):
        payload = self.payload()
        parent = self.module.save_record(payload)
        self.document_denials.add(parent["name"])
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "category", "C-1"), {"status": "not_found"})
        self.frappe.local.message_log = [{"message": "earlier independent message"}]
        before = deepcopy((self.documents, self.versions))
        with self.assertRaisesRegex(ValueError, "新建请求已使用") as caught:
            self.module.save_record(payload)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(parent["name"], str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.frappe.local.message_log, [{"message": "earlier independent message"}])
        self.assertEqual((self.documents, self.versions), before)

    def test_native_receipt_document_read_is_required_even_when_list_projection_is_visible(self):
        payload = self.payload()
        parent = self.module.save_record(payload)
        self.forced_lists[DOCTYPES["category"]] = [Record(self.documents[parent["name"]])]
        self.document_denials.add((parent["name"], "read"))
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "category", "C-1")
        before = deepcopy((self.documents, self.versions))
        with self.assertRaises(PermissionError):
            self.module.save_record(payload)
        self.assertEqual((self.documents, self.versions), before)

    def test_post_save_failure_rolls_back_records_versions_all_queues_and_prior_context(self):
        parent = self.parent()
        detail = self.detail(parent)
        callbacks = {}
        for key in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            callbacks[key] = lambda: None
            getattr(self.db, key).add(callbacks[key])
        self.frappe.local._realtime_log = ["original event"]
        self.frappe.flags.currently_saving = ["original context"]
        before = deepcopy((self.documents, self.versions))
        self.fail_after_save = True
        for archive in (parent, detail):
            field = "major_name" if archive["record_type"] == "category" else "minor_name"
            with self.subTest(kind=archive["record_type"]), self.assertRaisesRegex(ValueError, "native post-save failure"):
                self.update(archive, **{field: "failed original rename"})
            self.assertEqual((self.documents, self.versions), before)
            for key, callback in callbacks.items():
                self.assertEqual(getattr(self.db, key)._functions, [callback])
            self.assertEqual(self.frappe.local._realtime_log, ["original event"])
            self.assertEqual(self.frappe.flags.currently_saving, ["original context"])

    def test_failed_creation_restores_absent_context_and_empty_queues(self):
        self.fail_after_save = True
        with self.assertRaisesRegex(ValueError, "native post-save failure"):
            self.parent()
        self.assertEqual((self.documents, self.versions), ({}, []))
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))
        for key in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            self.assertEqual(getattr(self.db, key)._functions, [])

    def test_success_keeps_existing_and_new_native_after_commit_callbacks(self):
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
            archive = self.parent()
        self.assertIn(archive["name"], self.documents)
        self.assertEqual(self.db.after_commit._functions, [existing, native_notification])

    def test_invalid_types_names_text_uuid_versions_and_server_payload_fields_reject(self):
        invalid = ({"record_type": "other"}, {"major_name": " "}, {"major_name": 1}, {"major_name": "a" * 141},
                   {"legacy_code": []}, {"owner_name": {}}, {"region": 1}, {"request_id": str(uuid4()).upper()},
                   {"request_id": "not-a-uuid"}, {"request_id": "00000000-0000-1000-8000-000000000000"},
                   {"request_digest": "client-forged"}, {"version": "old"}, {"doctype": DOCTYPES["category"]},
                   {"field_meta": {}}, {"can_write": True}, {"_jf_category_server": True})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parent(**changes)
        self.assertFalse(self.documents)
        parent = self.parent()
        for value in (None, "", " ", 1):
            with self.subTest(name=value), self.assertRaises(ValueError):
                self.module.read_record(value, "category")
        with self.assertRaises((ValueError, RuntimeError)):
            self.module.save_record({"record_type": "category", "name": parent["name"], "major_name": "缺少版本"})
        with self.assertRaises(ValueError):
            self.module.save_record("not-json")
        for payload in ([], None, "[]", "null"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.module.save_record(payload)
        for value in (" ", None, 1, "a" * 141):
            with self.subTest(minor=value), self.assertRaises(ValueError):
                self.detail(parent, minor_name=value)

    def test_delete_is_explicitly_unimplemented_for_every_actor(self):
        for user in ("stock-manager@example.invalid", "Administrator"):
            self.frappe.session.user = user
            with self.assertRaisesRegex(ValueError, "删除尚未实现"):
                self.module.prevent_delete(None)


if __name__ == "__main__":
    unittest.main()
