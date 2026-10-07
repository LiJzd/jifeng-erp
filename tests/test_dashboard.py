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
        self.frappe.DoesNotExistError = LookupError
        self.frappe.ValidationError = ValueError
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
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
        reports = types.ModuleType("frappe.desk.query_report")
        reports.get_report_doc = Mock(return_value=Record(name="native report"))
        self.native_reports = reports.get_report_doc
        report_patch = patch.dict(sys.modules, {"frappe.desk.query_report": reports})
        report_patch.start()
        self.addCleanup(report_patch.stop)
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

    def test_general_ledger_scope_keeps_company_dates_and_excludes_cancelled_entries(self):
        route = self.module.financial_report("General Ledger", "JF", "2026-09-01", "2026-09-30")
        self.assertEqual(route, {"route": ["query-report", "General Ledger"], "filters": {
            "company": "JF", "from_date": "2026-09-01", "to_date": "2026-09-30",
            "show_cancelled_entries": 0, "show_amount_in_company_currency": 1,
        }})
        self.native_reports.assert_called_once_with("General Ledger")
        self.scope.readable_documents.assert_not_called()

    def test_financial_statements_use_native_date_range_not_fiscal_year_defaults(self):
        for name in ("Profit and Loss Statement", "Cash Flow", "Balance Sheet"):
            with self.subTest(name=name):
                filters = self.module.financial_report(name, "JF", "2026-09-03", "2026-09-20")["filters"]
                self.assertEqual(filters["filter_based_on"], "Date Range")
                self.assertEqual(filters["period_start_date"], "2026-09-03")
                self.assertEqual(filters["period_end_date"], "2026-09-20")
                self.assertEqual(filters["periodicity"], "Monthly")
                self.assertNotIn("from_date", filters)
                self.assertNotIn("from_fiscal_year", filters)
                if name == "Cash Flow":
                    self.assertEqual(filters["show_opening_and_closing_balance"], 0)
                else:
                    self.assertEqual(filters["accumulated_values"], int(name == "Balance Sheet"))

    def test_native_report_permission_denial_hides_only_that_report(self):
        def permission(name):
            if name == "Profit and Loss Statement":
                raise PermissionError("revoked")
            return Record(name=name)
        self.native_reports.side_effect = permission
        result = self.module.overview("JF", "2026-09-01", "2026-09-30")
        self.assertEqual([r["name"] for r in result["financial_reports"]],
                         ["General Ledger", "Cash Flow", "Balance Sheet"])
        self.assertEqual(result["metrics"]["sales"], 0)
        with self.assertRaises(PermissionError):
            self.module.financial_report("Profit and Loss Statement", "JF", "2026-09-01", "2026-09-30")

    def test_report_scope_rejects_hidden_company_unknown_report_and_empty_dates(self):
        cases = [("General Ledger", "HIDDEN", "2026-09-01", "2026-09-30"),
                 ("Private Report", "JF", "2026-09-01", "2026-09-30"),
                 ("General Ledger", "JF", "", "2026-09-30")]
        for args in cases:
            with self.subTest(args=args), self.assertRaises((ValueError, PermissionError)):
                self.module.financial_report(*args)
        self.native_reports.assert_not_called()

    def test_disabled_report_does_not_break_existing_overview_figures(self):
        self.native_reports.side_effect = ValueError("Report is disabled")
        self.sources["Sales Invoice"] = [Record(base_net_total="100")]
        result = self.module.overview("JF", "2026-09-01", "2026-09-30")
        self.assertEqual(result["metrics"]["sales"], 100)
        self.assertEqual(result["financial_reports"], [])


if __name__ == "__main__":
    unittest.main()
