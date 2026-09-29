"""Permission-aware figures for the classic manager workspace."""

import frappe
from frappe.utils import getdate, nowdate


def _aggregate(doctype, field, filters):
    if not frappe.has_permission(doctype, "read"):
        return None
    rows = frappe.get_list(
        doctype,
        filters=filters,
        fields=[{"SUM": field, "as": "value"}],
        limit_page_length=1,
    )
    return float(rows[0].value or 0) if rows else 0.0


def _count(doctype, filters):
    if not frappe.has_permission(doctype, "read"):
        return None
    rows = frappe.get_list(
        doctype,
        filters=filters,
        fields=[{"COUNT": "*", "as": "value"}],
        limit_page_length=1,
    )
    return int(rows[0].value or 0) if rows else 0


def _stock_value(company):
    """Current bin valuation and source warehouses visible to this user."""
    if not frappe.has_permission("Warehouse", "read") or not frappe.has_permission("Bin", "read"):
        return None, []
    warehouses = frappe.get_list(
        "Warehouse", filters={"company": company, "is_group": 0},
        pluck="name", limit_page_length=0,
    )
    if not warehouses:
        return None, []
    value = _aggregate("Bin", "stock_value", {
        "company": company, "warehouse": ["in", warehouses],
    })
    return value, warehouses


@frappe.whitelist()
def overview(company=None, from_date=None, to_date=None):
    """Return only figures from documents this user may read."""
    if not frappe.has_permission("Company", "read"):
        frappe.throw("没有查看公司的权限", frappe.PermissionError)
    companies = frappe.get_list(
        "Company", fields=["name", "default_currency"], order_by="name",
        limit_page_length=0,
    )
    if not companies:
        return {"companies": [], "company": None, "metrics": {}}

    accessible = {row.name: row.default_currency for row in companies}
    company = company or companies[0].name
    if company not in accessible:
        frappe.throw("没有查看该公司的权限", frappe.PermissionError)

    today = getdate(nowdate())
    start = getdate(from_date) if from_date else today.replace(day=1)
    end = getdate(to_date) if to_date else today
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")
    if (end - start).days > 366:
        frappe.throw("查询跨度不能超过 366 天")

    dates = [str(start), str(end)]
    posted = {"company": company, "docstatus": 1}
    stock_value, stock_warehouses = _stock_value(company)
    return {
        "companies": [dict(row) for row in companies],
        "company": company,
        "currency": accessible[company],
        "from_date": str(start),
        "to_date": str(end),
        "stock_warehouses": stock_warehouses,
        "metrics": {
            "sales": _aggregate("Sales Invoice", "base_net_total", {
                **posted, "posting_date": ["between", dates],
            }),
            "purchases": _aggregate("Purchase Invoice", "base_net_total", {
                **posted, "posting_date": ["between", dates],
            }),
            "expenses": _aggregate("Jifeng Expense Voucher", "amount", {
                **posted, "posting_date": ["between", dates],
            }),
            "open_work_orders": _count("Work Order", {
                **posted, "status": ["not in", ["Completed", "Stopped", "Closed"]],
            }),
            "stock_value": stock_value,
        },
    }
