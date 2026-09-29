"""Traceable company-currency cash receipts and payments from submitted vouchers."""

import frappe
from frappe.utils import getdate


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not frappe.has_permission("Payment Entry", "read"):
        frappe.throw("没有收付款凭证读取权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    if filters.payment_type not in ("Receive", "Pay"):
        frappe.throw("收付类型必须为收款或付款")
    if filters.get("party") and not filters.get("party_type"):
        frappe.throw("按往来方筛选时请先选择往来方类型")
    visible = frappe.get_list(
        "Company", filters={"name": filters.company}, pluck="name", limit_page_length=1,
    )
    if not visible:
        frappe.throw("没有查看该公司的权限", frappe.PermissionError)
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")

    payment_type = filters.payment_type
    account_field = "paid_to" if payment_type == "Receive" else "paid_from"
    amount_field = "base_received_amount" if payment_type == "Receive" else "base_paid_amount"
    query = {
        "company": visible[0], "docstatus": 1, "payment_type": payment_type,
        "posting_date": ["between", [str(start), str(end)]],
    }
    for field in ("party_type", "party"):
        if filters.get(field):
            query[field] = filters[field]
    if filters.get("account"):
        query[account_field] = filters.account

    vouchers = frappe.get_list(
        "Payment Entry", filters=query,
        fields=["name", "posting_date", "party_type", "party", "paid_from", "paid_to",
                "base_paid_amount", "base_received_amount", "reference_no", "remarks"],
        order_by="posting_date asc, name asc", limit_page_length=0,
    )
    currency = frappe.db.get_value("Company", visible[0], "default_currency")
    columns = [
        {"fieldname": "name", "label": "凭证号", "fieldtype": "Link", "options": "Payment Entry", "width": 170},
        {"fieldname": "posting_date", "label": "日期", "fieldtype": "Date", "width": 105},
        {"fieldname": "party_type", "label": "往来方类型", "fieldtype": "Data", "width": 110},
        {"fieldname": "party", "label": "往来方", "fieldtype": "Dynamic Link", "options": "party_type", "width": 160},
        {"fieldname": "paid_from", "label": "付款账户", "fieldtype": "Link", "options": "Account", "width": 170},
        {"fieldname": "paid_to", "label": "收款账户", "fieldtype": "Link", "options": "Account", "width": 170},
        {"fieldname": "amount", "label": "本币金额", "fieldtype": "Currency", "options": "currency", "width": 135},
        {"fieldname": "reference_no", "label": "银行流水号", "fieldtype": "Data", "width": 140},
        {"fieldname": "remarks", "label": "备注", "fieldtype": "Small Text", "width": 240},
    ]
    return columns, [{**row, "amount": row[amount_field], "currency": currency} for row in vouchers]
