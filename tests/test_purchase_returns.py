"""Return pools must preserve source identity and the native validator call."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class Row(dict):
    def __getattr__(self, name):
        return self.get(name)

    def precision(self, field):
        return 6


class AttributeDict(Row):
    def __setattr__(self, name, value):
        self[name] = value


class Document(types.SimpleNamespace):
    def get(self, field, default=None):
        return getattr(self, field, default)

    def check_permission(self, mode):
        return None


class PurchaseReturnTest(unittest.TestCase):
    def setUp(self):
        self.calls, self.queries = [], []
        self.parents, self.return_rows = [], []
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe._dict = AttributeDict
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.flt = lambda value, precision=None: round(float(value or 0), precision) if precision is not None else float(value or 0)
        erpnext = types.ModuleType("erpnext")
        erpnext.__path__ = []
        controllers = types.ModuleType("erpnext.controllers")
        controllers.__path__ = []
        self.native = types.ModuleType("erpnext.controllers.sales_and_purchase_return")
        self.native.StockOverReturnError = type("StockOverReturnError", (ValueError,), {})

        def original(doc, key, args, ref, valid_items, already_returned_items):
            self.calls.append((doc, key, args, ref, valid_items, already_returned_items))
            return "native-result"

        self.original = original
        self.native.validate_quantity = original
        controllers.sales_and_purchase_return = self.native
        self.modules = {"frappe": frappe, "frappe.utils": utils, "erpnext": erpnext,
                        "erpnext.controllers": controllers,
                        "erpnext.controllers.sales_and_purchase_return": self.native}
        with patch.dict(sys.modules, self.modules):
            spec = importlib.util.spec_from_file_location(
                "purchase_return_subject", pathlib.Path(__file__).parents[1] / "jifeng_erp/purchase_returns.py")
            self.subject = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.subject)
        self.source_row = Row(name="SOURCE-ROW", item_code="ITEM", stock_uom="Nos", stock_qty=4,
                              rejected_qty=0.5, conversion_factor=2, warehouse="A", rejected_warehouse="R")
        self.source = Document(name="SOURCE", docstatus=1, is_return=0, company="JF", supplier="SUP",
                               items=[self.source_row])
        self.source_locks = []
        def get_source(*args, **kwargs):
            self.source_locks.append(kwargs)
            return self.source
        frappe.get_doc = get_source
        frappe.db = types.SimpleNamespace(sql=self.get_returns)
        self.doc_class = type("Receipt", (self.subject.PurchaseReceiptMixin, Document), {})
        self.args = self.row(name="CURRENT-ROW", parent="CURRENT", qty=-0.5, stock_qty=-1)
        self.doc = self.doc_class(name="CURRENT", doctype="Purchase Receipt", is_return=1,
                                  company="JF", supplier="SUP", return_against="SOURCE", items=[self.args])
        self.key = ("ITEM", "SOURCE-ROW")

    def row(self, **values):
        row = Row(name="RETURN-ROW", parent="RETURN", parenttype="Purchase Receipt", item_code="ITEM",
                  purchase_receipt_item="SOURCE-ROW", stock_uom="Nos", conversion_factor=2,
                  qty=-0.5, stock_qty=-1, rejected_qty=0, warehouse="A", rejected_warehouse="R",
                  return_qty_from_rejected_warehouse=0)
        row.update(values)
        return row

    def get_returns(self, query, values, **kwargs):
        self.queries.append((query, values, kwargs))
        parents = {row.name: row for row in self.parents if row.docstatus == 1 and row.is_return
                   and row.return_against == values[0] and row.name != values[1]}
        return [Row(row, return_company=parents[row.parent].company, return_supplier=parents[row.parent].supplier)
                for row in self.return_rows if row.parent in parents]

    def add_prior(self, row, status=1, name="RETURN"):
        self.parents.append(Row(name=name, company="JF", supplier="SUP", docstatus=status,
                                is_return=1, return_against="SOURCE"))
        row["parent"] = name
        self.return_rows.append(row)

    def dispatch(self, initial=None):
        with patch.dict(sys.modules, self.modules):
            dispatch = self.subject.install_quantity_dispatcher()
            result = dispatch(self.doc, self.key, self.args, self.source_row, {self.key: self.source_row}, initial or {})
        self.assertEqual(result, "native-result")
        return self.calls[-1][-1][self.key]

    def test_accepted_return_does_not_use_rejected_remaining_pool(self):
        self.add_prior(self.row())
        self.args.update(warehouse="R", return_qty_from_rejected_warehouse=1)
        totals = self.dispatch({self.key: {"stock_qty": 1, "received_qty": 1}})
        self.assertEqual((totals["stock_qty"], totals["received_qty"]), (0, 0))

    def test_rejected_return_does_not_consume_accepted_remaining_pool(self):
        self.add_prior(self.row(warehouse="R", return_qty_from_rejected_warehouse=1))
        totals = self.dispatch()
        self.assertEqual((totals["stock_qty"], totals["received_qty"], totals["rejected_qty"]), (0, 1, 1))

    def test_mixed_return_and_rejected_return_share_rejected_bound(self):
        self.source_row["rejected_qty"] = 1
        self.add_prior(self.row(qty=-1, stock_qty=-2, rejected_qty=-0.25))
        self.add_prior(self.row(qty=-0.25, stock_qty=-0.5, warehouse="R",
                                return_qty_from_rejected_warehouse=1), name="RETURN-2")
        totals = self.dispatch()
        self.assertEqual((totals["stock_qty"], totals["received_qty"], totals["rejected_qty"]), (2, 3, 1))

    def test_other_exact_source_row_is_excluded_even_for_same_item(self):
        self.source.items.append(Row(self.source_row, name="OTHER-SOURCE"))
        self.add_prior(self.row(purchase_receipt_item="OTHER-SOURCE"))
        self.assertEqual(self.dispatch()["stock_qty"], 0)

    def test_missing_historical_source_is_not_guessed(self):
        self.add_prior(self.row(purchase_receipt_item=None))
        with self.assertRaisesRegex(ValueError, "不能猜测"):
            self.dispatch()

    def test_wrong_historical_source_is_not_guessed(self):
        self.add_prior(self.row(purchase_receipt_item="FOREIGN"))
        with self.assertRaisesRegex(ValueError, "不能猜测"):
            self.dispatch()

    def test_historical_wrong_return_warehouse_is_rejected(self):
        self.add_prior(self.row(warehouse="FOREIGN"))
        with self.assertRaisesRegex(ValueError, "仓库"):
            self.dispatch()

    def test_historical_stock_uom_mismatch_is_rejected(self):
        self.add_prior(self.row(stock_uom="Kg"))
        with self.assertRaisesRegex(ValueError, "库存单位"):
            self.dispatch()

    def test_historical_stock_quantity_mismatch_is_rejected(self):
        self.add_prior(self.row(stock_qty=-2))
        with self.assertRaisesRegex(ValueError, "换算不一致"):
            self.dispatch()

    def test_true_accepted_over_return_is_rejected_even_with_zero_client_stock_qty(self):
        self.args.update(qty=-2.5, stock_qty=0)
        with self.assertRaisesRegex(self.native.StockOverReturnError, "退接收"):
            self.dispatch()
        self.assertEqual(self.calls, [])

    def test_true_rejected_over_return_after_mixed_return_is_rejected(self):
        self.add_prior(self.row(rejected_qty=-0.25))
        self.args.update(warehouse="R", return_qty_from_rejected_warehouse=1)
        with self.assertRaisesRegex(self.native.StockOverReturnError, "退拒收"):
            self.dispatch()

    def test_full_accepted_pool_does_not_block_plain_rejected_quantity_return(self):
        self.add_prior(self.row(qty=-2, stock_qty=-4))
        self.args.update(qty=0, stock_qty=0, rejected_qty=-0.5)
        self.dispatch()
        self.assertEqual(self.calls[-1][3]["stock_qty"], 0)
        self.assertEqual(self.calls[-1][3]["rejected_qty"], 0.5)
        self.assertEqual(self.calls[-1][2]["received_qty"], -0.5)

    def test_full_rejected_pool_does_not_block_plain_accepted_quantity_return(self):
        self.add_prior(self.row(warehouse="R", return_qty_from_rejected_warehouse=1))
        self.dispatch()
        self.assertEqual(self.calls[-1][3]["stock_qty"], 4)
        self.assertEqual(self.calls[-1][3]["rejected_qty"], 0)

    def test_stale_current_stock_quantity_is_normalized_only_for_native_validation(self):
        self.args["stock_qty"] = -4
        self.dispatch()
        self.assertEqual(self.calls[-1][2]["stock_qty"], -1)
        self.assertEqual(self.args["stock_qty"], -4)

    def test_duplicate_current_lines_cannot_exceed_same_source_pool(self):
        self.args.update(qty=-1.5, stock_qty=-3)
        self.doc.items.append(self.row(name="CURRENT-2", parent="CURRENT", qty=-1, stock_qty=-2))
        with self.assertRaisesRegex(self.native.StockOverReturnError, "退接收"):
            self.dispatch()

    def test_canceled_draft_and_current_parent_are_not_consumed(self):
        self.add_prior(self.row(qty=-2, stock_qty=-4), status=0)
        self.add_prior(self.row(qty=-2, stock_qty=-4), status=2, name="CANCELED")
        self.add_prior(self.row(qty=-2, stock_qty=-4), status=1, name="CURRENT")
        self.assertEqual(self.dispatch()["stock_qty"], 0)
        query, values, _ = self.queries[0]
        self.assertIn("parent.docstatus = 1", query)
        self.assertIn("for update", query)
        self.assertEqual(values, ("SOURCE", "CURRENT"))
        self.assertEqual(self.source_locks, [{"for_update": True}])

    def test_native_mapping_is_copied_and_native_validator_remains_called(self):
        original = {self.key: {"stock_qty": 123, "received_qty": 123}, "OTHER": {"stock_qty": 2}}
        self.dispatch(original)
        self.assertEqual(original[self.key]["stock_qty"], 123)
        self.assertEqual(self.calls[-1][-1]["OTHER"], {"stock_qty": 2})
        self.assertIs(self.calls[-1][0], self.doc)
        self.assertIsNot(self.calls[-1][2], self.args)
        self.assertEqual(self.calls[-1][2]["stock_qty"], self.args["stock_qty"])

    def test_dispatch_install_is_idempotent_and_non_purchase_native_call_is_unchanged(self):
        with patch.dict(sys.modules, self.modules):
            one = self.subject.install_quantity_dispatcher()
            self.assertIs(one, self.subject.install_quantity_dispatcher())
            other = Document(doctype="Sales Invoice", is_return=1)
            initial = {"UNCHANGED": {"stock_qty": 8}}
            self.assertEqual(one(other, "KEY", None, None, None, initial), "native-result")
            self.assertIs(self.calls[-1][-1], initial)
        self.assertEqual(self.queries, [])

    def test_plain_purchase_receipt_class_keeps_native_path(self):
        with patch.dict(sys.modules, self.modules):
            dispatcher = self.subject.install_quantity_dispatcher()
            plain = Document(doctype="Purchase Receipt", is_return=1)
            native_input = {"UNCHANGED": 1}
            dispatcher(plain, None, None, None, None, native_input)
            self.assertIs(self.calls[-1][-1], native_input)
        self.assertEqual(self.queries, [])

    def test_hidden_original_receipt_is_not_read_without_permission(self):
        self.source.check_permission = lambda mode: (_ for _ in ()).throw(PermissionError("hidden"))
        with self.assertRaises(PermissionError):
            self.dispatch()


if __name__ == "__main__":
    unittest.main()
