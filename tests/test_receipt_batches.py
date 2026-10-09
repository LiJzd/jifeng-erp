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
        self.mock_preview_lock()
        # Inventory-unit qualification has its own existing Flow tests. The
        # actual source service below still checks Item/warehouse/UOM/SLE.
        self.workflow.stock_plan_quantity = Mock(return_value=Decimal(4))
        self.frappe.get_doc.side_effect = lambda doctype, name, **kwargs: self.native.get_doc(doctype, name)
        return self.preview

    def mock_preview_lock(self):
        source_value = self.frappe.db.get_value
        self.frappe.db.get_value = Mock(side_effect=lambda doctype, *args, **kwargs:
            "PREVIEW" if doctype == "Jifeng Cost Preview" else source_value(doctype, *args, **kwargs))

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
        self.mock_preview_lock()
        self.native.list_denied.add((self.native.module.SLE, "SCR-RM-SLE"))
        with self.assertRaises(PermissionError):
            self.preview.load_posted_costs()
        self.preview.save.assert_not_called()
        self.assertFalse(hasattr(self.preview, "material_amount"))

    def test_explicit_load_uses_completed_fee_closure_before_trial_write(self):
        self.preview.doctype, self.preview.name = "Jifeng Cost Preview", "PREVIEW"
        self.preview.receipt_cost_loaded_at = None
        self.preview.check_permission, self.preview.reload, self.preview.save = Mock(), Mock(), Mock()
        self.mock_preview_lock()
        result = self.preview.load_posted_costs()
        self.assertEqual(result["totals"]["material"], 4)
        self.assertEqual((self.preview.material_amount, self.preview.processing_amount), (4, 4))
        self.assertEqual(self.preview.receipt_cost_sources, "SCR")
        self.preview.save.assert_called_once_with()


    def test_posted_save_reprices_new_issue_and_receipt_then_cancelled_net_sources(self):
        preview = self.saving_preview()
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost, preview.batches[0].estimated_cost), ("8", "2.6667", "5.33"))
        issue, _row = self.native.issue(qty=2, factor=1)
        preview.save()
        self.assertEqual((preview.material_amount, preview.total_cost, preview.estimated_unit_cost,
                          preview.batches[0].estimated_cost), (12, "16", "5.3333", "10.67"))
        second, _row = self.native.receipt("SECOND", qty=1, rejected=0, loss=0)
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost), ("19", "6.3333"))
        self.assertEqual(preview.receipt_cost_sources, "ISSUE\nSCR\nSECOND")
        issue.docstatus = 2
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost), ("11", "3.6667"))
        second.docstatus = 2
        preview.save()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost), ("8", "2.6667"))
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
        self.assertEqual((preview.material_amount, preview.total_cost, preview.estimated_unit_cost), (4, "8", "2.6667"))
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
        self.assertEqual((result["totals"]["material"], preview.total_cost, preview.estimated_unit_cost), (4, "8", "2.6667"))
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

    def test_linked_box_order_uses_flow_stock_plan_without_changing_batch_classification(self):
        from jifeng_erp.flow_quantity import require_stock_plan, sales_row_snapshot

        snapshot = sales_row_snapshot(Record(qty=1, uom="Box", stock_uom="Nos",
            conversion_factor=144, stock_qty=144), "Nos")
        stock_plan = require_stock_plan(snapshot | {"sales_order": "SO", "planned_qty": 144}, "Nos")
        preview = self.saving_preview(None, None)
        self.workflow.stock_plan_quantity.return_value = stock_plan
        preview.planned_qty = 1  # A copied sales qty is not the cost denominator.
        preview.material_amount, preview.processing_amount, preview.other_amount = 1000, 440, 0
        batch = preview.batches[0]
        batch.update(received_qty=72, good_qty=72, rework_qty=0, lost_qty=0)
        preview.save()
        self.assertEqual((preview.planned_qty, preview.total_cost, preview.estimated_unit_cost),
                         (Decimal(144), "1440", "10"))
        self.assertEqual((preview.good_qty, preview.unreceived_qty, batch.estimated_cost), ("72", "72", "720"))
        self.assertIs(preview.batches[0], batch)
        self.assertEqual((batch.received_qty, batch.good_qty), (72, 72))
        # A later normal save with forged readonly input rereads the same plan.
        preview.planned_qty = 999
        preview.save()
        self.assertEqual((preview.planned_qty, preview.estimated_unit_cost), (Decimal(144), "10"))
        self.assert_no_posting()

    def test_linked_actual_recalculation_rereads_stock_plan_and_cost_sources(self):
        preview = self.saving_preview()
        self.workflow.stock_plan_quantity.return_value = Decimal(12)
        preview.planned_qty = 1
        preview.recalculate_actual_costs()
        self.assertEqual((preview.planned_qty, preview.total_cost, preview.estimated_unit_cost),
                         (Decimal(12), "8", "0.7273"))  # One loss remains classified.
        self.assertEqual((preview.batches[0].good_qty, preview.batches[0].lost_qty), (2, 1))
        self.receipt.docstatus = 2
        preview.recalculate_actual_costs()
        self.assertEqual((preview.planned_qty, preview.total_cost, preview.estimated_unit_cost),
                         (Decimal(12), "0", "0"))
        self.assert_no_posting()

    def test_standalone_manual_trial_retains_explicit_quantity(self):
        preview = self.saving_preview(None, None)
        preview.flow_order = None
        preview.planned_qty = 2
        preview.batches[0].update(received_qty=2, good_qty=2, rework_qty=0, lost_qty=0)
        preview.save()
        self.assertEqual((preview.planned_qty, preview.estimated_unit_cost), (2, "165"))
        self.workflow.stock_plan_quantity.assert_not_called()
        self.assert_no_posting()

    def test_late_flow_plan_change_cannot_save_costs_using_an_earlier_denominator(self):
        preview = self.saving_preview(None, None)
        self.workflow.stock_plan_quantity.side_effect = [Decimal(4), Decimal(12)]
        with self.assertRaisesRegex(ValueError, "计划库存数量已变化"):
            preview.validate()
        self.assert_no_posting()

    def test_flow_link_syncs_inventory_quantity_without_overwriting_existing_classifications(self):
        program = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let handlers,resolve;const properties=[];
const frappe={ui:{form:{on:(_,h)=>handlers=h}},db:{get_doc:()=>new Promise(r=>resolve=r)},
 msgprint:()=>{throw Error('unexpected message');}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{frappe});
(async()=>{
 const batch={received_qty:72,good_qty:60,rework_qty:12},doc={name:'CP',flow_order:'FLOW',planned_qty:1,
   cost_basis:'流程已过账实际',material_amount:123,batches:[batch]};
 const frm={doc,is_new:()=>true,set_intro:()=>{},set_df_property:(...args)=>properties.push(args),
   set_value:async(field,value)=>{assert.equal(field,'planned_qty');doc[field]=value;}};
 handlers.refresh(frm);assert.equal(properties.find(p=>p[0]==='planned_qty'&&p[1]==='read_only')[2],true);
 let pending=handlers.flow_order(frm);resolve({quantity_basis:'stock_v1',stock_uom:'Nos',planned_qty:144});await pending;
 assert.equal(doc.planned_qty,144);assert.strictEqual(doc.batches[0],batch);
 assert.equal(batch.good_qty,60);assert.equal(batch.rework_qty,12);assert.equal(doc.material_amount,123);
 pending=handlers.flow_order(frm);doc.flow_order='OTHER';resolve({quantity_basis:'stock_v1',stock_uom:'Nos',planned_qty:288});await pending;
 assert.equal(doc.planned_qty,144,'late reply must not overwrite another selected Flow');
 doc.flow_order=null;properties.length=0;await handlers.flow_order(frm);
 assert.equal(properties.find(p=>p[0]==='planned_qty'&&p[1]==='read_only')[2],false);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", program, str(ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.js")],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recalculate_unchanged_form_keeps_scope_batches_and_clears_cancelled_costs(self):
        preview = self.saving_preview()
        preview.recalculate_actual_costs()
        self.assertEqual((preview.total_cost, preview.cost_basis), ("8", "流程已过账实际"))
        batches = preview.batches
        issue, _row = self.native.issue(qty=2, factor=1)
        preview.recalculate_actual_costs()
        self.assertEqual((preview.total_cost, preview.receipt_cost_sources), ("16", "ISSUE\nSCR"))
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
 const before=JSON.stringify(doc),frm={doc,is_new:()=>false,is_dirty:()=>false,set_intro:()=>{},set_df_property:()=>{},
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
        self.workflow.stock_plan_quantity.return_value = Decimal(5)
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
        self.assertEqual((preview.material_amount, preview.processing_amount, preview.other_amount), (4, 4, 0))
        self.assertEqual(preview.receipt_cost_sources, "SCR")
        self.assert_no_posting()

    def manufacture_loss_preview(self):
        self.receipt.docstatus = 2
        preview = self.saving_preview()
        self.workflow.stock_plan_quantity.return_value = Decimal(5)
        source = self.native.manufacture(qty=4)
        source.from_bom, source.fg_completed_qty, source.process_loss_qty = 1, 5, 1
        batch = preview.batches[0]
        batch.update(source_manufacture=source.name, reference_no=source.name,
                     received_qty=5, good_qty=4, rework_qty=0, lost_qty=1)
        return preview, source, batch

    def test_manufacture_confirmed_loss_changes_cost_denominator_without_posting(self):
        preview, source, batch = self.manufacture_loss_preview()
        preview.validate()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost, batch.estimated_cost),
                         ("40", "10", "40"))
        self.assertEqual((preview.planned_qty, preview.good_qty, preview.lost_qty), (5, "4", "1"))
        self.assertEqual((source.fg_completed_qty, source.process_loss_qty), (5, 1))
        self.assert_no_posting()

    def test_removing_batch_or_link_cannot_erase_actual_manufacture_loss(self):
        for remove in ("link", "all_batches"):
            preview, _source, batch = self.manufacture_loss_preview()
            if remove == "link":
                batch.source_manufacture, batch.lost_qty, batch.good_qty = None, 0, 5
            else:
                preview.batches = []
            with self.subTest(remove=remove), self.assertRaisesRegex(ValueError, "存在确认损耗"):
                preview.validate()
        self.assert_no_posting()

    def test_manufacture_source_binding_rejects_duplicate_mixed_and_reclassification(self):
        preview, _source, batch = self.manufacture_loss_preview()
        preview._refresh_actual_costs()
        cases = (("source_receipt", "SCR", "同时关联"),
                 ("receipt_type", "返工回货", "首次回货"),
                 ("receipt_type", "待检处理", "首次回货"),
                 ("received_qty", 4, "已变化"), ("lost_qty", 0, "工艺损耗"),
                 ("source_manufacture", "OTHER", "未提交"))
        for field, value, error in cases:
            previous = batch.get(field)
            batch[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, error):
                preview._validate_batch_sources()
            batch[field] = previous
        preview.batches.append(type(batch)(batch))
        with self.assertRaisesRegex(ValueError, "重复"):
            preview._validate_batch_sources()
        self.assert_no_posting()

    def test_cancelled_manufacture_requires_classification_reconciliation_before_repricing(self):
        preview, source, _batch = self.manufacture_loss_preview()
        preview.validate()
        source.docstatus = 2
        with self.assertRaisesRegex(ValueError, "已撤销"):
            preview.validate()
        preview.batches = []
        preview.validate()
        self.assertEqual((preview.total_cost, preview.estimated_unit_cost, preview.lost_qty), ("0", "0", "0"))
        self.assertEqual(preview.cost_basis, "流程已过账实际")
        self.assert_no_posting()

    def test_estimate_manual_batches_remain_editable_but_explicit_actual_cannot_ignore_loss(self):
        preview, _source, batch = self.manufacture_loss_preview()
        preview.cost_basis = "流程估算"
        preview._before.cost_basis = "流程估算"
        batch.source_manufacture, batch.good_qty, batch.lost_qty = None, 5, 0
        preview.validate()
        self.assertEqual(preview.total_cost, "330")
        with self.assertRaisesRegex(ValueError, "存在确认损耗"):
            preview.load_posted_costs(preserve_input=True)
        self.assert_no_posting()

    def test_confirmed_loss_then_replacement_and_reverse_recompute_without_expanding_flow_target(self):
        preview, _first, _batch = self.manufacture_loss_preview()
        preview.validate()
        replacement = self.native.manufacture("REPLACE", material=8, qty=1, raw_qty=1)
        second = type(preview.batches[0])(source_manufacture=replacement.name,
            receipt_type="首次回货", received_qty=1, good_qty=1, lost_qty=0)
        preview.batches.append(second)
        preview.validate()
        self.assertEqual((preview.planned_qty, preview.good_qty, preview.lost_qty,
            preview.unreceived_qty, preview.total_cost, preview.estimated_unit_cost), (5, "5", "1", "0", "48", "9.6"))
        self.assertEqual([row.estimated_cost for row in preview.batches], ["38.4", "9.6"])
        replacement.docstatus = 2
        with self.assertRaisesRegex(ValueError, "已撤销"):
            preview.validate()
        preview.batches.pop()
        preview.validate()
        self.assertEqual((preview.planned_qty, preview.good_qty, preview.lost_qty,
            preview.total_cost, preview.estimated_unit_cost), (5, "4", "1", "40", "10"))
        self.assert_no_posting()

    def test_replacement_allowance_requires_all_sources_and_cannot_expand_unbound_manual_batches(self):
        preview, _source, _batch = self.manufacture_loss_preview()
        self.native.manufacture("REPLACE", material=8, qty=1, raw_qty=1)
        with self.assertRaisesRegex(ValueError, "存在确认损耗"):
            preview.validate()
        second = type(preview.batches[0])(source_manufacture="REPLACE",
            receipt_type="首次回货", received_qty=1, good_qty=1)
        preview.batches.append(second)
        preview.batches.append(type(second)(receipt_type="首次回货", received_qty=Decimal("0.1"), good_qty=Decimal("0.1")))
        with self.assertRaisesRegex(ValueError, "不能供未绑定"):
            preview.validate()
        preview.batches.pop()
        self.native.manufacture("EXCESS", material=8, qty=1, raw_qty=1)
        with self.assertRaisesRegex(ValueError, "超过流程目标库存量"):
            preview.validate()
        self.assert_no_posting()

    def test_wage_accrual_changes_cost_not_quantity_and_reverse_retains_inventory_sources(self):
        preview = self.saving_preview()
        self.native.manufacture(material=40, other=0)
        self.native.wage_cost_result.update(rows=[{"wage_entry": "WAGE", "journal_entry": "PAYROLL-JE",
            "work_order": "WO", "job_card": "CARD", "amount": Decimal(20)}], total=Decimal(20))
        preview.validate()
        self.assertEqual((preview.material_amount, preview.processing_amount, preview.total_cost,
                          preview.receipt_cost_sources), (44, 24, "68", "MAKE\nPAYROLL-JE\nSCR"))
        self.assertEqual((preview.batches[0].received_qty, preview.batches[0].good_qty), (4, 2))
        self.native.wage_cost_result.update(rows=[], total=Decimal(0))
        preview.recalculate_actual_costs()
        self.assertEqual((preview.total_cost, preview.processing_amount, preview.receipt_cost_sources), ("48", 4, "MAKE\nSCR"))
        self.assertEqual((preview.batches[0].received_qty, preview.batches[0].good_qty), (4, 2))
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
 const frm={doc,is_new:()=>false,is_dirty:()=>true,set_intro:()=>{},set_df_property:()=>{},add_custom_button:(n,f)=>buttons[n]=f,
 save:()=>{throw Error('no automatic pre-save');},call:async(method,args)=>calls.push({method,args,note:doc.note,batches:doc.batches}),reload_doc:async()=>{}};
 handlers.refresh(frm);const selected=buttons['带入已过账实际费用']();await new Promise(r=>setImmediate(r));
 assert.equal(messages.length,0);assert.equal(calls.length,0);assert.match(confirmation.message,/制造 MAKE&lt;1&gt;（工单 WO）/);
 assert.match(confirmation.message,/材料费 40.*其他费 6/);assert.match(confirmation.message,/内部转料.*不重复/);
 confirmation.yes();await selected;assert.equal(calls.length,1);assert.equal(calls[0].method,'load_posted_costs');
 assert.equal(calls[0].args.preserve_input,true);assert.equal(calls[0].note,'pending');assert.strictEqual(calls[0].batches,doc.batches);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(["node", "-e", program, str(ROOT / "jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.js")],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manufacture_batch_read_preserves_classification_manual_entries_and_stale_flow(self):
        program = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let handlers,release;const messages=[],requests=[],added=[];
const frappe={ui:{form:{on:(_,h)=>handlers=h}},utils:{escape_html:v=>v.replace(/</g,'&lt;').replace(/>/g,'&gt;')},
 call:options=>{requests.push(options);return new Promise(resolve=>release=resolve);},msgprint:message=>messages.push(message)};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{frappe});
(async()=>{
 const buttons={},manual={reference_no:'MANUAL',good_qty:2},matching={reference_no:'MATCH',receipt_type:'首次回货',received_qty:5,good_qty:3,rework_qty:1,lost_qty:1},classified={source_manufacture:'OLD',reference_no:'OLD',receipt_type:'首次回货',received_qty:5,good_qty:4,lost_qty:1};
 const doc={name:'CP',flow_order:'FLOW',batches:[manual,matching,classified]},frm={doc,is_new:()=>false,set_intro:()=>{},set_df_property:()=>{},dirty:()=>{},
 add_custom_button:(name,handler)=>buttons[name]=handler,add_child:(_,values)=>{added.push(values);doc.batches.push(values);return values;},refresh_field:()=>{}};
 handlers.refresh(frm);const read=buttons['读取已提交制造入库批次'];
 const stale=read();doc.flow_order='OTHER';release({message:[{manufacture:'WRONG'}]});await stale;assert.equal(added.length,0);assert.equal(messages.length,0);
 doc.flow_order='FLOW';const next=read();release({message:[
  {manufacture:'MAKE<1>',qty:5,inventory_qty:4,lost_qty:1,stock_uom:'Nos'},
  {manufacture:'MANUAL',qty:2,inventory_qty:2,lost_qty:0,stock_uom:'Nos'},
  {manufacture:'MATCH',qty:5,inventory_qty:4,lost_qty:1,stock_uom:'Nos'},
  {manufacture:'OLD',qty:6,inventory_qty:4,lost_qty:2,stock_uom:'Nos'}]});await next;
 assert.equal(added.length,1);assert.equal(added[0].source_manufacture,'MAKE<1>');assert.equal(added[0].received_qty,5);assert.equal(added[0].lost_qty,1);assert.equal(added[0].pending_qty,4);
 assert.deepEqual(manual,{reference_no:'MANUAL',good_qty:2});assert.equal(classified.received_qty,5);assert.equal(classified.good_qty,4);assert.equal(classified.lost_qty,1);
 assert.equal(matching.source_manufacture,'MATCH');assert.equal(matching.good_qty,3);assert.equal(matching.rework_qty,1);assert.equal(matching.lost_qty,1);
 assert.match(messages[0],/已跳过.*确认损耗只计一次.*未覆盖/);assert.match(messages[0],/OLD：实际入库 4，确认损耗 2，覆盖量 6 Nos/);
 assert.ok(requests.every(request=>request.method==='jifeng_erp.workflow.manufacture_batch_candidates'));
})().catch(error=>{console.error(error);process.exitCode=1;});
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
 const frm={doc,is_new:()=>false,is_dirty:()=>true,set_intro:()=>{},set_df_property:()=>{},add_custom_button:(name,fn)=>buttons[name]=fn,
 save:()=>{throw Error('historical ambiguous input must not autosave');},
 call:async(method,args)=>{ calls.push({method,args,remark:doc.remark,batches:doc.batches}); }, reload_doc:async()=>{},
 set_value:async values=>Object.assign(doc,values)};
 handlers.refresh(frm); assert.equal(Object.keys(buttons).length,5);
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
