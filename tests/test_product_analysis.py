"""Product counts preserve source permissions, empty keys and category identity."""

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestProductAnalysis(unittest.TestCase):
    def setUp(self):
        self.documents, self.hidden_from_list = {}, set()
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict, self.frappe.PermissionError = Record, PermissionError
        self.frappe.session = Record(user="manager@example.invalid")
        self.frappe.has_permission = Mock(return_value=True)
        self.company = types.SimpleNamespace(check_permission=Mock())
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: (
            self.company if doctype == "Company" else self.documents[doctype][name]))
        self.frappe.get_list = Mock(side_effect=lambda doctype, filters, **kwargs: [
            name for name in self.matching(doctype, filters) if name not in self.hidden_from_list])
        self.frappe.get_all = Mock(side_effect=lambda doctype, filters, **kwargs: self.matching(doctype, filters))
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value="CNY"))

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.getdate = Mock()
        picture = types.ModuleType("jifeng_erp.production_picture")
        picture._readable_image = Mock()
        picture._normalize_image_url = Mock(side_effect=AssertionError("count analysis must not inspect images"))
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = Mock(side_effect=AssertionError("count analysis must not request full catalog columns"))
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils, "frappe.model": model,
                                     "jifeng_erp.production_picture": picture}):
            scope = load_module("product_scope_under_test", ROOT / "jifeng_erp/inventory_scope.py")
            with patch.dict(sys.modules, {"jifeng_erp.inventory_scope": scope}):
                catalog = load_module("product_catalog_under_test", ROOT / "jifeng_erp/catalog_reports.py")
                with patch.dict(sys.modules, {"jifeng_erp.catalog_reports": catalog}):
                    self.module = load_module("product_analysis_under_test", ROOT / "jifeng_erp/product_analysis.py")
        self.picture = picture

    def matching(self, doctype, query):
        return [name for name, doc in self.documents.get(doctype, {}).items()
                if all(doc.get(field) == value for field, value in query.items())]

    def profile(self, name, inactive=0, company="JF", readable=True, item_code=None, **fields):
        item_code = item_code or f"ITEM-{name}"
        item = Record(name=item_code, item_name=f"商品 {name}",
                      image="/private/files/never-read.png", stock_uom="Nos")
        item["has_permission"] = Mock(return_value=True)
        self.documents.setdefault("Item", {})[item_code] = item
        doc = Record(name=name, company=company, item_code=item_code, inactive=inactive,
                     list_price="NaN", weight="bad", materials=[{}, {}, {}], **fields)
        doc["has_permission"] = Mock(return_value=readable)
        self.documents.setdefault("Jifeng Product Profile", {})[name] = doc
        return doc

    def report(self, **filters):
        return self.module.product_analysis({"company": "JF", **filters})

    def groups(self, **filters):
        return [row for row in self.report(**filters)[1] if row["is_group"]]

    def test_eight_dimensions_use_only_existing_text_fields(self):
        expected = {"品牌": ("brand",), "类别": ("major_category", "minor_category"),
                    "材质": ("material",), "来源": ("source",), "设计": ("designer",),
                    "打版": ("pattern_maker",), "建模": ("modeler",), "风格": ("style",)}
        self.profile("P1", **{field: f"文本-{field}" for fields in expected.values() for field in fields})
        for dimension, fields in expected.items():
            with self.subTest(dimension=dimension):
                columns, rows = self.report(dimension=dimension)
                for field in fields:
                    self.assertEqual(rows[0][field], f"文本-{field}")
                    self.assertEqual(next(col for col in columns if col["fieldname"] == field)["fieldtype"], "Data")
                self.assertEqual(rows[0]["record_count"], 1)
        self.picture._readable_image.assert_not_called()
        self.assertEqual({call.args[0] for call in self.frappe.get_doc.call_args_list},
                         {"Company", "Jifeng Product Profile", "Item"})

    def test_counts_are_per_profile_and_expansion_never_counts_twice(self):
        self.profile("P3", brand="甲", inactive=1)
        self.profile("P2", brand="甲", item_code="SHARED")
        self.profile("P1", brand="甲", item_code="SHARED")
        self.profile("P4", brand="乙")
        columns, rows = self.report()
        groups = [row for row in rows if row["is_group"]]
        self.assertEqual({row["brand"]: (row["record_count"], row["active_count"], row["inactive_count"])
                          for row in groups}, {"甲": (3, 2, 1), "乙": (1, 1, 0)})
        for index, group in enumerate(rows):
            if not group["is_group"]:
                continue
            children = []
            for row in rows[index + 1:]:
                if row["is_group"]:
                    break
                children.append(row)
            self.assertEqual(len(children), group["record_count"])
            self.assertTrue(all(row["indent"] == 1 and "record_count" not in row for row in children))
            self.assertEqual(group["record_count"], group["active_count"] + group["inactive_count"])
        self.assertEqual({row["document"] for row in rows if not row["is_group"]}, {"P1", "P2", "P3", "P4"})
        self.assertEqual(next(col for col in columns if col["fieldname"] == "document")["options"],
                         "Jifeng Product Profile")
        self.assertEqual(next(col for col in columns if col["fieldname"] == "item_code")["options"], "Item")
        self.assertFalse(any(col["fieldtype"] in ("Currency", "Float") for col in columns))

    def test_empty_keys_are_separate_from_placeholder_text(self):
        for name, value in (("NONE", None), ("EMPTY", ""), ("SPACE", " \t "),
                            ("TEXT", "未填写"), ("TEXT2", "品牌未填写")):
            self.profile(name, brand=value)
        groups = self.groups()
        by_value = {row["brand"]: row for row in groups}
        self.assertEqual(set(by_value), {None, "未填写", "品牌未填写"})
        self.assertEqual(by_value[None]["record_count"], 3)
        self.assertEqual(by_value[None]["empty_note"], "品牌未填写")
        self.assertEqual(by_value["品牌未填写"]["empty_note"], "")

    def test_categories_keep_two_independent_keys_and_blank_components(self):
        pairs = [("A/B", "C"), ("A", "B/C"), ("A", "B"), ("A", "D"),
                 (None, "B"), ("A", None), (None, None), ("未填写", "未填写")]
        for index, (major, minor) in enumerate(pairs):
            self.profile(f"P{index}", major_category=major, minor_category=minor)
        columns, rows = self.report(dimension="类别")
        groups = [row for row in rows if row["is_group"]]
        self.assertEqual({(row["major_category"], row["minor_category"]) for row in groups}, set(pairs))
        self.assertTrue(all(row["record_count"] == 1 for row in groups))
        self.assertTrue({"major_category", "minor_category"}.issubset(col["fieldname"] for col in columns))

    def test_special_text_is_preserved_as_data_and_native_source_links(self):
        value = '<script>alert("x")</script>/引号\'；类别'
        self.profile("P1", brand=value)
        self.assertEqual([row["brand"] for row in self.report()[1]], [value, value])
        script = (ROOT / "jifeng_erp/jifeng_erp/report/jifeng_product_analysis/jifeng_product_analysis.js").read_text(encoding="utf-8")
        self.assertIn("frappe.utils.escape_html", script)
        self.assertIn("tree: true", script)

    def test_hidden_profiles_are_absent_from_counts_and_sources(self):
        self.profile("VISIBLE", brand="甲")
        self.profile("DOC-DENIED", brand="隐藏", readable=False)
        self.profile("LIST-DENIED", brand="隐藏")
        self.hidden_from_list.add("LIST-DENIED")
        rows = self.report()[1]
        self.assertEqual(rows[0]["record_count"], 1)
        self.assertEqual([row["document"] for row in rows if not row["is_group"]], ["VISIBLE"])

    def test_hidden_related_item_rejects_counts_and_expansion(self):
        doc = self.profile("P1", brand="甲")
        self.documents["Item"][doc.item_code]["has_permission"].return_value = False
        with self.assertRaises(PermissionError):
            self.report()
        self.documents["Item"][doc.item_code]["has_permission"].return_value = True
        self.hidden_from_list.add(doc.item_code)
        with self.assertRaises(PermissionError):
            self.report()

    def test_guest_missing_read_and_denied_company_are_rejected(self):
        self.frappe.session["user"] = "Guest"
        with self.assertRaises(PermissionError):
            self.report()
        self.frappe.session["user"] = "manager@example.invalid"
        self.frappe.has_permission.return_value = False
        with self.assertRaises(PermissionError):
            self.report()
        self.frappe.has_permission.return_value = True
        self.company.check_permission.side_effect = PermissionError("无公司权限")
        with self.assertRaises(PermissionError):
            self.report()
        self.frappe.get_list.assert_not_called()

    def test_company_select_status_and_item_filters_follow_catalog_scope(self):
        self.profile("ACTIVE", brand="甲")
        stopped = self.profile("STOPPED", brand="甲", inactive=1)
        self.profile("OTHER", company="OTHER", brand="乙")
        rows = self.report(status="停用", item_code=stopped.item_code)[1]
        self.assertEqual((rows[0]["record_count"], rows[0]["active_count"], rows[0]["inactive_count"]), (1, 0, 1))
        self.assertEqual(rows[1]["document"], "STOPPED")
        self.company.check_permission.assert_called_with("select")
        self.frappe.get_list.assert_any_call("Jifeng Product Profile",
            filters={"company": "JF", "inactive": 1, "item_code": stopped.item_code},
            pluck="name", distinct=True, limit_page_length=0)
        with self.assertRaises(ValueError):
            self.report(status="已审核")

    def test_company_change_between_listing_and_loading_is_not_exposed(self):
        doc = self.profile("P1", brand="甲")
        native_get_list = self.frappe.get_list.side_effect

        def change_after_list(doctype, **kwargs):
            names = native_get_list(doctype, **kwargs)
            if doctype == "Jifeng Product Profile":
                doc["company"] = "OTHER"
            return names

        self.frappe.get_list.side_effect = change_after_list
        self.assertEqual(self.report()[1], [])

    def test_unknown_dimensions_reject_before_source_queries(self):
        for value in ("brand", "销量", "", None, ["品牌"], {"field": "brand"}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "分析维度"):
                self.report(dimension=value)
        self.frappe.get_list.assert_not_called()

    def test_report_roles_are_exactly_existing_catalog_roles(self):
        folder = ROOT / "jifeng_erp/jifeng_erp/report/jifeng_product_analysis"
        definition = json.loads((folder / "jifeng_product_analysis.json").read_text(encoding="utf-8"))
        self.assertEqual([role["role"] for role in definition["roles"]],
                         ["System Manager", "Stock Manager", "Manufacturing Manager"])
        self.assertEqual(definition["ref_doctype"], "Jifeng Product Profile")
        self.assertEqual((definition["prepared_report"], definition["add_total_row"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
