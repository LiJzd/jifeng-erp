"""Product archives against native controller and transaction models; no live DB."""

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
PROFILE = "Jifeng Product Profile"
CATEGORY = "Jifeng Product Category"
DETAIL = "Jifeng Product Category Detail"
QUEUES = ("before_commit", "after_commit", "before_rollback", "after_rollback")


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
        env = self.environment
        return ((self.doctype, permission) not in env.denials
                and (self.doctype, self.name, permission) not in env.document_denials
                and self.name not in env.denied_names
                and self.company not in env.denied_companies)

    def check_permission(self, permission):
        self.environment.permission_calls.append((self.doctype, self.name, permission))
        if not self.has_permission(permission):
            raise PermissionError("native " + self.doctype + " " + permission)

    def get_doc_before_save(self):
        previous = self.environment.documents.get((self.doctype, self.name))
        return self.environment.document(previous) if previous else None

    def reload(self):
        values = deepcopy(self.environment.documents[(self.doctype, self.name)])
        self.clear()
        self.update(values)
        return self

    def is_downloadable(self):
        return self.name not in self.environment.denied_downloads

    def save(self):
        env = self.environment
        new = self.is_new()
        self.check_permission("create" if new else "write")
        if self.doctype == PROFILE:
            if new:
                self.before_insert()
            self.validate()
        self.name = self.name or self.item_code
        for (doctype, name), previous in env.documents.items():
            if doctype != self.doctype or name == self.name:
                continue
            for field in self.meta.fields:
                if field.unique and self.get(field.fieldname) and previous.get(field.fieldname) == self.get(field.fieldname):
                    env.frappe.local.message_log = list(getattr(env.frappe.local, "message_log", None) or []) + [
                        {"message": "private native unique-index SQL"}]
                    raise env.frappe.UniqueValidationError("private native duplicate " + name)
        if new and (self.doctype, self.name) in env.documents:
            raise env.frappe.DuplicateEntryError("private native primary-key collision")
        env.tick += 1
        self.modified = datetime(2026, 10, 1, 10, 0, 0, env.tick)
        self["__islocal"] = 0
        env.documents[(self.doctype, self.name)] = deepcopy(dict(self))
        env.saves.append((self.doctype, self.name))
        if not new:
            env.versions.append((self.doctype, self.name, deepcopy(dict(self))))
        if self.doctype == "Item":
            env.db.after_commit.add(env.item_callback)
        if self.doctype == PROFILE and env.fail_profile:
            env.versions.append(("Version", self.name, "native failed save side effect"))
            for key in QUEUES:
                getattr(env.db, key).add(env.failed_callback)
            env.frappe.local._realtime_log = ["failed product event"]
            env.frappe.flags.currently_saving = ["failed product context"]
            raise ValueError("native post-save failure")
        return self


class TestProductArchive(unittest.TestCase):
    def setUp(self):
        self.documents, self.versions, self.snapshots, self.metas = {}, [], {}, {}
        self.denials, self.document_denials, self.denied_names, self.denied_companies = set(), set(), set(), set()
        self.denied_downloads, self.hidden, self.forced_lists, self.forced_files = set(), {}, {}, None
        self.tick, self.fail_profile, self.saves, self.permission_calls = 1, False, [], []
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError, self.frappe.TimestampMismatchError = PermissionError, RuntimeError
        self.frappe.DuplicateEntryError = type("DuplicateEntryError", (ValueError,), {})
        self.frappe.UniqueValidationError = type("UniqueValidationError", (ValueError,), {})
        self.frappe.DoesNotExistError = type("DoesNotExistError", (ValueError,), {})
        self.frappe.session = Record(user="stock-manager@example.invalid")
        self.frappe.local, self.frappe.flags = types.SimpleNamespace(), Record()
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))

        def whitelist(**kwargs):
            def decorate(function):
                function.allowed_methods = kwargs.get("methods")
                return function
            return decorate

        self.frappe.whitelist = whitelist
        self.frappe.get_doc, self.frappe.new_doc = Mock(side_effect=self.get_doc), Mock(side_effect=self.new_doc)
        self.frappe.get_meta = lambda dt: self.metas[dt]
        self.frappe.has_permission = Mock(side_effect=lambda dt, permission: (dt, permission) not in self.denials)
        self.frappe.get_list, self.frappe.get_all = Mock(side_effect=self.get_list), Mock(side_effect=self.get_all)
        self.db = types.SimpleNamespace(get_value=Mock(side_effect=self.get_value),
            savepoint=self.savepoint, rollback=self.rollback, **{key: Queue() for key in QUEUES})
        self.frappe.db = self.db
        self.item_callback, self.failed_callback = Mock(), Mock()
        model, document_module = types.ModuleType("frappe.model"), types.ModuleType("frappe.model.document")
        model.get_permitted_fields, document_module.Document = self.columns, NativeDocument
        separation = types.ModuleType("jifeng_erp.personnel_separation")
        separation._atomic = self.actual_function("personnel_separation.py", "_atomic", {
            "frappe": self.frappe, "deepcopy": deepcopy, "wraps": wraps, "uuid4": uuid4})
        workflow = types.ModuleType("jifeng_erp.workflow")
        import jifeng_erp.costing as costing
        workflow.recipe_totals = self.actual_function("workflow.py", "recipe_totals", {
            "frappe": self.frappe, "number": costing.number, "CostingError": costing.CostingError})
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
            "frappe.model.document": document_module, "jifeng_erp.personnel_separation": separation,
            "jifeng_erp.workflow": workflow})
        scope.start()
        self.addCleanup(scope.stop)
        self.module = self.load_module("product_archive_under_test", ROOT / "jifeng_erp/product_archive.py")
        binding = patch.dict(sys.modules, {"jifeng_erp.product_archive": self.module})
        binding.start()
        self.addCleanup(binding.stop)
        self.controller = self.load_module("product_profile_controller_under_test", ROOT /
            "jifeng_erp/jifeng_erp/doctype/jifeng_product_profile/jifeng_product_profile.py").JifengProductProfile
        NativeDocument.environment = self
        for dt in (PROFILE, CATEGORY, DETAIL):
            folder = dt.lower().replace(" ", "_")
            schema = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype" / folder / (folder + ".json")).read_text(encoding="utf-8"))
            self.metas[dt] = Meta(schema["fields"])
        self.metas["Item"] = Meta([
            {"fieldname": name, "fieldtype": kind, "options": options, "unique": name == "item_code",
             "reqd": name in ("item_name", "item_group", "stock_uom")}
            for name, kind, options in (("item_code", "Data", ""), ("item_name", "Data", ""),
                ("item_group", "Link", "Item Group"), ("stock_uom", "Link", "UOM"),
                ("is_stock_item", "Check", ""), ("opening_stock", "Float", ""), ("standard_rate", "Currency", ""),
                ("image", "Attach Image", ""))])
        self.metas["File"] = Meta([])
        for dt, name in (("Company", "JF"), ("Company", "OTHER"), ("Item Group", "Products"),
                         ("Item Group", "Parent Group"), ("UOM", "Nos"), ("UOM", "Kg"),
                         ("Supplier", "SUP"), ("Operation", "Polishing")):
            self.add(dt, name, is_group=int(name == "Parent Group"))
        self.item = self.add("Item", "BUSINESS-ITEM", item_code="BUSINESS-ITEM", item_name="原生商品名", image="/files/item.png",
            item_group="Products", stock_uom="Nos", disabled=0, uoms=[Record(name="UOM-CHILD", uom="Kg", conversion_factor=9)],
            item_defaults=[Record(name="DEFAULT-CHILD", company="OTHER", default_warehouse="WH")], unknown_native="KEEP")
        self.profile = self.add(PROFILE, "BUSINESS-ITEM", company="JF", item_code="BUSINESS-ITEM", inactive=0,
            legacy_number="旧编号", legacy_code="旧代码", display_name="旧名称", major_category="旧大类原文", minor_category="旧小类原文",
            display_uom="旧单位", material="旧材质", owner_name="旧负责人", region="旧区域", notes="原注意事项",
            requirements="默认工艺要求", packaging="默认包装要求", bom=None, supplier="SUP", supplier_product_code="SUP-CODE",
            materials=[Record(name="MAT-CHILD", item_code="BUSINESS-ITEM", uom="Nos", qty_per_unit=2, unit_price=3, supplier="SUP", unknown="KEEP")],
            operations=[Record(name="OP-CHILD", operation="Polishing", supplier="SUP", unit_price=4, unknown="KEEP")],
            request_id=str(uuid4()), request_digest="a" * 64, creation_mode="existing_item", unknown_profile="KEEP")
        self.category = self.add(CATEGORY, "CATEGORY-1", company="JF", major_name=" 类别大类原文 ", inactive=0,
            legacy_code="CATEGORY-LEGACY", owner_name="类别负责人", region="类别区域")
        self.detail = self.add(DETAIL, "DETAIL-1", company="JF", category="CATEGORY-1", minor_name=" 类别小类原文 ", inactive=0,
            material="类别材质", display_uom="类别单位", conversion_uom_text="类别换算单位", conversion_value="两箱")

    @staticmethod
    def load_module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def actual_function(filename, name, namespace):
        tree = ast.parse((ROOT / "jifeng_erp" / filename).read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual_product_" + name, "exec"), namespace)
        return namespace[name]

    def document(self, values):
        cls = self.controller if values["doctype"] == PROFILE else NativeDocument
        return cls(deepcopy(values))

    def add(self, doctype, name, **values):
        values.update(doctype=doctype, name=name, modified=datetime(2026, 10, 1, 9), __islocal=0)
        self.documents[(doctype, name)] = values
        return values

    def new_doc(self, doctype):
        values = {"doctype": doctype, "name": None, "__islocal": 1}
        if doctype == PROFILE:
            values.update(company="DEFAULT-DENIED", item_code="DEFAULT-ITEM", inactive=0, materials=[], operations=[])
        elif doctype == "Item":
            values.update(item_name="DEFAULT-NAME", item_group="DEFAULT-GROUP", stock_uom="DEFAULT-UOM")
        return self.document(values)

    def get_doc(self, doctype, name, **kwargs):
        try:
            return self.document(self.documents[(doctype, name)])
        except KeyError:
            raise self.frappe.DoesNotExistError("missing native document") from None

    def get_value(self, doctype, filters, field, **kwargs):
        return next((row.get(field) for (dt, _), row in self.documents.items()
                     if dt == doctype and all(row.get(key) == value for key, value in filters.items())), None)

    def get_all(self, doctype, filters, **kwargs):
        self.assertEqual(doctype, "File", "unscoped queries are reserved for exact private File binding")
        if self.forced_files is not None:
            return self.forced_files
        return sorted(name for (dt, name), row in self.documents.items()
                      if dt == doctype and all(row.get(key) == value for key, value in filters.items()))

    def get_list(self, doctype, filters, fields, **kwargs):
        if doctype in self.forced_lists:
            return deepcopy(self.forced_lists[doctype])

        def matches(row, field, value):
            if isinstance(value, (tuple, list)):
                operator, operand = value
                if operator == "in":
                    return row.get(field) in operand
                self.assertEqual(operator, "like")
                return operand.strip("%") in str(row.get(field) or "")
            return row.get(field) == value

        result = []
        for (dt, _), row in self.documents.items():
            if dt != doctype or not self.document(row).has_permission("read"):
                continue
            if not all(matches(row, field, value) for field, value in filters.items()):
                continue
            if kwargs.get("or_filters") and not any(matches(row, field, [operator, value])
                    for field, operator, value in kwargs["or_filters"]):
                continue
            result.append(Record({field: row[field] for field in fields if field in row and field in self.columns(dt, "read")}))
        return sorted(result, key=lambda row: row.name or "")

    def columns(self, doctype, permission_type, **kwargs):
        return ({field.fieldname for field in self.metas[doctype].fields} | {"name", "modified", "creation", "owner", "modified_by"}
                ) - self.hidden.get((doctype, permission_type), set())

    def savepoint(self, name):
        self.snapshots[name] = deepcopy((self.documents, self.versions))

    def rollback(self, save_point):
        self.documents, self.versions = deepcopy(self.snapshots[save_point])

    def creation_payload(self, **changes):
        return {"request_id": str(uuid4()), "profile": {"company": "JF", "legacy_number": " 旧编号2 ",
            "display_name": " 档案名称原文 ", "display_uom": " 档案单位原文 ", "material": "档案材质", "inactive": 0},
            "item": {"item_name": "明确业务商品名称", "item_group": "Products", "stock_uom": "Nos"}, **changes}

    def existing_payload(self, **changes):
        return {"name": "BUSINESS-ITEM", "profile_version": self.documents[(PROFILE, "BUSINESS-ITEM")]["modified"].isoformat(),
            "item_version": self.documents[("Item", "BUSINESS-ITEM")]["modified"].isoformat(), "profile": {}, **changes}

    def linked_payload(self, **changes):
        self.add("Item", "UNBOUND", item_code="UNBOUND", item_name="已有业务名", item_group="Products", stock_uom="Nos", unknown="KEEP")
        return self.creation_payload(item={}, existing_item="UNBOUND", item_version=self.documents[("Item", "UNBOUND")]["modified"].isoformat(), **changes)

    def attachment(self, name="PRIVATE-FILE", field="profile_image", url="/private/files/product.png", **changes):
        return self.add("File", name, file_url=url, is_private=1, attached_to_doctype=PROFILE,
            attached_to_name="BUSINESS-ITEM", attached_to_field=field, unique_url=url + "?fid=" + name, **changes)

    def assert_unchanged(self, before):
        self.assertEqual((self.documents, self.versions), before)

    def test_init_has_explicit_company_and_business_fields_without_native_defaults(self):
        dto = self.module.init_archive()
        self.assertIsNone(dto["name"])
        self.assertIsNone(dto["profile"]["company"])
        self.assertIsNone(dto["item"]["item_name"])
        self.assertIsNone(dto["item"]["item_group"])
        self.assertIsNone(dto["item"]["stock_uom"])
        self.assertEqual(dto["profile"]["inactive"], 0)
        self.assertTrue(dto["can_create_item"] and dto["can_link_item"] and dto["can_write"])
        self.assertTrue(all(dto["field_meta"]["profile"][field]["read_only"] for field in
                            ("legacy_code", "major_category", "minor_category", "profile_image", "patent_image")))
        self.assertFalse(any(call.args == ("Company", "DEFAULT-DENIED") for call in self.frappe.get_doc.call_args_list))

    def test_new_item_requires_explicit_name_group_uom_and_uses_uuid_code(self):
        payload = self.creation_payload()
        for field in ("company",):
            for value in (None, "", " JF "):
                invalid = deepcopy(payload)
                invalid["profile"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.module.save_archive(invalid)
        for field in self.module.ITEM_CREATE:
            for value in (None, "", " "):
                invalid = deepcopy(payload)
                invalid["item"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.module.save_archive(invalid)
        dto = self.module.save_archive(payload)
        code = "JF-PROD-" + UUID(payload["request_id"]).hex
        item = self.documents[("Item", code)]
        profile = self.documents[(PROFILE, dto["name"])]
        self.assertEqual((dto["item_code"], profile["item_code"], item["name"]), (code, code, code))
        self.assertEqual((item["item_name"], item["item_group"], item["stock_uom"]), ("明确业务商品名称", "Products", "Nos"))
        self.assertEqual((item["is_stock_item"], item["opening_stock"], item["standard_rate"]), (1, 0, 0))
        self.assertEqual(dto["profile"]["display_name"], " 档案名称原文 ")
        self.assertIsNone(profile["legacy_code"])
        self.assertEqual(profile["request_id"], payload["request_id"])
        self.assertEqual(profile["creation_mode"], "new_item")
        self.assertNotIn("request_digest", dto["profile"])
        self.assertEqual(self.module.save_archive.allowed_methods, ["POST"])

    def test_new_item_link_permissions_and_group_detail_are_checked(self):
        for denial in (("Item", "create"), ("Item Group", "select"), ("UOM", "select"), (PROFILE, "create")):
            before = deepcopy((self.documents, self.versions))
            self.denials.add(denial)
            with self.subTest(denial=denial), self.assertRaises(PermissionError):
                self.module.save_archive(self.creation_payload())
            self.assert_unchanged(before)
            self.denials.remove(denial)
        payload = self.creation_payload()
        payload["item"]["item_group"] = "Parent Group"
        with self.assertRaisesRegex(ValueError, "明细分组"):
            self.module.save_archive(payload)

    def test_existing_item_binding_requires_read_version_and_preserves_item(self):
        payload = self.linked_payload()
        before = deepcopy(self.documents[("Item", "UNBOUND")])
        initialized = self.module.init_archive("JF", "UNBOUND")
        self.assertEqual(initialized["existing_item"], "UNBOUND")
        self.assertTrue(all(value["read_only"] for value in initialized["field_meta"]["item"].values()))
        self.denials.update({("Item", "create"), ("Item", "write")})
        dto = self.module.save_archive(payload)
        self.assertEqual(dto["creation_mode"], "existing_item")
        self.assertEqual(self.documents[("Item", "UNBOUND")], before)
        self.assertNotIn(("Item", "UNBOUND"), self.saves)
        with self.assertRaisesRegex(ValueError, "已有产品档案"):
            self.module.init_archive("JF", "UNBOUND")
        with self.assertRaisesRegex(ValueError, "明确公司"):
            self.module.init_archive(existing_item="UNBOUND")

    def test_existing_item_missing_stale_versions_and_shared_fields_are_rejected(self):
        for version in (None, "", "2026-09-01T00:00:00"):
            payload = self.linked_payload()
            payload["item_version"] = version
            before = deepcopy((self.documents, self.versions))
            with self.subTest(version=version), self.assertRaises(RuntimeError):
                self.module.save_archive(payload)
            self.assert_unchanged(before)
        payload = self.linked_payload()
        payload["item"] = {"item_name": "不能改写"}
        with self.assertRaisesRegex(ValueError, "不能改写"):
            self.module.save_archive(payload)
        payload["item"] = {}
        self.denials.add(("Item", "read"))
        with self.assertRaises(PermissionError):
            self.module.save_archive(payload)

    def test_existing_updates_preserve_item_unknown_values_and_recipe_child_identity(self):
        before_item = deepcopy(self.item)
        before_recipe = deepcopy({field: self.profile[field] for field in
            ("materials", "operations", "requirements", "packaging", "supplier", "supplier_product_code", "unknown_profile")})
        self.denials.add(("Item", "write"))
        dto = self.module.save_archive(self.existing_payload(profile={"display_name": "新原文名", "display_uom": "新原文单位",
            "list_price": "12.5", "notes": "多行\n注意事项", "inactive": 1}))
        self.assertEqual(self.documents[("Item", "BUSINESS-ITEM")], before_item)
        current = self.documents[(PROFILE, "BUSINESS-ITEM")]
        self.assertEqual({field: current[field] for field in before_recipe}, before_recipe)
        self.assertEqual((current["material_unit_cost"], current["processing_unit_cost"]), (Decimal(6), Decimal(4)))
        self.assertEqual(dto["item"]["item_name"], "原生商品名")
        self.assertEqual(dto["profile"]["list_price"], 12.5)
        self.assertEqual(self.saves, [(PROFILE, "BUSINESS-ITEM")])
        self.assertEqual(len(self.versions), 1)

    def test_supplier_code_supplier_and_specification_are_independent_permission_checked_originals(self):
        values = {"supplier_product_code": " 供应商原货号 ", "supplier": "SUP", "specification": "规格原文\n第二行"}
        item_before = deepcopy(self.documents[("Item", "BUSINESS-ITEM")])
        recipe_before = deepcopy({field: self.profile[field] for field in ("materials", "operations", "requirements", "packaging")})
        dto = self.module.save_archive(self.existing_payload(profile=values))
        self.assertEqual({field: dto["profile"][field] for field in values}, values)
        self.assertEqual(self.documents[("Item", "BUSINESS-ITEM")], item_before)
        self.assertEqual({field: self.documents[(PROFILE, "BUSINESS-ITEM")][field] for field in recipe_before}, recipe_before)
        self.assertIn(("Supplier", "SUP", "select"), self.permission_calls)
        for permission in ("read", "write"):
            self.hidden[(PROFILE, permission)] = {"supplier_product_code"}
            before = deepcopy((self.documents, self.versions))
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.module.save_archive(self.existing_payload(profile={"supplier_product_code": "无权写入"}))
            self.assert_unchanged(before)
            if permission == "read":
                projected = self.module.read_archive("BUSINESS-ITEM")
                self.assertNotIn("supplier_product_code", projected["profile"])
                self.assertNotIn("supplier_product_code", projected["field_meta"]["profile"])
            self.hidden.clear()
        self.denials.add(("Supplier", "select"))
        before = deepcopy((self.documents, self.versions))
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.existing_payload(profile={"supplier": "SUP"}))
        self.assert_unchanged(before)
        self.assertEqual(len(self.versions), 1)

    def test_existing_source_versions_permissions_and_readonly_payload_are_checked(self):
        before = deepcopy((self.documents, self.versions))
        for changes in ({"item": {"item_name": "写入"}}, {"existing_item": "OTHER"}, {"request_id": str(uuid4())},
                        {"profile": {"company": "OTHER"}}, {"profile": {"legacy_code": "写入"}},
                        {"profile": {"major_category": "写入"}}, {"profile": {"minor_category": "写入"}},
                        {"profile": {"materials": []}}, {"profile": {"unknown_profile": "写入"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.save_archive(self.existing_payload(**changes))
            self.assert_unchanged(before)
        for field in ("profile_version", "item_version"):
            for version in (None, "", "2026-09-01T00:00:00"):
                with self.subTest(field=field, version=version), self.assertRaises(RuntimeError):
                    self.module.save_archive(self.existing_payload(**{field: version}))
                self.assert_unchanged(before)
        for denial in ((PROFILE, "write"), (PROFILE, "read"), ("Item", "read"), ("Company", "select")):
            self.denials.add(denial)
            with self.subTest(denial=denial), self.assertRaises(PermissionError):
                self.module.save_archive(self.existing_payload(profile={"notes": "拒绝"}))
            self.assert_unchanged(before)
            self.denials.remove(denial)

    def test_item_first_lock_detects_changed_profile_source_even_with_same_versions(self):
        payload = self.existing_payload(profile={"notes": "不应写入另一来源"})
        self.add("Item", "OTHER-ITEM", item_code="OTHER-ITEM", item_name="其它来源", item_group="Products", stock_uom="Nos")
        original_get_doc = self.get_doc

        for field, value in (("item_code", "OTHER-ITEM"), ("company", "OTHER")):
            def change_source(doctype, name, **kwargs):
                if doctype == PROFILE and kwargs.get("for_update"):
                    self.documents[(PROFILE, name)][field] = value
                return original_get_doc(doctype, name, **kwargs)

            before = deepcopy((self.documents, self.versions))
            self.frappe.get_doc.reset_mock()
            self.frappe.get_doc.side_effect = change_source
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "业务来源已变化"):
                self.module.save_archive(payload)
            self.assert_unchanged(before)
            locked = [call.args[:2] for call in self.frappe.get_doc.call_args_list if call.kwargs.get("for_update")]
            self.assertEqual(locked, [("Item", "BUSINESS-ITEM"), (PROFILE, "BUSINESS-ITEM")])

    def test_native_primary_key_rename_cannot_rewrite_frozen_item_binding(self):
        before = deepcopy((self.documents, self.versions))
        document = self.get_doc(PROFILE, "BUSINESS-ITEM")
        for merge in (False, True):
            with self.subTest(merge=merge), self.assertRaisesRegex(ValueError, "产品档案主键不能重命名"):
                document.before_rename("BUSINESS-ITEM", "OTHER-ITEM", merge)
            self.assert_unchanged(before)

    def test_new_item_requires_read_and_required_read_fields_before_any_native_save(self):
        before = deepcopy((self.documents, self.versions))
        self.denials.add(("Item", "read"))
        self.assertFalse(self.module.init_archive()["can_create_item"])
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.creation_payload())
        self.assert_unchanged(before)
        self.denials.clear()
        for field in self.module.REQUIRED_ITEM:
            self.hidden[("Item", "read")] = {field}
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.module.save_archive(self.creation_payload())
            self.assert_unchanged(before)
        self.assertEqual(self.saves, [])

    def test_field_permissions_omit_values_and_metadata_and_reject_writes(self):
        self.hidden[(PROFILE, "read")] = {"notes", "patent_image"}
        self.hidden[(PROFILE, "write")] = {"display_name"}
        self.hidden[("Item", "read")] = {"item_name"}
        dto = self.module.read_archive("BUSINESS-ITEM")
        self.assertNotIn("notes", dto["profile"])
        self.assertNotIn("notes", dto["field_meta"]["profile"])
        self.assertNotIn("patent_image", dto["image_states"])
        self.assertNotIn("item_name", dto["item"])
        self.assertNotIn("item_name", dto["field_meta"]["item"])
        self.assertTrue(dto["field_meta"]["profile"]["display_name"]["read_only"])
        for field in ("notes", "display_name"):
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.module.save_archive(self.existing_payload(profile={field: "禁止"}))
        for dt, field in ((PROFILE, "company"), (PROFILE, "modified"), ("Item", "item_code")):
            self.hidden.setdefault((dt, "read"), set()).add(field)
            with self.subTest(dt=dt, field=field), self.assertRaises(PermissionError):
                self.module.read_archive("BUSINESS-ITEM")
            self.hidden[(dt, "read")].remove(field)

    def test_metadata_readonly_is_enforced_even_if_field_permission_allows_write(self):
        self.metas[PROFILE].get_field("notes").read_only = 1
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.existing_payload(profile={"notes": "禁止"}))
        self.assertTrue(self.module.read_archive("BUSINESS-ITEM")["field_meta"]["profile"]["notes"]["read_only"])

    def test_replay_original_digest_never_rewrites_later_profile_or_item(self):
        payload = self.creation_payload()
        dto = self.module.save_archive(payload)
        digest = self.documents[(PROFILE, dto["name"])]["request_digest"]
        self.module.save_archive({"name": dto["name"], "profile_version": dto["profile_version"],
            "item_version": dto["item_version"], "profile": {"notes": "后续编辑"}})
        before = deepcopy((self.documents, self.versions))
        saved_count = len(self.saves)
        replayed = self.module.save_archive(deepcopy(payload))
        self.assertEqual(replayed["profile"]["notes"], "后续编辑")
        self.assertEqual(self.documents[(PROFILE, dto["name"])]["request_digest"], digest)
        self.assertEqual(len(self.saves), saved_count)
        self.assert_unchanged(before)
        changed = deepcopy(payload)
        changed["profile"]["notes"] = "不同初始请求"
        with self.assertRaisesRegex(ValueError, "新建请求已使用"):
            self.module.save_archive(changed)
        self.assert_unchanged(before)

    def test_replay_source_mode_conflicts_and_hidden_uuid_collision_are_generic(self):
        payload = self.creation_payload()
        dto = self.module.save_archive(payload)
        before = deepcopy((self.documents, self.versions))
        conflicts = [deepcopy(payload), deepcopy(payload)]
        conflicts[0]["item"]["stock_uom"] = "Kg"
        conflicts[1].update(item={}, existing_item="BUSINESS-ITEM", item_version=dto["item_version"])
        for changed in conflicts:
            with self.assertRaisesRegex(ValueError, "新建请求已使用"):
                self.module.save_archive(changed)
            self.assert_unchanged(before)
        self.denied_names.add(dto["name"])
        self.frappe.local.message_log = [{"message": "previous unrelated message"}]
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "JF"), {"status": "not_found"})
        with self.assertRaisesRegex(ValueError, "新建请求已使用") as caught:
            self.module.save_archive(payload)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(dto["name"], str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.frappe.local.message_log, [{"message": "previous unrelated message"}])
        self.assert_unchanged(before)

    def test_resolve_receipt_is_company_scoped_and_rechecks_document_item_and_fields(self):
        payload = self.creation_payload()
        dto = self.module.save_archive(payload)
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "JF")["archive"]["name"], dto["name"])
        self.assertEqual(self.module.resolve_creation(payload["request_id"], "OTHER"), {"status": "not_found"})
        self.assertEqual(self.module.resolve_creation(str(uuid4()), "JF"), {"status": "not_found"})
        for denial in ((PROFILE, "read"), ("Item", "read"), ("Company", "select")):
            self.denials.add(denial)
            with self.subTest(denial=denial), self.assertRaises(PermissionError):
                self.module.resolve_creation(payload["request_id"], "JF")
            self.denials.remove(denial)
        self.hidden[(PROFILE, "read")] = {"request_digest"}
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "JF")
        self.hidden.clear()
        self.forced_lists[PROFILE] = [Record(name=dto["name"])]
        self.document_denials.add((PROFILE, dto["name"], "read"))
        with self.assertRaises(PermissionError):
            self.module.resolve_creation(payload["request_id"], "JF")

    def test_failure_rolls_back_joint_item_profile_versions_four_queues_and_context(self):
        callbacks = {key: Mock() for key in QUEUES}
        for key, callback in callbacks.items():
            getattr(self.db, key).add(callback)
        self.frappe.local._realtime_log = ["original event"]
        self.frappe.flags.currently_saving = ["original context"]
        before = deepcopy((self.documents, self.versions))
        self.fail_profile = True
        for payload in (self.creation_payload(), self.existing_payload(profile={"notes": "失败更新"})):
            with self.assertRaisesRegex(ValueError, "native post-save failure"):
                self.module.save_archive(payload)
            self.assert_unchanged(before)
            for key, callback in callbacks.items():
                self.assertEqual(getattr(self.db, key)._functions, [callback])
            self.assertEqual(self.frappe.local._realtime_log, ["original event"])
            self.assertEqual(self.frappe.flags.currently_saving, ["original context"])
            self.assertIsNone(self.module._CREATING.get())

    def test_failed_creation_restores_absent_context_and_empty_queues(self):
        before = deepcopy((self.documents, self.versions))
        self.fail_profile = True
        with self.assertRaisesRegex(ValueError, "native post-save failure"):
            self.module.save_archive(self.creation_payload())
        self.assert_unchanged(before)
        self.assertNotIn("currently_saving", self.frappe.flags)
        self.assertFalse(hasattr(self.frappe.local, "_realtime_log"))
        self.assertTrue(all(getattr(self.db, key)._functions == [] for key in QUEUES))
        self.assertIsNone(self.module._CREATING.get())

    def test_success_preserves_existing_and_new_native_after_commit_callbacks(self):
        existing = Mock()
        self.db.after_commit.add(existing)
        self.module.save_archive(self.creation_payload())
        self.assertEqual(self.db.after_commit._functions, [existing, self.item_callback])

    def test_category_selection_snapshots_only_names_without_business_recipe_or_raw_overwrite(self):
        before = deepcopy(self.profile)
        item = deepcopy(self.item)
        dto = self.module.save_archive(self.existing_payload(profile={"category": "CATEGORY-1", "category_detail": "DETAIL-1"}))
        saved = self.documents[(PROFILE, "BUSINESS-ITEM")]
        self.assertEqual((saved["category"], saved["category_detail"], saved["major_category"], saved["minor_category"]),
                         ("CATEGORY-1", "DETAIL-1", " 类别大类原文 ", " 类别小类原文 "))
        for field in ("legacy_code", "material", "display_uom", "owner_name", "region", "materials", "operations", "unknown_profile"):
            self.assertEqual(saved[field], before[field])
        self.assertEqual(self.documents[("Item", "BUSINESS-ITEM")], item)
        self.assertTrue(dto["field_meta"]["profile"]["major_category"]["read_only"])
        for dt, name in ((CATEGORY, "CATEGORY-1"), (DETAIL, "DETAIL-1")):
            self.assertIn((dt, name, "read"), self.permission_calls)
            self.assertIn((dt, name, "select"), self.permission_calls)
        reads = [call for call in self.frappe.get_doc.call_args_list if call.args[0] in (CATEGORY, DETAIL)]
        self.assertEqual([call.args[0] for call in reads], [CATEGORY, DETAIL, CATEGORY, DETAIL])
        self.assertTrue(all(call.kwargs == {"for_update": True} for call in reads))

    def test_category_current_locked_read_rejects_stop_before_selection_can_be_saved(self):
        original = self.get_doc

        def changed_category(doctype, name, **kwargs):
            if doctype == CATEGORY and kwargs.get("for_update"):
                self.documents[(CATEGORY, name)]["inactive"] = 1
            return original(doctype, name, **kwargs)

        self.frappe.get_doc.side_effect = changed_category
        before = deepcopy((self.documents, self.versions))
        with self.assertRaisesRegex(ValueError, "未停用"):
            self.module.save_archive(self.existing_payload(profile={"category": "CATEGORY-1", "category_detail": "DETAIL-1"}))
        self.assert_unchanged(before)

    def test_existing_item_create_locks_item_before_category_and_saves_after_all_checks(self):
        payload = self.linked_payload()
        payload["profile"].update(category="CATEGORY-1", category_detail="DETAIL-1")
        self.module.save_archive(payload)
        locks = [call.args[0] for call in self.frappe.get_doc.call_args_list if call.kwargs.get("for_update")]
        self.assertEqual(locks[0], "Item")
        self.assertEqual(locks[1:3], [CATEGORY, DETAIL])
        self.assertEqual(self.saves, [(PROFILE, "UNBOUND")])

    def test_flat_category_choices_return_only_explicit_source_columns_without_writes(self):
        before = deepcopy((self.documents, self.versions))
        self.assertEqual(self.module.list_category_choices("JF"), [{"category": "CATEGORY-1", "category_detail": "DETAIL-1",
            "category_code": "CATEGORY-LEGACY", "major_name": " 类别大类原文 ", "minor_name": " 类别小类原文 ",
            "material": "类别材质", "owner_name": "类别负责人", "region": "类别区域", "abbreviation": ""}])
        self.assert_unchanged(before)
        self.frappe.get_all.assert_not_called()
        self.assertIn(("Company", "JF", "select"), self.permission_calls)
        self.assertEqual(self.module.list_category_choices("JF", "类别材质")[0]["category_detail"], "DETAIL-1")
        self.assertEqual(self.module.list_category_choices("JF", "CATEGORY-LEGACY")[0]["category"], "CATEGORY-1")

    def test_flat_category_child_keyword_matches_beyond_first_hundred_parent_rows(self):
        for index in range(150):
            self.add(CATEGORY, "OTHER-PARENT-" + str(index), company="JF", legacy_code=None,
                major_name="父项不匹配", owner_name=None, region=None, inactive=0)
        self.add(DETAIL, "MATCHING-CHILD", category="OTHER-PARENT-149", company="JF", inactive=0,
            minor_name="独立子项关键词", material="原材质")
        rows = self.module.list_category_choices("JF", "独立子项关键词")
        self.assertEqual([(row["category"], row["category_detail"]) for row in rows], [("OTHER-PARENT-149", "MATCHING-CHILD")])
        self.assertEqual(self.frappe.get_list.call_args_list[0].kwargs["limit_page_length"], 0)

    def test_flat_category_choices_recheck_role_document_field_company_and_active_permissions(self):
        for doctype in (CATEGORY, DETAIL):
            for permission in ("read", "select"):
                self.denials.add((doctype, permission))
                with self.subTest(doctype=doctype, permission=permission), self.assertRaises(PermissionError):
                    self.module.list_category_choices("JF")
                self.denials.clear()
            for field in ("material", "minor_name") if doctype == DETAIL else ("legacy_code", "owner_name", "major_name"):
                self.hidden[(doctype, "read")] = {field}
                with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                    self.module.list_category_choices("JF")
                self.hidden.clear()
            name = "CATEGORY-1" if doctype == CATEGORY else "DETAIL-1"
            self.document_denials.add((doctype, name, "select"))
            self.assertEqual(self.module.list_category_choices("JF"), [])
            self.document_denials.clear()
            self.documents[(doctype, name)]["inactive"] = 1
            self.assertEqual(self.module.list_category_choices("JF"), [])
            self.documents[(doctype, name)]["inactive"] = 0
        self.document_denials.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.module.list_category_choices("JF")

    def test_flat_category_choices_do_not_fill_missing_list_columns_or_changed_provenance(self):
        self.forced_lists[DETAIL] = [Record(name="DETAIL-1", category="CATEGORY-1", company="JF", inactive=0, minor_name="可读列")]
        with self.assertRaises(PermissionError):
            self.module.list_category_choices("JF")
        self.forced_lists.clear()
        original = self.get_doc

        def changed_source(doctype, name, **kwargs):
            doc = original(doctype, name, **kwargs)
            if doctype == DETAIL:
                doc.company = "OTHER"
            return doc

        self.frappe.get_doc.side_effect = changed_source
        self.assertEqual(self.module.list_category_choices("JF"), [])

    def test_flat_category_choices_require_specific_keywords_after_one_hundred_rows(self):
        for index in range(100):
            self.add(DETAIL, "DETAIL-LIMIT-" + str(index), category="CATEGORY-1", company="JF", inactive=0,
                minor_name="过多子项" + str(index), material=None)
        with self.assertRaisesRegex(ValueError, "更具体关键词"):
            self.module.list_category_choices("JF")
        self.assertEqual(len(self.module.list_category_choices("JF", "类别小类原文")), 1)
        for value in ([], None, "x" * 141):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.list_category_choices("JF", value)

    def test_category_selection_requires_four_profile_fields_read_write_and_editable_metadata(self):
        before = deepcopy((self.documents, self.versions))
        for permission in ("read", "write"):
            for field in self.module.CATEGORY_FIELDS:
                self.hidden[(PROFILE, permission)] = {field}
                with self.subTest(permission=permission, field=field), self.assertRaises(PermissionError):
                    self.module.save_archive(self.existing_payload(profile={"category": "CATEGORY-1", "category_detail": "DETAIL-1"}))
                self.assert_unchanged(before)
                self.hidden.clear()
        for field in self.module.CATEGORY_FIELDS:
            self.metas[PROFILE].get_field(field).read_only = 1
            with self.subTest(readonly=field), self.assertRaises(PermissionError):
                self.module.save_archive(self.existing_payload(profile={"category": "CATEGORY-1", "category_detail": "DETAIL-1"}))
            self.assert_unchanged(before)
            self.metas[PROFILE].get_field(field).read_only = 0

    def test_category_selection_rechecks_read_select_company_active_and_parent(self):
        payload = self.existing_payload(profile={"category": "CATEGORY-1", "category_detail": "DETAIL-1"})
        before = deepcopy((self.documents, self.versions))
        for dt, name in ((CATEGORY, "CATEGORY-1"), (DETAIL, "DETAIL-1")):
            for permission in ("read", "select"):
                self.document_denials.add((dt, name, permission))
                with self.subTest(dt=dt, permission=permission), self.assertRaises(PermissionError):
                    self.module.save_archive(payload)
                self.assert_unchanged(before)
                self.document_denials.clear()
            for field, value in (("company", "OTHER"), ("inactive", 1)):
                self.documents[(dt, name)][field] = value
                mutated = deepcopy((self.documents, self.versions))
                with self.subTest(dt=dt, field=field), self.assertRaisesRegex(ValueError, "须属于"):
                    self.module.save_archive(payload)
                self.assert_unchanged(mutated)
                self.documents, self.versions = deepcopy(before)
        self.documents[(DETAIL, "DETAIL-1")]["category"] = "OTHER-PARENT"
        with self.assertRaisesRegex(ValueError, "当前大类"):
            self.module.save_archive(payload)
        self.documents, self.versions = deepcopy(before)
        for dt, field in ((CATEGORY, "major_name"), (DETAIL, "minor_name")):
            self.hidden[(dt, "read")] = {field}
            with self.subTest(dt=dt, hidden=field), self.assertRaises(PermissionError):
                self.module.save_archive(payload)
            self.hidden.clear()

    def test_unchanged_category_rename_deactivation_does_not_refresh_old_snapshot(self):
        dto = self.module.save_archive(self.existing_payload(profile={"category": "CATEGORY-1", "category_detail": "DETAIL-1"}))
        self.documents[(CATEGORY, "CATEGORY-1")].update(major_name="重命名大类", inactive=1)
        self.documents[(DETAIL, "DETAIL-1")].update(minor_name="重命名小类", inactive=1)
        self.document_denials.update({(CATEGORY, "CATEGORY-1", "read"), (DETAIL, "DETAIL-1", "select")})
        self.hidden[(PROFILE, "write")] = {"major_category", "minor_category"}
        self.frappe.get_doc.reset_mock()
        result = self.module.save_archive({"name": dto["name"], "profile_version": dto["profile_version"],
            "item_version": dto["item_version"], "profile": {"notes": "普通编辑", "category": "CATEGORY-1", "category_detail": "DETAIL-1"}})
        self.assertEqual((result["profile"]["major_category"], result["profile"]["minor_category"]),
                         (" 类别大类原文 ", " 类别小类原文 "))
        self.assertFalse(any(call.args[0] in (CATEGORY, DETAIL) for call in self.frappe.get_doc.call_args_list))
        self.assertEqual(self.module.read_archive("BUSINESS-ITEM")["profile"]["category"], "CATEGORY-1")

    def test_category_clear_and_parent_change_clear_minor_and_native_snapshot_guard(self):
        self.module.save_archive(self.existing_payload(profile={"category": "CATEGORY-1", "category_detail": "DETAIL-1"}))
        self.add(CATEGORY, "CATEGORY-2", company="JF", major_name="另一个大类", inactive=0)
        dto = self.module.save_archive(self.existing_payload(profile={"category": "CATEGORY-2"}))
        self.assertEqual(dto["profile"]["major_category"], "另一个大类")
        self.assertIsNone(dto["profile"]["category_detail"])
        self.assertIsNone(dto["profile"]["minor_category"])
        direct = self.get_doc(PROFILE, "BUSINESS-ITEM")
        direct.major_category = "伪造类别原文"
        with self.assertRaisesRegex(ValueError, "仅能通过明确改选"):
            direct.save()
        dto = self.module.save_archive(self.existing_payload(profile={"category": None}))
        self.assertTrue(all(dto["profile"][field] is None for field in self.module.CATEGORY_FIELDS))
        with self.assertRaisesRegex(ValueError, "明细所属的大类"):
            self.module.save_archive(self.existing_payload(profile={"category_detail": "DETAIL-1"}))

    def test_legacy_without_links_preserves_originals_and_does_not_backfill_receipt(self):
        row = self.documents[(PROFILE, "BUSINESS-ITEM")]
        for field in self.module.RECEIPT:
            row.pop(field)
        self.module.save_archive(self.existing_payload(profile={"notes": "旧档案普通编辑"}))
        saved = self.documents[(PROFILE, "BUSINESS-ITEM")]
        self.assertEqual((saved["major_category"], saved["minor_category"], saved["legacy_code"]),
                         ("旧大类原文", "旧小类原文", "旧代码"))
        self.assertTrue(all(field not in saved for field in self.module.RECEIPT))
        direct = self.get_doc(PROFILE, "BUSINESS-ITEM")
        direct.major_category = "旧档案原文可改"
        direct.save()
        self.assertEqual(self.documents[(PROFILE, "BUSINESS-ITEM")]["major_category"], "旧档案原文可改")

    def test_remarks_multiline_original_is_preserved_without_moving_into_notes(self):
        original = "原备注第一行\n原备注第二行\r\n尾行"
        dto = self.module.save_archive(self.existing_payload(profile={"legacy_remarks": original}))
        self.assertEqual(dto["profile"]["legacy_remarks"], original)
        self.assertEqual(dto["profile"]["notes"], "原注意事项")
        self.assertEqual(self.documents[(PROFILE, "BUSINESS-ITEM")]["legacy_remarks"], original)
        self.add("Item", "NATIVE-MULTILINE", item_code="NATIVE-MULTILINE", item_name="原生备注商品", item_group="Products", stock_uom="Nos")
        direct = self.new_doc(PROFILE)
        direct.company, direct.item_code, direct.legacy_remarks = "JF", "NATIVE-MULTILINE", original
        direct.save()
        self.assertEqual(self.documents[(PROFILE, "NATIVE-MULTILINE")]["legacy_remarks"], original)

    def test_native_before_insert_generates_receipt_and_exact_server_marker(self):
        self.add("Item", "NATIVE-ITEM", item_code="NATIVE-ITEM", item_name="原生新档案商品", stock_uom="Nos", item_group="Products")
        direct = self.new_doc(PROFILE)
        direct.company, direct.item_code, direct.display_name = "JF", "NATIVE-ITEM", "原生档案原文"
        direct.save()
        saved = self.documents[(PROFILE, "NATIVE-ITEM")]
        self.assertEqual(UUID(saved["request_id"]).version, 4)
        self.assertEqual(saved["creation_mode"], "native_existing_item")
        self.assertEqual(len(saved["request_digest"]), 64)
        self.assertNotIn("_jf_product_archive_server", saved)
        self.assertIs(direct.__dict__["_jf_product_archive_server"], self.module._SERVER)
        self.assertEqual(self.saves, [(PROFILE, "NATIVE-ITEM")])

    def test_native_forged_receipts_markers_and_existing_source_tampering_reject(self):
        for field, value in (("request_id", str(uuid4())), ("request_digest", "b" * 64), ("creation_mode", "new_item")):
            direct = self.new_doc(PROFILE)
            direct.company, direct.item_code = "JF", "BUSINESS-ITEM"
            direct.set(field, value)
            direct.__dict__["_jf_product_archive_server"] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "只能由服务器生成"):
                direct.save()
        direct = self.new_doc(PROFILE)
        direct.request_id = str(uuid4())
        direct.__dict__["_jf_product_archive_server"] = True
        with self.assertRaisesRegex(ValueError, "只能由服务器生成"):
            self.module.validate_record(direct)
        for field, value in (("company", "OTHER"), ("item_code", "OTHER"), ("request_id", str(uuid4())),
                             ("request_digest", "b" * 64), ("creation_mode", "new_item")):
            direct = self.get_doc(PROFILE, "BUSINESS-ITEM")
            direct.set(field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "不能改|不能修改"):
                direct.save()

    def test_images_require_exact_private_field_bound_loaded_file_read_and_download(self):
        self.profile["profile_image"] = "/private/files/product.png"
        self.attachment()
        dto = self.module.read_archive("BUSINESS-ITEM")
        self.assertEqual(dto["image_states"]["profile_image"],
                         {"url": "/private/files/product.png?fid=PRIVATE-FILE", "state": "已上传"})
        self.assertEqual(dto["profile"]["profile_image"], "/private/files/product.png")
        for denial in ("read", "download"):
            if denial == "read":
                self.document_denials.add(("File", "PRIVATE-FILE", "read"))
            else:
                self.denied_downloads.add("PRIVATE-FILE")
            with self.subTest(denial=denial):
                dto = self.module.read_archive("BUSINESS-ITEM")
                self.assertEqual(dto["image_states"]["profile_image"], {"url": "", "state": "附件不可用"})
                self.assertNotIn("profile_image", dto["profile"])
                with self.assertRaises(PermissionError):
                    self.module.validate_image(self.get_doc(PROFILE, "BUSINESS-ITEM"), "profile_image", "/private/files/product.png")
            self.document_denials.clear()
            self.denied_downloads.clear()

    def test_two_image_fields_and_same_url_alias_never_authorize_wrong_binding(self):
        url = "/private/files/product.png"
        self.profile["profile_image"] = self.profile["patent_image"] = url
        self.attachment(field="patent_image")
        dto = self.module.read_archive("BUSINESS-ITEM")
        self.assertEqual(dto["image_states"]["profile_image"], {"url": "", "state": "附件不可用"})
        self.assertEqual(dto["image_states"]["patent_image"]["state"], "已上传")
        self.assertNotIn("profile_image", dto["profile"])
        self.forced_files = ["PRIVATE-FILE"]
        for field, value in (("attached_to_field", "patent_image"), ("attached_to_name", "OTHER-PROFILE"),
                             ("attached_to_doctype", "Item"), ("is_private", 0), ("file_url", "/private/files/alias.png")):
            attachment = self.documents[("File", "PRIVATE-FILE")]
            attachment.update(attached_to_field="profile_image", attached_to_name="BUSINESS-ITEM",
                              attached_to_doctype=PROFILE, is_private=1, file_url=url)
            attachment[field] = value
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.module.validate_image(self.get_doc(PROFILE, "BUSINESS-ITEM"), "profile_image", url)
            state = self.module._image_state(self.get_doc(PROFILE, "BUSINESS-ITEM"), "profile_image")
            self.assertEqual(state, {"url": "", "state": "附件不可用"})

    def test_image_malformed_public_external_and_missing_file_values_are_generic(self):
        for value in ("/files/public.png", "https://external.invalid/image.png", "//external.invalid/image.png",
                      "/private/files/image.png?fid=OTHER", "/private/files/image.png#fragment", "/private/files/missing.png"):
            self.profile["profile_image"] = value
            with self.subTest(value=value):
                dto = self.module.read_archive("BUSINESS-ITEM")
                self.assertEqual(dto["image_states"]["profile_image"], {"url": "", "state": "附件不可用"})
                self.assertNotIn("profile_image", dto["profile"])
                with self.assertRaises(PermissionError):
                    self.module.validate_image(self.get_doc(PROFILE, "BUSINESS-ITEM"), "profile_image", value)
        self.forced_files = ["DELETED-FILE"]
        self.profile["profile_image"] = "/private/files/missing.png"
        self.assertEqual(self.module._image_state(self.get_doc(PROFILE, "BUSINESS-ITEM"), "profile_image")["url"], "")

    def test_valid_image_replacement_and_clear_keep_private_stored_value(self):
        url = "/private/files/product.png"
        self.attachment()
        dto = self.module.save_archive(self.existing_payload(profile={"profile_image": url}))
        self.assertEqual(dto["profile"]["profile_image"], url)
        self.assertEqual(self.documents[(PROFILE, "BUSINESS-ITEM")]["profile_image"], url)
        dto = self.module.save_archive(self.existing_payload(profile={"profile_image": None}))
        self.assertEqual(dto["image_states"]["profile_image"], {"url": "", "state": "未上传"})
        self.assertIsNone(dto["profile"]["profile_image"])

    def test_image_upload_reconciliation_returns_exact_authorized_candidates_without_writes(self):
        self.metas["File"] = Meta([{"fieldname": field, "fieldtype": "Data"}
                                  for field in ("file_name", "file_url")])
        self.attachment(file_name="private.png", creation=datetime(2026, 10, 1, 9))
        self.attachment(name="OTHER", field="patent_image", file_name="other.png")
        before = deepcopy((self.documents, self.versions))
        candidates = self.module.list_image_attachments("BUSINESS-ITEM", "profile_image")
        self.assertEqual(candidates, [{"name": "PRIVATE-FILE", "file_name": "private.png",
            "file_url": "/private/files/product.png", "unique_url": "/private/files/product.png?fid=PRIVATE-FILE",
            "creation": "2026-10-01T09:00:00"}])
        self.assert_unchanged(before)
        for permission in ("read", "write"):
            self.hidden[(PROFILE, permission)] = {"profile_image"}
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.module.list_image_attachments("BUSINESS-ITEM", "profile_image")
            self.hidden.clear()
        self.hidden[("File", "read")] = {"file_url"}
        with self.assertRaises(PermissionError):
            self.module.list_image_attachments("BUSINESS-ITEM", "profile_image")
        self.hidden.clear()
        self.denials.add((PROFILE, "write"))
        with self.assertRaises(PermissionError):
            self.module.list_image_attachments("BUSINESS-ITEM", "profile_image")

    def test_image_upload_reconciliation_rechecks_alias_denials_limits_and_field_selectors(self):
        self.metas["File"] = Meta([{"fieldname": field, "fieldtype": "Data"}
                                  for field in ("file_name", "file_url")])
        self.attachment(file_name="private.png")
        self.forced_files = ["PRIVATE-FILE"]
        for field, value in (("attached_to_name", "OTHER"), ("attached_to_field", "patent_image"),
                             ("is_private", 0), ("file_url", "/private/files/../secret.png")):
            attachment = self.documents[("File", "PRIVATE-FILE")]
            previous = attachment[field]
            attachment[field] = value
            with self.subTest(field=field):
                self.assertEqual(self.module.list_image_attachments("BUSINESS-ITEM", "profile_image"), [])
            attachment[field] = previous
        self.denied_downloads.add("PRIVATE-FILE")
        self.assertEqual(self.module.list_image_attachments("BUSINESS-ITEM", "profile_image"), [])
        self.denied_downloads.clear()
        self.document_denials.add(("File", "PRIVATE-FILE", "read"))
        self.assertEqual(self.module.list_image_attachments("BUSINESS-ITEM", "profile_image"), [])
        self.document_denials.clear()
        self.forced_files = ["PRIVATE-FILE"] * 101
        with self.assertRaisesRegex(ValueError, "附件较多"):
            self.module.list_image_attachments("BUSINESS-ITEM", "profile_image")
        for field in ("image", None, [], {}):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.list_image_attachments("BUSINESS-ITEM", field)

    def test_new_images_are_rejected_before_profile_creation(self):
        for field in self.module.IMAGES:
            payload = self.creation_payload()
            payload["profile"][field] = "/private/files/product.png"
            before = deepcopy((self.documents, self.versions))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "先保存"):
                self.module.save_archive(payload)
            self.assert_unchanged(before)

    def test_unchanged_legacy_patent_external_url_is_preserved_but_never_returned(self):
        legacy = "https://legacy.invalid/patent.png"
        self.profile["patent_image"] = legacy
        dto = self.module.save_archive(self.existing_payload(profile={"notes": "保留历史专利图", "patent_image": legacy}))
        self.assertEqual(self.documents[(PROFILE, "BUSINESS-ITEM")]["patent_image"], legacy)
        self.assertNotIn("patent_image", dto["profile"])
        self.assertEqual(dto["image_states"]["patent_image"], {"url": "", "state": "附件不可用"})
        with self.assertRaises(PermissionError):
            self.module.save_archive(self.existing_payload(profile={"patent_image": "https://other.invalid/new.png"}))

    def test_list_scope_search_projection_activity_and_native_source_are_rechecked(self):
        self.profile["patent_image"] = "https://legacy.invalid/patent.png"
        dto = self.module.list_archives("JF", search="旧编号")
        self.assertEqual(len(dto), 1)
        self.assertEqual(dto[0]["profile_version"], self.profile["modified"].isoformat())
        self.assertNotIn("patent_image", dto[0]["profile"])
        self.hidden[(PROFILE, "read")] = {"legacy_number"}
        self.assertEqual(self.module.list_archives("JF", search="旧编号"), [])
        self.assertNotIn("legacy_number", {row[0] for row in self.frappe.get_list.call_args.kwargs["or_filters"]})
        self.hidden.clear()
        self.profile["inactive"] = 1
        self.assertEqual(self.module.list_archives("JF"), [])
        self.assertEqual(len(self.module.list_archives("JF", include_inactive=True)), 1)
        projected = Record(deepcopy(self.profile))
        self.forced_lists[PROFILE] = [projected]
        self.documents[(PROFILE, "BUSINESS-ITEM")]["company"] = "OTHER"
        self.assertEqual(self.module.list_archives("JF", include_inactive=True), [])
        self.documents[(PROFILE, "BUSINESS-ITEM")]["company"] = "JF"
        self.denials.add(("Item", "read"))
        with self.assertRaises(PermissionError):
            self.module.list_archives("JF", include_inactive=True)

    def test_guest_invalid_uuid_json_unknown_payload_and_nonfinite_reference_values_reject(self):
        before = deepcopy((self.documents, self.versions))
        for changes in ({"request_id": "bad"}, {"request_id": str(uuid4()).upper()},
                        {"request_id": "00000000-0000-1000-8000-000000000000"}, {"request_digest": "forged"},
                        {"profile_version": "old"}, {"item_version": "old"}, {"can_write": True}, {"doctype": PROFILE}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.module.save_archive(self.creation_payload(**changes))
            self.assert_unchanged(before)
        for payload in (None, [], "not-json", "[]", "null"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.module.save_archive(payload)
        for field, value in (("list_price", "NaN"), ("weight", "Infinity"), ("silver_weight", -1),
                             ("stone_weight", True), ("inactive", 2),
                             ("notes", []), ("display_name", "a" * 141)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.module.save_archive(self.existing_payload(profile={field: value}))
            self.assert_unchanged(before)
        self.frappe.session.user = "Guest"
        for call in (lambda: self.module.init_archive(), lambda: self.module.read_archive("BUSINESS-ITEM"),
                     lambda: self.module.list_archives("JF"), lambda: self.module.save_archive(self.creation_payload()),
                     lambda: self.module.resolve_creation(str(uuid4()), "JF")):
            with self.assertRaises(PermissionError):
                call()
        self.assert_unchanged(before)


if __name__ == "__main__":
    unittest.main()
