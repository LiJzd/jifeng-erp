"""Material issue quantities and source guards without a database."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class Record(dict):
    def __getattribute__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return dict.__getattribute__(self, key)

    def __setattr__(self, key, value):
        self[key] = value

    def check_permission(self, permission):
        return None


class TestMaterialIssue(unittest.TestCase):
    def setUp(self):
        self.flow = Record(
            doctype="Jifeng Flow Order", name="FLOW-1", company="JF", planned_qty=5,
            materials=[Record(item_code="SILVER", uom="Kg", qty_per_unit=0.2)],
        )
        self.warehouse = Record(company="JF", is_group=0, disabled=0)
        self.history = []
        frappe = types.ModuleType("frappe")
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe.get_doc = lambda doctype, name: {
            ("Jifeng Flow Order", "FLOW-1"): self.flow,
            ("Warehouse", "MAIN - JF"): self.warehouse,
        }[(doctype, name)]
        frappe.get_all = lambda doctype, **kwargs: self.history if doctype == "Stock Entry Detail" else []
        frappe.db = types.SimpleNamespace(get_value=self.db_value)
        erpnext = types.ModuleType("erpnext")
        stock = types.ModuleType("erpnext.stock")
        details = types.ModuleType("erpnext.stock.get_item_details")
        details.get_conversion_factor = lambda item_code, uom: {
            "conversion_factor": 1000 if uom == "Kg" else 1,
        }
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.validate_flow_source = lambda flow: None
        modules = {
            "frappe": frappe, "erpnext": erpnext, "erpnext.stock": stock,
            "erpnext.stock.get_item_details": details, "jifeng_erp.workflow": workflow,
        }
        with patch.dict(sys.modules, modules):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "material_issue.py"
            spec = importlib.util.spec_from_file_location("material_issue_under_test", path)
            self.issue = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.issue)

    def db_value(self, doctype, name, fields, as_dict=False, for_update=False):
        if doctype == "Item":
            return "Gram"
        if doctype == "Jifeng Flow Order":
            return "FLOW-1"
        if doctype == "Stock Entry":
            return Record(docstatus=1, stock_entry_type="Material Issue")
        raise AssertionError((doctype, name, fields))

    def entry(self, qty):
        return Record(
            doctype="Stock Entry", name="SE-NEW", company="JF",
            stock_entry_type="Material Issue", jf_material_issue_flow="FLOW-1",
            items=[Record(item_code="SILVER", uom="Gram", qty=qty,
                          s_warehouse="MAIN - JF", t_warehouse=None, jf_flow_order="FLOW-1")],
        )

    def test_required_quantity_converts_recipe_unit_to_stock_unit(self):
        required, units = self.issue._required_stock(self.flow)
        self.assertEqual(required["SILVER"], 1000)
        self.assertEqual(units["SILVER"], "Gram")

    def test_partial_issues_leave_balance(self):
        self.history = [Record(parent="SE-OLD", item_code="SILVER", stock_qty=400)]
        self.issue.validate_material_issue(self.entry(600))

    def test_overissue_is_rejected(self):
        self.history = [Record(parent="SE-OLD", item_code="SILVER", stock_qty=400)]
        with self.assertRaisesRegex(ValueError, "累计领料数量超过"):
            self.issue.validate_material_issue(self.entry(601))

    def test_other_flow_row_is_rejected(self):
        entry = self.entry(100)
        entry.items[0].jf_flow_order = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "全部关联本流程单"):
            self.issue.validate_material_issue(entry)


if __name__ == "__main__":
    unittest.main()
