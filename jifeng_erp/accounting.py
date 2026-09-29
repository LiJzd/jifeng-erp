"""Accounting controls for a submitted legacy-style expense voucher."""

from decimal import Decimal, InvalidOperation

import frappe


def positive_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("费用金额必须是有效数字")
    if not amount.is_finite() or amount <= 0:
        frappe.throw("费用金额必须大于零")
    return amount


def validate_expense_voucher(doc):
    if doc.expense_account == doc.payment_account:
        frappe.throw("费用科目与付款科目不能相同")
    currency = frappe.db.get_value("Company", doc.company, "default_currency")
    if not currency:
        frappe.throw("请先配置公司的默认币种")
    amount = positive_amount(doc.amount)
    expense = frappe.get_doc("Account", doc.expense_account)
    payment = frappe.get_doc("Account", doc.payment_account)
    cost_center = frappe.get_doc("Cost Center", doc.cost_center)
    for account in (expense, payment):
        account.check_permission("read")
        if account.company != doc.company or account.is_group or account.disabled:
            frappe.throw("请选择当前公司启用的明细会计科目")
        if account.account_currency and account.account_currency != currency:
            frappe.throw("费用登记暂只支持公司默认币种的会计科目")
    cost_center.check_permission("read")
    if cost_center.company != doc.company or cost_center.is_group:
        frappe.throw("请选择当前公司的明细成本中心")
    if expense.root_type != "Expense":
        frappe.throw("费用科目必须属于费用类")
    if payment.root_type != "Asset" or payment.account_type not in ("Cash", "Bank"):
        frappe.throw("付款科目必须是现金或银行资产科目")
    if doc.payment_method == "现金" and payment.account_type != "Cash":
        frappe.throw("现金付款只能选择现金科目")
    if doc.payment_method == "银行转账" and payment.account_type != "Bank":
        frappe.throw("银行转账只能选择银行科目")
    return amount


def validate_expense_journal_entry(doc, method=None):
    if doc.get("jf_expense_voucher") and not getattr(doc.flags, "from_jifeng_expense", False):
        frappe.throw("集沣费用登记的总账凭证只能由费用登记创建和修改")


def validate_expense_journal_entry_cancel(doc, method=None):
    if doc.get("jf_expense_voucher") and not getattr(doc.flags, "from_jifeng_expense", False):
        frappe.throw("请从集沣费用登记取消关联的总账凭证")
