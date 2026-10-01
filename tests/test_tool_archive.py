"""Actual tool controllers/API with isolated native permissions and transactions."""

import ast
from copy import deepcopy
from datetime import date, datetime
from functools import wraps
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4


ROOT = Path(__file__).parents[1]
DOCTYPES = {"mold": "Jifeng Mold", "plate": "Jifeng Plate"}


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


class Document(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.metadata[self.doctype]

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, field, value):
        self[field] = value

    def has_permission(self, action):
        env = self.environment
        return ((self.doctype, action) not in env.denials and (self.name, action) not in env.denials
                and self.company not in env.denied_companies)

    def check_permission(self, action):
        self.environment.calls.append((self.doctype, self.name, action))
        if not self.has_permission(action):
            raise PermissionError("native document permission")

    def is_downloadable(self):
        return self.name not in self.environment.download_denials

    @property
    def unique_url(self):
        return str(self.file_url) + "?_file=" + self.name

    def get_doc_before_save(self):
        row = self.environment.documents.get(self.name)
        return self.environment.make(row) if row else None

    def save(self):
        env, new = self.environment, self.is_new()
        self.check_permission("create" if new else "write")
        if new:
            self.before_insert()
        self.validate()
        for row in env.documents.values():
            if row["doctype"] == self.doctype and row["name"] != self.name:
                if row.get("request_id") == self.request_id or row.get(env.module.TITLE[env.kind(self.doctype)]) == self.get(
                        env.module.TITLE[env.kind(self.doctype)]):
                    env.frappe.local.message_log = [{"message": "private native unique SQL"}]
                    raise KeyError("private unique SQL")
        env.tick += 1
        if new:
            env.series += 1
            self.name = self.doctype.replace(" ", "-") + "-" + str(env.series)
        self.modified = datetime(2026, 10, 1, 12, 0, 0, env.tick)
        self.creation = self.creation or self.modified
        self.owner = self.owner or env.frappe.session.user
        self.modified_by = env.frappe.session.user
        self["__islocal"] = 0
        env.documents[self.name] = deepcopy(dict(self))
        if not new:
            env.versions.append((self.doctype, self.name, deepcopy(dict(self))))
        if env.fail_after_save:
            for key in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
                getattr(env.frappe.db, key).add(lambda: None)
            env.frappe.local._realtime_log = ["new event"]
            env.frappe.flags.currently_saving = ["new stack"]
            raise ValueError("post-save failure")
        return self


class TestToolArchive(unittest.TestCase):
    def setUp(self):
        self.schemas = {kind: json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype" / ("jifeng_" + kind)
            / ("jifeng_" + kind + ".json")).read_text(encoding="utf-8")) for kind in DOCTYPES}
        self.documents, self.versions, self.snapshots = {}, [], {}
        self.calls, self.tick, self.series, self.fail_after_save = [], 1, 0, False
        self.denials, self.denied_companies, self.download_denials = set(), set(), set()
        self.hidden, self.forced_rows = {}, None
        self.fields = {dt: {df["fieldname"]: Record(df) for df in self.schemas[kind]["fields"]}
                       for kind, dt in DOCTYPES.items()}
        for dt, fields in {"Company": ("name",), "Item": ("name",), "Employee": ("name", "company"),
                "File": ("name", "file_name", "file_url", "creation", "is_private", "attached_to_doctype",
                         "attached_to_name", "attached_to_field")}.items():
            self.fields[dt] = {field: Record(fieldname=field, fieldtype="Data") for field in fields}
        self.metadata = {dt: types.SimpleNamespace(get_field=lambda name, dt=dt: self.fields[dt].get(name))
                         for dt in self.fields}
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="stock@example.invalid")
        self.frappe.flags, self.frappe.local = Record(), types.SimpleNamespace()
        self.frappe.PermissionError, self.frappe.DoesNotExistError = PermissionError, LookupError
        self.frappe.TimestampMismatchError = RuntimeError
        self.frappe.DuplicateEntryError = self.frappe.UniqueValidationError = KeyError
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.has_permission = lambda dt, permission: (dt, permission) not in self.denials
        self.frappe.get_meta = lambda dt: self.metadata[dt]
        self.frappe.new_doc = lambda dt: self.make({"doctype": dt, "name": None, "company": "DENIED-DEFAULT",
                                                  "maker": "DEFAULT-EMP", "__islocal": 1})
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.db = types.SimpleNamespace(savepoint=self.savepoint, rollback=self.rollback,
            **{name: Queue() for name in ("before_commit", "after_commit", "before_rollback", "after_rollback")})
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda dt, permission_type, **kwargs: (
            set(self.fields[dt]) | {"name", "modified", "owner", "creation", "modified_by"}) - self.hidden.get(
                (dt, permission_type), set())
        native_document = types.ModuleType("frappe.model.document")
        native_document.Document = Document
        utils = types.ModuleType("frappe.utils")
        utils.today = lambda: "2026-10-01"
        utils.getdate = lambda value: value if isinstance(value, date) else date.fromisoformat(value)
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        separation._atomic = self.function("jifeng_erp/personnel_separation.py", "_atomic",
            {"frappe": self.frappe, "deepcopy": deepcopy, "wraps": wraps, "uuid4": uuid4})
        product = types.ModuleType("jifeng_erp.product_archive")
        product._private_url = self.function("jifeng_erp/product_archive.py", "_private_url",
                                             {"unquote": unquote, "urlsplit": urlsplit})
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils,
            "frappe.model.document": native_document, "jifeng_erp.personnel_separation": separation,
            "jifeng_erp.product_archive": product})
        scope.start()
        self.addCleanup(scope.stop)
        access = self.load("tool_access_test", "jifeng_erp/catalog_access.py")
        scope_access = patch.dict(sys.modules, {"jifeng_erp.catalog_access": access})
        scope_access.start()
        self.addCleanup(scope_access.stop)
        self.module = self.load("tool_archive_test", "jifeng_erp/tool_archive.py")
        tool_scope = patch.dict(sys.modules, {"jifeng_erp.tool_archive": self.module})
        tool_scope.start()
        self.addCleanup(tool_scope.stop)
        self.classes = {DOCTYPES[kind]: getattr(self.load("tool_" + kind + "_test",
            "jifeng_erp/jifeng_erp/doctype/jifeng_" + kind + "/jifeng_" + kind + ".py"),
            "Jifeng" + kind.title()) for kind in DOCTYPES}
        Document.environment = self
        self.documents.update({"ITEM": {"doctype": "Item", "name": "ITEM", "unknown": "unchanged"},
            "EMP": {"doctype": "Employee", "name": "EMP", "company": "C-1", "bank": "private", "history": ["exact"]}})

    def function(self, path, name, namespace):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "actual_" + name, "exec"), namespace)
        return namespace[name]

    def load(self, name, path):
        spec = importlib.util.spec_from_file_location(name, ROOT / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def kind(self, doctype):
        return next(kind for kind, dt in DOCTYPES.items() if dt == doctype)

    def make(self, row):
        return self.classes.get(row["doctype"], Document)(deepcopy(row))

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Company":
            return self.make({"doctype": doctype, "name": name, "company": name})
        if name not in self.documents or self.documents[name]["doctype"] != doctype:
            raise LookupError("not found")
        return self.make(self.documents[name])

    def get_list(self, doctype, filters, fields, **kwargs):
        if self.forced_rows is not None:
            return deepcopy(self.forced_rows)
        result = []
        for row in self.documents.values():
            if row["doctype"] != doctype or not self.make(row).has_permission("read"):
                continue
            if not all(row.get(field) == value for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(str(value).strip("%") in str(row.get(field) or "")
                    for field, operator, value in kwargs["or_filters"]):
                continue
            result.append(Record({field: row.get(field) for field in fields
                                  if field not in self.hidden.get((doctype, "read"), set())}))
        return result[:kwargs.get("limit_page_length", 100)]

    def get_all(self, doctype, filters, **kwargs):
        self.assertEqual(doctype, "File", "Only exact bound File IDs may use an unscoped lookup")
        result = [name for name, row in self.documents.items()
                  if row["doctype"] == doctype and all(row.get(field) == value for field, value in filters.items())]
        return result[:kwargs["limit_page_length"]] if kwargs.get("limit_page_length") else result

    def savepoint(self, point):
        self.snapshots[point] = deepcopy((self.documents, self.versions, self.series))

    def rollback(self, save_point):
        self.documents, self.versions, self.series = deepcopy(self.snapshots[save_point])

    def payload(self, kind="mold", **updates):
        values = {"company": "C-1", "legacy_number": " 007 原文 ", "legacy_copy": 1,
            "item_code": "ITEM", "maker": "EMP", "location": " 原库位 "}
        values.update({"mold_no": " M-1 ", "product_code": " P-RAW "} if kind == "mold" else {
            "plate_no": " P-1 ", "plate_type": " 类型原文 ", "product_code": " 旧产品原文 ",
            "quantity": " 001.0000000000000007 ", "assessment_score": "-0"})
        return {"record_type": kind, "request_id": str(uuid4()), "values": values | updates}

    def saved(self, kind="mold", **updates):
        return self.module.save_record(self.payload(kind, **updates))

    def update(self, view, **values):
        return self.module.save_record({"record_type": view["record_type"], "name": view["name"],
                                        "version": view["version"], "values": values})

    def file(self, view, name="FILE", **updates):
        row = {"doctype": "File", "name": name, "file_name": "tiny.png", "file_url": "/private/files/tiny.png",
            "is_private": 1, "attached_to_doctype": DOCTYPES[view["record_type"]], "attached_to_name": view["name"],
            "attached_to_field": "image", "creation": datetime(2026, 10, 1)} | updates
        self.documents[name] = row
        return row

    def test_metadata_preserves_existing_roles_business_identity_and_original_order(self):
        for kind, schema in self.schemas.items():
            fields = self.fields[DOCTYPES[kind]]
            self.assertTrue(schema["track_changes"])
            self.assertFalse(schema["allow_rename"])
            self.assertTrue(fields["company"].set_only_once)
            self.assertTrue(fields[self.module.TITLE[kind]].unique)
            self.assertTrue(fields[self.module.TITLE[kind]].reqd)
            self.assertFalse(fields["legacy_number"].unique)
            self.assertEqual({role["role"] for role in schema["permissions"]},
                             {"System Manager", "Stock Manager", "Manufacturing Manager"})
            self.assertTrue(all(fields[name].no_copy and fields[name].read_only and fields[name].hidden
                                for name in self.module.REQUEST_FIELDS))
        self.assertEqual(self.fields[DOCTYPES["mold"]]["quantity"].fieldtype, "Float")
        self.assertEqual(self.fields[DOCTYPES["plate"]]["quantity"].fieldtype, "Data")

    def test_init_explicit_company_and_image_unavailable_until_saved(self):
        self.denied_companies.add("DENIED-DEFAULT")
        for kind in DOCTYPES:
            empty = self.module.init_record(kind)
            self.assertIsNone(empty["values"]["company"])
            self.assertIsNone(empty["values"]["maker"])
            self.assertTrue(empty["field_meta"]["image"]["read_only"])
            self.assertEqual(self.module.init_record(kind, "C-1")["values"]["company"], "C-1")
        self.assertNotIn(("Company", "DENIED-DEFAULT", "select"), self.calls)
        self.assertTrue(all(name != "DEFAULT-EMP" for doctype, name, action in self.calls))

    def test_plate_init_only_suggests_independent_uuid_business_numbers_without_writes(self):
        before = deepcopy((self.documents, self.versions, self.series, self.snapshots))
        views = [self.module.init_record("plate", company) for company in (None, "C-1", "C-1")]
        numbers = [view["values"]["plate_no"] for view in views]
        self.assertEqual(len(set(numbers)), 3)
        for view, number in zip(views, numbers):
            self.assertTrue(number.startswith("JF-PL-"))
            self.assertEqual(UUID(number.removeprefix("JF-PL-")).version, 4)
            self.assertEqual(str(UUID(number.removeprefix("JF-PL-"))), number.removeprefix("JF-PL-"))
            self.assertIsNone(view["values"]["legacy_number"])
            self.assertIsNone(view["name"])
            self.assertNotIn("request_id", view)
        self.assertIsNone(self.module.init_record("mold", "C-1")["values"]["mold_no"])
        self.assertEqual((self.documents, self.versions, self.series, self.snapshots), before)

    def test_plate_init_field_permissions_do_not_generate_hidden_or_readonly_numbers(self):
        doctype = DOCTYPES["plate"]
        before = deepcopy((self.documents, self.versions, self.series))
        with patch.object(self.module, "uuid4", side_effect=AssertionError("unauthorized draft number")):
            self.hidden[doctype, "write"] = {"plate_no"}
            readonly = self.module.init_record("plate", "C-1")
            self.assertTrue(readonly["field_meta"]["plate_no"]["read_only"])
            self.assertIsNone(readonly["values"]["plate_no"])
            self.hidden.clear()
            self.fields[doctype]["plate_no"].read_only = 1
            self.assertIsNone(self.module.init_record("plate")["values"]["plate_no"])
            self.fields[doctype]["plate_no"].read_only = 0
            self.hidden[doctype, "read"] = {"plate_no"}
            with self.assertRaises(PermissionError):
                self.module.init_record("plate", "C-1")
            self.hidden.clear()
            self.denials.add((doctype, "create"))
            with self.assertRaises(PermissionError):
                self.module.init_record("plate")
        self.assertEqual((self.documents, self.versions, self.series), before)

    def test_plate_default_number_save_manual_edit_and_original_receipt_replay(self):
        candidate = self.module.init_record("plate", "C-1")
        payload = {"record_type": "plate", "request_id": str(uuid4()),
                   "values": candidate["values"] | {"legacy_number": " 旧号原文 "}}
        saved = self.module.save_record(payload)
        self.assertEqual(saved["values"]["plate_no"], candidate["values"]["plate_no"])
        self.assertEqual(saved["values"]["legacy_number"], " 旧号原文 ")
        self.assertNotEqual(saved["name"], saved["values"]["plate_no"])
        edited = self.update(saved, plate_no=" EXPLICIT-BUSINESS-NUMBER ")
        self.assertEqual(edited["values"]["plate_no"], "EXPLICIT-BUSINESS-NUMBER")
        self.assertEqual(edited["request_digest"], saved["request_digest"])
        before = deepcopy((self.documents, self.versions, self.series))
        self.assertEqual(self.module.save_record(payload), edited)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "plate", "C-1")["archive"], edited)
        self.assertEqual(self.module.read_record(saved["name"], "plate"), edited)
        self.module.init_record("plate", "C-1")
        self.assertEqual((self.documents, self.versions, self.series), before)

    def test_raw_text_and_reference_amounts_never_update_item_or_employee(self):
        before = deepcopy((self.documents["ITEM"], self.documents["EMP"]))
        mold = self.saved(amount="12.25", quantity="2.5", remark="说明\n次行")
        plate = self.saved("plate", notes=" 说明\n次行 ")
        self.assertEqual((mold["values"]["mold_no"], mold["values"]["product_code"]), ("M-1", "P-RAW"))
        self.assertEqual((mold["values"]["amount"], mold["values"]["quantity"]), (12.25, 2.5))
        self.assertEqual(plate["values"]["quantity"], " 001.0000000000000007 ")
        self.assertEqual(plate["values"]["assessment_score"], "-0")
        self.assertEqual(plate["values"]["product_code"], " 旧产品原文 ")
        self.assertEqual((self.documents["ITEM"], self.documents["EMP"]), before)
        self.assertNotIn(("Employee", "EMP", "read"), self.calls)
        self.assertIn(("Employee", "EMP", "select"), self.calls)

    def test_plate_new_none_score_defaults_one_and_explicit_empty_stays_empty(self):
        for value, expected in ((None, "1"), ("", ""), ("-0", "-0")):
            view = self.saved("plate", plate_no=str(uuid4()), assessment_score=value)
            self.assertEqual(view["values"]["assessment_score"], expected)

    def test_uuid_replay_resolves_current_record_without_rewriting_after_edits(self):
        payload = self.payload()
        view = self.module.save_record(payload)
        updated = self.update(view, location="后续原文")
        before = deepcopy((self.documents, self.versions, self.series))
        replay = self.module.save_record(payload)
        self.assertEqual(replay["values"]["location"], "后续原文")
        self.assertEqual(replay["version"], updated["version"])
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "mold", "C-1")["archive"], replay)
        self.assertEqual((self.documents, self.versions, self.series), before)
        with self.assertRaisesRegex(ValueError, "冲突"):
            self.module.save_record(payload | {"values": payload["values"] | {"amount": 99}})

    def test_uuid_identity_is_separate_for_two_doctypes_and_cannot_cross_company(self):
        payload = self.payload()
        mold = self.module.save_record(payload)
        plate = self.module.save_record(self.payload("plate") | {"request_id": payload["request_id"]})
        self.assertNotEqual(mold["name"], plate["name"])
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "plate", "C-2"), {"status": "not_found"})
        self.denied_companies.add("C-1")
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "mold", "C-1")

    def test_partial_edit_preserves_unknown_fields_children_and_initial_receipt(self):
        view = self.saved()
        extra = {"unknown_raw": " future ", "custom_rows": [{"name": "ROW", "idx": 1, "content": "unchanged"}]}
        self.documents[view["name"]].update(extra)
        updated = self.update(view, legacy_number="另原编号")
        self.assertEqual({field: self.documents[view["name"]][field] for field in extra}, extra)
        self.assertEqual(updated["request_digest"], view["request_digest"])
        self.assertEqual(updated["request_id"], view["request_id"])

    def test_stale_company_metadata_and_forged_marker_rejections_do_not_write(self):
        view = self.saved()
        newer = self.update(view, location="新库位")
        before = deepcopy((self.documents, self.versions, self.series))
        with self.assertRaises(RuntimeError):
            self.update(view, location="旧写")
        for values in ({"company": "C-2"}, {"request_id": str(uuid4())}, {"doctype": "User"},
                       {"flags": {"ignore_permissions": True}}, {"_jf_tool_archive_server": "forged"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.update(newer, **values)
        self.assertEqual((self.documents, self.versions, self.series), before)

    def test_create_read_write_field_and_link_permissions_are_enforced(self):
        for denial in ((DOCTYPES["mold"], "create"), ("ITEM", "read"), ("ITEM", "select"), ("EMP", "select")):
            self.denials.add(denial)
            with self.subTest(denial=denial), self.assertRaises(PermissionError):
                self.saved()
            self.denials.clear()
        self.documents["EMP"]["company"] = "C-2"
        with self.assertRaisesRegex(ValueError, "当前公司"):
            self.saved()
        self.documents["EMP"]["company"] = "C-1"
        view = self.saved()
        self.hidden[DOCTYPES["mold"], "read"] = {"remark"}
        packed = self.module.read_record(view["name"], "mold")
        self.assertNotIn("remark", packed["values"])
        self.assertNotIn("remark", packed["field_meta"])
        with self.assertRaises(PermissionError):
            self.update(view, remark="hidden")
        self.hidden[DOCTYPES["mold"], "write"] = {"location"}
        self.assertTrue(self.module.read_record(view["name"], "mold")["field_meta"]["location"]["read_only"])
        with self.assertRaises(PermissionError):
            self.update(view, location="readonly")

    def test_native_employee_select_search_projection_without_company_keeps_server_membership_guard(self):
        # Frappe Meta.get_permitted_fieldnames(select) returns get_search_fields;
        # company need not be in that projection even when native select is true.
        self.hidden["Employee", "select"] = {"company"}
        self.hidden["Employee", "read"] = {"company"}
        self.denials.add(("Employee", "read"))
        for kind in DOCTYPES:
            view = self.saved(kind)
            self.assertEqual(view["values"]["maker"], "EMP")
            self.assertNotIn("bank", str(view))
        self.assertIn(("Employee", "EMP", "select"), self.calls)
        self.assertNotIn(("Employee", "EMP", "read"), self.calls)
        self.documents["EMP"]["company"] = "C-2"
        with self.assertRaisesRegex(ValueError, "当前公司"):
            self.saved(mold_no="FOREIGN")
        self.documents["EMP"]["company"] = "C-1"
        self.denials.add(("EMP", "select"))
        with self.assertRaises(PermissionError):
            self.saved(mold_no="HIDDEN")

    def test_copy_link_does_not_copy_values_and_rejects_cycles_hidden_or_other_company(self):
        source = self.saved("plate", plate_no="SOURCE")
        target = self.saved("plate", plate_no="TARGET", copy_from=source["name"], legacy_number="独立编号")
        self.assertEqual(target["values"]["legacy_number"], "独立编号")
        with self.assertRaisesRegex(ValueError, "循环"):
            self.update(source, copy_from=target["name"])
        self.denials.add((source["name"], "read"))
        with self.assertRaises(PermissionError):
            self.update(target, notes="changed")
        self.denials.clear()
        self.documents[source["name"]]["company"] = "C-2"
        with self.assertRaisesRegex(ValueError, "同一公司"):
            self.update(target, notes="changed")

    def test_native_insert_server_receipt_client_metadata_and_force_rename_are_guarded(self):
        doc = self.frappe.new_doc(DOCTYPES["plate"])
        doc.update(self.module._defaults("plate") | {"company": "C-1", "plate_no": "NATIVE"})
        doc.save()
        self.assertEqual(UUID(doc.request_id).version, 4)
        self.assertEqual(len(doc.request_digest), 64)
        before = deepcopy((self.documents, self.versions, self.series))
        for field in ("company", "request_id", "request_digest"):
            changed = self.get_doc(doc.doctype, doc.name)
            changed[field] = "C-2" if field == "company" else "forged"
            with self.subTest(field=field), self.assertRaises(ValueError):
                changed.save()
        forged = self.frappe.new_doc(DOCTYPES["plate"])
        forged.update(self.module._defaults("plate") | {"company": "C-1", "plate_no": "FORGED", "request_id": str(uuid4())})
        forged.__dict__["_jf_tool_archive_server"] = "copied"
        with self.assertRaisesRegex(ValueError, "服务器"):
            forged.save()
        with self.assertRaisesRegex(ValueError, "重命名"):
            doc.before_rename(doc.name, "RENAMED", True)
        self.assertEqual((self.documents, self.versions, self.series), before)

    def test_exact_private_image_read_and_download_and_canonical_url_are_all_required(self):
        view = self.saved()
        exact = self.file(view)
        image = self.update(view, image=exact["file_url"])
        self.assertEqual(image["values"]["image"], exact["file_url"])
        self.assertEqual(image["image_states"]["image"]["url"], exact["file_url"] + "?_file=FILE")
        for restriction in ("read", "download", "field", "parent", "public"):
            with self.subTest(restriction=restriction):
                old = deepcopy(self.documents["FILE"])
                if restriction == "read":
                    self.denials.add(("FILE", "read"))
                elif restriction == "download":
                    self.download_denials.add("FILE")
                else:
                    self.documents["FILE"][{"field": "attached_to_field", "parent": "attached_to_name", "public": "is_private"}[
                        restriction]] = {"field": "remark", "parent": "OTHER", "public": 0}[restriction]
                self.file(view, "ALIAS", attached_to_name="OTHER")
                packed = self.module.read_record(view["name"], "mold")
                self.assertNotIn("image", packed["values"])
                self.assertEqual(packed["image_states"]["image"], {"state": "附件不可用", "url": ""})
                self.assertEqual(self.module.list_image_attachments(view["name"], "mold"), [])
                self.documents["FILE"] = old
                self.denials.clear()
                self.download_denials.clear()
        with self.assertRaises(PermissionError):
            self.update(image, image=exact["file_url"] + "?_file=FILE")

    def test_new_image_rejected_historical_bad_url_hidden_and_preserved_on_unrelated_edit(self):
        with self.assertRaises(ValueError):
            self.saved(image="/private/files/new.png")
        view = self.saved()
        self.documents[view["name"]]["image"] = "https://example.invalid/historical.png"
        packed = self.module.read_record(view["name"], "mold")
        self.assertNotIn("image", packed["values"])
        self.update(packed, location="新库位")
        self.assertEqual(self.documents[view["name"]]["image"], "https://example.invalid/historical.png")
        self.assertTrue(all("http" not in str(call) for call in self.frappe.get_all.call_args_list))

    def test_file_field_read_loss_hides_address_and_forbids_candidate_or_explicit_adoption(self):
        view = self.saved()
        exact = self.file(view)
        view = self.update(view, image=exact["file_url"])
        self.hidden["File", "read"] = {"file_url"}
        hidden = self.module.read_record(view["name"], "mold")
        self.assertNotIn("image", hidden["values"])
        self.assertEqual(hidden["image_states"]["image"], {"state": "附件不可用", "url": ""})
        with self.assertRaises(PermissionError):
            self.module.list_image_attachments(view["name"], "mold")
        with self.assertRaises(PermissionError):
            self.update(hidden, image=exact["file_url"])

    def test_read_list_projection_cap_and_attachment_cap_do_not_fallback_to_hidden_doc(self):
        view = self.saved()
        self.forced_rows = [Record(name=view["name"], modified=view["version"], company="C-1", mold_no="M-1", scrapped=0)]
        with self.assertRaises(PermissionError):
            self.module.list_records("mold", "C-1")
        self.forced_rows = [Record()] * 101
        with self.assertRaisesRegex(ValueError, "缩小"):
            self.module.list_records("mold", "C-1")
        self.forced_rows = None
        for index in range(101):
            self.file(view, "F-" + str(index))
        with self.assertRaisesRegex(ValueError, "不自动复传"):
            self.module.list_image_attachments(view["name"], "mold")

    def test_native_costing_quantity_and_scrap_reactivation_rules_remain(self):
        for quantity in (0, -1, "NaN"):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.saved(quantity=quantity)
        view = self.saved(scrapped=1, scrap_date="2026-10-02", scrap_reason="损坏")
        active = self.update(view, scrapped=0)
        self.assertIsNone(active["values"]["scrap_date"])
        self.assertIsNone(active["values"]["scrap_reason"])
        payload = self.payload(mold_no="EFFECTIVE", scrap_date="2026-10-02", scrap_reason="native clears")
        result = self.module.save_record(payload)
        self.assertIsNone(result["values"]["scrap_date"])
        self.assertEqual(self.module.save_record(payload)["name"], result["name"])

    def test_post_save_failure_rolls_back_data_series_versions_queues_and_context(self):
        view = self.saved()
        def existing_callback():
            return None
        for key in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            getattr(self.frappe.db, key).add(existing_callback)
        self.frappe.local._realtime_log = ["previous event"]
        self.frappe.flags.currently_saving = ["previous stack"]
        before = deepcopy((self.documents, self.versions, self.series))
        self.fail_after_save = True
        with self.assertRaisesRegex(ValueError, "post-save"):
            self.update(view, location="fails")
        self.assertEqual((self.documents, self.versions, self.series), before)
        self.assertEqual(self.frappe.local._realtime_log, ["previous event"])
        self.assertEqual(self.frappe.flags.currently_saving, ["previous stack"])
        self.assertTrue(all(getattr(self.frappe.db, key)._functions == [existing_callback]
                            for key in ("before_commit", "after_commit", "before_rollback", "after_rollback")))


if __name__ == "__main__":
    unittest.main()
