"""Read exact persisted sales fulfilment sources, without calculating net totals.

This is an independent read service. It makes no claim about the unverified old
sales tracking page. Native order delivery/billing and invoice outstanding fields
retain their own units, currencies and meanings. Operation output is not finished
stock; DN and stock-updating SI are separate sources, never added together here.
"""

from datetime import date, datetime
from decimal import Decimal

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate

from jifeng_erp.fulfillment import FLOW, RETURN_CHILD, SOURCES


IDENTITY = {"name", "modified", "docstatus"}
CHILD = {"name", "modified", "parent", "parenttype", "parentfield", "idx"}
COMMON = IDENTITY | {"company", "customer"}
SO = "Sales Order"
DN = "Delivery Note"
SI = "Sales Invoice"
PLE = "Payment Ledger Entry"
LIMIT = 20
SOURCE_LIMIT = 1000
PARENTS = {
    SO: (COMMON | {"status", "transaction_date"},
         {"delivery_date", "currency", "per_delivered", "per_billed", "delivery_status", "billing_status"}),
    FLOW: (IDENTITY | {"company", "sales_order", "sales_order_item", "item_code", "source_changed", "work_order"},
           {"planned_qty", "stock_uom", "source_qty", "source_uom", "source_stock_uom", "source_conversion_factor",
            "source_item_code", "source_status", "source_change_note", "status"}),
    "Work Order": (IDENTITY | {"company", "sales_order", "sales_order_item", "production_item", "jf_flow_order"},
                   {"status", "stock_uom", "qty", "produced_qty", "process_loss_qty", "planned_start_date",
                    "planned_end_date", "actual_start_date", "actual_end_date"}),
    "Stock Entry": (IDENTITY | {"company", "work_order", "purpose", "posting_date"},
                    {"fg_completed_qty", "process_loss_qty", "stock_entry_type"}),
    DN: (COMMON | {"is_return", "return_against", "posting_date", "status"},
         {"currency", "per_billed", "per_returned"}),
    SI: (COMMON | {"is_return", "return_against", "posting_date", "update_stock", "status", "debit_to",
                   "outstanding_amount", "party_account_currency"},
         {"currency", "due_date", "update_outstanding_for_self"}),
    "Payment Entry": (IDENTITY | {"company", "party_type", "party", "payment_type", "paid_from", "paid_to"},
                      {"posting_date", "status", "paid_from_account_currency", "paid_to_account_currency"}),
    "Journal Entry": (IDENTITY | {"company", "posting_date"}, {"voucher_type", "multi_currency"}),
    "Account": ({"name", "modified", "company", "account_type", "account_currency", "is_group"}, set()),
    "Item": ({"name", "modified"}, set()),
    "Customer": ({"name", "modified"}, set()),
    PLE: ({"name", "modified", "company", "party_type", "party", "account", "account_type", "account_currency",
           "posting_date", "voucher_type", "voucher_no", "against_voucher_type", "against_voucher_no",
           "amount", "amount_in_account_currency", "delinked"}, {"due_date"}),
}
QUANTITY = {"item_name", "qty", "uom", "stock_uom", "stock_qty", "conversion_factor"}
TABLES = {
    FLOW: ("materials", "Jifeng Material Line", CHILD | {"item_code"}, set()),
    SO: ("items", "Sales Order Item", CHILD | {"item_code"},
         QUANTITY | {"delivery_date", "delivered_qty", "billed_amt", "returned_qty", "jf_make_flow"}),
    DN: ("items", "Delivery Note Item", CHILD | {"item_code", "against_sales_order", "so_detail",
                                                "against_sales_invoice", "si_detail", "dn_detail", "jf_flow_order"},
         QUANTITY | {"billed_amt"}),
    SI: ("items", "Sales Invoice Item", CHILD | {"item_code", "sales_order", "so_detail", "delivery_note",
                                                "dn_detail", "sales_invoice_item", "jf_flow_order"},
         QUANTITY),
    "Stock Entry": ("items", "Stock Entry Detail", CHILD | {"item_code", "is_finished_item", "jf_flow_order"},
                    {"qty", "uom", "stock_uom", "transfer_qty"}),
    "Payment Entry": ("references", "Payment Entry Reference", CHILD | {"reference_doctype", "reference_name"},
                      {"allocated_amount", "account", "exchange_rate"}),
    "Journal Entry": ("accounts", "Journal Entry Account", CHILD | {"account", "party_type", "party",
                                                                     "reference_type", "reference_name"},
                      {"account_currency", "debit_in_account_currency", "credit_in_account_currency"}),
}


def _deny():
    frappe.throw("无法完整读取所选销售订单的来源或字段，不能提供部分履行或收款状态", frappe.PermissionError)


def _invalid():
    frappe.throw("销售履行的精确来源链不完整或范围不一致，请核对原生单据")


def _scalar(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else str(value) if isinstance(value, Decimal) else value


def _freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return type(value).__name__, repr(value)


def _columns(doctype, parenttype=None):
    readable = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type="read", ignore_virtual=True))
    if frappe.session.user == "Administrator":
        return readable
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype, user=frappe.session.user))
    return readable - {df.fieldname for df in meta.fields if df.get("mask") and (df.permlevel or 0) not in levels}


def _fields(doctype, required, optional=(), parenttype=None):
    columns = _columns(doctype, parenttype)
    if not set(required).issubset(columns):
        _deny()
    return sorted((set(required) | set(optional)) & columns)


def _table(parenttype, fieldname):
    meta = frappe.get_meta(parenttype)
    df = meta.get_field(fieldname)
    levels = meta.get_permlevel_access(permission_type="read", user=frappe.session.user)
    mask = meta.get_permlevel_access(permission_type="mask", user=frappe.session.user)
    if (not df or df.fieldtype != "Table" or (df.permlevel or 0) not in levels
            or (frappe.session.user != "Administrator" and df.get("mask") and (df.permlevel or 0) not in mask)):
        _deny()


def _scope(company):
    if frappe.session.user == "Guest":
        _deny()
    if frappe.conf.get("read_from_replica"):
        frappe.throw("销售履行与收款来源需要主库口径，请在主库完成核对后查询")
    if not isinstance(company, str) or not company or company != company.strip():
        frappe.throw("请选择明确的公司")
    try:
        frappe.get_doc("Company", company).check_permission("select")
    except frappe.DoesNotExistError:
        _deny()


def _authorization():
    """Internal raw permission fingerprint catches changes hidden by caches."""
    user = frappe.session.user
    doctypes = sorted(set(PARENTS) | {row[1] for row in TABLES.values()} | {"Company"})
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
    # No secrets or user profile are retained in this internal fingerprint.
    state.append(("User", _freeze(frappe.get_all("User", filters={"name": user},
        fields=["name", "enabled", "user_type"], order_by="name asc", limit_page_length=0))))
    return user, tuple(state)


def _reposting(keys):
    """Related pending/failed reposts cannot support a complete native state."""
    if not keys:
        return ()
    states = []
    for parenttype, childtype, status_field, completed in (
        ("Repost Payment Ledger", "Repost Payment Ledger Items", "repost_status", {"Completed"}),
        ("Repost Accounting Ledger", "Repost Accounting Ledger Items", "status", {"Completed", "Cancelled"}),
    ):
        rows = frappe.get_all(childtype, filters={"parenttype": parenttype,
            "voucher_type": ["in", sorted({key[0] for key in keys})],
            "voucher_no": ["in", sorted({key[1] for key in keys})]},
            fields=["*"], order_by="name asc", limit_page_length=0)
        rows = [row for row in rows if (row.get("voucher_type"), row.get("voucher_no")) in keys]
        states.append((childtype, _freeze(rows)))
        for parent in sorted({row["parent"] for row in rows}):
            jobs = frappe.get_all(parenttype, filters={"name": parent}, fields=["*"],
                                  order_by="name asc", limit_page_length=0)
            if len(jobs) != 1:
                _invalid()
            if jobs[0]["docstatus"] == 1 and jobs[0].get(status_field) not in completed:
                frappe.throw("相关收款凭证的原生账簿重过账尚未完成或已失败，请完成后查询")
            states.append((parenttype, _freeze(jobs)))
    return tuple(states)


class _Graph:
    def __init__(self, company):
        self.company = company
        self.queries = []
        self.documents = {}
        self.children = {}
        self.customer = None
        self.authorization = _authorization()
        self.reposts = []

    def records(self, doctype, filters, required, optional=(), parenttype=None, complete=True, raw_filters=None):
        if parenttype:
            if parenttype not in TABLES or TABLES[parenttype][1] != doctype:
                _invalid()
            fieldname = TABLES[parenttype][0]
            _table(parenttype, fieldname)
            filters = {"parenttype": parenttype, "parentfield": fieldname, **filters}
            if filters["parenttype"] != parenttype or filters["parentfield"] != fieldname:
                _invalid()
        fields = _fields(doctype, required, optional, parenttype)
        options = {"parent_doctype": parenttype} if parenttype else {}
        try:
            rows = frappe.get_list(doctype, filters=filters, fields=fields, order_by="name asc",
                                   limit_page_length=SOURCE_LIMIT + 1, **options)
        except frappe.PermissionError:
            _deny()
        if len(rows) > SOURCE_LIMIT:
            frappe.throw("履行来源较多，请缩小订单查询范围")
        if any(not set(required).issubset(row) for row in rows):
            _deny()
        raw_filters = dict(filters if raw_filters is None else raw_filters)
        raw = frappe.get_all(doctype, filters=raw_filters, fields=["*"], order_by="name asc",
                             limit_page_length=SOURCE_LIMIT + 1) if complete else None
        if raw is not None and {row["name"] for row in raw} != {row["name"] for row in rows}:
            _deny()
        if len({row["name"] for row in rows}) != len(rows):
            _invalid()
        self.queries.append((doctype, dict(filters), raw_filters, set(required), tuple(fields), parenttype,
                             _freeze(rows), _freeze(raw)))
        return rows

    def source(self, doctype, name):
        key = (doctype, name)
        if key in self.documents:
            return self.documents[key][1]
        if not name or doctype not in PARENTS:
            _invalid()
        required, optional = PARENTS[doctype]
        rows = self.records(doctype, {"name": name}, required, optional)
        if len(rows) != 1:
            _deny()
        row = rows[0]
        try:
            document = frappe.get_doc(doctype, name)
        except frappe.DoesNotExistError:
            _invalid()
        if not document.has_permission("read"):
            _deny()
        if any(document.get(field) != value for field, value in row.items()):
            _invalid()
        if "company" in row and row["company"] != self.company:
            _invalid()
        if "customer" in row and self.customer is not None and row["customer"] != self.customer:
            _invalid()
        self.documents[key] = (document, row)
        if doctype in TABLES:
            fieldname, childtype, child_required, child_optional = TABLES[doctype]
            _table(doctype, fieldname)
            children = self.records(childtype, {"parent": name, "parenttype": doctype, "parentfield": fieldname},
                                    child_required, child_optional, doctype,
                                    raw_filters={"parent": name} if doctype in ("Stock Entry", FLOW) else None)
            if {item.name for item in document.get(fieldname) or []} != {item["name"] for item in children}:
                _invalid()
            actual = {item.name: item for item in document.get(fieldname) or []}
            if any(any(actual[item["name"]].get(field) != value for field, value in item.items()) for item in children):
                _invalid()
            self.children[key] = children
        return row

    def detail(self, doctype, parent, child):
        document = self.source(doctype, parent)
        rows = [row for row in self.children.get((doctype, parent), []) if row["name"] == child]
        if len(rows) != 1:
            _invalid()
        self.source("Item", rows[0]["item_code"])
        return document, rows[0]

    def named(self, doctype, filters):
        return [row["name"] for row in self.records(doctype, filters, {"name", "modified"})]

    def finish(self):
        # Complete all document reloads first. A later load may change a former
        # document's permission; checks run across the entire accumulated result.
        try:
            loaded = {key: frappe.get_doc(*key) for key in self.documents}
        except frappe.DoesNotExistError:
            _deny()
        _scope(self.company)
        for (doctype, name), (_old, row) in self.documents.items():
            document = loaded[doctype, name]
            if (not document.has_permission("read") or not set(row).issubset(_columns(doctype))
                    or any(document.get(field) != value for field, value in row.items())):
                _deny()
            if doctype in TABLES:
                fieldname, childtype, _required, _optional = TABLES[doctype]
                _table(doctype, fieldname)
                actual = {item.name: item for item in document.get(fieldname) or []}
                projected = self.children[doctype, name]
                if set(actual) != {item["name"] for item in projected}:
                    _deny()
                for item in projected:
                    if (not set(item).issubset(_columns(childtype, doctype))
                            or any(actual[item["name"]].get(field) != value for field, value in item.items())):
                        _deny()
        # Recheck membership and native projections, including empty queries:
        # a new shipment/payment must not leave a formerly complete view stale.
        for doctype, filters, raw_filters, required, fields, parenttype, state, raw_state in self.queries:
            if not set(fields).issubset(_fields(doctype, required, fields, parenttype)):
                _deny()
            options = {"parent_doctype": parenttype} if parenttype else {}
            current = frappe.get_list(doctype, filters=filters, fields=list(fields), order_by="name asc",
                                      limit_page_length=SOURCE_LIMIT + 1, **options)
            raw = frappe.get_all(doctype, filters=raw_filters, fields=["*"], order_by="name asc",
                                 limit_page_length=SOURCE_LIMIT + 1) if raw_state != _freeze(None) else None
            if _freeze(current) != state or _freeze(raw) != raw_state:
                _deny()
        # Queries themselves may invoke dynamic permission hooks. One final
        # field/document pass prevents those hooks from invalidating earlier DTOs.
        _scope(self.company)
        for key, (_old, row) in self.documents.items():
            if not loaded[key].has_permission("read") or not set(row).issubset(_columns(key[0])):
                _deny()
            if key[0] in TABLES:
                fieldname, childtype, _required, _optional = TABLES[key[0]]
                _table(key[0], fieldname)
                if any(not set(child).issubset(_columns(childtype, key[0])) for child in self.children[key]):
                    _deny()
        # Later native queries and permission hooks can edit an earlier source
        # after its own query was checked. Recheck all raw source values and
        # memberships after those hooks, including changes without modified.
        # This is a serial consistency guard, not a concurrent snapshot claim.
        for doctype, _filters, raw_filters, _required, _fields_read, _parenttype, _state, raw_state in self.queries:
            if raw_state != _freeze(None):
                raw = frappe.get_all(doctype, filters=raw_filters, fields=["*"], order_by="name asc",
                                     limit_page_length=SOURCE_LIMIT + 1)
                if _freeze(raw) != raw_state:
                    _deny()
        for keys, state in self.reposts:
            if _reposting(keys) != state:
                _deny()
        if _authorization() != self.authorization:
            _deny()


def _resolve(graph, doctype, parent, child, visiting=None):
    visiting = set() if visiting is None else visiting
    key = (doctype, parent, child)
    if key in visiting:
        _invalid()
    visiting.add(key)
    try:
        document, row = graph.detail(doctype, parent, child)
        if doctype == SO:
            return parent, child
        origins = []
        returned_upstream = False
        if document["is_return"] and document["return_against"]:
            original, original_row = graph.detail(doctype, document["return_against"], row[RETURN_CHILD[doctype]])
            if original["docstatus"] not in (1, 2) or original["is_return"] or original_row["item_code"] != row["item_code"]:
                _invalid()
            origins.append(_resolve(graph, doctype, original["name"], original_row["name"], visiting))
            if row["jf_flow_order"] != original_row["jf_flow_order"]:
                _invalid()
        for source_type, parent_field, child_field in SOURCES[doctype]:
            if row[parent_field] or row[child_field]:
                if not row[parent_field] or not row[child_field]:
                    _invalid()
                upstream, upstream_row = graph.detail(source_type, row[parent_field], row[child_field])
                if (upstream["docstatus"] not in (1, 2) or upstream_row["item_code"] != row["item_code"]
                        or upstream["customer"] != document["customer"]):
                    _invalid()
                origins.append(_resolve(graph, source_type, upstream["name"], upstream_row["name"], visiting))
                if source_type != SO and row["jf_flow_order"] != upstream_row["jf_flow_order"]:
                    _invalid()
                returned_upstream = returned_upstream or (source_type != SO and bool(upstream.get("is_return")))
        if document["is_return"] and not document["return_against"] and not returned_upstream:
            _invalid()
        if not origins or len(set(origins)) != 1:
            _invalid()
        if row["jf_flow_order"]:
            flow = graph.source(FLOW, row["jf_flow_order"])
            if ((flow["sales_order"], flow["sales_order_item"]) != origins[0]
                    or flow["item_code"] != row["item_code"]):
                _invalid()
        return origins[0]
    finally:
        visiting.remove(key)


def _manufacturing_order_source(graph, order, detail):
    # A shared stock voucher may contain another readable order/customer. Read
    # its exact counterpart without changing the selected order's receivables
    # scope. Nothing from this counterpart is added to the result DTO.
    if not order or not detail:
        _invalid()
    customer = graph.customer
    graph.customer = None
    try:
        document, row = graph.detail(SO, order, detail)
        graph.source("Customer", document["customer"])
        return document, row
    finally:
        graph.customer = customer


def _manufacturing_flow_source(graph, name):
    flow = graph.source(FLOW, name)
    graph.source("Item", flow["item_code"])
    for row in graph.children[FLOW, name]:
        graph.source("Item", row["item_code"])
    if flow["sales_order"] or flow["sales_order_item"]:
        _order, detail = _manufacturing_order_source(graph, flow["sales_order"], flow["sales_order_item"])
        if not flow["source_changed"] and flow["item_code"] != detail["item_code"]:
            _invalid()
    return flow


def _manufacturing_work_source(graph, name):
    work = graph.source("Work Order", name)
    graph.source("Item", work["production_item"])
    if work["jf_flow_order"]:
        flow = _manufacturing_flow_source(graph, work["jf_flow_order"])
        if (work["production_item"] != flow["item_code"]
                or (work["sales_order"], work["sales_order_item"])
                != (flow["sales_order"], flow["sales_order_item"])):
            _invalid()
    elif work["sales_order"] or work["sales_order_item"]:
        _order, detail = _manufacturing_order_source(graph, work["sales_order"], work["sales_order_item"])
        if work["production_item"] != detail["item_code"]:
            _invalid()
    return work


def _manufacturing_stock_source(graph, name):
    entry = graph.source("Stock Entry", name)
    purpose = frappe.get_meta("Stock Entry").get_field("purpose")
    if not purpose or not isinstance(purpose.options, str) or entry["purpose"] not in purpose.options.split("\n"):
        _invalid()
    work = _manufacturing_work_source(graph, entry["work_order"]) if entry["work_order"] else None
    finished = []
    for row in graph.children["Stock Entry", name]:
        graph.source("Item", row["item_code"])
        if row["is_finished_item"] not in (0, 1):
            _invalid()
        if row["is_finished_item"]:
            finished.append(row)
        if row["jf_flow_order"]:
            if work and work["jf_flow_order"] and row["jf_flow_order"] != work["jf_flow_order"]:
                _invalid()
            flow = _manufacturing_flow_source(graph, row["jf_flow_order"])
            materials = {item["item_code"] for item in graph.children[FLOW, flow["name"]]}
            if row["item_code"] not in materials | {flow["item_code"]}:
                _invalid()
            if entry["purpose"] == "Manufacture":
                if row["is_finished_item"] and row["item_code"] != flow["item_code"]:
                    _invalid()
    if entry["purpose"] == "Manufacture":
        if not finished or len({row["item_code"] for row in finished}) != 1:
            _invalid()
        if work and any(row["item_code"] != work["production_item"]
                        or row["jf_flow_order"] != work["jf_flow_order"] for row in finished):
            _invalid()
    return entry, finished


def _manufacturing(graph, order, details, flows):
    names = set(graph.named("Work Order", {"sales_order": order}))
    names.update(graph.named("Work Order", {"sales_order_item": ["in", list(details)]}))
    for flow in flows.values():
        names.update(graph.named("Work Order", {"jf_flow_order": flow["name"]}))
        if flow["work_order"]:
            names.add(flow["work_order"])
    # Discover all exact Flow rows, including raw material rows and malformed
    # finished flags/purposes. Filtering to finished Manufacture rows here
    # would let an edited flag or parent silently hide a reverse source.
    reverse_filters = {"jf_flow_order": ["in", sorted(flows)]}
    # Do not send an empty IN to native SQL: some native builders normalize it
    # to an empty-string match. The complete Flow queries already guard a
    # previously empty Flow scope against late additions.
    candidates = graph.records("Stock Entry Detail", reverse_filters,
                               TABLES["Stock Entry"][2], parenttype="Stock Entry", raw_filters=reverse_filters) if flows else []
    independent = []
    for name in sorted({row["parent"] for row in candidates}):
        entry, finished = _manufacturing_stock_source(graph, name)
        if entry["purpose"] != "Manufacture":
            # Repack, Disassemble and subcontracting stock can legitimately
            # carry finished flags, with different stock meanings.
            continue
        selected = [row for row in finished if row["jf_flow_order"] in flows]
        if not selected:
            continue  # Current Flow was only a raw material source.
        if entry["work_order"]:
            names.add(entry["work_order"])
        else:
            independent.append({"document": entry, "finished_items": selected})
    result = []
    for name in sorted(names):
        work = graph.source("Work Order", name)
        if work["sales_order"] != order or work["sales_order_item"] not in details:
            _invalid()
        if work["jf_flow_order"]:
            flow = graph.source(FLOW, work["jf_flow_order"])
            if (flow["sales_order"] != order or flow["sales_order_item"] != work["sales_order_item"]
                    or flow["item_code"] != work["production_item"]):
                _invalid()
        elif work["production_item"] != details[work["sales_order_item"]]["item_code"]:
            _invalid()
        if any(flow["work_order"] == name and work["jf_flow_order"] != flow["name"] for flow in flows.values()):
            _invalid()
        graph.source("Item", work["production_item"])
        entries = []
        for entry_name in graph.named("Stock Entry", {"work_order": name, "purpose": "Manufacture"}):
            entry, finished = _manufacturing_stock_source(graph, entry_name)
            if entry["purpose"] != "Manufacture" or entry["work_order"] != name:
                _invalid()
            if not finished or any(row["item_code"] != work["production_item"]
                                   or row["jf_flow_order"] != work["jf_flow_order"] for row in finished):
                _invalid()
            entries.append({"document": entry, "finished_items": finished})
        result.append({"document": work, "manufacture_entries": entries})
    return result, independent


def _shipments(graph, order, details, flows):
    selected = {DN: set(), SI: set()}
    for doctype in (DN, SI):
        childtype = doctype + " Item"
        parent_field = SOURCES[doctype][0][1]
        for filters in ({parent_field: order}, {"so_detail": ["in", list(details)]},
                        *({"jf_flow_order": flow} for flow in flows)):
            filters = {"parenttype": doctype, "parentfield": "items", **filters}
            selected[doctype].update(row["name"] for row in graph.records(childtype, filters,
                                     TABLES[doctype][2], parenttype=doctype))
    inspected = set()
    while True:
        pending = [(doctype, row) for doctype in (DN, SI) for row in selected[doctype]
                   if (doctype, row) not in inspected]
        if not pending:
            break
        for doctype, child in sorted(pending):
            found = graph.records(doctype + " Item", {"name": child}, TABLES[doctype][2], parenttype=doctype)
            if len(found) != 1:
                _invalid()
            row = found[0]
            origin = _resolve(graph, doctype, row["parent"], child)
            if origin[0] != order or origin[1] not in details:
                _invalid()
            inspected.add((doctype, child))
        # Exact downstream child links and every return against each relevant
        # parent are part of the closure. Missing return child links reject.
        for source_type in (DN, SI):
            ids = sorted(selected[source_type])
            if not ids:
                continue
            other = SI if source_type == DN else DN
            link_field = "dn_detail" if source_type == DN else "si_detail"
            query = {"parenttype": other, "parentfield": "items", link_field: ["in", ids]}
            selected[other].update(row["name"] for row in graph.records(other + " Item", query,
                                      TABLES[other][2], parenttype=other))
            parents = {graph.records(source_type + " Item", {"name": child}, TABLES[source_type][2],
                                    parenttype=source_type)[0]["parent"] for child in ids}
            for name in graph.named(source_type, {"is_return": 1, "return_against": ["in", sorted(parents)]}):
                graph.source(source_type, name)
                for row in graph.children[source_type, name]:
                    original = row[RETURN_CHILD[source_type]]
                    if not original:
                        _invalid()
                    if original in selected[source_type]:
                        selected[source_type].add(row["name"])
    result = {DN: [], SI: []}
    for doctype in (DN, SI):
        parents = {}
        for child in sorted(selected[doctype]):
            raw = graph.records(doctype + " Item", {"name": child}, TABLES[doctype][2], parenttype=doctype)[0]
            parents.setdefault(raw["parent"], set()).add(child)
        for parent, ids in sorted(parents.items()):
            result[doctype].append({"document": graph.source(doctype, parent),
                                    "items": [row for row in graph.children[doctype, parent] if row["name"] in ids]})
    return result


def _receivables(graph, order, invoices):
    keys = {(SO, order)} | {(SI, name) for name in invoices}
    seen, entries = set(), {}
    while keys - seen:
        doctype, name = sorted(keys - seen)[0]
        seen.add((doctype, name))
        if doctype not in (SI, SO, "Payment Entry", "Journal Entry"):
            _invalid()
        source = graph.source(doctype, name)
        if doctype in (SI, SO) and source["customer"] != graph.customer:
            _invalid()
        if doctype == SI:
            if source["is_return"] and source["return_against"]:
                keys.add((SI, source["return_against"]))
            graph.source("Account", source["debit_to"])
        elif doctype == "Payment Entry":
            if source["party_type"] != "Customer" or source["party"] != graph.customer:
                _invalid()
            for field in ("paid_from", "paid_to"):
                graph.source("Account", source[field])
            keys.update((row["reference_doctype"], row["reference_name"])
                        for row in graph.children[doctype, name] if row["reference_name"])
        elif doctype == "Journal Entry":
            rows = graph.children[doctype, name]
            if not any(row["party_type"] == "Customer" and row["party"] == graph.customer for row in rows):
                _invalid()
            for row in rows:
                graph.source("Account", row["account"])
                if row["reference_name"]:
                    keys.add((row["reference_type"], row["reference_name"]))
        # Native reposts can temporarily remove a payment's PLE. Its precise
        # references still make the voucher part of this source closure; do not
        # derive the set of payment/repost sources from ledger rows alone.
        for parenttype, childtype, type_field, name_field in (
            ("Payment Entry", "Payment Entry Reference", "reference_doctype", "reference_name"),
            ("Journal Entry", "Journal Entry Account", "reference_type", "reference_name"),
        ):
            for row in graph.records(childtype, {type_field: doctype, name_field: name},
                                     TABLES[parenttype][2], parenttype=parenttype):
                keys.add((parenttype, row["parent"]))
        for type_field, name_field in (("voucher_type", "voucher_no"), ("against_voucher_type", "against_voucher_no")):
            rows = graph.records(PLE, {type_field: doctype, name_field: name, "company": graph.company,
                                      "party_type": "Customer", "party": graph.customer}, *PARENTS[PLE])
            for entry in rows:
                graph.source(PLE, entry["name"])
                account = graph.source("Account", entry["account"])
                if account["account_type"] != "Receivable" or account["is_group"]:
                    _invalid()
                if entry["account_type"] != "Receivable":
                    _invalid()
                entries[entry["name"]] = entry
                keys.add((entry["voucher_type"], entry["voucher_no"]))
                keys.add((entry["against_voucher_type"], entry["against_voucher_no"]))
        if len(keys) > SOURCE_LIMIT:
            frappe.throw("收款来源较多，请缩小查询范围")
    for entry in entries.values():
        for type_field, name_field in (("voucher_type", "voucher_no"), ("against_voucher_type", "against_voucher_no")):
            doctype = entry[type_field]
            source = graph.source(doctype, entry[name_field])
            if source["docstatus"] not in (1, 2):
                _invalid()
            if doctype == SI and source["debit_to"] != entry["account"]:
                _invalid()
            if doctype == "Payment Entry":
                party_account = {"Receive": source["paid_from"], "Pay": source["paid_to"]}.get(source["payment_type"])
                if party_account != entry["account"]:
                    _invalid()
            if doctype == "Journal Entry" and not any(
                    row["account"] == entry["account"] and row["party_type"] == "Customer"
                    and row["party"] == graph.customer for row in graph.children[doctype, source["name"]]):
                _invalid()
    for name in sorted(name for doctype, name in keys if doctype == SI):
        source = graph.source(SI, name)
        if source["outstanding_amount"] is None or not source["party_account_currency"]:
            _invalid()
        if (source["docstatus"] == 1 and source["outstanding_amount"] != 0
                and not any((entry["voucher_type"], entry["voucher_no"]) == (SI, name)
                            or (entry["against_voucher_type"], entry["against_voucher_no"]) == (SI, name)
                            for entry in entries.values())):
            _invalid()
    graph.reposts.append((set(keys), _reposting(keys)))
    return {"ledger_entries": [entries[name] for name in sorted(entries)],
            "voucher_sources": [{"doctype": doctype, "document": graph.source(doctype, name)}
                                for doctype, name in sorted(keys)]}


def _read(graph, name):
    order = graph.source(SO, name)
    if graph.customer is not None and graph.customer != order["customer"]:
        _invalid()
    graph.customer = order["customer"]
    graph.source("Customer", graph.customer)
    details = {row["name"]: row for row in graph.children[SO, name]}
    for row in details.values():
        graph.source("Item", row["item_code"])
    flows = {}
    flow_names = set(graph.named(FLOW, {"sales_order": name}))
    flow_names.update(graph.named(FLOW, {"sales_order_item": ["in", list(details)]}))
    for flow_name in sorted(flow_names):
        flow = graph.source(FLOW, flow_name)
        if flow["sales_order_item"] not in details or flow["sales_order"] != name:
            _invalid()
        if not flow["source_changed"] and flow["item_code"] != details[flow["sales_order_item"]]["item_code"]:
            _invalid()
        if any(existing["sales_order_item"] == flow["sales_order_item"] for existing in flows.values()):
            _invalid()
        graph.source("Item", flow["item_code"])
        flows[flow_name] = flow
    shipments = _shipments(graph, name, details, flows)
    work_orders, independent = _manufacturing(graph, name, details, flows)
    result = {"company": graph.company, "sales_order": order, "items": list(details.values()),
              "flows": list(flows.values()), "work_orders": work_orders,
              "independent_manufacture_entries": independent,
              "delivery_notes": shipments[DN], "sales_invoices": shipments[SI],
              "receivables": _receivables(graph, name, [row["document"]["name"] for row in shipments[SI]]),
              "readonly": True, "can_write": False, "quantities_aggregated": False}
    return result


def _output(value):
    if isinstance(value, dict):
        return {key: _output(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_output(item) for item in value]
    return _scalar(value)


@frappe.whitelist(methods=["GET"])
def read_order(company, sales_order):
    """Read one exact SO, with all related sources authorised before return."""
    _scope(company)
    if not isinstance(sales_order, str) or not sales_order or sales_order != sales_order.strip():
        frappe.throw("请选择明确的销售订单")
    graph = _Graph(company)
    result = _read(graph, sales_order)
    graph.finish()
    return _output(result)


@frappe.whitelist(methods=["GET"])
def query_orders(company, from_date, to_date, search=""):
    """Query readable SOs by their native transaction dates, without totals."""
    _scope(company)
    try:
        start, end = getdate(from_date), getdate(to_date)
        if str(start) != from_date or str(end) != to_date or start > end:
            raise ValueError
    except (TypeError, ValueError):
        frappe.throw("请选择有效的 YYYY-MM-DD 订单起止日期")
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效的销售订单号关键词")
    graph = _Graph(company)
    filters = {"company": company, "transaction_date": ["between", [from_date, to_date]]}
    if search:
        filters["name"] = ["like", "%" + search + "%"]
    rows = graph.records(SO, filters, {"name", "modified", "company", "transaction_date"}, complete=False)
    if len(rows) > LIMIT:
        frappe.throw("销售订单较多，请缩小日期范围或输入订单号关键词")
    results = []
    for row in rows:
        # A single batch can contain several customers, with each order's
        # receivable closure independently scoped to its exact native customer.
        graph.customer = None
        results.append(_read(graph, row["name"]))
    graph.finish()
    return _output(results)
