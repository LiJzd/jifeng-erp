"""Batch source/controller/native reservation integration; no business writes."""

import ast
from decimal import Decimal
import sys
import types
import unittest
from unittest.mock import patch

import test_production_loss as ordinary
import test_production_loss_batches as batches


class LossBatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = ordinary.ProductionLossTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.b = batches.BatchLossTests()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.source, self.work, self.card = self.h.module, self.h.work, self.h.card
        self.h.store.update(self.b.store)
        self.b.store = self.h.store
        self.b.work = self.work
        self.source.batch_sources = self.b.module
        self.h.transfer = self.b.transfer
        self.h.ledger["TRANSFER"] = [self.b.sle]
        self.doc = self.h.entry()
        self.doc.update(posting_date="2026-10-09", posting_time="10:00:00")
        self.doc.items[0].batch_no = "BATCH"
        self.doc.items[0].update(parent=self.doc.name, parenttype="Stock Entry", parentfield="items", docstatus=0)
        self.b.loss = self.doc
        self.events = []
        self.native = types.ModuleType("erpnext.manufacturing.doctype.work_order.work_order")
        self.native.get_consumed_qty = self.native_consumed
        self.patches = [
            patch.object(self.source, "_internal_read", self.read),
            patch.object(self.source, "_current_records", self.b.records),
            patch.dict(sys.modules, {self.native.__name__: self.native}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        old_sql = self.h.modules["frappe"].db.sql

        def sql(query, args, **kwargs):
            if "WHERE production_plan=%s" in query:
                self.h.queries.append(query)
                return [ordinary.Doc({field: doc.get(field) for field in (
                        "name", "qty", "produced_qty", "process_loss_qty", "docstatus")})
                    for (kind, _), doc in self.h.store.items() if kind == "Work Order"
                    and doc.get("production_plan") == args[0] and doc.get("production_plan_item") == args[1]
                    and doc.docstatus < 2 and doc.name != args[2]]
            if "tabStock Reservation Entry" in query:
                self.h.queries.append(query)
                if self.h.lock_error:
                    raise self.h.lock_error("reservation occupied")
                return [ordinary.Doc({field: doc.get(field) for field in self.source.RESERVATION_FIELDS})
                    for (kind, _), doc in self.h.store.items() if kind == "Stock Reservation Entry"
                    and (doc.voucher_no, doc.item_code, doc.warehouse, doc.docstatus) == (*args, 1)]
            if "tabSerial and Batch Entry" in query and "parenttype='Stock Reservation Entry'" in query:
                self.h.queries.append(query)
                doc = self.h.store["Stock Reservation Entry", args[0]]
                return [ordinary.Doc({field: row.get(field) for field in self.source.RESERVATION_ROW_FIELDS})
                        for row in doc.sb_entries]
            return old_sql(query, args, **kwargs)

        self.h.modules["frappe"].db.sql = sql

    def read(self, kind, name):
        return self.h.store[kind, name]

    def source_proof(self, *, posted=False):
        return self.source.read_loss_source(self.doc, read=self.read, records=self.b.records, posted=posted)

    def native_consumed(self, work, item):
        return sum(row.transfer_qty for (kind, _), doc in self.h.store.items() if kind == "Stock Entry"
                   and doc.docstatus == 1 and doc.get("work_order") == work
                   and doc.purpose in ("Manufacture", "Material Consumption for Manufacture")
                   for row in doc.items if row.item_code == item and row.get("s_warehouse") == "WIP")

    def make_reservation(self, name="SRE", qty=10, batch="BATCH", source="TRANSFER", detail="TRANSFER-ROW"):
        row = ordinary.Doc(name=name + "-ROW", parent=name, parenttype="Stock Reservation Entry",
            parentfield="sb_entries", docstatus=1, batch_no=batch, serial_no=None, qty=qty, delivered_qty=0)
        row.db_update = lambda: self.events.append((row.name, "batch", row.delivered_qty))
        doc = ordinary.Doc(doctype="Stock Reservation Entry", name=name, docstatus=1, company="JF", item_code="RM",
            stock_uom="Kg", warehouse="WIP", voucher_type="Work Order", voucher_no="WO", voucher_detail_no="RM-ROW",
            from_voucher_type="Stock Entry", from_voucher_no=source, from_voucher_detail_no=detail,
            reserved_qty=qty, consumed_qty=0, delivered_qty=0, transferred_qty=0, reservation_based_on="Serial and Batch",
            has_batch_no=1, has_serial_no=0, creation=name, sb_entries=[row])
        doc.update_status = lambda: self.events.append((name, "status", doc.consumed_qty))
        doc.update_reserved_stock_in_bin = lambda: self.events.append((name, "bin", doc.consumed_qty))
        self.h.store["Stock Reservation Entry", name] = doc
        self.work.reserve_stock = 1
        return doc

    def post_loss(self, quantity=10):
        self.doc.docstatus = 1
        self.doc.items[0].docstatus = 1
        self.h.store["Stock Entry", self.doc.name] = self.doc
        bundle = self.b.bundle("LOSS-BUNDLE", self.doc.name, self.doc.items[0].name,
                               quantity=-quantity, rate=4, time="10:00:00")
        self.doc.items[0].serial_and_batch_bundle = bundle.name
        sle = self.b.ledger("LOSS-SLE", self.doc.name, self.doc.items[0].name, quantity=-quantity,
                            value=-quantity * 4, bundle=bundle.name, time="10:00:00")
        self.h.ledger[self.doc.name] = [sle]
        return bundle, sle

    def partial_work_loss(self, quantity=7):
        self.card.for_quantity = self.card.process_loss_qty = Decimal(quantity) / 2
        self.doc.jf_production_loss_qty = self.card.process_loss_qty
        self.doc.jf_production_loss_source_signature = self.source.source_signature(self.card, self.work)
        row = self.doc.items[0]
        row.qty = row.transfer_qty = quantity

    def test_batch_source_proposal_keeps_stock_unit_and_does_not_copy_source_bundle(self):
        self.assertEqual(self.source_proof()["lost_qty"], 5)
        with patch.object(self.source, "_authorized_read", self.read), \
             patch.object(self.source, "_authorized_records", self.b.records):
            result = self.source.inspect_loss_issue("JC", ordinary.REQUEST)
        row = result["items"][0]
        self.assertEqual((row["batch_no"], row["qty"], row["conversion_factor"], row["use_serial_batch_fields"]),
                         ("BATCH", 10, 1, 1))
        self.assertNotIn("serial_and_batch_bundle", row)
        self.assertNotIn("source_transfers", row)
        self.assertNotIn("expected_value", row)
        self.assertNotIn("valuation_rate", row)
        with self.assertRaisesRegex(ValueError, "完整库存来源"):
            self.source.read_loss_source(self.doc, read=self.read)
        self.b.item.has_serial_no = 1
        with self.assertRaisesRegex(ValueError, "序号"):
            self.source_proof()

    def test_preledger_submit_releases_exact_batch_before_native_sle_and_cancel_restores(self):
        reservation = self.make_reservation()
        self.doc.docstatus = 1
        self.h.store["Stock Entry", self.doc.name] = self.doc
        progress = self.h.progress_module()
        events = self.events
        class NativeEntry(ordinary.Doc):
            def update_stock_ledger(self, *args, **kwargs):
                events.append(("native_sle", self.docstatus))
        class Entry(progress.ProductionLossStockEntryMixin, NativeEntry):
            pass
        self.assertNotIn("LOSS", self.h.ledger)
        Entry(self.doc).update_stock_ledger()
        self.assertEqual((reservation.consumed_qty, reservation.sb_entries[0].delivered_qty), (10, 10))
        self.assertEqual(self.events[-1], ("native_sle", 1))
        bundle, sle = self.post_loss()
        self.doc.docstatus = 2
        # Native cancellation enters the controller before delinking/canceling
        # the original bundle and SLE. It must exclude that original loss.
        Entry(self.doc).update_stock_ledger()
        self.assertEqual((reservation.consumed_qty, reservation.sb_entries[0].delivered_qty), (0, 0))
        self.assertEqual(self.events[-1], ("native_sle", 2))
        self.assertEqual((self.card.total_completed_qty, self.card.process_loss_qty), (0, 5))

    def test_native_same_batch_duplication_is_replaced_by_one_stable_consumption_pool(self):
        self.partial_work_loss(7)
        first, second = self.make_reservation("SRE-A", 6), self.make_reservation("SRE-B", 4)
        path = ordinary.ROOT.parent / "erpnext/erpnext/stock/doctype/stock_reservation_entry/stock_reservation_entry.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == "consume_serial_batch_for_material_transfer")
        namespace = {"flt": float}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        mapping = {("RM", "WIP"): ordinary.Doc(serial_nos=[], batch_nos={"BATCH": 7})}
        for doc in (first, second):
            namespace[node.name](doc, mapping)
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [7, 7])
        self.events.clear()
        self.doc.docstatus = 1
        self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], Decimal(7),
                                                   "WIP", current_doc=self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [6, 1])
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [6, 1])
        self.assertTrue(all("NOWAIT" in query for query in self.h.queries if "Stock Reservation Entry" in query))

    def test_posted_work_order_tail_and_cancel_restore_batch_net_without_wage_completion(self):
        first, second = self.make_reservation("SRE-A", 6), self.make_reservation("SRE-B", 4)
        progress = self.h.progress_module()
        events = self.events
        class NativeWork(ordinary.Doc):
            def update_consumed_qty_for_required_items(self):
                for item in self.required_items:
                    item.db_set("consumed_qty", 0)
            def update_consumed_qty_in_stock_reservation(self, *args):
                events.append(("native_reservation", args[1]))
        class Work(progress.ProductionLossWorkOrderMixin, NativeWork):
            pass
        work = Work(self.work)
        self.h.store["Work Order", "WO"] = self.work = self.b.work = work
        bundle, sle = self.post_loss()
        work.update_consumed_qty_for_required_items()
        self.assertEqual(work.required_items[0].consumed_qty, 10)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [6, 4])
        self.doc.docstatus = bundle.docstatus = bundle.entries[0].docstatus = 2
        bundle.is_cancelled = bundle.entries[0].is_cancelled = sle.is_cancelled = 1
        self.doc.items[0].serial_and_batch_bundle = ""
        work.update_work_order_qty = lambda: events.append("qty")
        work.update_status = lambda: events.append("work_status")
        work.update_planned_qty = lambda: events.append("plan")
        self.card.set_status = lambda **kwargs: events.append("card_status")
        self.source.update_loss_progress(self.doc)
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [0, 0])
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [0, 0])
        self.assertEqual((self.card.total_completed_qty, self.card.get("manufactured_qty") or 0), (0, 0))

    def test_reservation_wrong_source_or_over_cap_aborts_before_any_reservation_write(self):
        first = self.make_reservation("SRE-A", 6)
        second = self.make_reservation("SRE-B", 5)
        with self.assertRaisesRegex(ValueError, "累计预留"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.events)
        self.assertEqual(first.consumed_qty, 0)
        second.reserved_qty = second.sb_entries[0].qty = 4
        second.from_voucher_detail_no = "WRONG-DETAIL"
        with self.assertRaisesRegex(ValueError, "转料材料来源"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.events)
        second.from_voucher_detail_no = "TRANSFER-ROW"
        second.reservation_based_on = "Qty"
        with self.assertRaisesRegex(ValueError, "预留的类型"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.events)

    def test_reservation_lock_timeout_is_not_retried_or_partly_written(self):
        self.make_reservation()
        self.h.lock_error = TimeoutError
        with self.assertRaisesRegex(TimeoutError, "重新读取"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.events)
        self.assertEqual(sum("tabStock Reservation Entry" in query for query in self.h.queries), 1)

    def test_native_grouped_source_detail_keeps_second_batch_and_rejects_forged_first_pointer(self):
        self.b.transfer.items[0].qty = self.b.transfer.items[0].transfer_qty = 6
        self.b.inward.total_qty = self.b.inward.entries[0].qty = self.b.sle.actual_qty = 6
        self.b.inward.total_amount = self.b.inward.entries[0].stock_value_difference = self.b.sle.stock_value_difference = 24
        second = batches.Doc(self.b.transfer.items[0])
        second.update(name="TRANSFER-SECOND", qty=4, transfer_qty=4)
        self.b.transfer.items.append(second)
        self.b.add("Batch", "SECOND", item="RM", use_batchwise_valuation=0)
        inward = self.b.bundle("SECOND-IN", "TRANSFER", second.name, quantity=4, rate=4, batch="SECOND")
        self.b.ledger("SECOND-SLE", "TRANSFER", second.name, quantity=4, value=16, bundle=inward.name)
        self.doc.items[0].qty = self.doc.items[0].transfer_qty = 6
        loss_second = ordinary.Doc(self.doc.items[0])
        loss_second.update(name="LOSS-SECOND", batch_no="SECOND", qty=4, transfer_qty=4)
        self.doc.items.append(loss_second)
        reservation = self.make_reservation(qty=6)
        reserved_second = ordinary.Doc(reservation.sb_entries[0])
        reserved_second.update(name="SRE-SECOND", batch_no="SECOND", qty=4)
        reserved_second.db_update = lambda: self.events.append((reserved_second.name, "batch", reserved_second.delivered_qty))
        reservation.sb_entries.append(reserved_second)
        reservation.reserved_qty = 10
        self.doc.docstatus = 1
        self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 10, "WIP", current_doc=self.doc)
        self.assertEqual([row.delivered_qty for row in reservation.sb_entries], [6, 4])
        self.events.clear()
        reservation.from_voucher_detail_no = second.name
        with self.assertRaisesRegex(ValueError, "转料材料来源"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 10, "WIP", current_doc=self.doc)
        self.assertFalse(self.events)

    def test_controller_raw_native_projection_never_accepts_hidden_or_masked_batch_sources(self):
        scoped = types.ModuleType("jifeng_erp.flow_cost_scoped_query")
        scoped.record_list = lambda *args, **kwargs: []
        self.h.modules["frappe"].PermissionError = PermissionError
        with patch.dict(sys.modules, {scoped.__name__: scoped}), \
             patch.object(self.source, "_current_records", return_value=[ordinary.Doc(name="BATCH", item="RM")]):
            with self.assertRaises(PermissionError):
                self.source._authorized_records("Batch", {"item": "RM"}, {"name", "item"})
            scoped.record_list = lambda *args, **kwargs: [ordinary.Doc(name="BATCH", item="MASKED")]
            with self.assertRaises(PermissionError):
                self.source._authorized_records("Batch", {"item": "RM"}, {"name", "item"})

    def test_plan_batch_loss_requires_inverse_replacement_cancellation_before_releasing_source(self):
        self.work.update(production_plan="PLAN", production_plan_item="PLAN-ROW")
        plan_row = ordinary.Doc(name="PLAN-ROW", parent="PLAN", parenttype="Production Plan", parentfield="po_items",
            item_code="FG", jf_flow_order="FLOW", bom_no="BOM", stock_uom="Nos", planned_qty=5)
        plan = ordinary.Doc(name="PLAN", docstatus=1, company="JF", status="In Process", po_items=[plan_row])
        self.h.store.update({("Production Plan", "PLAN"): plan, ("Production Plan Item", "PLAN-ROW"): plan_row,
            ("BOM", "BOM"): ordinary.Doc(name="BOM", docstatus=1, company="JF", item="FG", uom="Nos", is_active=1)})
        self.doc.jf_production_loss_source_signature = self.source.source_signature(self.card, self.work)
        self.post_loss()
        self.assertEqual(self.source_proof(posted=True)["lost_qty"], 5)
        replacement = ordinary.Doc(self.work)
        replacement.update(name="REPLACE", docstatus=0, qty=5, produced_qty=0, process_loss_qty=0)
        self.h.store["Work Order", replacement.name] = replacement
        with patch.object(self.source, "_authorized_read", self.read), \
             patch.object(self.source, "_authorized_records", self.b.records):
            with self.assertRaisesRegex(ValueError, "生产计划.*先撤销补产"):
                self.source.before_cancel_loss_entry(self.doc)
            replacement.docstatus = 2
            self.source.before_cancel_loss_entry(self.doc)
        self.assertFalse(self.events)
        self.assertEqual(self.card.total_completed_qty, 0)


if __name__ == "__main__":
    unittest.main()
