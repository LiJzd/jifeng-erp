"""Submitted stock voucher business rows, checked against their permitted ledger entries."""

from decimal import Decimal

import frappe

from jifeng_erp.inventory_scope import (
    column, decimal_number, readable_documents, report_scope, selected_warehouses,
)
from jifeng_erp.sample_source_queries import check_protected_reference


PURPOSES = {"receipt": "Material Receipt", "issue": "Material Issue", "transfer": "Material Transfer"}
LABELS = {"receipt": "库存收货", "issue": "物料出库", "transfer": "仓库调拨"}


def _columns():
    return [
        column("posting_date", "记账日期", "Date"), column("posting_time", "记账时间", "Time", width=100),
        column("stock_entry", "库存凭证", "Link", "Stock Entry", 175), column("entry_type", "业务类型", width=100),
        column("item_code", "商品/原料", "Link", "Item", 155), column("item_name", "名称", width=155),
        column("flow_order", "流程单", "Link", "Jifeng Flow Order", 175),
        column("work_order", "生产工单", "Link", "Work Order", 175),
        column("stock_state", "库存核对状态", width=180), column("stock_uom", "库存单位", "Link", "UOM", 100),
        column("movement_qty", "业务库存数量", "Float"), column("source_qty", "出仓库存变动", "Float"),
        column("target_qty", "入仓库存变动", "Float"), column("net_qty", "公司库存净变动", "Float", width=150),
        column("s_warehouse", "发出仓库", "Link", "Warehouse", 170),
        column("t_warehouse", "接收仓库", "Link", "Warehouse", 170),
        column("source_value", "出仓库存价值变动", "Currency", "currency", 165),
        column("target_value", "入仓库存价值变动", "Currency", "currency", 165),
        column("net_value", "公司库存价值净变动", "Currency", "currency", 180),
        column("currency", "公司本币", "Link", "Currency", 100),
        column("transfer_stage", "调拨阶段", width=140), column("add_to_transit", "调入在途", "Check", width=95),
        column("outgoing_stock_entry", "来源调拨凭证", "Link", "Stock Entry", 175),
        column("material_return_against", "原领料凭证", "Link", "Stock Entry", 175),
        column("material_return_against_detail", "原领料明细", width=165),
        column("source_read_state", "来源核对", width=160), column("source_detail", "来源调拨明细", width=165),
        column("document_qty", "凭证数量", "Float"), column("document_uom", "凭证单位", "Link", "UOM", 100),
        column("stock_entry_detail", "本行明细", width=165),
    ]


def _selected_rows(entries, filters, warehouses):
    result = []
    for doc in entries.values():
        if doc.company != filters.company or doc.docstatus != 1 or doc.purpose != PURPOSES[filters.mode]:
            frappe.throw("库存台账来源的公司、审核状态或业务类型不一致，请核对")
        if filters.mode == "transfer" and doc.get("is_return"):
            continue
        for row in doc.get("items"):
            if row.parent != doc.name or row.parenttype != "Stock Entry":
                frappe.throw("库存明细与来源凭证不一致，请核对")
            if filters.item_code and row.item_code != filters.item_code:
                continue
            if filters.flow_order and row.get("jf_flow_order") != filters.flow_order:
                continue
            if not ({row.s_warehouse, row.t_warehouse} & set(warehouses)):
                continue
            result.append((doc, row))
    return result


def _same_quantity(actual, expected, row):
    precision = row.precision("transfer_qty") if callable(getattr(row, "precision", None)) else 6
    step = Decimal(1).scaleb(-int(precision if precision is not None else 6))
    return actual.quantize(step) == expected.quantize(step)


def _ledger_quantities(doc, row, item, warehouses, ledger):
    source, target = row.s_warehouse, row.t_warehouse
    if (doc.purpose == "Material Receipt" and (source or not target)
            or doc.purpose == "Material Issue" and (not source or target)
            or doc.purpose == "Material Transfer" and (not source or not target)):
        frappe.throw("库存凭证明细的发出和接收仓库与业务类型不一致，请核对")
    if row.stock_uom != item.stock_uom:
        frappe.throw("库存凭证的库存单位与商品档案不一致，请核对")
    for name in (source, target):
        if not name:
            continue
        warehouse = warehouses.get(name)
        if not warehouse or warehouse.company != doc.company or warehouse.is_group:
            frappe.throw("库存凭证来源仓库的公司或明细仓库状态不一致，请核对")
    if not item.is_stock_item:
        if ledger:
            frappe.throw("非库存条目存在库存台账，请先核对来源数据")
        return {field: None for field in ("movement_qty", "source_qty", "target_qty", "net_qty",
                                         "source_value", "target_value", "net_value")}
    quantity = decimal_number(row.transfer_qty)
    if quantity <= 0 or not ledger:
        frappe.throw("已审核库存条目缺少有效库存数量或正式库存台账，请核对")
    source_qty, target_qty, source_value, target_value = [Decimal(0)] * 4
    for sle in ledger:
        if (sle.voucher_type != "Stock Entry" or sle.voucher_no != doc.name
                or sle.voucher_detail_no != row.name or sle.company != doc.company
                or sle.item_code != row.item_code or sle.stock_uom != row.stock_uom
                or sle.docstatus != 1 or sle.is_cancelled
                or str(sle.posting_date) != str(doc.posting_date)):
            frappe.throw("正式库存台账与凭证来源、公司、商品、单位或日期不一致，请核对")
        qty, value = decimal_number(sle.actual_qty), decimal_number(sle.stock_value_difference)
        # Same warehouse transfers between inventory dimensions still have two
        # opposite movements. Never collapse both sides by warehouse name.
        if qty < 0 and source and sle.warehouse == source:
            source_qty += qty
            source_value += value
        elif qty > 0 and target and sle.warehouse == target:
            target_qty += qty
            target_value += value
        else:
            frappe.throw("库存台账的数量方向或仓库与凭证明细不一致，请核对")
    if not (_same_quantity(source_qty, -quantity if source else Decimal(0), row)
            and _same_quantity(target_qty, quantity if target else Decimal(0), row)):
        frappe.throw("库存台账数量与已审核凭证明细不一致，请核对")
    return {"movement_qty": quantity, "source_qty": source_qty, "target_qty": target_qty,
            "net_qty": source_qty + target_qty, "source_value": source_value,
            "target_value": target_value, "net_value": source_value + target_value}


def _outgoing_source(doc, row, origins):
    name = doc.get("outgoing_stock_entry")
    if not name:
        if row.get("against_stock_entry") or row.get("ste_detail"):
            frappe.throw("调拨明细有来源关联但凭证缺少来源调拨单，请核对")
        return "", "", "无来源调拨"
    origin = origins.get(name)
    if not origin:
        return "", "", "来源不可读"
    source = next((detail for detail in origin.get("items") if detail.name == row.get("ste_detail")), None)
    if (doc.purpose != "Material Transfer" or origin.purpose != "Material Transfer"
            or origin.docstatus != 1 or origin.company != doc.company or origin.get("is_return")
            or row.get("against_stock_entry") != origin.name or not source
            or source.parent != origin.name or source.parenttype != "Stock Entry"
            or source.item_code != row.item_code
            or source.stock_uom != row.stock_uom or source.t_warehouse != row.s_warehouse):
        frappe.throw("接收调拨的来源单据、商品、库存单位或在途仓库不一致，请核对")
    if decimal_number(row.transfer_qty) > decimal_number(source.transfer_qty):
        frappe.throw("本行接收调拨数量超过原来源明细数量，请核对")
    return origin.name, source.name, "已核对"


def _material_return_source(doc, row, origins):
    name = doc.get("jf_material_return_against")
    if not name:
        if row.get("jf_material_return_against_detail"):
            frappe.throw("退库明细有来源关联但凭证缺少原领料单，请核对")
        return "", "", ""
    origin = origins.get(name)
    if not origin:
        return "", "", "原领料不可读"
    source = next((detail for detail in origin.get("items")
                   if detail.name == row.get("jf_material_return_against_detail")), None)
    if (doc.purpose != "Material Receipt" or origin.purpose != "Material Issue"
            or origin.docstatus != 1 or origin.company != doc.company or not source
            or source.parent != origin.name or source.parenttype != "Stock Entry"
            or source.item_code != row.item_code or source.stock_uom != row.stock_uom
            or source.s_warehouse != row.t_warehouse or row.s_warehouse
            or source.get("jf_flow_order") != row.get("jf_flow_order")
            or decimal_number(row.transfer_qty) > decimal_number(source.transfer_qty)):
        frappe.throw("领料退库的原单、明细、物料、单位或仓库不一致，请核对")
    return origin.name, source.name, "原领料已核对"


def stock_entry_movements(selected, ledgers, items, warehouses, origins, currency):
    """Normalize permitted movements and check protected output references; never post."""
    grouped = {}
    for sle in ledgers.values():
        grouped.setdefault(sle.voucher_detail_no, []).append(sle)
    result = []
    for doc, row in selected:
        item = items.get(row.item_code)
        if not item:
            frappe.throw("无法读取库存条目对应的商品档案，请核对", frappe.PermissionError)
        quantities = _ledger_quantities(doc, row, item, warehouses, grouped.get(row.name, []))
        outgoing, source_detail, source_read_state = _outgoing_source(doc, row, origins)
        return_against, return_detail, return_state = _material_return_source(doc, row, origins)
        mode = next(key for key, purpose in PURPOSES.items() if purpose == doc.purpose)
        stage = "接收来源调拨" if doc.get("outgoing_stock_entry") else "调入在途" if doc.get("add_to_transit") else "直接调拨"
        check_protected_reference("Jifeng Flow Order", row.get("jf_flow_order"), company=doc.company)
        check_protected_reference("Work Order", doc.get("work_order"), company=doc.company)
        result.append({
            "company": doc.company, "posting_date": doc.posting_date, "posting_time": doc.posting_time,
            "stock_entry": doc.name, "stock_entry_detail": row.name, "idx": row.idx,
            "entry_type": "领料退库" if doc.get("jf_material_return_against") else LABELS[mode],
            "item_code": row.item_code, "item_name": row.item_name,
            "flow_order": row.get("jf_flow_order"), "work_order": doc.get("work_order"),
            "stock_state": "库存台账已核对" if item.is_stock_item else "非库存条目（无库存变动）",
            "stock_uom": row.stock_uom, **quantities, "s_warehouse": row.s_warehouse,
            "t_warehouse": row.t_warehouse, "currency": currency,
            "transfer_stage": stage if mode == "transfer" else "",
            "add_to_transit": int(bool(doc.get("add_to_transit"))),
            "outgoing_stock_entry": outgoing, "source_detail": source_detail,
            "material_return_against": return_against, "material_return_against_detail": return_detail,
            "source_read_state": return_state or (source_read_state if mode == "transfer" else ""),
            "document_qty": decimal_number(row.qty), "document_uom": row.uom,
        })
    return sorted(result, key=lambda row: (str(row["posting_date"]), str(row["posting_time"] or ""),
                                          row["stock_entry"], row["idx"]))


def stock_entry_ledger(filters=None):
    filters, start, end = report_scope(filters, ("Stock Entry", "Stock Ledger Entry", "Item", "Warehouse"))
    filters.mode = filters.get("mode") or "issue"
    if filters.mode not in PURPOSES:
        frappe.throw("请选择库存收货、物料出库或仓库调拨")
    from erpnext.stock.utils import check_pending_reposting

    if check_pending_reposting(end, filters.company, throw_error=False):
        frappe.throw("所选日期内仍有库存估价重算任务，请在重算完成后查询")
    query = {"company": filters.company, "docstatus": 1, "purpose": PURPOSES[filters.mode],
             "posting_date": ["between", [str(start), str(end)]]}
    if filters.mode == "transfer":
        query["is_return"] = 0
    entries = readable_documents("Stock Entry", query)
    selected = _selected_rows(entries, filters, selected_warehouses(filters))
    if not selected:
        return _columns(), []
    names = {row.item_code for _, row in selected}
    items = readable_documents("Item", {"name": ["in", list(names)]}, complete=True)
    names = {name for _, row in selected for name in (row.s_warehouse, row.t_warehouse) if name}
    warehouses = readable_documents("Warehouse", {"name": ["in", list(names)]}, complete=True)
    names = {doc.name for doc, _ in selected}
    ledgers = readable_documents("Stock Ledger Entry", {
        "company": filters.company, "docstatus": 1, "is_cancelled": 0,
        "voucher_type": "Stock Entry", "voucher_no": ["in", list(names)],
        "voucher_detail_no": ["in", [row.name for _, row in selected]],
    }, complete=True)
    names = {doc.outgoing_stock_entry for doc, _ in selected if doc.get("outgoing_stock_entry")}
    names.update(doc.get("jf_material_return_against") for doc, _ in selected if doc.get("jf_material_return_against"))
    origins = readable_documents("Stock Entry", {"name": ["in", list(names)]}) if names else {}
    currency = frappe.get_cached_value("Company", filters.company, "default_currency")
    return _columns(), stock_entry_movements(selected, ledgers, items, warehouses, origins, currency)
