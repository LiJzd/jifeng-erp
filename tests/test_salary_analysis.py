"""Exact saved-amount aggregation for salary-analysis charts."""

from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class TestSalaryAnalysis(unittest.TestCase):
	def setUp(self):
		self.frappe = types.ModuleType("frappe")
		self.frappe.PermissionError, self.frappe.DoesNotExistError = PermissionError, KeyError
		self.frappe.session = types.SimpleNamespace(user="payroll@example.invalid")
		self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
		self.frappe.whitelist = lambda **options: lambda fn: fn
		self.H = types.ModuleType("jifeng_erp.salary_history")
		self.H.WAGE_FIELDS = {"name", "modified", "docstatus", "company", "employee", "salary_component",
			"payroll_date", "piece_amount", "time_amount", "extra_amount", "total_amount"}
		self.H.AS_FIELDS = {"name", "modified", "docstatus", "company", "employee", "currency",
			"salary_component", "payroll_date", "amount", "ref_doctype", "ref_docname", "is_recurring",
			"overwrite_salary_structure_amount"}
		self.H.CONTEXT_FIELDS = {"company", "from_date", "to_date", "use_date", "date_basis", "search"}
		self.H._money = lambda value: format(Decimal(value), "f")
		self.H._Unclassified = type("Unclassified", (Exception,), {})
		self.H._source = lambda *args: (_ for _ in ()).throw(self.H._Unclassified())
		self.H._name = lambda value: value if isinstance(value, str) and value else (_ for _ in ()).throw(ValueError())
		self.H._context = lambda value: value
		self.H._permission_state = lambda: "same"
		self.H._stable_snapshot = lambda context: {"rows": [], "records": {}}
		self.H._Capture = lambda: types.SimpleNamespace(records={})
		self.H._meta_contract = lambda *args: None
		self.H._compare_snapshot = None
		package = types.ModuleType("jifeng_erp")
		package.__path__ = []
		package.salary_history = self.H
		self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "jifeng_erp": package,
			"jifeng_erp.salary_history": self.H, "jifeng_erp.archive_versions": types.SimpleNamespace(same_version=lambda left, right: left == right)})
		self.modules.start()
		self.addCleanup(self.modules.stop)
		spec = importlib.util.spec_from_file_location("salary_analysis_under_test", ROOT / "jifeng_erp/salary_analysis.py")
		self.A = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(self.A)

	def test_saved_department_and_totals_keep_exact_decimals_and_group_unclassified(self):
		snapshot = {"records": {}, "rows": [{"row": {"name": "SAL-1", "version": "v1", "employee": "EMP-1",
			"employee_name": "员工一", "department": "装配", "gross_pay": "1030.10", "total_deduction": "0.10",
			"net_pay": "1030.00", "rounded_total": "1030", "start_date": "2026-10-01", "end_date": "2026-10-31",
			"company": "QA", "currency": "CNY"}, "children": {"earnings": [{"amount": "1030.10",
			"additional_salary": "", "do_not_include_in_total": 0}], "deductions": []}}]}
		result = self.A._build(snapshot, "", "CNY")
		self.assertEqual(result["settlements"], {"gross_pay": "1030.10", "total_deduction": "0.10",
			"net_pay": "1030.00", "rounded_total": "1030"})
		self.assertEqual(result["department"], [{"name": "装配", "amount": "1030.10", "share_percent": "100.00"}])
		self.assertEqual(result["group"], [{"name": "未归类", "amount": "1030.10", "share_percent": "100.00"}])
		self.assertEqual(result["operation"], [{"name": "未归类", "amount": "1030.10", "share_percent": "100.00"}])

	def test_mixed_salary_detail_splits_only_exact_wage_increment_to_operation(self):
		snapshot = {"records": {}, "rows": [{"row": {"name": "SAL-2", "version": "v2", "employee": "EMP-1",
			"employee_name": "员工一", "department": "装配", "gross_pay": "1030", "total_deduction": "0",
			"net_pay": "1030", "rounded_total": "1030", "start_date": "2026-10-01", "end_date": "2026-10-31",
			"company": "QA", "currency": "CNY"}, "children": {"earnings": [{"amount": "1030",
			"additional_salary": "AS-1", "do_not_include_in_total": 0}], "deductions": []}}]}
		wage = {"name": "WAGE-1", "modified": "v3", "operation": "装配", "total_amount": "30"}
		with patch.object(self.A, "_operation", return_value=(wage, Decimal("30"), Decimal("1000"))):
			result = self.A._build(snapshot, "", "CNY")
		self.assertEqual(result["operation"], [
			{"name": "未归类", "amount": "1000", "share_percent": "97.09"},
			{"name": "装配", "amount": "30", "share_percent": "2.91"}])
		self.assertEqual(result["sources"][0]["wage_entry"], "WAGE-1")

	def test_independent_additional_salary_row_uses_salary_history_source_validation(self):
		wage = {"name": "WAGE-2", "modified": "v7", "docstatus": 1, "company": "QA", "employee": "EMP-1",
			"salary_component": "EXTRA", "payroll_date": "2026-10-15", "piece_amount": "0", "time_amount": "30",
			"extra_amount": "0", "total_amount": "30", "operation": "装配"}
		class Capture:
			records = {}
			def project(self, doctype, name, fields, **kwargs): return None, wage
		self.H._Capture = Capture
		self.H._source = lambda *args: ({"time": Decimal("30")}, [{"doctype": "Jifeng Wage Entry", "name": "WAGE-2", "version": "v7"}])
		child = {"additional_salary": "AS-2", "amount": "30", "default_amount": "0", "additional_amount": "30"}
		result = self.A._operation(Capture(), {"records": {}}, {}, child)
		self.assertEqual(result[1:], (Decimal("30"), Decimal(0)))

	def test_operation_link_requires_exact_additional_increment_and_reconciled_components(self):
		additional = {"name": "AS-1", "modified": "v2", "docstatus": 1, "company": "QA", "employee": "EMP-1",
			"currency": "CNY", "salary_component": "BASIC", "payroll_date": "2026-10-15", "amount": "30",
			"ref_doctype": "Jifeng Wage Entry", "ref_docname": "WAGE-1", "is_recurring": 0,
			"overwrite_salary_structure_amount": 0}
		wage = {"name": "WAGE-1", "modified": "v3", "docstatus": 1, "company": "QA", "employee": "EMP-1",
			"salary_component": "BASIC", "payroll_date": "2026-10-15", "piece_amount": "0", "time_amount": "30",
			"extra_amount": "0", "total_amount": "30", "operation": "装配"}
		company = {"name": "QA", "default_currency": "CNY"}
		class Capture:
			records = {}
			def project(self, doctype, name, fields, **kwargs):
				return None, {"Additional Salary": additional, "Jifeng Wage Entry": wage, "Company": company}[doctype]
		self.H._Capture = Capture
		capture = Capture()
		slip = {"company": "QA", "employee": "EMP-1", "currency": "CNY", "start_date": "2026-10-01", "end_date": "2026-10-31"}
		child = {"additional_salary": "AS-1", "additional_amount": "30", "default_amount": "1000",
			"amount": "1030", "salary_component": "BASIC"}
		result = self.A._operation(capture, {"records": {}}, slip, child)
		self.assertEqual(result[1:], (Decimal("30"), Decimal("1000")))
		child["amount"] = "1031"
		self.assertIsNone(self.A._operation(Capture(), {"records": {}}, slip, child))

	def test_public_query_is_get_only_and_returns_saved_scope_with_empty_valid_result(self):
		context = {"company": "QA", "from_date": "2026-10-01", "to_date": "2026-10-31",
			"use_date": True, "date_basis": "period_end", "search": "", "currency": ""}
		self.H._stable_snapshot = lambda selected: {"rows": [], "records": {}}
		result = self.A.query_salary_analysis(json.dumps({**context, "employee": ""}))
		self.assertTrue(result["readonly"])
		self.assertEqual(result["scope"], "salary_analysis")
		self.assertEqual(result["context"], {**context, "employee": ""})
		self.assertEqual(result["salary_slips"], 0)
		self.assertEqual(result["department"], [])

	def test_mixed_currency_scope_returns_selection_required_without_cross_currency_totals(self):
		context = {"company": "QA", "from_date": "2026-10-01", "to_date": "2026-10-31",
			"use_date": True, "date_basis": "period_end", "search": "", "employee": "", "currency": ""}
		self.H._stable_snapshot = lambda selected: {"records": {}, "rows": [
			{"row": {"currency": "CNY", "employee": "EMP-1"}}, {"row": {"currency": "USD", "employee": "EMP-2"}}]}
		result = self.A.query_salary_analysis(json.dumps(context))
		self.assertTrue(result["requires_currency"])
		self.assertEqual(result["currencies"], ["CNY", "USD"])
		self.assertNotIn("settlements", result)


if __name__ == "__main__":
	unittest.main()
