"""Focused permission and truthful-list tests for production instructions."""

import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class Record(dict):
    __getattr__ = dict.get


class Meta:
    def __init__(self, fields):
        self.fields = fields

    def get_field(self, name):
        return next((field for field in self.fields if field.fieldname == name), None)

    def get_permlevel_access(self, **kwargs):
        return [0] if kwargs.get("permission_type") == "read" else []


class ProductionInstructionListTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.ValidationError = ValueError
        self.frappe.PermissionError = PermissionError
        self.frappe.session = Record(user="qa@example.test")
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.has_permission = lambda *args, **kwargs: True
        self.frappe.get_meta = self.get_meta
        self.frappe.get_list = self.get_list
        self.frappe.get_doc = self.get_doc
        self.frappe.db = types.SimpleNamespace()
        self.documents = {}
        self.list_calls = []
        self.denied_fields = set()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.get_permitted_fields
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils})
        modules.start()
        self.addCleanup(modules.stop)
        path = Path(__file__).parents[1] / "jifeng_erp/production_instruction_list.py"
        spec = importlib.util.spec_from_file_location("production_instruction_list_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def get_meta(self, doctype):
        if doctype == "Production Plan":
            fields = [Record(fieldname=name, fieldtype="Data") for name in self.module.PARENT_FIELDS]
            fields.append(Record(fieldname="po_items", fieldtype="Table", options="Production Plan Item"))
            return Meta(fields)
        return Meta([Record(fieldname=name, fieldtype="Data") for name in self.module.CHILD_FIELDS])

    def get_permitted_fields(self, doctype, **kwargs):
        fields = self.module.PARENT_FIELDS if doctype == "Production Plan" else self.module.CHILD_FIELDS
        return [name for name in fields if (doctype, name) not in self.denied_fields]

    def get_list(self, doctype, **kwargs):
        self.list_calls.append((doctype, kwargs))
        if doctype == "Company":
            return ["QA Co"]
        return [Record(**{field: value for field, value in self.documents["PLAN-1"].items()
                          if field in kwargs["fields"]})]

    def get_doc(self, doctype, name):
        return self.documents[name]

    def setup_document(self):
        self.documents["PLAN-1"] = Record(
            name="PLAN-1", docstatus=1, company="QA Co", posting_date="2026-10-08",
            modified="2026-10-08 12:00:00", modified_by="qa@example.test",
            jf_instruction_no="OLD-17", jf_instruction_date="2026-10-07",
            jf_instruction_amount=None, jf_instruction_currency="CNY", jf_instruction_remark="备注",
            po_items=[
                Record(item_code="A", stock_uom="Nos", planned_qty=2),
                Record(item_code="A", stock_uom="Nos", planned_qty=3),
                Record(item_code="B", stock_uom="Kg", planned_qty=1.5),
            ],
            check_permission=lambda permission: None,
        )

    def call(self, **overrides):
        args = {"company": "QA Co", "from_date": "2026-10-01", "to_date": "2026-10-31"}
        args.update(overrides)
        return self.module.list_production_instructions(**args)

    def test_list_uses_native_plan_and_reports_reference_not_real_cost(self):
        self.setup_document()
        result = self.call()
        self.assertEqual(result["rows"][0]["name"], "PLAN-1")
        self.assertEqual(result["rows"][0]["instruction_no"], "OLD-17")
        self.assertEqual(result["rows"][0]["quantity_groups"], [
            {"stock_uom": "Kg", "qty": 1.5}, {"stock_uom": "Nos", "qty": 5},
        ])
        self.assertEqual(result["rows"][0]["style_count"], 2)
        self.assertIsNone(result["rows"][0]["reference_amount"])
        self.assertEqual(result["amount_label"], "参考金额")
        self.assertIn("不代表库存估值", result["amount_note"])
        filters = self.list_calls[-1][1]["filters"]
        self.assertEqual(filters["posting_date"], ["between", ["2026-10-01", "2026-10-31"]])

    def test_exact_date_and_search_are_native_filters(self):
        self.setup_document()
        self.call(exact_date="2026-10-08", search="OLD-17")
        kwargs = self.list_calls[-1][1]
        self.assertEqual(kwargs["filters"]["posting_date"], ["=", "2026-10-08"])
        self.assertEqual(len(kwargs["or_filters"]), 3)

    def test_missing_child_field_permission_rejects_complete_list(self):
        self.setup_document()
        self.denied_fields.add(("Production Plan Item", "planned_qty"))
        with self.assertRaisesRegex(PermissionError, "完整列表"):
            self.call()
        self.assertFalse(any(doctype == "Production Plan" for doctype, _ in self.list_calls))

    def test_exact_date_outside_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "指定日期"):
            self.call(exact_date="2026-11-01")


if __name__ == "__main__":
    unittest.main()
