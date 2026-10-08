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
        self.returned = {}
        self.work_orders = []
        frappe = types.ModuleType("frappe")
        frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe.ValidationError = type("ValidationError", (Exception,), {})
        frappe.PermissionError = PermissionError
        frappe.session = types.SimpleNamespace(user="qa@example.com")
        frappe.throw = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
        frappe.get_list = lambda doctype, **kwargs: self.work_orders if doctype == "Work Order" else []
        def get_doc(doctype, name, **kwargs):
            if doctype == "Jifeng Flow Order":
                return (self.current_flow or self.flow) if kwargs.get("for_update") else self.flow
            if doctype == "Warehouse":
                return self.warehouse
            if doctype == "Work Order":
                return self.work_orders[0]
            raise AssertionError(doctype)
        frappe.get_doc = get_doc
        frappe.get_all = lambda doctype, **kwargs: (
            self.history if doctype == "Stock Entry Detail" else self.work_orders if doctype == "Work Order" else [])
        frappe.db = types.SimpleNamespace(get_value=self.db_value, sql=self.db_sql)
        model = types.ModuleType("frappe.model")
        self.work_order_read_fields = {"name", "company", "jf_flow_order", "docstatus"}
        model.get_permitted_fields = lambda doctype, **kwargs: self.work_order_read_fields
        erpnext = types.ModuleType("erpnext")
        stock = types.ModuleType("erpnext.stock")
        stock_doctype = types.ModuleType("erpnext.stock.doctype")
        item_package = types.ModuleType("erpnext.stock.doctype.item")
        item_module = types.ModuleType("erpnext.stock.doctype.item.item")
        item_module.get_uom_conv_factor = lambda uom, stock_uom: None
        workflow = types.ModuleType("jifeng_erp.workflow")
        workflow.validate_flow_source = lambda flow: None
        workflow.stock_plan_quantity = lambda flow: Decimal(str(flow.planned_qty))
        returns = types.ModuleType("jifeng_erp.material_returns")
        returns.submitted_return_quantities_for_flow = lambda flow_name, issue_names: self.returned
        self.returns_module = returns
        modules = {
            "frappe": frappe, "frappe.model": model, "erpnext": erpnext, "erpnext.stock": stock,
            "erpnext.stock.doctype": stock_doctype,
            "erpnext.stock.doctype.item": item_package,
            "erpnext.stock.doctype.item.item": item_module,
            "jifeng_erp.workflow": workflow,
            "jifeng_erp.material_returns": returns,
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
        if "FROM `tabWork Order`" in sql:
            return [(row.name,) for row in self.work_orders]
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

    def validate(self, entry):
        with patch.dict(sys.modules, {"jifeng_erp.material_returns": self.returns_module}):
            self.issue.validate_material_issue(entry)

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
        self.validate(self.entry(600))

    def test_submitted_material_returns_reduce_net_flow_issue_cap(self):
        self.history = [Record(parent="SE-OLD", item_code="SILVER", transfer_qty=400)]
        self.returned = {"SILVER": Decimal("100")}
        self.validate(self.entry(700))
        with self.assertRaisesRegex(ValueError, "累计领料数量超过"):
            self.validate(self.entry(701))

    def test_overissue_is_rejected(self):
        self.history = [Record(parent="SE-OLD", item_code="SILVER", transfer_qty=400)]
        with self.assertRaisesRegex(ValueError, "累计领料数量超过"):
            self.validate(self.entry(601))

    def test_other_flow_row_is_rejected(self):
        entry = self.entry(100)
        entry.items[0].jf_flow_order = "FLOW-OTHER"
        with self.assertRaisesRegex(ValueError, "全部关联本流程单"):
            self.validate(entry)

    def test_validator_loads_current_locked_flow_instead_of_old_document(self):
        self.flow.planned_qty = 6
        self.current_flow = Record(self.flow)
        self.current_flow.planned_qty = 5
        with self.assertRaisesRegex(ValueError, "累计领料数量超过"):
            self.validate(self.entry(1100))

    def test_busy_submitted_history_is_rejected_instead_of_treated_as_empty(self):
        self.issue.frappe.db.sql = lambda *args, **kwargs: (_ for _ in ()).throw(
            self.issue.frappe.QueryTimeoutError("native lock conflict"))
        with self.assertRaisesRegex(ValueError, "正在被核对"):
            self.validate(self.entry(100))

    def test_active_work_order_blocks_expense_issue_path(self):
        self.work_orders.append(Record(name="WO-1", company="JF", jf_flow_order="FLOW-1", docstatus=1,
            meta=types.SimpleNamespace(get_permlevel_access=lambda **kwargs: {0},
                                       get_field=lambda name: Record(mask=0, permlevel=0))))
        with self.assertRaisesRegex(ValueError, "已有有效生产工单"):
            self.issue.assert_no_active_work_order(self.flow)

    def test_standard_work_order_fields_use_authoritative_permitted_field_projection(self):
        self.work_orders.append(Record(name="WO-1", company="JF", jf_flow_order="FLOW-1", docstatus=1,
            meta=types.SimpleNamespace(get_permlevel_access=lambda **kwargs: {0},
                                       get_field=lambda name: Record(mask=0, permlevel=0))))
        # The fixture deliberately models the authoritative projection that
        # includes framework fields name/docstatus even when Meta's scalar
        # permitted-fieldname projection would omit them.
        with self.assertRaisesRegex(ValueError, "已有有效生产工单"):
            self.issue.assert_no_active_work_order(self.flow)

    def test_masked_work_order_source_field_is_rejected(self):
        self.work_orders.append(Record(name="WO-1", company="JF", jf_flow_order="FLOW-1", docstatus=1,
            meta=types.SimpleNamespace(get_permlevel_access=lambda **kwargs: set(),
                                       get_field=lambda name: Record(mask=1, permlevel=0))))
        with self.assertRaisesRegex(ValueError, "无法读取本流程生产工单的来源字段"):
            self.issue.assert_no_active_work_order(self.flow)

    def test_no_work_order_does_not_require_work_order_read_permission(self):
        self.issue.frappe.get_list = lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied"))
        self.issue.assert_no_active_work_order(self.flow)

    def test_hidden_existing_work_order_fails_closed(self):
        self.work_orders.append(Record(name="WO-1", company="JF", jf_flow_order="FLOW-1", docstatus=1))
        self.issue.frappe.get_list = lambda *args, **kwargs: []
        with self.assertRaisesRegex(ValueError, "无法完整核对本流程的生产工单"):
            self.issue.assert_no_active_work_order(self.flow)


if __name__ == "__main__":
    unittest.main()
