"""Adversarial source closure models; no database or native permission changes."""

from copy import deepcopy
from datetime import date, datetime, timedelta
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

    def check_permission(self, permission):
        if ((self.doctype, permission) in self.environment.role_denials
                or (self.doctype, self.name, permission) in self.environment.denied):
            raise PermissionError("native denied")

    def precision(self, field):
        return self.environment.precisions.get((self.doctype, field),
            2 if self.environment.metas[self.doctype][field].fieldtype == "Currency" else 6)


class Meta:
    def __init__(self, env, doctype):
        self.env, self.doctype = env, doctype

    @property
    def fields(self):
        return list(self.env.metas[self.doctype].values())

    def get_field(self, name):
        return self.env.metas[self.doctype].get(name)

    def get_permlevel_access(self, permission_type, parenttype=None, user=None):
        if permission_type == "read":
            return self.env.read_levels.get(self.doctype, {0})
        return self.env.mask_levels.get((self.doctype, parenttype), set())


class TestFlowCostSources(unittest.TestCase):
    def setUp(self):
        self.documents, self.permitted, self.metas = {}, {}, {}
        self.hidden, self.omitted, self.mask_levels, self.read_levels = {}, {}, {}, {}
        self.denied, self.list_denied, self.role_denials = set(), set(), set()
        self.doc_hooks, self.list_hooks, self.raw_hooks, self.precisions = {}, {}, {}, {}
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="native-stock-reader")
        self.frappe.conf = Record()
        self.frappe.PermissionError = PermissionError
        self.frappe.DoesNotExistError = type("DoesNotExistError", (ValueError,), {})
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.get_list = Mock(side_effect=self.get_list)
        self.frappe.get_all = Mock(side_effect=self.get_all)
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.frappe.get_meta = lambda doctype: Meta(self, doctype)
        self.frappe.db = types.SimpleNamespace(commit=Mock(), set_value=Mock(), sql=Mock())
        self.frappe.new_doc, self.frappe.delete_doc = Mock(), Mock()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, **kw: self.permitted[doctype] - self.hidden.get(doctype, set())
        utils = types.ModuleType("frappe.utils")
        utils.flt = lambda value, precision=None: round(float(value), precision) if precision is not None else float(value)
        patches = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils})
        patches.start()
        self.addCleanup(patches.stop)
        spec = importlib.util.spec_from_file_location("flow_cost_sources_under_test", ROOT / "jifeng_erp/flow_cost_sources.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        money = {"rate", "amount", "base_amount", "rm_supp_cost", "stock_value_difference",
                 "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty"}
        for doctype, fields in self.module.PARENTS.items():
            self.schema(doctype, fields, money)
        self.schema("Company", {"name", "modified", "default_currency"}, money)
        for doctype, tables in self.module.TABLES.items():
            for fieldname, childtype, fields in tables:
                self.schema(childtype, fields, money)
                self.metas[doctype][fieldname] = Record(fieldname=fieldname, fieldtype="Table", permlevel=0, options=childtype)
                # Installed Frappe excludes Table from get_permitted_fields.
        Document.environment = self
        self.add("Company", "JF", default_currency="CNY")
        self.add("Currency", "CNY")
        self.add("Item", "FG", stock_uom="Nos", is_stock_item=1)
        self.add("Item", "RM", stock_uom="Kg", is_stock_item=1)
        self.add("Warehouse", "FG-WH", company="JF", is_group=0)
        self.add("Warehouse", "RM-WH", company="JF", is_group=0)
        self.add("Warehouse", "REJECT-WH", company="JF", is_group=0)
        self.add(self.module.FLOW, "FLOW", company="JF", item_code="FG", docstatus=0)
        self.child(self.module.FLOW, "FLOW", "materials", "MAT", item_code="RM")

    def schema(self, doctype, fields, money):
        # Independent installed ERPNext v16 schema fact: this is a temporary
        # mapper attribute, absent from SCR Item's DocFields/valid columns.
        # Do not fabricate permission to it from the service's required set.
        if doctype == "Subcontracting Receipt Item":
            fields = set(fields) - {"return_qty_from_rejected_warehouse"}
        self.permitted[doctype] = set(fields)
        self.metas[doctype] = {field: Record(fieldname=field, fieldtype="Currency" if field in money else "Float",
                                             permlevel=0) for field in fields}

    def add(self, doctype, name, **values):
        fields = self.module.PARENTS.get(doctype, set())
        row = Record({field: None for field in fields} | {"doctype": doctype, "name": name,
            "modified": datetime(2026, 10, 3, 10, 0), "docstatus": 1} | values)
        self.documents[doctype, name] = row
        return row

    def child(self, parenttype, parent, fieldname, name, **values):
        _field, childtype, fields = next(table for table in self.module.TABLES[parenttype] if table[0] == fieldname)
        row = self.add(childtype, name, **({field: None for field in fields - self.module.CHILD} | {
            "parent": parent, "parenttype": parenttype, "parentfield": fieldname, "idx": 1} | values))
        return row

    def get_doc(self, doctype, name):
        if doctype in self.doc_hooks:
            self.doc_hooks[doctype](name)
        if (doctype, name) not in self.documents:
            raise self.frappe.DoesNotExistError("missing")
        doc = Document(deepcopy(self.documents[doctype, name]))
        for fieldname, childtype, _fields in self.module.TABLES.get(doctype, ()):
            doc[fieldname] = [Document(deepcopy(row)) for (kind, _), row in self.documents.items()
                              if kind == childtype and row.parent == name and row.parenttype == doctype
                              and row.parentfield == fieldname]
        return doc

    def matches(self, row, filters):
        if isinstance(filters, list):
            for doctype, field, op, value in filters:
                if doctype == row.doctype:
                    if not self.match_value(row.get(field), op, value):
                        return False
                elif not any(kind == doctype and child.get("parent") == row.name
                             and self.match_value(child.get(field), op, value)
                             for (kind, _), child in self.documents.items()):
                    return False
            return True
        return all(self.match_value(row.get(field), *value) if isinstance(value, list)
                   else row.get(field) == value for field, value in filters.items())

    @staticmethod
    def match_value(actual, op, value):
        return actual == value if op == "=" else actual in value if op == "in" else False

    def get_list(self, doctype, filters, limit_page_length, fields=None, order_by=None, pluck=None, **kwargs):
        if (doctype, "read") in self.role_denials:
            raise PermissionError("native role denied")
        childtypes = {child for tables in self.module.TABLES.values() for _, child, _ in tables}
        if doctype in childtypes:
            self.assertIn("parent_doctype", kwargs)
        rows = [row for (kind, name), row in sorted(self.documents.items()) if kind == doctype
                and (kind, name) not in self.list_denied and self.matches(row, filters)]
        if pluck:
            projected = [row[pluck] for row in rows]
        else:
            projected = [Record({field: row.get(field) for field in fields
                                 if field not in self.omitted.get(doctype, set())}) for row in rows]
        if doctype in self.list_hooks:
            self.list_hooks[doctype](filters)
        return projected[:limit_page_length] if limit_page_length else projected

    def get_all(self, doctype, filters, fields, limit_page_length, order_by, **kwargs):
        if doctype in self.raw_hooks:
            self.raw_hooks[doctype](filters)
        rows = [deepcopy(row) for (kind, _), row in sorted(self.documents.items())
                if kind == doctype and self.matches(row, filters)]
        return rows[:limit_page_length] if limit_page_length else rows

    def ledger(self, name, doctype, voucher, detail, item, warehouse, quantity, value, adjustment=0):
        return self.add(self.module.SLE, name, company="JF", item_code=item, warehouse=warehouse,
            stock_uom=self.documents["Item", item].stock_uom, voucher_type=doctype, voucher_no=voucher,
            voucher_detail_no=detail, actual_qty=quantity, stock_value_difference=value, is_cancelled=0,
            is_adjustment_entry=adjustment, posting_date=date(2026, 10, 3), posting_time=timedelta(hours=10))

    def receipt(self, name="SCR", qty=70, rejected=0, loss=30, is_return=0, original=None, flow="FLOW"):
        received = Decimal(str(qty)) + Decimal(str(rejected)) + Decimal(str(loss))
        doc = self.add(self.module.SCR, name, company="JF", posting_date=date(2026, 10, 3),
            posting_time=timedelta(hours=10), is_return=is_return, return_against=original,
            status="Return" if is_return else "Completed", supplier_warehouse="RM-WH")
        item = self.child(self.module.SCR, name, "items", name + "-FG", item_code="FG", jf_flow_order=flow,
            qty=qty, rejected_qty=rejected, process_loss_qty=loss, received_qty=received, stock_uom="Nos",
            conversion_factor=1, warehouse="FG-WH", rejected_warehouse="REJECT-WH",
            subcontracting_receipt_item=original + "-FG" if original else None,
            rm_cost_per_qty=1, service_cost_per_qty=2, additional_cost_per_qty=0, rm_supp_cost=received,
            secondary_items_cost_per_qty=0, landed_cost_voucher_amount=0, amount=received * 3, rate=3)
        self.child(self.module.SCR, name, "supplied_items", name + "-RM", main_item_code="FG", rm_item_code="RM",
            reference_name=item.name, consumed_qty=received, stock_uom="Kg", rate=1, amount=received)
        if qty:
            self.ledger(name + "-FG-SLE", self.module.SCR, name, item.name, "FG", "FG-WH", qty, received * 3)
        if rejected:
            self.ledger(name + "-RJ-SLE", self.module.SCR, name, item.name, "FG", "REJECT-WH", rejected, 0)
        if received:
            self.ledger(name + "-RM-SLE", self.module.SCR, name, name + "-RM", "RM", "RM-WH", -received, -received)
        return doc, item

    def issue(self, name="ISSUE", qty=10, factor=2, flow="FLOW", **changes):
        doc = self.add(self.module.SE, name, **(dict(company="JF", purpose="Material Issue", stock_entry_type="Material Issue",
            posting_date=date(2026, 10, 3), posting_time=timedelta(hours=10)) | changes))
        row = self.child(self.module.SE, name, "items", name + "-RM", item_code="RM", jf_flow_order=flow,
            qty=qty, uom="Bag" if factor != 1 else "Kg", stock_uom="Kg", conversion_factor=factor,
            transfer_qty=Decimal(str(qty)) * Decimal(str(factor)), s_warehouse="RM-WH", t_warehouse=None)
        self.ledger(name + "-SLE", self.module.SE, name, row.name, "RM", "RM-WH", -row.transfer_qty, -row.transfer_qty * 4)
        return doc, row

    def test_loss_inclusive_fees_use_exact_consumption_not_accepted_times_rates(self):
        self.receipt()
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result, {"rows": [{"receipt": "SCR", "qty": 70, "material": Decimal(100),
            "processing": Decimal(200), "other": Decimal(0)}], "totals": {
            "material": Decimal(100), "processing": Decimal(200), "other": Decimal(0)}, "currency": "CNY"})
        self.assertNotEqual(sum(result["totals"].values()), 70 * (1 + 2))
        self.assertEqual(self.module.receipt_batch_candidates("FLOW")[0]["qty"], 100)
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql, self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_blank_flow_return_is_included_with_exact_signed_original_reference(self):
        self.receipt()
        self.receipt("RETURN", qty=-10, loss=0, is_return=1, original="SCR", flow=None)
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": 90, "processing": 180, "other": 0})
        batch = self.module.receipt_batch_candidates("FLOW")[0]
        self.assertEqual((batch["qty"], batch["original_qty"], batch["returned_qty"], batch["lost_qty"]), (90, 100, 10, 30))

    def test_accepted_rejected_and_lost_all_contribute_and_missing_qty_zero_cost_is_rejected(self):
        self.receipt(qty=60, rejected=10, loss=30)
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"]["material"], 100)
        self.documents.clear()
        self.setUp()
        _doc, item = self.receipt(qty=0, rejected=100, loss=0)
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"], {"material": 100, "processing": 200, "other": 0})
        item.rm_supp_cost = None  # native qty=0 branch can leave a missing/stale source
        with self.assertRaisesRegex(ValueError, "缺失"):
            self.module.receipt_costs("FLOW")

    def test_hidden_parent_return_child_and_ledger_reject_the_whole_cost(self):
        self.receipt()
        self.receipt("RETURN", qty=-10, loss=0, is_return=1, original="SCR", flow=None)
        for key in ((self.module.SCR, "SCR"), (self.module.SCR, "RETURN"),
                    ("Subcontracting Receipt Item", "SCR-FG"),
                    ("Subcontracting Receipt Supplied Item", "SCR-RM"), (self.module.SLE, "SCR-RM-SLE")):
            self.list_denied.add(key)
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.module.receipt_costs("FLOW")
            self.list_denied.clear()

    def test_parent_read_and_company_select_are_real_guards(self):
        self.receipt()
        self.denied.add(("Company", "JF", "read"))
        self.assertEqual(self.module.receipt_costs("FLOW")["currency"], "CNY")
        for key in (("Company", "JF", "select"), (self.module.FLOW, "FLOW", "read"),
                    (self.module.SCR, "SCR", "read"), ("Item", "RM", "read")):
            self.denied.add(key)
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.module.receipt_costs("FLOW")
            self.denied.remove(key)

    def test_fields_omitted_or_masked_cannot_be_filled_from_full_documents(self):
        self.receipt()
        for doctype, field in ((self.module.FLOW, "company"), ("Subcontracting Receipt Item", "rm_supp_cost"),
                              ("Subcontracting Receipt Supplied Item", "amount"), (self.module.SLE, "stock_value_difference")):
            for loss in (self.hidden, self.omitted):
                loss[doctype] = {field}
                with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                    self.module.receipt_costs("FLOW")
                loss.clear()
            df = self.metas[doctype][field]
            df.mask = 1
            with self.assertRaises(PermissionError):
                self.module.receipt_costs("FLOW")
            df.mask = 0

    def test_parent_table_permission_and_mask_and_corrupt_child_identity_reject(self):
        self.receipt()
        for field in ("items", "supplied_items", "additional_costs"):
            df = self.metas[self.module.SCR][field]
            df.permlevel = 1
            with self.assertRaises(PermissionError):
                self.module.receipt_costs("FLOW")
            df.permlevel = 0
            df.mask = 1
            with self.assertRaises(PermissionError):
                self.module.receipt_costs("FLOW")
            df.mask = 0
        self.documents["Subcontracting Receipt Item", "SCR-FG"].parenttype = "Purchase Receipt"
        with self.assertRaises(ValueError):
            self.module.receipt_costs("FLOW")

    def test_cross_company_item_return_reference_and_sign_do_not_silently_skip(self):
        self.receipt()
        ret, row = self.receipt("RETURN", qty=-10, loss=0, is_return=1, original="SCR", flow=None)
        cases = ((ret, "company", "OTHER"), (row, "item_code", "RM"),
                 (row, "subcontracting_receipt_item", None), (row, "subcontracting_receipt_item", "UNKNOWN-ROW"),
                 (row, "qty", 10), (row, "process_loss_qty", -2))
        for target, field, value in cases:
            old = target[field]
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.receipt_costs("FLOW")
            target[field] = old

    def test_overreturn_or_changed_material_source_and_ambiguous_allocation_reject(self):
        self.receipt()
        ret, row = self.receipt("RETURN", qty=-71, loss=0, is_return=1, original="SCR", flow=None)
        with self.assertRaises(ValueError):
            self.module.receipt_costs("FLOW")
        # Separate scopes isolate the historical amount and supplied-source gates.
        ret.docstatus = 2
        item = self.documents["Subcontracting Receipt Item", "SCR-FG"]
        for field, value in (("amount", 270), ("rm_supp_cost", 90), ("received_qty", 70),
                             ("secondary_items_cost_per_qty", 1), ("landed_cost_voucher_amount", 10),
                             ("conversion_factor", 2)):
            old = item[field]
            item[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.receipt_costs("FLOW")
            item[field] = old

    def test_exact_return_preserves_accepted_and_rejected_warehouse_limits(self):
        self.receipt(qty=70, rejected=10, loss=20)
        _doc, row = self.receipt("RETURN", qty=-75, loss=0, is_return=1, original="SCR", flow=None)
        with self.assertRaises(ValueError):
            self.module.receipt_costs("FLOW")  # cannot spend rejected balance as accepted stock
        row.qty, row.received_qty, row.rm_supp_cost, row.amount = -10, -10, -10, -30
        rm = self.documents["Subcontracting Receipt Supplied Item", "RETURN-RM"]
        rm.consumed_qty, rm.amount = -10, -10
        fg_sle = self.documents[self.module.SLE, "RETURN-FG-SLE"]
        fg_sle.actual_qty, fg_sle.stock_value_difference = -10, -30
        rm_sle = self.documents[self.module.SLE, "RETURN-RM-SLE"]
        rm_sle.actual_qty, rm_sle.stock_value_difference = 10, 10
        # A non-persisted mapper flag cannot override actual accepted warehouse.
        row.return_qty_from_rejected_warehouse = 1
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"]["material"], 90)
        row.warehouse = "REJECT-WH"
        row.rejected_warehouse = ""
        fg_sle.warehouse = "REJECT-WH"
        self.assertEqual(self.module.receipt_batch_candidates("FLOW")[0]["qty"], 90)

    def test_native_scr_columns_exclude_temporary_mapper_flag_without_granting_access(self):
        self.receipt()
        childtype = "Subcontracting Receipt Item"
        self.assertNotIn("return_qty_from_rejected_warehouse", self.permitted[childtype])
        self.assertNotIn("return_qty_from_rejected_warehouse", self.metas[childtype])
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"], {"material": 100, "processing": 200, "other": 0})
        self.assertTrue(any(call.args[0] == childtype for call in self.frappe.get_list.call_args_list))
        for call in self.frappe.get_list.call_args_list:
            if call.args[0] == childtype:
                self.assertNotIn("return_qty_from_rejected_warehouse", call.kwargs["fields"])
        self.hidden[childtype] = {"rm_supp_cost"}
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")

    def test_shared_accepted_rejected_warehouse_does_not_guess_return_split(self):
        _doc, original = self.receipt(qty=70, rejected=10, loss=20)
        original.rejected_warehouse = "FG-WH"
        self.documents[self.module.SLE, "SCR-RJ-SLE"].warehouse = "FG-WH"
        _doc, row = self.receipt("RETURN", qty=-10, loss=0, is_return=1, original="SCR", flow=None)
        row.rejected_warehouse = ""
        # Even if a temporary caller attribute claims the rejected branch, the
        # persisted identical warehouses cannot establish its historical split.
        row.return_qty_from_rejected_warehouse = 1
        with self.assertRaisesRegex(ValueError, "同一仓库"):
            self.module.receipt_costs("FLOW")

    def test_issue_uses_native_stock_quantity_custom_type_and_all_adjustments(self):
        self.issue(stock_entry_type="Custom Material Issue")
        self.ledger("VALUE-ADJUSTMENT", self.module.SE, "ISSUE", "ISSUE-RM", "RM", "RM-WH", 0, -5, adjustment=1)
        result = self.module.material_issue_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 85)
        self.assertEqual(sum(row["qty"] for row in result["rows"]), 20)
        self.assertEqual({row["item_code"] for row in result["rows"]}, {"RM"})

    def test_issue_wrong_purpose_item_quantity_and_missing_ledger_reject(self):
        doc, row = self.issue()
        for target, field, value in ((doc, "purpose", "Material Transfer"), (row, "transfer_qty", 19),
                                     (row, "t_warehouse", "FG-WH"), (row, "item_code", "FG"),
                                     (self.documents[self.module.SLE, "ISSUE-SLE"], "actual_qty", -19),
                                     (self.documents[self.module.SLE, "ISSUE-SLE"], "stock_value_difference", None)):
            old = target[field]
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.material_issue_costs("FLOW")
            target[field] = old
        self.documents.pop((self.module.SLE, "ISSUE-SLE"))
        with self.assertRaises(ValueError):
            self.module.material_issue_costs("FLOW")

    def test_explicit_issue_header_link_cannot_hide_missing_or_conflicting_child_flow(self):
        doc, row = self.issue(flow=None, jf_material_issue_flow="FLOW")
        with self.assertRaises(ValueError):
            self.module.material_issue_costs("FLOW")
        row.jf_flow_order = "FLOW"
        self.assertEqual(self.module.material_issue_costs("FLOW")["totals"]["material"], 80)
        doc.jf_material_issue_flow = "OTHER-FLOW"
        with self.assertRaises(ValueError):
            self.module.material_issue_costs("FLOW")

    def test_native_precision_tolerance_not_fixed_six_decimal(self):
        self.issue(qty=1, factor=1)
        self.precisions[self.module.SLE, "actual_qty"] = 3
        self.documents[self.module.SLE, "ISSUE-SLE"].actual_qty = Decimal("-1.0004")
        self.assertEqual(self.module.material_issue_costs("FLOW")["rows"][0]["qty"], Decimal("1.0004"))
        self.documents[self.module.SLE, "ISSUE-SLE"].actual_qty = Decimal("-1.0006")
        with self.assertRaises(ValueError):
            self.module.material_issue_costs("FLOW")

    def test_posted_sources_use_one_session_and_later_receipt_cannot_invalidate_earlier_issue(self):
        self.issue()
        self.receipt()
        called = False
        def revoke_earlier(name):
            nonlocal called
            if name == "SCR" and not called:
                called = True
                self.denied.add((self.module.SE, "ISSUE", "read"))
        self.doc_hooks[self.module.SCR] = revoke_earlier
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")

    def test_later_list_hook_or_unchanged_modified_source_edit_rejects(self):
        self.receipt()
        count = 0
        def edit_after_projection(filters):
            nonlocal count
            count += 1
            if count == 2:
                self.documents["Subcontracting Receipt Supplied Item", "SCR-RM"].amount = 101
        self.list_hooks["Company"] = edit_after_projection
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")

    def test_permission_metadata_or_late_mask_change_rejects(self):
        self.receipt()
        called = False
        def mask_during_reload(name):
            nonlocal called
            if name == "SCR" and called:
                self.metas["Subcontracting Receipt Supplied Item"]["amount"].mask = 1
            called = True
        self.doc_hooks[self.module.SCR] = mask_during_reload
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")
        self.doc_hooks.clear()
        self.metas["Subcontracting Receipt Supplied Item"]["amount"].mask = 0
        count = 0
        def permission_change(filters):
            nonlocal count
            count += 1
            if count == 2:
                self.add("User Permission", "NEW", user=self.frappe.session.user, allow="Company", for_value="OTHER")
        self.list_hooks["Company"] = permission_change
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")

    def test_queued_failed_inprogress_transaction_repost_with_blank_item_columns_blocks(self):
        self.issue()
        job = self.add(self.module.REPOST, "JOB", company="JF", based_on="Transaction", item_code=None,
            warehouse=None, voucher_type="Stock Entry", voucher_no="OTHER", items_to_be_repost='[{"item_code":"RM","warehouse":"RM-WH","posting_date":"2026-10-01"}]')
        for state in ("Queued", "In Progress", "Failed"):
            job.status = state
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "重过账"):
                self.module.material_issue_costs("FLOW")
        job.status = "Completed"
        self.assertEqual(self.module.material_issue_costs("FLOW")["totals"]["material"], 80)
        job.status = "Queued"
        job.items_to_be_repost = None
        self.ledger("OTHER-SEED", self.module.SE, "OTHER", "OTHER-ROW", "RM", "RM-WH", -1, -1)
        with self.assertRaisesRegex(ValueError, "重过账"):
            self.module.material_issue_costs("FLOW")

    def test_later_repost_creation_cannot_leave_complete_cost_stale(self):
        self.issue()
        count = 0
        def new_job(filters):
            nonlocal count
            count += 1
            if count == 2:
                self.add(self.module.REPOST, "JOB", company="JF", based_on="Item and Warehouse", status="Queued",
                    item_code="RM", warehouse="RM-WH", posting_date=date(2026, 10, 1))
        self.raw_hooks[self.module.REPOST] = new_job
        with self.assertRaisesRegex(ValueError, "重过账"):
            self.module.material_issue_costs("FLOW")

    def test_more_than_one_thousand_sources_have_no_silent_truncation(self):
        # Individual child ledger values are all part of the aggregate, including
        # the last record beyond former get_all defaults / 1000-record limits.
        self.issue(qty=1001, factor=1)
        self.documents.pop((self.module.SLE, "ISSUE-SLE"))
        for i in range(1001):
            self.ledger(f"SLE-{i:04}", self.module.SE, "ISSUE", "ISSUE-RM", "RM", "RM-WH", -1, -2)
        result = self.module.material_issue_costs("FLOW")
        self.assertEqual((len(result["rows"]), result["totals"]["material"]), (1001, 2002))

    def test_unlinked_sources_are_not_invented_and_empty_scope_is_valid(self):
        self.receipt(flow="OTHER-FLOW")
        self.issue(flow="OTHER-FLOW")
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["issues"], result["receipts"], result["totals"]["material"]), ([], [], 0))
        self.assertEqual(self.module.receipt_batch_candidates("FLOW"), [])


if __name__ == "__main__":
    unittest.main()
