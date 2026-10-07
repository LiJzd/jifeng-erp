"""Native-shaped source/permission adversarial models, without any database."""

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

    def has_permission(self, permission):
        return ((self.doctype, permission) not in self.environment.role_denials
                and (self.doctype, self.name, permission) not in self.environment.denied)


class Meta:
    def __init__(self, environment, doctype):
        self.environment, self.doctype = environment, doctype

    @property
    def fields(self):
        return list(self.environment.metas[self.doctype].values())

    def get_field(self, field):
        return self.environment.metas[self.doctype].get(field)

    def get_permlevel_access(self, permission_type, parenttype=None, user=None):
        if permission_type == "read":
            return self.environment.read_levels.get(self.doctype, {0})
        return self.environment.mask_levels.get((self.doctype, parenttype), set())


class TestProcurementFulfillment(unittest.TestCase):
    def setUp(self):
        self.documents, self.metas, self.permitted = {}, {}, {}
        self.hidden, self.omitted, self.mask_levels, self.read_levels = {}, {}, {}, {}
        self.denied, self.list_denied, self.role_denials = set(), set(), set()
        self.doc_hooks, self.list_hooks, self.raw_hooks = {}, {}, {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.session, self.frappe.conf = Record(user="native-reader"), Record()
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = type("DoesNotExistError", (ValueError,), {})
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_meta = lambda doctype: Meta(self, doctype)
        self.frappe.db = types.SimpleNamespace(commit=Mock(), set_value=Mock(), sql=Mock())
        self.frappe.new_doc, self.frappe.delete_doc = Mock(), Mock()
        model, utils = types.ModuleType("frappe.model"), types.ModuleType("frappe.utils")
        model.get_permitted_fields = self.columns
        utils.getdate = lambda value: date.fromisoformat(value) if isinstance(value, str) else value
        patches = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils})
        patches.start()
        self.addCleanup(patches.stop)
        spec = importlib.util.spec_from_file_location("procurement_under_test", ROOT / "jifeng_erp/procurement_fulfillment_workbench.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        for doctype, (required, optional) in self.module.PARENTS.items():
            self.schema(doctype, required | optional)
        self.schema("Company", {"name", "modified", "default_currency"})
        for parent, (field, childtype, required, optional) in list(self.module.TABLES.items()) + list(self.module.EXTRAS.items()):
            self.schema(childtype, required | optional)
            self.metas[parent][field] = Record(fieldname=field, fieldtype="Table", permlevel=0)
        Document.environment = self
        self.add("Company", "JF", default_currency="CNY")
        self.add("Supplier", "SUP")
        for item in ("ITEM", "SERVICE", "FG", "SECONDARY", "RAW"):
            self.add("Item", item)
        self.order = self.po()
        self.first = self.line("Purchase Order", "PO", "PO-1", qty=10, uom="Pair", stock_uom="Nos", stock_qty=20)
        self.second = self.line("Purchase Order", "PO", "PO-2", qty=5, uom="Set", stock_uom="Nos", stock_qty=15)

    def schema(self, doctype, fields):
        self.permitted[doctype] = set(fields)
        self.metas[doctype] = {field: Record(fieldname=field, fieldtype="Data", permlevel=0) for field in fields}

    def add(self, doctype, name, **values):
        defaults = {field: None for field in self.module.PARENTS.get(doctype, (set(), set()))[0] | self.module.PARENTS.get(doctype, (set(), set()))[1]}
        row = Record(defaults | dict(doctype=doctype, name=name, modified=datetime(2026, 10, 1, 12)) | values)
        self.documents[doctype, name] = row
        return row

    def line(self, parenttype, parent, name, extra=False, **values):
        field, childtype, required, optional = (self.module.EXTRAS if extra else self.module.TABLES)[parenttype]
        defaults = {field: None for field in (required | optional) - self.module.CHILD}
        row = self.add(childtype, name, **(defaults | dict(parent=parent, parenttype=parenttype, parentfield=field,
            item_code="ITEM", conversion_factor=2, idx=1 + sum(kind == childtype and item.parent == parent for (kind, _), item in self.documents.items())) | values))
        return row

    def columns(self, doctype, parenttype=None, **kwargs):
        self.assertEqual(kwargs, {"permission_type": "read", "ignore_virtual": True})
        return self.permitted[doctype] - self.hidden.get(doctype, set())

    def get_doc(self, doctype, name):
        if doctype in self.doc_hooks:
            self.doc_hooks[doctype](name)
        if (doctype, name) not in self.documents:
            raise self.frappe.DoesNotExistError("missing")
        doc = Document(deepcopy(self.documents[doctype, name]))
        for schema in ([self.module.TABLES[doctype]] if doctype in self.module.TABLES else []) + ([self.module.EXTRAS[doctype]] if doctype in self.module.EXTRAS else []):
            field, childtype, _r, _o = schema
            doc[field] = [Record(deepcopy(item)) for (kind, _), item in self.documents.items()
                          if kind == childtype and item.parent == name and item.parenttype == doctype and item.parentfield == field]
        return doc

    @staticmethod
    def matches(document, filters):
        for field, wanted in filters.items():
            actual = document.get(field)
            if isinstance(wanted, list):
                op, value = wanted
                if op == "in" and actual not in value:
                    return False
                if op == "between" and not str(value[0]) <= str(actual) <= str(value[1]):
                    return False
                if op == "like" and value.strip("%") not in str(actual):
                    return False
            elif actual != wanted:
                return False
        return True

    def get_list(self, doctype, filters, fields, limit_page_length, order_by, **kwargs):
        if (doctype, "read") in self.role_denials:
            raise PermissionError("native role")
        if doctype in {schema[1] for schema in list(self.module.TABLES.values()) + list(self.module.EXTRAS.values())}:
            self.assertIn("parent_doctype", kwargs)
        rows = [Record({field: row.get(field) for field in fields if field not in self.omitted.get(doctype, set())})
                for (kind, name), row in sorted(self.documents.items())
                if kind == doctype and (kind, name) not in self.list_denied and self.matches(row, filters)]
        if doctype in self.list_hooks:
            self.list_hooks[doctype](filters)
        return rows[:limit_page_length]

    def get_all(self, doctype, filters, fields, limit_page_length, order_by):
        self.assertIn(fields, (["*"], ["name", "enabled", "user_type"]))
        rows = [deepcopy(row) for (kind, _), row in sorted(self.documents.items()) if kind == doctype and self.matches(row, filters)]
        if doctype in self.raw_hooks:
            self.raw_hooks[doctype](filters)
        return rows[:limit_page_length] if limit_page_length else rows

    def po(self, name="PO", **changes):
        return self.add("Purchase Order", name, **(dict(company="JF", supplier="SUP", docstatus=1,
            transaction_date=date(2026, 10, 1), currency="USD", is_subcontracted=0, is_old_subcontracting_flow=0) | changes))

    def receipt(self, name="PR", detail="PO-1", **changes):
        doc = self.add("Purchase Receipt", name, **(dict(company="JF", supplier="SUP", docstatus=1,
            posting_date=date(2026, 9, 20), currency="USD", is_return=0) | changes))
        row = self.line("Purchase Receipt", name, name + "-ROW", purchase_order="PO", purchase_order_item=detail,
                        qty=3, uom="Pair", stock_uom="Nos", stock_qty=6)
        return doc, row

    def invoice(self, name="PI", detail="PO-1", ledger=True, **changes):
        doc = self.add("Purchase Invoice", name, **(dict(company="JF", supplier="SUP", docstatus=1,
            posting_date=date(2026, 11, 10), currency="USD", party_account_currency="USD", is_return=0,
            credit_to="AP", outstanding_amount=Decimal("17.25"), grand_total=Decimal("42"), base_grand_total=Decimal("294")) | changes))
        row = self.line("Purchase Invoice", name, name + "-ROW", purchase_order="PO", po_detail=detail,
                        qty=2, uom="Pair", stock_uom="Nos", stock_qty=4, amount=Decimal("42"), base_amount=Decimal("294"))
        self.account("AP", "Payable", "USD")
        if ledger:
            self.ledger("PLE-" + name, voucher_no=name, against_voucher_no=name)
        return doc, row

    def account(self, name, kind=None, currency="CNY", **changes):
        return self.add("Account", name, **(dict(company="JF", account_type=kind, account_currency=currency, is_group=0) | changes))

    def ledger(self, name="PLE", **changes):
        return self.add(self.module.PLE, name, **(dict(company="JF", party_type="Supplier", party="SUP", account="AP",
            account_type="Payable", account_currency="USD", posting_date=date(2026, 11, 10), voucher_type="Purchase Invoice",
            voucher_no="PI", against_voucher_type="Purchase Invoice", against_voucher_no="PI", amount=Decimal("294"),
            amount_in_account_currency=Decimal("42"), delinked=0) | changes))

    def subcontract(self):
        self.order.is_subcontracted = 1
        self.first.update(item_code="SERVICE", fg_item="FG", fg_item_qty=4, qty=12, uom="Hour")
        sco = self.add(self.module.SCO, "SCO", company="JF", supplier="SUP", docstatus=1, purchase_order="PO",
                       transaction_date=date(2026, 9, 18), supplier_currency="USD")
        fg = self.line(self.module.SCO, "SCO", "SCO-FG", item_code="FG", purchase_order_item="PO-1", qty=4, stock_uom="Nos")
        service = self.line(self.module.SCO, "SCO", "SCO-SERVICE", extra=True, item_code="SERVICE", purchase_order_item="PO-1", qty=12, rate=Decimal("2"), amount=Decimal("24"))
        scr = self.add(self.module.SCR, "SCR", company="JF", supplier="SUP", docstatus=1, is_return=0, posting_date=date(2026, 10, 4))
        receipt = self.line(self.module.SCR, "SCR", "SCR-FG", item_code="FG", qty=2, stock_uom="Nos", purchase_order="PO", purchase_order_item="PO-1",
                            subcontracting_order="SCO", subcontracting_order_item="SCO-FG", amount=Decimal("137"))
        return sco, fg, service, scr, receipt

    def payment(self, name="PE", reference="PI", reference_type="Purchase Invoice", **changes):
        self.account("BANK", "Bank")
        doc = self.add(self.module.PE, name, **(dict(company="JF", docstatus=1, party_type="Supplier", party="SUP",
            payment_type="Pay", paid_from="BANK", paid_to="AP", paid_from_account_currency="CNY", paid_to_account_currency="USD",
            paid_amount=Decimal("173.25"), received_amount=Decimal("24.75")) | changes))
        row = self.line(self.module.PE, name, name + "-REF", reference_doctype=reference_type, reference_name=reference, allocated_amount=Decimal("24.75"))
        return doc, row

    def journal(self, name="JE", reference="PI", reference_type="Purchase Invoice", **changes):
        self.account("BANK", "Bank")
        doc = self.add(self.module.JE, name, **(dict(company="JF", docstatus=1, posting_date=date(2026, 11, 13)) | changes))
        row = self.line(self.module.JE, name, name + "-AP", account="AP", party_type="Supplier", party="SUP",
                        reference_type=reference_type, reference_name=reference, debit_in_account_currency=Decimal("24.75"), account_currency="USD")
        self.line(self.module.JE, name, name + "-BANK", account="BANK", credit_in_account_currency=Decimal("173.25"), account_currency="CNY")
        return doc, row

    def read(self):
        return self.module.read_procurement_source("JF", "PO")

    def query(self):
        return self.module.query_procurement_sources("JF", "2026-10-01", "2026-10-01", "SUP")

    def test_read_query_native_units_and_read_only(self):
        result = self.read()
        self.assertEqual([row["uom"] for row in result["items"]], ["Pair", "Set"])
        self.assertEqual([row["stock_qty"] for row in result["items"]], [20, 15])
        self.assertEqual(self.query(), [result])
        self.assertTrue(result["readonly"])
        for flag in ("can_write", "quantities_aggregated", "amounts_allocated_to_items"):
            self.assertFalse(result[flag])
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql, self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_direct_po_invoice_native_current_whole_voucher_currency_and_base(self):
        self.invoice()
        result = self.read()
        self.assertEqual(result["purchase_receipts"], [])
        invoice = result["purchase_invoices"][0]
        self.assertEqual(invoice["items"][0]["amount"], "42")
        self.assertEqual(invoice["items"][0]["base_amount"], "294")
        self.assertEqual(invoice["document"]["outstanding_amount"], "17.25")
        self.assertEqual(invoice["document"]["party_account_currency"], "USD")
        self.assertEqual(result["payables"]["balance_basis"], "native_current_outstanding")
        self.assertEqual(result["payables"]["ledger_entries"][0]["amount_in_account_currency"], "42")
        self.assertFalse(invoice["amounts_allocated_to_items"])
        self.assertEqual(self.query(), [result])  # Related future invoices are not date-cut.

    def test_paid_invoice_without_ledger_and_cancelled_history(self):
        doc, row = self.invoice(ledger=False, outstanding_amount=0)
        self.assertEqual(self.read()["purchase_invoices"][0]["document"]["outstanding_amount"], 0)
        doc.docstatus, doc.outstanding_amount = 2, Decimal("42")
        self.assertEqual(self.read()["purchase_invoices"][0]["document"]["docstatus"], 2)

    def test_posted_nonzero_invoice_without_ledger_fails(self):
        self.invoice(ledger=False)
        with self.assertRaises(ValueError):
            self.read()

    def test_split_receipts_invoices_and_same_item_exact_po_row(self):
        self.receipt()
        self.receipt("PR2", "PO-2")
        _d, row = self.invoice()
        row.update(purchase_receipt="PR", pr_detail="PR-ROW")
        _d, row2 = self.invoice("PI2", "PO-2")
        row2.update(purchase_receipt="PR2", pr_detail="PR2-ROW")
        result = self.read()
        self.assertEqual([i["items"][0]["purchase_order_item"] for i in result["purchase_receipts"]], ["PO-1", "PO-2"])
        row2.pr_detail = "PR-ROW"
        row2.purchase_receipt = "PR"
        with self.assertRaises(ValueError):
            self.read()

    def test_receipt_to_invoice_without_direct_po_pair(self):
        self.receipt()
        _doc, row = self.invoice()
        row.update(purchase_order=None, po_detail=None, purchase_receipt="PR", pr_detail="PR-ROW")
        self.assertEqual(self.read()["purchase_invoices"][0]["items"][0]["pr_detail"], "PR-ROW")

    def test_invoice_first_then_receipt_both_native_links_and_exact_cycle(self):
        _doc, invoice = self.invoice()
        _doc, receipt = self.receipt()
        receipt.update(purchase_invoice="PI", purchase_invoice_item="PI-ROW")
        self.assertEqual(self.read()["purchase_receipts"][0]["items"][0]["purchase_invoice_item"], "PI-ROW")
        invoice.update(purchase_receipt="PR", pr_detail="PR-ROW")
        self.assertEqual(len(self.read()["purchase_invoices"]), 1)

    def test_new_subcontract_services_differ_from_finished_goods(self):
        self.subcontract()
        doc, receipt = self.receipt(subcontracting_receipt="SCR")
        receipt.update(item_code="SERVICE", subcontracting_receipt_item="SCR-FG", qty=6, uom="Hour")
        _doc, invoice = self.invoice()
        invoice.update(item_code="SERVICE", purchase_receipt="PR", pr_detail="PR-ROW")
        result = self.read()
        self.assertEqual(result["subcontracting_orders"][0]["document"]["supplier_currency"], "USD")
        self.assertEqual(result["subcontracting_orders"][0]["service_items"][0]["item_code"], "SERVICE")
        scr = result["subcontracting_receipts"][0]
        self.assertEqual(scr["valuation_currency"], "CNY")
        self.assertNotIn("currency", scr["document"])
        self.assertNotIn("stock_qty", scr["items"][0])
        self.assertEqual(result["purchase_receipts"][0]["items"][0]["qty"], 6)
        self.assertEqual(scr["items"][0]["qty"], 2)

    def test_old_subcontract_receipt_without_sco_scr_is_legal(self):
        self.order.update(is_subcontracted=1, is_old_subcontracting_flow=1)
        self.receipt()
        result = self.read()
        self.assertEqual(len(result["purchase_receipts"]), 1)
        self.assertEqual(result["subcontracting_orders"], [])

    def test_secondary_scr_uses_exact_local_finished_row(self):
        self.subcontract()
        secondary = self.line(self.module.SCR, "SCR", "SCR-SECONDARY", item_code="SECONDARY", secondary_item_type="Co-Product",
                              valuation_type="Manual", reference_name="SCR-FG", qty=1, stock_uom="Nos")
        result = self.read()["subcontracting_receipts"][0]
        self.assertEqual(len(result["items"]), 2)
        secondary.reference_name = "UNKNOWN"
        with self.assertRaises((ValueError, PermissionError)):
            self.read()

    def test_returns_preserve_signed_quantities_and_exact_original_row(self):
        self.receipt()
        _doc, returned = self.receipt("RET", is_return=1, return_against="PR")
        returned.update(purchase_receipt_item="PR-ROW", qty=-1, stock_qty=-2)
        self.assertEqual(self.read()["purchase_receipts"][1]["items"][0]["qty"], -1)
        returned.purchase_order_item = "PO-2"
        with self.assertRaises(ValueError):
            self.read()

    def test_invoice_credit_exact_original_row_and_currency(self):
        self.invoice()
        _doc, credit = self.invoice("CREDIT", is_return=1, return_against="PI", outstanding_amount=Decimal("-5"))
        credit.update(purchase_invoice_item="PI-ROW", qty=-1, amount=Decimal("-5"))
        invoice = self.read()["purchase_invoices"][0]
        self.assertEqual(invoice["document"]["name"], "CREDIT")
        self.assertEqual(invoice["items"][0]["amount"], "-5")

    def test_return_with_only_original_link_is_found(self):
        self.receipt()
        _doc, returned = self.receipt("RET", is_return=1, return_against="PR")
        returned.update(purchase_order=None, purchase_order_item=None, purchase_receipt_item="PR-ROW", qty=-1)
        self.assertEqual(len(self.read()["purchase_receipts"]), 2)

    def test_corrupted_return_flag_or_parent_still_discovered(self):
        self.receipt()
        doc, returned = self.receipt("RET", is_return=1, return_against="PR")
        returned.update(purchase_order=None, purchase_order_item=None, purchase_receipt_item="PR-ROW")
        for changes in (dict(is_return=0), dict(is_return=1, return_against=None)):
            with self.subTest(changes=changes):
                doc.update(changes)
                with self.assertRaises(ValueError):
                    self.read()

    def test_companion_invoice_other_order_and_unlinked_rows_authorized(self):
        self.po("OTHER")
        self.line("Purchase Order", "OTHER", "OTHER-ROW", qty=7)
        self.invoice()
        self.line("Purchase Invoice", "PI", "PI-OTHER", purchase_order="OTHER", po_detail="OTHER-ROW", qty=1)
        self.line("Purchase Invoice", "PI", "PI-UNLINKED", qty=1)
        invoice = self.read()["purchase_invoices"][0]
        self.assertEqual([r["name"] for r in invoice["items"]], ["PI-ROW"])
        self.assertEqual([r["name"] for r in invoice["other_items"]], ["PI-OTHER"])
        self.assertEqual([r["name"] for r in invoice["unlinked_items"]], ["PI-UNLINKED"])
        self.denied.add(("Purchase Order", "OTHER", "read"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_supplier_company_status_and_item_scope(self):
        self.receipt()
        document = self.documents["Purchase Receipt", "PR"]
        for field, value in (("company", "OTHER"), ("supplier", "OTHER")):
            with self.subTest(field=field):
                old = document[field]
                document[field] = value
                with self.assertRaises(ValueError):
                    self.read()
                document[field] = old
        self.order.docstatus = 0
        with self.assertRaises(ValueError):
            self.read()

    def test_missing_exact_child_does_not_match_item(self):
        _doc, row = self.receipt()
        row.purchase_order_item = None
        with self.assertRaises(ValueError):
            self.read()

    def test_field_permissions_masks_and_native_projection_omission(self):
        for mode in ("hidden", "omitted", "masked"):
            with self.subTest(mode=mode):
                self.hidden.clear()
                self.omitted.clear()
                self.metas["Purchase Order Item"]["fg_item"].mask = 0
                if mode == "masked":
                    self.metas["Purchase Order Item"]["fg_item"].mask = 1
                else:
                    getattr(self, mode)["Purchase Order Item"] = {"fg_item"}
                with self.assertRaises(PermissionError):
                    self.read()

    def test_optional_money_omitted_without_full_document_fallback(self):
        self.invoice()
        self.hidden["Purchase Invoice"] = {"grand_total", "base_grand_total"}
        self.hidden["Purchase Invoice Item"] = {"amount", "base_amount"}
        result = self.read()["purchase_invoices"][0]
        self.assertNotIn("grand_total", result["document"])
        self.assertNotIn("amount", result["items"][0])

    def test_table_permission_and_mask_are_required(self):
        for changes in (dict(permlevel=1), dict(permlevel=0, mask=1)):
            self.metas["Purchase Order"]["items"].update(changes)
            with self.assertRaises(PermissionError):
                self.read()

    def test_company_select_only_supported_and_currency_mask_denied(self):
        self.denied.add(("Company", "JF", "read"))
        self.read()
        self.metas["Company"]["default_currency"].mask = 1
        with self.assertRaises(PermissionError):
            self.read()

    def test_hidden_downstream_or_item_denies_entire_source(self):
        self.receipt()
        for key in (("Purchase Receipt Item", "PR-ROW"), ("Purchase Receipt", "PR"), ("Item", "ITEM")):
            self.list_denied.add(key)
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.read()
            self.list_denied.clear()

    def test_malformed_parent_identity_discovery_and_complete_companion_guard(self):
        _doc, row = self.receipt()
        for field, value in (("parenttype", "Other"), ("parentfield", "other")):
            original = row[field]
            row[field] = value
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.read()
            row[field] = original
        companion = self.line("Purchase Receipt", "PR", "PR-OTHER", purchase_order=None, purchase_order_item=None)
        companion.parentfield = "other"
        with self.assertRaises(PermissionError):
            self.read()

    def test_sco_service_item_references_found_even_with_bad_parent_order(self):
        sco, fg, service, _scr, _row = self.subcontract()
        sco.purchase_order = "UNKNOWN"
        fg.purchase_order_item = None
        with self.assertRaises((PermissionError, ValueError)):
            self.read()

    def test_payment_and_journal_document_level_sources_keep_native_currency_values(self):
        self.invoice()
        self.payment()
        self.journal()
        self.ledger("PLE-PE", voucher_type="Payment Entry", voucher_no="PE", amount=Decimal("-173.25"), amount_in_account_currency=Decimal("-24.75"))
        self.ledger("PLE-JE", voucher_type="Journal Entry", voucher_no="JE", amount=Decimal("-173.25"), amount_in_account_currency=Decimal("-24.75"))
        result = self.read()["payables"]
        self.assertEqual({i["doctype"] for i in result["voucher_sources"]}, {"Purchase Order", "Purchase Invoice", "Payment Entry", "Journal Entry"})
        self.assertEqual(len(result["ledger_entries"]), 3)
        self.assertFalse(result["amounts_allocated_to_items"])
        self.assertEqual(len(next(item for item in result["voucher_sources"] if item["doctype"] == "Journal Entry")["accounts"]), 2)
        self.assertEqual(next(item for item in result["voucher_sources"] if item["doctype"] == "Payment Entry")["references"][0]["reference_name"], "PI")

    def test_direct_advance_pe_without_ledger_is_visible(self):
        self.account("AP", "Payable", "USD")
        self.payment(reference="PO", reference_type="Purchase Order")
        sources = self.read()["payables"]["voucher_sources"]
        self.assertIn("Payment Entry", {i["doctype"] for i in sources})

    def test_payment_other_invoice_source_closes_even_without_related_purchase_links(self):
        self.invoice()
        _doc, other = self.invoice("OTHERPI", outstanding_amount=0, ledger=False)
        other.update(purchase_order=None, po_detail=None)
        self.payment()
        self.line(self.module.PE, "PE", "PE-OTHER", reference_doctype="Purchase Invoice", reference_name="OTHERPI")
        self.assertIn("OTHERPI", {i["document"]["name"] for i in self.read()["payables"]["voucher_sources"]})
        self.denied.add(("Purchase Invoice", "OTHERPI", "read"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_ledger_sources_scope_status_account_and_currency(self):
        self.invoice()
        ledger = self.documents[self.module.PLE, "PLE-PI"]
        for field, value in (("account_currency", "CNY"), ("account_type", "Receivable"), ("voucher_type", "UNKNOWN"), ("against_voucher_no", "MISSING"),
                             ("company", "OTHER"), ("party", "OTHER"), ("party_type", "Customer")):
            old = ledger[field]
            ledger[field] = value
            with self.subTest(field=field), self.assertRaises((PermissionError, ValueError)):
                self.read()
            ledger[field] = old
        self.documents["Purchase Invoice", "PI"].docstatus = 0
        with self.assertRaises(ValueError):
            self.read()

    def test_payable_account_and_payment_party_are_exact(self):
        self.invoice()
        doc, _row = self.payment()
        doc.party = "OTHER"
        with self.assertRaises(ValueError):
            self.read()
        doc.party = "SUP"
        self.documents["Account", "AP"].is_group = 1
        with self.assertRaises(ValueError):
            self.read()

    def repost(self, status="Queued", parenttype="Repost Payment Ledger", childtype="Repost Payment Ledger Items", **changes):
        values = dict(company="JF", docstatus=1, repost_status=status, status=status) | changes
        self.add(parenttype, "JOB", **values)
        self.add(childtype, "JOB-ROW", parent="JOB", parenttype=parenttype,
                 parentfield="repost_vouchers" if parenttype == "Repost Payment Ledger" else "vouchers", voucher_type="Purchase Invoice", voucher_no="PI")

    def test_pending_failed_reposts_guard_settled_no_ledger_invoices(self):
        self.invoice(ledger=False, outstanding_amount=0)
        for status in ("Queued", "In Progress", "Failed"):
            self.repost(status)
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.read()
        self.repost("Completed")
        self.read()
        self.repost("Cancelled", "Repost Accounting Ledger", "Repost Accounting Ledger Items")
        self.read()

    def test_guest_replica_exact_inputs_dates_and_supplier(self):
        for user in ("Guest",):
            self.frappe.session.user = user
            with self.assertRaises(PermissionError):
                self.read()
        self.frappe.session.user = "native-reader"
        self.frappe.conf.read_from_replica = True
        with self.assertRaises(ValueError):
            self.read()
        self.frappe.conf.clear()
        for company, order in ((" JF", "PO"), ("JF", " PO"), (None, "PO"), ("JF", "")):
            with self.subTest(company=company, order=order), self.assertRaises(ValueError):
                self.module.read_procurement_source(company, order)
        for start, end, supplier in (("2026-10-2", "2026-10-03", "SUP"), ("2026-10-03", "2026-10-01", "SUP"), (None, "2026-10-01", "SUP"), ("2026-10-01", "2026-10-01", "")):
            with self.subTest(start=start), self.assertRaises(ValueError):
                self.module.query_procurement_sources("JF", start, end, supplier)

    def test_query_supplier_dates_search_and_batch_limit(self):
        self.po("OTHER", supplier="OTHER")
        self.po("OLD", transaction_date=date(2026, 9, 30))
        self.assertEqual(len(self.query()), 1)
        self.assertEqual(self.module.query_procurement_sources("JF", "2026-10-01", "2026-10-01", "SUP", "UNKNOWN"), [])
        self.module.LIMIT = 0
        with self.assertRaises(ValueError):
            self.query()

    def test_batch_readable_population_but_related_sources_must_be_complete(self):
        self.po("HIDDEN")
        self.list_denied.add(("Purchase Order", "HIDDEN"))
        self.assertEqual(len(self.query()), 1)
        self.receipt()
        self.list_denied.add(("Purchase Receipt", "PR"))
        with self.assertRaises(PermissionError):
            self.query()

    def test_source_limit(self):
        self.module.SOURCE_LIMIT = 1
        with self.assertRaises(ValueError):
            self.read()

    def test_source_change_without_modified_and_empty_reverse_late_insert(self):
        original = self.first.qty
        count = [0]
        def hook(_filters):
            count[0] += 1
            if count[0] == 2:
                self.first.qty = original + 1
        self.list_hooks["Purchase Order Item"] = hook
        with self.assertRaises((PermissionError, ValueError)):
            self.read()
        self.first.qty = original
        self.list_hooks.clear()
        count[0] = 0
        def add_late(filters):
            if "purchase_order_item" in filters:
                count[0] += 1
                if count[0] == 2:
                    self.receipt()
        self.list_hooks["Purchase Receipt Item"] = add_late
        with self.assertRaises((PermissionError, ValueError)):
            self.read()

    def test_final_field_mask_permission_and_authorization_raw_changes(self):
        count = [0]
        def hook(filters):
            if filters == {"name": "PO"}:
                count[0] += 1
                if count[0] == 2:
                    self.denied.add(("Purchase Order", "PO", "read"))
        self.list_hooks["Purchase Order"] = hook
        with self.assertRaises(PermissionError):
            self.read()
        self.list_hooks.clear()
        self.denied.clear()
        count[0] = 0
        def shares(_filters):
            count[0] += 1
            if count[0] == 1:
                self.add("User Permission", "NEW", user="native-reader", allow="Supplier", for_value="SUP")
        self.raw_hooks["DocShare"] = shares
        with self.assertRaises(PermissionError):
            self.read()

    def flow(self, item="ITEM", name="FLOW", **changes):
        self.add("Customer", "CUST")
        self.add("Sales Order", "SO", company="JF", customer="CUST", docstatus=1)
        self.line("Sales Order", "SO", "SO-ROW", item_code=item)
        return self.add(self.module.FLOW, name, **(dict(company="JF", docstatus=0, item_code=item,
            sales_order="SO", sales_order_item="SO-ROW", source_changed=0) | changes))

    def test_flow_material_and_exact_sales_source_closure(self):
        flow = self.flow()
        self.line(self.module.FLOW, "FLOW", "FLOW-RAW", item_code="RAW")
        self.first.jf_flow_order = "FLOW"
        _doc, row = self.receipt()
        row.jf_flow_order = "FLOW"
        self.read()
        for key in (("Item", "RAW", "read"), ("Sales Order", "SO", "read"), ("Customer", "CUST", "read")):
            self.denied.add(key)
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.read()
            self.denied.clear()
        flow.sales_order_item = "UNKNOWN"
        with self.assertRaises(ValueError):
            self.read()

    def test_flow_source_changed_preserves_historical_item(self):
        flow = self.flow()
        self.first.jf_flow_order = "FLOW"
        self.documents["Sales Order Item", "SO-ROW"].item_code = "FG"
        with self.assertRaises(ValueError):
            self.read()
        flow.source_changed = 1
        self.read()

    def test_hidden_companion_flow_material_and_malformed_parentfield(self):
        self.flow()
        self.first.jf_flow_order = "FLOW"
        material = self.line(self.module.FLOW, "FLOW", "FLOW-RAW", item_code="RAW")
        material.parentfield = "other"
        with self.assertRaises(PermissionError):
            self.read()

    def test_sco_fg_po_service_pair_flags_and_full_service_permission(self):
        sco, fg, service, _scr, _row = self.subcontract()
        self.list_denied.add(("Subcontracting Order Service Item", "SCO-SERVICE"))
        with self.assertRaises(PermissionError):
            self.read()
        self.list_denied.clear()
        service.item_code = "ITEM"
        with self.assertRaises(ValueError):
            self.read()
        service.item_code = "SERVICE"
        self.order.is_old_subcontracting_flow = 1
        with self.assertRaises(ValueError):
            self.read()
        self.order.is_old_subcontracting_flow = 0
        self.first.fg_item = "ITEM"
        with self.assertRaises(ValueError):
            self.read()

    def stage_subcontract(self):
        _sco, fg, _service, _scr, receipt = self.subcontract()
        self.flow(item="FG")
        self.add("Item", "SEMI")
        self.add("Job Card", "JC", company="JF", docstatus=0, work_order="WO", production_item="FG",
            operation_id="WO-OP", operation="PLATING", finished_good="SEMI", semi_fg_bom="STAGE-BOM",
            track_semi_finished_goods=1, is_subcontracted=1)
        self.add("Work Order", "WO", company="JF", docstatus=1, production_item="FG", jf_flow_order="FLOW",
            track_semi_finished_goods=1)
        self.line("Work Order", "WO", "WO-OP", operation="PLATING", finished_good="SEMI", is_subcontracted=1)
        self.add("BOM", "STAGE-BOM", company="JF", docstatus=1, item="SEMI")
        self.line("BOM", "STAGE-BOM", "BOM-RAW", item_code="RAW")
        self.first.update(fg_item="SEMI", job_card="JC", bom="STAGE-BOM", jf_flow_order="FLOW")
        for row in (fg, receipt):
            row.update(item_code="SEMI", job_card="JC", bom="STAGE-BOM", jf_flow_order="FLOW")
        return fg, receipt

    def test_native_stage_closure_keeps_service_and_two_partial_receipts_without_qty_conversion(self):
        _fg, receipt = self.stage_subcontract()
        self.add(self.module.SCR, "SCR2", company="JF", supplier="SUP", docstatus=1, is_return=0,
                 posting_date=date(2026, 10, 5))
        row = self.line(self.module.SCR, "SCR2", "SCR2-FG", **{key: value for key, value in receipt.items()
            if key not in self.module.CHILD | {"doctype"}})
        row.qty = 1
        result = self.read()
        self.assertEqual(result["items"][0]["item_code"], "SERVICE")
        self.assertEqual(result["items"][0]["fg_item"], "SEMI")
        self.assertEqual([(entry["items"][0]["item_code"], entry["items"][0]["qty"])
            for entry in result["subcontracting_receipts"]], [("SEMI", 2), ("SEMI", 1)])
        self.assertTrue(result["readonly"])
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.set_value.assert_not_called()

    def test_stage_cannot_replace_job_card_operation_or_bom_or_bypass_projected_permissions(self):
        fg, _receipt = self.stage_subcontract()
        card = self.documents["Job Card", "JC"]
        for document, field, value in ((fg, "job_card", "WRONG"), (fg, "bom", "WRONG"),
                (card, "operation_id", "WRONG"), (self.documents["BOM", "STAGE-BOM"], "item", "FG"),
                (self.documents["Work Order", "WO"], "company", "OTHER")):
            old = document[field]
            document[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, PermissionError)):
                self.read()
            document[field] = old
        for key in (("Job Card", "JC", "read"), ("Work Order", "WO", "read"), ("BOM", "STAGE-BOM", "read")):
            self.denied.add(key)
            with self.assertRaises(PermissionError):
                self.read()
            self.denied.clear()
        for doctype, field in (("Work Order Operation", "finished_good"), ("Purchase Order Item", "job_card")):
            self.hidden[doctype] = {field}
            with self.assertRaises((ValueError, PermissionError)):
                self.read()
            self.hidden.clear()

    def test_cancelled_stage_history_keeps_exact_native_links_and_live_edits_invalidate_read(self):
        self.stage_subcontract()
        self.documents["Work Order", "WO"].docstatus = 2
        self.documents["Job Card", "JC"].docstatus = 2
        self.read()
        counter = [0]
        def change(filters):
            if filters == {"name": "JC"}:
                counter[0] += 1
                if counter[0] == 2:
                    self.documents["Job Card", "JC"].semi_fg_bom = "OTHER"
        self.list_hooks["Job Card"] = change
        with self.assertRaises((ValueError, PermissionError)):
            self.read()

    def test_scr_return_primary_and_secondary_original_rows(self):
        self.subcontract()
        self.line(self.module.SCR, "SCR", "SCR-SECONDARY", item_code="SECONDARY", secondary_item_type="Scrap",
                  valuation_type="Manual", reference_name="SCR-FG", qty=1, stock_uom="Nos")
        self.add(self.module.SCR, "RETURN", company="JF", supplier="SUP", docstatus=1, is_return=1, return_against="SCR", posting_date=date(2026, 10, 9))
        self.line(self.module.SCR, "RETURN", "RET-FG", item_code="FG", qty=-1, stock_uom="Nos", subcontracting_receipt_item="SCR-FG",
                  purchase_order="PO", purchase_order_item="PO-1", subcontracting_order="SCO", subcontracting_order_item="SCO-FG")
        self.line(self.module.SCR, "RETURN", "RET-SECONDARY", item_code="SECONDARY", qty=-1, stock_uom="Nos", subcontracting_receipt_item="SCR-SECONDARY",
                  secondary_item_type="Scrap", valuation_type="Manual", reference_name="SCR-FG")
        receipts = self.read()["subcontracting_receipts"]
        self.assertEqual([row["qty"] for row in receipts[0]["items"]], [-1, -1])
        self.assertEqual(receipts[0]["document"]["name"], "RETURN")

    def test_on_account_return_can_keep_explicit_po_without_fabricated_original(self):
        self.receipt("RETURN", is_return=1)
        result = self.read()["purchase_receipts"][0]
        self.assertIsNone(result["document"]["return_against"])
        self.assertIsNone(result["items"][0]["purchase_receipt_item"])

    def expense(self):
        self.account("EXP", "Expense Account", "CNY", root_type="Expense")
        self.account("AP-CNY", "Payable", "CNY", root_type="Liability")
        self.add("Cost Center", "CC", company="JF", is_group=0)
        doc = self.add(self.module.EXPENSE, "EXPENSE", company="JF", supplier="SUP", docstatus=1, posting_date=date(2026, 11, 13),
            payment_method="赊账", expense_account="EXP", payable_account="AP-CNY", cost_center="CC", amount=Decimal("11"), journal_entry="EXPJE")
        journal = self.add(self.module.JE, "EXPJE", company="JF", docstatus=1, posting_date=date(2026, 11, 13), jf_expense_voucher="EXPENSE",
                           voucher_type="Journal Entry", multi_currency=0)
        debit = self.line(self.module.JE, "EXPJE", "EXPJE-EXP", account="EXP", debit_in_account_currency=Decimal("11"),
                          credit_in_account_currency=0, account_currency="CNY", cost_center="CC")
        credit = self.line(self.module.JE, "EXPJE", "EXPJE-AP", account="AP-CNY", party_type="Supplier", party="SUP",
                           credit_in_account_currency=Decimal("11"), debit_in_account_currency=0, account_currency="CNY")
        self.account("BANK", "Bank")
        self.add(self.module.PE, "EXPPE", company="JF", docstatus=1, party_type="Supplier", party="SUP", payment_type="Pay", paid_from="BANK", paid_to="AP-CNY",
                 paid_from_account_currency="CNY", paid_to_account_currency="CNY")
        self.line(self.module.PE, "EXPPE", "EXPPE-PO", reference_doctype="Purchase Order", reference_name="PO")
        self.line(self.module.PE, "EXPPE", "EXPPE-JE", reference_doctype="Journal Entry", reference_name="EXPJE")
        return doc, journal, debit, credit

    def test_credit_expense_native_two_row_source_and_cancelled_history(self):
        expense, journal, debit, credit = self.expense()
        self.assertIn("EXPJE", {item["document"]["name"] for item in self.read()["payables"]["voucher_sources"]})
        expense.docstatus, journal.docstatus = 2, 2
        self.read()
        credit.credit_in_account_currency = Decimal("10")
        with self.assertRaises(ValueError):
            self.read()

    def test_credit_expense_permission_currency_type_and_exact_journal_link(self):
        expense, journal, debit, credit = self.expense()
        self.hidden["Journal Entry Account"] = {"cost_center"}
        with self.assertRaises(PermissionError):
            self.read()
        self.hidden.clear()
        for target, field, value in ((expense, "journal_entry", "OTHER"), (journal, "multi_currency", 1),
                                     (journal, "posting_date", date(2026, 11, 12)), (self.documents["Account", "EXP"], "account_currency", "USD"),
                                     (self.documents["Account", "EXP"], "root_type", "Asset")):
            old = target[field]
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.read()
            target[field] = old
        self.denied.add((self.module.EXPENSE, "EXPENSE", "read"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_no_ledger_posted_reference_sources_guard_repost_and_hidden_membership(self):
        self.invoice(ledger=False, outstanding_amount=0)
        self.payment()
        self.repost("Failed", voucher="PE")
        self.documents["Repost Payment Ledger Items", "JOB-ROW"].update(voucher_type="Payment Entry", voucher_no="PE")
        with self.assertRaises(ValueError):
            self.read()
        self.documents["Repost Payment Ledger", "JOB"].repost_status = "Completed"
        self.list_denied.add(("Payment Entry Reference", "PE-REF"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_end_read_mask_and_table_recheck_after_native_list(self):
        count = [0]
        def hook(filters):
            if filters == {"name": "PO"}:
                count[0] += 1
                if count[0] == 2:
                    self.metas["Purchase Order Item"]["fg_item"].mask = 1
        self.list_hooks["Purchase Order"] = hook
        with self.assertRaises(PermissionError):
            self.read()
        count[0] = 0
        self.metas["Purchase Order Item"]["fg_item"].mask = 0
        def table(filters):
            if filters == {"name": "PO"}:
                count[0] += 1
                if count[0] == 2:
                    self.metas["Purchase Order"]["items"].mask = 1
        self.list_hooks["Purchase Order"] = table
        with self.assertRaises(PermissionError):
            self.read()

    def test_final_raw_detects_new_source_after_last_permission_projection(self):
        count = [0]
        def hook(filters):
            if "purchase_order_item" in filters:
                count[0] += 1
                if count[0] == 3:
                    self.receipt()
        self.raw_hooks["Purchase Receipt Item"] = hook
        with self.assertRaises(PermissionError):
            self.read()

    def test_query_multiple_same_supplier_orders_isolate_exact_rows(self):
        self.po("OTHER")
        self.line("Purchase Order", "OTHER", "OTHER-ROW", qty=7)
        self.invoice()
        self.line("Purchase Invoice", "PI", "PI-OTHER", purchase_order="OTHER", po_detail="OTHER-ROW", qty=1)
        results = self.query()
        self.assertEqual([r["purchase_order"]["name"] for r in results], ["OTHER", "PO"])
        self.assertEqual([r["purchase_invoices"][0]["items"][0]["name"] for r in results], ["PI-OTHER", "PI-ROW"])

    def test_settled_no_ledger_payment_journal_still_validate_exact_ap_accounts(self):
        self.invoice(ledger=False, outstanding_amount=0)
        payment, _row = self.payment()
        journal, account_row = self.journal()
        self.read()
        for target, changes in ((payment, dict(paid_to="BANK", paid_to_account_currency="CNY")),
                                (account_row, dict(account="BANK", account_currency="CNY"))):
            saved = dict(target)
            target.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.read()
            target.clear()
            target.update(saved)
        self.account("OTHER-AP", "Payable", "USD")
        account_row.account = "OTHER-AP"
        with self.assertRaises(ValueError):
            self.read()

    def test_subcontract_po_flow_service_must_be_explicit_material_per_current_hook(self):
        self.subcontract()
        self.flow("FG")
        self.first.jf_flow_order = "FLOW"
        with self.assertRaises(ValueError):
            self.read()
        self.line(self.module.FLOW, "FLOW", "FLOW-SERVICE", item_code="SERVICE")
        self.read()

    def sales_invoice(self, name="SI", **changes):
        self.add("Customer", "CUST")
        self.account("AR", "Receivable", "USD")
        self.add(self.module.SI, name, **(dict(company="JF", customer="CUST", docstatus=1, is_return=0,
                                            debit_to="AR", currency="USD", party_account_currency="USD") | changes))
        return self.line(self.module.SI, name, name + "-ROW", item_code="ITEM")

    def mixed_journal(self):
        self.invoice(ledger=False, outstanding_amount=0)
        doc, row = self.journal()
        invoice = self.sales_invoice()
        customer = self.line(self.module.JE, "JE", "JE-CUST", account="AR", party_type="Customer", party="CUST",
                             account_currency="USD", reference_type="Sales Invoice", reference_name="SI",
                             credit_in_account_currency=Decimal("9"))
        return doc, row, invoice, customer

    def test_mixed_supplier_customer_je_complete_other_source_without_other_balance(self):
        self.mixed_journal()
        result = self.read()["payables"]
        other = result["other_reference_sources"]
        self.assertEqual([(item["doctype"], item["document"]["name"]) for item in other], [("Sales Invoice", "SI")])
        self.assertFalse(other[0]["balance_included"])
        self.assertNotIn("outstanding_amount", other[0]["document"])
        self.assertEqual([row["name"] for row in other[0]["items"]], ["SI-ROW"])
        self.assertNotIn("Sales Invoice", {item["doctype"] for item in result["voucher_sources"]})
        for key in (("Customer", "CUST", "read"), ("Sales Invoice", "SI", "read")):
            self.denied.add(key)
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.read()
            self.denied.clear()

    def test_mixed_je_other_supplier_pi_exact_source_and_account(self):
        self.invoice(ledger=False, outstanding_amount=0)
        self.journal()
        self.add("Supplier", "OTHER")
        self.po("OTHERPO", supplier="OTHER")
        self.line("Purchase Order", "OTHERPO", "OTHERPO-ROW", item_code="ITEM", qty=1)
        _doc, invoice = self.invoice("OTHERPI", ledger=False, outstanding_amount=0, supplier="OTHER")
        invoice.update(purchase_order="OTHERPO", po_detail="OTHERPO-ROW")
        other_row = self.line(self.module.JE, "JE", "JE-OTHER", account="AP", party_type="Supplier", party="OTHER", account_currency="USD",
                              reference_type="Purchase Invoice", reference_name="OTHERPI")
        result = self.read()["payables"]
        self.assertEqual([item["document"]["name"] for item in result["other_reference_sources"]], ["OTHERPI"])
        self.assertNotIn("OTHERPI", {item["document"]["name"] for item in result["voucher_sources"]})
        self.account("OTHER-AP", "Payable", "USD")
        other_row.account = "OTHER-AP"
        with self.assertRaises(ValueError):
            self.read()

    def test_mixed_je_counterpart_exact_sales_links_and_full_permissions(self):
        _doc, _row, invoice, customer = self.mixed_journal()
        self.add("Sales Order", "SO", company="JF", customer="CUST", docstatus=1)
        self.line("Sales Order", "SO", "SO-ROW", item_code="ITEM")
        invoice.update(sales_order="SO", so_detail="SO-ROW")
        self.assertEqual({item["document"]["name"] for item in self.read()["payables"]["other_reference_sources"]}, {"SI", "SO"})
        self.denied.add(("Sales Order", "SO", "read"))
        with self.assertRaises(PermissionError):
            self.read()
        self.denied.clear()
        invoice.so_detail = "MISSING"
        with self.assertRaises(ValueError):
            self.read()

    def test_mixed_je_hidden_child_wrong_party_currency_and_late_counterpart_change(self):
        _doc, _row, invoice, customer = self.mixed_journal()
        self.list_denied.add(("Sales Invoice Item", "SI-ROW"))
        with self.assertRaises(PermissionError):
            self.read()
        self.list_denied.clear()
        customer.party = "WRONG"
        with self.assertRaises((PermissionError, ValueError)):
            self.read()
        customer.party = "CUST"
        customer.account_currency = "CNY"
        with self.assertRaises(ValueError):
            self.read()
        customer.account_currency = "USD"
        count = [0]
        def hook(filters):
            if filters == {"name": "SI"}:
                count[0] += 1
                if count[0] == 2:
                    invoice.item_code = "FG"
        self.list_hooks["Sales Invoice"] = hook
        with self.assertRaises(PermissionError):
            self.read()

    def test_other_source_je_does_not_contaminate_next_batch_order(self):
        self.mixed_journal()
        self.po("UNRELATED")
        self.line("Purchase Order", "UNRELATED", "UNRELATED-ROW")
        results = self.query()
        self.assertTrue(results[0]["payables"]["other_reference_sources"])
        self.assertEqual(results[1]["payables"]["other_reference_sources"], [])

    def test_repost_raw_membership_validates_parenttype_parentfield_company_and_job(self):
        self.invoice(ledger=False, outstanding_amount=0)
        self.repost("Failed")
        job = self.documents["Repost Payment Ledger", "JOB"]
        child = self.documents["Repost Payment Ledger Items", "JOB-ROW"]
        for target, field, value in ((child, "parenttype", "Wrong"), (child, "parentfield", "wrong"),
                                     (child, "parent", "MISSING"), (job, "company", "OTHER")):
            old = target[field]
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.read()
            target[field] = old

    def test_final_batch_root_membership_recheck_finds_visible_late_po(self):
        count = [0]
        def hook(filters):
            if "transaction_date" in filters:
                count[0] += 1
                if count[0] == 2:
                    self.po("LATE")
                    self.line("Purchase Order", "LATE", "LATE-ROW")
        self.list_hooks["Purchase Order"] = hook
        with self.assertRaises(PermissionError):
            self.query()

    def test_companion_sales_historical_flow_retains_exact_changed_source(self):
        _doc, _row, invoice, customer = self.mixed_journal()
        flow = self.flow(source_changed=1)
        self.documents["Sales Order Item", "SO-ROW"].item_code = "FG"
        invoice.update(sales_order="SO", so_detail="SO-ROW", jf_flow_order="FLOW")
        self.read()
        flow.source_changed = 0
        with self.assertRaises(ValueError):
            self.read()

    def test_native_builder_ignoring_empty_in_cannot_expand_return_discovery(self):
        self.receipt()
        self.invoice()
        self.add("Supplier", "OTHER")
        self.po("OLD", supplier="OTHER")
        self.line("Purchase Order", "OLD", "OLD-ROW")
        _doc, other_receipt = self.receipt("OLDPR", supplier="OTHER")
        other_receipt.update(purchase_order="OLD", purchase_order_item="OLD-ROW")
        _doc, other_invoice = self.invoice("OLDPI", supplier="OTHER", ledger=False, outstanding_amount=0)
        other_invoice.update(purchase_order="OLD", po_detail="OLD-ROW")
        matches = self.matches
        self.matches = lambda row, filters: matches(row, {
            field: wanted for field, wanted in filters.items()
            if not (isinstance(wanted, list) and wanted == ["in", []])})
        result = self.read()
        self.assertEqual([source["document"]["name"] for source in result["purchase_receipts"]], ["PR"])
        self.assertEqual([source["document"]["name"] for source in result["purchase_invoices"]], ["PI"])
        for call in self.frappe.get_list.call_args_list:
            self.assertNotIn(["in", []], call.kwargs["filters"].values())


if __name__ == "__main__":
    unittest.main()
