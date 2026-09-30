"""Department assignment belongs to the dispatched operation, independent of HR transfers."""

import frappe
from frappe.utils import cint


def _department(name, company=None, accepting=False):
    filters = {"name": name}
    if company:
        filters["company"] = company
    if not frappe.get_list("Department", filters=filters, pluck="name", limit_page_length=1):
        frappe.throw("生产部门不存在、无选择权限或不属于工单公司")
    department = frappe.db.get_value("Department", name, ["disabled", "is_group"], as_dict=True)
    if accepting and (cint(department.disabled) or cint(department.is_group)):
        frappe.throw("新派工必须选择启用的明细生产部门")


def validate_default_department(doc, method=None):
    if doc.get("jf_department"):
        _department(doc.jf_department, accepting=True)


def validate_work_order_departments(doc, method=None):
    accepting = doc.is_new() or doc.docstatus == 0 or getattr(doc, "_action", None) == "submit"
    for row in doc.get("operations") or []:
        if accepting and not row.get("jf_department"):
            if row.get("workstation"):
                row.jf_department = frappe.get_cached_value("Workstation", row.workstation, "jf_department")
            if not row.get("jf_department") and row.get("operation"):
                row.jf_department = frappe.get_cached_value("Operation", row.operation, "jf_department")
        if row.get("jf_department"):
            _department(row.jf_department, doc.company, accepting=accepting)


def validate_job_card_department(doc, method=None):
    if not doc.get("operation_id"):
        if doc.get("jf_department"):
            frappe.throw("报工单缺少工单工序来源，不能填写生产部门")
        return
    work = frappe.get_doc("Work Order", doc.work_order)
    work.check_permission("read")
    source = next((row for row in work.get("operations") or [] if row.name == doc.operation_id), None)
    if not source or source.operation != doc.operation or work.company != doc.company:
        frappe.throw("报工单部门来源与生产工单工序、公司不一致")
    department = source.get("jf_department")
    if doc.get("jf_department") and doc.jf_department != department:
        frappe.throw("报工单生产部门与派工工序不一致")
    if department:
        _department(department, work.company)
    doc.jf_department = department
