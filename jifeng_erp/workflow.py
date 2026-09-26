from decimal import Decimal

import frappe
from frappe.utils import add_days, today

from jifeng_erp.costing import CostingError, number
from jifeng_erp.jushuitan import record_key

MATERIAL_FIELDS = ("item_code", "item_name", "uom", "qty_per_unit", "unit_price", "supplier", "note")
OPERATION_FIELDS = ("operation", "supplier", "unit_price", "note")
BLOCKED_STATUSES = {"Cancelled", "Merged", "Split"}


def recipe_totals(doc):
    try:
        materials = sum(number(r.qty_per_unit, "单件用量") * number(r.unit_price, "物料单价") for r in doc.materials)
        operations = sum(number(r.unit_price, "加工单价") for r in doc.operations)
    except CostingError as exc:
        frappe.throw(str(exc))
    return materials, operations


def copy_recipe(target, source):
    for table, fields in (("materials", MATERIAL_FIELDS), ("operations", OPERATION_FIELDS)):
        target.set(table, [{f: row.get(f) for f in fields} for row in source.get(table)])


def create_flows(order):
    order.check_permission("write")
    frappe.db.get_value(order.doctype, order.name, "name", for_update=True)
    order.reload()
    if order.source_status in BLOCKED_STATUSES:
        frappe.throw("取消、合并或拆分的源订单不能生成新流程单")
    created = []
    for row in order.items:
        if row.source_status == "Cancelled":
            continue
        key = record_key(order.name, row.external_line_id)
        existing = frappe.db.get_value("Jifeng Flow Order", {"source_key": key}, "name")
        if existing:
            row.flow_order = existing
            created.append(existing)
            continue
        if not row.item_code or number(row.qty, "订单数量") <= 0:
            frappe.throw("请先完成商品映射，并确认订单数量大于零")
        profile = frappe.db.get_value("Jifeng Product Profile", {"item_code": row.item_code}, "name")
        flow = frappe.get_doc({
            "doctype": "Jifeng Flow Order", "source_key": key, "external_order": order.name,
            "external_line_id": row.external_line_id, "online_order_id": order.online_order_id,
            "company": order.company, "item_code": row.item_code, "profile": profile,
            "planned_qty": row.qty, "source_qty": row.qty, "source_status": order.source_status,
            "requirements": order.requirements, "packaging": order.packaging,
            "requirements_attachment": order.requirements_attachment,
        })
        if profile:
            template = frappe.get_doc("Jifeng Product Profile", profile)
            template.check_permission("read")
            if template.company != order.company:
                frappe.throw("商品档案公司与订单公司不一致")
            copy_recipe(flow, template)
            flow.supplier_product_code = template.supplier_product_code
            flow.specification = template.specification
            flow.requirements = order.requirements or template.requirements
            flow.packaging = order.packaging or template.packaging
        flow.flags.from_sync = True
        flow.insert()
        row.flow_order = flow.name
        created.append(flow.name)
    order.save()
    return created


@frappe.whitelist()
def receipt_costs(flow_order):
    flow = frappe.get_doc("Jifeng Flow Order", flow_order)
    flow.check_permission("read")
    if not frappe.has_permission("Subcontracting Receipt", "read"):
        frappe.throw("没有委外收货单读取权限", frappe.PermissionError)
    # Parent get_list respects company/user permissions; child rows alone do not.
    parents = frappe.get_list("Subcontracting Receipt", filters=[
        ["Subcontracting Receipt", "docstatus", "=", 1],
        ["Subcontracting Receipt", "company", "=", flow.company],
        ["Subcontracting Receipt Item", "jf_flow_order", "=", flow.name],
    ], pluck="name", distinct=True, limit_page_length=0)
    rows = frappe.get_all("Subcontracting Receipt Item", filters={
        "parent": ["in", parents], "parenttype": "Subcontracting Receipt",
        "jf_flow_order": flow.name, "item_code": flow.item_code,
    }, fields=["parent", "qty", "rm_cost_per_qty", "service_cost_per_qty", "additional_cost_per_qty"]) if parents else []
    result = [{
        "receipt": r.parent, "qty": r.qty,
        "material": Decimal(str(r.qty or 0)) * Decimal(str(r.rm_cost_per_qty or 0)),
        "processing": Decimal(str(r.qty or 0)) * Decimal(str(r.service_cost_per_qty or 0)),
        "other": Decimal(str(r.qty or 0)) * Decimal(str(r.additional_cost_per_qty or 0)),
    } for r in rows]
    return {"rows": result, "totals": {key: sum((r[key] for r in result), Decimal(0)) for key in ("material", "processing", "other")},
            "currency": frappe.db.get_value("Company", flow.company, "default_currency")}


@frappe.whitelist()
def stock_availability(flow_order):
    flow = frappe.get_doc("Jifeng Flow Order", flow_order)
    flow.check_permission("read")
    if not frappe.has_permission("Stock Entry", "read"):
        frappe.throw("没有库存读取权限", frappe.PermissionError)
    warehouses = frappe.get_list("Warehouse", filters={"company": flow.company, "is_group": 0}, pluck="name", limit_page_length=0)
    items = list({row.item_code for row in flow.materials})
    bins = frappe.get_all("Bin", filters={"warehouse": ["in", warehouses], "item_code": ["in", items]},
                         fields=["item_code", "warehouse", "actual_qty", "projected_qty"]) if warehouses and items else []
    return [{**row, "stock_uom": frappe.db.get_value("Item", row.item_code, "stock_uom")} for row in bins]


def make_material_request(flow):
    flow.check_permission("write")
    existing = frappe.db.get_value(flow.doctype, flow.name, "material_request", for_update=True)
    if existing and frappe.db.exists("Material Request", existing):
        return existing
    flow.reload()
    if flow.source_changed or flow.source_status in BLOCKED_STATUSES:
        frappe.throw("请先核对源订单变更或取消状态")
    if not flow.materials:
        frappe.throw("请先完成本单物料分析")
    if not flow.target_warehouse:
        frappe.throw("请先选择采购入库仓库")
    if any(number(r.qty_per_unit, "单件用量") <= 0 for r in flow.materials):
        frappe.throw("生成采购申请前，各物料单件用量必须大于零")
    request = frappe.get_doc({
        "doctype": "Material Request", "material_request_type": "Purchase",
        "company": flow.company, "schedule_date": add_days(today(), 1),
        "items": [{"item_code": r.item_code, "uom": r.uom,
                   "qty": float(number(r.qty_per_unit, "用量") * number(flow.planned_qty, "计划数量")),
                   "schedule_date": add_days(today(), 1), "warehouse": flow.target_warehouse,
                   "jf_flow_order": flow.name} for r in flow.materials],
    })
    request.insert()
    flow.material_request = request.name
    flow.save()
    return request.name
