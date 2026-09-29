"""Exercise expense accounting controls without a live Frappe database."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch


class Record(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value

    def check_permission(self, permission):
        return None

    def db_set(self, field, value, **kwargs):
        self[field] = value


class Journal(Record):
    def __init__(self, accounting, **kwargs):
        super().__init__(**kwargs)
        self.accounting = accounting
        self.flags = types.SimpleNamespace()
        self.name = "JV-1"
        self.docstatus = 0

    def insert(self):
        self.accounting.validate_expense_journal_entry(self)

    def submit(self):
        self.accounting.validate_expense_journal_entry(self)
        self.docstatus = 1

    def cancel(self):
        self.accounting.validate_expense_journal_entry_cancel(self)
        self.docstatus = 2


class TestExpenseVoucher(unittest.TestCase):
    def setUp(self):
        self.accounts = {
            "EXP": Record(company="JF", is_group=0, disabled=0, account_currency="CNY",
                          root_type="Expense", account_type="Expense Account"),
            "BANK": Record(company="JF", is_group=0, disabled=0, account_currency="CNY",
                           root_type="Asset", account_type="Bank"),
            "CASH": Record(company="JF", is_group=0, disabled=0, account_currency="CNY",
                           root_type="Asset", account_type="Cash"),
        }
        self.cost_center = Record(company="JF", is_group=0)
        self.journal = None
        self.existing = False
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe.db = types.SimpleNamespace(
            get_value=lambda doctype, name, field: "CNY" if doctype == "Company" else None,
            exists=lambda doctype, filters: self.existing,
        )

        def get_doc(doctype, name=None):
            if isinstance(doctype, dict):
                self.journal = Journal(self.accounting, **doctype)
                return self.journal
            if doctype == "Account":
                return self.accounts[name]
            if doctype == "Cost Center":
                return self.cost_center
            if doctype == "Journal Entry":
                return self.journal
            raise AssertionError(doctype)

        frappe.get_doc = get_doc
        model = types.ModuleType("frappe.model")
        document = types.ModuleType("frappe.model.document")
        document.Document = object
        modules = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document}
        root = pathlib.Path(__file__).parents[1] / "jifeng_erp"
        with patch.dict(sys.modules, modules):
            accounting_path = root / "accounting.py"
            spec = importlib.util.spec_from_file_location("jifeng_erp.accounting", accounting_path)
            self.accounting = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.accounting)
            with patch.dict(sys.modules, {"jifeng_erp.accounting": self.accounting}):
                voucher_path = root / "jifeng_erp" / "doctype" / "jifeng_expense_voucher" / "jifeng_expense_voucher.py"
                spec = importlib.util.spec_from_file_location("expense_voucher_under_test", voucher_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.voucher_class = module.JifengExpenseVoucher
        self.voucher = Record(
            name="EXP-1", docstatus=0, company="JF", posting_date="2026-09-30",
            store_name="一店", cost_center="CC-1", legacy_number="OLD-1",
            counterparty_name="维修师傅", payment_method="银行转账",
            expense_account="EXP", payment_account="BANK", amount="120.50",
            arrears_reference=0, summary="门店维修", journal_entry=None,
        )

    def test_submission_posts_balanced_journal_and_links_source(self):
        self.voucher_class.validate(self.voucher)
        self.voucher.docstatus = 1
        self.voucher_class.on_submit(self.voucher)
        self.assertEqual(self.journal.docstatus, 1)
        self.assertEqual(self.voucher.journal_entry, "JV-1")
        self.assertEqual(self.journal.jf_expense_voucher, "EXP-1")
        self.assertEqual(self.journal.accounts[0]["debit_in_account_currency"], Decimal("120.50"))
        self.assertEqual(self.journal.accounts[1]["credit_in_account_currency"], Decimal("120.50"))

    def test_cancellation_cancels_linked_journal(self):
        self.voucher.docstatus = 1
        self.voucher_class.on_submit(self.voucher)
        self.voucher_class.on_cancel(self.voucher)
        self.assertEqual(self.journal.docstatus, 2)

    def test_direct_journal_change_or_cancel_is_blocked(self):
        self.voucher.docstatus = 1
        self.voucher_class.on_submit(self.voucher)
        self.journal.flags.from_jifeng_expense = False
        with self.assertRaisesRegex(ValueError, "只能由费用登记"):
            self.accounting.validate_expense_journal_entry(self.journal)
        with self.assertRaisesRegex(ValueError, "请从集沣费用登记"):
            self.accounting.validate_expense_journal_entry_cancel(self.journal)

    def test_wrong_payment_account_and_nonfinite_amount_are_rejected(self):
        self.voucher.payment_account = "CASH"
        with self.assertRaisesRegex(ValueError, "银行转账"):
            self.voucher_class.validate(self.voucher)
        self.voucher.payment_account = "BANK"
        self.voucher.amount = "Infinity"
        with self.assertRaisesRegex(ValueError, "大于零"):
            self.voucher_class.validate(self.voucher)

    def test_existing_journal_prevents_duplicate_posting(self):
        self.voucher.docstatus = 1
        self.existing = True
        with self.assertRaisesRegex(ValueError, "已有总账凭证"):
            self.voucher_class.on_submit(self.voucher)
        self.assertIsNone(self.journal)


if __name__ == "__main__":
    unittest.main()
