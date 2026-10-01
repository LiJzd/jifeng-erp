"""Read exact native procurement sources; never allocate payments to item rows.

This independent service does not implement an unverified legacy screen. Invoice
outstanding is the native current whole-voucher amount, not a cutoff balance or
a value allocated to the selected PO lines. Receipt valuation, service invoices,
transaction currency and company/account currency retain their native meanings.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate


PO, PR, PI = "Purchase Order", "Purchase Receipt", "Purchase Invoice"
SCO, SCR = "Subcontracting Order", "Subcontracting Receipt"
PE, JE, PLE = "Payment Entry", "Journal Entry", "Payment Ledger Entry"
FLOW, EXPENSE = "Jifeng Flow Order", "Jifeng Expense Voucher"
SI, DN, SO = "Sales Invoice", "Delivery Note", "Sales Order"
LIMIT, SOURCE_LIMIT = 20, 1000
IDENTITY = {"name", "modified", "docstatus"}
CHILD = {"name", "modified", "parent", "parenttype", "parentfield", "idx"}
PARTY = IDENTITY | {"company", "supplier"}
MONEY = {"grand_total", "base_grand_total", "net_total", "base_net_total", "rounded_total", "base_rounded_total"}
QTY = {"qty", "uom", "stock_uom", "conversion_factor", "stock_qty"}
ROW_MONEY = {"rate", "base_rate", "amount", "base_amount", "net_amount", "base_net_amount"}
PARENTS = {
    PO: (PARTY | {"transaction_date", "currency", "is_subcontracted", "is_old_subcontracting_flow"},
         MONEY | {"status", "conversion_rate", "per_received", "per_billed"}),
    PR: (PARTY | {"posting_date", "currency", "is_return", "return_against", "subcontracting_receipt"},
         MONEY | {"status", "conversion_rate", "is_subcontracted", "is_old_subcontracting_flow"}),
    PI: (PARTY | {"posting_date", "currency", "is_return", "return_against", "credit_to",
                  "outstanding_amount", "party_account_currency"},
         MONEY | {"status", "conversion_rate", "update_stock", "due_date"}),
    SCO: (PARTY | {"transaction_date", "purchase_order", "supplier_currency"}, {"status", "total_qty"}),
    SCR: (PARTY | {"posting_date", "is_return", "return_against"}, {"status", "total_qty", "total"}),
    PE: (IDENTITY | {"company", "party_type", "party", "payment_type", "paid_from", "paid_to",
                    "paid_from_account_currency", "paid_to_account_currency"},
         {"posting_date", "status", "paid_amount", "received_amount", "base_paid_amount", "base_received_amount",
          "source_exchange_rate", "target_exchange_rate"}),
    JE: (IDENTITY | {"company", "posting_date", "jf_expense_voucher"}, {"voucher_type", "multi_currency"}),
    "Account": ({"name", "modified", "company", "account_type", "account_currency", "is_group"}, {"root_type"}),
    "Item": ({"name", "modified"}, set()),
    "Supplier": ({"name", "modified"}, set()),
    "Customer": ({"name", "modified"}, set()),
    "Sales Order": (IDENTITY | {"company", "customer"}, set()),
    SI: (IDENTITY | {"company", "customer", "debit_to", "currency", "party_account_currency", "is_return", "return_against"},
         {"posting_date"}),
    DN: (IDENTITY | {"company", "customer", "is_return", "return_against"}, {"posting_date"}),
    FLOW: (IDENTITY | {"company", "item_code", "sales_order", "sales_order_item", "source_changed"}, set()),
    EXPENSE: (IDENTITY | {"company", "supplier", "posting_date", "payment_method", "payable_account",
                          "expense_account", "payment_account", "cost_center", "amount", "journal_entry"}, set()),
    "Cost Center": ({"name", "modified", "company", "is_group"}, set()),
    PLE: ({"name", "modified", "company", "party_type", "party", "account", "account_type", "account_currency",
           "posting_date", "voucher_type", "voucher_no", "against_voucher_type", "against_voucher_no",
           "amount", "amount_in_account_currency", "delinked"}, {"due_date"}),
}
TABLES = {
    PO: ("items", "Purchase Order Item", CHILD | QTY | {"item_code", "fg_item", "jf_flow_order"},
         ROW_MONEY | {"item_name", "received_qty", "returned_qty", "billed_amt", "fg_item_qty"}),
    PR: ("items", "Purchase Receipt Item", CHILD | QTY | {"item_code", "purchase_order", "purchase_order_item",
         "purchase_receipt_item", "purchase_invoice", "purchase_invoice_item", "subcontracting_receipt_item", "jf_flow_order"},
         ROW_MONEY | {"item_name", "received_qty", "rejected_qty", "returned_qty", "billed_amt", "warehouse", "rejected_warehouse",
                      "return_qty_from_rejected_warehouse"}),
    PI: ("items", "Purchase Invoice Item", CHILD | QTY | {"item_code", "purchase_order", "po_detail",
         "purchase_receipt", "pr_detail", "purchase_invoice_item", "jf_flow_order"}, ROW_MONEY | {"item_name"}),
    SCO: ("items", "Subcontracting Order Item", CHILD | {"item_code", "purchase_order_item", "jf_flow_order",
          "qty", "stock_uom", "conversion_factor"}, {"item_name", "received_qty", "returned_qty", "rate", "amount",
          "service_cost_per_qty", "rm_cost_per_qty", "additional_cost_per_qty", "subcontracting_conversion_factor"}),
    SCR: ("items", "Subcontracting Receipt Item", CHILD | {"item_code", "subcontracting_order", "subcontracting_order_item",
          "purchase_order", "purchase_order_item", "subcontracting_receipt_item", "secondary_item_type", "reference_name",
          "valuation_type", "jf_flow_order", "qty", "stock_uom", "conversion_factor"}, {"item_name", "received_qty", "rejected_qty",
          "process_loss_qty", "rate", "amount", "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty",
          "warehouse", "rejected_warehouse", "return_qty_from_rejected_warehouse"}),
    PE: ("references", "Payment Entry Reference", CHILD | {"reference_doctype", "reference_name"},
         {"allocated_amount", "account", "exchange_rate"}),
    JE: ("accounts", "Journal Entry Account", CHILD | {"account", "party_type", "party", "reference_type", "reference_name", "account_currency"},
         {"debit_in_account_currency", "credit_in_account_currency", "cost_center"}),
    FLOW: ("materials", "Jifeng Material Line", CHILD | {"item_code"}, set()),
    "Sales Order": ("items", "Sales Order Item", CHILD | {"item_code"}, set()),
    SI: ("items", "Sales Invoice Item", CHILD | {"item_code", "sales_order", "so_detail", "delivery_note", "dn_detail",
                                               "sales_invoice_item", "jf_flow_order"}, set()),
    DN: ("items", "Delivery Note Item", CHILD | {"item_code", "against_sales_order", "so_detail", "against_sales_invoice", "si_detail",
                                               "dn_detail", "jf_flow_order"}, set()),
}
EXTRAS = {
    SCO: ("service_items", "Subcontracting Order Service Item", CHILD | {"item_code", "purchase_order_item"},
          {"qty", "rate", "amount"}),
}
RETURNS = {PR: "purchase_receipt_item", PI: "purchase_invoice_item", SCR: "subcontracting_receipt_item"}
# (upstream, downstream, downstream parent pointer, exact upstream child pointer)
EDGES = (
    (PO, PR, "purchase_order", "purchase_order_item"),
    (PO, PI, "purchase_order", "po_detail"),
    (PO, SCO, "purchase_order", "purchase_order_item"),
    (PO, SCR, "purchase_order", "purchase_order_item"),
    (SCO, SCR, "subcontracting_order", "subcontracting_order_item"),
    (SCR, PR, "subcontracting_receipt", "subcontracting_receipt_item"),
    (PR, PI, "purchase_receipt", "pr_detail"),
    (PI, PR, "purchase_invoice", "purchase_invoice_item"),
)


def _deny():
    frappe.throw("无法完整读取所选采购单的来源或字段，不能提供部分履行或付款状态", frappe.PermissionError)


def _invalid():
    frappe.throw("采购履行的精确来源链不完整或范围不一致，请核对原生单据")


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


def _authorization():
    """Internal raw permission fingerprint catches changes hidden by caches."""
    user = frappe.session.user
    doctypes = sorted(set(PARENTS) | {row[1] for row in TABLES.values()} | {row[1] for row in EXTRAS.values()} | {"Company"})
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


def _reposting(keys, company):
    """Related pending/failed reposts cannot support a complete native state."""
    if not keys:
        return ()
    states = []
    for parenttype, childtype, parentfield, status_field, completed in (
        ("Repost Payment Ledger", "Repost Payment Ledger Items", "repost_vouchers", "repost_status", {"Completed"}),
        ("Repost Accounting Ledger", "Repost Accounting Ledger Items", "vouchers", "status", {"Completed", "Cancelled"}),
    ):
        rows = frappe.get_all(childtype, filters={"voucher_type": ["in", sorted({key[0] for key in keys})],
            "voucher_no": ["in", sorted({key[1] for key in keys})]},
            fields=["*"], order_by="name asc", limit_page_length=0)
        rows = [row for row in rows if (row.get("voucher_type"), row.get("voucher_no")) in keys]
        if any(row.get("parenttype") != parenttype or row.get("parentfield") != parentfield or not row.get("parent") for row in rows):
            _invalid()
        states.append((childtype, _freeze(rows)))
        for parent in sorted({row["parent"] for row in rows}):
            jobs = frappe.get_all(parenttype, filters={"name": parent}, fields=["*"],
                                  order_by="name asc", limit_page_length=0)
            if len(jobs) != 1:
                _invalid()
            if jobs[0].get("company") != company:
                _invalid()
            if jobs[0]["docstatus"] == 1 and jobs[0].get(status_field) not in completed:
                frappe.throw("相关应付凭证的原生账簿重过账尚未完成或已失败，请完成后查询")
            states.append((parenttype, _freeze(jobs)))
    return tuple(states)


def _scope(company):
    if frappe.session.user == "Guest":
        _deny()
    if frappe.conf.get("read_from_replica"):
        frappe.throw("采购履行与付款来源需要主库口径，请完成主库核对后查询")
    if not isinstance(company, str) or not company or company != company.strip():
        frappe.throw("请选择明确的公司")
    try:
        document = frappe.get_doc("Company", company)
    except frappe.DoesNotExistError:
        _deny()
    if not document.has_permission("select"):
        _deny()
    field = frappe.get_meta("Company").get_field("default_currency")
    mask = frappe.get_meta("Company").get_permlevel_access(permission_type="mask", user=frappe.session.user)
    if (not field or not document.default_currency or
            (frappe.session.user != "Administrator" and field.get("mask") and (field.permlevel or 0) not in mask)):
        _deny()
    return document.default_currency


class _Sources:
    def __init__(self, company, supplier=None):
        self.company, self.supplier = company, supplier
        self.company_currency = _scope(company)
        self.queries, self.documents, self.children, self.extra_children = [], {}, {}, {}
        self.authorization = _authorization()
        self.reposts, self.companions = [], {}

    def records(self, doctype, filters, required, optional=(), parenttype=None, complete=True, raw_filters=None):
        filters = dict(filters)
        if parenttype:
            schemas = [TABLES[parenttype]] + ([EXTRAS[parenttype]] if parenttype in EXTRAS else [])
            matching = [schema for schema in schemas if schema[1] == doctype]
            if len(matching) != 1:
                _invalid()
            fieldname = matching[0][0]
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
            frappe.throw("采购来源较多，请缩小查询范围")
        if any(not set(required).issubset(row) for row in rows):
            _deny()
        raw_filters = dict(filters if raw_filters is None else raw_filters)
        raw = frappe.get_all(doctype, filters=raw_filters, fields=["*"], order_by="name asc",
                             limit_page_length=SOURCE_LIMIT + 1) if complete else None
        if raw is not None and {row["name"] for row in raw} != {row["name"] for row in rows}:
            _deny()
        if len({row["name"] for row in rows}) != len(rows):
            _invalid()
        self.queries.append((doctype, filters, raw_filters, set(required), tuple(fields), parenttype,
                             _freeze(rows), _freeze(raw)))
        return rows

    def source(self, doctype, name):
        key = doctype, name
        if key in self.documents:
            row = self.documents[key][1]
            if "supplier" in row and self.supplier is not None and row["supplier"] != self.supplier:
                _invalid()
            return row
        if not name or doctype not in PARENTS:
            _invalid()
        rows = self.records(doctype, {"name": name}, *PARENTS[doctype])
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
        if "supplier" in row and self.supplier is not None and row["supplier"] != self.supplier:
            _invalid()
        self.documents[key] = document, row
        for schema in ([TABLES[doctype]] if doctype in TABLES else []) + ([EXTRAS[doctype]] if doctype in EXTRAS else []):
            fieldname, childtype, required, optional = schema
            _table(doctype, fieldname)
            children = self.records(childtype, {"parent": name}, required, optional, doctype,
                                    raw_filters={"parent": name})
            actual = {item.name: item for item in document.get(fieldname) or []}
            if set(actual) != {item["name"] for item in children}:
                _invalid()
            if any(any(actual[item["name"]].get(field) != value for field, value in item.items()) for item in children):
                _invalid()
            if doctype in TABLES and fieldname == TABLES[doctype][0]:
                self.children[key] = children
            else:
                self.extra_children[key] = children
        return row

    def detail(self, doctype, parent, child, extra=False):
        document = self.source(doctype, parent)
        rows = [row for row in (self.extra_children if extra else self.children).get((doctype, parent), [])
                if row["name"] == child]
        if len(rows) != 1:
            _invalid()
        self.source("Item", rows[0]["item_code"])
        return document, rows[0]

    def named(self, doctype, filters):
        return [row["name"] for row in self.records(doctype, filters, {"name", "modified"})]

    def finish(self):
        try:
            loaded = {key: frappe.get_doc(*key) for key in self.documents}
        except frappe.DoesNotExistError:
            _deny()
        if _scope(self.company) != self.company_currency:
            _deny()
        for (doctype, name), (_old, row) in self.documents.items():
            document = loaded[doctype, name]
            if (not document.has_permission("read") or not set(row).issubset(_columns(doctype))
                    or any(document.get(field) != value for field, value in row.items())):
                _deny()
            for schema in ([TABLES[doctype]] if doctype in TABLES else []) + ([EXTRAS[doctype]] if doctype in EXTRAS else []):
                fieldname, childtype, _required, _optional = schema
                _table(doctype, fieldname)
                actual = {item.name: item for item in document.get(fieldname) or []}
                projected = (self.children if fieldname == TABLES[doctype][0] else self.extra_children)[doctype, name]
                if set(actual) != {item["name"] for item in projected}:
                    _deny()
                for item in projected:
                    if (not set(item).issubset(_columns(childtype, doctype))
                            or any(actual[item["name"]].get(field) != value for field, value in item.items())):
                        _deny()
        for doctype, filters, raw_filters, required, fields, parenttype, state, raw_state in self.queries:
            if not set(fields).issubset(_fields(doctype, required, fields, parenttype)):
                _deny()
            current = frappe.get_list(doctype, filters=filters, fields=list(fields), order_by="name asc",
                limit_page_length=SOURCE_LIMIT + 1, **({"parent_doctype": parenttype} if parenttype else {}))
            raw = frappe.get_all(doctype, filters=raw_filters, fields=["*"], order_by="name asc",
                                 limit_page_length=SOURCE_LIMIT + 1) if raw_state != _freeze(None) else None
            if _freeze(current) != state or _freeze(raw) != raw_state:
                _deny()
        if _scope(self.company) != self.company_currency:
            _deny()
        for key, (_old, row) in self.documents.items():
            if not loaded[key].has_permission("read") or not set(row).issubset(_columns(key[0])):
                _deny()
            for schema in ([TABLES[key[0]]] if key[0] in TABLES else []) + ([EXTRAS[key[0]]] if key[0] in EXTRAS else []):
                fieldname, childtype, _r, _o = schema
                _table(key[0], fieldname)
                rows = (self.children if fieldname == TABLES[key[0]][0] else self.extra_children)[key]
                if any(not set(child).issubset(_columns(childtype, key[0])) for child in rows):
                    _deny()
        for doctype, _filters, raw_filters, _r, _f, _p, _s, raw_state in self.queries:
            if raw_state != _freeze(None) and _freeze(frappe.get_all(doctype, filters=raw_filters,
                    fields=["*"], order_by="name asc", limit_page_length=SOURCE_LIMIT + 1)) != raw_state:
                _deny()
        # Root batches deliberately include the readable population. Recheck it
        # without treating hidden roots as a permission failure or leaking them.
        for doctype, filters, _raw, required, fields, parenttype, state, raw_state in self.queries:
            if raw_state == _freeze(None):
                current = frappe.get_list(doctype, filters=filters, fields=list(fields), order_by="name asc",
                    limit_page_length=SOURCE_LIMIT + 1, **({"parent_doctype": parenttype} if parenttype else {}))
                if _freeze(current) != state or not set(fields).issubset(_fields(doctype, required, fields, parenttype)):
                    _deny()
        for keys, state in self.reposts:
            if _reposting(keys, self.company) != state:
                _deny()
        if _authorization() != self.authorization:
            _deny()


def _flow(graph, name, item, finished=False):
    flow = graph.source(FLOW, name)
    graph.source("Item", flow["item_code"])
    materials = graph.children[FLOW, name]
    for row in materials:
        graph.source("Item", row["item_code"])
    if item not in ({flow["item_code"]} if finished else {flow["item_code"]} | {row["item_code"] for row in materials}):
        _invalid()
    if flow["sales_order"] or flow["sales_order_item"]:
        _order, detail = graph.detail("Sales Order", flow["sales_order"], flow["sales_order_item"])
        graph.source("Customer", _order["customer"])
        if not flow["source_changed"] and detail["item_code"] != flow["item_code"]:
            _invalid()


def _direct_order(graph, doctype, document, row, extra=False):
    if doctype == PO:
        if row["fg_item"]:
            graph.source("Item", row["fg_item"])
        return document["name"], row["name"]
    if doctype == SCO:
        parent, child = document["purchase_order"], row["purchase_order_item"]
    else:
        parent, child = row.get("purchase_order"), row.get("po_detail" if doctype == PI else "purchase_order_item")
    if not parent and not child:
        return None
    if not parent or not child:
        _invalid()
    source, detail = graph.detail(PO, parent, child)
    if source["docstatus"] not in (1, 2):
        _invalid()
    service_to_finished = doctype in (SCO, SCR) and not extra
    if service_to_finished:
        if (not source["is_subcontracted"] or source["is_old_subcontracting_flow"]
                or not detail["fg_item"] or detail["fg_item"] != row["item_code"]):
            _invalid()
        graph.source("Item", detail["fg_item"])
    elif detail["item_code"] != row["item_code"]:
        _invalid()
    return source["name"], detail["name"]


def _origin(graph, doctype, parent, child, visiting=None, extra=False):
    visiting = set() if visiting is None else visiting
    key = doctype, parent, child, extra
    document, row = graph.detail(doctype, parent, child, extra)
    if doctype not in (PO, PR, PI, SCO, SCR):
        _invalid()
    if row.get("jf_flow_order"):
        _flow(graph, row["jf_flow_order"], row["item_code"], finished=doctype in (SCO, SCR))
    direct = _direct_order(graph, doctype, document, row, extra)
    if key in visiting:
        # Native PI -> later PR can coexist with the reverse invoice link.
        # A cycle has an exact PO basis only if this node stores that basis.
        if direct is None:
            _invalid()
        return direct
    visiting.add(key)
    try:
        origins = [direct] if direct else []
        if doctype in RETURNS and document["is_return"] and document["return_against"]:
            original, original_row = graph.detail(doctype, document["return_against"], row[RETURNS[doctype]])
            if (original["docstatus"] not in (1, 2) or original["is_return"]
                    or original_row["item_code"] != row["item_code"]):
                _invalid()
            origins.append(_origin(graph, doctype, original["name"], original_row["name"], visiting))
        if doctype in RETURNS:
            if not document["is_return"] and (document["return_against"] or row[RETURNS[doctype]]):
                _invalid()
            if document["is_return"] and not document["return_against"] and row[RETURNS[doctype]]:
                _invalid()
        if doctype == SCR and (row["secondary_item_type"] or row["valuation_type"]):
            if not row["reference_name"]:
                _invalid()
            reference_parent = parent
            if not any(item["name"] == row["reference_name"] for item in graph.children[SCR, parent]):
                reference_parent = document["return_against"] if document["is_return"] else None
            _source, reference = graph.detail(SCR, reference_parent, row["reference_name"])
            if reference["secondary_item_type"] or reference["valuation_type"] or reference["name"] == child:
                _invalid()
            origins.append(_origin(graph, SCR, reference_parent, reference["name"], visiting))
        for upstream, downstream, parent_field, child_field in EDGES:
            if downstream != doctype or (extra and doctype == SCO):
                continue
            parent_name = document.get(parent_field) if doctype == SCO or parent_field == "subcontracting_receipt" else row.get(parent_field)
            detail_name = row.get(child_field)
            if upstream == PO:
                continue  # Already checked, including the service/finished boundary.
            if not parent_name and not detail_name:
                continue
            if not parent_name or not detail_name:
                _invalid()
            source, detail = graph.detail(upstream, parent_name, detail_name)
            if source["docstatus"] not in (1, 2):
                _invalid()
            if upstream != SCR and detail["item_code"] != row["item_code"]:
                _invalid()
            origin = _origin(graph, upstream, source["name"], detail["name"], visiting)
            if upstream == SCR:
                # A converted service PR deliberately differs from SCR goods.
                if origin is None or origin != direct:
                    _invalid()
            origins.append(origin)
        if doctype == SCO and direct is None:
            _invalid()
        if doctype == SCR and not (row["secondary_item_type"] or row["valuation_type"]) and not (row["subcontracting_order"] and row["subcontracting_order_item"]):
            _invalid()
        exact = {origin for origin in origins if origin is not None}
        if len(exact) > 1:
            _invalid()
        return next(iter(exact)) if exact else None
    finally:
        visiting.remove(key)


def _authorize_parent(graph, doctype, name):
    document = graph.source(doctype, name)
    origins = {}
    for row in graph.children[doctype, name]:
        origins[row["name"]] = _origin(graph, doctype, name, row["name"])
    for row in graph.extra_children.get((doctype, name), []):
        _origin(graph, doctype, name, row["name"], extra=True)
    return document, origins


def _procurement_closure(graph, order):
    selected = {doctype: {} for doctype in (PO, PR, PI, SCO, SCR)}
    parent, origins = _authorize_parent(graph, PO, order)
    selected[PO][order] = set(origins)
    checked, examined = set(), set()
    while True:
        pending = [(doctype, name) for doctype, parents in selected.items() for name in parents if (doctype, name) not in checked]
        for doctype, name in sorted(pending):
            document, origins = _authorize_parent(graph, doctype, name)
            ids = {child for child, origin in origins.items() if origin and origin[0] == order}
            if not ids:
                _invalid()
            selected[doctype][name] = ids
            checked.add((doctype, name))
        changed = False
        for upstream, downstream, parent_field, child_field in EDGES:
            ids = sorted({child for children in selected[upstream].values() for child in children})
            parents = sorted(selected[upstream])
            if not parents:
                continue
            token = upstream, downstream, tuple(ids), tuple(parents)
            if token in examined:
                continue
            examined.add(token)
            childtype = TABLES[downstream][1]
            found = []
            if ids:
                query = {child_field: ["in", ids]}
                found.extend(graph.records(childtype, query, TABLES[downstream][2], parenttype=downstream, raw_filters=query))
            if downstream == SCO or parent_field == "subcontracting_receipt":
                for name in graph.named(downstream, {parent_field: ["in", parents]}):
                    graph.source(downstream, name)
                    found.extend(graph.children[downstream, name])
            else:
                query = {parent_field: ["in", parents]}
                found.extend(graph.records(childtype, query, TABLES[downstream][2], parenttype=downstream, raw_filters=query))
            for row in found:
                if row["parent"] not in selected[downstream]:
                    selected[downstream][row["parent"]] = set()
                    changed = True
            if downstream == SCO and ids:
                query = {"purchase_order_item": ["in", ids]}
                for row in graph.records(EXTRAS[SCO][1], query, EXTRAS[SCO][2], parenttype=SCO, raw_filters=query):
                    if row["parent"] not in selected[SCO]:
                        selected[SCO][row["parent"]] = set()
                        changed = True
        for doctype in RETURNS:
            parents = sorted(selected[doctype])
            if not parents:
                continue
            ids = sorted({child for children in selected[doctype].values() for child in children})
            token = doctype, "returns", tuple(parents), tuple(ids)
            if token in examined:
                continue
            examined.add(token)
            for name in graph.named(doctype, {"return_against": ["in", parents]}):
                if name not in selected[doctype]:
                    selected[doctype][name] = set()
                    changed = True
            query = {RETURNS[doctype]: ["in", ids]}
            for row in graph.records(TABLES[doctype][1], query, TABLES[doctype][2], parenttype=doctype, raw_filters=query):
                if row["parent"] not in selected[doctype]:
                    selected[doctype][row["parent"]] = set()
                    changed = True
        if not changed and not pending:
            break
        if sum(len(parents) for parents in selected.values()) > SOURCE_LIMIT:
            frappe.throw("采购履行来源较多，请缩小查询范围")
    result = {}
    for doctype, parents in selected.items():
        if doctype == PO:
            continue
        result[doctype] = []
        for name, ids in sorted(parents.items()):
            document, origins = _authorize_parent(graph, doctype, name)
            rows = graph.children[doctype, name]
            result[doctype].append({"document": document, "items": [row for row in rows if row["name"] in ids],
                "other_items": [row for row in rows if row["name"] not in ids and origins[row["name"]] is not None],
                "unlinked_items": [row for row in rows if origins[row["name"]] is None],
                **({"service_items": graph.extra_children[doctype, name]} if doctype in EXTRAS else {}),
                "amount_scope": "whole_voucher", "amounts_allocated_to_items": False,
                "valuation_currency": graph.company_currency if doctype == SCR else document.get("currency", document.get("supplier_currency"))})
    return parent, result


def _expense_source(graph, journal, rows):
    """Verify the native two-row credit-expense source, including cancelled history."""
    expense = graph.source(EXPENSE, journal["jf_expense_voucher"])
    if (expense["docstatus"] not in (1, 2) or expense["docstatus"] != journal["docstatus"]
            or expense["payment_method"] != "赊账" or expense["journal_entry"] != journal["name"]):
        _invalid()
    for doctype, fields in ((JE, {"voucher_type", "multi_currency"}),
                           (TABLES[JE][1], {"cost_center", "debit_in_account_currency", "credit_in_account_currency"})):
        _fields(doctype, fields, parenttype=JE if doctype != JE else None)
    if (not {"voucher_type", "multi_currency"}.issubset(journal) or journal["voucher_type"] != "Journal Entry"
            or journal["multi_currency"] or journal["posting_date"] != expense["posting_date"] or len(rows) != 2):
        _invalid()
    accounts = [graph.source("Account", expense[field]) for field in ("expense_account", "payable_account")]
    _fields("Account", {"root_type"})
    if (any("root_type" not in account or account["is_group"]
            or (account["account_currency"] or graph.company_currency) != graph.company_currency for account in accounts)
            or accounts[0]["root_type"] != "Expense" or accounts[1]["root_type"] != "Liability"
            or accounts[1]["account_type"] != "Payable" or accounts[0]["name"] == accounts[1]["name"]):
        _invalid()
    if expense["payment_account"]:
        graph.source("Account", expense["payment_account"])
    center = graph.source("Cost Center", expense["cost_center"])
    if center["is_group"]:
        _invalid()
    try:
        amount = Decimal(str(expense["amount"]))
        if not amount.is_finite() or amount <= 0:
            raise ValueError
        debit, credit = sorted(rows, key=lambda row: row["idx"])

        def money(row, field):
            return Decimal(str(row[field] or 0))

        if (any(not {"cost_center", "debit_in_account_currency", "credit_in_account_currency"}.issubset(row) for row in rows)
                or debit["account"] != expense["expense_account"] or debit["cost_center"] != expense["cost_center"]
                or money(debit, "debit_in_account_currency") != amount or money(debit, "credit_in_account_currency") != 0
                or credit["account"] != expense["payable_account"] or money(credit, "credit_in_account_currency") != amount
                or money(credit, "debit_in_account_currency") != 0 or debit["party_type"] or debit["party"]
                or credit["party_type"] != "Supplier" or credit["party"] != graph.supplier
                or any(row["reference_type"] or row["reference_name"] for row in rows)):
            _invalid()
    except (InvalidOperation, TypeError, ValueError, KeyError):
        _invalid()


def _party_account(graph, payment):
    field = {"Pay": "paid_to", "Receive": "paid_from"}.get(payment["payment_type"])
    if not field:
        _invalid()
    for name_field in ("paid_from", "paid_to"):
        account = graph.source("Account", payment[name_field])
        if (account["is_group"] or payment[name_field + "_account_currency"] != (account["account_currency"] or graph.company_currency)
                or (name_field == field and account["account_type"] != "Payable")):
            _invalid()
    return payment[field]


def _reference_account(graph, doctype, name, account):
    source = graph.source(doctype, name)
    if doctype == PI and source["credit_to"] != account:
        _invalid()
    if doctype == PE and _party_account(graph, source) != account:
        _invalid()
    if doctype == JE and not any(row["account"] == account and row["party_type"] == "Supplier" and row["party"] == graph.supplier
                               for row in graph.children[JE, name]):
        _invalid()


def _sales_companion(graph, doctype, name, customer, seen=None):
    """Read exact companion sales links; no customer balance is calculated here."""
    seen = set() if seen is None else seen
    document = graph.source(doctype, name)
    if document["customer"] != customer:
        _invalid()
    graph.source("Customer", customer)
    if (doctype, name) in seen:
        return document
    seen.add((doctype, name))
    for row in graph.children[doctype, name]:
        graph.source("Item", row["item_code"])
        if row.get("jf_flow_order"):
            _flow(graph, row["jf_flow_order"], row["item_code"], finished=True)
        if doctype == SO:
            continue
        if not document["is_return"] and (document["return_against"] or row["sales_invoice_item" if doctype == SI else "dn_detail"]):
            _invalid()
        pairs = ((SO, "sales_order", "so_detail"), (DN, "delivery_note", "dn_detail")) if doctype == SI else (
            (SO, "against_sales_order", "so_detail"), (SI, "against_sales_invoice", "si_detail"))
        for upstream, parent_field, child_field in pairs:
            parent, child = row[parent_field], row[child_field]
            if not parent and not child:
                continue
            if not parent or not child:
                _invalid()
            source, detail = graph.detail(upstream, parent, child)
            historical = False
            if upstream == SO and row.get("jf_flow_order"):
                flow = graph.source(FLOW, row["jf_flow_order"])
                if (flow["sales_order"], flow["sales_order_item"]) != (parent, child):
                    _invalid()
                historical = bool(flow["source_changed"] and flow["item_code"] == row["item_code"])
            if source["docstatus"] not in (1, 2) or (detail["item_code"] != row["item_code"] and not historical):
                _invalid()
            _sales_companion(graph, upstream, parent, customer, seen)
        original = row["sales_invoice_item" if doctype == SI else "dn_detail"]
        if document["is_return"] and document["return_against"]:
            source, detail = graph.detail(doctype, document["return_against"], original)
            if source["docstatus"] not in (1, 2) or source["is_return"] or detail["item_code"] != row["item_code"]:
                _invalid()
            _sales_companion(graph, doctype, source["name"], customer, seen)
        elif document["is_return"] and original:
            _invalid()
    return document


def _companion_projection(graph, doctype, name):
    source = graph.documents[doctype, name][1]
    fields = IDENTITY | {"company", "supplier", "customer", "debit_to", "credit_to", "currency", "party_account_currency"}
    graph.companions[doctype, name] = {"doctype": doctype,
        "document": {field: value for field, value in source.items() if field in fields},
        TABLES[doctype][0]: [{field: value for field, value in row.items() if field not in ROW_MONEY |
                   {"debit_in_account_currency", "credit_in_account_currency", "allocated_amount"}}
                  for row in graph.children[doctype, name]],
        "balance_included": False, "amounts_allocated_to_items": False}


def _companion_reference(graph, row, seen=None, selected=None):
    """Authorize other parties in one JE as whole native source documents.

    These sources support complete JE provenance, not the selected Supplier's
    payment totals. Their balances and amounts are deliberately not projected.
    """
    seen = set() if seen is None else seen
    selected = set() if selected is None else selected
    doctype, name, party_type, party = row["reference_type"], row["reference_name"], row["party_type"], row["party"]
    key = doctype, name, party_type, party, row["account"]
    selected.add((doctype, name))
    if key in seen:
        return
    seen.add(key)
    account = graph.source("Account", row["account"])
    if not party or party_type not in ("Supplier", "Customer") or account["is_group"]:
        _invalid()
    expected = "Payable" if party_type == "Supplier" else "Receivable"
    if account["account_type"] != expected:
        _invalid()
    current_supplier = graph.supplier
    try:
        graph.supplier = party if party_type == "Supplier" else None
        graph.source(party_type, party)
        if doctype in (PO, PI) and party_type == "Supplier":
            source, _origins = _authorize_parent(graph, doctype, name)
            if source["supplier"] != party or (doctype == PI and (source["credit_to"] != row["account"]
                    or source["party_account_currency"] != (account["account_currency"] or graph.company_currency))):
                _invalid()
        elif doctype in (SO, SI) and party_type == "Customer":
            linked = set()
            source = _sales_companion(graph, doctype, name, party, linked)
            selected.update(linked)
            for source_type, source_name in linked:
                _companion_projection(graph, source_type, source_name)
            if doctype == SI and (source["debit_to"] != row["account"]
                    or source["party_account_currency"] != (account["account_currency"] or graph.company_currency)):
                _invalid()
        elif doctype == JE:
            source = graph.source(JE, name)
            if not any(item["account"] == row["account"] and item["party_type"] == party_type and item["party"] == party
                       for item in graph.children[JE, name]):
                _invalid()
            for item in graph.children[JE, name]:
                linked_account = graph.source("Account", item["account"])
                if linked_account["is_group"] or item["account_currency"] != (linked_account["account_currency"] or graph.company_currency):
                    _invalid()
                if bool(item["reference_type"]) != bool(item["reference_name"]):
                    _invalid()
                if item["reference_name"]:
                    _companion_reference(graph, item, seen, selected)
            if source["jf_expense_voucher"]:
                _expense_source(graph, source, graph.children[JE, name])
        else:
            _invalid()  # Unknown complete source shape cannot yield partial AP.
        if source["docstatus"] not in (1, 2):
            _invalid()
        _companion_projection(graph, doctype, name)
        graph.reposts.append(({(doctype, name)}, _reposting({(doctype, name)}, graph.company)))
    finally:
        graph.supplier = current_supplier


def _payables(graph, order, invoices):
    keys, seen, entries = {(PO, order)} | {(PI, name) for name in invoices}, set(), {}
    companions = set()
    while keys - seen:
        doctype, name = sorted(keys - seen)[0]
        seen.add((doctype, name))
        if doctype not in (PO, PI, PE, JE):
            _invalid()
        source = graph.source(doctype, name)
        if doctype in (PO, PI):
            _authorize_parent(graph, doctype, name)
        if doctype == PI:
            if source["is_return"] and source["return_against"]:
                keys.add((PI, source["return_against"]))
            account = graph.source("Account", source["credit_to"])
            if (account["is_group"] or account["account_type"] != "Payable"
                    or source["party_account_currency"] != (account["account_currency"] or graph.company_currency)):
                _invalid()
        elif doctype == PE:
            if source["party_type"] != "Supplier" or source["party"] != graph.supplier:
                _invalid()
            _party_account(graph, source)
            for row in graph.children[doctype, name]:
                if row["reference_name"] or row["reference_doctype"]:
                    if not row["reference_name"] or not row["reference_doctype"]:
                        _invalid()
                    keys.add((row["reference_doctype"], row["reference_name"]))
        elif doctype == JE:
            rows = graph.children[doctype, name]
            if not any(row["party_type"] == "Supplier" and row["party"] == graph.supplier for row in rows):
                _invalid()
            for row in rows:
                account = graph.source("Account", row["account"])
                if account["is_group"] or row["account_currency"] != (account["account_currency"] or graph.company_currency):
                    _invalid()
                if row["reference_type"] or row["reference_name"]:
                    if not row["reference_type"] or not row["reference_name"]:
                        _invalid()
                    if row["party_type"] == "Supplier" and row["party"] == graph.supplier:
                        keys.add((row["reference_type"], row["reference_name"]))
                    else:
                        _companion_reference(graph, row, selected=companions)
            if source["jf_expense_voucher"]:
                _expense_source(graph, source, rows)
        # Read precise PE/JE references even when a native repost has removed PLE.
        for parenttype, childtype, type_field, name_field in ((PE, TABLES[PE][1], "reference_doctype", "reference_name"),
                                                             (JE, TABLES[JE][1], "reference_type", "reference_name")):
            query = {type_field: doctype, name_field: name}
            for row in graph.records(childtype, query, TABLES[parenttype][2], parenttype=parenttype, raw_filters=query):
                keys.add((parenttype, row["parent"]))
        for type_field, name_field in (("voucher_type", "voucher_no"), ("against_voucher_type", "against_voucher_no")):
            # PI/PE belong to one supplier. Broad voucher membership also catches
            # a malformed company/party/account change that a scope filter hides.
            # A JE can contain several parties; its full accounts remain guarded.
            query = {type_field: doctype, name_field: name}
            if doctype == JE:
                query.update(party_type="Supplier", party=graph.supplier)
            for row in graph.records(PLE, query, *PARENTS[PLE]):
                graph.source(PLE, row["name"])
                account = graph.source("Account", row["account"])
                if (row["party_type"] != "Supplier" or row["party"] != graph.supplier
                        or account["account_type"] != "Payable" or account["is_group"] or row["account_type"] != "Payable"
                        or row["account_currency"] != (account["account_currency"] or graph.company_currency)):
                    _invalid()
                entries[row["name"]] = row
                keys.update(((row["voucher_type"], row["voucher_no"]), (row["against_voucher_type"], row["against_voucher_no"])))
        if len(keys) > SOURCE_LIMIT:
            frappe.throw("付款来源较多，请缩小查询范围")
    # Native references remain necessary evidence even if a settled invoice or
    # reposting process has no PLE row. Do not accept a Bank account as AP.
    for doctype, name in keys:
        source = graph.source(doctype, name)
        if doctype == PE:
            account = _party_account(graph, source)
            for row in graph.children[PE, name]:
                if row["reference_name"]:
                    _reference_account(graph, row["reference_doctype"], row["reference_name"], account)
        elif doctype == JE:
            for row in graph.children[JE, name]:
                if row["reference_name"]:
                    if row["party_type"] != "Supplier" or row["party"] != graph.supplier:
                        continue  # Fully checked companion; no selected-Supplier balance.
                    account = graph.source("Account", row["account"])
                    if (row["party_type"] != "Supplier" or row["party"] != graph.supplier
                            or account["account_type"] != "Payable"):
                        _invalid()
                    _reference_account(graph, row["reference_type"], row["reference_name"], row["account"])
    for row in entries.values():
        for type_field, name_field in (("voucher_type", "voucher_no"), ("against_voucher_type", "against_voucher_no")):
            doctype = row[type_field]
            source = graph.source(doctype, row[name_field])
            if source["docstatus"] not in (1, 2) or (type_field == "voucher_type" and doctype == PO):
                _invalid()
            if doctype == PI and source["credit_to"] != row["account"]:
                _invalid()
            if doctype == PE and {"Pay": source["paid_to"], "Receive": source["paid_from"]}.get(source["payment_type"]) != row["account"]:
                _invalid()
            if doctype == JE and not any(item["account"] == row["account"] and item["party_type"] == "Supplier"
                    and item["party"] == graph.supplier for item in graph.children[JE, source["name"]]):
                _invalid()
    for name in sorted(name for doctype, name in keys if doctype == PI):
        source = graph.source(PI, name)
        if source["outstanding_amount"] is None or not source["party_account_currency"]:
            _invalid()
        if (source["docstatus"] == 1 and source["outstanding_amount"] != 0 and not any(
                (row["voucher_type"], row["voucher_no"]) == (PI, name) or
                (row["against_voucher_type"], row["against_voucher_no"]) == (PI, name) for row in entries.values())):
            _invalid()
    graph.reposts.append((set(keys), _reposting(keys, graph.company)))
    return {"ledger_entries": [entries[name] for name in sorted(entries)],
            "voucher_sources": [{"doctype": doctype, "document": graph.source(doctype, name),
                TABLES[doctype][0]: graph.children[doctype, name]} for doctype, name in sorted(keys)],
            "other_reference_sources": [graph.companions[key] for key in sorted(companions)],
            "amount_scope": "whole_voucher", "balance_basis": "native_current_outstanding",
            "amounts_allocated_to_items": False}


def _read(graph, name):
    order = graph.source(PO, name)
    if graph.supplier is not None and graph.supplier != order["supplier"]:
        _invalid()
    graph.supplier = order["supplier"]
    graph.source("Supplier", graph.supplier)
    order, related = _procurement_closure(graph, name)
    return {"company": graph.company, "company_currency": graph.company_currency, "supplier": graph.supplier,
            "purchase_order": order, "items": graph.children[PO, name],
            "purchase_receipts": related[PR], "subcontracting_orders": related[SCO],
            "subcontracting_receipts": related[SCR], "purchase_invoices": related[PI],
            "payables": _payables(graph, name, [item["document"]["name"] for item in related[PI]]),
            "readonly": True, "can_write": False, "quantities_aggregated": False, "amounts_allocated_to_items": False}


def _output(value):
    if isinstance(value, dict):
        return {key: _output(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_output(item) for item in value]
    return _scalar(value)


@frappe.whitelist(methods=["GET"])
def read_procurement_source(company, purchase_order):
    """Read one exact PO and its authorized native source closure, without totals."""
    if not isinstance(purchase_order, str) or not purchase_order or purchase_order != purchase_order.strip():
        frappe.throw("请选择明确的采购订单")
    graph = _Sources(company)
    result = _read(graph, purchase_order)
    graph.finish()
    return _output(result)


@frappe.whitelist(methods=["GET"])
def query_procurement_sources(company, from_date, to_date, supplier, search=""):
    """A readable PO batch by order dates; related receipts/payments keep all dates."""
    if not isinstance(supplier, str) or not supplier or supplier != supplier.strip():
        frappe.throw("请选择明确的供应商")
    try:
        start, end = getdate(from_date), getdate(to_date)
        if str(start) != from_date or str(end) != to_date or start > end:
            raise ValueError
    except (TypeError, ValueError):
        frappe.throw("请选择有效的 YYYY-MM-DD 采购订单起止日期")
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效的采购订单号关键词")
    graph = _Sources(company, supplier)
    graph.source("Supplier", supplier)
    query = {"company": company, "supplier": supplier, "transaction_date": ["between", [from_date, to_date]]}
    if search:
        query["name"] = ["like", "%" + search + "%"]
    orders = graph.records(PO, query, {"name", "modified", "company", "supplier", "transaction_date"}, complete=False)
    if len(orders) > LIMIT:
        frappe.throw("采购订单较多，请缩小日期范围或输入订单号关键词")
    result = [_read(graph, row["name"]) for row in orders]
    graph.finish()
    return _output(result)
