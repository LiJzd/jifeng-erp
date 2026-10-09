"""Exact serial/multiple-transfer sources through native loss/SRE callbacks."""

from decimal import Decimal
import unittest
from unittest.mock import patch

import test_production_loss as ordinary
import test_production_loss_batches as batches
import test_production_loss_batches_integration as integration


class SerialLossIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.i = integration.LossBatchIntegrationTests()
        self.i.setUp()
        self.addCleanup(self.i.doCleanups)
        self.h, self.b, self.source = self.i.h, self.i.b, self.i.source
        self.work, self.card, self.doc = self.i.work, self.i.card, self.i.doc
        self.b.item.update(has_serial_no=1, has_batch_no=1, use_serial_no_wise_valuation=1)
        self.numbers = [f"SN-{index:02}" for index in range(1, 11)]
        self.b.inward.has_serial_no = 1
        for row in self.b.inward.entries:
            del self.h.store["Serial and Batch Entry", row.name]
        self.b.inward.entries = []
        self.add_serial_entries(self.b.inward, self.numbers, quantity=1)
        self.doc.items[0].serial_no = "\n".join(self.numbers)
        self.b.transfer.items[0].serial_no = self.doc.items[0].serial_no

    def add_serial_entries(self, bundle, numbers, *, quantity):
        for number in numbers:
            self.b.add("Serial No", number, serial_no=number, item_code="RM", company="JF", warehouse="WIP",
                       status="Active", batch_no="BATCH", purchase_rate=4)
            row = self.b.add("Serial and Batch Entry", bundle.name + "-" + number, parent=bundle.name,
                parenttype="Serial and Batch Bundle", parentfield="entries", docstatus=bundle.docstatus,
                item_code="RM", warehouse="WIP", batch_no="BATCH", serial_no=number, qty=quantity,
                incoming_rate=4, stock_value_difference=quantity * 4, is_cancelled=int(bundle.docstatus == 2))
            bundle.entries.append(row)

    def reserve_serials(self, numbers=None, *, name="SRE", source="TRANSFER", detail="TRANSFER-ROW"):
        numbers = self.numbers if numbers is None else numbers
        doc = self.i.make_reservation(name, len(numbers), source=source, detail=detail)
        doc.has_serial_no = 1
        doc.sb_entries = []
        for number in numbers:
            row = ordinary.Doc(name=name + "-" + number, parent=name, parenttype="Stock Reservation Entry",
                parentfield="sb_entries", docstatus=1, batch_no="BATCH", serial_no=number, qty=1, delivered_qty=0)
            row.db_update = lambda row=row: self.i.events.append((row.name, "serial", row.delivered_qty))
            doc.sb_entries.append(row)
        return doc

    def posted(self, numbers=None):
        numbers = self.numbers if numbers is None else numbers
        bundle, sle = self.i.post_loss(len(numbers))
        bundle.has_serial_no = 1
        for row in bundle.entries:
            del self.h.store["Serial and Batch Entry", row.name]
        bundle.entries = []
        self.add_serial_entries(bundle, numbers, quantity=-1)
        for number in numbers:
            self.h.store["Serial No", number].update(warehouse=None, status="Inactive", purchase_rate=999)
        return bundle, sle

    def test_source_and_proposal_preserve_exact_serial_and_batch_without_old_bundle_or_price_copy(self):
        self.assertEqual(self.i.source_proof()["lost_qty"], 5)
        with patch.object(self.source, "_authorized_read", self.i.read), \
             patch.object(self.source, "_authorized_records", self.b.records):
            result = self.source.inspect_loss_issue("JC", ordinary.REQUEST)
        self.assertEqual(len(result["items"]), 10)
        self.assertEqual({row["serial_no"] for row in result["items"]}, set(self.numbers))
        self.assertTrue(all(row["qty"] == 1 and row["batch_no"] == "BATCH" for row in result["items"]))
        self.assertTrue(all("source_transfers" not in row and "serial_and_batch_bundle" not in row
                            and "expected_value" not in row for row in result["items"]))
        self.doc.items[0].serial_no += "\n" + self.numbers[0]
        with self.assertRaisesRegex(ValueError, "重复"):
            self.i.source_proof()

    def test_preledger_consumes_each_original_serial_once_and_cancel_restores_each(self):
        first = self.reserve_serials(self.numbers[:6], name="SRE-A")
        second = self.reserve_serials(self.numbers[6:], name="SRE-B")
        self.doc.docstatus = 1
        self.h.store["Stock Entry", self.doc.name] = self.doc
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [6, 4])
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [1] * 10)
        bundle, sle = self.posted()
        self.doc.docstatus = 2
        # Cancel enters the real native stock callback before original SLE
        # cancellation. Current serial status is not a historical price source.
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [0, 0])
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [0] * 10)
        self.assertEqual(self.card.total_completed_qty, 0)

    def test_sre_duplicate_serial_wrong_batch_and_fractional_cap_are_rejected_before_writes(self):
        first = self.reserve_serials(name="SRE-A")
        duplicate = self.reserve_serials([self.numbers[0]], name="SRE-B")
        with self.assertRaises(ValueError):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.i.events)
        del self.h.store["Stock Reservation Entry", duplicate.name]
        row = first.sb_entries[0]
        row.batch_no = "WRONG"
        with self.assertRaisesRegex(ValueError, "序号预留"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.i.events)
        row.batch_no, row.qty = "BATCH", Decimal("0.5")
        with self.assertRaisesRegex(ValueError, "序号预留"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.i.events)

    def test_posted_source_uses_historical_sle_not_current_serial_purchase_price_and_cancel_delinks(self):
        bundle, sle = self.posted()
        self.assertEqual(self.i.source_proof(posted=True)["lost_qty"], 5)
        # Cancelled callback remains read-only proof of the original negative
        # SLE even after its bundle has been delinked by native cancellation.
        self.doc.docstatus = bundle.docstatus = 2
        bundle.is_cancelled = sle.is_cancelled = 1
        for row in bundle.entries:
            row.docstatus, row.is_cancelled = 2, 1
        self.doc.items[0].serial_and_batch_bundle = ""
        self.assertEqual(self.i.source_proof()["lost_qty"], 5)
        sle.stock_value_difference = -39
        with self.assertRaises(ValueError):
            self.i.source_proof()

    def test_serial_progress_keeps_wage_completed_zero_and_reuses_exact_existing_request(self):
        reservation = self.reserve_serials()
        progress = self.h.progress_module()
        class NativeWork(ordinary.Doc):
            def update_consumed_qty_for_required_items(self):
                for item in self.required_items:
                    item.db_set("consumed_qty", 0)
            def update_consumed_qty_in_stock_reservation(self, *args):
                pass
        class Work(progress.ProductionLossWorkOrderMixin, NativeWork):
            pass
        work = Work(self.work)
        self.i.work = self.work = self.b.work = work
        self.h.store["Work Order", "WO"] = work
        self.posted()
        work.update_consumed_qty_for_required_items()
        self.assertEqual(work.required_items[0].consumed_qty, 10)
        self.assertEqual(reservation.consumed_qty, 10)
        self.assertEqual(self.card.total_completed_qty, 0)
        with patch.object(self.source, "_authorized_read", self.i.read), \
             patch.object(self.source, "_authorized_records", self.b.records):
            result = self.source.create_loss_issue("JC", "unknown-old", 1, "重复回执", "WIP", ordinary.REQUEST)
        self.assertTrue(result["existing"])
        self.assertEqual(result["stock_entry"], self.doc.name)
        self.assertFalse(self.h.insert_count)

    def test_serials_from_two_transfers_release_only_their_exact_source_reservations(self):
        self.b.transfer.items[0].qty = self.b.transfer.items[0].transfer_qty = 6
        self.b.transfer.items[0].serial_no = "\n".join(self.numbers[:6])
        removed = self.b.inward.entries[6:]
        for row in removed:
            del self.h.store["Serial and Batch Entry", row.name]
        self.b.inward.entries = self.b.inward.entries[:6]
        self.b.inward.total_qty = self.b.sle.actual_qty = 6
        self.b.inward.total_amount = self.b.sle.stock_value_difference = 24
        second_row = batches.Doc(self.b.transfer.items[0])
        second_row.update(name="TRANSFER2-ROW", parent="TRANSFER2", qty=4, transfer_qty=4,
                          serial_no="\n".join(self.numbers[6:]))
        self.b.add("Stock Entry", "TRANSFER2", company="JF", work_order="WO", docstatus=1,
            purpose="Material Transfer for Manufacture", posting_date="2026-10-09", posting_time="09:30:00", items=[second_row])
        inward = self.b.bundle("INWARD2", "TRANSFER2", second_row.name, quantity=4, rate=4)
        for row in inward.entries:
            del self.h.store["Serial and Batch Entry", row.name]
        inward.entries = []
        inward.has_serial_no = 1
        self.add_serial_entries(inward, self.numbers[6:], quantity=1)
        sle = self.b.ledger("SLE-IN2", "TRANSFER2", second_row.name, quantity=4, value=16,
            bundle=inward.name, time="09:30:00")
        self.h.ledger["TRANSFER2"] = [sle]
        first = self.reserve_serials(self.numbers[:6], name="SRE-A")
        second = self.reserve_serials(self.numbers[6:], name="SRE-B", source="TRANSFER2", detail="TRANSFER2-ROW")
        self.doc.docstatus = 1
        self.h.store["Stock Entry", self.doc.name] = self.doc
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [6, 4])
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [1] * 10)
        self.i.events.clear()
        second.from_voucher_no, second.from_voucher_detail_no = "TRANSFER", "TRANSFER-ROW"
        with self.assertRaisesRegex(ValueError, "准确原转料来源"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 10,
                                                       "WIP", current_doc=self.doc)
        self.assertFalse(self.i.events)

    def test_serial_only_item_does_not_invent_batch_and_keeps_exact_reservation_membership(self):
        self.b.item.has_batch_no = self.b.inward.has_batch_no = 0
        self.doc.items[0].batch_no = None
        for row in self.b.inward.entries:
            row.batch_no = None
        for number in self.numbers:
            self.h.store["Serial No", number].batch_no = None
        self.assertEqual(self.i.source_proof()["lost_qty"], 5)
        with patch.object(self.source, "_authorized_read", self.i.read), \
             patch.object(self.source, "_authorized_records", self.b.records):
            result = self.source.inspect_loss_issue("JC", ordinary.REQUEST)
        self.assertTrue(all("batch_no" not in row and row["serial_no"] in self.numbers for row in result["items"]))
        reservation = self.reserve_serials()
        reservation.has_batch_no = 0
        for row in reservation.sb_entries:
            row.batch_no = None
        self.doc.docstatus = 1
        self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 10, "WIP", current_doc=self.doc)
        self.assertEqual(reservation.consumed_qty, 10)
        self.assertTrue(all(row.delivered_qty == 1 for row in reservation.sb_entries))


class MultipleTransferLossTests(unittest.TestCase):
    def setUp(self):
        self.i = integration.LossBatchIntegrationTests()
        self.i.setUp()
        self.addCleanup(self.i.doCleanups)
        self.h, self.b, self.source = self.i.h, self.i.b, self.i.source
        self.work, self.doc = self.i.work, self.i.doc
        self.b.transfer.items[0].qty = self.b.transfer.items[0].transfer_qty = 6
        self.b.inward.total_qty = self.b.inward.entries[0].qty = self.b.sle.actual_qty = 6
        self.b.inward.total_amount = self.b.inward.entries[0].stock_value_difference = self.b.sle.stock_value_difference = 24
        second_row = batches.Doc(self.b.transfer.items[0])
        second_row.update(name="TRANSFER2-ROW", parent="TRANSFER2", qty=4, transfer_qty=4)
        self.transfer2 = self.b.add("Stock Entry", "TRANSFER2", company="JF", work_order="WO", docstatus=1,
            purpose="Material Transfer for Manufacture", posting_date="2026-10-09", posting_time="09:30:00", items=[second_row])
        inward = self.b.bundle("INWARD2", "TRANSFER2", second_row.name, quantity=4, rate=4)
        self.sle2 = self.b.ledger("SLE-IN2", "TRANSFER2", second_row.name, quantity=4, value=16,
            bundle=inward.name, time="09:30:00")
        self.h.ledger["TRANSFER2"] = [self.sle2]

    def test_two_exact_transfers_share_one_batch_consumption_and_cancel_net_without_borrowing_other_header(self):
        self.i.partial_work_loss(7)
        first = self.i.make_reservation("SRE-A", 6)
        second = self.i.make_reservation("SRE-B", 4, source="TRANSFER2", detail="TRANSFER2-ROW")
        self.doc.docstatus = 1
        self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 7, "WIP", current_doc=self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [6, 1])
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [6, 1])
        bundle, sle = self.i.post_loss(7)
        self.assertEqual(self.i.source_proof(posted=True)["lost_qty"], Decimal("3.5"))
        self.doc.docstatus = 2
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [0, 0])
        self.assertEqual([row.delivered_qty for doc in (first, second) for row in doc.sb_entries], [0, 0])

    def test_each_original_transfer_header_cap_is_checked_before_any_write(self):
        first = self.i.make_reservation("SRE-A", 7)
        second = self.i.make_reservation("SRE-B", 3, source="TRANSFER2", detail="TRANSFER2-ROW")
        with self.assertRaisesRegex(ValueError, "准确原转料数量"):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.i.events)
        first.reserved_qty = first.sb_entries[0].qty = 6
        second.reserved_qty = second.sb_entries[0].qty = 4
        self.transfer2.work_order = "OTHER"
        with self.assertRaises(ValueError):
            self.source.update_batch_loss_reservations(self.work, self.work.required_items[0], 0, "WIP")
        self.assertFalse(self.i.events)


if __name__ == "__main__":
    unittest.main()
