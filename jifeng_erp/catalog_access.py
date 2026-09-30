"""Permission checks for the limited employee links used by catalog records."""

import frappe


def check_employee(name, company, label):
    """Allow native selection without exposing an Employee's private fields."""
    if not name:
        return None
    employee = frappe.get_doc("Employee", name)
    employee.check_permission("select")
    if employee.company != company:
        frappe.throw(f"{label}必须属于当前公司")
    return {"name": employee.name, "company": employee.company}
