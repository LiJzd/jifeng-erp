"""Check production stock rows keep their source flow when ERPNext creates entries."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class Record(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value

    def check_permission(self, permission):
        return None


class TestFlowStockEntry(unittest.TestCase):
    def setUp(self):
        self.flow = Record(name="FLOW-1", company="JF", item_code="RING",
                           materials=[Record(item_code="SILVER")])
        self.work_order = Record(company="JF", production_item="RING", jf_flow_order="FLOW-1")
        self.entry = Record(doctype="Stock Entry", company="JF", work_order="WO-1", items=[
            Record(item_code="SILVER", jf_flow_order=None),
            Record(item_code="RING", jf_flow_order=None),
            Record(item_code="SCRAP", jf_flow_order=None),
        ])
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe.get_doc = lambda doctype, name: {
            ("Work Order", "WO-1"): self.work_order,
            ("Jifeng Flow Order", "FLOW-1"): self.flow,
        }[(doctype, name)]
        sources = {
            ("Material Request Item", "MR-ROW"): Record(
                parent="MR-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Purchase Order Item", "PO-ROW"): Record(
                parent="PO-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Purchase Receipt Item", "PR-ROW"): Record(
                parent="PR-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Purchase Invoice Item", "PI-ROW"): Record(
                parent="PI-1", item_code="SILVER", jf_flow_order="FLOW-1"),
            ("Subcontracting Order Item", "SCO-ROW"): Record(
                parent="SCO-1", item_code="RING", jf_flow_order="FLOW-1"),
            ("Subcontracting Receipt Item", "SCR-ROW"): Record(
                parent="SCR-1", item_code="RING", jf_flow_order="FLOW-1"),
            ("Subcontracting Order Supplied Item", "SCO-RM-ROW"): Record(
                parent="SCO-1", reference_name="SCO-ROW", main_item_code="RING",
                rm_item_code="SILVER"),
        }
        frappe.db = types.SimpleNamespace(get_value=lambda doctype, name, fields, as_dict=False:
                                          sources.get((doctype, name)))
        custom = types.ModuleType("frappe.custom.doctype.custom_field.custom_field")
        custom.create_custom_fields = lambda *args, **kwargs: None
        modules = {
            "frappe": frappe,
            "frappe.custom": types.ModuleType("frappe.custom"),
            "frappe.custom.doctype": types.ModuleType("frappe.custom.doctype"),
            "frappe.custom.doctype.custom_field": types.ModuleType("frappe.custom.doctype.custom_field"),
            "frappe.custom.doctype.custom_field.custom_field": custom,
        }
        with patch.dict(sys.modules, modules):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "setup.py"
            spec = importlib.util.spec_from_file_location("flow_setup_under_test", path)
            self.setup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.setup)

    def test_work_order_flow_is_copied_to_material_and_finished_item(self):
        self.setup.validate_flow_links(self.entry)
        self.assertEqual([row.jf_flow_order for row in self.entry["items"]],
                         ["FLOW-1", "FLOW-1", None])

    def test_other_flow_cannot_be_linked_to_work_order_entry(self):
        self.entry["items"][0].jf_flow_order = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "其他流程单"):
            self.setup.validate_flow_links(self.entry)

    def test_company_mismatch_blocks_stock_entry(self):
        self.entry.company = "OTHER"
        with self.assertRaisesRegex(ValueError, "公司不一致"):
            self.setup.validate_flow_links(self.entry)

    def test_unrelated_work_order_item_keeps_manual_flow_empty(self):
        self.entry["items"] = [Record(item_code="SCRAP", jf_flow_order=None)]
        self.setup.validate_flow_links(self.entry)
        self.assertIsNone(self.entry["items"][0].jf_flow_order)

    def test_procurement_documents_accept_only_materials_from_matching_flow(self):
        for doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
            with self.subTest(doctype=doctype):
                doc = Record(doctype=doctype, company="JF", items=[
                    Record(item_code="SILVER", jf_flow_order="FLOW-1"),
                ])
                self.setup.validate_flow_links(doc)
                doc["items"][0].item_code = "SCRAP"
                with self.assertRaisesRegex(ValueError, "不在流程单物料分析"):
                    self.setup.validate_flow_links(doc)
                doc["items"][0].item_code = "SILVER"
                doc.company = "OTHER"
                with self.assertRaisesRegex(ValueError, "公司不一致"):
                    self.setup.validate_flow_links(doc)

    def test_procurement_source_links_fill_flow_and_reject_conflicts(self):
        cases = (
            ("Purchase Order", {"material_request_item": "MR-ROW", "material_request": "MR-1"}),
            ("Purchase Receipt", {"purchase_order_item": "PO-ROW", "purchase_order": "PO-1"}),
            ("Purchase Invoice", {"pr_detail": "PR-ROW", "purchase_receipt": "PR-1",
                                  "po_detail": "PO-ROW", "purchase_order": "PO-1"}),
            ("Subcontracting Receipt", {"subcontracting_order_item": "SCO-ROW",
                                          "subcontracting_order": "SCO-1"}),
        )
        for doctype, references in cases:
            with self.subTest(doctype=doctype):
                item_code = "RING" if doctype == "Subcontracting Receipt" else "SILVER"
                row = Record(item_code=item_code, jf_flow_order=None, **references)
                doc = Record(doctype=doctype, company="JF", items=[row])
                self.setup.validate_flow_links(doc)
                self.assertEqual(row.jf_flow_order, "FLOW-1")
                row.jf_flow_order = "FLOW-OTHER"
                with self.assertRaisesRegex(ValueError, "与来源单据不一致"):
                    self.setup.validate_flow_links(doc)

    def test_subcontracting_order_and_receipt_match_finished_item(self):
        for doctype in ("Subcontracting Order", "Subcontracting Receipt"):
            with self.subTest(doctype=doctype):
                doc = Record(doctype=doctype, company="JF", items=[
                    Record(item_code="RING", jf_flow_order="FLOW-1"),
                ])
                self.setup.validate_flow_links(doc)
                doc["items"][0].item_code = "SILVER"
                with self.assertRaisesRegex(ValueError, "商品与流程单商品不一致"):
                    self.setup.validate_flow_links(doc)

    def test_return_inherits_flow_from_original_item_and_checks_source(self):
        cases = (
            ("Purchase Receipt", "purchase_receipt_item", "PR-ROW", "PR-1", "SILVER"),
            ("Purchase Invoice", "purchase_invoice_item", "PI-ROW", "PI-1", "SILVER"),
            ("Subcontracting Receipt", "subcontracting_receipt_item", "SCR-ROW", "SCR-1", "RING"),
        )
        for doctype, source_field, source_row, parent, item_code in cases:
            with self.subTest(doctype=doctype):
                row = Record(item_code=item_code, jf_flow_order=None, **{source_field: source_row})
                doc = Record(doctype=doctype, company="JF", is_return=1,
                             return_against=parent, items=[row])
                self.setup.validate_flow_links(doc)
                self.assertEqual(row.jf_flow_order, "FLOW-1")
                doc.return_against = "OTHER"
                with self.assertRaisesRegex(ValueError, "来源单据与来源明细不一致"):
                    self.setup.validate_flow_links(doc)
                doc.return_against = parent
                row.item_code = "SCRAP"
                with self.assertRaisesRegex(ValueError, "商品与来源明细不一致"):
                    self.setup.validate_flow_links(doc)

    def test_subcontracting_transfer_inherits_source_flow(self):
        row = Record(item_code="SILVER", subcontracted_item="RING",
                     sco_rm_detail="SCO-RM-ROW", jf_flow_order=None)
        entry = Record(doctype="Stock Entry", company="JF", subcontracting_order="SCO-1", items=[row])
        self.setup.validate_flow_links(entry)
        self.assertEqual(row.jf_flow_order, "FLOW-1")

        row.jf_flow_order = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "与来源单据不一致"):
            self.setup.validate_flow_links(entry)

        row.jf_flow_order = None
        entry.subcontracting_order = "SCO-OTHER"
        with self.assertRaisesRegex(ValueError, "来源明细与委外订单不一致"):
            self.setup.validate_flow_links(entry)

    def test_subcontract_return_cannot_copy_original_positive_loss(self):
        row = Record(item_code="RING", jf_flow_order=None, subcontracting_receipt_item="SCR-ROW",
                     process_loss_qty=1)
        doc = Record(doctype="Subcontracting Receipt", company="JF", is_return=1,
                     return_against="SCR-1", items=[row])
        with self.assertRaisesRegex(ValueError, "工艺损耗须为 0"):
            self.setup.validate_flow_links(doc)
        row.process_loss_qty = 0
        self.setup.validate_flow_links(doc)
        self.assertEqual(row.jf_flow_order, "FLOW-1")


if __name__ == "__main__":
    unittest.main()
