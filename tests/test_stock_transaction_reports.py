"""Inventory business rows must agree with exact permitted native posting rows."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class TestStockTransactionReports(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.column = lambda field, label, *args, **kwargs: {"fieldname": field, "label": label}

        def number(value):
            value = Decimal(str(value or 0))
            if not value.is_finite():
                throw("不是有限数字")
            return value

        scope.decimal_number = number
        scope.report_scope = Mock()
        scope.readable_documents = Mock()
        scope.selected_warehouses = Mock(return_value=["A", "B"])
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "stock_transaction_reports.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "jifeng_erp.inventory_scope": scope}):
            spec = importlib.util.spec_from_file_location("stock_transaction_reports_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.row = Record(name="SED-1", idx=1, parent="STE-1", parenttype="Stock Entry",
                          item_code="RAW", item_name="原料", qty=1.5, uom="Box", transfer_qty=3,
                          stock_uom="Kg", s_warehouse="A", t_warehouse="B", jf_flow_order="FLOW",
                          basic_rate=999, basic_amount=999, conversion_factor=2)
        self.doc = Record(name="STE-1", company="公司", docstatus=1, purpose="Material Transfer",
                          posting_date="2026-09-30", posting_time="10:00:00", items=[self.row], is_return=0)
        self.item = Record(name="RAW", stock_uom="Kg", is_stock_item=1)
        self.items = {"RAW": self.item}
        self.warehouses = {name: Record(name=name, company="公司", is_group=0) for name in ("A", "B")}
        self.ledgers = {"SLE-S": self.sle("SLE-S", "A", -3, -30), "SLE-T": self.sle("SLE-T", "B", 3, 32)}
        self.origins = {}
        self.filters = Record(company="公司", mode="transfer", from_date="2026-09-01", to_date="2026-09-30")

    def sle(self, name, warehouse, qty, value):
        return Record(name=name, voucher_type="Stock Entry", voucher_no="STE-1", voucher_detail_no="SED-1",
                      company="公司", item_code="RAW", stock_uom="Kg", docstatus=1, is_cancelled=0,
                      posting_date="2026-09-30", warehouse=warehouse, actual_qty=qty, stock_value_difference=value)

    def movements(self):
        return self.module.stock_entry_movements([(self.doc, self.row)], self.ledgers, self.items,
                                                self.warehouses, self.origins, "CNY")

    def test_transfer_counts_one_business_row_and_separate_ledger_sides(self):
        rows = self.movements()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["movement_qty"], rows[0]["source_qty"], rows[0]["target_qty"], rows[0]["net_qty"]),
                         (Decimal(3), Decimal(-3), Decimal(3), Decimal(0)))
        self.assertEqual((rows[0]["source_value"], rows[0]["target_value"], rows[0]["net_value"]),
                         (Decimal(-30), Decimal(32), Decimal(2)))
        self.assertEqual((rows[0]["document_qty"], rows[0]["document_uom"], rows[0]["stock_uom"]),
                         (Decimal("1.5"), "Box", "Kg"))
        self.assertEqual((rows[0]["flow_order"], rows[0]["currency"]), ("FLOW", "CNY"))

    def test_receipt_and_issue_net_signs_come_from_stock_ledger(self):
        self.doc["purpose"] = "Material Receipt"
        self.row["s_warehouse"] = None
        self.ledgers.pop("SLE-S")
        self.assertEqual(self.movements()[0]["net_qty"], Decimal(3))
        self.doc["purpose"] = "Material Issue"
        self.row.update(s_warehouse="A", t_warehouse=None)
        self.ledgers = {"SLE-S": self.sle("SLE-S", "A", -3, -30)}
        self.assertEqual(self.movements()[0]["net_qty"], Decimal(-3))

    def test_same_warehouse_opposite_dimensions_are_not_collapsed(self):
        self.row["t_warehouse"] = "A"
        self.ledgers["SLE-T"]["warehouse"] = "A"
        row = self.movements()[0]
        self.assertEqual((row["source_qty"], row["target_qty"], row["net_qty"]),
                         (Decimal(-3), Decimal(3), Decimal(0)))

    def test_multiple_native_entries_for_one_side_are_checked_as_a_group(self):
        self.ledgers["SLE-S"]["actual_qty"] = -1
        self.ledgers["SLE-S"]["stock_value_difference"] = -10
        self.ledgers["SLE-S2"] = self.sle("SLE-S2", "A", -2, -20)
        self.assertEqual(self.movements()[0]["source_qty"], Decimal(-3))

    def test_wrong_ledger_source_company_item_unit_date_or_status_rejected(self):
        for field, value in (("voucher_type", "Purchase Receipt"), ("voucher_no", "OTHER"),
                             ("company", "其他公司"), ("item_code", "OTHER"), ("stock_uom", "Nos"),
                             ("posting_date", "2026-09-29"), ("docstatus", 0), ("is_cancelled", 1)):
            with self.subTest(field=field):
                original = self.ledgers["SLE-S"][field]
                self.ledgers["SLE-S"][field] = value
                with self.assertRaisesRegex(ValueError, "正式库存台账与凭证来源"):
                    self.movements()
                self.ledgers["SLE-S"][field] = original

    def test_missing_or_wrong_child_ledger_is_not_zero_stock(self):
        self.ledgers.clear()
        with self.assertRaisesRegex(ValueError, "缺少有效库存数量或正式库存台账"):
            self.movements()
        self.ledgers["SLE-OTHER"] = self.sle("SLE-OTHER", "A", -3, -30)
        self.ledgers["SLE-OTHER"]["voucher_detail_no"] = "OTHER-SED"
        with self.assertRaisesRegex(ValueError, "缺少有效库存数量或正式库存台账"):
            self.movements()

    def test_wrong_quantity_direction_or_warehouse_rejected(self):
        for field, value in (("actual_qty", -2), ("actual_qty", 3), ("warehouse", "OTHER")):
            with self.subTest(field=field, value=value):
                original = self.ledgers["SLE-S"][field]
                self.ledgers["SLE-S"][field] = value
                with self.assertRaises(ValueError):
                    self.movements()
                self.ledgers["SLE-S"][field] = original

    def test_zero_quantity_ledger_cannot_supply_a_posted_movement(self):
        self.ledgers["SLE-S"]["actual_qty"] = 0
        with self.assertRaisesRegex(ValueError, "数量方向或仓库"):
            self.movements()

    def test_item_stock_unit_and_warehouse_company_are_checked(self):
        self.item["stock_uom"] = "Nos"
        with self.assertRaisesRegex(ValueError, "库存单位与商品档案"):
            self.movements()
        self.item["stock_uom"] = "Kg"
        self.warehouses["B"]["company"] = "其他公司"
        with self.assertRaisesRegex(ValueError, "来源仓库的公司"):
            self.movements()

    def test_non_stock_row_keeps_document_qty_and_leaves_stock_qty_empty(self):
        self.item["is_stock_item"] = 0
        self.ledgers.clear()
        row = self.movements()[0]
        self.assertEqual(row["document_qty"], Decimal("1.5"))
        self.assertIsNone(row["movement_qty"])
        self.assertIsNone(row["net_qty"])
        self.assertIsNone(row["net_value"])
        self.assertIn("非库存", row["stock_state"])

    def test_non_stock_with_stock_ledger_is_a_conflict(self):
        self.item["is_stock_item"] = 0
        with self.assertRaisesRegex(ValueError, "非库存条目存在库存台账"):
            self.movements()

    def test_warehouse_selection_matches_either_side_and_not_double_rows(self):
        for warehouses in (["A"], ["B"], ["A", "B"]):
            with self.subTest(warehouses=warehouses):
                self.assertEqual(len(self.module._selected_rows({self.doc.name: self.doc}, self.filters, warehouses)), 1)
        self.assertEqual(self.module._selected_rows({self.doc.name: self.doc}, self.filters, ["OTHER"]), [])

    def test_item_and_flow_filters_do_not_guess_other_rows(self):
        self.filters["item_code"] = "OTHER"
        self.assertEqual(self.module._selected_rows({self.doc.name: self.doc}, self.filters, ["A"]), [])
        self.filters.update(item_code=None, flow_order="OTHER-FLOW")
        self.assertEqual(self.module._selected_rows({self.doc.name: self.doc}, self.filters, ["A"]), [])

    def test_subcontract_raw_material_returns_are_excluded_from_transfers(self):
        self.doc["is_return"] = 1
        self.assertEqual(self.module._selected_rows({self.doc.name: self.doc}, self.filters, ["A"]), [])

    def test_child_parent_and_posted_status_guard(self):
        self.row["parent"] = "OTHER-STE"
        with self.assertRaisesRegex(ValueError, "明细与来源凭证"):
            self.module._selected_rows({self.doc.name: self.doc}, self.filters, ["A"])
        self.row["parent"] = "STE-1"
        self.doc["docstatus"] = 0
        with self.assertRaisesRegex(ValueError, "审核状态"):
            self.module._selected_rows({self.doc.name: self.doc}, self.filters, ["A"])

    def set_origin(self):
        self.doc["outgoing_stock_entry"] = "OUT-1"
        self.row.update(against_stock_entry="OUT-1", ste_detail="OUT-SED")
        self.origin_row = Record(name="OUT-SED", parent="OUT-1", parenttype="Stock Entry", item_code="RAW", stock_uom="Kg",
                                 t_warehouse="A", transfer_qty=5)
        self.origin = Record(name="OUT-1", company="公司", purpose="Material Transfer", docstatus=1,
                             add_to_transit=1, items=[self.origin_row])
        self.origins["OUT-1"] = self.origin

    def test_two_stage_transfer_keeps_exact_readable_source(self):
        self.set_origin()
        row = self.movements()[0]
        self.assertEqual((row["outgoing_stock_entry"], row["source_detail"], row["source_read_state"]),
                         ("OUT-1", "OUT-SED", "已核对"))
        self.assertEqual(row["transfer_stage"], "接收来源调拨")

    def test_hidden_outgoing_source_hides_source_names_and_details(self):
        self.set_origin()
        self.origins.clear()
        row = self.movements()[0]
        self.assertEqual((row["outgoing_stock_entry"], row["source_detail"], row["source_read_state"]),
                         ("", "", "来源不可读"))
        self.assertNotIn("OUT-1", repr(row))
        self.assertNotIn("OUT-SED", repr(row))
        self.assertEqual(row["movement_qty"], Decimal(3))

    def test_wrong_outgoing_source_company_detail_item_unit_or_warehouse_rejected(self):
        self.set_origin()
        for target, field, value in ((self.origin, "company", "其他公司"), (self.origin_row, "parent", "OTHER"),
                                     (self.origin_row, "parenttype", "Purchase Receipt"),
                                     (self.origin_row, "item_code", "OTHER"), (self.origin_row, "stock_uom", "Nos"),
                                     (self.origin_row, "t_warehouse", "OTHER"), (self.row, "ste_detail", "MISSING")):
            with self.subTest(field=field):
                original = target[field]
                target[field] = value
                with self.assertRaisesRegex(ValueError, "来源单据"):
                    self.movements()
                target[field] = original

    def test_partial_source_transfer_allowed_but_more_than_original_is_rejected(self):
        self.set_origin()
        self.assertEqual(self.movements()[0]["movement_qty"], Decimal(3))
        self.origin_row["transfer_qty"] = 2
        with self.assertRaisesRegex(ValueError, "超过原来源明细"):
            self.movements()

    def test_add_to_transit_stage_is_separate_from_final_receipt(self):
        self.doc["add_to_transit"] = 1
        row = self.movements()[0]
        self.assertEqual(row["transfer_stage"], "调入在途")
        self.assertEqual(row["add_to_transit"], 1)

    def test_api_bounds_ledger_to_current_submitted_parent_and_exact_child(self):
        self.module.report_scope.return_value = (self.filters, "2026-09-01", "2026-09-30")
        self.module.selected_warehouses.return_value = ["A", "B"]
        self.module.readable_documents.side_effect = [{self.doc.name: self.doc}, self.items, self.warehouses, self.ledgers]
        self.frappe.get_cached_value = Mock(return_value="CNY")
        utils = types.ModuleType("erpnext.stock.utils")
        utils.check_pending_reposting = Mock(return_value=False)
        with patch.dict(sys.modules, {"erpnext.stock.utils": utils}):
            columns, rows = self.module.stock_entry_ledger(self.filters)
        queries = self.module.readable_documents.call_args_list
        self.assertEqual(queries[0].args[1]["is_return"], 0)
        self.assertEqual(queries[0].args[1]["posting_date"], ["between", ["2026-09-01", "2026-09-30"]])
        self.assertEqual(queries[3].args[0], "Stock Ledger Entry")
        self.assertEqual(queries[3].args[1]["voucher_type"], "Stock Entry")
        self.assertEqual(queries[3].args[1]["voucher_no"], ["in", ["STE-1"]])
        self.assertEqual(queries[3].args[1]["voucher_detail_no"], ["in", ["SED-1"]])
        self.assertEqual(queries[3].kwargs, {"complete": True})
        self.assertEqual(rows[0]["net_qty"], 0)
        self.assertIn("source_value", [c["fieldname"] for c in columns])
        utils.check_pending_reposting.assert_called_once_with("2026-09-30", "公司", throw_error=False)

    def test_pending_reposting_blocks_reading_voucher_and_valuation(self):
        self.module.report_scope.return_value = (self.filters, "2026-09-01", "2026-09-30")
        utils = types.ModuleType("erpnext.stock.utils")
        utils.check_pending_reposting = Mock(return_value=True)
        with patch.dict(sys.modules, {"erpnext.stock.utils": utils}):
            with self.assertRaisesRegex(ValueError, "库存估价重算任务"):
                self.module.stock_entry_ledger(self.filters)
        self.module.readable_documents.assert_not_called()

    def test_api_rejects_unknown_mode_before_reading_documents(self):
        self.filters["mode"] = "Manufacture"
        self.module.report_scope.return_value = (self.filters, "2026-09-01", "2026-09-30")
        with self.assertRaisesRegex(ValueError, "请选择库存收货"):
            self.module.stock_entry_ledger(self.filters)
        self.module.readable_documents.assert_not_called()


if __name__ == "__main__":
    unittest.main()
