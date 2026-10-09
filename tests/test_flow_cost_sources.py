"""Adversarial source closure models; no database or native permission changes."""

from copy import deepcopy
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[1]

# Installed 16.36 StockEntry.py matches the locally pinned file after newline
# normalization. Parent loss belongs to the single FG code, including split rows.
_NATIVE_MANUFACTURE_COVERAGE = '''
def validate_fg_completed_qty(self):
    if self.purpose != "Manufacture" or not self.from_bom:
        return
    fg_qty = defaultdict(float)
    for d in self.items:
        if d.is_finished_item:
            fg_qty[d.item_code] += flt(d.qty)
    if not fg_qty:
        return
    precision = frappe.get_precision("Stock Entry Detail", "qty")
    fg_item = next(iter(fg_qty.keys()))
    fg_item_qty = flt(fg_qty[fg_item], precision)
    fg_completed_qty = flt(self.fg_completed_qty, precision)
    for d in self.items:
        if not fg_qty.get(d.item_code):
            continue
        if (fg_completed_qty - fg_item_qty) > 0:
            self.process_loss_qty = fg_completed_qty - fg_item_qty
        if not self.process_loss_qty:
            continue
        if fg_completed_qty != (flt(fg_item_qty, precision) + flt(self.process_loss_qty, precision)):
            frappe.throw(_(
                "Since there is a process loss of {0} units for the finished good {1}, you should reduce the quantity by {0} units for the finished good {1} in the Items Table."
            ).format(frappe.bold(self.process_loss_qty), frappe.bold(d.item_code)))
'''


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Document(Record):
    environment = None

    @property
    def items(self):
        return self.get("items", [])

    def as_dict(self):
        return dict(self)

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
    def test_stage_input_accepts_native_child_documents_without_dict_subscription(self):
        class NativeChild:
            name = "EARLIER-OP"
            def get(self, field):
                return {"finished_good": "SEMI", "sequence_id": 1}.get(field)
        session = self.module._Session.__new__(self.module._Session)
        earlier = NativeChild()
        stage = {"work_order": Record(name="WO", operations=[earlier]),
                 "job_card": Record(sequence_id=2),
                 "bom": Record(items=[Record(item_code="SEMI")])}
        self.assertEqual(session._stage_input(stage, "SEMI"), ("WO", "EARLIER-OP", "SEMI"))
        stage["job_card"].sequence_id = 1
        with self.assertRaisesRegex(ValueError, "前序阶段"):
            session._stage_input(stage, "SEMI")

    def setUp(self):
        self.documents, self.permitted, self.metas = {}, {}, {}
        self.hidden, self.omitted, self.mask_levels, self.read_levels = {}, {}, {}, {}
        self.denied, self.list_denied, self.role_denials = set(), set(), set()
        self.doc_hooks, self.list_hooks, self.raw_hooks, self.precisions = {}, {}, {}, {}
        self.marked_receipts = set()
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = Record(user="native-stock-reader")
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
        self.frappe.has_permission = lambda *args: True
        self.frappe.get_cached_value = lambda *args: "CNY"
        self.frappe.new_doc, self.frappe.delete_doc = Mock(), Mock()
        model = types.ModuleType("frappe.model")
        model.get_permitted_fields = lambda doctype, **kw: self.permitted[doctype] - self.hidden.get(doctype, set())
        utils = types.ModuleType("frappe.utils")
        utils.cint = lambda value: int(value or 0)
        utils.get_datetime = datetime.fromisoformat
        utils.flt = lambda value, precision=None: round(float(value), precision) if precision is not None else float(value)
        # Payroll source closure is tested in its own native-shape harness.
        # These inventory cases receive no accrued wages unless stated below.
        wage_sources = types.ModuleType("jifeng_erp.flow_wage_cost_sources")
        self.wage_cost_result = {"rows": [], "total": Decimal(0), "currency": "CNY", "finish_guard": Mock()}
        wage_sources.posted_flow_wage_costs = Mock(side_effect=lambda flow: self.wage_cost_result)
        # Engine hook ordering/population is covered by the adapter harness;
        # these source-closure cases retain their independent native read model.
        scoped_query = types.ModuleType("jifeng_erp.flow_cost_scoped_query")
        scoped_query.record_list = lambda doctype, *, filters, fields, parenttype=None, **options: self.frappe.get_list(
            doctype, filters=filters, fields=fields, order_by="name asc", limit_page_length=0,
            **({"parent_doctype": parenttype} if parenttype else {}), **options)
        patches = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.model": model, "frappe.utils": utils,
                                          "jifeng_erp.flow_wage_cost_sources": wage_sources,
                                          "jifeng_erp.flow_cost_scoped_query": scoped_query})
        patches.start()
        self.addCleanup(patches.stop)
        returns_spec = importlib.util.spec_from_file_location("jifeng_erp.subcontracting_returns", ROOT / "jifeng_erp/subcontracting_returns.py")
        returns_module = importlib.util.module_from_spec(returns_spec)
        sys.modules[returns_spec.name] = returns_module
        returns_spec.loader.exec_module(returns_module)
        spec = importlib.util.spec_from_file_location("flow_cost_sources_under_test", ROOT / "jifeng_erp/flow_cost_sources.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        money = {"rate", "amount", "base_amount", "rm_supp_cost", "stock_value_difference",
                 "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty",
                 "total_additional_costs", "additional_cost", "landed_cost_voucher_amount"}
        for doctype, fields in self.module.PARENTS.items():
            self.schema(doctype, fields, money)
        self.schema("Company", {"name", "modified", "default_currency"}, money)
        for doctype, tables in self.module.TABLES.items():
            for fieldname, childtype, fields in tables:
                self.schema(childtype, fields, money)
                self.metas[doctype][fieldname] = Record(fieldname=fieldname, fieldtype="Table", permlevel=0, options=childtype)
                # Installed Frappe excludes Table from get_permitted_fields.
        manufacture_fields = self.module.MANUFACTURE_FIELDS | self.module.MANUFACTURE_QUANTITY_FIELDS
        self.permitted[self.module.SE].update(manufacture_fields)
        self.metas[self.module.SE].update({field: Record(fieldname=field, fieldtype="Currency" if field in money else "Link", permlevel=0)
                                          for field in manufacture_fields})
        for field in self.module.MANUFACTURE_QUANTITY_FIELDS:
            self.metas[self.module.SE][field].fieldtype = "Check" if field == "from_bom" else "Float"
        childtype = "Stock Entry Detail"
        self.permitted[childtype].update(self.module.MANUFACTURE_ITEM_FIELDS)
        self.metas[childtype].update({field: Record(fieldname=field, fieldtype="Currency" if field in money else "Check", permlevel=0)
                                    for field in self.module.MANUFACTURE_ITEM_FIELDS})
        self.metas[self.module.SE]["additional_costs"] = Record(fieldname="additional_costs", fieldtype="Table", permlevel=0,
                                                               options="Landed Cost Taxes and Charges")
        for doctype, fields in self.module.subcontract_stage.PARENTS.items():
            if doctype == self.module.subcontract_stage.JC:
                fields = fields | {"sequence_id"}
            self.permitted[doctype].update(fields)
            self.metas[doctype].update({field: Record(fieldname=field, fieldtype="Float", permlevel=0)
                                      for field in fields if field not in self.metas[doctype]})
        field, childtype, fields = self.module.subcontract_stage.TABLES[self.module.WO]
        self.schema(childtype, fields | {"sequence_id"}, money)
        self.metas[self.module.WO][field] = Record(fieldname=field, fieldtype="Table", permlevel=0, options=childtype)
        for doctype, fields in ((self.module.SE, {"bom_no"}),
                ("Subcontracting Receipt Item", self.module.subcontract_stage.TABLES[self.module.SCR][2])):
            self.permitted[doctype].update(fields)
            self.metas[doctype].update({field: Record(fieldname=field, fieldtype="Data", permlevel=0)
                                      for field in fields if field not in self.metas[doctype]})
        childtype = "Subcontracting Receipt Supplied Item"
        tracking = {"serial_and_batch_bundle", "serial_no", "batch_no", "bom_detail_no"}
        self.permitted[childtype].update(tracking)
        self.metas[childtype].update({field: Record(fieldname=field, fieldtype="Data", permlevel=0) for field in tracking})
        childtype = "Subcontracting Receipt Item"
        self.permitted[childtype].update(self.module.material_returns.FG_TRACKING_FIELDS)
        self.metas[childtype].update({field: Record(fieldname=field, fieldtype="Data", permlevel=0)
            for field in self.module.material_returns.FG_TRACKING_FIELDS})
        Document.environment = self
        self.metas[self.module.SCR]["total_additional_costs"] = Record(fieldname="total_additional_costs", fieldtype="Currency", permlevel=0)
        from test_subcontracting_accounting import install_native_gl
        self.native_gl = install_native_gl(self.frappe, Document, Record,
            lambda name: [row for (kind, _), row in self.documents.items()
                          if kind == self.module.SLE and row.voucher_no == name and not row.is_cancelled])
        # Native GL identity also includes finance book, party and all enabled
        # dimensions. Model actual metadata, rather than silently projecting
        # only the old account/cost-center subset.
        self.proof_schema()
        self.add("Company", "JF", default_currency="CNY")
        self.add("Currency", "CNY")
        self.add("Item", "FG", stock_uom="Nos", is_stock_item=1)
        self.add("Item", "RM", stock_uom="Kg", is_stock_item=1)
        self.add("Warehouse", "FG-WH", company="JF", is_group=0)
        self.add("Warehouse", "RM-WH", company="JF", is_group=0)
        self.add("Warehouse", "REJECT-WH", company="JF", is_group=0)
        self.add(self.module.FLOW, "FLOW", company="JF", item_code="FG", docstatus=0)
        self.child(self.module.FLOW, "FLOW", "materials", "MAT", item_code="RM")

    def proof_schema(self):
        from erpnext.accounts.general_ledger import get_merge_properties
        # Native merge keys include advance-voucher attributes which are not
        # database columns on ordinary GL Entry. Do not fabricate them.
        persisted_merge = set(get_merge_properties(self.native_gl["dimensions"])) - {
            "advance_voucher_no", "advance_voucher_type"}
        for doctype, fields in ((self.module.GLE, persisted_merge),
                (self.module.SCR, {"project", "finance_book"} | set(self.native_gl["dimensions"])),
                ("Subcontracting Receipt Item", set(self.native_gl["dimensions"]))):
            self.permitted[doctype].update(fields)
            self.metas[doctype].update({field: Record(fieldname=field, fieldtype="Data", permlevel=0)
                for field in fields if field not in self.metas[doctype]})

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
        tables = self.module.TABLES.get(parenttype, ())
        if parenttype == self.module.WO:
            tables = (self.module.subcontract_stage.TABLES[parenttype],)
        if parenttype == self.module.SE:
            tables = (*tables, self.module.MANUFACTURE_CHARGES)
        _field, childtype, fields = next(table for table in tables if table[0] == fieldname)
        row = self.add(childtype, name, **({field: None for field in fields - self.module.CHILD} | {
            "parent": parent, "parenttype": parenttype, "parentfield": fieldname, "idx": 1,
            "docstatus": self.documents[parenttype, parent].docstatus} | values))
        return row

    def get_doc(self, doctype, name, **kwargs):
        if doctype in self.doc_hooks:
            self.doc_hooks[doctype](name)
        if (doctype, name) not in self.documents:
            raise self.frappe.DoesNotExistError("missing")
        doc = Document(deepcopy(self.documents[doctype, name]))
        tables = self.module.TABLES.get(doctype, ())
        if doctype == self.module.WO:
            tables = (self.module.subcontract_stage.TABLES[doctype],)
        if doctype == self.module.SE:
            tables = (*tables, self.module.MANUFACTURE_CHARGES)
        for fieldname, childtype, _fields in tables:
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
        return actual == value if op == "=" else actual in value if op == "in" else actual <= value if op == "<=" else False

    def get_list(self, doctype, filters, limit_page_length, fields=None, order_by=None, pluck=None, **kwargs):
        if doctype == self.module.GLE:
            self.accounting_fixture(filters.get("voucher_no"))
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

    def get_all(self, doctype, filters, fields=None, limit_page_length=0, order_by=None, **kwargs):
        if doctype == self.module.GLE:
            if fields and fields != ["*"] and set(fields) - self.permitted[doctype]:
                raise RuntimeError("query selected a nonexistent GL column")
            self.accounting_fixture(filters.get("voucher_no"))
        if doctype in self.raw_hooks:
            self.raw_hooks[doctype](filters)
        rows = [deepcopy(row) for (kind, _), row in sorted(self.documents.items())
                if kind == doctype and self.matches(row, filters)]
        if kwargs.get("pluck"):
            rows = [row[kwargs["pluck"]] for row in rows]
        return rows[:limit_page_length] if limit_page_length else rows

    def accounting_fixture(self, name):
        """Independent saved GL for the explicit source model, including merged expense."""
        if (self.module.SCR, name) not in self.documents:
            return
        doc = self.documents[self.module.SCR, name]
        for key in [key for key in self.documents if key[0] == self.module.GLE and self.documents[key].voucher_no == name]:
            del self.documents[key]
        items = [row for (kind, _), row in self.documents.items()
                 if kind == "Subcontracting Receipt Item" and row.parent == name]
        values = {}
        def add(account, center, project, amount, *, header=False, detail=None):
            dimensions = tuple((field, doc.get(field) if header else item.get(field) or doc.get(field))
                               for field in self.native_gl["dimensions"])
            key = (detail or (item.name if name in self.marked_receipts else None),
                   account, center, project, doc.get("finance_book"), dimensions)
            values[key] = values.get(key, Decimal(0)) + Decimal(str(amount))
        for item in items:
            center, project = item.get("cost_center"), item.get("project")
            fg, material = Decimal(0), Decimal(0)
            for (kind, _), sle in list(self.documents.items()):
                if kind != self.module.SLE or sle.voucher_no != name or sle.is_cancelled:
                    continue
                if sle.voucher_detail_no == item.name:
                    fg += Decimal(str(sle.stock_value_difference))
                    add("STOCK-"+sle.warehouse, center, project, sle.stock_value_difference)
                else:
                    raw = self.documents.get(("Subcontracting Receipt Supplied Item", sle.voucher_detail_no))
                    if raw and raw.reference_name == item.name:
                        material -= Decimal(str(sle.stock_value_difference))
                        add("STOCK-"+sle.warehouse, raw.get("cost_center") or center, project, sle.stock_value_difference)
                        add(raw.get("expense_account") or item.expense_account,
                            raw.get("cost_center") or center, project, -Decimal(str(sle.stock_value_difference)))
            qty = Decimal(str(item.qty))
            if doc.is_return:
                original = self.documents["Subcontracting Receipt Item", item.subcontracting_receipt_item]
                qty = qty if item.warehouse == original.warehouse else Decimal(0)
            unit_precision = self.precisions.get(("Subcontracting Receipt Item", "service_cost_per_qty"), 2)
            qty_precision = self.precisions.get(("Subcontracting Receipt Item", "qty"), 6)
            processing = Decimal(str(round(float(qty), qty_precision))) * Decimal(str(round(float(item.service_cost_per_qty or 0), unit_precision)))
            other = qty * Decimal(str(item.additional_cost_per_qty or 0))
            add(item.expense_account, center, project, -fg + processing - (material+processing+other-fg))
            add(item.service_expense_account or item.expense_account, center, project, -processing)
            add("ADJUST", center, project, material+processing+other-fg)
            add(item.expense_account, doc.get("cost_center") or "HEADER-CC", doc.get("project"), other, header=True)
        for (kind, _), charge in list(self.documents.items()):
            if kind == "Landed Cost Taxes and Charges" and charge.parent == name:
                amount = charge.base_amount or charge.amount
                add(charge.expense_account, doc.get("cost_center") or "HEADER-CC", doc.get("project"),
                    -Decimal(str(amount)), header=True, detail=charge.name if name in self.marked_receipts else None)
        generated = []
        for index, ((detail, account, center, project, book, dimensions), value) in enumerate(values.items()):
            if value:
                entry = {field: None for field in self.module.material_returns.receipt_gl_proof_fields()
                          - {"name", "modified"}}
                entry.update(company="JF", voucher_type=self.module.SCR, voucher_no=name,
                    posting_date=doc.posting_date,
                    voucher_detail_no=detail, account=account, cost_center=center, project=project, finance_book=book,
                    debit=max(value,0), credit=max(-value,0), is_cancelled=0)
                entry.update(dimensions)
                generated.append(Record(entry))
        generated = self.native_gl["process_gl_map"](generated, from_repost=True)
        for index, entry in enumerate(generated):
            self.add(self.module.GLE, name+"-GL-"+str(index), **entry)

    def ledger(self, name, doctype, voucher, detail, item, warehouse, quantity, value, adjustment=0):
        return self.add(self.module.SLE, name, creation=datetime(2026, 10, 3, 10), company="JF", item_code=item, warehouse=warehouse,
            stock_uom=self.documents["Item", item].stock_uom, voucher_type=doctype, voucher_no=voucher,
            voucher_detail_no=detail, actual_qty=quantity, stock_value_difference=value, is_cancelled=0,
            is_adjustment_entry=adjustment, posting_date=date(2026, 10, 3), posting_time=timedelta(hours=10))

    def receipt(self, name="SCR", qty=70, rejected=0, loss=30, is_return=0, original=None, flow="FLOW", from_rejected=False):
        received = Decimal(str(qty)) + Decimal(str(rejected)) + Decimal(str(loss))
        doc = self.add(self.module.SCR, name, company="JF", posting_date=date(2026, 10, 3),
            posting_time=timedelta(hours=10), is_return=is_return, return_against=original,
            status="Return" if is_return else "Completed", supplier_warehouse="RM-WH", cost_center="HEADER-CC",
            use_item_inventory_account=False, company_currency="CNY")
        item = self.child(self.module.SCR, name, "items", name + "-FG", item_code="FG", jf_flow_order=flow,
            qty=qty, rejected_qty=rejected, process_loss_qty=loss, received_qty=received, stock_uom="Nos",
            conversion_factor=1, warehouse="FG-WH", rejected_warehouse="REJECT-WH",
            subcontracting_receipt_item=original + "-FG" if original else None,
            rm_cost_per_qty=1, service_cost_per_qty=2, additional_cost_per_qty=0, rm_supp_cost=received,
            secondary_items_cost_per_qty=0, landed_cost_voucher_amount=0, amount=received * 3, rate=3,
            expense_account="EXPENSE", service_expense_account="SERVICE", cost_center="FG-CC", project="PROJECT")
        self.child(self.module.SCR, name, "supplied_items", name + "-RM", main_item_code="FG", rm_item_code="RM",
            reference_name=item.name, consumed_qty=received, stock_uom="Kg", rate=1, amount=received)
        # Native originals allocate all covered cost to accepted stock; the
        # physical return uses the exact original accepted SLE unit value.
        value = received * 3
        if is_return and original and qty:
            source = self.documents[self.module.SLE, original + ("-RJ-SLE" if from_rejected else "-FG-SLE")]
            value = Decimal(str(source.stock_value_difference)) * Decimal(str(qty)) / Decimal(str(source.actual_qty))
            if from_rejected:
                item.warehouse, item.rejected_warehouse = "REJECT-WH", ""
        item.amount, item.rate = value, value / Decimal(str(qty or rejected)) if qty or rejected else 0
        if qty:
            self.ledger(name + "-FG-SLE", self.module.SCR, name, item.name, "FG", item.warehouse, qty, value)
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

    def multi_receipt(self):
        """Two real primary rows share accounts, but belong to different flows."""
        doc, first = self.receipt(qty=60, loss=0)
        self.add("Item", "FG-KG", stock_uom="Kg", is_stock_item=1)
        self.add(self.module.FLOW, "FLOW-KG", company="JF", item_code="FG-KG", docstatus=0)
        self.child(self.module.FLOW, "FLOW-KG", "materials", "MAT-KG", item_code="RM")
        _second_doc, second = self.receipt("SECOND", qty=30, loss=0, flow="FLOW-KG")
        second.update(parent="SCR", item_code="FG-KG", stock_uom="Kg", idx=2,
                      service_cost_per_qty=3, amount=120, rate=4)
        self.documents["Subcontracting Receipt Supplied Item", "SECOND-RM"].update(
            parent="SCR", main_item_code="FG-KG", idx=2)
        self.documents[self.module.SLE, "SECOND-FG-SLE"].update(
            voucher_no="SCR", item_code="FG-KG", stock_uom="Kg", stock_value_difference=120)
        self.documents[self.module.SLE, "SECOND-RM-SLE"].voucher_no = "SCR"
        del self.documents[self.module.SCR, "SECOND"]
        # Native merge combines raw/material/service contra entries in each
        # group; account totals alone cannot prove either flow's fees.
        first.service_expense_account = second.service_expense_account = "EXPENSE"
        self.marked_receipts.add("SCR")
        return doc, first, second

    def test_multi_receipt_partial_return_keeps_each_flow_cost_and_stock_separate(self):
        self.multi_receipt()
        _doc, returned = self.receipt("RETURN", qty=-15, loss=0, is_return=1, original="SCR")
        returned.service_expense_account = "EXPENSE"
        self.marked_receipts.add("RETURN")
        first = self.module.receipt_costs("FLOW")
        second = self.module.receipt_costs("FLOW-KG")
        self.assertEqual(first["totals"], {"material": 45, "processing": 90, "other": 0})
        self.assertEqual(second["totals"], {"material": 30, "processing": 90, "other": 0})
        self.assertEqual({row["receipt_item"]: row["qty"] for row in first["rows"]},
                         {"SCR-FG": 60, "RETURN-FG": -15})
        self.assertEqual([(row["receipt_item"], row["stock_uom"]) for row in second["rows"]],
                         [("SECOND-FG", "Kg")])
        self.assertEqual(first["receipt_valuation"]["inventory_value_change"], 135)
        self.assertEqual(second["receipt_valuation"]["inventory_value_change"], 120)

    def test_multi_receipt_native_dimensions_and_cost_center_allocation_are_proved(self):
        self.native_gl["dimensions"][:] = ["department"]
        self.native_gl["cost_allocations"]["FG-CC"] = [("CC-A", 40), ("CC-B", 60)]
        self.proof_schema()
        doc, first, second = self.multi_receipt()
        doc.department, first.department, second.department = "HEADER", "CASTING", "PLATING"
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"]["processing"], 120)
        self.assertEqual(self.module.receipt_costs("FLOW-KG")["totals"]["processing"], 90)

    def test_multi_receipt_hidden_identity_dimension_or_book_refuses_partial_cost(self):
        self.native_gl["dimensions"][:] = ["department"]
        self.proof_schema()
        self.multi_receipt()
        for doctype, field in ((self.module.GLE, "voucher_detail_no"), (self.module.GLE, "finance_book"),
                (self.module.GLE, "department"), (self.module.SCR, "department"),
                ("Subcontracting Receipt Item", "department")):
            self.hidden[doctype] = {field}
            with self.subTest(doctype=doctype, field=field), self.assertRaises(PermissionError):
                self.module.receipt_costs("FLOW")
            self.hidden.clear()
        self.metas[self.module.GLE]["department"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")

    def test_multi_receipt_unidentified_legacy_account_overlap_or_wrong_row_refuses(self):
        self.multi_receipt()
        self.marked_receipts.clear()
        with self.assertRaisesRegex(ValueError, "合并会计费用"):
            self.module.receipt_costs("FLOW")
        self.marked_receipts.add("SCR")
        saved_fixture = self.accounting_fixture
        def corrupt_identity(name):
            saved_fixture(name)
            for (kind, _), row in self.documents.items():
                if kind == self.module.GLE and row.voucher_no == name:
                    row.voucher_detail_no = "FOREIGN-ROW"
                    break
        self.accounting_fixture = corrupt_identity
        with self.assertRaisesRegex(ValueError, "明细标识"):
            self.module.receipt_costs("FLOW")

    def test_receipt_proof_cache_is_request_scoped_and_closed_against_late_gl_edit(self):
        self.multi_receipt()
        _doc, returned = self.receipt("RETURN", qty=-15, loss=0, is_return=1, original="SCR")
        returned.service_expense_account = "EXPENSE"
        self.marked_receipts.add("RETURN")
        session = self.module._Session("FLOW")
        session.load_receipts()
        source = session.source(self.module.SCR, "SCR")
        self.assertIs(session._receipt_fee_proof(source), session._receipt_fee_proof(source))
        self.assertEqual(sum(kind == self.module.GLE for kind, *_ in session.queries), 2)
        self.assertEqual(len(session.receipt_gl_sources), 2)
        self.assertEqual(sum(call.args[0] == self.module.GLE
                             for call in self.frappe.get_all.call_args_list), 2)
        saved_fixture = self.accounting_fixture
        def corrupt_amount(name):
            saved_fixture(name)
            for (kind, _), row in self.documents.items():
                if kind == self.module.GLE and row.voucher_no == name:
                    row.debit += 1
                    break
        self.accounting_fixture = corrupt_amount
        with self.assertRaises(PermissionError):
            session.finish()

    def test_receipt_proof_closure_rejects_changed_native_dimension_configuration(self):
        self.multi_receipt()
        session = self.module._Session("FLOW")
        session.load_receipts()
        self.native_gl["dimensions"][:] = ["department"]
        with self.assertRaises(PermissionError):
            session.finish()

    def test_gl_projection_reads_actual_columns_without_transient_advance_merge_keys(self):
        self.receipt(qty=3, loss=0)
        fields = self.module.material_returns.receipt_gl_proof_fields()
        self.assertFalse({"advance_voucher_no", "advance_voucher_type"} & fields)
        self.assertTrue({"finance_book", "party", "against_voucher", "voucher_detail_no"} <= fields)
        rows = self.module.material_returns._receipt_fee_ledger(self.get_doc(self.module.SCR, "SCR"))
        self.assertTrue(rows)
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"]["processing"], 6)

    def test_receipt_proof_closes_native_request_cached_allocation_headers_and_children(self):
        self.multi_receipt()
        allocation = self.add("Cost Center Allocation", "ALLOC", company="JF", main_cost_center="FG-CC",
                              valid_from=date(2026, 10, 1))
        percentage = self.add("Cost Center Allocation Percentage", "PERCENT", parent=allocation.name,
                              cost_center="CC-A", percentage=100)
        # The native request cache deliberately stays unchanged. Closure must
        # inspect raw configuration, not trust a second cached calculation.
        self.native_gl["cost_allocations"]["FG-CC"] = [("CC-A", 100)]
        session = self.module._Session("FLOW")
        session.load_receipts()
        percentage.percentage = 40
        with self.assertRaises(PermissionError):
            session.finish()
        percentage.percentage = 100
        session = self.module._Session("FLOW")
        session.load_receipts()
        self.add("Cost Center Allocation", "NEWER", company="JF", main_cost_center="FG-CC",
                 valid_from=date(2026, 10, 3))
        with self.assertRaises(PermissionError):
            session.finish()
        # Capture an empty configuration scope as well: a first allocation
        # appearing after calculation must invalidate the returned costs.
        del self.documents["Cost Center Allocation", "NEWER"]
        del self.documents["Cost Center Allocation", "ALLOC"]
        del self.documents["Cost Center Allocation Percentage", "PERCENT"]
        self.native_gl["cost_allocations"].clear()
        session = self.module._Session("FLOW")
        session.load_receipts()
        self.add("Cost Center Allocation", "FIRST", company="JF", main_cost_center="FG-CC",
                 valid_from=date(2026, 10, 3))
        with self.assertRaises(PermissionError):
            session.finish()

    def issue_return(self, name="ISSUE-RETURN", qty=5, source="ISSUE", status=1, value=None):
        original = self.documents[self.module.SE, source]
        source_row = self.get_doc(self.module.SE, source).get("items")[0]
        doc = self.add(self.module.SE, name, company="JF", purpose="Material Receipt", stock_entry_type="Material Receipt",
            jf_material_return_against=source, jf_material_issue_flow=original.get("jf_material_issue_flow") or source_row.jf_flow_order,
            posting_date=date(2026, 10, 4), posting_time=timedelta(hours=10), docstatus=status)
        row = self.child(self.module.SE, name, "items", name + "-RM", item_code="RM", jf_flow_order=source_row.jf_flow_order,
            jf_material_return_against_detail=source_row.name, qty=qty, uom="Kg", stock_uom="Kg",
            conversion_factor=1, transfer_qty=qty, s_warehouse=None, t_warehouse="RM-WH")
        self.ledger(name + "-SLE", self.module.SE, name, row.name, "RM", "RM-WH", qty,
                    qty * 4 if value is None else value)
        self.documents[self.module.SLE, name + "-SLE"].is_cancelled = status != 1
        self.documents[self.module.SLE, name + "-SLE"].posting_date = doc.posting_date
        return doc, row

    def manufacture(self, name="MAKE", material=40, other=0, qty=5, raw_qty=4, work="WO", flow="FLOW"):
        if (self.module.WO, work) not in self.documents:
            self.add(self.module.WO, work, company="JF", production_item="FG", jf_flow_order=flow, track_semi_finished_goods=0)
        doc = self.add(self.module.SE, name, company="JF", purpose="Manufacture", stock_entry_type="Manufacture",
            work_order=work, job_card=None, total_additional_costs=other,
            from_bom=1, fg_completed_qty=qty, process_loss_qty=0,
            posting_date=date(2026, 10, 3), posting_time=timedelta(hours=10))
        for suffix, item, quantity, source, target, finished, additional in (
            ("RM", "RM", raw_qty, "RM-WH", None, 0, 0),
            ("FG", "FG", qty, None, "FG-WH", 1, other),
        ):
            row = self.child(self.module.SE, name, "items", name + "-" + suffix, item_code=item, jf_flow_order=flow,
                qty=quantity, uom="Kg" if item == "RM" else "Nos", stock_uom="Kg" if item == "RM" else "Nos",
                conversion_factor=1, transfer_qty=quantity, s_warehouse=source, t_warehouse=target,
                is_finished_item=finished, secondary_item_type=None, valuation_type=None,
                additional_cost=additional, landed_cost_voucher_amount=0)
            self.ledger(name + "-" + suffix + "-SLE", self.module.SE, name, row.name, item, source or target,
                        -quantity if source else quantity, -material if source else material + other)
        if other:
            self.child(self.module.SE, name, "additional_costs", name + "-COST", amount=other,
                       account_currency="CNY", exchange_rate=1, base_amount=other)
        return doc

    def stage_chain(self, *, qty=4, rejected=0, loss=0, material=40, other=2):
        self.add("Item", "SEMI", stock_uom="Nos", is_stock_item=1)
        wo = self.add(self.module.WO, "STAGE-WO", company="JF", production_item="FG", jf_flow_order="FLOW",
                      track_semi_finished_goods=1)
        for sequence, suffix, item, raw, outsourced in ((1, "CAST", "SEMI", "RM", 0), (2, "PLATE", "FG", "SEMI", 1)):
            self.child(self.module.WO, wo.name, "operations", suffix + "-OP", operation=suffix,
                       finished_good=item, is_subcontracted=outsourced, sequence_id=sequence)
            self.add("BOM", suffix + "-BOM", company="JF", item=item)
            self.child("BOM", suffix + "-BOM", "items", suffix + "-MAT", item_code=raw)
            self.add("Job Card", suffix + "-JC", company="JF", work_order=wo.name, production_item="FG",
                operation_id=suffix + "-OP", operation=suffix, finished_good=item, semi_fg_bom=suffix + "-BOM",
                track_semi_finished_goods=1, is_subcontracted=outsourced, jf_flow_order="FLOW", sequence_id=sequence)
        cast = self.manufacture("CAST", work=wo.name, material=material, other=other, qty=10)
        cast.job_card, cast.bom_no = "CAST-JC", "CAST-BOM"
        self.documents["Stock Entry Detail", "CAST-FG"].item_code = "SEMI"
        self.documents[self.module.SLE, "CAST-FG-SLE"].item_code = "SEMI"
        self.add("Purchase Order", "STAGE-PO", company="JF", supplier="PROCESSOR", is_subcontracted=1,
                 is_old_subcontracting_flow=0)
        self.child("Purchase Order", "STAGE-PO", "items", "PO-STAGE", item_code="PLATING-SERVICE", fg_item="FG",
                   job_card="PLATE-JC", bom="PLATE-BOM", jf_flow_order="FLOW")
        self.add("Item", "PLATING-SERVICE", stock_uom="Nos", is_stock_item=0)
        self.add("Subcontracting Order", "STAGE-SCO", company="JF", supplier="PROCESSOR", purchase_order="STAGE-PO")
        self.child("Subcontracting Order", "STAGE-SCO", "items", "SCO-STAGE", item_code="FG",
                   job_card="PLATE-JC", bom="PLATE-BOM", purchase_order_item="PO-STAGE", jf_flow_order="FLOW")
        receipt, row = self.stage_receipt("PLATE", qty=qty, rejected=rejected, loss=loss, rate=Decimal(str(material + other)) / 10)
        return cast, receipt, row

    def stage_receipt(self, name, *, qty, rejected=0, loss=0, rate=Decimal("4.2"), is_return=0, original=None, flow="FLOW"):
        doc, row = self.receipt(name, qty=qty, rejected=rejected, loss=loss, is_return=is_return, original=original, flow=flow)
        doc.supplier = "PROCESSOR"
        row.update(job_card="PLATE-JC", bom="PLATE-BOM", purchase_order="STAGE-PO", purchase_order_item="PO-STAGE",
            subcontracting_order="STAGE-SCO", subcontracting_order_item="SCO-STAGE", rm_cost_per_qty=rate,
            rm_supp_cost=rate * row.received_qty, service_cost_per_qty=3, amount=(rate + 3) * row.received_qty)
        supplied = self.documents["Subcontracting Receipt Supplied Item", name + "-RM"]
        supplied.update(rm_item_code="SEMI", stock_uom="Nos", rate=rate, amount=rate * row.received_qty)
        self.documents[self.module.SLE, name + "-RM-SLE"].update(item_code="SEMI", stock_uom="Nos",
            stock_value_difference=-rate * row.received_qty)
        if qty:
            if is_return:
                source = self.documents[self.module.SLE, original + "-FG-SLE"]
                row.amount = Decimal(str(source.stock_value_difference)) * Decimal(str(qty)) / Decimal(str(source.actual_qty))
            row.rate = row.amount / Decimal(str(qty))
            self.documents[self.module.SLE, name + "-FG-SLE"].stock_value_difference = row.amount
        return doc, row

    def test_material_covers_loss_while_booked_fees_use_native_accepted_quantity(self):
        self.receipt()
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": Decimal(100), "processing": Decimal(140), "other": Decimal(0)})
        self.assertEqual(result["rows"], [{"receipt": "SCR", "receipt_item": "SCR-FG", "item_code": "FG", "stock_uom": "Nos",
            "qty": 70, "material": Decimal(100), "processing": Decimal(140), "other": Decimal(0),
            "inventory_qty_change": Decimal(70), "inventory_value_change": Decimal(300),
            "valuation_cost_basis": Decimal(240), "valuation_difference": Decimal(60)}])
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 300,
                                                     "valuation_cost_basis": 240, "valuation_difference": 60})
        self.assertEqual(result["currency"], "CNY")
        self.assertNotEqual(sum(result["totals"].values()), 70 * (1 + 2))
        self.assertEqual(self.module.receipt_batch_candidates("FLOW")[0]["qty"], 100)
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql, self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_blank_flow_return_is_included_with_exact_signed_original_reference(self):
        self.receipt()
        self.receipt("RETURN", qty=-10, loss=0, is_return=1, original="SCR", flow=None)
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": 90, "processing": 120, "other": 0})
        batch = self.module.receipt_batch_candidates("FLOW")[0]
        self.assertEqual((batch["qty"], batch["original_qty"], batch["returned_qty"], batch["lost_qty"]), (90, 100, 10, 30))

    def loss_return_costs(self, *, rejected=0, loss=1, from_rejected=False):
        source, row = self.receipt(qty=2, rejected=rejected, loss=loss)
        row.update(rm_supp_cost=24, rm_cost_per_qty=8, amount=30, rate=15)
        self.documents["Subcontracting Receipt Supplied Item", "SCR-RM"].update(rate=8, amount=24)
        self.documents[self.module.SLE, "SCR-RM-SLE"].stock_value_difference = -24
        self.documents[self.module.SLE, "SCR-FG-SLE"].stock_value_difference = 30
        returned, retrow = self.receipt("RETURN", qty=-1, loss=0, is_return=1, original="SCR", flow=None,
                                        from_rejected=from_rejected)
        retrow.update(rm_supp_cost=-8, rm_cost_per_qty=8)
        self.documents["Subcontracting Receipt Supplied Item", "RETURN-RM"].update(rate=8, amount=-8)
        self.documents[self.module.SLE, "RETURN-RM-SLE"].stock_value_difference = 8
        return source, returned, retrow

    def test_loss_return_reports_original_stock_value_and_cost_difference_separately(self):
        self.loss_return_costs()
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": 16, "processing": 2, "other": 0})
        rows = {row["receipt"]: row for row in result["rows"]}
        self.assertEqual((rows["SCR"]["inventory_qty_change"], rows["SCR"]["inventory_value_change"],
                          rows["SCR"]["valuation_cost_basis"], rows["SCR"]["valuation_difference"]), (2, 30, 28, 2))
        self.assertEqual((rows["RETURN"]["inventory_qty_change"], rows["RETURN"]["inventory_value_change"],
                          rows["RETURN"]["valuation_cost_basis"], rows["RETURN"]["valuation_difference"]), (-1, -15, -10, -5))
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 15,
                                                     "valuation_cost_basis": 18, "valuation_difference": -3})
        self.assertEqual(self.documents["Subcontracting Receipt Item", "SCR-FG"].process_loss_qty, 1)

    def test_rejected_zero_value_stock_return_preserves_fees_and_positive_difference(self):
        self.loss_return_costs(rejected=1, loss=0, from_rejected=True)
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": 16, "processing": 4, "other": 0})
        returned = next(row for row in result["rows"] if row["receipt"] == "RETURN")
        self.assertEqual((returned["inventory_value_change"], returned["valuation_cost_basis"],
                          returned["valuation_difference"]), (0, -8, 8))
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 30,
                                                     "valuation_cost_basis": 20, "valuation_difference": 10})

    def test_all_rejected_receipt_zero_stock_value_is_explicit_and_partial_return_is_exact(self):
        self.receipt(qty=0, rejected=100, loss=0)
        before = self.module.receipt_costs("FLOW")
        self.assertEqual(before["receipt_valuation"], {"inventory_value_change": 0,
                                                     "valuation_cost_basis": 100, "valuation_difference": -100})
        self.receipt("RETURN", qty=-1, loss=0, is_return=1, original="SCR", flow=None, from_rejected=True)
        after = self.module.receipt_costs("FLOW")
        self.assertEqual(after["totals"], {"material": 99, "processing": 0, "other": 0})
        self.assertEqual(after["receipt_valuation"], {"inventory_value_change": 0,
                                                    "valuation_cost_basis": 99, "valuation_difference": -99})

    def test_return_and_original_reverse_cancel_restore_valuation_and_net_costs(self):
        original, returned, _ = self.loss_return_costs()
        self.assertEqual(self.module.receipt_costs("FLOW")["receipt_valuation"]["valuation_difference"], -3)
        returned.docstatus = 2
        for (kind, _), row in self.documents.items():
            if kind == self.module.SLE and row.voucher_no == returned.name:
                row.is_cancelled = 1
        after = self.module.receipt_costs("FLOW")
        self.assertEqual(after["totals"], {"material": 24, "processing": 4, "other": 0})
        self.assertEqual(after["receipt_valuation"], {"inventory_value_change": 30,
                                                    "valuation_cost_basis": 28, "valuation_difference": 2})
        original.docstatus = 2
        for (kind, _), row in self.documents.items():
            if kind == self.module.SLE and row.voucher_no == original.name:
                row.is_cancelled = 1
        zero = self.module.receipt_costs("FLOW")
        self.assertEqual(zero["rows"], [])
        self.assertEqual(zero["totals"], {"material": 0, "processing": 0, "other": 0})
        self.assertEqual(zero["receipt_valuation"], {"inventory_value_change": 0,
                                                   "valuation_cost_basis": 0, "valuation_difference": 0})

    def test_old_covered_price_or_tampered_stock_value_cannot_be_called_valuation_difference(self):
        self.loss_return_costs()
        row = self.documents["Subcontracting Receipt Item", "RETURN-FG"]
        ledger = self.documents[self.module.SLE, "RETURN-FG-SLE"]
        for changed in ("amount", "ledger", "both"):
            row.amount, ledger.stock_value_difference = -15, -15
            if changed in ("amount", "both"):
                row.amount = -10
            if changed in ("ledger", "both"):
                ledger.stock_value_difference = -10
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "原入库单价"):
                self.module.receipt_costs("FLOW")

    def test_restored_material_and_unit_fees_must_match_original_facts(self):
        self.loss_return_costs()
        row = self.documents["Subcontracting Receipt Item", "RETURN-FG"]
        row.service_cost_per_qty = 3
        with self.assertRaisesRegex(ValueError, "原单位工费"):
            self.module.receipt_costs("FLOW")
        row.service_cost_per_qty = 2
        row.rm_supp_cost, row.rm_cost_per_qty = -9, 9
        self.documents["Subcontracting Receipt Supplied Item", "RETURN-RM"].update(rate=9, amount=-9)
        self.documents[self.module.SLE, "RETURN-RM-SLE"].stock_value_difference = 9
        with self.assertRaisesRegex(ValueError, "原耗料份额"):
            self.module.receipt_costs("FLOW")

    def test_unit_fee_rounding_cannot_hide_a_material_total_fee_change(self):
        _source, row = self.receipt(qty=200, rejected=0, loss=1)
        row.service_cost_per_qty = Decimal("2.004")
        row.amount = Decimal(201) * Decimal("3.004")
        self.documents[self.module.SLE, "SCR-FG-SLE"].stock_value_difference = row.amount
        self.receipt("RETURN", qty=-100, loss=0, is_return=1, original="SCR", flow=None)
        # Per-unit currency rounding equates 2.004 and 2.000, but their actual
        # signed processing amounts differ by 0.40 and must not be disguised
        # as a new inventory valuation difference.
        with self.assertRaisesRegex(ValueError, "原单位工费"):
            self.module.receipt_costs("FLOW")

    def test_non_receipt_manufacturing_and_wages_do_not_enter_receipt_valuation_difference(self):
        self.loss_return_costs()
        self.manufacture(material=40, other=0)
        self.wage_cost_result.update(rows=[{"journal_entry": "PAYROLL", "work_order": "WO", "job_card": None}],
                                     total=Decimal(13))
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["processing"], result["totals"]["other"]), (56, 15, 0))
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 15,
                                                     "valuation_cost_basis": 18, "valuation_difference": -3})

    def test_stage_loss_return_basis_retains_internal_supply_but_fee_material_does_not_double_count(self):
        self.stage_chain(qty=3, loss=1)
        self.stage_receipt("RETURN", qty=-1, is_return=1, original="PLATE", flow=None)
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["processing"], result["totals"]["other"]), (40, 6, 2))
        self.assertTrue(all(row["material"] == 0 for row in result["receipts"]))
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": Decimal("19.2"),
            "valuation_cost_basis": Decimal("18.6"), "valuation_difference": Decimal("0.6")})
        returned = next(row for row in result["receipts"] if row["receipt"] == "RETURN")
        self.assertEqual((returned["inventory_value_change"], returned["valuation_cost_basis"],
                          returned["valuation_difference"]), (Decimal("-9.6"), Decimal("-7.2"), Decimal("-2.4")))

    def test_accepted_rejected_and_lost_all_contribute_and_qty_zero_cache_is_reconstructed(self):
        self.receipt(qty=60, rejected=10, loss=30)
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"]["material"], 100)
        self.documents.clear()
        self.setUp()
        _doc, item = self.receipt(qty=0, rejected=100, loss=0)
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"], {"material": 100, "processing": 0, "other": 0})
        item.rm_supp_cost = None  # native qty=0 branch can leave a missing/stale source
        item.rm_cost_per_qty = None
        item.amount = 200  # native original used only the persisted service fee
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": 100, "processing": 0, "other": 0})
        self.assertEqual(result["receipt_valuation"]["valuation_difference"], -100)

    def test_native_all_rejected_stale_cache_loss_and_partial_return_use_actual_consumption(self):
        _doc, item = self.receipt(qty=0, rejected=2, loss=1)
        item.update(rm_supp_cost=0, rm_cost_per_qty=0, amount=6)
        self.receipt("RETURN", qty=-1, loss=0, is_return=1, original="SCR", flow=None, from_rejected=True)
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual({field: result["totals"][field] for field in ("material", "processing", "other")},
                         {"material": 2, "processing": 0, "other": 0})
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 0,
                        "valuation_cost_basis": 2, "valuation_difference": -2})
        returned = next(row for row in result["receipts"] if row["receipt"] == "RETURN")
        self.assertEqual((returned["inventory_value_change"], returned["valuation_cost_basis"],
                          returned["valuation_difference"]), (0, -1, 1))

    def test_all_rejected_missing_fees_or_material_sle_cannot_supply_partial_cost(self):
        _doc, item = self.receipt(qty=0, rejected=100, loss=0)
        item.update(rm_supp_cost=0, rm_cost_per_qty=None, amount=200)
        for field in ("service_cost_per_qty", "additional_cost_per_qty"):
            saved = item[field]
            item[field] = None
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "缺失"):
                self.module.receipt_costs("FLOW")
            item[field] = saved
        ledger = self.documents[self.module.SLE, "SCR-RM-SLE"]
        ledger.stock_value_difference = -99
        with self.assertRaisesRegex(ValueError, "实际材料库存价值"):
            self.module.receipt_costs("FLOW")
        ledger.stock_value_difference = -100
        self.list_denied.add((self.module.SLE, ledger.name))
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")

    def mixed_lot_source(self):
        _doc, row = self.receipt(qty=4, rejected=0, loss=0)
        row.update(rm_supp_cost=8, rm_cost_per_qty=2, rate=4, amount=16)
        self.documents["Subcontracting Receipt Supplied Item", "SCR-RM"].update(
            rate=2, amount=8, bom_detail_no="BOM-RAW", serial_and_batch_bundle="SOURCE-LOTS")
        self.documents[self.module.SLE, "SCR-RM-SLE"].stock_value_difference = -8
        self.documents[self.module.SLE, "SCR-FG-SLE"].stock_value_difference = 16
        kind = self.module.material_returns.BUNDLE
        self.add(kind, "SOURCE-LOTS", company="JF", item_code="RM", warehouse="RM-WH", type_of_transaction="Outward",
                 voucher_type=self.module.SCR, voucher_no="SCR", voucher_detail_no="SCR-RM", total_qty=-4, is_cancelled=0)
        for batch, rate in (("A", 1), ("B", 3)):
            self.child(kind, "SOURCE-LOTS", "entries", "SOURCE-" + batch, batch_no=batch, qty=-2,
                       incoming_rate=rate, stock_value_difference=-2 * rate, warehouse="RM-WH")

    def mixed_lot_return(self, name="RETURN", batch="B", rate=3):
        _doc, row = self.receipt(name, qty=-1, loss=0, is_return=1, original="SCR", flow=None)
        row.update(rm_supp_cost=-rate, rm_cost_per_qty=rate)
        self.documents["Subcontracting Receipt Supplied Item", name + "-RM"].update(
            rate=rate, amount=-rate, bom_detail_no="BOM-RAW", serial_and_batch_bundle=name + "-LOTS")
        self.documents[self.module.SLE, name + "-RM-SLE"].stock_value_difference = rate
        kind = self.module.material_returns.BUNDLE
        self.add(kind, name + "-LOTS", company="JF", item_code="RM", warehouse="RM-WH", type_of_transaction="Inward",
            voucher_type=self.module.SCR, voucher_no=name, voucher_detail_no=name + "-RM", total_qty=1, is_cancelled=0)
        self.child(kind, name + "-LOTS", "entries", name + "-LOT", batch_no=batch, qty=1,
            incoming_rate=rate, stock_value_difference=rate, warehouse="RM-WH")

    def test_mixed_batch_return_cost_uses_selected_original_rate_and_keeps_fg_value_separate(self):
        self.mixed_lot_source()
        self.mixed_lot_return()
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": 5, "processing": 6, "other": 0})
        returned = next(row for row in result["rows"] if row["receipt"] == "RETURN")
        self.assertEqual((returned["material"], returned["inventory_value_change"], returned["valuation_cost_basis"],
                          returned["valuation_difference"]), (-3, -4, -5, 1))
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 12,
                        "valuation_cost_basis": 11, "valuation_difference": 1})

    def test_return_batch_capacity_cancellation_and_value_tampering_are_checked(self):
        self.mixed_lot_source()
        for name in ("RET-1", "RET-2", "RET-3"):
            self.mixed_lot_return(name)
        with self.assertRaisesRegex(ValueError, "累计退回"):
            self.module.receipt_costs("FLOW")
        self.documents[self.module.SCR, "RET-1"].docstatus = 2
        for (kind, _), ledger in self.documents.items():
            if kind == self.module.SLE and ledger.voucher_no == "RET-1":
                ledger.is_cancelled = 1
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 2)
        entry = self.documents[self.module.material_returns.BUNDLE_ENTRY, "RET-2-LOT"]
        entry.update(incoming_rate=2, stock_value_difference=2)
        with self.assertRaisesRegex(ValueError, "估值"):
            self.module.receipt_costs("FLOW")

    def test_hidden_bundle_entry_or_source_lot_changes_refuse_complete_cost(self):
        self.mixed_lot_source()
        self.mixed_lot_return()
        self.hidden[self.module.material_returns.BUNDLE_ENTRY] = {"incoming_rate"}
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")
        self.hidden.clear()
        self.documents[self.module.material_returns.BUNDLE_ENTRY, "SOURCE-B"].stock_value_difference = -5
        with self.assertRaisesRegex(ValueError, "估值"):
            self.module.receipt_costs("FLOW")

    def finished_lot_source(self):
        _doc, row = self.receipt(qty=4, rejected=0, loss=0)
        row.serial_and_batch_bundle = "SOURCE-FG-LOTS"
        self.documents[self.module.SLE, "SCR-FG-SLE"].serial_and_batch_bundle = row.serial_and_batch_bundle
        kind = self.module.material_returns.BUNDLE
        self.add(kind, row.serial_and_batch_bundle, company="JF", item_code="FG", warehouse="FG-WH",
            type_of_transaction="Inward", voucher_type=self.module.SCR, voucher_no="SCR",
            voucher_detail_no=row.name, total_qty=4, is_cancelled=0)
        for batch, rate in (("A", 1), ("B", 5)):
            self.child(kind, row.serial_and_batch_bundle, "entries", "FG-SOURCE-" + batch, batch_no=batch,
                qty=2, incoming_rate=rate, stock_value_difference=2 * rate, warehouse="FG-WH")

    def finished_lot_return(self, name="RETURN", batch="B", rate=5):
        _doc, row = self.receipt(name, qty=-1, loss=0, is_return=1, original="SCR", flow=None)
        row.update(amount=-rate, rate=rate, serial_and_batch_bundle=name + "-FG-LOTS")
        self.documents[self.module.SLE, name + "-FG-SLE"].update(stock_value_difference=-rate,
            serial_and_batch_bundle=row.serial_and_batch_bundle)
        kind = self.module.material_returns.BUNDLE
        self.add(kind, row.serial_and_batch_bundle, company="JF", item_code="FG", warehouse="FG-WH",
            type_of_transaction="Outward", voucher_type=self.module.SCR, voucher_no=name,
            voucher_detail_no=row.name, total_qty=-1, is_cancelled=0)
        self.child(kind, row.serial_and_batch_bundle, "entries", name + "-FG-LOT", batch_no=batch,
            qty=-1, incoming_rate=rate, stock_value_difference=-rate, warehouse="FG-WH")

    def test_finished_selected_historical_stock_value_and_covered_material_fees_have_separate_cost_bases(self):
        self.finished_lot_source()
        self.finished_lot_return()
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["totals"], {"material": 3, "processing": 6, "other": 0})
        returned = next(row for row in result["rows"] if row["receipt"] == "RETURN")
        self.assertEqual((returned["inventory_value_change"], returned["valuation_cost_basis"],
            returned["valuation_difference"]), (-5, -3, -2))
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 7,
            "valuation_cost_basis": 9, "valuation_difference": -2})

    def test_finished_cost_read_checks_per_lot_quota_and_cancel_releases_only_cancelled_return(self):
        self.finished_lot_source()
        for name in ("RET-1", "RET-2", "RET-3"):
            self.finished_lot_return(name)
        with self.assertRaisesRegex(ValueError, "累计退回"):
            self.module.receipt_costs("FLOW")
        self.documents[self.module.SCR, "RET-1"].docstatus = 2
        self.documents[self.module.material_returns.BUNDLE, "RET-1-FG-LOTS"].is_cancelled = 1
        for (kind, _), ledger in self.documents.items():
            if kind == self.module.SLE and ledger.voucher_no == "RET-1":
                ledger.is_cancelled = 1
        result = self.module.receipt_costs("FLOW")
        self.assertEqual(result["receipt_valuation"], {"inventory_value_change": 2,
            "valuation_cost_basis": 6, "valuation_difference": -4})
        self.assertEqual(result["totals"]["material"], 2)

    def test_finished_cost_cannot_disclose_hidden_lots_or_accept_changed_sle_value_or_bundle(self):
        self.finished_lot_source()
        self.finished_lot_return()
        self.hidden["Subcontracting Receipt Item"] = {"serial_and_batch_bundle"}
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")
        self.hidden.clear()
        self.list_denied.add((self.module.material_returns.BUNDLE_ENTRY, "FG-SOURCE-B"))
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")
        self.list_denied.clear()
        ledger = self.documents[self.module.SLE, "RETURN-FG-SLE"]
        for field, value in (("stock_value_difference", -3), ("serial_and_batch_bundle", "OTHER")):
            previous = ledger[field]
            ledger[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.receipt_costs("FLOW")
            ledger[field] = previous

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
        value = Decimal(-10) * Decimal(300) / 70
        row.qty, row.received_qty, row.rm_supp_cost, row.amount = -10, -10, -10, value
        rm = self.documents["Subcontracting Receipt Supplied Item", "RETURN-RM"]
        rm.consumed_qty, rm.amount = -10, -10
        fg_sle = self.documents[self.module.SLE, "RETURN-FG-SLE"]
        fg_sle.actual_qty, fg_sle.stock_value_difference = -10, value
        rm_sle = self.documents[self.module.SLE, "RETURN-RM-SLE"]
        rm_sle.actual_qty, rm_sle.stock_value_difference = 10, 10
        # A non-persisted mapper flag cannot override actual accepted warehouse.
        row.return_qty_from_rejected_warehouse = 1
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"]["material"], 90)
        row.warehouse = "REJECT-WH"
        row.rejected_warehouse = ""
        fg_sle.warehouse = "REJECT-WH"
        fg_sle.stock_value_difference, row.amount, row.rate = 0, 0, 0
        self.assertEqual(self.module.receipt_batch_candidates("FLOW")[0]["qty"], 90)

    def test_native_scr_columns_exclude_temporary_mapper_flag_without_granting_access(self):
        self.receipt()
        childtype = "Subcontracting Receipt Item"
        self.assertNotIn("return_qty_from_rejected_warehouse", self.permitted[childtype])
        self.assertNotIn("return_qty_from_rejected_warehouse", self.metas[childtype])
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"], {"material": 100, "processing": 140, "other": 0})
        self.assertTrue(any(call.args[0] == childtype for call in self.frappe.get_list.call_args_list))
        for call in self.frappe.get_list.call_args_list:
            if call.args[0] == childtype:
                self.assertNotIn("return_qty_from_rejected_warehouse", call.kwargs["fields"])
        self.hidden[childtype] = {"rm_supp_cost"}
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")

    def test_fee_gl_source_permissions_and_hidden_rows_reject_partial_costs(self):
        self.receipt(qty=1, loss=0)
        self.hidden[self.module.GLE] = {"credit"}
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")
        self.hidden.clear()
        self.metas[self.module.GLE]["debit"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.receipt_costs("FLOW")
        self.metas[self.module.GLE]["debit"].mask = 0
        self.accounting_fixture("SCR")
        source = next(name for kind, name in self.documents if kind == self.module.GLE)
        self.list_denied.add((self.module.GLE, source))
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

    def test_partial_material_return_reduces_exact_flow_quantity_and_actual_cost(self):
        self.issue(jf_material_issue_flow="FLOW")
        self.issue_return(qty=5)
        result = self.module.material_issue_costs("FLOW")
        self.assertEqual(result["totals"]["material"], 60)
        rows = {row["issue"]: row for row in result["rows"]}
        self.assertEqual((rows["ISSUE"]["qty"], rows["ISSUE"]["material"]), (20, 80))
        self.assertEqual((rows["ISSUE-RETURN"]["qty"], rows["ISSUE-RETURN"]["material"],
                          rows["ISSUE-RETURN"]["source_material_issue"]), (-5, -20, "ISSUE"))
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 60)

    def test_cancelled_or_draft_material_return_does_not_reduce_posted_cost(self):
        self.issue()
        self.issue_return(status=2)
        self.assertEqual(self.module.material_issue_costs("FLOW")["totals"]["material"], 80)
        self.issue_return(name="DRAFT", status=0)
        self.assertEqual(self.module.material_issue_costs("FLOW")["totals"]["material"], 80)

    def test_manual_issue_child_flow_can_be_returned_with_derived_header_flow(self):
        self.issue()
        self.issue_return(qty=5)
        self.assertEqual(self.module.material_issue_costs("FLOW")["totals"]["material"], 60)

    def test_material_return_requires_precise_source_unit_warehouse_quantity_and_value(self):
        self.issue()
        doc, row = self.issue_return()
        cases = ((row, "jf_material_return_against_detail", "UNKNOWN"), (row, "jf_flow_order", None),
                 (row, "t_warehouse", "FG-WH"), (row, "s_warehouse", "FG-WH"),
                 (row, "stock_uom", "Nos"), (row, "item_code", "FG"),
                 (doc, "jf_material_issue_flow", "OTHER-FLOW"), (doc, "company", "OTHER"),
                 (self.documents[self.module.SLE, "ISSUE-RETURN-SLE"], "stock_value_difference", 21))
        for target, field, value in cases:
            old = target.get(field)
            target[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, PermissionError)):
                self.module.material_issue_costs("FLOW")
            target[field] = old

    def test_total_material_returns_cannot_exceed_original_issue_row(self):
        self.issue()
        self.issue_return(qty=15)
        self.issue_return(name="SECOND", qty=6)
        with self.assertRaises(ValueError):
            self.module.material_issue_costs("FLOW")

    def test_hidden_return_source_or_ledger_refuses_partial_net_cost(self):
        self.issue()
        self.issue_return()
        for doctype, name in ((self.module.SE, "ISSUE-RETURN"), (self.module.SLE, "ISSUE-RETURN-SLE")):
            self.list_denied.add((doctype, name))
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.module.material_issue_costs("FLOW")
            self.list_denied.clear()

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

    def test_manufacturing_consumption_and_fees_are_counted_once_not_wip_or_fg_value(self):
        self.manufacture(other=6)
        transfer = self.issue("TRANSFER", qty=4, factor=1, purpose="Material Transfer for Manufacture",
                              stock_entry_type="Material Transfer for Manufacture", work_order="WO")[0]
        self.documents["Stock Entry Detail", "TRANSFER-RM"].t_warehouse = "FG-WH"
        self.ledger("TRANSFER-IN", self.module.SE, transfer.name, "TRANSFER-RM", "RM", "FG-WH", 4, 40)
        self.ledger("MAKE-ADJUST", self.module.SE, "MAKE", "MAKE-RM", "RM", "RM-WH", 0, -2, adjustment=1)
        self.ledger("MAKE-FG-ADJUST", self.module.SE, "MAKE", "MAKE-FG", "FG", "FG-WH", 0, 2, adjustment=1)
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["manufactures"], [{"manufacture": "MAKE", "work_order": "WO", "qty": 5,
                                                  "material": 42, "other": 6}])
        self.assertEqual((result["totals"]["material"], result["totals"]["other"]), (42, 6))
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql,
                       self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_manufacturing_posting_and_cancel_recomputes_exact_source_set(self):
        first = self.manufacture(other=6)
        second = self.manufacture("MAKE2", material=20, other=2, qty=2, raw_qty=2)
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["other"]), (60, 8))
        second.docstatus = 2
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["other"]), (40, 6))
        self.assertEqual([row["manufacture"] for row in result["manufactures"]], ["MAKE"])
        first.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["manufactures"], [])

    def test_native_manufacture_loss_enters_exact_stock_batch_and_cost_denominator(self):
        doc = self.manufacture(qty=4)
        doc.fg_completed_qty = 5
        namespace = {"defaultdict": defaultdict, "flt": self.module.flt, "frappe": self.frappe,
                     "_": lambda value: value}
        self.frappe.get_precision = lambda doctype, field: self.precisions.get((doctype, field), 6)
        self.frappe.bold = str
        exec(_NATIVE_MANUFACTURE_COVERAGE, namespace)
        native_doc = self.get_doc(self.module.SE, doc.name)
        namespace["validate_fg_completed_qty"](native_doc)
        doc.process_loss_qty = native_doc.process_loss_qty
        self.assertEqual(doc.process_loss_qty, 1)
        result = self.module.posted_flow_costs("FLOW")
        expected = [{"manufacture": "MAKE", "work_order": "WO", "company": "JF", "item_code": "FG",
                     "stock_uom": "Nos", "qty": 5, "inventory_qty": 4, "lost_qty": 1}]
        self.assertEqual(result["manufacture_batches"], expected)
        self.assertEqual(self.module.manufacture_batch_candidates("FLOW"), expected)
        from jifeng_erp.costing import calculate
        cost = calculate(5, result["totals"]["material"], 0, 0, [{
            "receipt_type": "首次回货", "received_qty": 5, "good_qty": 4,
            "lost_qty": 1, "rework_qty": 0, "pending_qty": 0, "extra_processing_amount": 0}])
        self.assertEqual((cost["estimated_unit_cost"], cost["allocated_good_cost"]), ("10", "40"))

    def test_manufacture_split_same_fg_warehouses_keeps_one_parent_loss(self):
        doc = self.manufacture(qty=4)
        doc.update(fg_completed_qty=5, process_loss_qty=1)
        fg = self.documents["Stock Entry Detail", "MAKE-FG"]
        fg.update(qty=2, transfer_qty=2)
        self.documents[self.module.SLE, "MAKE-FG-SLE"].update(actual_qty=2, stock_value_difference=20)
        self.child(self.module.SE, "MAKE", "items", "MAKE-FG2", item_code="FG", jf_flow_order="FLOW",
            qty=2, uom="Nos", stock_uom="Nos", conversion_factor=1, transfer_qty=2,
            s_warehouse=None, t_warehouse="REJECT-WH", is_finished_item=1,
            secondary_item_type=None, valuation_type=None, additional_cost=0, landed_cost_voucher_amount=0)
        self.ledger("MAKE-FG2-SLE", self.module.SE, "MAKE", "MAKE-FG2", "FG", "REJECT-WH", 2, 20)
        self.assertEqual([(row["qty"], row["inventory_qty"], row["lost_qty"])
                          for row in self.module.manufacture_batch_candidates("FLOW")], [(5, 4, 1)])
        self.documents[self.module.SLE, "MAKE-FG2-SLE"].actual_qty = 1
        with self.assertRaises(ValueError): self.module.manufacture_batch_candidates("FLOW")

    def test_manufacture_batch_cancel_removes_only_its_source_and_confirmed_loss(self):
        first = self.manufacture(qty=4)
        first.update(fg_completed_qty=5, process_loss_qty=1)
        second = self.manufacture("MAKE2", qty=2, material=20)
        self.assertEqual([row["manufacture"] for row in self.module.manufacture_batch_candidates("FLOW")],
                         ["MAKE", "MAKE2"])
        first.docstatus = 2
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((len(result["manufacture_batches"]), result["manufacture_batches"][0]["lost_qty"],
                          result["totals"]["material"]), (1, 0, 20))
        second.docstatus = 2
        self.assertEqual(self.module.manufacture_batch_candidates("FLOW"), [])

    def test_manufacture_nonstock_loss_refuses_but_zero_loss_converted_money_stays_valid(self):
        doc = self.manufacture(qty=5)
        fg = self.documents["Stock Entry Detail", "MAKE-FG"]
        fg.update(qty=Decimal("1.25"), uom="Box", conversion_factor=4)
        doc.fg_completed_qty = Decimal("1.25")
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["manufacture_batches"][0]["qty"]), (40, 5))
        doc.process_loss_qty = 1
        with self.assertRaisesRegex(ValueError, "单位口径"): self.module.posted_flow_costs("FLOW")
        fg.update(qty=5, uom="Nos", conversion_factor=1)
        doc.update(from_bom=0, fg_completed_qty=0)
        with self.assertRaisesRegex(ValueError, "单位口径"): self.module.manufacture_batch_candidates("FLOW")

    def test_manufacture_quantity_uses_native_precision_and_rejects_missing_or_negative_loss(self):
        doc = self.manufacture(qty=4)
        doc.update(fg_completed_qty=Decimal("5.0000004"), process_loss_qty=1)
        self.precisions[self.module.SE, "fg_completed_qty"] = 3
        self.assertEqual(self.module.manufacture_batch_candidates("FLOW")[0]["qty"], 5)
        self.precisions[self.module.SE, "fg_completed_qty"] = 9
        with self.assertRaisesRegex(ValueError, "覆盖数量"): self.module.manufacture_batch_candidates("FLOW")
        doc.fg_completed_qty = 5
        for value in (None, -1):
            doc.process_loss_qty = value
            with self.subTest(loss=value), self.assertRaises(ValueError):
                self.module.manufacture_batch_candidates("FLOW")

    def test_manufacture_quantity_fields_permissions_masks_and_changes_are_closed(self):
        doc = self.manufacture(qty=4)
        doc.update(fg_completed_qty=5, process_loss_qty=1)
        for field in self.module.MANUFACTURE_QUANTITY_FIELDS:
            self.omitted[self.module.SE] = {field}
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.module.manufacture_batch_candidates("FLOW")
        self.omitted.clear()
        self.metas[self.module.SE]["process_loss_qty"].mask = 1
        with self.assertRaises(PermissionError): self.module.posted_flow_costs("FLOW")
        self.metas[self.module.SE]["process_loss_qty"].mask = 0
        count = 0
        def change(filters):
            nonlocal count
            count += 1
            if count == 2: doc.process_loss_qty = 0
        self.list_hooks["Company"] = change
        with self.assertRaises((PermissionError, ValueError)):
            self.module.manufacture_batch_candidates("FLOW")

    def test_intermediate_manufacture_loss_is_not_added_to_final_item_batches(self):
        cast, _receipt, _row = self.stage_chain()
        cast.update(fg_completed_qty=12, process_loss_qty=2)
        self.assertEqual(self.module.manufacture_batch_candidates("FLOW"), [])
        self.assertEqual(self.module.posted_flow_costs("FLOW")["manufacture_batches"], [])

    def test_exact_accrued_wages_add_processing_once_and_cancel_leaves_material(self):
        self.manufacture(material=40, other=0)
        self.wage_cost_result.update(rows=[{"wage_entry": "WAGE", "journal_entry": "PAYROLL-JE",
                                            "work_order": "WO", "job_card": "CARD", "amount": Decimal(20)}], total=Decimal(20))
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["processing"]), (40, 20))
        self.assertEqual(result["wages"][0]["journal_entry"], "PAYROLL-JE")
        self.wage_cost_result["finish_guard"].assert_called_once()
        self.wage_cost_result.update(rows=[], total=Decimal(0))
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["processing"], result["wages"]), (40, 0, []))

    def test_unidentified_manufacturing_labor_never_doubles_an_accrued_wage(self):
        self.manufacture(other=6)
        self.wage_cost_result.update(rows=[{"work_order": "WO", "job_card": "CARD", "amount": Decimal(20)}], total=Decimal(20))
        with self.assertRaisesRegex(ValueError, "避免重复"):
            self.module.posted_flow_costs("FLOW")
        self.wage_cost_result["rows"][0].update(work_order=None, job_card=None)
        with self.assertRaisesRegex(ValueError, "避免重复"):
            self.module.posted_flow_costs("FLOW")

    def test_final_wage_permission_hook_cannot_change_earlier_inventory_source(self):
        source = self.manufacture(other=0)
        self.wage_cost_result["finish_guard"].side_effect = lambda: source.update(docstatus=2)
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")

    def test_manufacturing_uses_persisted_company_currency_fee_not_current_conversion(self):
        self.manufacture(other=6)
        self.add("Currency", "USD")
        charge = self.documents["Landed Cost Taxes and Charges", "MAKE-COST"]
        charge.amount, charge.account_currency, charge.exchange_rate = 1, "USD", 6
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["other"], 6)
        for field, value in (("exchange_rate", 0), ("exchange_rate", 7), ("amount", 2), ("base_amount", 5)):
            original = charge[field]
            charge[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.module.posted_flow_costs("FLOW")
            charge[field] = original
        charge.account_currency = "CNY"
        with self.assertRaises(ValueError):
            self.module.posted_flow_costs("FLOW")
        charge.account_currency = "USD"
        self.documents[self.module.SE, "MAKE"].total_additional_costs = 7
        with self.assertRaises(ValueError):
            self.module.posted_flow_costs("FLOW")

    def test_manufacturing_negative_consumption_or_unmatched_fg_valuation_refuses_simplification(self):
        self.manufacture(other=6)
        raw = self.documents[self.module.SLE, "MAKE-RM-SLE"]
        fg = self.documents[self.module.SLE, "MAKE-FG-SLE"]
        for doc, value, message in ((raw, 40, "净成本为负"), (fg, 45, "有效库存账")):
            original = doc.stock_value_difference
            doc.stock_value_difference = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                self.module.posted_flow_costs("FLOW")
            doc.stock_value_difference = original
        self.ledger("RAW-ONLY-ADJUST", self.module.SE, "MAKE", "MAKE-RM", "RM", "RM-WH", 0, -2, adjustment=1)
        with self.assertRaisesRegex(ValueError, "有效库存账"):
            self.module.posted_flow_costs("FLOW")

    def test_manufacturing_stage_secondary_and_lcv_sources_are_explicitly_rejected(self):
        self.manufacture()
        wo = self.documents[self.module.WO, "WO"]
        fg = self.documents["Stock Entry Detail", "MAKE-FG"]
        for doc, field, value in ((wo, "track_semi_finished_goods", 1),
                                  (fg, "secondary_item_type", "By-Product"),
                                  (fg, "valuation_type", "Manual"),
                                  (fg, "landed_cost_voucher_amount", 5)):
            original = doc[field]
            doc[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "半成品|副产品"):
                self.module.posted_flow_costs("FLOW")
            doc[field] = original

    def test_manufacturing_source_permissions_mask_and_child_projection_are_required(self):
        self.manufacture(other=6)
        for doctype, name in ((self.module.WO, "WO"), (self.module.SE, "MAKE"), (self.module.SLE, "MAKE-RM-SLE")):
            self.denied.add((doctype, name, "read"))
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.module.posted_flow_costs("FLOW")
            self.denied.clear()
        for doctype, field in ((self.module.WO, "jf_flow_order"), ("Stock Entry Detail", "is_finished_item"),
                              ("Landed Cost Taxes and Charges", "base_amount")):
            self.omitted[doctype] = {field}
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self.module.posted_flow_costs("FLOW")
            self.omitted.clear()
        self.metas["Landed Cost Taxes and Charges"]["base_amount"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")

    def test_no_manufacturing_does_not_add_work_order_or_money_permissions_to_old_rpcs(self):
        self.receipt()
        self.issue()
        self.role_denials.add((self.module.WO, "read"))
        self.hidden[self.module.SE] = {"total_additional_costs"}
        self.hidden["Stock Entry Detail"] = {"additional_cost"}
        self.assertEqual(self.module.material_issue_costs("FLOW")["totals"]["material"], 80)
        self.assertEqual(self.module.receipt_costs("FLOW")["totals"]["material"], 100)
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 180)

    def test_manufacturing_wrong_flow_company_unit_and_ledger_never_gives_partial_cost(self):
        self.manufacture()
        wo = self.documents[self.module.WO, "WO"]
        raw = self.documents["Stock Entry Detail", "MAKE-RM"]
        fg_sle = self.documents[self.module.SLE, "MAKE-FG-SLE"]
        for doc, field, value in ((wo, "company", "OTHER"), (wo, "production_item", "RM"),
                                  (wo, "jf_flow_order", "OTHER"), (raw, "stock_uom", "Nos"),
                                  (raw, "jf_flow_order", "OTHER"), (raw, "transfer_qty", 3),
                                  (fg_sle, "actual_qty", 4)):
            original = doc[field]
            doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.module.posted_flow_costs("FLOW")
            doc[field] = original

    def test_late_manufacturing_cancel_fee_change_or_repost_refuses_stale_result(self):
        doc = self.manufacture(other=6)
        for kind in ("cancel", "fee", "repost"):
            count = 0
            def change_at_final_read(filters):
                nonlocal count
                count += 1
                if count == 2:
                    if kind == "cancel":
                        doc.docstatus = 2
                    elif kind == "fee":
                        self.documents["Landed Cost Taxes and Charges", "MAKE-COST"].base_amount = 7
                    else:
                        self.add(self.module.REPOST, "JOB", company="JF", based_on="Item and Warehouse", status="Queued",
                                 item_code="RM", warehouse="RM-WH", posting_date=date(2026, 10, 1))
            self.list_hooks["Company"] = change_at_final_read
            with self.subTest(kind=kind), self.assertRaises((PermissionError, ValueError)):
                self.module.posted_flow_costs("FLOW")
            doc.docstatus = 1
            self.documents["Landed Cost Taxes and Charges", "MAKE-COST"].base_amount = 6
            self.documents.pop((self.module.REPOST, "JOB"), None)

    def test_stage_raw_material_and_native_fees_are_counted_once_across_batches(self):
        self.stage_chain()
        first = self.module.posted_flow_costs("FLOW")
        self.assertEqual((first["totals"]["material"], first["totals"]["processing"], first["totals"]["other"]), (40, 12, 2))
        self.assertEqual(first["receipts"][0]["material"], 0)
        self.assertEqual(sum(first["totals"][field] for field in ("material", "processing", "other")), 54)
        self.stage_receipt("PLATE2", qty=6)
        second = self.module.posted_flow_costs("FLOW")
        self.assertEqual((second["totals"]["material"], second["totals"]["processing"], second["totals"]["other"]), (40, 30, 2))
        self.assertEqual({row["receipt"] for row in second["receipts"]}, {"PLATE", "PLATE2"})
        self.assertEqual(second["manufactures"][0]["material"], 40)
        self.stage_receipt("RETURN", qty=-2, is_return=1, original="PLATE", flow=None)
        returned = self.module.posted_flow_costs("FLOW")
        self.assertEqual((returned["totals"]["material"], returned["totals"]["processing"], returned["totals"]["other"]), (40, 24, 2))
        self.assertEqual(sum(returned["totals"][field] for field in ("material", "processing", "other")), 66)
        candidates = self.module.receipt_batch_candidates("FLOW")
        self.assertEqual([(row["receipt"], row["qty"]) for row in candidates], [("PLATE", 2), ("PLATE2", 6)])
        for writer in (self.frappe.db.commit, self.frappe.db.set_value, self.frappe.db.sql,
                       self.frappe.new_doc, self.frappe.delete_doc):
            writer.assert_not_called()

    def test_stage_return_and_reverse_cancel_recompute_net_fees_without_readding_internal_stock(self):
        cast, plate, _row = self.stage_chain()
        returned, _ = self.stage_receipt("RETURN", qty=-2, is_return=1, original="PLATE", flow=None)
        net = self.module.posted_flow_costs("FLOW")
        self.assertEqual((net["totals"]["material"], net["totals"]["processing"], net["totals"]["other"]), (40, 6, 2))
        returned.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["processing"], 12)
        plate.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["processing"], 0)
        cast.docstatus = 2
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 0)

    def test_stage_cancelled_missing_or_overconsumed_upstream_cannot_borrow_same_item_stock(self):
        cast, _plate, _row = self.stage_chain()
        cast.docstatus = 2
        with self.assertRaisesRegex(ValueError, "前序半成品缺少"):
            self.module.posted_flow_costs("FLOW")
        cast.docstatus = 1
        self.stage_receipt("PLATE2", qty=7)
        with self.assertRaisesRegex(ValueError, "净耗用超过"):
            self.module.posted_flow_costs("FLOW")

    def test_stage_exact_job_card_operation_bom_purchase_and_flow_keys_are_required(self):
        self.stage_chain()
        for key, field, value in (
            (("Job Card", "PLATE-JC"), "operation_id", "CAST-OP"),
            (("Job Card", "PLATE-JC"), "semi_fg_bom", "CAST-BOM"),
            ((self.module.WO, "STAGE-WO"), "jf_flow_order", "OTHER"),
            (("Work Order Operation", "CAST-OP"), "sequence_id", 2),
            (("Subcontracting Order Item", "SCO-STAGE"), "purchase_order_item", "OTHER"),
            (("Subcontracting Receipt Item", "PLATE-FG"), "subcontracting_order_item", "OTHER"),
            (("BOM", "CAST-BOM"), "company", "OTHER"),
        ):
            document, old = self.documents[key], self.documents[key].get(field)
            document[field] = value
            with self.subTest(field=field, key=key), self.assertRaises((ValueError, PermissionError)):
                self.module.posted_flow_costs("FLOW")
            document[field] = old
        self.child(self.module.WO, "STAGE-WO", "operations", "DUPLICATE-OP", operation="OTHER",
                   finished_good="SEMI", is_subcontracted=0, sequence_id=1)
        with self.assertRaisesRegex(ValueError, "唯一"):
            self.module.posted_flow_costs("FLOW")

    def test_stage_mixed_value_partial_batches_refuse_but_complete_native_value_can_close(self):
        self.stage_chain()
        second = self.manufacture("CAST2", work="STAGE-WO", material=60, other=2, qty=10)
        second.job_card, second.bom_no = "CAST-JC", "CAST-BOM"
        self.documents["Stock Entry Detail", "CAST2-FG"].item_code = "SEMI"
        self.documents[self.module.SLE, "CAST2-FG-SLE"].item_code = "SEMI"
        with self.assertRaisesRegex(ValueError, "分批半成品估值不同"):
            self.module.posted_flow_costs("FLOW")
        # The source is a complete native consumption of both batches, whose
        # aggregate value is already persisted. The service computes no average.
        self.documents[self.module.SCR, "PLATE"].docstatus = 2
        self.stage_receipt("ALL", qty=20, rate=Decimal("5.2"))
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["processing"], result["totals"]["other"]), (100, 60, 4))

    def test_stage_source_sle_value_and_posting_order_cannot_be_replaced_by_current_bom_price(self):
        cast, _plate, _row = self.stage_chain()
        supplied = self.documents["Subcontracting Receipt Supplied Item", "PLATE-RM"]
        supplied.amount += 1
        with self.assertRaises(ValueError):
            self.module.posted_flow_costs("FLOW")
        supplied.amount -= 1
        # 9 sorts after 10 as a timedelta string, but is earlier as native time.
        cast.posting_time = timedelta(hours=9)
        for (kind, _), row in self.documents.items():
            if kind == self.module.SLE and row.voucher_no == cast.name:
                row.posting_time = cast.posting_time
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["material"], 40)
        cast.posting_time = timedelta(hours=11)
        for (kind, _), row in self.documents.items():
            if kind == self.module.SLE and row.voucher_no == cast.name:
                row.posting_time = cast.posting_time
        with self.assertRaisesRegex(ValueError, "产出之前"):
            self.module.posted_flow_costs("FLOW")

    def test_stage_loss_cost_is_native_and_secondary_or_lcv_stays_rejected(self):
        self.stage_chain(qty=3, loss=1)
        self.assertEqual(self.module.posted_flow_costs("FLOW")["totals"]["processing"], 9)
        for field, value in (("secondary_item_type", "By-Product"), ("landed_cost_voucher_amount", 1)):
            row = self.documents["Subcontracting Receipt Item", "PLATE-FG"]
            old = row[field]
            row[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "副产品"):
                self.module.posted_flow_costs("FLOW")
            row[field] = old

    def test_stage_missing_permission_hidden_child_or_late_edit_rejects_complete_result(self):
        self.stage_chain()
        self.denied.add(("Job Card", "PLATE-JC", "read"))
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.denied.clear()
        self.list_denied.add(("Work Order Operation", "CAST-OP"))
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.list_denied.clear()
        self.metas["Job Card"]["semi_fg_bom"].mask = 1
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")
        self.metas["Job Card"]["semi_fg_bom"].mask = 0
        count = 0
        def change_at_final_read(filters):
            nonlocal count
            count += 1
            if count == 2:
                self.documents["Job Card", "PLATE-JC"].sequence_id = 3
        self.list_hooks["Company"] = change_at_final_read
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")

    def test_receipt_only_stage_does_not_silently_switch_to_posted_manufacturing_basis(self):
        self.stage_chain()
        with self.assertRaisesRegex(ValueError, "带入流程已过账"):
            self.module.receipt_costs("FLOW")
        self.documents["Subcontracting Receipt Item", "PLATE-FG"].job_card = None
        with self.assertRaisesRegex(ValueError, "耗用缺少准确"):
            self.module.posted_flow_costs("FLOW")

    def test_stage_final_quantity_does_not_acquire_manufacturing_money_permissions_or_guess_cost(self):
        self.stage_chain()
        self.hidden[self.module.SE] = {"total_additional_costs"}
        self.hidden["Stock Entry Detail"] = {"additional_cost"}
        self.documents[self.module.SLE, "CAST-FG-SLE"].stock_value_difference = 99
        self.assertEqual(self.module.receipt_batch_candidates("FLOW")[0]["qty"], 4)
        with self.assertRaises(PermissionError):
            self.module.posted_flow_costs("FLOW")

    def test_stage_additional_external_raw_consumption_remains_material_not_internal_credit(self):
        _cast, _receipt, row = self.stage_chain()
        self.child("BOM", "PLATE-BOM", "items", "EXTRA-RAW", item_code="RM")
        supplied = self.child(self.module.SCR, "PLATE", "supplied_items", "PLATE-EXTRA", main_item_code="FG",
            rm_item_code="RM", reference_name=row.name, consumed_qty=1, stock_uom="Kg", rate=10, amount=10)
        row.update(rm_supp_cost=Decimal("26.8"), rm_cost_per_qty=Decimal("6.7"), amount=Decimal("38.8"))
        self.ledger("PLATE-EXTRA-SLE", self.module.SCR, "PLATE", supplied.name, "RM", "RM-WH", -1, -10)
        self.documents[self.module.SLE, "PLATE-FG-SLE"].stock_value_difference = Decimal("38.8")
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["processing"], result["totals"]["other"]), (50, 12, 2))
        self.assertEqual(result["receipts"][0]["material"], 10)

    def test_outsourced_semi_product_then_native_final_manufacture_does_not_double_value(self):
        cast, receipt, row = self.stage_chain(qty=10)
        cast.docstatus = 2
        for suffix, outsourced in (("CAST", 1), ("PLATE", 0)):
            self.documents["Work Order Operation", suffix + "-OP"].is_subcontracted = outsourced
            self.documents["Job Card", suffix + "-JC"].is_subcontracted = outsourced
        self.documents["Purchase Order Item", "PO-STAGE"].update(fg_item="SEMI", job_card="CAST-JC", bom="CAST-BOM")
        self.documents["Subcontracting Order Item", "SCO-STAGE"].update(item_code="SEMI", job_card="CAST-JC", bom="CAST-BOM")
        row.update(item_code="SEMI", job_card="CAST-JC", bom="CAST-BOM", rm_cost_per_qty=4,
                   rm_supp_cost=40, service_cost_per_qty=1, amount=50)
        self.documents["Subcontracting Receipt Supplied Item", "PLATE-RM"].update(main_item_code="SEMI", rm_item_code="RM",
                   consumed_qty=4, stock_uom="Kg", rate=10, amount=40)
        self.documents[self.module.SLE, "PLATE-RM-SLE"].update(item_code="RM", stock_uom="Kg", actual_qty=-4,
                   stock_value_difference=-40)
        self.documents[self.module.SLE, "PLATE-FG-SLE"].update(item_code="SEMI", stock_value_difference=50)
        final = self.manufacture("FINAL", work="STAGE-WO", material=50, other=1, qty=10, raw_qty=10)
        final.job_card, final.bom_no = "PLATE-JC", "PLATE-BOM"
        self.documents["Stock Entry Detail", "FINAL-RM"].update(item_code="SEMI", uom="Nos", stock_uom="Nos")
        self.documents[self.module.SLE, "FINAL-RM-SLE"].update(item_code="SEMI", stock_uom="Nos")
        final.posting_time = timedelta(hours=11)
        for (kind, _), ledger in self.documents.items():
            if kind == self.module.SLE and ledger.voucher_no == "FINAL":
                ledger.posting_time = final.posting_time
        result = self.module.posted_flow_costs("FLOW")
        self.assertEqual((result["totals"]["material"], result["totals"]["processing"], result["totals"]["other"]), (40, 10, 1))
        self.assertEqual(result["manufactures"][0]["material"], 0)
        self.assertEqual(self.module.receipt_batch_candidates("FLOW"), [])


if __name__ == "__main__":
    unittest.main()
