"""Exact native Job Card stages; no quantities, warehouses or callbacks changed."""

import frappe


JC, WO, FLOW, BOM = "Job Card", "Work Order", "Jifeng Flow Order", "BOM"
PARENTS = {
    JC: {"name", "modified", "docstatus", "company", "work_order", "production_item", "operation_id",
         "operation", "finished_good", "semi_fg_bom", "track_semi_finished_goods", "is_subcontracted", "jf_flow_order"},
    WO: {"name", "modified", "docstatus", "company", "production_item", "jf_flow_order", "track_semi_finished_goods"},
    BOM: {"name", "modified", "docstatus", "company", "item"},
    FLOW: {"name", "modified", "company", "item_code"},
    "Purchase Order": {"name", "modified", "docstatus", "company", "supplier", "is_subcontracted", "is_old_subcontracting_flow"},
    "Subcontracting Order": {"name", "modified", "docstatus", "company", "supplier", "purchase_order"},
    "Subcontracting Receipt": {"name", "modified", "docstatus", "company", "supplier", "is_return", "return_against"},
}
CHILD = {"name", "modified", "parent", "parenttype", "parentfield", "idx"}
TABLES = {
    WO: ("operations", "Work Order Operation", CHILD | {"operation", "finished_good", "is_subcontracted"}),
    BOM: ("items", "BOM Item", CHILD | {"item_code"}),
    "Purchase Order": ("items", "Purchase Order Item", CHILD | {"item_code", "fg_item", "job_card", "bom", "jf_flow_order"}),
    "Subcontracting Order": ("items", "Subcontracting Order Item", CHILD | {"item_code", "job_card", "bom", "purchase_order_item", "jf_flow_order"}),
    "Subcontracting Receipt": ("items", "Subcontracting Receipt Item", CHILD | {"item_code", "job_card", "bom",
        "purchase_order", "purchase_order_item", "subcontracting_order", "subcontracting_order_item", "subcontracting_receipt_item", "jf_flow_order"}),
}


def _invalid():
    frappe.throw("半成品阶段须来自同流程工单的准确报工工序、阶段商品及BOM，请重新从原生来源生成单据")


def _fields(doctype, fields, parenttype=None):
    from frappe.model import get_permitted_fields

    if not fields.issubset(set(get_permitted_fields(doctype, parenttype=parenttype,
            permission_type="read", ignore_virtual=True))):
        frappe.throw("没有完整读取半成品阶段来源字段的权限", frappe.PermissionError)
    meta = frappe.get_meta(doctype)
    levels = meta.get_permlevel_access(permission_type="mask", parenttype=parenttype, user=frappe.session.user)
    if frappe.session.user != "Administrator" and any(df and df.get("mask") and (df.permlevel or 0) not in levels
            for df in (meta.get_field(field) for field in fields)):
        frappe.throw("没有完整读取半成品阶段来源字段的权限", frappe.PermissionError)


def native_reader(doctype, name):
    document = frappe.get_doc(doctype, name)
    document.check_permission("read")
    _fields(doctype, PARENTS.get(doctype, {"name"}))
    if doctype in TABLES:
        field, childtype, fields = TABLES[doctype]
        meta = frappe.get_meta(doctype)
        df = meta.get_field(field)
        if (not df or df.fieldtype != "Table" or df.options != childtype or (df.permlevel or 0) not in
                meta.get_permlevel_access(permission_type="read", user=frappe.session.user)):
            frappe.throw("没有读取半成品阶段来源明细的权限", frappe.PermissionError)
        if (frappe.session.user != "Administrator" and df.get("mask") and (df.permlevel or 0) not in
                meta.get_permlevel_access(permission_type="mask", user=frappe.session.user)):
            frappe.throw("没有读取半成品阶段来源明细的权限", frappe.PermissionError)
        _fields(childtype, fields, doctype)
    return document


def detail(document, name, field="items"):
    rows = [row for row in document.get(field) or [] if row.get("name") == name]
    if len(rows) != 1:
        _invalid()
    row = rows[0]
    if (row.get("parent") != document.get("name") or row.get("parenttype") != document.get("doctype")
            or row.get("parentfield") != field):
        _invalid()
    return row


def job_card_stage(job_card, item, bom, company, flow_name=None, *, read=native_reader,
                   outsourced=True, historical=False):
    if not job_card or not item or not bom:
        _invalid()
    card = read(JC, job_card)
    work = read(WO, card.get("work_order"))
    flow = read(FLOW, work.get("jf_flow_order"))
    stage_bom = read(BOM, bom)
    read("Item", item)
    if (card.get("docstatus") not in ({0, 1, 2} if historical else {0, 1})
            or work.get("docstatus") not in ({1, 2} if historical else {1})
            or stage_bom.get("docstatus") != 1
            or any(source.get("company") != company for source in (card, work, flow, stage_bom))
            or work.get("production_item") != flow.get("item_code")
            or card.get("production_item") != work.get("production_item")
            or (card.get("jf_flow_order") and card.get("jf_flow_order") != flow.get("name"))
            or not card.get("track_semi_finished_goods") or not work.get("track_semi_finished_goods")
            or card.get("finished_good") != item or card.get("semi_fg_bom") != bom
            or stage_bom.get("item") != item or (flow_name and flow_name != flow.get("name"))):
        _invalid()
    operations = [row for row in work.get("operations") or [] if row.get("name") == card.get("operation_id")]
    if len(operations) != 1:
        _invalid()
    operation = detail(work, card.get("operation_id"), "operations")
    if (not card.get("operation") or operation.get("operation") != card.get("operation")
            or operation.get("finished_good") != item
            or bool(operation.get("is_subcontracted")) != bool(card.get("is_subcontracted"))
            or (outsourced is not None and bool(card.get("is_subcontracted")) != outsourced)):
        _invalid()
    return {"flow": flow, "work_order": work, "job_card": card, "bom": stage_bom, "item": item}


def procurement_stage(document, row, *, read=native_reader, historical=False):
    """Return a stage only through exact native primary/service source keys."""
    doctype = document.get("doctype")
    if doctype == "Purchase Order":
        if not row.get("job_card"):
            return None
        if not document.get("is_subcontracted") or document.get("is_old_subcontracting_flow"):
            _invalid()
        read("Item", row.get("item_code"))
        return job_card_stage(row.get("job_card"), row.get("fg_item"), row.get("bom"), document.get("company"),
                              row.get("jf_flow_order"), read=read, historical=historical)
    if doctype in ("Subcontracting Order", "Subcontracting Receipt"):
        if not row.get("job_card"):
            return None
        if doctype == "Subcontracting Receipt":
            parent = read("Subcontracting Order", row.get("subcontracting_order"))
            upstream = detail(parent, row.get("subcontracting_order_item"))
            stage = procurement_stage(parent, upstream, read=read, historical=historical)
            if (not stage or (row.get("job_card"), row.get("bom"), row.get("item_code")) !=
                    (upstream.get("job_card"), upstream.get("bom"), upstream.get("item_code"))
                    or row.get("purchase_order") != parent.get("purchase_order")
                    or row.get("purchase_order_item") != upstream.get("purchase_order_item")):
                _invalid()
            if document.get("is_return"):
                original = read("Subcontracting Receipt", document.get("return_against"))
                original_row = detail(original, row.get("subcontracting_receipt_item"))
                if (original.get("is_return") or original.get("company") != document.get("company")
                        or original.get("supplier") != document.get("supplier")
                        or original.get("docstatus") not in ({1, 2} if historical else {1})
                        or any(row.get(field) != original_row.get(field) for field in (
                            "item_code", "job_card", "bom", "purchase_order", "purchase_order_item",
                            "subcontracting_order", "subcontracting_order_item"))):
                    _invalid()
        else:
            parent = read("Purchase Order", document.get("purchase_order"))
            upstream = detail(parent, row.get("purchase_order_item"))
            stage = procurement_stage(parent, upstream, read=read, historical=historical)
            if (not stage or (row.get("job_card"), row.get("bom"), row.get("item_code")) !=
                    (upstream.get("job_card"), upstream.get("bom"), upstream.get("fg_item"))):
                _invalid()
        if (parent.get("company") != document.get("company") or parent.get("supplier") != document.get("supplier")
                or parent.get("docstatus") not in ({1, 2} if historical else {1})
                or (row.get("jf_flow_order") and row.get("jf_flow_order") != stage["flow"].get("name"))):
            _invalid()
        return stage
    if doctype in ("Purchase Receipt", "Purchase Invoice"):
        pointer = "po_detail" if doctype == "Purchase Invoice" else "purchase_order_item"
        if not row.get("purchase_order") or not row.get(pointer):
            return None
        parent = read("Purchase Order", row.get("purchase_order"))
        upstream = detail(parent, row.get(pointer))
        stage = procurement_stage(parent, upstream, read=read, historical=historical)
        if stage and (parent.get("company") != document.get("company")
                or parent.get("supplier") != document.get("supplier")
                or parent.get("docstatus") not in ({1, 2} if historical else {1})
                or row.get("item_code") != upstream.get("item_code")
                or (row.get("jf_flow_order") and row.get("jf_flow_order") != stage["flow"].get("name"))):
            _invalid()
        return stage
    return None


def stock_stage(document, *, read=native_reader):
    if not document.get("job_card"):
        return None
    card = read(JC, document.get("job_card"))
    if not card.get("track_semi_finished_goods"):
        return None
    stage = job_card_stage(card.get("name"), card.get("finished_good"), document.get("bom_no"),
                           document.get("company"), read=read, outsourced=None)
    if document.get("work_order") != stage["work_order"].get("name"):
        _invalid()
    if document.get("purpose") not in ("Manufacture", "Material Transfer for Manufacture"):
        _invalid()
    if document.get("purpose") == "Manufacture" and card.get("is_subcontracted"):
        frappe.throw("委外工序应从委外收货回写报工数量，不能另造制造入库重复产出")
    stage["materials"] = {row.get("item_code") for row in stage["bom"].get("items") or []}
    if None in stage["materials"]:
        _invalid()
    for item in stage["materials"]:
        read("Item", item)
    return stage


def subcontract_material_stage(document, row, *, read=native_reader):
    order = read("Subcontracting Order", document.get("subcontracting_order"))
    _fields("Subcontracting Order Supplied Item", CHILD | {"reference_name", "main_item_code", "rm_item_code"},
            "Subcontracting Order")
    meta = frappe.get_meta("Subcontracting Order")
    df = meta.get_field("supplied_items")
    if (not df or df.options != "Subcontracting Order Supplied Item" or df.fieldtype != "Table"
            or (df.permlevel or 0) not in meta.get_permlevel_access(permission_type="read", user=frappe.session.user)
            or (frappe.session.user != "Administrator" and df.get("mask") and (df.permlevel or 0) not in
                meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))):
        frappe.throw("没有读取委外发料阶段来源的权限", frappe.PermissionError)
    supplied = detail(order, row.get("sco_rm_detail"), "supplied_items")
    source = detail(order, supplied.get("reference_name"))
    stage = procurement_stage(order, source, read=read)
    if (not stage or document.get("company") != order.get("company")
            or supplied.get("main_item_code") != stage["item"] or supplied.get("rm_item_code") != row.get("item_code")
            or (row.get("subcontracted_item") and row.get("subcontracted_item") != stage["item"])
            or row.get("item_code") not in {item.get("item_code") for item in stage["bom"].get("items") or []}):
        _invalid()
    read("Item", row.get("item_code"))
    return stage
