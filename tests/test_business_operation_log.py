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
    fields = []

    def has_field(self, name):
        return name == "company"

    def get_permlevel_access(self, **kwargs):
        return []


class Document(Record):
    meta = Meta()


class TestBusinessOperationLog(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="qa@example.test")
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = LookupError
        self.frappe.ValidationError = ValueError
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.has_permission = Mock(side_effect=self.has_permission)
        self.frappe.get_meta = Mock(return_value=Meta())
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(return_value=[])
        self.frappe.local = Record(message_log=[])
        self.sources = {}
        self.doctypes = {}
        self.companies = [Record(name="QA Co")]
        utils = types.ModuleType("frappe.utils")
        utils.get_datetime = lambda value: datetime.datetime.fromisoformat(str(value).replace(" ", "T"))
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.get_permitted_fields
        self.patch = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils, "frappe.model": model})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        path = Path(__file__).parents[1] / "jifeng_erp/business_operation_log.py"
        spec = importlib.util.spec_from_file_location("business_operation_log_under_test", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def get_list(self, doctype, **kwargs):
        if doctype == "Company":
            return self.companies
        filters = kwargs.get("filters", {})
        if "name" in filters and isinstance(filters.get("company"), list):
            source = self.sources.get((doctype, filters["name"]))
            if source and source.get("list_visible", True) and source.company in filters["company"][1]:
                return [Record(name=source.name, company=source.company)]
            return []
        return self.doctypes.get(doctype, [])

    def get_doc(self, doctype, name):
        if doctype == "Company":
            return Document(name=name, readable=True)
        return self.sources[(doctype, name)]

    def has_permission(self, doctype, permission, doc=None, throw=True):
        return bool(doc.get("readable", True)) if doc else True

    def get_permitted_fields(self, doctype, **kwargs):
        if doctype == "Version":
            # Ordinary roles receive only Version's default projection. Native
            # parent timelines still expose authorized source history.
            return {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
        fields = {"name", "company", "owner", "creation", "docstatus"}
        if doctype == "Purchase Order" and getattr(self, "hide_docstatus", False):
            fields.remove("docstatus")
        return fields

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

        result = self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "物料")

        self.assertEqual([row["action"] for row in result["rows"]], ["提交", "创建"])
        self.assertEqual(result["rows"][0]["source_name"], "PO-1")
        self.assertEqual(result["rows"][0]["actor"], "manager@example.test")
        self.assertNotIn("old_value", str(result))
        self.assertTrue(result["history_available"])

    def test_version_history_uses_parent_timeline_without_global_version_fields(self):
        at = datetime.datetime(2026, 10, 8, 10)
        document = Document(name="EXP-15", doctype="Jifeng Expense Voucher", company="QA Co",
                            owner="buyer@example.test", creation=at)
        self.sources[("Jifeng Expense Voucher", "EXP-15")] = document
        self.frappe.get_all.return_value = [Record(
            name="VER-EXP-15", docname="EXP-15", owner="manager@example.test",
            creation=at, data='{"changed":[["docstatus",0,1]]}')]

        result = self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "财务",
                                                 document_name="EXP-15")

        submit_rows = [row for row in result["rows"] if row["action"] == "提交"]
        self.assertEqual([row["source_name"] for row in submit_rows], ["EXP-15"])
        self.assertEqual(submit_rows[0]["actor"], "manager@example.test")

    def test_inaccessible_source_version_is_never_returned(self):
        hidden = Document(name="SECRET-1", doctype="Purchase Order", company="QA Co",
                          owner="secret@example.test", creation=datetime.datetime(2026, 10, 8), readable=False)
        self.sources[("Purchase Order", "SECRET-1")] = hidden
        self.frappe.get_all.return_value = [Record(
            name="VER-SECRET", docname="SECRET-1", owner="secret@example.test",
            creation=datetime.datetime(2026, 10, 8, 10),
            data='{"changed":[["docstatus",1,2]]}')]
        result = self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "物料")
        self.assertEqual(result["rows"], [])
        self.assertNotIn("SECRET-1", str(self.frappe.local.message_log))

    def test_scope_requires_module_and_company_permission(self):
        with self.assertRaises(ValueError):
            self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "不存在")
        with self.assertRaises(PermissionError):
            self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "物料", company="HIDDEN")

    def test_required_datetime_and_masked_status_action(self):
        with self.assertRaises(ValueError):
            self.module.query_operation_log(None, "2026-10-08T23:59", "物料")
        with self.assertRaises(ValueError):
            self.module.query_operation_log("2026-10-08", "2026-10-08T23:59", "物料")
        self.assertEqual(self.module._version_action('{"changed":[["docstatus",0,1]]}',
                                                     allow_status_change=False), (None, None))

    def test_operator_and_action_filters_are_applied_before_pagination(self):
        at = datetime.datetime(2026, 10, 8, 9)
        for name, owner in (("PO-1", "alice@example.test"), ("PO-2", "bob@example.test")):
            self.sources[("Purchase Order", name)] = Document(name=name, doctype="Purchase Order",
                company="QA Co", owner=owner, creation=at)
            self.doctypes.setdefault("Purchase Order", []).append(
                Record(name=name, owner=owner, creation=at, company="QA Co"))
        result = self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "物料",
                                                 operator="ALICE", action="创建")
        self.assertEqual([row["source_name"] for row in result["rows"]], ["PO-1"])

    def test_action_filter_scans_past_recent_nonmatching_versions(self):
        at = datetime.datetime(2026, 10, 8, 9)
        document = Document(name="PO-1", doctype="Purchase Order", company="QA Co",
                            owner="buyer@example.test", creation=at)
        self.sources[("Purchase Order", "PO-1")] = document
        versions = [Record(name=f"VER-{index:02}", docname="PO-1", owner="manager@example.test",
                           creation=at + datetime.timedelta(minutes=index),
                           data='{"changed":[["grand_total",1,2]]}') for index in range(26)]
        versions.append(Record(name="VER-SUBMIT", docname="PO-1", owner="manager@example.test",
                               creation=at - datetime.timedelta(minutes=1),
                               data='{"changed":[["docstatus",0,1]]}'))
        self.frappe.get_all.return_value = versions

        result = self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "物料",
                                                 action="提交", page_size=25)

        self.assertEqual([row["action"] for row in result["rows"]], ["提交"])
        self.assertFalse(result["has_more"])

    def test_status_version_is_suppressed_when_source_docstatus_is_not_readable(self):
        at = datetime.datetime(2026, 10, 8, 9)
        document = Document(name="PO-1", doctype="Purchase Order", company="QA Co",
                            owner="buyer@example.test", creation=at)
        self.sources[("Purchase Order", "PO-1")] = document
        self.doctypes["Purchase Order"] = [Record(name="PO-1", owner=document.owner,
                                                   creation=at, company="QA Co")]
        self.frappe.get_all.return_value = [Record(name="VER-1", docname="PO-1", owner="actor",
            creation=at, data='{"changed":[["docstatus",0,1]]}')]
        self.hide_docstatus = True

        result = self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "物料")

        self.assertEqual([row["action"] for row in result["rows"]], ["创建"])

    def test_permission_filtered_version_cap_is_reported_as_truncated(self):
        self.sources = {
            ("Material Request", f"HIDDEN-{index}"): Document(
                name=f"HIDDEN-{index}", doctype="Material Request", company="QA Co", readable=False)
            for index in range(2000)
        }

        def versions(doctype, filters, fields, order_by, limit_start, limit_page_length):
            if filters["ref_doctype"] != "Material Request":
                return []
            return [Record(name=f"VER-{index}", docname=f"HIDDEN-{index}", owner="hidden",
                           creation=datetime.datetime(2026, 10, 8),
                           data='{"changed":[["grand_total",1,2]]}')
                    for index in range(limit_start, limit_start + limit_page_length)]

        self.frappe.get_all.side_effect = versions
        result = self.module.query_operation_log("2026-10-08T00:00", "2026-10-08T23:59", "物料")
        self.assertTrue(result["truncated"])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["rows"], [])

    def test_only_status_changes_are_named_submit_or_cancel(self):
        self.assertEqual(self.module._version_action(
            '{"changed":[["docstatus",1,2]]}')[0], "取消")
        self.assertEqual(self.module._version_action(
            '{"changed":[["grand_total",10,12]]}')[0], "修改")
        self.assertEqual(self.module._version_action("not-json"), (None, None))


if __name__ == "__main__":
    unittest.main()
