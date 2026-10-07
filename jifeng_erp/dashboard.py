"""Permission-aware figures for the classic manager workspace."""

import frappe
from frappe.utils import getdate, nowdate

from jifeng_erp.inventory_scope import decimal_number, readable_documents


FINANCIAL_REPORTS = {
    "General Ledger": ("总账", "按公司和期间核对有效会计分录，可打开原始凭证"),
    "Profit and Loss Statement": ("损益表", "按原生收入和费用科目读取期间损益"),
    "Cash Flow": ("现金流量表", "按原生报表归集经营、投资和筹资活动现金流量"),
    "Balance Sheet": ("资产负债表", "核对期末资产、负债及现金和银行科目余额"),
}


def _aggregate(doctype, field, filters):
    if not frappe.has_permission(doctype, "read"):
        return None
    return float(sum((decimal_number(doc.get(field)) for doc in readable_documents(doctype, filters).values()),
                     decimal_number(0)))


def _count(doctype, filters):
    if not frappe.has_permission(doctype, "read"):
        return None
    return len(readable_documents(doctype, filters))


def _stock_value(company):
    """Current bin valuation and source warehouses visible to this user."""
    if not frappe.has_permission("Warehouse", "read") or not frappe.has_permission("Bin", "read"):
        return None, []
    warehouses = list(readable_documents("Warehouse", {"company": company, "is_group": 0}))
    if not warehouses:
        return None, []
    value = _aggregate("Bin", "stock_value", {
        "company": company, "warehouse": ["in", warehouses],
    })
    return value, warehouses


def _scope(company, from_date, to_date):
    if not frappe.has_permission("Company", "read"):
        frappe.throw("没有查看公司的权限", frappe.PermissionError)
    companies = frappe.get_list(
        "Company", fields=["name", "default_currency"], order_by="name",
        limit_page_length=0,
    )
    if not companies:
        return companies, None, None, None

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
    return companies, company, start, end


def _permitted_report(name):
    # Use the same Report/Custom Role, source-report and disabled gates as the
    # native report page. An Accounts role alone is not an access grant.
    from frappe.desk.query_report import get_report_doc

    try:
        return get_report_doc(name)
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        return None


@frappe.whitelist(methods=["GET"])
def financial_report(name, company, from_date, to_date):
    """Open one permitted native ledger report in the queried company/period."""
    if not isinstance(name, str) or name not in FINANCIAL_REPORTS:
        frappe.throw("请选择经营总览中的账簿报表")
    if not company or not from_date or not to_date:
        frappe.throw("请先查询公司、开始日期和结束日期")
    _, company, start, end = _scope(company, from_date, to_date)
    if not company or not _permitted_report(name):
        frappe.throw("没有查看该账簿报表的权限", frappe.PermissionError)

    filters = {"company": company}
    if name == "General Ledger":
        filters.update(from_date=str(start), to_date=str(end), show_cancelled_entries=0,
                       show_amount_in_company_currency=1)
    else:
        # These are the native financial_statements.js date-range filters;
        # from_date/to_date alone would leave its default fiscal-year scope.
        filters.update(filter_based_on="Date Range", period_start_date=str(start),
                       period_end_date=str(end), periodicity="Monthly")
        if name in ("Profit and Loss Statement", "Balance Sheet"):
            filters["accumulated_values"] = int(name == "Balance Sheet")
        if name == "Cash Flow":
            # Keep the native default: this report shows period cash movements.
            # Opening/closing cash additionally needs a company Fiscal Year link;
            # the separate Balance Sheet retains its native period-end balances.
            filters["show_opening_and_closing_balance"] = 0
    return {"route": ["query-report", name], "filters": filters}


@frappe.whitelist()
def overview(company=None, from_date=None, to_date=None):
    """Return only figures from documents this user may read."""
    companies, company, start, end = _scope(company, from_date, to_date)
    if not company:
        return {"companies": [], "company": None, "metrics": {}, "financial_reports": []}

    dates = [str(start), str(end)]
    posted = {"company": company, "docstatus": 1}
    stock_value, stock_warehouses = _stock_value(company)
    return {
        "companies": [dict(row) for row in companies],
        "company": company,
        "currency": next(row.default_currency for row in companies if row.name == company),
        "from_date": str(start),
        "to_date": str(end),
        "stock_warehouses": stock_warehouses,
        "financial_reports": [
            {"name": name, "label": label, "hint": hint}
            for name, (label, hint) in FINANCIAL_REPORTS.items() if _permitted_report(name)
        ],
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
