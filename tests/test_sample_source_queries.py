"""Native association counts must hide protected sources, not ordinary counts."""

import ast
import copy
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get


class MissingError(Exception):
    pass


class ValidationError(Exception):
    pass


class TestSampleSourceQueries(unittest.TestCase):
    def setUp(self):
        self.docs = {}
        self.raw = {}
        self.metas = {}
        self.checks = []
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = MissingError
        self.frappe.ValidationError = ValidationError
        self.frappe.session = Row(user="actor")
        self.frappe.whitelist = lambda: lambda fn: fn

        def throw(message, error=ValueError):
            raise error(message)

        self.frappe.throw = throw
        self.frappe.has_permission = Mock(return_value=True)
        self.frappe.get_meta = lambda dt: self.metas.get(dt, Row(istable=False, issingle=False))
        self.frappe.db = Row(exists=Mock(return_value=True), get_value=Mock())
        self.frappe.get_all = Mock(side_effect=lambda dt, **kwargs: [Row(name=n) for n in self.raw[dt]])
        self.sources = types.ModuleType("jifeng_erp.sample_source_permissions")
        self.sources.PARENTS = frozenset({"Jifeng Sample Order", "Quotation", "Sales Order",
                                          "Jifeng Flow Order", "Work Order"})
        self.sources._reference = lambda dt, name: self.docs.get((dt, name))
        self.sources._scope = Mock(side_effect=lambda doc: bool(doc.get("protected")))

        def child_parent(doc):
            parent = self.docs.get((doc.get("parenttype"), doc.get("parent")))
            if not parent or doc.get("parentfield") != "items":
                throw("invalid native child identity", PermissionError)
            matches = [row for row in parent.get("items", []) if row.name == doc.name]
            keys = ("name", "parent", "parenttype", "parentfield")
            if len(matches) != 1 or any(matches[0].get(k) != doc.get(k) for k in keys):
                throw("invalid native child identity", PermissionError)
            return parent

        self.sources._child_parent = Mock(side_effect=child_parent)

        def check(doc, user):
            self.checks.append((doc.doctype, doc.name, user))
            if not doc.get("readable", True) or user != "actor":
                throw("sensitive-source-name-and-value", PermissionError)

        self.sources._check = Mock(side_effect=check)
        self.native = types.ModuleType("frappe.desk.form.linked_with")
        self.linkinfo = {"Work Order": {"fieldname": "production_item"}}
        self.native_result = {}
        self.tree = {"docs": [], "count": 0}
        self.native.get_linked_doctypes = Mock(side_effect=lambda **kwargs: self.linkinfo)
        self.native.get_linked_docs = Mock(side_effect=lambda **kwargs: copy.deepcopy(self.native_result))
        self.native.get_submitted_linked_docs = Mock(side_effect=lambda **kwargs: copy.deepcopy(self.tree))
        form = types.ModuleType("frappe.desk.form")
        form.linked_with = self.native
        app = types.ModuleType("jifeng_erp")
        app.sample_source_permissions = self.sources
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.desk.form": form,
                                      "jifeng_erp": app,
                                      "jifeng_erp.sample_source_permissions": self.sources}):
            spec = importlib.util.spec_from_file_location("sample_queries_under_test",
                                                         ROOT / "jifeng_erp/sample_source_queries.py")
            self.subject = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.subject)

    def doc(self, dt, name, protected=False, readable=True, **fields):
        doc = Row(doctype=dt, name=name, protected=protected, readable=readable, company="JF", **fields)
        self.docs[dt, name] = doc
        return doc

    def bucket(self, raw, visible, dt="Work Order"):
        self.raw[dt] = raw
        self.native_result[dt] = {"docs": visible, "hidden_count": len(raw) - len(visible)}

    def get(self):
        return self.subject.get("Item", "ITEM")

    def test_readable_protected_payload_and_native_pagination_count_unchanged(self):
        self.doc("Work Order", "PROTECTED", True)
        self.bucket(["PROTECTED", "PROTECTED"], [Row(name="PROTECTED", qty=3, amount=27)])
        expected = copy.deepcopy(self.native_result)
        self.assertEqual(self.get(), expected)
        self.assertEqual(self.checks, [("Work Order", "PROTECTED", "actor")])
        self.native.get_linked_doctypes.assert_called_once_with(doctype="Item")
        self.native.get_linked_docs.assert_called_once_with(
            doctype="Item", name="ITEM", linkinfo=self.linkinfo)
        self.frappe.get_all.assert_called_once_with(
            "Work Order", filters=[], or_filters=[["Work Order", "production_item", "=", "ITEM"]],
            fields=["name"], order_by=None)

    def test_ordinary_hidden_count_and_noncovered_bucket_remain_native(self):
        self.doc("Work Order", "ORDINARY", readable=False)
        self.bucket(["ORDINARY"], [])
        self.native_result["Purchase Order"] = {"docs": [Row(name="PO", grand_total=45)], "hidden_count": 6}
        expected = copy.deepcopy(self.native_result)
        self.assertEqual(self.get(), expected)
        self.sources._check.assert_not_called()

    def test_unreadable_protected_hidden_contribution_removed_but_ordinary_kept(self):
        self.doc("Work Order", "PROTECTED", True, False)
        self.doc("Work Order", "ORDINARY", readable=False)
        self.bucket(["PROTECTED", "ORDINARY"], [])
        self.assertEqual(self.get(), {"Work Order": {"docs": [], "hidden_count": 1}})

    def test_all_unreadable_protected_bucket_disappears(self):
        self.doc("Work Order", "PROTECTED", True, False)
        self.bucket(["PROTECTED"], [])
        self.assertEqual(self.get(), {})

    def test_unreadable_visible_document_removes_entire_sensitive_payload(self):
        self.doc("Work Order", "PROTECTED", True, False)
        self.bucket(["PROTECTED"], [Row(name="PROTECTED", custom_secret=999, amount=100, qty=3)])
        result = self.get()
        self.assertEqual(result, {})
        self.assertNotIn("999", repr(result))

    def test_child_join_multiplicity_removed_without_deduplicating_ordinary_count(self):
        self.doc("Quotation", "PROTECTED", True, False)
        self.doc("Quotation", "ORDINARY", readable=False)
        self.linkinfo = {"Quotation": {"child_doctype": "Quotation Item", "fieldname": ["item_code"]}}
        self.bucket(["PROTECTED", "PROTECTED", "ORDINARY", "ORDINARY"],
                    [Row(name="PROTECTED", amount=88)], "Quotation")
        self.assertEqual(self.get(), {"Quotation": {"docs": [], "hidden_count": 2}})
        self.assertEqual(self.checks, [("Quotation", "PROTECTED", "actor")])

    def test_visible_ordinary_payload_survives_protected_removal(self):
        self.doc("Work Order", "PROTECTED", True, False)
        self.doc("Work Order", "ORDINARY")
        ordinary = Row(name="ORDINARY", qty=2)
        self.bucket(["PROTECTED", "ORDINARY"], [Row(name="PROTECTED", qty=3), ordinary])
        self.assertEqual(self.get(), {"Work Order": {"docs": [ordinary], "hidden_count": 0}})

    def test_count_change_does_not_guess_a_hidden_balance(self):
        self.doc("Work Order", "ORDINARY")
        self.bucket(["ORDINARY"], [])
        self.raw["Work Order"].append("NEW-PRIVATE")
        with self.assertRaisesRegex(PermissionError, "关联数据已变化") as error:
            self.get()
        self.assertNotIn("NEW-PRIVATE", str(error.exception))
        self.sources._check.assert_not_called()

    def test_returned_document_not_matching_raw_query_is_rejected(self):
        self.doc("Work Order", "ORDINARY")
        self.bucket(["ORDINARY"], [Row(name="ALIEN")])
        with self.assertRaisesRegex(PermissionError, "关联数据已变化"):
            self.get()

    def test_disappeared_raw_reference_is_changed_not_an_ordinary_count_deduction(self):
        self.bucket(["DISAPPEARED"], [])
        with self.assertRaisesRegex(PermissionError, "关联数据已变化"):
            self.get()

    def test_native_origin_permission_refusal_precedes_query(self):
        self.frappe.has_permission.side_effect = PermissionError("native denied")
        with self.assertRaisesRegex(PermissionError, "native denied"):
            self.get()
        self.native.get_linked_docs.assert_not_called()

    def test_protected_starting_document_also_requires_complete_source(self):
        self.doc("Quotation", "SOURCE", True, False)
        with self.assertRaisesRegex(PermissionError, "无法完整读取") as error:
            self.subject.get("Quotation", "SOURCE")
        self.assertNotIn("sensitive-source", str(error.exception))
        self.native.get_linked_docs.assert_not_called()

    def test_no_cross_request_permission_receipt_cache(self):
        doc = self.doc("Work Order", "PROTECTED", True)
        self.bucket(["PROTECTED"], [Row(name="PROTECTED", qty=3)])
        self.assertIn("Work Order", self.get())
        doc["readable"] = False
        self.assertEqual(self.get(), {})
        self.assertEqual(len(self.checks), 2)

    def test_direct_link_fields_or_dynamic_link_filter_is_native(self):
        context = {"fieldname": ["link_a", "link_b"], "doctype_fieldname": "link_type"}
        self.assertEqual(self.subject._matching_filters("Item", "ITEM", "Work Order", context),
                         ([["Work Order", "link_type", "=", "Item"]],
                          [["Work Order", "link_a", "=", "ITEM"], ["Work Order", "link_b", "=", "ITEM"]]))

    def test_multiple_child_link_tables_are_or_not_and(self):
        context = {"child_doctype": "Quotation Item", "fieldname": ["fallback"],
                   "child_links": [{"child_doctype": "Quotation Item", "fieldname": ["item_code", "other"]},
                                   {"child_doctype": "Packed Item", "fieldname": ["item_code"]}],
                   "doctype_fieldname": "link_type"}
        self.assertEqual(self.subject._matching_filters("Item", "ITEM", "Quotation", context),
                         ([["Quotation Item", "link_type", "=", "Item"]],
                          [["Quotation Item", "item_code", "=", "ITEM"],
                           ["Quotation Item", "other", "=", "ITEM"],
                           ["Packed Item", "item_code", "=", "ITEM"]]))

    def test_custom_filters_take_native_precedence(self):
        filters = [["Quotation", "name", "=", "EXACT"]]
        context = {"filters": filters, "get_parent": True, "fieldname": ["unused"]}
        self.assertEqual(self.subject._matching_filters("Item", "ITEM", "Quotation", context), (filters, []))
        self.frappe.db.get_value.assert_not_called()

    def test_empty_native_context_keeps_native_unfiltered_branch(self):
        self.doc("Work Order", "ORDINARY")
        self.linkinfo = {"Work Order": {}}
        self.bucket(["ORDINARY"], [])
        self.assertEqual(self.get(), self.native_result)
        self.frappe.get_all.assert_called_once_with(
            "Work Order", filters=[], or_filters=[], fields=["name"], order_by=None)

    def test_get_parent_requires_native_child_and_matching_parenttype(self):
        self.assertIsNone(self.subject._matching_filters("Item", "ITEM", "Quotation", {"get_parent": True}))
        self.metas["Quotation Item"] = Row(istable=True, issingle=False)
        self.frappe.db.get_value.return_value = Row(parenttype="Sales Order", parent="SO")
        self.assertIsNone(self.subject._matching_filters("Quotation Item", "ROW", "Quotation", {"get_parent": True}))
        self.frappe.db.get_value.return_value = Row(parenttype="Quotation", parent="QUOTE")
        self.assertEqual(self.subject._matching_filters("Quotation Item", "ROW", "Quotation", {"get_parent": True}),
                         ([["Quotation", "name", "=", "QUOTE"]], []))
        self.frappe.db.get_value.assert_called_with("Quotation Item", "ROW", ["parenttype", "parent"],
                                                  as_dict=True, order_by=None)

    def test_single_and_unreferenced_native_child_are_skipped(self):
        self.metas["Quotation"] = Row(issingle=True, istable=False)
        self.assertIsNone(self.subject._matching_filters("Item", "ITEM", "Quotation", {"fieldname": "item_code"}))
        self.metas["Quotation Item"] = Row(issingle=False, istable=True)
        self.frappe.db.exists.return_value = False
        self.assertIsNone(self.subject._matching_filters("Item", "ITEM", "Quotation Item", {"fieldname": "item_code"}))
        self.assertEqual(self.frappe.db.exists.call_args_list[0].args,
                         ("DocField", {"options": "Quotation Item"}))
        self.assertEqual(self.frappe.db.exists.call_args_list[1].args,
                         ("Quotation Item", {"parenttype": "Item", "parent": "ITEM"}))

    def test_protected_reference_company_and_current_user_are_exact(self):
        self.doc("Jifeng Flow Order", "FLOW", True)
        self.assertTrue(self.subject.check_protected_reference("Jifeng Flow Order", "FLOW", company="JF"))
        with self.assertRaisesRegex(PermissionError, "无法完整读取"):
            self.subject.check_protected_reference("Jifeng Flow Order", "FLOW", company="OTHER")
        self.assertEqual(self.checks, [("Jifeng Flow Order", "FLOW", "actor")])
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.subject.check_protected_reference("Jifeng Flow Order", "FLOW", company="JF")

    def test_ordinary_report_reference_keeps_existing_permissions_and_company_behavior(self):
        self.doc("Work Order", "ORDINARY", readable=False)
        self.assertFalse(self.subject.check_protected_reference("Work Order", "ORDINARY", company="OTHER"))
        self.assertFalse(self.subject.check_protected_reference("Work Order", None, company="JF"))
        self.assertFalse(self.subject.check_protected_reference("Work Order", "OLD-DANGLING", company="JF"))
        self.assertFalse(self.subject.check_protected_reference("Purchase Order", "PO", company="JF"))
        self.sources._check.assert_not_called()

    def test_exact_child_uses_its_parent_complete_source_gate(self):
        child = self.doc("Quotation Item", "ROW", parent="QUOTE", parenttype="Quotation", parentfield="items")
        self.doc("Quotation", "QUOTE", True, items=[child])
        self.assertTrue(self.subject.check_protected_reference("Quotation Item", "ROW", company="JF"))
        self.assertEqual(self.checks, [("Quotation", "QUOTE", "actor")])
        child["parentfield"] = "other_table"
        with self.assertRaisesRegex(PermissionError, "无法完整读取"):
            self.subject.check_protected_reference("Quotation Item", "ROW", company="JF")

    def test_child_with_wrong_native_parent_identity_never_exposes_payload(self):
        child = self.doc("Work Order Item", "ROW", parent="WO", parenttype="Work Order", parentfield="other")
        self.doc("Work Order", "WO", True, items=[child])
        self.linkinfo = {"Work Order Item": {"fieldname": "item_code"}}
        self.bucket(["ROW"], [Row(name="ROW", qty=88)], "Work Order Item")
        self.assertEqual(self.get(), {})
        self.sources._check.assert_not_called()

    def test_submitted_tree_is_returned_whole_when_sources_readable(self):
        self.doc("Quotation", "QUOTE", True)
        self.doc("Work Order", "WO", True)
        self.tree = {"docs": [Row(doctype="Quotation", name="QUOTE", docstatus=1),
                              Row(doctype="Work Order", name="WO", docstatus=1),
                              Row(doctype="Purchase Order", name="PO", docstatus=1)], "count": 3}
        self.assertEqual(self.subject.get_submitted_linked_docs("Item", "ITEM", '["Sales Invoice"]'), self.tree)
        self.native.get_submitted_linked_docs.assert_called_once_with(
            doctype="Item", name="ITEM", ignore_doctypes_on_cancel_all='["Sales Invoice"]')

    def test_submitted_tree_with_one_unreadable_source_is_rejected_whole_without_ids(self):
        self.doc("Quotation", "QUOTE", True)
        self.doc("Work Order", "PRIVATE-WO", True, False)
        self.tree = {"docs": [Row(doctype="Quotation", name="QUOTE", docstatus=1),
                              Row(doctype="Work Order", name="PRIVATE-WO", docstatus=1)], "count": 2}
        with self.assertRaisesRegex(PermissionError, "无法完整读取") as error:
            self.subject.get_submitted_linked_docs("Item", "ITEM")
        self.assertNotIn("PRIVATE-WO", str(error.exception))
        self.assertNotIn("QUOTE", str(error.exception))

    def test_submitted_ordinary_tree_and_ignore_list_remain_native(self):
        self.doc("Work Order", "ORDINARY", readable=False)
        self.tree = {"docs": [Row(doctype="Work Order", name="ORDINARY", docstatus=1)], "count": 1}
        ignored = ["Sales Invoice"]
        self.assertEqual(self.subject.get_submitted_linked_docs("Item", "ITEM", ignored), self.tree)
        self.native.get_submitted_linked_docs.assert_called_once_with(
            doctype="Item", name="ITEM", ignore_doctypes_on_cancel_all=ignored)
        self.sources._check.assert_not_called()

    def test_submitted_disappeared_protected_candidate_is_refused(self):
        self.tree = {"docs": [Row(doctype="Work Order", name="DISAPPEARED", docstatus=1)], "count": 1}
        with self.assertRaisesRegex(PermissionError, "无法完整读取"):
            self.subject.get_submitted_linked_docs("Item", "ITEM")

    def test_hooks_cover_exact_same_children_and_only_two_native_overrides(self):
        tree = ast.parse((ROOT / "jifeng_erp/hooks.py").read_text(encoding="utf-8"))
        assignments = {node.targets[0].id: node.value for node in tree.body if isinstance(node, ast.Assign)
                       and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
        self.assertEqual(set(ast.literal_eval(assignments["_sample_permission_children"])), self.subject.CHILDREN)
        self.assertEqual(set(ast.literal_eval(assignments["_sample_permission_parents"])), self.sources.PARENTS)
        overrides = ast.literal_eval(assignments["override_whitelisted_methods"])
        native_overrides = {key: value for key, value in overrides.items() if key.startswith("frappe.")}
        self.assertEqual(native_overrides, {
            "frappe.desk.form.linked_with.get": "jifeng_erp.sample_source_queries.get",
            "frappe.desk.form.linked_with.get_submitted_linked_docs":
                "jifeng_erp.sample_source_queries.get_submitted_linked_docs",
        })


if __name__ == "__main__":
    unittest.main()
