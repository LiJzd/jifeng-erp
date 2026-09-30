"""Guard receipt quantity and provenance before a non-posting trial is saved."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch

from jifeng_erp.costing import CostingError, receipt_batch_quantities


class Record(dict):
    def __getattr__(self, name):
        return self.get(name)

    def check_permission(self, permission):
        return None


class TestReceiptBatches(unittest.TestCase):
    def setUp(self):
        self.readable = True
        self.returns = []
        self.return_details = []
        self.total_returns = None
        self.flow = Record(name="FLOW", company="JF", item_code="RING")
        self.receipt = Record(name="SCR", company="JF", docstatus=1, is_return=0)
        self.detail = Record(name="ROW", parent="SCR", idx=1, item_code="RING",
                             jf_flow_order="FLOW", qty=2, rejected_qty=1, process_loss_qty=1)
        self.parents = [Record(name="SCR", posting_date="2026-09-30", is_return=0)]
        frappe = types.ModuleType("frappe")
        frappe.PermissionError = PermissionError
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.whitelist = lambda: lambda function: function
        frappe.has_permission = lambda *args: self.readable
        frappe.get_doc = lambda doctype, name: self.flow if doctype == "Jifeng Flow Order" else self.receipt
        frappe.get_list = self.get_list
        frappe.get_all = self.get_all
        frappe.db = types.SimpleNamespace(
            count=lambda *args: len(self.returns) if self.total_returns is None else self.total_returns,
        )
        utils = types.ModuleType("frappe.utils")
        utils.add_days = lambda *args: None
        utils.cint = int
        utils.today = lambda: "2026-09-30"
        utils.now = lambda: "2026-09-30 00:00:00"
        document = types.ModuleType("frappe.model.document")
        document.Document = type("Document", (), {})
        with patch.dict(sys.modules, {
            "frappe": frappe, "frappe.utils": utils, "frappe.model.document": document,
        }):
            self.workflow = self.load("workflow", "jifeng_erp/workflow.py")
            self.preview_module = self.load("preview", "jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.py")
        workflow_patch = patch.dict(sys.modules, {"jifeng_erp.workflow": self.workflow})
        workflow_patch.start()
        self.addCleanup(workflow_patch.stop)
        self.preview = self.preview_module.JifengCostPreview()
        self.preview.company, self.preview.item_code, self.preview.flow_order = "JF", "RING", "FLOW"
        self.preview.batches = [Record(source_receipt="SCR", source_receipt_item="ROW", received_qty=4, lost_qty=1)]

    def get_list(self, doctype, **kwargs):
        if isinstance(kwargs.get("filters"), dict):
            return self.returns
        return self.parents if self.receipt.docstatus == 1 else []

    def get_all(self, doctype, **kwargs):
        if "jf_flow_order" in kwargs["filters"]:
            return [self.detail] if self.detail.jf_flow_order == "FLOW" else []
        return self.return_details

    def return_qty(self, qty):
        self.returns = [Record(name="RETURN", return_against="SCR")]
        self.return_details = [Record(parent="RETURN", subcontracting_receipt_item="ROW",
                                      item_code="RING", jf_flow_order="FLOW", qty=-qty, rejected_qty=0)]

    @staticmethod
    def load(name, relative_path):
        spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).parents[1] / relative_path)
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
        self.readable = False
        with self.assertRaises(PermissionError):
            self.workflow.receipt_batch_candidates("FLOW")

    def test_partial_return_reduces_net_quantity_and_keeps_loss(self):
        self.return_qty(1)
        row = self.workflow.receipt_batch_candidates("FLOW")[0]
        self.assertEqual((row["qty"], row["lost_qty"], row["returned_qty"]), (3, 1, 1))

    def test_duplicate_source_cannot_be_saved(self):
        self.preview.batches.append(self.preview.batches[0].copy())
        self.preview.batches[-1] = Record(self.preview.batches[-1])
        with self.assertRaisesRegex(ValueError, "重复"):
            self.preview._validate_batch_sources()

    def test_source_quantity_change_requires_reconciliation(self):
        self.detail["qty"] = 1
        with self.assertRaisesRegex(ValueError, "净回货数量已变化"):
            self.preview._validate_batch_sources()

    def test_confirmed_source_loss_cannot_be_reclassified_as_good(self):
        self.preview.batches[0]["lost_qty"] = 0
        with self.assertRaisesRegex(ValueError, "工艺损耗"):
            self.preview._validate_batch_sources()

    def test_other_flow_source_cannot_be_used(self):
        self.detail["jf_flow_order"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "流程单不一致"):
            self.preview._validate_batch_sources()

    def test_cancelled_source_cannot_be_saved(self):
        self.receipt["docstatus"] = 2
        with self.assertRaisesRegex(ValueError, "未提交"):
            self.preview._validate_batch_sources()

    def test_changed_return_requires_classification_reconciliation(self):
        self.return_qty(1)
        with self.assertRaisesRegex(ValueError, "净回货数量已变化"):
            self.preview._validate_batch_sources()
        self.preview.batches[0]["received_qty"] = 3
        self.preview._validate_batch_sources()

    def test_all_physical_goods_returned_keeps_confirmed_loss(self):
        self.return_qty(3)
        row = self.workflow.receipt_batch_candidates("FLOW")[0]
        self.assertEqual((row["qty"], row["lost_qty"]), (1, 1))

    def test_full_return_without_loss_requires_batch_removal(self):
        self.detail["process_loss_qty"] = 0
        self.return_qty(3)
        with self.assertRaisesRegex(ValueError, "全额退回"):
            self.preview._validate_batch_sources()

    def test_inaccessible_returns_cannot_be_silently_omitted(self):
        self.total_returns = 1
        with self.assertRaises(PermissionError):
            self.workflow.receipt_batch_candidates("FLOW")

    def test_return_without_source_detail_cannot_be_ignored(self):
        self.return_qty(1)
        self.return_details[0]["subcontracting_receipt_item"] = None
        with self.assertRaisesRegex(ValueError, "缺少来源"):
            self.workflow.receipt_batch_candidates("FLOW")

    def test_return_cannot_remove_confirmed_loss(self):
        with self.assertRaises(CostingError):
            receipt_batch_quantities(self.detail, 4)


if __name__ == "__main__":
    unittest.main()
