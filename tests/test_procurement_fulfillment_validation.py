"""No-DB source-oracle and real callback-cleanup models for native acceptance."""

import ast
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]
HELPER = "jifeng_erp/procurement_fulfillment_validation.py"
CALLBACKS = ("before_commit", "after_commit", "before_rollback", "after_rollback")


class Row(dict):
    __setattr__ = dict.__setitem__

    def __getattribute__(self, field):
        if field in self:
            return self[field]
        try:
            return super().__getattribute__(field)
        except AttributeError:
            return None

    def __deepcopy__(self, memo):
        result = type(self)()
        memo[id(self)] = result
        for field, value in dict.items(self):
            result[deepcopy(field, memo)] = deepcopy(value, memo)
        return result


class Queue:
    def __init__(self, functions):
        self._functions = list(functions)

    def add(self, function):
        self._functions.append(function)

    def reset(self):
        self._functions.clear()


def load_functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, "exec"), namespace)


def oracle_namespace():
    namespace = {"Counter": Counter, "date": date, "datetime": datetime, "Decimal": Decimal}
    for path in ("jifeng_erp/procurement_fulfillment_workbench.py", HELPER):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        constants = [node for node in tree.body if isinstance(node, ast.Assign)]
        exec(compile(ast.Module(body=constants, type_ignores=[]), path, "exec"), namespace)
    load_functions(HELPER, {"_number", "_assert_projection", "_assert_related", "_assert_native_purchase_sources",
        "_assert_native_receipt_ledger", "_assert_native_payables", "_assert_native_settlement_ledger", "_assert_native_subcontract_sources"}, namespace)
    return namespace


class TestNativeProcurementSourceOracles(unittest.TestCase):
    """Independent exact identities/signs/currency, with same-item mutations."""

    def setUp(self):
        self.ns = oracle_namespace()
        self.PO, self.PR, self.PI, self.PE, self.JE, self.SCO, self.SCR = (
            self.ns[name] for name in ("PO", "PR", "PI", "PE", "JE", "SCO", "SCR"))
        self.order = self.document(self.PO, "PO-A", items=[self.child(self.PO, "PO-A", "PO-A1", qty=6, rate=10, jf_flow_order="FLOW-A"),
            self.child(self.PO, "PO-A", "PO-A2", idx=2, qty=4, rate=15, jf_flow_order="FLOW-B")])
        self.receipts = [self.document(self.PR, "PR-" + str(index), items=[self.child(self.PR, "PR-" + str(index), "PR-L" + str(index),
            qty=qty, rate=rate, purchase_order="PO-A", purchase_order_item=detail, jf_flow_order=flow)])
            for index, (detail, qty, rate, flow) in enumerate((("PO-A1", 2, 10, "FLOW-A"), ("PO-A1", 1, 10, "FLOW-A"), ("PO-A2", 1, 15, "FLOW-B")), 1)]
        self.invoices = [self.document(self.PI, "PI-" + str(index), items=[self.child(self.PI, "PI-" + str(index), "PI-L" + str(index),
            qty=receipt.items[0].qty, rate=receipt.items[0].rate, purchase_order="PO-A", po_detail=receipt.items[0].purchase_order_item,
            purchase_receipt=receipt.name, pr_detail=receipt.items[0].name, jf_flow_order=receipt.items[0].jf_flow_order)])
            for index, receipt in enumerate(self.receipts, 1)]
        self.invoices.append(self.document(self.PI, "PI-DIRECT", items=[self.child(self.PI, "PI-DIRECT", "PI-DIRECT-L",
            qty=1, rate=15, purchase_order="PO-A", po_detail="PO-A2", jf_flow_order="FLOW-B")]))

    def document(self, kind, name, **values):
        return Row(doctype=kind, name=name, modified=datetime(2026, 10, 1, 8, 0), docstatus=1, company="A", supplier="SUP-A",
            transaction_date=date(2026, 9, 29), posting_date=date(2026, 10, 1), currency="CNY", supplier_currency="CNY",
            status="To Bill", is_subcontracted=0, is_old_subcontracting_flow=0, is_return=0, return_against=None,
            outstanding_amount=Decimal("20.00"), credit_to="AP-A", party_account_currency="CNY", update_stock=0,
            **values)

    def child(self, kind, parent, name, **values):
        qty, rate = values.pop("qty", 1), values.pop("rate", 10)
        data = dict(doctype=self.ns["TABLES"][kind][1], name=name, modified=datetime(2026, 10, 1, 8, 0),
            parent=parent, parenttype=kind, parentfield="items", idx=1, item_code="SAME-RAW", fg_item=None,
            qty=qty, stock_qty=qty, received_qty=qty, rejected_qty=0, uom="Nos", stock_uom="Nos", conversion_factor=1,
            rate=rate, amount=Decimal(str(qty)) * Decimal(str(rate)), base_amount=Decimal(str(qty)) * Decimal(str(rate)),
            warehouse="WH-A", jf_flow_order=None, purchase_order=None, purchase_order_item=None,
            purchase_receipt_item=None, purchase_invoice=None, purchase_invoice_item=None, subcontracting_receipt_item=None,
            po_detail=None, purchase_receipt=None, pr_detail=None, subcontracting_order=None, subcontracting_order_item=None,
            secondary_item_type=None, valuation_type=None, reference_name=None)
        data.update(values)
        return Row(data)

    def projection(self, kind, native, child=False, extra=False):
        schema = self.ns["EXTRAS"][kind] if extra else self.ns["TABLES"][kind] if child else self.ns["PARENTS"][kind]
        fields = schema[2] | schema[3] if child or extra else schema[0] | schema[1]
        return {field: value.isoformat() if isinstance(value := native.get(field), (date, datetime)) else str(value)
                if isinstance(value, Decimal) else value for field in fields}

    def related(self, kind, documents):
        return [{"document": self.projection(kind, document), "items": [self.projection(kind, row, child=True) for row in document.items],
            "other_items": [], "unlinked_items": [], "amount_scope": "whole_voucher", "amounts_allocated_to_items": False,
            "valuation_currency": "CNY", **({"service_items": [self.projection(kind, row, extra=True) for row in document.service_items]}
                                         if kind == self.SCO else {})} for document in documents]

    def result(self):
        return {"company": "A", "supplier": "SUP-A", "company_currency": "CNY", "readonly": True, "can_write": False,
            "quantities_aggregated": False, "amounts_allocated_to_items": False, "purchase_order": self.projection(self.PO, self.order),
            "items": [self.projection(self.PO, row, child=True) for row in self.order.items],
            "purchase_receipts": self.related(self.PR, self.receipts), "purchase_invoices": self.related(self.PI, self.invoices)}

    def verify(self, result=None):
        self.ns["_assert_native_purchase_sources"](result or self.result(), self.order, self.receipts, self.invoices)

    def return_sources(self):
        received, billed = self.receipts[2], self.invoices[2]
        returned_receipt = self.document(self.PR, "PR-RETURN", items=[self.child(self.PR, "PR-RETURN", "PR-RETURN-L", qty=-1, rate=15,
            purchase_order="PO-A", purchase_order_item="PO-A2", purchase_receipt_item=received.items[0].name, jf_flow_order="FLOW-B")])
        returned_receipt.update(is_return=1, return_against=received.name)
        returned_invoice = self.document(self.PI, "PI-CREDIT", items=[self.child(self.PI, "PI-CREDIT", "PI-CREDIT-L", qty=-1, rate=15,
            purchase_order="PO-A", po_detail="PO-A2", purchase_receipt=billed.items[0].purchase_receipt,
            pr_detail=billed.items[0].pr_detail, purchase_invoice_item=billed.items[0].name, jf_flow_order="FLOW-B")])
        returned_invoice.update(is_return=1, return_against=billed.name, outstanding_amount=Decimal("-15.00"))
        self.receipts.append(returned_receipt)
        self.invoices.append(returned_invoice)
        return returned_receipt, returned_invoice

    def test_three_same_item_receipt_invoice_batches_and_direct_invoice_preserve_exact_sources_and_raw_amounts(self):
        self.verify()
        result = self.result()
        self.assertEqual([part["items"][0]["purchase_order_item"] for part in result["purchase_receipts"]], ["PO-A1", "PO-A1", "PO-A2"])
        self.assertEqual([part["items"][0]["amount"] for part in result["purchase_receipts"]], ["20", "10", "15"])
        self.assertIsNone(result["purchase_invoices"][-1]["items"][0]["pr_detail"])

    def test_source_parent_child_omission_duplication_other_unlinked_or_allocated_values_are_rejected(self):
        changes = [lambda r: r["purchase_receipts"].pop(), lambda r: r["purchase_receipts"].append(deepcopy(r["purchase_receipts"][0])),
            lambda r: r["purchase_receipts"][0]["items"].clear(),
            lambda r: r["purchase_receipts"][0]["items"].append(deepcopy(r["purchase_receipts"][0]["items"][0])),
            lambda r: r["purchase_receipts"][0]["other_items"].append({"name": "OTHER-PO-L"}),
            lambda r: r["purchase_receipts"][0]["unlinked_items"].append({"name": "UNLINKED"}),
            lambda r: r["purchase_invoices"][0].update(amounts_allocated_to_items=True),
            lambda r: r["purchase_invoices"][0]["items"][0].update(amount="10"),
            lambda r: r["purchase_receipts"][0].update(valuation_currency="USD")]
        for change in changes:
            with self.subTest(change=change):
                result = self.result()
                change(result)
                with self.assertRaises(AssertionError):
                    self.verify(result)

    def test_saved_source_itself_with_wrong_same_item_child_flow_company_supplier_or_pr_link_is_rejected(self):
        mutations = [(self.receipts[0].items[0], "purchase_order_item", "PO-A2"),
            (self.receipts[0].items[0], "jf_flow_order", "FLOW-B"), (self.receipts[0], "company", "B"),
            (self.invoices[0], "supplier", "SUP-B"), (self.invoices[0].items[0], "pr_detail", self.receipts[1].items[0].name)]
        for row, field, value in mutations:
            with self.subTest(field=field):
                original = row[field]
                row[field] = value
                try:
                    with self.assertRaises(AssertionError):
                        self.verify()  # DTO mirrors corruption; the native-source oracle must still reject it.
                finally:
                    row[field] = original

    def test_returns_and_cancellations_retain_exact_original_positive_rows_and_negative_child_history(self):
        returned_receipt, returned_invoice = self.return_sources()
        self.verify()
        returned_receipt["docstatus"] = returned_invoice["docstatus"] = 2
        self.verify()
        self.assertEqual(self.receipts[2].items[0].qty, 1)
        self.assertEqual(returned_receipt.items[0].qty, -1)

    def test_return_wrong_original_missing_pointer_or_positive_return_even_when_dto_matches_native_rejected(self):
        returned_receipt, returned_invoice = self.return_sources()
        for row, field, value in [(returned_receipt.items[0], "purchase_receipt_item", None),
            (returned_receipt, "return_against", self.receipts[0].name),
            (returned_invoice.items[0], "purchase_invoice_item", self.invoices[0].items[0].name),
            (returned_invoice.items[0], "qty", 1)]:
            with self.subTest(field=field):
                original = row[field]
                row[field] = value
                try:
                    with self.assertRaises(AssertionError):
                        self.verify()
                finally:
                    row[field] = original

    def stock_ledger(self):
        ledger = []
        for document in self.receipts:
            for child in document.items:
                for qty in [child.stock_qty] + ([-Decimal(str(child.stock_qty))] if document.docstatus == 2 else []):
                    ledger.append(Row(voucher_type=self.PR, voucher_no=document.name, voucher_detail_no=child.name,
                        company="A", warehouse="WH-A", item_code="SAME-RAW", actual_qty=qty, is_cancelled=int(document.docstatus == 2)))
        return ledger

    def test_exact_stock_detail_sign_and_cancelled_inverse_rows_not_only_net_stock(self):
        returned_receipt, _ = self.return_sources()
        returned_receipt["docstatus"] = 2
        verify = self.ns["_assert_native_receipt_ledger"]
        ledger = self.stock_ledger()
        verify(self.receipts, ledger, "A", "WH-A")
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[0])),
                       lambda rows: rows[0].update(voucher_detail_no=self.receipts[1].items[0].name),
                       lambda rows: rows[0].update(actual_qty=3), lambda rows: rows[-1].update(is_cancelled=0),
                       lambda rows: rows[0].update(company="B"), lambda rows: rows[0].update(warehouse="WH-B")):
            rows = deepcopy(ledger)
            change(rows)
            with self.assertRaises(AssertionError):
                verify(self.receipts, rows, "A", "WH-A")
        wrong_net = deepcopy(ledger)
        wrong_net[0]["actual_qty"], wrong_net[1]["actual_qty"] = 1, 2
        with self.assertRaises(AssertionError):
            verify(self.receipts, wrong_net, "A", "WH-A")

    def payable_fixture(self):
        reference = Row(name="PE-REF", modified=datetime(2026, 10, 1, 9, 0), parent="PE-A", parenttype=self.PE, parentfield="references",
            idx=1, reference_doctype=self.PI, reference_name=self.invoices[0].name, allocated_amount=3, account="AP-A", exchange_rate=1)
        payment = self.document(self.PE, "PE-A", references=[reference], party_type="Supplier", party="SUP-A", payment_type="Pay",
            paid_from="CASH-A", paid_to="AP-A", paid_from_account_currency="CNY", paid_to_account_currency="CNY", paid_amount=3,
            received_amount=3, base_paid_amount=3, base_received_amount=3, source_exchange_rate=1, target_exchange_rate=1)
        account = Row(name="JE-REF", modified=datetime(2026, 10, 1, 9, 0), parent="JE-A", parenttype=self.JE, parentfield="accounts", idx=1,
            account="AP-A", party_type="Supplier", party="SUP-A", reference_type=self.PI, reference_name=self.invoices[1].name,
            account_currency="CNY", debit_in_account_currency=2, credit_in_account_currency=0, cost_center="CC-A")
        cash = Row(name="JE-CASH", modified=datetime(2026, 10, 1, 9, 0), parent="JE-A", parenttype=self.JE, parentfield="accounts", idx=2,
            account="CASH-A", party_type=None, party=None, reference_type=None, reference_name=None, account_currency="CNY",
            debit_in_account_currency=0, credit_in_account_currency=2, cost_center="CC-A")
        journal = self.document(self.JE, "JE-A", accounts=[account, cash], jf_expense_voucher=None, multi_currency=0, voucher_type="Journal Entry")
        documents = [self.order, *self.invoices, payment, journal]
        ledger = [Row(name="PLE-" + str(index), modified=datetime(2026, 10, 1, 9, 0), company="A", party_type="Supplier", party="SUP-A",
            account="AP-A", account_type="Payable", account_currency="CNY", posting_date=date(2026, 10, 1),
            voucher_type=kind, voucher_no=name, against_voucher_type=self.PI, against_voucher_no=invoice.name,
            amount=Decimal(amount), amount_in_account_currency=Decimal(amount), delinked=0, due_date=None)
            for index, (kind, name, invoice, amount) in enumerate(((self.PI, self.invoices[0].name, self.invoices[0], "20.00"),
                (self.PE, payment.name, self.invoices[0], "-3.00"), (self.JE, journal.name, self.invoices[1], "-2.00")), 1)]
        result = self.result()
        sources = []
        for document in documents:
            field = self.ns["TABLES"][document.doctype][0]
            sources.append({"doctype": document.doctype, "document": self.projection(document.doctype, document),
                field: [self.projection(document.doctype, child, child=True) for child in document.get(field)]})
        result["payables"] = {"voucher_sources": sources, "ledger_entries": [self.projection(self.ns["PLE"], row) for row in ledger],
            "other_reference_sources": [], "amount_scope": "whole_voucher", "balance_basis": "native_current_outstanding", "amounts_allocated_to_items": False}
        return result, documents, ledger

    def test_payment_and_journal_keep_full_native_children_and_ple_currency_values_without_item_allocation(self):
        result, documents, ledger = self.payable_fixture()
        self.ns["_assert_native_payables"](result, documents, ledger)
        self.assertEqual(result["payables"]["ledger_entries"][1]["amount"], "-3.00")
        self.assertEqual(len(result["payables"]["voucher_sources"][-1]["accounts"]), 2)

    def test_partial_other_supplier_wrong_currency_amount_or_reference_in_payable_dto_is_rejected(self):
        mutations = [lambda r: r["payables"]["voucher_sources"].pop(), lambda r: r["payables"]["ledger_entries"].pop(),
            lambda r: r["payables"]["ledger_entries"].append(deepcopy(r["payables"]["ledger_entries"][0])),
            lambda r: r["payables"]["ledger_entries"][1].update(party="SUP-B"),
            lambda r: r["payables"]["ledger_entries"][1].update(amount="-1.50"),
            lambda r: r["payables"]["ledger_entries"][1].update(account_currency="USD"),
            lambda r: r["payables"]["voucher_sources"][-1]["accounts"].pop(),
            lambda r: r["payables"]["voucher_sources"][-2]["references"][0].update(reference_name="PI-DIRECT"),
            lambda r: r["payables"].update(balance_basis="as_of_date"), lambda r: r["payables"].update(amounts_allocated_to_items=True)]
        for change in mutations:
            result, documents, ledger = self.payable_fixture()
            change(result)
            with self.assertRaises(AssertionError):
                self.ns["_assert_native_payables"](result, documents, ledger)

    def test_native_ple_with_wrong_supplier_or_unregistered_voucher_rejected_even_when_projection_matches(self):
        for field, value in (("party", "SUP-B"), ("company", "B"), ("against_voucher_no", "OTHER-PI")):
            result, documents, ledger = self.payable_fixture()
            ledger[1][field] = value
            result["payables"]["ledger_entries"][1][field] = value
            with self.assertRaises(AssertionError):
                self.ns["_assert_native_payables"](result, documents, ledger)

    def test_actual_posted_payment_journal_gl_requires_exact_ap_debit_cash_credit_and_source(self):
        verify = self.ns["_assert_native_settlement_ledger"]
        for kind, amount in ((self.PE, 3), (self.JE, 2)):
            document = self.document(kind, kind + "-POSTED")
            ledger = [Row(voucher_type=kind, voucher_no=document.name, company="A", account_currency="CNY", is_cancelled=0,
                account="AP-A", party_type="Supplier", party="SUP-A", debit=amount, credit=0, debit_in_account_currency=amount, credit_in_account_currency=0),
                Row(voucher_type=kind, voucher_no=document.name, company="A", account_currency="CNY", is_cancelled=0,
                    account="CASH-A", party_type="", party="", debit=0, credit=amount, debit_in_account_currency=0, credit_in_account_currency=amount)]
            verify(document, ledger, "CASH-A", "AP-A", "SUP-A", amount, "CNY")
            for change in (lambda rows: rows.pop(), lambda rows: rows.append(deepcopy(rows[0])),
                lambda rows: rows[0].update(debit=0, credit=amount), lambda rows: rows[0].update(party="SUP-B"),
                lambda rows: rows[0].update(voucher_no="OTHER-PAYMENT"), lambda rows: rows[0].update(account_currency="USD")):
                mutated = deepcopy(ledger)
                change(mutated)
                with self.assertRaises(AssertionError):
                    verify(document, mutated, "CASH-A", "AP-A", "SUP-A", amount, "CNY")

    def subcontract_fixture(self):
        purchase = self.document(self.PO, "PO-SUB", items=[self.child(self.PO, "PO-SUB", "PO-SERVICE", qty=2, rate=20,
            item_code="SERVICE", fg_item="FG", fg_item_qty=4)])
        purchase.update(is_subcontracted=1)
        fg = self.child(self.SCO, "SCO", "SCO-FG", qty=4, rate=15, item_code="FG", purchase_order_item="PO-SERVICE", jf_flow_order="FLOW-FG")
        service = Row(name="SCO-SERVICE", modified=datetime(2026, 10, 1, 8, 0), parent="SCO", parenttype=self.SCO, parentfield="service_items",
            idx=1, item_code="SERVICE", purchase_order_item="PO-SERVICE", qty=2, rate=20, amount=Decimal("40"))
        subcontract = self.document(self.SCO, "SCO", purchase_order=purchase.name, items=[fg], service_items=[service])
        received = self.child(self.SCR, "SCR", "SCR-FG", qty=2, rate=15, item_code="FG", purchase_order="PO-SUB", purchase_order_item="PO-SERVICE",
            subcontracting_order="SCO", subcontracting_order_item="SCO-FG", jf_flow_order="FLOW-FG")
        receipt = self.document(self.SCR, "SCR", items=[received])
        purchased = self.child(self.PR, "PR-SERVICE", "PR-SERVICE-L", qty=1, rate=20, item_code="SERVICE", purchase_order="PO-SUB",
            purchase_order_item="PO-SERVICE", subcontracting_receipt_item="SCR-FG")
        service_receipt = self.document(self.PR, "PR-SERVICE", subcontracting_receipt="SCR", items=[purchased])
        billed = self.child(self.PI, "PI-SERVICE", "PI-SERVICE-L", qty=1, rate=20, item_code="SERVICE", purchase_order="PO-SUB", po_detail="PO-SERVICE",
            purchase_receipt="PR-SERVICE", pr_detail="PR-SERVICE-L")
        invoice = self.document(self.PI, "PI-SERVICE", items=[billed])
        result = {"company": "A", "supplier": "SUP-A", "company_currency": "CNY", "readonly": True, "can_write": False,
            "quantities_aggregated": False, "amounts_allocated_to_items": False, "items": [self.projection(self.PO, purchase.items[0], child=True)],
            "purchase_order": self.projection(self.PO, purchase), "subcontracting_orders": self.related(self.SCO, [subcontract]),
            "subcontracting_receipts": self.related(self.SCR, [receipt]), "purchase_receipts": self.related(self.PR, [service_receipt]),
            "purchase_invoices": self.related(self.PI, [invoice])}
        return result, [purchase, subcontract, receipt, service_receipt, invoice]

    def test_service_different_from_fg_exact_half_conversion_and_company_valuation_preserved(self):
        result, documents = self.subcontract_fixture()
        self.ns["_assert_native_subcontract_sources"](result, *documents)
        self.assertEqual(result["purchase_receipts"][0]["items"][0]["qty"], 1)
        self.assertEqual(result["subcontracting_receipts"][0]["items"][0]["qty"], 2)

    def test_service_fg_alias_wrong_sco_scr_pointer_conversion_or_forced_service_flow_rejected(self):
        for index, child, field, value in ((0, "items", "fg_item", "SERVICE"), (1, "service_items", "purchase_order_item", "OTHER-PO-L"),
            (2, "items", "subcontracting_order_item", "OTHER-SCO-L"), (3, "items", "subcontracting_receipt_item", "OTHER-SCR-L"),
            (3, "items", "qty", 2), (3, "items", "jf_flow_order", "FLOW-FG")):
            with self.subTest(field=field):
                result, documents = self.subcontract_fixture()
                documents[index].get(child)[0][field] = value
                with self.assertRaises(AssertionError):
                    self.ns["_assert_native_subcontract_sources"](result, *documents)

    def test_decimal_low_digits_not_reduced_to_float_or_nonfinite_value(self):
        verify = self.ns["_assert_projection"]
        native = Row(name="EXACT", amount=Decimal("123456789012345.123456789"))
        verify({"name": "EXACT", "amount": "123456789012345.123456789"}, native, {"name", "amount"})
        for value in ("123456789012345.123456788", "Infinity", "NaN"):
            with self.assertRaises(AssertionError):
                verify({"name": "EXACT", "amount": value}, native, {"name", "amount"})


class TestNativeProcurementNestedCleanup(unittest.TestCase):
    """Execute actual nested gate with native cache invalidation side effects."""

    def setUp(self):
        self.rows, self.events, self.checks = [], [], []
        self.failure = None
        self.original_callbacks = {name: [Mock(name=name + "-first"), Mock(name=name + "-second")] for name in CALLBACKS}
        self.frappe = types.SimpleNamespace(db=types.SimpleNamespace(**{name: Queue(functions) for name, functions in self.original_callbacks.items()}),
            session=types.SimpleNamespace(user="reader"), in_test=True, PermissionError=PermissionError,
            flags=Row(currently_saving=[("Original", "SOURCE")]), local=types.SimpleNamespace(_realtime_log=[{"event": "original"}]),
            generate_hash=lambda length: "point")
        self.frappe.set_user = lambda user: (self.events.append(("actor", user)), setattr(self.frappe.session, "user", user))
        self.frappe.clear_cache = lambda **kwargs: self.invalidate("meta" if "doctype" in kwargs else "user")
        self.frappe.clear_document_cache = lambda *args: self.invalidate("document")
        callbacks = types.ModuleType("jifeng_erp.personnel_changes_validation")
        callbacks.frappe = self.frappe
        load_functions("jifeng_erp/personnel_changes_validation.py", {"_callbacks", "_restore_callbacks"}, callbacks.__dict__)
        context = types.ModuleType("jifeng_erp.personnel_separation_validation")
        context.frappe, context.deepcopy = self.frappe, deepcopy
        load_functions("jifeng_erp/personnel_separation_validation.py", {"_local_context", "_restore_local_context"}, context.__dict__)
        modules = patch.dict(sys.modules, {callbacks.__name__: callbacks, context.__name__: context})
        modules.start()
        self.addCleanup(modules.stop)
        self.callbacks, self.context = callbacks, context
        namespace = {"frappe": self.frappe, "_callbacks": callbacks._callbacks, "_local_context": context._local_context,
            "_begin_rollback_only": Mock(), "_end_rollback_only": self.rollback, "_contents": lambda: deepcopy(self.rows),
            "actors": {"reader": "reader"}, "records": {("Purchase Order", "PO")}, "checks": self.checks,
            "company": "A", "order": types.SimpleNamespace(name="PO"), "read_procurement_source": lambda *args: {"order": "PO"}}
        load_functions("jifeng_erp/catalog_validation.py", {"_profile_rollback", "_profile_restore_caller", "_profile_audit"}, namespace)
        tree = ast.parse((ROOT / HELPER).read_text(encoding="utf-8"))
        smoke = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "smoke")
        functions = [deepcopy(node) for node in smoke.body if isinstance(node, ast.FunctionDef) and node.name in {"read", "rejected", "nested_gate"}]
        gate = next(node for node in functions if node.name == "nested_gate")
        functions.remove(gate)
        wrapper = ast.FunctionDef(name="make_gate", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Assign(targets=[ast.Name(id="current", ctx=ast.Store())], value=ast.Constant(None)), gate,
                  ast.Return(value=ast.Name(id="nested_gate", ctx=ast.Load()))], decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[*functions, wrapper], type_ignores=[])), HELPER, "exec"), namespace)
        self.gate = namespace["make_gate"]()
        self.namespace = namespace
        self.smoke = smoke
        self.context_before = context._local_context()

    def invalidate(self, kind):
        self.events.append(("invalidate", kind))
        self.frappe.db.after_commit.add(Mock(name="new-cache-commit"))
        self.frappe.db.after_rollback.add(Mock(name="new-cache-rollback"))
        self.frappe.local._realtime_log = [{"event": "cache"}]
        self.frappe.flags["currently_saving"] = [("Cache", "new")]
        if self.failure and self.failure[0] == kind:
            raise self.failure[1]

    def rollback(self, point):
        self.events.append(("rollback", point))
        self.rows.clear()

    def setup(self):
        self.rows.append({"owned": "nested source restriction"})
        self.frappe.db.before_rollback.add(Mock(name="new-before-rollback"))
        self.frappe.db.after_rollback.add(Mock(name="new-after-rollback"))

    def assert_restored(self):
        self.assertEqual(self.rows, [])
        self.assertEqual(self.context._local_context(), self.context_before)
        self.assertEqual(self.frappe.session.user, "reader")
        for field, original in self.original_callbacks.items():
            actual = self.callbacks._callbacks()[field]
            self.assertEqual(actual, original)
            for caller, restored in zip(original, actual, strict=True):
                self.assertIs(caller, restored)
                caller.assert_not_called()

    def test_after_rollback_meta_and_document_invalidations_restore_callback_identity_order_context(self):
        self.gate("gate", self.setup, errors=None, doctypes=("Purchase Receipt Item",))
        self.assertEqual(self.checks, ["gate"])
        self.assert_restored()
        rollback = next(index for index, event in enumerate(self.events) if event[0] == "rollback")
        invalidations = [index for index, event in enumerate(self.events) if event[0] == "invalidate"]
        self.assertTrue(all(index > rollback for index in invalidations))
        self.assertEqual([self.events[index][1] for index in invalidations], ["meta", "user", "document"])

    def test_meta_user_document_invalidation_failure_always_restores_and_propagates(self):
        for kind in ("meta", "user", "document"):
            with self.subTest(kind=kind):
                error = RuntimeError(kind + " native cache failure")
                self.failure = kind, error
                with self.assertRaises(RuntimeError) as raised:
                    self.gate("gate", self.setup, errors=None, doctypes=("Purchase Receipt Item",))
                self.assertIs(raised.exception, error)
                self.assert_restored()
        self.assertEqual(self.checks, [])

    def test_primary_business_error_not_masked_by_cache_cleanup_failure_and_every_audit_runs(self):
        primary = ValueError("native source save failed")
        self.failure = "meta", RuntimeError("native metadata invalidation failed")
        def setup():
            self.setup()
            raise primary
        with self.assertRaises(ValueError) as raised:
            self.gate("gate", setup, errors=None, doctypes=("Purchase Receipt Item",))
        self.assertIs(raised.exception, primary)
        self.assertTrue(any("nested_meta_owned_caches_actor_context_restore" in note for note in primary.__notes__))
        self.assert_restored()
        self.assertEqual(self.checks, [])

    def outer_cleanup(self, primary=None, leave_change=False):
        """Execute smoke's actual finally block, including every SQL/ledger gate."""
        child = types.SimpleNamespace(doctype="Purchase Order Item", name="OWNED-L")
        document = types.SimpleNamespace(doctype="Purchase Order", name="OWNED-PO", get_all_children=lambda: [child])
        protected = {"Series": "unchanged", "__Auth": "unchanged", "Purchase Order": "baseline", "Payment Ledger Entry": "baseline"}
        before = deepcopy(protected)
        queued = self.callbacks._callbacks()
        local = self.context._local_context()
        owned = {("Purchase Order", "OWNED-PO"), ("Purchase Order Item", "OWNED-L")}
        protected["Series"] = "incremented"
        self.frappe.flags["through_repost_item_valuation"] = True
        self.frappe.db.MAX_WRITES_PER_TRANSACTION = 999
        self.setup()
        native_rollback = self.namespace["_profile_rollback"]
        def rollback(point, callbacks):
            native_rollback(point, callbacks)
            owned.clear()
            protected.clear()
            protected.update(before)
            if leave_change:
                # Same row counts, but Series/auth/source contents differ.
                protected["__Auth"] = "modified-with-same-count"
        self.frappe.db.exists = lambda kind, name: (kind, name) in owned
        events = []
        def contents():
            events.append("complete_sql")
            return deepcopy(protected)
        def counts(tables):
            events.append("all_counts")
            return tables, {"tabPurchase Order": 7}
        def ledgers():
            events.append("whole_ledgers")
            return {"GL Entry": "baseline", "Stock Ledger Entry": "baseline"}
        namespace = {**self.namespace, "objects": [document], "records": set(), "users": ["reader"],
            "original_user": "reader", "original_test": True, "original_repost": None, "original_max_writes": 123,
            "callbacks": queued, "context": local, "point": "outer", "primary": primary,
            "tables": {"tabPurchase Order": "InnoDB"}, "counts": {"tabPurchase Order": 7},
            "before": before, "ledger_before": {"GL Entry": "baseline", "Stock Ledger Entry": "baseline"},
            "_profile_rollback": rollback, "_contents": contents, "_tab_counts": counts, "_ledger_snapshot": ledgers}
        outer = next(node for node in self.smoke.body if isinstance(node, ast.Try) and node.finalbody)
        wrapper = ast.FunctionDef(name="cleanup", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=deepcopy(outer.finalbody), decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), HELPER, "exec"), namespace)
        return namespace["cleanup"], namespace["records"], events

    def test_outer_finally_collects_owned_children_and_checks_complete_sql_counts_ledgers_callbacks(self):
        cleanup, records, events = self.outer_cleanup()
        cleanup()
        self.assertEqual(records, {("Purchase Order", "OWNED-PO"), ("Purchase Order Item", "OWNED-L")})
        self.assertEqual(events, ["complete_sql", "all_counts", "whole_ledgers"])
        self.assertIsNone(self.frappe.flags.through_repost_item_valuation)
        self.assertEqual(self.frappe.db.MAX_WRITES_PER_TRANSACTION, 123)
        self.assert_restored()

    def test_outer_same_count_auth_change_fails_full_sql_gate_but_runs_all_remaining_audits(self):
        cleanup, _, events = self.outer_cleanup(leave_change=True)
        with self.assertRaises(AssertionError) as raised:
            cleanup()
        self.assertIn("full_business_children_auth_roles_series_metadata_ledgers_sql_restored", str(raised.exception))
        self.assertEqual(events, ["complete_sql", "all_counts", "whole_ledgers"])
        self.assert_restored()

    def test_outer_native_failure_retained_when_cache_restore_fails_after_database_rollback(self):
        primary = ValueError("native submit failed")
        self.failure = "document", RuntimeError("document invalidation failed")
        cleanup, _, events = self.outer_cleanup(primary=primary)
        cleanup()  # Actual try block re-raises primary; audit only annotates it.
        self.assertTrue(any("restore_actor_context_flags_and_exact_owned_caches" in note for note in primary.__notes__))
        self.assertEqual(events, ["complete_sql", "all_counts", "whole_ledgers"])
        self.assert_restored()


if __name__ == "__main__":
    unittest.main()
