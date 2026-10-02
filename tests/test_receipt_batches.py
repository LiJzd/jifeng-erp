"""Preview provenance risks through the real complete native source service."""

import importlib.util
from pathlib import Path
import sys
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
        document.Document = type("Document", (), {})
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


if __name__ == "__main__":
    unittest.main()
