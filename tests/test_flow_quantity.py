"""Unit changes must not silently become manufacturing or costing quantities."""

from decimal import Decimal
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from jifeng_erp.flow_quantity import (
    MANUAL_BASIS, SOURCE_FIELDS, FlowQuantityError, assert_no_quantity_downstream,
    read_item_stock_uom, read_sales_quantity_snapshot,
    require_stock_plan, sales_row_snapshot, same_version, source_snapshot_changed,
    plan_quantity,
)


class Record(dict):
    def __getattr__(self, key):
        return self.get(key)

    def check_permission(self, permission):
        if self.get("denied"):
            raise PermissionError("denied native document")

    def __setattr__(self, key, value):
        self[key] = value

    def precision(self, field):
        return 6


def sales_row(**changes):
    return Record(name="SO-ROW", parent="SO", parenttype="Sales Order", parentfield="items",
                  item_code="RING", qty=1, uom="Box", stock_uom="Nos", conversion_factor=12, stock_qty=12) | changes


class TestFlowQuantity(unittest.TestCase):
    def test_box_sales_snapshot_is_twelve_stock_units(self):
        snapshot = sales_row_snapshot(sales_row(), "Nos")
        self.assertEqual(snapshot["source_qty"], Decimal(1))
        self.assertEqual(snapshot["source_stock_qty"], Decimal(12))
        self.assertEqual((snapshot["source_uom"], snapshot["stock_uom"]), ("Box", "Nos"))

    def test_fractional_source_and_decimal_conversion(self):
        snapshot = sales_row_snapshot(sales_row(qty="1.25", uom="Kg", stock_uom="Gram",
                                               conversion_factor=1000, stock_qty=1250), "Gram")
        self.assertEqual(snapshot["source_stock_qty"], Decimal(1250))

    def test_native_rounded_stock_qty_is_preserved(self):
        snapshot = sales_row_snapshot(sales_row(conversion_factor="0.333333333", stock_qty="0.333333"), "Nos", 6)
        self.assertEqual(snapshot["source_stock_qty"], Decimal("0.333333"))
        with self.assertRaises(FlowQuantityError):
            sales_row_snapshot(sales_row(conversion_factor="0.333333333", stock_qty="0.333333"), "Nos", 9)

    def test_three_and_zero_precision_snapshots_roundtrip_without_six_digit_guess(self):
        for precision, factor, stock_qty in ((3, "1.2344", "1.234"), (0, "1.4", "1")):
            snapshot = sales_row_snapshot(sales_row(conversion_factor=factor, stock_qty=stock_qty), "Nos", precision)
            plan = snapshot | {"sales_order": "SO", "planned_qty": stock_qty}
            self.assertEqual(require_stock_plan(plan, "Nos"), Decimal(stock_qty))
            self.assertEqual(snapshot["source_stock_precision"], precision)
            self.assertFalse(source_snapshot_changed(snapshot, snapshot))
            self.assertTrue(source_snapshot_changed(snapshot, snapshot | {"source_stock_precision": precision + 1}))
            with self.assertRaises(FlowQuantityError):
                require_stock_plan(plan | {"source_stock_precision": None}, "Nos")

    def test_native_bankers_commercial_ties_and_unrounded_values_are_preserved(self):
        for precision, factor, stock_qty in ((3, "1.2345", "1.234"), (3, "1.2345", "1.235"),
                                            (0, "2.5", "2"), (0, "2.5", "3"), (3, "1.2345", "1.2345")):
            snapshot = sales_row_snapshot(sales_row(conversion_factor=factor, stock_qty=stock_qty), "Nos", precision)
            self.assertEqual(require_stock_plan(snapshot | {"sales_order": "SO", "planned_qty": stock_qty}, "Nos"),
                             Decimal(stock_qty))
        with self.assertRaises(FlowQuantityError):
            sales_row_snapshot(sales_row(conversion_factor="1.2345", stock_qty="1.2343"), "Nos", 3)

    def test_plan_quantities_fit_actual_field_precision_without_rounding_or_overflow(self):
        self.assertEqual(plan_quantity("1.123456789"), Decimal("1.123456789"))
        self.assertEqual(plan_quantity("1.234", 3), Decimal("1.234"))
        for value, precision in (("1.2344", 3), ("1.1234567891", 9), ("1e100", 9), ("1000000000000", 9), ("0.1", 0)):
            with self.subTest(value=value, precision=precision), self.assertRaises(FlowQuantityError):
                plan_quantity(value, precision)

    def test_missing_invalid_or_inconsistent_source_never_defaults(self):
        failures = [{field: value} for field in ("qty", "stock_qty", "conversion_factor")
                    for value in (None, "", True, "NaN", "Infinity", 0, -1)]
        failures += [{"uom": None}, {"stock_uom": ""}, {"uom": " Box "}, {"stock_qty": 11},
                     {"uom": "Nos", "conversion_factor": 12}]
        for changes in failures:
            with self.subTest(changes=changes), self.assertRaises(FlowQuantityError):
                sales_row_snapshot(sales_row(**changes), "Nos")

    def test_item_stock_unit_and_native_precision_are_checked(self):
        with self.assertRaises(FlowQuantityError):
            sales_row_snapshot(sales_row(), "Gram")
        for precision in (-1, 10, True, "6", None):
            with self.subTest(precision=precision), self.assertRaises(FlowQuantityError):
                sales_row_snapshot(sales_row(), "Nos", precision)

    def test_same_qty_unit_factor_or_stock_unit_change_requires_review(self):
        original = sales_row_snapshot(sales_row(), "Nos")
        self.assertFalse(source_snapshot_changed(original, original))
        for changed in (sales_row(uom="Pack"), sales_row(conversion_factor=6, stock_qty=6),
                        sales_row(stock_uom="Gram")):
            snapshot = sales_row_snapshot(changed, changed["stock_uom"])
            self.assertTrue(source_snapshot_changed(original, snapshot))
        self.assertTrue(source_snapshot_changed({"source_qty": 1}, original))

    def test_stock_plan_preserves_explicit_plan_without_rewriting_source(self):
        snapshot = sales_row_snapshot(sales_row(), "Nos")
        plan = snapshot | {"sales_order": "SO", "planned_qty": 6}
        self.assertEqual(require_stock_plan(plan, "Nos"), Decimal(6))
        self.assertEqual(plan["source_stock_qty"], Decimal(12))
        for changes in ({"quantity_basis": ""}, {"source_uom": None}, {"source_conversion_factor": 0},
                        {"planned_qty": 0}, {"stock_uom": "Gram"}, {"source_stock_uom": "Gram"}):
            with self.subTest(changes=changes), self.assertRaises(FlowQuantityError):
                require_stock_plan(plan | changes, "Nos")

    def test_external_manual_plan_never_invents_upstream_units(self):
        plan = {"external_order": "JST", "quantity_basis": MANUAL_BASIS, "planned_qty": 12,
                "stock_uom": "Nos", "source_qty": 1, "stock_plan_confirmation_note": "仓库确认12件",
                "stock_plan_confirmed_by": "writer", "stock_plan_confirmed_at": "2026-10-03 10:00:00"}
        self.assertEqual(require_stock_plan(plan, "Nos"), Decimal(12))
        self.assertNotIn("source_uom", plan)
        self.assertNotIn("source_conversion_factor", plan)
        for changes in ({"external_order": ""}, {"stock_plan_confirmation_note": ""},
                        {"stock_plan_confirmed_by": None}, {"stock_plan_confirmed_at": None}):
            with self.subTest(changes=changes), self.assertRaises(FlowQuantityError):
                require_stock_plan(plan | changes, "Nos")

    def test_version_requires_complete_native_timestamp(self):
        self.assertTrue(same_version("2026-10-03 10:00:00.000000", "2026-10-03 10:00:00"))
        for value in ("2026-10-03", "2026-10-03 10:00:01", "2026-10-03T10:00:00Z", None, "bad"):
            self.assertFalse(same_version(value, "2026-10-03 10:00:00"))

    def test_schema_leaves_history_without_a_guessed_default_or_required_unit(self):
        path = Path(__file__).parents[1] / "jifeng_erp/jifeng_erp/doctype/jifeng_flow_order/jifeng_flow_order.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        fields = {row["fieldname"]: row for row in schema["fields"]}
        self.assertFalse(fields["stock_uom"].get("reqd"))
        self.assertNotIn("default", fields["quantity_basis"])
        self.assertTrue(all(fields[field]["read_only"] for field in SOURCE_FIELDS))
        self.assertIn("库存单位", fields["planned_qty"]["label"])


class TestNativeQuantityGuards(unittest.TestCase):
    def setUp(self):
        self.columns = {"Item": {"stock_uom"}, "Sales Order": {"name", "company", "docstatus", "status"},
                        "Sales Order Item": {"item_code", "qty", "uom", "stock_uom", "conversion_factor", "stock_qty"}}
        self.masked, self.mask_levels = set(), set()
        self.item = Record(stock_uom="Nos")
        self.row = Record(sales_row())
        self.order = Record(name="SO", items=[self.row])
        self.calls, self.busy, self.blocked = [], False, None
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.QueryTimeoutError = TimeoutError
        self.frappe.QueryDeadlockError = RuntimeError
        self.frappe.session = Record(user="real-reader")
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.get_doc = lambda *args, **kwargs: self.item
        self.frappe.get_meta = lambda doctype: types.SimpleNamespace(
            fields=[Record(fieldname=field, permlevel=0, mask=(doctype, field) in self.masked)
                    for field in self.columns[doctype]],
            get_permlevel_access=lambda **kwargs: self.mask_levels if kwargs.get("permission_type") == "mask" else {0},
            get_field=lambda name: Record(fieldtype="Table" if doctype == "Sales Order" and name == "items" else "Data",
                                          options="Sales Order Item", permlevel=0, mask=(doctype, name) in self.masked))
        self.frappe.db = types.SimpleNamespace(sql=self.sql)
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, **kwargs: self.columns[doctype]
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def sql(self, query, values):
        self.calls.append((query, values))
        if self.busy:
            raise TimeoutError("busy hidden source")
        return [("hidden-source-id",)] if self.blocked and "`tab" + self.blocked + "`" in query else []

    def test_item_full_read_and_stock_field_permissions_remain_native(self):
        self.assertEqual(read_item_stock_uom("RING"), "Nos")
        self.item.denied = True
        with self.assertRaises(PermissionError):
            read_item_stock_uom("RING")
        self.item.denied = False
        self.columns["Item"] = set()
        with self.assertRaises(PermissionError):
            read_item_stock_uom("RING")

    def test_quantity_mask_and_native_source_table_fields_are_not_backfilled(self):
        self.masked.add(("Sales Order Item", "conversion_factor"))
        with self.assertRaises(PermissionError):
            read_sales_quantity_snapshot(self.order, self.row)
        self.mask_levels.add(0)
        self.assertEqual(read_sales_quantity_snapshot(self.order, self.row)["source_stock_qty"], 12)
        self.columns["Sales Order"] = set()
        with self.assertRaises(PermissionError):
            read_sales_quantity_snapshot(self.order, self.row)

    def test_exact_child_scope_and_source_parent_read_are_required(self):
        self.order.denied = True
        with self.assertRaises(PermissionError):
            read_sales_quantity_snapshot(self.order, self.row)
        self.order.denied = False
        for changes in ({"parent": "OTHER"}, {"parenttype": "Quotation"}, {"parentfield": "other"}, {"name": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                read_sales_quantity_snapshot(self.order, Record(self.row | changes))

    def test_native_no_value_table_is_authorized_by_meta_not_scalar_columns(self):
        self.assertNotIn("items", self.columns["Sales Order"])
        self.assertEqual(read_sales_quantity_snapshot(self.order, self.row)["source_stock_qty"], 12)
        self.masked.add(("Sales Order", "items"))
        with self.assertRaises(PermissionError):
            read_sales_quantity_snapshot(self.order, self.row)

    def test_every_linked_quantity_source_blocks_even_when_hidden(self):
        types_to_check = ("Work Order", "Job Card", "Jifeng Cost Preview", "Production Plan Item",
                          "Material Request Item", "Purchase Order Item", "Purchase Receipt Item",
                          "Purchase Invoice Item", "Subcontracting Order Item", "Subcontracting Receipt Item",
                          "Stock Entry Detail", "Delivery Note Item", "Sales Invoice Item")
        for doctype in types_to_check:
            self.blocked = doctype
            with self.subTest(doctype=doctype), self.assertRaises(FlowQuantityError) as caught:
                assert_no_quantity_downstream("FLOW")
            self.assertNotIn("hidden-source-id", str(caught.exception))
        self.blocked = None
        assert_no_quantity_downstream("FLOW")
        self.assertTrue(all("FOR UPDATE NOWAIT" in query and values == ("FLOW",) for query, values in self.calls))

    def test_busy_source_never_proceeds_or_repeats(self):
        self.busy = True
        with self.assertRaisesRegex(FlowQuantityError, "正在核对"):
            assert_no_quantity_downstream("FLOW")
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
