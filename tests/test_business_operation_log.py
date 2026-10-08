"""Business operation log emits only dated native facts from readable sources."""

import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Record(dict):
    __getattr__ = dict.get


class Meta:
    def has_field(self, name):
        return name == "company"


class Document(Record):
    meta = Meta()

    def check_permission(self, permission):
        if not self.get("readable", True):
            raise PermissionError("no source read permission")


class TestBusinessOperationLog(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="qa@example.test")
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = LookupError
        self.frappe.ValidationError = ValueError
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.has_permission = Mock(return_value=True)
        self.frappe.get_meta = Mock(return_value=Meta())
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(return_value=[])
        self.sources = {}
        self.doctypes = {}
        self.companies = [Record(name="QA Co")]
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: datetime.date.fromisoformat(str(value)[:10])
        self.patch = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        path = Path(__file__).parents[1] / "jifeng_erp/business_operation_log.py"
        spec = importlib.util.spec_from_file_location("business_operation_log_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def get_list(self, doctype, **kwargs):
        if doctype == "Company":
            return self.companies
        return self.doctypes.get(doctype, [])

    def get_doc(self, doctype, name):
        if doctype == "Company":
            return Document(name=name, readable=True)
        return self.sources[(doctype, name)]

    def test_created_and_version_events_are_sorted_and_link_to_source(self):
        created_at = datetime.datetime(2026, 10, 8, 9, 0)
        document = Document(name="PO-1", doctype="Purchase Order", company="QA Co",
                            owner="buyer@example.test", creation=created_at)
        self.sources[("Purchase Order", "PO-1")] = document
        self.doctypes["Purchase Order"] = [Record(name="PO-1", owner=document.owner,
                                                   creation=created_at, company="QA Co")]
        self.frappe.get_all.return_value = [Record(
            name="VER-1", docname="PO-1", owner="manager@example.test",
            creation=datetime.datetime(2026, 10, 8, 10, 0),
            data='{"changed":[["docstatus",0,1]]}')]

        result = self.module.query_operation_log("2026-10-08", "2026-10-08", "物料")

        self.assertEqual([row["action"] for row in result["rows"]], ["提交", "创建"])
        self.assertEqual(result["rows"][0]["source_name"], "PO-1")
        self.assertEqual(result["rows"][0]["actor"], "manager@example.test")
        self.assertNotIn("old_value", str(result))
        self.assertTrue(result["history_available"])

    def test_inaccessible_source_version_is_never_returned(self):
        hidden = Document(name="SECRET-1", doctype="Purchase Order", company="QA Co",
                          owner="secret@example.test", creation=datetime.datetime(2026, 10, 8), readable=False)
        self.sources[("Purchase Order", "SECRET-1")] = hidden
        self.frappe.get_all.return_value = [Record(
            name="VER-SECRET", docname="SECRET-1", owner="secret@example.test",
            creation=datetime.datetime(2026, 10, 8, 10),
            data='{"changed":[["docstatus",1,2]]}')]
        result = self.module.query_operation_log("2026-10-08", "2026-10-08", "物料")
        self.assertEqual(result["rows"], [])

    def test_scope_requires_module_and_company_permission(self):
        with self.assertRaises(ValueError):
            self.module.query_operation_log("2026-10-08", "2026-10-08", "不存在")
        with self.assertRaises(PermissionError):
            self.module.query_operation_log("2026-10-08", "2026-10-08", "物料", company="HIDDEN")

    def test_only_status_changes_are_named_submit_or_cancel(self):
        self.assertEqual(self.module._version_action(
            '{"changed":[["docstatus",1,2]]}')[0], "取消")
        self.assertEqual(self.module._version_action(
            '{"changed":[["grand_total",10,12]]}')[0], "修改")
        self.assertEqual(self.module._version_action("not-json"), (None, None))


if __name__ == "__main__":
    unittest.main()
