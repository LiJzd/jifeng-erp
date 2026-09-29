"""Submitted wage amounts by employee, operation and earning component."""

import frappe
from frappe.utils import getdate


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not frappe.has_permission("Jifeng Wage Entry", "read"):
        frappe.throw("没有计薪记录读取权限", frappe.PermissionError)
    if not filters.company or not filters.from_date or not filters.to_date:
        frappe.throw("请选择公司、开始日期和结束日期")
    company = frappe.get_doc("Company", filters.company)
    company.check_permission("read")
    start, end = getdate(filters.from_date), getdate(filters.to_date)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期")

    query = {
        "company": company.name, "docstatus": 1,
        "work_date": ["between", [str(start), str(end)]],
    }
    for field in ("employee", "operation", "salary_component"):
        if filters.get(field):
            query[field] = filters[field]

    rows = frappe.get_list(
        "Jifeng Wage Entry", filters=query,
        fields=[
            "employee", "employee_name", "operation", "salary_component",
            {"COUNT": "name", "as": "record_count"},
            {"SUM": "quantity", "as": "quantity"},
            {"SUM": "piece_amount", "as": "piece_amount"},
            {"SUM": "hours", "as": "hours"},
            {"SUM": "time_amount", "as": "time_amount"},
            {"SUM": "extra_amount", "as": "extra_amount"},
            {"SUM": "total_amount", "as": "total_amount"},
        ],
        group_by="employee, employee_name, operation, salary_component",
        order_by="employee asc, operation asc, salary_component asc",
        limit_page_length=0,
    )
    currency = company.default_currency
    columns = [
        {"fieldname": "employee", "label": "员工编号", "fieldtype": "Link", "options": "Employee", "width": 130},
        {"fieldname": "employee_name", "label": "姓名", "fieldtype": "Data", "width": 130},
        {"fieldname": "operation", "label": "工序", "fieldtype": "Data", "width": 140},
        {"fieldname": "salary_component", "label": "工资项目", "fieldtype": "Link", "options": "Salary Component", "width": 150},
        {"fieldname": "record_count", "label": "审核笔数", "fieldtype": "Int", "width": 95},
        {"fieldname": "quantity", "label": "计件数量", "fieldtype": "Float", "width": 110},
        {"fieldname": "piece_amount", "label": "计件金额", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"fieldname": "hours", "label": "计时小时", "fieldtype": "Float", "width": 110},
        {"fieldname": "time_amount", "label": "计时金额", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"fieldname": "extra_amount", "label": "附加费", "fieldtype": "Currency", "options": "currency", "width": 110},
        {"fieldname": "total_amount", "label": "合计", "fieldtype": "Currency", "options": "currency", "width": 130},
    ]
    return columns, [{**row, "currency": currency} for row in rows]
