import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Doc(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def has_permission(self, permission):
        return self.get("readable", True)

    def is_new(self):
        return bool(self.get("__islocal"))


class TestFulfillment(unittest.TestCase):
    def setUp(self):
        self.documents, self.order_flows, self.native_defaults = {}, {}, {}
        frappe = types.ModuleType("frappe")
        frappe.PermissionError = PermissionError
        frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        frappe.get_doc = Mock(side_effect=lambda doctype, name: self.documents[(doctype, name)])
        frappe.new_doc = Mock(side_effect=lambda doctype, **kwargs: Doc(
            doctype=doctype, __islocal=1, jf_flow_order=self.native_defaults.get(doctype)))
        def flow_names(doctype, filters, **kwargs):
            return [name for (parent, child), names in self.order_flows.items()
                    if parent == filters.get("sales_order") and (
                        not filters.get("sales_order_item") or child == filters["sales_order_item"])
                    for name in names]

        def probe_value(doctype, name, fields, **kwargs):
            for (parent_type, parent_name), parent in self.documents.items():
                if parent_type + " Item" != doctype:
                    continue
                for row in parent.get("items") or []:
                    if row.name == name:
                        return Doc({field: parent_name if field == "parent" else row.get(field) for field in fields})
            return None

        def probe_exists(doctype, filters):
            parent = self.documents.get((doctype.removesuffix(" Item"), filters["parent"]))
            return any(row.get("jf_flow_order") for row in parent.get("items") or []) if parent else False

        frappe.get_all = Mock(side_effect=flow_names)
        frappe.db = types.SimpleNamespace(get_value=Mock(side_effect=probe_value), exists=Mock(side_effect=probe_exists))
        self.frappe = frappe
        path = Path(__file__).parents[1] / "jifeng_erp/fulfillment.py"
        with patch.dict(sys.modules, {"frappe": frappe}):
            spec = importlib.util.spec_from_file_location("fulfillment_under_test", path)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.order = self.source("Sales Order", "SO", [Doc(name="SO-1", item_code="ITEM"),
                                                        Doc(name="SO-2", item_code="ITEM")])
        self.flow = self.source("Jifeng Flow Order", "FLOW-1", [], item_code="ITEM", sales_order="SO",
                                sales_order_item="SO-1", source_changed=1, planned_qty=1)
        self.other_flow = self.source("Jifeng Flow Order", "FLOW-2", [], item_code="ITEM", sales_order="SO",
                                      sales_order_item="SO-2")
        self.order_flows[("SO", "SO-1")] = ["FLOW-1"]
        self.order_flows[("SO", "SO-2")] = ["FLOW-2"]

    def source(self, doctype, name, items, **values):
        document = Doc(doctype=doctype, name=name, company="COMPANY", customer="CUSTOMER", docstatus=1,
                       items=items, **values)
        self.documents[(doctype, name)] = document
        return document

    def target(self, doctype="Delivery Note", **values):
        row = Doc(doctype=doctype + " Item", __islocal=1, flags=Doc(),
                  item_code="ITEM", qty=7, rate=29, stock_qty=14, conversion_factor=2,
                  base_rate=58, amount=203, warehouse="WAREHOUSE", **values)
        return Doc(doctype=doctype, name="TARGET", __islocal=1,
                   company="COMPANY", customer="CUSTOMER", items=[row])

    def validate(self, document):
        self.module.validate_fulfillment_flow(document)
        return document["items"][0]

    def delivery(self, name="DN", child="DN-1", flow="FLOW-1", **values):
        return self.source("Delivery Note", name, [Doc(name=child, item_code="ITEM", jf_flow_order=flow,
                          against_sales_order="SO", so_detail="SO-1")], **values)

    def invoice(self, name="SI", child="SI-1", **values):
        return self.source("Sales Invoice", name, [Doc(name=child, item_code="ITEM", jf_flow_order="FLOW-1",
                          sales_order="SO", so_detail="SO-1", delivery_note="DN", dn_detail="DN-1")], **values)

    def test_same_item_order_rows_use_exact_identity(self):
        row = self.validate(self.target(against_sales_order="SO", so_detail="SO-2"))
        self.assertEqual(row.jf_flow_order, "FLOW-2")
        self.frappe.get_all.assert_any_call("Jifeng Flow Order", filters={
            "sales_order": "SO", "sales_order_item": "SO-2"}, pluck="name", limit_page_length=0)

    def test_standalone_untracked_sales_preserve_native_values(self):
        document = self.target()
        before = dict(document["items"][0])
        row = self.validate(document)
        self.assertEqual({key: row[key] for key in before}, before)
        self.assertIsNone(row.jf_flow_order)
        self.frappe.get_doc.assert_not_called()
        self.frappe.get_all.assert_not_called()

    def test_source_change_marker_and_flow_plan_do_not_freeze_or_price_sales(self):
        document = self.target(against_sales_order="SO", so_detail="SO-1")
        row = self.validate(document)
        self.assertEqual((row.jf_flow_order, row.qty, row.rate, row.stock_qty), ("FLOW-1", 7, 29, 14))

    def test_sales_order_without_flow_stays_untracked(self):
        self.order_flows[("SO", "SO-1")] = []
        self.assertIsNone(self.validate(self.target(against_sales_order="SO", so_detail="SO-1")).jf_flow_order)

    def test_untracked_selector_only_parent_is_noop_without_complete_read(self):
        self.order_flows[("SO", "SO-1")] = []
        self.order.readable = False
        document = self.target("Sales Invoice", sales_order="SO", so_detail="SO-1")
        document.customer = "DIFFERENT-NATIVE-PENDING-VALIDATION"
        before = dict(document["items"][0])
        self.validate(document)
        self.assertEqual(document["items"][0], before)
        self.frappe.get_doc.assert_not_called()

    def test_arbitrary_flow_without_precise_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "精确来源"):
            self.validate(self.target(jf_flow_order="FLOW-1"))

    def test_new_standalone_sales_clear_only_actual_native_flow_default(self):
        for doctype in ("Delivery Note", "Sales Invoice"):
            with self.subTest(doctype=doctype):
                self.native_defaults[doctype + " Item"] = "FLOW-2"
                document = self.target(doctype, jf_flow_order="FLOW-2")
                original = dict(document["items"][0])
                row = self.validate(document)
                self.assertIsNone(row.jf_flow_order)
                self.assertEqual({key: row[key] for key in original if key != "jf_flow_order"},
                                 {key: value for key, value in original.items() if key != "jf_flow_order"})
                self.frappe.new_doc.assert_any_call(doctype + " Item", parent_doc=document, parentfield="items")
        self.frappe.get_doc.assert_not_called()
        self.frappe.get_all.assert_not_called()

    def test_new_untracked_selector_only_source_remains_native_with_flow_default(self):
        self.native_defaults["Sales Invoice Item"] = "FLOW-2"
        self.order_flows[("SO", "SO-1")] = []
        self.order.readable = False
        document = self.target("Sales Invoice", sales_order="SO", so_detail="SO-1", jf_flow_order="FLOW-2")
        document.customer = "DIFFERENT-NATIVE-PENDING-VALIDATION"
        self.assertIsNone(self.validate(document).jf_flow_order)
        self.frappe.get_doc.assert_not_called()

    def test_native_default_other_flow_does_not_conflict_with_exact_order_source(self):
        for doctype, parent_field in (("Delivery Note", "against_sales_order"), ("Sales Invoice", "sales_order")):
            with self.subTest(doctype=doctype):
                self.native_defaults[doctype + " Item"] = "FLOW-2"
                document = self.target(doctype, **{parent_field: "SO"}, so_detail="SO-1", jf_flow_order="FLOW-2")
                self.assertEqual(self.validate(document).jf_flow_order, "FLOW-1")

    def test_client_boolean_flag_cannot_impersonate_server_inherited_state(self):
        self.native_defaults["Delivery Note Item"] = "FLOW-2"
        document = self.target(against_sales_order="SO", so_detail="SO-1", jf_flow_order="FLOW-2")
        document["items"][0].flags.jf_fulfillment_inherited_flow = True
        self.assertEqual(self.validate(document).jf_flow_order, "FLOW-1")

    def test_native_default_never_exempts_hidden_source_or_ancestor(self):
        self.native_defaults["Sales Invoice Item"] = "FLOW-2"
        self.delivery()
        for denied in (self.order, self.flow):
            denied.readable = False
            with self.subTest(doctype=denied.doctype), self.assertRaises(PermissionError) as error:
                self.validate(self.target("Sales Invoice", delivery_note="DN", dn_detail="DN-1",
                                          jf_flow_order="FLOW-2"))
            self.assertNotIn("FLOW-1", str(error.exception))
            self.assertNotIn("FLOW-2", str(error.exception))
            denied.readable = True

    def test_nondefault_forged_flow_remains_rejected_with_native_default(self):
        self.native_defaults["Delivery Note Item"] = "FLOW-2"
        for values in ({}, {"against_sales_order": "SO", "so_detail": "SO-2"}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "精确来源"):
                self.validate(self.target(**values, jf_flow_order="FLOW-1"))

    def test_saved_rows_and_existing_children_never_clear_flow_as_native_default(self):
        self.native_defaults["Delivery Note Item"] = "FLOW-2"
        for saved_parent in (True, False):
            document = self.target(against_sales_order="SO", so_detail="SO-1", jf_flow_order="FLOW-2")
            if saved_parent:
                document["__islocal"] = 0
            else:
                document["items"][0]["__islocal"] = 0
            with self.subTest(saved_parent=saved_parent), self.assertRaisesRegex(ValueError, "精确来源"):
                self.validate(document)
            self.assertEqual(document["items"][0].jf_flow_order, "FLOW-2")
        self.frappe.new_doc.assert_not_called()

    def test_inherited_flow_matching_default_survives_repeat_and_rejects_changed_source(self):
        self.native_defaults["Delivery Note Item"] = "FLOW-1"
        document = self.target(against_sales_order="SO", so_detail="SO-1", jf_flow_order="FLOW-1")
        row = self.validate(document)
        self.assertEqual(self.validate(document).jf_flow_order, "FLOW-1")
        row.so_detail = "SO-2"
        with self.assertRaisesRegex(ValueError, "精确来源"):
            self.validate(document)
        self.assertEqual(row.jf_flow_order, "FLOW-1")
        row.so_detail = "SO-1"
        self.order_flows[("SO", "SO-1")] = []
        with self.assertRaisesRegex(ValueError, "精确来源"):
            self.validate(document)
        self.assertEqual(row.jf_flow_order, "FLOW-1")

    def test_saved_untracked_history_and_traced_history_keep_authoritative_flow(self):
        self.native_defaults["Sales Invoice Item"] = "FLOW-2"
        for source_flow in (None, "FLOW-1"):
            self.delivery(flow=source_flow, readable=bool(source_flow))
            with self.subTest(source_flow=source_flow):
                document = self.target("Sales Invoice", delivery_note="DN", dn_detail="DN-1",
                                       sales_order="SO", so_detail="SO-1", jf_flow_order="FLOW-2")
                self.assertEqual(self.validate(document).jf_flow_order, source_flow)
        saved = self.target("Sales Invoice", delivery_note="DN", dn_detail="DN-1", jf_flow_order="FLOW-1")
        saved["__islocal"] = 0
        self.assertEqual(self.validate(saved).jf_flow_order, "FLOW-1")

    def test_missing_or_wrong_order_child_is_rejected(self):
        for child in (None, "OTHER-ORDER-ROW"):
            with self.subTest(child=child), self.assertRaises(ValueError):
                self.validate(self.target(against_sales_order="SO", so_detail=child))

    def test_unreadable_source_and_flow_never_disclose_flow_identifier(self):
        for denied in (self.order, self.flow):
            denied.readable = False
            with self.subTest(doctype=denied.doctype), self.assertRaises(PermissionError) as error:
                self.validate(self.target(against_sales_order="SO", so_detail="SO-1"))
            self.assertNotIn("FLOW-1", str(error.exception))
            denied.readable = True

    def test_hidden_source_flow_rejects_even_when_native_link_default_is_other_flow(self):
        self.native_defaults["Delivery Note Item"] = "FLOW-2"
        self.flow.readable = False
        with self.assertRaises(PermissionError) as error:
            self.validate(self.target(against_sales_order="SO", so_detail="SO-1", jf_flow_order="FLOW-2"))
        self.assertNotIn("FLOW-1", str(error.exception))
        self.assertNotIn("FLOW-2", str(error.exception))

    def test_cancelled_source_or_other_company_customer_item_is_rejected(self):
        for field, value in (("docstatus", 2), ("company", "OTHER"), ("customer", "OTHER")):
            previous = self.order[field]
            self.order[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(self.target(against_sales_order="SO", so_detail="SO-1"))
            self.order[field] = previous
        self.order["items"][0].item_code = "OTHER"
        with self.assertRaisesRegex(ValueError, "商品"):
            self.validate(self.target(against_sales_order="SO", so_detail="SO-1"))

    def test_flow_company_or_item_mismatch_is_rejected(self):
        for field in ("company", "item_code"):
            previous = self.flow[field]
            self.flow[field] = "OTHER"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "公司、商品"):
                self.validate(self.target(against_sales_order="SO", so_detail="SO-1"))
            self.flow[field] = previous

    def test_ambiguous_order_flows_and_tampered_target_are_rejected(self):
        self.order_flows[("SO", "SO-1")] = ["FLOW-1", "FLOW-2"]
        with self.assertRaisesRegex(ValueError, "多个流程"):
            self.validate(self.target(against_sales_order="SO", so_detail="SO-1"))
        self.order_flows[("SO", "SO-1")] = ["FLOW-1"]
        with self.assertRaisesRegex(ValueError, "精确来源"):
            self.validate(self.target(against_sales_order="SO", so_detail="SO-1", jf_flow_order="FLOW-2"))

    def test_invoice_from_delivery_and_direct_order(self):
        self.delivery()
        for values in ({"delivery_note": "DN", "dn_detail": "DN-1", "sales_order": "SO", "so_detail": "SO-1"},
                       {"sales_order": "SO", "so_detail": "SO-1"}):
            with self.subTest(values=values):
                self.assertEqual(self.validate(self.target("Sales Invoice", **values)).jf_flow_order, "FLOW-1")

    def test_order_link_conflicting_with_delivery_is_rejected_even_for_same_item(self):
        self.delivery()
        with self.assertRaisesRegex(ValueError, "来源链"):
            self.validate(self.target("Sales Invoice", delivery_note="DN", dn_detail="DN-1",
                                      sales_order="SO", so_detail="SO-2"))

    def test_historical_untracked_delivery_is_not_backfilled_from_current_order(self):
        self.delivery(flow=None, readable=False)
        self.assertIsNone(self.validate(self.target("Sales Invoice", delivery_note="DN", dn_detail="DN-1",
                                                   sales_order="SO", so_detail="SO-1")).jf_flow_order)
        self.frappe.get_doc.assert_not_called()
        self.frappe.get_all.assert_not_called()

    def test_delivery_from_invoice_inherits_its_exact_flow(self):
        invoice = self.invoice()
        invoice["items"][0].update(delivery_note=None, dn_detail=None)
        self.assertEqual(self.validate(self.target(against_sales_invoice="SI", si_detail="SI-1",
                                                   against_sales_order="SO", so_detail="SO-1")).jf_flow_order,
                         "FLOW-1")

    def test_invoice_without_order_fields_still_checks_delivery_ancestor(self):
        delivery = self.delivery()
        self.order.readable = False
        with self.assertRaises(PermissionError):
            self.validate(self.target("Sales Invoice", delivery_note="DN", dn_detail="DN-1"))
        self.order.readable = True
        delivery["items"][0].so_detail = "SO-2"
        with self.assertRaisesRegex(ValueError, "精确来源"):
            self.validate(self.target("Sales Invoice", delivery_note="DN", dn_detail="DN-1"))

    def test_return_original_flow_cannot_conflict_with_delivery_flow(self):
        self.delivery(flow="FLOW-2")
        self.invoice()
        document = self.target("Sales Invoice", sales_invoice_item="SI-1")
        document.update(is_return=1, return_against="SI")
        with self.assertRaisesRegex(ValueError, "不同流程"):
            self.validate(document)

    def test_exact_delivery_and_invoice_returns(self):
        self.delivery()
        self.invoice()
        delivery_return = self.target(dn_detail="DN-1", against_sales_order="SO", so_detail="SO-1")
        delivery_return.update(is_return=1, return_against="DN")
        self.assertEqual(self.validate(delivery_return).jf_flow_order, "FLOW-1")
        invoice_return = self.target("Sales Invoice", sales_invoice_item="SI-1", delivery_note="DN", dn_detail="DN-1",
                                     sales_order="SO", so_detail="SO-1")
        invoice_return.update(is_return=1, return_against="SI")
        self.assertEqual(self.validate(invoice_return).jf_flow_order, "FLOW-1")

    def test_return_delivery_credit_invoice_needs_no_original_invoice(self):
        self.delivery("DN-RETURN", "DNR-1", is_return=1)
        document = self.target("Sales Invoice", delivery_note="DN-RETURN", dn_detail="DNR-1",
                               sales_order="SO", so_detail="SO-1")
        document.is_return = 1
        self.assertEqual(self.validate(document).jf_flow_order, "FLOW-1")

    def test_tracked_returns_need_precise_original_child(self):
        self.delivery()
        document = self.target()
        document.update(is_return=1, return_against="DN")
        with self.assertRaisesRegex(ValueError, "精确原明细"):
            self.validate(document)
        document["items"][0].dn_detail = "OTHER-DN-ROW"
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.validate(document)

    def test_untracked_return_without_child_remains_native(self):
        self.delivery(flow=None, readable=False)
        document = self.target()
        document.update(is_return=1, return_against="DN")
        before = dict(document["items"][0])
        self.assertIsNone(self.validate(document).jf_flow_order)
        self.assertEqual(document["items"][0], before)
        self.frappe.get_doc.assert_not_called()

    def test_invoice_return_cannot_switch_delivery_row_even_with_same_flow(self):
        self.delivery()
        self.delivery("DN-OTHER", "DN-OTHER-ROW")
        self.invoice()
        document = self.target("Sales Invoice", sales_invoice_item="SI-1", delivery_note="DN-OTHER", dn_detail="DN-OTHER-ROW",
                               sales_order="SO", so_detail="SO-1")
        document.update(is_return=1, return_against="SI")
        with self.assertRaisesRegex(ValueError, "来源链"):
            self.validate(document)

    def test_return_against_another_return_is_rejected(self):
        self.delivery(is_return=1)
        document = self.target(dn_detail="DN-1")
        document.update(is_return=1, return_against="DN")
        with self.assertRaisesRegex(ValueError, "原销售单据"):
            self.validate(document)


if __name__ == "__main__":
    unittest.main()
