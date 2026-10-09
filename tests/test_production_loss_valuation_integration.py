"""Native policy current reads and priced-lot loss/SRE integration, no DB writes."""

from decimal import Decimal
from unittest.mock import patch
import unittest

import test_production_loss as ordinary
import test_production_loss_batches as batches
import test_production_loss_batches_integration as integration


class SingleValuationSourceTests(unittest.TestCase):
    def setUp(self):
        self.h = ordinary.ProductionLossTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.source, self.frappe = self.h.module, self.h.modules["frappe"]
        self.source.PARENTS.setdefault("Stock Settings", {"name", "modified", "do_not_use_batchwise_valuation"})
        self.doc = ordinary.Doc(doctype="Stock Settings", name="Stock Settings", docstatus=0,
            modified="2026-10-09 09:00:00", do_not_use_batchwise_valuation=0)
        self.raw = {"modified": self.doc.modified, "do_not_use_batchwise_valuation": "0"}
        self.events = []
        self.frappe.get_meta = lambda kind: ordinary.Doc(issingle=kind == "Stock Settings")

        def sql(query, args, **kwargs):
            self.events.append(("sql", query, args))
            if self.h.lock_error:
                raise self.h.lock_error("policy locked")
            self.assertIn("`tabSingles`", query)
            self.assertIn("FOR UPDATE NOWAIT", query)
            self.assertEqual(args, ("Stock Settings",))
            return [ordinary.Doc(field=field, value=value) for field, value in self.raw.items()]

        def get_doc(kind, name, **kwargs):
            self.events.append(("doc", kind, name, kwargs))
            return self.doc

        def singles(kind, **kwargs):
            self.events.append(("singles", kind, kwargs))
            return dict(self.raw)

        self.frappe.db.sql, self.frappe.db.get_singles_dict = sql, singles
        self.frappe.get_doc = get_doc

    def test_native_single_current_lock_and_load_preserve_default_when_flag_unstored(self):
        result = self.source._authorized_read("Stock Settings", "Stock Settings")
        self.assertIs(result, self.doc)
        self.assertEqual([event[0] for event in self.events], ["sql", "doc", "singles"])
        self.assertTrue(self.events[1][3]["for_update"])
        self.assertTrue(self.events[2][2]["for_update"])
        self.h.modules["jifeng_erp.flow_quantity"].check_quantity_fields.assert_called_with(
            "Stock Settings", self.source.PARENTS["Stock Settings"])
        self.raw.pop("do_not_use_batchwise_valuation")
        self.assertEqual(self.source._authorized_read("Stock Settings", "Stock Settings").do_not_use_batchwise_valuation, 0)
        self.assertNotIn("do_not_use_batchwise_valuation", self.raw)
        self.assertFalse(any("tabStock Settings" in str(event) for event in self.events))

    def test_single_name_list_and_duplicate_raw_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "单例来源"):
            self.source._lock_source("Stock Settings", "CLIENT")
        self.assertFalse(self.events)
        with self.assertRaisesRegex(ValueError, "不能使用单据列表"):
            self.source._current_records("Stock Settings", {"name": "Stock Settings"}, {"name"})
        with patch.object(self.source, "_sql", return_value=[ordinary.Doc(field="flag", value="0"),
                                                           ordinary.Doc(field="flag", value="1")]):
            with self.assertRaisesRegex(ValueError, "重复字段"):
                self.source._lock_source("Stock Settings", "Stock Settings")
        self.assertFalse(self.events)

    def test_single_current_change_or_native_identity_mismatch_is_not_guessed(self):
        self.frappe.db.get_singles_dict = lambda *args, **kwargs: dict(self.raw, do_not_use_batchwise_valuation="1")
        with self.assertRaisesRegex(RuntimeError, "已变化"):
            self.source._authorized_read("Stock Settings", "Stock Settings")
        self.frappe.db.get_singles_dict = lambda *args, **kwargs: dict(self.raw)
        self.doc.name = "OTHER"
        with self.assertRaisesRegex(RuntimeError, "已变化"):
            self.source._authorized_read("Stock Settings", "Stock Settings")

    def test_single_busy_and_permissions_do_not_retry_or_return_policy(self):
        for error in (TimeoutError, ConnectionAbortedError):
            self.events.clear()
            self.h.lock_error = error
            with self.assertRaisesRegex(error, "重新读取"):
                self.source._authorized_read("Stock Settings", "Stock Settings")
            self.assertEqual(len(self.events), 1)
        self.h.lock_error = None
        self.doc.denied = ("read",)
        with self.assertRaises(PermissionError):
            self.source._authorized_read("Stock Settings", "Stock Settings")
        self.doc.denied = ()
        self.h.modules["jifeng_erp.flow_quantity"].check_quantity_fields.side_effect = PermissionError("masked policy")
        with self.assertRaisesRegex(PermissionError, "masked"):
            self.source._authorized_read("Stock Settings", "Stock Settings")
        self.assertFalse(self.h.insert_count)


class PricedLossReservationTests(unittest.TestCase):
    def setUp(self):
        self.i = integration.LossBatchIntegrationTests()
        self.i.setUp()
        self.addCleanup(self.i.doCleanups)
        self.h, self.b, self.source = self.i.h, self.i.b, self.i.source
        self.work, self.doc = self.i.work, self.i.doc
        self.b.batch.use_batchwise_valuation = 1
        self.b.item.valuation_method = ""
        self.b.add("Company", "JF", valuation_method="Moving Average")
        self.b.add("Stock Settings", "Stock Settings", docstatus=0, do_not_use_batchwise_valuation=0)
        self.b.transfer.items[0].qty = self.b.transfer.items[0].transfer_qty = 6
        self.b.inward.total_qty = self.b.inward.entries[0].qty = self.b.sle.actual_qty = 6
        self.b.inward.total_amount = self.b.inward.entries[0].stock_value_difference = self.b.sle.stock_value_difference = 24
        second = batches.Doc(self.b.transfer.items[0])
        second.update(name="TRANSFER2-ROW", parent="TRANSFER2", qty=4, transfer_qty=4)
        self.transfer2 = self.b.add("Stock Entry", "TRANSFER2", company="JF", work_order="WO", docstatus=1,
            purpose="Material Transfer for Manufacture", posting_date="2026-10-09", posting_time="09:30:00", items=[second])
        self.inward2 = self.b.bundle("INWARD2", self.transfer2.name, second.name, quantity=4, rate=9, time="09:30:00")
        self.sle2 = self.b.ledger("SLE-IN2", self.transfer2.name, second.name, quantity=4, value=36,
            bundle=self.inward2.name, time="09:30:00")
        self.h.ledger[self.transfer2.name] = [self.sle2]

    def different_batches(self):
        self.b.add("Batch", "SECOND", item="RM", use_batchwise_valuation=1)
        self.inward2.entries[0].batch_no = "SECOND"
        self.doc.items[0].qty = self.doc.items[0].transfer_qty = 6
        second = ordinary.Doc(self.doc.items[0])
        second.update(name="LOSS-SECOND", batch_no="SECOND", qty=4, transfer_qty=4)
        self.doc.items.append(second)

    def post_rows(self, rates):
        self.doc.docstatus = 1
        self.h.store["Stock Entry", self.doc.name] = self.doc
        ledger = []
        for row, rate in zip(self.doc.items, rates, strict=True):
            row.docstatus = 1
            bundle = self.b.bundle("OUT-" + row.name, self.doc.name, row.name, quantity=-row.transfer_qty,
                rate=rate, batch=row.batch_no, time=self.doc.posting_time)
            row.serial_and_batch_bundle = bundle.name
            ledger.append(self.b.ledger("SLE-" + row.name, self.doc.name, row.name, quantity=-row.transfer_qty,
                value=-Decimal(str(row.transfer_qty)) * Decimal(str(rate)), bundle=bundle.name, time=self.doc.posting_time))
        self.h.ledger[self.doc.name] = ledger
        return ledger

    def cancel_rows(self):
        self.doc.docstatus = 2
        for row in self.doc.items:
            bundle = self.h.store["Serial and Batch Bundle", row.serial_and_batch_bundle]
            bundle.docstatus, bundle.is_cancelled = 2, 1
            for child in bundle.entries:
                child.docstatus, child.is_cancelled = 2, 1
            row.docstatus, row.serial_and_batch_bundle = 2, ""
        for sle in self.h.ledger[self.doc.name]:
            sle.is_cancelled = 1

    def test_different_batch_values_keep_quantities_and_native_reservation_cancel_net(self):
        self.different_batches()
        first = self.i.make_reservation("SRE-A", 6)
        second = self.i.make_reservation("SRE-B", 4, batch="SECOND", source="TRANSFER2", detail="TRANSFER2-ROW")
        with patch.object(self.source, "_authorized_read", self.i.read), \
             patch.object(self.source, "_authorized_records", self.b.records):
            response = self.source.inspect_loss_issue("JC", ordinary.REQUEST)
        self.assertEqual([(row["batch_no"], row["qty"]) for row in response["items"]], [("BATCH", 6), ("SECOND", 4)])
        self.assertTrue(all("expected_value" not in row and "valuation_rate" not in row for row in response["items"]))
        self.doc.docstatus = 1
        self.h.store["Stock Entry", self.doc.name] = self.doc
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [6, 4])
        ledger = self.post_rows([4, 9])
        self.assertEqual(sum(-Decimal(str(sle.stock_value_difference)) for sle in ledger), 60)
        self.assertEqual(self.i.source_proof(posted=True)["lost_qty"], 5)
        proof = self.b.proof(posted=True)
        self.assertEqual([proof[row.name]["value"] for row in self.doc.items], [24, 36])
        self.cancel_rows()
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [0, 0])
        self.assertEqual([doc.sb_entries[0].delivered_qty for doc in (first, second)], [0, 0])
        self.assertEqual(self.i.card.total_completed_qty, 0)

    def test_same_batch_prior_actual_value_changes_mean_but_cancel_uses_recorded_value(self):
        # Before the second transfer, another actor consumed 2 @ 4. Its exact
        # value is 8, leaving (24 - 8 + 36) / (6 - 2 + 4) = 6.5, not 6.
        other_row = batches.Doc(self.doc.items[0])
        other_row.update(name="OTHER-ROW", parent="OTHER", parenttype="Stock Entry", parentfield="items", docstatus=1,
                         qty=2, transfer_qty=2)
        other = self.b.add("Stock Entry", "OTHER", company="JF", work_order="OTHER-WO", docstatus=1,
            purpose="Material Consumption for Manufacture", posting_date="2026-10-09", posting_time="09:15:00", items=[other_row])
        other_bundle = self.b.bundle("OTHER-OUT", other.name, other_row.name, quantity=-2, rate=4, time=other.posting_time)
        other_sle = self.b.ledger("OTHER-SLE", other.name, other_row.name, quantity=-2, value=-8,
            bundle=other_bundle.name, time=other.posting_time)
        self.i.partial_work_loss(7)
        first = self.i.make_reservation("SRE-A", 6)
        second = self.i.make_reservation("SRE-B", 4, source="TRANSFER2", detail="TRANSFER2-ROW")
        self.doc.docstatus = 1
        self.h.store["Stock Entry", self.doc.name] = self.doc
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [6, 1])
        self.post_rows([Decimal("6.5")])
        self.assertEqual(self.b.proof(posted=True)[self.doc.items[0].name]["value"], Decimal("45.5"))
        self.assertEqual(self.i.source_proof(posted=True)["lost_qty"], Decimal("3.5"))
        # Later cancellation of the other actor's older outflow changes the
        # current net value. It cannot revalue this loss's historical inverse.
        other.docstatus = 2
        other_sle.is_cancelled = 1
        other_bundle.docstatus, other_bundle.is_cancelled = 2, 1
        for row in other_bundle.entries:
            row.docstatus, row.is_cancelled = 2, 1
        self.cancel_rows()
        proof = self.b.proof()
        self.assertEqual(proof[self.doc.items[0].name]["value"], Decimal("45.5"))
        self.source.prepare_loss_stock_reservations(self.doc)
        self.assertEqual([doc.consumed_qty for doc in (first, second)], [0, 0])
        self.assertEqual(self.i.card.total_completed_qty, 0)

    def test_inspection_cutoff_cannot_offer_future_original_transfer(self):
        self.transfer2.posting_time = self.sle2.posting_time = "11:00:00"
        self.sle2.creation = "2026-10-09 11:00:00"
        for child in self.inward2.entries:
            child.posting_datetime = "2026-10-09 11:00:00"
        with patch.object(self.source, "_authorized_read", self.i.read), \
             patch.object(self.source, "_authorized_records", self.b.records), \
             patch.object(self.b.module, "proposal", wraps=self.b.module.proposal) as proposal:
            with self.assertRaisesRegex(ValueError, "库存不足"):
                self.source.inspect_loss_issue("JC", ordinary.REQUEST)
        self.assertEqual(str(proposal.call_args.kwargs["posting_date"]), "2026-10-09")
        self.assertEqual(str(proposal.call_args.kwargs["posting_time"]), "10:00:00")
        self.assertFalse(self.h.insert_count)
        self.assertFalse(self.i.events)


if __name__ == "__main__":
    unittest.main()
