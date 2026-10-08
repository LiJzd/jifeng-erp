"""Read submitted subcontracting movements with their exact source rows and permissions."""

from decimal import Decimal, InvalidOperation
import json

import frappe
from frappe.utils import cint, date_diff, getdate, today

from jifeng_erp.sample_source_queries import check_protected_reference
from jifeng_erp.ledger_source_guard import ledger_guard


STATUS_LABELS = {"Draft": "草稿", "Open": "待收回", "Partially Received": "部分收回",
                 "Completed": "已完成", "Closed": "已关闭", "Cancelled": "已取消",
                 "Material Transferred": "已发料", "Partial Material Transferred": "部分发料"}


def _column(field, label, kind="Data", options=None, width=130):
    return {"fieldname": field, "label": label, "fieldtype": kind, "options": options, "width": width}


def _scope(filters, doctypes, *, guard=None):
    filters = frappe._dict(filters or {})
    for doctype in doctypes:
        if not frappe.has_permission(doctype, "read"):
            frappe.throw("没有委外单据读取权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    query = guard.query if guard else lambda method, doctype, **kwargs: getattr(frappe, method)(doctype, **kwargs)
    if not query("get_list", "Company", filters={"name": filters.company}, pluck="name", limit_page_length=1):
        frappe.throw("没有查看该公司的权限", frappe.PermissionError)
    if guard:
        company = guard.document(frappe.get_doc("Company", filters.company), permission="select")
        if not company.has_permission("select"):
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
        names = guard.query("get_list", doctype, filters=query, pluck="name", limit_page_length=1) if guard else frappe.get_list(doctype, filters=query, pluck="name", limit_page_length=1)
        if not names:
            frappe.throw("没有读取所选委外查询条件的权限", frappe.PermissionError)
        document = frappe.get_doc(doctype, filters[field])
        if guard:
            guard.document(document)
        document.check_permission("read")
    return filters, start, end


def _readable_documents(doctype, query, complete=False, *, guard=None):
    names = guard.query("get_list", doctype, filters=query, pluck="name", distinct=True, limit_page_length=0) if guard else frappe.get_list(doctype, filters=query, pluck="name", distinct=True, limit_page_length=0)
    result = {}
    for name in names:
        doc = frappe.get_doc(doctype, name)
        if doc.has_permission("read"):
            if guard:
                guard.document(doc)
            result[name] = doc
    if complete:
        # This comparison is bounded by already readable source parents. Never return
        # hidden document names or a misleading balance with hidden movements omitted.
        expected = set(frappe.get_all(doctype, filters=query, pluck="name", distinct=True, limit_page_length=0))
        if expected != set(result):
            frappe.throw("无法完整读取相关收回、退货或来源单据，请核对读取权限", frappe.PermissionError)
    return result


def _orders(filters, start=None, end=None, names=None, complete=False, *, guard=None):
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
    result = _readable_documents("Subcontracting Order", query, complete=complete, guard=guard)
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


def _receipt_closure(company, order_items, *, guard=None):
    if not order_items:
        return {}
    query = [["Subcontracting Receipt", "company", "=", company],
             ["Subcontracting Receipt", "docstatus", "=", 1],
             ["Subcontracting Receipt Item", "subcontracting_order_item", "in", list(order_items)]]
    receipts = _readable_documents("Subcontracting Receipt", query, complete=True, guard=guard)
    order_names = list({row["subcontracting_order"] for row in order_items.values()})
    receipts.update(_readable_documents("Subcontracting Receipt", [
        ["Subcontracting Receipt", "company", "=", company],
        ["Subcontracting Receipt", "docstatus", "=", 1],
        ["Subcontracting Receipt Item", "subcontracting_order", "in", order_names],
    ], complete=True, guard=guard))
    originals = [name for name, doc in receipts.items() if not doc.is_return]
    if originals:
        # A return can have a damaged/missing order link. Include all returns against
        # these source receipts so that source integrity is checked rather than skipped.
        returns = _readable_documents("Subcontracting Receipt", {
            "company": company, "docstatus": 1, "is_return": 1,
            "return_against": ["in", originals],
        }, complete=True, guard=guard)
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
    guard = ledger_guard("subcontracting_progress")
    filters, start, end = _scope(filters, ("Subcontracting Order", "Subcontracting Receipt"), guard=guard)
    orders = _orders(filters, start, end, guard=guard)
    items = _order_items(orders, filters)
    all_items = _order_items(orders, frappe._dict()) if items else {}
    receipts = _receipt_closure(filters.company, all_items, guard=guard)
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
        if item.get("jf_flow_order"):
            guard.document(frappe.get_doc("Jifeng Flow Order", item["jf_flow_order"]))
        check_protected_reference("Jifeng Flow Order", item.get("jf_flow_order"), company=item["company"])
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
    guard.finish()
    return columns, sorted(rows, key=lambda r: (str(r["schedule_date"] or ""), r["subcontracting_order"], r["order_item"]))


def subcontracting_receipt_ledger(filters=None):
    guard = ledger_guard("subcontracting_receipt")
    filters, start, end = _scope(filters, ("Subcontracting Order", "Subcontracting Receipt"), guard=guard)
    query = {"company": filters.company, "docstatus": 1,
             "posting_date": ["between", [str(start), str(end)]]}
    if filters.supplier:
        query["supplier"] = filters.supplier
    selected = _readable_documents("Subcontracting Receipt", query, guard=guard)
    originals = {doc.return_against for doc in selected.values() if doc.is_return and doc.return_against}
    source_docs = _readable_documents("Subcontracting Receipt", {
        "name": ["in", list(originals)], "company": filters.company, "docstatus": 1, "is_return": 0,
    }, complete=True, guard=guard) if originals else {}
    order_names = {row.subcontracting_order for doc in [*selected.values(), *source_docs.values()]
                   for row in doc.items if row.subcontracting_order and not row.get("secondary_item_type")}
    if filters.subcontracting_order:
        order_names &= {filters.subcontracting_order}
    orders = _orders(filters, names=order_names, complete=True, guard=guard)
    items = _order_items(orders, filters)
    all_items = _order_items(orders, frappe._dict()) if items else {}
    receipts = _receipt_closure(filters.company, all_items, guard=guard)
    for doc in selected.values():
        for row in doc.items:
            if row.get("secondary_item_type") or filters.item_code and row.item_code != filters.item_code:
                continue
            if filters.flow_order and row.get("jf_flow_order") != filters.flow_order:
                continue
            if not row.subcontracting_order and not filters.subcontracting_order:
                frappe.throw("委外收回或退货缺少订单来源，无法生成订单收回台账，请先核对")
    rows = [row for row in receipt_movements(items, receipts, all_items) if row["receipt"] in selected]
    for row in rows:
        if row.get("flow_order"):
            guard.document(frappe.get_doc("Jifeng Flow Order", row["flow_order"]))
        check_protected_reference("Jifeng Flow Order", row.get("flow_order"), company=filters.company)
    company = guard.document(frappe.get_doc("Company", filters.company), permission="select",
                             extra={"default_currency"})
    valuation = _receipt_stock_valuation(rows, selected, company.default_currency, filters.company, guard)
    for row in rows:
        row.update(valuation[(row["receipt"], row["receipt_item"])])
    columns = [_column("posting_date", "记账日期", "Date"), _column("receipt", "收回/退回单", "Link", "Subcontracting Receipt", 175),
               _column("entry_type", "业务类型", width=110)] + _source_columns() + [
        _column("accepted_qty", "接收变动", "Float"), _column("rejected_qty", "拒收变动", "Float"),
        _column("loss_qty", "确认损耗", "Float"), _column("covered_qty", "覆盖数量变动", "Float"),
        _column("inventory_qty_change", "库存数量变动", "Float"),
        _column("inventory_value_change", "库存价值变动（公司本币）", "Currency", "currency", 180),
        _column("currency", "本币", "Link", "Currency", 90),
        _column("inventory_value_status", "库存估价状态", width=150),
        _column("warehouse", "本行库存仓库", "Link", "Warehouse", 170),
        _column("rejected_warehouse", "拒收仓库", "Link", "Warehouse", 170),
        _column("quality_inspection", "品检单", "Link", "Quality Inspection", 155),
        _column("source_receipt", "原收回单", "Link", "Subcontracting Receipt", 175),
        _column("source_receipt_item", "原收回明细", width=165), _column("receipt_item", "本行明细", width=165),
        _column("order_item", "来源订单明细", width=165),
    ]
    guard.finish()
    return columns, rows


def _receipt_stock_valuation(rows, receipts, currency, company, guard):
    """Project signed stock movements from active ledger rows for exact receipt details."""
    result, expected, detail_names = {}, {}, {}
    receipt_by_name = {name: doc for name, doc in receipts.items()}
    for movement in rows:
        doc = receipt_by_name.get(movement["receipt"])
        source = next((row for row in doc.items if row.name == movement["receipt_item"]), None) if doc else None
        if not source:
            frappe.throw("委外收回库存估价缺少原始明细，请先核对", frappe.PermissionError)
        key = (movement["receipt"], movement["receipt_item"])
        item = guard.document(frappe.get_doc("Item", source.item_code), extra={"stock_uom", "is_stock_item"})
        if item.stock_uom != source.stock_uom:
            frappe.throw("委外收回库存单位与商品库存单位不一致，不能读取库存价值", frappe.ValidationError)
        if not item.is_stock_item:
            result[key] = {"inventory_qty_change": None, "inventory_value_change": None,
                           "currency": currency, "inventory_value_status": "非库存商品"}
            continue
        quantities = {}
        for field, warehouse_field in (("accepted_qty", "warehouse"), ("rejected_qty", "rejected_warehouse")):
            quantity = _quantity(movement[field])
            if not quantity:
                continue
            warehouse_name = source.get(warehouse_field)
            if not warehouse_name:
                frappe.throw("委外收回库存仓库缺失，不能读取库存价值", frappe.ValidationError)
            warehouse = guard.document(frappe.get_doc("Warehouse", warehouse_name),
                                       extra={"company", "is_group"})
            if warehouse.company != company or warehouse.is_group:
                frappe.throw("委外收回库存仓库与公司不一致，不能读取库存价值", frappe.ValidationError)
            pair = (source.item_code, warehouse_name)
            quantities[pair] = quantities.get(pair, Decimal(0)) + quantity
        expected[key] = quantities
        if quantities:
            detail_names.setdefault(movement["receipt"], set()).add(movement["receipt_item"])
        result[key] = {"inventory_qty_change": sum(quantities.values(), Decimal(0)),
                       "inventory_value_change": Decimal(0) if not quantities else None,
                       "currency": currency,
                       "inventory_value_status": "无实物流入/退回" if not quantities else "待核对库存账"}

    voucher_names = sorted(detail_names)
    details = sorted({name for values in detail_names.values() for name in values})
    actual = {}
    if voucher_names and details:
        filters = {"company": company, "voucher_type": "Subcontracting Receipt",
                   "voucher_no": ["in", voucher_names], "voucher_detail_no": ["in", details], "is_cancelled": 0}
        fields = ["name", "company", "docstatus", "posting_date", "posting_time", "voucher_type", "voucher_no",
                  "voucher_detail_no", "item_code", "warehouse", "stock_uom", "actual_qty",
                  "stock_value_difference", "is_cancelled", "is_adjustment_entry"]
        raw_rows = guard.query("get_all", "Stock Ledger Entry", filters=filters, fields=fields,
                               order_by="name asc", limit_page_length=0)
        visible_rows = guard.query("get_list", "Stock Ledger Entry", filters=filters, fields=fields,
                                   order_by="name asc", limit_page_length=0)
        if {row.name for row in raw_rows} != {row.name for row in visible_rows}:
            frappe.throw("无法完整读取委外收回库存账，不能显示部分库存价值", frappe.PermissionError)
        for row in visible_rows:
            ledger = guard.document(frappe.get_doc("Stock Ledger Entry", row.name), extra=set(fields) - {"name"})
            key = (row.voucher_no, row.voucher_detail_no)
            target = (row.item_code, row.warehouse)
            receipt = receipt_by_name.get(row.voucher_no)
            source = next((item for item in receipt.items if item.name == row.voucher_detail_no), None) if receipt else None
            if (not receipt or not source or row.company != company or row.docstatus != 1 or row.is_cancelled
                    or row.voucher_type != "Subcontracting Receipt"
                    or row.posting_date != receipt.posting_date or row.posting_time != receipt.posting_time):
                frappe.throw("委外收回库存账来源或记账时间不一致，不能显示库存价值", frappe.ValidationError)
            item = guard.document(frappe.get_doc("Item", row.item_code), extra={"stock_uom", "is_stock_item"})
            warehouse = guard.document(frappe.get_doc("Warehouse", row.warehouse), extra={"company", "is_group"})
            if (not item.is_stock_item or row.item_code != source.item_code or row.stock_uom != item.stock_uom
                    or warehouse.company != company or warehouse.is_group or target not in expected.get(key, {})):
                frappe.throw("委外收回库存账商品、单位或仓库无法与收回明细对应", frappe.ValidationError)
            if row.actual_qty in (None, "") or row.stock_value_difference in (None, ""):
                frappe.throw("委外收回库存账缺少数量或库存价值变动", frappe.ValidationError)
            qty = _quantity(row.actual_qty)
            value = _quantity(row.stock_value_difference)
            if row.is_adjustment_entry:
                if qty:
                    frappe.throw("委外库存重估行出现数量变动，不能显示库存价值", frappe.ValidationError)
            elif not qty or (qty > 0) != (expected[key][target] > 0):
                frappe.throw("委外收回库存账数量方向与原始明细不一致", frappe.ValidationError)
            bucket = actual.setdefault(key, {"quantities": {}, "value": Decimal(0)})
            bucket["quantities"][target] = bucket["quantities"].get(target, Decimal(0)) + qty
            bucket["value"] += value
            bucket.setdefault("documents", []).append(ledger)

    for key, pairs in expected.items():
        if not pairs:
            continue
        bucket = actual.get(key)
        if not bucket or set(bucket["quantities"]) != set(pairs):
            frappe.throw("委外收回有效库存账缺行，不能显示部分库存价值", frappe.ValidationError)
        for pair, quantity in pairs.items():
            rows_for_pair = [doc for doc in bucket["documents"] if (doc.item_code, doc.warehouse) == pair]
            actual_qty = bucket["quantities"].get(pair, Decimal(0))
            if not rows_for_pair:
                frappe.throw("委外收回有效库存账缺行，不能显示部分库存价值", frappe.ValidationError)
            precision = rows_for_pair[0].precision("actual_qty") if callable(getattr(rows_for_pair[0], "precision", None)) else 6
            if abs(actual_qty - quantity) > Decimal(1).scaleb(-precision):
                frappe.throw("委外收回库存数量与有效库存账不一致", frappe.ValidationError)
        result[key]["inventory_value_change"] = bucket["value"]
        result[key]["inventory_value_status"] = "有效库存账"

    pending = (_receipt_reposting_rows(rows, expected, receipts, company, guard)
               if any(expected.values()) else set())
    for key in pending:
        if key in result:
            result[key]["inventory_value_change"] = None
            result[key]["inventory_value_status"] = "库存重估未完成"
    return result


def _receipt_reposting_rows(rows, expected, receipts, company, guard):
    """Identify only active native revaluation jobs that can change these rows."""
    if not any(expected.values()):
        return set()
    guard.include_doctypes({"Repost Item Valuation"})
    try:
        jobs = guard.query("get_all", "Repost Item Valuation",
                           filters={"company": company, "docstatus": 1,
                                    "status": ["not in", ["Completed", "Skipped", "Cancelled"]]},
                           fields=["name", "company", "docstatus", "status", "based_on", "reposting_data_file",
                                   "items_to_be_repost", "voucher_type", "voucher_no", "posting_date", "item_code",
                                   "warehouse"],
                           order_by="name asc", limit_page_length=0)
    except (frappe.DoesNotExistError, frappe.ValidationError, frappe.PermissionError):
        frappe.throw("无法确认库存重估状态，不能显示部分库存价值", frappe.ValidationError)
    keys_by_pair, dates = {}, {}
    receipt_names = {row["receipt"] for row in rows}
    for movement in rows:
        key = (movement["receipt"], movement["receipt_item"])
        dates[key] = str(receipts[movement["receipt"]].posting_date)
        for pair in expected.get(key, {}):
            keys_by_pair.setdefault(pair, set()).add(key)
    pending = set()
    for job in jobs:
        if job.get("status") not in {"Queued", "In Progress", "Failed"}:
            frappe.throw("存在未完成且状态无法核实的库存重估，不能显示部分库存价值", frappe.ValidationError)
        if job.get("voucher_type") == "Subcontracting Receipt" and job.get("voucher_no") in receipt_names:
            pending.update(key for key in dates if key[0] == job.voucher_no)
            continue
        seeds = []
        if job.get("based_on") == "Item and Warehouse":
            seeds = [job]
        elif job.get("based_on") == "Transaction":
            if job.get("reposting_data_file"):
                pending.update(dates)
                continue
            if job.get("items_to_be_repost"):
                try:
                    seeds = json.loads(job.items_to_be_repost)
                except (TypeError, ValueError):
                    frappe.throw("库存重估范围无法确认，不能显示部分库存价值", frappe.ValidationError)
                if not isinstance(seeds, list):
                    frappe.throw("库存重估范围无法确认，不能显示部分库存价值", frappe.ValidationError)
            elif job.get("voucher_type") and job.get("voucher_no"):
                seeds = guard.query("get_all", "Stock Ledger Entry", filters={"company": company,
                    "voucher_type": job.voucher_type, "voucher_no": job.voucher_no},
                    fields=["item_code", "warehouse", "posting_date"], order_by="name asc", limit_page_length=0)
            if not seeds:
                pending.update(dates)
                continue
        else:
            pending.update(dates)
            continue
        for seed in seeds:
            item_code, warehouse, posting_date = seed.get("item_code"), seed.get("warehouse"), seed.get("posting_date")
            if not item_code or not warehouse or not posting_date:
                pending.update(dates)
                continue
            for pair, keys in keys_by_pair.items():
                if pair == (item_code, warehouse):
                    pending.update(key for key in keys if str(posting_date) <= dates[key])
    return pending


def _source_columns():
    return [_column("subcontracting_order", "委外订单", "Link", "Subcontracting Order", 175),
            _column("supplier", "委外供应商", "Link", "Supplier", 160), _column("supplier_name", "供应商名称", width=160),
            _column("flow_order", "流程单", "Link", "Jifeng Flow Order", 170),
            _column("item_code", "商品编号", "Link", "Item", 150), _column("item_name", "商品名称", width=150),
            _column("stock_uom", "库存单位", "Link", "UOM", 85)]
