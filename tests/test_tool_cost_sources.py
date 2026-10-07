"""Business contracts for explicit tool costs, without a database or accounts writes."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
NATIVE_TABLES = {
    "Journal Entry": {"accounts": "Journal Entry Account"},
    "Payment Entry": {"references": "Payment Entry Reference", "deductions": "Payment Entry Deduction",
                      "taxes": "Advance Taxes and Charges"},
    "Jifeng Tool Cost Allocation": {"items": "Jifeng Tool Cost Allocation Item"},
}
EMPTY_NATIVE_FIELDS = {
    "Payment Entry Reference": set("reference_doctype reference_name allocated_amount exchange_rate reconcile_effect_on".split()),
    "Payment Ledger Entry": set("company posting_date account account_type account_currency party_type party voucher_type voucher_no "
        "against_voucher_type against_voucher_no voucher_detail_no finance_book amount amount_in_account_currency delinked".split()),
}


class Record(dict):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def set(self, name, value):
        self[name] = value


class Meta:
    def __init__(self, test, doctype):
        self.test, self.doctype = test, doctype

    def get_field(self, name):
        if name in NATIVE_TABLES.get(self.doctype, {}):
            return Record(fieldtype="Table", options=NATIVE_TABLES[self.doctype][name],
                          permlevel=self.test.levels.get((self.doctype, name), 0),
                          mask=(self.doctype, name) in self.test.masked)
        return Record(fieldtype="Data", permlevel=self.test.levels.get((self.doctype, name), 0),
                      mask=(self.doctype, name) in self.test.masked)

    def get_permlevel_access(self, permission_type, **kwargs):
        return [0] if permission_type != "mask" else []


class Doc(Record):
    def __init__(self, test, values):
        super().__init__(deepcopy(dict(values)))
        self.__dict__["test"] = test

    @property
    def meta(self):
        return Meta(self.__dict__["test"], self.doctype)

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("PRIVATE " + str(self.name))

    def has_permission(self, permission, **kwargs):
        test = self.__dict__["test"]
        if (self.doctype, self.name, permission) in test.denied or (self.doctype, permission) in test.denied:
            return False
        if self.get("company") in test.blocked_companies:
            return False
        if test.hooks_enabled and self.doctype == "Jifeng Tool Cost Allocation":
            return test.module.tool_cost_allocation_has_permission(self, permission)
        if test.hooks_enabled and self.doctype == "Jifeng Tool Cost Allocation Item":
            return test.module.tool_cost_allocation_item_has_permission(self, permission)
        return True

    def is_new(self):
        return not self.name or (self.doctype, self.name) not in self.__dict__["test"].docs

    def get_doc_before_save(self):
        return self.__dict__.get("before")

    def precision(self, field):
        return 2

    def insert(self):
        test = self.__dict__["test"]
        test.module.before_insert_allocation(self)
        self.name = "ALLOC-" + str(test.sequence)
        test.sequence += 1
        self.modified, self.docstatus = "2026-10-03 10:00:00", 0
        self.__dict__["new"] = True
        test.bind_rows(self)
        test.module.validate_allocation(self)
        test.persist(self)
        return self

    def cancel(self):
        test = self.__dict__["test"]
        self.__dict__["before"] = test.doc(self.doctype, self.name)
        self.docstatus = 2
        test.module.validate_allocation(self)
        test.module.before_cancel_allocation(self)
        self.modified = "2026-10-03 11:00:00"
        test.bind_rows(self)
        test.persist(self)


class ToolCosts(unittest.TestCase):
    def setUp(self):
        self.docs, self.denied, self.hidden, self.masked, self.levels = {}, set(), set(), set(), {}
        self.blocked_companies, self.sequence, self.hooks_enabled = set(), 1, False
        self.calls, self.raw_mutation = [], None
        self.schema = {}
        for folder, name in (("jifeng_tool_cost_allocation", "Jifeng Tool Cost Allocation"),
                             ("jifeng_tool_cost_allocation_item", "Jifeng Tool Cost Allocation Item")):
            data = json.loads((ROOT / "jifeng_erp/jifeng_erp/doctype" / folder / (folder + ".json")).read_text(encoding="utf-8"))
            self.schema[name] = {field["fieldname"] for field in data["fields"] if field["fieldtype"] != "Table"}
        frappe = types.ModuleType("frappe")
        frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError = PermissionError, LookupError, ValueError
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.whitelist = lambda *args, **kwargs: (lambda function: function)
        frappe.session, frappe.conf = Record(user="finance"), Record(read_from_replica=False)
        frappe.share = types.SimpleNamespace(get_shared=lambda *args, **kwargs: [])
        frappe.get_meta = lambda doctype: Meta(self, doctype)
        frappe.get_doc = self.get_doc
        frappe.has_permission = lambda doctype, permission, doc=None, **kwargs: (
            doc.has_permission(permission) if doc is not None else (doctype, permission) not in self.denied)
        self.hooks = {
            "has_permission": {"Jifeng Tool Cost Allocation": ["jifeng_erp.tool_cost_sources.tool_cost_allocation_has_permission"],
                "Jifeng Tool Cost Allocation Item": ["jifeng_erp.tool_cost_sources.tool_cost_allocation_item_has_permission"]},
            "permission_query_conditions": {"Jifeng Tool Cost Allocation": ["jifeng_erp.tool_cost_sources.tool_cost_allocation_query_conditions"],
                "Jifeng Tool Cost Allocation Item": ["jifeng_erp.tool_cost_sources.tool_cost_allocation_item_query_conditions"]},
            "doc_events": {"DocShare": {"before_validate": ["jifeng_erp.tool_cost_sources.prevent_tool_cost_docshare"]}},
        }
        frappe.get_hooks = lambda category: self.hooks[category]
        frappe.get_all, frappe.get_list = self.get_all, self.get_list
        frappe.db = types.SimpleNamespace(get_value=self.get_value, escape=lambda value: "'" + value.replace("'", "''") + "'")
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.fields
        atomic = types.ModuleType("jifeng_erp.personnel_separation")
        atomic._atomic = lambda function: function
        versions = types.ModuleType("jifeng_erp.archive_versions")
        versions.same_version = lambda first, second: str(first).replace("T", " ") == str(second).replace("T", " ")
        self.patch = patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model,
            "jifeng_erp.personnel_separation": atomic, "jifeng_erp.archive_versions": versions})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        spec = importlib.util.spec_from_file_location("tool_cost_test", ROOT / "jifeng_erp/tool_cost_sources.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.frappe = frappe
        self.add("Company", "C", default_currency="CNY")
        self.add("Currency", "CNY")
        self.add("Account", "EXP", company="C", root_type="Expense", account_type="Expense Account", account_currency="CNY", is_group=0)
        self.add("Account", "PAY", company="C", root_type="Liability", account_type="Payable", account_currency="CNY", is_group=0)
        self.add("Account", "CASH", company="C", root_type="Asset", account_type="Cash", account_currency="CNY", is_group=0)
        self.add("Cost Center", "CC", company="C", is_group=0)
        self.add("Supplier", "SUP")
        self.add("Jifeng Mold", "M", company="C", mold_no="工具一", amount=9999)
        self.add("Jifeng Plate", "P", company="C", plate_no="工具二", reference_cost=8888)
        self.expense()

    def add(self, doctype, name, **values):
        doc = Doc(self, {"doctype": doctype, "name": name, "modified": "2026-10-03 09:00:00", "docstatus": 0, **values})
        self.docs[doctype, name] = doc
        return doc

    def bind_rows(self, parent):
        for table, doctype in NATIVE_TABLES.get(parent.doctype, {}).items():
            rows = []
            for index, value in enumerate(parent.get(table) or [], 1):
                row = Record(value)
                row.update(doctype=doctype, name=row.get("name") or parent.name + "-" + table + "-" + str(index),
                    modified=parent.modified, docstatus=parent.docstatus, parent=parent.name,
                    parenttype=parent.doctype, parentfield=table, idx=index)
                rows.append(row)
            parent[table] = rows

    def persist(self, doc):
        for existing in self.docs.values():
            if existing.doctype == doc.doctype and existing.name != doc.name:
                for field in ("request_id", "active_source_key"):
                    if doc.get(field) and doc.get(field) == existing.get(field):
                        raise ValueError("unique key")
        self.docs[doc.doctype, doc.name] = doc
        for table, doctype in NATIVE_TABLES.get(doc.doctype, {}).items():
            for key in [key for key, value in self.docs.items() if key[0] == doctype and value.get("parent") == doc.name]:
                del self.docs[key]
            for row in doc.get(table) or []:
                self.add(doctype, row.name, **{key: value for key, value in row.items() if key not in {"doctype", "name"}})

    def doc(self, doctype, name):
        if (doctype, name) not in self.docs:
            raise LookupError("private missing " + str(name))
        return Doc(self, self.docs[doctype, name])

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            values = {key: None for key in self.schema[doctype["doctype"]]}
            values.update(doctype)
            values.update(name=None, docstatus=0)
            return Doc(self, values)
        self.calls.append((doctype, name, kwargs))
        return self.doc(doctype, name)

    def fields(self, doctype, permission_type, **kwargs):
        fields = {"name", "modified", "docstatus", "parent", "parenttype", "parentfield", "idx"} | self.schema.get(doctype, set())
        fields |= EMPTY_NATIVE_FIELDS.get(doctype, set())
        for (kind, _), doc in self.docs.items():
            if kind == doctype:
                fields.update(doc)
        # Real Frappe explicitly excludes every Table from value projections.
        fields -= set(NATIVE_TABLES.get(doctype, {}))
        if doctype in {"Payment Entry Deduction", "Advance Taxes and Charges"}:
            fields |= {"account", "amount", "account_head", "tax_amount"}
        fields -= {field for kind, field in self.hidden if kind == doctype}
        return fields

    def matches(self, doc, filters):
        for field, value in (filters or {}).items():
            if isinstance(value, list) and value[0] == "in":
                if doc.get(field) not in value[1]:
                    return False
            elif doc.get(field) != value:
                return False
        return True

    def get_all(self, doctype, filters=None, fields=None, **kwargs):
        if self.raw_mutation:
            self.raw_mutation(doctype, filters)
        rows = [doc for (kind, _), doc in self.docs.items() if kind == doctype and self.matches(doc, filters)]
        rows.sort(key=lambda row: row.name)
        return [Record({field: row.get(field) for field in fields}) if fields != ["*"] else Record(row) for row in rows]

    def get_list(self, doctype, filters=None, fields=None, parent_doctype=None, **kwargs):
        self.calls.append(("list", doctype, kwargs))
        if (parent_doctype or doctype, "read") in self.denied:
            raise PermissionError("native read")
        raw = self.get_all(doctype, filters, fields)
        rows = [row for row in raw if self.docs[doctype, row.name].has_permission("read")]
        start = kwargs.get("limit_start", 0)
        return rows[start:start + kwargs.get("limit_page_length", 200)]

    def get_value(self, doctype, filters, field):
        rows = self.get_all(doctype, filters, [field])
        return rows[0].get(field) if rows else None

    def expense(self, *, cash=False):
        self.add("Jifeng Expense Voucher", "E", company="C", docstatus=1, posting_date="2026-10-03",
            payment_method="现金" if cash else "赊账", expense_account="EXP", payment_account="CASH" if cash else None,
            payable_account=None if cash else "PAY", supplier=None if cash else "SUP", cost_center="CC", amount=120,
            journal_entry="J", private_text="NEVER_EXPOSE")
        common = {"account_currency": "CNY", "exchange_rate": 1, "reference_type": None, "reference_name": None}
        journal = self.add("Journal Entry", "J", company="C", docstatus=1, posting_date="2026-10-03",
            voucher_type="Journal Entry", jf_expense_voucher="E", multi_currency=0, finance_book=None, accounts=[
                {**common, "account": "EXP", "party_type": None, "party": None, "cost_center": "CC",
                 "debit": 120, "credit": 0, "debit_in_account_currency": 120, "credit_in_account_currency": 0},
                {**common, "account": "CASH" if cash else "PAY", "party_type": None if cash else "Supplier",
                 "party": None if cash else "SUP", "cost_center": None, "debit": 0, "credit": 120,
                 "debit_in_account_currency": 0, "credit_in_account_currency": 120}])
        self.bind_rows(journal)
        self.persist(journal)
        self.gl("JG1", "Journal Entry", "J", "EXP", 120, 0)
        self.gl("JG2", "Journal Entry", "J", "CASH" if cash else "PAY", 0, 120, party=None if cash else "SUP")
        if not cash:
            self.ple("JL", "Journal Entry", "J", 120)

    def gl(self, name, voucher_type, voucher_no, account, debit, credit, *, party=None, reference=None):
        return self.add("GL Entry", name, company="C", posting_date="2026-10-03", account=account, account_currency="CNY",
            party_type="Supplier" if party else None, party=party, cost_center="CC" if account == "EXP" else None,
            voucher_type=voucher_type, voucher_no=voucher_no, against_voucher_type="Journal Entry" if reference else None,
            against_voucher=reference, voucher_detail_no=None, finance_book=None, debit=debit, credit=credit,
            debit_in_account_currency=debit, credit_in_account_currency=credit, is_cancelled=0)

    def ple(self, name, voucher_type, voucher_no, amount):
        return self.add("Payment Ledger Entry", name, company="C", posting_date="2026-10-03", account="PAY",
            account_type="Payable", account_currency="CNY", party_type="Supplier", party="SUP", voucher_type=voucher_type,
            voucher_no=voucher_no, against_voucher_type="Journal Entry", against_voucher_no="J", voucher_detail_no=None,
            finance_book=None, amount=amount, amount_in_account_currency=amount, delinked=0)

    def payment(self, name, amount, *, cancelled=False):
        amount = Decimal(str(amount))
        doc = self.add("Payment Entry", name, company="C", docstatus=2 if cancelled else 1, posting_date="2026-10-03",
            payment_type="Pay", party_type="Supplier", party="SUP", paid_from="CASH", paid_to="PAY",
            paid_from_account_currency="CNY", paid_to_account_currency="CNY", paid_amount=amount, received_amount=amount,
            base_paid_amount=amount, base_received_amount=amount, source_exchange_rate=1, target_exchange_rate=1,
            total_allocated_amount=amount, unallocated_amount=0, difference_amount=0,
            book_advance_payments_in_separate_party_account=0, deductions=[], taxes=[],
            bank_account_no="PRIVATE_BANK", references=[{"reference_doctype": "Journal Entry", "reference_name": "J",
                "allocated_amount": amount, "exchange_rate": 1, "reconcile_effect_on": None}])
        self.bind_rows(doc)
        self.persist(doc)
        self.gl(name + "BANK", "Payment Entry", name, "CASH", 0, amount)
        self.gl(name + "PAY", "Payment Entry", name, "PAY", amount, 0, party="SUP", reference="J")
        self.ple(name + "PLE", "Payment Entry", name, -amount)
        if cancelled:
            self.gl(name + "RBANK", "Payment Entry", name, "CASH", amount, 0)
            self.gl(name + "RPAY", "Payment Entry", name, "PAY", 0, amount, party="SUP", reference="J")
            self.ple(name + "RPLE", "Payment Entry", name, amount)
        return doc

    def create(self, amounts=("70", "50"), *, request_id=None):
        return self.module.create_tool_cost_allocation({"company": "C", "expense_voucher": "E",
            "expense_version": "2026-10-03 09:00:00", "reason": "明确模具70、版种50"}, [
                {"record_type": kind, "name": name, "version": "2026-10-03 09:00:00", "amount": amount}
                for (kind, name), amount in zip((("mold", "M"), ("plate", "P")), amounts)], request_id or str(uuid4()))

    def submit(self, name):
        doc = self.doc("Jifeng Tool Cost Allocation", name)
        doc.__dict__["before"] = self.doc(doc.doctype, name)
        doc.docstatus = 1
        self.module.validate_allocation(doc)
        self.module.before_submit_allocation(doc)
        self.bind_rows(doc)
        self.persist(doc)
        return doc

    def test_explicit_120_shares_70_50_and_no_reference_price_accounting(self):
        receipt = self.create()
        self.submit(receipt["name"])
        view = self.module.get_tool_cost_allocation(receipt["name"])
        self.assertEqual([(row["historical_amount"], row["paid_amount"], row["unpaid_amount"]) for row in view["items"]],
                         [("70", "0", "70"), ("50", "0", "50")])
        self.assertEqual(view["source"]["incurred_amount"], "120")
        self.assertNotIn("9999", json.dumps(view))
        self.assertEqual({key[0] for key in self.docs if key[0] in {"Journal Entry", "Payment Entry"}}, {"Journal Entry"})

    def test_native_form_json_date_roundtrip_accepts_same_value_and_rejects_edit(self):
        receipt = self.create()
        stored = self.docs["Jifeng Tool Cost Allocation", receipt["name"]]
        stored.source_date = date(2026, 10, 3)
        incoming = self.doc(stored.doctype, stored.name)
        incoming.source_date = "2026-10-03"
        incoming.docstatus = 1
        incoming.__dict__["before"] = self.doc(stored.doctype, stored.name)
        self.module.validate_allocation(incoming)
        self.module.before_submit_allocation(incoming)
        self.assertTrue(incoming.active_source_key)
        for altered in ("2026-10-04", "2026-10-03T00:00:00", None):
            with self.subTest(altered=altered):
                changed = self.doc(stored.doctype, stored.name)
                changed.source_date, changed.docstatus = altered, 1
                changed.__dict__["before"] = self.doc(stored.doctype, stored.name)
                with self.assertRaisesRegex(ValueError, "不能直接修改"):
                    self.module.validate_allocation(changed)

    def test_operation_hints_follow_native_role_rights_and_never_grant_writes(self):
        receipt = self.create()
        draft = self.module.get_tool_cost_allocation(receipt["name"])
        self.assertFalse(draft["can_cancel"])
        self.submit(receipt["name"])
        self.denied.update({("Jifeng Tool Cost Allocation", "create"),
                            ("Jifeng Tool Cost Allocation", "cancel")})
        view = self.module.get_tool_costs("C", "mold", "M")
        self.assertFalse(view["can_create"])
        self.assertFalse(view["allocations"][0]["can_cancel"])
        with self.assertRaises(PermissionError):
            self.module.cancel_tool_cost_allocation(receipt["name"], receipt["version"], "明确撤销")
        with self.assertRaises(PermissionError):
            self.create()
        self.denied.clear()
        self.assertTrue(self.module.get_tool_costs("C", "mold", "M")["can_create"])
        self.assertTrue(self.module.get_tool_cost_allocation(receipt["name"])["can_cancel"])

    def test_native_numeric_wrappers_compare_exactly_without_repricing(self):
        original = self.frappe.get_meta
        def meta(doctype):
            value = original(doctype)
            get_field = value.get_field
            def field(name):
                result = get_field(name)
                if (doctype, name) in {("Jifeng Expense Voucher", "amount"),
                                      ("Journal Entry Account", "debit"), ("GL Entry", "debit")}:
                    result.fieldtype = "Currency"
                return result
            value.get_field = field
            return value
        self.frappe.get_meta = meta
        self.assertEqual(self.module._cell("Jifeng Expense Voucher", "amount", Decimal("120.000")),
                         self.module._cell("Jifeng Expense Voucher", "amount", 120.0))
        self.assertEqual(self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})["paid_amount"], "0")
        self.assertEqual(self.module._text(Decimal("0.000")), "0")

    def test_partial40_unknown_per_tool_then_exact_40_plus80_fully_paid(self):
        receipt = self.create()
        self.submit(receipt["name"])
        self.payment("P40", 40)
        view = self.module.get_tool_cost_allocation(receipt["name"])
        self.assertEqual((view["source"]["paid_amount"], view["source"]["unpaid_amount"]), ("40", "80"))
        self.assertTrue(all(row["paid_amount"] is None and row["unpaid_amount"] is None for row in view["items"]))
        self.assertTrue(all(row["payment_state"] == "partial_payment_tool_unknown" for row in view["items"]))
        self.payment("P80", 80)
        view = self.module.get_tool_cost_allocation(receipt["name"])
        self.assertEqual([(row["paid_amount"], row["unpaid_amount"]) for row in view["items"]], [("70", "0"), ("50", "0")])
        self.assertNotIn("PRIVATE_BANK", json.dumps(view))

    def test_cash_payment_proven_by_native_cash_gl(self):
        del self.docs["Payment Ledger Entry", "JL"]
        self.expense(cash=True)
        source = self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})
        self.assertEqual((source["paid_amount"], source["unpaid_amount"], source["payment_basis"]), ("120", "0", "native_cash_bank_gl"))

    def test_cancelled_payment_reversals_do_not_count_as_paid(self):
        self.payment("P40", 40, cancelled=True)
        source = self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})
        self.assertEqual((source["paid_amount"], source["unpaid_amount"]), ("0", "120"))
        self.assertEqual(source["payments"][0]["docstatus"], 2)

    def test_docstatus_does_not_replace_a_missing_ledger(self):
        del self.docs["GL Entry", "JG1"]
        with self.assertRaises(ValueError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})

    def test_hidden_payment_rejects_whole_result_without_identity(self):
        self.payment("PRIVATE_PAYMENT", 40)
        self.denied.add(("Payment Entry", "PRIVATE_PAYMENT", "read"))
        with self.assertRaises(PermissionError) as error:
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})
        self.assertNotIn("PRIVATE_PAYMENT", str(error.exception))

    def test_income_masks_and_parent_table_masks_are_not_value_projection_shortcuts(self):
        for doctype, field in (("Jifeng Expense Voucher", "amount"), ("GL Entry", "debit"),
                              ("Journal Entry Account", "credit_in_account_currency"), ("Journal Entry", "accounts")):
            with self.subTest(doctype=doctype, field=field):
                self.masked = {(doctype, field)}
                with self.assertRaises(PermissionError):
                    self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})
        self.masked = set()
        self.assertNotIn("accounts", self.fields("Journal Entry", "read"))
        self.assertEqual(self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})["unpaid_amount"], "120")

    def test_table_permlevel_and_child_parent_corruption_reject(self):
        self.levels["Journal Entry", "accounts"] = 1
        with self.assertRaises(PermissionError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})
        self.levels.clear()
        row = self.docs["Journal Entry Account", "J-accounts-1"]
        row.parentfield = "unrelated"
        with self.assertRaises(ValueError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})

    def test_company_up_and_currency_scope_reject(self):
        self.blocked_companies.add("C")
        with self.assertRaises(PermissionError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})
        self.blocked_companies.clear()
        self.docs["GL Entry", "JG1"].account_currency = "USD"
        with self.assertRaises(ValueError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})

    def test_partial_excess_negative_precision_and_duplicate_tools_reject(self):
        for amounts in (("70", "49"), ("70", "51"), ("-1", "121"), ("70.001", "49.999")):
            with self.subTest(amounts=amounts), self.assertRaises(ValueError):
                self.create(amounts)
        document = self.get_doc({"doctype": "Jifeng Tool Cost Allocation", "company": "C", "expense_voucher": "E", "reason": "明确"})
        document["items"] = [Record(tool_type="模具", tool_name="M", tool_doctype="Jifeng Mold", amount="60") for _ in range(2)]
        with self.assertRaises(ValueError):
            document.insert()

    def test_whole_source_unique_and_cancellation_keeps_history_receipt(self):
        first, second = self.create(), self.create()
        doc = self.submit(first["name"])
        with self.assertRaises(ValueError):
            self.submit(second["name"])
        result = self.module.cancel_tool_cost_allocation(doc.name, doc.modified, "归属录错，另行核对")
        self.assertEqual(result["docstatus"], 2)
        self.assertEqual(self.module.resolve_tool_cost_creation("C", first["request_id"])["name"], first["name"])
        self.assertIsNone(self.docs[doc.doctype, doc.name].active_source_key)
        self.submit(second["name"])
        self.assertEqual(self.docs["Jifeng Expense Voucher", "E"].docstatus, 1)

    def test_native_link_currency_default_is_replaced_by_exact_expense_currency(self):
        document = self.get_doc({"doctype": "Jifeng Tool Cost Allocation", "company": "C",
            "expense_voucher": "E", "reason": "明确", "currency": "USD", "items": [
                {"tool_type": "模具", "tool_name": "M", "tool_doctype": "Jifeng Mold", "amount": "70"},
                {"tool_type": "版种", "tool_name": "P", "tool_doctype": "Jifeng Plate", "amount": "50"}]})
        document.insert()
        self.assertEqual(document.currency, "CNY")
        self.assertEqual(document.source_amount, "120")

    def test_native_child_documents_need_no_dictionary_subscription(self):
        values = {"tool_type": "模具", "tool_name": "M", "tool_doctype": "Jifeng Mold", "amount": "120"}
        native_child = types.SimpleNamespace(get=values.get)
        result = self.module._items(self.module._Read("C"), [native_child])
        self.assertEqual(result[0]["tool_type"], "模具")
        self.assertEqual(result[0]["amount"], "120")

    def test_cleanup_failure_remains_unknown_even_when_original_is_permission_error(self):
        calls = []
        def reject(uncertain):
            calls.append(uncertain)
            error = PermissionError("拒绝")
            if uncertain:
                error.add_note("离职局部回滚清理失败（database_rollback:RuntimeError），调用方须完整回滚并核对")
            raise error
        action = self.module._tool_atomic(reject)
        with self.assertRaises(PermissionError):
            action(False)
        with self.assertRaises(self.module.ToolCostWriteUncertain):
            action(True)
        self.assertEqual(calls, [False, True])

    def test_missing_native_share_guard_fails_closed(self):
        del self.hooks["doc_events"]["DocShare"]
        with self.assertRaises(PermissionError):
            self.module.get_tool_costs("C", "mold", "M")

    def test_creation_retry_is_receipt_not_a_second_insert(self):
        request_id = str(uuid4())
        first, second = self.create(request_id=request_id), self.create(request_id=request_id)
        self.assertEqual(first, second)
        self.assertEqual(len([key for key in self.docs if key[0] == "Jifeng Tool Cost Allocation"]), 1)
        with self.assertRaises(ValueError):
            self.create(("71", "49"), request_id=request_id)

    def test_tool_and_expense_version_changes_before_submit_reject(self):
        receipt = self.create()
        self.docs["Jifeng Mold", "M"].modified = "2026-10-03 09:30:00"
        with self.assertRaises(ValueError):
            self.submit(receipt["name"])
        self.docs["Jifeng Mold", "M"].modified = "2026-10-03 09:00:00"
        self.docs["Jifeng Expense Voucher", "E"].modified = "2026-10-03 09:30:00"
        with self.assertRaises(ValueError):
            self.submit(receipt["name"])

    def test_native_noncash_journal_offset_is_not_payment(self):
        self.ple("OFFSET", "Journal Entry", "OTHERJE", -40)
        with self.assertRaises(ValueError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})

    def test_payment_reference_without_native_gl_or_ple_rejects(self):
        self.payment("P40", 40)
        del self.docs["Payment Ledger Entry", "P40PLE"]
        with self.assertRaises(ValueError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})

    def test_request_local_source_change_cannot_return_old_total(self):
        calls = [0]
        def mutate(doctype, filters):
            if doctype == "Company" and filters == {"name": "C"}:
                calls[0] += 1
                if calls[0] == 3:
                    self.docs["GL Entry", "JG1"].debit = 121
        self.raw_mutation = mutate
        with self.assertRaises(ValueError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})

    def test_native_permission_hook_denies_hidden_financial_source(self):
        receipt = self.create()
        doc = self.doc("Jifeng Tool Cost Allocation", receipt["name"])
        self.assertTrue(self.module.tool_cost_allocation_has_permission(doc, "read"))
        self.denied.add(("Jifeng Expense Voucher", "E", "read"))
        self.assertFalse(self.module.tool_cost_allocation_has_permission(doc, "read"))
        self.assertEqual(self.module.tool_cost_allocation_query_conditions(), "1=0")
        self.assertEqual(self.module.tool_cost_allocation_item_query_conditions(), "1=0")

    def test_opaque_snapshot_and_bank_fields_never_appear_in_public_response(self):
        receipt = self.create()
        self.submit(receipt["name"])
        self.payment("P40", 40)
        public = json.dumps(self.module.get_tool_costs("C", "mold", "M"), ensure_ascii=False)
        for private in ("source_snapshot", "source_digest", "facts", "PRIVATE_BANK", "NEVER_EXPOSE", "active_source_key"):
            self.assertNotIn(private, public)

    def test_metadata_is_only_new_owned_scope_no_native_permission_expansion(self):
        folder = ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_tool_cost_allocation"
        meta = json.loads((folder / "jifeng_tool_cost_allocation.json").read_text(encoding="utf-8"))
        self.assertEqual({row["role"] for row in meta["permissions"]}, {"Accounts User", "Accounts Manager", "System Manager"})
        # Fixed native DocPerm defaults delete/report/export/share/print/email
        # to 1. Omission would grant those rights when metadata is loaded.
        unused = {"delete", "amend", "report", "export", "import", "share", "print", "email"}
        self.assertTrue(all(all(row.get(right) == 0 for right in unused) for row in meta["permissions"]))
        clerk = next(row for row in meta["permissions"] if row["role"] == "Accounts User")
        self.assertEqual((clerk["submit"], clerk["cancel"]), (0, 0))
        self.assertEqual({field["fieldname"] for field in meta["fields"] if field.get("unique")}, {"request_id", "active_source_key"})
        self.assertEqual(meta["is_submittable"], 1)

    def test_required_native_parent_and_child_hooks_fail_closed(self):
        del self.hooks["permission_query_conditions"]["Jifeng Tool Cost Allocation Item"]
        with self.assertRaises(PermissionError):
            self.create()
        self.assertFalse(any(key[0] == "Jifeng Tool Cost Allocation" for key in self.docs))

    def test_native_hook_recursion_token_does_not_grant_missing_ordinary_read(self):
        receipt = self.create()
        self.hooks_enabled = True
        self.assertEqual(self.module.get_tool_cost_allocation(receipt["name"])["docstatus"], 0)
        self.denied.add(("Jifeng Tool Cost Allocation", receipt["name"], "read"))
        with self.assertRaises(PermissionError):
            self.module.get_tool_cost_allocation(receipt["name"])
        self.assertIsNone(self.module._AUTHORIZING.get())

    def test_exact_allocation_capture_does_not_enumerate_unrelated_allocations(self):
        target, unrelated = self.create(), self.create()
        self.hooks_enabled = True
        scans, conditions = [], []
        get_all, get_list = self.frappe.get_all, self.frappe.get_list
        def raw(doctype, filters=None, **kwargs):
            if doctype == self.module.DOCTYPE and filters is None:
                scans.append(doctype)
            return get_all(doctype, filters=filters, **kwargs)
        def native_query(doctype, **kwargs):
            # Native DatabaseQuery runs its query condition before row access.
            if doctype == self.module.CHILD:
                conditions.append(self.module.tool_cost_allocation_item_query_conditions())
            return get_list(doctype, **kwargs)
        self.frappe.get_all, self.frappe.get_list = raw, native_query
        with patch.object(self.module, "_native_guard", wraps=self.module._native_guard) as guards:
            view = self.module.get_tool_cost_allocation(target["name"])
        self.assertEqual([row["historical_amount"] for row in view["items"]], ["70", "50"])
        self.assertEqual(scans, [])
        self.assertEqual(guards.call_count, 1)  # The public document gate remains.
        self.assertEqual(len(conditions), 2)  # Each stable capture reads its own children.
        self.assertTrue(all(target["name"] in condition and unrelated["name"] not in condition
                            for condition in conditions))
        self.assertIsNone(self.module._AUTHORIZING.get())

    def test_exact_allocation_capture_still_rejects_hidden_sources_and_table_fields(self):
        receipt = self.create()
        self.payment("PRIVATE_PAYMENT", 40)
        self.hooks_enabled = True
        for doctype, name in (("Payment Entry", "PRIVATE_PAYMENT"), ("Journal Entry", "J"),
                              (self.module.DOCTYPE, receipt["name"])):
            with self.subTest(doctype=doctype):
                self.denied = {(doctype, name, "read")}
                with self.assertRaises(PermissionError) as error:
                    self.module._stable("C", lambda read: self.module._allocation_view(read, receipt["name"]))
                self.assertNotIn("PRIVATE_PAYMENT", str(error.exception))
                self.assertIsNone(self.module._AUTHORIZING.get())
        self.denied.clear()
        for hidden, masked in (({(self.module.CHILD, "amount")}, set()),
                               (set(), {(self.module.DOCTYPE, "items")})):
            with self.subTest(hidden=hidden, masked=masked):
                self.hidden, self.masked = hidden, masked
                with self.assertRaises(PermissionError):
                    self.module._stable("C", lambda read: self.module._allocation_view(read, receipt["name"]))
                self.assertIsNone(self.module._AUTHORIZING.get())
        self.hidden, self.masked = set(), set()
        self.assertEqual(self.module.get_tool_cost_allocation(receipt["name"])["source"]["paid_amount"], "40")

    def test_exact_allocation_capture_rereads_children_between_stable_captures(self):
        receipt = self.create()
        self.hooks_enabled = True
        calls = [0]
        def mutate(doctype, filters):
            # Change both stored parent/child views before the second capture
            # loads them, rather than merely corrupting one projection.
            if doctype == "Company" and filters == {"name": "C"}:
                calls[0] += 1
                if calls[0] == 3:
                    parent = self.docs[self.module.DOCTYPE, receipt["name"]]
                    parent["items"][0].tool_label = "changed between captures"
                    self.docs[self.module.CHILD, parent["items"][0].name].tool_label = "changed between captures"
        self.raw_mutation = mutate
        with self.assertRaisesRegex(ValueError, "来源或权限已变化"):
            self.module._stable("C", lambda read: self.module._allocation_view(read, receipt["name"]))
        self.assertIsNone(self.module._AUTHORIZING.get())

    def test_complete_batch_scan_does_not_skip_payment_201(self):
        for index in range(201):
            self.payment("P" + str(index).zfill(3), "0.01")
        source = self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})
        self.assertEqual((source["paid_amount"], source["unpaid_amount"]), ("2.01", "117.99"))
        self.assertTrue(any(call[0] == "list" and call[1] == "Payment Entry Reference"
                            and call[2].get("limit_start") == 200 for call in self.calls))

    def test_cancelled_source_retains_historical_shares_and_proven_zero_ledger(self):
        receipt = self.create()
        self.submit(receipt["name"])
        for doctype, name in (("Jifeng Expense Voucher", "E"), ("Journal Entry", "J")):
            self.docs[doctype, name].docstatus = 2
            self.docs[doctype, name].modified = "2026-10-03 12:00:00"
        journal = self.docs["Journal Entry", "J"]
        self.bind_rows(journal)
        self.persist(journal)
        self.docs["GL Entry", "JG1"].is_cancelled = 1
        self.docs["GL Entry", "JG2"].is_cancelled = 1
        self.gl("JRG1", "Journal Entry", "J", "EXP", 0, 120)
        self.gl("JRG2", "Journal Entry", "J", "PAY", 120, 0, party="SUP")
        self.ple("JRL", "Journal Entry", "J", -120)
        view = self.module.get_tool_cost_allocation(receipt["name"])
        self.assertEqual(view["source"]["source_state"], "cancelled")
        self.assertEqual(view["source"]["incurred_amount"], "0")
        self.assertEqual([row["historical_amount"] for row in view["items"]], ["70", "50"])
        self.assertTrue(all(row["incurred_amount"] == "0" and row["paid_amount"] == "0" for row in view["items"]))

    def test_permissions_change_during_complete_capture_rejects(self):
        self.add("DocPerm", "PERM", parent="Jifeng Expense Voucher", read=1)
        calls = [0]
        def mutate(doctype, filters):
            if doctype == "DocPerm":
                calls[0] += 1
                if calls[0] == 2:
                    self.docs["DocPerm", "PERM"]["read"] = 0
        self.raw_mutation = mutate
        with self.assertRaises(ValueError):
            self.module.get_tool_cost_source({"company": "C", "expense_voucher": "E"})

    def test_cancelled_payment_gl_unlinks_only_readable_cancelled_journal(self):
        payment = self.payment("P40", 40, cancelled=True)
        for row in self.docs.values():
            if row.doctype == "GL Entry" and row.voucher_type == "Payment Entry":
                row.against_voucher_type, row.against_voucher, row.is_cancelled = None, None, 1
        read = self.module._Read("C")
        source, journal = read.source("Jifeng Expense Voucher", "E"), read.source("Journal Entry", "J")
        ledger = read.ledger("Payment Ledger Entry", {"against_voucher_type": "Journal Entry", "against_voucher_no": "J"})
        with self.assertRaises(ValueError):
            self.module._payment(read, payment.name, source, journal, ledger)
        self.docs["Journal Entry", "J"].docstatus = 2
        read = self.module._Read("C")
        source, journal = read.source("Jifeng Expense Voucher", "E"), read.source("Journal Entry", "J")
        view = self.module._payment(read, payment.name, source, journal, ledger)
        self.assertEqual(view["actual_paid"], "0")
        self.denied.add(("Journal Entry", "J", "read"))
        with self.assertRaises(PermissionError):
            self.module._Read("C").source("Journal Entry", "J")


if __name__ == "__main__":
    unittest.main()
