"""Material issue quantities and source guards without a database."""

import importlib.util
import pathlib
import sys
import types
import unittest
from decimal import Decimal
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

    def reload(self):
        return self


class TestMaterialIssue(unittest.TestCase):
    def setUp(self):
        self.flow = Record(
            doctype="Jifeng Flow Order", name="FLOW-1", company="JF", planned_qty=5,
            materials=[Record(item_code="SILVER", uom="Kg", qty_per_unit=0.2)], flags=Record(),
        )
        self.current_flow = None
        self.warehouse = Record(company="JF", is_group=0, disabled=0)
        self.history = []
        frappe = types.ModuleType("frappe")
        frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe.get_doc = lambda doctype, name, **kwargs: {
            ("Jifeng Flow Order", "FLOW-1"): (self.current_flow or self.flow) if kwargs.get("for_update") else self.flow,
            ("Warehouse", "MAIN - JF"): self.warehouse,
        }[(doctype, name)]
        frappe.get_all = lambda doctype, **kwargs: self.history if doctype == "Stock Entry Detail" else []
        frappe.db = types.SimpleNamespace(get_value=self.db_value, sql=self.db_sql)
        erpnext = types.ModuleType("erpnext")
        stock = types.ModuleType("erpnext.stock")
        stock_doctype = types.ModuleType("erpnext.stock.doctype")
        item_package = types.ModuleType("erpnext.stock.doctype.item")
        item_module = types.ModuleType("erpnext.stock.doctype.item.item")
        item_module.get_uom_conv_factor = lambda uom, stock_uom: None
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.validate_flow_source = lambda flow: None
        workflow.stock_plan_quantity = lambda flow: Decimal(str(flow.planned_qty))
        modules = {
            "frappe": frappe, "erpnext": erpnext, "erpnext.stock": stock,
            "erpnext.stock.doctype": stock_doctype,
            "erpnext.stock.doctype.item": item_package,
            "erpnext.stock.doctype.item.item": item_module,
            "jifeng_erp.workflow": workflow,
        }
        with patch.dict(sys.modules, modules):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "material_issue.py"
            spec = importlib.util.spec_from_file_location("material_issue_under_test", path)
            self.issue = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.issue)

    def db_value(self, doctype, name, fields, as_dict=False, for_update=False):
        if doctype == "Item":
            return Record(stock_uom="Gram", variant_of=None)
        if doctype == "UOM Conversion Detail":
            return 1000 if name["uom"] == "Kg" else None
        if doctype == "Jifeng Flow Order":
            return "FLOW-1"
        if doctype == "Stock Entry":
            return Record(docstatus=1, stock_entry_type="Material Issue")
        raise AssertionError((doctype, name, fields))

    def db_sql(self, sql, values, as_dict=False):
        if "JOIN `tabStock Entry`" in sql:
            self.assertIn("FOR UPDATE", sql)
            self.assertIn("entry.docstatus=1", sql)
            self.assertEqual(values, ("FLOW-1",))
            return self.history
        if "docstatus=0" in sql:
            return []
        raise AssertionError(sql)

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

    def test_missing_unit_conversion_is_rejected(self):
        self.flow.materials[0].uom = "Furnace"
        with self.assertRaisesRegex(ValueError, "缺少 Furnace 到库存单位 Gram 的换算关系"):
            self.issue._required_stock(self.flow)

    def test_partial_issues_leave_balance(self):
        self.history = [Record(parent="SE-OLD", item_code="SILVER", transfer_qty=400)]
        self.issue.validate_material_issue(self.entry(600))

    def test_overissue_is_rejected(self):
        self.history = [Record(parent="SE-OLD", item_code="SILVER", transfer_qty=400)]
        with self.assertRaisesRegex(ValueError, "累计领料数量超过"):
            self.issue.validate_material_issue(self.entry(601))

    def test_other_flow_row_is_rejected(self):
        entry = self.entry(100)
        entry.items[0].jf_flow_order = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "全部关联本流程单"):
            self.issue.validate_material_issue(entry)

    def test_validator_loads_current_locked_flow_instead_of_old_document(self):
        self.flow.planned_qty = 6
        self.current_flow = Record(self.flow)
        self.current_flow.planned_qty = 5
        with self.assertRaisesRegex(ValueError, "累计领料数量超过"):
            self.issue.validate_material_issue(self.entry(1100))

    def test_busy_submitted_history_is_rejected_instead_of_treated_as_empty(self):
        self.issue.frappe.db.sql = lambda *args, **kwargs: (_ for _ in ()).throw(
            self.issue.frappe.QueryTimeoutError("native lock conflict"))
        with self.assertRaisesRegex(ValueError, "正在被核对"):
            self.issue.validate_material_issue(self.entry(100))


if __name__ == "__main__":
    unittest.main()
