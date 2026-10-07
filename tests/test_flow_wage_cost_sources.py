"""Exact posted payroll attribution models; no native payroll or database writes."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Document(Record):
    environment = None

    def check_permission(self, permission):
        if (self.doctype, self.name) in self.environment.denied:
            raise PermissionError("native denied")

    def precision(self, field):
        return 2


class Meta:
    def __init__(self, env, kind):
        self.env, self.kind = env, kind

    def get_field(self, name):
        return self.env.metas[self.kind].get(name)

    def get_permlevel_access(self, permission_type, parenttype=None, user=None):
        return {0} if permission_type == "read" else set()


class TestFlowWageCostSources(unittest.TestCase):
    def setUp(self):
        self.data, self.metas, self.permitted = {}, {}, {}
        self.hidden, self.denied, self.omitted = {}, set(), set()
        self.list_hook, self.doc_hook = None, None
        self.frappe = types.ModuleType("frappe")
        self.frappe.session, self.frappe.conf = Record(user="ordinary-payroll-reader"), Record()
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = type("DoesNotExistError", (ValueError,), {})
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_meta = lambda kind: Meta(self, kind)
        self.frappe.db = Record(commit=Mock(), sql=Mock(), set_value=Mock())
        self.frappe.new_doc, self.frappe.delete_doc = Mock(), Mock()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda kind, **kwargs: self.permitted[kind] - self.hidden.get(kind, set())
        utils = types.ModuleType("frappe.utils")
        utils.flt = lambda value, precision=None: round(float(value), precision) if precision is not None else float(value)
        utils.getdate = lambda value: date.fromisoformat(str(value).split(" ")[0])
        scope = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils})
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("flow_wage_source_under_test", ROOT / "jifeng_erp/flow_wage_cost_sources.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        for kind, fields in self.module.FIELDS.items():
            self.schema(kind, fields)
        for kind, tables in self.module.TABLES.items():
            for field, child, fields in tables:
                self.schema(child, fields)
                self.metas[kind][field] = Record(fieldname=field, fieldtype="Table", options=child, permlevel=0)
        Document.environment = self
        self.add("Company", "C", default_currency="CNY")
        self.add(self.module.FLOW, "FLOW", company="C", item_code="FG")
        self.add("Work Order", "WO", company="C", production_item="FG", jf_flow_order="FLOW")
        self.child("Work Order", "WO", "operations", "OP", operation="Assembly")
        self.add("Job Card", "JC", company="C", work_order="WO", operation="Assembly", operation_id="OP", stock_uom="Nos", is_subcontracted=0)
        self.add("Salary Component", "Piece", type="Earning")
        self.add("Salary Component", "Basic", type="Earning")
        self.add("Account", "Expense", company="C", account_currency="CNY", root_type="Expense", is_group=0)
        self.add("Account", "Payable", company="C", account_currency="CNY", root_type="Liability", is_group=0)
        self.child("Salary Component", "Piece", "accounts", "MAP-P", company="C", account="Expense")
        self.child("Salary Component", "Basic", "accounts", "MAP-B", company="C", account="Expense")
        self.add(self.module.PAYROLL, "PAY", company="C", currency="CNY", exchange_rate=1,
            start_date="2026-10-01", end_date="2026-10-31", payroll_payable_account="Payable", salary_slip_based_on_timesheet=0)
        for index, amount in ((1, 8), (2, 12)):
            employee = f"EMP-{index}"
            self.add("Employee", employee, company="C")
            self.add(self.module.WAGE, f"W-{index}", company="C", flow_order="FLOW", employee=employee,
                salary_component="Piece", payroll_date="2026-10-08", work_order="WO", job_card="JC", operation="Assembly",
                stock_uom="Nos", quantity=index + 1, piece_amount=amount, time_amount=0, extra_amount=0, total_amount=amount)
            self.add(self.module.AS, f"AS-{index}", company="C", employee=employee, currency="CNY", salary_component="Piece",
                payroll_date="2026-10-08", type="Earning", amount=amount, ref_doctype=self.module.WAGE,
                ref_docname=f"W-{index}", is_recurring=0, disabled=0, overwrite_salary_structure_amount=0)
            self.add(self.module.SLIP, f"SS-{index}", company="C", employee=employee, currency="CNY", exchange_rate=1,
                start_date="2026-10-01", end_date="2026-10-31", payroll_entry="PAY", journal_entry="ACCRUAL", salary_slip_based_on_timesheet=0)
            self.earning(f"SS-{index}", f"B-{index}", "Basic", 1000)
            self.earning(f"SS-{index}", f"D-{index}", "Piece", amount, additional_salary=f"AS-{index}")
        self.add(self.module.JE, "ACCRUAL", company="C", voucher_type="Journal Entry", multi_currency=0,
            total_debit=2020, total_credit=2020)
        self.child(self.module.JE, "ACCRUAL", "accounts", "JE-E", account="Expense", account_currency="CNY",
            exchange_rate=1, debit=2020, credit=0, reference_type=None, reference_name=None)
        self.child(self.module.JE, "ACCRUAL", "accounts", "JE-P", account="Payable", account_currency="CNY",
            exchange_rate=1, debit=0, credit=2020, reference_type=self.module.PAYROLL, reference_name="PAY")
        self.add(self.module.GL, "GL-E", company="C", voucher_type=self.module.JE, voucher_no="ACCRUAL",
            account="Expense", account_currency="CNY", debit=2020, credit=0, is_cancelled=0)
        self.add(self.module.GL, "GL-P", company="C", voucher_type=self.module.JE, voucher_no="ACCRUAL",
            account="Payable", account_currency="CNY", debit=0, credit=2020, is_cancelled=0)

    def schema(self, kind, fields):
        self.permitted.setdefault(kind, set()).update(fields)
        self.metas.setdefault(kind, {}).update({field: Record(fieldname=field, fieldtype="Data", permlevel=0) for field in fields})

    def add(self, kind, name, **values):
        fields = self.module.FIELDS.get(kind, self.permitted.get(kind, set()))
        row = Record({key: None for key in fields} | {"doctype": kind, "name": name, "docstatus": 1,
            "modified": datetime(2026, 10, 8, 8)} | values)
        self.data[kind, name] = row
        return row

    def child(self, kind, name, field, child_name, **values):
        _, child, fields = next(table for table in self.module.TABLES[kind] if table[0] == field)
        return self.add(child, child_name, **({key: None for key in fields - self.module.IDENTITY} | {"parent": name, "parenttype": kind,
            "parentfield": field, "idx": 1, "docstatus": 1} | values))

    def earning(self, slip, name, component, amount, **values):
        return self.child(self.module.SLIP, slip, "earnings", name, salary_component=component, amount=amount,
            **({"additional_salary": None, "do_not_include_in_total": 0, "do_not_include_in_accounts": 0,
                "is_recurring_additional_salary": 0} | values))

    def get_all(self, kind, filters, fields, **kwargs):
        rows = []
        for (doctype, _), row in sorted(self.data.items()):
            if doctype != kind:
                continue
            if all(row.get(key) in value[1] if isinstance(value, list) and value[0] == "in"
                   else row.get(key) == value for key, value in filters.items()):
                rows.append(deepcopy(row) if fields == ["*"] else Record({field: row.get(field) for field in fields}))
        return rows

    def get_list(self, kind, filters, fields, **kwargs):
        if self.list_hook:
            self.list_hook(kind, filters)
        return [row for row in self.get_all(kind, filters, fields) if (kind, row.name) not in self.omitted]

    def get_doc(self, kind, name):
        if self.doc_hook:
            self.doc_hook(kind, name)
        if (kind, name) not in self.data:
            raise self.frappe.DoesNotExistError("missing")
        doc = Document(deepcopy(self.data[kind, name]))
        for field, childtype, _ in self.module.TABLES.get(kind, ()):
            doc[field] = [Document(deepcopy(row)) for (doctype, _), row in sorted(self.data.items())
                if doctype == childtype and row.parent == name and row.parenttype == kind and row.parentfield == field]
        return doc

    def result(self):
        return self.module.posted_flow_wage_costs("FLOW")

    def test_exact_twenty_not_company_monthly_2020_or_a_payment(self):
        self.add(self.module.JE, "PAYMENT", company="C", voucher_type="Bank Entry", total_debit=2020, total_credit=2020)
        result = self.result()
        self.assertEqual(result["total"], Decimal(20))
        self.assertEqual([row["amount"] for row in result["rows"]], [Decimal(8), Decimal(12)])
        self.assertEqual({row["journal_entry"] for row in result["rows"]}, {"ACCRUAL"})
        self.assertEqual({row["work_order"] for row in result["rows"]}, {"WO"})
        self.assertEqual({row["job_card"] for row in result["rows"]}, {"JC"})
        result["finish_guard"]()
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.sql.assert_not_called()
        self.frappe.db.set_value.assert_not_called()
        self.frappe.new_doc.assert_not_called()
        self.frappe.delete_doc.assert_not_called()

    def test_approval_additional_or_unaccrued_slip_is_not_actual_cost(self):
        original = deepcopy(self.data)
        for shape in ("wages_only", "draft_additional", "draft_slip", "no_accrual", "draft_accrual"):
            self.data = deepcopy(original)
            if shape == "wages_only":
                self.data = {key: row for key, row in self.data.items() if key[0] != self.module.AS}
                self.hidden[self.module.AS] = {"amount"}
            elif shape == "draft_additional":
                for index in (1, 2): self.data[self.module.AS, f"AS-{index}"].docstatus = 0
            elif shape == "draft_slip":
                for index in (1, 2): self.data[self.module.SLIP, f"SS-{index}"].docstatus = 0
            elif shape == "no_accrual":
                for index in (1, 2): self.data[self.module.SLIP, f"SS-{index}"].journal_entry = None
            else:
                self.data[self.module.JE, "ACCRUAL"].docstatus = 0
            with self.subTest(shape=shape): self.assertEqual(self.result()["total"], 0)
            self.hidden.clear()

    def test_cancelled_each_real_source_withdraws_cost_and_replacement_reappears_once(self):
        original = deepcopy(self.data)
        for kind, names in ((self.module.WAGE, ["W-1", "W-2"]), (self.module.AS, ["AS-1", "AS-2"]),
                (self.module.SLIP, ["SS-1", "SS-2"]), (self.module.PAYROLL, ["PAY"]), (self.module.JE, ["ACCRUAL"])):
            self.data = deepcopy(original)
            for name in names: self.data[kind, name].docstatus = 2
            with self.subTest(kind=kind): self.assertEqual(self.result()["total"], 0)
        self.data = deepcopy(original)
        self.data[self.module.AS, "OLD-AS"] = Record(self.data[self.module.AS, "AS-1"], name="OLD-AS", docstatus=2)
        self.assertEqual(self.result()["total"], 20)

    def test_cancelled_one_slip_leaves_exact_other_share_when_accrual_is_recreated(self):
        self.data[self.module.WAGE, "W-2"].docstatus = 2
        self.data[self.module.AS, "AS-2"].docstatus = 2
        self.data[self.module.SLIP, "SS-2"].docstatus = 2
        for kind, name, field in ((self.module.JE, "ACCRUAL", "total_debit"), (self.module.JE, "ACCRUAL", "total_credit"),
                ("Journal Entry Account", "JE-E", "debit"), ("Journal Entry Account", "JE-P", "credit"),
                (self.module.GL, "GL-E", "debit"), (self.module.GL, "GL-P", "credit")):
            self.data[kind, name][field] = 1008
        self.assertEqual(self.result()["total"], 8)

    def test_same_company_other_flow_earning_is_not_squeezed_into_this_flow(self):
        self.data[self.module.WAGE, "W-2"].flow_order = "OTHER-FLOW"
        self.assertEqual(self.result()["total"], 8)

    def test_manual_explicit_flow_wages_keep_real_accrual_without_inventing_a_job_card(self):
        for index in (1, 2):
            self.data[self.module.WAGE, f"W-{index}"].work_order = None
            self.data[self.module.WAGE, f"W-{index}"].job_card = None
        result = self.result()
        self.assertEqual(result["total"], 20)
        self.assertTrue(all(row["work_order"] is None and row["job_card"] is None for row in result["rows"]))
        self.data[self.module.WAGE, "W-1"].company = "OTHER"
        with self.assertRaisesRegex(ValueError, "有效计提"): self.result()

    def test_manual_with_explicit_work_order_keeps_company_and_flow_proof(self):
        for index in (1, 2): self.data[self.module.WAGE, f"W-{index}"].job_card = None
        self.assertEqual(self.result()["total"], 20)
        self.data["Work Order", "WO"].company = "OTHER"
        with self.assertRaisesRegex(ValueError, "有效计提"): self.result()

    def test_native_accountable_non_gross_component_and_zero_component(self):
        self.data["Salary Detail", "D-1"].do_not_include_in_total = 1
        self.assertEqual(self.result()["total"], 20)
        self.earning("SS-1", "ZERO", "Basic", 0)
        self.assertEqual(self.result()["total"], 20)
        self.data["Salary Detail", "D-1"].do_not_include_in_accounts = 1
        with self.assertRaisesRegex(ValueError, "有效计提"): self.result()

    def test_native_tax_deduction_reconciles_payable_without_reducing_attributed_gross_wage(self):
        self.add("Salary Component", "Tax", type="Deduction")
        self.add("Account", "Tax-Payable", company="C", account_currency="CNY", root_type="Liability", is_group=0)
        self.child("Salary Component", "Tax", "accounts", "MAP-T", company="C", account="Tax-Payable")
        self.child(self.module.SLIP, "SS-1", "deductions", "TAX", salary_component="Tax", amount=100,
            additional_salary=None, do_not_include_in_total=0, do_not_include_in_accounts=0, is_recurring_additional_salary=0)
        self.data["Journal Entry Account", "JE-P"].credit = 1920
        self.data[self.module.GL, "GL-P"].credit = 1920
        self.child(self.module.JE, "ACCRUAL", "accounts", "JE-T", account="Tax-Payable", account_currency="CNY",
            exchange_rate=1, debit=0, credit=100, reference_type=None, reference_name=None)
        self.add(self.module.GL, "GL-T", company="C", voucher_type=self.module.JE, voucher_no="ACCRUAL",
            account="Tax-Payable", account_currency="CNY", debit=0, credit=100, is_cancelled=0)
        self.assertEqual(self.result()["total"], 20)

    def test_late_native_list_hook_cannot_revoke_previously_checked_slip_read(self):
        result = self.result()
        def revoke(kind, filters):
            if kind == self.module.GL:
                self.denied.add((self.module.SLIP, "SS-1"))
        self.list_hook = revoke
        with self.assertRaises(PermissionError): result["finish_guard"]()

    def test_submitted_je_without_effective_gl_or_wrong_expense_group_is_rejected(self):
        original = deepcopy(self.data)
        for change in ("no_gl", "cancelled_gl", "wrong_gl", "wrong_je", "asset_component", "wrong_reference"):
            self.data = deepcopy(original)
            if change == "no_gl": self.data.pop((self.module.GL, "GL-E"))
            elif change == "cancelled_gl": self.data[self.module.GL, "GL-E"].is_cancelled = 1
            elif change == "wrong_gl": self.data[self.module.GL, "GL-E"].debit = 2000
            elif change == "wrong_je": self.data["Journal Entry Account", "JE-E"].debit = 2000
            elif change == "asset_component": self.data["Account", "Expense"].root_type = "Asset"
            else: self.data["Journal Entry Account", "JE-P"].reference_name = "OTHER-PAYROLL"
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "有效计提"): self.result()

    def test_precise_identity_currency_period_and_operation_are_required(self):
        changes = [(self.module.AS, "AS-1", "employee", "OTHER"), (self.module.AS, "AS-1", "amount", 7),
            ("Salary Detail", "D-1", "amount", 7), (self.module.SLIP, "SS-1", "company", "OTHER"),
            (self.module.PAYROLL, "PAY", "currency", "USD"), (self.module.PAYROLL, "PAY", "exchange_rate", 7),
            (self.module.SLIP, "SS-1", "start_date", "2026-10-09"), ("Work Order", "WO", "jf_flow_order", "OTHER"),
            ("Job Card", "JC", "operation_id", "OTHER"), ("Job Card", "JC", "stock_uom", "Kg"),
            ("Job Card", "JC", "is_subcontracted", 1)]
        original = deepcopy(self.data)
        for kind, name, field, value in changes:
            self.data = deepcopy(original)
            self.data[kind, name][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "有效计提"): self.result()

    def test_duplicate_live_source_or_salary_detail_cannot_double_count(self):
        self.data[self.module.AS, "DUP-AS"] = Record(self.data[self.module.AS, "AS-1"], name="DUP-AS")
        with self.assertRaises(ValueError): self.result()
        self.data.pop((self.module.AS, "DUP-AS"))
        self.earning("SS-2", "DUP-DETAIL", "Piece", 8, additional_salary="AS-1")
        with self.assertRaises(ValueError): self.result()

    def test_missing_document_field_mask_table_or_filtered_source_fails_without_partial_total(self):
        for kind, name, field in ((self.module.WAGE, "W-1", "total_amount"), (self.module.AS, "AS-1", "amount"),
                (self.module.SLIP, "SS-1", "journal_entry"), (self.module.PAYROLL, "PAY", "currency"),
                (self.module.JE, "ACCRUAL", "total_debit"), (self.module.GL, "GL-E", "debit")):
            with self.subTest(kind=kind):
                self.denied.add((kind, name))
                with self.assertRaises(PermissionError): self.result()
                self.denied.clear()
                self.hidden[kind] = {field}
                with self.assertRaises(PermissionError): self.result()
                self.hidden.clear()
                self.metas[kind][field].mask = 1
                with self.assertRaises(PermissionError): self.result()
                self.metas[kind][field].mask = 0
                self.omitted.add((kind, name))
                with self.assertRaises(PermissionError): self.result()
                self.omitted.clear()
        self.metas[self.module.SLIP]["earnings"].permlevel = 1
        with self.assertRaises(PermissionError): self.result()

    def test_finish_guard_rejects_same_total_source_or_permission_changes(self):
        original = deepcopy(self.data)
        for change in ("same_total", "document_permission", "field_permission", "new_empty_range", "raw_role"):
            self.data = deepcopy(original)
            result = self.result()
            if change == "same_total":
                self.data[self.module.WAGE, "W-1"].extra_amount = 8
                self.data[self.module.WAGE, "W-1"].piece_amount = 0
            elif change == "document_permission": self.denied.add((self.module.SLIP, "SS-1"))
            elif change == "field_permission": self.hidden[self.module.GL] = {"debit"}
            elif change == "new_empty_range":
                # A future submitted earning appears in a previously empty deduction table.
                self.child(self.module.SLIP, "SS-1", "deductions", "NEW", salary_component="Basic", amount=0)
            else: self.add("Has Role", "NEW-ROLE", parent="ordinary-payroll-reader", parenttype="User", role="Payroll")
            with self.subTest(change=change), self.assertRaises(PermissionError): result["finish_guard"]()
            self.denied.clear()
            self.hidden.clear()

    def test_empty_discovery_range_is_guarded_without_reading_private_salary_amounts(self):
        self.data = {key: row for key, row in self.data.items() if key[0] != self.module.AS}
        self.hidden[self.module.AS] = {"amount"}
        result = self.result()
        self.assertEqual(result["total"], 0)
        self.add(self.module.AS, "NEW", ref_doctype=self.module.WAGE, ref_docname="W-1", docstatus=0)
        with self.assertRaises(PermissionError): result["finish_guard"]()

    def test_guest_replica_and_invalid_finite_money_are_rejected(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError): self.result()
        self.frappe.session.user = "ordinary-payroll-reader"
        self.frappe.conf.read_from_replica = True
        with self.assertRaisesRegex(ValueError, "主库"): self.result()
        self.frappe.conf.clear()
        for value in (True, "NaN", "Infinity", -1):
            self.data[self.module.AS, "AS-1"].amount = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.result()


if __name__ == "__main__":
    unittest.main()
