"""Exact plan-child Flow propagation through the shared real source reader."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import test_production_loss as loss_fixture


class SubassemblyStockFlowTest(unittest.TestCase):
    def setUp(self):
        self.h = loss_fixture.ProductionLossTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.Doc = D = loss_fixture.Doc
        self.work, self.flow, self.card = self.h.work, self.h.flow, self.h.card
        self.work.update(production_plan="PLAN", production_plan_item=None,
            production_plan_sub_assembly_item="CHILD-ROW", jf_flow_order=None,
            production_item="CHILD", bom_no="CHILD-BOM", qty=10, fg_warehouse="CHILD-FG",
            required_items=[D(name="CHILD-RM", item_code="CHILD-MATERIAL", required_qty=30, stock_uom="Kg")])
        self.card.update(production_item="CHILD", for_quantity=10, process_loss_qty=10)
        self.flow.materials = [D(item_code="PARENT-MATERIAL")]
        self.parent = D(name="PARENT-ROW", parent="PLAN", parenttype="Production Plan", parentfield="po_items",
            item_code="FG", jf_flow_order="FLOW", bom_no="BOM", planned_qty=5, stock_uom="Nos")
        self.child = D(name="CHILD-ROW", parent="PLAN", parenttype="Production Plan", parentfield="sub_assembly_items",
            production_item="CHILD", parent_item_code="FG", production_plan_item="PARENT-ROW",
            bom_no="CHILD-BOM", bom_level=0, type_of_manufacturing="In House", qty=10,
            stock_uom="Nos", uom="Nos", fg_warehouse="CHILD-FG")
        self.plan = D(doctype="Production Plan", name="PLAN", company="JF", docstatus=1, status="Submitted",
            combine_sub_items=0, po_items=[self.parent], sub_assembly_items=[self.child])
        self.h.store.update({
            ("Production Plan", "PLAN"): self.plan,
            ("Production Plan Sub Assembly Item", "CHILD-ROW"): self.child,
            ("BOM", "BOM"): D(name="BOM", docstatus=1, is_active=1, company="JF", item="FG", uom="Nos", quantity=1,
                items=[D(item_code="CHILD", bom_no="CHILD-BOM", stock_uom="Nos", stock_qty=2)]),
            ("BOM", "CHILD-BOM"): D(name="CHILD-BOM", docstatus=1, is_active=1, company="JF", item="CHILD",
                uom="Nos", quantity=1, items=[D(item_code="CHILD-MATERIAL", stock_uom="Kg", stock_qty=3)]),
            ("Item", "CHILD"): D(name="CHILD", is_stock_item=1, stock_uom="Nos"),
            ("Item", "CHILD-MATERIAL"): D(name="CHILD-MATERIAL", is_stock_item=1, stock_uom="Kg"),
            ("Warehouse", "CHILD-FG"): D(name="CHILD-FG", company="JF", is_group=0, disabled=0),
        })
        frappe = self.h.modules["frappe"]
        original_sql = frappe.db.sql
        def sql(query, args, **kwargs):
            if "jf_flow_order AS flow" in query:
                self.h.queries.append(query)
                return [D(flow=row.jf_flow_order) for row in self.plan.po_items]
            return original_sql(query, args, **kwargs)
        frappe.db.sql = sql
        custom = types.ModuleType("frappe.custom.doctype.custom_field.custom_field")
        custom.create_custom_fields = lambda *args, **kwargs: None
        issue = types.ModuleType("jifeng_erp.material_issue")
        self.guard_calls = []
        issue.assert_no_net_issue_for_manufacturing = lambda flow: self.guard_calls.append(flow.name)
        issue.assert_no_active_work_order = lambda flow: self.fail("child loss is not a manual issue")
        module_patch = patch.dict(sys.modules, {
            "frappe.custom.doctype.custom_field.custom_field": custom,
            "jifeng_erp.material_issue": issue,
        })
        module_patch.start()
        self.addCleanup(module_patch.stop)
        spec = importlib.util.spec_from_file_location("setup_child_flow_under_test",
            Path(__file__).parents[1] / "jifeng_erp/setup.py")
        self.setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.setup)

    def entry(self, purpose="Material Transfer for Manufacture", *, status=0):
        D = self.Doc
        loss = purpose == "Material Issue"
        rows = [D(item_code="CHILD-MATERIAL", stock_uom="Kg", jf_flow_order=None,
            s_warehouse="RAW" if purpose == "Material Transfer for Manufacture" else "WIP",
            t_warehouse="WIP" if purpose == "Material Transfer for Manufacture" else None)]
        if purpose == "Manufacture":
            rows.append(D(item_code="CHILD", stock_uom="Nos", is_finished_item=1,
                s_warehouse=None, t_warehouse="CHILD-FG", jf_flow_order=None))
        return D(doctype="Stock Entry", name="ENTRY", company="JF", docstatus=status,
            purpose=purpose, stock_entry_type=self.h.module.LOSS_TYPE if loss else purpose,
            work_order=None if loss else "WO", jf_production_loss_work_order="WO" if loss else None,
            bom_no=None if loss else "CHILD-BOM", items=rows)

    def test_child_transfer_manufacture_and_loss_inherit_flow_without_parent_fg_link(self):
        for purpose in ("Material Transfer for Manufacture", "Manufacture", "Material Issue"):
            with self.subTest(purpose=purpose):
                entry = self.entry(purpose)
                self.setup.validate_flow_links(entry)
                self.assertEqual({row.jf_flow_order for row in entry.items}, {"FLOW"})
                self.assertIsNone(self.work.jf_flow_order)
                self.assertEqual(self.work.qty, 10)  # parent planned qty is five.
        self.assertEqual(self.guard_calls, ["FLOW", "FLOW"])

    def test_child_job_card_inherits_parent_flow_and_retains_child_identity(self):
        self.card.jf_flow_order = None
        self.setup.validate_flow_links(self.card)
        self.assertEqual(self.card.jf_flow_order, "FLOW")
        self.assertEqual(self.card.production_item, "CHILD")
        self.assertIsNone(self.work.jf_flow_order)
        self.card.production_item = "FG"
        with self.assertRaisesRegex(ValueError, "公司、商品不一致"):
            self.setup.validate_flow_links(self.card)

    def test_parent_item_bom_or_material_cannot_replace_exact_child_source(self):
        for key, value in (("item_code", "PARENT-MATERIAL"), ("stock_uom", "Nos"),
                           ("t_warehouse", "OTHER-WIP")):
            with self.subTest(key=key):
                entry = self.entry()
                entry.items[0][key] = value
                with self.assertRaisesRegex(ValueError, "准确原子工单"):
                    self.setup.validate_flow_links(entry)
        entry = self.entry()
        entry.bom_no = "BOM"
        with self.assertRaisesRegex(ValueError, "BOM与原子工单"):
            self.setup.validate_flow_links(entry)
        entry = self.entry("Manufacture")
        entry.items[-1].item_code = "FG"
        with self.assertRaisesRegex(ValueError, "原子商品"):
            self.setup.validate_flow_links(entry)

    def test_exact_child_permissions_apply_before_flow_is_copied(self):
        entry = self.entry()
        self.h.modules["jifeng_erp.flow_quantity"].check_quantity_fields.side_effect = (
            lambda kind, *args, **kwargs: (_ for _ in ()).throw(PermissionError("child table hidden"))
            if kind == "Production Plan Sub Assembly Item" else None)
        with self.assertRaises(PermissionError):
            self.setup.validate_flow_links(entry)
        self.assertIsNone(entry.items[0].jf_flow_order)
        self.assertIsNone(self.work.jf_flow_order)

    def test_merged_or_external_plan_child_and_parent_quantity_link_are_rejected(self):
        for doc, key, value in ((self.plan, "combine_sub_items", 1),
                (self.child, "type_of_manufacturing", "Subcontract"),
                (self.parent, "item_reference", "merged-parent"),
                (self.work, "jf_flow_order", "FLOW"),
                (self.work, "track_semi_finished_goods", 1)):
            with self.subTest(key=key):
                original = doc.get(key)
                doc[key] = value
                with self.assertRaises(ValueError):
                    self.setup.validate_flow_links(self.entry())
                doc[key] = original

    def test_submitted_reverse_uses_original_scope_without_new_plan_status_gate(self):
        self.plan.status = "Closed"
        entry = self.entry("Material Issue", status=2)
        self.setup.validate_flow_links(entry)
        self.assertEqual(entry.items[0].jf_flow_order, "FLOW")
        self.assertIsNone(entry.work_order)
        self.assertIsNone(self.work.jf_flow_order)

    def test_conflicting_flow_or_loss_work_order_cannot_redirect_stock_origin(self):
        entry = self.entry()
        entry.items[0].jf_flow_order = "OTHER"
        with self.assertRaisesRegex(ValueError, "其他流程单"):
            self.setup.validate_flow_links(entry)
        entry = self.entry()
        entry.jf_production_loss_work_order = "OTHER-WO"
        with self.assertRaisesRegex(ValueError, "损耗原工单来源不一致"):
            self.setup.validate_flow_links(entry)

    def test_current_work_order_source_change_cannot_reuse_a_stale_child_pointer(self):
        entry = self.entry()
        changed = deepcopy(self.work)
        changed.production_plan_sub_assembly_item = "OTHER-CHILD"
        with patch.object(self.h.module, "_authorized_read", return_value=changed):
            with self.assertRaisesRegex(ValueError, "来源已变化"):
                self.setup.validate_flow_links(entry)
        self.assertIsNone(entry.items[0].jf_flow_order)


if __name__ == "__main__":
    unittest.main()
