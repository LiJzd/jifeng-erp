"""Keep native production planning linked to its authoritative flow source."""

from collections import defaultdict
from math import isfinite

import frappe
from frappe.utils import flt, now_datetime

from jifeng_erp.costing import CostingError, number


def _quantity(value, label):
    try:
        result = float(number(value, label))
        if not isfinite(result):
            raise CostingError(f"{label} 超出可保存范围")
        return result
    except CostingError as exc:
        frappe.throw(str(exc))


def _flow(name, company=None):
    # Lock before reading allocations so concurrent factories cannot overschedule.
    frappe.db.get_value("Jifeng Flow Order", name, "name", for_update=True)
    flow = frappe.get_doc("Jifeng Flow Order", name)
    flow.check_permission("read")
    if company and flow.company != company:
        frappe.throw("生产计划与流程单公司不一致")
    return flow


def _open_source(flow):
    if flow.get("source_changed") or flow.get("source_status") in {"Cancelled", "Merged", "Split", "Closed"}:
        frappe.throw("流程单来源已变更或关闭，请先核对来源再安排生产")


def _allocated(flow_name, exclude_work=None, exclude_plan=None):
    filters = {"jf_flow_order": flow_name, "docstatus": ["<", 2]}
    if exclude_work:
        filters["name"] = ["!=", exclude_work]
    # Include hidden allocations in the guard; never return their identities.
    rows = frappe.get_all("Work Order", filters=filters,
                          fields=["qty", "production_plan", "docstatus", "produced_qty", "process_loss_qty"])
    return sum(_committed(row) for row in rows if not exclude_plan or row.production_plan != exclude_plan)


def _committed(work):
    # Match native plan semantics for submitted loss, while reserving draft quantities.
    if work.docstatus == 1:
        return max(0, flt(work.get("produced_qty")), flt(work.qty) - flt(work.get("process_loss_qty")))
    return flt(work.qty)


class ProductionInstructionMixin:
    def get_production_items(self):
        frappe.db.get_value("Production Plan", self.name, "name", for_update=True)
        result = super().get_production_items()
        for row in self.po_items:
            if not row.get("jf_flow_order"):
                continue
            works = frappe.get_all("Work Order", filters={"production_plan": self.name,
                                   "production_plan_item": row.name, "docstatus": ["<", 2]},
                                   fields=["qty", "docstatus", "produced_qty", "process_loss_qty"])
            # The native calculation counts submitted orders only. Draft orders from
            # a previous factory click also reserve this linked instruction row.
            result[row.name]["qty"] = max(0, flt(row.planned_qty) - sum(_committed(work) for work in works))
        return result


def _match(flow, item, bom, sales_order, sales_order_item):
    if (flow.item_code != item or flow.bom != bom
            or (flow.get("sales_order") or "") != (sales_order or "")
            or (flow.get("sales_order_item") or "") != (sales_order_item or "")):
        frappe.throw("生产明细与流程单的商品、BOM 或销售来源不一致")


def _bom(flow):
    bom = frappe.get_doc("BOM", flow.bom) if flow.bom else None
    if not bom:
        frappe.throw("所选流程单尚未指定生产 BOM")
    bom.check_permission("read")
    if bom.docstatus != 1 or not bom.is_active or bom.company != flow.company or bom.item != flow.item_code:
        frappe.throw("所选流程单的生产 BOM 未提交、已停用或来源不一致")
    return bom


def validate_work_order(doc):
    if doc.get("production_plan_item"):
        source = frappe.db.get_value("Production Plan Item", doc.production_plan_item,
                                    ["parent", "item_code", "jf_flow_order", "planned_qty"], as_dict=True)
        if not source or source.parent != doc.get("production_plan") or source.item_code != doc.production_item:
            frappe.throw("生产工单与生产计划来源明细不一致")
        if source.jf_flow_order:
            plan = frappe.get_doc("Production Plan", source.parent)
            plan.check_permission("read")
            arranging = doc.docstatus == 0 or doc.is_new() or getattr(doc, "_action", None) == "submit"
            if plan.company != doc.company or plan.docstatus != 1 or (arranging and plan.status == "Closed"):
                frappe.throw("只能从同公司已提交且未关闭的生产计划生成工单")
            if doc.get("jf_flow_order") and doc.jf_flow_order != source.jf_flow_order:
                frappe.throw("生产工单流程单与生产计划来源不一致")
            doc.jf_flow_order = source.jf_flow_order
            works = frappe.get_all("Work Order", filters={"production_plan_item": doc.production_plan_item,
                                   "docstatus": ["<", 2], "name": ["!=", doc.name or ""]},
                                   fields=["qty", "docstatus", "produced_qty", "process_loss_qty"])
            if sum(_committed(work) for work in works) + _committed(doc) > flt(source.planned_qty) + 1e-6:
                frappe.throw("有效生产工单数量超过对应生产计划明细数量")
    if not doc.get("jf_flow_order"):
        return
    flow = _flow(doc.jf_flow_order, doc.company)
    _match(flow, doc.production_item, doc.bom_no, doc.get("sales_order"), doc.get("sales_order_item"))
    # Saving existing submitted orders for native progress updates must stay possible.
    if doc.docstatus == 0 or doc.is_new() or getattr(doc, "_action", None) == "submit":
        _open_source(flow)
        _bom(flow)
    quantity = _quantity(doc.qty, "工单数量")
    if quantity <= 0 or _allocated(flow.name, exclude_work=doc.name) + _committed(doc) > flt(flow.planned_qty) + 1e-6:
        frappe.throw("有效生产工单数量合计超过流程单计划数量，请核对已有工单")


def validate_plan(doc, method=None):
    weight = _quantity(doc.get("jf_instruction_weight"), "指令重量")
    _quantity(doc.get("jf_instruction_amount"), "指令参考金额")
    if weight and not doc.get("jf_instruction_weight_uom"):
        frappe.throw("填写指令重量时必须选择重量单位")
    doc.jf_instruction_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
    quantities = defaultdict(float)
    flows = {}
    for row in sorted(doc.get("po_items") or [], key=lambda row: row.get("jf_flow_order") or ""):
        if not row.get("jf_flow_order"):
            continue
        flow = flows.setdefault(row.jf_flow_order, None)
        if flow is None:
            flow = flows[row.jf_flow_order] = _flow(row.jf_flow_order, doc.company)
        _match(flow, row.item_code, row.bom_no, row.get("sales_order"), row.get("sales_order_item"))
        if row.get("item_reference"):
            frappe.throw("关联流程单的计划明细不能合并多个销售来源，请分别安排")
        if doc.docstatus == 0 or getattr(doc, "_action", None) == "submit":
            _open_source(flow)
            _bom(flow)
        quantities[flow.name] += _quantity(row.planned_qty, "计划数量")
    for name, quantity in quantities.items():
        if quantity <= 0 or quantity + _allocated(name, exclude_plan=doc.name) > flt(flows[name].planned_qty) + 1e-6:
            frappe.throw("本计划数量加其他有效工单数量超过流程单计划数量")


def validate_flow_allocation(doc, method=None):
    if doc.is_new():
        return
    frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
    works = frappe.get_all("Work Order", filters={"jf_flow_order": doc.name, "docstatus": ["<", 2]},
                           fields=["qty", "bom_no", "company", "production_item", "docstatus",
                                   "produced_qty", "process_loss_qty"])
    if sum(_committed(work) for work in works) > flt(doc.planned_qty) + 1e-6:
        frappe.throw("流程单计划数量不能小于已有有效工单数量")
    if any(work.bom_no != doc.bom or work.company != doc.company or work.production_item != doc.item_code
           for work in works):
        frappe.throw("已有有效生产工单，请先处理工单再变更公司、商品或生产 BOM")


@frappe.whitelist()
def flow_plan_rows(company, flow_names):
    """Return an unsaved planning proposal, using readable flows only."""
    if not frappe.has_permission("Production Plan", "create"):
        frappe.throw("无权新建生产计划", frappe.PermissionError)
    names = frappe.parse_json(flow_names)
    if not isinstance(names, list) or not 1 <= len(names) <= 100 or any(not isinstance(name, str) for name in names):
        frappe.throw("请选择 1 至 100 张流程单")
    if len(set(names)) != len(names):
        frappe.throw("流程单不能重复选择")
    company_doc = frappe.get_doc("Company", company)
    company_doc.check_permission("read")
    result = []
    # Sorted locking order also keeps multi-flow imports from deadlocking each other.
    for name in sorted(names):
        flow = _flow(name, company)
        _open_source(flow)
        _bom(flow)
        pending = flt(flow.planned_qty) - _allocated(name)
        if pending <= 1e-6:
            frappe.throw("所选流程单已全部安排工单，请查看已有工单")
        item = frappe.get_doc("Item", flow.item_code)
        item.check_permission("read")
        result.append({"jf_flow_order": name, "item_code": flow.item_code, "bom_no": flow.bom,
                       "planned_qty": pending, "stock_uom": item.stock_uom,
                       "description": item.description, "sales_order": flow.get("sales_order"),
                       "sales_order_item": flow.get("sales_order_item"), "include_exploded_items": 1,
                       "planned_start_date": now_datetime()})
    return result
