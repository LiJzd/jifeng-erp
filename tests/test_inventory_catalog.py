"""Permission and source projection models for inventory browse; no live DB."""

import ast
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).parents[1]
PROFILE = "Jifeng Inventory Profile"


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Meta:
    def __init__(self, environment, doctype):
        self.environment, self.doctype = environment, doctype

    @property
    def fields(self):
        return list(self.environment.metas[self.doctype].values())

    def get_field(self, name):
        return self.environment.metas[self.doctype].get(name)

    def get_permlevel_access(self, permission_type, parenttype=None):
        self.environment.assertEqual((permission_type, parenttype), ("mask", None))
        return self.environment.mask_levels.get(self.doctype, set())


class NativeDocument(Record):
    environment = None

    @property
    def meta(self):
        return Meta(self.environment, self.doctype)

    def has_permission(self, permission):
        environment = self.environment
        return ((self.doctype, permission) not in environment.role_denials
                and (self.doctype, self.name, permission) not in environment.doc_denials)

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native permission")

    def is_downloadable(self):
        return self.name not in self.environment.download_denials

    @property
    def unique_url(self):
        return self.file_url + "?fid=" + self.name


class TestInventoryCatalog(unittest.TestCase):
    def setUp(self):
        self.documents, self.metas = {}, {}
        self.hidden, self.projection_omissions, self.list_denials = {}, {}, set()
        self.mask_levels = {}
        self.role_denials, self.doc_denials, self.download_denials = set(), set(), set()
        self.list_hooks, self.doc_hooks, self.forced_rows = {}, {}, {}
        self.forced_files = None
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="stock-user@example.invalid")
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = type("DoesNotExistError", (ValueError,), {})
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.has_permission = lambda doctype, permission: (doctype, permission) not in self.role_denials
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.get_meta = lambda doctype: Meta(self, doctype)
        self.frappe.db = types.SimpleNamespace(commit=Mock(), set_value=Mock(), sql=Mock())
        self.frappe.new_doc, self.frappe.delete_doc = Mock(), Mock()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        product = types.ModuleType("jifeng_erp.product_archive")
        tree = ast.parse((ROOT / "jifeng_erp/product_archive.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_private_url")
        namespace = {"urlsplit": urlsplit, "unquote": unquote}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "canonical_private_url", "exec"), namespace)
        product._private_url = namespace["_private_url"]
        self.module_patches = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model,
                                                     "jifeng_erp.product_archive": product})
        self.module_patches.start()
        self.addCleanup(self.module_patches.stop)
        spec = importlib.util.spec_from_file_location("inventory_catalog_under_test", ROOT / "jifeng_erp/inventory_catalog.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        schema = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_inventory_profile/jifeng_inventory_profile.json")
                            .read_text(encoding="utf-8"))
        self.metas[PROFILE] = {field["fieldname"]: Record(field) for field in schema["fields"]}
        self.metas["Item"] = {field: Record(fieldname=field, label=field, fieldtype="Data")
                              for field in self.module.ITEM_FIELDS}
        self.metas["File"] = {field: Record(fieldname=field, fieldtype="Data", label=field)
                              for field in self.module.FILE_REQUIRED}
        self.metas["Company"] = {}
        self.permitted = {PROFILE: self.module.PROFILE_FIELDS | self.module.PROFILE_REQUIRED,
                          "Item": self.module.ITEM_FIELDS | self.module.ITEM_REQUIRED,
                          "File": self.module.FILE_REQUIRED, "Company": {"name"}}
        NativeDocument.environment = self
        self.add("Company", "JF")
        self.add("Company", "OTHER")
        self.item = self.add("Item", "ITEM-A", item_code="ITEM-A", item_name="原生名称", item_group="Raw Material",
                             stock_uom="Nos", disabled=1, image="/private/files/item.png")
        self.profile = self.add(PROFILE, "PROFILE-A", company="JF", archive_kind="物料", item_code="ITEM-A",
                                legacy_number=" 0007 ", legacy_code=" 同原代码 ", old_number=" 旧号 ", display_name="旧名称",
                                display_uom="对（旧原文）", specification=" 5×7\n保留第二行 ", list_price=Decimal("17.50"),
                                processing_price=Decimal("2.30"), upper_limit=Decimal("99.5"), lower_limit=1,
                                conversion_uom="Kg", conversion_value=Decimal("9.00"), supplier="SUP", location="原库位",
                                inactive=0, material=" 原材质 ", shape="无关形状", color="无关颜色")

    def add(self, doctype, name, **values):
        row = Record(doctype=doctype, name=name, modified=datetime(2026, 10, 1, 12, 0, 0, 123456), **values)
        self.documents[doctype, name] = row
        return row

    def columns(self, doctype, **kwargs):
        self.assertEqual(kwargs, {"permission_type": "read", "ignore_virtual": True})
        return self.permitted[doctype] - self.hidden.get(doctype, set())

    def get_doc(self, doctype, name):
        if (doctype, name) not in self.documents:
            raise self.frappe.DoesNotExistError("missing native record")
        if doctype in self.doc_hooks:
            self.doc_hooks[doctype](name)
        return NativeDocument(deepcopy(self.documents[doctype, name]))

    @staticmethod
    def matches(document, filters):
        return all(document.get(key) == value for key, value in filters.items())

    def get_list(self, doctype, filters, fields, limit_page_length, **kwargs):
        start = kwargs.get("limit_start", 0)
        if doctype in self.forced_rows:
            rows = deepcopy(self.forced_rows[doctype][start:start + limit_page_length])
        else:
            documents = [document for (kind, name), document in self.documents.items()
                         if kind == doctype and (doctype, name) not in self.list_denials and self.matches(document, filters)]
            if kwargs.get("or_filters"):
                documents = [doc for doc in documents if any(
                    condition[2].strip("%").lower() in str(doc.get(condition[0], "")).lower()
                    for condition in kwargs["or_filters"])]
            documents.sort(key=lambda document: document.name)
            rows = [Record({field: doc.get(field) for field in fields if field not in (
                self.hidden.get(doctype, set()) | self.projection_omissions.get(doctype, set()))})
                for doc in documents[start:start + limit_page_length]]
        if doctype in self.list_hooks:
            self.list_hooks[doctype]()
        return rows

    def get_all(self, doctype, filters, pluck, order_by, limit_page_length):
        self.assertEqual((doctype, pluck, order_by), ("File", "name", "name"))
        self.assertEqual(set(filters), {"file_url", "is_private", "attached_to_doctype", "attached_to_name", "attached_to_field"})
        if self.forced_files is not None:
            return self.forced_files
        return sorted(name for (kind, name), document in self.documents.items()
                      if kind == doctype and self.matches(document, filters))[:limit_page_length]

    def file(self, name="EXACT", **changed):
        values = {"file_url": "/private/files/item.png", "is_private": 1, "attached_to_doctype": "Item",
                  "attached_to_name": "ITEM-A", "attached_to_field": "image", **changed}
        return self.add("File", name, **values)

    def second_archive(self):
        self.add("Item", "ITEM-B", item_code="ITEM-B", item_name="第二件商品", stock_uom="Kg", image=None)
        self.add(PROFILE, "PROFILE-B", **{key: value for key, value in self.profile.items()
                                        if key not in {"doctype", "name", "modified", "item_code"}}, item_code="ITEM-B")

    def query(self, **kwargs):
        return self.module.list_archives("物料", "JF", **kwargs)

    def page(self, **kwargs):
        return self.module.query_archives("物料", "JF", **kwargs)

    def add_archives(self, count):
        for index in range(count):
            suffix = f"{index:04d}"
            self.add("Item", "ITEM-" + suffix, item_code="ITEM-" + suffix, stock_uom="Nos", image=None)
            self.add(PROFILE, "PROFILE-" + suffix, company="JF", archive_kind="物料",
                     item_code="ITEM-" + suffix, display_name="分页商品", inactive=0, list_price=0)

    def test_pagination_counts_only_exact_readable_sources_and_preserves_saved_values(self):
        self.second_archive()
        self.add_archives(4)
        self.list_denials.add((PROFILE, "PROFILE-0000"))
        self.list_denials.add(("Item", "ITEM-0001"))
        self.doc_denials.add((PROFILE, "PROFILE-0002", "read"))
        del self.documents["Item", "ITEM-0003"]
        self.doc_denials.add(("Company", "JF", "read"))
        before = deepcopy(self.documents)
        first, second = self.page(page_size="1"), self.page(page="2", page_size=1)
        self.assertEqual((first["total"], first["total_pages"], first["page_size"]), (2, 2, 1))
        self.assertEqual((first["rows"][0]["name"], second["rows"][0]["name"]), ("PROFILE-A", "PROFILE-B"))
        self.assertEqual(first["rows"][0]["profile"]["list_price"], "17.50")
        self.assertTrue(first["readonly"])
        self.assertFalse(first["can_write"])
        self.assertEqual(self.documents, before)
        self.assertTrue(all(call.args[0] == "File" for call in self.frappe.get_all.call_args_list))
        for function in (self.frappe.db.sql, self.frappe.db.commit, self.frappe.db.set_value,
                         self.frappe.new_doc, self.frappe.delete_doc):
            function.assert_not_called()

    def test_true_pages_above_compatibility_limit_and_duplicate_candidates(self):
        self.add_archives(101)
        with self.assertRaisesRegex(ValueError, "缩小查询范围"):
            self.query()
        result = self.page(page=2)
        self.assertEqual((result["total"], result["total_pages"], result["page"]), (102, 2, 2))
        self.assertEqual([row["name"] for row in result["rows"]], ["PROFILE-0100", "PROFILE-A"])
        self.assertEqual(self.frappe.get_list.call_args_list[1].kwargs["limit_page_length"], self.module.SCAN_BATCH_SIZE)
        row = Record({field: self.profile.get(field) for field in self.module.PROFILE_FIELDS | self.module.PROFILE_REQUIRED})
        self.forced_rows[PROFILE] = [row] * 1001
        self.frappe.get_doc.reset_mock()
        with self.assertRaises(PermissionError):
            self.page()
        self.assertTrue(all(call.args[0] == "Company" for call in self.frappe.get_doc.call_args_list))

    def test_complete_scans_above_1000_have_true_totals_and_last_pages(self):
        self.add_archives(1500)
        self.add(PROFILE, "OTHER-COMPANY", company="OTHER", archive_kind="物料", item_code="ITEM-A", inactive=0)
        self.add(PROFILE, "OTHER-KIND", company="JF", archive_kind="材料", item_code="ITEM-A", inactive=0)
        result = self.page(page=16)
        self.assertEqual((result["total"], result["total_pages"], result["page"]), (1501, 16, 16))
        self.assertEqual([row["name"] for row in result["rows"]], ["PROFILE-A"])
        calls = [call for call in self.frappe.get_list.call_args_list if call.args[0] == PROFILE]
        self.assertEqual([call.kwargs["limit_start"] for call in calls], [0, 500, 1000, 1500] * 2)
        for call in calls:
            self.assertEqual(call.kwargs["order_by"], "name asc")
            self.assertEqual(call.kwargs["limit_page_length"], 500)
            self.assertEqual(call.kwargs["filters"], {"company": "JF", "archive_kind": "物料", "inactive": 0})
            self.assertNotIn("ignore_permissions", call.kwargs)
        result = self.page(page="1501", page_size="1")
        self.assertEqual((result["total"], result["page"], result["rows"][0]["name"]), (1501, 1501, "PROFILE-A"))
        self.assertTrue(all(call.args[0] == "File" for call in self.frappe.get_all.call_args_list))
        for function in (self.frappe.db.sql, self.frappe.db.commit, self.frappe.db.set_value,
                         self.frappe.new_doc, self.frappe.delete_doc):
            function.assert_not_called()

    def test_cross_batch_duplicate_projection_and_field_revocation_refuse_everything(self):
        self.add_archives(501)
        original = self.get_list

        def duplicate(doctype, **kwargs):
            if doctype == PROFILE and kwargs.get("limit_start") == 500:
                kwargs["limit_start"] = 0
            return original(doctype, **kwargs)

        self.frappe.get_list.side_effect = duplicate
        with self.assertRaises(PermissionError):
            self.page()
        self.assertFalse(any(call.args[0] == "Item" for call in self.frappe.get_doc.call_args_list))
        self.frappe.get_list.side_effect = original
        for change in (lambda: self.projection_omissions.update({PROFILE: {"display_name"}}),
                       lambda: self.hidden.update({PROFILE: {"display_name"}})):
            self.projection_omissions.clear()
            self.hidden.clear()
            self.list_hooks[PROFILE] = change
            with self.assertRaises(PermissionError):
                self.page()

    def test_rescan_changes_to_membership_versions_and_skipped_sources_refuse_total(self):
        self.add_archives(501)
        baseline = deepcopy(self.documents)
        for change in (
                lambda: self.documents[PROFILE, "PROFILE-0000"].update(modified=datetime(2026, 10, 2)),
                lambda: self.documents[PROFILE, "PROFILE-0000"].update(display_name="changed without modified"),
                lambda: self.list_denials.add((PROFILE, "PROFILE-0000")),
                lambda: self.doc_denials.add(("Item", "ITEM-0000", "read"))):
            self.documents = deepcopy(baseline)
            self.list_denials.clear()
            self.doc_denials.clear()
            calls = 0

            def mutate_on_rescan():
                nonlocal calls
                calls += 1
                if calls == 3:
                    change()

            self.list_hooks[PROFILE] = mutate_on_rescan
            with self.assertRaises(PermissionError):
                self.page(page=6)

    def test_page_numbers_are_strict_and_outside_visible_range_is_not_silently_clamped(self):
        invalid = [True, False, 0, -1, 1.0, Decimal("1"), None, [], "", " 1", "1 ", "1\n", "01", "+1", "1.0", "1e0",
                   self.module.MAX_SAFE_INTEGER + 1, str(self.module.MAX_SAFE_INTEGER + 1)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.page(page=value)
        for value in invalid + [101, "101", 1001, "1001"]:
            with self.subTest(size=value), self.assertRaises(ValueError):
                self.page(page_size=value)
        self.frappe.get_list.assert_not_called()
        self.assertEqual(self.module._page_integer(str(self.module.MAX_SAFE_INTEGER), "页码", self.module.MAX_SAFE_INTEGER),
                         self.module.MAX_SAFE_INTEGER)
        with self.assertRaisesRegex(ValueError, "超过查询结果范围"):
            self.page(page="1001")
        with self.assertRaisesRegex(ValueError, "超过查询结果范围"):
            self.page(page=2)
        empty = self.page(search="无此原文")
        self.assertEqual((empty["page"], empty["total"], empty["total_pages"], empty["rows"]), (1, 0, 0, []))
        with self.assertRaisesRegex(ValueError, "超过查询结果范围"):
            self.page(search="无此原文", page=2)

    def test_total_and_rows_use_same_company_kind_status_and_authorized_keyword_projection(self):
        self.second_archive()
        self.documents[PROFILE, "PROFILE-B"].inactive = 1
        self.add(PROFILE, "OTHER-KIND", company="JF", archive_kind="材料", item_code="ITEM-A", inactive=0)
        self.add(PROFILE, "OTHER-COMPANY", company="OTHER", archive_kind="物料", item_code="ITEM-A", inactive=0)
        self.assertEqual(self.page()["total"], 1)
        self.assertEqual(self.page(status="全部")["total"], 2)
        result = self.page(status="停用", search="旧名称")
        self.assertEqual((result["status"], result["search"], result["rows"][0]["name"]), ("停用", "旧名称", "PROFILE-B"))
        self.hidden[PROFILE] = self.module.SEARCH_FIELDS
        with self.assertRaises(PermissionError):
            self.page(search="旧名称")

    def test_off_page_source_field_and_image_changes_reject_the_whole_page_and_total(self):
        self.file()
        self.second_archive()
        initial_documents, initial_meta = deepcopy(self.documents), deepcopy(self.metas)
        actions = {
            "permission": lambda: self.doc_denials.add((PROFILE, "PROFILE-B", "read")),
            "version": lambda: self.documents[PROFILE, "PROFILE-B"].update(modified=datetime(2026, 10, 2)),
            "projection": lambda: self.documents[PROFILE, "PROFILE-B"].update(list_price=Decimal("6")),
            "mask": lambda: self.metas[PROFILE]["list_price"].update(mask=1),
            "off_page_image": lambda: self.download_denials.add("EXACT"),
        }
        for label, change in actions.items():
            self.documents, self.metas = deepcopy(initial_documents), deepcopy(initial_meta)
            self.doc_denials.clear()
            self.download_denials.clear()
            loads = []

            def mutate_after_full_load(name):
                if name == "ITEM-A":
                    loads.append(name)
                    if len(loads) == 2:
                        change()

            self.doc_hooks["Item"] = mutate_after_full_load
            with self.subTest(change=label), self.assertRaises(PermissionError):
                # page two does not project A or its image, but both authorize total.
                self.page(page=2, page_size=1)
            self.assertEqual(loads, ["ITEM-A", "ITEM-A"])

    def test_paged_optional_projection_omissions_never_fall_back_to_full_documents(self):
        self.projection_omissions[PROFILE] = {"display_name", "display_uom", "list_price"}
        self.hidden["Item"] = {"stock_uom"}
        row = self.page()["rows"][0]
        self.assertEqual(self.page()["total"], 1)
        for field in self.projection_omissions[PROFILE]:
            self.assertNotIn(field, row["profile"])
            self.assertNotIn(field, row["field_meta"]["profile"])
        self.assertNotIn("stock_uom", row["item"])

    def test_empty_page_still_checks_current_scope_and_duplicate_candidates_cannot_inflate_total(self):
        self.list_hooks[PROFILE] = lambda: self.doc_denials.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.page(search="空结果")
        self.list_hooks.clear()
        self.doc_denials.clear()
        row = Record({field: self.profile.get(field) for field in self.module.PROFILE_FIELDS | self.module.PROFILE_REQUIRED})
        self.forced_rows[PROFILE] = [row, deepcopy(row)]
        with self.assertRaises(PermissionError):
            self.page()

    def test_reader_without_create_or_write_can_query_and_read_immutable_saved_values(self):
        self.role_denials.update({(PROFILE, "create"), (PROFILE, "write"), ("Item", "create"), ("Item", "write")})
        before = deepcopy(self.documents)
        row = self.query()[0]
        detail = self.module.read_archive("PROFILE-A", "物料", "JF")
        self.assertEqual(row, detail)
        self.assertTrue(row["readonly"])
        self.assertFalse(row["can_write"])
        self.assertEqual(row["profile"]["display_uom"], "对（旧原文）")
        self.assertEqual(row["item"]["stock_uom"], "Nos")
        self.assertEqual((row["profile"]["inactive"], row["item"]["disabled"]), (0, 1))
        self.assertEqual(row["profile"]["specification"], " 5×7\n保留第二行 ")
        self.assertEqual(row["profile"]["legacy_number"], " 0007 ")
        self.assertEqual(row["profile"]["list_price"], "17.50")
        self.assertEqual(row["profile"]["conversion_value"], "9.00")
        self.assertNotIn("stock_qty", row["item"])
        self.assertNotIn("shape", row["profile"])
        self.assertNotIn("color", row["profile"])
        self.assertTrue(all(meta["read_only"] and not meta["reqd"]
                            for group in row["field_meta"].values() for meta in group.values()))
        self.assertEqual(self.documents, before)
        for function in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql,
                         self.frappe.new_doc, self.frappe.delete_doc):
            function.assert_not_called()

    def test_manager_with_write_permissions_receives_the_same_readonly_contract(self):
        row = self.query()[0]
        self.assertTrue(row["readonly"])
        self.assertFalse(row["can_write"])
        self.assertTrue(all(meta["read_only"] for group in row["field_meta"].values() for meta in group.values()))

    def test_company_select_does_not_need_company_read(self):
        self.doc_denials.add(("Company", "JF", "read"))
        self.assertEqual(len(self.query()), 1)
        self.doc_denials.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.query()

    def test_guest_role_denial_and_invalid_scope_do_not_list_records(self):
        for user, denied in (("Guest", None), ("reader", PROFILE), ("reader", "Item")):
            self.frappe.session.user = user
            self.role_denials.clear()
            if denied:
                self.role_denials.add((denied, "read"))
            with self.subTest(user=user, denied=denied), self.assertRaises(PermissionError):
                self.query()
        self.frappe.get_list.assert_not_called()
        self.frappe.session.user = "reader"
        self.role_denials.clear()
        for kind, company in (("产品", "JF"), ([], "JF"), ("物料", " JF "), ("物料", None)):
            with self.subTest(kind=kind, company=company), self.assertRaises(ValueError):
                self.module.list_archives(kind, company)

    def test_same_legacy_code_does_not_merge_distinct_items_or_kinds(self):
        self.add("Item", "ITEM-B", item_code="ITEM-B", item_name="另一原生名称", stock_uom="Kg")
        self.add(PROFILE, "PROFILE-B", **{key: value for key, value in self.profile.items()
                                        if key not in {"doctype", "name", "modified", "item_code"}}, item_code="ITEM-B")
        self.add("Item", "ITEM-C", item_code="ITEM-C", stock_uom="Set")
        self.add(PROFILE, "PROFILE-C", company="JF", archive_kind="材料", item_code="ITEM-C", inactive=0,
                 legacy_code=" 同原代码 ", shape="原形状", color="原颜色", material="无关材质")
        self.assertEqual([row["item_code"] for row in self.query()], ["ITEM-A", "ITEM-B"])
        materials = self.module.list_archives("材料", "JF")
        self.assertEqual([row["item_code"] for row in materials], ["ITEM-C"])
        self.assertEqual((materials[0]["profile"]["shape"], materials[0]["profile"]["color"]), ("原形状", "原颜色"))
        self.assertNotIn("material", materials[0]["profile"])

    def test_archive_status_and_keyword_filters_use_native_projection(self):
        self.profile.inactive = 1
        self.assertEqual(self.query(), [])
        self.assertEqual(len(self.query(status="停用", search="同原代码")), 1)
        self.assertEqual(len(self.query(status="全部")), 1)
        self.assertEqual(len(self.module.read_archive("PROFILE-A", "物料", "JF")["profile"]),
                         len(self.query(status="全部")[0]["profile"]))
        self.assertEqual(self.query(search="无匹配", status="全部"), [])
        for options in ({"status": "未知"}, {"status": None}, {"search": []}, {"search": "x" * 141}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.query(**options)
        self.hidden[PROFILE] = self.module.SEARCH_FIELDS
        with self.assertRaises(PermissionError):
            self.query(search="旧号")

    def test_optional_fields_omitted_by_native_list_never_reappear_from_document(self):
        self.projection_omissions[PROFILE] = {"list_price", "display_uom", "supplier"}
        self.projection_omissions["Item"] = {"stock_uom", "image"}
        row = self.query()[0]
        for field in self.projection_omissions[PROFILE]:
            self.assertNotIn(field, row["profile"])
            self.assertNotIn(field, row["field_meta"]["profile"])
        self.assertNotIn("stock_uom", row["item"])
        self.assertEqual(row["image"]["state"], "无字段读取权限")
        self.frappe.get_all.assert_not_called()

    def test_field_permission_revoked_after_query_is_rechecked_and_hidden(self):
        self.doc_hooks["Item"] = lambda name: self.hidden.update({PROFILE: {"list_price"}, "Item": {"stock_uom", "image"}})
        row = self.query()[0]
        self.assertNotIn("list_price", row["profile"])
        self.assertNotIn("stock_uom", row["item"])
        self.assertEqual(row["image"]["url"], "")
        self.frappe.get_all.assert_not_called()

    def test_required_fields_permission_and_projection_loss_are_rejected(self):
        for doctype, field in ((PROFILE, "item_code"), (PROFILE, "company"), ("Item", "item_code")):
            for loss in (self.hidden, self.projection_omissions):
                loss.clear()
                loss[doctype] = {field}
                with self.subTest(doctype=doctype, field=field, loss=loss), self.assertRaises(PermissionError):
                    self.query()
                loss.clear()

    def test_native_list_and_document_user_permissions_both_guard_source(self):
        for doctype, name in ((PROFILE, "PROFILE-A"), ("Item", "ITEM-A")):
            self.list_denials = {(doctype, name)}
            self.assertEqual(self.query(), [])
            with self.assertRaises(PermissionError):
                self.module.read_archive("PROFILE-A", "物料", "JF")
            self.list_denials.clear()
            self.doc_denials = {(doctype, name, "read")}
            self.assertEqual(self.query(), [])
            with self.assertRaises(PermissionError):
                self.module.read_archive("PROFILE-A", "物料", "JF")
            self.doc_denials.clear()
        self.frappe.get_all.assert_not_called()

    def test_scope_status_source_or_version_change_after_list_is_not_exposed(self):
        for changed in ({"company": "OTHER"}, {"archive_kind": "材料"}, {"inactive": 1},
                        {"item_code": "ITEM-OTHER"}, {"modified": datetime(2026, 10, 1, 12, 0, 1)}):
            original = deepcopy(self.profile)
            self.list_hooks[PROFILE] = lambda changed=changed: self.profile.update(changed)
            self.assertEqual(self.query(), [], changed)
            self.profile.clear()
            self.profile.update(original)
        self.list_hooks.clear()
        self.list_hooks["Item"] = lambda: self.item.update(item_code="CHANGED-NATIVE-CODE")
        self.assertEqual(self.query(), [])

    def test_selected_company_and_kind_are_required_for_details(self):
        for kind, company in (("材料", "JF"), ("物料", "OTHER")):
            with self.assertRaises(PermissionError):
                self.module.read_archive("PROFILE-A", kind, company)
        for name in (None, "", [], " PROFILE-A "):
            with self.assertRaises(ValueError):
                self.module.read_archive(name, "物料", "JF")

    def test_company_permission_change_during_item_load_is_rechecked(self):
        self.doc_hooks["Item"] = lambda name: self.doc_denials.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.query()
        self.frappe.get_all.assert_not_called()

    def test_list_limit_is_explicit_instead_of_silently_truncating(self):
        row = {field: self.profile.get(field) for field in self.module.PROFILE_REQUIRED}
        self.forced_rows[PROFILE] = [row] * 101
        with self.assertRaises(ValueError):
            self.query()
        self.assertEqual(self.frappe.get_list.call_args.kwargs["limit_page_length"], 101)

    def test_authorized_item_image_is_labeled_and_exact_private_binding_is_queried(self):
        exact = self.file()
        self.file("ALIAS", attached_to_name="OTHER")
        row = self.query()[0]
        self.assertEqual(row["image"], {"source_doctype": "Item", "source_name": "ITEM-A", "source_field": "image",
                                      "label": "关联业务商品图片", "url": exact.file_url + "?fid=" + exact.name,
                                      "state": "已上传"})
        self.assertNotIn("image", row["item"])
        self.assertEqual(self.frappe.get_all.call_args.kwargs["filters"], {
            "file_url": self.item.image, "is_private": 1, "attached_to_doctype": "Item",
            "attached_to_name": "ITEM-A", "attached_to_field": "image"})

    def test_same_url_other_parent_or_field_public_and_external_images_do_not_authorize(self):
        for changed in ({"attached_to_name": "OTHER"}, {"attached_to_doctype": PROFILE},
                        {"attached_to_field": "description"}, {"is_private": 0}):
            self.file(**changed)
            self.forced_files = ["EXACT"]
            row = self.query()[0]
            self.assertEqual((row["image"]["url"], row["image"]["state"]), ("", "附件不可用"))
            self.assertNotIn("EXACT", str(row))
        self.forced_files = None
        for url in ("https://example.invalid/x.png", "/files/x.png", "/private/files/%2fescape.png",
                    "/private/files/%2e%2e/x.png", "/private/files/x.png?_file=ALIAS", "/private/files/x.png#fragment"):
            self.item.image = url
            self.frappe.get_all.reset_mock()
            row = self.query()[0]
            self.assertEqual(row["image"]["url"], "")
            self.assertNotIn(url, str(row))
            self.frappe.get_all.assert_not_called()

    def test_file_read_download_or_field_permission_denial_masks_image(self):
        self.file()
        self.file("READABLE-ALIAS", attached_to_name="OTHER")
        for denied in ("read", "download", "field"):
            self.doc_denials.clear()
            self.download_denials.clear()
            self.hidden.clear()
            if denied == "read":
                self.doc_denials.add(("File", "EXACT", "read"))
            elif denied == "download":
                self.download_denials.add("EXACT")
            else:
                self.hidden["File"] = {"file_url"}
            row = self.query()[0]
            self.assertEqual(row["image"]["url"], "", denied)
            self.assertNotIn("EXACT", str(row))
            self.assertNotIn("READABLE-ALIAS", str(row))

    def test_file_disappears_or_field_permission_changes_after_exact_query(self):
        self.forced_files = ["MISSING"]
        self.assertEqual(self.query()[0]["image"]["url"], "")
        self.file()
        self.forced_files = ["EXACT"]
        self.doc_hooks["File"] = lambda name: self.hidden.update({"File": {"file_url"}})
        self.assertEqual(self.query()[0]["image"]["url"], "")

    def test_omitted_or_changed_item_image_never_uses_full_document_or_alias(self):
        self.file()
        self.projection_omissions["Item"] = {"image"}
        self.assertEqual(self.query()[0]["image"]["url"], "")
        self.frappe.get_all.assert_not_called()
        self.projection_omissions.clear()
        self.list_hooks["Item"] = lambda: self.item.update(image="/private/files/changed.png")
        self.assertEqual(self.query()[0]["image"]["url"], "")
        self.frappe.get_all.assert_not_called()

    def test_missing_source_records_and_forged_native_list_identity_are_not_returned(self):
        del self.documents["Item", "ITEM-A"]
        self.assertEqual(self.query(), [])
        with self.assertRaises(PermissionError):
            self.module.read_archive("PROFILE-A", "物料", "JF")
        self.documents["Item", "ITEM-A"] = self.item
        self.forced_rows["Item"] = [Record({field: self.item.get(field) for field in self.module.ITEM_REQUIRED}, name="OTHER")]
        self.assertEqual(self.query(), [])

    def test_masked_required_identity_denies_read_instead_of_comparing_raw_documents(self):
        for doctype, field in ((PROFILE, "company"), (PROFILE, "item_code"), (PROFILE, "inactive"),
                              ("Item", "item_code")):
            df = self.metas[doctype][field]
            df.mask, df.permlevel = 1, 0
            for action in (self.query, lambda: self.module.read_archive("PROFILE-A", "物料", "JF")):
                with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                    action()
            df.mask = 0
        self.frappe.get_all.assert_not_called()

    def test_optional_masks_are_omitted_and_current_mask_revocation_is_rechecked(self):
        self.metas[PROFILE]["legacy_code"].mask = 1
        self.metas["Item"]["stock_uom"].mask = 1
        row = self.query()[0]
        self.assertNotIn("legacy_code", row["profile"])
        self.assertNotIn("stock_uom", row["item"])
        self.doc_hooks["Item"] = lambda name: self.metas[PROFILE]["list_price"].update(mask=1)
        self.assertNotIn("list_price", self.query()[0]["profile"])
        self.mask_levels[PROFILE] = {0}
        row = self.query()[0]
        self.assertEqual(row["profile"]["legacy_code"], " 同原代码 ")
        self.assertEqual(row["profile"]["list_price"], "17.50")

    def test_item_global_disabled_mask_does_not_filter_company_archive_status(self):
        self.metas["Item"]["disabled"].mask = 1
        row = self.query()[0]
        self.assertNotIn("disabled", row["item"])
        self.assertEqual(row["profile"]["inactive"], 0)
        self.assertEqual(self.query(status="停用"), [])
        self.assertEqual(len(self.query(status="全部")), 1)

    def test_masked_image_and_file_binding_are_never_authorized_from_raw_file_fields(self):
        self.file()
        for doctype, field in (("Item", "image"), ("File", "file_url"), ("File", "attached_to_name"),
                              ("File", "attached_to_field")):
            df = self.metas[doctype][field]
            df.mask, df.permlevel = 1, 0
            self.frappe.get_all.reset_mock()
            row = self.query()[0]
            self.assertEqual(row["image"]["url"], "", (doctype, field))
            self.assertNotIn("EXACT", str(row))
            self.frappe.get_all.assert_not_called()
            df.mask = 0
        self.doc_hooks["File"] = lambda name: self.metas["File"]["file_url"].update(mask=1)
        self.assertEqual(self.query()[0]["image"]["url"], "")

    def test_admin_or_current_mask_permlevel_grant_uses_native_readable_projection(self):
        self.file()
        self.metas["File"]["file_url"].update(mask=1, permlevel=0)
        self.metas["Item"]["image"].update(mask=1, permlevel=0)
        self.metas[PROFILE]["legacy_code"].update(mask=1, permlevel=0)
        self.mask_levels.update({"File": {0}, "Item": {0}, PROFILE: {0}})
        self.assertEqual(self.query()[0]["image"]["url"], "/private/files/item.png?fid=EXACT")
        self.mask_levels.clear()
        self.frappe.session.user = "Administrator"
        row = self.query()[0]
        self.assertEqual(row["profile"]["legacy_code"], " 同原代码 ")
        self.assertEqual(row["image"]["url"], "/private/files/item.png?fid=EXACT")

    def test_file_load_revoke_precedes_projection_and_never_exposes_old_field_access(self):
        self.file()
        self.doc_hooks["File"] = lambda name: self.metas[PROFILE]["list_price"].update(mask=1)
        row = self.query()[0]
        self.assertNotIn("list_price", row["profile"])
        self.assertNotIn("list_price", row["field_meta"]["profile"])
        self.assertEqual(row["profile"]["display_uom"], "对（旧原文）")
        self.assertEqual((row["profile"]["inactive"], row["item"]["disabled"]), (0, 1))
        self.assertEqual(row["image"]["url"], "/private/files/item.png?fid=EXACT")

    def test_final_file_reload_revoke_rejects_an_already_completed_single_projection(self):
        self.file()
        loads = []

        def revoke_after_projection(name):
            loads.append(name)
            if len(loads) == 2:
                self.metas[PROFILE]["list_price"].mask = 1

        self.doc_hooks["File"] = revoke_after_projection
        with self.assertRaises(PermissionError):
            self.module.read_archive("PROFILE-A", "物料", "JF")
        self.assertEqual(loads, ["EXACT", "EXACT"])

    def test_later_record_cannot_revoke_an_earlier_projection_field_or_source_read(self):
        self.second_archive()
        actions = {
            "profile_mask": lambda: self.metas[PROFILE]["list_price"].update(mask=1),
            "item_mask": lambda: self.metas["Item"]["stock_uom"].update(mask=1),
            "profile_read": lambda: self.doc_denials.add((PROFILE, "PROFILE-A", "read")),
            "item_read": lambda: self.doc_denials.add(("Item", "ITEM-A", "read")),
            "profile_field_read": lambda: self.hidden.update({PROFILE: {"list_price"}}),
            "item_field_read": lambda: self.hidden.update({"Item": {"stock_uom"}}),
        }
        initial_meta = deepcopy(self.metas)
        for label, revoke in actions.items():
            self.metas = deepcopy(initial_meta)
            self.hidden.clear()
            self.doc_denials.clear()
            self.doc_hooks["Item"] = lambda name, revoke=revoke: revoke() if name == "ITEM-B" else None
            with self.subTest(label=label), self.assertRaises(PermissionError):
                self.query()

    def test_last_source_reload_revoke_is_checked_across_the_whole_result(self):
        self.second_archive()
        loads = []

        def revoke_on_final_item(name):
            if name == "ITEM-B":
                loads.append(name)
                if len(loads) == 2:
                    self.metas[PROFILE]["list_price"].mask = 1

        self.doc_hooks["Item"] = revoke_on_final_item
        with self.assertRaises(PermissionError):
            self.query()
        self.assertEqual(loads, ["ITEM-B", "ITEM-B"])

    def test_later_record_cannot_invalidate_an_earlier_exact_private_image(self):
        self.file()
        self.second_archive()
        actions = {
            "file_read": lambda: self.doc_denials.add(("File", "EXACT", "read")),
            "download": lambda: self.download_denials.add("EXACT"),
            "file_mask": lambda: self.metas["File"]["attached_to_name"].update(mask=1),
            "file_field_read": lambda: self.hidden.update({"File": {"file_url"}}),
            "item_image_mask": lambda: self.metas["Item"]["image"].update(mask=1),
            "binding": lambda: self.documents["File", "EXACT"].update(attached_to_name="OTHER"),
            "public": lambda: self.documents["File", "EXACT"].update(is_private=0),
            "item_image": lambda: self.documents["Item", "ITEM-A"].update(image="/private/files/changed.png"),
            "deleted": lambda: self.documents.pop(("File", "EXACT")),
            "version": lambda: self.documents["File", "EXACT"].update(modified=datetime(2026, 10, 1, 12, 1)),
        }
        initial_documents, initial_meta = deepcopy(self.documents), deepcopy(self.metas)
        for label, revoke in actions.items():
            self.documents, self.metas = deepcopy(initial_documents), deepcopy(initial_meta)
            self.hidden.clear()
            self.doc_denials.clear()
            self.download_denials.clear()
            self.doc_hooks["Item"] = lambda name, revoke=revoke: revoke() if name == "ITEM-B" else None
            with self.subTest(label=label), self.assertRaises(PermissionError):
                self.query()

    def test_later_record_source_edits_do_not_replace_or_silently_keep_an_earlier_projection(self):
        self.second_archive()
        actions = {
            "profile_value_without_modified": lambda: self.documents[PROFILE, "PROFILE-A"].update(list_price=Decimal("9")),
            "item_value_without_modified": lambda: self.documents["Item", "ITEM-A"].update(stock_uom="Set"),
            "profile_version": lambda: self.documents[PROFILE, "PROFILE-A"].update(modified=datetime(2026, 10, 1, 12, 1)),
            "profile_scope": lambda: self.documents[PROFILE, "PROFILE-A"].update(company="OTHER"),
            "profile_status": lambda: self.documents[PROFILE, "PROFILE-A"].update(inactive=1),
            "item_deleted": lambda: self.documents.pop(("Item", "ITEM-A")),
        }
        initial_documents = deepcopy(self.documents)
        for label, change in actions.items():
            self.documents = deepcopy(initial_documents)
            self.doc_hooks["Item"] = lambda name, change=change: change() if name == "ITEM-B" else None
            with self.subTest(label=label), self.assertRaises(PermissionError):
                self.query()


if __name__ == "__main__":
    unittest.main()
