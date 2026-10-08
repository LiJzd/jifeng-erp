"""Candidate source models only; no Frappe/native/SQL/GUI execution."""

import importlib
import pathlib
import sys
import types
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).parents[1]


class Row(dict):
    __getattr__ = dict.get

    def __getattribute__(self, name):
        if name == "items" and "items" in self:
            return self["items"]
        return super().__getattribute__(name)


class Doc(Row):
    def __init__(self, env, **values):
        super().__init__(values)
        object.__setattr__(self, "env", env)

    def __deepcopy__(self, memo):
        return Doc(self.env, **{key: deepcopy(value, memo) for key, value in self.items_dict()})

    def items_dict(self):
        return dict.items(self)

    def as_dict(self):
        return dict(self)

    def has_permission(self, permission):
        key = self.doctype, self.name, permission
        self.env.permission_calls[key] = self.env.permission_calls.get(key, 0) + 1
        if self.env.permission_hook:
            self.env.permission_hook(self, permission, self.env.permission_calls[key])
        return key not in self.env.denied

    def check_permission(self, permission):
        if not self.has_permission(permission):
            raise PermissionError("native row gate")

    def precision(self, field):
        return 6


class Meta:
    def __init__(self, env, name, fields, tables=None):
        self.env, self.name = env, name
        self.fields = {field: Row(fieldname=field, fieldtype="Data", permlevel=0, mask=0) for field in fields}
        for field, childtype in (tables or {}).items():
            self.fields[field] = Row(fieldname=field, fieldtype="Table", options=childtype, permlevel=0, mask=0)

    def get_field(self, field):
        return self.fields.get(field)

    def get_valid_columns(self):
        return [field for field, value in self.fields.items() if value.fieldtype != "Table"]

    def get_permlevel_access(self, permission_type="read", parenttype=None, *, user=None):
        self.env.level_calls.append((self.name, permission_type, parenttype, user))
        return self.env.mask_levels.get((self.name, parenttype), set()) if permission_type == "mask" else {0}


class Environment:
    def __init__(self):
        self.data, self.meta = {}, {}
        self.denied, self.hidden, self.unreadable = set(), set(), set()
        self.mask_levels, self.mask_calls, self.field_calls, self.level_calls = {}, [], [], []
        self.permission_calls, self.permission_hook, self.mask_hook = {}, None, None
        self.auth = {"User": [Row(name="ordinary", enabled=1)], "Has Role": [Row(name="ROLE", parent="ordinary", parenttype="User", role="Purchase User")],
                     "User Permission": [], "DocPerm": [], "Custom DocPerm": [], "DocShare": [],
                     "DocType": [], "Custom Field": [], "Property Setter": [], "Report": [], "Custom Role": []}
        self.frappe = types.ModuleType("frappe")
        self.frappe._dict, self.frappe.PermissionError = Row, PermissionError
        self.frappe.ValidationError, self.frappe.DoesNotExistError = ValueError, LookupError
        self.frappe.session = types.SimpleNamespace(user="ordinary")
        self.frappe.throw = lambda message, error=ValueError: (_ for _ in ()).throw(error(message))
        self.frappe.get_meta = lambda doctype: self.meta[doctype]
        self.frappe.get_doc = lambda doctype, name: deepcopy(self.data[doctype, name])
        self.frappe.get_list = lambda doctype, **kwargs: self.query(doctype, True, **kwargs)
        self.frappe.get_all = lambda doctype, **kwargs: self.query(doctype, False, **kwargs)
        self.frappe.has_permission = lambda doctype, permission: (doctype, "*", permission) not in self.denied
        self.frappe.share = types.SimpleNamespace(get_shared=lambda *args, **kwargs: [])

    def permitted(self, doctype, parenttype=None, user=None, permission_type=None, *, ignore_virtual=False):
        self.field_calls.append((doctype, parenttype, user, permission_type, ignore_virtual))
        assert permission_type in ("read", "select") and user == "ordinary" and ignore_virtual
        if permission_type == "select":
            return {"name", "modified", "docstatus"}
        return set(self.meta[doctype].get_valid_columns()) - {field for dt, field in self.unreadable if dt == doctype}

    def mask(self, doctype, fields, result, as_dict=True, pluck=False, parent_doctype=None):
        self.mask_calls.append((doctype, tuple(fields), parent_doctype))
        if self.mask_hook:
            self.mask_hook(doctype, result, parent_doctype)
        return result  # Deliberately stale native mask cache: explicit current Meta must still deny.

    @staticmethod
    def match(value, rule):
        if isinstance(rule, (list, tuple)):
            operator, expected = rule
            if operator == "=": return value == expected
            if operator == "in": return value in expected
            if operator == "not in": return value not in expected
            if operator == "between": return value is not None and expected[0] <= str(value) <= expected[1]
            if operator == "is": return bool(value) == (expected == "set")
        return value == rule

    def records(self, doctype):
        if doctype == "DocField":
            return [Row(name=dt + ":" + field, parent=dt, **dict(value)) for dt, meta in self.meta.items() for field, value in meta.fields.items()]
        if doctype in self.auth: return self.auth[doctype]
        if doctype in self.meta and any(value.fieldtype == "Table" and value.options == doctype for meta in self.meta.values() for value in meta.fields.values()):
            return [row for doc in self.data.values() for field, value in self.meta[doc.doctype].fields.items()
                    if value.fieldtype == "Table" and value.options == doctype for row in doc.get(field) or []]
        return [doc for (dt, _name), doc in self.data.items() if dt == doctype]

    def query(self, doctype, visible, filters=None, or_filters=None, pluck=None, fields=None,
              distinct=False, limit_page_length=0, **kwargs):
        def fits(row):
            if isinstance(filters, list):
                for dt, field, operator, value in filters:
                    candidates = [row] if dt == doctype else [child for table, meta in self.meta[doctype].fields.items()
                                    if meta.fieldtype == "Table" and meta.options == dt for child in row.get(table) or []]
                    if not any(self.match(candidate.get(field), [operator, value]) for candidate in candidates): return False
            elif not all(self.match(row.get(field), rule) for field, rule in (filters or {}).items()): return False
            return not or_filters or any(self.match(row.get(field), rule) for field, rule in or_filters.items())
        result = [row for row in self.records(doctype) if fits(row) and (not visible or (doctype, row.get("name")) not in self.hidden)]
        if pluck: result = [row.get(pluck) for row in result]
        elif fields != ["*"] and fields: result = [Row({field: row.get(field) for field in fields}) for row in result]
        else: result = [Row(row) for row in result]
        if distinct: result = list(dict.fromkeys(result)) if pluck else result
        return deepcopy(result[:limit_page_length] if limit_page_length else result)

    def schemas(self, guard):
        for kind in ("purchase_request", "purchase_receipt", "subcontracting_progress", "subcontracting_receipt", "subcontracting_material"):
            for dt, value in guard.dependencies(kind).items():
                meta = self.meta.setdefault(dt, Meta(self, dt, set()))
                for field in value["fields"] | value["optional"]:
                    meta.fields.setdefault(field, Row(fieldname=field, fieldtype="Data", permlevel=0, mask=0))
                for field, (child, required, optional) in value["tables"].items():
                    meta.fields[field] = Row(fieldname=field, fieldtype="Table", options=child, permlevel=0, mask=0)
                    child_meta = self.meta.setdefault(child, Meta(self, child, set()))
                    for name in required | optional | {"docstatus", "modified"}:
                        child_meta.fields.setdefault(name, Row(fieldname=name, fieldtype="Data", permlevel=0, mask=0))
        self.meta["Item"].fields["uoms"] = Row(fieldname="uoms", fieldtype="Table", options="UOM Conversion Detail", permlevel=0, mask=0)
        self.meta["UOM Conversion Detail"] = Meta(self, "UOM Conversion Detail", {"name", "parent", "parenttype", "parentfield", "idx", "uom"})
        for field in ("lft", "rgt"):
            self.meta["Warehouse"].fields[field] = Row(fieldname=field, fieldtype="Int", permlevel=0, mask=0)

    def doc(self, doctype, name, **values):
        data = {field: [] if meta.fieldtype == "Table" else None for field, meta in self.meta[doctype].fields.items()}
        data.update(doctype=doctype, name=name, docstatus=1, modified="2026-10-06 00:00:00")
        data.update(values)
        document = Doc(self, **data)
        for table, meta in self.meta[doctype].fields.items():
            if meta.fieldtype == "Table":
                for idx, row in enumerate(document.get(table) or [], 1):
                    row.update(doctype=meta.options, parent=name, parenttype=doctype, parentfield=table, idx=idx, docstatus=1)
        self.data[doctype, name] = document
        return document

    def row(self, doctype, name, **values):
        return Doc(self, **{**{field: None for field in self.meta[doctype].fields}, "doctype":doctype, "name":name, **values})


class CandidateModels(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        model = types.ModuleType("frappe.model"); model.get_permitted_fields = self.env.permitted
        query = types.ModuleType("frappe.query_builder.utils"); query.mask_fields = self.env.mask
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.getdate = lambda value: value
        utils.today = lambda: "2026-10-06"
        utils.date_diff = lambda *args: 0
        refs = types.ModuleType("jifeng_erp.sample_source_queries")
        self.reference_calls = []
        refs.check_protected_reference = lambda *args, **kwargs: self.reference_calls.append((args, kwargs))
        package = types.ModuleType("jifeng_erp"); package.__path__ = [str(ROOT / "jifeng_erp")]
        self.patch = patch.dict(sys.modules, {"frappe": self.env.frappe, "frappe.model":model,
                           "frappe.query_builder.utils":query, "frappe.utils":utils,
                           "jifeng_erp":package, "jifeng_erp.sample_source_queries":refs})
        self.patch.start(); self.addCleanup(self.patch.stop)
        for name in ("ledger_source_guard", "inventory_scope", "procurement_reports", "subcontracting", "subcontracting_material"):
            sys.modules.pop("jifeng_erp." + name, None)
        self.guard = importlib.import_module("jifeng_erp.ledger_source_guard")
        self.env.schemas(self.guard)
        self.env.meta["Company"].fields["default_currency"] = Row(fieldname="default_currency", fieldtype="Link", permlevel=0, mask=0)
        self.proc = importlib.import_module("jifeng_erp.procurement_reports")
        self.sub = importlib.import_module("jifeng_erp.subcontracting")
        self.material = importlib.import_module("jifeng_erp.subcontracting_material")
        e = self.env
        e.doc("Company", "CO", default_currency="USD")
        e.doc("Item", "RAW", stock_uom="Kg", is_stock_item=1)
        e.doc("Item", "FG", stock_uom="Nos", is_stock_item=1)
        e.doc("Warehouse", "MAIN", company="CO", is_group=0)
        e.doc("Warehouse", "REJECT", company="CO", is_group=0)
        e.doc("Supplier", "SUP")
        self.pr_row = e.row("Purchase Receipt Item", "PRI", item_code="RAW", item_name="raw", stock_uom="Kg",
                           qty=2, stock_qty=6, conversion_factor=3, rejected_qty=1, warehouse="MAIN", rejected_warehouse="REJECT",
                           amount=Decimal("12.500000000000000001"), base_amount=Decimal("25.000000000000000002"),
                           net_amount=Decimal("12"), base_net_amount=Decimal("24"), return_qty_from_rejected_warehouse=0)
        e.doc("Purchase Receipt", "PR", company="CO", supplier="SUP", supplier_name="supplier", currency="EUR", conversion_rate=2,
              is_return=0, posting_date="2026-10-06", posting_time="10:00:00", items=[self.pr_row])
        self.sco_row = e.row("Subcontracting Order Item", "SCOI", item_code="FG", item_name="finished", stock_uom="Nos", qty=10, conversion_factor=1)
        supplied = e.row("Subcontracting Order Supplied Item", "SUPP", reference_name="SCOI", main_item_code="FG", rm_item_code="RAW", stock_uom="Kg")
        e.doc("Subcontracting Order", "SCO", company="CO", supplier="SUP", supplier_name="supplier", transaction_date="2026-10-06", status="Open", items=[self.sco_row], supplied_items=[supplied])
        self.scr_row = e.row("Subcontracting Receipt Item", "SCRI", item_code="FG", item_name="finished", stock_uom="Nos", qty=3, rejected_qty=1, process_loss_qty=1,
                            conversion_factor=2, warehouse="MAIN", rejected_warehouse="REJECT", subcontracting_order="SCO", subcontracting_order_item="SCOI")
        e.doc("Subcontracting Receipt", "SCR", company="CO", supplier="SUP", supplier_name="supplier", posting_date="2026-10-06", posting_time="10:00:00", is_return=0, items=[self.scr_row])
        e.doc("Stock Ledger Entry", "SLE-SCR-MAIN", company="CO", posting_date="2026-10-06", posting_time="10:00:00",
              voucher_type="Subcontracting Receipt", voucher_no="SCR", voucher_detail_no="SCRI", item_code="FG",
              warehouse="MAIN", stock_uom="Nos", actual_qty=6, stock_value_difference=Decimal("15"),
              is_cancelled=0, is_adjustment_entry=0)
        e.doc("Stock Ledger Entry", "SLE-SCR-REJECT", company="CO", posting_date="2026-10-06", posting_time="10:00:00",
              voucher_type="Subcontracting Receipt", voucher_no="SCR", voucher_detail_no="SCRI", item_code="FG",
              warehouse="REJECT", stock_uom="Nos", actual_qty=2, stock_value_difference=Decimal("5"),
              is_cancelled=0, is_adjustment_entry=0)
        se_row = e.row("Stock Entry Detail", "SEI", item_code="RAW", item_name="raw", stock_uom="Kg", transfer_qty=Decimal("4.125"), sco_rm_detail="SUPP", subcontracted_item="FG", s_warehouse="MAIN", t_warehouse="SUPWH")
        e.doc("Stock Entry", "SE", company="CO", posting_date="2026-10-06", posting_time="10:00:00", purpose="Send to Subcontractor", is_return=0, subcontracting_order="SCO", items=[se_row])
        self.filters = {"company":"CO", "from_date":"2026-10-01", "to_date":"2026-10-06"}

    def receipt(self): return self.proc.purchase_receipt_ledger(self.filters)[1]

    def test_positive_money_stock_conversion_and_native_child_callsite(self):
        result = self.receipt()[0]
        self.assertEqual(result["amount"], Decimal("12.500000000000000001"))
        self.assertEqual((result["accepted_qty"], result["rejected_qty"]), (6, 3))
        self.assertEqual((result["currency"], result["company_currency"]), ("EUR", "USD"))
        self.assertTrue(any(dt=="Purchase Receipt Item" and parent=="Purchase Receipt" and permission=="read" for dt,parent,_user,permission,_virtual in self.env.field_calls))
        self.assertTrue(any(dt=="Purchase Receipt Item" and parent=="Purchase Receipt" for dt,_fields,parent in self.env.mask_calls))

    def test_company_select_without_read_is_preserved(self):
        self.env.denied.add(("Company","CO","read"))
        self.assertEqual(len(self.receipt()),1)
        self.assertNotIn(("Company","CO","read"), self.env.permission_calls)

    def test_company_currency_field_without_read_does_not_borrow_company_select(self):
        self.env.unreadable.add(("Company","default_currency"))
        with self.assertRaises(PermissionError): self.receipt()
        self.assertNotIn(("Company","CO","read"), self.env.permission_calls)
        self.assertEqual(len(self.sub.subcontracting_progress(self.filters)[1]), 1)

    def test_company_currency_native_mask_rejects(self):
        self.env.meta["Company"].fields["default_currency"]["mask"] = 1
        with self.assertRaises(PermissionError): self.receipt()

    def test_missing_real_child_field_rejects(self):
        del self.env.meta["Purchase Receipt Item"].fields["amount"]
        with self.assertRaises(PermissionError): self.receipt()

    def test_real_child_amount_read_layer_rejects(self):
        self.env.unreadable.add(("Purchase Receipt Item","amount"))
        with self.assertRaises(PermissionError): self.receipt()

    def test_child_mask_rejects_even_with_stale_native_mask_cache(self):
        self.env.meta["Purchase Receipt Item"].fields["amount"]["mask"] = 1
        with self.assertRaises(PermissionError): self.receipt()

    def test_derived_quantity_inputs_each_require_read_mask(self):
        for field in ("qty","stock_qty","conversion_factor","rejected_qty","return_qty_from_rejected_warehouse"):
            with self.subTest(field=field):
                self.env.meta["Purchase Receipt Item"].fields[field]["mask"] = 1
                with self.assertRaises(PermissionError): self.receipt()
                self.env.meta["Purchase Receipt Item"].fields[field]["mask"] = 0

    def test_parent_table_mask_and_read_level_reject(self):
        field = self.env.meta["Purchase Receipt"].fields["items"]
        field["mask"] = 1
        with self.assertRaises(PermissionError): self.receipt()
        field.update(mask=0,permlevel=1)
        with self.assertRaises(PermissionError): self.receipt()

    def test_mask_permission_at_correct_parent_allows(self):
        self.env.meta["Purchase Receipt Item"].fields["amount"]["mask"] = 1
        self.env.mask_levels["Purchase Receipt Item","Purchase Receipt"] = {0}
        self.assertEqual(len(self.receipt()),1)

    def test_unconsumed_item_uoms_mask_does_not_narrow_plain_receipt(self):
        self.env.meta["Item"].fields["uoms"]["mask"] = 1
        self.assertEqual(len(self.receipt()),1)

    def add_flow(self):
        material = self.env.row("Jifeng Material Line", "FM", item_code="RAW", uom="Box")
        self.env.doc("Jifeng Flow Order", "FLOW", company="CO", item_code="FG", materials=[material])
        uom = self.env.row("UOM Conversion Detail", "UM", uom="Box")
        item = self.env.data["Item", "RAW"]
        item["uoms"] = [uom]
        uom.update(parent="RAW", parenttype="Item", parentfield="uoms", idx=1, docstatus=0)
        self.env.data["Purchase Receipt","PR"].items[0]["jf_flow_order"] = "FLOW"

    def test_consumed_flow_material_and_item_uom_dependency_mask_rejects(self):
        self.add_flow()
        self.assertEqual(self.receipt()[0]["flow_order"], "FLOW")
        for doctype, field in (("Jifeng Material Line","uom"),("UOM Conversion Detail","uom"),("Item","uoms")):
            with self.subTest(doctype=doctype, field=field):
                self.env.meta[doctype].fields[field]["mask"] = 1
                with self.assertRaises(PermissionError): self.receipt()
                self.env.meta[doctype].fields[field]["mask"] = 0

    def test_request_exact_coverage_links_and_pending_math_unchanged(self):
        e = self.env
        request = e.row("Material Request Item", "MRI", item_code="RAW", item_name="raw", stock_uom="Kg", qty=5,
                        stock_qty=15, conversion_factor=3, warehouse="MAIN", schedule_date="2026-10-10")
        e.doc("Material Request", "MR", company="CO", material_request_type="Purchase", transaction_date="2026-10-06", status="Partially Ordered", items=[request])
        order = e.row("Purchase Order Item", "POI", item_code="RAW", stock_uom="Kg", qty=3, stock_qty=9,
                      conversion_factor=3, material_request="MR", material_request_item="MRI")
        e.doc("Purchase Order", "PO", company="CO", supplier="SUP", status="Open", items=[order])
        e.data["Purchase Receipt","PR"].items[0].update(material_request="MR", material_request_item="MRI", purchase_order="PO", purchase_order_item="POI")
        result = self.proc.purchase_request_ledger(self.filters)[1][0]
        self.assertEqual((result["request_item"], result["requested_qty"], result["ordered_qty"], result["received_qty"], result["pending_qty"]),
                         ("MRI", 15, 9, 6, 6))
        e.meta["Purchase Order Item"].fields["material_request_item"]["mask"] = 1
        with self.assertRaises(PermissionError): self.proc.purchase_request_ledger(self.filters)

    def test_purchase_return_preserves_signed_amounts_and_exact_original_child(self):
        row = deepcopy(self.env.data["Purchase Receipt","PR"].items[0])
        row.update(name="RET-I", qty=-1, stock_qty=-3, rejected_qty=0, purchase_receipt_item="PRI",
                   amount=Decimal("-6.25"), base_amount=Decimal("-12.5"), net_amount=Decimal("-6"), base_net_amount=Decimal("-12"))
        self.env.doc("Purchase Receipt","RET", company="CO", supplier="SUP", supplier_name="supplier", currency="EUR", conversion_rate=2,
                     is_return=1, return_against="PR", posting_date="2026-10-06", items=[row])
        result = next(row for row in self.receipt() if row["purchase_receipt"] == "RET")
        self.assertEqual((result["accepted_qty"], result["amount"], result["source_receipt"], result["source_receipt_item"]),
                         (-3, Decimal("-6.25"), "PR", "PRI"))
        self.env.meta["Purchase Receipt Item"].fields["purchase_receipt_item"]["mask"] = 1
        with self.assertRaises(PermissionError): self.receipt()

    def test_subcontract_return_closure_and_loss_not_refunded(self):
        row = deepcopy(self.env.data["Subcontracting Receipt","SCR"].items[0])
        row.update(name="SRET-I", qty=-1, rejected_qty=0, process_loss_qty=0, subcontracting_receipt_item="SCRI")
        self.env.doc("Subcontracting Receipt","SRET", company="CO", supplier="SUP", supplier_name="supplier", posting_date="2026-10-06",
                     is_return=1, return_against="SCR", items=[row])
        result = self.sub.subcontracting_progress(self.filters)[1][0]
        self.assertEqual((result["accepted_qty"],result["loss_qty"],result["returned_qty"],result["pending_qty"]),(4,2,2,2))
        self.env.meta["Subcontracting Receipt Item"].fields["subcontracting_receipt_item"]["mask"] = 1
        with self.assertRaises(PermissionError): self.sub.subcontracting_receipt_ledger(self.filters)

    def test_subcontract_progress_and_receipt_keep_quantity_semantics(self):
        progress = self.sub.subcontracting_progress(self.filters)[1][0]
        receipt = self.sub.subcontracting_receipt_ledger(self.filters)[1][0]
        self.assertEqual((progress["accepted_qty"],progress["rejected_qty"],progress["loss_qty"],progress["remaining_qty"]),(6,2,2,0))
        self.assertEqual(receipt["covered_qty"],10)
        self.assertEqual((receipt["inventory_qty_change"], receipt["inventory_value_change"], receipt["currency"],
                          receipt["inventory_value_status"]), (Decimal(8), Decimal(20), "USD", "有效库存账"))

    def test_subcontract_receipt_ledger_reports_exact_signed_return_sle_value(self):
        row = deepcopy(self.env.data["Subcontracting Receipt", "SCR"].items[0])
        row.update(name="SRET-I", qty=-1, rejected_qty=0, process_loss_qty=0, warehouse="MAIN",
                   subcontracting_receipt_item="SCRI")
        self.env.doc("Subcontracting Receipt", "SRET", company="CO", supplier="SUP", supplier_name="supplier",
                     posting_date="2026-10-06", posting_time="11:00:00", is_return=1, return_against="SCR", items=[row])
        self.env.doc("Stock Ledger Entry", "SLE-SRET", company="CO", posting_date="2026-10-06", posting_time="11:00:00",
                     voucher_type="Subcontracting Receipt", voucher_no="SRET", voucher_detail_no="SRET-I", item_code="FG",
                     warehouse="MAIN", stock_uom="Nos", actual_qty=-2, stock_value_difference=Decimal("-5"),
                     is_cancelled=0, is_adjustment_entry=0)

        rows = self.sub.subcontracting_receipt_ledger(self.filters)[1]
        values = {row["receipt"]: (row["inventory_qty_change"], row["inventory_value_change"])
                  for row in rows}
        self.assertEqual(values, {"SCR": (Decimal(8), Decimal(20)), "SRET": (Decimal(-2), Decimal("-5"))})

    def test_subcontract_receipt_ledger_rejects_masked_stock_value(self):
        self.env.meta["Stock Ledger Entry"].fields["stock_value_difference"]["mask"] = 1
        with self.assertRaises(PermissionError):
            self.sub.subcontracting_receipt_ledger(self.filters)

    def test_subcontract_receipt_ledger_rejects_missing_or_mismatched_sle(self):
        del self.env.data["Stock Ledger Entry", "SLE-SCR-REJECT"]
        with self.assertRaisesRegex(ValueError, "缺行"):
            self.sub.subcontracting_receipt_ledger(self.filters)
        self.env.data["Stock Ledger Entry", "SLE-SCR-REJECT"] = self.env.doc(
            "Stock Ledger Entry", "SLE-SCR-REJECT", company="CO", posting_date="2026-10-06", posting_time="10:00:00",
            voucher_type="Subcontracting Receipt", voucher_no="SCR", voucher_detail_no="SCRI", item_code="FG",
            warehouse="REJECT", stock_uom="Nos", actual_qty=1, stock_value_difference=Decimal("5"),
            is_cancelled=0, is_adjustment_entry=0)
        with self.assertRaisesRegex(ValueError, "数量与有效库存账不一致"):
            self.sub.subcontracting_receipt_ledger(self.filters)

    def test_pending_native_revaluation_hides_affected_value(self):
        self.env.meta["Repost Item Valuation"] = Meta(self.env, "Repost Item Valuation", {
            "name", "company", "docstatus", "status", "based_on", "item_code", "warehouse", "posting_date",
            "reposting_data_file", "items_to_be_repost", "voucher_type", "voucher_no"})
        self.env.doc("Repost Item Valuation", "RIV-1", company="CO", docstatus=1, status="Queued",
                     based_on="Item and Warehouse", item_code="FG", warehouse="MAIN", posting_date="2026-10-01")

        row = self.sub.subcontracting_receipt_ledger(self.filters)[1][0]

        self.assertEqual(row["inventory_qty_change"], Decimal(8))
        self.assertIsNone(row["inventory_value_change"])
        self.assertEqual(row["inventory_value_status"], "库存重估未完成")

    def test_empty_inventory_rows_do_not_query_reposting(self):
        guard = Mock()
        self.assertEqual(self.sub._receipt_reposting_rows([], {}, {}, "CO", guard), set())
        self.assertEqual(self.sub._receipt_reposting_rows(
            [{"receipt": "SCR", "receipt_item": "LOSS-ONLY"}], {("SCR", "LOSS-ONLY"): {}}, {}, "CO", guard), set())
        guard.include_doctypes.assert_not_called()
        guard.query.assert_not_called()

    def test_completed_unrelated_revaluation_is_not_loaded_or_blocking(self):
        self.env.meta["Repost Item Valuation"] = Meta(self.env, "Repost Item Valuation", {
            "name", "company", "docstatus", "status", "based_on", "item_code", "warehouse", "posting_date",
            "reposting_data_file", "items_to_be_repost", "voucher_type", "voucher_no"})
        self.env.doc("Repost Item Valuation", "RIV-DONE", company="CO", docstatus=1, status="Completed",
                     based_on="Item and Warehouse", item_code="RAW", warehouse="MAIN", posting_date="2026-10-01")

        row = self.sub.subcontracting_receipt_ledger(self.filters)[1][0]

        self.assertEqual(row["inventory_value_change"], Decimal(20))

    def test_subcontract_child_loss_mask_is_not_hidden_by_derived_column(self):
        self.env.meta["Subcontracting Receipt Item"].fields["process_loss_qty"]["mask"] = 1
        with self.assertRaises(PermissionError): self.sub.subcontracting_progress(self.filters)

    def test_material_exact_source_and_decimal_projection_unchanged(self):
        row = self.material.subcontracting_material_ledger(self.filters)[1][0]
        self.assertEqual((row["order_item"],row["supplied_item"],row["stock_entry_detail"]),("SCOI","SUPP","SEI"))
        self.assertEqual(row["net_qty"],Decimal("4.125"))

    def test_material_supplied_reference_and_transfer_mask_reject(self):
        for doctype,field in (("Subcontracting Order Supplied Item","reference_name"),("Stock Entry Detail","transfer_qty")):
            with self.subTest(field=field):
                self.env.meta[doctype].fields[field]["mask"] = 1
                with self.assertRaises(PermissionError): self.material.subcontracting_material_ledger(self.filters)
                self.env.meta[doctype].fields[field]["mask"] = 0

    def late(self, change):
        def hook(doc, permission, count):
            if doc.doctype == "Purchase Receipt" and count == 2:
                change(doc)
        self.env.permission_hook = hook

    def test_late_raw_role_permission_share_changes_reject_cached_native_allow(self):
        for doctype,row in (("Has Role",Row(name="LATE",parent="ordinary",parenttype="User",role="Stock User")),
                            ("DocPerm",Row(name="LATE",parent="Purchase Receipt",read=0)),
                            ("DocShare",Row(name="LATE",share_doctype="Purchase Receipt",share_name="PR",user="ordinary",read=0))):
            with self.subTest(doctype=doctype):
                self.late(lambda _doc, dt=doctype, value=row: self.env.auth[dt].append(value))
                with self.assertRaises(PermissionError): self.receipt()
                self.env.auth[doctype].clear(); self.env.permission_calls.clear()

    def test_late_persisted_value_without_modified_rejects(self):
        self.late(lambda _doc: self.env.data["Purchase Receipt","PR"].items[0].update(amount=999))
        with self.assertRaises(PermissionError): self.receipt()

    def test_late_in_memory_native_callback_value_rejects(self):
        self.late(lambda doc: doc.items[0].update(amount=999))
        with self.assertRaises(PermissionError): self.receipt()

    def test_late_matching_parent_membership_insert_rejects(self):
        self.late(lambda _doc: self.env.doc("Purchase Receipt","NEW",**{key:deepcopy(value) for key,value in self.env.data["Purchase Receipt","PR"].items_dict() if key not in ("doctype","name")}))
        with self.assertRaises(PermissionError): self.receipt()

    def test_late_child_membership_without_modified_rejects(self):
        self.late(lambda _doc: self.env.data["Purchase Receipt","PR"].items.append(deepcopy(self.pr_row)))
        with self.assertRaises(PermissionError): self.receipt()

    def test_late_native_document_read_revocation_rejects(self):
        self.late(lambda _doc: self.env.denied.add(("Item","RAW","read")))
        with self.assertRaises(PermissionError): self.receipt()

    def test_late_mask_user_permission_or_actor_change_rejects(self):
        self.late(lambda _doc: self.env.meta["Purchase Receipt Item"].fields["amount"].update(mask=1))
        with self.assertRaises(PermissionError): self.receipt()
        self.env.meta["Purchase Receipt Item"].fields["amount"]["mask"] = 0
        self.env.permission_calls.clear()
        self.late(lambda _doc: self.env.auth["User Permission"].append(Row(name="UP", user="ordinary", allow="Company", for_value="OTHER")))
        with self.assertRaises(PermissionError): self.receipt()
        self.env.auth["User Permission"].clear(); self.env.permission_calls.clear()
        self.late(lambda _doc: setattr(self.env.frappe.session, "user", "another"))
        with self.assertRaises(PermissionError): self.receipt()

    def test_late_report_custom_role_child_change_without_parent_modified_rejects(self):
        self.env.auth["Custom Role"].append(Row(name="CUSTOM", report="Jifeng Purchase Receipt Ledger"))
        self.env.auth["Has Role"].append(Row(name="CR", parent="CUSTOM", parenttype="Custom Role", role="Purchase User"))
        self.late(lambda _doc: self.env.auth["Has Role"][-1].update(role="Accounts Manager"))
        with self.assertRaises(PermissionError): self.receipt()

    def test_final_native_mask_callback_cannot_mutate_earlier_source_after_checks(self):
        guard = self.guard.LedgerSourceGuard("purchase_receipt")
        guard.document(self.env.frappe.get_doc("Purchase Receipt", "PR"))
        guard.document(self.env.frappe.get_doc("Item", "RAW"))
        calls = []
        def callback(doctype, _result, _parenttype):
            if doctype == "Item":
                calls.append(doctype)
                if len(calls) == 2:
                    self.env.data["Purchase Receipt", "PR"].items[0]["amount"] = 999
        self.env.mask_hook = callback
        with self.assertRaises(PermissionError): guard.finish()

    def test_empty_parent_query_late_membership_cannot_become_empty_report(self):
        self.env.data.pop(("Purchase Receipt", "PR"))
        def hook(doc, permission, count):
            if doc.doctype == "Company" and count == 2:
                self.env.doc("Purchase Receipt", "NEW", company="CO", supplier="SUP", currency="EUR", conversion_rate=2,
                             posting_date="2026-10-06", is_return=0, items=[])
        self.env.permission_hook = hook
        with self.assertRaises(PermissionError): self.receipt()

    def test_other_inventory_scope_callers_without_guard_are_unchanged(self):
        self.env.meta["Purchase Receipt Item"].fields["amount"]["mask"] = 1
        scope = importlib.import_module("jifeng_erp.inventory_scope")
        self.assertEqual(set(scope.readable_documents("Purchase Receipt", {"name":"PR"})),{"PR"})


if __name__ == "__main__":
    unittest.main()
