"""Focused projections for source-bounded subcontracting reconciliation."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


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

    def test_missing_native_ap_row_rejects_partial_balance(self):
        with self.assertRaisesRegex(ValueError, "不完整对应"):
            self.module._select_payables([], {"PI-SUB"})

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


if __name__ == "__main__":
    unittest.main()
