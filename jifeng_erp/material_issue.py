"""Create and guard stock issues from one flow order's material analysis."""

from collections import defaultdict
from decimal import Decimal

import frappe
from erpnext.stock.get_item_details import get_conversion_factor

from jifeng_erp.costing import CostingError, number
from jifeng_erp.workflow import validate_flow_source


TOLERANCE = Decimal("0.000001")


def _stock_quantity(item_code, uom, qty):
    stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
    if not stock_uom:
        frappe.throw(f"物料 {item_code} 缺少库存单位")
    unit = uom or stock_uom
    factor = Decimal(str(get_conversion_factor(item_code, unit)["conversion_factor"] or 0))
    if not factor.is_finite() or factor <= 0:
        frappe.throw(f"物料 {item_code} 的单位换算系数无效")
    return number(qty, "物料数量") * factor, stock_uom


def _required_stock(flow):
    if not flow.materials:
        frappe.throw("请先完成本单物料分析")
    required = defaultdict(Decimal)
    units = {}
    try:
        planned = number(flow.planned_qty, "计划数量")
        for row in flow.materials:
            per_unit = number(row.qty_per_unit, "单件用量")
            if per_unit <= 0:
                frappe.throw("按单配料前，各物料单件用量必须大于零")
            stock_qty, stock_uom = _stock_quantity(row.item_code, row.uom, planned * per_unit)
            required[row.item_code] += stock_qty
            units[row.item_code] = stock_uom
    except CostingError as exc:
        frappe.throw(str(exc))
    return required, units


def _submitted_stock(flow_name, exclude=None):
    """Include manual Material Issue rows linked to this flow, not just generated ones."""
    rows = frappe.get_all(
        "Stock Entry Detail",
        filters={"jf_flow_order": flow_name, "parenttype": "Stock Entry"},
        fields=["parent", "item_code", "transfer_qty"],
    )
    issued = defaultdict(Decimal)
    parent_status = {}
    for row in rows:
        if row.parent == exclude:
            continue
        if row.parent not in parent_status:
            parent_status[row.parent] = frappe.db.get_value(
                "Stock Entry", row.parent, ["docstatus", "stock_entry_type"], as_dict=True,
            )
        parent = parent_status[row.parent]
        if parent and parent.docstatus == 1 and parent.stock_entry_type == "Material Issue":
            issued[row.item_code] += Decimal(str(row.transfer_qty or 0))
    return issued


def _source_warehouse(name, company):
    if not name:
        frappe.throw("请选择领料来源仓库")
    warehouse = frappe.get_doc("Warehouse", name)
    warehouse.check_permission("read")
    if warehouse.company != company or warehouse.is_group or warehouse.disabled:
        frappe.throw("领料来源仓库必须属于当前公司且为启用的明细仓库")
    return warehouse


def make_material_issue(flow, warehouse):
    """Return an editable Stock Entry draft; only an explicit submit moves stock."""
    flow.check_permission("write")
    frappe.has_permission("Stock Entry", "create", throw=True)
    frappe.db.get_value(flow.doctype, flow.name, "name", for_update=True)
    flow.reload()
    validate_flow_source(flow)
    _source_warehouse(warehouse, flow.company)
    required, units = _required_stock(flow)

    drafts = frappe.get_all(
        "Stock Entry", filters={"jf_material_issue_flow": flow.name, "docstatus": 0},
        pluck="name", limit=2,
    )
    if len(drafts) > 1:
        frappe.throw("本流程单已有多张未提交领料单，请先核对")
    if drafts:
        existing = frappe.get_doc("Stock Entry", drafts[0])
        existing.check_permission("read")
        if existing.company != flow.company or existing.stock_entry_type != "Material Issue":
            frappe.throw("已有领料草稿与当前流程单不一致")
        return existing.name

    issued = _submitted_stock(flow.name)
    items = []
    for item_code, total in required.items():
        remaining = total - issued[item_code]
        if remaining > TOLERANCE:
            items.append({
                "item_code": item_code, "qty": float(remaining), "uom": units[item_code],
                "s_warehouse": warehouse, "jf_flow_order": flow.name,
            })
    if not items:
        frappe.throw("本流程单物料已按分析数量全部出库；额外损耗请单独登记并说明")
    entry = frappe.get_doc({
        "doctype": "Stock Entry", "company": flow.company,
        "stock_entry_type": "Material Issue", "purpose": "Material Issue",
        "jf_material_issue_flow": flow.name, "items": items,
    })
    entry.insert()
    return entry.name


def validate_material_issue(doc):
    """Reject cross-flow edits and accidental over-issue on save and submit."""
    flow = frappe.get_doc("Jifeng Flow Order", doc.jf_material_issue_flow)
    flow.check_permission("read")
    frappe.db.get_value(flow.doctype, flow.name, "name", for_update=True)
    validate_flow_source(flow)
    if doc.company != flow.company or doc.stock_entry_type != "Material Issue":
        frappe.throw("按单配料单必须为本公司物料出库凭证")
    required, _ = _required_stock(flow)
    issued = _submitted_stock(flow.name, exclude=doc.name)
    current = defaultdict(Decimal)
    if not doc.items:
        frappe.throw("按单配料单缺少物料明细")
    try:
        for row in doc.items:
            if row.jf_flow_order != flow.name or row.item_code not in required:
                frappe.throw("领料明细必须全部关联本流程单物料分析")
            _source_warehouse(row.s_warehouse, flow.company)
            if row.t_warehouse:
                frappe.throw("按单配料出库不能设置目标仓库")
            qty, _ = _stock_quantity(row.item_code, row.uom, row.qty)
            if qty <= 0:
                frappe.throw("领料数量必须大于零")
            current[row.item_code] += qty
    except CostingError as exc:
        frappe.throw(str(exc))
    for item_code, qty in current.items():
        if qty + issued[item_code] > required[item_code] + TOLERANCE:
            frappe.throw(f"物料 {item_code} 的累计领料数量超过流程单物料分析数量")
