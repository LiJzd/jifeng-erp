"""Payroll calculation and source-link checks without a Frappe site."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


class TestPayroll(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value=None))
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "payroll.py"
        with patch.dict(sys.modules, {"frappe": self.frappe}):
            spec = importlib.util.spec_from_file_location("payroll_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def test_piece_time_and_extra_are_rounded_as_money(self):
        self.assertEqual(
            self.module.wage_amounts("2.5", "1.235", "1.25", "20", "0.10"),
            (Decimal("3.09"), Decimal("25.00"), Decimal("28.19")),
        )

    def test_non_finite_and_negative_inputs_are_rejected(self):
        for invalid in ("NaN", "Infinity", "-1", "n/a"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.module.wage_amounts(invalid, 1, 0, 0, 0)

    def test_additional_salary_must_match_submitted_wage(self):
        wage = types.SimpleNamespace(name="W-1", docstatus=1, employee="E-1",
                                     company="C-1", salary_component="Piece",
                                     payroll_date="2026-09-30", total_amount=Decimal("28.19"))
        self.frappe.get_doc = Mock(return_value=wage)
        salary = types.SimpleNamespace(name="A-1", ref_doctype="Jifeng Wage Entry",
                                       ref_docname="W-1", employee="E-1", company="C-1",
                                       salary_component="Piece", payroll_date="2026-09-30",
                                       amount=28.19, is_recurring=0,
                                       overwrite_salary_structure_amount=0)
        self.module.validate_wage_additional_salary(salary)
        salary.amount = 30
        with self.assertRaisesRegex(ValueError, "金额一致"):
            self.module.validate_wage_additional_salary(salary)

    def test_duplicate_active_salary_is_rejected(self):
        wage = types.SimpleNamespace(name="W-1", docstatus=1, employee="E-1",
                                     company="C-1", salary_component="Piece",
                                     payroll_date="2026-09-30", total_amount=Decimal("10"))
        self.frappe.get_doc = Mock(return_value=wage)
        self.frappe.db.get_value.side_effect = ["W-1", "A-existing"]
        salary = types.SimpleNamespace(name="A-2", ref_doctype="Jifeng Wage Entry",
                                       ref_docname="W-1")
        with self.assertRaisesRegex(ValueError, "重复计薪"):
            self.module.validate_wage_additional_salary(salary)


if __name__ == "__main__":
    unittest.main()
