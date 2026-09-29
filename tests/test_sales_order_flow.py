"""Exercise the manual Sales Order handoff without an installed Frappe site."""

import importlib.util
import pathlib
import sys
import types
import unittest
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

    def insert(self):
        self.name = f"JF-FLOW-{len(self.store) + 1}"
        self.store[self.name] = self
        return self

    def save(self, **kwargs):
        return self


class FakeDatabase:
    def __init__(self, flows):
        self.flows = flows

    def get_value(self, doctype, lookup, field, **kwargs):
        if doctype == "Sales Order":
            return lookup
        if doctype == "Jifeng Product Profile":
            return None
        if doctype == "Jifeng Flow Order":
            return next((name for name, flow in self.flows.items()
                         if flow.source_key == lookup["source_key"]), None)
        raise AssertionError(doctype)


class TestSalesOrderFlow(unittest.TestCase):
    def setUp(self):
        self.flows = {}
        self.order = Record(
            doctype="Sales Order", name="SO-0001", docstatus=1,
            status="To Deliver and Bill", company="JF",
            items=[Record(name="ROW-1", item_code="RING", qty=10, jf_make_flow=1,
                          jf_requirements="刻字", jf_packaging="礼盒", jf_requirements_attachment=None),
                   Record(name="ROW-2", item_code="SERVICE", qty=1, jf_make_flow=0)],
        )
        frappe = types.ModuleType("frappe")
        frappe.whitelist = lambda: lambda function: function
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe.db = FakeDatabase(self.flows)
        frappe.get_all = lambda doctype, **kwargs: list(self.flows)

        def get_doc(doctype, name=None):
            if isinstance(doctype, dict):
                flow = Record(**doctype)
                flow.store = self.flows
                return flow
            if doctype == "Sales Order":
                return self.order
            if doctype == "Jifeng Flow Order":
                return self.flows[name]
            raise AssertionError(doctype)

        frappe.get_doc = get_doc
        utils = types.ModuleType("frappe.utils")
        utils.add_days = lambda date, days: date
        utils.cint = lambda value: int(value or 0)
        utils.today = lambda: "2026-09-30"
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

    def test_source_changes_flag_review_without_overwriting_plan(self):
        name = self.workflow.create_sales_order_flows(self.order.name)[0]
        flow = self.flows[name]
        self.order.items[0].qty = 12
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

    def test_draft_cannot_create_production_flow(self):
        self.order.docstatus = 0
        with self.assertRaisesRegex(ValueError, "已提交"):
            self.workflow.create_sales_order_flows(self.order.name)
        self.assertFalse(self.flows)


if __name__ == "__main__":
    unittest.main()
