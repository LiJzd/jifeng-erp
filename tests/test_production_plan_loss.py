"""Plan -> real loss issue -> native pending -> inverse cancellation risks.

No business writes. The native quantity methods below are pinned to the actual
16.36 source captured in production-loss-plan(-lock)-native-r1.private.json.
The adapters provide rows/locks only; they do not implement the tested math.
"""

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

import test_production_loss as _base

Doc, REQUEST = _base.Doc, _base.REQUEST


class NativePlanQuantities:
    def get_pending_quantities(self, plan):
        committed = self.get_committed_quantities()
        precision = self.frappe.get_precision("Work Order", "qty")
        pending = {}
        for table, reference_field, qty_field in (
            ("po_items", "production_plan_item", "planned_qty"),
            ("sub_assembly_items", "production_plan_sub_assembly_item", "qty"),
        ):
            pending[reference_field] = {
                row.name: max(
                    0, self.flt(self.flt(row.get(qty_field)) - committed[reference_field].get(row.name, 0), precision)
                )
                for row in plan.get(table)
                if table == "po_items" or row.type_of_manufacturing == "In House"
            }
        return pending

    def _get_committed_qty(self, work_order, process_loss_qty):
        return max(0, self.flt(work_order.produced_qty), self.flt(work_order.qty) - self.flt(process_loss_qty))

    def validate_work_order(self, work_order, *, process_loss_qty=0):
        row = self.lock_plan_row(work_order)
        committed = self.get_committed_quantities(
            exclude_work_order=work_order.name,
            reference_field=row.reference_field,
            reference_name=row.name,
            for_update=True,
        )[row.reference_field].get(row.name, 0)
        allowance = self.flt(self.frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_work_order"))
        precision = work_order.precision("qty")
        maximum_qty = self.flt(self.flt(row.planned_qty) * (1 + allowance / 100) - committed, precision)
        committed_qty = self.flt(self._get_committed_qty(work_order, process_loss_qty), precision)
        if committed_qty > maximum_qty:
            raise ValueError("native Plan quantity exceeded")


class NativeWork(Doc):
    def set_process_loss_qty(self):
        # Exact installed method dispatch/rollup; the class adapter provides
        # native reads and the omitted translated error text only.
        quantities = None
        if self.docstatus == 1 and self.production_plan:
            quantities = self.quantity_class(self.production_plan)
            quantities.lock_plan_row(self)
        process_loss_qty = self._process_loss_qty()
        if quantities:
            previous_loss_qty = self.frappe.db.get_value("Work Order", self.name, "process_loss_qty", for_update=True)
            if process_loss_qty < self.flt(previous_loss_qty):
                quantities.validate_work_order(self, process_loss_qty=process_loss_qty)
        self.db_set("process_loss_qty", process_loss_qty)

    def _process_loss_qty(self):
        return 0


class ProductionPlanLossTests(unittest.TestCase):
    def setUp(self):
        # Compose the shared real-source fixture without inheriting/re-running
        # every unrelated direct-Flow or recovery case in this new test file.
        self.h = h = _base.ProductionLossTests()
        h.setUp()
        self.addCleanup(h.doCleanups)
        self.module = h.module
        h.work.update(production_plan="PLAN", production_plan_item="PLAN-ROW", sales_order="SO", sales_order_item="SO-ROW")
        h.flow.update(sales_order="SO", sales_order_item="SO-ROW")
        self.row = Doc(name="PLAN-ROW", parent="PLAN", parenttype="Production Plan", parentfield="po_items", idx=1,
                       item_code="FG", jf_flow_order="FLOW", bom_no="BOM", planned_qty=5, stock_uom="Nos",
                       sales_order="SO", sales_order_item="SO-ROW")
        self.plan = Doc(doctype="Production Plan", name="PLAN", modified="plan-v1", docstatus=1, company="JF",
                        status="In Process", po_items=[self.row], sub_assembly_items=[])
        self.bom = Doc(doctype="BOM", name="BOM", modified="bom-v1", docstatus=1, company="JF", item="FG",
                       uom="Nos", is_active=1)
        self.so_row = Doc(name="SO-ROW", parent="SO", parenttype="Sales Order", parentfield="items", idx=1,
                          item_code="FG", qty=1, uom="Box", stock_uom="Nos", conversion_factor=5, stock_qty=5)
        self.order = Doc(doctype="Sales Order", name="SO", modified="so-v1", company="JF", docstatus=1, items=[self.so_row])
        h.store.update({("Production Plan", "PLAN"): self.plan, ("Production Plan Item", "PLAN-ROW"): self.row,
                        ("BOM", "BOM"): self.bom, ("Sales Order", "SO"): self.order})
        old_sql = h.modules["frappe"].db.sql

        def sql(query, args, **kwargs):
            if h.lock_error:
                return old_sql(query, args, **kwargs)
            if "WHERE production_plan=%s" in query:
                h.queries.append(query)
                field = "production_plan_sub_assembly_item" if "production_plan_sub_assembly_item=%s" in query else "production_plan_item"
                rows = [work for (kind, _), work in h.store.items() if kind == "Work Order"
                        and work.get("production_plan") == args[0] and work.get(field) == args[1]
                        and work.docstatus < 2 and work.name != args[2]]
                return [Doc({field: work.get(field) for field in (
                    "name", "qty", "produced_qty", "process_loss_qty", "docstatus")}) for work in rows]
            if "tabProduction Plan Item` WHERE name=%s AND parent=%s" in query:
                h.queries.append(query)
                row = h.store.get(("Production Plan Item", args[0]))
                return [Doc(name=row.name)] if row and row.parent == args[1] else []
            if "SELECT qty, produced_qty, process_loss_qty" in query:
                h.queries.append(query)
                return [Doc({field: work.get(field) for field in (
                    "name", "qty", "produced_qty", "process_loss_qty", "docstatus")}) for (kind, _), work in h.store.items() if kind == "Work Order"
                        and work.get("jf_flow_order") == args[0] and work.docstatus < 2 and work.name != args[1]]
            return old_sql(query, args, **kwargs)

        h.modules["frappe"].db.sql = sql

    def replacement(self, status=0, flow="FLOW", quantity=5):
        work = Doc(self.h.work)
        work.update(name="REPLACEMENT", docstatus=status, qty=quantity, produced_qty=0,
                    process_loss_qty=0, jf_flow_order=flow)
        self.h.store["Work Order", work.name] = work
        return work

    def test_plan_loss_is_exact_stock_source_and_closed_history_can_reverse(self):
        h = self.h
        source = h.source()
        self.assertEqual((source["qty"], source["inventory_qty"], source["work_order"]), (Decimal(5), Decimal(0), "WO"))
        self.assertFalse(h.entry().get("work_order") or h.entry().get("job_card"))
        self.module.validate_loss_entry(h.entry())
        self.assertEqual(self.module.inspect_loss_issue("JC", REQUEST)["items"][0]["qty"], 10)
        doc = h.post()
        self.plan.status = "Closed"
        self.bom.is_active = 0
        self.assertEqual(h.source(doc)["lost_qty"], 5)
        self.module.before_cancel_loss_entry(doc)
        with self.assertRaisesRegex(ValueError, "生产计划"):
            self.module.validate_loss_entry(h.entry(name="OTHER"))
        self.assertEqual(h.card.total_completed_qty, 0)

    def test_draft_cross_company_wrong_child_dual_refs_and_merged_source_rejected(self):
        h = self.h
        for doc, field, value in (
                (self.plan, "docstatus", 0), (self.plan, "company", "OTHER"),
                (self.row, "parent", "OTHER-PLAN"), (self.row, "parenttype", "Other"),
                (self.row, "parentfield", "sub_assembly_items"), (self.row, "jf_flow_order", "OTHER"),
                (self.row, "item_code", "OTHER"), (self.row, "stock_uom", "Kg"),
                (self.row, "bom_no", "OTHER"), (self.row, "planned_qty", 4),
                (self.row, "item_reference", "MERGED"), (h.work, "production_plan_sub_assembly_item", "SUB")):
            original = doc.get(field)
            doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                h.source()
            doc[field] = original
        self.plan.po_items.append(deepcopy(self.row))
        with self.assertRaises(ValueError): h.source()

    def test_bom_and_exact_sales_line_quantity_unit_proofs_do_not_guess_conversion(self):
        h = self.h
        for doc, field, value in (
                (self.bom, "docstatus", 0), (self.bom, "company", "OTHER"), (self.bom, "item", "OTHER"),
                (self.bom, "uom", "Box"), (self.order, "company", "OTHER"), (self.order, "docstatus", 0),
                (self.row, "sales_order_item", "WRONG"), (self.so_row, "parent", "OTHER"),
                (self.so_row, "stock_uom", "Kg"), (self.so_row, "conversion_factor", 1),
                (self.so_row, "stock_qty", 4)):
            original = doc.get(field)
            doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): h.source()
            doc[field] = original
        self.so_row.precisions = {"stock_qty": 3}
        self.so_row.update(qty="0.3333333", conversion_factor=3, stock_qty=1)
        self.assertEqual(h.source()["qty"], 5)

    def test_new_closed_stopped_plan_and_inactive_bom_reject_but_read_source_stays_legal(self):
        for status in ("Closed", "Stopped", "Cancelled", "Draft"):
            self.plan.status = status
            self.assertEqual(self.h.source()["qty"], 5)
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "生产计划"):
                self.module.inspect_loss_issue("JC", REQUEST)
        self.plan.status = "In Process"
        self.bom.is_active = 0
        with self.assertRaisesRegex(ValueError, "BOM 已停用"):
            self.module.validate_loss_entry(self.h.entry())

    def test_plan_sales_bom_permissions_and_masked_child_fields_stop_new_draft(self):
        h = self.h
        for doc in (self.plan, self.order, self.bom):
            doc.denied = ("read",)
            with self.assertRaises(PermissionError): self.module.inspect_loss_issue("JC", REQUEST)
            doc.denied = ()
        for kind, field in (("Production Plan Item", "planned_qty"), ("Sales Order Item", "conversion_factor"),
                            ("Production Plan", "company"), ("BOM", "item"), ("Work Order", "sales_order_item")):
            def deny(doctype, fields, **kwargs):
                if doctype == kind and field in fields: raise PermissionError("masked source field")
            with patch.object(h.modules["jifeng_erp.flow_quantity"], "check_quantity_fields", deny), self.assertRaises(PermissionError):
                self.module.inspect_loss_issue("JC", REQUEST)
        self.assertFalse(h.insert_count)

    def test_scope_lock_order_flow_plan_row_peers_work_card_and_timeout_rollback(self):
        h = self.h
        self.replacement()
        self.module.inspect_loss_issue("JC", REQUEST)
        indexes = [next(i for i, query in enumerate(h.queries) if marker in query) for marker in (
            "tabJifeng Flow Order", "tabProduction Plan`", "tabProduction Plan Item` WHERE name",
            "tabWork Order` WHERE production_plan", "tabWork Order` WHERE name", "tabJob Card` WHERE name")]
        self.assertEqual(indexes, sorted(indexes))
        self.assertTrue(all("NOWAIT" in query for query in h.queries if "FOR UPDATE" in query))
        self.assertTrue(any("docstatus<2" in query for query in h.queries if "WHERE production_plan=" in query))
        for error in (TimeoutError, ConnectionAbortedError):
            h.lock_error = error
            with self.subTest(error=error), self.assertRaisesRegex(error, "正在更新"):
                self.module.inspect_loss_issue("JC", REQUEST)
        self.assertFalse(h.insert_count)

    def test_advisory_plan_pointer_must_match_current_locked_work(self):
        h = self.h
        original_sql = h.modules["frappe"].db.sql
        def change(query, args, **kwargs):
            result = original_sql(query, args, **kwargs)
            if "tabProduction Plan`" in query:
                h.work.production_plan_item = "CHANGED"
            return result
        with patch.dict(h.modules["frappe"].db, {"sql": change}), self.assertRaises(RuntimeError):
            self.module._lock_work_scope("WO")

    def test_cancel_checks_plan_row_drafts_and_submitted_work_independent_of_flow_capacity(self):
        h = self.h
        doc = h.post()
        # A larger Flow target cannot allow reversing loss that already freed
        # this particular Plan row for another active WO, including drafts.
        h.modules["jifeng_erp.workflow"].stock_plan_quantity = lambda flow: 10
        for status in (0, 1):
            replacement = self.replacement(status=status, flow="OTHER-FLOW")
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "原生产计划补产预留"):
                self.module.before_cancel_loss_entry(doc)
            replacement.docstatus = 2
            self.module.before_cancel_loss_entry(doc)
        self.assertEqual(h.card.total_completed_qty, 0)

    def test_actual_native_pending_and_loss_setter_dispatch_release_then_reverse_once(self):
        h = self.h
        frappe = h.modules["frappe"]
        flt = h.modules["frappe.utils"].flt
        frappe.get_precision = lambda *args: 6
        frappe.db.get_single_value = lambda *args: 0
        fixture = self
        class Quantities(NativePlanQuantities):
            def __init__(self, name): self.production_plan = name
            def lock_plan_row(self, work):
                h.queries.append("native plan child lock (already prelocked)")
                row = Doc(fixture.row)
                row.reference_field = "production_plan_item"
                return row
            def get_committed_quantities(self, exclude_work_order=None, reference_field=None, reference_name=None, for_update=False):
                totals = {"production_plan_item": defaultdict(float), "production_plan_sub_assembly_item": defaultdict(float)}
                for (kind, _), work in h.store.items():
                    if (kind == "Work Order" and work.get("production_plan") == self.production_plan
                            and work.docstatus == 1 and work.name != exclude_work_order):
                        field = "production_plan_sub_assembly_item" if work.get("production_plan_sub_assembly_item") else "production_plan_item"
                        if not reference_field or (field == reference_field and work.get(field) == reference_name):
                            totals[field][work.get(field)] += self._get_committed_qty(work, work.get("process_loss_qty"))
                return totals
        Quantities.frappe, Quantities.flt = frappe, staticmethod(flt)
        progress = h.progress_module()
        class Work(progress.ProductionLossWorkOrderMixin, NativeWork): pass
        work = Work(h.work)
        work.frappe, work.flt, work.quantity_class = frappe, flt, Quantities
        h.store["Work Order", "WO"] = h.work = work
        quantities = Quantities("PLAN")
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_item"]["PLAN-ROW"], 0)
        doc = h.post()
        h.queries.clear()
        work.set_process_loss_qty()
        native_index = next(i for i, query in enumerate(h.queries) if query.startswith("native plan"))
        plan_index = next(i for i, query in enumerate(h.queries) if "tabProduction Plan`" in query)
        self.assertLess(plan_index, native_index)
        self.assertEqual((work.process_loss_qty, h.card.total_completed_qty, work.produced_qty), (5, 0, 0))
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_item"]["PLAN-ROW"], 5)
        replacement = self.replacement(status=1)
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_item"]["PLAN-ROW"], 0)
        # Native setter itself also refuses restoring original commitment while
        # submitted replacement remains. Drafts are covered by controller cap.
        doc.docstatus = 2
        with self.assertRaisesRegex(ValueError, "native Plan quantity exceeded"):
            work.set_process_loss_qty()
        self.assertEqual(work.process_loss_qty, 5)
        replacement.docstatus = 2
        work.set_process_loss_qty()
        self.assertEqual(work.process_loss_qty, 0)
        self.assertEqual(quantities.get_pending_quantities(self.plan)["production_plan_item"]["PLAN-ROW"], 0)
        self.assertEqual(h.card.total_completed_qty, 0)


if __name__ == "__main__":
    unittest.main()
