"""Preview provenance risks through the real complete native source service."""

import importlib.util
from pathlib import Path
import sys
import subprocess
import types
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from jifeng_erp.costing import CostingError, receipt_batch_quantities


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("receipt_native_harness", ROOT / "tests/test_flow_cost_sources.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
Record = harness.Record


class TestReceiptBatches(unittest.TestCase):
    def setUp(self):
        self.native = harness.TestFlowCostSources()
        self.native.setUp()
        self.addCleanup(self.native.doCleanups)
        self.frappe = self.native.frappe
        self.frappe.whitelist = lambda: lambda function: function
        utils = sys.modules["frappe.utils"]
        utils.add_days, utils.cint = lambda *args: None, int
        utils.today, utils.now = lambda: "2026-10-03", lambda: "2026-10-03 10:00:00"
        document = types.ModuleType("frappe.model.document")
        document.Document = type("Document", (), {
            "get": lambda self, key, default=None: getattr(self, key, default),
            "get_doc_before_save": lambda self: getattr(self, "_before", None),
            "as_dict": lambda self: {key: getattr(self, key) for key in (
                "modified", "company", "item_code", "flow_order", "planned_qty", "batches",
                "material_amount", "processing_amount", "other_amount", "cost_basis",
                "receipt_cost_loaded_at", "receipt_cost_sources", "note") if hasattr(self, key)},
            "update": lambda self, values: [setattr(self, key, value) for key, value in values.items()],
        })
        patches = patch.dict(sys.modules, {"frappe.model.document": document,
                                           "jifeng_erp.flow_cost_sources": self.native.module})
        patches.start()
        self.addCleanup(patches.stop)
        self.workflow = self.load("receipt_workflow", "jifeng_erp/workflow.py")
        self.preview_module = self.load("receipt_preview", "jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.py")
        patches = patch.dict(sys.modules, {"jifeng_erp.workflow": self.workflow})
        patches.start()
        self.addCleanup(patches.stop)
        self.flow = self.native.documents[self.native.module.FLOW, "FLOW"]
        self.receipt, self.detail = self.native.receipt(qty=2, rejected=1, loss=1)
        self.native.documents.pop(("Subcontracting Receipt Item", self.detail.name))
        self.detail.name = "ROW"
        self.native.documents["Subcontracting Receipt Item", "ROW"] = self.detail
        self.native.documents["Subcontracting Receipt Supplied Item", "SCR-RM"].reference_name = "ROW"
        for suffix in ("FG", "RJ"):
            self.native.documents[self.native.module.SLE, f"SCR-{suffix}-SLE"].voucher_detail_no = "ROW"
        self.preview = self.preview_module.JifengCostPreview()
        self.preview.company, self.preview.item_code, self.preview.flow_order = "JF", "FG", "FLOW"
        self.preview.batches = [Record(source_receipt="SCR", source_receipt_item="ROW", received_qty=4, lost_qty=1)]

    def saving_preview(self, basis="流程已过账实际", loaded_at="2026-10-03 09:00:00"):
        class Batch(Record):
            def as_dict(self):
                return dict(self)

        self.preview.doctype, self.preview.name = "Jifeng Cost Preview", "PREVIEW"
        self.preview.modified = "2026-10-03 09:00:00"
        self.preview.cost_basis, self.preview.receipt_cost_loaded_at = basis, loaded_at
        self.preview.receipt_cost_sources = "SCR"
        self.preview.planned_qty = 4
        self.preview.material_amount, self.preview.processing_amount, self.preview.other_amount = 100, 200, 30
        self.preview.note = "pending input"
        self.preview.batches = [Batch(receipt_type="首次回货", received_qty=4, good_qty=2,
            rework_qty=1, lost_qty=1, pending_qty=0, extra_processing_amount=0,
            source_receipt=None, source_receipt_item=None)]
        self.preview._before = Record(cost_basis=basis, receipt_cost_loaded_at=loaded_at)
        self.preview.check_permission = Mock()
        self.preview.reload = Mock()
        self.preview.save = Mock(side_effect=self.preview.validate)
        self.preview.is_new = lambda: False
        self.preview.flags = types.SimpleNamespace()
        self.frappe.db.get_value = Mock(return_value="PREVIEW")
        # Inventory-unit qualification has its own existing Flow tests. The
        # actual source service below still checks Item/warehouse/UOM/SLE.
        self.workflow.stock_plan_quantity = Mock(return_value=Decimal(4))
        self.frappe.get_doc.side_effect = lambda doctype, name, **kwargs: self.native.get_doc(doctype, name)
        return self.preview

    def assert_no_posting(self):
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql,
                       self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def sync_native_quantity(self):
        """Represent an internally consistent newly posted source revision."""
        received = self.detail.qty + self.detail.rejected_qty + self.detail.process_loss_qty
        self.detail.received_qty, self.detail.rm_supp_cost, self.detail.amount = received, received, received * 3
        supplied = self.native.documents["Subcontracting Receipt Supplied Item", "SCR-RM"]
        supplied.consumed_qty, supplied.amount = received, received
        fg = self.native.documents[self.native.module.SLE, "SCR-FG-SLE"]
        fg.actual_qty, fg.stock_value_difference = self.detail.qty, received * 3
        rm = self.native.documents[self.native.module.SLE, "SCR-RM-SLE"]
        rm.actual_qty, rm.stock_value_difference = -received, -received

    def return_qty(self, qty):
        accepted = min(qty, self.detail.qty)
        rejected = qty - accepted
        _parent, detail = self.native.receipt("RETURN", qty=-accepted, rejected=-rejected,
            loss=0, is_return=1, original="SCR", flow=None)
        detail.subcontracting_receipt_item = "ROW"
        return detail

    @staticmethod
    def load(name, relative_path):
        spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_candidate_includes_rejected_and_process_loss(self):
        row = self.workflow.receipt_batch_candidates("FLOW")[0]
        self.assertEqual(row["qty"], Decimal(4))
        self.assertEqual(row["lost_qty"], Decimal(1))
        self.assertEqual(row["receipt_item"], "ROW")
        self.preview._validate_batch_sources()

    def test_unreadable_receipt_is_not_exposed(self):
        self.native.denied.add((self.native.module.SCR, "SCR", "read"))
        with self.assertRaises(PermissionError):
            self.workflow.receipt_batch_candidates("FLOW")

    def test_partial_return_reduces_net_quantity_and_keeps_loss(self):
        self.return_qty(1)
        row = self.workflow.receipt_batch_candidates("FLOW")[0]
        self.assertEqual((row["qty"], row["lost_qty"], row["returned_qty"]), (3, 1, 1))

    def test_duplicate_source_cannot_be_saved(self):
        self.preview.batches.append(Record(self.preview.batches[0]))
        with self.assertRaisesRegex(ValueError, "重复"):
            self.preview._validate_batch_sources()

    def test_source_quantity_change_requires_reconciliation(self):
        self.detail.qty = 1
        self.sync_native_quantity()
        with self.assertRaisesRegex(ValueError, "净回货数量已变化"):
            self.preview._validate_batch_sources()

    def test_confirmed_source_loss_cannot_be_reclassified_as_good(self):
        self.preview.batches[0].lost_qty = 0
        with self.assertRaisesRegex(ValueError, "工艺损耗"):
            self.preview._validate_batch_sources()

    def test_other_flow_source_cannot_be_used(self):
        self.detail.jf_flow_order = "OTHER"
        with self.assertRaisesRegex(ValueError, "流程单不一致"):
            self.preview._validate_batch_sources()

    def test_cancelled_source_cannot_be_saved(self):
        self.receipt.docstatus = 2
        with self.assertRaisesRegex(ValueError, "未提交"):
            self.preview._validate_batch_sources()

    def test_changed_return_requires_classification_reconciliation(self):
        self.return_qty(1)
        with self.assertRaisesRegex(ValueError, "净回货数量已变化"):
            self.preview._validate_batch_sources()
        self.preview.batches[0].received_qty = 3
        self.preview._validate_batch_sources()

    def test_all_physical_goods_returned_keeps_confirmed_loss(self):
        self.return_qty(3)
        row = self.workflow.receipt_batch_candidates("FLOW")[0]
        self.assertEqual((row["qty"], row["lost_qty"]), (1, 1))

    def test_full_return_without_loss_requires_batch_removal(self):
        self.detail.process_loss_qty = 0
        self.sync_native_quantity()
        self.return_qty(3)
        with self.assertRaisesRegex(ValueError, "全额退回"):
            self.preview._validate_batch_sources()

    def test_inaccessible_returns_cannot_be_silently_omitted(self):
        self.return_qty(1)
        self.native.list_denied.add((self.native.module.SCR, "RETURN"))
        with self.assertRaises(PermissionError):
            self.workflow.receipt_batch_candidates("FLOW")

    def test_return_without_source_detail_cannot_be_ignored(self):
        self.return_qty(1).subcontracting_receipt_item = None
        with self.assertRaisesRegex(ValueError, "来源"):
            self.workflow.receipt_batch_candidates("FLOW")

    def test_return_cannot_remove_confirmed_loss(self):
        with self.assertRaises(CostingError):
            receipt_batch_quantities(self.detail, 4)

    def test_real_service_denial_prevents_explicit_trial_write(self):
        self.preview.doctype, self.preview.name = "Jifeng Cost Preview", "PREVIEW"
        self.preview.receipt_cost_loaded_at = None
        self.preview.check_permission = Mock()
        self.preview.reload = Mock()
        self.preview.save = Mock()
        self.frappe.db.get_value = Mock(return_value="PREVIEW")
        self.native.list_denied.add((self.native.module.SLE, "SCR-RM-SLE"))
        with self.assertRaises(PermissionError):
            self.preview.load_posted_costs()
        self.preview.save.assert_not_called()
        self.assertFalse(hasattr(self.preview, "material_amount"))

    def test_explicit_load_uses_completed_fee_closure_before_trial_write(self):
        self.preview.doctype, self.preview.name = "Jifeng Cost Preview", "PREVIEW"
        self.preview.receipt_cost_loaded_at = None
        self.preview.check_permission, self.preview.reload, self.preview.save = Mock(), Mock(), Mock()
        self.frappe.db.get_value = Mock(return_value="PREVIEW")
        result = self.preview.load_posted_costs()
        self.assertEqual(result["totals"]["material"], 4)
        self.assertEqual((self.preview.material_amount, self.preview.processing_amount), (4, 8))
        self.assertEqual(self.preview.receipt_cost_sources, "SCR")
        self.preview.save.assert_called_once_with()


    def test_posted_save_reprices_new_issue_and_receipt_then_cancelled_net_sources(self):
        preview = self.saving_preview()
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost, preview.batches[0].estimated_cost), ("12", "4", "8"))
        issue, _row = self.native.issue(qty=2, factor=1)
        preview.save()
        self.assertEqual((preview.material_amount, preview.total_cost, preview.estimated_unit_cost,
                          preview.batches[0].estimated_cost), (12, "20", "6.6667", "13.33"))
        second, _row = self.native.receipt("SECOND", qty=1, rejected=0, loss=0)
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost), ("23", "7.6667"))
        self.assertEqual(preview.receipt_cost_sources, "ISSUE\nSCR\nSECOND")
        issue.docstatus = 2
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost), ("15", "5"))
        second.docstatus = 2
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost), ("12", "4"))
        self.receipt.docstatus = 2
        preview.save()
        self.assertEqual((preview.material_amount, preview.processing_amount, preview.other_amount,
                          preview.total_cost, preview.estimated_unit_cost, preview.batches[0].estimated_cost), (0, 0, 0, "0", "0", "0"))
        self.assertIsNone(preview.receipt_cost_sources)
        self.assertEqual(preview.cost_basis, "流程已过账实际")
        self.assert_no_posting()

    def test_receipt_scope_never_silently_includes_material_issues(self):
        preview = self.saving_preview("委外回货实际")
        self.native.issue(qty=2, factor=1)
        preview.save()
        self.assertEqual((preview.material_amount, preview.total_cost, preview.estimated_unit_cost), (4, "12", "4"))
        self.assertEqual(preview.receipt_cost_sources, "SCR")
        self.receipt.docstatus = 2
        preview.save()
        self.assertEqual((preview.material_amount, preview.total_cost, preview.estimated_unit_cost), (0, "0", "0"))
        self.assertEqual(preview.cost_basis, "委外回货实际")
        self.assert_no_posting()

    def test_legacy_estimate_keeps_manual_amounts_without_source_refresh(self):
        preview = self.saving_preview(None, None)
        self.native.denied.add((self.native.module.SCR, "SCR", "read"))
        preview.save()
        self.assertEqual((preview.cost_basis, preview.material_amount, preview.processing_amount,
                          preview.other_amount, preview.total_cost, preview.estimated_unit_cost), ("流程估算", 100, 200, 30, "330", "110"))
        self.assertIsNone(preview.receipt_cost_loaded_at)
        self.assert_no_posting()

    def test_unknown_historical_scope_cannot_be_hidden_by_client_defaults(self):
        for incoming_basis, incoming_time in [(None, "2026-10-03"), ("流程估算", None), ("流程已过账实际", None)]:
            preview = self.saving_preview(None)
            preview.cost_basis, preview.receipt_cost_loaded_at = incoming_basis, incoming_time
            with self.subTest(basis=incoming_basis), self.assertRaisesRegex(ValueError, "口径尚未确认"):
                preview.save()
            self.assertEqual(preview.material_amount, 100)
        self.assert_no_posting()

    def test_readonly_scope_cannot_be_changed_or_forged_on_regular_save(self):
        for incoming in ["流程估算", "流程已过账实际", "", "unknown"]:
            preview = self.saving_preview("委外回货实际")
            preview.cost_basis = incoming
            preview._cost_basis_selection = True  # JSON cannot produce the server object identity.
            with self.subTest(incoming=incoming), self.assertRaisesRegex(ValueError, "只能通过"):
                preview.save()
        self.assert_no_posting()

    def test_explicit_legacy_choice_keeps_pending_inputs_and_normal_modified_value(self):
        preview = self.saving_preview(None)
        pending_batches = preview.batches
        original_modified = preview.modified
        def reloaded_saved_document():
            preview.note = "old saved input"
            preview.batches = []
            preview.modified = "a newer saved version"
        preview.reload.side_effect = reloaded_saved_document
        result = preview.load_receipt_costs(preserve_input=True)
        self.assertEqual(preview.cost_basis, "委外回货实际")
        self.assertEqual((preview.note, preview.modified), ("pending input", original_modified))
        self.assertIs(preview.batches, pending_batches)
        self.assertEqual((result["totals"]["material"], preview.total_cost, preview.estimated_unit_cost), (4, "12", "4"))
        self.assertFalse(hasattr(preview, "_cost_basis_selection"))
        preview.check_permission.assert_any_call("write")
        self.assert_no_posting()

    def test_preserved_input_keeps_normal_concurrent_version_rejection(self):
        preview = self.saving_preview(None)
        client_modified = preview.modified
        persisted = {"modified": "2026-10-03 11:00:00", "writes": 0}
        def reload_latest():
            preview.modified = persisted["modified"]
        def normal_optimistic_save():
            for flag in ("ignore_version", "ignore_validate", "ignore_permissions"):
                self.assertFalse(getattr(preview.flags, flag, False))
            # Same-version native source: set_user_and_timestamp captures
            # the incoming modified before check_if_latest compares it.
            preview._original_modified = preview.modified
            preview.modified = "2026-10-03 12:00:00"
            if str(preview._original_modified) != str(persisted["modified"]):
                raise ValueError("normal TimestampMismatchError")
            preview.validate()
            persisted["writes"] += 1
        preview.reload.side_effect = reload_latest
        preview.save.side_effect = normal_optimistic_save
        with self.assertRaisesRegex(ValueError, "TimestampMismatchError"):
            preview.load_receipt_costs(preserve_input=True)
        self.assertEqual(preview._original_modified, client_modified)
        self.assertEqual(persisted["writes"], 0)
        self.assertFalse(hasattr(preview, "_cost_basis_selection"))
        self.assert_no_posting()

    def test_explicit_flow_estimate_is_not_overwritten_by_prior_actual_scope(self):
        preview = self.saving_preview("流程已过账实际")
        preview.batches = []
        self.flow.planned_qty, self.flow.material_amount, self.flow.processing_amount = 4, 30, 15
        self.flow.online_order_id, self.flow.sales_order = "EXT", None
        preview.load_flow_costs()
        self.assertEqual((preview.cost_basis, preview.total_cost, preview.estimated_unit_cost), ("流程估算", "45", "11.25"))
        self.assertIsNone(preview.receipt_cost_loaded_at)
        self.assertIsNone(preview.receipt_cost_sources)
        self.assertFalse(hasattr(preview, "_cost_basis_selection"))
        self.assert_no_posting()

    def test_actual_save_keeps_read_company_item_stock_unit_and_batch_gates(self):
        preview = self.saving_preview()
        self.native.denied.add((self.native.module.SCR, "SCR", "read"))
        with self.assertRaises(PermissionError):
            preview.save()
        self.assertEqual(preview.material_amount, 100)
        self.native.denied.clear()
        self.flow.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "公司、商品"):
            preview.save()
        self.flow.company = "JF"
        self.native.documents["Item", "FG"].stock_uom = "OTHER"
        with self.assertRaises(ValueError):
            preview.save()
        self.native.documents["Item", "FG"].stock_uom = self.detail.stock_uom
        self.workflow.stock_plan_quantity.side_effect = ValueError("库存单位未确认")
        with self.assertRaisesRegex(ValueError, "库存单位未确认"):
            preview.save()
        self.assert_no_posting()

    def test_failed_native_save_keeps_source_selection_marker_private_and_restored(self):
        preview = self.saving_preview(None)
        preview.save.side_effect = ValueError("normal save failed")
        with self.assertRaisesRegex(ValueError, "normal save failed"):
            preview.load_receipt_costs()
        self.assertFalse(hasattr(preview, "_cost_basis_selection"))
        self.assert_no_posting()

    def test_recalculate_unchanged_form_keeps_scope_batches_and_clears_cancelled_costs(self):
        preview = self.saving_preview()
        preview.recalculate_actual_costs()
        self.assertEqual((preview.total_cost, preview.cost_basis), ("12", "流程已过账实际"))
        batches = preview.batches
        issue, _row = self.native.issue(qty=2, factor=1)
        preview.recalculate_actual_costs()
        self.assertEqual((preview.total_cost, preview.receipt_cost_sources), ("20", "ISSUE\nSCR"))
        self.assertIs(preview.batches, batches)
        issue.docstatus = self.receipt.docstatus = 2
        preview.recalculate_actual_costs()
        self.assertEqual((preview.total_cost, preview.receipt_cost_sources, preview.cost_basis),
                         ("0", None, "流程已过账实际"))
        self.assertIs(preview.batches, batches)
        self.assert_no_posting()

    def test_clean_form_recalculation_calls_normal_method_without_dirtying_or_selecting_basis(self):
        program = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let handlers,confirmation;const calls=[];
const frappe={ui:{form:{on:(_,h)=>handlers=h}},utils:{escape_html:v=>v},
 call:async()=>({message:{issues:[{issue:'SECOND'}],receipts:[],manufactures:[],
 totals:{material:16,processing:0,other:0},currency:'CNY'}}),msgprint:v=>{throw Error(v);},
 confirm:(message,yes,no)=>confirmation={message,yes,no}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{frappe,format_currency:v=>String(v)});
(async()=>{
 const buttons={},doc={name:'CP',flow_order:'FLOW',cost_basis:'流程已过账实际',receipt_cost_loaded_at:'OLD',batches:[{good_qty:10}]};
 const before=JSON.stringify(doc),frm={doc,is_new:()=>false,is_dirty:()=>false,set_intro:()=>{},
 add_custom_button:(n,f)=>buttons[n]=f,dirty:()=>{throw Error('no forced dirty');},
 save:()=>{throw Error('no synthetic form save');},call:async(method,args)=>calls.push({method,args}),reload_doc:async()=>{}};
 handlers.refresh(frm);const pending=buttons['重新核算当前实际费用']();await new Promise(r=>setImmediate(r));
 assert.match(confirmation.message,/材料费 16/);assert.equal(JSON.stringify(doc),before);
 confirmation.yes();await pending;assert.equal(calls.length,1);
 assert.equal(calls[0].method,'recalculate_actual_costs');assert.equal(calls[0].args.preserve_input,false);
 assert.equal(JSON.stringify(doc),before);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", program, str(ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.js")],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manufacturing_only_selection_and_later_normal_saves_refresh_money_and_sources(self):
        self.receipt.docstatus = 2
        preview = self.saving_preview("流程估算", None)
        preview.planned_qty = 5
        preview.batches[0].received_qty = preview.batches[0].good_qty = 5
        preview.batches[0].lost_qty = preview.batches[0].rework_qty = 0
        first = self.native.manufacture(other=6)
        preview.load_posted_costs(preserve_input=True)
        self.assertEqual((preview.material_amount, preview.other_amount, preview.total_cost,
                          preview.estimated_unit_cost, preview.receipt_cost_sources), (40, 6, "46", "9.2", "MAKE"))
        self.assertEqual(preview.note, "pending input")
        preview._before = Record(cost_basis="流程已过账实际", receipt_cost_loaded_at=preview.receipt_cost_loaded_at)
        second = self.native.manufacture("MAKE2", material=20, other=2, qty=2, raw_qty=2)
        preview.validate()
        self.assertEqual((preview.material_amount, preview.other_amount, preview.total_cost,
                          preview.estimated_unit_cost, preview.receipt_cost_sources), (60, 8, "68", "13.6", "MAKE\nMAKE2"))
        second.docstatus = 2
        preview.validate()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost, preview.receipt_cost_sources), ("46", "9.2", "MAKE"))
        first.docstatus = 2
        preview.validate()
        self.assertEqual((preview.material_amount, preview.other_amount, preview.total_cost,
                          preview.estimated_unit_cost, preview.receipt_cost_sources), (0, 0, "0", "0", None))
        self.assertEqual(preview.cost_basis, "流程已过账实际")
        self.assert_no_posting()

    def test_receipt_basis_does_not_silently_include_manufacturing(self):
        preview = self.saving_preview("委外回货实际")
        self.native.manufacture(material=40, other=6)
        preview.validate()
        self.assertEqual((preview.material_amount, preview.processing_amount, preview.other_amount), (4, 8, 0))
        self.assertEqual(preview.receipt_cost_sources, "SCR")
        self.assert_no_posting()

    def test_manufacturing_read_permission_failure_never_saves_partial_estimate(self):
        preview = self.saving_preview("流程估算", None)
        self.native.manufacture()
        self.native.denied.add((self.native.module.WO, "WO", "read"))
        with self.assertRaises(PermissionError):
            preview.load_posted_costs(preserve_input=True)
        preview.save.assert_not_called()
        self.assertEqual((preview.cost_basis, preview.material_amount, preview.note), ("流程估算", 100, "pending input"))
        self.assert_no_posting()

    def test_dirty_gui_manufacturing_only_cost_source_is_displayed_and_selected_normally(self):
        program = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let handlers,confirmation;const calls=[],messages=[];
const frappe={ui:{form:{on:(_,h)=>handlers=h}},utils:{escape_html:v=>v.replace(/</g,'&lt;').replace(/>/g,'&gt;')},
 call:async()=>({message:{issues:[],receipts:[],manufactures:[{manufacture:'MAKE<1>',work_order:'WO',material:40,other:6}],
 totals:{material:40,processing:0,other:6},currency:'CNY'}}),msgprint:v=>messages.push(v),
 confirm:(message,yes,no)=>confirmation={message,yes,no}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{frappe,format_currency:v=>String(v)});
(async()=>{
 const buttons={},doc={name:'CP',flow_order:'FLOW',cost_basis:'流程估算',receipt_cost_loaded_at:null,note:'pending',batches:[{good_qty:5}]};
 const frm={doc,is_new:()=>false,is_dirty:()=>true,set_intro:()=>{},add_custom_button:(n,f)=>buttons[n]=f,
 save:()=>{throw Error('no automatic pre-save');},call:async(method,args)=>calls.push({method,args,note:doc.note,batches:doc.batches}),reload_doc:async()=>{}};
 handlers.refresh(frm);const selected=buttons['带入已过账实际费用']();await new Promise(r=>setImmediate(r));
 assert.equal(messages.length,0);assert.equal(calls.length,0);assert.match(confirmation.message,/普通制造 MAKE&lt;1&gt;（工单 WO）/);
 assert.match(confirmation.message,/材料费 40.*其他费 6/);assert.match(confirmation.message,/WIP移库.*不重复/);
 confirmation.yes();await selected;assert.equal(calls.length,1);assert.equal(calls[0].method,'load_posted_costs');
 assert.equal(calls[0].args.preserve_input,true);assert.equal(calls[0].note,'pending');assert.strictEqual(calls[0].batches,doc.batches);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", program, str(ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.js")],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_historical_dirty_gui_choice_is_confirmed_without_autosave_or_input_loss(self):
        program = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
let handlers; const confirmations = [], reads = [], calls = [];
const frappe = { ui: { form: { on: (_, value) => handlers = value } },
 db: { get_doc: async () => ({ name:'FLOW', company:'JF', item_code:'FG', planned_qty:4, quantity_basis:'stock_v1', stock_uom:'Nos', material_amount:30, processing_amount:15 }) },
 call: async options => { reads.push(options); return { message:{ rows:[{receipt:'SCR'}], totals:{material:4,processing:8,other:0}, currency:'CNY' } }; },
 confirm: (message, yes, no) => confirmations.push({message,yes,no}), msgprint: message => { throw Error(message); } };
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{frappe,format_currency:value=>String(value)});
(async()=>{
 const buttons={}, doc={name:'CP',flow_order:'FLOW',receipt_cost_loaded_at:'OLD',cost_basis:null,material_amount:123,remark:'pending',batches:[{good_qty:2}]};
 const frm={doc,is_new:()=>false,is_dirty:()=>true,set_intro:()=>{},add_custom_button:(name,fn)=>buttons[name]=fn,
 save:()=>{throw Error('historical ambiguous input must not autosave');},
 call:async(method,args)=>{ calls.push({method,args,remark:doc.remark,batches:doc.batches}); }, reload_doc:async()=>{},
 set_value:async values=>Object.assign(doc,values)};
 handlers.refresh(frm); assert.equal(Object.keys(buttons).length,4);
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 const unchanged=JSON.stringify(doc), cancelled=buttons['带入委外回货实际费用'](); await flush();
 assert.equal(calls.length,0); confirmations.at(-1).no(); await cancelled; assert.equal(JSON.stringify(doc),unchanged);
 const chosen=buttons['带入委外回货实际费用'](); await flush();
 assert.match(confirmations.at(-1).message,/历史实际费用口径尚未确认/); assert.match(confirmations.at(-1).message,/未保存.*输入.*一起.*保存/);
 confirmations.at(-1).yes(); await chosen; assert.equal(calls.length,1);
 assert.equal(calls[0].method,'load_receipt_costs'); assert.equal(calls[0].args.preserve_input,true);
 assert.equal(calls[0].remark,'pending'); assert.strictEqual(calls[0].batches,doc.batches);
 doc.batches=[]; await handlers.flow_order(frm); assert.equal(doc.receipt_cost_loaded_at,'OLD'); assert.equal(doc.cost_basis,null); assert.equal(doc.material_amount,123);
 assert.ok(reads.every(request=>request.method==='jifeng_erp.workflow.receipt_costs'));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", program, str(ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.js")],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
