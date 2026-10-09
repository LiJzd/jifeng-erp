"""Complete, permission checked native sources for one flow's explicit cost load.

Raw reads are internal completeness guards, never a replacement for native
document/field access. A missing source rejects the whole result. Two serial
passes detect request-local changes; they do not promise a concurrent snapshot.
This service writes nothing. Material covers accepted, rejected and loss;
booked fees use accepted quantity. Returns preserve exact original physical
stock value separately from restored material and unit fees. Secondary items,
LCV and unproved allocation still reject the simplified fee load. The three fee
categories are not a claim of native stock valuation or GL totals.
"""

from datetime import timedelta, time
from decimal import Decimal, InvalidOperation
import json

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import flt

from jifeng_erp.costing import CostingError, receipt_batch_quantities
from jifeng_erp import subcontract_stage
from jifeng_erp import subcontracting_returns as material_returns
from jifeng_erp.flow_cost_scoped_query import record_list


FLOW = "Jifeng Flow Order"
SCR = "Subcontracting Receipt"
SE = "Stock Entry"
WO = "Work Order"
SLE = "Stock Ledger Entry"
GLE = "GL Entry"
REPOST = "Repost Item Valuation"
IDENTITY = {"name", "modified", "docstatus"}
CHILD = {"name", "modified", "parent", "parenttype", "parentfield", "idx"}
PARENTS = {
    FLOW: IDENTITY | {"company", "item_code"},
    SCR: IDENTITY | {"company", "posting_date", "posting_time", "is_return", "return_against",
                     "status", "supplier_warehouse", "cost_center"},
    SE: IDENTITY | {"company", "posting_date", "posting_time", "purpose", "stock_entry_type", "jf_material_issue_flow"},
    SLE: IDENTITY | {"creation", "company", "item_code", "warehouse", "stock_uom", "posting_date", "posting_time",
                     "voucher_type", "voucher_no", "voucher_detail_no", "actual_qty",
                     "stock_value_difference", "is_cancelled", "is_adjustment_entry", "serial_and_batch_bundle",
                     "serial_no", "batch_no"},
    "Item": {"name", "modified", "stock_uom", "is_stock_item"},
    "Warehouse": {"name", "modified", "company", "is_group"},
    "Currency": {"name", "modified"},
    WO: IDENTITY | {"company", "production_item", "jf_flow_order", "track_semi_finished_goods"},
}
PARENTS[GLE] = material_returns.GL_FEE_FIELDS
PARENTS[SE] |= {"jf_material_return_against"}
PARENTS[material_returns.BUNDLE] = material_returns.BUNDLE_FIELDS
# These native sources are read only when an actual Job Card stage is present.
PARENTS.update({kind: fields for kind, fields in subcontract_stage.PARENTS.items() if kind not in PARENTS})

# These fields are needed only when reading a manufacturing source. Existing
# issue/receipt RPCs must not acquire new manufacturing money-field requirements.
MANUFACTURE_FIELDS = {"work_order", "job_card", "total_additional_costs"}
MANUFACTURE_QUANTITY_FIELDS = {"from_bom", "fg_completed_qty", "process_loss_qty"}
MANUFACTURE_ITEM_FIELDS = {"is_finished_item", "secondary_item_type", "valuation_type",
                           "additional_cost", "landed_cost_voucher_amount"}
LOSS_TYPE = "制造损耗出库"
LOSS_PREFIX = "jf_production_loss_"
LOSS_WORK_ORDER = LOSS_PREFIX + "work_order"
TABLES = {
    material_returns.BUNDLE: (("entries", material_returns.BUNDLE_ENTRY, material_returns.BUNDLE_ENTRY_FIELDS),),
    FLOW: (("materials", "Jifeng Material Line", CHILD | {"item_code"}),),
    SCR: (
        ("items", "Subcontracting Receipt Item", CHILD | {
            "item_code", "jf_flow_order", "qty", "rejected_qty", "process_loss_qty", "stock_uom",
            "conversion_factor", "warehouse", "rejected_warehouse", "subcontracting_receipt_item",
            "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty", "rm_supp_cost",
            "received_qty", "bom", "secondary_item_type", "valuation_type", "reference_name",
            "secondary_items_cost_per_qty", "landed_cost_voucher_amount", "rate", "amount",
            "expense_account", "service_expense_account", "cost_center", "project"}),
        ("supplied_items", "Subcontracting Receipt Supplied Item", CHILD | {
            "main_item_code", "rm_item_code", "reference_name", "consumed_qty", "stock_uom", "rate", "amount",
            "expense_account", "cost_center"}),
        ("additional_costs", "Landed Cost Taxes and Charges", CHILD | {
            "amount", "account_currency", "exchange_rate", "base_amount", "expense_account"}),
    ),
    SE: (("items", "Stock Entry Detail", CHILD | {
        "item_code", "jf_flow_order", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
        "s_warehouse", "t_warehouse", "jf_material_return_against_detail"}),),
}
TABLES.update({kind: (table,) for kind, table in subcontract_stage.TABLES.items() if kind not in TABLES and kind != WO})
MANUFACTURE_CHARGES = TABLES[SCR][2]
STAGE_SEQUENCE_FIELDS = {"sequence_id"}


def _deny():
    frappe.throw("无法完整读取流程单的成本来源、退货、库存账或字段，不能返回部分成本", frappe.PermissionError)


def _invalid():
    frappe.throw("流程成本的原生来源链、单位或库存账不完整或范围不一致，请先核对原生单据")


def _freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return type(value).__name__, repr(value)


def _unmasked(doctype, fields, parenttype=None):
    if frappe.session.user == "Administrator":
        return
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype, user=frappe.session.user))
    if any(df and df.get("mask") and (df.permlevel or 0) not in levels
           for df in (meta.get_field(field) for field in fields)):
        _deny()


def _fields(doctype, fields, parenttype=None):
    columns = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type="read", ignore_virtual=True))
    if not set(fields).issubset(columns):
        _deny()
    _unmasked(doctype, fields, parenttype)


def _table(parenttype, fieldname, childtype):
    meta = frappe.get_meta(parenttype)
    df = meta.get_field(fieldname)
    levels = meta.get_permlevel_access(permission_type="read", user=frappe.session.user)
    if (not df or df.fieldtype != "Table" or df.options != childtype
            or (df.permlevel or 0) not in levels):
        _deny()
    # Native get_permitted_fields excludes Table (NO_VALUE_FIELDS); its
    # permission lives on the actual Table DocField, not a database column.
    _unmasked(parenttype, {fieldname})


def _number(value):
    if isinstance(value, bool):
        _invalid()
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError, TypeError):
        _invalid()
    if not result.is_finite():
        _invalid()
    return result


def _same_quantity(actual, expected, doc, field):
    # Use the installed native field's precision and native rounding, including
    # System Settings, instead of inventing a fixed six-place tolerance.
    precision = doc.precision(field)
    if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 9:
        _invalid()
    try:
        left, right = flt(actual, precision), flt(expected, precision)
    except (ValueError, OverflowError):
        _invalid()
    if not Decimal(str(left)).is_finite() or not Decimal(str(right)).is_finite():
        _invalid()
    return left == right


def _stock_time(value):
    # mysqlclient returns a timedelta; documents may use time or native strings.
    if isinstance(value, timedelta):
        seconds = Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / 1000000
    elif isinstance(value, time):
        seconds = Decimal(value.hour * 3600 + value.minute * 60 + value.second) + Decimal(value.microsecond) / 1000000
    else:
        parts = str(value).split(":")
        if len(parts) != 3:
            _invalid()
        hours, minutes, second = map(_number, parts)
        if not (0 <= hours < 24 and 0 <= minutes < 60 and 0 <= second < 60):
            _invalid()
        seconds = hours * 3600 + minutes * 60 + second
    if not 0 <= seconds < 86400:
        _invalid()
    return seconds


def _authorization_doctypes():
    return (set(PARENTS) | {child for tables in TABLES.values() for _, child, _ in tables}
            | {child for _, child, _ in subcontract_stage.TABLES.values()} | {"Company", REPOST})


def _authorization(doctypes=None):
    user = frappe.session.user
    doctypes = sorted(_authorization_doctypes() if doctypes is None else set(doctypes))
    state = []
    for doctype, filters in (
        ("Has Role", {"parenttype": "User", "parent": user}),
        ("User Permission", {"user": user}),
        ("DocPerm", {"parent": ["in", doctypes]}),
        ("Custom DocPerm", {"parent": ["in", doctypes]}),
        ("DocType", {"name": ["in", doctypes]}),
        ("DocField", {"parent": ["in", doctypes]}),
        ("Custom Field", {"dt": ["in", doctypes]}),
        ("Property Setter", {"doc_type": ["in", doctypes]}),
        ("DocShare", {"share_doctype": ["in", doctypes]}),
    ):
        rows = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc", limit_page_length=0)
        if doctype == "DocShare":
            rows = [row for row in rows if row.get("user") == user or row.get("everyone")]
        state.append((doctype, _freeze(rows)))
    state.append(("User", _freeze(frappe.get_all("User", filters={"name": user},
        fields=["name", "enabled", "user_type"], order_by="name asc", limit_page_length=0))))
    return user, tuple(state)


class _Session:
    def __init__(self, flow_name, *, include_stages=False, stage_quantities_only=False):
        if frappe.session.user == "Guest":
            _deny()
        if frappe.conf.get("read_from_replica"):
            frappe.throw("流程成本来源需要主库口径，请在主库核对后查询")
        if not isinstance(flow_name, str) or not flow_name or flow_name != flow_name.strip():
            frappe.throw("请选择明确的流程单")
        self.queries, self.documents, self.children = [], {}, {}
        self.include_stages = include_stages
        self.stage_quantities_only = stage_quantities_only
        self.extra_tables, self.receipt_stages, self.stage_events = {}, {}, []
        self.authorization = _authorization()
        self.company = None
        self.flow = self.source(FLOW, flow_name)
        self.company = self.flow.get("company")
        if not isinstance(self.company, str) or not self.company or self.company != self.company.strip():
            _invalid()
        self.company_doc = self._company()
        self.currency = self.company_doc.get("default_currency")
        if not self.currency:
            frappe.throw("请先配置公司本币")
        self.source("Currency", self.currency).check_permission("select")
        self.fg = self.source("Item", self.flow.get("item_code"))
        if not self.fg.get("stock_uom"):
            _invalid()
        self.allowed_items = {self.fg.name}
        for row in self.table(FLOW, flow_name, "materials"):
            self.allowed_items.add(self.source("Item", row.get("item_code")).name)
        self.receipts = None
        self.issues = None
        self.manufactures = None
        self.manufacture_batches = None
        self.manufacture_names = set()
        self.loss_batches = None
        self.loss_tables = {}
        self.loss_authorization = {}
        self.ledger = {}
        self.repost_state = None
        self.receipt_fees = {}
        self.receipt_gl_sources = {}
        self.receipt_gl_proofs = {}
        self.receipt_dimensions = None
        self.receipt_allocation_queries = set()
        self.receipt_lot_usage, self.receipt_fg_lot_usage, self.receipt_tracking_tables = {}, {}, {}

    def _receipt_dimensions(self):
        if self.receipt_dimensions is None:
            self.receipt_dimensions = tuple(sorted(material_returns.receipt_accounting_dimensions()))
            # Native accounting configuration changes the proof's grouping.
            # Track it only when an actual receipt is read, never add a new
            # accounting permission to ordinary issue/stock-only queries.
            self.raw("Accounting Dimension", {"disabled": 0})
        return set(self.receipt_dimensions)

    def _parent_fields(self, doctype):
        fields = set(PARENTS[doctype])
        if doctype == SCR:
            meta = frappe.get_meta(SCR)
            fields |= {field for field in self._receipt_dimensions() | {"project", "finance_book"}
                       if meta.get_field(field)}
        return fields

    def _child_fields(self, parenttype, childtype, fields):
        fields = set(fields)
        if parenttype == SCR and childtype == "Subcontracting Receipt Item":
            meta = frappe.get_meta(childtype)
            fields |= {field for field in self._receipt_dimensions() if meta.get_field(field)}
        return fields

    def _receipt_fee_ledger(self, source):
        key = (source.doctype, source.name)
        if key not in self.receipt_gl_sources:
            fields = material_returns.receipt_gl_proof_fields()
            self.receipt_gl_sources[key] = self.records(GLE, {"company": self.company,
                "voucher_type": SCR, "voucher_no": source.name, "is_cancelled": 0}, fields)
        return self.receipt_gl_sources[key]

    def _receipt_fee_proof(self, source, *, for_return=False):
        key = (source.doctype, source.name, for_return)
        if key not in self.receipt_gl_proofs:
            self._receipt_allocation_state(source)
            original_proof = None
            if source.get("is_return"):
                original = self.source(SCR, source.get("return_against"))
                original_proof = self._receipt_fee_proof(original, for_return=True)
            self.receipt_gl_proofs[key] = material_returns._prove_receipt_fees(source,
                ledger_reader=self._receipt_fee_ledger, for_return=for_return, _source_proof=original_proof)
        return self.receipt_gl_proofs[key]

    def _receipt_allocation_state(self, source):
        # Native allocation lookup is request-cached. Recomputing GL at the
        # end could therefore reuse a stale percentage or ignore a newer
        # effective allocation. Record the same narrow configuration scope
        # independently, including its empty result and selected children.
        centers = {row.get("cost_center") for table in ("items", "supplied_items")
                   for row in source.get(table) or [] if row.get("cost_center")}
        header = source.get("cost_center") or source.get_company_default("cost_center")
        if header:
            centers.add(header)
        key = (tuple(sorted(centers)), source.posting_date)
        if key in self.receipt_allocation_queries:
            return
        self.receipt_allocation_queries.add(key)
        parents = self.raw("Cost Center Allocation", {"docstatus": 1, "company": self.company,
            "main_cost_center": ["in", sorted(centers)], "valid_from": ["<=", source.posting_date]})
        latest = {}
        for row in parents:
            center = row["main_cost_center"]
            effective = row["valid_from"]
            if center not in latest or effective > latest[center]:
                latest[center] = effective
        # Include all equally dated latest candidates. Native date ordering
        # alone does not decide a tie; any such candidate could be selected.
        names = sorted(row["name"] for row in parents if row["valid_from"] == latest[row["main_cost_center"]])
        if names:
            self.raw("Cost Center Allocation Percentage", {"parent": ["in", names]})

    def raw(self, doctype, filters, **options):
        rows = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                              limit_page_length=0, **options)
        self.queries.append((doctype, filters, None, None, options, _freeze(rows), None))
        return rows

    def records(self, doctype, filters, fields, parenttype=None, **options):
        _fields(doctype, fields, parenttype)
        raw = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                             limit_page_length=0, **options)
        try:
            rows = record_list(doctype, filters=filters, fields=sorted(fields),
                               parenttype=parenttype, **options)
        except frappe.PermissionError:
            _deny()
        names = [row.get("name") for row in rows]
        raw_names = [row.get("name") for row in raw]
        if len(set(names)) != len(names) or len(set(raw_names)) != len(raw_names) or None in names + raw_names:
            _invalid()
        if set(names) != set(raw_names) or any(not set(fields).issubset(row) for row in rows):
            _deny()
        actual = {row["name"]: row for row in raw}
        if any(any(value != actual[row["name"]].get(field) for field, value in row.items()) for row in rows):
            _invalid()
        self.queries.append((doctype, filters, tuple(sorted(fields)), parenttype, options,
                             _freeze(raw), _freeze(rows)))
        return rows

    def source(self, doctype, name):
        key = (doctype, name)
        if key in self.documents:
            return self.documents[key][0]
        if not isinstance(name, str) or not name or doctype not in PARENTS:
            _invalid()
        fields = self._parent_fields(doctype)
        rows = self.records(doctype, {"name": name}, fields)
        if len(rows) != 1:
            _deny()
        try:
            doc = frappe.get_doc(doctype, name)
        except frappe.DoesNotExistError:
            _invalid()
        doc.check_permission("read")
        if any(doc.get(field) != value for field, value in rows[0].items()):
            _invalid()
        if self.company is not None and "company" in fields and doc.get("company") != self.company:
            _invalid()
        self.documents[key] = (doc, _freeze(doc), "read", fields)
        for fieldname, childtype, fields in TABLES.get(doctype, ()):
            fields = self._child_fields(doctype, childtype, fields)
            _table(doctype, fieldname, childtype)
            # parent-only raw scope also detects children with a corrupt
            # parenttype/parentfield instead of silently dropping them.
            children = self.records(childtype, {"parent": name}, fields, doctype)
            actual = {row.name: row for row in doc.get(fieldname) or []}
            if len(actual) != len(doc.get(fieldname) or []) or set(actual) != {row["name"] for row in children}:
                _invalid()
            for row in children:
                if (row.get("parenttype") != doctype or row.get("parentfield") != fieldname
                        or any(actual[row["name"]].get(field) != value for field, value in row.items())):
                    _invalid()
            self.children[doctype, name, fieldname] = children
        return doc

    def _company(self):
        # Company selection derives its business currency, as in the existing
        # supplier workbench; no Company profile is returned or read grant added.
        names = frappe.get_list("Company", filters={"name": self.company}, pluck="name", limit_page_length=0)
        if names != [self.company]:
            _deny()
        doc = frappe.get_doc("Company", self.company)
        doc.check_permission("select")
        _unmasked("Company", {"default_currency"})
        self.raw("Company", {"name": self.company})
        self.documents["Company", self.company] = (doc, _freeze(doc), "select", {"name", "default_currency"})
        return doc

    def table(self, doctype, name, fieldname):
        return self.children[doctype, name, fieldname]

    def _stage_reader(self, doctype, name):
        """Use the existing stage proof within this request's complete read graph."""
        doc = self.source(doctype, name)
        fields = PARENTS[doctype] | subcontract_stage.PARENTS.get(doctype, set())
        if doctype == subcontract_stage.JC:
            fields = fields | STAGE_SEQUENCE_FIELDS
        rows = self.records(doctype, {"name": name}, fields)
        if len(rows) != 1 or any(doc.get(key) != value for key, value in rows[0].items()):
            _invalid()
        self.documents[doctype, name] = (doc, _freeze(doc), "read", fields)
        if doctype in subcontract_stage.TABLES:
            field, childtype, fields = subcontract_stage.TABLES[doctype]
            for native_field, _child, native_fields in TABLES.get(doctype, ()):
                if native_field == field:
                    fields = fields | native_fields
            if doctype == WO:
                fields = fields | STAGE_SEQUENCE_FIELDS
            _table(doctype, field, childtype)
            children = self.records(childtype, {"parent": name}, fields, doctype)
            actual = {row.name: row for row in doc.get(field) or []}
            if len(actual) != len(doc.get(field) or []) or set(actual) != {row["name"] for row in children}:
                _invalid()
            for row in children:
                if (row.get("parenttype") != doctype or row.get("parentfield") != field
                        or any(actual[row["name"]].get(key) != value for key, value in row.items())):
                    _invalid()
            self.children[doctype, name, field] = children
            self.extra_tables[doctype, name] = (field, childtype, fields)
        return doc

    def _stage_node(self, stage):
        work, card = stage["work_order"], stage["job_card"]
        operation = subcontract_stage.detail(work, card.get("operation_id"), "operations")
        if len([row for row in work.get("operations") or [] if row.get("finished_good") == stage["item"]]) != 1:
            frappe.throw("阶段商品没有唯一的同工单产出工序，不能推测半成品或最终批次来源")
        sequence = _number(operation.get("sequence_id"))
        if (operation.get("sequence_id") in (None, "") or sequence <= 0
                or _number(card.get("sequence_id")) != sequence):
            frappe.throw("阶段工序顺序缺失或与原工单不一致，不能推测半成品成本来源")
        return work.name, operation.name, stage["item"]

    def _stage_input(self, stage, item):
        """A unique earlier operation is an internal source, never an Item guess."""
        work, card = stage["work_order"], stage["job_card"]
        if item not in {row.get("item_code") for row in stage["bom"].get("items") or []}:
            _invalid()
        stages = [row for row in work.get("operations") or [] if row.get("finished_good") == item]
        if stages:
            if (len(stages) != 1 or stages[0].get("sequence_id") in (None, "")
                    or _number(stages[0].get("sequence_id")) >= _number(card.get("sequence_id"))):
                frappe.throw("耗用商品没有唯一的同工单前序阶段，不能推测内部半成品成本")
            return work.name, stages[0].name, item
        if item not in self.allowed_items - {self.fg.name}:
            frappe.throw("阶段耗料既非流程原料也非准确前序半成品，不能加载部分成本")
        return None

    def _stage_event(self, stage, doc, detail, item, ledger, *, output=False):
        node = self._stage_node(stage) if output else self._stage_input(stage, item)
        quantity = sum((_number(row["actual_qty"]) for row in ledger), Decimal(0))
        value = sum((_number(row["stock_value_difference"]) for row in ledger), Decimal(0))
        if not output:
            quantity, value = -quantity, -value
        if (quantity > 0 and value < 0) or (quantity < 0 and value > 0):
            frappe.throw("阶段库存净价值方向与实际数量不一致，请先核对原生估值调整")
        if node:
            if not ledger:
                _invalid()
            primary = next((row for row in ledger if _number(row["actual_qty"])), None)
            if not primary or not primary.get("creation"):
                _invalid()
            self.stage_events.append((node, output, quantity, value, doc, detail, primary["name"]))
        return Decimal(0) if node and not output else value

    def _check_stage_values(self):
        """Exclude internal value only after its actual same-WO supply closes.

        Full consumption must match the complete native net output. Partial
        consumption accepts one observed unit value only; differing batches
        need provenance that these vouchers do not provide. No FIFO or average
        of other warehouse stock is invented here.
        """
        nodes = {event[0] for event in self.stage_events}
        stage_items = {node[2] for node in nodes}
        bound = {(event[4].doctype, event[4].name, event[5]) for event in self.stage_events}
        if any(row.get("item_code") in stage_items and _number(row.get("actual_qty")) < 0
                and (doctype, name, row.get("voucher_detail_no")) not in bound
                for (doctype, name), rows in self.ledger.items() for row in rows):
            frappe.throw("半成品耗用缺少准确同工单工序来源，不能与阶段成本直接相加")
        for node in nodes:
            produced = [event for event in self.stage_events if event[0] == node and event[1]]
            consumed = [event for event in self.stage_events if event[0] == node and not event[1]]
            if not consumed:
                continue
            if not produced:
                frappe.throw("前序半成品缺少同流程工单的已过账产出，不能用其他库存代替成本来源")
            supply_qty = sum((event[2] for event in produced), Decimal(0))
            supply_value = sum((event[3] for event in produced), Decimal(0))
            used_qty = sum((event[2] for event in consumed), Decimal(0))
            used_value = sum((event[3] for event in consumed), Decimal(0))
            quantity_doc = self.documents[SLE, consumed[0][6]][0]
            if (supply_qty <= 0 or supply_value < 0 or used_qty < 0 or used_value < 0
                    or (used_qty > supply_qty and not _same_quantity(used_qty, supply_qty, quantity_doc, "actual_qty"))
                    or (used_value > supply_value and not _same_quantity(used_value, supply_value, quantity_doc, "stock_value_difference"))):
                frappe.throw("内部阶段净耗用超过同工单实际产出或价值，不能重复或借用其他流程库存成本")
            if _same_quantity(used_qty, supply_qty, quantity_doc, "actual_qty"):
                if not _same_quantity(used_value, supply_value, quantity_doc, "stock_value_difference"):
                    frappe.throw("内部阶段全部耗用的净价值与前序产出不一致，请核对原生库存估值")
            elif used_qty:
                samples = [event for event in produced if event[2] > 0]
                unit_value = samples[0][3] / samples[0][2] if samples else None
                if (unit_value is None or any(not _same_quantity(event[3], event[2] * unit_value,
                        self.documents[SLE, event[6]][0], "stock_value_difference") for event in produced)
                        or not _same_quantity(used_value, used_qty * unit_value, quantity_doc, "stock_value_difference")):
                    frappe.throw("分批半成品估值不同或耗用价值不符且没有精确批次来源，不能平均其他库存推算成本")
            # Native stock operations have their own posting order. Never let a
            # later production retroactively justify earlier external stock.
            def key(event):
                doc = event[4]
                ledger = self.documents[SLE, event[6]][0]
                return (str(doc.get("posting_date")), _stock_time(doc.get("posting_time")),
                        str(ledger.creation), not event[1], doc.name, event[5])
            available_qty = available_value = Decimal(0)
            for event in sorted(produced + consumed, key=key):
                available_qty += event[2] if event[1] else -event[2]
                available_value += event[3] if event[1] else -event[3]
                if ((available_qty < 0 and not _same_quantity(available_qty, 0, quantity_doc, "actual_qty"))
                        or (available_value < 0 and not _same_quantity(available_value, 0, quantity_doc, "stock_value_difference"))):
                    frappe.throw("内部半成品在准确前序产出之前已被耗用或退回，不能推算流程成本")

    def _posted(self, doctype, doc):
        if doc.get("company") != self.company or doc.get("docstatus") != 1:
            _invalid()
        if doctype == SCR and doc.get("status") not in {"Completed", "Return", "Return Issued", "Closed"}:
            _invalid()

    def _receipt_stage(self, doc, row):
        actual = next(item for item in doc.get("items") if item.name == row["name"])
        if not actual.get("job_card"):
            return None
        if not self.include_stages:
            frappe.throw("委外回货包含工序阶段，请选择带入流程已过账实际费用，不能只加载回货重复累计半成品价值")
        self._stage_reader(SCR, doc.name)
        stage = subcontract_stage.procurement_stage(doc, actual, read=self._stage_reader)
        if not stage or stage["flow"].name != self.flow.name:
            _invalid()
        self._stage_node(stage)
        self.receipt_stages[row["name"]] = stage
        return stage

    def load_receipts(self):
        if self.receipts is not None:
            return self.receipts
        child = "Subcontracting Receipt Item"
        direct = self.records(SCR, [[SCR, "docstatus", "=", 1], [child, "jf_flow_order", "=", self.flow.name]],
                              self._parent_fields(SCR), distinct=True)
        docs = {row["name"]: self.source(SCR, row["name"]) for row in direct}
        originals = {name: doc for name, doc in docs.items() if not doc.get("is_return")}
        for doc in list(docs.values()):
            self._posted(SCR, doc)
            if doc.get("is_return"):
                original = self.source(SCR, doc.get("return_against"))
                self._posted(SCR, original)
                if original.get("is_return"):
                    _invalid()
                originals[original.name] = original
        if originals:
            # Blank flow links on historic returns cannot escape this closure.
            returns = self.records(SCR, {"docstatus": 1, "is_return": 1,
                "return_against": ["in", sorted(originals)]}, self._parent_fields(SCR))
            docs.update((row["name"], self.source(SCR, row["name"])) for row in returns)
        docs.update(originals)
        targets = {}
        for name, doc in originals.items():
            self._posted(SCR, doc)
            if doc.get("return_against"):
                _invalid()
            for row in self.table(SCR, name, "items"):
                if row.get("jf_flow_order") == self.flow.name:
                    stage = self._receipt_stage(doc, row)
                    if row.get("item_code") != (stage["item"] if stage else self.fg.name):
                        _invalid()
                    self._receipt_row(doc, row)
                    targets[row["name"]] = row
        selected, returned = [], {name: Decimal(0) for name in targets}
        returned_splits = {name: [Decimal(0), Decimal(0)] for name in targets}
        for name, doc in sorted(docs.items()):
            self._posted(SCR, doc)
            for row in self.table(SCR, name, "items"):
                if not doc.get("is_return"):
                    if row["name"] in targets:
                        selected.append(row)
                    continue
                reference = row.get("subcontracting_receipt_item")
                if reference not in targets:
                    original_rows = {item["name"]: item for item in self.table(SCR, doc.get("return_against"), "items")}
                    known = original_rows.get(reference)
                    if (row.get("jf_flow_order") == self.flow.name
                            or (not known and row.get("item_code") == self.fg.name
                                and row.get("jf_flow_order") in (None, "", self.flow.name))
                            or (known and (known.get("item_code") != row.get("item_code")
                                           or known.get("jf_flow_order") == self.flow.name))):
                        _invalid()
                    continue
                original = targets[reference]
                if (doc.get("return_against") != original["parent"] or row.get("item_code") != original["item_code"]
                        or row.get("jf_flow_order") not in (None, "", self.flow.name)):
                    _invalid()
                stage = self._receipt_stage(doc, row)
                if bool(stage) != bool(self.receipt_stages.get(reference)):
                    _invalid()
                self._receipt_row(doc, row)
                accepted, rejected = -_number(row.get("qty")), -_number(row.get("rejected_qty"))
                # The installed SCR Item has no persisted
                # return_qty_from_rejected_warehouse DocField. Native mapping
                # sets that temporary attribute before save; historic source
                # evidence is the exact original item and persisted warehouses.
                if accepted:
                    warehouse = row.get("warehouse")
                    from_accepted = (warehouse == original.get("warehouse")
                                     and _number(original.get("qty")) > 0)
                    from_rejected = (warehouse == original.get("rejected_warehouse")
                                     and _number(original.get("rejected_qty")) > 0)
                    if from_accepted and from_rejected:
                        frappe.throw("委外退货的合格品与拒收品使用同一仓库，已过账来源无法明确区分退回份额，请核对原生单据")
                    if from_rejected:
                        # Native rejected-warehouse mapping moves its quantity
                        # to qty/warehouse and clears rejected_qty/warehouse.
                        if rejected or row.get("rejected_warehouse") not in (None, ""):
                            _invalid()
                        returned_splits[reference][1] += accepted
                    elif from_accepted:
                        returned_splits[reference][0] += accepted
                    else:
                        _invalid()
                if rejected:
                    if (row.get("rejected_warehouse") != original.get("rejected_warehouse")
                            or _number(original.get("rejected_qty")) <= 0):
                        _invalid()
                    returned_splits[reference][1] += rejected
                returned[reference] -= _number(row.get("qty")) + _number(row.get("rejected_qty"))
                selected.append(row)
            self._receipt_ledger(doc)
        for reference, quantity in returned.items():
            original = targets[reference]
            native = next(item for item in docs[original["parent"]].get("items") if item.name == reference)
            for total, field in zip(returned_splits[reference], ("qty", "rejected_qty")):
                allowed = _number(original.get(field))
                if total > allowed and not _same_quantity(total, allowed, native, field):
                    _invalid()
            if quantity > _number(original.get("qty")) + _number(original.get("rejected_qty")):
                _invalid()
        for row in selected:
            if not (self.stage_quantities_only and row["name"] in self.receipt_stages):
                self.receipt_fees[row["name"]] = self._receipt_fee_split(docs[row["parent"]], row)
        self.receipts = (docs, targets, selected, returned)
        return self.receipts

    def _receipt_row(self, doc, row):
        item = self.source("Item", row.get("item_code"))
        if row.get("stock_uom") != item.get("stock_uom") or _number(row.get("conversion_factor")) != 1:
            frappe.throw("委外回货的库存单位或换算系数不能明确用于当前成本拆分，请核对原生来源")
        quantities = [_number(row.get(field)) for field in ("qty", "rejected_qty", "process_loss_qty")]
        if doc.get("is_return"):
            if quantities[0] > 0 or quantities[1] > 0 or quantities[2] != 0 or not any(quantities):
                _invalid()
        elif any(value < 0 for value in quantities) or not sum(quantities):
            _invalid()

    def _receipt_fee_split(self, doc, row):
        """Verify a split from persisted source values, not a current BOM.

        v16 SRC.calculate_items_qty_and_amount derives supplied amount by exact
        reference_name, and then divides that cost by received_qty. Its final
        amount can additionally allocate to BOM/secondary/LCV. Equality with the
        persisted primary amount proves only this simplified split, never that
        a current BOM's cost_allocation_per was the historical setting.
        """
        if (row.get("secondary_item_type") or row.get("valuation_type")
                or _number(row.get("secondary_items_cost_per_qty"))
                or _number(row.get("landed_cost_voucher_amount"))
                or any(item.get("reference_name") == row["name"]
                       and (item.get("secondary_item_type") or item.get("valuation_type"))
                       for item in self.table(SCR, doc.name, "items"))):
            frappe.throw("回货包含副产品、估值分摊或到岸费用，尚不能准确拆分三项流程成本，请核对原生来源")
        actual = next(item for item in doc.get("items") if item.name == row["name"])
        all_rejected = (not doc.get("is_return") and not _number(row.get("qty"))
                        and _number(row.get("rejected_qty")) > 0)
        required = ("received_qty", "service_cost_per_qty", "additional_cost_per_qty", "amount")
        if not all_rejected:
            required += ("rm_supp_cost", "rm_cost_per_qty")
        if any(row.get(field) in (None, "") for field in required):
            frappe.throw("回货的已过账材料或费用来源缺失，不能按零成本补齐")
        received = _number(row["received_qty"])
        quantity = sum((_number(row.get(field)) for field in ("qty", "rejected_qty", "process_loss_qty")), Decimal(0))
        if (not received or not _same_quantity(received, quantity, actual, "received_qty")
                or (doc.get("is_return") and received >= 0) or (not doc.get("is_return") and received <= 0)
                or (not _number(row.get("qty")) and not _number(row.get("rejected_qty")))):
            _invalid()
        supplied = [item for item in self.table(SCR, doc.name, "supplied_items")
                    if item.get("reference_name") == row["name"]]
        if not doc.get("is_return") and material_returns.has_finished_tracking(actual):
            self._receipt_tracking_children(doc, "items")
            for field, column in (("qty", "warehouse"), ("rejected_qty", "rejected_warehouse")):
                if _number(actual.get(field)):
                    material_returns.finished_source_lots(doc, actual, actual.get(column),
                        ledger=self.ledger[SCR, doc.name], reader=self.source)
        if not doc.get("is_return") and any(material_returns._has_tracking(item)
                for item in doc.get("supplied_items") or [] if item.get("reference_name") == row["name"]):
            self._receipt_tracking_children(doc)
            for item in doc.get("supplied_items") or []:
                if item.get("reference_name") != row["name"] or not material_returns._has_tracking(item):
                    continue
                material_returns._origin_raw(doc, row["name"], item)
                material_returns.material_lots(item, self.company, doc.supplier_warehouse, original=True, reader=self.source)
                entries = [entry for entry in self.ledger[SCR, doc.name] if entry["voucher_detail_no"] == item.name]
                value = -sum((_number(entry["stock_value_difference"]) for entry in entries), Decimal(0))
                if not _same_quantity(value, item.amount, item, "amount"):
                    frappe.throw("原批次耗料与有效库存估值不一致，不能猜测部分退回金额")
        total = Decimal(0)
        for item in supplied:
            if item.get("rate") in (None, "") or item.get("amount") in (None, ""):
                _invalid()
            child = next(child for child in doc.get("supplied_items") if child.name == item["name"])
            if not _same_quantity(item["amount"], _number(item.get("consumed_qty")) * _number(item["rate"]), child, "amount"):
                _invalid()
            total += _number(item["amount"])
        if all_rejected:
            # Installed v16.36 clears rm_supp_cost when creating raw rows, then
            # assigns the primary material caches only if accepted qty != 0.
            # Neither its zero/stale cache nor primary amount proves consumed
            # cost in this case. Exact raw rows AND their effective native SLE
            # must agree; rejected FG SLE remains the separate stock value.
            material = self._receipt_material_stock_value(doc, supplied)
            if (not _same_quantity(material, total, actual, "rm_supp_cost")
                    or _number(row["amount"]) < 0):
                frappe.throw("全部拒收的实际材料库存价值与准确耗料不一致，不能猜测历史成本")
            material_rate = material / received
        else:
            material = _number(row["rm_supp_cost"])
            if (not _same_quantity(material, total, actual, "rm_supp_cost")
                    or not _same_quantity(_number(row["rm_cost_per_qty"]) * received, material, actual, "rm_supp_cost")):
                frappe.throw("回货已过账材料费用与精确耗料来源不一致，不能提供部分成本")
            material_rate = _number(row["rm_cost_per_qty"])
        rates = [material_rate, _number(row["service_cost_per_qty"]), _number(row["additional_cost_per_qty"])]
        if any(rate < 0 for rate in rates) or (material > 0) != (received > 0) and material:
            _invalid()
        # Keep the frozen estimated FG amount check separate from native
        # booked fees: native books accepted qty, not rejected/lost coverage.
        estimated_amount = material + rates[1] * received + rates[2] * received
        original = None
        if doc.get("is_return"):
            self._receipt_return_values(doc, row, supplied, actual)
            original_doc = self.source(SCR, doc.get("return_against"))
            original = next((item for item in original_doc.get("items")
                if item.name == row.get("subcontracting_receipt_item")), None)
        fee_ledger = self._receipt_fee_ledger
        source_proof = None
        if doc.get("is_return"):
            source_proof = self._receipt_fee_proof(original_doc, for_return=True)
        processing = material_returns.receipt_accounting_fee(doc, actual, "service_cost_per_qty", original,
            ledger_reader=fee_ledger, fee_source=original_doc if doc.get("is_return") else None, _proof=source_proof)
        other = material_returns.receipt_accounting_fee(doc, actual, "additional_cost_per_qty", original,
            ledger_reader=fee_ledger, fee_source=original_doc if doc.get("is_return") else None, _proof=source_proof)
        if processing or other:
            self._receipt_fee_proof(doc)
        if not doc.get("is_return") and not all_rejected and not _same_quantity(row["amount"], estimated_amount, actual, "amount"):
            frappe.throw("回货已过账金额包含无法准确拆分的分摊或费用，不能简化为三项流程成本")
        # Rates are persisted in Company currency. A foreign account amount in
        # additional_costs is not silently treated as company currency.
        for charge in self.table(SCR, doc.name, "additional_costs"):
            if _number(charge.get("amount")) and (charge.get("account_currency") != self.currency
                    or _number(charge.get("exchange_rate")) != 1):
                frappe.throw("回货附加费用币种不能明确拆分为公司本币，请核对原生来源")
        output = [entry for entry in self.ledger[SCR, doc.name] if entry["voucher_detail_no"] == row["name"]]
        inventory_qty = sum((_number(entry["actual_qty"]) for entry in output), Decimal(0))
        inventory_value = sum((_number(entry["stock_value_difference"]) for entry in output), Decimal(0))
        if (inventory_qty > 0 and inventory_value < 0) or (inventory_qty < 0 and inventory_value > 0):
            frappe.throw("回货成品有效库存净价值方向与实物数量不一致，请核对原生估价调整")
        valuation_basis = material + processing + other
        stage = self.receipt_stages.get(row["name"])
        if stage:
            actual_material, external_material = Decimal(0), Decimal(0)
            for item in supplied:
                ledger = [entry for entry in self.ledger[SCR, doc.name] if entry["voucher_detail_no"] == item["name"]]
                actual_material -= sum((_number(entry["stock_value_difference"]) for entry in ledger), Decimal(0))
                if _number(item.get("consumed_qty")):
                    external_material += self._stage_event(stage, doc, item["name"], item["rm_item_code"], ledger)
            self._stage_event(stage, doc, row["name"], row["item_code"], output, output=True)
            if not _same_quantity(actual_material, material, actual, "rm_supp_cost"):
                frappe.throw("阶段回货材料库存净价值与精确耗料不一致，不能提供部分成本")
            material = external_material
        return {"material": material, "processing": processing, "other": other,
                "inventory_qty_change": inventory_qty, "inventory_value_change": inventory_value,
                # Keep the original gross supplied cost here. The fee category
                # may exclude same-WO semi products to prevent double counting.
                "valuation_cost_basis": valuation_basis,
                "valuation_difference": inventory_value - valuation_basis}

    def _receipt_material_stock_value(self, doc, supplied):
        """Authoritative raw consumption, after complete ledger/permission checks."""
        total = Decimal(0)
        for item in supplied:
            entries = [entry for entry in self.ledger[SCR, doc.name]
                       if entry["voucher_detail_no"] == item["name"]]
            value = -sum((_number(entry["stock_value_difference"]) for entry in entries), Decimal(0))
            child = next(child for child in doc.get("supplied_items") if child.name == item["name"])
            if value < 0 or not _same_quantity(value, item.get("amount"), child, "amount"):
                frappe.throw("全部拒收的实际材料库存价值与准确耗料不一致，不能猜测历史成本")
            total += value
        return total

    def _receipt_return_values(self, doc, row, supplied, actual):
        """Prove a physical return from original facts; never today's BOM.

        Accepted loss/rejection cost is already allocated into the original FG
        SLE unit value. Material returns and unit fees instead use covered
        received_qty. They can differ without inventing an extra cost category.
        """
        original_doc = self.source(SCR, doc.get("return_against"))
        original = next((item for item in self.table(SCR, original_doc.name, "items")
                         if item["name"] == row.get("subcontracting_receipt_item")), None)
        if not original or original.get("item_code") != row.get("item_code"):
            _invalid()
        covered = _number(original.get("received_qty"))
        if covered <= 0:
            _invalid()
        physical = -_number(row.get("qty")) - _number(row.get("rejected_qty"))
        ratio = physical / covered
        for field in ("service_cost_per_qty", "additional_cost_per_qty"):
            if (original.get(field) in (None, "")
                    or not _same_quantity(row[field], original[field], actual, field)
                    or not _same_quantity(_number(row[field]) * _number(row["received_qty"]),
                                          _number(original[field]) * _number(row["received_qty"]), actual, "amount")):
                frappe.throw("委外退回的原单位工费或附加费用与原收回不一致，不能改写历史成本")

        def materials(rows):
            values = {}
            for item in rows:
                if not _number(item.get("consumed_qty")):
                    continue
                key = (item.get("rm_item_code"), item.get("stock_uom"))
                totals = values.setdefault(key, [Decimal(0), Decimal(0)])
                totals[0] += _number(item.get("consumed_qty"))
                totals[1] += _number(item.get("amount"))
            return values

        original_materials = materials([item for item in self.table(SCR, original_doc.name, "supplied_items")
                                        if item.get("reference_name") == original["name"]])
        returned_materials = materials(supplied)
        if set(original_materials) != set(returned_materials):
            frappe.throw("委外退回材料种类或库存单位与准确原耗料不同，不能提供部分成本")
        selected_values = {}
        if any(material_returns._has_tracking(item) for item in original_doc.get("supplied_items") or []):
            self._receipt_tracking_children(original_doc)
            self._receipt_tracking_children(doc)
            for item in doc.get("supplied_items") or []:
                if item.get("reference_name") != row["name"] or not _number(item.get("consumed_qty")):
                    continue
                origin = material_returns._origin_raw(original_doc, original["name"], item)
                if material_returns._has_tracking(origin):
                    expected = material_returns.selected_material_value(origin, item, self.company,
                        original_doc.supplier_warehouse, reader=self.source,
                        used=self.receipt_lot_usage.setdefault((original_doc.name, origin.name), {}))
                    if (not _same_quantity(-_number(item.consumed_qty), _number(origin.consumed_qty) * ratio, item, "consumed_qty")
                            or not _same_quantity(-_number(item.amount), expected, item, "amount")):
                        frappe.throw("批次退料数量或金额与明确选择的准确原耗料不一致")
                    key = (origin.rm_item_code, origin.stock_uom)
                    selected_values[key] = selected_values.get(key, Decimal(0)) + expected - _number(origin.amount) * ratio
        for key, (quantity, value) in original_materials.items():
            returned_qty, returned_value = returned_materials[key]
            native = next(item for item in doc.get("supplied_items")
                          if (item.rm_item_code, item.stock_uom) == key and item.reference_name == row["name"])
            if (not _same_quantity(returned_qty, -quantity * ratio, native, "consumed_qty")
                    or not _same_quantity(returned_value, -value * ratio - selected_values.get(key, Decimal(0)), native, "amount")):
                frappe.throw("委外退回材料数量或金额与准确原耗料份额不一致，不能猜测历史成本")

        # Original accepted/rejected pools require distinct persisted warehouses
        # when both have physical stock; a temporary mapper flag proves neither.
        if (_number(original.get("qty")) and _number(original.get("rejected_qty"))
                and original.get("warehouse") == original.get("rejected_warehouse")):
            frappe.throw("原收回接收和拒收仓相同，无法唯一核对退回的原入库单价")
        original_ledger = self.ledger[SCR, original_doc.name]
        expected_value = Decimal(0)
        native_original = next(item for item in original_doc.get("items") if item.name == original["name"])
        if material_returns.has_finished_tracking(native_original) or material_returns.has_finished_tracking(actual):
            self._receipt_tracking_children(original_doc, "items")
            self._receipt_tracking_children(doc, "items")
            selected_value = material_returns.finished_return_value(original_doc, native_original, actual,
                ledger=original_ledger, reader=self.source,
                used=self.receipt_fg_lot_usage.setdefault(original_doc.name, {}),
                return_ledger=self.ledger[SCR, doc.name])
            if selected_value is None:
                _invalid()
            expected_value = -selected_value
        else:
            for field, warehouse in (("qty", row.get("warehouse")),
                                     ("rejected_qty", row.get("rejected_warehouse"))):
                quantity = -_number(row.get(field))
                if not quantity:
                    continue
                entries = [entry for entry in original_ledger
                           if entry["voucher_detail_no"] == original["name"] and entry["warehouse"] == warehouse]
                source_qty = sum((_number(entry["actual_qty"]) for entry in entries), Decimal(0))
                source_value = sum((_number(entry["stock_value_difference"]) for entry in entries), Decimal(0))
                if source_qty <= 0 or source_value < 0:
                    _invalid()
                expected_value -= source_value * quantity / source_qty
        returned_value = sum((_number(entry["stock_value_difference"])
                              for entry in self.ledger[SCR, doc.name]
                              if entry["voucher_detail_no"] == row["name"]), Decimal(0))
        if (not _same_quantity(row["amount"], expected_value, actual, "amount")
                or not _same_quantity(returned_value, expected_value, actual, "amount")):
            frappe.throw("委外实物退回金额或库存价值与准确原入库单价不一致，不能重新分摊损耗或拒收成本")

    def _receipt_tracking_children(self, doc, table="supplied_items"):
        key = (SCR, doc.name, table)
        if key in self.receipt_tracking_tables:
            return
        _field, child, base_fields = next(record for record in TABLES[SCR] if record[0] == table)
        fields = base_fields | (material_returns.FG_TRACKING_FIELDS if table == "items" else
                               {"serial_and_batch_bundle", "serial_no", "batch_no", "bom_detail_no"})
        rows = self.records(child, {"parent": doc.name}, fields, SCR)
        actual = {row.name: row for row in doc.get(table) or []}
        if set(actual) != {row["name"] for row in rows} or any(
                item.get("parenttype") != SCR or item.get("parentfield") != table
                or any(actual[item["name"]].get(field) != value for field, value in item.items()) for item in rows):
            _invalid()
        self.receipt_tracking_tables[key] = (child, fields)

    def _receipt_ledger(self, doc):
        expected = {}
        for row in self.table(SCR, doc.name, "items"):
            item = self.source("Item", row.get("item_code"))
            if row.get("stock_uom") != item.get("stock_uom"):
                _invalid()
            factor = _number(row.get("conversion_factor"))
            if factor <= 0:
                _invalid()
            if item.get("is_stock_item"):
                for field, warehouse in (("qty", row.get("warehouse")), ("rejected_qty", row.get("rejected_warehouse"))):
                    quantity = _number(row.get(field)) * factor
                    if quantity:
                        self._warehouse(warehouse)
                        key = (row["name"], item.name, warehouse)
                        expected[key] = expected.get(key, Decimal(0)) + quantity
        fg_rows = {row["name"]: row for row in self.table(SCR, doc.name, "items")}
        for row in self.table(SCR, doc.name, "supplied_items"):
            reference = fg_rows.get(row.get("reference_name"))
            item = self.source("Item", row.get("rm_item_code"))
            if (not reference or reference.get("item_code") != row.get("main_item_code")
                    or item.get("stock_uom") != row.get("stock_uom") or row.get("consumed_qty") in (None, "")):
                _invalid()
            quantity = -_number(row.get("consumed_qty"))
            if (doc.get("is_return") and quantity < 0) or (not doc.get("is_return") and quantity > 0):
                _invalid()
            if quantity:
                self._warehouse(doc.get("supplier_warehouse"))
                expected[row["name"], item.name, doc.get("supplier_warehouse")] = quantity
        self._ledger(doc, SCR, expected)

    def _warehouse(self, name):
        doc = self.source("Warehouse", name)
        if doc.get("company") != self.company or doc.get("is_group"):
            _invalid()
        return doc

    def _ledger(self, doc, doctype, expected):
        # Include cancelled entries in the membership fingerprint, but only
        # active native ledger entries may represent the submitted voucher.
        self.raw(SLE, {"voucher_type": doctype, "voucher_no": doc.name})
        rows = self.records(SLE, {"voucher_type": doctype, "voucher_no": doc.name, "is_cancelled": 0}, PARENTS[SLE])
        quantities = {key: Decimal(0) for key in expected}
        for row in rows:
            ledger = self.source(SLE, row["name"])
            key = (row.get("voucher_detail_no"), row.get("item_code"), row.get("warehouse"))
            if (key not in expected or row.get("company") != self.company or row.get("docstatus") != 1
                    or row.get("posting_date") != doc.get("posting_date") or row.get("posting_time") != doc.get("posting_time")
                    or row.get("stock_uom") != self.source("Item", row.get("item_code")).get("stock_uom")):
                _invalid()
            if row.get("actual_qty") in (None, "") or row.get("stock_value_difference") in (None, ""):
                _invalid()
            actual = _number(row.get("actual_qty"))
            _number(row.get("stock_value_difference"))
            if row.get("is_adjustment_entry"):
                if actual:
                    _invalid()
            elif not actual or (actual > 0) != (expected[key] > 0):
                _invalid()
            quantities[key] += actual
            if not _same_quantity(actual, actual, ledger, "actual_qty"):
                _invalid()
        for key, quantity in quantities.items():
            matching = [row for row in rows if (row.get("voucher_detail_no"), row.get("item_code"), row.get("warehouse")) == key]
            if not matching or not _same_quantity(quantity, expected[key], self.documents[SLE, matching[0]["name"]][0], "actual_qty"):
                _invalid()
        self.ledger[doctype, doc.name] = rows

    def _loss_reader(self, doctype, name):
        """Freeze the loss proof's exact native documents and all child sets."""
        from jifeng_erp import production_loss

        configured = production_loss.TABLES.get(doctype, ())
        if configured and isinstance(configured[0], str):
            configured = (configured,)
        # A plan or sales source may be read only by the dynamic loss graph.
        # Its role/field/share metadata must remain unchanged even if native
        # permission lookups are cached for the rest of this request. Capture
        # only newly used types; ordinary cost reads need no extra scope.
        scope = tuple(sorted(({doctype} | {child for _, child, _ in configured})
                             - _authorization_doctypes()))
        if scope and scope not in self.loss_authorization:
            self.loss_authorization[scope] = _authorization(scope)
        fields = set(PARENTS.get(doctype, set())) | set(production_loss.PARENTS.get(doctype, set()))
        if not fields or not isinstance(name, str) or not name:
            _invalid()
        key = doctype, name
        if key in self.documents:
            doc, _state, permission, previous_fields = self.documents[key]
            fields |= previous_fields
        else:
            try:
                doc = frappe.get_doc(doctype, name)
            except frappe.DoesNotExistError:
                _invalid()
            permission = "read"
        doc.check_permission(permission)
        rows = self.records(doctype, {"name": name}, fields)
        if (len(rows) != 1 or any(doc.get(field) != value for field, value in rows[0].items())
                or ("company" in fields and doc.get("company") != self.company)):
            _invalid()
        self.documents[key] = doc, _freeze(doc), permission, fields
        tables = {field: (childtype, set(child_fields))
                  for field, childtype, child_fields in TABLES.get(doctype, ())}
        for field, childtype, child_fields in configured:
            if field in tables and tables[field][0] != childtype:
                _invalid()
            tables[field] = childtype, set(child_fields) | tables.get(field, (None, set()))[1]
        for field, (childtype, child_fields) in tables.items():
            _table(doctype, field, childtype)
            children = self.records(childtype, {"parent": name}, child_fields, doctype)
            actual = {row.name: row for row in doc.get(field) or []}
            if len(actual) != len(doc.get(field) or []) or set(actual) != {row["name"] for row in children}:
                _invalid()
            for child in children:
                if (child.get("parenttype") != doctype or child.get("parentfield") != field
                        or any(actual[child["name"]].get(column) != value for column, value in child.items())):
                    _invalid()
            self.children[doctype, name, field] = children
            self.loss_tables[doctype, name, field] = childtype, child_fields
        return doc

    def load_losses(self):
        if self.loss_batches is not None:
            return self.loss_batches
        work_rows = self.raw(WO, {"jf_flow_order": self.flow.name})
        parents = {}
        if work_rows:
            found = self.raw(SE, {"docstatus": 1, LOSS_WORK_ORDER: ["in", sorted(row["name"] for row in work_rows)]})
            parents.update((row["name"], row) for row in found)
        found = self.raw(SE, [[SE, "docstatus", "=", 1], [SE, "stock_entry_type", "=", LOSS_TYPE],
                             ["Stock Entry Detail", "jf_flow_order", "=", self.flow.name]], distinct=True)
        parents.update((row["name"], row) for row in found)
        result, by_card, by_work = [], {}, {}
        if parents:
            from jifeng_erp import production_loss

            if production_loss.LOSS_TYPE != LOSS_TYPE:
                _invalid()
            for name in sorted(parents):
                doc = self._loss_reader(SE, name)
                self._posted(SE, doc)
                source = production_loss.read_loss_source(doc, read=self._loss_reader)
                if ((source["flow_order"], source["company"], source["item_code"], source["stock_uom"]) !=
                        (self.flow.name, self.company, self.fg.name, self.fg.stock_uom)
                        or source["loss_entry"] != name):
                    _invalid()
                qty = _number(source["qty"])
                if qty <= 0 or _number(source["lost_qty"]) != qty or _number(source["inventory_qty"]) != 0:
                    _invalid()
                card = self._loss_reader("Job Card", source["job_card"])
                key = source["job_card"]
                by_card[key] = by_card.get(key, Decimal(0)) + qty
                if by_card[key] > _number(card.process_loss_qty):
                    frappe.throw("同一报工的有效制造损耗出库累计数量超过已确认损耗，不能重复计入成本或补产覆盖")
                work = self._loss_reader(WO, source["work_order"])
                by_work[work.name] = by_work.get(work.name, Decimal(0)) + qty
                if by_work[work.name] > _number(work.qty):
                    frappe.throw("同一工单的有效制造损耗出库累计数量超过原工单库存数量，不能借用其他生产范围")
                # Money belongs to the ordinary Material Issue SLE collector.
                # This independent source contributes quantity coverage only.
                result.append({"loss_issue": name, "work_order": source["work_order"],
                    "job_card": source["job_card"], "company": self.company, "item_code": self.fg.name,
                    "stock_uom": self.fg.stock_uom, "qty": qty, "inventory_qty": Decimal(0), "lost_qty": qty})
        self.loss_batches = result
        return result

    def _check_loss_coverage(self):
        by_work = {}
        for source in self.load_losses():
            by_work[source["work_order"]] = by_work.get(source["work_order"], Decimal(0)) + source["qty"]
        for name, loss in by_work.items():
            work = self._loss_reader(WO, name)
            manufactured = [source for source in self.manufacture_batches or [] if source["work_order"] == name]
            if (any(source["lost_qty"] for source in manufactured)
                    or loss + sum((source["qty"] for source in manufactured), Decimal(0)) > _number(work.qty)):
                frappe.throw("同一工单制造入库和损耗出库的覆盖量冲突，不能重复计入已确认损耗或借用其他生产范围")

    def load_issues(self):
        if self.issues is not None:
            return self.issues
        self.load_losses()
        parents = {}
        for field in ("purpose", "stock_entry_type"):
            found = self.records(SE, [[SE, "docstatus", "=", 1], [SE, field, "=", "Material Issue"],
                ["Stock Entry Detail", "jf_flow_order", "=", self.flow.name]], PARENTS[SE], distinct=True)
            parents.update((row["name"], row) for row in found)
        header_linked = self.records(SE, {"docstatus": 1, "jf_material_issue_flow": self.flow.name}, PARENTS[SE])
        tagged_returns = {}
        for row in header_linked:
            if row.get("jf_material_return_against"):
                tagged_returns[row["name"]] = row
            else:
                parents[row["name"]] = row
        selected = []
        issue_sources = {}
        self.material_return_sources = {}
        loss_names = {source["loss_issue"] for source in self.load_losses()}
        for row in parents.values():
            doc = self.source(SE, row["name"])
            self._posted(SE, doc)
            if (doc.get("stock_entry_type") == LOSS_TYPE
                    or any(doc.get(field) for field in doc.as_dict() if field.startswith(LOSS_PREFIX))):
                if doc.name not in loss_names:
                    _invalid()
            if doc.get("purpose") != "Material Issue" or doc.get("jf_material_issue_flow") not in (None, "", self.flow.name):
                _invalid()
            expected, targets = {}, set()
            for detail in self.table(SE, doc.name, "items"):
                if doc.get("jf_material_issue_flow") == self.flow.name and detail.get("jf_flow_order") != self.flow.name:
                    _invalid()
                item = self.source("Item", detail.get("item_code"))
                if (not item.get("is_stock_item") or detail.get("stock_uom") != item.get("stock_uom")
                        or not detail.get("s_warehouse") or detail.get("t_warehouse")
                        or not detail.get("uom") or _number(detail.get("qty")) <= 0
                        or _number(detail.get("conversion_factor")) <= 0
                        or (detail.get("uom") == detail.get("stock_uom") and _number(detail.get("conversion_factor")) != 1)
                        or _number(detail.get("transfer_qty")) <= 0):
                    _invalid()
                native = next(item for item in doc.get("items") if item.name == detail["name"])
                if not _same_quantity(detail["transfer_qty"], _number(detail["qty"]) * _number(detail["conversion_factor"]),
                                      native, "transfer_qty"):
                    _invalid()
                self._warehouse(detail.get("s_warehouse"))
                expected[detail["name"], item.name, detail.get("s_warehouse")] = -_number(detail.get("transfer_qty"))
                if detail.get("jf_flow_order") == self.flow.name:
                    if item.name not in self.allowed_items:
                        _invalid()
                    targets.add(detail["name"])
            self._ledger(doc, SE, expected)
            selected.extend(row for row in self.ledger[SE, doc.name] if row.get("voucher_detail_no") in targets)
            issue_sources[doc.name] = (doc, {row["name"]: row for row in self.table(SE, doc.name, "items")}, targets)
        return_parents = dict(tagged_returns)
        if issue_sources:
            query = {"jf_material_return_against": ["in", sorted(issue_sources)]}
            self.raw(SE, query)
            return_parents.update((row["name"], row) for row in self.records(
                SE, {**query, "docstatus": 1}, PARENTS[SE]))
        returned = {}
        for row in return_parents.values():
            doc = self.source(SE, row["name"])
            self._posted(SE, doc)
            origin = issue_sources.get(doc.get("jf_material_return_against"))
            if not origin:
                _invalid()
            source, source_rows, targets = origin
            source_flows = {detail.get("jf_flow_order") for detail in source_rows.values()}
            source_flows.discard(None)
            source_flows.discard("")
            source_header_flow = source.get("jf_material_issue_flow")
            if not source_header_flow and len(source_flows) == 1:
                source_header_flow = next(iter(source_flows))
            if (doc.get("purpose") != "Material Receipt" or doc.get("stock_entry_type") != "Material Receipt"
                    or doc.get("jf_material_issue_flow") != source_header_flow):
                _invalid()
            expected, return_targets, links = {}, set(), {}
            for detail in self.table(SE, doc.name, "items"):
                source_row = source_rows.get(detail.get("jf_material_return_against_detail"))
                if (not source_row or any(detail.get(field) != source_row.get(field)
                        for field in ("item_code", "stock_uom", "jf_flow_order"))
                        or detail.get("s_warehouse") or detail.get("t_warehouse") != source_row.get("s_warehouse")
                        or not detail.get("uom") or _number(detail.get("qty")) <= 0
                        or _number(detail.get("conversion_factor")) <= 0
                        or (detail.get("uom") == detail.get("stock_uom") and _number(detail.get("conversion_factor")) != 1)
                        or _number(detail.get("transfer_qty")) <= 0):
                    _invalid()
                native = next(item for item in doc.get("items") if item.name == detail["name"])
                quantity = _number(detail["transfer_qty"])
                if not _same_quantity(quantity, _number(detail["qty"]) * _number(detail["conversion_factor"]), native, "transfer_qty"):
                    _invalid()
                key = (source.name, source_row["name"])
                returned[key] = returned.get(key, Decimal(0)) + quantity
                source_qty = _number(source_row["transfer_qty"])
                if returned[key] > source_qty and not _same_quantity(returned[key], source_qty, native, "transfer_qty"):
                    _invalid()
                item = self.source("Item", detail["item_code"])
                self._warehouse(detail["t_warehouse"])
                expected[detail["name"], item.name, detail["t_warehouse"]] = quantity
                links[detail["name"]] = source_row
                if source_row["name"] in targets:
                    return_targets.add(detail["name"])
            self._ledger(doc, SE, expected)
            # Use the return's actual positive inventory value only when it
            # reconciles to its exact issue row, including valuation adjustments.
            for detail_name, source_row in links.items():
                origin_ledger = [entry for entry in self.ledger[SE, source.name]
                                 if entry.get("voucher_detail_no") == source_row["name"]]
                return_ledger = [entry for entry in self.ledger[SE, doc.name]
                                 if entry.get("voucher_detail_no") == detail_name]
                source_value = -sum((_number(entry["stock_value_difference"]) for entry in origin_ledger), Decimal(0))
                source_qty = -sum((_number(entry["actual_qty"]) for entry in origin_ledger), Decimal(0))
                value = sum((_number(entry["stock_value_difference"]) for entry in return_ledger), Decimal(0))
                quantity = sum((_number(entry["actual_qty"]) for entry in return_ledger), Decimal(0))
                ledger_doc = self.documents[SLE, return_ledger[0]["name"]][0]
                if (source_qty <= 0 or source_value < 0 or value < 0
                        or not _same_quantity(value, source_value * quantity / source_qty, ledger_doc, "stock_value_difference")):
                    _invalid()
            self.material_return_sources[doc.name] = source.name
            selected.extend(entry for entry in self.ledger[SE, doc.name] if entry.get("voucher_detail_no") in return_targets)
        self.issues = selected
        return self.issues

    def _manufacture_source(self, name):
        doc = self.source(SE, name)
        fields = PARENTS[SE] | MANUFACTURE_FIELDS | MANUFACTURE_QUANTITY_FIELDS
        rows = self.records(SE, {"name": name}, fields)
        if len(rows) != 1 or any(doc.get(key) != value for key, value in rows[0].items()):
            _invalid()
        self.documents[SE, name] = (doc, _freeze(doc), "read", fields)
        for fieldname, childtype, fields in (
            ("items", "Stock Entry Detail", TABLES[SE][0][2] | MANUFACTURE_ITEM_FIELDS),
            MANUFACTURE_CHARGES,
        ):
            _table(SE, fieldname, childtype)
            children = self.records(childtype, {"parent": name}, fields, SE)
            actual = {row.name: row for row in doc.get(fieldname) or []}
            if set(actual) != {row["name"] for row in children} or len(actual) != len(doc.get(fieldname) or []):
                _invalid()
            for row in children:
                if (row.get("parenttype") != SE or row.get("parentfield") != fieldname
                        or any(actual[row["name"]].get(key) != value for key, value in row.items())):
                    _invalid()
            self.children[SE, name, fieldname] = children
        self.manufacture_names.add(name)
        return doc

    def load_manufactures(self):
        if self.manufactures is not None:
            return self.manufactures
        work_rows = self.raw(WO, {"jf_flow_order": self.flow.name})
        work_names = [row["name"] for row in work_rows]
        parents = {}
        if work_names:
            rows = self.raw(SE, {"docstatus": 1, "purpose": "Manufacture", "work_order": ["in", work_names]})
            parents.update((row["name"], row) for row in rows)
        # Also discover child-linked entries, so a missing/wrong Work Order
        # cannot silently make an explicitly linked manufacturing cost vanish.
        rows = self.raw(SE, [[SE, "docstatus", "=", 1], [SE, "purpose", "=", "Manufacture"],
                            ["Stock Entry Detail", "jf_flow_order", "=", self.flow.name]], distinct=True)
        parents.update((row["name"], row) for row in rows)
        result, batches = [], []
        for name in sorted(parents):
            doc = self._manufacture_source(name)
            self._posted(SE, doc)
            work = self.source(WO, doc.get("work_order"))
            if (doc.get("purpose") != "Manufacture" or work.get("docstatus") != 1
                    or work.get("jf_flow_order") != self.flow.name or work.get("production_item") != self.fg.name):
                _invalid()
            if work.get("track_semi_finished_goods") or doc.get("job_card"):
                if not self.include_stages or not work.get("track_semi_finished_goods") or not doc.get("job_card"):
                    frappe.throw("半成品制造缺少准确工序阶段来源，不能重复累计阶段价值")
                # bom_no is native, but is needed only for a real stage source.
                fields = self.documents[SE, name][3] | {"bom_no"}
                rows = self.records(SE, {"name": name}, fields)
                if len(rows) != 1 or any(doc.get(key) != value for key, value in rows[0].items()):
                    _invalid()
                self.documents[SE, name] = (doc, _freeze(doc), "read", fields)
                stage = subcontract_stage.stock_stage(doc, read=self._stage_reader)
                if not stage or stage["flow"].name != self.flow.name:
                    _invalid()
                self._stage_node(stage)
            else:
                stage = None
            expected, consumed, output_qty, allocated = {}, set(), Decimal(0), Decimal(0)
            for row in self.table(SE, name, "items"):
                item = self.source("Item", row.get("item_code"))
                source, target = row.get("s_warehouse"), row.get("t_warehouse")
                quantity, factor = _number(row.get("transfer_qty")), _number(row.get("conversion_factor"))
                if (not item.get("is_stock_item") or row.get("stock_uom") != item.get("stock_uom")
                        or not row.get("uom") or _number(row.get("qty")) <= 0 or quantity <= 0 or factor <= 0
                        or (row.get("uom") == row.get("stock_uom") and factor != 1)
                        or (not source and not target) or (source and target)
                        or row.get("jf_flow_order") not in (None, "", self.flow.name)):
                    _invalid()
                native = next(child for child in doc.get("items") if child.name == row["name"])
                if not _same_quantity(quantity, _number(row["qty"]) * factor, native, "transfer_qty"):
                    _invalid()
                if (row.get("secondary_item_type") or row.get("valuation_type")
                        or _number(row.get("landed_cost_voucher_amount"))):
                    frappe.throw("制造凭证包含副产品、估值分摊或到岸费用，尚不能准确拆分本流程材料与附加费用")
                self._warehouse(source or target)
                if source:
                    if row.get("is_finished_item") or (not stage and item.name not in self.allowed_items - {self.fg.name}):
                        _invalid()
                    if stage:
                        self._stage_input(stage, item.name)
                    consumed.add(row["name"])
                else:
                    if not row.get("is_finished_item") or item.name != (stage["item"] if stage else self.fg.name):
                        _invalid()
                    output_qty += quantity
                    allocated += _number(row.get("additional_cost"))
                expected[row["name"], item.name, source or target] = -quantity if source else quantity
            if not consumed or output_qty <= 0:
                frappe.throw("制造凭证没有完整的直接耗料与成品入库来源，不能按零耗料补齐成本")
            self._ledger(doc, SE, expected)
            consumed_values = {detail: Decimal(0) for detail in consumed}
            output_value = Decimal(0)
            for row in self.ledger[SE, name]:
                value = _number(row["stock_value_difference"])
                if row["voucher_detail_no"] in consumed:
                    consumed_values[row["voucher_detail_no"]] -= value
                else:
                    output_value += value
            if any(value < 0 for value in consumed_values.values()):
                frappe.throw("制造耗料的有效库存账净成本为负，请先核对原生估值调整")
            material = sum(consumed_values.values(), Decimal(0))
            charges = self.table(SE, name, "additional_costs")
            other = Decimal(0)
            for row in charges:
                if row.get("base_amount") in (None, "") or row.get("amount") in (None, "") or not row.get("account_currency"):
                    _invalid()
                self.source("Currency", row["account_currency"]).check_permission("select")
                amount, exchange = _number(row["amount"]), _number(row.get("exchange_rate"))
                if exchange <= 0 or (row["account_currency"] == self.currency and exchange != 1):
                    _invalid()
                native = next(child for child in doc.get("additional_costs") if child.name == row["name"])
                precision = native.precision("amount")
                if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 9:
                    _invalid()
                # Match init_landed_taxes_and_totals using the saved FX; never
                # call its initializer, which can fetch a missing exchange rate.
                expected_base = flt(amount, precision) * flt(exchange)
                if not _same_quantity(row["base_amount"], expected_base, native, "base_amount"):
                    _invalid()
                # Native distribute_additional_costs sums persisted base_amount;
                # do not convert again using today's FX or add FG stock value.
                other += _number(row["base_amount"])
            if (doc.get("total_additional_costs") in (None, "")
                    or not _same_quantity(other, doc.total_additional_costs, doc, "total_additional_costs")
                    or not _same_quantity(allocated, other, doc, "total_additional_costs")):
                _invalid()
            if not _same_quantity(output_value, material + other, doc, "total_additional_costs"):
                frappe.throw("制造成品有效库存账与直接耗料、附加费用不一致；手工估值或独立耗用等来源须先单独核对")
            if stage:
                external_material = Decimal(0)
                for row in self.table(SE, name, "items"):
                    ledger = [entry for entry in self.ledger[SE, name] if entry["voucher_detail_no"] == row["name"]]
                    value = self._stage_event(stage, doc, row["name"], row["item_code"], ledger,
                                              output=row["name"] not in consumed)
                    if row["name"] in consumed:
                        external_material += value
                material = external_material
            batch = self._manufacture_batch(doc, work, consumed, output_qty)
            if batch is not None:
                batches.append(batch)
            result.append({"manufacture": name, "work_order": work.name, "qty": output_qty,
                           "material": material, "other": other})
        self.manufactures = result
        self.manufacture_batches = batches
        return result

    def _manufacture_batch(self, doc, work, consumed, output_qty):
        """One final-product quantity source per parent; no per-row loss guess.

        Installed Manufacture stores coverage in fg_completed_qty and loss in
        its parent. Actual FG stock is the closed detail/SLE transfer quantity.
        Intermediate-stage loss is in that item's unit, not this Flow's FG unit.
        """
        outputs = [row for row in self.table(SE, doc.name, "items") if row["name"] not in consumed]
        if any(row["item_code"] != self.fg.name for row in outputs):
            return None
        if any(doc.get(field) in (None, "") for field in MANUFACTURE_QUANTITY_FIELDS):
            _invalid()
        loss, coverage = _number(doc.process_loss_qty), _number(doc.fg_completed_qty)
        if loss < 0 or coverage < 0:
            _invalid()
        stock_basis = all(row.get("uom") == self.fg.stock_uom
                          and row.get("stock_uom") == self.fg.stock_uom
                          and _number(row.get("conversion_factor")) == 1 for row in outputs)
        if loss and (not doc.from_bom or not stock_basis):
            frappe.throw("制造确认损耗与成品库存数量的单位口径无法准确核对，请先核对原生库存单位及换算系数")
        if not _same_quantity(loss, loss, doc, "process_loss_qty"):
            _invalid()
        # With loss, only the proven stock-unit native shape can supply its
        # coverage. Zero-loss historical conversion paths keep their actual
        # stock quantities; do not mix their transaction header with stock UOM.
        if doc.from_bom and stock_basis and not _same_quantity(
                coverage, output_qty + loss, doc, "fg_completed_qty"):
            frappe.throw("制造覆盖数量与准确成品入库、已确认损耗不一致，请核对原生制造来源")
        return {"manufacture": doc.name, "work_order": work.name, "company": self.company,
                "item_code": self.fg.name, "stock_uom": self.fg.stock_uom,
                "qty": output_qty + loss, "inventory_qty": output_qty, "lost_qty": loss}

    def _reposting(self):
        # Native Transaction jobs clear item_code/warehouse. Resolve their
        # actual serialized seeds or voucher SLE; never rely on those blank
        # columns. A file-backed/unresolvable active job cannot prove safety.
        jobs = frappe.get_all(REPOST, filters={"company": self.company}, fields=["*"],
                              order_by="name asc", limit_page_length=0)
        state = [(REPOST, _freeze(jobs))]
        stock = [row for rows in self.ledger.values() for row in rows]
        pairs = {(row.get("item_code"), row.get("warehouse")) for row in stock}
        vouchers = set(self.ledger)
        if not pairs:
            return tuple(state)
        for job in jobs:
            if job.get("docstatus") != 1 or job.get("status") in {"Completed", "Skipped", "Cancelled"}:
                continue
            if job.get("status") not in {"Queued", "In Progress", "Failed"}:
                _invalid()
            if (job.get("voucher_type"), job.get("voucher_no")) in vouchers:
                frappe.throw("相关库存账重过账尚未完成或已失败，不能加载流程成本")
            seeds = []
            if job.get("based_on") == "Item and Warehouse":
                seeds = [job]
            elif job.get("based_on") == "Transaction":
                if job.get("reposting_data_file"):
                    frappe.throw("公司存在尚未完成且范围需进一步核对的库存重过账，不能加载流程成本")
                if job.get("items_to_be_repost"):
                    try:
                        seeds = json.loads(job["items_to_be_repost"])
                    except (ValueError, TypeError):
                        _invalid()
                if not seeds and job.get("voucher_type") and job.get("voucher_no"):
                    seeds = frappe.get_all(SLE, filters={"company": self.company,
                        "voucher_type": job["voucher_type"], "voucher_no": job["voucher_no"]},
                        fields=["*"], order_by="name asc", limit_page_length=0)
                    state.append((job["name"], _freeze(seeds)))
            if not isinstance(seeds, list) or not seeds:
                frappe.throw("公司存在尚未完成且范围不能确定的库存重过账，不能加载流程成本")
            for seed in seeds:
                if not isinstance(seed, dict) or not seed.get("item_code") or not seed.get("warehouse"):
                    _invalid()
                if (seed["item_code"], seed["warehouse"]) in pairs:
                    if not seed.get("posting_date"):
                        _invalid()
                    # Reposting affects future transactions in this pair. Date
                    # equality is conservatively blocked even for a later time.
                    if any(str(seed["posting_date"]) <= str(row["posting_date"]) for row in stock
                           if (row["item_code"], row["warehouse"]) == (seed["item_code"], seed["warehouse"])):
                        frappe.throw("相关库存账重过账尚未完成或已失败，不能加载流程成本")
        return tuple(state)

    def finish(self, additional_guard=None):
        self._check_stage_values()
        self.repost_state = self._reposting()
        try:
            loaded = {key: frappe.get_doc(*key) for key in self.documents}
        except frappe.DoesNotExistError:
            _deny()
        # Reload all first; a later native permission hook can revoke an earlier
        # source. No DTO is returned until the whole accumulated graph passes.
        self._check_documents(loaded)
        for doctype, filters, fields, parenttype, options, raw_state, native_state in self.queries:
            if fields is not None:
                _fields(doctype, fields, parenttype)
                rows = record_list(doctype, filters=filters, fields=list(fields),
                                   parenttype=parenttype, **options)
                if _freeze(rows) != native_state:
                    _deny()
            raw = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                                 limit_page_length=0, **options)
            if _freeze(raw) != raw_state:
                _deny()
        self._check_documents(loaded)
        if additional_guard:
            additional_guard()
        # Catch source edits performed by a later list hook, including edits
        # that do not update modified. Authorization metadata is also uncached.
        for doctype, filters, _fields_read, _parenttype, options, raw_state, _native_state in self.queries:
            raw = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                                 limit_page_length=0, **options)
            if _freeze(raw) != raw_state:
                _deny()
        if self._reposting() != self.repost_state or _authorization() != self.authorization:
            _deny()
        if any(_authorization(scope) != state for scope, state in self.loss_authorization.items()):
            _deny()

    def _check_documents(self, loaded):
        if (self.receipt_dimensions is not None
                and tuple(sorted(material_returns.receipt_accounting_dimensions())) != self.receipt_dimensions):
            _deny()
        for (doctype, name), (_old, state, permission, fields) in self.documents.items():
            doc = loaded[doctype, name]
            doc.check_permission(permission)
            if doctype == "Company":
                _unmasked(doctype, fields)
                if frappe.get_list("Company", filters={"name": name}, pluck="name", limit_page_length=0) != [name]:
                    _deny()
            else:
                _fields(doctype, fields)
            if _freeze(doc) != state:
                _deny()
            if doctype == "Currency":
                doc.check_permission("select")
            for fieldname, childtype, child_fields in TABLES.get(doctype, ()):
                _table(doctype, fieldname, childtype)
                _fields(childtype, self._child_fields(doctype, childtype, child_fields), doctype)
            if doctype == SE and name in self.manufacture_names:
                _fields("Stock Entry Detail", TABLES[SE][0][2] | MANUFACTURE_ITEM_FIELDS, SE)
                fieldname, childtype, fields = MANUFACTURE_CHARGES
                _table(SE, fieldname, childtype)
                _fields(childtype, fields, SE)
            if (doctype, name) in self.extra_tables:
                fieldname, childtype, fields = self.extra_tables[doctype, name]
                _table(doctype, fieldname, childtype)
                _fields(childtype, fields, doctype)
            for (parenttype, parent, fieldname), (childtype, fields) in self.loss_tables.items():
                if (parenttype, parent) == (doctype, name):
                    _table(doctype, fieldname, childtype)
                    _fields(childtype, fields, doctype)
        for (doctype, _name, _table_field), (child, fields) in self.receipt_tracking_tables.items():
            _fields(child, fields, doctype)


def _receipt_dto(session):
    _docs, _targets, selected, _returned = session.load_receipts()
    rows = []
    for row in selected:
        rows.append({"receipt": row["parent"], "qty": row.get("qty"),
                     "receipt_item": row["name"], "item_code": row["item_code"], "stock_uom": row["stock_uom"],
                     **session.receipt_fees[row["name"]]})
    return {"rows": rows, "totals": {field: sum((row[field] for row in rows), Decimal(0))
        for field in ("material", "processing", "other")},
        "receipt_valuation": {field: sum((row[field] for row in rows), Decimal(0))
            for field in ("inventory_value_change", "valuation_cost_basis", "valuation_difference")},
        "currency": session.currency}


def _issue_dto(session):
    rows = [{"issue": row["voucher_no"], "item_code": row["item_code"],
             "qty": -_number(row.get("actual_qty")), "material": -_number(row.get("stock_value_difference")),
             "source_material_issue": session.material_return_sources.get(row["voucher_no"], "")}
            for row in session.load_issues()]
    return {"rows": rows, "totals": {"material": sum((row["material"] for row in rows), Decimal(0))},
            "currency": session.currency}


def receipt_costs(flow_order):
    session = _Session(flow_order)
    result = _receipt_dto(session)
    session.finish()
    return result


def material_issue_costs(flow_order):
    session = _Session(flow_order)
    result = _issue_dto(session)
    session.finish()
    return result


def receipt_batch_candidates(flow_order):
    # This selects final-product quantities, not a receipt-only cost basis.
    session = _Session(flow_order, include_stages=True, stage_quantities_only=True)
    docs, targets, _selected, returned = session.load_receipts()
    rows = []
    for row in sorted(targets.values(), key=lambda value: (str(docs[value["parent"]].get("posting_date")),
                                                           value["parent"], value.get("idx"))):
        if row.get("item_code") != session.fg.name:
            continue
        try:
            quantities = receipt_batch_quantities(row, returned[row["name"]])
        except CostingError as exc:
            frappe.throw(str(exc))
        rows.append({"receipt": row["parent"], "receipt_item": row["name"], "company": session.company,
                     "item_code": row["item_code"], **quantities})
    session.finish()
    return rows


def manufacture_batch_candidates(flow_order):
    session = _Session(flow_order, include_stages=True)
    # A final stage may consume outsourced semi-products. Close that actual
    # same-WO graph with its receipt sources; never borrow unrelated inventory.
    _receipt_dto(session)
    session.load_manufactures()
    rows = session.manufacture_batches
    state = _freeze(rows)
    session.finish()
    if _freeze(rows) != state:
        _deny()
    return rows


def production_batch_candidates(flow_order):
    """One closed read of actual stock-backed production and loss coverage."""
    session = _Session(flow_order, include_stages=True)
    session.load_issues()
    _receipt_dto(session)
    session.load_manufactures()
    session._check_loss_coverage()
    rows = [{"source_kind": "Manufacture", "source_name": source["manufacture"],
             **{field: source[field] for field in ("work_order", "company", "item_code", "stock_uom", "qty", "inventory_qty", "lost_qty")}}
            for source in session.manufacture_batches]
    rows.extend({"source_kind": "Production Loss", "source_name": source["loss_issue"],
                 **{field: source[field] for field in ("work_order", "job_card", "company", "item_code", "stock_uom", "qty", "inventory_qty", "lost_qty")}}
                for source in session.load_losses())
    state = _freeze(rows)
    session.finish()
    if _freeze(rows) != state:
        _deny()
    return rows


def posted_flow_costs(flow_order):
    from jifeng_erp.flow_wage_cost_sources import posted_flow_wage_costs

    session = _Session(flow_order, include_stages=True)
    issues = _issue_dto(session)
    receipts = _receipt_dto(session)
    manufactures = session.load_manufactures()
    manufacture_material = sum((row["material"] for row in manufactures), Decimal(0))
    manufacture_other = sum((row["other"] for row in manufactures), Decimal(0))
    wages = posted_flow_wage_costs(flow_order)
    if wages["currency"] != session.currency:
        _invalid()
    if wages["total"]:
        wage_work_orders = {row["work_order"] for row in wages["rows"] if row.get("work_order")}
        manual_wages = any(not row.get("job_card") for row in wages["rows"])
        if any(row["other"] and (manual_wages or row["work_order"] in wage_work_orders)
               for row in manufactures):
            frappe.throw("同一生产范围已有制造附加费和工资计提，尚未核实附加费是否包含人工；请先明确费用来源，避免重复计入工资成本")
    manufacture_batches = session.manufacture_batches
    loss_batches = session.load_losses()
    session._check_loss_coverage()
    computed_sources = _freeze((issues, receipts, manufactures, manufacture_batches, loss_batches))
    session.finish(additional_guard=wages["finish_guard"])
    if _freeze((issues, receipts, manufactures, manufacture_batches, loss_batches)) != computed_sources:
        _deny()
    return {"issues": issues["rows"], "receipts": receipts["rows"], "manufactures": manufactures, "totals": {
        "issue_material": issues["totals"]["material"], "receipt_material": receipts["totals"]["material"],
        "manufacture_material": manufacture_material, "manufacture_other": manufacture_other,
        "material": issues["totals"]["material"] + receipts["totals"]["material"] + manufacture_material,
        "processing": receipts["totals"]["processing"] + wages["total"], "other": receipts["totals"]["other"] + manufacture_other},
        "receipt_valuation": receipts["receipt_valuation"],
        "wages": wages["rows"], "manufacture_batches": manufacture_batches, "loss_batches": loss_batches,
        "currency": session.currency}
