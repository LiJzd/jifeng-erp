"""Archive protocol and native-save model checks; no live database mutations."""

import ast
from copy import deepcopy
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

    def add(self, function):
        self._functions.append(function)

    def reset(self):
        self._functions.clear()


class Meta:
    def __init__(self, fields):
        self.fields = [Record(field) for field in fields]

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)


class NativeDocument(Record):
    environment = None

    @property
    def meta(self):
        return self.environment.metas[self.doctype]

    def is_new(self):
        return bool(self.get("__islocal"))

    def set(self, field, value):
        self[field] = value

    def has_permission(self, permission):
        return ((self.doctype, permission) not in self.environment.denials
                and self.name not in self.environment.denied_names
                and self.company not in self.environment.denied_companies)

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native " + self.doctype + " " + permission)

    def get_doc_before_save(self):
        before = self.environment.documents.get((self.doctype, self.name))
        return NativeDocument(deepcopy(before)) if before else None

    def save(self):
        environment = self.environment
        self.check_permission("create" if self.is_new() else "write")
        if self.doctype == environment.module.PROFILE:
            environment.module.validate_profile(self)
            if environment.fail_profile:
                environment.frappe.db.after_commit.add(environment.new_callback)
                environment.frappe.local._realtime_log = ["failed-item-event"]
                environment.frappe.flags.currently_saving = ["failed-item"]
                raise ValueError("profile native failure")
        if self.doctype == environment.module.DICTIONARY:
            environment.module.validate_dictionary(self)
        self.name = self.name or (self.item_code if self.doctype == "Item" else self.doctype + "-" + str(environment.tick))
        for (doctype, name), previous in environment.documents.items():
            if doctype != self.doctype or name == self.name:
                continue
            for field in self.meta.fields:
                if field.unique and self.get(field.fieldname) and previous.get(field.fieldname) == self.get(field.fieldname):
                    raise environment.frappe.DuplicateEntryError("duplicate " + field.fieldname)
        if self.doctype == "Item" and self.is_new() and (self.doctype, self.name) in environment.documents:
            raise environment.frappe.DuplicateEntryError("duplicate item")
        if self.doctype == "Item":
            environment.frappe.db.after_commit.add(environment.new_callback)
        environment.tick += 1
        self.modified = "2026-10-01T01:00:" + str(environment.tick).zfill(2)
        self["__islocal"] = 0
        environment.documents[(self.doctype, self.name)] = deepcopy(dict(self))
        environment.saves.append(self.doctype)
        return self


class TestInventoryArchive(unittest.TestCase):
    def setUp(self):
        self.documents, self.snapshots, self.metas = {}, {}, {}
        self.denials, self.denied_names, self.denied_companies = set(), set(), set()
        self.hidden, self.saves, self.tick, self.fail_profile = {}, [], 1, False
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.TimestampMismatchError = RuntimeError
        self.frappe.DuplicateEntryError = type("DuplicateEntryError", (ValueError,), {})
        self.frappe.session = Record(user="stock-manager@example.invalid")
        self.frappe.local, self.frappe.flags = types.SimpleNamespace(), Record()
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.throw = lambda text, error=ValueError: (_ for _ in ()).throw(error(text))
        self.frappe.get_doc, self.frappe.new_doc = Mock(side_effect=self.get_doc), Mock(side_effect=self.new_doc)
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
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
                                     "jifeng_erp.personnel_separation": separation}):
            spec = importlib.util.spec_from_file_location("inventory_archive_under_test", ROOT / "jifeng_erp/inventory_archive.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        NativeDocument.environment = self
        for folder in ("jifeng_inventory_profile", "jifeng_inventory_dictionary"):
            schema = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype" / folder / (folder + ".json")).read_text(encoding="utf-8"))
            self.metas[schema["name"]] = Meta(schema["fields"])
        self.metas["Item"] = Meta([
            {"fieldname": field, "fieldtype": kind, "label": field, "options": options,
             "reqd": field in ("item_group", "stock_uom"), "unique": field == "item_code"}
            for field, kind, options in (("item_group", "Link", "Item Group"), ("stock_uom", "Link", "UOM"),
                                         ("item_code", "Data", ""), ("item_name", "Data", ""),
                                         ("is_stock_item", "Check", ""))])
        for doctype, name in (("Company", "JF"), ("Company", "OTHER"), ("Item Group", "Raw Material"),
                              ("UOM", "Nos"), ("UOM", "Kg"), ("Supplier", "SUP")):
            self.add(doctype, name)
        self.item = self.add("Item", "BUSINESS-ITEM", item_name="原生名称", item_group="Raw Material",
                             stock_uom="Nos", disabled=0, uoms=[{"uom": "Kg", "conversion_factor": 9}],
                             item_defaults=[{"company": "OTHER", "default_warehouse": "WH"}], unknown_native="KEEP")
        self.profile = self.add(self.module.PROFILE, "PROFILE", company="JF", archive_kind="物料",
                                item_code="BUSINESS-ITEM", legacy_number="旧编号", legacy_code="旧代码",
                                display_name="旧名称", display_uom="旧单位", conversion_value=1,
                                inactive=0, request_id=str(uuid4()), request_digest="a" * 64, unknown_profile="KEEP")

    def add(self, doctype, name, **values):
        values.update(doctype=doctype, name=name, modified="2026-10-01T00:00:00", __islocal=0)
        self.documents[(doctype, name)] = values
        return values

    def new_doc(self, doctype):
        values = {"doctype": doctype, "name": None, "__islocal": 1}
        for field in self.metas[doctype].fields:
            if field.default is not None:
                values[field.fieldname] = float(field.default) if field.fieldtype == "Float" else (
                    int(field.default) if field.fieldtype == "Check" else field.default)
        return NativeDocument(values)

    def get_doc(self, doctype, name, **kwargs):
        return NativeDocument(deepcopy(self.documents[(doctype, name)]))

    def get_value(self, doctype, filters, field, **kwargs):
        return next((document.get(field) for (kind, _), document in self.documents.items()
                     if kind == doctype and all(document.get(key) == value for key, value in filters.items())), None)

    def get_list(self, doctype, filters, **kwargs):
        def matches(document):
            for field, value in filters.items():
                if isinstance(value, list):
                    if value[1].strip("%") not in document.get(field, ""):
                        return False
                elif document.get(field) != value:
                    return False
            return True
        return [name for (kind, name), document in self.documents.items() if kind == doctype and matches(document)]

    def columns(self, doctype, permission_type, **kwargs):
        return {field.fieldname for field in self.metas[doctype].fields} - self.hidden.get((doctype, permission_type), set())

    def savepoint(self, name):
        self.snapshots[name] = deepcopy(self.documents)

    def rollback(self, save_point):
        self.documents = deepcopy(self.snapshots[save_point])

    def existing_payload(self, **changes):
        return {"kind": "物料", "name": "PROFILE", "profile_version": self.documents[(self.module.PROFILE, "PROFILE")]["modified"],
                "item_version": self.documents[("Item", "BUSINESS-ITEM")]["modified"], "profile": {}, **changes}

    def creation_payload(self, kind="物料", **changes):
        return {"kind": kind, "request_id": str(uuid4()),
                "profile": {"company": "JF", "legacy_number": "编号-2", "legacy_code": "代码-2",
                            "display_name": "新档案", "display_uom": "档案原文单位"},
                "item": {"item_group": "Raw Material", "stock_uom": "Nos"}, **changes}

    def test_new_item_uses_independent_identity_and_stores_both_old_codes(self):
        payload = self.creation_payload()
        result = self.module.save_archive(payload)
        item = self.documents[("Item", result["item_code"])]
        self.assertNotIn(result["item_code"], ("编号-2", "代码-2"))
        self.assertEqual(item["item_name"], "新档案")
        self.assertEqual(item["stock_uom"], "Nos")
        self.assertEqual(item["is_stock_item"], 1)
        self.assertEqual(item["opening_stock"], 0)
        self.assertEqual(item["standard_rate"], 0)
        self.assertEqual(result["profile"]["legacy_number"], "编号-2")
        self.assertEqual(result["profile"]["legacy_code"], "代码-2")
        self.assertEqual(result["profile"]["display_uom"], "档案原文单位")
        self.assertEqual(result["profile"]["conversion_value"], 1)
        self.assertNotIn("request_digest", result["profile"])

    def test_existing_display_values_never_write_shared_item_or_uoms(self):
        before = deepcopy(self.item)
        result = self.module.save_archive(self.existing_payload(profile={"display_name": "新显示名",
            "display_uom": "档案单位", "conversion_uom": "Kg", "conversion_value": 7,
            "location": "任意旧库位", "owner_name": "原文负责", "inactive": 1}))
        self.assertEqual(self.documents[("Item", "BUSINESS-ITEM")], before)
        self.assertEqual(result["item_name"], "原生名称")
        self.assertEqual(result["item"]["stock_uom"], "Nos")
        self.assertEqual(result["profile"]["display_name"], "新显示名")
        self.assertTrue(result["field_meta"]["item"]["stock_uom"]["read_only"])
        self.assertEqual(result["profile"]["inactive"], 1)

    def test_same_request_reuses_original_and_changed_content_is_rejected(self):
        payload = self.creation_payload()
        first = self.module.save_archive(payload)
        count = len(self.documents)
        second = self.module.save_archive(deepcopy(payload))
        self.assertEqual(first["name"], second["name"])
        self.assertEqual(len(self.documents), count)
        payload["profile"]["notes"] = "不同内容"
        with self.assertRaisesRegex(ValueError, "不同内容"):
            self.module.save_archive(payload)
        self.assertEqual(len(self.documents), count)

    def test_replay_and_resolution_recheck_profile_item_and_company_access(self):
        payload = self.creation_payload()
        result = self.module.save_archive(payload)
        for denial in ((self.module.PROFILE, "read"), ("Item", "read"), ("Company", "select")):
            with self.subTest(denial=denial):
                self.denials.add(denial)
                with self.assertRaises(PermissionError):
                    self.module.save_archive(payload)
                with self.assertRaises(PermissionError):
                    self.module.resolve_creation(payload["request_id"], "物料")
                self.denials.remove(denial)
        resolved = self.module.resolve_creation(payload["request_id"], "物料")
        self.assertEqual(resolved["archive"]["name"], result["name"])
        self.assertEqual(self.module.resolve_creation(str(uuid4()), "物料"), {"status": "not_found"})

    def test_request_uuid4_and_create_parameters_are_mandatory(self):
        for changes in ({"request_id": "bad"}, {"request_id": "00000000-0000-0000-0000-000000000000"},
                        {"item": {}}, {"item": {"item_group": "Raw Material"}}, {"profile_version": "old"}):
            with self.subTest(changes=changes):
                before = deepcopy(self.documents)
                with self.assertRaises(ValueError):
                    self.module.save_archive(self.creation_payload(**changes))
                self.assertEqual(self.documents, before)

    def test_profile_failure_rolls_back_item_and_callbacks_and_context(self):
        before = deepcopy(self.documents)
        previous = Mock()
        self.frappe.db.after_commit.add(previous)
        self.frappe.local._realtime_log = ["previous"]
        self.frappe.flags.currently_saving = ["previous"]
        self.fail_profile = True
        with self.assertRaisesRegex(ValueError, "profile native failure"):
            self.module.save_archive(self.creation_payload())
        self.assertEqual(self.documents, before)
        self.assertEqual(self.frappe.db.after_commit._functions, [previous])
        self.assertEqual(self.frappe.local._realtime_log, ["previous"])
        self.assertEqual(self.frappe.flags.currently_saving, ["previous"])
        self.assertIsNone(self.module._CREATING.get())

    def test_native_direct_profile_creation_and_identity_tampering_are_blocked(self):
        direct = self.new_doc(self.module.PROFILE)
        direct.archive_kind = "物料"
        with self.assertRaisesRegex(ValueError, "联合新建"):
            direct.save()
        for field, value in (("company", "OTHER"), ("archive_kind", "材料"), ("item_code", "NEW"),
                             ("request_id", str(uuid4())), ("request_digest", "b" * 64)):
            with self.subTest(field=field):
                direct = self.get_doc(self.module.PROFILE, "PROFILE")
                direct.set(field, value)
                with self.assertRaisesRegex(ValueError, "不能修改"):
                    direct.save()

    def test_native_controller_keeps_receipt_even_for_administrator_deletion(self):
        document_module = types.ModuleType("frappe.model.document")
        document_module.Document = NativeDocument
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model.document": document_module,
                                     "jifeng_erp.inventory_archive": self.module}):
            source = ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_inventory_profile/jifeng_inventory_profile.py"
            spec = importlib.util.spec_from_file_location("inventory_controller_under_test", source)
            controller = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(controller)
        self.frappe.session.user = "Administrator"
        document = controller.JifengInventoryProfile(self.profile)
        with self.assertRaisesRegex(ValueError, "不可删除"):
            document.on_trash()

    def test_two_kind_fields_cannot_mix_and_native_form_also_enforces_it(self):
        for profile in ({"shape": "圆"}, {"color": "红"}):
            with self.assertRaisesRegex(ValueError, "不能混用"):
                self.module.save_archive(self.existing_payload(profile=profile))
        payload = self.creation_payload(kind="材料")
        payload["profile"]["material"] = "金属"
        with self.assertRaisesRegex(ValueError, "不能混用"):
            self.module.save_archive(payload)
        direct = self.get_doc(self.module.PROFILE, "PROFILE")
        direct.shape = "圆"
        with self.assertRaisesRegex(ValueError, "不能混用"):
            direct.save()
        with self.assertRaisesRegex(ValueError, "种类不匹配"):
            self.module.read_archive("PROFILE", "材料")

    def test_only_profile_write_is_needed_for_existing_and_item_read_is_required(self):
        self.denials.add(("Item", "write"))
        self.module.save_archive(self.existing_payload(profile={"notes": "许可"}))
        self.denials.add(("Item", "read"))
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.existing_payload(profile={"notes": "拒绝"}))

    def test_field_permissions_and_unknown_fields_are_preserved(self):
        self.hidden[(self.module.PROFILE, "read")] = {"notes"}
        self.hidden[(self.module.PROFILE, "write")] = {"location"}
        result = self.module.read_archive("PROFILE", "物料")
        self.assertNotIn("notes", result["profile"])
        self.assertTrue(result["field_meta"]["profile"]["location"]["read_only"])
        for field in ("notes", "location"):
            with self.assertRaises(PermissionError):
                self.module.save_archive(self.existing_payload(profile={field: "拒绝"}))
        result = self.module.save_archive(self.existing_payload(profile={"legacy_code": "保留"}))
        self.assertEqual(self.documents[(self.module.PROFILE, result["name"])]["unknown_profile"], "KEEP")
        for profile in ({"request_digest": "x"}, {"unknown_profile": "overwrite"}):
            with self.assertRaises(ValueError):
                self.module.save_archive(self.existing_payload(profile=profile))

    def test_profile_and_item_versions_are_both_checked_before_save(self):
        for field in ("profile_version", "item_version"):
            before = deepcopy(self.documents)
            with self.assertRaises(RuntimeError):
                self.module.save_archive(self.existing_payload(**{field: "stale"}, profile={"notes": "拒绝"}))
            self.assertEqual(self.documents, before)

    def test_hidden_source_code_is_not_returned_through_convenience_fields(self):
        for doctype in (self.module.PROFILE, "Item"):
            with self.subTest(doctype=doctype):
                self.hidden[(doctype, "read")] = {"item_code"}
                result = self.module.read_archive("PROFILE", "物料")
                self.assertIsNone(result["item_code"])
                self.assertNotIn("item_code", result["profile"])
                self.hidden.clear()

    def test_nonfinite_boolean_negative_limits_and_conversion_are_rejected(self):
        for profile in ({"list_price": "NaN"}, {"weight": True}, {"processing_price": -1},
                        {"conversion_value": 0}, {"conversion_value": -1}, {"conversion_value": None},
                        {"upper_limit": 1, "lower_limit": 2}, {"inactive": "yes"}):
            with self.subTest(profile=profile):
                before = deepcopy(self.documents)
                with self.assertRaises(ValueError):
                    self.module.save_archive(self.existing_payload(profile=profile))
                self.assertEqual(self.documents, before)

    def test_create_permissions_and_link_selection_are_all_enforced(self):
        for doctype, permission in ((self.module.PROFILE, "create"), ("Item", "create"), ("Company", "select"),
                                    ("Item Group", "select"), ("UOM", "select"), ("Supplier", "select")):
            with self.subTest(doctype=doctype):
                self.denials.add((doctype, permission))
                payload = self.creation_payload()
                payload["profile"]["supplier"] = "SUP"
                before = deepcopy(self.documents)
                with self.assertRaises(PermissionError):
                    self.module.save_archive(payload)
                self.assertEqual(self.documents, before)
                self.denials.remove((doctype, permission))

    def test_copy_clears_identities_and_checks_kind_company_permissions(self):
        result = self.module.init_archive("物料", copy_from="PROFILE")
        self.assertIsNone(result["name"])
        self.assertIsNone(result["profile"]["legacy_number"])
        self.assertIsNone(result["profile"]["legacy_code"])
        self.assertIsNone(result["profile"]["old_number"])
        self.assertEqual(result["profile"]["company"], "JF")
        self.assertEqual(result["profile"]["display_name"], "旧名称")
        self.assertEqual(result["profile"]["inactive"], 0)
        self.assertEqual(result["item"]["stock_uom"], "Nos")
        with self.assertRaises(ValueError):
            self.module.init_archive("物料", company="OTHER", copy_from="PROFILE")
        self.denials.add((self.module.PROFILE, "read"))
        with self.assertRaises(PermissionError):
            self.module.init_archive("物料", copy_from="PROFILE")

    def test_persistent_dictionary_has_scoped_unique_values_and_native_guards(self):
        payload = {"kind": "材料", "company": "JF", "dictionary_type": "形状", "value": " 圆形 ", "inactive": 0}
        first = self.module.save_dictionary(payload)
        self.assertEqual(first["value"], "圆形")
        self.assertEqual(self.module.list_dictionary("材料", "形状", "JF"), [{"name": first["name"], "value": "圆形", "inactive": 0}])
        self.assertEqual(self.module.list_dictionary("材料", "颜色", "JF"), [])
        self.assertEqual(self.module.list_dictionary("材料", "形状", "OTHER"), [])
        with self.assertRaises(self.frappe.DuplicateEntryError):
            self.module.save_dictionary(payload)
        for field, value in (("archive_kind", "物料"), ("company", "OTHER"), ("dictionary_type", "颜色")):
            direct = self.get_doc(self.module.DICTIONARY, first["name"])
            direct.set(field, value)
            with self.assertRaises(ValueError):
                direct.save()
        with self.assertRaises(ValueError):
            self.module.save_dictionary({**payload, "kind": "物料"})
        self.denials.add(("Company", "select"))
        direct = self.get_doc(self.module.DICTIONARY, first["name"])
        with self.assertRaises(PermissionError):
            direct.save()

    def test_dictionary_hidden_value_and_guest_cannot_read_or_write(self):
        self.hidden[(self.module.DICTIONARY, "read")] = {"value"}
        with self.assertRaises(PermissionError):
            self.module.list_dictionary("物料", "大类", "JF")
        self.frappe.session.user = "Guest"
        for action in (lambda: self.module.init_archive("物料"),
                       lambda: self.module.read_archive("PROFILE", "物料"),
                       lambda: self.module.save_archive(self.creation_payload()),
                       lambda: self.module.resolve_creation(str(uuid4()), "物料")):
            with self.assertRaises(PermissionError):
                action()


if __name__ == "__main__":
    unittest.main()
