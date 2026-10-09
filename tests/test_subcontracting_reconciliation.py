"""Focused projections for source-bounded subcontracting reconciliation."""

import importlib.util
from datetime import date
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Row(dict):
    __getattr__ = dict.get


def load_module():
    frappe = types.ModuleType("frappe")
    frappe.session = types.SimpleNamespace(user="qa@example.test")
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.whitelist = lambda **kwargs: (lambda fn: fn)
    frappe.throw = lambda message, exc=None: (_ for _ in ()).throw((exc or ValueError)(message))
    model = types.ModuleType("frappe.model")
    model.get_permitted_fields = lambda *args, **kwargs: set()
    utils = types.ModuleType("frappe.utils")
    utils.getdate = lambda value: value
    path = Path(__file__).parents[1] / "jifeng_erp" / "subcontracting_reconciliation.py"
    spec = importlib.util.spec_from_file_location("subcontracting_reconciliation_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model, "frappe.utils": utils}):
        spec.loader.exec_module(module)
    return module


class TestSubcontractingReconciliationProjections(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_only_explicit_partner_profile_kind_classifies_business(self):
        self.assertTrue(self.module._kind_matches("电镀厂", "plating"))
        self.assertTrue(self.module._kind_matches("外发工厂", "outsourcing"))
        self.assertTrue(self.module._kind_matches("未分类", "all"))
        self.assertFalse(self.module._kind_matches("未分类", "plating"))
        self.assertFalse(self.module._kind_matches("外发工厂", "plating"))

    def test_payable_rows_are_restricted_to_exact_subcontracting_pi_closure(self):
        rows = [
            {"voucher_type": "Purchase Invoice", "voucher_no": "PI-SUB"},
            {"voucher_type": "Purchase Invoice", "voucher_no": "PI-REGULAR"},
            {"voucher_type": "Payment Entry", "voucher_no": "PE-OTHER"},
        ]
        selected = self.module._select_payables(rows, {"PI-SUB"})
        self.assertEqual([row["voucher_no"] for row in selected], ["PI-SUB"])

    def test_missing_native_ap_row_retains_source_and_does_not_invent_cash_payment(self):
        row = self.module._select_payables([], {"PI-SUB"})[0]
        self.assertEqual(row["voucher_no"], "PI-SUB")
        self.assertIsNone(row["paid"])
        self.assertIsNone(row["outstanding"])

    def test_duplicate_same_kind_profiles_cannot_supply_unique_classification(self):
        self.module._fields = Mock()
        self.module.frappe.get_list = Mock(return_value=[Row(name="PROFILE-1"), Row(name="PROFILE-2")])
        self.module.frappe.get_all = Mock(return_value=["PROFILE-1", "PROFILE-2"])
        with self.assertRaisesRegex(ValueError, "唯一档案"):
            self.module._partner_kind("QA", "SUP")

    def test_no_profile_is_unclassified_and_hidden_profile_rejects_partial_classification(self):
        self.module._fields = Mock()
        self.module.frappe.get_list = Mock(return_value=[])
        self.module.frappe.get_all = Mock(return_value=[])
        self.assertEqual(self.module._partner_kind("QA", "SUP"), "未分类")
        self.module.frappe.get_all.return_value = ["HIDDEN"]
        with self.assertRaises(self.module.frappe.PermissionError):
            self.module._partner_kind("QA", "SUP")

    def test_child_query_uses_native_parent_permissions_and_validates_table(self):
        table = Row(fieldtype="Table", options="Purchase Invoice Item", permlevel=0)
        meta = types.SimpleNamespace(get_field=lambda name: table, get_permlevel_access=lambda **kwargs: [0])
        self.module.frappe.get_meta = Mock(return_value=meta)
        self.module._fields = Mock()
        self.module.frappe.get_list = Mock(return_value=[Row(name="ROW", parent="PI")])
        self.module.frappe.get_all = Mock(return_value=["ROW"])
        self.module._child_rows("Purchase Invoice Item", "Purchase Invoice", "items", {}, {"purchase_order"})
        self.assertEqual(self.module.frappe.get_list.call_args.kwargs["parent_doctype"], "Purchase Invoice")
        table.options = "OTHER"
        with self.assertRaisesRegex(ValueError, "明细表定义"):
            self.module._child_rows("Purchase Invoice Item", "Purchase Invoice", "items", {}, {"purchase_order"})

    def test_standard_document_identity_fields_need_no_custom_field_definition(self):
        self.module.get_permitted_fields = lambda *args, **kwargs: {"name", "docstatus"}
        self.module.frappe.get_meta = Mock(return_value=types.SimpleNamespace(
            get_field=lambda name: None, get_permlevel_access=lambda **kwargs: [0]))
        self.module._fields("Purchase Invoice", {"name", "docstatus"})

    def test_mixed_invoice_and_its_credit_cannot_attribute_regular_purchase_to_subcontracting(self):
        invoices = {"PI-SUB": Row(is_return=0), "PI-MIX": Row(is_return=0)}
        returns = {"PI-CREDIT": Row(is_return=1, return_against="PI-MIX")}
        self.module._visible_docs = Mock(side_effect=[invoices, returns])
        self.module._child_rows = Mock(side_effect=[
            [Row(parent="PI-SUB", purchase_order="PO-SUB"), Row(parent="PI-MIX", purchase_order="PO-SUB")],
            [Row(parent="PI-SUB", purchase_order="PO-SUB"), Row(parent="PI-MIX", purchase_order="PO-SUB"),
             Row(parent="PI-MIX", purchase_order="PO-REGULAR"), Row(parent="PI-CREDIT", purchase_order=None)],
        ])
        docs, exact = self.module._purchase_invoice_closure("QA", "SUP", date(2026, 10, 1), date(2026, 10, 8), {"PO-SUB": Row()})
        self.assertEqual(set(docs), {"PI-SUB", "PI-MIX", "PI-CREDIT"})
        self.assertEqual(exact, {"PI-SUB"})

    def test_flow_filter_cannot_allocate_po_shared_with_another_flow(self):
        self.module._child_rows = Mock(return_value=[
            Row(parent="PO-ONE", jf_flow_order="FLOW-1"),
            Row(parent="PO-MIX", jf_flow_order="FLOW-1"),
            Row(parent="PO-MIX", jf_flow_order="FLOW-2"),
            Row(parent="PO-UNLINKED", jf_flow_order=None),
        ])
        self.assertEqual(self.module._flow_purchase_orders(
            {"PO-ONE": Row(), "PO-MIX": Row(), "PO-UNLINKED": Row()}, "FLOW-1"), {"PO-ONE"})

    def test_financial_cutoff_keeps_older_order_with_no_current_logistics_and_exact_flow_scope(self):
        self.module._visible_docs = Mock(return_value={"SCO-OLD": Row(), "SCO-OTHER": Row()})
        self.module._child_rows = Mock(return_value=[Row(parent="SCO-OLD", jf_flow_order="FLOW-1"),
                                                     Row(parent="SCO-OTHER", jf_flow_order="FLOW-2")])
        selected = self.module._financial_source_names("QA", "SUP", date(2026, 10, 8), "FLOW-1", "SCO-OLD")
        self.assertEqual(selected, {"SCO-OLD"})
        filters = self.module._visible_docs.call_args.args[1]
        self.assertEqual(filters["transaction_date"], ["<=", "2026-10-08"])
        self.assertEqual(filters["name"], "SCO-OLD")

    def test_subcontracting_sources_require_linked_subcontracted_po(self):
        self.module._visible_docs = Mock(side_effect=[
            {"SCO": Row(purchase_order="PO")}, {"PO": Row(is_subcontracted=0, company="QA", supplier="SUP")},
        ])
        with self.assertRaisesRegex(ValueError, "采购来源无法核对"):
            self.module._source_order_data("QA", "SUP", {"SCO"})
        self.module._visible_docs = Mock(return_value={"SCO": Row(purchase_order=None)})
        with self.assertRaisesRegex(ValueError, "完整的采购来源"):
            self.module._source_order_data("QA", "SUP", {"SCO"})

    def payables(self, report_rows, *, currency="CNY", account_currency="CNY", exact=True):
        provider = types.ModuleType("jifeng_erp.supplier_reconciliation")
        provider.query_payables = Mock(return_value={"company": "QA", "supplier": "SUP", "report_date": "2026-10-08", "rows": report_rows})
        invoice = Row(posting_date="2026-10-02", credit_to="AP", currency=currency, grand_total="4", rounded_total="4", outstanding_amount="0")
        self.module._visible_docs = Mock(return_value={"PI-SUB": invoice})
        self.module._account = Mock(return_value=Row(name="AP", account_currency=account_currency, account_type="Payable"))
        with patch.dict(sys.modules, {"jifeng_erp.supplier_reconciliation": provider}):
            return self.module._payable_rows("QA", "SUP", date(2026, 10, 8), {"PI-SUB": invoice}, {"PI-SUB"} if exact else set())

    def test_cleared_native_invoice_shows_real_zero_balance_without_inferred_cash(self):
        row = self.payables([])[0]
        self.assertEqual(row["invoiced"], "4")
        self.assertEqual(row["outstanding"], "0")
        self.assertIsNone(row["paid"])
        self.assertEqual(row["currency"], "CNY")
        self.assertIn("发票原生余额（当前", row["source_scope"])

    def test_missing_ap_foreign_currency_mismatch_does_not_label_false_balance(self):
        row = self.payables([], currency="USD", account_currency="CNY")[0]
        self.assertIsNone(row["invoiced"])
        self.assertIsNone(row["outstanding"])
        self.assertIn("币种不同", row["source_scope"])

    def test_mixed_invoice_amount_stays_unassigned_and_regular_pi_is_excluded(self):
        row = self.payables([
            {"voucher_type": "Purchase Invoice", "voucher_no": "PI-SUB", "invoiced": "84", "outstanding": "84"},
            {"voucher_type": "Purchase Invoice", "voucher_no": "PI-REGULAR", "invoiced": "80", "outstanding": "80"},
        ], exact=False)[0]
        self.assertIsNone(row["invoiced"])
        self.assertIsNone(row["outstanding"])
        self.assertIn("混合", row["source_scope"])

    def payment(self, *, exact=True, unallocated="0", allocated="4", account_kind="Bank"):
        refs = [Row(parent="PE", reference_doctype="Purchase Invoice", reference_name="PI-SUB", allocated_amount=allocated)]
        self.module._child_rows = Mock(side_effect=[refs, refs])
        self.module._visible_docs = Mock(return_value={"PE": Row(posting_date="2026-10-03", paid_from="BANK", paid_to="AP",
            paid_amount="1", paid_from_account_currency="USD", total_allocated_amount="4", unallocated_amount=unallocated)})
        self.module._account = Mock(side_effect=[Row(account_currency="USD", account_type=account_kind), Row(account_type="Payable")])
        return self.module._payments("QA", "SUP", date(2026, 10, 1), date(2026, 10, 8), {"PO-SUB": Row()}, {"PI-SUB": Row()}, {"PI-SUB"} if exact else set())[0]

    def test_fx_payment_displays_native_cash_amount_and_account_currency_without_proration(self):
        row = self.payment()
        self.assertEqual(row["paid_amount"], "1")
        self.assertEqual(row["currency"], "USD")

    def test_mixed_invoice_unallocated_or_zero_reference_cannot_show_cash_as_subcontracting(self):
        for options in ({"exact": False}, {"unallocated": "1"}, {"allocated": "0"}, {"account_kind": "Receivable"}):
            with self.subTest(options=options):
                row = self.payment(**options)
                self.assertIsNone(row["paid_amount"])
                self.assertIsNone(row["currency"])

    def test_payments_require_all_references_in_scope_before_showing_amount(self):
        refs = [Row(reference_doctype="Purchase Invoice", reference_name="PI-SUB")]
        self.assertEqual(self.module._payment_scope(refs, {"PI-SUB"}, {"PO-SUB"}), "invoice")
        self.assertEqual(self.module._payment_scope(
            [*refs, Row(reference_doctype="Purchase Invoice", reference_name="PI-REGULAR")],
            {"PI-SUB"}, {"PO-SUB"}), "mixed")
        self.assertEqual(self.module._payment_scope(
            [Row(reference_doctype="Purchase Order", reference_name="PO-SUB")],
            {"PI-SUB"}, {"PO-SUB"}), "purchase_order")
        self.assertEqual(self.module._payment_scope([], {"PI-SUB"}, {"PO-SUB"}), "unlinked")

    def test_cancelled_history_keeps_only_requested_source_flow_and_order(self):
        self.module._child_rows = Mock(return_value=[
            Row(parent="SCR-ONE", jf_flow_order="FLOW-1", subcontracting_order="SCO-1"),
            Row(parent="SCR-OTHER", jf_flow_order="FLOW-2", subcontracting_order="SCO-1"),
            Row(parent="SCR-ORDER", jf_flow_order="FLOW-1", subcontracting_order="SCO-2"),
        ])
        docs = {name: Row(name=name) for name in ("SCR-ONE", "SCR-OTHER", "SCR-ORDER")}
        result = self.module._history_flow_documents("Subcontracting Receipt", "Subcontracting Receipt Item",
                                                      docs, "FLOW-1", "SCO-1")
        self.assertEqual(set(result), {"SCR-ONE"})
        required = self.module._child_rows.call_args.args[4]
        self.assertEqual(required, {"jf_flow_order", "subcontracting_order"})

    def test_unfiltered_cancelled_history_preserves_existing_complete_scope(self):
        self.module._child_rows = Mock()
        docs = {"SCR": Row(name="SCR")}
        self.assertIs(self.module._history_flow_documents("Subcontracting Receipt", "Subcontracting Receipt Item",
                                                         docs, None), docs)
        self.module._child_rows.assert_not_called()

    def test_full_cancelled_history_does_not_print_other_flow_receipts_or_material_vouchers(self):
        docs = lambda prefix, **extra: {prefix + str(i): Row(name=prefix + str(i),
            transaction_date="2026-10-02", posting_date="2026-10-02", **extra) for i in (1, 2)}
        orders = docs("SCO", purchase_order="PO1")
        orders["SCO2"].update(purchase_order="PO2")
        self.module._visible_docs = Mock(side_effect=[orders, docs("PO"), docs("SCR"), docs("STE")])
        self.module._child_rows = Mock(side_effect=[
            [Row(parent="SCO1", jf_flow_order="FLOW-1"), Row(parent="SCO2", jf_flow_order="FLOW-2")],
            [Row(parent="PO1", jf_flow_order="FLOW-1"), Row(parent="PO2", jf_flow_order="FLOW-2")],
            [Row(parent="SCR1", jf_flow_order="FLOW-1", subcontracting_order="SCO1"),
             Row(parent="SCR2", jf_flow_order="FLOW-2", subcontracting_order="SCO2")],
            [Row(parent="STE1", jf_flow_order="FLOW-1"), Row(parent="STE2", jf_flow_order="FLOW-2")],
            [Row(parent="PO1", jf_flow_order="FLOW-1")], [], [],
        ])
        rows = self.module._cancelled_history("QA", "SUP", date(2026, 10, 1), date(2026, 10, 8),
                                               {}, {}, "FLOW-1", "SCO1")
        self.assertEqual({row["voucher_no"] for row in rows}, {"SCO1", "PO1", "SCR1", "STE1"})
        self.assertEqual(self.module._visible_docs.call_args_list[1].args[1]["name"], ["in", ["PO1"]])


if __name__ == "__main__":
    unittest.main()
