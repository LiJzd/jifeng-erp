"""Source-average batch returns, native inward queue and read-only costing proof."""

import ast
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import test_material_returns_tracking as tracking

Record = tracking.Record
ValidationError = tracking.ValidationError


class TestMaterialReturnLayers(unittest.TestCase):
    def setUp(self):
        self.h = tracking.TestMaterialReturnTracking()
        self.h.setUp()
        self.m = self.h.m
        self.evidence = self.h.evidence()
        self.evidence["row"].update(qty=4, uom="Nos", stock_uom="Nos", conversion_factor=1)
        self.evidence["item"].update(stock_uom="Nos", is_stock_item=1)
        self.source = self.h.source(self.evidence)
        self.source.update(docstatus=1, stock_entry_type="Material Issue", purpose="Material Issue",
                           is_return=0, jf_material_issue_flow="FLOW-1")
        self.original_ledger = []

    def named_bundle(self, name, entries, *, original=True, voucher=None, detail=None):
        bundle = self.h.bundle(self.evidence, entries, original=original)
        del self.h.docs[self.m.BUNDLE, bundle.name]
        bundle.name = name
        if voucher:
            bundle.voucher_no = voucher
        if detail:
            bundle.voucher_detail_no = detail
        self.h.docs[self.m.BUNDLE, name] = bundle
        for index, row in enumerate(bundle.entries):
            row.name, row.parent = name + "-" + str(index), name
            self.h.docs[self.m.BUNDLE_ENTRY, row.name] = row
        for (kind, name), doc in self.h.docs.items():
            doc.setdefault("name", name)
            doc.setdefault("doctype", kind)
        return bundle

    def ledger(self, bundle, qty, value, *, original=True):
        return Record(name=bundle.name + "-SLE", company="JF", docstatus=1, is_cancelled=0,
            voucher_type="Stock Entry", voucher_no=bundle.voucher_no, voucher_detail_no=bundle.voucher_detail_no,
            item_code="RAW", warehouse="Stores - JF", stock_uom="Nos", actual_qty=-qty if original else qty,
            stock_value_difference=-value if original else value, serial_and_batch_bundle=bundle.name,
            is_adjustment_entry=0)

    def read(self, kind, name):
        return self.h.docs[kind, name]

    def records(self, kind, filters, fields, parenttype=None):
        return [Record({field: doc.get(field) for field in fields}) for (doctype, _), doc in self.h.docs.items()
                if doctype == kind and all(doc.get(field) == value for field, value in filters.items())]

    def issue(self, *, split=True, serial=False):
        for (kind, name), document in list(self.h.docs.items()):
            if kind == self.m.BUNDLE and document.get("voucher_no") == "ISSUE":
                del self.h.docs[kind, name]
                for child in document.entries:
                    self.h.docs.pop((self.m.BUNDLE_ENTRY, child.name), None)
        if serial:
            self.evidence["item"].has_serial_no = 1
            self.evidence["row"].qty = self.evidence["row"].transfer_qty = 2
            entries = [("S-LOW", "B1", 1, 3), ("S-HIGH", "B1", 1, 27)]
            bundle = self.named_bundle("ORIG-SERIAL", entries)
            self.original_ledger = [self.ledger(bundle, 2, 30)]
        elif split:
            first = self.named_bundle("ORIG-4", [(None, "B1", 2, 4)])
            second = self.named_bundle("ORIG-20", [(None, "B1", 2, 20)])
            self.original_ledger = [self.ledger(first, 2, 8), self.ledger(second, 2, 40)]
        else:
            bundle = self.named_bundle("ORIG-BATCHES", [(None, "CHEAP", 2, 4), (None, "EXPENSIVE", 2, 20)])
            self.original_ledger = [self.ledger(bundle, 4, 48)]
        return self.m.material_issue_evidence(self.source, self.evidence["row"], self.evidence["item"],
            self.original_ledger, read=self.read, records=self.records)

    def returned(self, name="RETURN-1", *, quantity=1, batch="B1", serial=None, rate=12):
        doc, row = self.h.returned(self.evidence, quantity=quantity, batch=batch, serial=serial, status=1)
        doc.name, row.name, row.basic_rate = name, name + "-ROW", rate
        bundle = self.named_bundle(name + "-BUNDLE", [(serial, batch, quantity, rate)], original=False,
            voucher=doc.name, detail=row.name)
        row.serial_and_batch_bundle = bundle.name
        return doc, row, bundle, [self.ledger(bundle, quantity, Decimal(str(quantity)) * Decimal(str(rate)), original=False)]

    def value(self, evidence, returned, usage):
        doc, row, _bundle, ledger = returned
        return self.m.material_return_value(evidence, self.source, doc, row, ledger,
            read=self.read, records=self.records, used=usage)

    def test_partial_same_batch_returns_use_exact_issue_pool_and_cancellation_releases_value(self):
        evidence = self.issue()
        self.assertEqual(evidence["pools"][("batch", "B1")]["value"], 48)
        usage = self.m.MaterialReturnUsage()
        first, second = self.returned(), self.returned("RETURN-2", quantity=3)
        self.assertEqual(self.value(evidence, first, usage), 12)
        self.assertEqual(self.value(evidence, second, usage), 36)
        self.assertEqual((usage[("batch", "B1")], usage.values_used[("batch", "B1")]), (4, 48))
        with self.assertRaisesRegex(ValidationError, "累计退回超过"):
            self.value(evidence, self.returned("EXTRA"), usage)
        second[0].docstatus, second[3][0].is_cancelled = 2, 1
        with self.assertRaises(ValidationError):
            self.value(evidence, second, self.m.MaterialReturnUsage())
        # Rebuild from only active returns, as native cancellation removes the
        # second voucher from the cumulative source scope.
        usage = self.m.MaterialReturnUsage()
        self.assertEqual(self.value(evidence, first, usage), 12)
        self.assertEqual(self.value(evidence, self.returned("AFTER-CANCEL", quantity=3), usage), 36)

    def test_each_original_bundle_must_match_its_own_sle_not_just_whole_row_total(self):
        self.issue()
        self.original_ledger[0].stock_value_difference = -20
        self.original_ledger[1].stock_value_difference = -28
        with self.assertRaisesRegex(ValidationError, "准确出库库存账"):
            self.m.material_issue_evidence(self.source, self.evidence["row"], self.evidence["item"],
                self.original_ledger, read=self.read, records=self.records)

    def test_selected_expensive_batch_and_serial_keep_their_values_not_row_average(self):
        evidence = self.issue(split=False)
        self.assertEqual(self.value(evidence, self.returned(batch="EXPENSIVE", rate=20), self.m.MaterialReturnUsage()), 20)
        with self.assertRaisesRegex(ValidationError, "逐批/逐号价值"):
            self.value(evidence, self.returned("WRONG", batch="EXPENSIVE", rate=12), self.m.MaterialReturnUsage())
        evidence = self.issue(serial=True)
        self.assertEqual(self.value(evidence, self.returned("HIGH", batch="B1", serial="S-HIGH", rate=27),
                                    self.m.MaterialReturnUsage()), 27)

    def test_return_sle_amount_bundle_owner_and_complete_children_are_required(self):
        evidence = self.issue()
        returned = self.returned()
        returned[3][0].stock_value_difference = 13
        usage = self.m.MaterialReturnUsage()
        with self.assertRaisesRegex(ValidationError, "实际库存金额"):
            self.value(evidence, returned, usage)
        self.assertFalse(usage)
        self.assertFalse(usage.values_used)
        returned[3][0].stock_value_difference = 12
        returned[2].voucher_detail_no = "OTHER-ROW"
        with self.assertRaisesRegex(ValidationError, "原单、原行"):
            self.value(evidence, returned, self.m.MaterialReturnUsage())
        returned[2].voucher_detail_no = returned[1].name
        self.h.docs.pop((self.m.BUNDLE_ENTRY, returned[2].entries[0].name))
        with self.assertRaisesRegex(ValidationError, "隐藏实物"):
            self.value(evidence, returned, self.m.MaterialReturnUsage())

    def test_exact_single_legacy_batch_accepts_multiple_issue_values_without_guessing_serials(self):
        self.evidence["row"].batch_no = "B1"
        self.h.docs["Batch", "B1"] = Record(name="B1", item="RAW")
        dummy = Record(name="LEGACY", voucher_no="ISSUE", voucher_detail_no="ORIGINAL-ROW")
        ledger = [self.ledger(dummy, 2, 8), self.ledger(dummy, 2, 40)]
        for entry in ledger:
            entry.serial_and_batch_bundle, entry.batch_no, entry.serial_no = None, "B1", None
        evidence = self.m.material_issue_evidence(self.source, self.evidence["row"], self.evidence["item"],
            ledger, read=self.read, records=self.records)
        self.assertEqual(self.value(evidence, self.returned(), self.m.MaterialReturnUsage()), 12)
        ledger[0].batch_no = "OTHER"
        with self.assertRaisesRegex(ValidationError, "旧批次/序列字段"):
            self.m.material_issue_evidence(self.source, self.evidence["row"], self.evidence["item"],
                ledger, read=self.read, records=self.records)

    def test_zero_price_layer_stays_part_of_the_real_batch_value(self):
        evidence = self.issue()
        bundle = self.h.docs[self.m.BUNDLE, "ORIG-4"]
        bundle.entries[0].incoming_rate = bundle.entries[0].stock_value_difference = bundle.total_amount = 0
        self.original_ledger[0].stock_value_difference = 0
        evidence = self.m.material_issue_evidence(self.source, self.evidence["row"], self.evidence["item"],
            self.original_ledger, read=self.read, records=self.records)
        self.assertEqual(self.value(evidence, self.returned(rate=10), self.m.MaterialReturnUsage()), 10)

    def test_read_only_callback_proof_cannot_fall_back_to_private_db_or_write_permissions(self):
        self.issue()
        with patch.object(self.m, "_lock_source", side_effect=AssertionError("writer lock")), \
             patch.object(self.m, "_permitted_fields", side_effect=AssertionError("private permission query")), \
             patch.object(self.m, "_permitted_query_fields", side_effect=AssertionError("private list")), \
             patch.object(self.h.frappe, "get_doc", side_effect=AssertionError("private read")):
            evidence = self.m.material_issue_evidence(self.source, self.evidence["row"], self.evidence["item"],
                self.original_ledger, read=self.read, records=self.records)
            self.assertEqual(self.value(evidence, self.returned(), self.m.MaterialReturnUsage()), 12)

    def test_cumulative_actual_value_cap_catches_small_per_return_rounding_errors(self):
        evidence = self.h.evidence(quantity="3")
        self.h.original_pools(evidence, [(None, "B1", 3, 1)])
        usage = self.m.MaterialReturnUsage()
        chosen = {("batch", "B1"): {"qty": Decimal(1), "value": Decimal("1.00000075"),
                  "rates": {Decimal("1.00000075")}, "batch": "B1"}}
        self.m._selected_value(evidence, chosen, usage, posted=True)
        self.m._selected_value(evidence, chosen, usage, posted=True)
        with self.assertRaisesRegex(ValidationError, "累计退回金额"):
            self.m._selected_value(evidence, chosen, usage, posted=True)

    def test_installed_native_inward_return_dispatch_prices_batch_and_appends_exact_fifo_queue(self):
        evidence = self.issue()
        returned = self.returned()
        doc, row, selected, _ledger = returned
        selected.entries[0].incoming_rate = selected.entries[0].stock_value_difference = 999
        path = Path(__file__).parents[2] / "erpnext/erpnext/stock/doctype/serial_and_batch_bundle/serial_and_batch_bundle.py"
        # These two primary-source function ASTs were checked equal to the
        # captured installed 16.36 methods before this driver was added.
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {"set_incoming_rate", "set_valuation_rate_for_return_entry"}
        nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {"frappe": self.h.frappe, "flt": float, "parse_json": json.loads, "json": json, "cint": int}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
        Native = type("Native", (), {name: namespace[name] for name in names})
        cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record, Native), {})
        bundle = cls(selected)
        for entry in bundle.entries:
            # Native Float fields are loaded as floats, not fixture Decimals.
            entry.qty = float(entry.qty)
        bundle.is_new = lambda: False
        bundle.get_batchwise_return_avg_rates = lambda: {}
        bundle.validate_returned_serial_batch_no = lambda *args: None
        self.h.frappe.flags = Record(through_repost_item_valuation=False)
        self.h.frappe.get_all = lambda *args, **kwargs: ["B1"]
        utils = types.ModuleType("erpnext.stock.utils")
        utils.get_valuation_method = lambda *args: "FIFO"
        utils.is_serial_no_wise_valuation_disabled = lambda *args: False
        with patch.dict(sys.modules, {utils.__name__: utils}), self.h.native_context(evidence, doc):
            bundle.set_incoming_rate(parent=doc, row=row, save=False, prev_sle=Record(stock_queue="[]"))
        self.assertEqual((bundle.entries[0].incoming_rate, bundle.entries[0].stock_value_difference), (12, 12))
        self.assertEqual(json.loads(bundle.entries[0].stock_queue), [[1, 12]])


if __name__ == "__main__":
    unittest.main()
