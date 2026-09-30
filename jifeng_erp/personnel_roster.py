"""Current readable native Employees, with optional readable supplement fields."""

import frappe

from jifeng_erp.inventory_scope import column


EMPLOYEE = "Employee"
SUPPLEMENT = "Jifeng Employee Supplement"
STATUSES = ("Active", "Inactive", "Suspended", "Left")
EMPLOYEE_FIELDS = ("name", "employee_number", "employee_name", "company", "department", "designation",
                   "status", "date_of_joining", "relieving_date")
SUPPLEMENT_FIELDS = ("name", "employee", "company", "term_type", "wage_method")


def _scope(filters):
    if frappe.session.user == "Guest" or not frappe.has_permission(EMPLOYEE, "read"):
        frappe.throw("没有读取员工档案的权限", frappe.PermissionError)
    if not isinstance(filters.get("company"), str) or not filters.company.strip():
        frappe.throw("请选择公司")
    frappe.get_doc("Company", filters.company).check_permission("select")
    query = {"company": filters.company}
    status = filters.get("status")
    if status not in (None, ""):
        if not isinstance(status, str) or status not in STATUSES:
            frappe.throw("请选择有效的原生员工状态")
        query["status"] = status
    for field, doctype in (("department", "Department"), ("designation", "Designation")):
        value = filters.get(field)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            frappe.throw("请选择有效的部门或职位")
        selected = frappe.get_doc(doctype, value)
        selected.check_permission("select")
        if field == "department" and selected.get("company") and selected.company != filters.company:
            frappe.throw("部门必须属于所选公司")
        query[field] = value
    return query


def _employees(query):
    # Native list permissions and User Permissions run before any full document
    # load. Request only report fields, so native field permissions apply too.
    listed = frappe.get_list(EMPLOYEE, filters=query, fields=list(EMPLOYEE_FIELDS),
                             order_by="employee_name asc, name asc", limit_page_length=0)
    documents = {}
    for row in listed:
        document = frappe.get_doc(EMPLOYEE, row.name)
        if not document.has_permission("read"):
            continue
        # Guard source/filter changes between listing and loading. A roster is
        # current data, so an out-of-scope source must not appear in the result.
        if any(document.get(field) != value for field, value in query.items()):
            continue
        documents[document.name] = document
    return sorted(documents.values(), key=lambda doc: (doc.employee_name or "", doc.name))


def _supplements(company, employees):
    if not frappe.has_permission(SUPPLEMENT, "read"):
        return False, {}
    if not employees:
        return True, {}
    try:
        listed = frappe.get_list(SUPPLEMENT, filters={"company": company, "employee": ["in", employees]},
                                 fields=list(SUPPLEMENT_FIELDS), limit_page_length=0)
    except frappe.PermissionError:
        # Losing optional supplement access cannot block the native roster.
        return False, {}
    documents = {}
    for row in listed:
        try:
            document = frappe.get_doc(SUPPLEMENT, row.name)
            readable = document.has_permission("read")
        except frappe.PermissionError:
            continue
        if readable and document.company == company and document.employee in employees:
            documents[document.employee] = document
    return True, documents


def personnel_roster(filters=None):
    """Return current records, never inferred historical staffing or performance."""
    filters = frappe._dict(filters or {})
    employees = _employees(_scope(filters))
    show_supplement, supplements = _supplements(filters.company, [doc.name for doc in employees])
    columns = [
        column("employee", "员工档案编号", "Link", EMPLOYEE, 170),
        column("employee_number", "员工号", width=130),
        column("employee_name", "姓名", width=150),
        column("company", "公司", "Link", "Company", 180),
        column("department", "部门", "Link", "Department", 170),
        column("designation", "职位", "Link", "Designation", 150),
        column("status", "原生员工状态", "Select", "\n".join(STATUSES), 135),
        column("date_of_joining", "入职日期", "Date", width=120),
        column("relieving_date", "离职日期", "Date", width=120),
    ]
    if show_supplement:
        columns.extend((column("term_type", "人员类型（旧档案）", width=160),
                        column("wage_method", "工资方式（档案）", width=160)))
    rows = []
    for document in employees:
        row = {"employee": document.name, **{field: document.get(field) for field in EMPLOYEE_FIELDS[1:]}}
        if show_supplement:
            supplement = supplements.get(document.name)
            row.update({field: supplement.get(field) if supplement else None
                        for field in ("term_type", "wage_method")})
        rows.append(row)
    return columns, rows
