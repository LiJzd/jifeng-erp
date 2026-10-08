"""Native-source permissions and filters for subcontracting reversal lookup."""

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

    def get_permlevel_access(self, **kwargs):
        return []


class SubcontractingReversalTests(unittest.TestCase):
    def setUp(self):
        frappe = types.ModuleType("frappe")
        frappe.ValidationError = ValueError
        frappe.PermissionError = PermissionError
        frappe.session = Record(user="qa@example.test")
        frappe.whitelist = lambda **kwargs: lambda fn: fn
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.has_permission = lambda *args, **kwargs: True
        frappe.get_list = self.get_list
        frappe.get_doc = self.get_doc
        frappe.get_meta = self.get_meta
        frappe.db = types.SimpleNamespace(count=self.count)
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.get_permitted_fields
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value))
        modules = patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model, "frappe.utils": utils})
        modules.start()
        self.addCleanup(modules.stop)
        path = Path(__file__).parents[1] / "jifeng_erp/subcontracting_reversal.py"
        spec = importlib.util.spec_from_file_location("subcontracting_reversal_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.documents = {}
        self.profiles = {}
        self.calls = []
        self.denied = set()

    def get_meta(self, doctype):
        if doctype == "Jifeng Partner Profile":
            return Meta([Record(fieldname=field) for field in {"name", "company", "supplier", "archive_kind"}])
        return Meta([Record(fieldname=field) for field in self.module.SOURCES[next(
            kind for kind, spec in self.module.SOURCES.items() if spec["doctype"] == doctype
        )]["fields"]])

    def get_permitted_fields(self, doctype, **kwargs):
        if doctype == "Jifeng Partner Profile":
            return ["name", "company", "supplier", "archive_kind"]
        fields = next(spec["fields"] for spec in self.module.SOURCES.values() if spec["doctype"] == doctype)
        return [field for field in fields if (doctype, field) not in self.denied]

    def get_list(self, doctype, **kwargs):
        self.calls.append((doctype, kwargs))
        if doctype == "Company":
            return ["QA Co"]
        if doctype == "Jifeng Partner Profile":
            filters = kwargs.get("filters", {})
            values = [profile for profile in self.profiles.values()
                      if all(profile.get(field) == value for field, value in filters.items())]
            if kwargs.get("pluck") == "name":
                return [profile.name for profile in values]
            return [Record(name=profile.name) for profile in values]
        return [Record(name=doc.name) for doc in self.documents.values()]

    def count(self, doctype, filters):
        return sum(all(profile.get(field) == value for field, value in filters.items())
                   for profile in self.profiles.values())

    def get_doc(self, doctype, name):
        if doctype == "Jifeng Partner Profile":
            return self.profiles[name]
        return self.documents[name]

    def add_doc(self, doctype, **values):
        values.setdefault("doctype", doctype)
        values.setdefault("company", "QA Co")
        self.documents[values["name"]] = Record(**values)

    def add_profile(self, name="PROFILE-1", company="QA Co", supplier="SUP-1", archive_kind="电镀厂"):
        self.profiles[name] = Record(name=name, company=company, supplier=supplier, archive_kind=archive_kind)

    def call(self, kind, **overrides):
        args = {"kind": kind, "company": "QA Co", "from_date": "2026-10-01",
                "to_date": "2026-10-31"}
        args.update(overrides)
        return self.module.list_subcontracting_reversals(**args)

    def test_subcontract_order_is_returned_as_native_cancel_source(self):
        self.add_profile()
        self.add_doc("Subcontracting Order", name="SCO-1", docstatus=1,
                     transaction_date="2026-10-08", status="Partially Received",
                     supplier="SUP-1", supplier_name="Supplier", total_qty=2,
                     purchase_order="PO-1")
        result = self.call("plating_order")
        row = result["rows"][0]
        self.assertEqual((row["source_doctype"], row["name"], row["docstatus_label"]),
                         ("Subcontracting Order", "SCO-1", "已提交"))
        self.assertIn("取消", result["action_note"])
        self.assertEqual(row["source_classification"], "电镀厂")
        self.assertIn("未分类", result["source_note"])

    def test_receipt_return_fields_are_preserved_not_reclassified(self):
        self.add_profile(archive_kind="外发工厂")
        self.add_doc("Subcontracting Receipt", name="SCR-RET-1", docstatus=1,
                     posting_date="2026-10-08", status="Return", supplier="SUP-1",
                     supplier_name="Supplier", total_qty=-1, is_return=1,
                     return_against="SCR-1")
        row = self.call("outer_receipt")["rows"][0]
        self.assertEqual(row["business_type"], "委外收回退回")
        self.assertEqual(row["return_against"], "SCR-1")

    def test_quality_inspection_query_is_limited_to_subcontract_receipts(self):
        self.add_profile()
        self.add_doc("Subcontracting Receipt", name="SCR-1", docstatus=1,
                     posting_date="2026-10-08", status="Completed", supplier="SUP-1",
                     supplier_name="Supplier", total_qty=2, is_return=0, return_against=None)
        self.add_doc("Quality Inspection", name="QI-1", docstatus=1,
                     report_date="2026-10-08", status="Accepted", inspection_type="Incoming",
                     reference_type="Subcontracting Receipt", reference_name="SCR-1",
                     item_code="FG-1", item_name="Finished Good", company="QA Co")
        result = self.call("plating_inspection")
        filters = next(kwargs["filters"] for doctype, kwargs in self.calls if doctype == "Quality Inspection")
        self.assertEqual(filters["reference_type"], "Subcontracting Receipt")
        self.assertEqual(result["rows"][0]["source_classification"], "电镀厂")

    def test_cancelled_filter_and_company_date_scope_are_native(self):
        self.call("outer_receipt", status="cancelled")
        filters = next(kwargs["filters"] for doctype, kwargs in self.calls if doctype == "Subcontracting Receipt")
        self.assertEqual(filters["docstatus"], 2)
        self.assertEqual(filters["company"], "QA Co")
        self.assertEqual(filters["posting_date"], ["between", ["2026-10-01", "2026-10-31"]])

    def test_missing_required_read_field_rejects_instead_of_partial_rows(self):
        self.denied.add(("Subcontracting Receipt", "return_against"))
        with self.assertRaisesRegex(PermissionError, "完整来源字段"):
            self.call("outer_receipt")
        self.assertFalse(any(name == "Subcontracting Receipt" for name, _ in self.calls))

    def test_duplicate_partner_archive_is_unclassified(self):
        self.add_profile(name="PROFILE-1")
        self.add_profile(name="PROFILE-2")
        self.add_doc("Subcontracting Order", name="SCO-1", docstatus=1,
                     transaction_date="2026-10-08", status="Open", supplier="SUP-1",
                     supplier_name="Supplier", total_qty=2, purchase_order="PO-1")
        row = self.call("plating_order")["rows"][0]
        self.assertEqual(row["source_classification"], "未分类")


if __name__ == "__main__":
    unittest.main()
