"""Exact historical physical pools, native selector lifecycle and permission risks."""

from collections import defaultdict
from decimal import Decimal
import types
import unittest
from unittest.mock import patch

import test_material_returns as base

Record = base.Record
ValidationError = base.ValidationError
PermissionError = base.PermissionError


class BundleMeta(base.Meta):
    def __init__(self, name, fields):
        super().__init__(name)
        self.allowed = set(fields)

    def get_field(self, name):
        if name == "entries":
            return Record(fieldtype="Table", options="Serial and Batch Entry", permlevel=0,
                          mask=int(name in self.masked))
        return super().get_field(name)


class TestMaterialReturnTracking(unittest.TestCase):
    def setUp(self):
        base.TestMaterialReturns.setUp(self)
        self.m = self.module
        self.metas[self.m.BUNDLE] = BundleMeta(self.m.BUNDLE, self.m.BUNDLE_FIELDS)
        self.metas[self.m.BUNDLE_ENTRY] = BundleMeta(self.m.BUNDLE_ENTRY, self.m.ENTRY_FIELDS)
        self.metas["Serial No"] = BundleMeta("Serial No", ("item_code", "batch_no"))
        self.metas["Batch"] = BundleMeta("Batch", ("item",))
        self.metas["Item"] = BundleMeta("Item", ("use_serial_no_wise_valuation",))
        self.docs = {}
        self.frappe.get_doc = lambda doctype, name, **kwargs: self.docs[(doctype, name)]

    def evidence(self, *, serial=False, quantity="4"):
        source_row = Record(name="ORIGINAL-ROW", idx=1, item_code="RAW", item_name="原物料",
                            s_warehouse="Stores - JF", expense_account="Expense - JF", cost_center=None,
                            project=None, jf_flow_order="FLOW-1", transfer_qty=quantity)
        item = Record(name="RAW", has_serial_no=int(serial), has_batch_no=1,
                      use_serial_no_wise_valuation=1, meta=BundleMeta("Item", ("use_serial_no_wise_valuation",)))
        return {"row": source_row, "item": item, "uom": "Nos", "flow": "FLOW-1",
                "qty": Decimal(quantity), "tracked": True}

    def bundle(self, evidence, entries, *, original=True, status=1, total=None):
        name = "ORIGINAL-BUNDLE" if original else "RETURN-BUNDLE"
        direction = -1 if original else 1
        rows = []
        for idx, (serial, batch, quantity, rate) in enumerate(entries, 1):
            signed = Decimal(str(quantity)) * direction
            rows.append(Record(name=f"ENTRY-{idx}", parent=name, parenttype=self.m.BUNDLE,
                               parentfield="entries", docstatus=status, item_code="RAW", warehouse="Stores - JF",
                               serial_no=serial, batch_no=batch, qty=signed, incoming_rate=rate,
                               stock_value_difference=signed * Decimal(str(rate))))
            if batch:
                self.docs[("Batch", batch)] = Record(item="RAW", meta=self.metas["Batch"])
            if serial:
                self.docs[("Serial No", serial)] = Record(item_code="RAW", batch_no=batch, meta=self.metas["Serial No"])
        result = Record(name=name, doctype=self.m.BUNDLE, meta=self.metas[self.m.BUNDLE], docstatus=status,
                        company="JF", item_code="RAW", warehouse="Stores - JF", voucher_type="Stock Entry",
                        voucher_no="ISSUE" if original else "RETURN", voucher_detail_no="ORIGINAL-ROW" if original else "RETURN-ROW",
                        type_of_transaction="Outward" if original else "Inward", is_cancelled=0,
                        has_serial_no=evidence["item"].has_serial_no, has_batch_no=1, entries=rows,
                        total_qty=sum((row.qty for row in rows), Decimal(0)) if total is None else total,
                        total_amount=sum((row.stock_value_difference for row in rows), Decimal(0)))
        self.docs[(self.m.BUNDLE, name)] = result
        return result

    def source(self, evidence):
        return Record(name="ISSUE", company="JF", items=[evidence["row"]])

    def returned(self, evidence, *, quantity="1", batch="B-EXPENSIVE", serial=None, bundle=None, status=0):
        row = Record(name="RETURN-ROW", item_code="RAW", jf_material_return_against_detail="ORIGINAL-ROW",
                     uom="Nos", stock_uom="Nos", conversion_factor=1, transfer_qty=quantity, qty=quantity,
                     t_warehouse="Stores - JF", s_warehouse=None, expense_account="Expense - JF", cost_center=None,
                     project=None, jf_flow_order="FLOW-1", batch_no=batch, serial_no=serial,
                     serial_and_batch_bundle=bundle, use_serial_batch_fields=1, basic_rate=99,
                     set_basic_rate_manually=1, additional_cost=0, landed_cost_voucher_amount=0)
        doc = Record(name="RETURN", doctype="Stock Entry", company="JF", items=[row], docstatus=status,
                     stock_entry_type="Material Receipt", purpose="Material Receipt", is_return=0,
                     jf_material_return_against="ISSUE", jf_material_issue_flow="FLOW-1", additional_costs=[],
                     total_additional_costs=0, meta=base.Meta("Stock Entry"))
        return doc, row

    def original_pools(self, evidence, entries):
        bundle = self.bundle(evidence, entries)
        source = self.source(evidence)
        pools = self.m._bundle_pools(source, source, evidence["row"], evidence, bundle, original=True)
        evidence["pools"] = pools
        return pools

    def test_partial_return_of_expensive_batch_uses_its_historical_price_not_row_average(self):
        evidence = self.evidence()
        self.original_pools(evidence, [(None, "B-CHEAP", 2, 4), (None, "B-EXPENSIVE", 2, 20)])
        doc, row = self.returned(evidence)
        chosen = self.m._selected_pools(self.source(evidence), doc, row, evidence)
        value = self.m._selected_value(evidence, chosen, {})
        self.assertEqual(value, Decimal("20"))
        self.assertNotEqual(value, Decimal("12"), "whole original row average must not price a selected expensive batch")

    def test_two_serials_keep_different_original_prices_and_explicit_order(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.original_pools(evidence, [("S-LOW", "B1", 1, 3), ("S-HIGH", "B1", 1, 27)])
        doc, row = self.returned(evidence, quantity="1", batch="B1", serial="S-HIGH")
        chosen = self.m._selected_pools(self.source(evidence), doc, row, evidence)
        self.assertEqual(self.m._selected_value(evidence, chosen, {}), Decimal("27"))
        self.assertEqual(list(chosen), [("serial", "S-HIGH")])

    def test_same_source_row_total_does_not_allow_overdraw_of_one_batch(self):
        evidence = self.evidence()
        self.original_pools(evidence, [(None, "B-CHEAP", 2, 4), (None, "B-EXPENSIVE", 2, 20)])
        doc, row = self.returned(evidence, quantity="1.1")
        selected = self.m._selected_pools(self.source(evidence), doc, row, evidence)
        with self.assertRaisesRegex(ValidationError, "批次/序列号累计退回超过"):
            self.m._selected_value(evidence, selected, {("batch", "B-EXPENSIVE"): Decimal("1")})

    def test_foreign_lot_serial_and_correct_serial_with_wrong_batch_are_rejected(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.original_pools(evidence, [("S1", "B1", 1, 3), ("S2", "B1", 1, 27)])
        for serial, batch in (("NOT-ISSUED", "B1"), ("S1", "B-OTHER")):
            doc, row = self.returned(evidence, batch=batch, serial=serial)
            chosen = self.m._selected_pools(self.source(evidence), doc, row, evidence)
            with self.assertRaisesRegex(ValidationError, "不属于准确原领料行"):
                self.m._selected_value(evidence, chosen, {})

    def test_duplicate_serial_and_fractional_serial_count_are_rejected(self):
        evidence = self.evidence(serial=True, quantity="2")
        for quantity, serial in (("2", "S1\nS1"), ("0.5", "S1")):
            doc, row = self.returned(evidence, quantity=quantity, batch="B1", serial=serial)
            with self.assertRaises(ValidationError):
                self.m._selected_pools(self.source(evidence), doc, row, evidence)

    def test_same_batch_multiple_issue_prices_support_exact_original_pool_partial_returns(self):
        evidence = self.evidence()
        self.original_pools(evidence, [(None, "B1", 2, 4), (None, "B1", 2, 20)])
        doc, row = self.returned(evidence, batch="B1", quantity="1")
        chosen = self.m._selected_pools(self.source(evidence), doc, row, evidence)
        used = self.m.MaterialReturnUsage()
        self.assertEqual(self.m._selected_value(evidence, chosen, used), Decimal("12"))
        row.qty = row.transfer_qty = 3
        chosen = self.m._selected_pools(self.source(evidence), doc, row, evidence)
        self.assertEqual(self.m._selected_value(evidence, chosen, used), Decimal("36"))
        self.assertEqual(used.values_used[("batch", "B1")], Decimal("48"))
        with self.assertRaisesRegex(ValidationError, "累计退回超过"):
            self.m._selected_value(evidence, chosen, used)

    def test_original_bundle_exact_company_voucher_row_direction_flags_and_entry_parent_are_required(self):
        evidence = self.evidence()
        source = self.source(evidence)
        mutations = [("company", "OTHER"), ("voucher_no", "OTHER-ISSUE"), ("voucher_detail_no", "OTHER-ROW"),
                     ("warehouse", "Other - JF"), ("type_of_transaction", "Inward"), ("has_batch_no", 0)]
        for field, value in mutations:
            bundle = self.bundle(evidence, [(None, "B1", 4, 10)])
            bundle[field] = value
            with self.assertRaises(ValidationError):
                self.m._bundle_pools(source, source, evidence["row"], evidence, bundle, original=True)
        bundle = self.bundle(evidence, [(None, "B1", 4, 10)])
        bundle.entries[0].parent = "OTHER-BUNDLE"
        with self.assertRaisesRegex(ValidationError, "父凭证不一致"):
            self.m._bundle_pools(source, source, evidence["row"], evidence, bundle, original=True)

    def test_original_bundle_incoming_price_and_stock_value_must_agree(self):
        evidence = self.evidence()
        source = self.source(evidence)
        bundle = self.bundle(evidence, [(None, "B1", 4, 10)])
        bundle.entries[0].stock_value_difference = -39
        with self.assertRaisesRegex(ValidationError, "历史单价与库存价值不一致"):
            self.m._bundle_pools(source, source, evidence["row"], evidence, bundle, original=True)

    def test_masked_bundle_entries_or_historical_price_cannot_be_read(self):
        evidence = self.evidence()
        self.bundle(evidence, [(None, "B1", 4, 10)])
        self.metas[self.m.BUNDLE].masked.add("entries")
        with self.assertRaises(PermissionError):
            self.m._read_bundle("ORIGINAL-BUNDLE")
        self.metas[self.m.BUNDLE].masked.clear()
        self.metas[self.m.BUNDLE_ENTRY].masked.add("incoming_rate")
        with self.assertRaises(PermissionError):
            self.m._read_bundle("ORIGINAL-BUNDLE")

    def test_hidden_original_bundle_is_not_treated_as_available_or_leaked(self):
        evidence = self.evidence()
        evidence["ledger"] = [Record(actual_qty=-4, serial_and_batch_bundle="HIDDEN")]
        self.frappe.get_all = lambda *args, **kwargs: ["HIDDEN"]
        self.frappe.get_list = lambda *args, **kwargs: []
        with self.assertRaisesRegex(PermissionError, "无法完整读取原领料批次凭证"):
            self.m._original_pools(self.source(evidence), evidence, Decimal("40"))

    def test_ledger_link_original_bundle_quantity_and_value_agree(self):
        evidence = self.evidence()
        self.bundle(evidence, [(None, "B1", 4, 10)])
        evidence["ledger"] = [Record(actual_qty=-4, stock_value_difference=-40, serial_and_batch_bundle="ORIGINAL-BUNDLE")]
        self.frappe.get_all = self.frappe.get_list = lambda *args, **kwargs: ["ORIGINAL-BUNDLE"]
        pools = self.m._original_pools(self.source(evidence), evidence, Decimal("40"))
        self.assertEqual(pools[("batch", "B1")]["value"], Decimal("40"))
        with self.assertRaisesRegex(ValidationError, "历史价值与有效领料库存账不一致"):
            self.m._original_pools(self.source(evidence), evidence, Decimal("41"))

    def test_legacy_multi_serial_history_is_not_priced_at_an_average(self):
        evidence = self.evidence(serial=True, quantity="2")
        evidence["row"].serial_no = "S1\nS2"
        evidence["row"].batch_no = "B1"
        evidence["ledger"] = [Record(actual_qty=-2, serial_no="S1\nS2", batch_no="B1", stock_value_difference=-30)]
        with self.assertRaisesRegex(ValidationError, "不能用整行均价推测"):
            self.m._original_pools(self.source(evidence), evidence, Decimal("30"))

    def test_serial_non_wise_valuation_rejects_mixed_prices_even_when_sle_total_matches(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.bundle(evidence, [("S1", "B1", 1, 3), ("S2", "B1", 1, 27)])
        evidence["item"].use_serial_no_wise_valuation = 0
        evidence["ledger"] = [Record(actual_qty=-2, stock_value_difference=-30, serial_and_batch_bundle="ORIGINAL-BUNDLE")]
        self.frappe.get_all = self.frappe.get_list = lambda *args, **kwargs: ["ORIGINAL-BUNDLE"]
        with self.assertRaisesRegex(ValidationError, "未启用逐号估值且原行序列价格不一致"):
            self.m._original_pools(self.source(evidence), evidence, Decimal("30"))

    def test_serial_non_wise_valuation_supports_uniform_original_price_without_changing_settings(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.bundle(evidence, [("S1", "B1", 1, 8), ("S2", "B1", 1, 8)])
        evidence["item"].use_serial_no_wise_valuation = 0
        evidence["ledger"] = [Record(actual_qty=-2, stock_value_difference=-16, serial_and_batch_bundle="ORIGINAL-BUNDLE")]
        self.frappe.get_all = self.frappe.get_list = lambda *args, **kwargs: ["ORIGINAL-BUNDLE"]
        evidence["pools"] = self.m._original_pools(self.source(evidence), evidence, Decimal("16"))
        self.assertTrue(evidence["serial_row_valuation"])
        doc, row = self.returned(evidence, batch="B1", serial="S2")
        chosen = self.m._selected_pools(self.source(evidence), doc, row, evidence)
        self.assertEqual(self.m._selected_value(evidence, chosen, {}), Decimal("8"))
        self.assertEqual(evidence["item"].use_serial_no_wise_valuation, 0)

    def test_native_new_bundle_serial_gate_accepts_not_yet_calculated_total_but_exact_selected_qty(self):
        evidence = self.evidence(serial=True, quantity="1")
        self.original_pools(evidence, [("S1", "B1", 1, 10)])
        selected = self.bundle(evidence, [("S1", "B1", 1, 999)], original=False, status=0, total=0)
        doc, row = self.returned(evidence, batch="B1", serial="S1", bundle="RETURN-BUNDLE")
        pools = self.m._selected_pools(self.source(evidence), doc, row, evidence, bundle=selected, allow_draft=True)
        self.assertEqual(self.m._selected_value(evidence, pools, {}), Decimal("10"))
        row.qty = row.transfer_qty = 2
        with self.assertRaisesRegex(ValidationError, "所选|明选"):
            self.m._selected_pools(self.source(evidence), doc, row, evidence, bundle=selected, allow_draft=True)

    def test_draft_keeps_selected_serials_reprices_them_and_submit_refuses_blank_selection(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.original_pools(evidence, [("S1", "B1", 1, 3), ("S2", "B1", 1, 27)])
        source = self.source(evidence)
        doc, row = self.returned(evidence, batch="B1", serial="S2")
        doc.is_new = lambda: True
        doc.set = lambda key, value: doc.__setitem__(key, value)
        with patch.object(self.m, "_lock_source"), patch.object(self.m, "_source", return_value=source), \
             patch.object(self.m, "_source_rows", return_value={"ORIGINAL-ROW": evidence}), \
             patch.object(self.m, "_source_rates", return_value={"ORIGINAL-ROW": Decimal("15")}), \
             patch.object(self.m, "_related", return_value={}), patch.object(self.m, "_permitted_fields"), \
             patch.object(self.m, "_permitted_table"):
            self.m.validate_material_return(doc)
            self.assertEqual(row.serial_no, "S2")
            self.assertEqual(row.batch_no, "B1")
            self.assertEqual(row.basic_rate, 27)
            row.serial_no = row.batch_no = None
            self.m.validate_material_return(doc)
            self.assertEqual(row.basic_rate, 0)
            doc.docstatus = 1; doc._action = "submit"
            with self.assertRaisesRegex(ValidationError, "明选原领料实物后提交"):
                self.m.validate_material_return(doc)

    def test_canceled_returns_release_lot_allocation_but_current_submission_is_not_double_counted(self):
        evidence = self.evidence()
        self.original_pools(evidence, [(None, "B-EXPENSIVE", 4, 20)])
        source = self.source(evidence)
        submitted, row = self.returned(evidence, status=1)
        submitted.name = "PRIOR"; row.basic_rate = 20
        canceled = Record(submitted); canceled.name = "CANCELED"; canceled.docstatus = 2
        current = Record(submitted); current.name = "CURRENT"
        used = self.m._submitted_lots(source, {"ORIGINAL-ROW": evidence},
            {"PRIOR": submitted, "CANCELED": canceled, "CURRENT": current}, "CURRENT")
        self.assertEqual(used["ORIGINAL-ROW"][("batch", "B-EXPENSIVE")], Decimal("1"))

    def test_posted_bundle_cannot_hide_wrong_per_serial_price_behind_correct_row_average(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.original_pools(evidence, [("S1", "B1", 1, 3), ("S2", "B1", 1, 27)])
        chosen = self.bundle(evidence, [("S1", "B1", 1, 15), ("S2", "B1", 1, 15)], original=False)
        doc, row = self.returned(evidence, quantity="2", batch="B1", serial="S1\nS2", bundle="RETURN-BUNDLE", status=1)
        row.basic_rate = 15
        with self.assertRaisesRegex(ValidationError, "逐批/逐号价值"):
            self.m._submitted_lots(self.source(evidence), {"ORIGINAL-ROW": evidence}, {doc.name: doc}, None)

    def test_current_locked_return_names_must_match_visible_snapshot(self):
        self.frappe.db = types.SimpleNamespace(sql=lambda *args, **kwargs: [("NEW-RETURN",)])
        self.frappe.get_all = self.frappe.get_list = lambda *args, **kwargs: []
        with self.assertRaisesRegex(ValidationError, "本次读取期间已变化"):
            self.m._related(Record(name="ISSUE"), lock=True)
        with self.assertRaisesRegex(ValidationError, "退库关联已变化"):
            self.m.assert_no_material_returns("ISSUE")

    def test_material_receipt_other_vouchers_and_ordinary_bundle_use_native_super(self):
        calls = []
        class Native:
            def get_return_against(self, parent=None):
                calls.append("return"); return "NATIVE"
            def get_valuation_rate_for_return_entry(self, source):
                calls.append(source); return {"native": True}
            def allow_existing_serial_nos(self):
                calls.append("existing"); return "NATIVE"
        cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record, Native), {})
        bundle = cls(voucher_type="Purchase Receipt", type_of_transaction="Inward")
        self.assertEqual(bundle.get_return_against(), "NATIVE")
        self.assertEqual(bundle.get_valuation_rate_for_return_entry("SOURCE"), {"native": True})
        self.assertEqual(bundle.allow_existing_serial_nos(), "NATIVE")
        self.assertEqual(calls, ["return", "SOURCE", "existing"])

    def test_native_return_mapping_retains_each_serial_rate_and_batch_membership(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.original_pools(evidence, [("S1", "B1", 1, 3), ("S2", "B1", 1, 27)])
        cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record), {})
        with patch.object(self.m, "_material_bundle_context", return_value=(Record(name="ISSUE"), None, None, evidence)):
            mapped = cls().get_valuation_rate_for_return_entry("ISSUE")
            self.assertEqual(mapped["serial_nos"], {"S1": 3.0, "S2": 27.0})
            self.assertIn("B1", mapped["batches"])
            with self.assertRaisesRegex(ValidationError, "原领料来源不一致"):
                cls().get_valuation_rate_for_return_entry("OTHER")

    def native_context(self, evidence, doc):
        """Keep real lot authorization; isolate only external document reads."""
        from contextlib import ExitStack
        stack = ExitStack()
        self.docs[("Stock Entry", doc.name)] = doc
        stack.enter_context(patch.object(self.m, "_lock_source"))
        stack.enter_context(patch.object(self.m, "_source", return_value=self.source(evidence)))
        stack.enter_context(patch.object(self.m, "_source_rows", return_value={"ORIGINAL-ROW": evidence}))
        stack.enter_context(patch.object(self.m, "_source_rates"))
        stack.enter_context(patch.object(self.m, "_related", return_value={}))
        stack.enter_context(patch.object(self.m, "_permitted_fields"))
        stack.enter_context(patch.object(self.m, "_permitted_table"))
        return stack

    def test_submit_prices_each_lot_after_authorization_not_at_initial_row_average(self):
        evidence = self.evidence(serial=True, quantity="2")
        self.original_pools(evidence, [("S-LOW", "B1", 1, 3), ("S-HIGH", "B1", 1, 27)])
        selected = self.bundle(evidence, [("S-LOW", "B1", 1, 15), ("S-HIGH", "B1", 1, 15)],
                               original=False, status=1)
        doc, _ = self.returned(evidence, quantity="2", batch="B1", serial="S-LOW\nS-HIGH",
                               bundle=selected.name, status=1)
        calls = []
        class Native:
            # This is the installed native lifecycle: identify return source,
            # obtain exact original rates, then write each entry's value. The
            # initial row average must not fail the first authorization gate.
            def set_incoming_rate(native, **kwargs):
                calls.append(kwargs)
                source = native.get_return_against(parent=kwargs.get("parent"))
                rates = native.get_valuation_rate_for_return_entry(source)
                for entry in native.entries:
                    entry.incoming_rate = rates["serial_nos"][entry.serial_no]
                    entry.stock_value_difference = entry.qty * Decimal(str(entry.incoming_rate))
                return "NATIVE"
        cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record, Native), {})
        bundle = cls(selected); bundle.is_new = lambda: False
        with self.native_context(evidence, doc):
            self.assertEqual(bundle.set_incoming_rate(parent=doc, row=doc.items[0], save=True,
                                                     allow_negative_stock=False, prev_sle="PREVIOUS"), "NATIVE")
        self.assertEqual([entry.incoming_rate for entry in bundle.entries], [3.0, 27.0])
        self.assertEqual(calls[0]["prev_sle"], "PREVIOUS")
        self.assertTrue(calls[0]["save"])

    def test_native_pending_child_defaults_and_total_do_not_lose_explicit_original_selection(self):
        evidence = self.evidence(serial=True, quantity="1")
        self.original_pools(evidence, [("S1", "B1", 1, 10)])
        selected = self.bundle(evidence, [("S1", "B1", 1, 0)], original=False, status=1, total=0)
        selected.is_new = lambda: False
        for key in ("incoming_rate", "stock_value_difference", "item_code", "warehouse"):
            selected.entries[0][key] = None
        doc, _ = self.returned(evidence, batch="B1", serial="S1", bundle=selected.name, status=1)
        with self.native_context(evidence, doc):
            context = self.m._material_bundle_context(selected, doc)
            self.assertIs(context[3], evidence)
        # Stored/terminal bundles still need complete native child identities
        # and totals. The relaxation exists only inside pricing callbacks.
        with self.assertRaises(ValidationError):
            self.m._selected_pools(self.source(evidence), doc, doc.items[0], evidence, bundle=selected)
        selected.entries[0].item_code = "OTHER"
        with self.native_context(evidence, doc), self.assertRaisesRegex(ValidationError, "父凭证不一致"):
            self.m._material_bundle_context(selected, doc)

    def test_native_rate_result_cannot_substitute_whole_row_average_or_drop_zero_value(self):
        for rates in ((3, 27), (0, 30)):
            evidence = self.evidence(serial=True, quantity="2")
            self.original_pools(evidence, [("S1", "B1", 1, rates[0]), ("S2", "B1", 1, rates[1])])
            selected = self.bundle(evidence, [("S1", "B1", 1, 15), ("S2", "B1", 1, 15)],
                                   original=False, status=1)
            doc, _ = self.returned(evidence, quantity="2", batch="B1", serial="S1\nS2",
                                   bundle=selected.name, status=1)
            class Native:
                def set_incoming_rate(native, **kwargs):
                    return None
            cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record, Native), {})
            bundle = cls(selected); bundle.is_new = lambda: False
            with self.native_context(evidence, doc), self.assertRaisesRegex(ValidationError, "逐批/逐号价值"):
                bundle.set_incoming_rate(parent=doc)

    def test_native_incoming_rate_for_unrelated_bundle_keeps_all_native_arguments(self):
        calls = []
        class Native:
            def set_incoming_rate(self, **kwargs):
                calls.append(kwargs); return "NORMAL"
        cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record, Native), {})
        bundle = cls(voucher_type="Purchase Receipt")
        self.assertEqual(bundle.set_incoming_rate(parent="PARENT", row="ROW", save=True,
            allow_negative_stock=True, prev_sle="PREVIOUS"), "NORMAL")
        self.assertEqual(calls, [{"parent": "PARENT", "row": "ROW", "save": True,
                                 "allow_negative_stock": True, "prev_sle": "PREVIOUS"}])

    def test_serial_non_wise_native_skip_still_records_exact_uniform_rate_and_zero(self):
        for original_rate in (8, 0):
            evidence = self.evidence(serial=True, quantity="2")
            self.original_pools(evidence, [("S1", "B1", 1, original_rate), ("S2", "B1", 1, original_rate)])
            evidence["item"].use_serial_no_wise_valuation = 0
            evidence["serial_row_valuation"] = True
            selected = self.bundle(evidence, [("S2", "B1", 1, 99)], original=False, status=1)
            recorded = []
            selected.entries[0].db_set = lambda fields: recorded.append(dict(fields))
            doc, row = self.returned(evidence, batch="B1", serial="S2", bundle=selected.name, status=1)
            row.basic_rate = original_rate
            class Native:
                def set_incoming_rate(native, **kwargs):
                    # Installed native function skips rate updates for this
                    # setting. Parent Stock Entry sets the same historical
                    # incoming row price before native ledger calculation.
                    return "SKIPPED"
            cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record, Native), {})
            bundle = cls(selected); bundle.is_new = lambda: False
            with self.native_context(evidence, doc):
                self.assertEqual(bundle.set_incoming_rate(parent=doc, row=row, save=False), "SKIPPED")
                self.assertEqual(recorded, [], "draft calculation cannot force database writes")
                bundle.set_incoming_rate(parent=doc, row=row, save=True)
            self.assertEqual(recorded, [{"incoming_rate": float(original_rate),
                                        "stock_value_difference": float(original_rate)}])
            self.assertEqual(row.serial_no, "S2")
            self.assertEqual(evidence["item"].use_serial_no_wise_valuation, 0)

    def test_independent_bundle_editor_cannot_unlink_or_rebind_original_return_after_native_lock(self):
        import sys
        source_scope = Record(voucher_type="Stock Entry", voucher_no="RETURN", voucher_detail_no="RETURN-ROW",
                              company="JF", item_code="RAW", warehouse="Stores - JF", type_of_transaction="Inward")
        calls = []
        class Native:
            def check_if_latest(self):
                calls.append("native"); return None
        cls = type("Bundle", (self.m.MaterialReturnBundleMixin, Record, Native), {})
        self.frappe.db = types.SimpleNamespace(get_value=lambda dt, name, fields, **kwargs:
            source_scope if dt == self.m.BUNDLE else "ISSUE" if name == "RETURN" else None)
        locking = types.ModuleType("jifeng_erp.material_issue_locking")
        locking._lock_issue_sources = lambda sources: calls.append(("source", tuple(sorted(sources))))
        for field, value in (("voucher_no", "OTHER"), ("voucher_detail_no", "OTHER-ROW"),
                             ("warehouse", "OTHER"), ("type_of_transaction", "Outward")):
            bundle = cls(source_scope); bundle.name = "BUNDLE"; bundle[field] = value
            bundle.is_new = lambda: False; bundle.get_doc_before_save = lambda: source_scope
            with patch.dict(sys.modules, {"jifeng_erp.material_issue_locking": locking}), \
                 patch.object(self.m, "_lock_flows", side_effect=lambda _: calls.append("flow")), \
                 self.assertRaisesRegex(ValidationError, "不可改绑"):
                bundle.check_if_latest()
            self.assertEqual(calls[-3:], ["flow", ("source", ("ISSUE",)), "native"])


if __name__ == "__main__":
    unittest.main()
