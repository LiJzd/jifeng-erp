"""Read submitted subcontracting movements with their exact source rows and permissions."""

from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import cint, date_diff, getdate, today


STATUS_LABELS = {"Draft": "草稿", "Open": "待收回", "Partially Received": "部分收回",
                 "Completed": "已完成", "Closed": "已关闭", "Cancelled": "已取消",
                 "Material Transferred": "已发料", "Partial Material Transferred": "部分发料"}


def _column(field, label, kind="Data", options=None, width=130):
    return {"fieldname": field, "label": label, "fieldtype": kind, "options": options, "width": width}


def _scope(filters, doctypes):
    filters = frappe._dict(filters or {})
    for doctype in doctypes:
        if not frappe.has_permission(doctype, "read"):
            frappe.throw("没有委外单据读取权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    if not frappe.get_list("Company", filters={"name": filters.company}, pluck="name", limit_page_length=1):
        frappe.throw("没有查看该公司的权限", frappe.PermissionError)
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")
    for field, doctype in (("supplier", "Supplier"), ("item_code", "Item"),
                           ("flow_order", "Jifeng Flow Order"), ("subcontracting_order", "Subcontracting Order")):
        if not filters.get(field):
            continue
        query = {"name": filters[field]}
        if field in ("flow_order", "subcontracting_order"):
            query["company"] = filters.company
        if not frappe.get_list(doctype, filters=query, pluck="name", limit_page_length=1):
            frappe.throw("没有读取所选委外查询条件的权限", frappe.PermissionError)
        frappe.get_doc(doctype, filters[field]).check_permission("read")
    return filters, start, end


def _readable_documents(doctype, query, complete=False):
    names = frappe.get_list(doctype, filters=query, pluck="name", distinct=True, limit_page_length=0)
    result = {}
    for name in names:
        doc = frappe.get_doc(doctype, name)
        if doc.has_permission("read"):
            result[name] = doc
    if complete:
        # This comparison is bounded by already readable source parents. Never return
        # hidden document names or a misleading balance with hidden movements omitted.
        expected = set(frappe.get_all(doctype, filters=query, pluck="name", distinct=True, limit_page_length=0))
        if expected != set(result):
            frappe.throw("无法完整读取相关收回、退货或来源单据，请核对读取权限", frappe.PermissionError)
    return result


def _orders(filters, start=None, end=None, names=None, complete=False):
    query = {"company": filters.company, "docstatus": 1}
    if names is not None:
        if not names:
            return {}
        query["name"] = ["in", list(names)]
    elif filters.subcontracting_order:
        query["name"] = filters.subcontracting_order
    if start is not None:
        query["transaction_date"] = ["between", [str(start), str(end)]]
    if filters.supplier:
        query["supplier"] = filters.supplier
    result = _readable_documents("Subcontracting Order", query, complete=complete)
    if complete and names is not None and set(names) != set(result):
        frappe.throw("无法完整读取已提交委外订单来源，请核对来源或读取权限", frappe.PermissionError)
    return result


def _order_items(orders, filters):
    items = {}
    for order in orders.values():
        for item in order.items:
            if filters.item_code and item.item_code != filters.item_code:
                continue
            if filters.flow_order and item.get("jf_flow_order") != filters.flow_order:
                continue
            items[item.name] = {**item.as_dict(), "subcontracting_order": order.name,
                                "company": order.company, "supplier": order.supplier,
                                "supplier_name": order.supplier_name, "transaction_date": order.transaction_date,
                                "order_status": order.status}
    return items


def _receipt_closure(company, order_items):
    if not order_items:
        return {}
    query = [["Subcontracting Receipt", "company", "=", company],
             ["Subcontracting Receipt", "docstatus", "=", 1],
             ["Subcontracting Receipt Item", "subcontracting_order_item", "in", list(order_items)]]
    receipts = _readable_documents("Subcontracting Receipt", query, complete=True)
    order_names = list({row["subcontracting_order"] for row in order_items.values()})
    receipts.update(_readable_documents("Subcontracting Receipt", [
        ["Subcontracting Receipt", "company", "=", company],
        ["Subcontracting Receipt", "docstatus", "=", 1],
        ["Subcontracting Receipt Item", "subcontracting_order", "in", order_names],
    ], complete=True))
    originals = [name for name, doc in receipts.items() if not doc.is_return]
    if originals:
        # A return can have a damaged/missing order link. Include all returns against
        # these source receipts so that source integrity is checked rather than skipped.
        returns = _readable_documents("Subcontracting Receipt", {
            "company": company, "docstatus": 1, "is_return": 1,
            "return_against": ["in", originals],
        }, complete=True)
        receipts.update(returns)
    return receipts


def _quantity(value):
    try:
        number = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("委外数量或金额不是有效数字，请核对来源单据")
    if not number.is_finite():
        frappe.throw("委外数量或金额不是有限数字，请核对来源单据")
    return number


def _factor(row):
    factor = _quantity(row.get("conversion_factor") if row.get("conversion_factor") is not None else 1)
    if factor <= 0:
        frappe.throw("委外数量的库存单位换算系数必须大于零，请核对")
    return factor


def _return_movement(row, source):
    factor = _factor(row)
    qty, rejected = _quantity(row.get("qty")) * factor, _quantity(row.get("rejected_qty")) * factor
    if qty > 0 or rejected > 0 or _quantity(row.get("process_loss_qty")) != 0:
        frappe.throw("委外退货只能退回实物且数量必须为负数，不能冲回原收货损耗，请核对")
    accepted_movement, rejected_movement = Decimal(0), rejected
    if qty:
        accepted_pool = row.get("warehouse") == source.get("warehouse") and _quantity(source.get("qty")) > 0
        rejected_pool = row.get("warehouse") == source.get("rejected_warehouse") and _quantity(source.get("rejected_qty")) > 0
        if accepted_pool == rejected_pool:
            frappe.throw("委外退货仓库无法唯一对应原接收或拒收数量，请核对来源仓库")
        if accepted_pool:
            accepted_movement += qty
        else:
            rejected_movement += qty
    if rejected and row.get("rejected_warehouse") != source.get("rejected_warehouse"):
        frappe.throw("委外拒收退货仓库与原收货明细不一致，请核对")
    return accepted_movement, rejected_movement


def receipt_movements(order_items, receipts, all_order_items=None):
    """Normalize signed movements; native cached returned_qty can include loss."""
    all_order_items = all_order_items or order_items
    order_names = {item["subcontracting_order"] for item in all_order_items.values()}
    originals, movements = {}, []
    for doc in receipts.values():
        if doc.is_return:
            continue
        for row in doc.items:
            if row.get("secondary_item_type"):
                continue
            source = all_order_items.get(row.subcontracting_order_item)
            if not source:
                if row.subcontracting_order in order_names:
                    frappe.throw("委外收回缺少可核对的来源订单明细，请先核对")
                continue
            if (row.subcontracting_order != source["subcontracting_order"]
                    or row.item_code != source["item_code"] or row.stock_uom != source["stock_uom"]
                    or doc.company != source["company"] or doc.supplier != source["supplier"]):
                frappe.throw("委外收回的订单明细、公司、供应商、商品或单位不一致，请核对")
            if row.get("jf_flow_order") != source.get("jf_flow_order"):
                frappe.throw("委外收回流程单与来源订单明细不一致，请核对")
            accepted, rejected, loss = (_quantity(row.get(field)) * _factor(row) for field in ("qty", "rejected_qty", "process_loss_qty"))
            if min(accepted, rejected, loss) < 0:
                frappe.throw("原委外收回数量不能为负数，请核对")
            originals[row.name] = {**row.as_dict(), "receipt": doc.name,
                                    "company": doc.company, "supplier": doc.supplier}
            if row.subcontracting_order_item in order_items:
                movements.append(_movement(doc, row, source, accepted, rejected, loss))
    returned = {name: [Decimal(0), Decimal(0)] for name in originals}
    original_receipt_names = {source["receipt"] for source in originals.values()}
    for doc in receipts.values():
        if not doc.is_return:
            continue
        for row in doc.items:
            if row.get("secondary_item_type"):
                continue
            source = originals.get(row.subcontracting_receipt_item)
            if not source:
                if row.subcontracting_order_item in order_items or doc.return_against in original_receipt_names:
                    # A different item on the same multi-item receipt can be out of
                    # scope. Its exact source link proves that it belongs elsewhere.
                    if row.subcontracting_receipt_item:
                        original_doc = receipts.get(doc.return_against)
                        other = next((r for r in original_doc.items if r.name == row.subcontracting_receipt_item), None) if original_doc else None
                        if other and other.subcontracting_order_item not in order_items:
                            continue
                    frappe.throw("委外退货缺少可核对的来源收货明细，请先核对")
                continue
            order_item = all_order_items[source["subcontracting_order_item"]]
            if (doc.return_against != source["receipt"] or doc.company != source["company"]
                    or doc.supplier != source["supplier"] or row.item_code != source["item_code"]
                    or row.stock_uom != source["stock_uom"]
                    or row.subcontracting_order != source["subcontracting_order"]
                    or row.subcontracting_order_item != source["subcontracting_order_item"]
                    or row.get("jf_flow_order") != source.get("jf_flow_order")):
                frappe.throw("委外退货的来源、公司、供应商、商品、单位或流程单不一致，请核对")
            accepted, rejected = _return_movement(row, source)
            returned[source["name"]][0] -= accepted
            returned[source["name"]][1] -= rejected
            if source["subcontracting_order_item"] in order_items:
                movements.append(_movement(doc, row, order_item, accepted, rejected, Decimal(0), source))
    for name, totals in returned.items():
        factor = _factor(originals[name])
        if totals[0] > _quantity(originals[name]["qty"]) * factor or totals[1] > _quantity(originals[name]["rejected_qty"]) * factor:
            frappe.throw("委外实物退货累计超过原接收或拒收数量，请核对")
    return sorted(movements, key=lambda r: (str(r["posting_date"]), r["receipt"], r["idx"]))


def _movement(doc, row, order_item, accepted, rejected, loss, source=None):
    return {"receipt": doc.name, "receipt_item": row.name, "idx": row.idx,
            "posting_date": doc.posting_date, "supplier": doc.supplier, "supplier_name": doc.supplier_name,
            "subcontracting_order": order_item["subcontracting_order"], "order_item": order_item["name"],
            "flow_order": order_item.get("jf_flow_order"), "item_code": row.item_code, "item_name": row.item_name,
            "stock_uom": row.stock_uom, "entry_type": "实物退回" if doc.is_return else "收回",
            "source_receipt": source["receipt"] if source else "",
            "source_receipt_item": source["name"] if source else "",
            "accepted_qty": accepted, "rejected_qty": rejected, "loss_qty": loss,
            "covered_qty": accepted + rejected + loss,
            "returned_qty": -(accepted + rejected) if doc.is_return else Decimal(0),
            "warehouse": row.warehouse, "rejected_warehouse": row.rejected_warehouse,
            "quality_inspection": row.quality_inspection}


def subcontracting_progress(filters=None):
    filters, start, end = _scope(filters, ("Subcontracting Order", "Subcontracting Receipt"))
    orders = _orders(filters, start, end)
    items = _order_items(orders, filters)
    all_items = _order_items(orders, frappe._dict()) if items else {}
    receipts = _receipt_closure(filters.company, all_items)
    totals = {name: {field: Decimal(0) for field in ("accepted_qty", "rejected_qty", "loss_qty", "returned_qty")} for name in items}
    for movement in receipt_movements(items, receipts, all_items):
        for field in totals[movement["order_item"]]:
            totals[movement["order_item"]][field] += movement[field]
    rows = []
    for name, item in items.items():
        qty = _quantity(item.get("qty")) * _factor(item)
        if qty <= 0:
            frappe.throw("委外订单计划数量必须大于零，请核对来源单据")
        total = totals[name]
        covered = total["accepted_qty"] + total["rejected_qty"] + total["loss_qty"]
        remaining = max(qty - covered, Decimal(0))
        pending = Decimal(0) if item["order_status"] in ("Closed", "Cancelled") else remaining
        due = item.get("expected_delivery_date") or item.get("schedule_date")
        overdue = max(date_diff(today(), due), 0) if due and pending > 0 else 0
        if cint(filters.only_pending) and pending <= 0 or cint(filters.only_overdue) and overdue <= 0:
            continue
        rows.append({"subcontracting_order": item["subcontracting_order"], "order_item": name,
                     "transaction_date": item["transaction_date"], "supplier": item["supplier"],
                     "supplier_name": item["supplier_name"], "flow_order": item.get("jf_flow_order"),
                     "item_code": item["item_code"], "item_name": item.get("item_name"), "stock_uom": item["stock_uom"],
                     "status_label": STATUS_LABELS.get(item["order_status"], item["order_status"]),
                     "planned_qty": qty, **total, "covered_qty": covered, "remaining_qty": remaining,
                     "pending_qty": pending, "over_received_qty": max(covered - qty, Decimal(0)),
                     "schedule_date": due, "overdue_days": overdue,
                     "progress_percent": min(covered / qty * 100, Decimal(100)) if qty else Decimal(0)})
    columns = _source_columns() + [
        _column("transaction_date", "订单日期", "Date"), _column("status_label", "订单状态"),
        _column("planned_qty", "委外计划数量", "Float"), _column("accepted_qty", "净接收数量", "Float"),
        _column("rejected_qty", "净拒收数量", "Float"), _column("loss_qty", "确认损耗", "Float"),
        _column("returned_qty", "累计实物退回", "Float"), _column("covered_qty", "已覆盖数量", "Float"),
        _column("remaining_qty", "尚未覆盖数量", "Float"), _column("pending_qty", "待收回数量", "Float"),
        _column("over_received_qty", "超计划数量", "Float"), _column("progress_percent", "覆盖进度 %", "Percent"),
        _column("schedule_date", "交期", "Date"), _column("overdue_days", "逾期天数", "Int"),
        _column("order_item", "来源订单明细", width=165),
    ]
    return columns, sorted(rows, key=lambda r: (str(r["schedule_date"] or ""), r["subcontracting_order"], r["order_item"]))


def subcontracting_receipt_ledger(filters=None):
    filters, start, end = _scope(filters, ("Subcontracting Order", "Subcontracting Receipt"))
    query = {"company": filters.company, "docstatus": 1,
             "posting_date": ["between", [str(start), str(end)]]}
    if filters.supplier:
        query["supplier"] = filters.supplier
    selected = _readable_documents("Subcontracting Receipt", query)
    originals = {doc.return_against for doc in selected.values() if doc.is_return and doc.return_against}
    source_docs = _readable_documents("Subcontracting Receipt", {
        "name": ["in", list(originals)], "company": filters.company, "docstatus": 1, "is_return": 0,
    }, complete=True) if originals else {}
    order_names = {row.subcontracting_order for doc in [*selected.values(), *source_docs.values()]
                   for row in doc.items if row.subcontracting_order and not row.get("secondary_item_type")}
    if filters.subcontracting_order:
        order_names &= {filters.subcontracting_order}
    orders = _orders(filters, names=order_names, complete=True)
    items = _order_items(orders, filters)
    all_items = _order_items(orders, frappe._dict()) if items else {}
    receipts = _receipt_closure(filters.company, all_items)
    for doc in selected.values():
        for row in doc.items:
            if row.get("secondary_item_type") or filters.item_code and row.item_code != filters.item_code:
                continue
            if filters.flow_order and row.get("jf_flow_order") != filters.flow_order:
                continue
            if not row.subcontracting_order and not filters.subcontracting_order:
                frappe.throw("委外收回或退货缺少订单来源，无法生成订单收回台账，请先核对")
    rows = [row for row in receipt_movements(items, receipts, all_items) if row["receipt"] in selected]
    columns = [_column("posting_date", "记账日期", "Date"), _column("receipt", "收回/退回单", "Link", "Subcontracting Receipt", 175),
               _column("entry_type", "业务类型", width=110)] + _source_columns() + [
        _column("accepted_qty", "接收变动", "Float"), _column("rejected_qty", "拒收变动", "Float"),
        _column("loss_qty", "确认损耗", "Float"), _column("covered_qty", "覆盖数量变动", "Float"),
        _column("warehouse", "本行库存仓库", "Link", "Warehouse", 170),
        _column("rejected_warehouse", "拒收仓库", "Link", "Warehouse", 170),
        _column("quality_inspection", "品检单", "Link", "Quality Inspection", 155),
        _column("source_receipt", "原收回单", "Link", "Subcontracting Receipt", 175),
        _column("source_receipt_item", "原收回明细", width=165), _column("receipt_item", "本行明细", width=165),
        _column("order_item", "来源订单明细", width=165),
    ]
    return columns, rows


def _source_columns():
    return [_column("subcontracting_order", "委外订单", "Link", "Subcontracting Order", 175),
            _column("supplier", "委外供应商", "Link", "Supplier", 160), _column("supplier_name", "供应商名称", width=160),
            _column("flow_order", "流程单", "Link", "Jifeng Flow Order", 170),
            _column("item_code", "商品编号", "Link", "Item", 150), _column("item_name", "商品名称", width=150),
            _column("stock_uom", "库存单位", "Link", "UOM", 85)]
