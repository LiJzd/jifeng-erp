"""Guard receipt quantity and provenance before a non-posting trial is saved."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch


class Record(dict):
    def __getattr__(self, name):
        return self.get(name)

    def check_permission(self, permission):
        return None


class TestReceiptBatches(unittest.TestCase):
    def setUp(self):
        self.readable = True
        self.returned = False
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
        frappe.get_list = lambda *args, **kwargs: self.parents
        frappe.get_all = lambda *args, **kwargs: [self.detail]
        frappe.db = types.SimpleNamespace(
            get_value=lambda *args, **kwargs: self.detail,
            exists=lambda *args, **kwargs: self.returned,
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
        self.preview = self.preview_module.JifengCostPreview()
        self.preview.company, self.preview.item_code, self.preview.flow_order = "JF", "RING", "FLOW"
        self.preview.batches = [Record(source_receipt="SCR", source_receipt_item="ROW", received_qty=4, lost_qty=1)]

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

    def test_return_candidate_requires_manual_reconciliation(self):
        self.parents.append(Record(name="RETURN", is_return=1))
        with self.assertRaisesRegex(ValueError, "退货"):
            self.workflow.receipt_batch_candidates("FLOW")

    def test_duplicate_source_cannot_be_saved(self):
        self.preview.batches.append(self.preview.batches[0].copy())
        self.preview.batches[-1] = Record(self.preview.batches[-1])
        with self.assertRaisesRegex(ValueError, "重复"):
            self.preview._validate_batch_sources()

    def test_source_quantity_change_requires_reconciliation(self):
        self.detail["qty"] = 1
        with self.assertRaisesRegex(ValueError, "数量不一致"):
            self.preview._validate_batch_sources()

    def test_confirmed_source_loss_cannot_be_reclassified_as_good(self):
        self.preview.batches[0]["lost_qty"] = 0
        with self.assertRaisesRegex(ValueError, "工艺损耗"):
            self.preview._validate_batch_sources()

    def test_other_flow_source_cannot_be_used(self):
        self.detail["jf_flow_order"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "流程单或数量不一致"):
            self.preview._validate_batch_sources()

    def test_cancelled_source_cannot_be_saved(self):
        self.receipt["docstatus"] = 2
        with self.assertRaisesRegex(ValueError, "未提交"):
            self.preview._validate_batch_sources()

    def test_return_against_source_requires_manual_reconciliation(self):
        self.returned = True
        with self.assertRaisesRegex(ValueError, "已有退货"):
            self.preview._validate_batch_sources()


if __name__ == "__main__":
    unittest.main()
