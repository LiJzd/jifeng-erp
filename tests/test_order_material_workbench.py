"""Net material issue quantities shown by the order material workbench."""

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

    def check_permission(self, permission):
        return None

    def has_permission(self, permission):
        return True


class TestOrderMaterialWorkbenchIssues(unittest.TestCase):
    def setUp(self):
        self.issue_docs = {
            "MI-1": Record(name="MI-1", docstatus=1, company="JF", stock_entry_type="Material Issue",
                           jf_material_issue_flow="FLOW-1", items=[Record(
                               jf_flow_order="FLOW-1", item_code="RAW", transfer_qty=Decimal("2"),
                               s_warehouse="Stores - JF")]),
        }
        self.returned = {}
        frappe = types.ModuleType("frappe")
        frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
        frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
        frappe.ValidationError = type("ValidationError", (Exception,), {})
        frappe.PermissionError = type("PermissionError", (Exception,), {})
        frappe.throw = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
        frappe.whitelist = lambda *args, **kwargs: lambda function: function
        frappe.session = types.SimpleNamespace(user="qa@example.com")
        frappe.db = types.SimpleNamespace(sql=self.sql, get_value=self.get_value)
        frappe.get_doc = lambda doctype, name, **kwargs: self.issue_docs[name]
        utils = types.ModuleType("frappe.utils")
        utils.cint = int
        utils.getdate = lambda value: value
        scope = types.ModuleType("jifeng_erp.inventory_scope")
        scope.decimal_number = lambda value: Decimal(str(value or 0))
        material_issue = types.ModuleType("jifeng_erp.material_issue")
        material_issue._required_stock = lambda flow: ({}, {})
        material_issue.make_material_issue = lambda *args, **kwargs: None
        returns = types.ModuleType("jifeng_erp.material_returns")

        def returned_for_flow(flow_name, issue_names):
            self.assertEqual(flow_name, "FLOW-1")
            self.assertEqual(issue_names, {"MI-1"})
            return self.returned

        returns.submitted_return_quantities_for_flow = returned_for_flow
        self.returns_module = returns
        modules = {"frappe": frappe, "frappe.utils": utils,
                   "jifeng_erp.inventory_scope": scope,
                   "jifeng_erp.material_issue": material_issue,
                   "jifeng_erp.material_returns": returns}
        self.frappe = frappe
        with patch.dict(sys.modules, modules):
            path = pathlib.Path(__file__).parents[1] / "jifeng_erp" / "order_material_workbench.py"
            spec = importlib.util.spec_from_file_location("order_material_workbench_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.module._fields = lambda *args, **kwargs: None
        self.module._table = lambda *args, **kwargs: None
        self.module._read = lambda *args, **kwargs: Record(stock_uom="Nos", company="JF")

    def sql(self, query, values=(), **kwargs):
        if "SELECT parent FROM `tabStock Entry Detail`" in query:
            return [("MI-1",)]
        if "SELECT name FROM `tabStock Entry`" in query:
            return []
        raise AssertionError(query)

    def get_value(self, doctype, name, fieldname, **kwargs):
        return name

    def test_partial_return_reduces_workbench_issued_and_allows_remaining_one(self):
        flow = Record(name="FLOW-1", company="JF")
        self.returned = {"RAW": Decimal("1")}
        with patch.dict(sys.modules, {"jifeng_erp.material_returns": self.returns_module}):
            issued, vouchers = self.module._issues(flow)
        self.assertEqual(issued["RAW"], Decimal("1"))
        self.assertEqual(vouchers, [{"name": "MI-1", "docstatus": 1}])
        self.assertEqual(Decimal("2") - issued["RAW"], Decimal("1"))

    def test_draft_or_cancelled_returns_do_not_reduce_workbench_balance(self):
        flow = Record(name="FLOW-1", company="JF")
        # The return helper only includes submitted active receipts; draft and
        # cancelled records therefore produce no net quantity reduction.
        for helper_result in ({}, {"RAW": Decimal("0")}):
            with self.subTest(helper_result=helper_result):
                self.returned = helper_result
                with patch.dict(sys.modules, {"jifeng_erp.material_returns": self.returns_module}):
                    issued, _ = self.module._issues(flow)
                self.assertEqual(issued["RAW"], Decimal("2"))

    def test_net_return_over_original_issue_is_rejected(self):
        flow = Record(name="FLOW-1", company="JF")
        self.returned = {"RAW": Decimal("3")}
        with self.assertRaisesRegex(ValueError, "超过流程单有效领料数量"):
            with patch.dict(sys.modules, {"jifeng_erp.material_returns": self.returns_module}):
                self.module._issues(flow)


if __name__ == "__main__":
    unittest.main()
