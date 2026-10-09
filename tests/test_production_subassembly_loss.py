"""Exact plan child -> full WIP loss -> replacement -> inverse cancellation."""

from copy import deepcopy
from collections import defaultdict
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_production_plan_loss as plan_test

Doc = plan_test.Doc


class ProductionSubassemblyLossTests(unittest.TestCase):
    def setUp(self):
        self.plan_h = plan_test.ProductionPlanLossTests()
        self.plan_h.setUp()
        self.addCleanup(self.plan_h.doCleanups)
        self.h = h = self.plan_h.h
        self.subject = h.module
        self.plan = self.plan_h.plan
        self.plan.combine_sub_items = 0
        self.sub = Doc(doctype="Production Plan Sub Assembly Item", name="SUB-ROW", parent="PLAN",
            parenttype="Production Plan", parentfield="sub_assembly_items", idx=1, production_item="SUB",
            parent_item_code="FG", production_plan_item="PLAN-ROW", bom_no="SUB-BOM", bom_level=0,
            type_of_manufacturing="In House", qty=10, uom="Kg", stock_uom="Kg",
            sales_order="SO", sales_order_item="SO-ROW", fg_warehouse="SUB-FG")
        self.plan.sub_assembly_items = [self.sub]
        parent_bom = self.plan_h.bom
        parent_bom.quantity = 1
        parent_bom.items = [Doc(doctype="BOM Item", name="PARENT-COMPONENT", parent="BOM",
            parenttype="BOM", parentfield="items", item_code="SUB", bom_no="SUB-BOM", stock_uom="Kg", stock_qty=2)]
        self.bom = Doc(doctype="BOM", name="SUB-BOM", docstatus=1, modified="sub-bom-v1", company="JF",
            item="SUB", uom="Kg", quantity=2, is_active=1, items=[Doc(doctype="BOM Item", name="SUB-RAW",
                parent="SUB-BOM", parenttype="BOM", parentfield="items", item_code="RM", stock_uom="Kg", stock_qty=4)])
        h.work.update(production_plan_item=None, production_plan_sub_assembly_item="SUB-ROW", jf_flow_order=None,
            production_item="SUB", stock_uom="Kg", bom_no="SUB-BOM", qty=10, fg_warehouse="SUB-FG")
        h.work.required_items[0].required_qty = 20
        h.card.update(production_item="SUB", stock_uom="Kg", for_quantity=10, process_loss_qty=10)
        # Root Flow contains the subassembly, not its raw material. The child
        # must prove its own BOM and WO quantities rather than invent root raw.
        h.flow.materials = [Doc(item_code="SUB")]
        h.transfer.items[0].transfer_qty = 20
        h.ledger["TRANSFER"][0].update(actual_qty=20, stock_value_difference=80)
        h.store.update({("Production Plan Sub Assembly Item", "SUB-ROW"): self.sub,
            ("BOM", "SUB-BOM"): self.bom,
            ("Item", "SUB"): Doc(doctype="Item", name="SUB", stock_uom="Kg", is_stock_item=1),
            ("Warehouse", "SUB-FG"): Doc(doctype="Warehouse", name="SUB-FG", company="JF", is_group=0, disabled=0)})
        old_sql = h.modules["frappe"].db.sql

        def sql(query, args, **kwargs):
            if not h.lock_error and "SELECT jf_flow_order AS flow FROM `tabProduction Plan Item`" in query:
                h.queries.append(query)
                return [Doc(flow=row.jf_flow_order) for row in self.plan.po_items if row.get("jf_flow_order")]
            return old_sql(query, args, **kwargs)
        h.modules["frappe"].db.sql = sql

    def entry(self, name="LOSS", status=0):
        doc = self.h.entry(name, status)
        doc.jf_production_loss_qty = 10
        doc.items[0].qty = doc.items[0].transfer_qty = 20
        return doc

    def post(self):
        doc = self.entry(status=1)
        self.h.store["Stock Entry", doc.name] = doc
        self.h.ledger[doc.name] = [Doc(name="LOSS-SLE", voucher_detail_no="LOSS-ROW", company="JF", item_code="RM",
            warehouse="WIP", stock_uom="Kg", actual_qty=-20, stock_value_difference=-80)]
        self.h.work.process_loss_qty = 10
        return doc

    def test_exact_child_stock_source_and_root_units_are_not_mixed(self):
        source = self.subject.read_loss_source(self.entry(), read=lambda kind, name: self.h.store[kind, name])
        self.assertEqual((source["flow_order"], source["item_code"], source["stock_uom"], source["qty"]),
                         ("FLOW", "SUB", "Kg", Decimal(10)))
        self.assertEqual(source["production_plan_sub_assembly_item"], "SUB-ROW")
        self.subject.validate_loss_entry(self.entry())
        result = self.subject.inspect_loss_issue("JC", plan_test.REQUEST)
        self.assertEqual(result["items"][0]["qty"], 20)
        self.assertFalse(self.h.work.get("jf_flow_order"))
        self.assertEqual(self.h.card.total_completed_qty, 0)
        self.assertFalse(self.h.insert_count)

    def test_wrong_child_parent_bom_unit_and_merged_or_non_house_sources_rejected(self):
        for doc, field, value in ((self.plan, "combine_sub_items", 1), (self.plan, "company", "OTHER"),
                (self.sub, "type_of_manufacturing", "Subcontract"), (self.sub, "production_plan_item", "OTHER"),
                (self.sub, "parent_item_code", "OTHER"), (self.sub, "bom_level", 1),
                (self.sub, "bom_no", "BOM"), (self.sub, "qty", 11), (self.sub, "uom", "Box"),
                (self.sub, "sales_order_item", "OTHER"), (self.plan_h.row, "item_reference", "MERGED"),
                (self.bom, "company", "OTHER"), (self.bom, "uom", "Nos"),
                (self.h.work, "production_plan_item", "PLAN-ROW"), (self.h.work, "jf_flow_order", "FLOW"),
                (self.h.work, "fg_warehouse", "WIP"), (self.h.work.required_items[0], "required_qty", 19)):
            previous = doc.get(field)
            doc[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                self.subject.read_loss_source(self.entry(), read=lambda kind, name: self.h.store[kind, name])
            doc[field] = previous
        duplicate = deepcopy(self.sub)
        duplicate.name = "SECOND-SUB-ROW"
        self.plan.sub_assembly_items.append(duplicate)
        with self.assertRaises(ValueError): self.h.source(self.entry())

    def test_closed_history_can_read_and_cancel_but_new_write_rejects(self):
        doc = self.post()
        self.plan.status = "Closed"
        self.bom.is_active = 0
        self.assertEqual(self.h.source(doc)["qty"], 10)
        self.subject.before_cancel_loss_entry(doc)
        with self.assertRaisesRegex(ValueError, "生产计划"):
            self.subject.inspect_loss_issue("JC", plan_test.REQUEST)

    def test_replacement_reserves_exact_child_and_must_cancel_first(self):
        doc = self.post()
        replacement = self.plan_h.replacement(quantity=10, flow=None)
        self.h.work.process_loss_qty = 10
        # An unrelated final-FG WO does not consume child Kg reservations.
        self.h.other_allocated = 5
        self.h.store["Work Order", "FINAL-WO"] = Doc(doctype="Work Order", name="FINAL-WO", jf_flow_order="FLOW",
            qty=5, docstatus=0, production_plan=None, produced_qty=0, process_loss_qty=0)
        with self.assertRaisesRegex(ValueError, "生产计划补产预留"):
            self.subject.before_cancel_loss_entry(doc)
        replacement.docstatus = 1
        with self.assertRaisesRegex(ValueError, "生产计划补产预留"):
            self.subject.before_cancel_loss_entry(doc)
        replacement.docstatus = 2
        self.subject.before_cancel_loss_entry(doc)
        doc.docstatus = 2
        self.assertEqual(self.subject.get_booked_loss_for_work_order("WO"), 0)

    def test_native_child_manufacture_loss_cancel_guards_same_child_reservations(self):
        manufacture = Doc(doctype="Stock Entry", name="MAKE", company="JF", work_order="WO", purpose="Manufacture",
            stock_entry_type="Manufacture", docstatus=1, process_loss_qty=2,
            items=[Doc(is_finished_item=1, transfer_qty=8)])
        self.h.store["Stock Entry", "MAKE"] = manufacture
        self.h.work.update(process_loss_qty=2, produced_qty=8)
        replacement = self.plan_h.replacement(quantity=2, flow=None)
        with self.assertRaisesRegex(ValueError, "生产计划补产预留"):
            self.subject.before_cancel_loss_entry(manufacture)
        replacement.docstatus = 2
        self.subject.before_cancel_loss_entry(manufacture)

    def test_parent_flow_plan_child_work_card_current_lock_order_and_changes(self):
        self.subject.inspect_loss_issue("JC", plan_test.REQUEST)
        queries = self.h.queries
        indices = [next(index for index, query in enumerate(queries) if key in query and "FOR UPDATE" in query) for key in (
            "tabJifeng Flow Order", "tabProduction Plan` WHERE name", "tabProduction Plan Item` WHERE parent",
            "tabProduction Plan Sub Assembly Item` WHERE parent", "tabWork Order` WHERE production_plan",
            "tabWork Order` WHERE name", "tabJob Card` WHERE name")]
        self.assertEqual(indices, sorted(indices))
        self.assertTrue(all("NOWAIT" in query for query in queries if "FOR UPDATE" in query))
        # Advisory Flow pointers may not introduce another Flow after Plan
        # locks are taken, including another parent of this same Plan.
        old_get_doc = self.h.modules["frappe"].get_doc
        def read(kind, name=None, **kwargs):
            if kind == "Production Plan":
                self.plan_h.row.jf_flow_order = "LATE-FLOW"
            return old_get_doc(kind, name, **kwargs)
        with patch.object(self.h.modules["frappe"], "get_doc", read), self.assertRaises(RuntimeError):
            self.subject._lock_work_scope("WO")

    def test_child_source_permissions_include_actual_parent_bom_and_raw_contract(self):
        for kind, field in (("Production Plan", "combine_sub_items"),
                ("Production Plan Sub Assembly Item", "production_plan_item"),
                ("BOM Item", "stock_qty"), ("Work Order", "fg_warehouse")):
            def deny(doctype, fields, **kwargs):
                if doctype == kind and field in fields: raise PermissionError("masked source")
            with patch.object(self.h.modules["jifeng_erp.flow_quantity"], "check_quantity_fields", deny), self.assertRaises(PermissionError):
                self.subject.inspect_loss_issue("JC", plan_test.REQUEST)
        self.plan_h.order.denied = ("read",)
        with self.assertRaises(PermissionError): self.subject.inspect_loss_issue("JC", plan_test.REQUEST)

    def test_native_progress_child_locks_source_before_plan_setter(self):
        path = Path(__file__).resolve().parents[1] / "jifeng_erp" / "production_loss_progress.py"
        spec = importlib.util.spec_from_file_location("child_loss_progress_risk", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        class Native(Doc):
            def set_process_loss_qty(self): calls.append("native"); return "unchanged"
        Work = type("Work", (module.ProductionLossWorkOrderMixin, Native), {})
        work = Work(self.h.work)
        with patch.object(module, "_lock_work_scope", lambda name: calls.append(("scope", name))):
            self.assertEqual(work.set_process_loss_qty(), "unchanged")
        self.assertEqual(calls, [("scope", "WO"), "native"])

    def test_captured_native_pending_and_setter_loss_replacement_cancel_cycle(self):
        h = self.h
        frappe, flt = h.modules["frappe"], h.modules["frappe.utils"].flt
        frappe.get_precision = lambda *args: 6
        frappe.db.get_single_value = lambda *args: 0
        fixture = self
        class Quantities(plan_test.NativePlanQuantities):
            def __init__(self, name): self.production_plan = name
            def lock_plan_row(self, work):
                h.queries.append("native child lock (already prelocked)")
                row = Doc(fixture.sub, reference_field="production_plan_sub_assembly_item", planned_qty=fixture.sub.qty)
                return row
            def get_committed_quantities(self, exclude_work_order=None, reference_field=None, reference_name=None, for_update=False):
                totals = {"production_plan_item": defaultdict(float), "production_plan_sub_assembly_item": defaultdict(float)}
                for (kind, _), work in h.store.items():
                    if kind == "Work Order" and work.get("production_plan") == self.production_plan and work.docstatus == 1 and work.name != exclude_work_order:
                        field = "production_plan_sub_assembly_item" if work.get("production_plan_sub_assembly_item") else "production_plan_item"
                        if not reference_field or field == reference_field and work.get(field) == reference_name:
                            totals[field][work.get(field)] += self._get_committed_qty(work, work.get("process_loss_qty"))
                return totals
        Quantities.frappe, Quantities.flt = frappe, staticmethod(flt)
        progress = h.progress_module()
        class Work(progress.ProductionLossWorkOrderMixin, plan_test.NativeWork): pass
        work = Work(h.work)
        work.frappe, work.flt, work.quantity_class = frappe, flt, Quantities
        h.store["Work Order", "WO"] = h.work = work
        quantities = Quantities("PLAN")
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_sub_assembly_item"]["SUB-ROW"], 0)
        doc = self.post()
        work.set_process_loss_qty()
        self.assertEqual((work.process_loss_qty, work.produced_qty, h.card.total_completed_qty), (10, 0, 0))
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_sub_assembly_item"]["SUB-ROW"], 10)
        replacement = self.plan_h.replacement(status=1, quantity=10, flow=None)
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_sub_assembly_item"]["SUB-ROW"], 0)
        doc.docstatus = 2
        with self.assertRaisesRegex(ValueError, "native Plan quantity exceeded"):
            work.set_process_loss_qty()
        replacement.docstatus = 2
        work.set_process_loss_qty()
        self.assertEqual(work.process_loss_qty, 0)
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_sub_assembly_item"]["SUB-ROW"], 0)
        self.assertEqual(h.card.total_completed_qty, 0)


if __name__ == "__main__":
    unittest.main()
