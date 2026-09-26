import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def install():
    create_custom_fields({doctype: [{
        "fieldname": "jf_flow_order", "fieldtype": "Link", "label": "集沣流程单",
        "options": "Jifeng Flow Order", "insert_after": "item_code", "no_copy": 1,
    }] for doctype in ("Subcontracting Receipt Item", "Material Request Item", "Stock Entry Detail")}, update=True)


def validate_flow_links(doc, method=None):
    for row in doc.get("items") or []:
        if not row.get("jf_flow_order"):
            continue
        flow = frappe.get_doc("Jifeng Flow Order", row.jf_flow_order)
        flow.check_permission("read")
        if flow.company != doc.company:
            frappe.throw("流程单与业务凭证公司不一致")
        if doc.doctype == "Subcontracting Receipt" and flow.item_code != row.item_code:
            frappe.throw("委外收货商品与流程单商品不一致")
        if doc.doctype in ("Stock Entry", "Material Request"):
            valid_items = {m.item_code for m in flow.materials} | {flow.item_code}
            if row.item_code not in valid_items:
                frappe.throw("凭证物料不在流程单物料分析中，请先补齐分析")
