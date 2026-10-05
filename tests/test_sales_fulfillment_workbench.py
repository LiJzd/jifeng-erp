"""Exact source and permission adversarial models, without a live database."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Document(Record):
    environment = None

    def has_permission(self, permission):
        return ((self.doctype, permission) not in self.environment.role_denials
                and (self.doctype, self.name, permission) not in self.environment.denied)

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native denied")


class Meta:
    def __init__(self, environment, doctype):
        self.environment, self.doctype = environment, doctype

    @property
    def fields(self):
        return list(self.environment.metas[self.doctype].values())

    def get_field(self, field):
        return self.environment.metas[self.doctype].get(field)

    def get_permlevel_access(self, permission_type, parenttype=None, user=None):
        if permission_type == "read":
            return self.environment.read_levels.get(self.doctype, {0})
        return self.environment.mask_levels.get((self.doctype, parenttype), set())


class TestSalesFulfillment(unittest.TestCase):
    def setUp(self):
        self.documents, self.metas, self.permitted = {}, {}, {}
        self.hidden, self.omitted = {}, {}
        self.denied, self.list_denied, self.role_denials = set(), set(), set()
        self.mask_levels, self.read_levels, self.doc_hooks, self.list_hooks = {}, {}, {}, {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="native-reader")
        self.frappe.conf = Record()
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = type("DoesNotExistError", (ValueError,), {})
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_meta = lambda doctype: Meta(self, doctype)
        self.frappe.db = types.SimpleNamespace(commit=Mock(), set_value=Mock(), sql=Mock())
        self.frappe.new_doc, self.frappe.delete_doc = Mock(), Mock()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = self.columns
        utils = types.ModuleType("frappe.utils")
        utils.getdate = lambda value: date.fromisoformat(value) if isinstance(value, str) else value
        fulfillment = types.ModuleType("jifeng_erp.fulfillment")
        fulfillment.FLOW = "Jifeng Flow Order"
        fulfillment.SOURCES = {
            "Delivery Note": (("Sales Order", "against_sales_order", "so_detail"),
                              ("Sales Invoice", "against_sales_invoice", "si_detail")),
            "Sales Invoice": (("Sales Order", "sales_order", "so_detail"),
                              ("Delivery Note", "delivery_note", "dn_detail")),
        }
        fulfillment.RETURN_CHILD = {"Delivery Note": "dn_detail", "Sales Invoice": "sales_invoice_item"}
        patches = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils,
                                          "jifeng_erp.fulfillment": fulfillment})
        patches.start()
        self.addCleanup(patches.stop)
        spec = importlib.util.spec_from_file_location("sales_fulfillment_under_test", ROOT / "jifeng_erp/sales_fulfillment_workbench.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        for doctype, (required, optional) in self.module.PARENTS.items():
            self.schema(doctype, required | optional)
        self.schema("Company", {"name", "modified"})
        for doctype, (fieldname, childtype, required, optional) in self.module.TABLES.items():
            self.schema(childtype, required | optional)
            self.metas[doctype][fieldname] = Record(fieldname=fieldname, fieldtype="Table", permlevel=0)
        self.metas["Stock Entry"]["purpose"].options = "\n".join((
            "Material Issue", "Material Receipt", "Material Transfer", "Material Transfer for Manufacture",
            "Material Consumption for Manufacture", "Manufacture", "Repack", "Send to Subcontractor",
            "Disassemble", "Receive from Customer", "Return Raw Material to Customer",
            "Subcontracting Delivery", "Subcontracting Return"))
        Document.environment = self
        self.add("Company", "JF")
        self.add("Customer", "CUST")
        self.add("Item", "ITEM")
        self.order = self.add("Sales Order", "SO", company="JF", customer="CUST", docstatus=1,
                              status="To Deliver and Bill", transaction_date=date(2026, 10, 1), currency="CNY",
                              per_delivered=Decimal("30"), per_billed=Decimal("15"))
        self.first = self.child("Sales Order", "SO", "SO-ROW-1", item_code="ITEM", qty=10, uom="Pair",
                                stock_uom="Nos", stock_qty=20, delivered_qty=3, billed_amt=Decimal("70"))
        self.second = self.child("Sales Order", "SO", "SO-ROW-2", item_code="ITEM", qty=5, uom="Set",
                                 stock_uom="Nos", stock_qty=15, delivered_qty=0, billed_amt=0)

    def schema(self, doctype, fields):
        self.permitted[doctype] = set(fields)
        self.metas[doctype] = {field: Record(fieldname=field, fieldtype="Data", permlevel=0) for field in fields}

    def add(self, doctype, name, **values):
        row = Record(doctype=doctype, name=name, modified=datetime(2026, 10, 1, 12, 0), **values)
        self.documents[doctype, name] = row
        return row

    def child(self, parenttype, parent, name, **values):
        fieldname, childtype, required, optional = self.module.TABLES[parenttype]
        values = {field: None for field in required | optional if field not in self.module.CHILD} | values
        return self.add(childtype, name, parent=parent, parenttype=parenttype, parentfield=fieldname,
                        idx=1 + sum(kind == childtype and item.parent == parent for (kind, _), item in self.documents.items()), **values)

    def columns(self, doctype, parenttype=None, **kwargs):
        self.assertEqual(kwargs, {"permission_type": "read", "ignore_virtual": True})
        return self.permitted[doctype] - self.hidden.get(doctype, set())

    def get_doc(self, doctype, name):
        if doctype in self.doc_hooks:
            self.doc_hooks[doctype](name)
        if (doctype, name) not in self.documents:
            raise self.frappe.DoesNotExistError("missing")
        doc = Document(deepcopy(self.documents[doctype, name]))
        if doctype in self.module.TABLES:
            fieldname, childtype, _r, _o = self.module.TABLES[doctype]
            doc[fieldname] = [Record(deepcopy(item)) for (kind, _), item in self.documents.items()
                              if kind == childtype and item.parent == name and item.parenttype == doctype
                              and item.parentfield == fieldname]
        return doc

    @staticmethod
    def matches(document, filters):
        for field, wanted in filters.items():
            actual = document.get(field)
            if isinstance(wanted, list):
                op, value = wanted
                if op == "in" and actual not in value:
                    return False
                if op == "between" and not str(value[0]) <= str(actual) <= str(value[1]):
                    return False
                if op == "like" and value.strip("%") not in str(actual):
                    return False
            elif actual != wanted:
                return False
        return True

    def get_list(self, doctype, filters, fields, limit_page_length, order_by, **kwargs):
        if (doctype, "read") in self.role_denials:
            raise PermissionError("native role")
        if doctype in {table[1] for table in self.module.TABLES.values()}:
            self.assertIn("parent_doctype", kwargs)
        rows = [Record({field: row.get(field) for field in fields if field not in self.omitted.get(doctype, set())})
                for (kind, name), row in sorted(self.documents.items())
                if kind == doctype and (kind, name) not in self.list_denied and self.matches(row, filters)]
        if doctype in self.list_hooks:
            self.list_hooks[doctype](filters)
        return rows[:limit_page_length]

    def get_all(self, doctype, filters, fields, limit_page_length, order_by):
        self.assertIn(fields, (["*"], ["name", "enabled", "user_type"]))
        rows = [deepcopy(row) for (kind, _), row in sorted(self.documents.items())
                if kind == doctype and self.matches(row, filters)]
        return rows[:limit_page_length] if limit_page_length else rows

    def read(self):
        return self.module.read_order("JF", "SO")

    def query(self):
        return self.module.query_orders("JF", "2026-10-01", "2026-10-01", "SO")

    def flow(self, name="FLOW", detail="SO-ROW-1", **changed):
        return self.add(self.module.FLOW, name, company="JF", docstatus=0, sales_order="SO", sales_order_item=detail,
                        item_code="ITEM", source_changed=0, work_order=None, planned_qty=10, **changed)

    def delivery(self, name="DN", detail="SO-ROW-1", flow=None, **changed):
        values = dict(company="JF", customer="CUST", docstatus=1, is_return=0, return_against=None,
                      posting_date=date(2026, 10, 1), status="To Bill", currency="CNY") | changed
        doc = self.add("Delivery Note", name, **values)
        row = self.child("Delivery Note", name, name + "-ROW", item_code="ITEM", qty=3, stock_qty=6,
                         uom="Pair", stock_uom="Nos", against_sales_order="SO", so_detail=detail, jf_flow_order=flow)
        return doc, row

    def invoice(self, name="SI", detail="SO-ROW-1", flow=None, **changed):
        values = dict(company="JF", customer="CUST", docstatus=1, is_return=0, return_against=None,
                      posting_date=date(2026, 10, 1), status="Unpaid", update_stock=0, debit_to="AR",
                      outstanding_amount=Decimal("37.25"), party_account_currency="USD", currency="USD") | changed
        doc = self.add("Sales Invoice", name, **values)
        row = self.child("Sales Invoice", name, name + "-ROW", item_code="ITEM", qty=2, stock_qty=4,
                         uom="Pair", stock_uom="Nos", sales_order="SO", so_detail=detail, jf_flow_order=flow)
        if ("Account", "AR") not in self.documents:
            self.add("Account", "AR", company="JF", account_type="Receivable", account_currency="USD", is_group=0)
        self.ledger("PLE-" + name, voucher_no=name, against_voucher_no=name)
        return doc, row

    def ledger(self, name="PLE", **changed):
        values = dict(company="JF", party_type="Customer", party="CUST", account="AR", account_type="Receivable",
                      account_currency="USD", posting_date=date(2026, 10, 1), voucher_type="Sales Invoice",
                      voucher_no="SI", against_voucher_type="Sales Invoice", against_voucher_no="SI",
                      amount=Decimal("80"), amount_in_account_currency=Decimal("40"), delinked=0) | changed
        return self.add(self.module.PLE, name, **values)

    def manufacture(self, name="SE", flow="FLOW", **changed):
        document = self.add("Stock Entry", name, **(dict(company="JF", docstatus=1, work_order=None,
                            purpose="Manufacture", posting_date=date(2026, 9, 20), fg_completed_qty=2) | changed))
        row = self.child("Stock Entry", name, name + "-FG", item_code="ITEM", is_finished_item=1,
                         jf_flow_order=flow, qty=2, uom="Pair", stock_uom="Nos", transfer_qty=4)
        return document, row

    def material(self, flow="FLOW", item="RAW"):
        if ("Item", item) not in self.documents:
            self.add("Item", item)
        return self.child(self.module.FLOW, flow, flow + "-MAT-" + item, item_code=item)

    def other_manufacturing_order(self, name="SO-OTHER", customer="CUST-OTHER"):
        if ("Customer", customer) not in self.documents:
            self.add("Customer", customer)
        self.add("Sales Order", name, company="JF", customer=customer, docstatus=1,
                 status="To Deliver and Bill", transaction_date=date(2026, 10, 1), currency="CNY")
        row = self.child("Sales Order", name, name + "-ROW", item_code="ITEM", qty=3, stock_uom="Nos")
        flow = self.flow("FLOW-" + name, row.name)
        flow.sales_order = name
        return flow

    def test_read_and_query_preserve_native_per_line_units_and_no_arithmetic_or_writes(self):
        result = self.read()
        self.assertEqual([row["name"] for row in result["items"]], ["SO-ROW-1", "SO-ROW-2"])
        self.assertEqual([row["uom"] for row in result["items"]], ["Pair", "Set"])
        self.assertEqual(result["sales_order"]["per_delivered"], "30")
        self.assertEqual(result["items"][0]["billed_amt"], "70")
        self.assertEqual(self.query(), [result])
        self.assertTrue(result["readonly"])
        self.assertFalse(result["can_write"])
        self.assertFalse(result["quantities_aggregated"])
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_company_select_suffices_without_company_profile_read(self):
        self.denied.add(("Company", "JF", "read"))
        self.assertTrue(self.read()["readonly"])
        self.denied.add(("Company", "JF", "select"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_explicit_company_order_dates_and_guest_scope(self):
        for company, order in ((None, "SO"), (" JF", "SO"), ("JF", " SO "), ("JF", [])):
            with self.subTest(company=company, order=order), self.assertRaises(ValueError):
                self.module.read_order(company, order)
        for start, end in (("2026-10-02", "2026-10-01"), ("2026-1-1", "2026-10-01"), (None, "2026-10-01")):
            with self.subTest(start=start), self.assertRaises(ValueError):
                self.module.query_orders("JF", start, end)
        self.frappe.session.user = "Guest"
        with self.assertRaises(PermissionError):
            self.read()

    def test_required_identity_permission_or_native_omission_rejects(self):
        for doctype, field in (("Sales Order", "company"), ("Sales Order Item", "name"), ("Sales Order Item", "item_code")):
            for loss in (self.hidden, self.omitted):
                loss[doctype] = {field}
                with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                    self.read()
                loss.clear()

    def test_optional_fields_omitted_by_native_projection_do_not_come_from_document(self):
        self.omitted.update({"Sales Order": {"per_billed"}, "Sales Order Item": {"billed_amt", "stock_qty"}})
        result = self.read()
        self.assertNotIn("per_billed", result["sales_order"])
        self.assertNotIn("billed_amt", result["items"][0])
        self.assertNotIn("stock_qty", result["items"][0])

    def test_flow_unit_snapshots_remain_distinct_and_keep_exact_decimal_conversion(self):
        self.flow(stock_uom="Dozen", source_uom="Carton", source_stock_uom="Piece",
                  source_conversion_factor=Decimal("12.500000000000000001"), source_qty=Decimal("1.250000000001"))
        result = self.read()
        flow = result["flows"][0]
        self.assertEqual((flow["stock_uom"], flow["source_uom"], flow["source_stock_uom"]),
                         ("Dozen", "Carton", "Piece"))
        self.assertEqual(flow["source_conversion_factor"], "12.500000000000000001")
        self.assertEqual(flow["source_qty"], "1.250000000001")
        self.assertEqual((result["items"][0]["uom"], result["items"][0]["stock_uom"]), ("Pair", "Nos"))
        self.assertEqual(self.query(), [result])
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_changed_order_keeps_persisted_flow_units_instead_of_current_child_units(self):
        snapshot = self.flow(stock_uom="Dozen", source_uom="Carton", source_stock_uom="Piece",
                             source_conversion_factor=Decimal("12.500000000000000001"))
        snapshot.source_changed = 1
        self.first.update(uom="Box", stock_uom="Nos", conversion_factor=Decimal("6"))
        result = self.read()
        flow = result["flows"][0]
        self.assertEqual(flow["source_changed"], 1)
        self.assertEqual((flow["stock_uom"], flow["source_uom"], flow["source_stock_uom"], flow["source_conversion_factor"]),
                         ("Dozen", "Carton", "Piece", "12.500000000000000001"))
        self.assertEqual((result["items"][0]["uom"], result["items"][0]["stock_uom"],
                          result["items"][0]["conversion_factor"]), ("Box", "Nos", "6"))

    def test_flow_unit_read_mask_and_native_omission_never_fall_back_to_full_document(self):
        units = {"stock_uom": "Dozen", "source_uom": "Carton", "source_stock_uom": "Piece",
                 "source_conversion_factor": Decimal("12.500000000000000001")}
        snapshot = self.flow(**units)
        for denied_by in ("read", "mask", "native_projection"):
            with self.subTest(denied_by=denied_by):
                if denied_by == "read":
                    self.hidden[self.module.FLOW] = set(units)
                elif denied_by == "mask":
                    for field in units:
                        self.metas[self.module.FLOW][field].mask = 1
                else:
                    self.omitted[self.module.FLOW] = set(units)
                result = self.read()
                self.assertTrue(set(units).isdisjoint(result["flows"][0]))
                self.assertEqual({field: snapshot[field] for field in units}, units)
                for value in units.values():
                    self.assertNotIn(str(value), repr(result))
                self.hidden.clear()
                self.omitted.clear()
                for field in units:
                    self.metas[self.module.FLOW][field].mask = 0
        for field in units:
            self.metas[self.module.FLOW][field].mask = 1
        self.mask_levels[self.module.FLOW, None] = {0}
        self.assertEqual(self.read()["flows"][0]["source_conversion_factor"], "12.500000000000000001")

    def test_legacy_flow_without_unit_snapshots_never_borrows_order_units_or_conversion(self):
        self.flow()
        self.first.conversion_factor = Decimal("2")
        flow = self.read()["flows"][0]
        for field in ("stock_uom", "source_uom", "source_stock_uom", "source_conversion_factor"):
            self.assertIsNone(flow[field])

    def test_flow_unit_snapshot_edit_or_mask_revoke_during_read_rejects_whole_result(self):
        snapshot = self.flow(stock_uom="Dozen", source_uom="Carton", source_stock_uom="Piece",
                             source_conversion_factor=Decimal("12.500000000000000001"))
        self.invoice()
        for mutation in (lambda: snapshot.update(source_conversion_factor=Decimal("9")),
                         lambda: self.metas[self.module.FLOW]["source_uom"].update(mask=1)):
            calls = []
            def change(_name, mutation=mutation):
                calls.append(True)
                if len(calls) == 2:
                    mutation()
            self.doc_hooks["Account"] = change
            with self.subTest(mutation=mutation), self.assertRaises(PermissionError):
                self.read()
            self.assertEqual(len(calls), 2)
            snapshot.source_conversion_factor = Decimal("12.500000000000000001")
            self.metas[self.module.FLOW]["source_uom"].mask = 0
        self.doc_hooks.clear()

    def test_list_or_full_document_hidden_sources_reject_without_identifiers(self):
        for denial in (self.list_denied, self.denied):
            value = ("Item", "ITEM") if denial is self.list_denied else ("Item", "ITEM", "read")
            denial.add(value)
            with self.assertRaises(PermissionError) as caught:
                self.read()
            self.assertNotIn("ITEM", str(caught.exception))
            denial.clear()

    def test_native_list_only_selects_readable_orders_without_claiming_complete_order_population(self):
        self.list_denied.add(("Sales Order", "SO"))
        self.assertEqual(self.query(), [])
        with self.assertRaises(PermissionError):
            self.read()

    def test_same_item_two_order_rows_have_exact_independent_sources(self):
        self.flow()
        self.delivery(flow="FLOW")
        self.delivery("DN-OTHER", "SO-ROW-2")
        result = self.read()
        self.assertEqual([(d["document"]["name"], d["items"][0]["so_detail"]) for d in result["delivery_notes"]],
                         [("DN", "SO-ROW-1"), ("DN-OTHER", "SO-ROW-2")])
        self.assertIsNone(result["delivery_notes"][1]["items"][0]["jf_flow_order"])

    def test_historical_empty_flow_is_not_backfilled(self):
        self.flow()
        self.delivery()
        self.assertIsNone(self.read()["delivery_notes"][0]["items"][0]["jf_flow_order"])

    def test_missing_exact_source_pair_wrong_parent_row_item_company_or_customer_rejects(self):
        doc, row = self.delivery()
        baseline_doc, baseline_row = deepcopy(doc), deepcopy(row)
        for target, changed in ((row, {"so_detail": None}), (row, {"so_detail": "MISSING"}),
                                (row, {"against_sales_order": None}), (row, {"item_code": "OTHER"}),
                                (doc, {"company": "OTHER"}), (doc, {"customer": "OTHER"})):
            target.update(changed)
            with self.subTest(changed=changed), self.assertRaises((ValueError, PermissionError)):
                self.read()
            doc.clear()
            doc.update(deepcopy(baseline_doc))
            row.clear()
            row.update(deepcopy(baseline_row))

    def test_dn_to_invoice_and_direct_stock_invoice_are_not_combined(self):
        self.flow()
        _doc, delivery = self.delivery(flow="FLOW")
        _invoice, row = self.invoice(flow="FLOW")
        row.delivery_note, row.dn_detail = "DN", delivery.name
        self.invoice("SI-DIRECT", "SO-ROW-2", update_stock=1)
        result = self.read()
        self.assertEqual(len(result["delivery_notes"]), 1)
        self.assertEqual([entry["document"]["update_stock"] for entry in result["sales_invoices"]], [0, 1])
        self.assertEqual(result["sales_invoices"][0]["document"]["outstanding_amount"], "37.25")
        self.assertNotIn("paid_amount", result["sales_invoices"][0]["document"])
        self.assertNotIn("net_delivered_qty", result)

    def test_cross_link_origin_and_persisted_flow_must_match(self):
        self.flow()
        _doc, delivery = self.delivery(flow="FLOW")
        _invoice, row = self.invoice(flow="FLOW")
        row.delivery_note, row.dn_detail, row.so_detail = "DN", delivery.name, "SO-ROW-2"
        with self.assertRaises(ValueError):
            self.read()
        row.so_detail, row.jf_flow_order = "SO-ROW-1", None
        with self.assertRaises(ValueError):
            self.read()

    def test_exact_return_original_child_is_required_and_cancelled_sources_retained(self):
        self.delivery()
        doc, row = self.delivery("DN-RETURN", is_return=1, return_against="DN", docstatus=2, status="Cancelled")
        row.dn_detail, row.qty = "DN-ROW", -1
        result = self.read()
        self.assertEqual(result["delivery_notes"][1]["document"]["docstatus"], 2)
        self.assertEqual(result["delivery_notes"][1]["items"][0]["dn_detail"], "DN-ROW")
        row.dn_detail = None
        with self.assertRaises(ValueError):
            self.read()

    def test_return_row_cannot_reference_same_item_different_order_line(self):
        self.delivery()
        self.delivery("DN-SECOND", "SO-ROW-2")
        _doc, row = self.delivery("RETURN", is_return=1, return_against="DN")
        row.dn_detail = "DN-SECOND-ROW"
        with self.assertRaises(ValueError):
            self.read()

    def test_related_parent_or_child_hidden_never_yields_partial_result(self):
        self.delivery()
        self.delivery("HIDDEN-DN", "SO-ROW-2")
        for doctype, name in (("Delivery Note", "HIDDEN-DN"), ("Delivery Note Item", "HIDDEN-DN-ROW")):
            self.list_denied.add((doctype, name))
            with self.assertRaises(PermissionError) as caught:
                self.read()
            self.assertNotIn("HIDDEN-DN", str(caught.exception))
            self.list_denied.clear()

    def test_flow_duplicates_or_wrong_precise_line_reject(self):
        self.flow()
        self.flow("FLOW-B")
        with self.assertRaises(ValueError):
            self.read()
        del self.documents[self.module.FLOW, "FLOW-B"]
        self.documents[self.module.FLOW, "FLOW"].sales_order_item = "MISSING"
        with self.assertRaises(ValueError):
            self.read()

    def test_flow_exact_child_or_parent_link_cannot_silently_hide_wrong_counterpart(self):
        flow = self.flow()
        for field, value in (("sales_order", "OTHER"), ("sales_order_item", None)):
            original = flow[field]
            flow[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.read()
            flow[field] = original
    def test_work_order_finished_manufacture_sources_do_not_read_job_cards(self):
        flow = self.flow()
        flow.work_order = "WO"
        self.add("Work Order", "WO", company="JF", docstatus=1, sales_order="SO", sales_order_item="SO-ROW-1",
                 production_item="ITEM", jf_flow_order="FLOW", produced_qty=2, stock_uom="Nos")
        self.add("Stock Entry", "SE", company="JF", docstatus=1, work_order="WO", purpose="Manufacture",
                 posting_date=date(2026, 10, 1), fg_completed_qty=2)
        self.child("Stock Entry", "SE", "SE-ROW", item_code="ITEM", is_finished_item=1, jf_flow_order="FLOW", qty=2)
        result = self.read()
        self.assertEqual(result["work_orders"][0]["document"]["produced_qty"], 2)
        self.assertEqual(result["work_orders"][0]["manufacture_entries"][0]["document"]["fg_completed_qty"], 2)
        self.assertFalse(any(call.args[0] == "Job Card" for call in self.frappe.get_list.call_args_list))
        self.documents["Stock Entry Detail", "SE-ROW"].jf_flow_order = "OTHER"
        with self.assertRaises(ValueError):
            self.read()

    def test_work_order_exact_parent_child_and_untracked_product_must_match(self):
        work = self.add("Work Order", "WO", company="JF", docstatus=1, sales_order="SO",
                        sales_order_item="SO-ROW-1", production_item="ITEM", jf_flow_order=None)
        self.assertEqual(self.read()["work_orders"][0]["document"]["name"], "WO")
        self.add("Item", "OTHER-ITEM")
        for field, value in (("sales_order", "OTHER"), ("sales_order_item", None),
                             ("production_item", "OTHER-ITEM")):
            original = work[field]
            work[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.read()
            work[field] = original

    def test_independent_manufacture_preserves_exact_rows_units_dates_cancel_history_and_no_work_order(self):
        self.flow()
        self.flow("FLOW-B", "SO-ROW-2")
        self.manufacture()
        self.manufacture("SE-CANCELLED", "FLOW-B", docstatus=2)
        result = self.read()
        self.assertEqual(result["work_orders"], [])
        entries = result["independent_manufacture_entries"]
        self.assertEqual([entry["document"]["name"] for entry in entries], ["SE", "SE-CANCELLED"])
        self.assertEqual([entry["document"]["docstatus"] for entry in entries], [1, 2])
        self.assertEqual(entries[0]["document"]["posting_date"], "2026-09-20")
        self.assertEqual([(entry["finished_items"][0]["jf_flow_order"], entry["finished_items"][0]["uom"],
                           entry["finished_items"][0]["transfer_qty"]) for entry in entries],
                         [("FLOW", "Pair", 4), ("FLOW-B", "Pair", 4)])
        self.assertNotIn("net_produced_qty", result)
        self.assertFalse(any(call.args[0] == "Job Card" for call in self.frappe.get_list.call_args_list))
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_shared_independent_manufacture_authorizes_other_order_and_customer_but_only_projects_exact_current_rows(self):
        self.flow()
        other = self.other_manufacturing_order()
        self.manufacture()
        self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="ITEM", is_finished_item=1,
                   jf_flow_order=other.name, qty=3, stock_uom="Nos", transfer_qty=3)
        result = self.read()
        self.assertEqual([row["name"] for row in result["independent_manufacture_entries"][0]["finished_items"]], ["SE-FG"])
        self.assertEqual([flow["name"] for flow in result["flows"]], ["FLOW"])
        self.assertEqual(result["receivables"]["voucher_sources"][0]["document"]["customer"], "CUST")
        queried = self.query()
        self.assertEqual([entry["sales_order"]["customer"] for entry in queried], ["CUST", "CUST-OTHER"])
        self.assertEqual([[row["name"] for row in entry["independent_manufacture_entries"][0]["finished_items"]]
                          for entry in queried], [["SE-FG"], ["SE-OTHER-FG"]])

    def test_hidden_companion_flow_order_exact_child_customer_material_or_unlinked_item_denies_whole_result(self):
        self.flow()
        other = self.other_manufacturing_order()
        material = self.material(other.name)
        self.manufacture()
        self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="ITEM", is_finished_item=1, jf_flow_order=other.name)
        self.add("Item", "UNLINKED")
        self.child("Stock Entry", "SE", "SE-UNLINKED", item_code="UNLINKED", is_finished_item=0)
        for doctype, name in ((self.module.FLOW, other.name), ("Sales Order", other.sales_order),
                             ("Sales Order Item", other.sales_order_item), ("Customer", "CUST-OTHER"),
                             ("Jifeng Material Line", material.name), ("Item", "RAW"), ("Item", "UNLINKED")):
            self.list_denied.add((doctype, name))
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError) as caught:
                self.read()
            self.assertNotIn(name, str(caught.exception))
            self.list_denied.clear()

    def test_companion_flow_exact_parent_child_and_source_changed_semantics_are_preserved(self):
        current = self.flow()
        other = self.other_manufacturing_order()
        _document, first = self.manufacture()
        second = self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="ITEM", is_finished_item=1, jf_flow_order=other.name)
        other.sales_order_item = "SO-ROW-2"
        with self.assertRaises(ValueError):
            self.read()
        other.sales_order_item = "SO-OTHER-ROW"
        self.add("Item", "CHANGED")
        current.item_code = other.item_code = first.item_code = second.item_code = "CHANGED"
        current.source_changed = other.source_changed = 1
        self.assertEqual(self.read()["independent_manufacture_entries"][0]["finished_items"][0]["item_code"], "CHANGED")
        other.source_changed = 0
        with self.assertRaises(ValueError):
            self.read()

    def test_raw_material_only_association_is_authorized_but_not_current_order_finished_stock(self):
        self.flow()
        self.material()
        other = self.other_manufacturing_order()
        self.manufacture(flow=other.name)
        self.child("Stock Entry", "SE", "SE-RAW", item_code="RAW", is_finished_item=0, jf_flow_order="FLOW", qty=4)
        self.assertEqual(self.read()["independent_manufacture_entries"], [])
        self.denied.add((self.module.FLOW, other.name, "read"))
        with self.assertRaises(PermissionError):
            self.read()

    def test_supported_nonmanufacture_purposes_finished_flags_and_raw_issues_never_become_manufacture_output(self):
        self.flow()
        self.material()
        document, row = self.manufacture()
        for purpose in ("Disassemble", "Repack", "Subcontracting Delivery", "Subcontracting Return", "Material Receipt"):
            document.purpose = purpose
            with self.subTest(purpose=purpose):
                self.assertEqual(self.read()["independent_manufacture_entries"], [])
        row.item_code, row.is_finished_item = "RAW", 0
        for purpose in ("Material Issue", "Material Transfer", "Material Consumption for Manufacture"):
            document.purpose = purpose
            with self.subTest(purpose=purpose):
                self.assertEqual(self.read()["independent_manufacture_entries"], [])

    def test_independent_manufacture_missing_or_changed_finished_flag_wrong_product_company_or_unknown_purpose_denies(self):
        self.flow()
        self.material()
        document, row = self.manufacture()
        baseline_document, baseline_row = deepcopy(document), deepcopy(row)
        for target, changed in ((row, {"is_finished_item": 0}), (row, {"is_finished_item": None}),
                                (row, {"item_code": "RAW"}), (document, {"company": "OTHER"}),
                                (document, {"purpose": "UNKNOWN"}), (document, {"work_order": "MISSING"})):
            target.update(changed)
            with self.subTest(changed=changed), self.assertRaises((ValueError, PermissionError)):
                self.read()
            document.clear()
            document.update(deepcopy(baseline_document))
            row.clear()
            row.update(deepcopy(baseline_row))

    def test_multiple_different_finished_products_are_not_accepted_as_native_manufacture(self):
        self.flow()
        self.manufacture()
        self.add("Item", "OTHER")
        self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="OTHER", is_finished_item=1)
        with self.assertRaises(ValueError):
            self.read()

    def test_precise_work_order_source_is_preserved_and_not_duplicated_as_independent_manufacture(self):
        flow = self.flow()
        self.add("Work Order", "WO", company="JF", docstatus=1, sales_order="SO", sales_order_item="SO-ROW-1",
                 production_item="ITEM", jf_flow_order="FLOW")
        flow.work_order = "WO"
        document, row = self.manufacture(work_order="WO")
        result = self.read()
        self.assertEqual(result["independent_manufacture_entries"], [])
        self.assertEqual(result["work_orders"][0]["manufacture_entries"][0]["finished_items"][0]["name"], row.name)
        other = self.other_manufacturing_order()
        self.add("Work Order", "WO-OTHER", company="JF", docstatus=1, sales_order=other.sales_order,
                 sales_order_item=other.sales_order_item, production_item="ITEM", jf_flow_order=other.name)
        document.work_order = "WO-OTHER"
        with self.assertRaises(ValueError):
            self.read()

    def test_independent_parent_full_child_and_required_field_or_table_permissions_are_not_bypassed(self):
        self.flow()
        self.manufacture()
        for doctype, name in (("Stock Entry", "SE"), ("Stock Entry Detail", "SE-FG")):
            self.list_denied.add((doctype, name))
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.read()
            self.list_denied.clear()
            self.denied.add(("Stock Entry", "SE", "read"))
            with self.assertRaises(PermissionError):
                self.read()
            self.denied.clear()
        for doctype, field in (("Stock Entry", "purpose"), ("Stock Entry Detail", "is_finished_item"),
                               (self.module.FLOW, "materials"), ("Stock Entry", "items")):
            self.metas[doctype][field].mask = 1
            with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                self.read()
            self.metas[doctype][field].mask = 0

    def test_malformed_reverse_parent_field_or_parent_type_cannot_hide_a_flow_source(self):
        self.flow()
        _document, row = self.manufacture()
        for field, value in (("parentfield", "unknown"), ("parenttype", "OTHER"), ("parent", "MISSING")):
            original = row[field]
            row[field] = value
            with self.subTest(field=field), self.assertRaises((PermissionError, ValueError)):
                self.read()
            row[field] = original

    def test_malformed_companion_or_unlinked_stock_child_cannot_hide_from_native_parent_loading(self):
        self.flow()
        other = self.other_manufacturing_order()
        self.manufacture()
        companion = self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="ITEM", is_finished_item=1, jf_flow_order=other.name)
        self.add("Item", "UNLINKED")
        unlinked = self.child("Stock Entry", "SE", "SE-UNLINKED", item_code="UNLINKED", is_finished_item=0)
        for row in (companion, unlinked):
            for field, value in (("parentfield", "unknown"), ("parenttype", "OTHER")):
                original = row[field]
                row[field] = value
                # Both native parent loading and the typed child query omit
                # this row. Only the complete raw parent closure detects it.
                self.assertNotIn(row.name, [item.name for item in self.get_doc("Stock Entry", "SE").get("items")])
                with self.subTest(row=row.name, field=field), self.assertRaises(PermissionError):
                    self.read()
                row[field] = original

    def test_malformed_companion_flow_material_child_cannot_hide_from_native_flow_loading(self):
        self.flow()
        other = self.other_manufacturing_order()
        material = self.material(other.name)
        self.manufacture()
        self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="ITEM", is_finished_item=1, jf_flow_order=other.name)
        for field, value in (("parentfield", "unknown"), ("parenttype", "OTHER")):
            original = material[field]
            material[field] = value
            self.assertEqual(self.get_doc(self.module.FLOW, other.name).materials, [])
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.read()
            material[field] = original

    def test_late_independent_parent_child_flow_counterpart_or_material_changes_without_modified_reject(self):
        self.flow()
        other = self.other_manufacturing_order()
        material = self.material(other.name)
        self.manufacture()
        self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="ITEM", is_finished_item=1, jf_flow_order=other.name)
        self.invoice()
        baseline = deepcopy(self.documents)
        for doctype, name, field, value in (("Stock Entry", "SE", "work_order", "MISSING"),
                                          ("Stock Entry Detail", "SE-FG", "is_finished_item", 0),
                                          ("Stock Entry Detail", "SE-FG", "jf_flow_order", other.name),
                                          ("Stock Entry Detail", "SE-FG", "parentfield", "unknown"),
                                          (self.module.FLOW, other.name, "sales_order_item", "SO-ROW-1"),
                                          ("Sales Order Item", other.sales_order_item, "item_code", "RAW"),
                                          ("Jifeng Material Line", material.name, "item_code", "ITEM")):
            self.documents = deepcopy(baseline)
            self.doc_hooks["Account"] = lambda _name, key=(doctype, name), field=field, value=value: self.documents[key].update({field: value})
            with self.subTest(doctype=doctype, field=field), self.assertRaises((PermissionError, ValueError)):
                self.read()
        self.doc_hooks.clear()

    def test_empty_reverse_query_rechecks_new_valid_or_malformed_flow_stock_sources(self):
        self.flow()
        self.invoice()
        baseline = deepcopy(self.documents)
        for malformed in (False, True):
            self.documents = deepcopy(baseline)
            fired = []
            def insert(_name):
                if fired:
                    return
                fired.append(True)
                _document, row = self.manufacture()
                if malformed:
                    row.parentfield = "unknown"
            self.doc_hooks["Account"] = insert
            with self.subTest(malformed=malformed), self.assertRaises(PermissionError):
                self.read()
        self.doc_hooks.clear()

    def test_final_manufacturing_source_reloads_and_native_queries_cannot_revoke_earlier_companion_sources(self):
        self.flow()
        other = self.other_manufacturing_order()
        self.manufacture()
        self.child("Stock Entry", "SE", "SE-OTHER-FG", item_code="ITEM", is_finished_item=1, jf_flow_order=other.name)
        self.invoice()
        actions = (lambda: self.denied.add(("Sales Order", other.sales_order, "read")),
                   lambda: self.metas["Stock Entry Detail"]["qty"].update(mask=1),
                   lambda: self.metas[self.module.FLOW]["materials"].update(mask=1))
        for action in actions:
            original_meta = deepcopy(self.metas)
            loads = []
            def revoke(_name, action=action):
                loads.append(True)
                if len(loads) == 2:
                    action()
            self.doc_hooks["Account"] = revoke
            with self.assertRaises(PermissionError):
                self.read()
            self.metas, self.denied = original_meta, set()
        self.doc_hooks.clear()

    def test_invoice_state_is_native_and_payment_ledger_is_unaggregated(self):
        self.invoice()
        self.ledger()
        result = self.read()
        self.assertEqual(result["sales_invoices"][0]["document"]["outstanding_amount"], "37.25")
        entry = result["receivables"]["ledger_entries"][0]
        self.assertEqual((entry["amount"], entry["amount_in_account_currency"], entry["account_currency"]), ("80", "40", "USD"))
        self.assertNotIn("balance", result["receivables"])

    def test_hidden_payment_or_account_or_ledger_denies_entire_native_invoice_state(self):
        self.invoice()
        self.ledger(voucher_type="Payment Entry", voucher_no="PE", amount=-3)
        self.add("Account", "BANK", company="JF", account_type="Bank", account_currency="USD", is_group=0)
        self.add("Payment Entry", "PE", company="JF", docstatus=1, party_type="Customer", party="CUST",
                 payment_type="Receive", paid_from="AR", paid_to="BANK")
        self.child("Payment Entry", "PE", "PE-REF", reference_doctype="Sales Invoice", reference_name="SI", allocated_amount=3)
        self.assertEqual(len(self.read()["receivables"]["voucher_sources"]), 3)
        for doctype, name in (("Payment Entry", "PE"), ("Account", "AR"), (self.module.PLE, "PLE")):
            self.list_denied.add((doctype, name))
            with self.assertRaises(PermissionError) as caught:
                self.read()
            self.assertNotIn(name, str(caught.exception))
            self.list_denied.clear()

    def test_payment_reference_without_ple_is_authorized_and_its_repost_checked(self):
        self.invoice()
        self.add("Account", "BANK", company="JF", account_type="Bank", account_currency="USD", is_group=0)
        self.add("Payment Entry", "PE", company="JF", docstatus=1, party_type="Customer", party="CUST",
                 payment_type="Receive", paid_from="AR", paid_to="BANK")
        self.child("Payment Entry", "PE", "PE-REF", reference_doctype="Sales Invoice", reference_name="SI",
                   allocated_amount=3)
        self.assertIn(("Payment Entry", "PE"), {(row["doctype"], row["document"]["name"])
                                                for row in self.read()["receivables"]["voucher_sources"]})
        for doctype, name in (("Payment Entry", "PE"), ("Payment Entry Reference", "PE-REF")):
            self.list_denied.add((doctype, name))
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.read()
            self.list_denied.clear()
        self.add("Repost Payment Ledger Items", "REPOST-ROW", parent="REPOST", parenttype="Repost Payment Ledger",
                 voucher_type="Payment Entry", voucher_no="PE")
        self.add("Repost Payment Ledger", "REPOST", docstatus=1, repost_status="Queued")
        with self.assertRaises(ValueError):
            self.read()

    def test_journal_reference_without_ple_is_authorized_and_its_repost_checked(self):
        self.invoice()
        self.add("Journal Entry", "JE", company="JF", docstatus=1, posting_date=date(2026, 10, 1))
        self.child("Journal Entry", "JE", "JE-ROW", account="AR", party_type="Customer", party="CUST",
                   reference_type="Sales Invoice", reference_name="SI", credit_in_account_currency=3)
        self.assertIn(("Journal Entry", "JE"), {(row["doctype"], row["document"]["name"])
                                                for row in self.read()["receivables"]["voucher_sources"]})
        self.list_denied.add(("Journal Entry", "JE"))
        with self.assertRaises(PermissionError):
            self.read()
        self.list_denied.clear()
        self.add("Repost Accounting Ledger Items", "REPOST-ROW", parent="REPOST", parenttype="Repost Accounting Ledger",
                 voucher_type="Journal Entry", voucher_no="JE")
        self.add("Repost Accounting Ledger", "REPOST", docstatus=1, status="Failed")
        with self.assertRaises(ValueError):
            self.read()

    def test_unknown_ledger_voucher_source_and_mismatched_party_account_are_rejected(self):
        self.invoice()
        entry = self.ledger(voucher_type="Unknown Voucher", voucher_no="PRIVATE")
        with self.assertRaises(ValueError) as caught:
            self.read()
        self.assertNotIn("PRIVATE", str(caught.exception))
        entry.voucher_type, entry.voucher_no, entry.account = "Sales Invoice", "SI", "AR-OTHER"
        self.add("Account", "AR-OTHER", company="JF", account_type="Receivable", account_currency="USD", is_group=0)
        with self.assertRaises(ValueError):
            self.read()

    def test_required_field_masks_deny_optional_masks_omit_and_grants_restore(self):
        self.invoice()
        self.metas["Sales Invoice"]["outstanding_amount"].mask = 1
        with self.assertRaises(PermissionError):
            self.read()
        self.mask_levels["Sales Invoice", None] = {0}
        self.assertEqual(self.read()["sales_invoices"][0]["document"]["outstanding_amount"], "37.25")
        self.metas["Sales Order Item"]["billed_amt"].mask = 1
        self.assertNotIn("billed_amt", self.read()["items"][0])
        self.frappe.session.user = "Administrator"
        self.assertIn("billed_amt", self.read()["items"][0])

    def test_table_field_read_or_mask_denial_is_not_bypassed_by_child_projection(self):
        self.metas["Sales Order"]["items"].mask = 1
        with self.assertRaises(PermissionError):
            self.read()
        self.metas["Sales Order"]["items"].mask = 0
        self.metas["Sales Order"]["items"].permlevel = 1
        with self.assertRaises(PermissionError):
            self.read()

    def test_later_document_load_cannot_revoke_prior_native_projection_fields_or_source(self):
        self.invoice()
        actions = [lambda: self.metas["Sales Order Item"]["billed_amt"].update(mask=1),
                   lambda: self.denied.add(("Sales Order", "SO", "read")),
                   lambda: self.hidden.update({"Sales Order": {"per_delivered"}})]
        for action in actions:
            initial = deepcopy(self.metas)
            self.doc_hooks["Account"] = lambda name, action=action: action()
            with self.assertRaises(PermissionError):
                self.read()
            self.metas, self.hidden, self.denied = initial, {}, set()

    def test_final_reloads_are_completed_before_whole_result_mask_check(self):
        self.invoice()
        loads = []
        def revoke(name):
            loads.append(name)
            if len(loads) == 2:
                self.metas["Sales Order Item"]["billed_amt"].mask = 1
        self.doc_hooks["Account"] = revoke
        with self.assertRaises(PermissionError):
            self.read()
        self.assertEqual(loads, ["AR", "AR"])

    def test_raw_source_edit_without_modified_and_new_late_shipment_reject(self):
        self.invoice()
        counter = []
        def change(name):
            counter.append(name)
            if len(counter) == 2:
                self.first.billed_amt = Decimal("999")
        self.doc_hooks["Account"] = change
        with self.assertRaises(PermissionError):
            self.read()
        self.doc_hooks.clear()
        self.first.billed_amt = Decimal("70")
        loads = []
        def add_source(name):
            loads.append(name)
            if len(loads) == 2:
                self.delivery("LATE-DN")
        self.doc_hooks["Account"] = add_source
        with self.assertRaises(PermissionError):
            self.read()

    def test_query_batch_rechecks_first_order_when_later_order_load_revokes_it(self):
        self.add("Sales Order", "SO-Z", company="JF", customer="CUST", docstatus=1, status="To Deliver and Bill",
                 transaction_date=date(2026, 10, 1))
        self.child("Sales Order", "SO-Z", "SO-Z-ROW", item_code="ITEM", qty=1)
        self.doc_hooks["Sales Order"] = lambda name: self.denied.add(("Sales Order", "SO", "read")) if name == "SO-Z" else None
        with self.assertRaises(PermissionError):
            self.query()

    def test_query_batch_keeps_distinct_customer_receivables_with_shared_item_and_account(self):
        self.invoice()
        self.add("Customer", "CUST-Z")
        self.add("Sales Order", "SO-Z", company="JF", customer="CUST-Z", docstatus=1, status="To Deliver and Bill",
                 transaction_date=date(2026, 10, 1))
        self.child("Sales Order", "SO-Z", "SO-Z-ROW", item_code="ITEM", qty=1)
        invoice, row = self.invoice("SI-Z", "SO-Z-ROW", customer="CUST-Z", outstanding_amount=Decimal("12"))
        row.sales_order = "SO-Z"
        self.documents[self.module.PLE, "PLE-SI-Z"].party = "CUST-Z"
        results = self.query()
        self.assertEqual([result["sales_order"]["customer"] for result in results], ["CUST", "CUST-Z"])
        self.assertEqual([[row["party"] for row in result["receivables"]["ledger_entries"]]
                          for result in results], [["CUST"], ["CUST-Z"]])
        self.assertEqual(results[1]["sales_invoices"][0]["document"]["outstanding_amount"], "12")
        self.list_denied.add(("Customer", "CUST-Z"))
        with self.assertRaises(PermissionError):
            self.query()

    def test_empty_child_search_still_requires_parent_table_permission(self):
        self.metas["Delivery Note"]["items"].mask = 1
        with self.assertRaises(PermissionError):
            self.read()

    def test_cached_authorization_changes_are_detected_from_raw_inputs(self):
        self.invoice()
        seen = []
        def change(name):
            seen.append(name)
            if len(seen) == 2:
                self.add("Has Role", "ROLE-CHANGE", parenttype="User", parent="native-reader", role="Accounts User")
        self.doc_hooks["Account"] = change
        with self.assertRaises(PermissionError):
            self.read()

    def test_pending_failed_and_changed_reposts_are_not_complete_native_states(self):
        self.invoice()
        self.add("Repost Payment Ledger Items", "REPOST-ROW", parent="REPOST", parenttype="Repost Payment Ledger",
                 voucher_type="Sales Invoice", voucher_no="SI")
        job = self.add("Repost Payment Ledger", "REPOST", docstatus=1, repost_status="Queued")
        for status in ("Queued", "Failed", "In Progress"):
            job.repost_status = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.read()
        job.repost_status = "Completed"
        self.assertTrue(self.read()["readonly"])
        seen = []
        def change(name):
            seen.append(name)
            if len(seen) == 2:
                job.repost_status = "Failed"
        self.doc_hooks["Account"] = change
        with self.assertRaises(ValueError):
            self.read()

    def test_nonzero_submitted_invoice_requires_native_ledger_source(self):
        invoice, _row = self.invoice()
        del self.documents[self.module.PLE, "PLE-SI"]
        with self.assertRaises(ValueError):
            self.read()
        invoice.outstanding_amount = 0
        self.assertTrue(self.read()["readonly"])

    def test_credit_from_return_dn_keeps_exact_history_without_si_return_against(self):
        self.delivery()
        _doc, returned = self.delivery("DN-RETURN", is_return=1, return_against="DN")
        returned.dn_detail, returned.qty = "DN-ROW", -1
        _invoice, row = self.invoice("CREDIT", is_return=1)
        row.delivery_note, row.dn_detail, row.qty = "DN-RETURN", "DN-RETURN-ROW", -1
        self.assertTrue(self.read()["sales_invoices"][0]["document"]["is_return"])
        row.delivery_note, row.dn_detail = None, None
        with self.assertRaises(ValueError):
            self.read()

    def test_replica_and_oversized_order_batches_do_not_claim_complete_current_view(self):
        self.frappe.conf.read_from_replica = True
        with self.assertRaises(ValueError):
            self.read()
        self.frappe.conf.clear()
        for index in range(21):
            self.add("Sales Order", "SO-LIMIT-" + str(index), company="JF", transaction_date=date(2026, 10, 1))
        with self.assertRaises(ValueError):
            self.query()

    def test_read_mask_changes_during_final_native_queries_recheck_whole_projection(self):
        self.invoice()
        matches = []
        def revoke(filters):
            if filters.get("name") == "AR":
                matches.append(True)
                if len(matches) == 2:
                    self.metas["Sales Order Item"]["billed_amt"].mask = 1
        self.list_hooks["Account"] = revoke
        with self.assertRaises(PermissionError):
            self.read()
        self.assertEqual(len(matches), 2)

    def test_final_query_hooks_cannot_edit_previously_checked_values_or_membership(self):
        self.invoice()
        for change in (lambda: self.first.update(billed_amt=Decimal("999")),
                       lambda: self.order.update(per_delivered=Decimal("999")),
                       lambda: self.delivery("LATE-DN")):
            calls = []
            def edit(filters):
                if filters.get("name") == "AR":
                    calls.append(True)
                    if len(calls) == 2:
                        change()
            self.list_hooks["Account"] = edit
            with self.subTest(change=change), self.assertRaises(PermissionError):
                self.read()
            self.assertEqual(len(calls), 2)
            self.first.billed_amt, self.order.per_delivered = Decimal("70"), Decimal("30")
            self.documents.pop(("Delivery Note", "LATE-DN"), None)
            self.documents.pop(("Delivery Note Item", "LATE-DN-ROW"), None)

    def test_last_document_permission_hook_cannot_change_raw_roles_behind_cached_checks(self):
        self.invoice()
        original = Document.has_permission
        calls = []
        def cached_permission(document, permission):
            if document.doctype == "Account":
                calls.append(True)
                if len(calls) == 3:
                    self.add("Has Role", "LATE-ROLE-CHANGE", parenttype="User", parent="native-reader",
                             role="Accounts User")
            return original(document, permission)
        with patch.object(Document, "has_permission", cached_permission), self.assertRaises(PermissionError):
            self.read()
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
