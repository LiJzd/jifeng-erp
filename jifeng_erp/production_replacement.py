"""Create explicit replacement orders from posted manufacturing losses."""

import hashlib
import json

import frappe

from jifeng_erp.costing import number
from jifeng_erp.flow_quantity import check_quantity_fields

ZERO = number(0, "数量")
TOLERANCE = number("0.000001", "数量精度")


WORK_FIELDS = (
    "name", "modified", "docstatus", "company", "production_item", "bom_no", "qty",
    "produced_qty", "process_loss_qty", "sales_order", "sales_order_item", "jf_flow_order",
    "jf_replacement_of", "track_semi_finished_goods", "status",
)


def _locked_context(flow_order, expected_version):
    from jifeng_erp.production_instruction import _match
    from jifeng_erp.workflow import stock_plan_quantity, validate_flow_source
    from jifeng_erp.flow_cost_sources import production_batch_candidates

    flow = frappe.get_doc("Jifeng Flow Order", flow_order, for_update=True)
    flow.check_permission("read")
    flow.check_permission("write")
    check_quantity_fields("Jifeng Flow Order", (
        "name", "modified", "company", "item_code", "bom", "work_order", "stock_uom",
        "sales_order", "sales_order_item",
    ))
    if str(flow.modified) != str(expected_version):
        frappe.throw("流程单已变化，请刷新后重新核对补产数量", frappe.TimestampMismatchError)
    validate_flow_source(flow)
    plan = number(stock_plan_quantity(flow), "流程单库存计划")
    check_quantity_fields("Work Order", WORK_FIELDS)
    try:
        rows = frappe.db.sql("""SELECT name FROM `tabWork Order`
            WHERE jf_flow_order=%s AND docstatus<2 ORDER BY name FOR UPDATE NOWAIT""",
                             (flow.name,), as_dict=True)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError):
        frappe.throw("本流程工单正在被处理，请刷新后再核对补产")
    works = []
    for row in rows:
        work = frappe.get_doc("Work Order", row.name, for_update=True)
        work.check_permission("read")
        if work.company != flow.company or work.jf_flow_order != flow.name or work.docstatus >= 2:
            frappe.throw("有效生产工单来源发生变化，请刷新后核对")
        _match(flow, work.production_item, work.bom_no, work.get("sales_order"), work.get("sales_order_item"))
        if work.get("track_semi_finished_goods"):
            frappe.throw("半成品工单请从生产计划按准确工序安排补产")
        for field in ("qty", "produced_qty", "process_loss_qty"):
            if number(work.get(field), "工单数量") < 0:
                frappe.throw("工单数量或损耗进度异常，请先核对原单")
        if number(work.process_loss_qty, "工单损耗") > number(work.qty, "工单数量"):
            frappe.throw("工单损耗超过工单数量，请先核对原单")
        works.append(work)
    candidates = production_batch_candidates(flow.name)
    losses = {}
    for source in candidates:
        if (source["company"] != flow.company or source["item_code"] != flow.item_code
                or source["stock_uom"] != flow.stock_uom):
            frappe.throw("已出账损耗与流程单的公司、商品或库存单位不一致")
        value = number(source["lost_qty"], "已出账制造损耗")
        if value < 0:
            frappe.throw("已出账制造损耗数量异常")
        losses[source["work_order"]] = losses.get(source["work_order"], ZERO) + value
    commitments = [max(ZERO, number(work.produced_qty, "产出数量"),
                       number(work.qty, "工单数量") - number(work.process_loss_qty, "损耗数量"))
                   if work.docstatus == 1 else number(work.qty, "草稿预留") for work in works]
    remainder = plan - sum(commitments, ZERO)
    if remainder < -TOLERANCE:
        frappe.throw("有效工单预留已超过流程单计划，请先核对原单")
    sources = []
    for work in works:
        loss = number(work.process_loss_qty, "工单损耗")
        if work.docstatus != 1 or loss <= 0 or work.status in ("Stopped", "Closed"):
            continue
        if abs(losses.get(work.name, ZERO) - loss) > TOLERANCE:
            frappe.throw("工单损耗进度与已出账损耗不一致，请先核对损耗凭证")
        drafts = [doc for doc in works if doc.docstatus == 0 and doc.get("jf_replacement_of") == work.name]
        if len(drafts) > 1:
            frappe.throw("同一损耗工单已有多张补产草稿，请先核对")
        existing = drafts[0] if drafts else None
        booked = sum((number(doc.qty, "原损耗已安排补产") for doc in works
                      if doc.get("jf_replacement_of") == work.name), ZERO)
        if booked > loss + TOLERANCE:
            frappe.throw("原工单的有效补产数量超过已出账损耗，请先核对原单")
        sources.append({"work_order": work.name, "qty": float(work.qty),
                        "produced_qty": float(work.produced_qty or 0), "loss_qty": float(loss),
                        "available_qty": float(max(ZERO, min(remainder, loss - booked))),
                        "draft": existing.name if existing else None,
                        "draft_qty": float(existing.qty) if existing else 0})
    state = {"user": frappe.session.user, "flow": flow.name, "version": str(flow.modified),
             "plan": str(plan), "works": [{field: work.get(field) for field in WORK_FIELDS} for work in works],
             "posted_sources": candidates}
    snapshot = hashlib.sha256(json.dumps(state, sort_keys=True, default=str,
                                        separators=(",", ":")).encode()).hexdigest()
    return flow, {"flow_order": flow.name, "version": str(flow.modified), "stock_uom": flow.stock_uom,
                  "planned_qty": float(plan), "available_qty": float(max(ZERO, remainder)),
                  "sources": sources, "snapshot": snapshot}


@frappe.whitelist(methods=["GET"])
def inspect_replacement(flow_order, expected_version):
    return _locked_context(flow_order, expected_version)[1]


@frappe.whitelist(methods=["POST"])
def make_replacement(flow_order, expected_version, source_work_order, snapshot):
    flow, context = _locked_context(flow_order, expected_version)
    if not snapshot or snapshot != context["snapshot"]:
        frappe.throw("工单、库存损耗或预留数量已变化，请重新核对后生成补产草稿")
    source = next((row for row in context["sources"] if row["work_order"] == source_work_order), None)
    if not source:
        frappe.throw("请选择当前流程已出账损耗的有效工单")
    if source["draft"]:
        return {"work_order": source["draft"], "qty": source["draft_qty"], "reused": True}
    quantity = number(source["available_qty"], "可补产数量")
    if quantity <= 0:
        frappe.throw("本流程没有未安排的补产数量，请核对已有工单")
    bom = frappe.get_doc("BOM", flow.bom)
    bom.check_permission("read")
    if (bom.docstatus != 1 or not bom.is_active or bom.company != flow.company
            or bom.item != flow.item_code or bom.get("track_semi_finished_goods")):
        frappe.throw("补产 BOM 未提交、停用、来源不一致或已改为半成品工序，请先核对")
    from jifeng_erp.material_issue import assert_no_net_issue_for_manufacturing

    assert_no_net_issue_for_manufacturing(flow)
    work = frappe.get_doc({"doctype": "Work Order", "company": flow.company,
                          "production_item": flow.item_code, "bom_no": flow.bom,
                          "qty": float(quantity), "sales_order": flow.get("sales_order"),
                          "sales_order_item": flow.get("sales_order_item"), "jf_flow_order": flow.name,
                          "jf_replacement_of": source_work_order})
    work.get_items_and_operations_from_bom()
    work.insert()
    # Keep the Flow's original order as its primary link; the explicit source on
    # the new native order and the Flow-filtered order list retain every cycle.
    return {"work_order": work.name, "qty": float(quantity), "reused": False}


def validate_replacement(doc):
    previous = doc.get_doc_before_save() if not doc.is_new() else None
    if previous and (previous.get("jf_replacement_of") or "") != (doc.get("jf_replacement_of") or ""):
        frappe.throw("补产原工单来源保存后不能修改，请取消错误草稿后重新生成")
    if not doc.get("jf_replacement_of"):
        return
    if doc.jf_replacement_of == doc.name or not doc.get("jf_flow_order"):
        frappe.throw("补产工单必须关联本流程的其他原工单")
    source = frappe.get_doc("Work Order", doc.jf_replacement_of, for_update=True)
    source.check_permission("read")
    if (source.docstatus != 1 or source.company != doc.company
            or source.get("jf_flow_order") != doc.jf_flow_order or source.production_item != doc.production_item
            or source.bom_no != doc.bom_no or source.get("track_semi_finished_goods")
            or doc.get("track_semi_finished_goods")
            or (source.get("sales_order") or "") != (doc.get("sales_order") or "")
            or (source.get("sales_order_item") or "") != (doc.get("sales_order_item") or "")):
        frappe.throw("补产原工单的状态、公司、商品、BOM 或销售来源不一致")
    if doc.docstatus == 0 or getattr(doc, "_action", None) == "submit":
        if number(source.get("process_loss_qty"), "原工单损耗") <= 0 or source.status in ("Stopped", "Closed"):
            frappe.throw("原工单尚无有效损耗，或已停止/关闭，请重新核对补产来源")
        other = frappe.db.sql("""SELECT qty FROM `tabWork Order`
            WHERE jf_replacement_of=%s AND docstatus<2 AND name!=%s ORDER BY name FOR UPDATE NOWAIT""",
                              (source.name, doc.name or ""), as_dict=True)
        if sum((number(row.qty, "有效补产数量") for row in other), ZERO) + number(doc.qty, "本次补产数量") > (
                number(source.process_loss_qty, "原工单损耗") + TOLERANCE):
            frappe.throw("有效补产数量超过原工单已出账损耗，请先核对原单")
