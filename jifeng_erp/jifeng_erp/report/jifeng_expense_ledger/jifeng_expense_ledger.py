"""Submitted Jifeng expense vouchers with links to their accounting entries."""

import frappe
from frappe.utils import getdate

from jifeng_erp.accounting import CREDIT, SOURCE, expense_journal, require_fields

REPORT_FIELDS = ("name", "posting_date", "store_name", "cost_center", "legacy_number", "counterparty_name",
                 "payment_method", "expense_account", "payment_account", "supplier", "payable_account",
                 "amount", "arrears_reference", "summary", "journal_entry")


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not frappe.has_permission("Jifeng Expense Voucher", "read"):
        frappe.throw("没有费用登记读取权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    visible = frappe.get_list(
        "Company", filters={"name": filters.company}, pluck="name", limit_page_length=1,
    )
    if not visible:
        frappe.throw("没有查看该公司的权限", frappe.PermissionError)
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")

    query = {
        "company": visible[0], "docstatus": 1,
        "posting_date": ["between", [str(start), str(end)]],
    }
    for field in ("expense_account", "cost_center", "store_name", "payment_method", "supplier"):
        if filters.get(field):
            query[field] = filters[field]
    require_fields(SOURCE, ["company", "docstatus", *REPORT_FIELDS])
    vouchers = frappe.get_list(
        "Jifeng Expense Voucher", filters=query,
        fields=list(REPORT_FIELDS),
        order_by="posting_date asc, name asc", limit_page_length=0,
    )
    company = frappe.get_doc("Company", visible[0])
    company.check_permission("read")
    require_fields("Company", ("name", "default_currency"))
    currency = company.default_currency
    rows = []
    for row in vouchers:
        document = frappe.get_doc(SOURCE, row.name)
        document.check_permission("read")
        if document.docstatus != 1 or document.company != visible[0]:
            continue
        if document.payment_method == CREDIT:
            expense_journal(document)
        result = {field: document.get(field) for field in REPORT_FIELDS} | {"currency": currency}
        if document.payment_method == CREDIT:
            result["payment_account"] = None
        else:
            result["supplier"] = result["payable_account"] = None
        rows.append(result)
    columns = [
        {"fieldname": "name", "label": "费用登记单", "fieldtype": "Link", "options": "Jifeng Expense Voucher", "width": 165},
        {"fieldname": "posting_date", "label": "费用日期", "fieldtype": "Date", "width": 105},
        {"fieldname": "store_name", "label": "店面", "fieldtype": "Data", "width": 125},
        {"fieldname": "cost_center", "label": "成本中心", "fieldtype": "Link", "options": "Cost Center", "width": 150},
        {"fieldname": "legacy_number", "label": "原系统编号", "fieldtype": "Data", "width": 125},
        {"fieldname": "counterparty_name", "label": "名称", "fieldtype": "Data", "width": 140},
        {"fieldname": "payment_method", "label": "方式", "fieldtype": "Data", "width": 100},
        {"fieldname": "expense_account", "label": "费用科目", "fieldtype": "Link", "options": "Account", "width": 175},
        {"fieldname": "payment_account", "label": "付款科目（非赊账）", "fieldtype": "Link", "options": "Account", "width": 175},
        {"fieldname": "supplier", "label": "应付供应商（明确来源）", "fieldtype": "Link", "options": "Supplier", "width": 175},
        {"fieldname": "payable_account", "label": "实际应付科目", "fieldtype": "Link", "options": "Account", "width": 175},
        {"fieldname": "amount", "label": "本币金额", "fieldtype": "Currency", "options": "currency", "width": 125},
        {"fieldname": "journal_entry", "label": "总账凭证", "fieldtype": "Link", "options": "Journal Entry", "width": 165},
        {"fieldname": "arrears_reference", "label": "旧欠款参考（不入账）", "fieldtype": "Currency", "options": "currency", "width": 165},
        {"fieldname": "summary", "label": "摘要", "fieldtype": "Small Text", "width": 240},
    ]
    return columns, rows
