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


if __name__ == "__main__":
    unittest.main()
