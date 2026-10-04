"""Reconciliation deltas and complete ledger access must not fabricate stock."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class Row(dict):
    def __getattr__(self, name):
        return self.get(name)


class InventoryReportsTest(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.PermissionError = PermissionError
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.getdate = lambda value: value
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        for name in ("column", "readable_documents", "report_scope", "selected_warehouses", "stock_items"):
            setattr(scope, name, lambda *args, **kwargs: None)
        from decimal import Decimal
        scope.decimal_number = lambda value: Decimal(str(value or 0))
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils, "jifeng_erp.inventory_scope": scope}):
            spec = importlib.util.spec_from_file_location(
                "inventory_test_subject", pathlib.Path(__file__).parents[1] / "jifeng_erp/inventory_reports.py")
            self.report = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.report)
        self.doc = Row(name="RECON", company="JF", purpose="Stock Reconciliation", posting_date="2026-09-30")
        self.row = Row(name="ROW", item_code="ITEM", item_name="Item", warehouse="A", stock_uom="Nos",
                       current_qty=27, qty=30, current_amount=270, amount=300,
                       quantity_difference=3, amount_difference=30)
        self.entry = Row(name="SLE", voucher_type="Stock Reconciliation", voucher_no="RECON", voucher_detail_no="ROW",
                         company="JF", item_code="ITEM", warehouse="A", stock_uom="Nos", actual_qty=0,
                         stock_value_difference=30, is_adjustment_entry=0, qty_after_transaction=30,
                         posting_date="2026-09-30", docstatus=1, is_cancelled=0)

    def movement(self, entries=None):
        return self.report.reconciliation_movement(self.doc, self.row, [self.entry] if entries is None else entries, "CNY")

    def test_zero_actual_quantity_still_has_real_count_gain(self):
        result = self.movement()
        self.assertEqual((result["before_qty"], result["after_qty"], result["quantity_difference"]), (27, 30, 3))

    def test_value_only_revaluation_has_no_quantity_movement(self):
        self.row.update(qty=27, quantity_difference=0, amount=324, amount_difference=54)
        self.entry["stock_value_difference"] = 54
        self.entry["qty_after_transaction"] = 27
        result = self.movement()
        self.assertEqual((result["quantity_difference"], result["value_difference"], result["adjustment_only"]), (0, 54, 0))

    def test_value_writeoff_uses_ledger_and_keeps_quantity(self):
        self.row.update(current_qty=0, qty=0, quantity_difference=0, current_amount=15, amount=15, amount_difference=-15)
        self.entry.update(stock_value_difference=-15, is_adjustment_entry=1)
        result = self.movement()
        self.assertEqual((result["before_value"], result["after_value"], result["quantity_difference"]), (15, 0, 0))
        self.assertEqual(result["adjustment_only"], 1)

    def test_value_writeoff_cannot_move_quantity(self):
        self.entry["is_adjustment_entry"] = 1
        with self.assertRaisesRegex(ValueError, "不能改变"):
            self.movement()

    def test_source_child_and_warehouse_must_match_exactly(self):
        for field, bad in (("voucher_detail_no", "OTHER"), ("warehouse", "B"), ("stock_uom", "Kg")):
            original = self.entry[field]
            self.entry[field] = bad
            with self.assertRaisesRegex(ValueError, "不一致"):
                self.movement()
            self.entry[field] = original

    def test_stale_displayed_value_cannot_claim_posted_difference(self):
        self.entry["stock_value_difference"] = 31
        with self.assertRaisesRegex(ValueError, "重算"):
            self.movement()

    def test_reset_ledger_balance_must_equal_counted_quantity(self):
        self.entry["qty_after_transaction"] = 31
        with self.assertRaisesRegex(ValueError, "余额与盘点数量"):
            self.movement()

    def test_counted_serial_bundle_must_remove_old_and_add_new_pool(self):
        self.row.update(current_serial_and_batch_bundle="OLD", serial_and_batch_bundle="NEW")
        old = Row(self.entry, actual_qty=-27, stock_value_difference=-270)
        new = Row(self.entry, actual_qty=30, stock_value_difference=300)
        self.assertEqual(self.movement([old, new])["quantity_difference"], 3)
        new["actual_qty"] = 29
        with self.assertRaisesRegex(ValueError, "移入数量"):
            self.movement([old, new])

    def test_no_change_row_in_mixed_voucher_can_have_no_ledger(self):
        self.row.update(qty=27, amount=270, quantity_difference=0, amount_difference=0)
        result = self.movement([])
        self.assertEqual((result["quantity_difference"], result["ledger_value_difference"]), (0, 0))

    def test_quantity_change_without_ledger_is_rejected(self):
        self.row.update(amount=270, amount_difference=0)
        with self.assertRaisesRegex(ValueError, "缺少"):
            self.movement([])

    def test_mixed_writeoff_and_count_reset_is_rejected(self):
        extra = Row(self.entry)
        extra["is_adjustment_entry"] = 1
        with self.assertRaisesRegex(ValueError, "混有"):
            self.movement([self.entry, extra])

    def test_hidden_sle_is_denied_instead_of_partial_balance(self):
        self.report.frappe.get_all = lambda *args, **kwargs: [self.entry, Row(self.entry, name="HIDDEN")]
        self.report.frappe.get_list = lambda *args, **kwargs: ["SLE"]
        with self.assertRaisesRegex(PermissionError, "部分库存余额"):
            self.report._complete_ledger({})

    def test_full_document_permission_is_checked_even_after_list_access(self):
        self.report.frappe.get_all = lambda *args, **kwargs: [self.entry]
        self.report.frappe.get_list = lambda *args, **kwargs: ["SLE"]
        self.report.frappe.get_meta = lambda *args: types.SimpleNamespace(get_table_fields=lambda: [])
        self.report.frappe.get_doc = lambda *args: types.SimpleNamespace(has_permission=lambda mode: False)
        with self.assertRaisesRegex(PermissionError, "部分库存余额"):
            self.report._complete_ledger({})

    def test_native_filter_currency_corruption_is_repaired_for_nonzero_and_historic_zero_pairs(self):
        self.report.frappe._dict = Row
        # Reproduce native filter_items_with_no_transactions: currency is not
        # excluded from flt(), and a zero pair with no period turnover is pruned.
        class NativeBalance:
            def __init__(self, filters):
                self.filters = filters
                self.company_currency = "CNY"
                self.item_warehouse_map = {}

            def prepare_item_warehouse_map(self, entry, key):
                pass

            def prepare_item_warehouse_map_for_current_period(self):
                for warehouse, qty, value in (("A", 10, 100), ("B", 0, 0)):
                    key = ("ITEM", warehouse)
                    self.item_warehouse_map[key] = Row(item_code="ITEM", warehouse=warehouse,
                        company="JF", currency=self.company_currency, stock_uom="Nos",
                        bal_qty=qty, bal_val=value, val_rate=10 if qty else 0)
                    self.prepare_item_warehouse_map(None, key)
                for row in self.item_warehouse_map.values():
                    row["currency"] = 0.0
                del self.item_warehouse_map["ITEM", "B"]

            def run(self):
                self.prepare_item_warehouse_map_for_current_period()
                return [], list(self.item_warehouse_map.values())

        native = types.ModuleType("erpnext.stock.report.stock_balance.stock_balance")
        native.StockBalanceReport = NativeBalance
        with patch.dict(sys.modules, {native.__name__: native}):
            rows = self.report._native_balance(types.SimpleNamespace(include_zero_stock_items=1), [], {})
        self.assertEqual([(row["warehouse"], row["bal_qty"], row["bal_val"], row["currency"])
                          for row in rows], [("A", 10, 100, "CNY"), ("B", 0, 0, "CNY")])
        self.assertTrue(all(row["company"] == "JF" and row["stock_uom"] == "Nos" for row in rows))


if __name__ == "__main__":
    unittest.main()
