"""Book a confirmed, unrecoverable manufacturing loss through native stock issue.

No fictitious finished item is produced. The native Stock Entry owns SLE/GL and
their reversal; this module proves the reporting source and its own WIP stock.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import UUID

import frappe
from frappe.utils import flt, get_datetime

LOSS_TYPE = "制造损耗出库"
PREFIX = "jf_production_loss_"
LOSS_FIELDS = {PREFIX + suffix for suffix in (
    "work_order", "job_card", "qty", "reason", "no_residual", "source_signature", "source_version", "request")}
IDENTITY = {"name", "modified", "docstatus"}
CHILD = {"name", "modified", "parent", "parenttype", "parentfield", "idx"}
PARENTS = {
    "Stock Entry": IDENTITY | LOSS_FIELDS | {"company", "purpose", "stock_entry_type", "work_order", "job_card",
        "from_warehouse", "jf_material_issue_flow", "jf_material_return_against", "fg_completed_qty", "process_loss_qty"},
    "Job Card": IDENTITY | {"company", "work_order", "production_item", "stock_uom", "operation", "operation_id",
        "for_quantity", "total_completed_qty", "process_loss_qty", "pending_qty", "is_subcontracted",
        "track_semi_finished_goods", "is_corrective_job_card", "finished_good", "wip_warehouse"},
    "Work Order": IDENTITY | {"company", "production_item", "stock_uom", "bom_no", "jf_flow_order", "qty",
        "track_semi_finished_goods", "wip_warehouse", "status", "produced_qty",
        "production_plan", "production_plan_item", "production_plan_sub_assembly_item"},
    "Jifeng Flow Order": IDENTITY | {"company", "item_code", "stock_uom", "bom"},
    "Item": {"name", "modified", "stock_uom", "is_stock_item", "has_serial_no", "has_batch_no"},
    "Warehouse": {"name", "modified", "company", "is_group", "disabled"},
}
TABLES = {
    "Stock Entry": (
        ("items", "Stock Entry Detail", CHILD | {"item_code", "jf_flow_order", "qty", "uom", "stock_uom",
            "conversion_factor", "transfer_qty", "s_warehouse", "t_warehouse", "is_finished_item",
            "secondary_item_type", "landed_cost_voucher_amount", "additional_cost", "jf_material_return_against_detail"}),
        ("additional_costs", "Landed Cost Taxes and Charges", CHILD | {"amount", "base_amount"}),
    ),
    "Job Card": (("time_logs", "Job Card Time Log", CHILD | {"employee", "from_time", "to_time", "completed_qty"}),),
    "Work Order": (
        ("required_items", "Work Order Item", CHILD | {"item_code", "required_qty", "stock_uom"}),
        ("operations", "Work Order Operation", CHILD | {"operation"}),
    ),
    "Jifeng Flow Order": (("materials", "Jifeng Material Line", CHILD | {"item_code"}),),
}
ZERO = Decimal(0)


def _qty(value, label="损耗数量"):
    try:
        result = Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        frappe.throw(f"{label}必须是有限数字")
    if not result.is_finite() or result < 0 or isinstance(value, bool):
        frappe.throw(f"{label}必须是非负有限数字")
    return result


def _equal(a, b, doc, field):
    # Actual native child/parent Documents provide the configured Float precision.
    precision = doc.precision(field)
    if precision is None:
        frappe.throw("损耗来源缺少原生数量字段精度")
    return flt(a, precision) == flt(b, precision)


def is_loss_entry(doc):
    return doc.get("stock_entry_type") == LOSS_TYPE or any(doc.get(field) for field in LOSS_FIELDS)


def _required(work):
    quantities = defaultdict(Decimal)
    units = {}
    for row in work.get("required_items") or []:
        qty = _qty(row.get("required_qty"), "工单材料库存数量")
        if not row.get("item_code") or not row.get("stock_uom") or qty <= 0:
            frappe.throw("原工单材料库存数量或单位不完整")
        if row.item_code in units and units[row.item_code] != row.stock_uom:
            frappe.throw("同一工单材料有不同库存单位")
        quantities[row.item_code] += qty
        units[row.item_code] = row.stock_uom
    if not quantities:
        frappe.throw("原工单没有可核实的在制材料")
    return quantities, units


def source_signature(card, work):
    """Stable business source; progress callbacks may change modified/status.

    This hash is an audit comparison, never an authorization token. Stock and
    source caps are re-proved by the controller independently of the hash.
    """
    card_fields = ("name", "company", "work_order", "production_item", "stock_uom", "operation", "operation_id",
                   "for_quantity", "total_completed_qty", "process_loss_qty", "pending_qty", "is_subcontracted",
                   "track_semi_finished_goods", "is_corrective_job_card", "finished_good", "wip_warehouse")
    work_fields = ("name", "company", "production_item", "stock_uom", "bom_no", "jf_flow_order", "qty",
                   "track_semi_finished_goods", "wip_warehouse", "production_plan", "production_plan_item",
                   "production_plan_sub_assembly_item")
    state = {
        "card": {field: card.get(field) for field in card_fields},
        "work": {field: work.get(field) for field in work_fields},
        "logs": [{field: row.get(field) for field in ("name", "employee", "from_time", "to_time", "completed_qty")}
                 for row in card.get("time_logs") or []],
        "materials": [{field: row.get(field) for field in ("name", "item_code", "required_qty", "stock_uom")}
                      for row in work.get("required_items") or []],
        "operations": [{field: row.get(field) for field in ("name", "operation")}
                       for row in work.get("operations") or []],
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _source_context(card, work, flow, read):
    if (card.docstatus != 1 or work.docstatus != 1 or card.work_order != work.name
            or work.get("track_semi_finished_goods") or card.get("track_semi_finished_goods")
            or card.get("is_subcontracted") or card.get("is_corrective_job_card")):
        frappe.throw("制造损耗出库仅支持已提交的普通制造工单和普通报工单")
    if any(work.get(field) for field in ("production_plan", "production_plan_item", "production_plan_sub_assembly_item")):
        frappe.throw("生产计划及子装配来源请使用原生计划损耗与剩余数量安排，当前专用全损耗入口暂不支持")
    if (card.company != work.company or work.company != flow.company
            or work.get("jf_flow_order") != flow.name or work.production_item != flow.item_code
            or card.production_item != work.production_item or work.bom_no != flow.get("bom")
            or card.stock_uom != work.stock_uom or work.stock_uom != flow.stock_uom):
        frappe.throw("损耗来源的公司、流程、商品、BOM 或库存单位不一致")
    if card.get("finished_good") and card.finished_good != work.production_item:
        frappe.throw("半成品报工损耗请按准确工序处理")
    loss = _qty(card.get("process_loss_qty"))
    if (loss <= 0 or _qty(card.get("total_completed_qty")) != 0 or _qty(card.get("pending_qty")) != 0
            or not _equal(loss, _qty(card.get("for_quantity")), card, "for_quantity")):
        frappe.throw("本入口只办理全部损耗、合格完成和待完成均为零的已审核报工")
    if loss > _qty(work.qty):
        frappe.throw("报工损耗超过原工单库存数量")
    operations = [row for row in work.get("operations") or [] if row.name == card.get("operation_id")]
    if len(operations) != 1 or operations[0].operation != card.operation:
        frappe.throw("损耗报工缺少准确工单工序来源")
    if not card.get("time_logs") or any(not row.get("from_time") or not row.get("to_time")
                                        or _qty(row.get("completed_qty")) != 0 for row in card.time_logs):
        frappe.throw("全部损耗必须来自已经结束且无合格完成量的实际报工记录")
    item = read("Item", work.production_item)
    if not item.get("is_stock_item") or item.stock_uom != work.stock_uom:
        frappe.throw("损耗成品库存单位与商品档案不一致")
    warehouse = read("Warehouse", work.get("wip_warehouse"))
    if (not work.get("wip_warehouse") or warehouse.company != work.company
            or warehouse.get("is_group") or warehouse.get("disabled")):
        frappe.throw("原工单在制仓库必须是本公司启用的明细仓库")
    if card.get("wip_warehouse") and card.wip_warehouse != work.wip_warehouse:
        frappe.throw("损耗报工与原工单的在制仓库不一致")
    _required(work)
    return loss


def read_loss_source(doc, *, read):
    """Read-only source proof. All documents come from the caller's closed reader."""
    if doc.get("stock_entry_type") != LOSS_TYPE or doc.get("purpose") != "Material Issue":
        frappe.throw("制造损耗标记和专用库存出库类型必须同时存在")
    if (doc.get("work_order") or doc.get("job_card") or doc.get("jf_material_issue_flow")
            or doc.get("jf_material_return_against") or _qty(doc.get("fg_completed_qty"))
            or _qty(doc.get("process_loss_qty"))):
        frappe.throw("制造损耗出库不能混用原生制造、领料或退库来源")
    try:
        request = str(UUID(str(doc.get(PREFIX + "request"))))
        version = get_datetime(doc.get(PREFIX + "source_version")) if doc.get(PREFIX + "source_version") else None
    except (ValueError, TypeError):
        frappe.throw("制造损耗缺少准确办理标记或来源版本")
    if request != doc.get(PREFIX + "request") or not version:
        frappe.throw("制造损耗缺少准确办理标记或来源版本")
    if doc.get(PREFIX + "no_residual") != 1 or not str(doc.get(PREFIX + "reason") or "").strip():
        frappe.throw("必须明确确认无可回收实物及残值，并填写实际损耗原因")
    card = read("Job Card", doc.get(PREFIX + "job_card"))
    work = read("Work Order", doc.get(PREFIX + "work_order"))
    flow = read("Jifeng Flow Order", work.get("jf_flow_order"))
    loss = _source_context(card, work, flow, read)
    qty = _qty(doc.get(PREFIX + "qty"))
    if qty <= 0 or qty != loss:
        frappe.throw("本次全损耗数量必须等于该报工确认的库存损耗数量")
    if doc.get(PREFIX + "source_signature") != source_signature(card, work):
        frappe.throw("损耗报工或工单来源已变化，请核对来源后重新办理")
    if doc.get("from_warehouse") != work.wip_warehouse or doc.get("additional_costs"):
        frappe.throw("制造损耗只允许原工单在制仓库实际材料出库，不能追加预计费用")
    required, units = _required(work)
    flow_items = {row.item_code for row in flow.get("materials") or []}
    if not doc.get("items"):
        frappe.throw("制造损耗缺少实际材料出库明细")
    for row in doc.items:
        item = read("Item", row.get("item_code"))
        if item.get("has_serial_no") or item.get("has_batch_no"):
            frappe.throw("批号或序号材料必须按原转料逐批核对损耗，当前专用全损耗入口暂不支持")
        if (row.item_code not in required or row.item_code not in flow_items or row.jf_flow_order != flow.name
                or row.stock_uom != units[row.item_code] or item.stock_uom != row.stock_uom
                or not item.get("is_stock_item") or row.s_warehouse != work.wip_warehouse or row.get("t_warehouse")
                or row.get("is_finished_item") or row.get("secondary_item_type")
                or row.get("jf_material_return_against_detail") or _qty(row.get("additional_cost"))
                or _qty(row.get("landed_cost_voucher_amount"))):
            frappe.throw("损耗材料必须属于原工单和流程、使用原在制仓库且不含成品、残值或附加费")
        # This purpose has only stock-unit outward rows. No invented FG UOM.
        if (row.get("uom") != row.stock_uom or _qty(row.get("conversion_factor")) != 1
                or _qty(row.get("qty")) <= 0
                or not _equal(row.qty, _qty(row.get("transfer_qty")), row, "transfer_qty")):
            frappe.throw("制造损耗材料出库数量必须使用准确库存单位、换算系数为一")
    return {"loss_entry": doc.name, "work_order": work.name, "job_card": card.name, "flow_order": flow.name,
            "company": work.company, "item_code": work.production_item, "stock_uom": work.stock_uom,
            "qty": qty, "lost_qty": qty, "inventory_qty": ZERO}


def _sql(query, values):
    try:
        return frappe.db.sql(query, values, as_dict=True)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError) as exc:
        frappe.throw("本工单损耗或在制库存正在更新，请重新读取后再操作", type(exc))


def _internal_read(doctype, name):
    return _lock_source(doctype, name)


def _lock_source(doctype, name):
    if not isinstance(name, str) or not name or doctype not in PARENTS:
        frappe.throw("损耗来源缺少准确单据")
    # Fixed table names; values remain bound parameters. No waits on other
    # Receipt/Entry parents while holding this source lock.
    if not _sql(f"SELECT name FROM `tab{doctype}` WHERE name=%s FOR UPDATE NOWAIT", (name,)):
        frappe.throw("损耗来源已不存在")
    for field, childtype, _fields in TABLES.get(doctype, ()):
        _sql(f"SELECT name FROM `tab{childtype}` WHERE parent=%s AND parenttype=%s AND parentfield=%s "
             "ORDER BY name FOR UPDATE NOWAIT", (name, doctype, field))
    return frappe.get_doc(doctype, name, for_update=True)


def _authorized_read(doctype, name):
    from jifeng_erp.flow_quantity import check_quantity_fields

    doc = _lock_source(doctype, name)
    doc.check_permission("read")
    check_quantity_fields(doctype, PARENTS[doctype])
    for field, childtype, fields in TABLES.get(doctype, ()):
        check_quantity_fields(doctype, (field,))
        check_quantity_fields(childtype, fields, parenttype=doctype)
    return doc


def _loss_documents(*, work_order=None, job_card=None, include_drafts=False, exclude=None):
    # Internal quantities cannot omit hidden submitted vouchers. Identities are
    # never exposed by the progress aggregate. Locking reads avoid old snapshots.
    field, name = ("work_order", work_order) if work_order else ("job_card", job_card)
    rows = _sql(f"SELECT name FROM `tabStock Entry` WHERE {PREFIX + field}=%s "
                f"AND docstatus {'<2' if include_drafts else '=1'} ORDER BY name FOR UPDATE NOWAIT", (name,))
    docs = []
    for row in rows:
        if row.name != exclude:
            doc = _lock_source("Stock Entry", row.name)
            source = read_loss_source(doc, read=_internal_read)
            if doc.docstatus == 1:
                _entry_wip_quantities(doc, _internal_read("Work Order", source["work_order"]))
            docs.append(doc)
    return docs


def _booked(work, card=None, exclude=None):
    docs = _loss_documents(work_order=work.name, exclude=exclude)
    totals = defaultdict(Decimal)
    for doc in docs:
        totals[doc.get(PREFIX + "job_card")] += _qty(doc.get(PREFIX + "qty"))
    for name, amount in totals.items():
        source = _internal_read("Job Card", name)
        if not _equal(min(amount, _qty(source.process_loss_qty)), amount, source, "process_loss_qty"):
            frappe.throw("同报工的有效损耗出库超过确认损耗")
    return docs, (totals[card.name] if card else sum(totals.values(), ZERO))


def get_booked_loss_for_work_order(work_order):
    work = _internal_read("Work Order", work_order)
    return _booked(work)[1]


def get_booked_loss_for_job_card(job_card):
    card = _internal_read("Job Card", job_card)
    work = _internal_read("Work Order", card.work_order)
    return _booked(work, card)[1]


def _entry_wip_quantities(doc, work):
    rows = _sql("""SELECT name, voucher_detail_no, item_code, warehouse, stock_uom, company, actual_qty,
        stock_value_difference, is_adjustment_entry FROM `tabStock Ledger Entry`
        WHERE voucher_type='Stock Entry' AND voucher_no=%s AND is_cancelled=0 ORDER BY name FOR UPDATE NOWAIT""",
                (doc.name,))
    expected, actual = {}, defaultdict(Decimal)
    details = {row.name: row for row in doc.items}
    for row in doc.items:
        if row.get("s_warehouse") == work.wip_warehouse:
            expected[row.name, row.item_code, work.wip_warehouse] = -_qty(row.transfer_qty, "库存出库数量")
        if row.get("t_warehouse") == work.wip_warehouse:
            key = row.name, row.item_code, work.wip_warehouse
            expected[key] = expected.get(key, ZERO) + _qty(row.transfer_qty, "库存入库数量")
    for entry in rows:
        if entry.warehouse != work.wip_warehouse:
            continue
        key = entry.voucher_detail_no, entry.item_code, entry.warehouse
        detail = details.get(entry.voucher_detail_no)
        if (key not in expected or not detail or entry.company != work.company
                or entry.stock_uom != detail.stock_uom or entry.get("is_adjustment_entry")):
            frappe.throw("原工单在制库存账与准确材料明细不一致")
        try:
            quantity = Decimal(str(entry.actual_qty))
            value = Decimal(str(entry.stock_value_difference))
        except (ValueError, InvalidOperation):
            frappe.throw("在制库存账数量异常")
        if not quantity.is_finite() or not value.is_finite() or quantity == 0 or quantity * expected[key] <= 0:
            frappe.throw("原工单在制库存账方向异常")
        actual[key] += quantity
    if set(actual) != set(expected) or any(not _equal(actual[key], qty, details[key[0]], "transfer_qty")
                                          for key, qty in expected.items()):
        frappe.throw("原工单在制库存账不完整，不能使用其他工单的混合库存")
    totals = defaultdict(Decimal)
    for (_detail, item, _warehouse), qty in actual.items():
        totals[item] += qty
    return totals


def _own_wip_pool(work, exclude=None):
    rows = _sql(f"""SELECT name FROM `tabStock Entry` WHERE docstatus=1 AND
        ((work_order=%s AND purpose IN ('Material Transfer for Manufacture','Manufacture','Material Consumption for Manufacture'))
         OR {PREFIX}work_order=%s) ORDER BY name FOR UPDATE NOWAIT""", (work.name, work.name))
    pool = defaultdict(Decimal)
    for row in rows:
        if row.name == exclude:
            continue
        doc = _lock_source("Stock Entry", row.name)
        if doc.company != work.company:
            frappe.throw("原工单材料出入库公司不一致")
        if is_loss_entry(doc):
            read_loss_source(doc, read=_internal_read)
        elif doc.work_order != work.name or doc.purpose not in (
                "Material Transfer for Manufacture", "Manufacture", "Material Consumption for Manufacture"):
            frappe.throw("原工单在制材料来源不一致")
        for item, qty in _entry_wip_quantities(doc, work).items():
            pool[item] += qty
    if any(qty < 0 for qty in pool.values()):
        frappe.throw("原工单已出账材料超过其转入在制库存，请先核对原单")
    return pool


def _validate_budget(doc, source, work, card):
    if _loss_documents(job_card=card.name, include_drafts=True, exclude=doc.name):
        frappe.throw("该报工已有有效损耗出库或草稿，不能重复办理")
    other_docs, booked = _booked(work, card, exclude=doc.name)
    if not _equal(booked + source["qty"], _qty(card.process_loss_qty), card, "process_loss_qty"):
        frappe.throw("该报工已办理有效损耗出库，不能重复办理")
    rows = _sql("""SELECT process_loss_qty FROM `tabStock Entry`
        WHERE work_order=%s AND purpose='Manufacture' AND docstatus=1 ORDER BY name FOR UPDATE NOWAIT""", (work.name,))
    native_loss = sum((_qty(row.process_loss_qty) for row in rows), ZERO)
    if native_loss:
        frappe.throw("本工单已有原生制造损耗，请从原制造批次核对成本和补产，不能混用专用全部损耗出库")
    total_loss = sum((_qty(other.get(PREFIX + "qty")) for other in other_docs), ZERO)
    if native_loss + total_loss + source["qty"] + _qty(work.get("produced_qty")) > _qty(work.qty):
        frappe.throw("工单制造、已出账损耗和本次损耗超过原工单数量，不能重复记损耗")
    pool = _own_wip_pool(work, exclude=doc.name)
    used = defaultdict(Decimal)
    for row in doc.items:
        used[row.item_code] += _qty(row.transfer_qty)
    for item, qty in used.items():
        if qty > pool[item]:
            frappe.throw(f"材料 {item} 的损耗出库超过本工单实际转入的剩余在制库存")


def validate_loss_entry(doc, method=None):
    previous = doc.get_doc_before_save() if not doc.is_new() else None
    marked = is_loss_entry(doc)
    if previous and is_loss_entry(previous):
        fixed = LOSS_FIELDS - {PREFIX + "reason", PREFIX + "no_residual"}
        fixed |= {"stock_entry_type", "purpose", "company", "from_warehouse"}
        if any(previous.get(field) != doc.get(field) for field in fixed):
            frappe.throw("损耗出库的来源、数量和办理标记保存后不能更改")
        marked = True
    if not marked:
        return
    work = _internal_read("Work Order", doc.get(PREFIX + "work_order"))
    # Flow lock precedes source locks, matching the existing Stock Entry mixin.
    _lock_source("Jifeng Flow Order", work.get("jf_flow_order"))
    work = _lock_source("Work Order", work.name)
    card = _lock_source("Job Card", doc.get(PREFIX + "job_card"))
    cache = {("Work Order", work.name): work, ("Job Card", card.name): card}

    def reader(kind, name):
        key = kind, name
        if key not in cache:
            cache[key] = _authorized_read(kind, name)
        else:
            cache[key].check_permission("read")
            from jifeng_erp.flow_quantity import check_quantity_fields

            check_quantity_fields(kind, PARENTS[kind])
            for table, child, fields in TABLES.get(kind, ()):
                check_quantity_fields(kind, (table,))
                check_quantity_fields(child, fields, parenttype=kind)
        return cache[key]

    source = read_loss_source(doc, read=reader)
    if doc.is_new() and get_datetime(doc.get(PREFIX + "source_version")) != get_datetime(card.modified):
        frappe.throw("首次保存的损耗来源版本必须是当前准确报工版本", frappe.TimestampMismatchError)
    if doc.docstatus != 2:
        if work.get("status") in ("Stopped", "Closed"):
            frappe.throw("停止或关闭的工单不能继续办理损耗出库")
        _validate_budget(doc, source, work, card)


def before_cancel_loss_entry(doc, method=None):
    if not is_loss_entry(doc):
        if doc.get("work_order") and doc.get("purpose") in (
                "Material Transfer for Manufacture", "Manufacture", "Material Consumption for Manufacture"):
            if _loss_documents(work_order=doc.work_order):
                frappe.throw("本工单已有损耗出库，请先逆序撤销损耗出库再撤销原转料或耗料")
            if doc.purpose == "Manufacture" and _qty(doc.get("process_loss_qty")):
                work = _internal_read("Work Order", doc.work_order)
                if work.get("jf_flow_order") and not work.get("track_semi_finished_goods"):
                    work.check_permission("read")
                    if work.company != doc.company:
                        frappe.throw("原制造与工单公司不一致")
                    _cancel_flow_capacity(doc, work, work.jf_flow_order)
        return
    source = read_loss_source(doc, read=_authorized_read)
    _cancel_flow_capacity(doc, _internal_read("Work Order", source["work_order"]), source["flow_order"])


def _cancel_flow_capacity(doc, work, flow_name):
    flow = _lock_source("Jifeng Flow Order", flow_name)
    work = _lock_source("Work Order", work.name)
    _lock_native_plan_quantities(work)
    from jifeng_erp.workflow import stock_plan_quantity

    rows = _sql("""SELECT name, process_loss_qty FROM `tabStock Entry`
        WHERE work_order=%s AND purpose='Manufacture' AND docstatus=1 ORDER BY name FOR UPDATE NOWAIT""", (work.name,))
    remaining = sum((_qty(row.process_loss_qty) for row in rows if row.name != doc.name), ZERO)
    remaining += sum((_qty(row.get(PREFIX + "qty")) for row in _loss_documents(work_order=work.name, exclude=doc.name)), ZERO)
    produced = _qty(work.get("produced_qty"))
    if doc.purpose == "Manufacture":
        produced -= sum((_qty(row.transfer_qty) for row in doc.items if row.get("is_finished_item")), ZERO)
    committed = max(ZERO, produced, _qty(work.qty) - remaining)
    other_works = _sql("""SELECT qty, produced_qty, process_loss_qty, docstatus FROM `tabWork Order`
        WHERE jf_flow_order=%s AND docstatus<2 AND name!=%s ORDER BY name FOR UPDATE NOWAIT""", (flow.name, work.name))
    others = sum((max(ZERO, _qty(row.produced_qty), _qty(row.qty) - _qty(row.process_loss_qty))
                  if row.docstatus == 1 else _qty(row.qty) for row in other_works), ZERO)
    if committed + others > Decimal(str(stock_plan_quantity(flow))):
        frappe.throw("此损耗已经释放补产预留，请先撤销关联补产工单或草稿，再撤销损耗出库")


def _lock_native_plan_quantities(work):
    """Pre-lock the exact installed native plan scope without waiting.

    The installed ProductionPlanWorkOrderQuantities locks one child and then
    its submitted peer WOs. Holding those scopes now makes its later native
    loss-reversal validation reuse these locks instead of forming a wait loop.
    """
    plan = work.get("production_plan")
    if not plan:
        return
    if work.get("production_plan_item") and work.get("production_plan_sub_assembly_item"):
        frappe.throw("工单不能同时关联生产计划成品行和子装配行")
    reference = "production_plan_sub_assembly_item" if work.get("production_plan_sub_assembly_item") else "production_plan_item"
    child = "Production Plan Sub Assembly Item" if reference == "production_plan_sub_assembly_item" else "Production Plan Item"
    row = work.get(reference)
    if (not row or not _sql("SELECT name FROM `tabProduction Plan` WHERE name=%s FOR UPDATE NOWAIT", (plan,))
            or not _sql(f"SELECT name FROM `tab{child}` WHERE name=%s AND parent=%s FOR UPDATE NOWAIT", (row, plan))):
        frappe.throw("工单缺少准确生产计划来源行，请先核对原计划")
    _sql(f"SELECT name FROM `tabWork Order` WHERE production_plan=%s AND {reference}=%s "
         "AND docstatus=1 AND name!=%s ORDER BY name FOR UPDATE NOWAIT", (plan, row, work.name))


def before_cancel_source(doc, method=None):
    kwargs = {"work_order": doc.name} if doc.doctype == "Work Order" else {"job_card": doc.name}
    if _loss_documents(**kwargs, include_drafts=True):
        frappe.throw("来源已有制造损耗出库或草稿，请先撤销或删除损耗凭证")


def validate_loss_source(doc, method=None):
    """Freeze reporting/BOM quantities while the exact loss voucher is effective.

    Native progress fields are deliberately absent from source_signature, so
    normal submitted Work Order / Job Card callbacks retain their usual writes.
    """
    if doc.is_new():
        return
    kwargs = {"work_order": doc.name} if doc.doctype == "Work Order" else {"job_card": doc.name}
    for entry in _loss_documents(**kwargs, include_drafts=True):
        card = doc if doc.doctype == "Job Card" else _internal_read("Job Card", entry.get(PREFIX + "job_card"))
        work = doc if doc.doctype == "Work Order" else _internal_read("Work Order", entry.get(PREFIX + "work_order"))
        if source_signature(card, work) != entry.get(PREFIX + "source_signature"):
            frappe.throw("来源已有有效损耗出库或草稿，不能修改确认损耗、工序或材料来源，请先逆序撤销")


def update_loss_progress(doc, method=None):
    if not is_loss_entry(doc):
        return
    source = read_loss_source(doc, read=_internal_read)
    _lock_source("Jifeng Flow Order", source["flow_order"])
    work = _lock_source("Work Order", source["work_order"])
    # Native methods retain plan/SO updates, stock reservations, and status.
    work.update_work_order_qty()
    work.update_consumed_qty_for_required_items()
    work.update_status()
    work.update_planned_qty()
    card = _lock_source("Job Card", source["job_card"])
    card.set_status(update_status=True)


def prepare_loss_stock_reservations(doc):
    """Release only this WO's proved consumption before native negative-stock gate.

    A failure in native SLE/GL rolls this temporary consumed/SRE update back in
    the same native submit transaction. Cancellation restores the remaining
    effective quantity; no flags or client-supplied override can skip proof.
    """
    source = read_loss_source(doc, read=_internal_read)
    _lock_source("Jifeng Flow Order", source["flow_order"])
    work = _lock_source("Work Order", source["work_order"])
    if not work.get("reserve_stock"):
        return
    if doc.docstatus == 1:
        _validate_budget(doc, source, work, _internal_read("Job Card", source["job_card"]))
    elif doc.docstatus != 2:
        frappe.throw("库存损耗预留只能在原生提交或撤销事务内办理")
    from erpnext.manufacturing.doctype.work_order.work_order import get_consumed_qty

    additional = defaultdict(Decimal)
    for entry in _loss_documents(work_order=work.name, exclude=doc.name):
        for row in entry.items:
            additional[row.item_code] += _qty(row.transfer_qty)
    if doc.docstatus == 1:
        for row in doc.items:
            additional[row.item_code] += _qty(row.transfer_qty)
    warehouse = work.wip_warehouse
    if work.get("skip_transfer") and not work.get("from_wip_warehouse"):
        warehouse = None
    for item in work.required_items:
        consumed = (Decimal(str(get_consumed_qty(work.name, item.item_code) or 0))
                    + _qty(item.get("returned_qty")) + additional[item.item_code])
        item.db_set("consumed_qty", float(consumed), update_modified=False)
        work.update_consumed_qty_in_stock_reservation(item, float(consumed), warehouse or item.source_warehouse)


def _request(value):
    try:
        result = str(UUID(str(value)))
    except (ValueError, TypeError):
        frappe.throw("请使用准确的损耗办理标记重新核对")
    if result != value:
        frappe.throw("请使用准确的损耗办理标记重新核对")
    return result


def _creation_context(job_card):
    # Initial pointer lookup does not lock Card before the existing Flow lock.
    pointer = frappe.db.get_value("Job Card", job_card, "work_order")
    flow_name = frappe.db.get_value("Work Order", pointer, "jf_flow_order") if pointer else None
    flow = _authorized_read("Jifeng Flow Order", flow_name)
    flow.check_permission("write")
    from jifeng_erp.workflow import validate_flow_source

    validate_flow_source(flow)
    work = _authorized_read("Work Order", pointer)
    card = _authorized_read("Job Card", job_card)
    loss = _source_context(card, work, flow, _authorized_read)
    if work.get("status") in ("Stopped", "Closed"):
        frappe.throw("停止或关闭的工单不能办理损耗出库")
    return flow, work, card, loss


def _request_records(request_id, *, lock=False):
    query = f"SELECT name, docstatus FROM `tabStock Entry` WHERE {PREFIX}request=%s ORDER BY name"
    rows = _sql(query + (" FOR UPDATE NOWAIT" if lock else ""), (request_id,))
    if len(rows) > 1:
        frappe.throw("同一损耗办理标记对应多张单据，请先核对原单")
    return rows


def _history_card(job_card):
    from jifeng_erp.flow_quantity import check_quantity_fields

    card = _lock_source("Job Card", job_card)
    card.check_permission("read")
    check_quantity_fields("Job Card", {"name"})
    if card.name != job_card:
        frappe.throw("原报工单指向不一致")
    return card


def _request_history(job_card, request_id, expected_name):
    """Read the exact immutable trace, without using history as a new source.

    A cancelled record remains readable after reporting/BOM/status changes.
    The caller must still be able to read the current original Card identity
    and every historical field used to identify this specific cancelled SE.
    """
    rows = _request_records(request_id, lock=True)
    if len(rows) != 1 or rows[0].name != expected_name or rows[0].docstatus != 2:
        frappe.throw("原办理记录状态已变化，请重新回读", frappe.TimestampMismatchError)
    from jifeng_erp.flow_quantity import check_quantity_fields

    doc = _lock_source("Stock Entry", rows[0].name)
    doc.check_permission("read")
    fields = {"name", "docstatus", "purpose", "stock_entry_type", PREFIX + "request", PREFIX + "job_card",
              PREFIX + "work_order"}
    check_quantity_fields("Stock Entry", fields)
    card = _history_card(job_card)
    if (doc.docstatus != 2 or card.name != job_card or doc.get(PREFIX + "request") != request_id
            or doc.get(PREFIX + "job_card") != card.name or not doc.get(PREFIX + "work_order")
            or doc.stock_entry_type != LOSS_TYPE or doc.purpose != "Material Issue"):
        frappe.throw("办理标记与原报工单或历史损耗出库指向不一致")
    return doc


def _inspect(job_card, request_id=None, *, only_previous=False):
    if only_previous and not request_id:
        frappe.throw("回读原办理结果必须提供准确的原办理标记")
    if request_id:
        request_id = _request(request_id)
        # Advisory only: never acquire an active SE before the normal Flow/
        # WO/Card source locks. Cancellation is re-proved under its own lock.
        rows = _request_records(request_id)
        if rows and rows[0].docstatus == 2:
            history = _request_history(job_card, request_id, rows[0].name)
            return {"stock_entry": history.name, "docstatus": 2, "existing": True}, None
        if not rows and only_previous:
            _history_card(job_card)
            # A current exact-UUID locking read catches a pending insert or a
            # stale advisory snapshot. Never clear an unknown write on a plain
            # repeatable-read snapshot. A newly appeared row requires a fresh
            # GET; do not acquire Flow/source locks after an active SE lock.
            if _request_records(request_id, lock=True):
                frappe.throw("原办理记录刚发生变化，请重新回读", frappe.TimestampMismatchError)
            return {"stock_entry": None, "docstatus": None, "existing": False}, None
    flow, work, card, loss = _creation_context(job_card)
    if request_id:
        rows = _request_records(request_id, lock=True)
        if rows:
            doc = _authorized_read("Stock Entry", rows[0].name)
            source = read_loss_source(doc, read=_authorized_read)
            if source["job_card"] != card.name or doc.docstatus not in (0, 1):
                frappe.throw("办理标记与当前有效损耗来源不一致，请重新回读")
            return {"stock_entry": doc.name, "docstatus": doc.docstatus, "existing": True}, (flow, work, card, loss)
    docs = _loss_documents(job_card=card.name, include_drafts=True)
    if len(docs) > 1:
        frappe.throw("同一全损耗报工已有多张有效损耗单，请核对原单")
    if docs:
        docs[0].check_permission("read")
        return {"stock_entry": docs[0].name, "docstatus": docs[0].docstatus, "existing": True}, (flow, work, card, loss)
    required, units = _required(work)
    pool = _own_wip_pool(work)
    items = []
    for item, qty in required.items():
        material = _authorized_read("Item", item)
        if (material.stock_uom != units[item] or not material.get("is_stock_item")
                or material.get("has_serial_no") or material.get("has_batch_no")):
            frappe.throw("损耗材料单位不一致，或带批号序号需逐批核对，当前入口暂不能办理")
        amount = qty * loss / _qty(work.qty)
        if amount <= 0 or amount > pool[item]:
            frappe.throw(f"材料 {item} 尚无足额的本工单在制库存，请先核对原转料")
        items.append({"item_code": item, "qty": float(amount), "uom": units[item], "stock_uom": units[item],
                      "conversion_factor": 1, "transfer_qty": float(amount), "s_warehouse": work.wip_warehouse,
                      "jf_flow_order": flow.name})
    return {"job_card": card.name, "work_order": work.name, "flow_order": flow.name,
            "source_version": str(card.modified), "source_signature": source_signature(card, work),
            "stock_uom": work.stock_uom, "loss_qty": float(loss), "warehouse": work.wip_warehouse,
            "items": items, "existing": False}, (flow, work, card, loss)


@frappe.whitelist(methods=["GET"])
def inspect_loss_issue(job_card, request_id=None, only_previous=0):
    if str(only_previous) not in ("0", "1"):
        frappe.throw("损耗办理回读方式不正确")
    return _inspect(job_card, request_id, only_previous=str(only_previous) == "1")[0]


@frappe.whitelist(methods=["POST"])
def create_loss_issue(job_card, expected_version, no_residual, reason, warehouse, request_id):
    request_id = _request(request_id)
    result, context = _inspect(job_card, request_id)
    if result["existing"]:
        if result["docstatus"] == 2:
            frappe.throw("此办理标记的损耗出库已取消，不能自动重新办理，请重新核对报工后另行操作")
        return result
    flow, work, card, loss = context
    if str(card.modified) != str(expected_version):
        frappe.throw("报工来源版本已变化，请刷新后重新核对损耗", frappe.TimestampMismatchError)
    if str(no_residual) != "1" or not isinstance(reason, str) or not reason.strip():
        frappe.throw("必须明确确认无可回收实物及残值，并填写实际损耗原因")
    if warehouse != work.wip_warehouse:
        frappe.throw("请使用原工单准确在制仓库")
    frappe.has_permission("Stock Entry", "create", throw=True)
    doc = frappe.get_doc({"doctype": "Stock Entry", "company": flow.company,
        "purpose": "Material Issue", "stock_entry_type": LOSS_TYPE, "from_warehouse": warehouse,
        PREFIX + "work_order": work.name, PREFIX + "job_card": card.name, PREFIX + "qty": float(loss),
        PREFIX + "reason": reason.strip(), PREFIX + "no_residual": 1,
        PREFIX + "source_signature": result["source_signature"], PREFIX + "source_version": card.modified,
        PREFIX + "request": request_id, "items": result["items"]})
    doc.insert()
    # Only the native Stock Entry's explicit submit moves inventory and money.
    return {"stock_entry": doc.name, "docstatus": doc.docstatus, "existing": False}
