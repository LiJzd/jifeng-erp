"""Explicit stock-plan confirmation keeps old plans and native writes reviewable."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from jifeng_erp.flow_quantity import MANUAL_BASIS, SOURCE_FIELDS


class Record(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    def __deepcopy__(self, memo):
        return type(self)({key: deepcopy(value, memo) for key, value in dict.items(self)})

    @property
    def items(self):
        return self.get("items", [])

    def check_permission(self, permission):
        if permission in self.get("denied", ()):
            raise PermissionError("native document denied")

    def precision(self, field):
        return 6


class FakeDocument(Record):
    def precision(self, field):
        return 9

    def get_doc_before_save(self):
        return self.get("_before")

    def is_new(self):
        return self.get("_new", False)

    def set(self, field, value):
        self[field] = value

    def reload(self):
        self["_before"] = deepcopy(self["_stored"])
        for field, value in dict.items(self["_stored"]):
            self[field] = deepcopy(value)
        self["_events"].append("reload")
        return self

    def save(self):
        self.validate()
        self["_events"].append("save")
        self.modified = datetime(2026, 10, 3, 12, 0, 1)
        self["_stored"].update({key: deepcopy(value) for key, value in dict.items(self)
                               if not key.startswith("_") and key != "flags"})
        return self


class TestFlowQuantityConfirmation(unittest.TestCase):
    def setUp(self):
        self.events, self.sql, self.denied_fields = [], [], set()
        self.blocked, self.busy_source, self.source_lock_present = None, False, True
        self.item = Record(stock_uom="Nos", image=None)
        self.sale_row = Record(name="SO-ROW", parent="SO", parenttype="Sales Order", parentfield="items",
                               item_code="RING", qty=1, uom="Box", stock_uom="Nos", conversion_factor=12,
                               stock_qty=12)
        self.sale = Record(name="SO", company="JF", docstatus=1, status="To Deliver and Bill", items=[self.sale_row])
        self.external_row = Record(name="EXT-DETAIL", parent="EXT", parenttype="Jifeng External Order", parentfield="items",
                                   external_line_id="EXT-ROW", item_code="RING", qty=1, source_status="WaitConfirm")
        self.external = Record(name="EXT", company="JF", source_status="WaitConfirm", items=[self.external_row])
        schema = Path(__file__).parents[1] / "jifeng_erp/jifeng_erp/doctype/jifeng_flow_order/jifeng_flow_order.py"
        frappe = types.ModuleType("frappe")
        frappe.PermissionError = PermissionError
        frappe.QueryTimeoutError = TimeoutError
        frappe.QueryDeadlockError = RuntimeError
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))

        def whitelist(**kwargs):
            def decorate(function):
                function.allowed_methods = kwargs.get("methods")
                return function
            return decorate

        frappe.whitelist = whitelist
        frappe.session = Record(user="actual-stock-manager")
        frappe.utils = types.SimpleNamespace(now=lambda: "2026-10-03 12:00:00")
        frappe.db = types.SimpleNamespace(get_value=self.get_value, sql=self.query, exists=lambda *args: False)
        frappe.get_doc = self.get_doc
        self.columns = {"Item": {"stock_uom"},
                        "Sales Order": {"name", "company", "docstatus", "status"},
                        "Sales Order Item": {"item_code", "qty", "uom", "stock_uom", "conversion_factor", "stock_qty"},
                        "Jifeng External Order": {"company", "source_status"},
                        "Jifeng External Order Item": {"name", "parent", "parenttype", "parentfield", "external_line_id",
                                                       "item_code", "qty", "source_status"}}
        import json
        schema_fields = json.loads(schema.with_suffix(".json").read_text(encoding="utf-8"))["fields"]
        self.columns["Jifeng Flow Order"] = {field["fieldname"] for field in schema_fields} | {"name", "modified"}
        frappe.get_meta = lambda doctype: types.SimpleNamespace(
            fields=[Record(fieldname=field, permlevel=0, mask=0) for field in self.columns[doctype]],
            get_permlevel_access=lambda **kwargs: set() if kwargs.get("permission_type") == "mask" else {0},
            get_field=lambda name: Record(fieldtype="Table" if name == "items" and doctype in ("Sales Order", "Jifeng External Order") else "Data",
                                          options=doctype + " Item", permlevel=0, mask=0))
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, permission_type="read", **kwargs: (
            self.columns[doctype] - {field for dt, action, field in self.denied_fields
                                    if dt == doctype and action == permission_type})
        document = types.ModuleType("frappe.model.document")
        document.Document = FakeDocument
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.copy_recipe = lambda *args: None
        workflow.make_material_request = lambda *args: None
        workflow.make_work_order = lambda *args: None
        workflow.recipe_totals = lambda doc: (Decimal(0), Decimal(0))
        self.patch = patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model,
            "frappe.model.document": document, "jifeng_erp.workflow": workflow})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        spec = importlib.util.spec_from_file_location("flow_quantity_confirmation_under_test", schema)
        self.controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.controller)
        self.frappe = frappe
        self.flow = self.make_flow()

    def make_flow(self, **changes):
        fields = {field: None for field in self.columns["Jifeng Flow Order"]}
        fields.update(doctype="Jifeng Flow Order", name="FLOW", company="JF", item_code="RING",
                      modified=datetime(2026, 10, 3, 12, 0), planned_qty=1, source_qty=1,
                      source_changed=0, materials=[], operations=[])
        fields.update(changes)
        return self.controller.JifengFlowOrder(fields | {"flags": Record(from_sync=False, from_sales_order=False),
            "_before": Record(deepcopy(fields)), "_stored": Record(deepcopy(fields)), "_events": self.events})

    def get_value(self, doctype, name, field, **kwargs):
        if doctype == "Jifeng Flow Order":
            self.events.append("flow-lock" if kwargs.get("for_update") else "flow-read")
            return name
        return None

    def get_doc(self, doctype, name=None, **kwargs):
        if doctype == "Item":
            return self.item
        if doctype == "Jifeng Flow Order":
            return self.flow["_stored"]
        self.events.append("source-read-current" if kwargs.get("for_update") else "source-read")
        return self.sale if doctype == "Sales Order" else self.external

    def query(self, query, values):
        self.sql.append((query, values))
        if "WHERE name=%s FOR UPDATE NOWAIT" in query:
            self.events.append("source-lock")
            if self.busy_source:
                raise TimeoutError("source busy")
            return [(values[0],)] if self.source_lock_present else []
        self.events.append("downstream-current-check")
        return [("hidden-native-source",)] if self.blocked and "`tab" + self.blocked + "`" in query else []

    def confirm(self, **changes):
        args = {"expected_version": "2026-10-03 12:00:00", "planned_qty": 12,
                "stock_uom": "Nos", "note": "按实物及原订单确认12件"} | changes
        return self.flow.confirm_stock_plan(**args)

    def test_legacy_plan_changes_only_after_explicit_versioned_native_save(self):
        result = self.confirm()
        self.assertEqual((result["planned_qty"], result["stock_uom"]), (Decimal(12), "Nos"))
        self.assertEqual(self.flow.quantity_basis, "stock_v1")
        self.assertEqual(self.flow.stock_plan_confirmed_by, "actual-stock-manager")
        self.assertEqual(self.flow.stock_plan_confirmation_note, "按实物及原订单确认12件")
        self.assertLess(self.events.index("flow-lock"), self.events.index("downstream-current-check"))
        self.assertEqual(self.events.count("save"), 1)
        self.assertEqual(self.controller.JifengFlowOrder.confirm_stock_plan.allowed_methods, ["POST"])

    def test_stale_version_readonly_role_invalid_unit_and_empty_note_never_save(self):
        for changes in ({"expected_version": "2026-10-03 12:00:01"}, {"stock_uom": "Box"},
                        {"note": " "}, {"planned_qty": "NaN"}, {"expected_version": "2026-10-03"},
                        {"planned_qty": "1e100"}, {"planned_qty": "0.1234567891"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.confirm(**changes)
            self.assertNotIn("save", self.events)
        self.flow.denied = {"write"}
        with self.assertRaises(PermissionError):
            self.confirm()
        self.assertNotIn("save", self.events)

    def test_write_fields_and_item_read_are_not_bypassed(self):
        self.denied_fields.add(("Jifeng Flow Order", "write", "stock_uom"))
        with self.assertRaises(PermissionError):
            self.confirm()
        self.denied_fields.clear()
        self.item.denied = {"read"}
        with self.assertRaises(PermissionError):
            self.confirm()
        self.assertNotIn("save", self.events)

    def test_linked_hidden_purchase_plan_stock_or_preview_blocks_even_same_quantity(self):
        for doctype in ("Work Order", "Material Request Item", "Purchase Order Item", "Purchase Receipt Item",
                        "Purchase Invoice Item", "Production Plan Item", "Stock Entry Detail",
                        "Subcontracting Order Item", "Subcontracting Receipt Item", "Jifeng Cost Preview"):
            self.blocked = doctype
            with self.subTest(doctype=doctype), self.assertRaisesRegex(ValueError, "有效下游") as caught:
                self.confirm(planned_qty=1)
            self.assertNotIn("hidden-native-source", str(caught.exception))
            self.assertNotIn("save", self.events)

    def test_sales_confirmation_records_exact_current_source_without_converting_old_plan(self):
        self.flow = self.make_flow(sales_order="SO", sales_order_item="SO-ROW", source_changed=1)
        result = self.confirm()
        self.assertEqual(result["planned_qty"], Decimal(12))
        self.assertEqual((self.flow.source_qty, self.flow.source_uom, self.flow.source_conversion_factor,
                          self.flow.source_stock_qty), (Decimal(1), "Box", Decimal(12), Decimal(12)))
        self.assertEqual(self.flow.source_changed, 1, "stock plan confirmation does not acknowledge other source changes")
        self.assertLess(self.events.index("source-lock"), self.events.index("source-read-current"))

    def test_closed_other_company_changed_item_hidden_source_and_busy_source_stop(self):
        self.flow = self.make_flow(sales_order="SO", sales_order_item="SO-ROW")
        for field, value in (("status", "Closed"), ("company", "OTHER"), ("docstatus", 2)):
            original = self.sale[field]
            self.sale[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.confirm()
            self.sale[field] = original
        self.sale_row.item_code = "OTHER"
        with self.assertRaises(ValueError):
            self.confirm()
        self.sale_row.item_code = "RING"
        self.denied_fields.add(("Sales Order Item", "read", "conversion_factor"))
        with self.assertRaises(PermissionError):
            self.confirm()
        self.denied_fields.clear()
        self.busy_source = True
        with self.assertRaisesRegex(ValueError, "正在被核对"):
            self.confirm()
        self.assertNotIn("save", self.events)

    def test_external_confirmation_promises_only_plan_and_preserves_unknown_source_unit(self):
        self.flow = self.make_flow(external_order="EXT", external_line_id="EXT-ROW", quantity_basis="pending", source_changed=1)
        self.confirm()
        self.assertEqual(self.flow.quantity_basis, MANUAL_BASIS)
        self.assertEqual(self.flow.source_qty, 1)
        self.assertIsNone(self.flow.source_uom)
        self.assertIsNone(self.flow.source_conversion_factor)
        self.assertIsNone(self.flow.source_stock_qty)
        self.assertEqual(self.flow.source_changed, 1)
        self.assertEqual(self.flow._require_stock_plan(), 12)

    def test_external_exact_child_parent_type_field_name_and_read_permission_are_required(self):
        self.flow = self.make_flow(external_order="EXT", external_line_id="EXT-ROW", quantity_basis="pending")
        for field, value in (("name", ""), ("parent", "OTHER"), ("parenttype", "Sales Order"), ("parentfield", "other")):
            original = self.external_row[field]
            self.external_row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.confirm()
            self.external_row[field] = original
        self.denied_fields.add(("Jifeng External Order Item", "read", "qty"))
        with self.assertRaises(PermissionError):
            self.confirm()
        self.assertNotIn("save", self.events)

    def test_pending_regular_remarks_save_and_source_update_do_not_infer_unit(self):
        self.flow = self.make_flow(external_order="EXT", external_line_id="EXT-ROW", material_amount=100, processing_amount=20)
        self.flow.requirements = "仅补充说明"
        self.flow.material_amount = 999
        self.flow.validate()
        self.assertIsNone(self.flow.stock_uom)
        self.assertIsNone(self.flow.quantity_basis)
        self.assertEqual((self.flow.material_amount, self.flow.processing_amount), (100, 20))
        self.flow.planned_qty = 12
        with self.assertRaisesRegex(ValueError, "历史流程单"):
            self.flow.validate()
        self.flow.planned_qty = 1
        self.flow.flags.from_sync = True
        self.flow.quantity_basis = MANUAL_BASIS
        with self.assertRaisesRegex(ValueError, "只能通过"):
            self.flow.validate()

    def test_pending_one_click_acknowledge_profile_and_preview_cannot_bypass(self):
        self.flow = self.make_flow(external_order="EXT", external_line_id="EXT-ROW")
        for method in (self.flow.acknowledge_source, self.flow.load_profile, self.flow.make_preview):
            with self.subTest(method=method.__name__), self.assertRaisesRegex(ValueError, "尚未核对"):
                method()
        self.assertNotIn("save", self.events)

    def test_new_manual_flow_gets_stock_unit_but_new_external_remains_pending(self):
        self.flow = self.make_flow()
        self.flow._before, self.flow._new = None, True
        self.flow.validate()
        self.assertEqual((self.flow.stock_uom, self.flow.quantity_basis), ("Nos", "stock_v1"))
        self.flow = self.make_flow(external_order="EXT", external_line_id="EXT-ROW")
        self.flow._before, self.flow._new = None, True
        self.flow.flags.from_sync = True
        self.flow.validate()
        self.assertEqual((self.flow.stock_uom, self.flow.quantity_basis, self.flow.source_changed), (None, "pending", 1))
        self.assertEqual((self.flow.material_amount, self.flow.processing_amount), (0, 0))
        self.assertTrue(all(self.flow.get(field) is None for field in SOURCE_FIELDS if field != "source_qty"))

    def test_new_flow_cannot_preload_human_confirmation_or_verified_external_unit(self):
        for payload in ({"quantity_basis": MANUAL_BASIS},
                        {"stock_plan_confirmation_note": "伪造此前已核对"},
                        {"stock_plan_confirmed_at": "2026-10-03 12:00:00"},
                        {"stock_plan_confirmed_by": "another-user"},
                        {"external_order": "EXT", "quantity_basis": "stock_v1", "stock_uom": "Nos"}):
            with self.subTest(payload=payload):
                self.flow = self.make_flow(**payload)
                self.flow._before, self.flow._new = None, True
                self.flow.flags.from_sync = True
                with self.assertRaisesRegex(ValueError, "不能预置人工核对"):
                    self.flow.validate()
        self.assertNotIn("save", self.events)

    def test_context_reads_saved_pending_value_and_current_unit_without_save_or_write_probe(self):
        self.flow = self.make_flow(external_order="EXT", external_line_id="EXT-ROW", quantity_basis="pending")
        self.flow.item_code, self.flow.planned_qty = "FORGED-CLIENT-ITEM", 999
        self.flow.denied = {"write"}
        context = self.flow.get_stock_plan_context()
        self.assertEqual(context, {"name": "FLOW", "version": "2026-10-03 12:00:00", "stock_uom": "Nos",
                                   "current_planned_qty": 1, "quantity_basis": "pending"})
        self.assertNotIn("save", self.events)
        self.assertNotIn("flow-lock", self.events)
        self.assertIsNone(self.flow["_stored"].stock_uom)
        self.assertEqual(self.controller.JifengFlowOrder.get_stock_plan_context.allowed_methods, ["GET", "POST"])

    def test_context_parent_field_masks_and_item_read_are_required(self):
        self.denied_fields.add(("Jifeng Flow Order", "read", "planned_qty"))
        with self.assertRaises(PermissionError):
            self.flow.get_stock_plan_context()
        self.denied_fields.clear()
        self.item.denied = {"read"}
        with self.assertRaises(PermissionError):
            self.flow.get_stock_plan_context()
        self.assertNotIn("save", self.events)


if __name__ == "__main__":
    unittest.main()
