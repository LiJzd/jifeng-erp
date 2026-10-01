"""Purpose-bound piece-pay eligibility without opening private HR archives.

Wage creation rights for the exact Company/Employee and readable native Employee
fields authorize only the necessary boolean: may this person receive piece pay?
The internal lookup reads only wage_method and audit metadata. It never loads or
lists whole Supplement documents, and never returns their private fields.
"""

import hashlib
from datetime import datetime

import frappe

from jifeng_erp.payroll import nonnegative


SUPPLEMENT = "Jifeng Employee Supplement"
WAGE = "Jifeng Wage Entry"
SNAPSHOT_FIELDS = ("piece_worker_qualified", "piece_worker_qualification_source", "piece_worker_qualification_version",
                   "piece_worker_qualification_status")
EMPLOYEE_FIELDS = ("name", "employee_number", "employee_name", "company", "department", "status")


def _get(document, field, default=None):
    return document.get(field, default) if isinstance(document, dict) else getattr(document, field, default)


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后查询计件人员", frappe.PermissionError)


def _fields(document, fields):
    if frappe.session.user != "Administrator" and not set(fields).issubset(
            document.meta.get_permitted_fieldnames(permission_type="read")):
        frappe.throw("没有读取计件人员资格所需员工字段的权限", frappe.PermissionError)


def _authorize(company, employee, *, active=True):
    _login()
    if not isinstance(company, str) or not company or not isinstance(employee, str) or not employee:
        frappe.throw("请选择公司及员工")
    if not frappe.has_permission(WAGE, "create"):
        frappe.throw("没有新增计件工资的权限", frappe.PermissionError)
    frappe.get_doc("Company", company).check_permission("select")
    person = frappe.get_doc("Employee", employee)
    person.check_permission("read")
    _fields(person, ("company", "status", "employee_name"))
    if active and (person.company != company or person.status != "Active"):
        frappe.throw("计件人员必须是当前公司的在职员工")
    # A role grant alone is insufficient: native User Permissions must authorize
    # creating this particular employee/company's wage document too.
    probe = frappe.get_doc({"doctype": WAGE, "company": company, "employee": employee})
    probe.check_permission("create")
    return person


def _lookup(company, employee, lock=False):
    # The purpose-bound authorization above is mandatory before this one-field
    # HR predicate. Audit metadata stays inside server-generated wage snapshots.
    return frappe.db.get_value(SUPPLEMENT, {"company": company, "employee": employee},
                               ["name", "wage_method", "modified"], as_dict=True, for_update=lock)


def require_piece_worker(company, employee, lock=True):
    _authorize(company, employee)
    source = _lookup(company, employee, lock)
    if not source or source.wage_method != "计件":
        frappe.throw("人员档案的工资方式必须为计件，才能录入计件工资")
    version = datetime.fromisoformat(str(source.modified)).isoformat(timespec="microseconds")
    return {"piece_worker_qualified": 1,
            "piece_worker_qualification_status": "已核实",
            "piece_worker_qualification_source": hashlib.sha256(str(source.name).encode()).hexdigest(),
            "piece_worker_qualification_version": hashlib.sha256(
                (str(source.name) + "\0" + version + "\0计件").encode()).hexdigest()}


def bind_piece_worker(document, company=None, employee=None, quantity=None, piece_rate=None, historical=None):
    """Recheck draft changes/submission; retain already-approved audit evidence."""
    quantity = _get(document, "quantity", 0) if quantity is None else quantity
    piece_rate = _get(document, "piece_rate", 0) if piece_rate is None else piece_rate
    piece = nonnegative(quantity, "计件数量") or nonnegative(piece_rate, "计件工价")
    if piece and historical:
        if _get(historical, "piece_worker_qualified") and all(
                _get(historical, field) for field in SNAPSHOT_FIELDS[1:3]):
            values = {field: _get(historical, field) for field in SNAPSHOT_FIELDS}
            values["piece_worker_qualification_status"] = "已核实"
        else:
            # Submitted records created before this qualification contract have
            # no evidence of the then-current HR classification. Preserve that
            # uncertainty instead of certifying it from today's archive.
            values = dict.fromkeys(SNAPSHOT_FIELDS, None)
            values.update(piece_worker_qualified=0, piece_worker_qualification_status="历史未记录")
    elif piece:
        values = require_piece_worker(company or document.company, employee or document.employee)
    else:
        values = dict.fromkeys(SNAPSHOT_FIELDS, None)
        values["piece_worker_qualified"] = 0
        values["piece_worker_qualification_status"] = "不适用"
    for field, value in values.items():
        setattr(document, field, value)


@frappe.whitelist()
def list_piece_workers(company, keyword=""):
    _login()
    if not isinstance(company, str) or not company:
        frappe.throw("请选择公司")
    if not isinstance(keyword, str) or len(keyword) > 140:
        frappe.throw("请输入有效人员关键词")
    if not frappe.has_permission(WAGE, "create") or not frappe.has_permission("Employee", "read"):
        frappe.throw("没有查询计件人员的权限", frappe.PermissionError)
    frappe.get_doc("Company", company).check_permission("select")
    query = {"company": company, "status": "Active"}
    permitted = (set(EMPLOYEE_FIELDS) if frappe.session.user == "Administrator" else
                 set(frappe.get_meta("Employee").get_permitted_fieldnames(permission_type="read")))
    if not {"company", "status", "employee_name"}.issubset(permitted):
        frappe.throw("没有读取计件人员资格所需员工字段的权限", frappe.PermissionError)
    options = {"or_filters": {field: ["like", "%" + keyword + "%"]
                              for field in ("employee_name", "employee_number") if field in permitted}} if keyword else {}
    listed = frappe.get_list("Employee", filters=query, fields=[field for field in EMPLOYEE_FIELDS if field in permitted],
                             order_by="employee_name asc, name asc", limit_page_length=1001, **options)
    if len(listed) > 1000:
        frappe.throw("人员过多，请输入姓名或编号关键词后查询")
    rows = []
    for row in listed:
        # Keep the native list's permitted projection; never refill omitted
        # display fields from a full Employee document.
        try:
            person = _authorize(company, row.name, active=False)
        except frappe.PermissionError:
            continue
        if person.company != company or person.status != "Active":
            continue
        source = _lookup(company, row.name)
        if source and source.wage_method == "计件":
            rows.append({field: row[field] for field in EMPLOYEE_FIELDS if field in row})
    return rows


@frappe.whitelist()
def select_piece_worker(company, employee):
    require_piece_worker(company, employee, lock=False)
    return {"company": company, "employee": employee, "eligible": True}
