"""Exercise the manual Sales Order handoff without an installed Frappe site."""

import importlib.util
import json
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
        if permission in self.get("denied", ()):
            raise PermissionError("native document denied")

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
        if doctype in ("Sales Order", "Jifeng External Order"):
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

    def sql(self, query, values):
        if "FOR UPDATE NOWAIT" in query:
            if getattr(self, "busy", False):
                raise TimeoutError("source order busy")
            return [(values[0],)]
        raise AssertionError(query)


class TestSalesOrderFlow(unittest.TestCase):
    def setUp(self):
        self.denied_fields = set()
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
        frappe.QueryTimeoutError = TimeoutError
        frappe.QueryDeadlockError = RuntimeError
        self.frappe = frappe
        frappe.session = types.SimpleNamespace(user="Synthetic")
        readable = {"name", "company", "docstatus", "status", "item_code", "planned_qty", "stock_uom",
                    "quantity_basis", "qty", "uom", "conversion_factor", "stock_qty", "sales_order", "external_order",
                    "source_qty", "source_uom", "source_stock_uom", "source_conversion_factor", "source_stock_qty",
                    "source_stock_precision", "stock_plan_confirmation_note", "stock_plan_confirmed_at", "stock_plan_confirmed_by"}
        model = types.ModuleType("frappe.model")
        self.schemas = {}
        for slug in ("jifeng_flow_order", "jifeng_product_profile", "jifeng_material_line", "jifeng_operation_line",
                     "jifeng_external_order", "jifeng_external_order_item"):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp/jifeng_erp/doctype" / slug / (slug + ".json")
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.schemas[schema["name"]] = {field["fieldname"]: Record(permlevel=0, mask=0, **field)
                                          for field in schema["fields"]}

        def allowed(doctype, permission_type="read", parenttype=None, **kwargs):
            columns = set(self.schemas.get(doctype, {})) | readable | {"parent", "parenttype", "parentfield"}
            return columns - {field for dt, action, field in self.denied_fields if dt == doctype and action == permission_type}

        model.get_permitted_fields = allowed

        def meta(doctype):
            fields = self.schemas.get(doctype, {})
            return types.SimpleNamespace(fields=list(fields.values()), has_field=lambda field: field in fields,
                get_permlevel_access=lambda permission_type="read", **kwargs: [] if permission_type == "mask" else [0],
                get_field=lambda field: fields.get(field) or Record(fieldname=field,
                    fieldtype="Table" if field == "items" else "Data",
                    options="Sales Order Item" if field == "items" else None, permlevel=0, mask=0))

        frappe.get_meta = meta
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
            if doctype == "Jifeng External Order":
                return self.external
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
            doctype="Jifeng Product Profile",
            bom="PROFILE-BOM", supplier_product_code="CODE", specification="SPEC",
            requirements="档案要求", packaging="档案包装",
            materials=[Record(doctype="Jifeng Material Line", name="RAW-ROW", parent="RING",
                              parenttype="Jifeng Product Profile", parentfield="materials",
                              item_code="RAW", qty_per_unit=2, unit_price=3)],
            operations=[Record(doctype="Jifeng Operation Line", name="OP-ROW", parent="RING",
                               parenttype="Jifeng Product Profile", parentfield="operations",
                               operation="POLISH", unit_price=4)])
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

    def external_order(self):
        self.external = Record(doctype="Jifeng External Order", name="EXT", company="JF", online_order_id="WEB-1",
            source_status="WaitConfirm", requirements="按样板", packaging="独立袋", requirements_attachment=None,
            items=[Record(doctype="Jifeng External Order Item", name="EXT-ROW", parent="EXT",
                          parenttype="Jifeng External Order", parentfield="items", external_line_id="LINE-1",
                          item_code="RING", qty=10, source_status="WaitConfirm")])
        return self.external

    def test_external_reuse_keeps_plan_recipe_and_notes_but_checks_exact_company_and_source(self):
        order = self.external_order()
        names = self.workflow.create_flows(order)
        flow = self.flows[names[0]]
        flow.planned_qty, flow.requirements, flow.materials = 7, "本单另定", [Record(item_code="RAW", qty_per_unit=9)]
        self.assertEqual(self.workflow.create_flows(order), names)
        self.assertEqual((flow.planned_qty, flow.requirements, flow.materials[0].qty_per_unit), (7, "本单另定", 9))
        for field, value in (("company", "OTHER"), ("external_order", "WRONG"), ("external_line_id", "WRONG"),
                             ("sales_order", "SO")):
            old = flow.get(field)
            flow[field] = value
            order.items[0].flow_order = None
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "来源行冲突"):
                self.workflow.create_flows(order)
            self.assertIsNone(order.items[0].flow_order)
            flow[field] = old
        flow.denied = {"read"}
        with self.assertRaises(PermissionError):
            self.workflow.create_flows(order)
        self.assertIsNone(order.items[0].flow_order)

    def test_external_current_source_is_checked_before_downstream_even_when_cached_status_is_active(self):
        order = self.external_order()
        flow = self.flows[self.workflow.create_flows(order)[0]]
        flow.source_changed, flow.sales_order = 0, None
        self.workflow.validate_flow_source(flow)
        for document, field, value in ((order, "company", "OTHER"), (order, "source_status", "Cancelled"),
                                       (order.items[0], "source_status", "Cancelled"), (order.items[0], "qty", 11),
                                       (order.items[0], "item_code", "WRONG"), (order.items[0], "external_line_id", "REMOVED")):
            old = document[field]
            document[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "流程单来源"):
                self.workflow.validate_flow_source(flow)
            document[field] = old
        self.denied_fields.add(("Jifeng External Order Item", "read", "qty"))
        with self.assertRaises(PermissionError):
            self.workflow.validate_flow_source(flow)

    def test_busy_source_order_stops_before_creating_downstream(self):
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        flow.bom, flow.source_changed = "BOM-RING", 0
        self.frappe.db.busy = True
        with self.assertRaisesRegex(ValueError, "正在修改"):
            self.workflow.make_work_order(flow)
        self.assertFalse(self.work_orders)

    def test_external_duplicate_or_wrong_child_identity_never_creates_flow(self):
        order = self.external_order()
        for field, value in (("parent", "OTHER"), ("parenttype", "Other"), ("parentfield", "other"),
                             ("doctype", "Other"), ("name", None)):
            old = order.items[0][field]
            order.items[0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "准确的来源"):
                self.workflow.create_flows(order)
            self.assertFalse(self.flows)
            order.items[0][field] = old
        order.items.append(Record(**dict(order.items[0], name="ANOTHER-ROW")))
        with self.assertRaisesRegex(ValueError, "唯一"):
            self.workflow.create_flows(order)
        self.assertFalse(self.flows)

    def test_profile_recipe_masks_writes_and_foreign_rows_never_copy_partial_or_zero_costs(self):
        profile = Record(doctype="Jifeng Product Profile", name="P", company="JF", item_code="RING",
            materials=[Record(doctype="Jifeng Material Line", name="M", parent="P", parenttype="Jifeng Product Profile",
                              parentfield="materials", item_code="RAW", qty_per_unit=2, unit_price=3)],
            operations=[Record(doctype="Jifeng Operation Line", name="O", parent="P", parenttype="Jifeng Product Profile",
                               parentfield="operations", operation="POLISH", unit_price=4)])
        target = Record(doctype="Jifeng Flow Order", materials=[{"old": 1}], operations=[{"old": 2}])
        self.schemas["Jifeng Material Line"]["unit_price"].mask = 1
        with self.assertRaises(PermissionError):
            self.workflow.copy_recipe(target, profile)
        self.schemas["Jifeng Material Line"]["unit_price"].mask = 0
        self.denied_fields.add(("Jifeng Operation Line", "write", "unit_price"))
        with self.assertRaises(PermissionError):
            self.workflow.copy_recipe(target, profile)
        self.denied_fields.clear()
        self.schemas["Jifeng Product Profile"]["materials"].permlevel = 1
        with self.assertRaises(PermissionError):
            self.workflow.copy_recipe(target, profile)
        self.schemas["Jifeng Product Profile"]["materials"].permlevel = 0
        profile.operations[0].parent = "OTHER"
        with self.assertRaisesRegex(ValueError, "准确来源"):
            self.workflow.copy_recipe(target, profile)
        self.assertEqual((target.materials, target.operations), ([{"old": 1}], [{"old": 2}]))
        profile.operations[0].parent = "P"
        self.workflow.copy_recipe(target, profile)
        self.assertEqual((target.materials[0]["qty_per_unit"], target.materials[0]["unit_price"],
                          target.operations[0]["unit_price"]), (2, 3, 4))

    def test_sales_reuse_rejects_foreign_company(self):
        flow = self.flows[self.workflow.create_sales_order_flows(self.order.name)[0]]
        flow.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "来源标识冲突"):
            self.workflow.create_sales_order_flows(self.order.name)

    def test_new_flow_image_requires_item_field_access_and_exact_authorized_attachment(self):
        item = Record(name="RING", image="/files/ring.jpg")
        self.schemas["Item"] = {"image": Record(fieldname="image", fieldtype="Attach Image", permlevel=0, mask=0)}
        archive = types.ModuleType("jifeng_erp.product_archive")
        archive._business_image_state = lambda source: {"state": "已上传", "url": "/different-download-url"}
        with patch.dict(sys.modules, {"jifeng_erp.product_archive": archive}), patch.object(self.frappe, "get_doc", return_value=item):
            self.assertEqual(self.workflow.item_image_snapshot("RING"), item.image)
            archive._business_image_state = lambda source: {"state": "附件不可用", "url": ""}
            self.assertIsNone(self.workflow.item_image_snapshot("RING"))
            self.schemas["Item"]["image"].mask = 1
            archive._business_image_state = lambda source: self.fail("masked image must not be read as an attachment")
            self.assertIsNone(self.workflow.item_image_snapshot("RING"))
            item.denied = {"read"}
            with self.assertRaises(PermissionError):
                self.workflow.item_image_snapshot("RING")

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
