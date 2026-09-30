"""Readable submitted wages grouped without mixing piecework units."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import getdate


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not frappe.has_permission("Jifeng Wage Entry", "read"):
        frappe.throw("没有计薪记录读取权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    visible_companies = frappe.get_list(
        "Company", filters={"name": filters.company}, pluck="name", limit_page_length=1,
    )
    if not visible_companies:
        frappe.throw("没有查看该公司的权限", frappe.PermissionError)
    company = visible_companies[0]
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")

    query = {
        "company": company, "docstatus": 1,
        "work_date": ["between", [str(start), str(end)]],
    }
    for field in ("employee", "operation", "salary_component"):
        if filters.get(field):
            query[field] = filters[field]

    names = frappe.get_list("Jifeng Wage Entry", filters=query, pluck="name",
                            order_by="employee asc, operation asc, name asc", limit_page_length=0)
    documents = []
    for name in names:
        doc = frappe.get_doc("Jifeng Wage Entry", name)
        if not doc.has_permission("read"):
            continue
        if doc.docstatus != 1 or doc.company != company or not start <= getdate(doc.work_date) <= end:
            frappe.throw("计薪来源的公司、日期或审核状态与查询不一致，请刷新后重试")
        documents.append(doc)
    rows = _group_wages(documents)
    currency = frappe.db.get_value("Company", company, "default_currency")
    columns = [
        {"fieldname": "employee", "label": "员工编号", "fieldtype": "Link", "options": "Employee", "width": 130},
        {"fieldname": "employee_name", "label": "姓名", "fieldtype": "Data", "width": 130},
        {"fieldname": "operation", "label": "工序", "fieldtype": "Data", "width": 140},
        {"fieldname": "salary_component", "label": "工资项目", "fieldtype": "Link", "options": "Salary Component", "width": 150},
        {"fieldname": "record_count", "label": "审核笔数", "fieldtype": "Int", "width": 95},
        {"fieldname": "stock_uom", "label": "计件单位", "fieldtype": "Link", "options": "UOM", "width": 110},
        {"fieldname": "unit_note", "label": "单位说明", "fieldtype": "Data", "width": 170},
        {"fieldname": "wage_entry", "label": "单笔来源", "fieldtype": "Link", "options": "Jifeng Wage Entry", "width": 180},
        {"fieldname": "quantity", "label": "计件数量", "fieldtype": "Float", "width": 110},
        {"fieldname": "piece_amount", "label": "计件金额", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"fieldname": "hours", "label": "计时小时", "fieldtype": "Float", "width": 110},
        {"fieldname": "time_amount", "label": "计时金额", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"fieldname": "extra_amount", "label": "附加费", "fieldtype": "Currency", "options": "currency", "width": 110},
        {"fieldname": "total_amount", "label": "合计", "fieldtype": "Currency", "options": "currency", "width": 130},
    ]
    # A site's existing Report can retain add_total_row=1 across upgrades.
    # Explicit native skip_total_row prevents server/export and grid totals
    # from mixing different or historically unknown piecework units.
    return columns, [{**row, "currency": currency} for row in rows], None, None, None, True


_AMOUNT_FIELDS = ("quantity", "piece_amount", "hours", "time_amount", "extra_amount", "total_amount")


def _number(value):
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("计薪来源数量或金额不是有效数字")
    if not result.is_finite() or result < 0:
        frappe.throw("计薪来源数量或金额必须为非负有限数字")
    return result


def _group_wages(documents):
    grouped = {}
    for doc in documents:
        amounts = {field: _number(doc.get(field)) for field in _AMOUNT_FIELDS}
        unit = doc.get("stock_uom") or ""
        # An old entry without a recorded unit cannot be combined with another
        # unknown unit. Keep its quantity and source in a separate single row.
        unknown_source = doc.name if amounts["quantity"] and not unit else ""
        key = (doc.employee, doc.operation, doc.salary_component, unit, unknown_source)
        if key not in grouped:
            grouped[key] = {
                "employee": doc.employee, "employee_name": doc.employee_name,
                "operation": doc.operation, "salary_component": doc.salary_component,
                "stock_uom": unit or None,
                "unit_note": "历史未记录单位，单笔展示" if unknown_source else "",
                "record_count": 0, "wage_entry": doc.name,
                "amounts": defaultdict(Decimal),
            }
        row = grouped[key]
        row["record_count"] += 1
        if row["record_count"] > 1:
            row["wage_entry"] = None
        for field, value in amounts.items():
            row["amounts"][field] += value
    result = []
    for key in sorted(grouped):
        row = grouped[key]
        amounts = row.pop("amounts")
        row.update({field: float(value) for field, value in amounts.items()})
        result.append(row)
    return result
