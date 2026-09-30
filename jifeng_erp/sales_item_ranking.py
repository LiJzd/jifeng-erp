"""Rank readable submitted invoice items by stored company-currency net sales."""

from decimal import Decimal

import frappe
from frappe.utils import cint, getdate

from jifeng_erp.inventory_scope import column, decimal_number, readable_documents, report_scope
from jifeng_erp.sales_reports import SI, _exact


FILTERS = {"company", "from_date", "to_date", "item_code"}


def _stored_number(row, field):
    if row.get(field) in (None, ""):
        frappe.throw("销售来源明细缺少已保存的数量或本币未税净额，请核对来源发票")
    return decimal_number(row.get(field))


def sales_item_ranking(filters=None):
    filters = frappe._dict(filters or {})
    if frappe.session.user == "Guest" or not frappe.has_permission(SI, "report"):
        frappe.throw("没有查询销售商品排行的权限", frappe.PermissionError)
    if any(value not in (None, "", 0, False) for field, value in filters.items() if field not in FILTERS):
        frappe.throw("净销售商品排行仅支持公司、记账日期和商品筛选")
    filters, start, end = report_scope(filters, (SI,))
    company_currency = frappe.db.get_value("Company", filters.company, "default_currency")
    if not company_currency:
        frappe.throw("公司未配置本币")
    query = {"company": filters.company, "docstatus": 1, "posting_date": ["between", [start, end]]}
    documents = readable_documents(SI, query)
    groups, items, seen = {}, {}, set()
    for doc in sorted(documents.values(), key=lambda doc: (str(doc.posting_date), doc.name)):
        # Native list permissions are followed by full document checks; recheck
        # the scope in case a document changed after its name was selected.
        if doc.company != filters.company or cint(doc.docstatus) != 1:
            continue
        posting_date = getdate(doc.posting_date)
        if not start <= posting_date <= end:
            continue
        for source in doc.get("items") or []:
            if filters.get("item_code") and source.get("item_code") != filters.item_code:
                continue
            if not source.get("item_code") or not source.get("stock_uom") or not source.get("name"):
                frappe.throw("销售来源明细缺少商品、库存单位或精确行标识，请核对来源发票")
            if (source.get("parent") != doc.name or source.get("parenttype") != SI
                    or source.get("parentfield") != "items"):
                frappe.throw("销售来源明细与所属发票不一致，请核对精确来源")
            identity = (doc.name, source.name)
            if identity in seen:
                frappe.throw("销售来源明细重复，请核对精确来源")
            seen.add(identity)
            item = _exact("Item", source.item_code, items)
            quantity = _stored_number(source, "stock_qty")
            amount = _stored_number(source, "base_net_amount")
            key = (source.item_code, source.stock_uom)
            group = groups.setdefault(key, {
                "item_code": source.item_code, "item_name": item.get("item_name"),
                "stock_uom": source.stock_uom, "stock_qty": Decimal(0),
                "base_net_amount": Decimal(0), "sources": [],
            })
            group["stock_qty"] += quantity
            group["base_net_amount"] += amount
            group["sources"].append({
                "document": doc.name, "posting_date": doc.posting_date,
                "invoice_item": source.name, "item_idx": source.get("idx"),
                "source_item_name": source.get("item_name"),
                "source_stock_qty": quantity, "source_base_net_amount": amount,
                "entry_type": "退货 / 贷项" if cint(doc.get("is_return")) else "销售",
                "updates_stock": cint(doc.get("update_stock")),
            })

    rows = []
    ranked = sorted(groups.values(), key=lambda group: (
        -group["base_net_amount"], group["item_code"], group["stock_uom"]))
    for rank, group in enumerate(ranked, 1):
        shared = {field: group[field] for field in ("item_code", "item_name", "stock_uom")}
        shared["company_currency"] = company_currency
        rows.append({
            "group_label": f"排行 {rank}", "rank": rank, **shared,
            "stock_qty": group["stock_qty"], "base_net_amount": group["base_net_amount"],
            "source_count": len(group["sources"]), "indent": 0, "is_group": 1,
        })
        for source in group["sources"]:
            rows.append({
                "group_label": f"{source['document']} / 行 {source['item_idx']}",
                **shared, **source, "indent": 1, "is_group": 0,
            })
    return _columns(), rows


def _columns():
    return [
        column("group_label", "排行 / 来源", width=220), column("rank", "排行", "Int", width=65),
        column("item_code", "商品", "Link", "Item", 160), column("item_name", "当前商品名称", width=180),
        column("stock_uom", "来源库存单位", "Link", "UOM", 110),
        column("stock_qty", "净销售数量", "Float", width=130),
        column("base_net_amount", "本币未税净额", "Currency", "company_currency", 155),
        column("source_count", "来源明细数", "Int", width=115),
        column("document", "来源销售发票", "Link", SI, 190),
        column("posting_date", "凭证记账日期", "Date", width=120),
        column("invoice_item", "精确发票明细", width=180), column("item_idx", "来源行号", "Int", width=90),
        column("source_item_name", "来源商品名称", width=180),
        column("source_stock_qty", "来源有符号数量", "Float", width=140),
        column("source_base_net_amount", "来源本币未税净额", "Currency", "company_currency", 165),
        column("entry_type", "凭证方向", width=115), column("updates_stock", "凭证更新库存", "Check", width=125),
        column("company_currency", "公司本币", "Link", "Currency", 110),
    ]
