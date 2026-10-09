"""Exact-WO batch WIP quantities/value; callbacks only, no business DB writes."""

from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path
import json
import sys
import textwrap
import types
import unittest
from unittest.mock import patch


class Doc(dict):
    def __getattribute__(self, name):
        if name not in ("__class__", "__dict__") and dict.__contains__(self, name):
            return self[name]
        return super().__getattribute__(name)

    def __getattr__(self, name):
        return self.get(name)

    __setattr__ = dict.__setitem__

    def precision(self, field):
        return 2 if field in {"amount", "basic_amount", "stock_value_difference"} else 9

    def check_permission(self, permission):
        if self.get("denied"):
            raise PermissionError("source denied")


class BatchLossTests(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        utils = types.ModuleType("frappe.utils")
        utils.flt = lambda value, precision=None: round(float(value or 0), precision) if precision is not None else float(value or 0)
        modules = patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils})
        modules.start()
        self.addCleanup(modules.stop)
        # Use the actual pure ERPNext queue class, with no DB/model harness.
        native_queue = Path(__file__).parents[2] / "erpnext/erpnext/stock/valuation.py"
        if native_queue.exists():
            valuation = types.ModuleType("erpnext.stock.valuation")
            exec(compile(native_queue.read_text(encoding="utf-8"), str(native_queue), "exec"), valuation.__dict__)
            queue_modules = patch.dict(sys.modules, {"erpnext.stock.valuation": valuation})
            queue_modules.start()
            self.addCleanup(queue_modules.stop)
        source = Path(__file__).parents[1] / "jifeng_erp/production_loss_batches.py"
        spec = importlib.util.spec_from_file_location("batch_loss_source_under_test", source)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.store, self.queries = {}, []
        self.work = Doc(name="WO", company="JF", wip_warehouse="WIP", required_items=[Doc(item_code="RM")])
        self.item = self.add("Item", "RM", stock_uom="Kg", has_batch_no=1, has_serial_no=0, is_stock_item=1)
        self.company = self.add("Company", "JF", valuation_method="FIFO")
        self.settings = self.add("Stock Settings", "Stock Settings", docstatus=0, do_not_use_batchwise_valuation=0)
        self.batch = self.add("Batch", "BATCH", item="RM", disabled=0, use_batchwise_valuation=0)
        row = Doc(name="TRANSFER-ROW", parent="TRANSFER", parenttype="Stock Entry", parentfield="items", docstatus=1,
                  item_code="RM", stock_uom="Kg", qty=10, uom="Kg", conversion_factor=1, transfer_qty=10,
                  s_warehouse="RAW", t_warehouse="WIP", serial_and_batch_bundle="SOURCE-OUTWARD", idx=1)
        self.transfer = self.add("Stock Entry", "TRANSFER", company="JF", work_order="WO", docstatus=1,
            purpose="Material Transfer for Manufacture", posting_date="2026-10-09", posting_time="09:00:00", items=[row])
        self.inward = self.bundle("INWARD", "TRANSFER", "TRANSFER-ROW", quantity=10, rate=4)
        self.sle = self.ledger("SLE-IN", "TRANSFER", "TRANSFER-ROW", quantity=10, value=40, bundle="INWARD")
        self.loss = Doc(name="LOSS", company="JF", docstatus=0, posting_date="2026-10-09", posting_time="10:00:00", items=[])

    def add(self, kind, name, **values):
        doc = Doc(doctype=kind, name=name, modified="v1", docstatus=1,
                  **{key: value for key, value in values.items() if key != "docstatus"})
        doc.docstatus = values.get("docstatus", 1)
        self.store[kind, name] = doc
        return doc

    def bundle(self, name, voucher, detail, *, quantity, rate, batch="BATCH", status=1, time="09:00:00"):
        doc = self.add("Serial and Batch Bundle", name, company="JF", item_code="RM", warehouse="WIP", voucher_type="Stock Entry",
            voucher_no=voucher, voucher_detail_no=detail, type_of_transaction="Inward" if quantity > 0 else "Outward",
            is_cancelled=int(status == 2), docstatus=status, has_batch_no=1, has_serial_no=0,
            total_qty=quantity, total_amount=quantity * rate, posting_datetime="2026-10-09 " + time)
        row = self.add("Serial and Batch Entry", name + "-ROW", parent=name, parenttype="Serial and Batch Bundle", parentfield="entries",
            docstatus=status, item_code="RM", warehouse="WIP", batch_no=batch, serial_no=None, qty=quantity,
            incoming_rate=rate, stock_value_difference=quantity * rate, is_cancelled=int(status == 2),
            voucher_type="Stock Entry", voucher_no=voucher, voucher_detail_no=detail,
            type_of_transaction=doc.type_of_transaction, posting_datetime="2026-10-09 " + time,
            creation="2026-10-09 " + time, idx=1)
        doc.entries = [row]
        return doc

    def ledger(self, name, voucher, detail, *, quantity, value, bundle=None, batch=None, time="09:00:00", cancelled=0, warehouse="WIP"):
        prior = [row for (kind, _name), row in self.store.items() if kind == "Stock Ledger Entry"
                 and row.warehouse == warehouse and not row.is_cancelled
                 and row.posting_time <= time]
        before_qty = sum(row.actual_qty for row in prior)
        before_value = sum(row.stock_value_difference for row in prior)
        before_queue = json.loads(max(prior, key=lambda row: (row.posting_time, row.creation, row.name)).stock_queue) if prior else []
        from erpnext.stock.valuation import FIFOValuation
        fifo = FIFOValuation(before_queue)
        if quantity > 0:
            fifo.add_stock(float(quantity), float(value / quantity))
        else:
            fifo.remove_stock(float(-quantity))
        return self.add("Stock Ledger Entry", name, company="JF", item_code="RM", stock_uom="Kg", warehouse=warehouse,
            voucher_type="Stock Entry", voucher_no=voucher, voucher_detail_no=detail, actual_qty=quantity, stock_value_difference=value,
            serial_and_batch_bundle=bundle, batch_no=batch, serial_no=None, is_cancelled=cancelled, is_adjustment_entry=0,
            posting_date="2026-10-09", posting_time=time, creation="2026-10-09 " + time,
            posting_datetime="2026-10-09 " + time, stock_queue=json.dumps(fifo.state),
            qty_after_transaction=before_qty + quantity, stock_value=before_value + value,
            valuation_rate=(before_value + value) / (before_qty + quantity) if before_qty + quantity else 0)

    def read(self, kind, name):
        document = self.store[kind, name]
        document.check_permission("read")
        return document

    def records(self, kind, filters, fields, parenttype=None):
        self.queries.append((kind, deepcopy(filters), set(fields), parenttype))
        result = []
        for (doctype, _), doc in self.store.items():
            if doctype != kind:
                continue
            matched = True
            for field, value in filters.items():
                actual = doc.get(field)
                if isinstance(value, list):
                    operator, expected = value
                    matched = matched and ({"in": lambda: actual in expected, "not in": lambda: actual not in expected,
                        "<": lambda: actual < expected, "<=": lambda: str(actual) <= str(expected)}[operator]())
                else:
                    matched = matched and actual == value
            if matched:
                result.append(Doc({field: doc.get(field) for field in fields}))
        return sorted(result, key=lambda row: row.name)

    def row(self, name="LOSS-ROW", qty=10, batch="BATCH", bundle=None):
        row = Doc(name=name, item_code="RM", batch_no=batch, qty=qty, transfer_qty=qty, stock_uom="Kg", uom="Kg", conversion_factor=1,
                  s_warehouse="WIP", t_warehouse=None, serial_and_batch_bundle=bundle, serial_no=None, idx=len(self.loss.items) + 1)
        self.loss.items.append(row)
        return row

    def proposal(self, quantity=10):
        return self.module.proposal(self.work, {"RM": quantity}, read=self.read, records=self.records)

    def proof(self, *, posted=False):
        return self.module.prove_loss_batches(self.loss, self.work, read=self.read, records=self.records, posted=posted)

    def posted(self, qty=10):
        row = self.row(qty=qty, bundle="LOSS-BUNDLE")
        self.loss.docstatus = 1
        bundle = self.bundle("LOSS-BUNDLE", "LOSS", row.name, quantity=-qty, rate=4, time="10:00:00")
        sle = self.ledger("SLE-LOSS", "LOSS", row.name, quantity=-qty, value=-qty * 4, bundle=bundle.name, time="10:00:00")
        return row, bundle, sle

    def second_transfer(self, quantity=5, rate=4):
        row = Doc(self.transfer.items[0])
        row.update(name="SECOND-ROW", parent="SECOND-TRANSFER", qty=quantity, transfer_qty=quantity)
        doc = self.add("Stock Entry", "SECOND-TRANSFER", company="JF", work_order="WO", docstatus=1,
            purpose="Material Transfer for Manufacture", posting_date="2026-10-09", posting_time="09:30:00", items=[row])
        bundle = self.bundle("SECOND-IN", doc.name, row.name, quantity=quantity, rate=rate, time=doc.posting_time)
        ledger = self.ledger("SECOND-SLE", doc.name, row.name, quantity=quantity, value=quantity * rate,
                             bundle=bundle.name, time=doc.posting_time)
        return doc, bundle, ledger

    def serial_bundle(self, name, voucher, detail, rates, *, direction=1, status=1, batch=None, time=None):
        time = time or ("10:00:00" if voucher == "LOSS" else "09:00:00")
        doc = self.bundle(name, voucher, detail, quantity=len(rates) * direction, rate=0, status=status, time=time)
        self.store.pop(("Serial and Batch Entry", name + "-ROW"))
        doc.update(has_serial_no=1, has_batch_no=int(batch is not None), total_amount=direction * sum(rates.values()))
        doc.entries = []
        for number, rate in rates.items():
            doc.entries.append(self.add("Serial and Batch Entry", name + "-" + number, parent=name,
                parenttype="Serial and Batch Bundle", parentfield="entries", docstatus=status, item_code="RM",
                warehouse="WIP", serial_no=number, batch_no=batch, qty=direction, incoming_rate=rate,
                stock_value_difference=direction * rate, is_cancelled=int(status == 2),
                voucher_type="Stock Entry", voucher_no=voucher, voucher_detail_no=detail,
                type_of_transaction=doc.type_of_transaction, posting_datetime="2026-10-09 " + time,
                creation="2026-10-09 " + time, idx=len(doc.entries) + 1))
        return doc

    def serial_source(self, rates=None, *, both=False, serial_valuation=1):
        rates = rates or {"SN-1": 4, "SN-2": 9}
        self.item.update(has_serial_no=1, has_batch_no=int(both), use_serial_no_wise_valuation=serial_valuation)
        batch = "BATCH" if both else None
        for number in rates:
            self.add("Serial No", number, docstatus=0, item_code="RM", company="JF", warehouse="WIP",
                     status="Active", batch_no=batch)
        self.transfer.items[0].update(qty=len(rates), transfer_qty=len(rates), serial_no="\n".join(rates), batch_no=batch)
        self.inward = self.serial_bundle("INWARD", "TRANSFER", "TRANSFER-ROW", rates, batch=batch)
        self.sle.update(actual_qty=len(rates), stock_value_difference=sum(rates.values()), serial_no="\n".join(rates))
        return rates

    def serial_loss(self, rates, *, status=1, both=False):
        batch = "BATCH" if both else None
        row = self.row(qty=len(rates), batch=batch, bundle="LOSS-BUNDLE")
        row.update(serial_no="\n".join(rates), parent="LOSS", parenttype="Stock Entry", parentfield="items")
        self.loss.update(docstatus=status, doctype="Stock Entry", purpose="Material Issue", jf_production_loss_work_order="WO")
        self.store["Stock Entry", "LOSS"] = self.loss
        bundle = self.serial_bundle("LOSS-BUNDLE", "LOSS", row.name, rates, direction=-1, status=status, batch=batch)
        ledger = self.ledger("SLE-LOSS", "LOSS", row.name, quantity=-len(rates), value=-sum(rates.values()),
                             bundle=bundle.name, time="10:00:00", cancelled=int(status == 2))
        if status:
            for number in rates:
                self.store["Serial No", number].update(status="Consumed", warehouse=None)
        return row, bundle, ledger

    def test_exact_inward_sle_bundle_proposal_and_native_draft_selection(self):
        proposal = self.proposal()[0]
        self.assertEqual((proposal["batch_no"], proposal["qty"], proposal["expected_value"]), ("BATCH", 10, 40))
        self.assertEqual(proposal["source_transfers"], [{"stock_entry": "TRANSFER", "detail": "TRANSFER-ROW",
                                                      "qty": 10, "original_qty": 10}])
        # Stock Entry Detail points to the source Outward bundle; WIP provenance
        # must follow the actual target SLE's independent Inward bundle.
        self.assertNotIn(("Serial and Batch Bundle", "SOURCE-OUTWARD"), self.store)
        row = self.row(bundle="DRAFT")
        self.bundle("DRAFT", "LOSS", row.name, quantity=-10, rate=999, status=0)
        self.assertEqual(self.proof()[row.name]["value"], 40)

    def test_wrong_batch_work_warehouse_parent_and_missing_transfer_sle_rejected(self):
        row = self.row()
        for doc, field, value in ((row, "batch_no", "FOREIGN"), (self.transfer, "work_order", "OTHER"),
                (self.inward, "warehouse", "OTHER"), (self.inward, "voucher_detail_no", "OTHER"),
                (self.transfer.items[0], "parentfield", "other"), (self.sle, "warehouse", "OTHER")):
            self.add("Batch", "FOREIGN", item="RM")
            original = doc.get(field)
            doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.proof()
            doc[field] = original
        self.assertEqual(self.loss.docstatus, 0)

    def test_other_consumption_return_and_cancel_release_actual_batch_pool(self):
        self.bundle("USE", "CONSUMPTION", "USE-ROW", quantity=-3, rate=4)
        used = self.ledger("SLE-USE", "CONSUMPTION", "USE-ROW", quantity=-3, value=-12, bundle="USE")
        self.assertEqual(self.proposal(7)[0]["qty"], 7)
        with self.assertRaisesRegex(ValueError, "库存不足"): self.proposal(8)
        # Native return transfer out of WIP is another negative movement.
        returned = self.ledger("SLE-RETURN", "RETURN", "RETURN-ROW", quantity=-2, value=-8, batch="BATCH")
        self.assertEqual(self.proposal(5)[0]["qty"], 5)
        used.is_cancelled = returned.is_cancelled = 1
        self.assertEqual(self.proposal()[0]["qty"], 10)

    def test_two_loss_rows_cannot_each_reuse_whole_batch_quantity(self):
        self.row(name="ROW-1", qty=6)
        self.row(name="ROW-2", qty=5)
        with self.assertRaisesRegex(ValueError, "累计出库"): self.proof()

    def test_other_work_inward_same_batch_is_not_borrowed(self):
        self.bundle("OTHER-IN", "OTHER-TRANSFER", "OTHER-ROW", quantity=2, rate=4)
        self.ledger("OTHER-SLE", "OTHER-TRANSFER", "OTHER-ROW", quantity=2, value=8, bundle="OTHER-IN")
        with self.assertRaisesRegex(ValueError, "混有其他来源"): self.proposal()

    def test_flag_zero_other_batch_price_uses_exact_fifo_queue(self):
        self.assertEqual(self.batch.use_batchwise_valuation, 0)
        self.assertEqual(self.proposal()[0]["expected_value"], 40)
        self.add("Batch", "OTHER-BATCH", item="RM", use_batchwise_valuation=1)
        self.bundle("OTHER-IN", "OTHER", "OTHER-ROW", quantity=2, rate=9, batch="OTHER-BATCH", time="09:30:00")
        self.ledger("OTHER-SLE", "OTHER", "OTHER-ROW", quantity=2, value=18, bundle="OTHER-IN", time="09:30:00")
        self.assertEqual(self.proposal()[0]["expected_value"], 40)

    def test_posted_real_lots_quantity_value_and_future_price_do_not_revalue_history(self):
        row, bundle, sle = self.posted()
        self.assertEqual(self.proof(posted=True)[row.name]["value"], 40)
        self.add("Batch", "FUTURE", item="RM")
        self.bundle("FUTURE-IN", "FUTURE", "FUTURE-ROW", quantity=2, rate=9, batch="FUTURE")
        self.ledger("FUTURE-SLE", "FUTURE", "FUTURE-ROW", quantity=2, value=18, bundle="FUTURE-IN", time="11:00:00")
        self.assertEqual(self.proof(posted=True)[row.name]["value"], 40)
        sle.stock_value_difference = -39
        with self.assertRaisesRegex(ValueError, "价值与确切库存账"): self.proof(posted=True)

    def test_cancelled_delinked_original_proves_origin_without_current_average(self):
        row, bundle, sle = self.posted()
        self.loss.docstatus = bundle.docstatus = bundle.entries[0].docstatus = 2
        bundle.is_cancelled = bundle.entries[0].is_cancelled = sle.is_cancelled = 1
        row.serial_and_batch_bundle = ""
        self.add("Batch", "NEW-PRICE", item="RM")
        self.ledger("FUTURE-SLE", "OTHER", "OTHER-ROW", quantity=2, value=99, batch="NEW-PRICE", time="11:00:00")
        self.assertEqual(self.proof()[row.name]["value"], 40)
        self.assertFalse(any(kind == "Stock Ledger Entry" and filters.get("item_code") == "RM"
            and "voucher_no" not in filters for kind, filters, _fields, _parent in self.queries))
        row.batch_no = "NEW-PRICE"
        with self.assertRaisesRegex(ValueError, "所选批号"): self.proof()

    def test_hidden_batch_sle_transfer_or_child_sources_cannot_be_omitted(self):
        for doc in (self.batch, self.sle, self.transfer):
            doc.denied = True
            with self.subTest(kind=doc.doctype), self.assertRaises(PermissionError): self.proposal()
            doc.denied = False
        original = self.records
        def hidden(kind, filters, fields, **kwargs):
            result = original(kind, filters, fields, **kwargs)
            return [] if kind == "Serial and Batch Entry" else result
        with patch.object(self, "records", hidden), self.assertRaisesRegex(ValueError, "不完整"): self.proposal()

    def test_read_change_and_lock_errors_abort_without_retry(self):
        original = self.records
        def changed(kind, filters, fields, **kwargs):
            result = original(kind, filters, fields, **kwargs)
            if kind == "Stock Ledger Entry": self.sle.stock_value_difference = 39
            return result
        with patch.object(self, "records", changed), self.assertRaisesRegex(ValueError, "本次读取期间已变化"): self.proposal()
        calls = []
        def locked(*args, **kwargs):
            calls.append(args)
            raise TimeoutError("NOWAIT occupied")
        with patch.object(self, "records", locked), self.assertRaises(TimeoutError): self.proposal()
        self.assertEqual(len(calls), 1)

    def test_same_transfer_multiple_original_rows_share_exact_same_price_pool(self):
        row = Doc(self.transfer.items[0])
        row.name = "SECOND-ROW"
        self.transfer.items.append(row)
        self.bundle("SECOND-IN", "TRANSFER", "SECOND-ROW", quantity=10, rate=4)
        self.ledger("SECOND-SLE", "TRANSFER", "SECOND-ROW", quantity=10, value=40, bundle="SECOND-IN")
        proposed = self.proposal(15)[0]
        self.assertEqual((proposed["qty"], proposed["expected_value"]), (15, 60))
        self.assertEqual({(entry["stock_entry"], entry["detail"]): entry["original_qty"]
                          for entry in proposed["source_transfers"]},
                         {("TRANSFER", "TRANSFER-ROW"): 10, ("TRANSFER", "SECOND-ROW"): 10})

    def test_nonbatch_material_path_remains_empty_and_zero_real_value_is_supported(self):
        self.item.has_batch_no = 0
        self.assertEqual(self.proposal(), [])
        self.row()
        self.assertEqual(self.proof(), {})
        self.item.has_batch_no = 1
        self.sle.stock_value_difference = self.inward.total_amount = self.inward.entries[0].incoming_rate = self.inward.entries[0].stock_value_difference = 0
        proposed = self.proposal()[0]
        self.assertEqual((proposed["expected_value"], proposed["allow_zero_valuation_rate"]), (0, 1))

    def test_native_bundle_amount_formula_closes_actual_outward_value(self):
        # Exact 16.36 method from material-return-bundle-native-read.private.json.
        # This is the upstream amount calculation, not the helper's pool math.
        source = '''
        def calculate_qty_and_amount(self, save=False):
            self.total_amount = 0.0
            self.total_qty = 0.0
            self.avg_rate = 0.0
            for row in self.entries:
                rate = flt(row.incoming_rate)
                row.stock_value_difference = flt(row.qty) * rate
                self.total_amount += flt(row.qty) * rate
                self.total_qty += flt(row.qty)
            if self.total_qty:
                self.avg_rate = flt(self.total_amount) / flt(self.total_qty)
            if save:
                self.db_set({"total_qty": self.total_qty, "avg_rate": self.avg_rate, "total_amount": self.total_amount})
        '''
        namespace = {"flt": sys.modules["frappe.utils"].flt}
        exec(compile(textwrap.dedent(source), "installed16.36-bundle-amount", "exec"), namespace)
        row, bundle, sle = self.posted()
        bundle.total_amount = bundle.entries[0].stock_value_difference = 999
        namespace["calculate_qty_and_amount"](bundle)
        sle.stock_value_difference = bundle.total_amount
        self.assertEqual((bundle.total_qty, bundle.total_amount, bundle.avg_rate), (-10, -40, 4))
        self.assertEqual(self.proof(posted=True)[row.name]["value"], 40)

    def test_disabled_batch_and_active_reposting_stop_new_issue_but_cancel_uses_original(self):
        self.batch.disabled = 1
        self.row()
        with self.assertRaisesRegex(ValueError, "已停用"): self.proof()
        self.batch.disabled = 0
        job = self.add("Repost Item Valuation", "REPOST", company="JF", status="Queued", item_code="RM", warehouse="WIP")
        with self.assertRaisesRegex(ValueError, "正在重估"): self.proposal()
        self.store.pop(("Repost Item Valuation", job.name))
        self.loss.items = []
        row, bundle, sle = self.posted()
        self.loss.docstatus = 2
        bundle.docstatus = bundle.entries[0].docstatus = 2
        bundle.is_cancelled = bundle.entries[0].is_cancelled = sle.is_cancelled = 1
        self.batch.disabled = 1
        self.assertEqual(self.proof()[row.name]["value"], 40)

    def test_exact_batch_reservation_consumption_excludes_foreign_and_cancellation_resets_zero(self):
        row, bundle, sle = self.posted(qty=7)
        row.update(parent="LOSS", parenttype="Stock Entry", parentfield="items")
        self.loss.update(doctype="Stock Entry", purpose="Material Issue", jf_production_loss_work_order="WO")
        self.store["Stock Entry", "LOSS"] = self.loss
        self.add("Stock Entry", "FOREIGN", company="JF", work_order="OTHER", purpose="Manufacture", docstatus=1,
            posting_date="2026-10-09", posting_time="10:00:00", items=[])
        self.ledger("FOREIGN-SLE", "FOREIGN", "OTHER-ROW", quantity=-1, value=-4, batch="BATCH", time="10:00:00")
        result = self.module.consumed_batches(self.work, read=self.read, records=self.records)["RM", "BATCH"]
        self.assertEqual((result["qty"], result["value"], result["original_qty"], result["available_qty"]), (7, 28, 10, 2))
        self.assertEqual((result["source_entry"], result["source_detail"]), ("TRANSFER", "TRANSFER-ROW"))
        excluded = self.module.consumed_batches(self.work, read=self.read, records=self.records, exclude="LOSS")["RM", "BATCH"]
        self.assertEqual((excluded["qty"], excluded["available_qty"]), (0, 9))
        self.loss.docstatus = bundle.docstatus = bundle.entries[0].docstatus = 2
        sle.is_cancelled = bundle.is_cancelled = bundle.entries[0].is_cancelled = 1
        result = self.module.consumed_batches(self.work, read=self.read, records=self.records)["RM", "BATCH"]
        self.assertEqual((result["qty"], result["available_qty"]), (0, 9))

    def test_missing_selected_batch_fails_explicitly_before_reader_lookup(self):
        self.row(batch=None)
        with self.assertRaisesRegex(ValueError, "缺少明确选择"): self.proof()

    def test_multiple_transfer_batch_capacity_actual_consumption_and_cancel(self):
        self.second_transfer()
        proposed = self.proposal(15)[0]
        self.assertEqual((proposed["qty"], proposed["expected_value"]), (15, 60))
        row, bundle, sle = self.posted(qty=12)
        row.update(parent="LOSS", parenttype="Stock Entry", parentfield="items")
        self.loss.update(doctype="Stock Entry", purpose="Material Issue", jf_production_loss_work_order="WO")
        self.store["Stock Entry", "LOSS"] = self.loss
        proven = self.proof(posted=True)[row.name]
        self.assertEqual((proven["value"], [source["original_qty"] for source in proven["sources"]]), (48, [10, 5]))
        used = self.module.consumed_batches(self.work, read=self.read, records=self.records)["RM", "BATCH"]
        self.assertEqual((used["qty"], used["original_qty"], used["available_qty"]), (12, 15, 3))
        self.assertIsNone(used["source_entry"])
        self.assertEqual([source["stock_entry"] for source in used["sources"]], ["TRANSFER", "SECOND-TRANSFER"])
        self.loss.docstatus = bundle.docstatus = bundle.entries[0].docstatus = 2
        sle.is_cancelled = bundle.is_cancelled = bundle.entries[0].is_cancelled = 1
        row.serial_and_batch_bundle = ""
        self.assertEqual(self.proof()[row.name]["value"], 48)
        restored = self.module.consumed_batches(self.work, read=self.read, records=self.records)["RM", "BATCH"]
        self.assertEqual((restored["qty"], restored["available_qty"]), (0, 15))

    def test_multiple_transfer_hidden_source_mixed_price_and_cumulative_overuse_rejected(self):
        _doc, bundle, ledger = self.second_transfer()
        ledger.denied = True
        with self.assertRaises(PermissionError): self.proposal(15)
        ledger.denied = False
        bundle.entries[0].incoming_rate = 5
        bundle.entries[0].stock_value_difference = bundle.total_amount = ledger.stock_value_difference = 25
        with self.assertRaisesRegex(ValueError, "前序库存余额"): self.proposal(15)
        bundle.entries[0].incoming_rate = 4
        bundle.entries[0].stock_value_difference = bundle.total_amount = ledger.stock_value_difference = 20
        self.row(name="A", qty=8)
        self.row(name="B", qty=8)
        with self.assertRaisesRegex(ValueError, "累计出库"): self.proof()

    def test_serial_proposal_uses_each_exact_source_rate_and_rejects_fractional_quantities(self):
        self.serial_source()
        proposed = self.proposal(2)
        self.assertEqual([(row["serial_no"], row["qty"], row["expected_value"]) for row in proposed],
                         [("SN-1", 1, 4), ("SN-2", 1, 9)])
        self.assertTrue(all(row["batch_no"] is None for row in proposed))
        with self.assertRaises(ValueError): self.proposal(Decimal("1.5"))
        self.store["Serial No", "SN-1"].warehouse = "OTHER"
        with self.assertRaisesRegex(ValueError, "不在本工单"): self.proposal(1)

    def test_serial_draft_native_bundle_price_is_not_trusted_and_duplicate_selection_rejected(self):
        self.serial_source()
        row, bundle, _sle = self.serial_loss({"SN-1": 999, "SN-2": 999}, status=0)
        self.store.pop(("Stock Ledger Entry", "SLE-LOSS"))
        proven = self.proof()[row.name]
        self.assertEqual((proven["qty"], proven["value"]), (2, 13))
        self.assertEqual([(lot["lot_key"], lot["value"]) for lot in proven["lots"]],
                         [(("serial", "SN-1"), 4), (("serial", "SN-2"), 9)])
        duplicate = Doc(row)
        duplicate.update(name="DUPLICATE", transfer_qty=1, qty=1, serial_no="SN-1", serial_and_batch_bundle=None)
        self.loss.items.append(duplicate)
        with self.assertRaisesRegex(ValueError, "累计出库"): self.proof()
        self.loss.items.pop()
        row.serial_no = "SN-1\nSN-1"
        with self.assertRaisesRegex(ValueError, "重复序号"): self.proof()

    def test_serial_posted_consumed_state_each_value_and_cancel_native_delink(self):
        rates = self.serial_source()
        row, bundle, sle = self.serial_loss(rates)
        self.assertEqual(self.proof(posted=True)[row.name]["value"], 13)
        used = self.module.consumed_batches(self.work, read=self.read, records=self.records)
        self.assertEqual([(used["RM", ("serial", name)]["qty"], used["RM", ("serial", name)]["value"])
                          for name in rates], [(1, 4), (1, 9)])
        self.loss.docstatus = bundle.docstatus = 2
        bundle.is_cancelled = sle.is_cancelled = 1
        for child in bundle.entries: child.update(docstatus=2, is_cancelled=1)
        row.serial_and_batch_bundle = ""
        self.assertEqual(self.proof()[row.name]["value"], 13)
        restored = self.module.consumed_batches(self.work, read=self.read, records=self.records)
        self.assertEqual([restored["RM", ("serial", name)]["qty"] for name in rates], [0, 0])
        sle.stock_value_difference = -12
        with self.assertRaisesRegex(ValueError, "价值与确切库存账"): self.proof()

    def test_serial_nonwise_valuation_requires_uniform_whole_material_layer(self):
        self.serial_source(serial_valuation=0)
        with self.assertRaisesRegex(ValueError, "多种历史价层"): self.proposal(2)
        for child in self.inward.entries:
            child.incoming_rate = child.stock_value_difference = 4
        self.inward.total_amount = self.sle.stock_value_difference = 8
        self.assertEqual(sum(row["expected_value"] for row in self.proposal(2)), 8)

    def test_serial_actual_prices_cannot_offset_each_other_into_same_total(self):
        self.serial_source()
        row, bundle, _sle = self.serial_loss({"SN-1": 6, "SN-2": 7})
        self.assertEqual(bundle.total_amount, -13)
        with self.assertRaisesRegex(ValueError, "逐号或逐批损耗金额"): self.proof(posted=True)

    def test_serial_and_batch_identity_permissions_and_legacy_unknown_value_rejected(self):
        self.serial_source(both=True)
        proposed = self.proposal(2)
        self.assertEqual([row["batch_no"] for row in proposed], ["BATCH", "BATCH"])
        serial = self.store["Serial No", "SN-1"]
        for field, invalid in (("item_code", "OTHER"), ("company", "OTHER"), ("batch_no", "OTHER")):
            original = serial[field]
            serial[field] = invalid
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "原序号"): self.proposal(2)
            serial[field] = original
        serial.denied = True
        with self.assertRaises(PermissionError): self.proposal(2)
        serial.denied = False
        self.sle.serial_and_batch_bundle = None
        with self.assertRaisesRegex(ValueError, "旧序号库存账"): self.proposal(2)

    def test_serial_requires_unique_exact_original_inward_and_all_source_entries(self):
        self.serial_source()
        row = Doc(self.transfer.items[0])
        row.update(name="SECOND-ROW", parent="SECOND-TRANSFER", qty=1, transfer_qty=1, serial_no="SN-1")
        self.add("Stock Entry", "SECOND-TRANSFER", company="JF", work_order="WO", docstatus=1,
            purpose="Material Transfer for Manufacture", posting_date="2026-10-09", posting_time="09:30:00", items=[row])
        self.serial_bundle("SECOND-IN", "SECOND-TRANSFER", row.name, {"SN-1": 4}, time="09:30:00")
        self.ledger("SECOND-SLE", "SECOND-TRANSFER", row.name, quantity=1, value=4, bundle="SECOND-IN", time="09:30:00")
        with self.assertRaisesRegex(ValueError, "多次有效入向"): self.proposal(1)

    def enable_weighted(self, quantity=5, rate=9):
        self.batch.use_batchwise_valuation = 1
        return self.second_transfer(quantity=quantity, rate=rate)

    def movement(self, name, quantity, rate, time):
        bundle = self.bundle(name, name, name + "-ROW", quantity=quantity, rate=rate, time=time)
        return bundle, self.ledger(name + "-SLE", name, name + "-ROW", quantity=quantity,
                                  value=quantity * rate, bundle=bundle.name, time=time)

    def test_batchwise_mixed_origins_use_remaining_value_after_actual_consumption_and_return(self):
        consumed, used = self.movement("EARLY-USE", -5, 4, "09:10:00")
        self.enable_weighted()
        returned, _return_sle = self.movement("WIP-RETURN", -2, Decimal("6.5"), "09:45:00")
        proposed = self.proposal(8)[0]
        self.assertEqual((proposed["qty"], proposed["expected_value"]), (8, 52))
        row, bundle, sle = self.posted(qty=8)
        bundle.entries[0].incoming_rate = Decimal("6.5")
        bundle.entries[0].stock_value_difference = bundle.total_amount = sle.stock_value_difference = -52
        self.assertEqual(self.proof(posted=True)[row.name]["value"], 52)
        # A preceding consumption is later canceled. The canceled loss still
        # reports its actual original 52, not a newly computed WIP average.
        used.is_cancelled = consumed.is_cancelled = consumed.entries[0].is_cancelled = 1
        self.loss.docstatus = bundle.docstatus = bundle.entries[0].docstatus = 2
        sle.is_cancelled = bundle.is_cancelled = bundle.entries[0].is_cancelled = 1
        row.serial_and_batch_bundle = ""
        self.assertEqual(self.proof()[row.name]["value"], 52)

    def test_distinct_enabled_batches_keep_each_actual_price_and_posted_value(self):
        _doc, bundle, ledger = self.enable_weighted()
        self.add("Batch", "SECOND-BATCH", item="RM", disabled=0, use_batchwise_valuation=1)
        bundle.entries[0].batch_no = "SECOND-BATCH"
        proposed = self.proposal(15)
        self.assertEqual([(row["batch_no"], row["expected_value"]) for row in proposed],
                         [("BATCH", 40), ("SECOND-BATCH", 45)])
        first, _bundle, _sle = self.posted()
        second = self.row("SECOND-LOSS", qty=5, batch="SECOND-BATCH", bundle="SECOND-LOSS-BUNDLE")
        second_bundle = self.bundle("SECOND-LOSS-BUNDLE", "LOSS", second.name, quantity=-5, rate=9,
                                    batch="SECOND-BATCH", time="10:00:00")
        self.ledger("SECOND-LOSS-SLE", "LOSS", second.name, quantity=-5, value=-45,
                    bundle=second_bundle.name, time="10:00:00")
        self.assertEqual(sum(row["value"] for row in self.proof(posted=True).values()), 85)

    def test_native_moving_average_policy_override_and_item_method_precedence(self):
        self.enable_weighted()
        self.company.valuation_method = "Moving Average"
        self.assertEqual(self.proposal(15)[0]["expected_value"], 85)
        self.settings.do_not_use_batchwise_valuation = 1
        self.assertEqual(self.proposal(15)[0]["expected_value"], 85)
        self.item.valuation_method = "FIFO"
        self.assertEqual(self.proposal(15)[0]["expected_value"], 85)
        self.item.valuation_method = "Moving Average"
        self.settings.do_not_use_batchwise_valuation = 0
        self.settings.denied = True
        with self.assertRaises(PermissionError): self.proposal(15)

    def test_exact_closing_seed_and_tied_native_candidates_must_equal_full_history(self):
        self.enable_weighted()
        self.transfer.posting_date = self.sle.posting_date = "2026-10-08"
        self.inward.entries[0].posting_datetime = "2026-10-08 09:00:00"
        self.add("Period Closing Voucher", "PCV", company="JF", period_end_date="2026-10-08")
        for name in ("CLOSE-1", "CLOSE-2"):
            self.add("Stock Closing Entry", name, company="JF", status="Completed", to_date="2026-10-08")
            self.add("Stock Closing Balance", name + "-SEED", stock_closing_entry=name,
                     item_code="RM", warehouse="WIP", batch_no="BATCH", actual_qty=10,
                     stock_value_difference=40, inventory_dimension_key="")
        self.assertEqual(self.proposal(15)[0]["expected_value"], 85)
        self.store["Stock Closing Balance", "CLOSE-2-SEED"].stock_value_difference = 41
        with self.assertRaisesRegex(ValueError, "期末逐批数量或价值"): self.proposal(15)
        self.store["Stock Closing Balance", "CLOSE-2-SEED"].stock_value_difference = 40
        self.store["Stock Closing Entry", "CLOSE-2"].denied = True
        with self.assertRaises(PermissionError): self.proposal(15)

    def test_native_legacy_mirrors_allow_equal_equation_and_reject_price_skew(self):
        _doc, _bundle, second = self.enable_weighted()
        self.sle.batch_no = "BATCH"
        with self.assertRaisesRegex(ValueError, "重复计价"): self.proposal(15)
        second.batch_no = "BATCH"
        self.assertEqual(self.proposal(15)[0]["expected_value"], 85)
        # Native legacy-only rows contribute once, exactly like their true SLE.
        self.sle.serial_and_batch_bundle = None
        self.store.pop(("Serial and Batch Bundle", "INWARD"))
        self.store.pop(("Serial and Batch Entry", "INWARD-ROW"))
        second.batch_no = None
        self.assertEqual(self.proposal(15)[0]["expected_value"], 85)

    def test_native_child_pool_cannot_omit_orphan_or_hidden_valuation_entries(self):
        self.enable_weighted()
        self.bundle("ORPHAN", "ORPHAN", "ORPHAN-ROW", quantity=-1, rate=3, time="09:15:00")
        with self.assertRaisesRegex(ValueError, "无账批次"): self.proposal(15)
        self.store.pop(("Serial and Batch Bundle", "ORPHAN"))
        self.store.pop(("Serial and Batch Entry", "ORPHAN-ROW"))
        original = self.records
        def hidden(kind, filters, fields, **kwargs):
            rows = original(kind, filters, fields, **kwargs)
            if kind == "Serial and Batch Entry" and filters.get("item_code") == "RM":
                return [row for row in rows if row["parent"] != "SECOND-IN"]
            return rows
        with patch.object(self, "records", hidden), self.assertRaisesRegex(ValueError, "来源不完整"):
            self.proposal(15)

    def test_posted_same_timestamp_later_transfer_uses_actual_sle_creation_boundary(self):
        row, _loss_bundle, _loss_sle = self.posted()
        doc, bundle, ledger = self.enable_weighted()
        doc.posting_time = ledger.posting_time = "10:00:00"
        ledger.creation = "2026-10-09 10:00:00.100000"
        bundle.entries[0].posting_datetime = "2026-10-09 10:00:00"
        self.assertEqual(self.proof(posted=True)[row.name]["value"], 40)

    def test_native_batch_price_equation_uses_net_current_qty_and_value(self):
        # Exact pinned BatchNoValuation.set_stock_value_difference; native
        # evaluates remaining sums, rather than averaging original receipts.
        source = '''
        def set_stock_value_difference(self):
            for batch_no, ledger in self.batch_nos.items():
                if batch_no in self.non_batchwise_valuation_batches:
                    continue
                if not self.available_qty[batch_no]:
                    continue
                self.batch_avg_rate[batch_no] = (
                    self.stock_value_differece[batch_no] / self.available_qty[batch_no]
                )
                stock_value_change = self.batch_avg_rate[batch_no] * ledger.qty
                self.stock_value_change += stock_value_change
        '''
        namespace = {}
        exec(compile(textwrap.dedent(source), "installed16.36-batch-equation", "exec"), namespace)
        native = Doc(batch_nos={"BATCH": Doc(qty=-8)}, non_batchwise_valuation_batches=[],
                     available_qty={"BATCH": 8}, stock_value_differece={"BATCH": 52},
                     batch_avg_rate={}, stock_value_change=0)
        namespace["set_stock_value_difference"](native)
        self.assertEqual((native.batch_avg_rate["BATCH"], native.stock_value_change), (6.5, -52))

    def test_disabled_fifo_prices_specific_quantity_across_layers_and_native_multiple_rows(self):
        self.second_transfer(rate=9)
        self.assertEqual(self.proposal(12)[0]["expected_value"], 58)
        first, _first_bundle, _first_sle = self.posted(qty=6)
        second = self.row("SECOND-LOSS", qty=6, bundle="SECOND-LOSS-BUNDLE")
        self.bundle("SECOND-LOSS-BUNDLE", "LOSS", second.name, quantity=-6,
                    rate=Decimal(34) / 6, time="10:00:00")
        second_sle = self.ledger("SECOND-LOSS-SLE", "LOSS", second.name, quantity=-6, value=-34,
                                  bundle="SECOND-LOSS-BUNDLE", time="10:00:00")
        proof = self.proof(posted=True)
        self.assertEqual([proof[row.name]["value"] for row in (first, second)], [24, 34])
        second_sle.stock_queue = "[[3, 8]]"
        with self.assertRaisesRegex(ValueError, "FIFO损耗账"): self.proof(posted=True)
        second_sle.stock_queue = "[[3, 9]]"
        self.loss.items.reverse()
        with self.assertRaises(ValueError): self.proof(posted=True)

    def test_disabled_fifo_previous_consumption_and_cancel_keep_original_actual_loss(self):
        _early_bundle, early = self.movement("EARLY-USE", -5, 4, "09:10:00")
        self.second_transfer(rate=9)
        self.assertEqual(self.proposal(6)[0]["expected_value"], 29)
        row, bundle, sle = self.posted(qty=6)
        bundle.entries[0].incoming_rate = Decimal(29) / 6
        bundle.entries[0].stock_value_difference = bundle.total_amount = sle.stock_value_difference = -29
        sle.stock_value = 36
        self.assertEqual(self.proof(posted=True)[row.name]["value"], 29)
        self.loss.docstatus = bundle.docstatus = bundle.entries[0].docstatus = 2
        sle.is_cancelled = bundle.is_cancelled = bundle.entries[0].is_cancelled = 1
        early.is_cancelled = 1
        row.serial_and_batch_bundle = ""
        self.assertEqual(self.proof()[row.name]["value"], 29)

    def test_disabled_fifo_physical_batch_choice_preserves_native_whole_warehouse_queue(self):
        _doc, bundle, _sle = self.second_transfer(rate=9)
        self.add("Batch", "SECOND-BATCH", item="RM", use_batchwise_valuation=0)
        bundle.entries[0].batch_no = "SECOND-BATCH"
        # Choosing the newer physical batch does not choose its historical price
        # when native valuation is the unqualified FIFO warehouse queue.
        second = self.row(qty=5, batch="SECOND-BATCH")
        first = self.row("FIRST-LOSS", qty=10)
        proof = self.proof()
        self.assertEqual([proof[row.name]["value"] for row in (second, first)], [20, 65])
        self.assertEqual(proof[second.name]["sources"][0]["stock_entry"], "SECOND-TRANSFER")

    def test_native_lifo_sabb_uses_pooled_rate_and_moving_average_respects_single_override(self):
        self.second_transfer(rate=9)
        self.company.valuation_method = "LIFO"
        self.assertEqual(self.proposal(12)[0]["expected_value"], 68)
        self.company.valuation_method = "Moving Average"
        self.assertEqual(self.proposal(12)[0]["expected_value"], 68)
        # Distinct batches: native MA batch-pool keeps 4 and 9; the Single
        # override selects the actual whole-warehouse 85/15 pooled rate.
        self.add("Batch", "SECOND-BATCH", item="RM", use_batchwise_valuation=0)
        self.store["Serial and Batch Entry", "SECOND-IN-ROW"].batch_no = "SECOND-BATCH"
        self.assertEqual([row["expected_value"] for row in self.proposal(15)], [40, 45])
        self.settings.do_not_use_batchwise_valuation = 1
        self.assertAlmostEqual(float(sum(row["expected_value"] for row in self.proposal(15))), 85)
        self.assertNotEqual(self.proposal(10)[0]["expected_value"], 40)

    def test_disabled_fifo_native_predecessor_queue_quantity_value_and_visibility_are_required(self):
        _doc, _bundle, second = self.second_transfer(rate=9)
        for field, invalid, message in (("stock_queue", "[[15, 4]]", "价层队列数量或金额"),
                                         ("stock_value", 84, "前序库存余额"),
                                         ("posting_datetime", "2026-10-09 09:29:00", "保存时间")):
            previous = second[field]
            second[field] = invalid
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message): self.proposal(12)
            second[field] = previous
        second.denied = True
        with self.assertRaises(PermissionError): self.proposal(12)

    def test_disabled_native_bundle_parent_time_and_orphan_pool_are_proved(self):
        _doc, bundle, _sle = self.second_transfer(rate=9)
        bundle.posting_datetime = "2026-10-09 09:29:00"
        with self.assertRaisesRegex(ValueError, "整仓估价批次池"): self.proposal(12)
        bundle.posting_datetime = "2026-10-09 09:30:00"
        self.bundle("ORPHAN", "ORPHAN", "ORPHAN-ROW", quantity=1, rate=7, time="09:35:00")
        with self.assertRaisesRegex(ValueError, "无账批次"): self.proposal(12)

    def test_unposted_nonbatch_native_millisecond_boundary_matches_saved_timestamps(self):
        self.second_transfer(rate=9)
        # Native advances the no-creation selector by exactly 1 ms. A physical
        # consumption 0.5 ms after the supplied timestamp is therefore included
        # in native price/cap proof, rather than guessed from Parent.creation.
        self.movement("MILLISECOND-USE", -5, 4, "10:00:00.000500")
        proposed = self.module.proposal(self.work, {"RM": 6}, read=self.read, records=self.records,
                                       posting_date="2026-10-09", posting_time="10:00:00")
        self.assertEqual(proposed[0]["expected_value"], 29)

    def test_native_captured_nonbatch_rate_formula_matches_lifo_and_ma_balances(self):
        # Exact pinned get_non_batchwise_avg_rate, shared by installed LIFO/MA
        # SABB paths. LIFOValuation is deliberately not substituted for it.
        source = '''
        def get_non_batchwise_avg_rate(self, batch_no, fallback_rate):
            balance_qty = flt(self.non_batchwise_balance_qty.get(batch_no))
            balance_value = flt(self.non_batchwise_balance_value.get(batch_no))
            if balance_qty > 0 and balance_value > 0:
                return balance_value / balance_qty
            return fallback_rate
        '''
        namespace = {"flt": sys.modules["frappe.utils"].flt}
        exec(compile(textwrap.dedent(source), "installed16.36-nonbatch-rate", "exec"), namespace)
        native = Doc(non_batchwise_balance_qty={"BATCH": 15}, non_batchwise_balance_value={"BATCH": 85})
        self.assertEqual(namespace["get_non_batchwise_avg_rate"](native, "BATCH", 4) * 12, 68)
        native.non_batchwise_balance_value["BATCH"] = -1
        self.assertEqual(namespace["get_non_batchwise_avg_rate"](native, "BATCH", 4), 4)

    def test_mixed_selected_native_strategies_reject_before_quoting_wrong_shared_queue(self):
        self.batch.use_batchwise_valuation = 1
        _doc, bundle, _sle = self.second_transfer(rate=9)
        self.add("Batch", "SECOND-BATCH", item="RM", use_batchwise_valuation=0)
        bundle.entries[0].batch_no = "SECOND-BATCH"
        self.assertEqual(self.proposal(10)[0]["expected_value"], 40)
        with self.assertRaisesRegex(ValueError, "不能混选"): self.proposal(15)
        self.row(qty=5, batch="SECOND-BATCH")
        self.assertEqual(self.proof()["LOSS-ROW"]["value"], 20)
        self.row("MIXED-ROW", qty=10, batch="BATCH")
        with self.assertRaisesRegex(ValueError, "不能混选"): self.proof()

    def test_cancel_source_capacity_and_sre_source_keys_do_not_borrow_later_same_time_transfer(self):
        row, bundle, sle = self.posted()
        doc, source_bundle, transfer_sle = self.second_transfer(rate=9)
        doc.posting_time = transfer_sle.posting_time = "10:00:00"
        transfer_sle.creation = "2026-10-09 10:00:00.100000"
        source_bundle.entries[0].posting_datetime = "2026-10-09 10:00:00"
        self.loss.docstatus = bundle.docstatus = bundle.entries[0].docstatus = 2
        sle.is_cancelled = bundle.is_cancelled = bundle.entries[0].is_cancelled = 1
        row.serial_and_batch_bundle = ""
        proof = self.proof()[row.name]
        self.assertEqual([source["stock_entry"] for source in proof["sources"]], ["TRANSFER"])
        self.assertEqual(proof["value"], 40)


if __name__ == "__main__":
    unittest.main()
