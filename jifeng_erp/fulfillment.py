"""Exact sales-row Flow provenance; native sales quantities and postings stay native."""

import frappe


FLOW = "Jifeng Flow Order"
SOURCES = {
    "Delivery Note": (("Sales Order", "against_sales_order", "so_detail"),
                      ("Sales Invoice", "against_sales_invoice", "si_detail")),
    "Sales Invoice": (("Sales Order", "sales_order", "so_detail"),
                      ("Delivery Note", "delivery_note", "dn_detail")),
}
RETURN_CHILD = {"Delivery Note": "dn_detail", "Sales Invoice": "sales_invoice_item"}


def _probe_row(doctype, child_name, cache):
    key = ("source_probe", doctype, child_name)
    if key not in cache:
        fields = ["parent"] if doctype == "Sales Order" else ["parent", "jf_flow_order"]
        cache[key] = frappe.db.get_value(doctype + " Item", child_name, fields, as_dict=True) if child_name else None
    return cache[key]


def _any_persisted_flow(doctype, parent_name):
    return bool(parent_name and frappe.db.exists(doctype + " Item", {
        "parent": parent_name, "jf_flow_order": ["!=", ""],
    }))


def _persisted_flow_context(doctype, parent_name, child_name, cache):
    source = _probe_row(doctype, child_name, cache)
    if source and (not parent_name or source.parent == parent_name):
        return bool(source.jf_flow_order)
    return bool(source and source.jf_flow_order) or _any_persisted_flow(doctype, parent_name)


def _order_flow_context(parent_name, child_name, cache):
    source = _probe_row("Sales Order", child_name, cache)
    filters = {"sales_order": source.parent if source else parent_name}
    if source:
        filters["sales_order_item"] = child_name
    if not filters["sales_order"]:
        return False
    return bool(frappe.get_all(FLOW, filters=filters, pluck="name", limit_page_length=1))


def _has_flow_context(document, row, cache):
    # These internal probes return only a boolean. They never authorize reads or
    # expose discovered names; full permission checks run when a Flow is present.
    if row.get("jf_flow_order"):
        return True
    if document.get("is_return") and document.get("return_against"):
        return _persisted_flow_context(document.doctype, document.return_against,
                                       row.get(RETURN_CHILD[document.doctype]), cache)
    historical_type = "Delivery Note" if document.doctype == "Sales Invoice" else "Sales Invoice"
    _, parent_field, child_field = SOURCES[document.doctype][1]
    if row.get(parent_field) or row.get(child_field):
        return _persisted_flow_context(historical_type, row.get(parent_field), row.get(child_field), cache)
    _, parent_field, child_field = SOURCES[document.doctype][0]
    if not document.get("is_return") and (row.get(parent_field) or row.get(child_field)):
        return _order_flow_context(row.get(parent_field), row.get(child_field), cache)
    return False


def _read_document(doctype, name, cache):
    key = (doctype, name)
    if key not in cache:
        document = frappe.get_doc(doctype, name)
        if not document.has_permission("read"):
            # Never put an internally discovered hidden Flow identifier in an error.
            frappe.throw("没有完整读取销售来源单据或流程单的权限", frappe.PermissionError)
        cache[key] = document
    return cache[key]


def _check_source(document, row, parent, source):
    if parent.docstatus != 1 or parent.company != document.company:
        frappe.throw("销售来源须已提交且属于同一公司")
    if parent.get("customer") != document.get("customer"):
        frappe.throw("销售来源客户与当前单据不一致")
    if source.item_code != row.item_code:
        frappe.throw("销售来源明细商品与当前明细不一致")


def _source_row(document, row, doctype, parent_name, child_name, cache):
    if not parent_name or not child_name:
        frappe.throw("销售来源单据和精确来源明细必须同时填写")
    parent = _read_document(doctype, parent_name, cache)
    source = next((item for item in parent.get("items") or [] if item.name == child_name), None)
    if not source:
        frappe.throw("销售来源明细不属于所选来源单据")
    _check_source(document, row, parent, source)
    return parent, source


def _sales_order_flow(parent, source, cache):
    key = ("sales_order_flow", parent.name, source.name)
    if key not in cache:
        names = frappe.get_all(FLOW, filters={"sales_order": parent.name, "sales_order_item": source.name},
                               pluck="name", limit_page_length=0)
        if len(names) > 1:
            frappe.throw("销售订单来源明细关联了多个流程单，请由管理员核对")
        cache[key] = names[0] if names else None
    return cache[key]


def _same_links(row, source, pairs):
    for target_field, source_field in pairs:
        if row.get(target_field) and row.get(target_field) != source.get(source_field):
            frappe.throw("销售明细的精确来源链不一致")


def _return_source(document, row, cache):
    if not document.get("is_return") or not document.get("return_against"):
        return None
    parent = _read_document(document.doctype, document.return_against, cache)
    if parent.get("is_return"):
        frappe.throw("销售退回必须引用原销售单据")
    child_name = row.get(RETURN_CHILD[document.doctype])
    if not child_name:
        if row.get("jf_flow_order") or any(item.get("jf_flow_order") for item in parent.get("items") or []):
            frappe.throw("关联流程单的销售退回必须提供精确原明细，不能按商品推断")
        return parent, None  # Preserve native untracked return handling.
    return _source_row(document, row, document.doctype, parent.name, child_name, cache)


def _validate_chain(document, row, doctype, parent, source, flow, cache, seen):
    key = (doctype, parent.name, source.name)
    if key in seen:
        return
    seen.add(key)
    if doctype == "Sales Order":
        if (flow.get("sales_order") != parent.name or flow.get("sales_order_item") != source.name
                or _sales_order_flow(parent, source, cache) != flow.name):
            frappe.throw("销售订单精确来源与流程单不一致")
        return
    if source.get("jf_flow_order") and source.jf_flow_order != flow.name:
        frappe.throw("销售来源链关联了不同流程单")
    # Inspect the persisted source's own links as well, even if the new row
    # omitted optional upstream links. All traced ancestors must be readable.
    for source_type, parent_field, child_field in SOURCES[doctype]:
        if source.get(parent_field) or source.get(child_field):
            upstream = _source_row(document, row, source_type, source.get(parent_field),
                                   source.get(child_field), cache)
            _validate_chain(document, row, source_type, *upstream, flow, cache, seen)


def validate_fulfillment_flow(document, method=None):
    """Inherit only an exact readable source's Flow, never an Item-based guess.

    A persisted DN/SI row is authoritative, including its empty Flow. This avoids
    retroactively assigning a newly generated Flow to old untracked shipments.
    """
    if document.doctype not in SOURCES:
        return
    cache = {}
    for row in document.get("items") or []:
        if not _has_flow_context(document, row, cache):
            continue
        references = {}
        for doctype, parent_field, child_field in SOURCES[document.doctype]:
            if row.get(parent_field) or row.get(child_field):
                references[doctype] = _source_row(
                    document, row, doctype, row.get(parent_field), row.get(child_field), cache)
        returned = _return_source(document, row, cache)

        historical_type = "Delivery Note" if document.doctype == "Sales Invoice" else "Sales Invoice"
        historical = references.get(historical_type)
        if returned:
            authoritative = returned[1]
            flow_name = authoritative.get("jf_flow_order") if authoritative else None
            if authoritative:
                _same_links(row, authoritative, [(field, field) for _, parent_field, child_field in
                            SOURCES[document.doctype] for field in (parent_field, child_field)])
        elif historical:
            authoritative = historical[1]
            flow_name = authoritative.get("jf_flow_order")
            _same_links(row, authoritative, [
                ("sales_order" if document.doctype == "Sales Invoice" else "against_sales_order",
                 "against_sales_order" if document.doctype == "Sales Invoice" else "sales_order"),
                ("so_detail", "so_detail"),
            ])
        elif references.get("Sales Order") and not document.get("is_return"):
            flow_name = _sales_order_flow(*references["Sales Order"], cache)
        else:
            flow_name = None

        if not flow_name:
            if row.get("jf_flow_order"):
                frappe.throw("销售明细流程单与精确来源不一致")
            row.jf_flow_order = None
            continue

        flow = _read_document(FLOW, flow_name, cache)
        if row.get("jf_flow_order") and row.jf_flow_order != flow_name:
            frappe.throw("销售明细流程单与精确来源不一致")
        if flow.company != document.company or flow.item_code != row.item_code:
            frappe.throw("销售明细与流程单的公司、商品不一致")
        seen = set()
        for doctype, (parent, source) in references.items():
            _validate_chain(document, row, doctype, parent, source, flow, cache, seen)
        if returned and returned[1]:
            _validate_chain(document, row, document.doctype, *returned, flow, cache, seen)
        row.jf_flow_order = flow.name
