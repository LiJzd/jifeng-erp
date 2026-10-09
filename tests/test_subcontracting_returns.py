"""Prevent submitted over-returns while preserving native receipt lifecycle."""

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch


class Row(dict):
    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def set(self, name, value):
        self[name] = value

    def precision(self, field):
        return 6

    def as_dict(self):
        return dict(self)


class Document(types.SimpleNamespace):
    def get(self, field, default=None):
        return getattr(self, field, default)

    def set(self, field, value):
        setattr(self, field, value)

    def append(self, field, value):
        rows = self.get(field) or []
        row = Row(value)
        row.setdefault("name", field + "-" + str(len(rows) + 1))
        row.setdefault("parent", self.get("name"))
        row.setdefault("parenttype", self.get("doctype"))
        row.setdefault("parentfield", field)
        rows.append(row)
        self.set(field, rows)
        return row

    def check_permission(self, mode):
        if self.get("unreadable"):
            raise PermissionError("source denied")


class SubcontractingReturnTest(unittest.TestCase):
    def setUp(self):
        self.events, self.prior, self.prior_raw, self.native_calls = [], [], [], []
        self.prior_docs = {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.PermissionError = PermissionError
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.has_permission = lambda doctype, mode: True
        self.frappe.get_cached_value = lambda doctype, name, field: 1
        self.frappe.session = types.SimpleNamespace(user="qa")
        self.tables = {"items": Row(fieldtype="Table", options="Subcontracting Receipt Item", permlevel=0),
                       "supplied_items": Row(fieldtype="Table", options="Subcontracting Receipt Supplied Item", permlevel=0),
                       "additional_costs": Row(fieldtype="Table", options="Landed Cost Taxes and Charges", permlevel=0)}
        self.frappe.get_meta = lambda doctype: types.SimpleNamespace(
            get_field=lambda field: (self.tables.get(field) if doctype == "Subcontracting Receipt" else
                Row(fieldname="entries", fieldtype="Table", options="Serial and Batch Entry", permlevel=0)
                if doctype == "Serial and Batch Bundle" and field == "entries" else None),
            get_permlevel_access=lambda **kw: {0})
        self.frappe.share = types.SimpleNamespace(get_shared=lambda *args, **kwargs: [])
        self.model = types.ModuleType("frappe.model")
        self.model.get_permitted_fields = lambda *args, **kw: []
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.flt = lambda value, precision=None: (round(float(value or 0), precision)
                                                  if precision is not None else float(value or 0))
        utils.get_datetime = datetime.datetime.fromisoformat
        self.native = types.ModuleType("erpnext.controllers.sales_and_purchase_return")
        self.native.make_return_doc = self.map_native
        self.modules = {"frappe": self.frappe, "frappe.utils": utils, "frappe.model": self.model,
                        "erpnext": types.ModuleType("erpnext"),
                        "erpnext.controllers": types.ModuleType("erpnext.controllers"),
                        "erpnext.controllers.sales_and_purchase_return": self.native}
        module_patch = patch.dict(sys.modules, self.modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        with patch.dict(sys.modules, self.modules):
            spec = importlib.util.spec_from_file_location(
                "subcontracting_returns_subject",
                pathlib.Path(__file__).parents[1] / "jifeng_erp/subcontracting_returns.py")
            self.subject = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.subject)
        self.source_row = Row(name="SRC-ROW", item_code="FG", stock_uom="Nos", conversion_factor=2,
                              qty=3, rejected_qty=1, process_loss_qty=1, warehouse="Good",
                              rejected_warehouse="Rejected", subcontracting_order="SCO",
                              subcontracting_order_item="SCO-ROW", purchase_order="PO",
                              purchase_order_item="PO-ROW", job_card="JC", bom="BOM",
                              jf_flow_order="FLOW", service_cost_per_qty=4, additional_cost_per_qty=0,
                              received_qty=5, amount=20, rm_supp_cost=0)
        self.source = self.document("SOURCE", [self.source_row], is_return=0, docstatus=1)

        def get_source(doctype, name, **kwargs):
            self.events.append(("source", name, kwargs))
            if name in self.prior_docs:
                return self.prior_docs[name]
            physical = [row for row in self.prior if row.parent == name]
            if physical:
                return self.document(name, physical, docstatus=1,
                    supplied_items=[row for row in self.prior_raw if row.parent == name])
            if name == "CURRENT":
                return self.doc
            return self.source

        def get_returns(query, values, **kwargs):
            self.events.append(("prior", query, values))
            if "select parent.name" in query:
                names = {row.parent for row in [*self.prior, *self.prior_raw]
                         if row.get("docstatus", 1) == 1}
                names.update(name for name, doc in self.prior_docs.items() if doc.docstatus == 1)
                return [Row(name=name) for name in sorted(names) if name != values[1]]
            rows = self.prior_raw if "tabSubcontracting Receipt Supplied Item" in query else self.prior
            return [row for row in rows if row.get("docstatus", 1) == 1
                    and row.parent != values[1]]

        self.frappe.get_doc = get_source
        self.frappe.db = types.SimpleNamespace(sql=get_returns, exists=lambda *args, **kwargs: False)
        self.frappe.get_list = self.stock_entries
        def membership(doctype, **kwargs):
            if doctype == "Stock Ledger Entry":
                return [row.name for row in self.stock_entries(doctype, **kwargs)]
            parent = get_source("Subcontracting Receipt", kwargs["filters"]["parent"])
            table = "items" if doctype == "Subcontracting Receipt Item" else "supplied_items"
            return [row.name for row in parent.get(table) or []]

        self.frappe.get_all = membership

        outer = self

        class Native(Document):
            def validate(self):
                outer.native_calls.append(self)
                if self.get("native_adjust_qty"):
                    self.items[0].qty *= 2
                if self.get("native_full_calculation"):
                    self.reset_supplied_items()
                    self.validate_items()
                    self.create_raw_materials_supplied_or_received()
                    self.set_valuation_rate_for_rm()
                    self.calculate_additional_costs()
                    self.calculate_items_qty_and_amount()
                    self.get_secondary_items(recalculate_rate=True)
                    self.validate_bom_required_qty()
                return "native-result"

            def initialized_fields(self):
                self.available_materials = {}

            def calculate_items_qty_and_amount(self):
                outer.native_calls.append("forward-cost")
                return "native-cost-result"

            def calculate_additional_costs(self):
                outer.native_calls.append("forward-additional-cost")
                return "native-additional-result"

        self.DocClass = type("Return", (self.subject.SubcontractingReceiptReturnMixin, Native), {})
        self.doc = self.document("CURRENT", [self.row()], cls=self.DocClass)

    def document(self, name, rows, cls=Document, **values):
        base = dict(name=name, doctype="Subcontracting Receipt", company="JF", supplier="SUP",
                    supplier_warehouse="Supplier", posting_date="2026-10-08", posting_time="10:00:00",
                    is_return=1, docstatus=0, return_against="SOURCE", items=rows)
        base.update(values)
        return cls(**base)

    def row(self, **values):
        row = Row(self.source_row, name="RETURN-ROW", parent="PRIOR", qty=-1,
                  parenttype="Subcontracting Receipt", parentfield="items",
                  rejected_qty=0, process_loss_qty=0, conversion_factor=1,
                  service_cost_per_qty=2,
                  subcontracting_receipt_item="SRC-ROW", return_company="JF", return_supplier="SUP",
                  return_supplier_warehouse="Supplier", idx=1)
        row.update(values)
        return row

    def map_native(self, doctype, source_name, target_doc, **kwargs):
        # A faithful problematic draft: native copied confirmed process loss
        # and returned_qty can be stale; our mapper must use actual source rows.
        self.events.append(("mapped", kwargs))
        rows = [Row(row, name="MAPPED-" + row.name, qty=-row.qty,
                    subcontracting_receipt_item=row.name) for row in self.source.items]
        return self.document("UNSAVED", rows, cls=self.DocClass)

    def stock_entries(self, doctype, **kwargs):
        self.assertEqual(doctype, "Stock Ledger Entry")
        self.assertEqual(kwargs["filters"], {"company": "JF", "voucher_type": "Subcontracting Receipt",
                                             "voucher_no": "SOURCE", "is_cancelled": 0})
        self.assertEqual(kwargs["limit_page_length"], 0)
        rows = []
        for original in self.source.items:
            # ERPNext 16.36 accepted rate = allocated source amount / accepted
            # document qty; SLE qty = document qty * conversion_factor. Rejected
            # entries use a separate warehouse and incoming rate zero.
            rate = Decimal(str(original.amount)) / Decimal(str(original.qty or original.rejected_qty))
            for field, warehouse, price in (("qty", original.warehouse, rate),
                                            ("rejected_qty", original.rejected_warehouse, Decimal(0))):
                qty = Decimal(str(original[field])) * Decimal(str(original.conversion_factor))
                if qty:
                    rows.append(Row(name="SLE-" + original.name + field, company="JF", item_code="FG",
                                    warehouse=warehouse, voucher_type="Subcontracting Receipt", voucher_no="SOURCE",
                                    voucher_detail_no=original.name, actual_qty=float(qty),
                                    stock_value_difference=float(qty * price), is_cancelled=0))
        for index, raw in enumerate(self.source.get("supplied_items") or []):
            raw.setdefault("name", "RAW-" + str(index))
            if raw.consumed_qty:
                rows.append(Row(name="SLE-" + raw.name, company="JF", item_code=raw.rm_item_code,
                    warehouse="Supplier", voucher_type="Subcontracting Receipt", voucher_no="SOURCE",
                    voucher_detail_no=raw.name, actual_qty=-raw.consumed_qty,
                    stock_value_difference=-raw.amount, is_cancelled=0))
        return rows

    def test_partial_accepted_return_preserves_rejected_pool_and_native_validation(self):
        self.prior.append(self.row(qty=-2, warehouse="Rejected"))
        self.assertEqual(self.doc.validate(), "native-result")
        self.assertEqual(len(self.native_calls), 1)
        totals = self.subject.validate_physical_return(self.doc)[2]
        self.assertEqual(totals["SRC-ROW"], [Decimal(1), Decimal(2)])

    def test_existing_and_current_duplicate_lines_share_exact_pool(self):
        self.prior.append(self.row(qty=-4))
        self.doc.items = [self.row(qty=-1.5), self.row(name="OTHER", qty=-1)]
        with self.assertRaisesRegex(ValueError, "累计实物"):
            self.doc.validate()
        self.assertEqual(self.native_calls, [])

    def test_same_item_different_source_rows_do_not_share_capacity(self):
        self.source.items.append(Row(self.source_row, name="OTHER-SOURCE", qty=10))
        self.prior.append(self.row(subcontracting_receipt_item="OTHER-SOURCE", qty=-20))
        self.assertEqual(self.doc.validate(), "native-result")
        self.doc.items[0].qty = -7
        with self.assertRaisesRegex(ValueError, "累计实物"):
            self.doc.validate()

    def test_cancelled_return_releases_remaining_quantity(self):
        self.prior.append(self.row(qty=-6, docstatus=2))
        self.doc.items[0].qty = -6
        self.assertEqual(self.doc.validate(), "native-result")
        self.prior[0].docstatus = 1
        with self.assertRaisesRegex(ValueError, "累计实物"):
            self.doc.validate()

    def test_source_lock_precedes_current_read_and_native_write(self):
        self.doc.validate()
        self.assertEqual(self.events[0], ("source", "SOURCE", {"for_update": True}))
        self.assertIn("for update", self.events[1][1].lower())
        self.assertIn("parent.docstatus = 1", self.events[1][1])
        self.assertEqual(self.events[1][2], ("SOURCE", "CURRENT"))

    def test_invalid_sources_cannot_reach_native_posting(self):
        cases = [{"subcontracting_receipt_item": None}, {"item_code": "OTHER"},
                 {"jf_flow_order": "OTHER"}, {"stock_uom": "Kg"}, {"job_card": "OTHER"},
                 {"subcontracting_order_item": "OTHER"}, {"bom": "OTHER"},
                 {"warehouse": "Elsewhere"}, {"conversion_factor": 0},
                 {"qty": float("nan")}, {"qty": 1}, {"process_loss_qty": 1},
                 {"qty": 0, "rejected_qty": 0}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.doc.items = [self.row(**changes)]
                with self.assertRaises(ValueError):
                    self.doc.validate()
        self.assertEqual(self.native_calls, [])

    def test_parent_company_supplier_supplier_warehouse_and_time_are_checked(self):
        cases = [{"company": "OTHER"}, {"supplier": "OTHER"}, {"supplier_warehouse": "OTHER"},
                 {"posting_time": "09:59:59"}]
        for changes in cases:
            with self.subTest(changes=changes):
                doc = self.document("CURRENT", [self.row()], cls=self.DocClass, **changes)
                with self.assertRaises(ValueError):
                    doc.validate()
        self.assertEqual(self.native_calls, [])

    def test_native_stock_and_subcontracted_item_rules_are_preserved(self):
        for field in ("is_stock_item", "is_sub_contracted_item"):
            with self.subTest(field=field):
                self.frappe.get_cached_value = lambda doctype, name, requested: requested != field
                with self.assertRaisesRegex(ValueError, "库存物料|委外加工物料"):
                    self.doc.validate_items()

    def test_rejected_qty_and_native_rejected_warehouse_mapping_use_same_pool(self):
        self.prior.append(self.row(qty=0, rejected_qty=-1))
        self.doc.items = [self.row(qty=-1, warehouse="Rejected")]
        self.assertEqual(self.doc.validate(), "native-result")
        self.doc.items[0].qty = -1.01
        with self.assertRaisesRegex(ValueError, "累计实物"):
            self.doc.validate()

    def test_same_warehouse_cannot_guess_return_pool(self):
        self.source_row.rejected_warehouse = "Good"
        with self.assertRaisesRegex(ValueError, "不能唯一"):
            self.doc.validate()

    def test_native_calculation_cannot_double_operator_quantity(self):
        self.doc.native_adjust_qty = True
        with self.assertRaisesRegex(ValueError, "原生计算改变"):
            self.doc.validate()

    def test_one_negative_row_does_not_allow_a_zero_row_to_divide_by_zero(self):
        self.doc.items.append(self.row(name="ZERO", qty=0, rejected_qty=0))
        self.doc.native_full_calculation = True
        with self.assertRaisesRegex(ValueError, "零数量明细"):
            self.doc.validate()
        self.assertEqual(self.native_calls, [])
        # Form recalculation can precede Save/validate_items, so the monetary
        # calculation itself also gives the operator the actionable error.
        with self.assertRaisesRegex(ValueError, "零数量明细"):
            self.doc.calculate_items_qty_and_amount()

    def add_material(self, qty=10, rate=8, amount=80, source_code="RAW", current_code="RAW"):
        self.source.supplied_items = [Row(reference_name="SRC-ROW", main_item_code="FG",
                                         rm_item_code=source_code, stock_uom="Nos",
                                         consumed_qty=10, rate=8, amount=80)]
        self.source_row.rm_supp_cost = 80
        self.source_row.amount = 100
        self.doc.supplied_items = [Row(reference_name="RETURN-ROW", main_item_code="FG",
                                      rm_item_code=current_code, stock_uom="Nos",
                                      consumed_qty=-qty, rate=rate, amount=-amount)]

    def test_historical_consumption_and_service_cost_match_exact_partial_stock_return(self):
        # Original covers 10 stock units including 2 confirmed loss. Returning
        # one physical unit restores 1 RAW / 8, with service -2. Loss is kept.
        self.add_material(qty=1, rate=8, amount=8)
        self.assertEqual(self.doc.validate(), "native-result")
        self.assertEqual(self.doc.supplied_items[0].consumed_qty, -1)
        self.assertEqual(self.source.supplied_items[0].consumed_qty, 10)
        self.assertEqual(self.source_row.process_loss_qty, 1)
        self.assertEqual(-self.doc.supplied_items[0].amount
                         - self.doc.items[0].qty * self.doc.items[0].service_cost_per_qty, 10)

    def test_current_bom_or_valuation_cannot_fake_historical_material_quantity_or_money(self):
        cases = [dict(qty=2, rate=8, amount=16), dict(qty=1, rate=9, amount=9),
                 dict(qty=1, rate=8, amount=9), dict(qty=1, rate=8, amount=8, current_code="OTHER")]
        for values in cases:
            with self.subTest(values=values):
                self.add_material(**values)
                with self.assertRaises(ValueError):
                    self.doc.validate()

    def test_service_or_additional_cost_changes_are_rejected_before_stock_posting(self):
        for field in ("service_cost_per_qty", "additional_cost_per_qty"):
            with self.subTest(field=field):
                self.doc.items = [self.row()]
                self.doc.items[0][field] += 1
                with self.assertRaisesRegex(ValueError, "工价与原收回"):
                    self.doc.validate()

    def test_multi_raw_rows_aggregate_only_within_the_exact_receipt_row(self):
        self.add_material(qty=0.5, rate=8, amount=4)
        self.doc.supplied_items.append(Row(self.doc.supplied_items[0], consumed_qty=-0.5, amount=-4))
        self.assertEqual(self.doc.validate(), "native-result")
        self.doc.supplied_items[1].reference_name = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "无法对应准确"):
            self.doc.validate()

    def test_current_bom_and_valuation_changes_do_not_control_actual_return_generation(self):
        self.add_material(qty=2, rate=9, amount=18)
        self.doc.native_full_calculation = True
        self.assertEqual(self.doc.validate(), "native-result")
        raw = self.doc.supplied_items[0]
        self.assertEqual((raw.consumed_qty, raw.rate, raw.amount), (-1, 8, -8))
        self.assertEqual((self.doc.items[0].rm_supp_cost, self.doc.items[0].service_cost_per_qty), (-8, 2))
        self.assertAlmostEqual(self.doc.items[0].amount, -100 / 3)
        self.assertAlmostEqual(self.doc.total, -100 / 3)
        # The source itself is neither modified nor credited.
        self.assertEqual(self.source.supplied_items[0].amount, 80)
        self.assertEqual(self.source_row.amount, 100)

    def loss_cost_fixture(self, rejected=0, loss=1):
        self.source_row.update(qty=2, rejected_qty=rejected, process_loss_qty=loss,
                               conversion_factor=1, received_qty=2 + rejected + loss,
                               service_cost_per_qty=2, amount=30, rm_supp_cost=24)
        self.source.supplied_items = [Row(doctype="Subcontracting Receipt Supplied Item",
            parent="SOURCE", reference_name="SRC-ROW", main_item_code="FG", rm_item_code="RAW",
            stock_uom="Nos", consumed_qty=3, rate=8, amount=24)]
        self.doc.items = [self.row()]
        self.doc.supplied_items = []
        self.doc.native_full_calculation = True

    def test_original_loss_stays_while_accepted_unit_stock_value_is_not_allocated_twice(self):
        self.loss_cost_fixture()
        self.assertEqual(self.doc.validate(), "native-result")
        row, raw = self.doc.items[0], self.doc.supplied_items[0]
        # Native original: 3 covered * (RAW8 + service2) = 30 allocated to
        # accepted2 => original SLE is +2 / +30. Returning accepted1 must be
        # -1 / -15, while restoring only RAW1 / 8 and original unit service2.
        self.assertEqual((row.qty, row.rate, row.amount, self.doc.total), (-1, 15, -15, -15))
        self.assertEqual((raw.consumed_qty, raw.rate, raw.amount, row.service_cost_per_qty), (-1, 8, -8, 2))
        self.assertEqual((self.source_row.qty, self.source_row.process_loss_qty,
                          self.source.supplied_items[0].consumed_qty), (2, 1, 3))

    def test_rejected_physical_return_keeps_original_zero_value_stock_pool(self):
        self.loss_cost_fixture(rejected=1, loss=0)
        self.doc.items[0].warehouse = "Rejected"
        self.assertEqual(self.doc.validate(), "native-result")
        row, raw = self.doc.items[0], self.doc.supplied_items[0]
        self.assertEqual((row.qty, row.rate, row.amount), (-1, 0, 0))
        self.assertEqual((raw.consumed_qty, raw.amount, row.service_cost_per_qty), (-1, -8, 2))
        self.assertEqual(self.subject.validate_physical_return(self.doc)[2]["SRC-ROW"],
                         [Decimal(0), Decimal(1)])

    def test_all_rejected_legacy_missing_cache_uses_exact_native_raw_stock_and_zero_fg_value(self):
        self.loss_cost_fixture(rejected=2, loss=1)
        self.source_row.update(qty=0, received_qty=3, amount=6, rm_supp_cost=None, rm_cost_per_qty=0)
        self.doc.items = [self.row(warehouse="Rejected")]
        self.assertEqual(self.doc.validate(), "native-result")
        row, raw = self.doc.items[0], self.doc.supplied_items[0]
        self.assertEqual((row.qty, row.rate, row.amount, raw.consumed_qty, raw.amount), (-1, 0, 0, -1, -8))
        self.assertEqual((row.rm_supp_cost, row.service_cost_per_qty), (-8, 2))
        self.assertEqual((self.source_row.rm_supp_cost, self.source_row.process_loss_qty), (None, 1))

    def test_all_rejected_raw_stock_missing_hidden_or_changed_is_not_a_zero_cost_fallback(self):
        for change in ("missing", "hidden", "quantity", "value"):
            with self.subTest(change=change):
                self.loss_cost_fixture(rejected=2, loss=1)
                self.source_row.update(qty=0, received_qty=3, amount=6, rm_supp_cost=0)
                self.source._jifeng_return_stock_entries = None
                rows = self.stock_entries("Stock Ledger Entry", filters={"company": "JF",
                    "voucher_type": "Subcontracting Receipt", "voucher_no": "SOURCE", "is_cancelled": 0},
                    limit_page_length=0)
                if change in ("missing", "hidden"):
                    rows = [row for row in rows if row.item_code != "RAW"]
                    if change == "missing":
                        self.frappe.get_all = lambda *args, **kwargs: [row.name for row in rows]
                elif change == "quantity":
                    rows[-1].actual_qty = -2
                else:
                    rows[-1].stock_value_difference = -23
                self.frappe.get_list = lambda *args, **kwargs: rows
                with self.assertRaises((ValueError, PermissionError)):
                    self.subject._material_facts(self.source)

    def test_forward_all_rejected_seeds_exact_material_then_calls_native_cost_calculation(self):
        self.doc.is_return = 0
        self.doc.items = [self.row(qty=0, rejected_qty=2, received_qty=0, process_loss_qty=1,
                                  rm_supp_cost=0, rm_cost_per_qty=999)]
        self.doc.supplied_items = [Row(reference_name="RETURN-ROW", main_item_code="FG",
                                     consumed_qty=3, rate=8)]
        self.assertEqual(self.doc.calculate_items_qty_and_amount(), "native-cost-result")
        row = self.doc.items[0]
        self.assertEqual((row.received_qty, row.rm_supp_cost, row.rm_cost_per_qty), (3, 24, 8))
        self.assertEqual(self.native_calls, ["forward-cost"])

    def test_forward_all_rejected_unknown_valuation_and_additional_allocation_stop_before_native(self):
        self.doc.is_return = 0
        self.doc.items = [self.row(qty=0, rejected_qty=2, process_loss_qty=1)]
        for field, value in (("bom", None), ("landed_cost_voucher_amount", 4)):
            with self.subTest(field=field):
                saved = self.doc.items[0].get(field)
                self.doc.items[0][field] = value
                with self.assertRaisesRegex(ValueError, "全部拒收"):
                    self.doc.calculate_items_qty_and_amount()
                self.doc.items[0][field] = saved
        self.doc.additional_costs = [Row(amount=4)]
        with self.assertRaisesRegex(ValueError, "附加费用分摊规则"):
            self.doc.calculate_additional_costs()
        self.assertEqual(self.native_calls, [])

    def test_all_rejected_keeps_native_stock_writer_warehouse_gate_explicit(self):
        self.doc.is_return = 0
        self.doc.items = [self.row(qty=0, rejected_qty=2, process_loss_qty=0, warehouse=None)]
        self.doc.supplied_items = []
        with self.assertRaisesRegex(ValueError, "接收仓库和拒收仓库"):
            self.doc.calculate_items_qty_and_amount()
        self.doc.set_warehouse = "Good"
        self.doc.calculate_items_qty_and_amount()
        self.assertEqual(self.doc.items[0].warehouse, "Good")

    def test_accepted_and_rejected_prices_cannot_be_blended_into_one_native_row(self):
        self.loss_cost_fixture(rejected=1, loss=0)
        self.doc.items[0].rejected_qty = -1
        with self.assertRaisesRegex(ValueError, "分开两行"):
            self.doc.validate()

    def test_plain_no_loss_receipt_returns_original_material_and_service_value(self):
        self.loss_cost_fixture(rejected=0, loss=0)
        self.source_row.update(received_qty=2, amount=20, rm_supp_cost=16)
        self.source.supplied_items[0].update(consumed_qty=2, amount=16)
        self.assertEqual(self.doc.validate(), "native-result")
        row, raw = self.doc.items[0], self.doc.supplied_items[0]
        self.assertEqual((row.rate, row.amount, raw.consumed_qty, raw.amount), (10, -10, -1, -8))

    def test_source_received_fact_must_match_accepted_rejected_and_loss(self):
        for received in (None, 0, 4):
            with self.subTest(received=received):
                self.source_row.received_qty = received
                with self.assertRaisesRegex(ValueError, "覆盖数量"):
                    self.doc.validate()

    def test_supplied_item_precision_uses_consumed_quantity_field(self):
        calls = []
        class SuppliedRow(Row):
            def precision(self, field):
                calls.append(field)
                if field == "qty":
                    raise AssertionError("Supplied Item has no qty field")
                return 3 if field == "consumed_qty" else 2
        raw = SuppliedRow(doctype="Subcontracting Receipt Supplied Item")
        self.assertTrue(self.subject._same(1.0001, 1, raw))
        self.assertTrue(self.subject._same(8.001, 8, raw, "amount"))
        self.assertEqual(calls, ["consumed_qty", "consumed_qty", "amount", "amount"])

    def test_exact_original_stock_quantity_and_value_are_required(self):
        for change in ("hidden", "wrong_row", "wrong_qty", "negative_value"):
            with self.subTest(change=change):
                self.source._jifeng_return_stock_pools = None
                self.source._jifeng_return_stock_entries = None
                rows = self.stock_entries("Stock Ledger Entry", filters={"company": "JF",
                    "voucher_type": "Subcontracting Receipt", "voucher_no": "SOURCE",
                    "is_cancelled": 0}, limit_page_length=0)
                if change == "hidden":
                    rows = rows[1:]
                elif change == "wrong_row":
                    rows[0].voucher_detail_no = "OTHER"
                elif change == "wrong_qty":
                    rows[0].actual_qty = 5
                else:
                    rows[0].stock_value_difference = -1
                self.frappe.get_list = lambda *args, **kwargs: rows
                with self.assertRaises((ValueError, PermissionError)):
                    self.subject._stock_pools(self.source)

    def test_stock_value_permission_and_unsettled_source_repost_are_not_bypassed(self):
        self.frappe.has_permission = lambda doctype, mode: doctype != "Stock Ledger Entry"
        with self.assertRaisesRegex(PermissionError, "实物入库价值"):
            self.subject._stock_pools(self.source)
        self.frappe.has_permission = lambda doctype, mode: True
        def repost(doctype, filters):
            self.assertEqual(doctype, "Repost Item Valuation")
            self.assertEqual((filters["company"], filters["voucher_type"], filters["voucher_no"]),
                             ("JF", "Subcontracting Receipt", "SOURCE"))
            return "PENDING-ONLY-THIS-SOURCE"
        self.frappe.db.exists = repost
        with self.assertRaisesRegex(ValueError, "库存重估"):
            self.subject._stock_pools(self.source)

    def test_partial_additional_cost_preserves_historical_rate_and_expense_account(self):
        self.add_material(qty=1, rate=8, amount=8)
        self.source_row.additional_cost_per_qty = 1
        self.source_row.amount = 105
        self.source.additional_costs = [Row(amount=3, base_amount=3, exchange_rate=1,
                                            expense_account="Historical Freight", account_currency="CNY")]
        self.doc.items[0].additional_cost_per_qty = 999
        self.doc.native_full_calculation = True
        self.assertEqual(self.doc.validate(), "native-result")
        self.assertEqual(self.doc.items[0].additional_cost_per_qty, 0.5)
        self.assertEqual(self.doc.items[0].amount, -35)
        self.assertEqual(self.doc.additional_costs[0].amount, -0.5)
        self.assertEqual(self.doc.additional_costs[0].base_amount, -0.5)
        self.assertEqual(self.doc.additional_costs[0].expense_account, "Historical Freight")

    def test_draft_serial_material_does_not_choose_real_serials_and_submit_requires_selection(self):
        self.add_material(qty=1, rate=8, amount=8)
        self.source.supplied_items[0].serial_no = "\n".join("SER-" + str(i) for i in range(10))
        self.doc.native_full_calculation = True
        self.assertEqual(self.doc.validate(), "native-result")
        self.assertEqual(self.doc.supplied_items[0].serial_no, "")
        self.assertEqual(self.doc.supplied_items[0].use_serial_batch_fields, 0)
        self.doc._action = "submit"
        with self.assertRaisesRegex(ValueError, "明确选择"):
            self.doc.validate()

    def test_legacy_serial_source_without_individual_valuation_is_not_guessed(self):
        self.add_material(qty=1, rate=8, amount=8)
        self.source.supplied_items[0].serial_no = "\n".join("SER-" + str(i) for i in range(10))
        self.prior_raw = [Row(parent="PRIOR", source_fg_row="SRC-ROW", rm_item_code="RAW",
                              name="PRIOR-RAW", reference_name="PRIOR-FG", parenttype="Subcontracting Receipt",
                              parentfield="supplied_items", stock_uom="Nos", serial_no="SER-0", consumed_qty=-1, docstatus=1)]
        self.prior_docs["PRIOR"] = self.document("PRIOR", [self.row(name="PRIOR-FG")],
            docstatus=1, supplied_items=[Row(self.prior_raw[0], doctype="Subcontracting Receipt Supplied Item")])
        self.doc.native_full_calculation = True
        with self.assertRaisesRegex(ValueError, "逐号历史估值"):
            self.doc.validate()

    def test_missing_actual_material_rows_cannot_be_treated_as_zero_cost(self):
        self.source_row.rm_supp_cost = 8
        self.doc.native_full_calculation = True
        with self.assertRaisesRegex(ValueError, "材料金额与准确耗料"):
            self.doc.validate()

    def test_draft_multi_batch_material_stays_unselected_and_submit_does_not_invent_quantities(self):
        self.add_material(qty=1, rate=8, amount=8)
        self.source.supplied_items[0].serial_and_batch_bundle = "SOURCE-BUNDLE"
        bundle = self.document("SOURCE-BUNDLE", [], item_code="RAW", docstatus=1,
                               voucher_type="Subcontracting Receipt", voucher_no=None,
                               warehouse="Supplier",
                               entries=[Row(batch_no="A"), Row(batch_no="B")])
        get_source = self.frappe.get_doc
        self.frappe.get_doc = lambda doctype, name, **kw: bundle if doctype == "Serial and Batch Bundle" else get_source(doctype, name, **kw)
        self.doc.native_full_calculation = True
        self.assertEqual(self.doc.validate(), "native-result")
        self.assertFalse(self.doc.supplied_items[0].serial_and_batch_bundle)
        self.doc._action = "submit"
        with self.assertRaisesRegex(ValueError, "明确选择"):
            self.doc.validate()

    def batch_fixture(self, *, serial=False):
        self.add_material(qty=1, rate=8, amount=8)
        source = self.source.supplied_items[0]
        source.update(name="SOURCE-RAW", parent="SOURCE", bom_detail_no="BOM-RAW", serial_and_batch_bundle="ORIGINAL-BUNDLE")
        current = self.doc.supplied_items[0]
        current.update(name="CURRENT-RAW", parent="CURRENT", bom_detail_no="BOM-RAW", serial_and_batch_bundle="CHOSEN-BUNDLE")
        entries = ([Row(serial_no="SER-" + str(index), qty=-1, incoming_rate=8, stock_value_difference=-8,
                        warehouse="Supplier") for index in range(10)] if serial else
                   [Row(batch_no="A", qty=-4, incoming_rate=5, stock_value_difference=-20, warehouse="Supplier"),
                    Row(batch_no="B", qty=-6, incoming_rate=10, stock_value_difference=-60, warehouse="Supplier")])
        self.bundles = {
            "ORIGINAL-BUNDLE": Document(name="ORIGINAL-BUNDLE", doctype="Serial and Batch Bundle", docstatus=1,
                company="JF", item_code="RAW", warehouse="Supplier", type_of_transaction="Outward",
                voucher_type="Subcontracting Receipt", voucher_no="SOURCE", voucher_detail_no="SOURCE-RAW",
                entries=entries, total_qty=-10),
            "CHOSEN-BUNDLE": Document(name="CHOSEN-BUNDLE", doctype="Serial and Batch Bundle", docstatus=0,
                company="JF", item_code="RAW", warehouse="Supplier", type_of_transaction="Inward",
                voucher_type="Subcontracting Receipt", voucher_no="CURRENT", voucher_detail_no="CURRENT-RAW",
                entries=[Row(serial_no="SER-7", qty=1, warehouse="Supplier")] if serial else
                        [Row(batch_no="A", qty=1, warehouse="Supplier")], total_qty=1),
        }
        for bundle in self.bundles.values():
            for entry in bundle.entries:
                entry.docstatus = bundle.docstatus
        fetch = self.frappe.get_doc
        self.frappe.get_doc = lambda kind, name, **kw: self.bundles[name] if kind == "Serial and Batch Bundle" else fetch(kind, name, **kw)
        self.doc.native_full_calculation = True
        return source, current

    def test_operator_multi_batch_selection_is_preserved_and_mixed_original_prices_are_exact(self):
        self.batch_fixture()
        self.doc._action = "submit"
        self.assertEqual(self.doc.validate(), "native-result")
        raw = self.doc.supplied_items[0]
        self.assertEqual((raw.serial_and_batch_bundle, raw.consumed_qty, raw.rate, raw.amount),
                         ("CHOSEN-BUNDLE", -1, 5, -5))
        self.assertEqual((self.doc.items[0].rm_supp_cost, self.doc.items[0].service_cost_per_qty), (-5, 2))
        # Native FG returns its original stock value separately, irrespective
        # of which precisely valued raw batch was physically selected.
        self.assertAlmostEqual(self.doc.items[0].amount, -100 / 3)

    def test_operator_serial_selection_is_preserved_without_replacing_it_with_first_serial(self):
        self.batch_fixture(serial=True)
        self.doc._action = "submit"
        self.assertEqual(self.doc.validate(), "native-result")
        self.assertEqual(self.doc.supplied_items[0].serial_and_batch_bundle, "CHOSEN-BUNDLE")
        self.assertEqual(self.bundles["CHOSEN-BUNDLE"].entries[0].serial_no, "SER-7")

    def test_serial_bundle_mapping_does_not_claim_native_disabled_serial_rates_are_written(self):
        self.batch_fixture(serial=True)
        self.frappe.db.exists = lambda kind, name: kind == "Subcontracting Receipt Supplied Item" and name == "CURRENT-RAW"
        Bundle = type("ReturnBundle", (self.subject.SubcontractingReceiptMaterialReturnBundleMixin, Document), {})
        bundle = Bundle(**vars(self.bundles["CHOSEN-BUNDLE"]))
        # Native deliberately skips the entry-rate update in this Item mode.
        self.source.has_serial_no, self.source.use_serial_no_wise_valuation = 1, 0
        with self.assertRaisesRegex(ValueError, "未启用原生逐号估值"):
            bundle.get_valuation_rate_for_return_entry("SOURCE")
        self.source.use_serial_no_wise_valuation = 1
        rates = bundle.get_valuation_rate_for_return_entry("SOURCE")
        self.assertEqual(rates["serial_nos"]["SER-7"], 8)
        self.assertEqual(rates["batches"], {})

    def test_submitted_lot_capacity_and_cancelled_return_release_are_exact(self):
        self.batch_fixture()
        prior = Row(name="PRIOR-RAW", parent="PRIOR", source_fg_row="SRC-ROW", reference_name="PRIOR-FG",
            parenttype="Subcontracting Receipt", parentfield="supplied_items",
            main_item_code="FG", rm_item_code="RAW", stock_uom="Nos", bom_detail_no="BOM-RAW",
            consumed_qty=-4, rate=5, amount=-20, serial_and_batch_bundle="PRIOR-BUNDLE", docstatus=1)
        self.prior_raw = [prior]
        self.prior_docs["PRIOR"] = self.document("PRIOR", [self.row(name="PRIOR-FG", qty=-4)],
            docstatus=1, supplied_items=[Row(prior, doctype="Subcontracting Receipt Supplied Item", _native_cache=1)])
        self.bundles["PRIOR-BUNDLE"] = Document(name="PRIOR-BUNDLE", docstatus=1, company="JF", item_code="RAW",
            warehouse="Supplier", type_of_transaction="Inward", voucher_type="Subcontracting Receipt",
            voucher_no="PRIOR", voucher_detail_no="PRIOR-RAW", total_qty=4,
            entries=[Row(batch_no="A", qty=4, incoming_rate=5, stock_value_difference=20, warehouse="Supplier", docstatus=1)])
        with self.assertRaisesRegex(ValueError, "累计退回"):
            self.doc.validate()
        prior.docstatus = 2
        self.prior_docs["PRIOR"].docstatus = 2
        self.assertEqual(self.doc.validate(), "native-result")

    def test_ambiguous_original_row_wrong_lot_or_wrong_selected_quantity_is_rejected(self):
        for change in ("duplicate_source", "other_batch", "wrong_quantity", "wrong_original_value"):
            with self.subTest(change=change):
                source, _ = self.batch_fixture()
                if change == "duplicate_source":
                    self.source.supplied_items.append(Row(source, name="OTHER-RAW", consumed_qty=0, amount=0))
                elif change == "other_batch":
                    self.bundles["CHOSEN-BUNDLE"].entries[0].batch_no = "OTHER"
                elif change == "wrong_quantity":
                    self.bundles["CHOSEN-BUNDLE"].total_qty = 2
                else:
                    self.bundles["ORIGINAL-BUNDLE"].entries[0].stock_value_difference = -19
                with self.assertRaises(ValueError):
                    self.doc.validate()
                self.source.supplied_items = [source]

    def test_hidden_submitted_return_is_not_skipped_when_raw_lock_query_has_no_rows(self):
        self.batch_fixture()
        self.prior_docs["HIDDEN"] = self.document("HIDDEN", [self.row(name="HIDDEN-FG")],
            docstatus=1, supplied_items=[], unreadable=True)
        with self.assertRaisesRegex(PermissionError, "无法完整读取既有"):
            self.doc.validate()
        self.prior_docs["HIDDEN"].unreadable = False
        with self.assertRaisesRegex(ValueError, "缺少或重复准确原耗料"):
            self.doc.validate()

    def test_prior_raw_child_table_permission_is_required_before_using_selected_lots(self):
        self.batch_fixture()
        self.prior_docs["PRIOR"] = self.document("PRIOR", [self.row(name="PRIOR-FG")],
            docstatus=1, supplied_items=[])
        self.tables["supplied_items"].permlevel = 1
        with self.assertRaisesRegex(PermissionError, "完整读取原收回明细表"):
            self.subject._prior_raw_returns(self.source, "CURRENT")

    def test_prior_native_child_doctype_and_temporary_attributes_are_not_sql_columns(self):
        self.batch_fixture()
        sql = Row(name="PRIOR-RAW", parent="PRIOR", parenttype="Subcontracting Receipt",
            parentfield="supplied_items", source_fg_row="SRC-ROW", reference_name="PRIOR-FG",
            main_item_code="FG", rm_item_code="RAW", stock_uom="Nos", bom_detail_no="BOM-RAW",
            consumed_qty=-1, rate=10, amount=-10, serial_and_batch_bundle="PRIOR-BUNDLE", docstatus=1)
        child = Row(sql, doctype="Subcontracting Receipt Supplied Item", _native_cache=1)
        self.prior_docs["PRIOR"] = self.document("PRIOR", [self.row(name="PRIOR-FG")],
            docstatus=1, supplied_items=[child])
        self.prior_raw = [sql]
        self.assertEqual(self.subject._prior_raw_returns(self.source, "CURRENT"), [sql])
        child.doctype = "Other Child"
        with self.assertRaisesRegex(ValueError, "准确原商品或耗料"):
            self.subject._prior_raw_returns(self.source, "CURRENT")

    def test_tracked_finished_goods_are_blocked_until_native_exact_return_valuation_is_connected(self):
        self.source_row.serial_and_batch_bundle = "FG-BUNDLE"
        with self.assertRaisesRegex(ValueError, "成品.*准确原入库估值"):
            self.doc.validate()

    def test_forward_and_cancel_keep_native_lifecycle(self):
        self.doc.is_return = 0
        self.assertEqual(self.doc.validate(), "native-result")
        self.doc.is_return, self.doc.docstatus = 1, 2
        self.assertEqual(self.doc.validate(), "native-result")
        self.assertEqual(self.events, [])

    def test_draft_mapper_uses_remaining_stock_qty_and_does_not_reverse_loss(self):
        self.prior.append(self.row(qty=-2))
        self.source_row.returned_qty = 999
        with patch.dict(sys.modules, self.modules):
            target = self.subject.make_subcontract_return("SOURCE")
        self.assertEqual((target.items[0].qty, target.items[0].received_qty), (-2, -2))
        self.assertEqual(target.items[0].process_loss_qty, 0)
        self.assertEqual(self.source_row.process_loss_qty, 1)
        self.assertEqual(target.items[0].subcontracting_receipt_item, "SRC-ROW")
        self.assertEqual(target.items[0].service_cost_per_qty, 4)
        # 2 units of source-UOM service at 4 => signed service -8; original
        # service invoice is untouched because the mapper returns only a draft.
        self.assertEqual(target.items[0].qty * target.items[0].service_cost_per_qty, -8)
        self.assertEqual(target.docstatus, 0)

    def test_rejected_mapper_preserves_other_pool_and_zeroes_original_loss(self):
        self.prior.append(self.row(qty=-1, warehouse="Rejected"))
        with patch.dict(sys.modules, self.modules):
            target = self.subject.make_subcontract_return_against_rejected_warehouse("SOURCE")
        row = target.items[0]
        self.assertEqual((row.qty, row.warehouse, row.rejected_qty, row.process_loss_qty),
                         (-0.5, "Rejected", 0, 0))

    def test_mapper_omits_exhausted_rows_and_rejects_all_exhausted(self):
        self.source.items.append(Row(self.source_row, name="SECOND", qty=1, received_qty=3))
        self.prior.append(self.row(qty=-6))
        with patch.dict(sys.modules, self.modules):
            target = self.subject.make_subcontract_return("SOURCE")
            self.assertEqual([row.subcontracting_receipt_item for row in target.items], ["SECOND"])
            self.prior.append(self.row(qty=-2, subcontracting_receipt_item="SECOND"))
            with self.assertRaisesRegex(ValueError, "全部退回"):
                self.subject.make_subcontract_return("SOURCE")

    def test_hidden_invalid_prior_cannot_be_ignored_and_unreadable_source_is_denied(self):
        self.prior.append(self.row(return_company="OTHER"))
        with self.assertRaisesRegex(ValueError, "既有"):
            self.doc.validate()
        self.prior = []
        self.source.unreadable = True
        with self.assertRaises(PermissionError):
            self.doc.validate()

    def test_source_cost_permission_and_mask_cannot_be_bypassed_by_draft_mapping(self):
        field = Row(mask=0, permlevel=0)
        self.frappe.get_meta = lambda doctype: types.SimpleNamespace(
            get_field=lambda name: field if name == "service_cost_per_qty" else self.tables.get(name),
            get_permlevel_access=lambda **kw: {0} if kw.get("permission_type") == "read" else set())
        with self.assertRaisesRegex(PermissionError, "来源字段的权限"):
            self.subject.make_subcontract_return("SOURCE")
        self.model.get_permitted_fields = lambda *args, **kw: ["service_cost_per_qty"]
        field.mask = 1
        with self.assertRaisesRegex(PermissionError, "被遮蔽"):
            self.subject.make_subcontract_return("SOURCE")

    def test_parent_tables_use_read_and_mask_levels_instead_of_scalar_projection(self):
        # Actual Frappe 16 scalar projection excludes all three Table fields.
        self.assertEqual(self.model.get_permitted_fields("Subcontracting Receipt"), [])
        self.subject._read_source_fields(self.source)
        self.tables["supplied_items"].permlevel = 1
        with self.assertRaisesRegex(PermissionError, "明细表的权限"):
            self.subject._read_source_fields(self.source)
        self.tables["supplied_items"].permlevel = 0
        self.tables["supplied_items"].mask = 1
        self.frappe.get_meta = lambda doctype: types.SimpleNamespace(
            get_field=lambda name: self.tables.get(name) if doctype == "Subcontracting Receipt" else None,
            get_permlevel_access=lambda **kw: {0} if kw.get("permission_type") == "read" else set())
        with self.assertRaisesRegex(PermissionError, "被遮蔽明细表"):
            self.subject._read_source_fields(self.source)


if __name__ == "__main__":
    unittest.main()
