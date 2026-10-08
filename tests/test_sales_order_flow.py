"""Exercise the manual Sales Order handoff without an installed Frappe site."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch


class Record(dict):
    def __init__(self, **values):
        super().__init__(values)
        self.flags = types.SimpleNamespace(from_sales_order=False)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        if name == "flags":
            object.__setattr__(self, name, value)
        else:
            self[name] = value

    @property
    def items(self):
        return self["items"]

    def check_permission(self, permission):
        return None

    def reload(self):
        return self

    def precision(self, field):
        return 6

    def insert(self):
        prefix = "MR" if self.doctype == "Material Request" else "JF-FLOW"
        self.name = f"{prefix}-{len(self.store) + 1}"
        self.store[self.name] = self
        return self

    def save(self, **kwargs):
        return self

    def set(self, field, value):
        self[field] = value

    def get_items_and_operations_from_bom(self):
        self.bom_loaded = True


class FakeDatabase:
    def __init__(self, flows, requests, profiles):
        self.flows = flows
        self.requests = requests
        self.profiles = profiles

    def get_value(self, doctype, lookup, field, **kwargs):
        if doctype == "Sales Order":
            return lookup
        if doctype == "Jifeng Product Profile":
            return next((name for name, profile in self.profiles.items()
                         if profile.item_code == lookup["item_code"]), None)
        if doctype == "Jifeng Flow Order":
            if isinstance(lookup, str):
                if field == "material_request":
                    return self.flows[lookup].get("material_request")
                return lookup
            return next((name for name, flow in self.flows.items()
                         if flow.source_key == lookup["source_key"]), None)
        raise AssertionError(doctype)

    def exists(self, doctype, name):
        if doctype == "Material Request":
            return name in self.requests
        raise AssertionError(doctype)


class TestSalesOrderFlow(unittest.TestCase):
    def setUp(self):
        self.flows = {}
        self.work_orders = {}
        self.requests = {}
        self.profiles = {}
        self.net_issues = {}
        self.bom = Record(name="BOM-1", item="RING", company="JF", docstatus=1, is_active=1)
        self.order = Record(
            doctype="Sales Order", name="SO-0001", docstatus=1,
            status="To Deliver and Bill", company="JF",
            items=[Record(name="ROW-1", parent="SO-0001", parenttype="Sales Order", parentfield="items",
                          item_code="RING", qty=10, uom="Nos", stock_uom="Nos", conversion_factor=1,
                          stock_qty=10, jf_make_flow=1,
                          jf_requirements="刻字", jf_packaging="礼盒", jf_requirements_attachment=None),
                   Record(name="ROW-2", parent="SO-0001", parenttype="Sales Order", parentfield="items",
                          item_code="SERVICE", qty=1, uom="Nos", stock_uom="Nos", conversion_factor=1,
                          stock_qty=1, jf_make_flow=0)],
        )
        frappe = types.ModuleType("frappe")
        frappe.whitelist = lambda: lambda function: function
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.PermissionError = PermissionError
        frappe.session = types.SimpleNamespace(user="Synthetic")
        readable = {"name", "company", "docstatus", "status", "item_code", "planned_qty", "stock_uom",
                    "quantity_basis", "qty", "uom", "conversion_factor", "stock_qty", "sales_order", "external_order",
                    "source_qty", "source_uom", "source_stock_uom", "source_conversion_factor", "source_stock_qty",
                    "source_stock_precision", "stock_plan_confirmation_note", "stock_plan_confirmed_at", "stock_plan_confirmed_by"}
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda *args, **kwargs: readable
        frappe.get_meta = lambda *args: types.SimpleNamespace(fields=[], has_field=lambda field: False,
            get_permlevel_access=lambda **kwargs: [0],
            get_field=lambda field: Record(fieldname=field, fieldtype="Table" if field == "items" else "Data",
                                          options="Sales Order Item" if field == "items" else None, permlevel=0, mask=0))
        frappe.db = FakeDatabase(self.flows, self.requests, self.profiles)
        frappe.get_all = lambda doctype, **kwargs: list(self.work_orders if doctype == "Work Order" else self.flows)

        def get_doc(doctype, name=None, **kwargs):
            if isinstance(doctype, dict):
                flow = Record(**doctype)
                if doctype["doctype"] == "Material Request":
                    flow.docstatus = 0
                stores = {"Work Order": self.work_orders, "Material Request": self.requests,
                          "Jifeng Flow Order": self.flows}
                flow.store = stores[doctype["doctype"]]
                return flow
            if doctype == "Sales Order":
                return self.order
            if doctype == "Jifeng Flow Order":
                return self.flows[name]
            if doctype == "Work Order":
                return self.work_orders[name]
            if doctype == "Material Request":
                return self.requests[name]
            if doctype == "BOM":
                return self.bom
            if doctype == "Item":
                return Record(name=name, stock_uom="Nos")
            if doctype == "Jifeng Product Profile":
                return self.profiles[name]
            raise AssertionError(doctype)

        frappe.get_doc = get_doc
        utils = types.ModuleType("frappe.utils")
        utils.add_days = lambda date, days: date
        utils.cint = lambda value: int(value or 0)
        utils.today = lambda: "2026-09-30"
        material_workbench = types.ModuleType("jifeng_erp.order_material_workbench")
        material_issue = types.ModuleType("jifeng_erp.material_issue")
        def assert_no_net_issue(flow):
            if any(value > 0 for value in self.net_issues.values()):
                raise ValueError("本流程仍有净领料。仅将确认尚未耗用的实物按原领料单退回后，再创建工单或转料制造")
        material_issue.assert_no_net_issue_for_manufacturing = assert_no_net_issue
        native_patch = patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils, "frappe.model": model,
                                                "jifeng_erp.order_material_workbench": material_workbench,
                                                "jifeng_erp.material_issue": material_issue})
        native_patch.start()
        self.addCleanup(native_patch.stop)
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils}):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "workflow.py"
            spec = importlib.util.spec_from_file_location("sales_workflow_under_test", path)
            self.workflow = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.workflow)

    def test_selected_lines_create_once_and_keep_source_snapshot(self):
        names = self.workflow.create_sales_order_flows(self.order.name)
        self.assertEqual(self.workflow.create_sales_order_flows(self.order.name), names)
        self.assertEqual(len(self.flows), 1)
        flow = self.flows[names[0]]
        self.assertEqual((flow.sales_order, flow.sales_order_item), ("SO-0001", "ROW-1"))
        self.assertEqual((flow.requirements, flow.packaging), ("刻字", "礼盒"))
        self.assertEqual((flow.source_qty, flow.planned_qty), (10, 10))

    def test_ordinary_sales_flow_keeps_native_profile_default_and_manual_plan_on_reuse(self):
        self.profiles["RING"] = Record(name="RING", item_code="RING", company="JF",
            bom="PROFILE-BOM", supplier_product_code="CODE", specification="SPEC",
            requirements="档案要求", packaging="档案包装",
            materials=[Record(item_code="RAW", qty_per_unit=2, unit_price=3)],
            operations=[Record(operation="POLISH", unit_price=4)])
        names = self.workflow.create_sales_order_flows(self.order.name)
        flow = self.flows[names[0]]
        self.assertEqual((flow.profile, flow.bom), ("RING", "PROFILE-BOM"))
        self.assertEqual(flow.materials[0]["qty_per_unit"], 2)
        self.assertEqual(flow.requirements, "刻字")
        flow.profile, flow.bom, flow.planned_qty = None, "MANUAL-BOM", 7
        flow.materials = [Record(item_code="RAW", qty_per_unit=9)]
        self.assertEqual(self.workflow.create_sales_order_flows(self.order.name), names)
        self.assertEqual((flow.profile, flow.bom, flow.planned_qty), (None, "MANUAL-BOM", 7))
        self.assertEqual(flow.materials[0].qty_per_unit, 9)

    def test_source_changes_flag_review_without_overwriting_plan(self):
        name = self.workflow.create_sales_order_flows(self.order.name)[0]
        flow = self.flows[name]
        self.order.items[0].qty = 12
        self.order.items[0].stock_qty = 12
        self.order.items[0].jf_requirements = "改刻字"
        self.workflow.refresh_sales_order_flows(self.order)
        self.assertEqual((flow.source_qty, flow.planned_qty), (12, 10))
        self.assertEqual((flow.source_requirements, flow.requirements), ("改刻字", "刻字"))
        self.assertEqual(flow.source_changed, 1)
        flow.source_changed = 0
        self.workflow.refresh_sales_order_flows(self.order)
        self.assertEqual(flow.source_changed, 0)
        self.order.docstatus = 2
        self.workflow.refresh_sales_order_flows(self.order)
        self.assertEqual((flow.source_status, flow.source_changed), ("Cancelled", 1))

    def test_same_product_on_two_lines_keeps_separate_flows(self):
        self.order.items[1].item_code = "RING"
        self.order.items[1].jf_make_flow = 1
        names = self.workflow.create_sales_order_flows(self.order.name)
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)
        self.assertEqual({self.flows[name].sales_order_item for name in names}, {"ROW-1", "ROW-2"})

    def test_sales_boxes_generate_stock_unit_plan_and_work_order(self):
        row = self.order.items[0]
        row.qty, row.uom, row.conversion_factor, row.stock_qty = 1, "Box", 12, 12
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        self.assertEqual((flow.source_qty, flow.source_uom, flow.planned_qty, flow.stock_uom),
                         (Decimal(1), "Box", Decimal(12), "Nos"))
        flow.bom, flow.work_order, flow.source_changed = "BOM-1", None, 0
        work = self.work_orders[self.workflow.make_work_order(flow)]
        self.assertEqual(work.qty, 12)

    def test_source_unit_or_factor_change_flags_review_with_same_sales_qty(self):
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        row = self.order.items[0]
        row.uom, row.conversion_factor, row.stock_qty = "Box", 12, 120
        self.workflow.refresh_sales_order_flows(self.order)
        self.assertEqual((flow.source_changed, flow.planned_qty, flow.source_stock_qty), (1, 10, 120))
        self.assertEqual((flow.source_uom, flow.source_conversion_factor), ("Box", 12))

    def test_missing_legacy_plan_unit_does_not_create_work_order(self):
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        flow.bom, flow.source_changed = "BOM-1", 0
        flow.pop("quantity_basis")
        with self.assertRaisesRegex(ValueError, "库存单位"):
            self.workflow.make_work_order(flow)
        self.assertFalse(self.work_orders)

    def test_sales_boxes_material_request_uses_inventory_plan_quantity(self):
        row = self.order.items[0]
        row.qty, row.uom, row.conversion_factor, row.stock_qty = 1, "Box", 12, 12
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        flow.materials = [Record(item_code="SILVER", uom="g", qty_per_unit=2)]
        flow.target_warehouse, flow.source_changed = "Stores - JF", 0
        request = self.requests[self.workflow.make_material_request(flow)]
        self.assertEqual(request.items[0]["qty"], 24)

    def test_draft_cannot_create_production_flow(self):
        self.order.docstatus = 0
        with self.assertRaisesRegex(ValueError, "已提交"):
            self.workflow.create_sales_order_flows(self.order.name)
        self.assertFalse(self.flows)

    def test_work_order_draft_is_reused_for_same_flow(self):
        name = self.workflow.create_sales_order_flows(self.order.name)[0]
        flow = self.flows[name]
        flow.bom = "BOM-1"
        flow.work_order = None
        flow.source_changed = 0
        work_order_name = self.workflow.make_work_order(flow)
        self.assertEqual(self.workflow.make_work_order(flow), work_order_name)
        self.assertEqual(len(self.work_orders), 1)
        work_order = self.work_orders[work_order_name]
        self.assertEqual((work_order.jf_flow_order, work_order.sales_order, work_order.sales_order_item),
                         (name, "SO-0001", "ROW-1"))
        self.assertEqual((work_order.bom_no, work_order.production_item, work_order.qty), ("BOM-1", "RING", 10))
        self.assertTrue(work_order.bom_loaded)

    def test_net_expense_issue_must_be_returned_before_creating_native_work_order(self):
        name = self.workflow.create_sales_order_flows(self.order.name)[0]
        flow = self.flows[name]
        flow.bom, flow.source_changed = "BOM-1", 0
        self.net_issues["RAW"] = Decimal("1")
        with self.assertRaisesRegex(ValueError, "净领料.*确认尚未耗用的实物"):
            self.workflow.make_work_order(flow)
        self.assertFalse(self.work_orders)

    def test_wrong_company_bom_cannot_create_work_order(self):
        name = self.workflow.create_sales_order_flows(self.order.name)[0]
        flow = self.flows[name]
        flow.bom = "BOM-1"
        flow.source_changed = 0
        self.bom.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "生产 BOM"):
            self.workflow.make_work_order(flow)
        self.assertFalse(self.work_orders)

    def test_changed_source_cannot_create_work_order(self):
        name = self.workflow.create_sales_order_flows(self.order.name)[0]
        flow = self.flows[name]
        flow.bom = "BOM-1"
        flow.source_changed = 1
        with self.assertRaisesRegex(ValueError, "核对源订单"):
            self.workflow.make_work_order(flow)
        self.assertFalse(self.work_orders)

    def test_cancelled_material_request_can_be_recreated(self):
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        flow.materials = [Record(item_code="SILVER", uom="g", qty_per_unit=2)]
        flow.target_warehouse = "Stores - JF"
        flow.source_changed = 0
        first = self.workflow.make_material_request(flow)
        self.assertEqual(self.workflow.make_material_request(flow), first)
        self.requests[first].docstatus = 2
        second = self.workflow.make_material_request(flow)
        self.assertNotEqual(second, first)
        self.assertEqual(flow.material_request, second)

    def test_mismatched_existing_request_is_not_reused(self):
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        flow.materials = [Record(item_code="SILVER", uom="g", qty_per_unit=2)]
        flow.target_warehouse = "Stores - JF"
        flow.source_changed = 0
        first = self.workflow.make_material_request(flow)
        self.requests[first].items[0]["jf_flow_order"] = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "未全部关联"):
            self.workflow.make_material_request(flow)


if __name__ == "__main__":
    unittest.main()
