"""Exercise expense accounting controls without a live Frappe database."""

import importlib.util
import pathlib
import sys
import types
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch


class Record(dict):
    denied = set()
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("no " + permission)

    def has_permission(self, permission):
        return (self.get("doctype"), self.get("name"), permission) not in self.denied

    def db_set(self, field, value, **kwargs):
        self[field] = value


class Journal(Record):
    def __init__(self, accounting, **kwargs):
        super().__init__(**kwargs)
        self.accounting = accounting
        self.flags = types.SimpleNamespace()
        self.name = "JV-1"
        self.docstatus = 0
        self.accounts = [Record(**row) for row in self.accounts]
        self.multi_currency = 0

    def insert(self):
        self.accounting.validate_expense_journal_entry(self)

    def submit(self):
        self.accounting.validate_expense_journal_entry(self)
        self.docstatus = 1

    def cancel(self):
        self.accounting.validate_expense_journal_entry_cancel(self)
        self.docstatus = 2


class QueryColumn:
    def __init__(self, field):
        self.field = field

    def __eq__(self, value):
        return self.field, value


class QueryTable:
    def __init__(self, doctype):
        self.doctype = doctype
        self.star = "*"

    def __getitem__(self, field):
        return QueryColumn(field)


class LockedQuery:
    def __init__(self, table, test):
        self.table, self.test = table, test
        self.fields, self.filters, self.locked = [], {}, False

    def select(self, *fields):
        self.fields = list(fields)
        return self

    def where(self, condition):
        self.filters[condition[0]] = condition[1]
        return self

    def for_update(self):
        self.locked = True
        return self

    def run(self, as_dict):
        self.test.lock_queries.append((self.table.doctype, self.fields, self.filters, self.locked))
        self.test.assertTrue(as_dict and self.locked)
        return self.test.references.get(self.table.doctype, [])


class TestExpenseVoucher(unittest.TestCase):
    def setUp(self):
        Record.denied = set()
        self.accounts = {
            "EXP": Record(company="JF", is_group=0, disabled=0, account_currency="CNY",
                          root_type="Expense", account_type="Expense Account"),
            "BANK": Record(company="JF", is_group=0, disabled=0, account_currency="CNY",
                           root_type="Asset", account_type="Bank"),
            "CASH": Record(company="JF", is_group=0, disabled=0, account_currency="CNY",
                           root_type="Asset", account_type="Cash"),
            "PAY": Record(company="JF", is_group=0, disabled=0, account_currency="CNY",
                          root_type="Liability", account_type="Payable"),
        }
        for name, account in self.accounts.items():
            account.update(doctype="Account", name=name)
        self.company = Record(doctype="Company", name="JF", default_currency="CNY")
        self.cost_center = Record(doctype="Cost Center", name="CC-1", company="JF", is_group=0)
        self.supplier = Record(doctype="Supplier", name="SUP", disabled=0)
        self.journal = None
        self.existing = False
        self.hidden_fields = {}
        self.table_level = 0
        self.create_payment = True
        self.balance = 120.5
        self.references = {}
        self.persisted = None
        self.payment_docstatus = 1
        self.reads = []
        self.lock_queries = []
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.PermissionError = PermissionError
        frappe.whitelist = lambda: (lambda function: function)
        frappe.session = types.SimpleNamespace(user="accounts-manager")
        frappe.has_permission = lambda dt, perm: self.create_payment if (dt, perm) == ("Payment Entry", "create") else True
        frappe._dict = Record
        frappe.get_meta = lambda dt: types.SimpleNamespace(
            get_field=lambda field: types.SimpleNamespace(fieldtype="Table", permlevel=self.table_level),
            get_permlevel_access=lambda **kwargs: [0],
        )
        def get_value(doctype, name, field):
            if doctype == "Company":
                return "CNY"
            if doctype == "Journal Entry" and self.journal and name == self.journal.name:
                return self.journal.get(field)
            if doctype == "Jifeng Expense Voucher" and name == self.voucher.name:
                return (self.persisted or self.voucher).get(field)

        frappe.db = types.SimpleNamespace(get_value=get_value, exists=lambda doctype, filters: self.existing)

        def get_doc(doctype, name=None, **kwargs):
            self.reads.append((doctype if isinstance(doctype, str) else doctype["doctype"], name, kwargs.get("for_update", False)))
            if isinstance(doctype, dict):
                self.journal = Journal(self.accounting, **doctype)
                return self.journal
            if doctype == "Account":
                return self.accounts[name]
            if doctype == "Company":
                return self.company
            if doctype == "Supplier":
                return self.supplier
            if doctype == "Cost Center":
                return self.cost_center
            if doctype == "Journal Entry":
                return self.journal
            if doctype == "Payment Entry":
                return Record(doctype=doctype, name=name, docstatus=self.payment_docstatus)
            if doctype == "Jifeng Expense Voucher":
                return self.persisted or self.voucher
            raise AssertionError(doctype)

        frappe.get_doc = get_doc
        frappe.get_all = lambda dt, **kwargs: self.references.get(dt, [])
        frappe.qb = types.SimpleNamespace(DocType=QueryTable, from_=lambda table: LockedQuery(table, self))
        frappe.get_list = lambda dt, **kwargs: ["JF"] if dt == "Company" else [Record(name=self.voucher.name)]
        self.frappe = frappe
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda dt, **kwargs: self.fields[dt] - self.hidden_fields.get(dt, set())
        document = types.ModuleType("frappe.model.document")
        document.Document = object
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: value
        payment = types.ModuleType("erpnext.accounts.doctype.payment_entry.payment_entry")
        payment.get_reference_details = lambda *args: {"total_amount": 120.5, "outstanding_amount": self.balance}
        self.native_reference_details = payment.get_reference_details
        modules = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document,
                   "frappe.utils": utils, "erpnext.accounts.doctype.payment_entry.payment_entry": payment}
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
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
            self.fields = {
                "Jifeng Expense Voucher": set(self.accounting.SOURCE_FIELDS) | {
                    "store_name", "legacy_number", "counterparty_name", "arrears_reference", "summary"},
                "Account": set(self.accounting.ACCOUNT_FIELDS), "Company": {"name", "default_currency"},
                "Cost Center": {"name", "company", "is_group"}, "Supplier": {"name", "disabled"},
                "Journal Entry": set(self.accounting.JOURNAL_FIELDS),
                "Journal Entry Account": set(self.accounting.JOURNAL_ROW_FIELDS),
            }
            with patch.dict(sys.modules, {"jifeng_erp.accounting": self.accounting}):
                path = root / "jifeng_erp" / "report" / "jifeng_expense_ledger" / "jifeng_expense_ledger.py"
                spec = importlib.util.spec_from_file_location("expense_report_under_test", path)
                self.report = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(self.report)
        self.voucher = Record(
            doctype="Jifeng Expense Voucher", name="EXP-1", docstatus=0, company="JF", posting_date="2026-09-30",
            store_name="一店", cost_center="CC-1", legacy_number="OLD-1",
            counterparty_name="维修师傅", payment_method="银行转账",
            expense_account="EXP", payment_account="BANK", amount="120.50",
            arrears_reference=0, summary="门店维修", journal_entry=None, supplier=None, payable_account=None,
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

    def credit(self):
        self.voucher.update(payment_method="赊账", supplier="SUP", payable_account="PAY", arrears_reference=9999)
        return self.voucher

    def submitted_credit(self):
        self.credit().docstatus = 1
        self.voucher_class.on_submit(self.voucher)
        self.references["Payment Ledger Entry"] = [Record(voucher_type="Journal Entry", voucher_no="JV-1",
            company="JF", account="PAY", party_type="Supplier", party="SUP")]
        return self.voucher

    def test_credit_posts_one_exact_supplier_payable_without_cash_or_old_balance(self):
        self.credit().payment_account = None
        self.submitted_credit()
        self.assertEqual(len(self.journal.accounts), 2)
        self.assertEqual(self.journal.accounts[1]["account"], "PAY")
        self.assertEqual(self.journal.accounts[1]["party_type"], "Supplier")
        self.assertEqual(self.journal.accounts[1]["party"], "SUP")
        self.assertEqual(self.journal.accounts[1]["credit_in_account_currency"], Decimal("120.50"))
        self.assertNotIn("reference_name", self.journal.accounts[1])

    def test_old_methods_keep_cash_bank_rules_and_ignore_unused_credit_fields(self):
        for method, account in (("现金", "CASH"), ("银行转账", "BANK"), ("其他", "CASH"), ("其他", "BANK")):
            self.voucher.update(payment_method=method, payment_account=account, supplier="SUP", payable_account="PAY")
            self.assertEqual(self.accounting.validate_expense_voucher(self.voucher), Decimal("120.50"))

    def test_credit_requires_real_supplier_and_payable_not_original_name(self):
        self.credit().supplier = None
        with self.assertRaisesRegex(ValueError, "明确选择"):
            self.accounting.validate_expense_voucher(self.voucher)
        self.voucher.supplier = "SUP"
        self.voucher.payable_account = "BANK"
        with self.assertRaisesRegex(ValueError, "应付科目"):
            self.accounting.validate_expense_voucher(self.voucher)

    def test_account_company_disabled_group_and_currency_reject_before_posting(self):
        self.credit()
        for changes in ({"company": "OTHER"}, {"disabled": 1}, {"is_group": 1}, {"account_currency": "USD"}):
            original = dict(self.accounts["PAY"])
            self.accounts["PAY"].update(changes)
            with self.assertRaises(ValueError):
                self.accounting.validate_expense_voucher(self.voucher)
            self.assertIsNone(self.journal)
            self.accounts["PAY"].update(original)

    def test_supplier_disabled_or_unreadable_rejects_credit(self):
        self.credit()
        self.supplier.disabled = 1
        with self.assertRaisesRegex(ValueError, "启用的供应商"):
            self.accounting.validate_expense_voucher(self.voucher)
        self.supplier.disabled = 0
        Record.denied.add(("Supplier", "SUP", "read"))
        with self.assertRaises(PermissionError):
            self.accounting.validate_expense_voucher(self.voucher)

    def test_all_reference_select_and_field_permissions_are_required(self):
        self.credit()
        for dt, name in (("Company", "JF"), ("Account", "PAY"), ("Cost Center", "CC-1"), ("Supplier", "SUP")):
            Record.denied.add((dt, name, "select"))
            with self.assertRaises(PermissionError):
                self.accounting.validate_expense_voucher(self.voucher)
            Record.denied.clear()
        for dt, field in (("Company", "default_currency"), ("Account", "company"), ("Cost Center", "is_group"),
                          ("Supplier", "disabled"), ("Jifeng Expense Voucher", "supplier")):
            self.hidden_fields[dt] = {field}
            with self.assertRaises(PermissionError):
                self.accounting.validate_expense_voucher(self.voucher)
            self.hidden_fields.clear()

    def test_boolean_flag_and_copied_object_cannot_authorize_managed_journal(self):
        self.submitted_credit()
        self.journal.flags.from_jifeng_expense = True
        with self.assertRaises(ValueError):
            self.journal.cancel()
        with self.accounting.expense_journal_action(self.journal, self.voucher.name):
            other = Journal(self.accounting, doctype="Journal Entry", jf_expense_voucher=self.voucher.name, accounts=[])
            with self.assertRaises(ValueError):
                other.insert()
        with self.assertRaises(ValueError):
            self.journal.submit()

    def test_context_is_cleared_after_failure_and_link_erasure_remains_guarded(self):
        self.submitted_credit()
        with self.assertRaises(RuntimeError):
            with self.accounting.expense_journal_action(self.journal, self.voucher.name):
                raise RuntimeError("native submit failed")
        self.journal.get_doc_before_save = lambda: Record(jf_expense_voucher="EXP-1")
        self.journal.jf_expense_voucher = None
        with self.assertRaises(ValueError):
            self.accounting.validate_expense_journal_entry(self.journal)
        self.assertIsNone(self.accounting._journal_context.get())

    def test_ordinary_journal_is_not_managed_by_expense_controls(self):
        journal = Journal(self.accounting, doctype="Journal Entry", accounts=[])
        journal.insert()
        journal.submit()
        journal.cancel()
        self.assertEqual(journal.docstatus, 2)

    def test_update_after_submit_guard_denies_even_native_editable_journal_fields(self):
        self.submitted_credit()
        self.journal.title = "客户端改标题"
        with self.assertRaisesRegex(ValueError, "只能由费用登记"):
            self.accounting.validate_expense_journal_entry(self.journal, "before_update_after_submit")

    def settlement(self):
        return Record(doctype="Payment Entry", company="JF", payment_type="Pay", party_type="Supplier",
            party="SUP", paid_to="PAY", references=[Record(reference_doctype="Journal Entry",
            reference_name="JV-1", allocated_amount=40)])

    def test_settlement_locks_source_before_journal_and_does_not_change_native_fields(self):
        self.submitted_credit()
        self.reads.clear()
        payment = self.settlement()
        old = deepcopy(payment)
        self.accounting.lock_expense_settlement_references(payment)
        locks = [(dt, name) for dt, name, locked in self.reads if locked]
        self.assertEqual(locks, [("Jifeng Expense Voucher", "EXP-1"), ("Journal Entry", "JV-1")])
        self.assertEqual(payment, old)

    def test_unmanaged_reference_is_unchanged_even_with_hidden_custom_field(self):
        self.submitted_credit()
        payment = self.settlement()
        payment.references[0].reference_name = "ORDINARY-JE"
        self.hidden_fields["Journal Entry"] = {"jf_expense_voucher"}
        self.reads.clear()
        old = deepcopy(payment)
        self.accounting.lock_expense_settlement_references(payment)
        self.assertEqual(payment, old)
        self.assertEqual(self.reads, [])

    def test_settlement_denies_hidden_or_cancelled_source_without_leaking_source_identity(self):
        self.submitted_credit()
        Record.denied.add(("Jifeng Expense Voucher", "EXP-1", "read"))
        with self.assertRaises(PermissionError) as caught:
            self.accounting.lock_expense_settlement_references(self.settlement())
        self.assertNotIn("EXP-1", str(caught.exception))
        Record.denied.clear()
        self.voucher.docstatus = 2
        with self.assertRaisesRegex(ValueError, "已失效"):
            self.accounting.lock_expense_settlement_references(self.settlement())

    def test_settlement_rechecks_native_company_party_and_payable_after_lock(self):
        self.submitted_credit()
        for changes in ({"company": "OTHER"}, {"party": "OTHER"}, {"paid_to": "BANK"}, {"payment_type": "Receive"}):
            payment = self.settlement()
            payment.update(changes)
            with self.assertRaises(ValueError):
                self.accounting.lock_expense_settlement_references(payment)
        self.journal.jf_expense_voucher = "EXP-1"
        self.persisted = deepcopy(self.voucher)
        self.persisted.journal_entry = "DIFFERENT-JE"
        different = Record({key: deepcopy(value) for key, value in self.journal.items() if key != "accounting"})
        different.name = "DIFFERENT-JE"
        original = self.frappe.get_doc
        self.frappe.get_doc = lambda dt, name=None, **kwargs: (
            different if dt == "Journal Entry" and name == "DIFFERENT-JE" else original(dt, name, **kwargs))
        with self.assertRaises(ValueError):
            self.accounting.lock_expense_settlement_references(self.settlement())

    def test_native_journal_settlement_uses_same_source_locks_and_party_account_contract(self):
        self.submitted_credit()
        row = Record(reference_type="Journal Entry", reference_name="JV-1", party_type="Supplier", party="SUP", account="PAY")
        journal = Record(doctype="Journal Entry", company="JF", accounts=[row])
        old = deepcopy(journal)
        self.accounting.lock_expense_settlement_references(journal)
        self.assertEqual(journal, old)
        row.account = "BANK"
        with self.assertRaisesRegex(ValueError, "供应商和应付科目"):
            self.accounting.lock_expense_settlement_references(journal)

    def test_payment_context_uses_native_reference_balance_and_persisted_source(self):
        self.submitted_credit()
        self.balance = 80
        result = self.accounting.payment_context("EXP-1")
        self.assertEqual(result["outstanding_amount"], 80)
        self.assertEqual(result["total_amount"], 120.5)
        self.assertEqual(result["reference_name"], "JV-1")
        self.assertEqual(result["party"], "SUP")
        self.assertNotIn("arrears_reference", result)
        self.assertNotIn("paid_from", result)
        self.assertEqual(self.journal.docstatus, 1)

    def test_payment_context_denies_permissions_noncredit_and_already_paid(self):
        self.submitted_credit()
        self.create_payment = False
        with self.assertRaises(PermissionError):
            self.accounting.payment_context("EXP-1")
        self.create_payment = True
        self.balance = 0
        with self.assertRaisesRegex(ValueError, "已无可付款余额"):
            self.accounting.payment_context("EXP-1")
        self.voucher.payment_method = "现金"
        with self.assertRaisesRegex(ValueError, "已提交的赊账"):
            self.accounting.payment_context("EXP-1")

    def test_journal_table_and_child_field_permissions_are_required(self):
        self.submitted_credit()
        self.table_level = 1
        with self.assertRaises(PermissionError):
            self.accounting.payment_context("EXP-1")
        self.table_level = 0
        self.hidden_fields["Journal Entry Account"] = {"party"}
        with self.assertRaises(PermissionError):
            self.accounting.payment_context("EXP-1")

    def test_cancel_rejects_partial_full_and_hidden_actual_payments(self):
        self.submitted_credit()
        self.references["Payment Entry Reference"] = [Record(parent="PRIVATE-PE")]
        for balance in (80, 0, 120.5):
            self.balance = balance
            with self.assertRaisesRegex(ValueError, "先取消"):
                self.accounting.validate_credit_cancellation(self.voucher)
            self.assertEqual(self.journal.docstatus, 1)
        Record.denied.add(("Payment Entry", "PRIVATE-PE", "read"))
        with self.assertRaises(PermissionError) as caught:
            self.accounting.validate_credit_cancellation(self.voucher)
        self.assertNotIn("PRIVATE-PE", str(caught.exception))

    def test_cancel_detects_journal_reconciliation_and_external_ple_without_visible_reference(self):
        self.submitted_credit()
        self.references["Journal Entry Account"] = [Record(parent="JV-OTHER")]
        with self.assertRaisesRegex(ValueError, "先取消"):
            self.accounting.validate_credit_cancellation(self.voucher)
        self.references.pop("Journal Entry Account")
        self.references["Payment Ledger Entry"].append(Record(voucher_type="Payment Entry", voucher_no="PE-HIDDEN",
            company="JF", account="PAY", party_type="Supplier", party="SUP"))
        with self.assertRaisesRegex(ValueError, "先取消"):
            self.accounting.validate_credit_cancellation(self.voucher)

    def test_cancel_requires_complete_original_outstanding_and_exact_je(self):
        self.submitted_credit()
        self.balance = 80
        with self.assertRaisesRegex(ValueError, "先取消"):
            self.accounting.validate_credit_cancellation(self.voucher)
        self.balance = 120.5
        self.journal.accounts[1].party = "OTHER"
        with self.assertRaisesRegex(ValueError, "供应商应付来源"):
            self.accounting.validate_credit_cancellation(self.voucher)

    def test_native_immutable_cancelled_payment_ple_must_net_to_zero(self):
        self.submitted_credit()
        self.payment_docstatus = 2
        for amount in (-40, 40):
            self.references["Payment Ledger Entry"].append(Record(voucher_type="Payment Entry", voucher_no="PE-1",
                company="JF", account="PAY", party_type="Supplier", party="SUP", amount_in_account_currency=amount))
        self.accounting.validate_credit_cancellation(self.voucher)
        self.references["Payment Ledger Entry"][-1].amount_in_account_currency = 39
        with self.assertRaisesRegex(ValueError, "先取消"):
            self.accounting.validate_credit_cancellation(self.voucher)

    def test_cancel_closure_uses_complete_current_locked_child_and_ple_reads(self):
        self.submitted_credit()
        self.accounting.validate_credit_cancellation(self.voucher)
        queries = {dt: (fields, filters, locked) for dt, fields, filters, locked in self.lock_queries}
        self.assertEqual(set(queries), {"Payment Entry Reference", "Journal Entry Account", "Payment Ledger Entry"})
        self.assertTrue(all(fields == ["*"] and locked for fields, _, locked in queries.values()))
        self.assertEqual(queries["Payment Entry Reference"][1], {"parenttype": "Payment Entry", "docstatus": 1,
            "reference_doctype": "Journal Entry", "reference_name": "JV-1"})
        self.assertEqual(queries["Journal Entry Account"][1], {"parenttype": "Journal Entry", "docstatus": 1,
            "reference_type": "Journal Entry", "reference_name": "JV-1"})
        self.assertEqual(queries["Payment Ledger Entry"][1], {"against_voucher_type": "Journal Entry",
            "against_voucher_no": "JV-1", "delinked": 0})

    def test_actual_ple_total_must_match_source_even_if_outstanding_is_equal(self):
        self.submitted_credit()
        module = sys.modules["erpnext.accounts.doctype.payment_entry.payment_entry"]
        module.get_reference_details = lambda *args: {"total_amount": 121, "outstanding_amount": 121}
        with self.assertRaisesRegex(ValueError, "实际应付总额"):
            self.accounting.payment_context("EXP-1")

    def test_cancel_in_memory_docstatus_two_rechecks_submitted_source_then_native_cancels(self):
        self.submitted_credit()
        self.persisted = deepcopy(self.voucher)
        self.voucher.docstatus = 2
        # Historical reversal remains possible when selection masters are disabled.
        self.accounts["PAY"].disabled = self.supplier.disabled = 1
        self.voucher_class.before_cancel(self.voucher)
        self.voucher_class.on_cancel(self.voucher)
        self.assertEqual(self.journal.docstatus, 2)
        self.assertEqual(self.voucher.journal_entry, "JV-1")

    def test_cancel_rejects_client_financial_change_before_posting(self):
        self.submitted_credit()
        self.persisted = deepcopy(self.voucher)
        self.voucher.docstatus = 2
        self.voucher.amount = 121
        with self.assertRaises(ValueError):
            self.voucher_class.before_cancel(self.voucher)
        self.assertEqual(self.journal.docstatus, 1)

    def test_ledger_separates_actual_payable_and_old_reference_without_writing(self):
        self.submitted_credit()
        old = deepcopy(self.voucher)
        columns, rows = self.report.execute({"company": "JF", "from_date": "2026-09-01", "to_date": "2026-10-01"})
        self.assertEqual(rows[0]["supplier"], "SUP")
        self.assertEqual(rows[0]["payable_account"], "PAY")
        self.assertIsNone(rows[0]["payment_account"])
        self.assertEqual(rows[0]["arrears_reference"], 9999)
        self.assertNotIn("outstanding_amount", rows[0])
        self.assertIn("旧欠款参考（不入账）", [column["label"] for column in columns])
        self.assertEqual(self.voucher, old)

    def test_ledger_rejects_unreadable_financial_field_and_cannot_infer_missing_provenance(self):
        self.submitted_credit()
        self.hidden_fields["Jifeng Expense Voucher"] = {"arrears_reference"}
        with self.assertRaises(PermissionError):
            self.report.execute({"company": "JF", "from_date": "2026-09-01", "to_date": "2026-10-01"})
        self.hidden_fields.clear()
        self.journal.jf_expense_voucher = "OTHER"
        with self.assertRaises(ValueError):
            self.report.execute({"company": "JF", "from_date": "2026-09-01", "to_date": "2026-10-01"})


if __name__ == "__main__":
    unittest.main()
