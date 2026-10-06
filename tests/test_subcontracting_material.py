"""Verify exact material sources and native stock-unit movement signs."""

import importlib.util
import pathlib
import sys
import types
import unittest
import fixture_adapter
from decimal import Decimal
from unittest.mock import ANY, Mock, patch


class Record(dict):
    __getattr__ = dict.get


class TestSubcontractingMaterial(unittest.TestCase):
    def setUp(self):
        self.item = Record(name="OI-1", parent="SCO-1", item_code="FG-1", item_name="成品", jf_flow_order="FLOW-1")
        self.supplied = Record(name="RM-1", parent="SCO-1", reference_name="OI-1",
                               main_item_code="FG-1", rm_item_code="RAW-1", stock_uom="Kg")
        self.order = Record(name="SCO-1", company="公司", supplier="供应商", supplier_name="供应商",
                            items=[self.item], supplied_items=[self.supplied])
        self.row = Record(name="SED-1", idx=1, sco_rm_detail="RM-1", item_code="RAW-1", item_name="原料",
                          subcontracted_item="FG-1", stock_uom="Kg", qty=2, conversion_factor=5,
                          transfer_qty=10, jf_flow_order="FLOW-1", s_warehouse="原料仓", t_warehouse="委外仓")
        self.entry = Record(name="STE-1", subcontracting_order="SCO-1", company="公司", supplier="供应商",
                            posting_date="2026-09-30", posting_time="10:00:00", docstatus=1,
                            purpose="Send to Subcontractor", is_return=0, items=[self.row])
        self.orders = {self.order.name: self.order}
        self.selected = {self.item.name: self.item}

        fixture_adapter.material(self)

    def movements(self):
        return self.module.material_movements(self.orders, self.selected, {self.entry.name: self.entry})

    def test_stock_quantity_is_transfer_qty_and_return_has_negative_net(self):
        outgoing = self.movements()[0]
        self.assertEqual((outgoing["issued_qty"], outgoing["returned_qty"], outgoing["net_qty"]),
                         (Decimal(10), Decimal(0), Decimal(10)))
        self.assertEqual(outgoing["stock_uom"], "Kg")
        self.entry.update(purpose="Material Transfer", is_return=1)
        self.row.update(s_warehouse="委外仓", t_warehouse="原料仓", transfer_qty=3)
        returned = self.movements()[0]
        self.assertEqual((returned["issued_qty"], returned["returned_qty"], returned["net_qty"]),
                         (Decimal(0), Decimal(3), Decimal(-3)))

    def test_output_flow_uses_exact_entry_company_and_keeps_material_unit(self):
        row = self.movements()[0]
        self.module.check_protected_reference.assert_called_once_with(
            "Jifeng Flow Order", "FLOW-1", company="公司")
        self.assertEqual((row["stock_uom"], row["net_qty"]), ("Kg", Decimal(10)))

    def test_unreadable_protected_flow_refuses_entire_issue_or_return_output(self):
        self.module.check_protected_reference.side_effect = PermissionError("来源不可读")
        for changes in ({}, {"purpose": "Material Transfer", "is_return": 1}):
            with self.subTest(changes=changes):
                self.entry.update(changes)
                with self.assertRaisesRegex(PermissionError, "来源不可读"):
                    self.movements()

    def test_filtered_finished_good_or_material_does_not_check_unreturned_flow(self):
        self.module.check_protected_reference.side_effect = PermissionError("来源不可读")
        self.selected.clear()
        self.assertEqual(self.movements(), [])
        self.selected[self.item.name] = self.item
        self.assertEqual(self.module.material_movements(self.orders, self.selected,
                         {self.entry.name: self.entry}, "OTHER-RAW"), [])
        self.module.check_protected_reference.assert_not_called()

    def test_out_of_scope_finished_good_needs_a_valid_source(self):
        self.selected.clear()
        self.assertEqual(self.movements(), [])
        self.row["sco_rm_detail"] = None
        with self.assertRaisesRegex(ValueError, "来源订单原料明细"):
            self.movements()

    def test_sources_cannot_be_borrowed_from_another_order(self):
        self.supplied["parent"] = "SCO-OTHER"
        with self.assertRaisesRegex(ValueError, "来源订单原料明细"):
            self.movements()

    def test_supplied_item_must_link_to_exact_order_item(self):
        self.supplied["reference_name"] = "OI-OTHER"
        with self.assertRaisesRegex(ValueError, "对应订单商品明细"):
            self.movements()

    def test_material_finished_good_and_units_must_match(self):
        for field, value in (("item_code", "RAW-OTHER"), ("subcontracted_item", "FG-OTHER"), ("stock_uom", "Nos")):
            with self.subTest(field=field):
                original = self.row[field]
                self.row[field] = value
                with self.assertRaisesRegex(ValueError, "库存单位与来源"):
                    self.movements()
                self.row[field] = original

    def test_source_flow_and_company_must_match(self):
        self.row["jf_flow_order"] = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "流程单与来源"):
            self.movements()
        self.row["jf_flow_order"] = "FLOW-1"
        self.entry["company"] = "其他公司"
        with self.assertRaisesRegex(ValueError, "公司或供应商"):
            self.movements()

    def test_non_native_or_unsubmitted_movements_rejected(self):
        for changes in (dict(docstatus=0), dict(purpose="Material Issue"), dict(is_return=1)):
            with self.subTest(changes=changes):
                original = dict(self.entry)
                self.entry.update(changes)
                with self.assertRaisesRegex(ValueError, "已提交的发料或退料"):
                    self.movements()
                self.entry.update(original)

    def test_non_finite_zero_and_negative_stock_quantities_rejected(self):
        for qty in ("nan", "Infinity", "-1", 0, "garbled"):
            with self.subTest(qty=qty):
                self.row["transfer_qty"] = qty
                with self.assertRaises(ValueError):
                    self.movements()

    def test_raw_material_filter_does_not_mix_units(self):
        self.assertEqual(self.module.material_movements(self.orders, self.selected, {self.entry.name: self.entry}, "RAW-OTHER"), [])
        rows = self.module.material_movements(self.orders, self.selected, {self.entry.name: self.entry}, "RAW-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_uom"], "Kg")

    def test_api_queries_native_parents_then_checks_source_orders(self):
        filters = Record(company="公司", from_date="2026-09-01", to_date="2026-09-30")
        columns, rows = self.module.subcontracting_material_ledger(filters)
        self.module._scope.assert_called_once_with(filters, ("Stock Entry", "Subcontracting Order"), guard=ANY)
        self.assertIsInstance(self.module._scope.call_args.kwargs["guard"], self.guard.LedgerSourceGuard)
        self.module._orders.assert_called_once_with(Record(**filters, supplier=None), names={"SCO-1"}, complete=True, guard=ANY)
        self.assertEqual([call.args[1]["purpose"] for call in self.module._readable_documents.call_args_list],
                         ["Send to Subcontractor", "Material Transfer"])
        self.assertEqual(self.module._readable_documents.call_args_list[1].args[1]["is_return"], 1)
        self.assertEqual(rows[0]["issued_qty"], Decimal(10))
        self.assertIn("stock_entry_detail", [column["fieldname"] for column in columns])

    def test_supplier_filter_applies_after_all_source_orders_are_checked(self):
        self.assertEqual(self.module.material_movements(self.orders, self.selected,
                         {self.entry.name: self.entry}, supplier="其他供应商"), [])
        filters = Record(company="公司", from_date="2026-09-01", to_date="2026-09-30", supplier="供应商")
        self.assertEqual(len(self.module.subcontracting_material_ledger(filters)[1]), 1)
        source_filters = self.module._orders.call_args.args[0]
        self.assertIsNone(source_filters.supplier)
        self.assertEqual(filters.supplier, "供应商")

    def test_api_raw_material_filter_requires_read_permission(self):
        filters = Record(company="公司", from_date="2026-09-01", to_date="2026-09-30", rm_item_code="RAW-HIDDEN")
        native_list = self.frappe.get_list
        self.frappe.get_list = Mock(side_effect=lambda doctype, **kwargs: [] if doctype == "Item" else native_list(doctype, **kwargs))
        with self.assertRaises(PermissionError):
            self.module.subcontracting_material_ledger(filters)
        self.module._readable_documents.assert_not_called()


if __name__ == "__main__":
    unittest.main()
