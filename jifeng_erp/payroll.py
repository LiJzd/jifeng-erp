"""Traceable wages: one employee, one operation and one payroll earning."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe


CENT = Decimal("0.01")


def nonnegative(value, label):
    try:
        result = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        frappe.throw(f"{label}必须是有效数字")
    if not result.is_finite() or result < 0:
        frappe.throw(f"{label}必须是非负有限数字")
    return result


def wage_amounts(quantity, piece_rate, hours, hourly_rate, extra_amount):
    qty = nonnegative(quantity, "计件数量")
    rate = nonnegative(piece_rate, "计件工价")
    time = nonnegative(hours, "计时小时")
    hourly = nonnegative(hourly_rate, "计时单价")
    extra = nonnegative(extra_amount, "附加费")
    piece = (qty * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    timed = (time * hourly).quantize(CENT, rounding=ROUND_HALF_UP)
    return piece, timed, (piece + timed + extra).quantize(CENT, rounding=ROUND_HALF_UP)


def active_additional_salary(wage_name, exclude=None):
    filters = {"ref_doctype": "Jifeng Wage Entry", "ref_docname": wage_name,
               "docstatus": ["<", 2]}
    if exclude:
        filters["name"] = ["!=", exclude]
    return frappe.db.get_value("Additional Salary", filters, "name")


def validate_wage_additional_salary(doc, method=None):
    """Guard references even when a user edits the HRMS document directly."""
    if doc.ref_doctype != "Jifeng Wage Entry":
        return
    if not doc.ref_docname:
        frappe.throw("工资附加项缺少集沣计薪记录编号")
    # Serialize competing HRMS edits against the wage entry.
    frappe.db.get_value("Jifeng Wage Entry", doc.ref_docname, "name", for_update=True)
    wage = frappe.get_doc("Jifeng Wage Entry", doc.ref_docname)
    if wage.docstatus != 1:
        frappe.throw("只有已审核的计薪记录可以生成工资附加项")
    if active_additional_salary(wage.name, doc.name):
        frappe.throw("该计薪记录已经关联有效工资附加项，不能重复计薪")
    expected = (wage.employee, wage.company, wage.salary_component,
                str(wage.payroll_date), Decimal(str(wage.total_amount)))
    actual = (doc.employee, doc.company, doc.salary_component,
              str(doc.payroll_date), Decimal(str(doc.amount or 0)))
    if actual != expected or doc.is_recurring or doc.overwrite_salary_structure_amount:
        frappe.throw("工资附加项必须与已审核计薪记录的员工、公司、日期、科目和金额一致")
