"""Payroll calculation and source-link checks without a Frappe site."""

import importlib.util
import datetime
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
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value=None), sql=Mock(return_value=[]))
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
                                     check_permission=Mock(),
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
                                     check_permission=Mock(),
                                     company="C-1", salary_component="Piece",
                                     payroll_date="2026-09-30", total_amount=Decimal("10"))
        self.frappe.get_doc = Mock(return_value=wage)
        self.frappe.db.get_value.return_value = "W-1"
        self.frappe.db.sql.return_value = [("A-existing",)]
        salary = types.SimpleNamespace(name="A-2", ref_doctype="Jifeng Wage Entry",
                                       ref_docname="W-1")
        with self.assertRaisesRegex(ValueError, "重复计薪"):
            self.module.validate_wage_additional_salary(salary)

    def test_submitted_wage_prevents_canceling_job_card(self):
        self.frappe.db.exists = Mock(return_value="JF-WAGE-1")
        with self.assertRaisesRegex(ValueError, "审核计薪记录"):
            self.module.validate_job_card_cancel(types.SimpleNamespace(name="JC-1"))
        self.frappe.db.exists.assert_called_once_with(
            "Jifeng Wage Entry", {"job_card": "JC-1", "docstatus": 1},
        )


class TestWageJobCardLink(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe.whitelist = lambda **kwargs: (lambda fn: fn)
        frappe.db = types.SimpleNamespace(sql=Mock(return_value=[]))
        self.employee = types.SimpleNamespace(company="C-1", status="Active", employee_name="员工甲", check_permission=Mock())
        self.card = types.SimpleNamespace(company="C-1", work_order="WO-1", operation="抛光",
                                          name="JC-1", stock_uom="Nos", total_completed_qty=5,
                                          check_permission=Mock(),
                                          docstatus=1, time_logs=[types.SimpleNamespace(employee="E-1")])
        self.work = types.SimpleNamespace(name="WO-1", company="C-1", jf_flow_order="FLOW-1", check_permission=Mock())
        self.flow = types.SimpleNamespace(company="C-1", check_permission=Mock())
        component = types.SimpleNamespace(type="Earning", disabled=0, check_permission=Mock())
        docs = {"Employee": self.employee, "Job Card": self.card, "Work Order": self.work,
                "Jifeng Flow Order": self.flow, "Salary Component": component}
        frappe.get_doc = lambda doctype, name, **kwargs: docs[doctype]
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        document.Document = type("Document", (), {})
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        payroll = types.ModuleType("jifeng_erp.payroll")
        payroll.active_additional_salary = Mock(return_value=None)
        payroll.wage_amounts = lambda *args: (Decimal("10"), Decimal("0"), Decimal("10"))
        payroll.nonnegative = lambda value, label: Decimal(str(value or 0))
        path = (pathlib.Path(__file__).parents[1] / "jifeng_erp" / "jifeng_erp" /
                "doctype" / "jifeng_wage_entry" / "jifeng_wage_entry.py")
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model,
                                      "frappe.model.document": document, "frappe.utils": utils,
                                      "jifeng_erp.payroll": payroll}):
            allocation_path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "wage_allocation.py"
            allocation_spec = importlib.util.spec_from_file_location("allocation_under_test", allocation_path)
            allocation = importlib.util.module_from_spec(allocation_spec)
            allocation_spec.loader.exec_module(allocation)
            with patch.dict(sys.modules, {"jifeng_erp.wage_allocation": allocation}):
                spec = importlib.util.spec_from_file_location("wage_entry_under_test", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
        self.wage = module.JifengWageEntry()
        self.wage.employee, self.wage.company = "E-1", "C-1"
        self.wage.operation, self.wage.flow_order, self.wage.work_order = "抛光", None, None
        self.wage.job_card, self.wage.salary_component = "JC-1", "Piece"
        self.wage.quantity, self.wage.piece_rate = 5, 2
        self.wage.hours, self.wage.hourly_rate, self.wage.extra_amount = 0, 0, 0
        self.wage.work_date, self.wage.payroll_date = "2026-09-29", "2026-09-30"

    def test_job_card_links_work_order_and_flow_before_approval(self):
        self.wage.validate()
        self.wage.before_submit()
        self.assertEqual(self.wage.work_order, "WO-1")
        self.assertEqual(self.wage.flow_order, "FLOW-1")
        self.assertEqual(self.wage.total_amount, Decimal("10"))

    def test_unsubmitted_or_unrelated_job_card_cannot_support_pay(self):
        self.wage.validate()
        self.card.docstatus = 0
        with self.assertRaisesRegex(ValueError, "已提交的报工单"):
            self.wage.before_submit()
        self.card.docstatus = 1
        self.card.time_logs = [types.SimpleNamespace(employee="E-2")]
        with self.assertRaisesRegex(ValueError, "没有这位员工"):
            self.wage.before_submit()

    def test_mismatched_job_card_operation_is_rejected(self):
        self.wage.operation = "电镀"
        with self.assertRaisesRegex(ValueError, "工序"):
            self.wage.validate()


if __name__ == "__main__":
    unittest.main()
