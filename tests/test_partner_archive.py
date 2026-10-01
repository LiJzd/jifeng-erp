"""Outsource archive save/permission/source models; no native DB or GUI."""

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


class NativeDuplicateEntryError(NameError):
    """The installed Frappe duplicate-name category, distinct from validation."""


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Queue:
    def __init__(self):
        self._functions = []

    def add(self, function):
        self._functions.append(function)

    def reset(self):
        self._functions.clear()


class Meta:
    def __init__(self, fields):
        self.fields = [Record(field) for field in fields]

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)

    def get_permlevel_access(self, **kwargs):
        return []


class Document(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.metas[self.doctype]

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, name, value):
        self[name] = value

    def has_permission(self, permission):
        return (self.doctype, permission) not in self.environment.denials and self.name not in self.environment.hidden_names

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError(self.doctype + " " + permission)

    def get_doc_before_save(self):
        value = self.environment.records.get((self.doctype, self.name))
        return Document(deepcopy(value)) if value else None

    def save(self):
        env = self.environment
        self.check_permission("create" if self.is_new() else "write")
        if self.doctype == env.module.PROFILE:
            env.module.validate_profile(self)
        else:
            if not self.supplier_name or not self.supplier_type:
                raise ValueError("native supplier mandatory")
        if env.fail_save and self.doctype == env.module.PROFILE:
            env.frappe.db.after_commit.add(env.new_callback)
            env.frappe.local._realtime_log = ["native-event"]
            env.frappe.flags.currently_saving = ["native-save"]
            raise ValueError("native save failure")
        if self.is_new():
            if self.doctype == env.module.PROFILE and any(doctype == env.module.PROFILE and row.get("request_id") == self.request_id
                   for (doctype, _), row in env.records.items()):
                raise ValueError("unique request constraint")
            self.name = ("PROFILE-" + str(env.tick) if self.doctype == env.module.PROFILE else
                         self.supplier_name if env.supplier_naming == "Supplier Name" else "SUP-" + str(env.tick))
            if (self.doctype, self.name) in env.records:
                raise env.frappe.DuplicateEntryError(self.doctype, self.name, ValueError("native duplicate name"))
            self.creation = "2026-10-02T00:00:00.000001"
        env.tick += 1
        self.modified = "2026-10-02T00:00:" + str(env.tick).zfill(2) + ".123456"
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.records[(self.doctype, self.name)] = deepcopy(dict(self))
        env.saves.append(self.doctype)
        return self


class TestPartnerArchive(unittest.TestCase):
    def setUp(self):
        self.records, self.metas, self.snapshots = {}, {}, {}
        self.denials, self.hidden_names = set(), set()
        self.hidden_fields = {}
        self.saves, self.tick, self.fail_save = [], 1, False
        self.supplier_naming, self.datetime_fresh = "Supplier Name", False
        self.hook = None
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = PermissionError, RuntimeError
        self.frappe.DuplicateEntryError = NativeDuplicateEntryError
        self.frappe.session = Record(user="purchase-manager@example.invalid")
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.throw = lambda text, error=ValueError: (_ for _ in ()).throw(error(text))
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.new_doc = self.new_doc
        self.frappe.get_meta = lambda doctype: self.metas[doctype]
        self.frappe.has_permission = lambda doctype, permission: (doctype, permission) not in self.denials
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.db = types.SimpleNamespace(get_value=Mock(side_effect=self.get_value),
            savepoint=self.savepoint, rollback=self.rollback,
            **{name: Queue() for name in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        self.new_callback = Mock()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        tree = ast.parse((ROOT / "jifeng_erp/personnel_separation.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_atomic")
        namespace = {"frappe": self.frappe, "wraps": wraps, "deepcopy": deepcopy, "uuid4": uuid4}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "native_atomic", "exec"), namespace)
        separation._atomic = namespace["_atomic"]
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
            "jifeng_erp.personnel_separation": separation})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location("partner_archive_model", ROOT / "jifeng_erp/partner_archive.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        Document.environment = self
        definition = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_partner_profile/jifeng_partner_profile.json").read_text())
        self.metas[self.module.PROFILE] = Meta(definition["fields"] + [{"fieldname": name} for name in ("creation", "modified_by")])
        self.metas["Supplier"] = Meta([{"fieldname": "name"}, {"fieldname": "disabled"},
            {"fieldname": "supplier_name", "fieldtype": "Data"},
            {"fieldname": "supplier_type", "fieldtype": "Select", "options": "Company\nIndividual\nPartnership", "default": "Company", "reqd": 1},
            {"fieldname": "supplier_group", "fieldtype": "Link", "options": "Supplier Group"},
            {"fieldname": "naming_series", "fieldtype": "Select", "options": "SUP-.YYYY.-"}])
        self.metas["Company"] = Meta([])
        self.records[("Company", "Company A")] = {"doctype": "Company", "name": "Company A"}
        self.records[("Company", "Company B")] = {"doctype": "Company", "name": "Company B"}
        self.records[("Supplier", "SUP-A")] = {"doctype": "Supplier", "name": "SUP-A", "disabled": 0,
            "modified": "2026-10-02T00:00:00.123456", "supplier_name": "Shared supplier must not change"}

    def columns(self, doctype, permission_type="read", **kwargs):
        return {field.fieldname for field in self.metas[doctype].fields} - self.hidden_fields.get((doctype, permission_type), set())

    def get_doc(self, doctype, name, **kwargs):
        result = Document(deepcopy(self.records[(doctype, name)]))
        if self.datetime_fresh:
            for field in ("creation", "modified"):
                if isinstance(result.get(field), str):
                    result[field] = datetime.fromisoformat(result[field])
        if self.hook:
            self.hook(doctype, name)
        return result

    def new_doc(self, doctype):
        document = Document(doctype=doctype, __islocal=1)
        if doctype == "Supplier":
            document.supplier_type, document.naming_series, document.disabled = "Company", "SUP-.YYYY.-", 0
        return document

    def get_value(self, doctype, filters, field, **kwargs):
        return next((row.get(field) for (kind, _), row in self.records.items() if kind == doctype
                     and all(row.get(name) == value for name, value in filters.items())), None)

    def get_list(self, doctype, filters, **kwargs):
        result = []
        for (kind, name), row in self.records.items():
            if kind != doctype or name in self.hidden_names or not all(row.get(field) == value for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(test[1].strip("%") in (row.get(field) or "")
                    for field, test in kwargs["or_filters"].items()):
                continue
            result.append(name)
        result.sort(key=lambda name: (self.records[(doctype, name)].get("modified"), name), reverse=True)
        return result[:kwargs.get("limit_page_length", 1001)]

    def savepoint(self, name):
        self.snapshots[name] = deepcopy((self.records, self.saves))

    def rollback(self, save_point=None):
        self.records, self.saves = deepcopy(self.snapshots[save_point])

    def payload(self, **changes):
        result = {"kind": self.module.KIND, "company": "Company A", "request_id": str(uuid4()),
            "supplier_version": self.records[("Supplier", "SUP-A")]["modified"],
            "profile": {"supplier": "SUP-A", "legacy_number": " 0007 ", "display_name": "<b>合成工厂</b>",
                "owner_name": "合成负责人", "mobile": "000000", "reception": "合成接货人", "phone": "0000",
                "address": "合成地址\n原文", "notes": "  合成备注  ", "bank_name": "开户原文",
                "account_name": "户名原文", "bank_account": "0000123", "inactive": 0}}
        result.update(changes)
        return result

    def create(self):
        return self.module.save_archive(self.payload())

    def create_supplier_payload(self, **changes):
        payload = self.payload()
        payload.pop("supplier_version")
        payload["profile"].pop("supplier")
        payload.update(changes)
        return payload

    def edit(self, row, **values):
        return {"kind": self.module.KIND, "company": row["company"], "name": row["name"],
                "profile_version": row["profile_version"], "supplier_version": row["supplier_version"], "profile": values}

    def query(self, **kwargs):
        return self.module.query_archives(self.module.KIND, "Company A", **kwargs)

    def test_joint_save_preserves_raw_and_shared_supplier(self):
        supplier = deepcopy(self.records[("Supplier", "SUP-A")])
        row = self.create()
        self.assertEqual(row["profile"]["bank_account"], "0000123")
        self.assertEqual(row["profile"]["legacy_number"], " 0007 ")
        self.assertEqual(row["profile"]["display_name"], "<b>合成工厂</b>")
        self.assertEqual(self.records[("Supplier", "SUP-A")], supplier)
        self.assertEqual(self.saves, [self.module.PROFILE])
        self.assertNotIn("request_id", row["profile"])
        self.assertEqual(row["supplier_disabled"], False)

    def test_init_does_not_choose_supplier_or_fill_shared_contact(self):
        before = deepcopy(self.records)
        row = self.module.init_archive(self.module.KIND, "Company A")
        self.assertIsNone(row["supplier"])
        self.assertIsNone(row["profile"]["display_name"])
        self.assertEqual(row["profile"]["inactive"], 0)
        self.assertEqual(self.records, before)

    def test_init_default_number_is_new_editable_and_does_not_save_series(self):
        first = self.module.init_archive(self.module.KIND, "Company A")
        second = self.module.init_archive(self.module.KIND, "Company A")
        self.assertRegex(first["profile"]["legacy_number"], r"^WF-[0-9a-f-]{36}$")
        self.assertNotEqual(first["profile"]["legacy_number"], second["profile"]["legacy_number"])
        self.assertFalse(first["field_meta"]["legacy_number"]["read_only"])
        self.assertEqual(self.saves, [])

    def test_supplier_default_create_matches_native_defaults(self):
        result = self.module.init_archive(self.module.KIND, "Company A")
        self.assertTrue(result["can_create_supplier"])
        self.assertTrue(result["can_link_supplier"])
        self.assertEqual(result["supplier_defaults"]["supplier_type"], "Company")
        self.assertIsNone(result["supplier_defaults"]["supplier_group"])
        self.assertEqual(result["supplier_defaults"]["naming_series"], "SUP-.YYYY.-")

    def test_joint_new_supplier_and_profile_native_name_and_idempotency(self):
        payload = self.create_supplier_payload()
        before_shared = deepcopy(self.records[("Supplier", "SUP-A")])
        first = self.module.save_archive(payload)
        supplier = self.records[("Supplier", first["supplier"])]
        self.assertEqual(supplier["supplier_name"], payload["profile"]["display_name"])
        self.assertEqual(supplier["supplier_type"], "Company")
        self.assertNotIn("bank_account", supplier)
        self.assertNotIn("company", supplier)
        self.assertEqual(self.saves, ["Supplier", self.module.PROFILE])
        second = self.module.save_archive(payload)
        self.assertEqual(first, second)
        self.assertEqual(self.saves, ["Supplier", self.module.PROFILE])
        self.assertEqual(self.records[("Supplier", "SUP-A")], before_shared)

    def test_new_supplier_respects_native_series_and_no_name_matching(self):
        self.supplier_naming = "Naming Series"
        result = self.module.save_archive(self.create_supplier_payload(supplier_create={"supplier_type": "Individual"}))
        self.assertTrue(result["supplier"].startswith("SUP-"))
        self.assertEqual(self.records[("Supplier", result["supplier"])]["supplier_type"], "Individual")

    def test_supplier_group_selection_permissions_and_leaf_scope(self):
        self.records[("Supplier Group", "GROUP-A")] = {"doctype": "Supplier Group", "name": "GROUP-A", "is_group": 0}
        self.records[("Supplier Group", "GROUP-TREE")] = {"doctype": "Supplier Group", "name": "GROUP-TREE", "is_group": 1}
        self.hidden_names.add("GROUP-A")
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.create_supplier_payload(supplier_create={"supplier_group": "GROUP-A"}))
        self.hidden_names.clear()
        with self.assertRaises(ValueError):
            self.module.save_archive(self.create_supplier_payload(supplier_create={"supplier_group": "GROUP-TREE"}))
        result = self.module.save_archive(self.create_supplier_payload(supplier_create={"supplier_group": "GROUP-A"}))
        self.assertEqual(self.records[("Supplier", result["supplier"])]["supplier_group"], "GROUP-A")

    def test_supplier_type_and_series_fields_obey_read_write_and_options(self):
        for field in ("supplier_type", "naming_series"):
            with self.subTest(field=field):
                self.hidden_fields[("Supplier", "write")] = {field}
                with self.assertRaises(PermissionError):
                    self.module.save_archive(self.create_supplier_payload(supplier_create={field: "Company" if field == "supplier_type" else "SUP-.YYYY.-"}))
        self.hidden_fields.clear()
        with self.assertRaises(ValueError):
            self.module.save_archive(self.create_supplier_payload(supplier_create={"supplier_type": "unsupported"}))

    def test_duplicate_supplier_fails_without_auto_binding(self):
        payload = self.create_supplier_payload()
        self.module.save_archive(payload)
        before = deepcopy((self.records, self.saves))
        callbacks = {name: list(getattr(self.frappe.db, name)._functions) for name in
                     ("before_commit", "after_commit", "before_rollback", "after_rollback")}
        context = deepcopy((self.frappe.flags, vars(self.frappe.local)))
        second = deepcopy(payload)
        second["request_id"] = str(uuid4())
        with self.assertRaisesRegex(ValueError, "同名业务供应商已存在，请修改名称") as failure:
            self.module.save_archive(second)
        self.assertNotIsInstance(failure.exception, NativeDuplicateEntryError)
        self.assertEqual((self.records, self.saves), before)
        self.assertEqual({name: list(getattr(self.frappe.db, name)._functions) for name in callbacks}, callbacks)
        self.assertEqual((self.frappe.flags, vars(self.frappe.local)), context)
        self.assertIsNone(self.module._CREATING.get())
        self.assertEqual(self.module.resolve_creation(second["request_id"], self.module.KIND, "Company A"),
                         {"status": "not_found"})
        # A human corrects the same rejected UUID; no existing Supplier is adopted.
        second["profile"]["display_name"] = "合成修正后的独立新供应商"
        corrected = self.module.save_archive(second)
        self.assertNotEqual(corrected["supplier"], payload["profile"]["display_name"])
        self.assertEqual(corrected["profile"]["display_name"], second["profile"]["display_name"])
        self.assertEqual(self.module.resolve_creation(second["request_id"], self.module.KIND, "Company A")
                         ["archive"]["name"], corrected["name"])

    def test_non_duplicate_native_supplier_error_keeps_original_category(self):
        with patch.object(Document, "save", side_effect=RuntimeError("native unrelated failure")):
            with self.assertRaisesRegex(RuntimeError, "native unrelated failure"):
                self.module.save_archive(self.create_supplier_payload())

    def test_other_native_document_duplicate_is_not_labeled_supplier_conflict(self):
        error = NativeDuplicateEntryError("Contact", "synthetic-contact", ValueError("native duplicate"))
        with patch.object(Document, "save", side_effect=error):
            with self.assertRaises(NativeDuplicateEntryError) as failure:
                self.module.save_archive(self.create_supplier_payload())
        self.assertIs(failure.exception, error)

    def test_joint_failure_rolls_new_supplier_back(self):
        self.fail_save = True
        before = deepcopy(self.records)
        with self.assertRaises(ValueError):
            self.module.save_archive(self.create_supplier_payload())
        self.assertEqual(self.records, before)
        self.assertEqual(self.saves, [])

    def test_no_supplier_create_permission_retains_existing_link(self):
        self.denials.add(("Supplier", "create"))
        result = self.module.init_archive(self.module.KIND, "Company A")
        self.assertFalse(result["can_create_supplier"])
        self.assertTrue(result["can_link_supplier"])
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.create_supplier_payload())
        self.assertEqual(self.create()["supplier"], "SUP-A")

    def test_source_modes_cannot_mix_or_edit_shared_supplier(self):
        with self.assertRaises(ValueError):
            self.module.save_archive(self.payload(supplier_create={}))
        row = self.create()
        payload = self.edit(row, notes="new")
        payload["supplier_create"] = {"supplier_type": "Individual"}
        with self.assertRaises(ValueError):
            self.module.save_archive(payload)
        with self.assertRaises(ValueError):
            self.module.save_archive(self.create_supplier_payload(supplier_create={"supplier_name": "bad"}))

    def test_saved_string_timestamps_fresh_datetime_preserve_microseconds(self):
        self.datetime_fresh = True
        row = self.create()
        self.assertEqual(row["profile_version"], "2026-10-02T00:00:02.123456")
        self.assertEqual(row["supplier_version"], "2026-10-02T00:00:00.123456")
        edited = self.module.save_archive(self.edit(row, bank_account="2026-10-02 00:00:00.123456"))
        self.assertEqual(edited["profile"]["bank_account"], "2026-10-02 00:00:00.123456")
        self.assertEqual(edited["profile_version"], "2026-10-02T00:00:03.123456")
        self.assertEqual(self.query()["rows"][0]["profile_version"], edited["profile_version"])

    def test_joint_new_supplier_saved_strings_fresh_datetime(self):
        self.datetime_fresh = True
        result = self.module.save_archive(self.create_supplier_payload())
        self.assertEqual(result["supplier_version"], "2026-10-02T00:00:02.123456")
        self.assertEqual(result["profile_version"], "2026-10-02T00:00:03.123456")

    def test_final_projection_rechecks_supplier_and_metadata_fields(self):
        self.create()
        original = self.module._guard
        def guard(*args):
            original(*args)
            self.hidden_fields[("Supplier", "read")] = {"disabled"}
        with patch.object(self.module, "_guard", side_effect=guard), self.assertRaises(PermissionError):
            self.query()
        self.hidden_fields.clear()
        def metadata_guard(*args):
            original(*args)
            self.hidden_fields[(self.module.PROFILE, "read")] = {"creation", "modified_by"}
        with patch.object(self.module, "_guard", side_effect=metadata_guard), self.assertRaises(ValueError):
            self.query()

    def test_supplier_selection_is_explicit_and_read_only(self):
        row = self.module.read_supplier("SUP-A", self.module.KIND, "Company A")
        self.assertEqual(row["name"], "SUP-A")
        self.assertNotIn("supplier_name", row)
        self.assertEqual(self.saves, [])

    def test_same_request_reuses_without_save(self):
        payload = self.payload()
        first = self.module.save_archive(payload)
        second = self.module.save_archive(deepcopy(payload))
        self.assertEqual(first, second)
        self.assertEqual(self.saves, [self.module.PROFILE])

    def test_same_request_changed_content_rejects(self):
        payload = self.payload()
        self.module.save_archive(payload)
        payload["profile"]["notes"] = "different"
        before = deepcopy(self.records)
        with self.assertRaises(ValueError):
            self.module.save_archive(payload)
        self.assertEqual(self.records, before)

    def test_creation_resolution_exact_scope_and_uuid(self):
        payload = self.payload()
        self.assertEqual(self.module.resolve_creation(payload["request_id"], self.module.KIND, "Company A"), {"status": "not_found"})
        self.module.save_archive(payload)
        result = self.module.resolve_creation(payload["request_id"], self.module.KIND, "Company A")
        self.assertEqual(result["status"], "found")
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], self.module.KIND, "Company B")

    def test_edit_preserves_identity_and_supplier(self):
        row = self.create()
        result = self.module.save_archive(self.edit(row, notes="合成新备注", inactive=1))
        self.assertEqual(result["profile"]["inactive"], 1)
        self.assertEqual(result["supplier"], row["supplier"])
        self.assertNotEqual(result["profile_version"], row["profile_version"])

    def test_edit_rejects_profile_staleness(self):
        row = self.create()
        self.module.save_archive(self.edit(row, notes="first"))
        with self.assertRaises(RuntimeError):
            self.module.save_archive(self.edit(row, notes="second"))

    def test_edit_rejects_supplier_staleness(self):
        row = self.create()
        self.records[("Supplier", "SUP-A")]["modified"] = "2026-10-02T00:00:01.123456"
        with self.assertRaises(RuntimeError):
            self.module.save_archive(self.edit(row, notes="new"))

    def test_edit_cannot_change_identity(self):
        row = self.create()
        for field, value in (("company", "Company B"), ("supplier", "OTHER"), ("archive_kind", "发镀"), ("request_id", str(uuid4()))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.save_archive(self.edit(row, **{field: value}))

    def test_supplier_permissions_are_required_on_read_save_and_selection(self):
        row = self.create()
        self.hidden_names.add("SUP-A")
        for operation in (lambda: self.module.read_archive(row["name"], self.module.KIND, "Company A"),
                          lambda: self.module.save_archive(self.edit(row, notes="new")),
                          lambda: self.module.read_supplier("SUP-A", self.module.KIND, "Company A")):
            with self.assertRaises(PermissionError):
                operation()

    def test_query_visible_totals_and_pages(self):
        one, two = self.create(), self.create()
        first = self.query(page_size="1", page="1")
        second = self.query(page_size=1, page=2)
        self.assertEqual((first["total"], first["total_pages"]), (2, 2))
        self.assertEqual({first["rows"][0]["name"], second["rows"][0]["name"]}, {one["name"], two["name"]})
        self.assertFalse(first["rows"][0]["can_write"])
        self.assertTrue(all(field["read_only"] for field in first["rows"][0]["field_meta"].values()))
        with self.assertRaises(ValueError):
            self.query(page_size=1, page=3)

    def test_unreadable_supplier_does_not_enter_total(self):
        self.create()
        self.hidden_names.add("SUP-A")
        result = self.query()
        self.assertEqual((result["total"], result["rows"]), (0, []))

    def test_profile_user_permission_does_not_enter_total(self):
        row = self.create()
        self.hidden_names.add(row["name"])
        self.assertEqual(self.query()["total"], 0)

    def test_status_follows_archive_not_supplier_disabled(self):
        row = self.create()
        self.records[("Supplier", "SUP-A")]["disabled"] = 1
        self.assertEqual(self.query()["total"], 1)
        row = self.module.read_archive(row["name"], self.module.KIND, "Company A")
        self.module.save_archive(self.edit(row, inactive=1))
        self.assertEqual(self.query()["total"], 0)
        self.assertEqual(self.query(status="停用")["total"], 1)

    def test_hidden_optional_bank_field_never_falls_back(self):
        row = self.create()
        self.hidden_fields[(self.module.PROFILE, "read")] = {"bank_account"}
        result = self.module.read_archive(row["name"], self.module.KIND, "Company A")
        self.assertNotIn("bank_account", result["profile"])
        self.assertNotIn("bank_account", result["field_meta"])
        self.assertNotIn("bank_account", self.query()["rows"][0]["profile"])

    def test_masked_optional_bank_field_is_hidden_and_cannot_be_written(self):
        row = self.create()
        self.metas[self.module.PROFILE].get_field("bank_account").mask = 1
        result = self.module.read_archive(row["name"], self.module.KIND, "Company A")
        self.assertNotIn("bank_account", result["profile"])
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.edit(row, bank_account="new"))

    def test_hidden_required_field_rejects_instead_of_partial_scope(self):
        self.create()
        for field in self.module.REQUIRED_READ:
            self.hidden_fields[(self.module.PROFILE, "read")] = {field}
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.query()

    def test_permission_revoked_after_supplier_read_rejects_dto(self):
        row = self.create()
        count = 0
        def revoke(doctype, name):
            nonlocal count
            if doctype == "Supplier":
                count += 1
                if count == 2:
                    self.hidden_fields[(self.module.PROFILE, "read")] = {"bank_account"}
        self.hook = revoke
        with self.assertRaises(PermissionError):
            self.module.read_archive(row["name"], self.module.KIND, "Company A")

    def test_source_changes_without_modified_rejects(self):
        row = self.create()
        count = 0
        def mutate(doctype, name):
            nonlocal count
            if doctype == "Supplier":
                count += 1
                if count == 1:
                    self.records[(self.module.PROFILE, row["name"])]["notes"] = "changed without modified"
        self.hook = mutate
        with self.assertRaises(ValueError):
            self.module.read_archive(row["name"], self.module.KIND, "Company A")

    def test_query_membership_changes_rejects(self):
        self.create()
        self.frappe.get_list.side_effect = [self.get_list(self.module.PROFILE,
            {"company": "Company A", "archive_kind": self.module.KIND, "inactive": 0}), []]
        with self.assertRaises(ValueError):
            self.query()

    def test_membership_read_changes_existing_source_without_modified(self):
        row = self.create()
        count = 0
        def query(*args, **kwargs):
            nonlocal count
            count += 1
            result = self.get_list(*args, **kwargs)
            if count == 2:
                self.records[(self.module.PROFILE, row["name"])]["bank_account"] = "changed source"
            return result
        self.frappe.get_list.side_effect = query
        with self.assertRaises(ValueError):
            self.query()

    def test_membership_read_revokes_prior_field(self):
        self.create()
        count = 0
        def query(*args, **kwargs):
            nonlocal count
            count += 1
            result = self.get_list(*args, **kwargs)
            if count == 2:
                self.hidden_fields[(self.module.PROFILE, "read")] = {"bank_account"}
            return result
        self.frappe.get_list.side_effect = query
        with self.assertRaises(PermissionError):
            self.query()

    def test_masked_metadata_hidden_in_all_views(self):
        row = self.create()
        self.assertIsNotNone(row["creation"])
        self.hidden_fields[(self.module.PROFILE, "read")] = {"creation", "modified_by"}
        result = self.module.read_archive(row["name"], self.module.KIND, "Company A")
        self.assertIsNone(result["creation"])
        self.assertIsNone(result["modified_by"])
        self.assertIsNone(self.query()["rows"][0]["creation"])

    def test_supplier_selection_late_create_revocation(self):
        count = 0
        def revoke(doctype, name):
            nonlocal count
            if doctype == "Supplier":
                count += 1
                if count == 2:
                    self.denials.add((self.module.PROFILE, "create"))
        self.hook = revoke
        with self.assertRaises(PermissionError):
            self.module.read_supplier("SUP-A", self.module.KIND, "Company A")

    def test_supplier_selection_late_supplier_field_revocation(self):
        count = 0
        def revoke(doctype, name):
            nonlocal count
            if doctype == "Supplier":
                count += 1
                if count == 2:
                    self.hidden_fields[("Supplier", "read")] = {"disabled"}
        self.hook = revoke
        with self.assertRaises(PermissionError):
            self.module.read_supplier("SUP-A", self.module.KIND, "Company A")

    def test_native_failed_save_restores_database_and_callbacks(self):
        before = deepcopy(self.records)
        self.fail_save = True
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(self.records, before)
        self.assertEqual(self.saves, [])
        self.assertEqual(self.frappe.db.after_commit._functions, [])
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))
        self.assertNotIn("currently_saving", self.frappe.flags)

    def test_new_supplier_binding_requires_source_version_and_active(self):
        for change in ({"supplier_version": None}, {"supplier_version": "bad"}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.module.save_archive(self.payload(**change))
        self.records[("Supplier", "SUP-A")]["disabled"] = 1
        with self.assertRaises(ValueError):
            self.create()

    def test_invalid_requests_and_uuid_are_rejected(self):
        for token in (None, "", str(uuid4()).upper(), "not-a-uuid", 1):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.module.save_archive(self.payload(request_id=token))
        with self.assertRaises(ValueError):
            self.module.save_archive(self.payload(unsupported=1))
        with self.assertRaises(ValueError):
            self.module.save_archive("invalid-json")

    def test_invalid_page_inputs_make_no_writes(self):
        before = deepcopy(self.records)
        for value in (True, False, 0, -1, 1.5, "01", " 1", "1 ", "+1", "1e0", "1001", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.query(page=value)
        self.assertEqual(self.records, before)
        with self.assertRaises(ValueError):
            self.query(page_size=101)

    def test_guest_wrong_kind_and_company_reject(self):
        with self.assertRaises(ValueError):
            self.module.init_archive("发镀", "Company A")
        self.hidden_names.add("Company A")
        with self.assertRaises(PermissionError):
            self.query()
        self.hidden_names.clear()
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.query()

    def test_read_only_role_cannot_save(self):
        row = self.create()
        self.denials.add((self.module.PROFILE, "write"))
        result = self.module.read_archive(row["name"], self.module.KIND, "Company A")
        self.assertFalse(result["can_write"])
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.edit(row, notes="forbidden"))

    def test_native_controller_protects_identity(self):
        row = self.create()
        document = self.get_doc(self.module.PROFILE, row["name"])
        document.supplier = "OTHER"
        with self.assertRaises(ValueError):
            document.save()

    def test_native_new_doc_requires_service_creation(self):
        document = Document(doctype=self.module.PROFILE, __islocal=1, company="Company A", archive_kind=self.module.KIND)
        with self.assertRaises(ValueError):
            document.save()

    def test_schema_old_field_order_permissions_and_no_delete(self):
        definition = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_partner_profile/jifeng_partner_profile.json").read_text())
        order = [field["label"] for field in definition["fields"] if field["fieldname"] in self.module.EDIT_FIELDS]
        self.assertEqual(order, ["编号", "名称", "负责", "手机", "接货", "电话", "地址", "备注", "开户", "户名", "账号", "停用"])
        self.assertTrue(definition["track_changes"])
        self.assertFalse(any(rule.get("delete") for rule in definition["permissions"]))
        self.assertEqual(next(field for field in definition["fields"] if field["fieldname"] == "request_id")["unique"], 1)


if __name__ == "__main__":
    unittest.main()
