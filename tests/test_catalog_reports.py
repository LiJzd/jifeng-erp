"""Archive visibility and quantities must not become partial costs or mixed units."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class TestCatalogReports(unittest.TestCase):
    def setUp(self):
        self.docs, self.denied = {}, set()
        frappe = types.ModuleType("frappe")
        frappe._dict, frappe.PermissionError = Record, PermissionError
        frappe.session = Record(user="manager@example.invalid")
        frappe.has_permission = Mock(return_value=True)
        frappe.get_list = Mock(return_value=["JF"])
        self.company = types.SimpleNamespace(check_permission=Mock())
        frappe.get_doc = Mock(return_value=self.company)
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
        self.picture = picture
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        path = Path(__file__).parents[1] / "jifeng_erp/catalog_reports.py"
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils,
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

    def test_guest_and_denied_company_cannot_query(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.module.product_catalog({"company": "JF"})
        self.frappe.session.user = "manager@example.invalid"
        self.company.check_permission.side_effect = PermissionError("无公司权限")
        with self.assertRaises(PermissionError):
            self.module.product_catalog({"company": "OTHER"})
        self.scope.readable_documents.assert_not_called()

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
