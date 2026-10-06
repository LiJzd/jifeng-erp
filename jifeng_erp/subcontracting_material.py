"""Permission checked, stock-unit raw-material movements for subcontracting."""

from decimal import Decimal, InvalidOperation

import frappe

from jifeng_erp.subcontracting import _column, _order_items, _orders, _readable_documents, _scope
from jifeng_erp.ledger_source_guard import LedgerSourceGuard
from jifeng_erp.sample_source_queries import check_protected_reference


def _transfer_quantity(row):
    try:
        qty = Decimal(str(row.get("transfer_qty") or 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("委外原料库存数量不是有效数字，请核对发退料明细")
    if not qty.is_finite() or qty <= 0:
        frappe.throw("委外原料库存数量必须是大于零的有限数字，请核对发退料明细")
    return qty


def material_movements(orders, selected_items, entries, rm_item_code=None, supplier=None, *, guard=None):
    """Resolve each native material row before applying finished-good filters."""
    supplied = {row.name: row for order in orders.values() for row in order.get("supplied_items")}
    all_items = {row.name: row for order in orders.values() for row in order.get("items")}
    result = []
    for entry in entries.values():
        order = orders.get(entry.subcontracting_order)
        if not order:
            frappe.throw("委外发退料缺少可核对的来源订单，请先核对")
        if supplier and order.supplier != supplier:
            continue
        is_return = bool(entry.is_return)
        if entry.docstatus != 1 or not (
            entry.purpose == "Send to Subcontractor" and not is_return
            or entry.purpose == "Material Transfer" and is_return
        ):
            frappe.throw("委外原料台账来源不是已提交的发料或退料凭证，请核对")
        if entry.company != order.company or entry.get("supplier") and entry.supplier != order.supplier:
            frappe.throw("委外原料凭证的公司或供应商与来源订单不一致，请核对")
        for row in entry.get("items"):
            source = supplied.get(row.get("sco_rm_detail"))
            if not source or source.parent != order.name:
                frappe.throw("委外发退料缺少可核对的来源订单原料明细，请先核对")
            item = all_items.get(source.get("reference_name"))
            if not item or item.parent != order.name or source.main_item_code != item.item_code:
                frappe.throw("委外发退料的原料来源无法对应订单商品明细，请先核对")
            if (source.rm_item_code != row.item_code
                    or row.get("subcontracted_item") and row.subcontracted_item != item.item_code
                    or not row.stock_uom or source.stock_uom != row.stock_uom):
                frappe.throw("委外发退料的原料、商品或库存单位与来源明细不一致，请核对")
            if row.get("jf_flow_order") != item.get("jf_flow_order"):
                frappe.throw("委外发退料流程单与来源订单商品明细不一致，请核对")
            # Exact source rows can prove a movement belongs to another finished
            # good in a multi-item order; missing links must never be guessed.
            if item.name not in selected_items or rm_item_code and row.item_code != rm_item_code:
                continue
            qty = _transfer_quantity(row)
            selected = selected_items[item.name]
            if guard and selected.get("jf_flow_order"):
                guard.document(frappe.get_doc("Jifeng Flow Order", selected["jf_flow_order"]))
            check_protected_reference("Jifeng Flow Order", selected.get("jf_flow_order"), company=entry.company)
            result.append({
                "company": entry.company, "posting_date": entry.posting_date,
                "posting_time": entry.posting_time, "stock_entry": entry.name,
                "stock_entry_detail": row.name, "idx": row.idx,
                "entry_type": "原料退回" if is_return else "原料发出",
                "subcontracting_order": order.name, "order_item": item.name,
                "supplier": order.supplier, "supplier_name": order.supplier_name,
                "flow_order": selected.get("jf_flow_order"),
                "item_code": item.item_code, "item_name": item.item_name,
                "rm_item_code": row.item_code, "rm_item_name": row.item_name,
                "stock_uom": row.stock_uom,
                "issued_qty": Decimal(0) if is_return else qty,
                "returned_qty": qty if is_return else Decimal(0),
                "net_qty": -qty if is_return else qty,
                "s_warehouse": row.s_warehouse, "t_warehouse": row.t_warehouse,
                "supplied_item": source.name,
            })
    return sorted(result, key=lambda row: (
        str(row["posting_date"]), str(row["posting_time"] or ""), row["stock_entry"], row["idx"],
    ))


def subcontracting_material_ledger(filters=None):
    guard = LedgerSourceGuard("subcontracting_material")
    filters, start, end = _scope(filters, ("Stock Entry", "Subcontracting Order"), guard=guard)
    if filters.rm_item_code:
        if not guard.query("get_list", "Item", filters={"name": filters.rm_item_code},
                               pluck="name", limit_page_length=1):
            frappe.throw("没有读取所选委外原料的权限", frappe.PermissionError)
        item = guard.document(frappe.get_doc("Item", filters.rm_item_code))
        item.check_permission("read")
    query = {"company": filters.company, "docstatus": 1,
             "posting_date": ["between", [str(start), str(end)]]}
    if filters.subcontracting_order:
        query["subcontracting_order"] = filters.subcontracting_order
    else:
        query["subcontracting_order"] = ["is", "set"]
    entries = _readable_documents("Stock Entry", {
        **query, "purpose": "Send to Subcontractor", "is_return": 0,
    }, guard=guard)
    entries.update(_readable_documents("Stock Entry", {
        **query, "purpose": "Material Transfer", "is_return": 1,
    }, guard=guard))
    names = {entry.subcontracting_order for entry in entries.values()}
    # Check every visible movement's source first. Supplier filtering belongs to
    # the resolved source Order, not Stock Entry's optional supplier field.
    source_filters = frappe._dict({**filters, "supplier": None})
    orders = _orders(source_filters, names=names, complete=True, guard=guard)
    selected_items = _order_items(orders, filters)
    rows = material_movements(orders, selected_items, entries, filters.rm_item_code, filters.supplier, guard=guard)
    columns = [
        _column("posting_date", "记账日期", "Date"),
        _column("posting_time", "记账时间", "Time", width=100),
        _column("stock_entry", "发退料凭证", "Link", "Stock Entry", 185),
        _column("entry_type", "业务类型", width=110),
        _column("subcontracting_order", "委外订单", "Link", "Subcontracting Order", 175),
        _column("supplier", "委外供应商", "Link", "Supplier", 160),
        _column("supplier_name", "供应商名称", width=160),
        _column("flow_order", "流程单", "Link", "Jifeng Flow Order", 170),
        _column("item_code", "来源商品", "Link", "Item", 150),
        _column("item_name", "商品名称", width=150),
        _column("rm_item_code", "发退原料", "Link", "Item", 150),
        _column("rm_item_name", "原料名称", width=150),
        _column("stock_uom", "原料库存单位", "Link", "UOM", 100),
        _column("issued_qty", "原料发出数量", "Float"),
        _column("returned_qty", "原料退回数量", "Float"),
        _column("net_qty", "原料净发出变动", "Float", width=145),
        _column("s_warehouse", "发出仓库", "Link", "Warehouse", 170),
        _column("t_warehouse", "接收仓库", "Link", "Warehouse", 170),
        _column("order_item", "来源商品明细", width=165),
        _column("supplied_item", "来源原料明细", width=165),
        _column("stock_entry_detail", "本行明细", width=165),
    ]
    guard.finish()
    return columns, rows
