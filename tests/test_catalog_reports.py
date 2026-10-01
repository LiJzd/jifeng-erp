"""Archive visibility and quantities must not become partial costs or mixed units."""

import ast
from copy import deepcopy
import importlib.util
import ipaddress
import json
from pathlib import Path
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit


class Record(dict):
    __getattr__ = dict.get


class TestCatalogReports(unittest.TestCase):
    def setUp(self):
        self.docs, self.denied, self.files, self.hidden_fields = {}, set(), {}, {}
        frappe = types.ModuleType("frappe")
        frappe._dict, frappe.PermissionError = Record, PermissionError
        frappe.DoesNotExistError = KeyError
        frappe.session = Record(user="manager@example.invalid")
        frappe.has_permission = Mock(return_value=True)
        frappe.get_list = Mock(return_value=["JF"])
        self.company = types.SimpleNamespace(check_permission=Mock())
        frappe.get_doc = Mock(side_effect=lambda doctype, name: self.company if doctype == "Company" else self.files[name])
        frappe.get_all = Mock(side_effect=lambda doctype, filters, **kwargs: [
            name for name, file in self.files.items() if all(file.get(key) == value for key, value in filters.items())])
        frappe.db = types.SimpleNamespace(get_value=Mock(return_value="CNY"))
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe = frappe
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.column = lambda field, label, kind="Data", options=None, width=130: {
            "fieldname": field, "fieldtype": kind, "options": options}
        scope.decimal_number = lambda value: Decimal(str(value or 0))

        def readable(doctype, query, complete=False):
            if query.get("name") in self.denied and complete:
                raise PermissionError("来源不可读")
            return {doc.name: doc for doc in self.docs.get(doctype, [])
                    if not query.get("name") or doc.name == query["name"]}

        scope.readable_documents = Mock(side_effect=readable)
        self.scope = scope
        picture = types.ModuleType("jifeng_erp.production_picture")
        picture._readable_image = Mock(return_value=("", "无附件权限"))
        # Use the production URL parser; tests of archive binding must not
        # replace normalization with a more permissive test implementation.
        source = Path(__file__).parents[1] / "jifeng_erp/production_picture.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name in ("_normalize_image_url", "_effective_port")]
        namespace = {"ipaddress": ipaddress, "unquote": unquote, "urlsplit": urlsplit,
                     "get_url": lambda: "https://erp.example.invalid"}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), "exec"), namespace)
        picture._normalize_image_url = namespace["_normalize_image_url"]
        self.picture = picture
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        schema = json.loads((Path(__file__).parents[1] / "jifeng_erp/jifeng_erp/doctype/"
            "jifeng_product_profile/jifeng_product_profile.json").read_text(encoding="utf-8"))
        self.fields = {schema["name"]: {"name", *(field["fieldname"] for field in schema["fields"])},
                       "Item": {"name", "item_name", "stock_uom", "image"}}
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = Mock(side_effect=lambda doctype, **kwargs:
            self.fields[doctype] - self.hidden_fields.get(doctype, set()))
        for kind in ("mold", "plate"):
            tool_schema = json.loads((Path(__file__).parents[1] / "jifeng_erp/jifeng_erp/doctype" /
                ("jifeng_" + kind) / ("jifeng_" + kind + ".json")).read_text(encoding="utf-8"))
            self.fields[tool_schema["name"]] = {"name", *(field["fieldname"] for field in tool_schema["fields"])}
        self.fields["File"] = {"name", "file_url", "is_private", "attached_to_doctype", "attached_to_name", "attached_to_field"}
        # Execute the actual frozen Tool image authorization and private parser;
        # this report suite does not need unrelated creation/transaction imports.
        archive_source = Path(__file__).parents[1] / "jifeng_erp/product_archive.py"
        archive_tree = ast.parse(archive_source.read_text(encoding="utf-8"))
        parser = next(node for node in archive_tree.body if isinstance(node, ast.FunctionDef) and node.name == "_private_url")
        image_namespace = {"frappe": frappe, "unquote": unquote, "urlsplit": urlsplit, "_UNSET": object(),
            "_columns": lambda dt, permission: set(model.get_permitted_fields(dt, permission_type=permission))}
        exec(compile(ast.Module(body=[parser], type_ignores=[]), str(archive_source), "exec"), image_namespace)
        tool_source = Path(__file__).parents[1] / "jifeng_erp/tool_archive.py"
        tool_tree = ast.parse(tool_source.read_text(encoding="utf-8"))
        image = next(node for node in tool_tree.body if isinstance(node, ast.FunctionDef) and node.name == "_image_state")
        exec(compile(ast.Module(body=[image], type_ignores=[]), str(tool_source), "exec"), image_namespace)
        tools = types.ModuleType("jifeng_erp.tool_archive")
        tools._image_state = image_namespace["_image_state"]
        access = types.ModuleType("jifeng_erp.catalog_access")
        access.check_employee = Mock()
        runtime = patch.dict(sys.modules, {"jifeng_erp.tool_archive": tools, "jifeng_erp.catalog_access": access})
        runtime.start()
        self.addCleanup(runtime.stop)
        path = Path(__file__).parents[1] / "jifeng_erp/catalog_reports.py"
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils, "frappe.model": model,
                                    "jifeng_erp.inventory_scope": scope,
                                    "jifeng_erp.production_picture": picture}):
            spec = importlib.util.spec_from_file_location("catalog_reports_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.item = Record(name="FG", item_name="成品", stock_uom="Nos", image="/private/files/private.png")
        self.profile = Record(name="FG", company="JF", item_code="FG", inactive=0,
                              material_unit_cost="17.5")
        self.docs["Item"] = [self.item]
        self.docs[self.module.PROFILE] = [self.profile]

    def attachment(self, name, **values):
        document = Record(file_url="/private/files/archive.png", is_private=1,
            attached_to_doctype=self.module.PROFILE, attached_to_name=self.profile.name,
            attached_to_field="profile_image", has_permission=Mock(return_value=True),
            is_downloadable=Mock(return_value=True), unique_url="/private/files/archive.png?fid=" + name)
        document.update(values)
        self.files[name] = document
        return document

    def test_guest_and_denied_company_cannot_query(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.module.product_catalog({"company": "JF"})
        self.frappe.session.user = "manager@example.invalid"
        self.company.check_permission.side_effect = PermissionError("无公司权限")
        with self.assertRaises(PermissionError):
            self.module.product_catalog({"company": "OTHER"})
        self.scope.readable_documents.assert_not_called()

    def tool(self, kind):
        doctype = self.module.MOLD if kind == "mold" else self.module.PLATE
        record = Record(doctype=doctype, name=kind.upper(), company="JF", item_code="FG", image="/private/files/archive.png",
            maker=None, modeler=None, location="原库位", quantity="1.5" if kind == "mold" else " 原数量 ",
            legacy_number=" 007 ", legacy_copy=0, copy_from=None, product_code="原号", is_new=lambda: False,
            mold_no="M-1", mold_date="2026-10-01", amount=3, scrapped=0, scrap_date=None, scrap_reason=None, remark="原文",
            plate_no="P-1", plate_date="2026-10-01", reference_cost=3, retired=0, retired_date=None, retired_reason=None,
            plate_type="原类型", assessment_score="-0", notes="原文")
        self.docs[doctype] = [record]
        return record, self.module.mold_catalog if kind == "mold" else self.module.plate_catalog

    def test_tool_report_exact_private_parent_and_field_without_global_url_authorization(self):
        for kind in ("mold", "plate"):
            document, report = self.tool(kind)
            self.files.clear()
            exact = self.attachment("EXACT", attached_to_doctype=document.doctype,
                                    attached_to_name=document.name, attached_to_field="image")
            self.attachment("ALIAS", attached_to_doctype=document.doctype, attached_to_name="OTHER", attached_to_field="image")
            before = deepcopy(document)
            with self.subTest(kind=kind):
                row = report({"company": "JF"})[1][0]
                self.assertEqual((row["image_url"], row["image_status"]), (exact.unique_url, "已上传"))
                self.assertEqual(document, before)
                self.assertEqual(row["legacy_number"], " 007 ")
                self.assertEqual(self.frappe.get_all.call_args.kwargs["filters"], {
                    "file_url": document.image, "is_private": 1, "attached_to_doctype": document.doctype,
                    "attached_to_name": document.name, "attached_to_field": "image"})
        self.picture._readable_image.assert_not_called()

    def test_tool_report_readable_alias_wrong_binding_public_and_denied_download_never_leak(self):
        for kind in ("mold", "plate"):
            document, report = self.tool(kind)
            for changed in ({"attached_to_name": "OTHER"}, {"attached_to_doctype": "Item"},
                            {"attached_to_field": "remark"}, {"is_private": 0},
                            {"has_permission": Mock(return_value=False)}, {"is_downloadable": Mock(return_value=False)}):
                self.files.clear()
                exact = self.attachment("EXACT", **({"attached_to_doctype": document.doctype,
                    "attached_to_name": document.name, "attached_to_field": "image"} | changed))
                self.attachment("READABLE-ALIAS", attached_to_doctype=document.doctype,
                                attached_to_name="OTHER", attached_to_field="image")
                with self.subTest(kind=kind, changed=changed):
                    row = report({"company": "JF"})[1][0]
                    self.assertEqual((row["image_url"], row["image_status"]), ("", "附件不可用"))
                    self.assertNotIn(exact.unique_url, str(row))
                    self.assertNotIn("EXACT", str(row))
                    self.assertNotIn("READABLE-ALIAS", str(row))
        self.picture._readable_image.assert_not_called()

    def test_tool_invalid_historical_urls_or_file_field_permission_loss_are_masked(self):
        for kind in ("mold", "plate"):
            document, report = self.tool(kind)
            for url in ("https://remote.example.invalid/a.png", "/files/a.png", "/private/files/a.png?_file=OTHER",
                        "/private/files/%2fescaped.png", "/private/files/%2e%2e/a.png"):
                document["image"] = url
                self.frappe.get_all.reset_mock()
                row = report({"company": "JF"})[1][0]
                self.assertEqual((row["image_url"], row["image_status"]), ("", "附件不可用"))
                self.assertNotIn(url, str(row))
                self.frappe.get_all.assert_not_called()
            document["image"] = "/private/files/archive.png"
            self.attachment("EXACT", attached_to_doctype=document.doctype, attached_to_name=document.name,
                            attached_to_field="image")
            self.hidden_fields["File"] = {"file_url"}
            row = report({"company": "JF"})[1][0]
            self.assertEqual(row["image_url"], "")
            self.hidden_fields.clear()
            self.hidden_fields[document.doctype] = {"image"}
            with self.assertRaises(PermissionError):
                report({"company": "JF"})
            self.hidden_fields.clear()
        self.picture._readable_image.assert_not_called()

    def test_tool_attachment_binding_change_or_disappearance_after_query_is_safe(self):
        document, report = self.tool("mold")
        self.attachment("MOVED", attached_to_doctype=document.doctype, attached_to_name="OTHER", attached_to_field="image")
        self.frappe.get_all.side_effect = None
        self.frappe.get_all.return_value = ["MOVED"]
        self.assertEqual(report({"company": "JF"})[1][0]["image_url"], "")
        self.files.clear()
        self.assertEqual(report({"company": "JF"})[1][0]["image_url"], "")

    def test_company_selection_does_not_require_full_company_read(self):
        self.module.product_catalog({"company": "JF"})
        self.frappe.get_doc.assert_called_once_with("Company", "JF")
        self.company.check_permission.assert_called_once_with("select")

    def test_company_change_between_list_and_load_is_not_exposed(self):
        self.profile.company = "OTHER"
        _, rows = self.module.product_catalog({"company": "JF"})
        self.assertEqual(rows, [])
        self.picture._readable_image.assert_not_called()

    def test_hidden_related_item_does_not_leak_image_or_price(self):
        self.denied.add("FG")
        with self.assertRaises(PermissionError):
            self.module.product_catalog({"company": "JF"})
        self.picture._readable_image.assert_not_called()

    def test_private_image_uses_native_download_guard(self):
        _, rows = self.module.product_catalog({"company": "JF"})
        self.assertEqual(rows[0]["image_url"], "")
        self.assertEqual(rows[0]["image_status"], "无附件权限")
        self.assertEqual(rows[0]["material_unit_cost"], Decimal("17.5"))
        self.picture._readable_image.assert_called_once_with("/private/files/private.png")

    def test_original_text_and_archive_image_are_independent_and_reads_do_not_mutate(self):
        values = dict(legacy_number="000001", legacy_code="旧代码", display_name="旧名称",
                      display_uom="对（不是UOM）", legacy_remarks="第一行\n第二行")
        self.profile.update(values)
        before = deepcopy((self.item, self.profile))
        columns, rows = self.module.product_catalog({"company": "JF"})
        self.assertEqual((rows[0]["product_name"], rows[0]["stock_uom"]), ("成品", "Nos"))
        self.assertEqual({field: rows[0][field] for field in values}, values)
        self.assertEqual(rows[0]["profile_image_url"], "")
        self.assertEqual(rows[0]["profile_image_status"], "未上传")
        self.assertEqual((self.item, self.profile), before)
        self.assertTrue({*values, "profile_image_url", "profile_image_status"}.issubset(
            {column["fieldname"] for column in columns}))
        self.profile.update({field: "" for field in values})
        _, empty = self.module.product_catalog({"company": "JF"})
        self.assertTrue(all(empty[0][field] == "" for field in values))

    def test_archive_private_image_requires_exact_binding_and_both_native_guards(self):
        self.profile["profile_image"] = "/private/files/archive.png"
        exact = self.attachment("EXACT")
        self.attachment("ALIAS", attached_to_name="OTHER-PROFILE")
        _, rows = self.module.product_catalog({"company": "JF"})
        self.assertEqual(rows[0]["profile_image_url"], exact.unique_url)
        self.assertEqual(rows[0]["profile_image_status"], "已上传")
        self.assertEqual(self.profile["profile_image"], "/private/files/archive.png")
        self.frappe.get_all.assert_called_once_with("File", filters={"file_url": "/private/files/archive.png",
            "is_private": 1, "attached_to_doctype": self.module.PROFILE,
            "attached_to_name": self.profile.name, "attached_to_field": "profile_image"},
            pluck="name", order_by="name", limit_page_length=0)
        exact.has_permission.assert_called_once_with("read")
        exact.is_downloadable.assert_called_once_with()

    def test_readable_same_url_alias_cannot_authorize_restricted_exact_attachment(self):
        self.profile["profile_image"] = "/private/files/archive.png"
        for binding in ({"attached_to_field": "patent_image"}, {"attached_to_name": "OTHER-PROFILE"}):
            self.files.clear()
            exact = self.attachment("RESTRICTED", has_permission=Mock(return_value=False))
            self.attachment("READABLE-ALIAS", **binding)
            with self.subTest(binding=binding):
                _, rows = self.module.product_catalog({"company": "JF"})
                self.assertEqual(rows[0]["profile_image_url"], "")
                self.assertEqual(rows[0]["profile_image_status"], "无附件权限")
                self.assertNotIn("RESTRICTED", str(rows))
                self.assertNotIn("READABLE-ALIAS", str(rows))
                exact.is_downloadable.assert_not_called()
        exact.has_permission.return_value = True
        exact.is_downloadable.return_value = False
        self.assertEqual(self.module.product_catalog({"company": "JF"})[1][0]["profile_image_url"], "")

    def test_wrong_binding_missing_public_remote_and_fid_urls_never_expose_archive_url(self):
        for key, value in (("attached_to_name", "OTHER"), ("attached_to_doctype", "Item"),
                           ("attached_to_field", "image"), ("is_private", 0)):
            self.files.clear()
            self.attachment("WRONG", **{key: value})
            self.profile["profile_image"] = "/private/files/archive.png"
            with self.subTest(key=key):
                row = self.module.product_catalog({"company": "JF"})[1][0]
                self.assertEqual(row["profile_image_url"], "")
                self.assertEqual(row["profile_image_status"], "附件记录不存在")
        self.files.clear()
        self.assertEqual(self.module.product_catalog({"company": "JF"})[1][0]["profile_image_url"], "")
        self.frappe.get_all.reset_mock()
        for value in ("/files/archive.png", "https://remote.example.invalid/archive.png",
                      "/private/files/archive.png?fid=OTHER", "/private/files/%2e%2e/archive.png"):
            self.profile["profile_image"] = value
            with self.subTest(value=value):
                row = self.module.product_catalog({"company": "JF"})[1][0]
                self.assertEqual(row["profile_image_url"], "")
                self.assertEqual(row["profile_image_status"], "图片地址无效")
        self.frappe.get_all.assert_not_called()

    def test_archive_binding_is_rechecked_after_file_load_and_missing_record_is_safe(self):
        self.profile["profile_image"] = "/private/files/archive.png"
        self.attachment("MOVED", attached_to_name="OTHER")
        self.frappe.get_all.return_value = ["MOVED"]
        self.frappe.get_all.side_effect = None
        row = self.module.product_catalog({"company": "JF"})[1][0]
        self.assertEqual(row["profile_image_url"], "")
        self.files.clear()
        self.assertEqual(self.module.product_catalog({"company": "JF"})[1][0]["profile_image_url"], "")

    def test_any_required_profile_or_item_read_field_denies_complete_product_report(self):
        for doctype, field in ((self.module.PROFILE, "legacy_remarks"), (self.module.PROFILE, "list_price"),
                               (self.module.PROFILE, "profile_image"), ("Item", "image"), ("Item", "stock_uom")):
            self.hidden_fields = {doctype: {field}}
            with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                self.module.product_catalog({"company": "JF"})
        self.picture._readable_image.assert_not_called()
        self.frappe.get_all.assert_not_called()

    def test_native_helper_file_callbacks_require_confirmed_database_rollback(self):
        source = Path(__file__).parents[1] / "jifeng_erp/catalog_original_validation.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_rollback")
        for fails in (False, True):
            with self.subTest(database_rollback_fails=fails):
                events = []
                previous = {field: [Mock()] for field in ("before_rollback", "after_rollback")}
                before = Mock(side_effect=lambda: events.append("new-before"))
                after = Mock(side_effect=lambda: events.append("new-file-cleanup"))
                db = types.SimpleNamespace(before_rollback=types.SimpleNamespace(_functions=previous["before_rollback"] + [before]),
                    after_rollback=types.SimpleNamespace(_functions=previous["after_rollback"] + [after]))

                def rollback(point):
                    events.append("database-rollback")
                    if fails:
                        raise RuntimeError("database rollback failed")

                restore = Mock(side_effect=lambda old: events.append("restore-caller"))
                namespace = {"frappe": types.SimpleNamespace(db=db), "_end_rollback_only": rollback,
                             "_restore_callbacks": restore}
                exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), "exec"), namespace)
                if fails:
                    with self.assertRaisesRegex(AssertionError, "database_rollback"):
                        namespace["_rollback"]("POINT", previous)
                    after.assert_not_called()
                else:
                    namespace["_rollback"]("POINT", previous)
                    after.assert_called_once_with()
                self.assertEqual(events, ["new-before", "database-rollback"]
                    + ([] if fails else ["new-file-cleanup"]) + ["restore-caller"])
                restore.assert_called_once_with(previous)
                for functions in previous.values():
                    functions[0].assert_not_called()

    def test_material_rows_keep_precise_identity_and_distinct_units(self):
        self.docs["Item"] += [Record(name="RAW", item_name="原料")]
        self.profile["materials"] = [Record(name="ROW-1", item_code="RAW", uom="Kg",
            qty_per_unit="2", unit_price="3.1", note=""), Record(name="ROW-2", item_code="RAW",
            uom="Gram", qty_per_unit="10", unit_price="0.01", note="")]
        _, rows = self.module.product_material_catalog({"company": "JF"})
        self.assertEqual([(row["material_row"], row["uom"], row["reference_amount"]) for row in rows],
                         [("ROW-1", "Kg", Decimal("6.2")), ("ROW-2", "Gram", Decimal("0.10"))])
        self.denied.add("RAW")
        with self.assertRaises(PermissionError):
            self.module.product_material_catalog({"company": "JF"})

    def test_history_filter_is_explicit_and_invalid_status_is_rejected(self):
        self.module.product_catalog({"company": "JF", "status": "停用"})
        self.scope.readable_documents.assert_any_call(self.module.PROFILE, {"company": "JF", "inactive": 1})
        with self.assertRaises(ValueError):
            self.module.product_catalog({"company": "JF", "status": "已过账"})


if __name__ == "__main__":
    unittest.main()
