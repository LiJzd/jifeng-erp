"""Dashboard figures retain full document permissions and posted sales scope."""

import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.has_permission = Mock(return_value=True)
        self.frappe.PermissionError = PermissionError
        self.frappe.whitelist = lambda: lambda fn: fn
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.get_list = Mock(return_value=[Record(name="JF", default_currency="CNY")])
        self.sources = {}
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.decimal_number = Decimal
        scope.readable_documents = Mock(side_effect=lambda doctype, query: {
            row.get("name") or str(index): row for index, row in enumerate(self.sources.get(doctype, []))})
        self.scope = scope
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        utils.nowdate = lambda: "2026-09-30"
        path = Path(__file__).parents[1] / "jifeng_erp/dashboard.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                                    "jifeng_erp.inventory_scope": scope}):
            spec = importlib.util.spec_from_file_location("dashboard_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def test_signed_posted_sales_do_not_add_pos_sources(self):
        self.sources["Sales Invoice"] = [Record(base_net_total="100"), Record(base_net_total="-20")]
        self.sources["POS Invoice"] = [Record(base_net_total="100")]
        self.sources["Warehouse"] = [Record(name="W1")]
        self.sources["Bin"] = [Record(stock_value="50")]
        result = self.module.overview("JF", "2026-09-01", "2026-09-30")
        self.assertEqual(result["metrics"]["sales"], 80)
        self.assertEqual(result["metrics"]["stock_value"], 50)
        calls = self.scope.readable_documents.call_args_list
        self.assertFalse(any(call.args[0] == "POS Invoice" for call in calls))
        sales = next(call for call in calls if call.args[0] == "Sales Invoice")
        self.assertEqual(sales.args[1], {"company": "JF", "docstatus": 1,
                                       "posting_date": ["between", ["2026-09-01", "2026-09-30"]]})

    def test_figures_use_only_full_readable_documents(self):
        # The shared scope helper already removed a list-visible/full-denied
        # source; aggregate must use that result rather than SQL SUM the list.
        self.sources["Sales Invoice"] = [Record(base_net_total="3.25")]
        self.assertEqual(self.module._aggregate("Sales Invoice", "base_net_total", {}), 3.25)
        self.scope.readable_documents.assert_called_once_with("Sales Invoice", {})
        self.frappe.get_list.assert_not_called()

    def test_no_role_does_not_expose_zero_as_a_known_amount(self):
        self.frappe.has_permission.return_value = False
        self.assertIsNone(self.module._aggregate("Sales Invoice", "base_net_total", {}))
        self.assertIsNone(self.module._count("Work Order", {}))
        self.scope.readable_documents.assert_not_called()

    def test_current_stock_keeps_exact_readable_warehouse_scope(self):
        self.sources["Warehouse"] = [Record(name="VISIBLE")]
        self.sources["Bin"] = [Record(stock_value="17")]
        value, warehouses = self.module._stock_value("JF")
        self.assertEqual((value, warehouses), (17, ["VISIBLE"]))
        self.scope.readable_documents.assert_any_call("Bin", {"company": "JF", "warehouse": ["in", ["VISIBLE"]]})


if __name__ == "__main__":
    unittest.main()
