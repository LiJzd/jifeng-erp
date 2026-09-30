import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def install():
    fields = {doctype: [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "item_code", "no_copy": 1,
    }] for doctype in (
        "Subcontracting Order Item", "Subcontracting Receipt Item", "Material Request Item", "Purchase Order Item",
        "Purchase Receipt Item", "Purchase Invoice Item", "Stock Entry Detail",
    )}
    fields["Sales Order Item"] = [
        {"fieldname": "jf_make_flow", "fieldtype": "Check", "label": "生成集沣流程单",
         "insert_after": "description", "default": "1", "in_list_view": 1, "allow_on_submit": 1},
        {"fieldname": "jf_requirements", "fieldtype": "Small Text", "label": "集沣定制/工艺要求",
         "insert_after": "jf_make_flow", "allow_on_submit": 1},
        {"fieldname": "jf_packaging", "fieldtype": "Small Text", "label": "集沣包装要求",
         "insert_after": "jf_requirements", "allow_on_submit": 1},
        {"fieldname": "jf_requirements_attachment", "fieldtype": "Attach", "label": "集沣要求附件",
         "insert_after": "jf_packaging", "allow_on_submit": 1},
    ]
    fields["Work Order"] = [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "sales_order", "in_list_view": 1,
    }]
    fields["Stock Entry"] = [{
        "fieldname": "jf_material_issue_flow", "fieldtype": "Link", "label": "按单配料流程单",
        "options": "Jifeng Flow Order", "insert_after": "work_order", "read_only": 1,
        "no_copy": 1,
    }]
    fields["Journal Entry"] = [{
        "fieldname": "jf_expense_voucher", "fieldtype": "Link", "label": "集沣费用登记",
        "options": "Jifeng Expense Voucher", "insert_after": "user_remark",
        "read_only": 1, "no_copy": 1, "unique": 1,
    }]
    create_custom_fields(fields, update=True)


_PROCUREMENT_SOURCES = {
    "Purchase Order": (("material_request_item", "Material Request Item", "material_request"),),
    "Purchase Receipt": (("purchase_order_item", "Purchase Order Item", "purchase_order"),),
    "Purchase Invoice": (
        ("pr_detail", "Purchase Receipt Item", "purchase_receipt"),
        ("po_detail", "Purchase Order Item", "purchase_order"),
    ),
    "Subcontracting Receipt": (
        ("subcontracting_order_item", "Subcontracting Order Item", "subcontracting_order"),
    ),
}

_RETURN_SOURCES = {
    "Purchase Receipt": ("purchase_receipt_item", "Purchase Receipt Item"),
    "Purchase Invoice": ("purchase_invoice_item", "Purchase Invoice Item"),
    "Subcontracting Receipt": ("subcontracting_receipt_item", "Subcontracting Receipt Item"),
}


def _propagate_procurement_flow(doc):
    sources = _PROCUREMENT_SOURCES.get(doc.doctype, ())
    if doc.get("is_return") and doc.doctype in _RETURN_SOURCES:
        source_field, source_doctype = _RETURN_SOURCES[doc.doctype]
        sources += ((source_field, source_doctype, "return_against"),)
    for row in doc.get("items") or []:
        source_flows = set()
        for source_field, source_doctype, parent_field in sources:
            source_name = row.get(source_field)
            if not source_name:
                continue
            source = frappe.db.get_value(
                source_doctype, source_name, ["parent", "item_code", "jf_flow_order"], as_dict=True,
            )
            if not source:
                frappe.throw("采购来源明细不存在，请重新生成单据")
            parent_name = row.get(parent_field) or doc.get(parent_field)
            if parent_field == "return_against" and not parent_name:
                frappe.throw("退回来源单据不存在")
            if parent_name and source.parent != parent_name:
                frappe.throw("采购来源单据与来源明细不一致")
            if source.item_code != row.item_code:
                frappe.throw("采购明细商品与来源明细不一致")
            if source.jf_flow_order:
                source_flows.add(source.jf_flow_order)
        if len(source_flows) > 1:
            frappe.throw("采购来源明细关联了不同流程单")
        if source_flows:
            flow_name = source_flows.pop()
            if row.get("jf_flow_order") and row.jf_flow_order != flow_name:
                frappe.throw("采购明细流程单与来源单据不一致")
            row.jf_flow_order = flow_name


def _propagate_subcontracting_stock_flow(doc):
    if not doc.get("subcontracting_order"):
        return
    for row in doc.get("items") or []:
        if not row.get("sco_rm_detail"):
            continue
        supplied = frappe.db.get_value(
            "Subcontracting Order Supplied Item", row.sco_rm_detail,
            ["parent", "reference_name", "main_item_code", "rm_item_code"], as_dict=True,
        )
        if not supplied or supplied.parent != doc.subcontracting_order:
            frappe.throw("委外发料来源明细与委外订单不一致")
        if supplied.rm_item_code != row.item_code or (
            row.get("subcontracted_item") and supplied.main_item_code != row.subcontracted_item
        ):
            frappe.throw("委外发料物料与来源明细不一致")
        if not supplied.reference_name:
            continue
        source = frappe.db.get_value(
            "Subcontracting Order Item", supplied.reference_name,
            ["parent", "item_code", "jf_flow_order"], as_dict=True,
        )
        if not source or source.parent != doc.subcontracting_order or source.item_code != supplied.main_item_code:
            frappe.throw("委外发料成品与委外订单明细不一致")
        if source.jf_flow_order:
            if row.get("jf_flow_order") and row.jf_flow_order != source.jf_flow_order:
                frappe.throw("委外发料流程单与来源单据不一致")
            row.jf_flow_order = source.jf_flow_order


def validate_flow_links(doc, method=None):
    if doc.doctype in _PROCUREMENT_SOURCES:
        _propagate_procurement_flow(doc)
    if doc.doctype == "Stock Entry":
        if doc.get("jf_material_issue_flow"):
            from jifeng_erp.material_issue import validate_material_issue

            validate_material_issue(doc)
        _propagate_subcontracting_stock_flow(doc)
    if doc.doctype == "Work Order":
        if not doc.get("jf_flow_order"):
            return
        flow = frappe.get_doc("Jifeng Flow Order", doc.jf_flow_order)
        flow.check_permission("read")
        if (flow.company != doc.company or flow.item_code != doc.production_item
                or flow.bom != doc.bom_no or flow.sales_order != doc.sales_order
                or flow.sales_order_item != doc.sales_order_item):
            frappe.throw("生产工单与集沣流程单的公司、商品、BOM 或销售来源不一致")
        return
    work_flow = None
    if doc.doctype == "Stock Entry" and doc.get("work_order"):
        work_order = frappe.get_doc("Work Order", doc.work_order)
        if work_order.get("jf_flow_order"):
            work_flow = frappe.get_doc("Jifeng Flow Order", work_order.jf_flow_order)
            work_flow.check_permission("read")
            if work_order.company != doc.company or work_flow.company != doc.company:
                frappe.throw("生产工单、库存凭证与流程单公司不一致")
            if work_order.production_item != work_flow.item_code:
                frappe.throw("生产工单商品与流程单商品不一致")
            valid_items = {row.item_code for row in work_flow.materials} | {work_flow.item_code}
            for row in doc.get("items") or []:
                if row.get("jf_flow_order") and row.jf_flow_order != work_flow.name:
                    frappe.throw("生产工单库存明细不能关联其他流程单")
                if row.item_code in valid_items:
                    row.jf_flow_order = work_flow.name
    for row in doc.get("items") or []:
        if not row.get("jf_flow_order"):
            continue
        if (doc.doctype == "Subcontracting Receipt" and doc.get("is_return")
                and float(row.get("process_loss_qty") or 0) != 0):
            frappe.throw("关联流程单的委外退货只能退回实物，工艺损耗须为 0；原收货损耗保留。"
                         "若需冲回损耗，请先处理或更正原收货凭证。")
        flow = work_flow if work_flow and row.jf_flow_order == work_flow.name else frappe.get_doc(
            "Jifeng Flow Order", row.jf_flow_order
        )
        flow.check_permission("read")
        if flow.company != doc.company:
            frappe.throw("流程单与业务凭证公司不一致")
        if doc.doctype in ("Subcontracting Order", "Subcontracting Receipt") and flow.item_code != row.item_code:
            frappe.throw("委外单据商品与流程单商品不一致")
        if doc.doctype in (
            "Stock Entry", "Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice",
        ):
            valid_items = {m.item_code for m in flow.materials} | {flow.item_code}
            if row.item_code not in valid_items:
                frappe.throw("凭证物料不在流程单物料分析中，请先补齐分析")
