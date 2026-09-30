"""Purchase request stock units and exact signed receipt provenance."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get

    def __getattribute__(self, name):
        if name == "items" and dict.__contains__(self, name):
            return dict.__getitem__(self, name)
        return super().__getattribute__(name)


class TestProcurementReports(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict = Record
        self.frappe.PermissionError = PermissionError

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.column = lambda field, label, kind="Data", options=None, width=130: {
            "fieldname": field, "label": label, "fieldtype": kind, "options": options}

        def decimal_number(value):
            result = Decimal(str(value if value not in (None, "") else 0))
            if not result.is_finite():
                raise ValueError("有限数字")
            return result

        scope.decimal_number = decimal_number
        scope.report_scope = Mock(side_effect=lambda filters, doctypes: (
            Record(filters or {}), (filters or {})["from_date"], (filters or {})["to_date"]))
        scope.readable_documents = Mock(side_effect=self.readable)
        scope.selected_warehouses = Mock(side_effect=lambda filters: {"GROUP": ["MAIN", "REJECT"]}.get(
            filters.warehouse, [filters.warehouse]))
        self.frappe.get_list = Mock(side_effect=lambda doctype, filters, **kwargs: list(self.candidates(doctype, filters, visible=True)))
        self.frappe.get_doc = Mock(side_effect=lambda doctype, name: self.documents[doctype][name])
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value="CNY"))
        path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "procurement_reports.py"
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
                                    "jifeng_erp.inventory_scope": scope}):
            spec = importlib.util.spec_from_file_location("procurement_reports_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.hidden, self.full_denied = set(), set()
        self.filters = Record(company="JF", from_date="2026-09-01", to_date="2026-09-30")
        self.request_row = Record(name="MRI-1", idx=1, item_code="RAW", item_name="原料", stock_uom="Kg",
                                  uom="Box", qty=2, conversion_factor=3, stock_qty=6, ordered_qty=3, received_qty=0,
                                  warehouse="MAIN", jf_flow_order="FLOW", schedule_date="2026-10-01")
        self.request = Record(name="MR-1", company="JF", docstatus=1, material_request_type="Purchase",
                              transaction_date="2026-09-05", status="Partially Ordered", items=[self.request_row])
        self.receipt_row = Record(name="PRI-1", idx=1, item_code="RAW", item_name="原料", stock_uom="Kg",
                                  uom="Box", qty=2, conversion_factor=3, stock_qty=6, rejected_qty=1,
                                  warehouse="MAIN", rejected_warehouse="REJECT", jf_flow_order="FLOW",
                                  amount=20, base_amount=140, net_amount=18, base_net_amount=126,
                                  return_qty_from_rejected_warehouse=0)
        self.receipt = Record(name="PR-1", company="JF", docstatus=1, posting_date="2026-09-05",
                              posting_time="10:00:00", is_return=0, supplier="SUP", supplier_name="供应商",
                              currency="USD", conversion_rate=7, items=[self.receipt_row])
        self.order_row = Record(self.request_row)
        self.order_row.update(name="POI-1", qty=1, stock_qty=3, material_request=self.request.name,
                              material_request_item=self.request_row.name)
        self.order = Record(name="PO-1", company="JF", supplier="SUP", docstatus=1,
                            status="To Receive and Bill", items=[self.order_row])
        self.documents = {
            "Material Request": {self.request.name: self.request}, "Purchase Receipt": {self.receipt.name: self.receipt},
            "Purchase Order": {self.order.name: self.order},
            "Item": {"RAW": Record(name="RAW", stock_uom="Kg", is_stock_item=1,
                                   uoms=[Record(uom="Box", conversion_factor=3)])},
            "Warehouse": {name: Record(name=name, company="JF", is_group=0) for name in ("MAIN", "REJECT")},
            "Jifeng Flow Order": {"FLOW": Record(name="FLOW", company="JF", item_code="FG",
                                                materials=[Record(item_code="RAW", uom="Box")])},
        }

    def candidates(self, doctype, query, visible=False):
        def matches(doc):
            for field, value in query.items():
                if isinstance(value, (list, tuple)) and value[0] == "between":
                    if not value[1][0] <= str(doc.get(field)) <= value[1][1]:
                        return False
                elif isinstance(value, (list, tuple)) and value[0] == "in":
                    if doc.get(field) not in value[1]:
                        return False
                elif doc.get(field) != value:
                    return False
            return True
        return {name: doc for name, doc in self.documents.get(doctype, {}).items()
                if matches(doc) and (not visible or (doctype, name) not in self.hidden)}

    def get_all(self, doctype, filters, or_filters=None, pluck=None, **kwargs):
        if doctype.endswith(" Item"):
            rows = []
            for parent in self.documents.get(doctype[:-5], {}).values():
                for child in parent.items:
                    row = Record(child)
                    row.update(parent=parent.name, parenttype=doctype[:-5], docstatus=parent.docstatus)
                    rows.append(row)
            def match(row, field, value):
                return row.get(field) in value[1] if isinstance(value, (list, tuple)) else row.get(field) == value
            result = [row for row in rows if all(match(row, field, value) for field, value in filters.items())
                      and (not or_filters or any(match(row, field, value) for field, value in or_filters.items()))]
        else:
            result = list(self.candidates(doctype, filters).values())
        return list(dict.fromkeys(row.get(pluck) for row in result)) if pluck else result

    def set_order_quantity(self, quantity):
        self.order_row.update(stock_qty=quantity, qty=Decimal(str(quantity)) / 3)

    def link_receipt_to_request(self, quantity, via_order=False):
        self.receipt_row.update(stock_qty=quantity, qty=Decimal(str(quantity)) / 3,
                                material_request=self.request.name, material_request_item=self.request_row.name)
        if via_order:
            self.receipt_row.update(purchase_order=self.order.name, purchase_order_item=self.order_row.name)

    def readable(self, doctype, query, complete=False):
        candidates = self.candidates(doctype, query)
        readable = {name: doc for name, doc in candidates.items()
                    if (doctype, name) not in self.hidden | self.full_denied}
        if complete and set(readable) != set(candidates):
            raise PermissionError("来源读取权限")
        return readable

    def request_rows(self, **filters):
        return self.module.purchase_request_ledger({**self.filters, **filters})[1]

    def receipt_rows(self, **filters):
        return self.module.purchase_receipt_ledger({**self.filters, **filters})[1]

    def return_doc(self, rejected=False):
        row = Record(self.receipt_row)
        row.update(name="RETURN-ROW", qty=-1, stock_qty=-3, rejected_qty=0, amount=-10,
                   base_amount=-70, net_amount=-9, base_net_amount=-63, purchase_receipt_item=self.receipt_row.name,
                   warehouse="REJECT" if rejected else "MAIN", rejected_warehouse=None,
                   return_qty_from_rejected_warehouse=int(rejected))
        returned = Record(self.receipt)
        returned.update(name="RETURN", posting_date="2026-09-30", posting_time="11:00:00", is_return=1,
                        return_against=self.receipt.name, items=[row])
        self.documents["Purchase Receipt"][returned.name] = returned
        return returned

    def test_request_uses_stock_quantity_and_stock_ordered_quantity(self):
        row = self.request_rows()[0]
        self.assertEqual((row["requested_qty"], row["ordered_qty"], row["remaining_qty"], row["pending_qty"]),
                         (Decimal(6), Decimal(3), Decimal(3), Decimal(3)))
        self.assertEqual((row["stock_uom"], row["request_item"]), ("Kg", "MRI-1"))

    def test_stopped_request_preserves_remaining_but_removes_pending(self):
        self.request["status"] = "Stopped"
        row = self.request_rows()[0]
        self.assertEqual((row["remaining_qty"], row["pending_qty"]), (Decimal(3), Decimal(0)))
        self.assertEqual(self.request_rows(only_pending=1), [])

    def test_request_over_order_is_not_a_negative_pending_balance(self):
        self.request_row["ordered_qty"] = 8
        self.set_order_quantity(8)
        row = self.request_rows()[0]
        self.assertEqual((row["remaining_qty"], row["pending_qty"]), (Decimal(0), Decimal(0)))

    def test_direct_receipts_reduce_pending_without_overwriting_unordered_history(self):
        self.request_row.update(ordered_qty=0, received_qty=4)
        self.order["docstatus"] = 2
        self.link_receipt_to_request(4)
        row = self.request_rows()[0]
        self.assertEqual((row["received_qty"], row["remaining_qty"], row["pending_qty"]), (4, 6, 2))
        self.request_row["received_qty"] = 6
        self.link_receipt_to_request(6)
        self.assertEqual(self.request_rows()[0]["pending_qty"], 0)
        self.assertEqual(self.request_rows(only_pending=1), [])

    def test_order_and_receipt_progress_overlap_instead_of_adding(self):
        self.request_row["received_qty"] = 4
        self.link_receipt_to_request(4, via_order=True)
        row = self.request_rows()[0]
        self.assertEqual((row["ordered_qty"], row["received_qty"], row["pending_qty"]), (3, 4, 2))
        self.request_row.update(ordered_qty=5, received_qty=2)
        self.set_order_quantity(5)
        self.link_receipt_to_request(2, via_order=True)
        self.assertEqual(self.request_rows()[0]["pending_qty"], 1)

    def test_direct_receipt_and_remaining_order_cover_distinct_parts(self):
        self.link_receipt_to_request(3)
        row = self.request_rows()[0]
        self.assertEqual((row["ordered_qty"], row["received_qty"], row["covered_qty"], row["pending_qty"]), (3, 3, 6, 0))
        self.assertEqual(self.request_rows(only_pending=1), [])

    def test_exact_po_item_can_retain_request_identity_without_duplicate_mr_links(self):
        self.link_receipt_to_request(3, via_order=True)
        self.receipt_row.update(material_request=None, material_request_item=None)
        self.assertEqual((self.request_rows()[0]["received_qty"], self.request_rows()[0]["pending_qty"]), (3, 3))

    def test_closed_order_keeps_order_history_but_only_receipts_cover_demand(self):
        self.link_receipt_to_request(2, via_order=True)
        self.order["status"] = "Closed"
        row = self.request_rows()[0]
        self.assertEqual((row["ordered_qty"], row["remaining_qty"], row["received_qty"], row["pending_qty"]), (3, 3, 2, 4))

    def test_cancelled_orders_release_commitment_and_cancelled_receipts_release_received(self):
        self.link_receipt_to_request(2)
        self.order["docstatus"] = 2
        self.assertEqual(self.request_rows()[0]["pending_qty"], 4)
        self.receipt["docstatus"] = 2
        row = self.request_rows()[0]
        self.assertEqual((row["ordered_qty"], row["received_qty"], row["pending_qty"]), (0, 0, 6))

    def test_rejected_return_does_not_reduce_request_net_accepted(self):
        self.link_receipt_to_request(6)
        self.order["docstatus"] = 2
        self.return_doc(rejected=True)
        # Native cached received_qty can include the rejected-return stock_qty.
        self.request_row["received_qty"] = 3
        row = self.request_rows()[0]
        self.assertEqual((row["received_qty"], row["pending_qty"]), (6, 0))

    def test_accepted_direct_return_opens_gap_and_cancel_return_restores_coverage(self):
        self.link_receipt_to_request(6)
        self.order["docstatus"] = 2
        returned = self.return_doc()
        self.assertEqual((self.request_rows()[0]["received_qty"], self.request_rows()[0]["pending_qty"]), (3, 3))
        returned["docstatus"] = 2
        self.assertEqual(self.request_rows()[0]["pending_qty"], 0)

    def test_active_order_return_keeps_unfilled_order_commitment(self):
        self.set_order_quantity(6)
        self.link_receipt_to_request(6, via_order=True)
        self.return_doc()
        self.assertEqual((self.request_rows()[0]["received_qty"], self.request_rows()[0]["pending_qty"]), (3, 0))

    def test_request_related_receipts_and_returns_outside_request_dates_are_included(self):
        self.link_receipt_to_request(6)
        self.order["docstatus"] = 2
        self.receipt["posting_date"] = "2026-08-01"
        returned = self.return_doc()
        returned["posting_date"] = "2026-10-01"
        self.assertEqual(self.request_rows()[0]["received_qty"], 3)

    def test_hidden_order_original_or_return_denies_incomplete_request_progress(self):
        self.link_receipt_to_request(6)
        returned = self.return_doc()
        for doctype, name in (("Purchase Order", self.order.name), ("Purchase Receipt", self.receipt.name),
                               ("Purchase Receipt", returned.name)):
            with self.subTest(doctype=doctype, name=name):
                self.hidden.add((doctype, name))
                with self.assertRaises(PermissionError):
                    self.request_rows()
                self.hidden.clear()

    def test_missing_request_or_order_source_never_falls_back_to_same_item(self):
        self.link_receipt_to_request(6, via_order=True)
        for document, field, value in ((self.order_row, "material_request_item", None),
                                       (self.receipt_row, "purchase_order_item", "MISSING"),
                                       (self.receipt_row, "material_request_item", "MISSING")):
            with self.subTest(field=field):
                original = document[field]
                document[field] = value
                with self.assertRaises(ValueError):
                    self.request_rows()
                document[field] = original

    def test_request_return_missing_original_key_fails_even_when_mr_link_is_intact(self):
        self.link_receipt_to_request(6)
        returned = self.return_doc()
        returned.items[0]["purchase_receipt_item"] = None
        with self.assertRaisesRegex(ValueError, "精确的原收货明细"):
            self.request_rows()

    def test_duplicate_requested_items_are_not_merged_by_item_code(self):
        second = Record(self.request_row)
        second.update(name="MRI-2", idx=2)
        self.request.items.append(second)
        self.link_receipt_to_request(3)
        self.receipt_row["material_request_item"] = second.name
        rows = self.request_rows()
        by_item = {row["request_item"]: row for row in rows}
        self.assertEqual((by_item["MRI-1"]["ordered_qty"], by_item["MRI-1"]["received_qty"]), (3, 0))
        self.assertEqual((by_item["MRI-2"]["ordered_qty"], by_item["MRI-2"]["received_qty"]), (0, 3))

    def test_multiple_individual_returns_cannot_overdraw_source_pool(self):
        self.link_receipt_to_request(3)
        first = self.return_doc()
        second = Record(first)
        second.update(name="RETURN-SECOND", items=[Record(first.items[0])])
        second.items[0]["name"] = "RETURN-SECOND-ROW"
        self.documents["Purchase Receipt"][second.name] = second
        with self.assertRaisesRegex(ValueError, "累计退货"):
            self.request_rows()

    def test_request_coverage_ignores_stale_native_cached_progress(self):
        self.request_row.update(ordered_qty=999, received_qty=-3)
        row = self.request_rows()[0]
        self.assertEqual((row["ordered_qty"], row["received_qty"], row["pending_qty"]), (3, 0, 3))

    def test_only_submitted_purchase_requests_and_transaction_dates(self):
        for changes in (dict(docstatus=0), dict(docstatus=2), dict(material_request_type="Material Transfer"),
                        dict(transaction_date="2026-08-31")):
            with self.subTest(changes=changes):
                original = dict(self.request)
                self.request.update(changes)
                self.assertEqual(self.request_rows(), [])
                self.request.update(original)

    def test_request_filters_keep_exact_rows_and_group_warehouses(self):
        self.assertEqual(len(self.request_rows(warehouse="GROUP", flow_order="FLOW", item_code="RAW")), 1)
        self.assertEqual(self.request_rows(warehouse="REJECT"), [])
        self.assertEqual(self.request_rows(flow_order="OTHER"), [])

    def test_stock_qty_conversion_mismatch_is_rejected(self):
        self.request_row["stock_qty"] = 2
        with self.assertRaisesRegex(ValueError, "换算系数不一致"):
            self.request_rows()
        self.receipt_row["stock_qty"] = 2
        with self.assertRaisesRegex(ValueError, "换算系数不一致"):
            self.receipt_rows()

    def test_native_stock_precision_rounding_is_allowed(self):
        self.request_row.update(qty=1, conversion_factor="0.333333333", stock_qty="0.333", ordered_qty=0)
        self.request_row.precision = lambda field: 3
        self.assertEqual(self.request_rows()[0]["requested_qty"], Decimal("0.333"))

    def test_invalid_non_finite_and_negative_request_quantities_fail(self):
        for field, value in (("conversion_factor", 0), ("qty", "nan"), ("stock_qty", None)):
            with self.subTest(field=field):
                original = self.request_row[field]
                self.request_row[field] = value
                with self.assertRaises(ValueError):
                    self.request_rows()
                self.request_row[field] = original

    def test_receipt_uses_stock_qty_and_reject_conversion(self):
        row = self.receipt_rows()[0]
        self.assertEqual((row["accepted_qty"], row["rejected_qty"], row["physical_stock_qty"]),
                         (Decimal(6), Decimal(3), Decimal(9)))

    def test_accepted_return_uses_exact_original_row_outside_date_range(self):
        returned = self.return_doc()
        rows = self.receipt_rows(from_date="2026-09-30")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["purchase_receipt"], row["source_receipt"], row["source_receipt_item"]),
                         (returned.name, self.receipt.name, self.receipt_row.name))
        self.assertEqual((row["accepted_qty"], row["rejected_qty"], row["physical_stock_qty"]),
                         (Decimal(-3), Decimal(0), Decimal(-3)))

    def test_persistent_rejected_return_flag_uses_rejected_pool(self):
        self.return_doc(rejected=True)
        row = self.receipt_rows(only_returns=1)[0]
        self.assertEqual((row["accepted_qty"], row["rejected_qty"], row["physical_stock_qty"]),
                         (Decimal(0), Decimal(-3), Decimal(-3)))
        self.assertEqual((row["warehouse"], row["rejected_warehouse"]), (None, "REJECT"))

    def test_negative_rejected_qty_without_flag_uses_original_rejected_warehouse(self):
        returned = self.return_doc()
        returned.items[0].update(qty=0, stock_qty=0, rejected_qty=-1, rejected_warehouse="REJECT")
        self.assertEqual(self.receipt_rows(only_returns=1)[0]["rejected_qty"], Decimal(-3))

    def test_rejected_flag_and_wrong_pool_warehouse_fail(self):
        returned = self.return_doc(rejected=True)
        returned.items[0]["warehouse"] = "MAIN"
        with self.assertRaisesRegex(ValueError, "退拒收仓库"):
            self.receipt_rows(only_returns=1)

    def test_transient_parent_flag_does_not_replace_persistent_item_flag(self):
        returned = self.return_doc(rejected=True)
        returned["return_qty_from_rejected_warehouse"] = 1
        returned.items[0]["return_qty_from_rejected_warehouse"] = 0
        with self.assertRaisesRegex(ValueError, "退接收仓库"):
            self.receipt_rows(only_returns=1)

    def test_missing_original_header_or_row_never_falls_back_to_item_code(self):
        returned = self.return_doc()
        for changes in (dict(return_against=None), dict(return_against="MISSING")):
            with self.subTest(changes=changes):
                returned.update(changes)
                with self.assertRaises(ValueError):
                    self.receipt_rows(only_returns=1)
        returned["return_against"] = self.receipt.name
        returned.items[0]["purchase_receipt_item"] = "OTHER-ROW"
        with self.assertRaisesRegex(ValueError, "精确的原收货明细"):
            self.receipt_rows(only_returns=1)

    def test_unreadable_source_fails_rather_than_omitting_return(self):
        self.return_doc()
        self.hidden.add(("Purchase Receipt", self.receipt.name))
        with self.assertRaises(PermissionError):
            self.receipt_rows(only_returns=1)

    def test_list_visible_full_denied_return_is_not_silently_omitted(self):
        returned = self.return_doc()
        self.full_denied.add(("Purchase Receipt", returned.name))
        with self.assertRaisesRegex(PermissionError, "完整来源"):
            self.receipt_rows(only_returns=1)

    def test_full_denied_normal_parent_is_excluded_whole(self):
        self.full_denied.add(("Purchase Receipt", self.receipt.name))
        self.assertEqual(self.receipt_rows(), [])

    def test_origin_company_supplier_currency_and_fx_must_match(self):
        returned = self.return_doc()
        for field, value in (("company", "OTHER"), ("supplier", "OTHER"), ("currency", "EUR"),
                             ("conversion_rate", 6), ("docstatus", 2), ("is_return", 1)):
            with self.subTest(field=field):
                original = self.receipt[field]
                self.receipt[field] = value
                with self.assertRaisesRegex(ValueError, "公司、供应商、币种或汇率"):
                    self.receipt_rows(only_returns=1, from_date="2026-09-30")
                self.receipt[field] = original
        self.assertEqual(returned.return_against, self.receipt.name)

    def test_return_flow_item_and_stock_units_must_match(self):
        returned = self.return_doc()
        returned.items[0]["jf_flow_order"] = None
        with self.assertRaisesRegex(ValueError, "流程单与原明细"):
            self.receipt_rows(only_returns=1)
        returned.items[0].update(jf_flow_order="FLOW", stock_uom="Nos")
        with self.assertRaisesRegex(ValueError, "库存单位与商品档案"):
            self.receipt_rows(only_returns=1)

    def test_flow_company_material_membership_and_legal_alternate_uom(self):
        self.assertEqual(len(self.receipt_rows()), 1)
        flow = self.documents["Jifeng Flow Order"]["FLOW"]
        for changes in (dict(company="OTHER"), dict(materials=[]),
                        dict(materials=[Record(item_code="RAW", uom="Unknown-UOM")])):
            with self.subTest(changes=changes):
                original = dict(flow)
                flow.update(changes)
                with self.assertRaises(ValueError):
                    self.receipt_rows()
                flow.update(original)

    def test_each_selected_warehouse_receives_only_its_own_quantity(self):
        accepted = self.receipt_rows(warehouse="MAIN")[0]
        rejected = self.receipt_rows(warehouse="REJECT")[0]
        grouped = self.receipt_rows(warehouse="GROUP")[0]
        self.assertEqual((accepted["accepted_qty"], accepted["rejected_qty"], accepted["physical_stock_qty"]), (6, 0, 6))
        self.assertEqual((rejected["accepted_qty"], rejected["rejected_qty"], rejected["physical_stock_qty"]), (0, 3, 3))
        self.assertEqual(grouped["physical_stock_qty"], 9)
        self.assertEqual((accepted["amount"], rejected["amount"]), (20, 20))

    def test_rejected_return_warehouse_filter_preserves_pool_and_signed_quantity(self):
        self.return_doc(rejected=True)
        self.assertEqual(self.receipt_rows(warehouse="MAIN", only_returns=1), [])
        row = self.receipt_rows(warehouse="REJECT", only_returns=1)[0]
        self.assertEqual((row["accepted_qty"], row["rejected_qty"]), (0, -3))

    def test_receipt_currency_amounts_and_net_values_are_native_not_valuation(self):
        self.receipt_row["valuation_rate"] = 999
        columns, rows = self.module.purchase_receipt_ledger(self.filters)
        row = rows[0]
        self.assertEqual((row["currency"], row["company_currency"], row["conversion_rate"]), ("USD", "CNY", 7))
        self.assertEqual((row["amount"], row["base_amount"], row["net_amount"], row["base_net_amount"]), (20, 140, 18, 126))
        options = {column["fieldname"]: column["options"] for column in columns}
        self.assertEqual((options["amount"], options["base_amount"]), ("currency", "company_currency"))
        self.assertNotIn("valuation_rate", row)

    def test_return_amounts_keep_the_native_signed_money(self):
        self.return_doc(rejected=True)
        row = self.receipt_rows(only_returns=1)[0]
        self.assertEqual((row["amount"], row["base_amount"], row["net_amount"], row["base_net_amount"]), (-10, -70, -9, -63))

    def test_duplicate_item_rows_keep_exact_source_ids(self):
        second = Record(self.receipt_row)
        second.update(name="PRI-2", idx=2, qty=1, stock_qty=3, rejected_qty=0)
        self.receipt.items.append(second)
        self.assertEqual([row["receipt_item"] for row in self.receipt_rows()], ["PRI-1", "PRI-2"])
        returned = self.return_doc()
        returned.items[0]["purchase_receipt_item"] = second.name
        self.assertEqual(self.receipt_rows(only_returns=1)[0]["source_receipt_item"], second.name)

    def test_drafts_cancelled_and_wrong_posting_dates_never_populate_ledger(self):
        for changes in (dict(docstatus=0), dict(docstatus=2), dict(posting_date="2026-08-31")):
            with self.subTest(changes=changes):
                original = dict(self.receipt)
                self.receipt.update(changes)
                self.assertEqual(self.receipt_rows(), [])
                self.receipt.update(original)

    def test_receipt_and_source_warehouses_must_be_company_leaf_warehouses(self):
        self.return_doc()
        for changes in (dict(company="OTHER"), dict(is_group=1)):
            with self.subTest(changes=changes):
                original = dict(self.documents["Warehouse"]["REJECT"])
                self.documents["Warehouse"]["REJECT"].update(changes)
                with self.assertRaisesRegex(ValueError, "明细仓库"):
                    self.receipt_rows(only_returns=1)
                self.documents["Warehouse"]["REJECT"].update(original)

    def test_oversized_returns_and_wrong_signs_are_rejected(self):
        returned = self.return_doc()
        for changes in (dict(qty=-3, stock_qty=-9), dict(qty=1, stock_qty=3),
                        dict(rejected_qty=1), dict(conversion_factor=0)):
            with self.subTest(changes=changes):
                original = dict(returned.items[0])
                returned.items[0].update(changes)
                with self.assertRaises(ValueError):
                    self.receipt_rows(only_returns=1)
                returned.items[0].update(original)

    def test_supplier_and_only_return_filters_are_applied_on_parent_query(self):
        self.return_doc()
        self.assertEqual(len(self.receipt_rows(supplier="SUP", only_returns=1)), 1)
        self.assertEqual(self.receipt_rows(supplier="OTHER"), [])
        call = self.module.readable_documents.call_args
        self.assertEqual(call.args[1]["supplier"], "OTHER")


if __name__ == "__main__":
    unittest.main()
