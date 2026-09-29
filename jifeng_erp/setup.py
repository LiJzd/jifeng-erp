import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def install():
    fields = {doctype: [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "item_code", "no_copy": 1,
    }] for doctype in ("Subcontracting Receipt Item", "Material Request Item", "Stock Entry Detail")}
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
    create_custom_fields(fields, update=True)


def validate_flow_links(doc, method=None):
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
        flow = work_flow if work_flow and row.jf_flow_order == work_flow.name else frappe.get_doc(
            "Jifeng Flow Order", row.jf_flow_order
        )
        flow.check_permission("read")
        if flow.company != doc.company:
            frappe.throw("流程单与业务凭证公司不一致")
        if doc.doctype == "Subcontracting Receipt" and flow.item_code != row.item_code:
            frappe.throw("委外收货商品与流程单商品不一致")
        if doc.doctype in ("Stock Entry", "Material Request"):
            valid_items = {m.item_code for m in flow.materials} | {flow.item_code}
            if row.item_code not in valid_items:
                frappe.throw("凭证物料不在流程单物料分析中，请先补齐分析")
