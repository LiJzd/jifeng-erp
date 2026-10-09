"""Native Plan subassembly factory with exact source and draft capacity.

Uses the captured installed native factory bodies and the real pure source
helper. These adapters do not submit business documents or verify a GUI.
"""

from copy import deepcopy
import unittest
from unittest.mock import patch

import test_production_plan_replacement as _factory


Doc = _factory.Doc


class ProductionPlanSubassemblyReplacementTests(unittest.TestCase):
    def setUp(self):
        self.driver = d = _factory.ProductionPlanReplacementTests()
        d.setUp()
        self.addCleanup(d.doCleanups)
        self.h, self.p, self.plan = d.h, d.fixture, d.plan
        self.child = Doc(name="SUB-ROW", parent="PLAN", parenttype="Production Plan",
            parentfield="sub_assembly_items", idx=1, production_plan_item="PLAN-ROW",
            production_item="SUB", parent_item_code="FG", bom_no="SUB-BOM", bom_level=0,
            type_of_manufacturing="In House", qty=10, stock_uom="Kg", uom="Kg",
            fg_warehouse="SUB-FG", sales_order="SO", sales_order_item="SO-ROW")
        self.p.plan.update(combine_sub_items=0, sub_assembly_items=[self.child])
        self.p.bom.update(quantity=1, items=[Doc(name="PARENT-COMP", item_code="SUB",
            bom_no="SUB-BOM", stock_qty=2, stock_uom="Kg")])
        self.child_bom = Doc(name="SUB-BOM", doctype="BOM", company="JF", docstatus=1,
            is_active=1, item="SUB", uom="Kg", quantity=1,
            items=[Doc(name="SUB-COMP", item_code="RM", stock_qty=2, stock_uom="Kg")])
        self.h.store.update({("Production Plan Sub Assembly Item", "SUB-ROW"): self.child,
            ("BOM", "SUB-BOM"): self.child_bom,
            ("Item", "SUB"): Doc(name="SUB", stock_uom="Kg", is_stock_item=1),
            ("Warehouse", "SUB-FG"): Doc(name="SUB-FG", company="JF", is_group=0, disabled=0)})
        self.plan.update(deepcopy(self.p.plan))

    def child_work(self, name="CHILD-WO", *, quantity=10, loss=0, status=1):
        work = Doc(name=name, doctype="Work Order", docstatus=status, company="JF", qty=quantity,
            produced_qty=0, process_loss_qty=loss, production_item="SUB", bom_no="SUB-BOM",
            stock_uom="Kg", production_plan="PLAN", production_plan_item=None,
            production_plan_sub_assembly_item="SUB-ROW", jf_flow_order=None,
            track_semi_finished_goods=0, fg_warehouse="SUB-FG", wip_warehouse="WIP",
            source_warehouse=None, sales_order="SO", sales_order_item="SO-ROW")
        self.h.store["Work Order", name] = work
        return work

    def created(self):
        return [self.h.store["Work Order", name] for name in self.driver.created]

    def test_native_child_loss_capacity_reuses_exact_draft_and_keeps_parent_units(self):
        original = self.child_work(loss=10)
        self.assertEqual(self.driver.quantities.get_pending_quantities(self.p.plan)
                         ["production_plan_sub_assembly_item"]["SUB-ROW"], 10)
        self.plan.make_work_order()
        self.assertEqual(len(self.created()), 1)
        replacement = self.created()[0]
        self.assertEqual((replacement.qty, replacement.production_item, replacement.stock_uom,
            replacement.production_plan, replacement.production_plan_item,
            replacement.production_plan_sub_assembly_item, replacement.get("jf_flow_order")),
            (10, "SUB", "Kg", "PLAN", None, "SUB-ROW", None))
        self.assertEqual((replacement.wip_warehouse, replacement.fg_warehouse, replacement.reserve_stock),
                         ("WIP", "SUB-FG", 1))
        self.assertEqual(replacement.required_items[0].required_qty, 20)
        self.plan.make_work_order()
        self.assertEqual(len(self.created()), 1)
        self.assertEqual(self.driver.work_messages()[-1], (replacement.name,))
        self.assertEqual(original.qty, 10)
        self.assertEqual(self.h.flow.planned_qty, 5)
        self.assertEqual(self.driver.module._allocated("FLOW"), 5)

    def test_partial_draft_only_fills_remaining_and_cancelled_drafts_release(self):
        self.child_work(loss=10)
        draft = self.child_work("DRAFT", quantity=4, status=0)
        self.plan.make_work_order()
        self.assertEqual(draft.qty, 4)
        self.assertEqual(self.created()[0].qty, 6)
        self.assertEqual(set(self.driver.work_messages()[-1]), {draft.name, self.created()[0].name})
        self.plan.make_work_order()
        self.assertEqual(len(self.created()), 1)
        draft.docstatus = 2
        self.plan.make_work_order()
        self.assertEqual([work.qty for work in self.created()], [6, 4])

    def test_hidden_draft_occupies_quantity_without_returning_name(self):
        self.child_work(loss=10)
        draft = self.child_work("HIDDEN", status=0)
        draft.denied = ("read",)
        self.plan.make_work_order()
        self.assertFalse(self.created())
        self.assertTrue(all(not names for names in self.driver.work_messages()))

    def test_child_row_cap_other_rows_and_plans_do_not_borrow_capacity(self):
        self.child_work(loss=10)
        other = self.child_work("OTHER", quantity=30, status=0)
        other.production_plan = "OTHER-PLAN"
        self.plan.make_work_order()
        self.assertEqual(self.created()[0].qty, 10)
        over = self.child_work("OVER", quantity=0.1, status=0)
        with self.assertRaisesRegex(ValueError, "超过对应生产计划"):
            self.plan.make_work_order()
        self.assertEqual(over.qty, 0.1)

    def test_exact_child_draft_identity_and_native_warehouse_choices(self):
        self.child_work(loss=10)
        draft = self.child_work("DRAFT", status=0)
        for field, value in (("production_plan_item", "PLAN-ROW"), ("jf_flow_order", "FLOW"),
                ("bom_no", "BOM"), ("production_item", "FG"), ("stock_uom", "Nos"),
                ("company", "OTHER"), ("fg_warehouse", "OTHER"), ("sales_order_item", "OTHER")):
            old = draft.get(field)
            draft[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan.make_work_order()
            draft[field] = old
        draft.wip_warehouse = "ORIGINAL-WIP"
        draft.source_warehouse = "ORIGINAL-SOURCE"
        self.plan.make_work_order()
        self.assertEqual(self.driver.work_messages()[-1], (draft.name,))
        self.assertEqual(draft.wip_warehouse, "ORIGINAL-WIP")

    def test_ambiguous_or_changed_parent_source_stops_before_native_insert(self):
        for record, field, value in ((self.p.plan, "combine_sub_items", 1),
                (self.p.row, "item_reference", "MERGED"), (self.child, "production_plan_item", "MISSING"),
                (self.child, "parent_item_code", "OTHER"), (self.child, "bom_level", 1),
                (self.child, "stock_uom", "Nos"), (self.child, "bom_no", "BOM"),
                (self.p.plan, "status", "Closed"), (self.child_bom, "is_active", 0),
                (self.h.flow, "source_changed", 1), (self.h.flow, "source_status", "Closed")):
            old = record.get(field)
            record[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan.make_work_order()
            self.assertFalse(self.created())
            record[field] = old

    def test_manual_child_validator_refuses_non_inhouse_and_parent_flow_quantity(self):
        work = self.child_work(status=0)
        for value in ("Subcontract", "Material Request"):
            self.child.type_of_manufacturing = value
            with self.assertRaisesRegex(ValueError, "In House"):
                self.driver.module.validate_work_order(work)
        self.child.type_of_manufacturing = "In House"
        work.jf_flow_order = "FLOW"
        with self.assertRaisesRegex(ValueError, "子装配工单"):
            self.driver.module.validate_work_order(work)

    def test_manual_child_closed_plan_progress_and_reverse_native_quantity_cap(self):
        original = self.child_work(loss=10)
        draft = self.child_work("DRAFT", status=0)
        with self.assertRaisesRegex(ValueError, "超过对应生产计划"):
            original.process_loss_qty = 0
            self.driver.module.validate_work_order(original)
        original.process_loss_qty = 10
        draft.docstatus = 2
        self.p.plan.status = "Closed"
        self.driver.module.validate_work_order(original)
        original.process_loss_qty = 0
        self.driver.module.validate_work_order(original)
        self.assertEqual(self.driver.quantities.get_pending_quantities(self.p.plan)
                         ["production_plan_sub_assembly_item"]["SUB-ROW"], 0)
        draft.docstatus = 0
        with self.assertRaisesRegex(ValueError, "停止、关闭"):
            self.driver.module.validate_work_order(draft)

    def test_real_pure_loss_source_and_inverse_cap_use_child_not_parent_quantity(self):
        self.h.work.update(production_plan_item=None, production_plan_sub_assembly_item="SUB-ROW",
            jf_flow_order=None, production_item="SUB", bom_no="SUB-BOM", stock_uom="Kg", fg_warehouse="SUB-FG")
        self.h.card.update(production_item="SUB", stock_uom="Kg")
        loss = self.h.post()
        source = self.h.source(loss)
        self.assertEqual((source["flow_order"], source["item_code"], source["stock_uom"],
                         source["qty"], source["inventory_qty"]), ("FLOW", "SUB", "Kg", 5, 0))
        self.h.work.process_loss_qty = 5
        self.plan.make_work_order()
        children = [work for work in self.created() if work.production_item == "SUB"]
        self.assertEqual(children[0].qty, 10)
        with self.assertRaisesRegex(ValueError, "原生产计划补产预留"):
            self.h.module.before_cancel_loss_entry(loss)
        children[0].docstatus = 2
        self.h.module.before_cancel_loss_entry(loss)
        loss.docstatus = 2
        self.h.work.process_loss_qty = 0
        self.plan.make_work_order()
        self.assertEqual([work.qty for work in self.created() if work.production_item == "SUB"], [10, 5])
        self.assertEqual(self.h.card.total_completed_qty, 0)

    def test_child_source_permission_and_native_stock_precision(self):
        self.child_bom.denied = ("read",)
        with self.assertRaises(PermissionError):
            self.plan.make_work_order()
        self.assertFalse(self.created())
        self.child_bom.denied = ()
        self.driver.precision = 9
        self.child.qty = 1
        self.child_work("DRAFT", quantity=1, status=0)
        self.child_work("TINY", quantity=0.000000001, status=0)
        with self.assertRaisesRegex(ValueError, "超过对应生产计划"):
            self.plan.make_work_order()

    def test_masked_parent_child_bom_and_sales_fields_stop_before_native_insert(self):
        for kind, field in (("Production Plan", "sub_assembly_items"),
                ("Production Plan Sub Assembly Item", "qty"), ("Production Plan Item", "jf_flow_order"),
                ("BOM", "quantity"), ("BOM Item", "stock_qty"),
                ("Sales Order Item", "conversion_factor"), ("Jifeng Flow Order", "source_status")):
            def deny(doctype, fields, **kwargs):
                if doctype == kind and field in fields:
                    raise PermissionError("masked source field")
            with self.subTest(kind=kind, field=field), patch.object(
                    self.h.modules["jifeng_erp.flow_quantity"], "check_quantity_fields", deny):
                with self.assertRaises(PermissionError):
                    self.plan.make_work_order()
            self.assertFalse(self.created())

    def test_lock_order_and_advisory_pointer_change_have_no_reversed_new_flow_lock(self):
        self.child_work(loss=10)
        self.h.queries.clear()
        self.plan.make_work_order()
        first_flow = next(i for i, query in enumerate(self.h.queries)
                          if isinstance(query, str) and "tabJifeng Flow Order`" in query)
        first_plan = next(i for i, query in enumerate(self.h.queries)
                          if isinstance(query, str) and "tabProduction Plan`" in query)
        first_work = next(i for i, query in enumerate(self.h.queries)
                          if isinstance(query, tuple) and query[0] == "current-WO")
        self.assertLess(first_flow, first_plan)
        self.assertLess(first_plan, first_work)
        work = self.child_work("MANUAL", quantity=1, status=0)
        old_sql = self.driver.frappe.db.sql
        def change(query, args, **kwargs):
            result = old_sql(query, args, **kwargs)
            if "tabProduction Plan` WHERE name=%s" in query:
                self.child.production_plan_item = "CHANGED"
            return result
        self.driver.frappe.db.sql = change
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            self.driver.module.validate_work_order(work)

    def test_unlinked_native_plan_retains_original_subassembly_factory(self):
        self.p.row.jf_flow_order = None
        self.child.sales_order = self.child.sales_order_item = None
        self.p.plan.combine_sub_items = 1
        self.plan.make_work_order()
        self.assertEqual(len(self.created()), 1)
        self.assertEqual((self.created()[0].production_item, self.created()[0].qty), ("SUB", 10))


if __name__ == "__main__":
    unittest.main()
