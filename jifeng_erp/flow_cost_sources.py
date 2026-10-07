"""Complete, permission checked native sources for one flow's explicit cost load.

Raw reads are internal completeness guards, never a replacement for native
document/field access. A missing source rejects the whole result. Two serial
passes detect request-local changes; they do not promise a concurrent snapshot.
This service writes nothing. Primary receipt fees include the signed received
quantity (accepted, rejected and loss). Persisted amounts must support that exact
split; secondary items, LCV and ambiguous allocation reject the simplified load.
The three fee categories are not a claim of native stock valuation or GL totals.
"""

from datetime import timedelta, time
from decimal import Decimal, InvalidOperation
import json

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import flt

from jifeng_erp.costing import CostingError, receipt_batch_quantities
from jifeng_erp import subcontract_stage


FLOW = "Jifeng Flow Order"
SCR = "Subcontracting Receipt"
SE = "Stock Entry"
WO = "Work Order"
SLE = "Stock Ledger Entry"
REPOST = "Repost Item Valuation"
IDENTITY = {"name", "modified", "docstatus"}
CHILD = {"name", "modified", "parent", "parenttype", "parentfield", "idx"}
PARENTS = {
    FLOW: IDENTITY | {"company", "item_code"},
    SCR: IDENTITY | {"company", "posting_date", "posting_time", "is_return", "return_against",
                     "status", "supplier_warehouse"},
    SE: IDENTITY | {"company", "posting_date", "posting_time", "purpose", "stock_entry_type", "jf_material_issue_flow"},
    SLE: IDENTITY | {"creation", "company", "item_code", "warehouse", "stock_uom", "posting_date", "posting_time",
                     "voucher_type", "voucher_no", "voucher_detail_no", "actual_qty",
                     "stock_value_difference", "is_cancelled", "is_adjustment_entry"},
    "Item": {"name", "modified", "stock_uom", "is_stock_item"},
    "Warehouse": {"name", "modified", "company", "is_group"},
    "Currency": {"name", "modified"},
    WO: IDENTITY | {"company", "production_item", "jf_flow_order", "track_semi_finished_goods"},
}
# These native sources are read only when an actual Job Card stage is present.
PARENTS.update({kind: fields for kind, fields in subcontract_stage.PARENTS.items() if kind not in PARENTS})

# These fields are needed only when reading a manufacturing source. Existing
# issue/receipt RPCs must not acquire new manufacturing money-field requirements.
MANUFACTURE_FIELDS = {"work_order", "job_card", "total_additional_costs"}
MANUFACTURE_ITEM_FIELDS = {"is_finished_item", "secondary_item_type", "valuation_type",
                           "additional_cost", "landed_cost_voucher_amount"}
TABLES = {
    FLOW: (("materials", "Jifeng Material Line", CHILD | {"item_code"}),),
    SCR: (
        ("items", "Subcontracting Receipt Item", CHILD | {
            "item_code", "jf_flow_order", "qty", "rejected_qty", "process_loss_qty", "stock_uom",
            "conversion_factor", "warehouse", "rejected_warehouse", "subcontracting_receipt_item",
            "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty", "rm_supp_cost",
            "received_qty", "bom", "secondary_item_type", "valuation_type", "reference_name",
            "secondary_items_cost_per_qty", "landed_cost_voucher_amount", "rate", "amount"}),
        ("supplied_items", "Subcontracting Receipt Supplied Item", CHILD | {
            "main_item_code", "rm_item_code", "reference_name", "consumed_qty", "stock_uom", "rate", "amount"}),
        ("additional_costs", "Landed Cost Taxes and Charges", CHILD | {
            "amount", "account_currency", "exchange_rate", "base_amount"}),
    ),
    SE: (("items", "Stock Entry Detail", CHILD | {
        "item_code", "jf_flow_order", "qty", "uom", "stock_uom", "conversion_factor", "transfer_qty",
        "s_warehouse", "t_warehouse"}),),
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


def _authorization():
    user = frappe.session.user
    doctypes = sorted(set(PARENTS) | {child for tables in TABLES.values() for _, child, _ in tables}
                      | {child for _, child, _ in subcontract_stage.TABLES.values()} | {"Company", REPOST})
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
        self.manufacture_names = set()
        self.ledger = {}
        self.repost_state = None
        self.receipt_fees = {}

    def raw(self, doctype, filters, **options):
        rows = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                              limit_page_length=0, **options)
        self.queries.append((doctype, filters, None, None, options, _freeze(rows), None))
        return rows

    def records(self, doctype, filters, fields, parenttype=None, **options):
        _fields(doctype, fields, parenttype)
        native_options = {"parent_doctype": parenttype} if parenttype else {}
        raw = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                             limit_page_length=0, **options)
        try:
            rows = frappe.get_list(doctype, filters=filters, fields=sorted(fields), order_by="name asc",
                                   limit_page_length=0, **native_options, **options)
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
        rows = self.records(doctype, {"name": name}, PARENTS[doctype])
        if len(rows) != 1:
            _deny()
        try:
            doc = frappe.get_doc(doctype, name)
        except frappe.DoesNotExistError:
            _invalid()
        doc.check_permission("read")
        if any(doc.get(field) != value for field, value in rows[0].items()):
            _invalid()
        if self.company is not None and "company" in PARENTS[doctype] and doc.get("company") != self.company:
            _invalid()
        self.documents[key] = (doc, _freeze(doc), "read", PARENTS[doctype])
        for fieldname, childtype, fields in TABLES.get(doctype, ()):
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
                    or _number(stages[0]["sequence_id"]) >= _number(card.get("sequence_id"))):
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
                              PARENTS[SCR], distinct=True)
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
                "return_against": ["in", sorted(originals)]}, PARENTS[SCR])
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
        if any(row.get(field) in (None, "") for field in
               ("received_qty", "rm_supp_cost", "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty", "amount")):
            frappe.throw("回货的已过账材料或费用来源缺失，不能按零成本补齐")
        received = _number(row["received_qty"])
        quantity = sum((_number(row.get(field)) for field in ("qty", "rejected_qty", "process_loss_qty")), Decimal(0))
        if (not received or not _same_quantity(received, quantity, actual, "received_qty")
                or (doc.get("is_return") and received >= 0) or (not doc.get("is_return") and received <= 0)
                or (not _number(row.get("qty")) and not _number(row.get("rejected_qty")))):
            _invalid()
        supplied = [item for item in self.table(SCR, doc.name, "supplied_items")
                    if item.get("reference_name") == row["name"]]
        total = Decimal(0)
        for item in supplied:
            if item.get("rate") in (None, "") or item.get("amount") in (None, ""):
                _invalid()
            child = next(child for child in doc.get("supplied_items") if child.name == item["name"])
            if not _same_quantity(item["amount"], _number(item.get("consumed_qty")) * _number(item["rate"]), child, "amount"):
                _invalid()
            total += _number(item["amount"])
        material = _number(row["rm_supp_cost"])
        if (not _same_quantity(material, total, actual, "rm_supp_cost")
                or not _same_quantity(_number(row["rm_cost_per_qty"]) * received, material, actual, "rm_supp_cost")):
            frappe.throw("回货已过账材料费用与精确耗料来源不一致，不能提供部分成本")
        rates = [_number(row[field]) for field in ("rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty")]
        if any(rate < 0 for rate in rates) or (material > 0) != (received > 0) and material:
            _invalid()
        processing, other = rates[1] * received, rates[2] * received
        if not _same_quantity(row["amount"], material + processing + other, actual, "amount"):
            frappe.throw("回货已过账金额包含无法准确拆分的分摊或费用，不能简化为三项流程成本")
        # Rates are persisted in Company currency. A foreign account amount in
        # additional_costs is not silently treated as company currency.
        for charge in self.table(SCR, doc.name, "additional_costs"):
            if _number(charge.get("amount")) and (charge.get("account_currency") != self.currency
                    or _number(charge.get("exchange_rate")) != 1):
                frappe.throw("回货附加费用币种不能明确拆分为公司本币，请核对原生来源")
        stage = self.receipt_stages.get(row["name"])
        if stage:
            actual_material, external_material = Decimal(0), Decimal(0)
            for item in supplied:
                ledger = [entry for entry in self.ledger[SCR, doc.name] if entry["voucher_detail_no"] == item["name"]]
                actual_material -= sum((_number(entry["stock_value_difference"]) for entry in ledger), Decimal(0))
                if _number(item.get("consumed_qty")):
                    external_material += self._stage_event(stage, doc, item["name"], item["rm_item_code"], ledger)
            output = [entry for entry in self.ledger[SCR, doc.name] if entry["voucher_detail_no"] == row["name"]]
            output_value = self._stage_event(stage, doc, row["name"], row["item_code"], output, output=True)
            if (not _same_quantity(actual_material, material, actual, "rm_supp_cost")
                    or not _same_quantity(output_value, material + processing + other, actual, "amount")):
                frappe.throw("阶段回货库存净价值与精确耗料、委外及附加费用不一致，不能简化成本")
            material = external_material
        return {"material": material, "processing": processing, "other": other}

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

    def load_issues(self):
        if self.issues is not None:
            return self.issues
        parents = {}
        for field in ("purpose", "stock_entry_type"):
            found = self.records(SE, [[SE, "docstatus", "=", 1], [SE, field, "=", "Material Issue"],
                ["Stock Entry Detail", "jf_flow_order", "=", self.flow.name]], PARENTS[SE], distinct=True)
            parents.update((row["name"], row) for row in found)
        header_linked = self.records(SE, {"docstatus": 1, "jf_material_issue_flow": self.flow.name}, PARENTS[SE])
        parents.update((row["name"], row) for row in header_linked)
        selected = []
        for row in parents.values():
            doc = self.source(SE, row["name"])
            self._posted(SE, doc)
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
        self.issues = selected
        return self.issues

    def _manufacture_source(self, name):
        doc = self.source(SE, name)
        fields = PARENTS[SE] | MANUFACTURE_FIELDS
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
        result = []
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
            result.append({"manufacture": name, "work_order": work.name, "qty": output_qty,
                           "material": material, "other": other})
        self.manufactures = result
        return result

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

    def finish(self):
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
                native_options = {"parent_doctype": parenttype} if parenttype else {}
                rows = frappe.get_list(doctype, filters=filters, fields=list(fields), order_by="name asc",
                                       limit_page_length=0, **native_options, **options)
                if _freeze(rows) != native_state:
                    _deny()
            raw = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                                 limit_page_length=0, **options)
            if _freeze(raw) != raw_state:
                _deny()
        self._check_documents(loaded)
        # Catch source edits performed by a later list hook, including edits
        # that do not update modified. Authorization metadata is also uncached.
        for doctype, filters, _fields_read, _parenttype, options, raw_state, _native_state in self.queries:
            raw = frappe.get_all(doctype, filters=filters, fields=["*"], order_by="name asc",
                                 limit_page_length=0, **options)
            if _freeze(raw) != raw_state:
                _deny()
        if self._reposting() != self.repost_state or _authorization() != self.authorization:
            _deny()

    def _check_documents(self, loaded):
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
                _fields(childtype, child_fields, doctype)
            if doctype == SE and name in self.manufacture_names:
                _fields("Stock Entry Detail", TABLES[SE][0][2] | MANUFACTURE_ITEM_FIELDS, SE)
                fieldname, childtype, fields = MANUFACTURE_CHARGES
                _table(SE, fieldname, childtype)
                _fields(childtype, fields, SE)
            if (doctype, name) in self.extra_tables:
                fieldname, childtype, fields = self.extra_tables[doctype, name]
                _table(doctype, fieldname, childtype)
                _fields(childtype, fields, doctype)


def _receipt_dto(session):
    _docs, _targets, selected, _returned = session.load_receipts()
    rows = []
    for row in selected:
        rows.append({"receipt": row["parent"], "qty": row.get("qty"),
                     **session.receipt_fees[row["name"]]})
    return {"rows": rows, "totals": {field: sum((row[field] for row in rows), Decimal(0))
        for field in ("material", "processing", "other")}, "currency": session.currency}


def _issue_dto(session):
    rows = [{"issue": row["voucher_no"], "item_code": row["item_code"],
             "qty": -_number(row.get("actual_qty")), "material": -_number(row.get("stock_value_difference"))}
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


def posted_flow_costs(flow_order):
    session = _Session(flow_order, include_stages=True)
    issues = _issue_dto(session)
    receipts = _receipt_dto(session)
    manufactures = session.load_manufactures()
    manufacture_material = sum((row["material"] for row in manufactures), Decimal(0))
    manufacture_other = sum((row["other"] for row in manufactures), Decimal(0))
    session.finish()
    return {"issues": issues["rows"], "receipts": receipts["rows"], "manufactures": manufactures, "totals": {
        "issue_material": issues["totals"]["material"], "receipt_material": receipts["totals"]["material"],
        "manufacture_material": manufacture_material, "manufacture_other": manufacture_other,
        "material": issues["totals"]["material"] + receipts["totals"]["material"] + manufacture_material,
        "processing": receipts["totals"]["processing"], "other": receipts["totals"]["other"] + manufacture_other},
        "currency": session.currency}
