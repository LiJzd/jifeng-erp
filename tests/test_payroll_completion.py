"""Focused offline tests for payroll batch completion guards and idempotency."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Field:
    def __init__(self, fieldtype="Data", options=None, permlevel=0, mask=False):
        self.fieldtype, self.options = fieldtype, options
        self.permlevel, self.mask = permlevel, mask


class Meta:
    def __init__(self, fields):
        self.fields = fields

    def get_field(self, name):
        return self.fields.get(name)

    def has_field(self, name):
        return name in self.fields

    def get_permlevel_access(self, **kwargs):
        return [0]


class Doc(Record):
    def __init__(self, **values):
        super().__init__(**values)
        self.permissions = {"read": True, "write": True, "submit": True}

    def has_permission(self, permission):
        return self.permissions.get(permission, True)

    def get(self, key, default=None):
        return super().get(key, default)

    def db_set(self, key, value):
        self[key] = value

    def reload(self):
        return self

    def submit(self):
        if self.get("submit_error"):
            raise RuntimeError("native submit failed")
        self.docstatus = 1


class TestPayrollCompletion(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.ValidationError = ValueError
        self.frappe.session = Record(user="qa@example.test")
        self.frappe.flags = Record()
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.has_permission = Mock(return_value=True)
        self.frappe.get_meta = self.get_meta
        self.frappe.get_doc = self.get_doc
        self.frappe.get_list = self.get_list
        self.frappe.get_all = self.get_all
        self.docs = {}
        self.slip_names = []
        self.visible_names = None
        self.accrual_calls = 0
        self.parent = Doc(name="PR-1", doctype="Payroll Entry", docstatus=1,
                          company="QA Co", start_date="2026-10-01", end_date="2026-10-31",
                          currency="CNY", salary_slips_submitted=0,
                          employees=[Record(employee="HR1"), Record(employee="HR2")])
        self.docs[("Payroll Entry", "PR-1")] = self.parent
        self.company = Doc(name="QA Co", doctype="Company", default_currency="CNY")
        self.docs[("Company", "QA Co")] = self.company
        self.employee("HR1")
        self.employee("HR2")
        self.slip("SS-1", "HR1", "8", 1)
        self.slip("SS-2", "HR2", "12", 0)
        self.journal = None
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: str(value)[:10]
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, **kwargs: list(self.get_meta(doctype).fields)
        modules = {"frappe": self.frappe, "frappe.utils": utils, "frappe.model": model}
        self.modules_patch = patch.dict(sys.modules, modules)
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        path = Path(__file__).parents[1] / "jifeng_erp/payroll_completion.py"
        spec = importlib.util.spec_from_file_location("payroll_completion_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def employee(self, name, company="QA Co"):
        doc = Doc(name=name, doctype="Employee", company=company)
        self.docs[("Employee", name)] = doc

    def slip(self, name, employee, net, status, journal_entry=None):
        doc = Doc(name=name, doctype="Salary Slip", docstatus=status, company="QA Co",
                  employee=employee, currency="CNY", start_date="2026-10-01",
                  end_date="2026-10-31", payroll_entry="PR-1", net_pay=net,
                  journal_entry=journal_entry)
        self.docs[("Salary Slip", name)] = doc
        self.slip_names.append(name)

    def get_meta(self, doctype):
        if doctype == "Payroll Entry":
            return Meta({"company": Field(), "start_date": Field(), "end_date": Field(),
                         "currency": Field(), "salary_slips_submitted": Field(),
                         "employees": Field("Table", "Payroll Employee Detail")})
        if doctype == "Payroll Employee Detail":
            return Meta({"employee": Field()})
        if doctype == "Salary Slip":
            return Meta({key: Field() for key in ("company", "employee", "currency", "start_date",
                         "end_date", "payroll_entry", "net_pay", "journal_entry")})
        if doctype == "Company":
            return Meta({"default_currency": Field()})
        if doctype == "Employee":
            return Meta({"company": Field()})
        if doctype == "Journal Entry":
            return Meta({"company": Field()})
        return Meta({})

    def get_doc(self, doctype, name, **kwargs):
        if doctype == "Journal Entry" and self.journal and self.journal.name == name:
            return self.journal
        doc = self.docs[(doctype, name)]
        if doctype == "Payroll Entry":
            doc.make_accrual_jv_entry = self.make_accrual
        return doc

    def get_list(self, doctype, **kwargs):
        if doctype == "Salary Slip":
            return self.visible_names if self.visible_names is not None else list(self.slip_names)
        return []

    def get_all(self, doctype, **kwargs):
        if doctype == "Salary Slip":
            return list(self.slip_names)
        return []

    def make_accrual(self, slips):
        self.accrual_calls += 1
        self.journal = Doc(name="JE-1", doctype="Journal Entry", docstatus=1, company="QA Co")
        self.docs[("Journal Entry", "JE-1")] = self.journal
        for slip in slips:
            slip.journal_entry = "JE-1"

    def run_endpoint(self):
        return self.module.complete_payroll_accrual("PR-1")

    def test_mixed_slips_complete_through_one_native_accrual(self):
        result = self.run_endpoint()
        self.assertEqual(self.docs[("Salary Slip", "SS-1")].docstatus, 1)
        self.assertEqual(self.docs[("Salary Slip", "SS-2")].docstatus, 1)
        self.assertEqual(self.docs[("Salary Slip", "SS-1")].journal_entry, "JE-1")
        self.assertEqual(self.docs[("Salary Slip", "SS-2")].journal_entry, "JE-1")
        self.assertEqual(result["journal_entry"], "JE-1")
        self.assertEqual(result["salary_slips"], ["SS-1", "SS-2"])
        self.assertFalse(result["already_completed"])
        self.assertEqual(self.accrual_calls, 1)
        self.assertEqual(self.parent.salary_slips_submitted, 1)

    def test_repeat_returns_existing_journal_without_second_accrual(self):
        self.docs[("Salary Slip", "SS-2")].docstatus = 1
        self.journal = Doc(name="JE-1", doctype="Journal Entry", docstatus=1, company="QA Co")
        self.docs[("Salary Slip", "SS-1")].journal_entry = "JE-1"
        self.docs[("Salary Slip", "SS-2")].journal_entry = "JE-1"
        result = self.run_endpoint()
        self.assertTrue(result["already_completed"])
        self.assertEqual(result["journal_entry"], "JE-1")
        self.assertEqual(self.accrual_calls, 0)

    def test_hidden_slip_or_cross_company_employee_is_rejected_before_writes(self):
        self.visible_names = ["SS-1"]
        with self.assertRaises(PermissionError):
            self.run_endpoint()
        self.visible_names = None
        self.docs[("Employee", "HR2")].company = "Other Co"
        with self.assertRaises(ValueError):
            self.run_endpoint()
        self.assertEqual(self.docs[("Salary Slip", "SS-2")].docstatus, 0)
        self.assertEqual(self.accrual_calls, 0)

    def test_submit_failure_restores_via_flag_and_does_not_accrue(self):
        self.docs[("Salary Slip", "SS-2")].submit_error = True
        with self.assertRaisesRegex(RuntimeError, "native submit failed"):
            self.run_endpoint()
        self.assertNotIn("via_payroll_entry", self.frappe.flags)
        self.assertEqual(self.accrual_calls, 0)

    def test_guard_only_blocks_individual_payroll_linked_submit(self):
        linked = Record(payroll_entry="PR-1")
        with self.assertRaisesRegex(ValueError, "请从工资发放提交工资条并生成月度计提"):
            self.module.guard_salary_slip_submit(linked)
        self.frappe.flags.via_payroll_entry = True
        self.module.guard_salary_slip_submit(linked)
        self.frappe.flags.via_payroll_entry = False
        self.module.guard_salary_slip_submit(Record(payroll_entry=None))


if __name__ == "__main__":
    unittest.main()
