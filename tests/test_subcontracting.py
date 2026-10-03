"""Subcontracting net quantities must survive native return mapping and unit conversion."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


class Row(dict):
    def __getattr__(self, name):
        return self.get(name)

    def as_dict(self):
        return dict(self)


class TestSubcontractingMovements(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe._dict = Row
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.getdate = lambda value: value
        utils.today = lambda: "2026-09-30"
        utils.date_diff = lambda *args: 0
        references = types.ModuleType("jifeng_erp.sample_source_queries")
        references.check_protected_reference = Mock(return_value=False)
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils,
                                      "jifeng_erp.sample_source_queries": references}):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp/subcontracting.py"
            spec = importlib.util.spec_from_file_location("subcontracting_test_subject", path)
            self.report = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.report)
        self.order = {"O1": Row(name="O1", subcontracting_order="SCO", item_code="FG", stock_uom="Nos",
                                 company="JF", supplier="SUP", jf_flow_order="FLOW", qty=10, conversion_factor=1)}
        self.row = Row(name="R1", subcontracting_order_item="O1", subcontracting_order="SCO",
                       item_code="FG", stock_uom="Nos", jf_flow_order="FLOW", idx=1,
                       qty=3, rejected_qty=1, process_loss_qty=1, conversion_factor=2,
                       warehouse="Good", rejected_warehouse="Rejected")
        self.original = self.doc("SCR", [self.row])
        self.return_row = Row(name="RET1", subcontracting_order_item="O1", subcontracting_order="SCO",
                              subcontracting_receipt_item="R1", item_code="FG", stock_uom="Nos", jf_flow_order="FLOW",
                              qty=-1, rejected_qty=0, process_loss_qty=0, conversion_factor=1, idx=1,
                              warehouse="Good", rejected_warehouse="Rejected")
        self.return_doc = self.doc("RETURN", [self.return_row], is_return=1, return_against="SCR")

    @staticmethod
    def doc(name, rows, **kwargs):
        return types.SimpleNamespace(name=name, items=rows, company="JF", supplier="SUP", supplier_name="Supplier",
                                     posting_date="2026-09-30", is_return=kwargs.get("is_return", 0),
                                     return_against=kwargs.get("return_against"))

    def movements(self):
        return self.report.receipt_movements(self.order, {"SCR": self.original, "RETURN": self.return_doc})

    def ledger(self, selected=None):
        filters = Row(company="JF")
        selected = selected if selected is not None else {"SCR": self.original, "RETURN": self.return_doc}
        with patch.object(self.report, "_scope", return_value=(filters, "2026-09-01", "2026-09-30")), \
                patch.object(self.report, "_readable_documents", side_effect=[selected, {"SCR": self.original}]), \
                patch.object(self.report, "_orders", return_value={}), \
                patch.object(self.report, "_order_items", return_value=self.order), \
                patch.object(self.report, "_receipt_closure", return_value={"SCR": self.original, "RETURN": self.return_doc}):
            return self.report.subcontracting_receipt_ledger(filters)[1]

    def test_each_voucher_uses_own_stock_conversion(self):
        rows = self.movements()
        self.assertEqual(sum(r["accepted_qty"] for r in rows), Decimal(5))
        self.assertEqual(sum(r["rejected_qty"] for r in rows), Decimal(2))
        self.assertEqual(sum(r["loss_qty"] for r in rows), Decimal(2))
        self.assertEqual(sum(r["covered_qty"] for r in rows), Decimal(9))

    def test_receipt_and_return_check_exact_output_flow_and_company(self):
        rows = self.ledger()
        self.assertEqual([call.args for call in self.report.check_protected_reference.call_args_list],
                         [("Jifeng Flow Order", "FLOW"), ("Jifeng Flow Order", "FLOW")])
        self.assertEqual([call.kwargs for call in self.report.check_protected_reference.call_args_list],
                         [{"company": "JF"}, {"company": "JF"}])
        self.assertEqual(sum(row["covered_qty"] for row in rows), 9)

    def test_unreadable_protected_flow_does_not_drop_movement_or_fake_total(self):
        self.report.check_protected_reference.side_effect = PermissionError("来源不可读")
        with self.assertRaisesRegex(PermissionError, "来源不可读"):
            self.ledger()

    def test_source_closure_not_in_output_does_not_add_reference_gate(self):
        rows = self.ledger({"RETURN": self.return_doc})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["receipt"], "RETURN")
        self.report.check_protected_reference.assert_called_once_with("Jifeng Flow Order", "FLOW", company="JF")

    def test_progress_without_receipts_checks_output_order_flow(self):
        item = Row(self.order["O1"], order_status="Open", transaction_date="2026-09-30")
        filters = Row(company="JF")
        with patch.object(self.report, "_scope", return_value=(filters, "2026-09-01", "2026-09-30")), \
                patch.object(self.report, "_orders", return_value={}), \
                patch.object(self.report, "_order_items", return_value={"O1": item}), \
                patch.object(self.report, "_receipt_closure", return_value={}), \
                patch.object(self.report, "receipt_movements", return_value=[]):
            self.report.check_protected_reference.side_effect = PermissionError("来源不可读")
            with self.assertRaisesRegex(PermissionError, "来源不可读"):
                self.report.subcontracting_progress(filters)
        self.report.check_protected_reference.assert_called_once_with(
            "Jifeng Flow Order", "FLOW", company="JF")

    def test_native_rejected_return_maps_qty_to_rejected_warehouse(self):
        # Native mapper's temporary flag is not present after a document reload.
        self.return_row["warehouse"] = "Rejected"
        rows = self.movements()
        returned = next(r for r in rows if r["entry_type"] == "实物退回")
        self.assertEqual((returned["accepted_qty"], returned["rejected_qty"]), (0, -1))
        self.assertEqual(sum(r["accepted_qty"] for r in rows), 6)

    def test_native_cached_returned_qty_is_not_physical_return(self):
        self.row["returned_qty"] = 999
        self.assertEqual(sum(r["returned_qty"] for r in self.movements()), 1)

    def test_return_limit_is_compared_in_stock_units(self):
        self.return_row["qty"], self.return_row["conversion_factor"] = -4, 1
        self.movements()  # Four stock units do not exceed 3 * 2 accepted stock units.
        self.return_row["conversion_factor"] = 2
        with self.assertRaisesRegex(ValueError, "累计超过"):
            self.movements()

    def test_same_warehouse_cannot_guess_accepted_vs_rejected_pool(self):
        self.row["rejected_warehouse"] = "Good"
        with self.assertRaisesRegex(ValueError, "无法唯一"):
            self.movements()

    def test_physical_return_cannot_reverse_confirmed_loss(self):
        self.return_row["process_loss_qty"] = -1
        with self.assertRaisesRegex(ValueError, "不能冲回"):
            self.movements()

    def test_missing_original_order_row_is_rejected(self):
        self.row["subcontracting_order_item"] = None
        with self.assertRaisesRegex(ValueError, "来源订单明细"):
            self.movements()

    def test_missing_return_source_cannot_be_silently_skipped(self):
        self.return_row["subcontracting_receipt_item"] = None
        with self.assertRaisesRegex(ValueError, "来源收货明细"):
            self.movements()

    def test_secondary_product_does_not_add_main_order_coverage(self):
        self.original.items.append(Row(self.row, name="SECONDARY", secondary_item_type="By Product", qty=99))
        self.assertEqual(sum(r["covered_qty"] for r in self.movements()), 9)

    def test_unknown_or_zero_conversion_is_rejected(self):
        self.return_row["conversion_factor"] = 0
        with self.assertRaisesRegex(ValueError, "换算系数"):
            self.movements()

    def test_other_company_source_is_rejected(self):
        self.return_doc.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "公司"):
            self.movements()


if __name__ == "__main__":
    unittest.main()
