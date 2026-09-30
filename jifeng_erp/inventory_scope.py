"""Shared read scope for procurement, stock vouchers and complete stock balances."""

from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import getdate


def column(field, label, kind="Data", options=None, width=130):
    return {"fieldname": field, "label": label, "fieldtype": kind, "options": options, "width": width}


def decimal_number(value):
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("采购或库存数量、金额不是有效数字，请核对来源凭证")
    if not number.is_finite():
        frappe.throw("采购或库存数量、金额不是有限数字，请核对来源凭证")
    return number


def report_scope(filters, doctypes):
    filters = frappe._dict(filters or {})
    for doctype in doctypes:
        if not frappe.has_permission(doctype, "read"):
            frappe.throw("没有读取采购或库存单据的权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    # Stock/Purchase users can select Company without reading its full profile.
    if not frappe.get_list("Company", filters={"name": filters.company}, pluck="name", limit_page_length=1):
        frappe.throw("没有查询该公司的权限", frappe.PermissionError)
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")
    for field, doctype in (("item_code", "Item"), ("warehouse", "Warehouse"), ("supplier", "Supplier"),
                           ("flow_order", "Jifeng Flow Order")):
        if not filters.get(field):
            continue
        query = {"name": filters[field]}
        if field in ("warehouse", "flow_order"):
            query["company"] = filters.company
        if not frappe.get_list(doctype, filters=query, pluck="name", limit_page_length=1):
            frappe.throw("没有读取所选采购或库存查询条件的权限", frappe.PermissionError)
        frappe.get_doc(doctype, filters[field]).check_permission("read")
    return filters, start, end


def readable_documents(doctype, query, complete=False):
    names = frappe.get_list(doctype, filters=query, pluck="name", distinct=True, limit_page_length=0)
    documents = {}
    for name in names:
        doc = frappe.get_doc(doctype, name)
        if doc.has_permission("read"):
            documents[name] = doc
    if complete:
        expected = set(frappe.get_all(doctype, filters=query, pluck="name", distinct=True, limit_page_length=0))
        if expected != set(documents):
            frappe.throw("无法完整读取相关来源凭证，请核对来源读取权限", frappe.PermissionError)
    return documents


def selected_warehouses(filters):
    """Include disabled leaf warehouses with history, never infer hidden children."""
    query = {"company": filters.company, "is_group": 0}
    if filters.warehouse:
        warehouse = frappe.get_doc("Warehouse", filters.warehouse)
        warehouse.check_permission("read")
        if warehouse.is_group:
            query.update({"lft": [">", warehouse.lft], "rgt": ["<", warehouse.rgt]})
        else:
            query["name"] = warehouse.name
    rows = frappe.get_list("Warehouse", filters=query, fields=["name"], limit_page_length=0)
    return [row.name for row in rows if frappe.get_doc("Warehouse", row.name).has_permission("read")]


def stock_items(filters):
    query = {"is_stock_item": 1}
    if filters.item_code:
        query["name"] = filters.item_code
    rows = frappe.get_list("Item", filters=query, pluck="name", limit_page_length=0)
    return [name for name in rows if frappe.get_doc("Item", name).has_permission("read")]
