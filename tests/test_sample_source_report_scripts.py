"""Regression models for source identities lost by native Script aggregation.

These tests exercise exact selector contracts and adversarial contributing joins.
They do not replace real Frappe roles, SQL, reports, or cross-connection checks.
"""

import copy
import functools
import hashlib
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
from datetime import date
from unittest.mock import Mock, patch
from contextlib import contextmanager


ROOT = pathlib.Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


def freeze(value):
    if isinstance(value, dict):
        return tuple((key, freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    return value


class TestSampleSourceReportScripts(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict = Row
        self.frappe._ = lambda text: text
        self.frappe.PermissionError = PermissionError
        self.frappe.session = Row(user="actor")

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        self.frappe.get_all = Mock(return_value=[])
        self.frappe.get_meta = Mock(return_value=Row(get_options=Mock(return_value="Not Started\nCompleted\nClosed")))
        self.frappe.get_cached_value = Mock(return_value=("2026-01-01", "2026-12-31"))
        self.frappe.db = Row(sql=Mock(return_value=[]), get_value=Mock(return_value="SUP"))
        self.sources = types.ModuleType("jifeng_erp.sample_source_permissions")
        self.sources.PARENTS = {"Work Order", "Sales Order", "Quotation"}
        self.sources._AUTHORIZING = types.SimpleNamespace(get=lambda: frozenset())
        self.sources._key = lambda doc, user: (doc.doctype, doc.name, user)
        self.sources._scope = Mock(return_value=False)
        self.sources._reference = Mock(side_effect=lambda dt, name: Row(doctype=dt, name=name, company="JF"))
        self.sources._check = Mock()
        self.fields = types.ModuleType("jifeng_erp.flow_quantity")
        self.fields.check_quantity_fields = Mock()
        self.orders = types.ModuleType("jifeng_erp.sample_orders")
        self.orders._freeze = freeze
        self.app = types.ModuleType("jifeng_erp")
        self.app.__path__ = [str(ROOT / "jifeng_erp")]
        self.app.sample_source_permissions = self.sources
        self.modules = {
            "frappe": self.frappe, "jifeng_erp": self.app,
            "jifeng_erp.sample_source_permissions": self.sources,
            "jifeng_erp.flow_quantity": self.fields, "jifeng_erp.sample_orders": self.orders,
        }
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.infra = self.load("jifeng_erp.sample_source_reports", "jifeng_erp/sample_source_reports.py")
        self.subject = self.load("jifeng_erp.sample_source_report_scripts",
                                 "jifeng_erp/sample_source_report_scripts.py")
        self.dates = Row(today=Mock(return_value="2026-10-04"),
                         date_diff=lambda end, start: (date.fromisoformat(end) - date.fromisoformat(start or end)).days,
                         getdate=date.fromisoformat,
                         flt=lambda value: float(value or 0),
                         get_timespan_date_range=Mock(return_value=("2026-01-01", "2026-12-31")))
        self.erp = Row(get_default_company=Mock(return_value="DEFAULT"),
                       get_company_currency=Mock(return_value="CNY"))
        self.so = Row(validate_filters=Mock(), get_conditions=Mock(return_value=" and so.company = %(company)s"))
        self.periods = Row(get_period_date_ranges=Mock(return_value=[("2026-01-01", "2026-12-31")]),
                           get_period=Mock(return_value="2026"), get_period_columns=Mock())
        self.fiscal = Row(get_fiscal_year=Mock(return_value=("FY", "2026-01-01", "2026-12-31")))
        self.periods.get_fiscal_year = self.fiscal.get_fiscal_year
        self.so.update(date_diff=self.dates.date_diff, flt=self.dates.flt, erpnext=self.erp)
        self.trends = Row(get_columns=Mock(side_effect=self.trend_columns), get_data=Mock(),
                          get_fiscal_year=self.fiscal.get_fiscal_year,
                          today=self.dates.today, getdate=self.dates.getdate, erpnext=self.erp)
        self.real_module_check = self.subject._native_module
        self.subject._native_module = Mock(side_effect=self.native_module)

    def load(self, name, path):
        spec = importlib.util.spec_from_file_location(name, ROOT / path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        self.addCleanup(lambda: sys.modules.pop(name, None))
        return module

    def native_module(self, name, digest, methods=()):
        if name == self.subject._DATA:
            return self.dates
        if name == "erpnext":
            return self.erp
        if name == self.subject._SO_ANALYSIS_MODULE:
            return self.so
        if name == self.subject._TRENDS:
            return self.trends
        if name == self.subject._STOCK_ANALYTICS:
            return self.periods
        if name == self.subject._ACCOUNTS_UTILS:
            return self.fiscal
        if name == self.subject._WO_SUMMARY_MODULE:
            return Row(today=self.dates.today, date_diff=self.dates.date_diff,
                       getdate=self.dates.getdate, flt=self.dates.flt,
                       get_period=self.periods.get_period,
                       get_period_date_ranges=self.periods.get_period_date_ranges)
        if name == self.subject._PRODUCTION_MODULE:
            return Row(today=self.dates.today, getdate=self.dates.getdate,
                       get_period=self.periods.get_period,
                       get_period_columns=self.periods.get_period_columns,
                       get_period_date_ranges=self.periods.get_period_date_ranges)
        if name == self.subject._LOST_MODULE:
            return Row(get_timespan_date_range=self.dates.get_timespan_date_range)
        if name in (self.subject._QUOTE_TRENDS_MODULE, self.subject._SO_TRENDS_MODULE):
            return Row(get_columns=self.trends.get_columns, get_data=self.trends.get_data)
        return Row()

    def trend_columns(self, filters, trans):
        filters.setdefault("fiscal_year", "FY")
        filters.setdefault("company", "JF")
        fields = {"Item": "t2.item_code, t2.item_name,", "Item Group": "t2.item_group,",
                  "Customer": "t1.party_name," if trans == "Quotation" else "t1.customer,",
                  "Project": "t1.project,", "Supplier": "t1.supplier,",
                  "Supplier Group": "t3.supplier_group,"}
        supplier = filters.get("based_on") in ("Supplier", "Supplier Group")
        return Row(trans=trans, based_on_select=fields.get(filters.get("based_on"), "t1.territory,")
                   + "t4.default_currency as currency,",
                   addl_tables=(", `tabSupplier` t3" if supplier else "") + ", `tabCompany` t4",
                   addl_tables_relational_cond=(" and t1.supplier=t3.name" if supplier else "")
                   + " and t1.company=t4.name", company_currency="CNY")

    def sql(self, call=0):
        return " ".join(self.frappe.db.sql.call_args_list[call].args[0].split())

    def test_exact_six_registry_and_fixed_modules(self):
        specs = self.subject.get_script_specs()
        self.assertEqual(set(specs), {"Work Order Summary", "Production Analytics", "Sales Order Analysis",
                                      "Lost Quotations", "Quotation Trends", "Sales Order Trends"})
        for spec in specs.values():
            self.assertEqual(spec["kind"], "Script Report")
            self.assertEqual(spec["is_standard"], "Yes")
            self.assertFalse(spec["execute_module"].endswith(".execute"))
            self.assertEqual(len(spec["module_sha256"]), 64)
        self.assertEqual(specs["Quotation Trends"]["dependency_module_sha256"][self.subject._TRENDS],
                         "209d8405570ea2060f9bc2b87bdffd9fd91f9c483ffd8d4e49b592b23e50612a")

    def test_summary_native_company_omission_and_age_boundary(self):
        self.frappe.get_all.return_value = [
            Row(name="A", status="Completed", planned_start_date="2026-01-01"),
            Row(name="B", status="In Process", actual_start_date="2026-10-02"),
            Row(name="C", status="In Process", planned_start_date="2026-10-03"),
        ]
        original = {"from_date": "2026-01-01", "to_date": "2026-10-04", "age": 2}
        saved = copy.deepcopy(original)
        scope = self.subject.work_order_summary_scope(original)
        self.assertEqual(scope.parents, [("Work Order", "B")])
        self.assertEqual(scope.rows[0]["age"], 2)
        self.assertEqual(original, saved)
        filters = self.frappe.get_all.call_args.kwargs["filters"]
        self.assertNotIn("company", filters)
        self.assertEqual(filters["creation"], ("between", ["2026-01-01", "2026-10-04"]))
        self.assertEqual(filters["docstatus"], ("<", 2))

    def test_summary_completed_age_zero_retained_with_default_zero(self):
        self.frappe.get_all.return_value = [Row(name="A", status="Completed")]
        scope = self.subject.work_order_summary_scope({"age": "", "from_date": "S", "to_date": "E"})
        self.assertEqual(scope.rows[0]["age"], 0)
        self.assertEqual(scope.parents, [("Work Order", "A")])

    def test_summary_quantity_forces_monthly_without_fiscal_authorization(self):
        self.frappe.get_all.return_value = [Row(name="A", status="Completed")]
        filters = {"charts_based_on": "Quantity", "range": "Yearly", "from_date": "S", "to_date": "E"}
        scope = self.subject.work_order_summary_scope(filters)
        self.assertEqual(filters["range"], "Yearly")
        self.assertEqual(self.periods.get_period_date_ranges.call_args.args[0]["range"], "Monthly")
        self.assertIn("summary_quantity_periods", scope.rows[-1])
        self.assertEqual(scope.dependencies, [])
        self.fiscal.get_fiscal_year.assert_not_called()

    def test_summary_status_observes_meta_options_without_doctype_permissions(self):
        self.frappe.get_all.return_value = [Row(name="A", status="Completed")]
        scope = self.subject.work_order_summary_scope({"charts_based_on": "Status"})
        self.assertEqual(scope.rows[-1], {"summary_status_options": "Not Started\nCompleted\nClosed"})
        self.assertEqual(scope.dependencies, [])
        self.periods.get_period_date_ranges.assert_not_called()

    def test_summary_actual_and_planned_ranges_are_not_creation_guess(self):
        for based, prefix in (("Actual Date", "actual"), ("Planned Date", "planned")):
            self.subject.work_order_summary_scope({"based_on": based, "from_date": "S", "to_date": "E",
                                                   "sales_order": ["SO1", "SO2"], "production_item": ["I"],
                                                   "status": "Closed", "company": "JF"})
            filters = self.frappe.get_all.call_args.kwargs["filters"]
            self.assertNotIn("creation", filters)
            self.assertEqual(filters[f"{prefix}_start_date"], (">=", "S"))
            self.assertEqual(filters[f"{prefix}_end_date"], ("<=", "E"))
            self.assertEqual(filters["sales_order"], ("in", ["SO1", "SO2"]))

    def test_production_retains_raw_identity_and_date_or(self):
        self.frappe.db.sql.return_value = [Row(name="A", status="Completed"), Row(name="B", status="Stopped")]
        scope = self.subject.production_analytics_scope({"company": "JF", "from_date": "S", "to_date": "E",
                                                         "range": "Monthly", "status": "Completed"})
        self.assertEqual(scope.parents, [("Work Order", "A"), ("Work Order", "B")])
        sql = self.sql()
        self.assertIn("OR wo.actual_end_date BETWEEN", sql)
        self.assertNotIn("wo.status =", sql)
        self.assertIn("SELECT wo.name", sql)
        self.assertEqual(scope.rows[-1]["production_periods"]["today"], "2026-10-04")

    def test_production_yearly_records_actual_fiscal_sources(self):
        self.frappe.db.sql.return_value = [Row(name="A")]
        scope = self.subject.production_analytics_scope({"company": "JF", "from_date": "2026-01-01",
                                                         "to_date": "2026-12-31", "range": "Yearly"})
        fiscal = [dep for dep in scope.dependencies if dep.doctype == "Fiscal Year"]
        self.assertEqual(len(fiscal), 2)
        self.assertTrue(all(dep.owner == ("Work Order", "A") for dep in fiscal))
        self.assertEqual(fiscal[0].bindings["year_start_date"], "2026-01-01")

    def test_so_no_filters_is_native_empty_without_default_company(self):
        scope = self.subject.sales_order_analysis_scope(None)
        self.assertEqual(scope.parents, [])
        self.frappe.db.sql.assert_not_called()
        self.erp.get_default_company.assert_not_called()

    def test_so_native_defaults_validate_and_caller_filters_preserved(self):
        filters = {"from_date": "S", "to_date": "E"}
        self.subject.sales_order_analysis_scope(filters)
        self.assertEqual(filters, {"from_date": "S", "to_date": "E"})
        self.assertEqual(self.so.validate_filters.call_args.args[0]["company"], "DEFAULT")
        self.assertIn(" and so.company = %(company)s", self.frappe.db.sql.call_args.args[0])
        self.assertNotIn("GROUP BY", self.sql())

    def test_so_item_group_keeps_all_orders_and_invoice_join_members(self):
        raw = [Row(sales_order="SO1", sales_order_item="L1", sales_invoice_item="SI1L", sales_invoice="SI1"),
               Row(sales_order="SO1", sales_order_item="L1", sales_invoice_item="SI2L", sales_invoice="SI2"),
               Row(sales_order="SO2", sales_order_item="L2", sales_invoice_item=None)]
        self.frappe.db.sql.return_value = raw
        scope = self.subject.sales_order_analysis_scope({"company": "JF", "group_by_item": 1})
        self.assertEqual(scope.parents, [("Sales Order", "SO1"), ("Sales Order", "SO2")])
        self.assertEqual(len(scope.rows), 3)
        self.assertEqual(self.frappe.db.sql.call_count, 1)
        invoices = [dep.name for dep in scope.dependencies if dep.doctype == "Sales Invoice"]
        self.assertEqual(invoices, ["SI1", "SI2"])
        self.assertTrue(all("items" in dep.fields for dep in scope.dependencies if dep.doctype == "Sales Invoice"))

    def test_so_elapsed_reads_items_outside_first_warehouse(self):
        self.so.get_conditions.return_value = " and so.company=%(company)s and soi.warehouse=%(warehouse)s"
        self.frappe.db.sql.side_effect = [
            [Row(sales_order="SO", sales_order_item="IN-WH", sales_invoice_item=None)],
            [Row(sales_order="SO", elapsed_so_item="OTHER-WH", delivery_note_item="DN-L", delivery_note="DN")],
        ]
        scope = self.subject.sales_order_analysis_scope({"company": "JF", "warehouse": "W1"})
        self.assertIn("soi.warehouse", self.sql(0))
        self.assertNotIn("warehouse", self.sql(1))
        self.assertNotIn("dni.docstatus", self.sql(1))
        self.assertEqual(self.frappe.db.sql.call_args_list[1].args[1]["sales_orders"], ["SO"])
        dependencies = {(dep.doctype, dep.name) for dep in scope.dependencies}
        self.assertIn(("Sales Order Item", "OTHER-WH"), dependencies)
        self.assertIn(("Delivery Note", "DN"), dependencies)
        self.assertIn(("Delivery Note Item", "DN-L"), dependencies)
        self.assertEqual(scope.rows[-1]["elapsed_source"]["elapsed_so_item"], "OTHER-WH")

    def test_so_invoice_child_identity_cannot_disappear_via_sql_where(self):
        self.frappe.db.sql.return_value = [Row(sales_order="SO", sales_order_item="L", sales_invoice_item=None)]
        scope = self.subject.sales_order_analysis_scope({"company": "JF", "group_by_item": 1})
        self.assertNotIn("parenttype =", self.sql())
        dep = next(dep for dep in scope.dependencies if dep.kind == "rows")
        self.assertEqual(dep.bound_child, ("Sales Invoice", None, "items"))
        self.assertEqual(dep.filters, {"so_detail": "L", "docstatus": 1})
        self.assertEqual(dep.bindings, {"so_detail": "L", "docstatus": 1})

    def test_so_company_currency_reference_declared_without_inventing_native_call(self):
        self.frappe.db.sql.return_value = [Row(sales_order="SO", sales_order_item="L", sales_invoice_item=None)]
        scope = self.subject.sales_order_analysis_scope({"company": "JF", "group_by_item": 1})
        currency = next(dep for dep in scope.dependencies if dep.doctype == "Company")
        self.assertEqual((currency.owner, currency.name), (("Sales Order", "SO"), "JF"))
        self.assertIn("default_currency", currency.fields)
        self.erp.get_company_currency.assert_not_called()

    def test_native_invalid_filters_keep_error_before_raw_scope(self):
        error = ValueError("To Date cannot be before From Date.")
        self.so.validate_filters.side_effect = error
        spec = self.subject.get_script_specs()["Sales Order Analysis"]
        with self.assertRaises(ValueError) as caught:
            spec["validate_filters"]({"company": "JF", "from_date": "E", "to_date": "S"})
        self.assertIs(caught.exception, error)
        self.frappe.db.sql.assert_not_called()

    def test_lost_denominator_keeps_null_dimension_and_multiplicity(self):
        raw = [Row(quotation="Q1", dimension_name=None), Row(quotation="Q2", dimension_name="R1"),
               Row(quotation="Q2", dimension_name="R2")]
        self.frappe.db.sql.return_value = raw
        scope = self.subject.lost_quotations_scope({"timespan": "This Year", "company": "JF", "group_by": "Lost Reason"})
        self.assertEqual(scope.parents, [("Quotation", "Q1"), ("Quotation", "Q2")])
        self.assertEqual(len(scope.rows), 3)
        self.assertIn("LEFT JOIN `tabQuotation Lost Reason Detail`", self.sql())
        self.assertNotIn("GROUP BY", self.sql())
        self.assertNotIn("dimension_name IS NOT NULL", self.sql())
        self.assertIn("base_net_total", scope.fields["Quotation"])

    def test_lost_uses_native_timespan_and_competitor_branch(self):
        self.subject.lost_quotations_scope({"timespan": "Last 6 months", "company": "JF", "group_by": "Competitor",
                                            "from_date": "IGNORED"})
        self.dates.get_timespan_date_range.assert_called_once_with("last 6 months")
        self.assertIn("LEFT JOIN `tabCompetitor Detail`", self.sql())
        self.assertEqual(self.frappe.db.sql.call_args.args[1]["from_date"], "2026-01-01")

    def test_lost_invalid_group_does_not_guess_a_dimension(self):
        with self.assertRaisesRegex(ValueError, "Invalid Group By"):
            self.subject.lost_quotations_scope({"timespan": "This Year", "group_by": "Customer"})
        self.frappe.db.sql.assert_not_called()

    def trend_rows(self):
        return [Row(parent_name="A", item_name="L1"), Row(parent_name="B", item_name="L2"),
                Row(parent_name="B", item_name="L2")]

    def test_trends_item_aggregation_retains_all_sources_and_company_join(self):
        self.frappe.db.sql.return_value = self.trend_rows()
        filters = {"based_on": "Item", "period": "Yearly"}
        scope = self.subject.quotation_trends_scope(filters)
        self.assertEqual(filters, {"based_on": "Item", "period": "Yearly"})
        self.assertEqual(scope.parents, [("Quotation", "A"), ("Quotation", "B")])
        self.assertEqual(len(scope.rows), 4)  # three raw rows plus frozen auxiliaries
        self.assertIn("`tabCompany` t4", self.sql())
        self.assertIn("t1.company=t4.name", self.sql())
        self.assertNotIn("GROUP BY", self.sql())
        children = [dep for dep in scope.dependencies if dep.doctype == "Quotation Item"]
        self.assertEqual([dep.name for dep in children], ["L1", "L2", "L2"])
        self.assertTrue(all({"stock_qty", "base_net_amount", "item_name"} <= set(dep.fields) for dep in children))

    def test_trends_cached_fiscal_and_currency_have_fresh_document_bindings(self):
        self.frappe.db.sql.return_value = [Row(parent_name="Q", item_name="L")]
        scope = self.subject.quotation_trends_scope({"based_on": "Item", "period": "Monthly"})
        fy = next(dep for dep in scope.dependencies if dep.doctype == "Fiscal Year")
        company = next(dep for dep in scope.dependencies if dep.doctype == "Company")
        self.assertEqual(fy.name, "FY")
        self.assertEqual(fy.bindings, {"year_start_date": "2026-01-01", "year_end_date": "2026-12-31"})
        self.assertEqual(company.bindings, {"default_currency": "CNY"})

    def test_trends_quotation_customer_condition_is_only_group_customer(self):
        self.subject.quotation_trends_scope({"based_on": "Customer", "group_by": "Item", "period": "Monthly"})
        self.assertNotIn("quotation_to", self.sql())
        self.subject.quotation_trends_scope({"based_on": "Item", "group_by": "Customer", "period": "Monthly"})
        self.assertIn("t1.quotation_to = 'Customer'", self.sql(1))

    def test_trends_closed_and_native_project_null_semantics(self):
        self.subject.sales_order_trends_scope({"based_on": "Project", "period": "Monthly"})
        self.assertIn("t1.status != 'Closed'", self.sql())
        self.assertNotIn("IS Not NULL", self.sql())
        self.subject.sales_order_trends_scope({"based_on": "Project", "period": "Monthly", "include_closed_orders": 1})
        self.assertNotIn("status !=", self.sql(1))

    def test_trends_supplier_join_preserved_for_native_server_branch(self):
        self.frappe.db.sql.return_value = [Row(parent_name="SO", item_name="L")]
        scope = self.subject.sales_order_trends_scope({"based_on": "Supplier Group", "period": "Yearly"})
        self.assertIn("`tabSupplier` t3", self.sql())
        self.assertIn("t1.supplier=t3.name", self.sql())
        supplier = next(dep for dep in scope.dependencies if dep.doctype == "Supplier")
        self.assertEqual((supplier.name, supplier.owner), ("SUP", ("Sales Order", "SO")))

    def test_ordinary_sources_do_not_gain_auxiliary_read_or_field_denials(self):
        self.frappe.db.sql.return_value = [Row(parent_name="ORD", item_name="L")]
        spec = self.subject.get_script_specs()["Quotation Trends"]
        self.fields.check_quantity_fields.side_effect = PermissionError("private amount")
        self.infra._capture(spec, {"based_on": "Item", "period": "Yearly"}, "actor")
        self.sources._check.assert_not_called()
        self.fields.check_quantity_fields.assert_not_called()
        self.assertEqual(self.sources._reference.call_args_list, [unittest.mock.call("Quotation", "ORD")])

    def test_alien_child_is_rejected_in_complete_dependency_gate(self):
        owner = Row(doctype="Sales Order", name="SO", company="JF")
        child = Row(doctype="Sales Order Item", name="L", parent="SO", parenttype="Quotation", parentfield="items")
        self.sources._reference.return_value = child
        dep = self.infra.ReportDependency(owner=("Sales Order", "SO"), doctype="Sales Order Item", name="L",
                                         fields=("name", "qty"), parenttype="Sales Order",
                                         bound_child=("Sales Order", "SO", "items"))
        with self.assertRaises(PermissionError):
            self.infra._dependency(dep, owner, "actor")

    def test_native_module_rejects_content_or_callable_origin_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "audited_native.py"
            source = "def execute(filters):\n    return filters\n"
            path.write_bytes(source.encode("utf-8"))
            spec = importlib.util.spec_from_file_location("audited_native", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            digest = hashlib.sha256(source.encode()).hexdigest()
            with patch.dict(sys.modules, {"audited_native": module}):
                self.assertIs(self.real_module_check("audited_native", digest, ("execute",)), module)
                module.execute = lambda filters: filters
                with self.assertRaises(PermissionError):
                    self.real_module_check("audited_native", digest, ("execute",))
                with self.assertRaises(PermissionError):
                    self.real_module_check("audited_native", "0" * 64)

    def test_loaded_native_helper_alias_change_rejected_before_scope_query(self):
        original = self.subject._native_module.side_effect

        def substituted(name, digest, methods=()):
            module = original(name, digest, methods)
            if name == self.subject._LOST_MODULE:
                module.get_timespan_date_range = lambda timespan: ("OLD", "OTHER")
            return module

        self.subject._native_module.side_effect = substituted
        with self.assertRaises(PermissionError):
            self.subject.lost_quotations_scope({"timespan": "This Year", "group_by": "Lost Reason"})
        self.frappe.db.sql.assert_not_called()

    @contextmanager
    def fiscal_wrapper_fixture(self):
        """A real @wraps function/closure, without importing Frappe or a DB."""
        with tempfile.TemporaryDirectory() as folder:
            base = pathlib.Path(folder)
            framework_path = base / "__init__.py"
            typing_path = base / "typing_validations.py"
            fiscal_path = base / "accounts_utils.py"
            framework_source = (
                "whitelisted = set()\n"
                "def _in_request_or_test():\n    return False\n"
                "def whitelist():\n"
                "    def inner(fn):\n"
                "        from frappe.utils.typing_validations import validate_argument_types\n"
                "        fn = validate_argument_types(fn, apply_condition=_in_request_or_test)\n"
                "        whitelisted.add(fn)\n        return fn\n"
                "    return inner\n"
            )
            typing_source = (
                "from functools import wraps\n"
                "def validate_argument_types(func, apply_condition=None):\n"
                "    @wraps(func)\n"
                "    def wrapper(*args, **kwargs):\n"
                "        if apply_condition is None or apply_condition():\n"
                "            args, kwargs = transform_parameter_types(func, args, kwargs)\n"
                "        return func(*args, **kwargs)\n"
                "    return wrapper\n"
                "def transform_parameter_types(func, args, kwargs):\n    return args, kwargs\n"
            )
            fiscal_source = (
                "import frappe\n"
                "@frappe.whitelist()\n"
                "def get_fiscal_year(date=None, fiscal_year=None, label='Date', verbose=1, company=None,\n"
                "                    as_dict=False, boolean=None, raise_on_missing=True, truncate=False):\n"
                "    return date, fiscal_year\n"
            )
            for path, source in ((framework_path, framework_source), (typing_path, typing_source),
                                 (fiscal_path, fiscal_source)):
                path.write_bytes(source.encode())
            framework = types.ModuleType("frappe")
            framework.__file__ = str(framework_path)
            framework.__path__ = [folder]
            utils = types.ModuleType("frappe.utils")
            utils.__path__ = [folder]
            typing = types.ModuleType(self.subject._TYPING)
            typing.__file__ = str(typing_path)
            fiscal = types.ModuleType(self.subject._ACCOUNTS_UTILS)
            fiscal.__file__ = str(fiscal_path)
            with patch.dict(sys.modules, {"frappe": framework, "frappe.utils": utils,
                                          self.subject._TYPING: typing, self.subject._ACCOUNTS_UTILS: fiscal}), \
                 patch.object(self.subject, "_native_module", self.real_module_check), \
                 patch.object(self.subject, "_FRAPPE_SHA", hashlib.sha256(framework_source.encode()).hexdigest()), \
                 patch.object(self.subject, "_TYPING_SHA", hashlib.sha256(typing_source.encode()).hexdigest()):
                exec(compile(framework_source, str(framework_path), "exec"), vars(framework))
                exec(compile(typing_source, str(typing_path), "exec"), vars(typing))
                exec(compile(fiscal_source, str(fiscal_path), "exec"), vars(fiscal))
                digest = hashlib.sha256(fiscal_source.encode()).hexdigest()
                yield framework, typing, fiscal, fiscal_path, digest

    def test_standard_whitelist_wraps_passes_but_other_methods_stay_plain(self):
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            wrapped = fiscal.get_fiscal_year
            self.assertNotEqual(path.resolve(), pathlib.Path(wrapped.__code__.co_filename).resolve())
            self.assertEqual(wrapped.__code__.co_freevars, ("apply_condition", "func"))
            self.assertIs(self.real_module_check(self.subject._ACCOUNTS_UTILS, digest,
                                                  ("get_fiscal_year",)), fiscal)
            self.assertEqual(wrapped("2026-10-04"), ("2026-10-04", None))
            self.assertIs(wrapped.__globals__, vars(typing))
            self.assertIn(wrapped, framework.whitelisted)

    def test_whitelist_without_source_wrapper_is_rejected(self):
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            fiscal.get_fiscal_year = fiscal.get_fiscal_year.__wrapped__
            framework.whitelisted.add(fiscal.get_fiscal_year)
            with self.assertRaises(PermissionError):
                self.real_module_check(self.subject._ACCOUNTS_UTILS, digest, ("get_fiscal_year",))

    def test_whitelist_wrong_condition_closure_is_rejected(self):
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            fiscal.get_fiscal_year = typing.validate_argument_types(fiscal.get_fiscal_year.__wrapped__,
                                                                    apply_condition=lambda: False)
            framework.whitelisted.add(fiscal.get_fiscal_year)
            with self.assertRaises(PermissionError):
                self.real_module_check(self.subject._ACCOUNTS_UTILS, digest, ("get_fiscal_year",))

    def test_whitelist_wrapped_attribute_must_be_the_actual_closure_function(self):
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            raw = fiscal.get_fiscal_year.__wrapped__
            copied = types.FunctionType(raw.__code__, raw.__globals__, raw.__name__, raw.__defaults__)
            fiscal.get_fiscal_year.__wrapped__ = copied
            with self.assertRaises(PermissionError):
                self.real_module_check(self.subject._ACCOUNTS_UTILS, digest, ("get_fiscal_year",))

    def test_whitelist_extra_standard_wrapper_is_rejected(self):
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            fiscal.get_fiscal_year = typing.validate_argument_types(fiscal.get_fiscal_year,
                                                                    apply_condition=framework._in_request_or_test)
            framework.whitelisted.add(fiscal.get_fiscal_year)
            with self.assertRaises(PermissionError):
                self.real_module_check(self.subject._ACCOUNTS_UTILS, digest, ("get_fiscal_year",))

    def test_whitelist_forged_globals_or_extra_wraps_are_rejected(self):
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            wrapped = fiscal.get_fiscal_year
            fake = types.FunctionType(wrapped.__code__, dict(wrapped.__globals__), wrapped.__name__,
                                      wrapped.__defaults__, wrapped.__closure__)
            functools.update_wrapper(fake, wrapped.__wrapped__)
            fiscal.get_fiscal_year = fake
            framework.whitelisted.add(fake)
            with self.assertRaises(PermissionError):
                self.real_module_check(self.subject._ACCOUNTS_UTILS, digest, ("get_fiscal_year",))

    def test_whitelist_changed_original_or_decorator_source_is_rejected(self):
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            path.write_bytes(path.read_bytes() + b"\n# changed source\n")
            with self.assertRaises(PermissionError):
                self.real_module_check(self.subject._ACCOUNTS_UTILS, digest, ("get_fiscal_year",))
        with self.fiscal_wrapper_fixture() as (framework, typing, fiscal, path, digest):
            pathlib.Path(typing.__file__).write_bytes(b"def validate_argument_types(): pass\n")
            with self.assertRaises(PermissionError):
                self.real_module_check(self.subject._ACCOUNTS_UTILS, digest, ("get_fiscal_year",))


if __name__ == "__main__":
    unittest.main()
