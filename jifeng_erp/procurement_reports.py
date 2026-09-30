"""Permission-scoped purchase requests and signed receipt voucher rows."""

from decimal import Decimal

import frappe
from frappe.utils import cint

from jifeng_erp.inventory_scope import (
    column, decimal_number, readable_documents, report_scope, selected_warehouses,
)


REQUEST_STATUS = {"Pending": "待采购", "Partially Ordered": "部分订购", "Ordered": "已订购",
                  "Partially Received": "部分收货", "Received": "已收货", "Stopped": "已停止",
                  "Cancelled": "已取消", "Draft": "草稿", "Submitted": "已提交"}


def _exact_document(doctype, name, cache):
    if not name:
        frappe.throw("采购凭证明细缺少可核对的来源，请先核对")
    key = (doctype, name)
    if key not in cache:
        documents = readable_documents(doctype, {"name": name}, complete=True)
        if name not in documents:
            frappe.throw("采购凭证关联的来源不存在，请先核对")
        cache[key] = documents[name]
    return cache[key]


def _factor(row):
    factor = decimal_number(row.get("conversion_factor"))
    if factor <= 0:
        frappe.throw("采购明细单位换算系数必须大于零，请核对来源凭证")
    return factor


def _stock_qty(row):
    if row.get("stock_qty") is None or row.get("qty") is None:
        frappe.throw("采购明细缺少交易数量或库存数量，请先核对")
    quantity, stock, factor = decimal_number(row.qty), decimal_number(row.stock_qty), _factor(row)
    precision = row.precision("stock_qty") if callable(getattr(row, "precision", None)) else 6
    tolerance = Decimal(10) ** -int(precision if precision is not None else 6)
    if abs(stock - quantity * factor) > tolerance:
        frappe.throw("采购库存数量与交易数量和换算系数不一致，请先核对")
    return stock


def _context(doc, row, cache):
    if not row.item_code or not row.stock_uom:
        frappe.throw("采购明细缺少商品或库存单位，请先核对")
    item = _exact_document("Item", row.item_code, cache)
    if row.stock_uom != item.stock_uom:
        frappe.throw("采购明细库存单位与商品档案不一致，请先核对")
    flow_name = row.get("jf_flow_order")
    if flow_name:
        flow = _exact_document("Jifeng Flow Order", flow_name, cache)
        if flow.company != doc.company:
            frappe.throw("采购凭证与流程单公司不一致，请先核对")
        materials = [material for material in flow.get("materials") or [] if material.item_code == row.item_code]
        if flow.item_code != row.item_code and not materials:
            frappe.throw("采购商品不在流程单商品或物料分析中，请先核对")
        # Analysis can use an alternate purchasing UOM while quantities below
        # use the voucher's stored conversion into the Item's stock UOM.
        units = {item.stock_uom} | {unit.uom for unit in item.get("uoms") or []}
        if materials and not any(material.get("uom") in units for material in materials):
            frappe.throw("流程单物料单位无法与采购商品单位核对，请先核对")
    return item


def _warehouse(name, company, cache, required=False):
    if not name:
        if required:
            frappe.throw("采购库存数量缺少对应仓库，请先核对")
        return
    warehouse = _exact_document("Warehouse", name, cache)
    if warehouse.company != company or warehouse.is_group:
        frappe.throw("采购明细仓库必须为凭证公司的明细仓库，请先核对")


def _item_match(row, filters):
    return ((not filters.item_code or row.item_code == filters.item_code)
            and (not filters.flow_order or row.get("jf_flow_order") == filters.flow_order))


def purchase_request_ledger(filters=None):
    filters, start, end = report_scope(filters, ("Material Request",))
    warehouses = set(selected_warehouses(filters)) if filters.warehouse else None
    documents = readable_documents("Material Request", {"company": filters.company, "docstatus": 1,
        "material_request_type": "Purchase", "transaction_date": ["between", [str(start), str(end)]]})
    rows, cache = [], {}
    for doc in documents.values():
        for row in doc.items:
            if not _item_match(row, filters) or warehouses is not None and row.warehouse not in warehouses:
                continue
            item = _context(doc, row, cache)
            _warehouse(row.warehouse, doc.company, cache, required=bool(item.is_stock_item))
            requested, ordered = _stock_qty(row), decimal_number(row.get("ordered_qty"))
            received = decimal_number(row.get("received_qty"))
            if min(requested, ordered, received) < 0:
                frappe.throw("采购申请库存数量、已订数量或已收数量不能为负数，请先核对")
            remaining = max(requested - ordered, Decimal(0))
            # Native direct receipts can satisfy a request without a PO. Orders
            # and receipts overlap; adding them would count the same supply twice.
            pending = max(requested - max(ordered, received), Decimal(0))
            if doc.status in ("Stopped", "Cancelled", "Closed"):
                pending = Decimal(0)
            if cint(filters.only_pending) and pending <= 0:
                continue
            rows.append({"material_request": doc.name, "request_item": row.name, "idx": row.idx,
                         "transaction_date": doc.transaction_date, "schedule_date": row.schedule_date,
                         "item_code": row.item_code, "item_name": row.item_name, "stock_uom": row.stock_uom,
                         "warehouse": row.warehouse, "flow_order": row.get("jf_flow_order"),
                         "status_label": REQUEST_STATUS.get(doc.status, doc.status), "status": doc.status,
                         "requested_qty": requested, "ordered_qty": ordered, "received_qty": received,
                         "remaining_qty": remaining, "pending_qty": pending})
    columns = [column("material_request", "采购申请", "Link", "Material Request", 180),
               column("transaction_date", "申请日期", "Date"), column("status_label", "申请状态"),
               *_item_columns(), column("warehouse", "申请仓库", "Link", "Warehouse", 175),
               column("requested_qty", "申请库存数量", "Float"), column("ordered_qty", "库存已订数量", "Float"),
               column("received_qty", "库存已收数量", "Float"),
               column("remaining_qty", "尚未订购数量", "Float"), column("pending_qty", "待采购数量", "Float"),
               column("schedule_date", "需求日期", "Date"), column("request_item", "申请明细", width=175)]
    return columns, sorted(rows, key=lambda row: (str(row["transaction_date"]), row["material_request"], row["idx"]))


def _receipt_quantities(doc, row, cache):
    item = _context(doc, row, cache)
    factor = _factor(row)
    accepted = _stock_qty(row)
    rejected = decimal_number(row.get("rejected_qty")) * factor
    if not doc.is_return:
        if accepted < 0 or rejected < 0 or decimal_number(row.qty) < 0 or cint(row.get("return_qty_from_rejected_warehouse")):
            frappe.throw("原采购收货数量或退拒收标记异常，请先核对")
        accepted_wh, rejected_wh = row.warehouse, row.rejected_warehouse
        source_doc, source_row = None, None
    else:
        if accepted > 0 or rejected > 0 or decimal_number(row.qty) > 0:
            frappe.throw("采购实物退货数量必须为负数，请先核对")
        source_doc = _exact_document("Purchase Receipt", doc.return_against, cache)
        if (source_doc.docstatus != 1 or source_doc.is_return or source_doc.company != doc.company
                or source_doc.supplier != doc.supplier or source_doc.currency != doc.currency
                or decimal_number(source_doc.conversion_rate) != decimal_number(doc.conversion_rate)):
            frappe.throw("采购退货的原收货公司、供应商、币种或汇率不一致，请先核对")
        source_row = next((source for source in source_doc.items if source.name == row.purchase_receipt_item), None)
        if not source_row:
            frappe.throw("采购退货缺少精确的原收货明细，请先核对")
        _context(source_doc, source_row, cache)
        if (source_row.item_code != row.item_code or source_row.stock_uom != row.stock_uom
                or source_row.get("jf_flow_order") != row.get("jf_flow_order")):
            frappe.throw("采购退货的商品、库存单位或流程单与原明细不一致，请先核对")
        source_accepted = _stock_qty(source_row)
        source_rejected = decimal_number(source_row.get("rejected_qty")) * _factor(source_row)
        if min(source_accepted, source_rejected) < 0:
            frappe.throw("采购退货原明细的数量异常，请先核对")
        _warehouse(source_row.warehouse, source_doc.company, cache, required=bool(source_accepted and item.is_stock_item))
        _warehouse(source_row.rejected_warehouse, source_doc.company, cache,
                   required=bool(source_rejected and item.is_stock_item))
        # The native mapper's update_item writes this persistent Check on
        # Purchase Receipt Item. Purchase Receipt has no corresponding field.
        if cint(row.get("return_qty_from_rejected_warehouse")):
            if not source_row.rejected_warehouse or row.warehouse != source_row.rejected_warehouse or source_rejected <= 0:
                frappe.throw("采购退拒收仓库与原拒收明细不一致，请先核对")
            rejected += accepted
            accepted, accepted_wh, rejected_wh = Decimal(0), None, source_row.rejected_warehouse
        else:
            if accepted and (row.warehouse != source_row.warehouse or source_accepted <= 0):
                frappe.throw("采购退接收仓库与原接收明细不一致，请先核对")
            accepted_wh, rejected_wh = row.warehouse, row.rejected_warehouse
        if decimal_number(row.get("rejected_qty")) and row.rejected_warehouse != source_row.rejected_warehouse:
            frappe.throw("采购退拒收仓库与原拒收明细不一致，请先核对")
        if -accepted > source_accepted or -rejected > source_rejected:
            frappe.throw("采购实物退货数量超过原明细对应数量，请先核对")
    _warehouse(accepted_wh, doc.company, cache, required=bool(accepted and item.is_stock_item))
    _warehouse(rejected_wh, doc.company, cache, required=bool(rejected and item.is_stock_item))
    return accepted, rejected, accepted_wh, rejected_wh, source_doc, source_row


def purchase_receipt_ledger(filters=None):
    filters, start, end = report_scope(filters, ("Purchase Receipt",))
    warehouses = set(selected_warehouses(filters)) if filters.warehouse else None
    query = {"company": filters.company, "docstatus": 1,
             "posting_date": ["between", [str(start), str(end)]]}
    if filters.supplier:
        query["supplier"] = filters.supplier
    if cint(filters.only_returns):
        query["is_return"] = 1
    documents = readable_documents("Purchase Receipt", query)
    # A list-visible return can fail full read due to an inaccessible return_against
    # link. Report an incomplete source rather than silently dropping that return.
    listed = frappe.get_list("Purchase Receipt", filters=query, pluck="name", distinct=True, limit_page_length=0)
    for name in set(listed) - set(documents):
        if frappe.get_doc("Purchase Receipt", name).is_return:
            frappe.throw("无法读取采购退货或其完整来源，请核对来源读取权限", frappe.PermissionError)
    currency = frappe.db.get_value("Company", filters.company, "default_currency")
    cache, rows = {}, []
    for doc in documents.values():
        for row in doc.items:
            if not _item_match(row, filters):
                continue
            accepted, rejected, accepted_wh, rejected_wh, source_doc, source_row = _receipt_quantities(doc, row, cache)
            voucher_accepted, voucher_rejected = accepted, rejected
            if warehouses is not None:
                accepted = accepted if accepted_wh in warehouses else Decimal(0)
                rejected = rejected if rejected_wh in warehouses else Decimal(0)
                if accepted_wh not in warehouses and rejected_wh not in warehouses:
                    continue
            amount, base_amount, net, base_net = (decimal_number(row.get(field)) for field in
                ("amount", "base_amount", "net_amount", "base_net_amount"))
            rows.append({"purchase_receipt": doc.name, "receipt_item": row.name, "idx": row.idx,
                         "posting_date": doc.posting_date, "posting_time": doc.posting_time,
                         "entry_type": "实物退回" if doc.is_return else "采购收货", "is_return": cint(doc.is_return),
                         "supplier": doc.supplier, "supplier_name": doc.supplier_name,
                         "item_code": row.item_code, "item_name": row.item_name, "stock_uom": row.stock_uom,
                         "flow_order": row.get("jf_flow_order"), "warehouse": accepted_wh,
                         "rejected_warehouse": rejected_wh, "voucher_warehouse": row.warehouse,
                         "voucher_rejected_warehouse": row.rejected_warehouse,
                         "accepted_qty": accepted, "rejected_qty": rejected, "physical_stock_qty": accepted + rejected,
                         "voucher_accepted_qty": voucher_accepted, "voucher_rejected_qty": voucher_rejected,
                         "currency": doc.currency, "company_currency": currency,
                         "conversion_rate": decimal_number(doc.conversion_rate),
                         "amount": amount, "base_amount": base_amount, "net_amount": net, "base_net_amount": base_net,
                         "source_receipt": source_doc.name if source_doc else None,
                         "source_receipt_item": source_row.name if source_row else None})
    columns = [column("purchase_receipt", "采购收货/退货单", "Link", "Purchase Receipt", 185),
               column("posting_date", "记账日期", "Date"), column("entry_type", "业务类型"),
               column("supplier", "供应商", "Link", "Supplier", 160), column("supplier_name", "供应商名称", width=165),
               *_item_columns(), column("warehouse", "接收数量对应仓库", "Link", "Warehouse", 180),
               column("rejected_warehouse", "拒收数量对应仓库", "Link", "Warehouse", 180),
               column("accepted_qty", "接收库存数量变动", "Float", width=150),
               column("rejected_qty", "拒收库存数量变动", "Float", width=150),
               column("physical_stock_qty", "实物库存数量变动", "Float", width=150),
               column("currency", "交易币种", "Link", "Currency", 100),
               column("amount", "整行金额（交易币）", "Currency", "currency", 160),
               column("net_amount", "整行净额（交易币）", "Currency", "currency", 160),
               column("company_currency", "公司本币", "Link", "Currency", 100),
               column("base_amount", "整行金额（本币）", "Currency", "company_currency", 160),
               column("base_net_amount", "整行净额（本币）", "Currency", "company_currency", 160),
               column("conversion_rate", "单据汇率", "Float"),
               column("source_receipt", "原采购收货单", "Link", "Purchase Receipt", 185),
               column("source_receipt_item", "原收货明细", width=180), column("receipt_item", "本行明细", width=180)]
    return columns, sorted(rows, key=lambda row: (str(row["posting_date"]), str(row["posting_time"] or ""),
                                                row["purchase_receipt"], row["idx"]))


def _item_columns():
    return [column("item_code", "商品编号", "Link", "Item", 160), column("item_name", "商品名称", width=160),
            column("stock_uom", "库存单位", "Link", "UOM", 95), column("flow_order", "流程单", "Link", "Jifeng Flow Order", 180)]
