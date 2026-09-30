"""Native personnel property dialogs, with Employee field permissions."""
import frappe
from frappe.model import get_permitted_fields
from frappe.utils import format_datetime, formatdate

from jifeng_erp.personnel_changes import JOB_FIELDS, WATCH_FIELDS, _field_permissions


def _readable_employee(employee):
    document = frappe.get_doc("Employee", employee)
    document.check_permission("read")
    return document


@frappe.whitelist()
def get_employee_field_property(employee, fieldname):
    """Keep the native RPC result shape without returning unreadable fields."""
    if not employee or not fieldname:
        return
    field = frappe.get_meta("Employee").get_field(fieldname)
    if not field:
        return
    if fieldname not in get_permitted_fields("Employee", permission_type="read", ignore_virtual=True):
        frappe.throw("没有读取该员工属性的权限", frappe.PermissionError)
    document = _readable_employee(employee)
    value = document.get(fieldname)
    if field.fieldtype == "Date":
        value = formatdate(value)
    elif field.fieldtype == "Datetime":
        value = format_datetime(value)
    return {"value": value, "datatype": field.fieldtype, "label": field.label, "options": field.options}


@frappe.whitelist()
def get_job_properties(employee, doctype):
    """Return only editable job properties supported by the submission chain."""
    if doctype not in ("Employee Promotion", "Employee Transfer"):
        frappe.throw("请选择晋升或调岗单据")
    if not frappe.has_permission(doctype, "create"):
        frappe.throw("没有办理该人事单据的权限", frappe.PermissionError)
    document = _readable_employee(employee)
    document.check_permission("write")
    _field_permissions(WATCH_FIELDS | ({"ctc"} if doctype == "Employee Promotion" else set()))
    readable = set(get_permitted_fields("Employee", permission_type="read", ignore_virtual=True))
    writable = set(get_permitted_fields("Employee", permission_type="write", ignore_virtual=True))
    return [{"label": frappe._(field.label), "value": field.fieldname}
            for field in document.meta.fields if field.fieldname in JOB_FIELDS & readable & writable
            and not field.hidden and not field.read_only]
