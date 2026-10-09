"""Exact Plan child losses/costs without counting child stock twice as FG."""

from datetime import datetime, timedelta
from decimal import Decimal
import unittest

import test_production_loss_costs as loss_fixture


class SubassemblyFlowCostTests(unittest.TestCase):
    def setUp(self):
        self.h = loss_fixture.TestProductionLossSources(methodName="runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.base, self.module = self.h.base, self.h.module
        self.loss, self.work, self.plan, self.parent = self.h.seed_plan_source()
        self.plan.combine_sub_items = 0
        self.base.documents["BOM", "FG-BOM"].quantity = 1
        self.h.child("BOM", "FG-BOM", "items", "FG-SUB", item_code="SUB", bom_no="SUB-BOM",
                     stock_uom="Kg", stock_qty=2)
        self.base.add("BOM", "SUB-BOM", company="JF", item="SUB", uom="Kg", quantity=2, is_active=1)
        self.h.child("BOM", "SUB-BOM", "items", "SUB-RAW", item_code="RM", stock_uom="Kg", stock_qty=4)
        self.base.add("Item", "SUB", stock_uom="Kg", is_stock_item=1, has_serial_no=0, has_batch_no=0)
        self.base.add("Warehouse", "SUB-FG", company="JF", is_group=0, disabled=0)
        self.sub = self.h.child("Production Plan", "PLAN", "sub_assembly_items", "SUB-ROW",
            production_item="SUB", parent_item_code="FG", production_plan_item="PLAN-ROW", bom_no="SUB-BOM",
            bom_level=0, type_of_manufacturing="In House", qty=10, uom="Kg", stock_uom="Kg",
            sales_order="SO", sales_order_item="SO-ROW", fg_warehouse="SUB-FG")
        self.work.update(production_plan_item=None, production_plan_sub_assembly_item="SUB-ROW", jf_flow_order=None,
            production_item="SUB", stock_uom="Kg", bom_no="SUB-BOM", qty=10, fg_warehouse="SUB-FG")
        self.base.documents["Work Order Item", "WO-RAW"].required_qty = 20
        self.base.documents["Job Card", "CARD"].update(production_item="SUB", stock_uom="Kg",
            for_quantity=10, process_loss_qty=10)
        self.base.documents["Jifeng Material Line", "MAT"].item_code = "SUB"
        self.loss.jf_production_loss_qty = 10
        self.base.documents["Stock Entry Detail", "LOSS-RM"].update(qty=20, transfer_qty=20)
        self.base.documents[self.module.SLE, "LOSS-SLE"].update(actual_qty=-20, stock_value_difference=-80)
        self.loss.jf_production_loss_source_signature = self.h.loss.source_signature(
            self.h.get_doc("Job Card", "CARD"), self.h.get_doc(self.module.WO, "WO"))

    def child_make(self, name="SUB-MAKE", qty=10, material=80, hour=11):
        work_name = name + "-WO"
        doc = self.base.manufacture(name, work=work_name, qty=qty, raw_qty=qty * 2, material=material)
        work = self.base.documents[self.module.WO, work_name]
        work.update(company="JF", jf_flow_order=None, production_plan="PLAN", production_plan_item=None,
            production_plan_sub_assembly_item="SUB-ROW", sales_order="SO", sales_order_item="SO-ROW",
            production_item="SUB", stock_uom="Kg", bom_no="SUB-BOM", qty=qty,
            wip_warehouse="RM-WH", fg_warehouse="SUB-FG", process_loss_qty=0, produced_qty=qty)
        self.h.child(self.module.WO, work_name, "required_items", work_name + "-RAW",
                     item_code="RM", stock_uom="Kg", required_qty=qty * 2)
        self.h.child(self.module.WO, work_name, "operations", work_name + "-OP", operation="CAST")
        doc.bom_no = "SUB-BOM"
        row = self.base.documents["Stock Entry Detail", name + "-FG"]
        row.update(item_code="SUB", stock_uom="Kg", uom="Kg", t_warehouse="SUB-FG")
        self.base.documents[self.module.SLE, name + "-FG-SLE"].update(
            item_code="SUB", stock_uom="Kg", warehouse="SUB-FG")
        self.at_hour(doc, hour)
        return doc

    def parent_make(self, name="FG-MAKE", qty=5, material=80, hour=12):
        work_name = name + "-WO"
        doc = self.base.manufacture(name, work=work_name, qty=qty, raw_qty=qty * 2, material=material)
        work = self.base.documents[self.module.WO, work_name]
        work.update(production_plan="PLAN", production_plan_item="PLAN-ROW", qty=qty,
            production_plan_sub_assembly_item=None, sales_order="SO", sales_order_item="SO-ROW",
            bom_no="FG-BOM", stock_uom="Nos")
        doc.bom_no = "FG-BOM"
        row = self.base.documents["Stock Entry Detail", name + "-RM"]
        row.update(item_code="SUB", s_warehouse="SUB-FG")
        self.base.documents[self.module.SLE, name + "-RM-SLE"].update(item_code="SUB", warehouse="SUB-FG")
        self.at_hour(doc, hour)
        return doc

    def at_hour(self, doc, hour):
        doc.posting_time = timedelta(hours=hour)
        for (kind, _), row in self.base.documents.items():
            if kind == self.module.SLE and row.get("voucher_no") == doc.name:
                row.posting_time = doc.posting_time
                row.creation = datetime(2026, 10, 3, hour)

    def test_child_loss_replacement_parent_manufacture_and_inverse_count_cost_once(self):
        before = self.module.posted_flow_costs("FLOW")
        self.assertEqual((before["totals"]["material"], before["loss_batches"]), (80, []))
        self.assertEqual(self.module.production_batch_candidates("FLOW"), [])
        child = self.child_make()
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 160)
        self.assertEqual(self.module.manufacture_batch_candidates("FLOW"), [])
        parent = self.parent_make()
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 160)
        self.assertEqual({row["manufacture"]: row["material"] for row in result["manufactures"]},
                         {"SUB-MAKE": 80, "FG-MAKE": 0})
        self.assertEqual([(row["item_code"], row["qty"], row["lost_qty"]) for row in result["manufacture_batches"]],
                         [("FG", Decimal(5), Decimal(0))])
        parent.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 160)
        child.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 80)
        self.loss.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 0)
        self.h.assert_no_writes()

    def test_parent_consumption_before_child_output_or_over_value_rejected(self):
        self.child_make(hour=13)
        parent = self.parent_make(hour=12)
        with self.assertRaisesRegex(ValueError, "之前"):
            self.module.posted_flow_costs("FLOW")
        self.at_hour(parent, 14)
        self.base.documents[self.module.SLE, "FG-MAKE-RM-SLE"].stock_value_difference = -81
        self.base.documents[self.module.SLE, "FG-MAKE-FG-SLE"].stock_value_difference = 81
        with self.assertRaisesRegex(ValueError, "超过|不一致"):
            self.module.posted_flow_costs("FLOW")
        self.h.assert_no_writes()

    def test_partial_child_consumption_with_different_values_has_no_invented_average(self):
        self.child_make("FIRST", qty=5, material=40)
        self.child_make("SECOND", qty=5, material=60)
        self.parent_make(qty=2, material=40)
        with self.assertRaisesRegex(ValueError, "估值不同"):
            self.module.posted_flow_costs("FLOW")
        self.h.assert_no_writes()

    def test_hidden_child_or_bom_field_and_late_plan_identity_refuse_partial_cost(self):
        self.child_make()
        for kind, field in (("Production Plan Sub Assembly Item", "production_plan_item"), ("BOM Item", "stock_qty")):
            self.base.hidden[kind] = {field}
            with self.subTest(kind=kind), self.assertRaises(PermissionError):
                self.module.posted_flow_costs("FLOW")
            self.base.hidden.clear()
        session = self.module._Session("FLOW", include_stages=True)
        session.load_issues(); session.load_manufactures()
        self.sub.production_plan_item = "OTHER"
        with self.assertRaises(PermissionError): session.finish()
        self.h.assert_no_writes()

    def test_cancelled_child_supply_cannot_leave_parent_cost_readable(self):
        child = self.child_make()
        self.parent_make()
        child.docstatus = 2
        with self.assertRaisesRegex(ValueError, "缺少.*产出"):
            self.module.posted_flow_costs("FLOW")
        self.h.assert_no_writes()

    def test_parent_full_loss_consumes_child_value_once_with_fg_only_loss_quantity(self):
        self.child_make()
        doc, row = self.h.seed_loss("PARENT-LOSS", card="PARENT-CARD", work="PARENT-WO", qty=5)
        work = self.base.documents[self.module.WO, "PARENT-WO"]
        work.update(production_plan="PLAN", production_plan_item="PLAN-ROW", stock_uom="Nos",
                    sales_order="SO", sales_order_item="SO-ROW", wip_warehouse="SUB-FG")
        required = self.base.documents["Work Order Item", "PARENT-WO-RAW"]
        required.update(item_code="SUB", required_qty=10, stock_uom="Kg")
        self.base.documents["Job Card", "PARENT-CARD"].wip_warehouse = "SUB-FG"
        row.update(item_code="SUB", s_warehouse="SUB-FG")
        doc.from_warehouse = "SUB-FG"
        self.base.documents[self.module.SLE, "PARENT-LOSS-SLE"].update(item_code="SUB",
            warehouse="SUB-FG", stock_value_difference=-80)
        self.at_hour(doc, 12)
        doc.jf_production_loss_source_signature = self.h.loss.source_signature(
            self.h.get_doc("Job Card", "PARENT-CARD"), self.h.get_doc(self.module.WO, "PARENT-WO"))
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 160)
        self.assertEqual([(row["item_code"], row["lost_qty"]) for row in result["loss_batches"]], [("FG", Decimal(5))])
        self.assertEqual({row["issue"]: row["material"] for row in result["issues"]},
                         {"LOSS": Decimal(80), "PARENT-LOSS": Decimal(0)})
        self.h.assert_no_writes()

    def test_native_child_manufacture_partial_loss_does_not_require_special_loss_entry(self):
        child = self.child_make()
        child.process_loss_qty = 2
        output = self.base.documents["Stock Entry Detail", "SUB-MAKE-FG"]
        output.update(qty=8, transfer_qty=8)
        self.base.documents[self.module.SLE, "SUB-MAKE-FG-SLE"].actual_qty = 8
        self.parent_make(qty=4, material=80)
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 160)
        self.assertEqual(result["manufacture_batches"][0]["qty"], 4)
        self.assertEqual(result["loss_batches"], [])
        self.h.assert_no_writes()


if __name__ == "__main__":
    unittest.main()
