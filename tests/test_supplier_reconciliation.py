"""Cutoff payables reject hidden settlement history and never invent balances."""

import importlib.util
import pathlib
import sys
import types
import unittest
from datetime import date
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class Document(Record):
    def has_permission(self, action):
        return action not in self.get("denied", set())

    def as_dict(self):
        return dict(self)


# Actual AccountsPayable.get_columns ordering/types with fixed adapter switches:
# Date first, currency amounts, ageing buckets, then Link/Currency. Source:
# ERPNext accounts_receivable.py:1177 and Frappe query_report.add_total_row:785.
AP_COLUMNS = [
    ("posting_date", "Date", None), ("party_type", "Data", None),
    ("party", "Dynamic Link", "party_type"), ("party_account", "Link", "Account"),
    ("cost_center", "Data", None), ("project", "Link", "Project"),
    ("voucher_type", "Data", None), ("voucher_no", "Dynamic Link", "voucher_type"),
    ("due_date", "Date", None), ("bill_no", "Data", None), ("bill_date", "Date", None),
    ("invoiced", "Currency", "currency"), ("paid", "Currency", "currency"),
    ("credit_note", "Currency", "currency"), ("outstanding", "Currency", "currency"),
    ("age", "Int", None), ("range0", "Currency", "currency"),
    ("range1", "Currency", "currency"), ("range2", "Currency", "currency"),
    ("range3", "Currency", "currency"), ("range4", "Currency", "currency"),
    ("range5", "Currency", "currency"),
    ("currency", "Link", "Currency"), ("supplier_group", "Link", "Supplier Group"),
]


class TestSupplierReconciliation(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = LookupError
        self.frappe.conf = {}
        self.frappe._ = lambda message: {"Total": "总计"}.get(message, message)
        self.frappe.session = Record(user="synthetic-accounts-user")
        self.frappe.whitelist = lambda **kwargs: lambda function: function

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        utilities = types.ModuleType("frappe.utils")
        utilities.getdate = lambda value: value if isinstance(value, date) else date.fromisoformat(value)
        self.model = types.ModuleType("frappe.model")
        self.fields_denied = {}
        self.masked_fields, self.mask_levels, self.mask_calls = {}, {}, []
        self.model.get_permitted_fields = lambda doctype, **kwargs: self.permitted_fields[doctype] - self.fields_denied.get(doctype, set())
        self.report = types.ModuleType("frappe.desk.query_report")
        self.report.get_report_doc = Mock(return_value=Record(name="Accounts Payable"))
        self.report.run = Mock(side_effect=lambda *args, **kwargs: self.report_result)
        desk = types.ModuleType("frappe.desk")
        desk.query_report = self.report
        self.accounting = types.ModuleType("jifeng_erp.accounting")
        self.accounting.SOURCE = "Jifeng Expense Voucher"
        self.accounting.CREDIT = "赊账"
        self.accounting.SOURCE_FIELDS = ("name", "company", "docstatus", "payment_method", "supplier", "payable_account", "journal_entry")
        self.module_patch = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utilities,
            "frappe.model": self.model, "frappe.desk": desk, "frappe.desk.query_report": self.report,
            "jifeng_erp.accounting": self.accounting})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "supplier_reconciliation.py"
        spec = importlib.util.spec_from_file_location("supplier_reconciliation_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.permitted_fields = {"Company": {"name", "default_currency"}, "Supplier": {"name"}, "Currency": {"name"},
            "Account": set(self.module.ACCOUNT_FIELDS), self.module.PLE: set(self.module.LEDGER_FIELDS),
            "Journal Entry Account": set(self.module.JOURNAL_ROW_FIELDS),
            self.accounting.SOURCE: set(self.accounting.SOURCE_FIELDS),
            **{doctype: set(fields) for doctype, fields in self.module.SOURCE_FIELDS.items()}}
        self.permitted_fields["Journal Entry"].add("jf_expense_voucher")
        self.hidden = {}
        self.records = {doctype: [] for doctype in (
            "Account", self.module.PLE, "Purchase Invoice", "Repost Payment Ledger Items", "Repost Accounting Ledger Items")}
        self.documents = {}
        self.add_document("Company", "COMPANY", default_currency="CNY")
        self.records["Company"] = [self.documents["Company", "COMPANY"]]
        self.add_document("Supplier", "SUPPLIER")
        self.add_document("Currency", "CNY")
        self.account = self.add_document("Account", "PAYABLE", company="COMPANY", account_type="Payable", account_currency="CNY", is_group=0)
        self.records["Account"].append(self.account)
        self.invoice = self.add_invoice("PI", grand_total=999, outstanding_amount=999)
        self.ledger = self.add_ledger("PLE-INVOICE", "Purchase Invoice", "PI", "Purchase Invoice", "PI", 120)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.get_list = Mock(side_effect=lambda doctype, filters, **kwargs: [row.name for row in self.get_all(doctype, filters)
            if row.name not in self.hidden.get(doctype, set())])
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        def meta(doctype):
            def levels(**kwargs):
                if kwargs.get("permission_type") == "mask":
                    self.mask_calls.append((doctype, kwargs.get("parenttype")))
                    return self.mask_levels.get(doctype, [])
                return [0]
            return Record(get_field=lambda field: Record(fieldtype="Table", permlevel=0,
                mask=int(field in self.masked_fields.get(doctype, set()))), get_permlevel_access=levels,
                get_masked_fields=Mock(return_value=[]))  # cached list may predate mask changes
        self.frappe.get_meta = meta
        self.job_states = {}
        self.frappe.db = Record(get_value=lambda doctype, name, fields, **kwargs: self.job_states.get((doctype, name)))
        self.native_row = Record(posting_date="2026-09-29", due_date="2026-10-15", party_type="Supplier", party="SUPPLIER",
            party_account="PAYABLE", voucher_type="Purchase Invoice", voucher_no="PI", currency="CNY",
            invoiced=120.0, paid=30.25, credit_note=5.18, outstanding=84.57,
            age=-15, range0=84.57, range1=0.0, range2=0.0, range3=0.0, range4=0.0, range5=0.0)
        self.report_result = {"columns": [dict(fieldname=field, fieldtype=kind, options=options)
            for field, kind, options in AP_COLUMNS],
            "result": [self.native_row], "add_total_row": False, "message": "must not be exposed",
            "chart": {"labels": ["private-other-party"]}, "report_summary": [{"value": "hidden"}]}

    def add_document(self, doctype, name, **values):
        doc = Document(name=name, doctype=doctype, **values)
        self.documents[doctype, name] = doc
        return doc

    def add_invoice(self, name, account="PAYABLE", **values):
        values = {"company": "COMPANY", "supplier": "SUPPLIER", "credit_to": account, "docstatus": 1,
            "posting_date": "2026-09-29", "due_date": "2026-10-15", "is_return": 0, "return_against": None, **values}
        doc = self.add_document("Purchase Invoice", name, **values)
        self.records["Purchase Invoice"].append(doc)
        return doc

    def add_ledger(self, name, voucher_type, voucher_no, against_type, against_no, amount, account="PAYABLE", **values):
        row = Record(name=name, company="COMPANY", party_type="Supplier", party="SUPPLIER", account=account,
            account_type="Payable", account_currency="CNY", delinked=0, posting_date="2026-09-29", due_date=None,
            voucher_type=voucher_type, voucher_no=voucher_no, against_voucher_type=against_type,
            against_voucher_no=against_no, amount=amount, amount_in_account_currency=amount, **values)
        self.records[self.module.PLE].append(row)
        return row

    def add_payment(self, name="PRIVATE-PE"):
        doc = self.add_document("Payment Entry", name, company="COMPANY", docstatus=1, posting_date="2026-09-30",
            party_type="Supplier", party="SUPPLIER", payment_type="Pay", paid_from="BANK", paid_to="PAYABLE")
        self.add_ledger("PLE-PAYMENT", "Payment Entry", name, "Purchase Invoice", "PI", -40)
        return doc

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            values = dict(doctype)
            original = self.documents.get((values["doctype"], values.get("name")))
            if original and original.get("denied"):
                values["denied"] = original["denied"]
            return Document(values)
        if (doctype, name) not in self.documents:
            raise LookupError("private lookup")
        return self.documents[doctype, name]

    @staticmethod
    def matches(row, filters):
        for field, expected in (filters or {}).items():
            actual = row.get(field)
            if isinstance(expected, list):
                operator, value = expected
                if operator == "in" and actual not in value:
                    return False
                if operator == "<=" and actual > value:
                    return False
            elif actual != expected:
                return False
        return True

    def get_all(self, doctype, filters=None, **kwargs):
        return [row for row in self.records.get(doctype, []) if self.matches(row, filters)]

    def query(self, **kwargs):
        return self.module.query_payables("COMPANY", "SUPPLIER", "2026-09-30", **kwargs)

    def native_total(self):
        # Literal output of native add_total_row for the single native_row:
        # numeric nonzero cells filled, zero cells remain "", Date blank,
        # Currency Link copied; a Data first column is overwritten by _('Total').
        values = {"invoiced": 120.0, "paid": 30.25, "credit_note": 5.18,
                  "outstanding": 84.57, "currency": "CNY", "age": -15.0, "range0": 84.57}
        total = [values.get(column["fieldname"], "") for column in self.report_result["columns"]]
        if self.report_result["columns"][0]["fieldtype"] == "Data":
            total[0] = "总计"
        return total

    def test_native_cutoff_amounts_are_projected_without_current_invoice_arithmetic(self):
        result = self.query()
        self.assertEqual(result["rows"][0]["outstanding"], "84.57")
        self.assertEqual((result["rows"][0]["invoiced"], result["rows"][0]["paid"]), ("120.0", "30.25"))
        self.assertEqual(result["amount_basis"], "company_currency")
        self.assertNotIn("message", result)
        self.assertNotIn("chart", result)
        self.assertNotIn("report_summary", result)
        args, kwargs = self.report.run.call_args
        self.assertEqual(args, ("Accounts Payable",))
        self.assertEqual(kwargs["filters"]["party"], ["SUPPLIER"])
        self.assertEqual(kwargs["filters"]["party_type"], "Supplier")
        self.assertEqual(kwargs["filters"]["report_date"], "2026-09-30")
        self.assertEqual(kwargs["filters"]["show_future_payments"], 0)
        self.assertEqual(kwargs["filters"]["ignore_accounts"], 0)
        self.assertTrue(kwargs["ignore_prepared_report"])
        self.assertFalse(kwargs["are_default_filters"])

    def test_hidden_payment_ledger_rejects_all_instead_of_showing_unpaid_invoice(self):
        self.add_payment()
        self.hidden[self.module.PLE] = {"PLE-PAYMENT"}
        with self.assertRaises(PermissionError) as error:
            self.query()
        self.assertNotIn("PRIVATE-PE", str(error.exception))
        self.assertNotIn("PLE-PAYMENT", str(error.exception))
        self.report.run.assert_not_called()

    def test_readable_ledger_does_not_authorize_hidden_payment_source(self):
        self.add_payment()["denied"] = {"read"}
        with self.assertRaises(PermissionError) as error:
            self.query()
        self.assertNotIn("PRIVATE-PE", str(error.exception))
        self.report.run.assert_not_called()

    def test_against_voucher_must_be_readable_even_when_payment_is_visible(self):
        self.add_payment()
        self.records[self.module.PLE].remove(self.ledger)
        self.invoice["denied"] = {"read"}
        with self.assertRaises(PermissionError):
            self.query()
        self.report.run.assert_not_called()

    def test_all_accounts_scope_rejects_hidden_account_but_explicit_scope_is_independent(self):
        self.records["Account"].append(self.add_document("Account", "HIDDEN-ACCOUNT", company="COMPANY",
            account_type="Payable", account_currency="CNY", is_group=0))
        self.hidden["Account"] = {"HIDDEN-ACCOUNT"}
        with self.assertRaises(PermissionError) as error:
            self.query()
        self.assertNotIn("HIDDEN-ACCOUNT", str(error.exception))
        result = self.query(payable_account="PAYABLE")
        self.assertEqual(result["accounts"], ["PAYABLE"])
        self.assertEqual(self.report.run.call_args.kwargs["filters"]["party_account"], "PAYABLE")

    def test_selected_foreign_account_rejected_but_unfiltered_native_base_amounts_allowed(self):
        self.account["account_currency"] = "USD"
        self.ledger["account_currency"] = "USD"
        self.ledger["amount_in_account_currency"] = 20
        with self.assertRaisesRegex(ValueError, "公司本币"):
            self.query(payable_account="PAYABLE")
        result = self.query()
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["rows"][0]["outstanding"], "84.57")
        self.assertNotIn("party_account", self.report.run.call_args.kwargs["filters"])

    def test_blank_historical_account_currency_uses_verified_native_company_fallback(self):
        self.account["account_currency"] = None
        self.assertEqual(self.query(payable_account="PAYABLE")["currency"], "CNY")
        self.assertIsNone(self.account["account_currency"])

    def test_financial_field_revocation_cannot_be_bypassed_through_ledger_report(self):
        for doctype, field in ((self.module.PLE, "amount"), ("Purchase Invoice", "grand_total"), ("Account", "account_currency")):
            with self.subTest(doctype=doctype, field=field):
                self.fields_denied[doctype] = {field}
                with self.assertRaises(PermissionError):
                    self.query()
                self.fields_denied.clear()
                self.report.run.assert_not_called()

    def test_masked_required_amounts_and_company_currency_reject_report_aliases(self):
        for doctype, field in ((self.module.PLE, "amount"), ("Purchase Invoice", "grand_total"),
                              ("Account", "account_currency"), ("Company", "default_currency")):
            with self.subTest(doctype=doctype, field=field):
                self.masked_fields[doctype] = {field}
                with self.assertRaises(PermissionError):
                    self.query()
                self.report.run.assert_not_called()
                self.masked_fields.clear()

    def test_native_unmask_permission_and_administrator_follow_meta_rule(self):
        for doctype, field in ((self.module.PLE, "amount"), ("Purchase Invoice", "grand_total"),
                              ("Account", "account_currency"), ("Company", "default_currency")):
            self.masked_fields[doctype] = {field}
            self.mask_levels[doctype] = [0]
        self.assertEqual(self.query()["rows"][0]["outstanding"], "84.57")
        self.mask_levels.clear()
        self.frappe.session.user = "Administrator"
        self.assertEqual(self.query()["rows"][0]["outstanding"], "84.57")

    def test_masked_journal_table_and_child_amount_need_separate_parent_mask_access(self):
        self.masked_fields["Journal Entry"] = {"accounts"}
        with self.assertRaises(PermissionError):
            self.module._table("Journal Entry", "accounts")
        self.masked_fields.clear()
        self.masked_fields["Journal Entry Account"] = {"credit_in_account_currency"}
        with self.assertRaises(PermissionError):
            self.module._fields("Journal Entry Account", ("credit_in_account_currency",), parenttype="Journal Entry")
        self.assertIn(("Journal Entry Account", "Journal Entry"), self.mask_calls)

    def test_mask_revocation_during_report_rejects_even_with_stale_native_mask_list(self):
        def revoke(*args, **kwargs):
            self.masked_fields[self.module.PLE] = {"amount"}
            return self.report_result
        self.report.run.side_effect = revoke
        with self.assertRaises(PermissionError):
            self.query()

    def test_supplier_currency_or_company_permission_cannot_be_replaced_by_report_role(self):
        for doctype, name, action in (("Company", "COMPANY", "select"),
                                     ("Supplier", "SUPPLIER", "read"), ("Currency", "CNY", "read")):
            with self.subTest(doctype=doctype):
                self.documents[doctype, name]["denied"] = {action}
                with self.assertRaises(PermissionError):
                    self.query()
                self.documents[doctype, name]["denied"] = set()
        self.report.run.assert_not_called()

    def test_company_select_without_profile_read_can_derive_currency(self):
        self.documents["Company", "COMPANY"].update(denied={"read"}, private_profile="private-company-profile")
        self.fields_denied["Company"] = {"default_currency"}
        result = self.query()
        self.assertEqual(result["currency"], "CNY")
        self.assertNotIn("private-company-profile", repr(result))

    def test_cancelled_native_history_is_authorized_without_dropping_original_or_reversal(self):
        self.invoice["docstatus"] = 2
        self.add_ledger("PLE-REVERSAL", "Purchase Invoice", "PI", "Purchase Invoice", "PI", -120)
        self.report_result["result"] = []
        result = self.query()
        self.assertEqual(result["rows"], [])
        ledger_calls = [call for call in self.frappe.get_all.call_args_list if call.args[0] == self.module.PLE]
        self.assertTrue(ledger_calls)
        self.assertNotIn("docstatus", ledger_calls[0].kwargs["filters"])
        self.report.run.assert_called_once()

    def test_return_original_permission_and_cycles_are_checked_beyond_direct_ledger_pair(self):
        self.invoice.update(is_return=1, return_against="PRIVATE-ORIGINAL")
        original = self.add_invoice("PRIVATE-ORIGINAL", denied={"read"})
        with self.assertRaises(PermissionError) as error:
            self.query()
        self.assertNotIn("PRIVATE-ORIGINAL", str(error.exception))
        original.update(denied=set(), is_return=1, return_against="PI")
        with self.assertRaisesRegex(ValueError, "不完整或范围不一致"):
            self.query()
        self.report.run.assert_not_called()

    def test_repost_blocks_even_when_submitted_invoice_has_no_ledger_yet(self):
        self.records[self.module.PLE].clear()
        self.records["Repost Payment Ledger Items"] = [Record(parent="PRIVATE-JOB", parenttype="Repost Payment Ledger",
            voucher_type="Purchase Invoice", voucher_no="PI")]
        for status in ("Queued", "Failed", ""):
            with self.subTest(status=status):
                self.job_states["Repost Payment Ledger", "PRIVATE-JOB"] = Record(docstatus=1, repost_status=status)
                with self.assertRaises(ValueError) as error:
                    self.query()
                self.assertNotIn("PRIVATE-JOB", str(error.exception))
        self.report.run.assert_not_called()

    def test_rebuilding_payment_or_journal_with_temporarily_missing_ledger_is_not_skipped(self):
        payment = self.add_payment("PAYMENT-REBUILD")
        self.records[self.module.PLE].clear()
        self.records["Payment Entry"] = [payment]
        self.records["Repost Payment Ledger Items"] = [Record(parent="PE-JOB", parenttype="Repost Payment Ledger",
            voucher_type="Payment Entry", voucher_no="PAYMENT-REBUILD")]
        self.job_states["Repost Payment Ledger", "PE-JOB"] = Record(docstatus=1, repost_status="Queued")
        with self.assertRaisesRegex(ValueError, "重过账"):
            self.query()
        self.records["Payment Entry"].clear()
        self.records["Repost Payment Ledger Items"].clear()
        journal = self.add_document("Journal Entry", "JE-REBUILD", company="COMPANY", docstatus=1)
        self.records["Journal Entry"] = [journal]
        self.records["Journal Entry Account"] = [Record(parent="JE-REBUILD", parenttype="Journal Entry",
            account="PAYABLE", party_type="Supplier", party="SUPPLIER")]
        self.records["Repost Accounting Ledger Items"] = [Record(parent="JE-JOB", parenttype="Repost Accounting Ledger",
            voucher_type="Journal Entry", voucher_no="JE-REBUILD")]
        self.job_states["Repost Accounting Ledger", "JE-JOB"] = Record(docstatus=1, status="Partially Reposted")
        with self.assertRaisesRegex(ValueError, "重过账"):
            self.query()
        self.report.run.assert_not_called()

    def test_accounting_partial_repost_blocks_and_terminal_or_draft_states_do_not(self):
        self.records["Repost Accounting Ledger Items"] = [Record(parent="JOB", parenttype="Repost Accounting Ledger",
            voucher_type="Purchase Invoice", voucher_no="PI")]
        for status in ("Queued", "In Progress", "Partially Reposted", "Failed"):
            self.job_states["Repost Accounting Ledger", "JOB"] = Record(docstatus=1, status=status)
            with self.assertRaisesRegex(ValueError, "重过账"):
                self.query()
        for docstatus, status in ((1, "Completed"), (1, "Cancelled"), (0, "Queued"), (2, "Failed")):
            self.job_states["Repost Accounting Ledger", "JOB"] = Record(docstatus=docstatus, status=status)
            self.assertEqual(len(self.query()["rows"]), 1)

    def test_repost_matches_type_and_name_together_instead_of_cartesian_candidate_names(self):
        self.add_payment("PE")
        self.records["Repost Payment Ledger Items"] = [Record(parent="UNRELATED", parenttype="Repost Payment Ledger",
            voucher_type="Payment Entry", voucher_no="PI")]
        self.job_states["Repost Payment Ledger", "UNRELATED"] = Record(docstatus=1, repost_status="Failed")
        self.assertEqual(len(self.query()["rows"]), 1)

    def test_native_result_must_still_match_authorized_scope_and_cutoff(self):
        for field, value in (("party", "PRIVATE-OTHER"), ("party_type", "Customer"), ("party_account", "OTHER-ACCOUNT"),
                             ("currency", "USD"), ("voucher_no", "PRIVATE-PI"), ("posting_date", "2026-10-01")):
            with self.subTest(field=field):
                original = self.native_row[field]
                self.native_row[field] = value
                with self.assertRaises(ValueError) as error:
                    self.query()
                self.assertNotIn(str(value), str(error.exception))
                self.native_row[field] = original

    def test_native_total_tail_is_not_a_voucher_and_zero_total_cells_can_be_blank(self):
        total = self.native_total()
        self.assertEqual(total[0], "")  # Real AP's first column is Date.
        self.report_result.update(result=[self.native_row, total], add_total_row=True)
        self.assertEqual(len(self.query()["rows"]), 1)
        self.report_result["result"] = [total, self.native_row]
        with self.assertRaises(ValueError):
            self.query()
        self.report_result.update(result=[self.native_row, total], add_total_row=False)
        with self.assertRaises(ValueError):
            self.query()

    def test_native_total_label_obeys_first_column_type_and_current_translation(self):
        columns = self.report_result["columns"]
        # Standard Date-first AP cannot have a translated or English total label.
        for label in ("总计", "Total"):
            total = self.native_total()
            total[0] = label
            self.report_result.update(result=[self.native_row, total], add_total_row=True)
            with self.assertRaises(ValueError):
                self.query()
        # Frappe supports a native Data-first column with a translated label.
        self.report_result["columns"] = [columns[1], columns[0], *columns[2:]]
        total = self.native_total()
        self.assertEqual(total[0], "总计")
        self.report_result["result"] = [self.native_row, total]
        self.assertEqual(len(self.query()["rows"]), 1)
        total[0] = "Total"
        with self.assertRaises(ValueError):
            self.query()

    def test_bad_total_content_cannot_hide_a_corrupt_voucher_row(self):
        for field, value in (("voucher_no", "PRIVATE-SOURCE"), ("party_account", "PAYABLE"),
                             ("currency", "USD"), ("invoiced", float("inf"))):
            total = self.native_total()
            index = next(index for index, column in enumerate(self.report_result["columns"])
                         if column["fieldname"] == field)
            total[index] = value
            self.report_result.update(result=[self.native_row, total], add_total_row=True)
            with self.assertRaises(ValueError) as error:
                self.query()
            self.assertNotIn("PRIVATE-SOURCE", str(error.exception))

    def test_new_hidden_payment_during_native_report_rejects_complete_result(self):
        def during_report(*args, **kwargs):
            self.add_payment()["denied"] = {"read"}
            return self.report_result

        self.report.run.side_effect = during_report
        with self.assertRaises(PermissionError) as error:
            self.query()
        self.assertNotIn("PRIVATE-PE", str(error.exception))
        self.report.run.assert_called_once()

    def test_new_readable_payment_during_native_report_requires_a_fresh_query(self):
        self.report.run.side_effect = lambda *args, **kwargs: (self.add_payment(), self.report_result)[1]
        with self.assertRaisesRegex(ValueError, "查询期间") as error:
            self.query()
        self.assertNotIn("PRIVATE-PE", str(error.exception))
        self.report.run.assert_called_once()

    def test_ledger_reallocation_or_source_child_change_during_report_is_detected(self):
        self.add_payment("PE")
        payment = self.documents["Payment Entry", "PE"]
        payment["references"] = [Record(reference_doctype="Purchase Invoice", reference_name="PI", allocated_amount=40)]
        changes = ((self.records[self.module.PLE][1], "amount", -45),
                   (payment["references"][0], "allocated_amount", 45),
                   (self.invoice, "grand_total", 1000))
        for document, field, value in changes:
            original = document[field]

            def during_report(*args, **kwargs):
                document[field] = value
                return self.report_result

            self.report.run.side_effect = during_report
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "查询期间"):
                self.query()
            document[field] = original

    def test_account_party_and_repost_scope_changes_during_report_are_detected(self):
        self.job_states["Repost Payment Ledger", "JOB"] = Record(docstatus=1, repost_status="Completed")
        changes = (
            lambda: self.account.update(account_name="Renamed"),
            lambda: self.documents["Supplier", "SUPPLIER"].update(supplier_name="Changed"),
            lambda: self.records["Repost Payment Ledger Items"].append(Record(parent="JOB", parenttype="Repost Payment Ledger",
                voucher_type="Purchase Invoice", voucher_no="PI")),
        )
        for change in changes:
            self.report.run.side_effect = lambda *args, **kwargs: (change(), self.report_result)[1]
            with self.assertRaisesRegex(ValueError, "查询期间"):
                self.query()

    def test_fresh_permission_checks_reject_revocation_during_report(self):
        def during_report(*args, **kwargs):
            self.invoice["denied"] = {"read"}
            return self.report_result

        self.report.run.side_effect = during_report
        with self.assertRaises(PermissionError):
            self.query()

    def test_raw_authorization_change_is_detected_even_if_native_permission_cache_is_unchanged(self):
        # Simulate a native cached has_permission still returning True after the
        # persisted role permission changed. Raw auth inputs must still differ.
        self.records["DocPerm"] = [Record(name="ROLE-PERM", parent="Purchase Invoice", role="Accounts User", read=1)]

        def during_report(*args, **kwargs):
            self.records["DocPerm"][0]["read"] = 0
            return self.report_result

        self.report.run.side_effect = during_report
        with self.assertRaisesRegex(ValueError, "查询期间"):
            self.query()

    def test_blank_native_voucher_or_nonfinite_amount_is_not_silently_omitted(self):
        self.report_result["result"] = [{}]
        with self.assertRaises(ValueError):
            self.query()
        self.report_result["result"] = [self.native_row]
        for field, value in (("outstanding", float("nan")), ("paid", None), ("invoiced", True)):
            original = self.native_row[field]
            self.native_row[field] = value
            with self.assertRaises(ValueError):
                self.query()
            self.native_row[field] = original

    def test_standalone_payment_without_due_date_is_a_valid_native_source(self):
        self.records[self.module.PLE].clear()
        self.add_document("Payment Entry", "ADVANCE", company="COMPANY", docstatus=1, posting_date="2026-09-29",
            party_type="Supplier", party="SUPPLIER", payment_type="Pay", paid_from="BANK", paid_to="PAYABLE")
        self.add_ledger("PLE-ADVANCE", "Payment Entry", "ADVANCE", "Payment Entry", "ADVANCE", -30)
        self.native_row.update(voucher_type="Payment Entry", voucher_no="ADVANCE", invoiced=0, paid=30, outstanding=-30)
        self.native_row.pop("due_date")
        self.assertIsNone(self.query()["rows"][0]["due_date"])

    def test_journal_party_rows_and_managed_expense_reverse_source_are_not_assumed(self):
        self.records[self.module.PLE].clear()
        journal = self.add_document("Journal Entry", "JE", company="COMPANY", docstatus=1, posting_date="2026-09-29",
            accounts=[Record(account="PAYABLE", party_type="Supplier", party="SUPPLIER")], jf_expense_voucher="PRIVATE-EXPENSE")
        expense = self.add_document(self.accounting.SOURCE, "PRIVATE-EXPENSE", company="COMPANY", docstatus=1,
            payment_method="赊账", supplier="SUPPLIER", payable_account="PAYABLE", journal_entry="JE", denied={"read"})
        self.add_ledger("PLE-JE", "Journal Entry", "JE", "Journal Entry", "JE", 120)
        self.native_row.update(voucher_type="Journal Entry", voucher_no="JE")
        with self.assertRaises(PermissionError) as error:
            self.query()
        self.assertNotIn("PRIVATE-EXPENSE", str(error.exception))
        expense.update(denied=set(), journal_entry="ANOTHER-JE")
        with self.assertRaises(ValueError):
            self.query()
        expense["journal_entry"] = "JE"
        self.fields_denied["Journal Entry Account"] = {"credit_in_account_currency"}
        with self.assertRaises(PermissionError):
            self.query()
        self.fields_denied.clear()
        journal["accounts"][0]["party"] = "OTHER"
        with self.assertRaises(ValueError):
            self.query()

    def test_report_permission_entry_cannot_be_bypassed_by_source_read(self):
        self.report.get_report_doc.side_effect = PermissionError("report denied")
        with self.assertRaises(PermissionError):
            self.query()
        self.report.run.assert_not_called()

    def test_purchase_order_is_an_advance_reference_and_cannot_pose_as_posted_voucher(self):
        self.add_document("Purchase Order", "PO", company="COMPANY", supplier="SUPPLIER", docstatus=1)
        self.ledger.update(voucher_type="Purchase Order", voucher_no="PO")
        with self.assertRaises(ValueError):
            self.query()
        self.ledger.update(voucher_type="Purchase Invoice", voucher_no="PI", against_voucher_type="Purchase Order", against_voucher_no="PO")
        self.assertEqual(len(self.query()["rows"]), 1)

    def test_replica_mode_does_not_mix_primary_preflight_with_replica_report(self):
        self.frappe.conf["read_from_replica"] = True
        with self.assertRaisesRegex(ValueError, "主库"):
            self.query()
        self.frappe.get_all.assert_not_called()
        self.report.run.assert_not_called()

    def test_explicit_iso_date_and_scalar_scope_are_required(self):
        for company, supplier, cutoff in ((["COMPANY"], "SUPPLIER", "2026-09-30"), ("COMPANY", ["SUPPLIER"], "2026-09-30"),
                                         (" COMPANY", "SUPPLIER", "2026-09-30"), ("COMPANY", "SUPPLIER", "2026-9-30"),
                                         ("COMPANY", "SUPPLIER", "")):
            with self.subTest(company=company, supplier=supplier, cutoff=cutoff), self.assertRaises(ValueError):
                self.module.query_payables(company, supplier, cutoff)
        self.report.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
