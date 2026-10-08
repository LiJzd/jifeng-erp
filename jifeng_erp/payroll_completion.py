"""Complete an existing Payroll Entry through native HRMS submission/accounting."""

from decimal import Decimal, InvalidOperation

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate


PAYROLL = "Payroll Entry"
SLIP = "Salary Slip"
JOURNAL = "Journal Entry"


def _deny(message="没有读取或处理本工资批次完整来源的权限"):
    frappe.throw(message, frappe.PermissionError)


def _invalid(message="工资批次来源或状态不一致，请先核对原生工资单"):
    frappe.throw(message, frappe.ValidationError)


def _fields(doctype, names, parenttype=None):
    permitted = set(get_permitted_fields(
        doctype, parenttype=parenttype, permission_type="read", ignore_virtual=True))
    meta = frappe.get_meta(doctype)
    masks = set(meta.get_permlevel_access(
        permission_type="mask", parenttype=parenttype, user=frappe.session.user))
    levels = set(meta.get_permlevel_access(
        permission_type="read", parenttype=parenttype, user=frappe.session.user))
    for name in names:
        field = meta.get_field(name)
        if (name not in permitted or not field or (field.permlevel or 0) not in levels
                or (field.mask and (field.permlevel or 0) in masks)):
            _deny()


def _table(parent, fieldname, childtype, child_fields):
    meta = frappe.get_meta(parent.doctype)
    field = meta.get_field(fieldname)
    levels = set(meta.get_permlevel_access(
        permission_type="read", user=frappe.session.user))
    masks = set(meta.get_permlevel_access(
        permission_type="mask", user=frappe.session.user))
    if (not field or field.fieldtype != "Table" or field.options != childtype
            or (field.permlevel or 0) not in levels
            or (field.mask and (field.permlevel or 0) in masks)):
        _deny()
    _fields(childtype, child_fields, parent.doctype)


def _require_document(doc, permission):
    if not doc or not doc.has_permission(permission):
        _deny()


def _amount(value):
    if isinstance(value, bool) or value in (None, ""):
        _invalid("工资单净应发金额不完整，不能生成月度计提")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _invalid("工资单净应发金额无效，不能生成月度计提")
    if not amount.is_finite() or amount < 0:
        _invalid("工资单净应发不能为负数，不能生成月度计提")
    return amount


def _names(doctype, filters, *, visible):
    reader = frappe.get_list if visible else frappe.get_all
    rows = reader(doctype, filters=filters, pluck="name", limit_page_length=0)
    return list(rows or [])


def _employee_selection(payroll):
    meta = frappe.get_meta(PAYROLL)
    table_field = meta.get_field("employees")
    if not table_field:
        _invalid("工资发放单缺少员工选择明细")
    _table(payroll, "employees", table_field.options, {"employee"})
    names = [row.get("employee") for row in (payroll.get("employees") or [])]
    if not names or any(not name for name in names) or len(names) != len(set(names)):
        _invalid("工资发放单员工范围为空或重复")
    return set(names)


def _source_slips(payroll):
    filters = {"payroll_entry": payroll.name}
    all_names = _names(SLIP, filters, visible=False)
    visible_names = _names(SLIP, filters, visible=True)
    if (not all_names or len(all_names) != len(set(all_names))
            or set(all_names) != set(visible_names)):
        _deny("无法完整读取本工资批次工资单，未执行提交或计提")
    slips = []
    for name in sorted(all_names):
        slip = frappe.get_doc(SLIP, name, for_update=True)
        _require_document(slip, "read")
        slips.append(slip)
    _fields(SLIP, {"company", "employee", "currency", "start_date", "end_date",
                   "payroll_entry", "net_pay", "journal_entry"})
    return slips


def _read_batch(payroll, slips, selected_employees, company, currency):
    if not payroll.start_date or not payroll.end_date:
        _invalid("工资发放单缺少完整工资期间")
    period = (getdate(payroll.start_date), getdate(payroll.end_date))
    locked_slips = slips
    validated_slips, seen_employees = [], set()
    for slip in locked_slips:
        _require_document(slip, "read")
        if not slip.start_date or not slip.end_date:
            _invalid("工资单缺少完整工资期间")
        if (slip.payroll_entry != payroll.name or slip.company != company.name
                or slip.currency != currency
                or (getdate(slip.start_date), getdate(slip.end_date)) != period
                or slip.employee not in selected_employees):
            _invalid("工资单公司、币种、期间或员工与工资发放单不一致")
        if slip.employee in seen_employees:
            _invalid("同一工资批次包含重复员工工资单")
        seen_employees.add(slip.employee)
        _amount(slip.net_pay)
        if slip.docstatus not in (0, 1):
            _invalid("本工资批次包含已取消或无效工资单")
        if slip.docstatus == 0:
            _require_document(slip, "write")
            _require_document(slip, "submit")
        else:
            _require_document(slip, "write")
        validated_slips.append(slip)
    if seen_employees != selected_employees:
        _invalid("工资单员工与工资发放单员工选择不完全一致")
    return validated_slips


def _linked_journal(slips, company):
    links = {slip.journal_entry for slip in slips if slip.journal_entry}
    if not links:
        return None
    if (len(links) != 1 or any(slip.docstatus != 1 or not slip.journal_entry for slip in slips)):
        _invalid("工资单已部分关联计提凭证，不能重复或补建计提")
    name = next(iter(links))
    journal = frappe.get_doc(JOURNAL, name)
    _require_document(journal, "read")
    _fields(JOURNAL, {"company"})
    if journal.docstatus != 1 or journal.company != company.name:
        _invalid("工资单关联的计提凭证未提交或公司不一致")
    return name


def _complete_response(payroll, journal_name, slips, already_complete):
    return {
        "status": "completed",
        "payroll_entry": payroll.name,
        "journal_entry": journal_name,
        "salary_slips": sorted(slip.name for slip in slips),
        "already_completed": bool(already_complete),
    }


def _set_flag(flags, name, present, value):
    if present:
        setattr(flags, name, value)
    else:
        try:
            flags.pop(name, None)
        except AttributeError:
            if hasattr(flags, name):
                delattr(flags, name)


@frappe.whitelist(methods=["POST"])
def complete_payroll_accrual(payroll_entry):
    """Finish every slip in a submitted Payroll Entry and make one native accrual."""
    if not isinstance(payroll_entry, str) or not payroll_entry or payroll_entry != payroll_entry.strip():
        _invalid("请先选择有效的工资发放单")

    payroll = frappe.get_doc(PAYROLL, payroll_entry)
    _require_document(payroll, "read")
    _require_document(payroll, "write")
    parent_fields = {"company", "start_date", "end_date"}
    payroll_meta = frappe.get_meta(PAYROLL)
    if payroll_meta.has_field("currency"):
        parent_fields.add("currency")
    _fields(PAYROLL, parent_fields)
    if payroll.docstatus != 1:
        _invalid("只能完成已提交的工资发放单")

    payroll = frappe.get_doc(PAYROLL, payroll.name, for_update=True)
    _require_document(payroll, "read")
    _require_document(payroll, "write")
    if payroll.docstatus != 1:
        _invalid("工资发放单状态已变化，请刷新后核对")

    company = frappe.get_doc("Company", payroll.company)
    _require_document(company, "read")
    if not company.has_permission("select"):
        _deny("没有选择本工资发放单所属公司的权限")
    _fields("Company", {"default_currency"})
    currency = payroll.get("currency") or company.default_currency
    if not currency:
        _invalid("工资发放单缺少有效币种")

    selected_employees = _employee_selection(payroll)
    employee_fields = set(get_permitted_fields("Employee", permission_type="read", ignore_virtual=True))
    employee_meta = frappe.get_meta("Employee")
    employee_masks = set(employee_meta.get_permlevel_access(
        permission_type="mask", user=frappe.session.user))
    employee_company = employee_meta.get_field("company")
    if ("company" not in employee_fields or not employee_company
            or (employee_company.mask and (employee_company.permlevel or 0) in employee_masks)):
        _deny()
    for employee_name in selected_employees:
        employee = frappe.get_doc("Employee", employee_name)
        _require_document(employee, "read")
        if employee.company != company.name:
            _invalid("工资发放单员工与公司范围不一致")

    slips = _source_slips(payroll)
    names = [slip.name for slip in slips]
    slips = _read_batch(payroll, slips, selected_employees, company, currency)
    linked = _linked_journal(slips, company)
    if linked:
        return _complete_response(payroll, linked, slips, True)

    if not frappe.has_permission(JOURNAL, "create") or not frappe.has_permission(JOURNAL, "submit"):
        _deny("没有创建并提交工资计提凭证的权限")

    flags = frappe.flags
    previous_flag = getattr(flags, "via_payroll_entry", None)
    try:
        had_flag = "via_payroll_entry" in flags
    except TypeError:
        had_flag = hasattr(flags, "via_payroll_entry")
    try:
        flags.via_payroll_entry = True
        for slip in slips:
            if slip.docstatus == 0:
                slip.submit()

        submitted_unlinked = []
        for name in names:
            slip = frappe.get_doc(SLIP, name)
            _require_document(slip, "read")
            if slip.docstatus != 1:
                _invalid("工资批次中仍有未提交工资单，未生成计提凭证")
            if not slip.journal_entry:
                submitted_unlinked.append(slip)
        if not submitted_unlinked:
            _invalid("没有可计提的未关联工资单")

        payroll.make_accrual_jv_entry(submitted_unlinked)
    finally:
        _set_flag(flags, "via_payroll_entry", had_flag, previous_flag)

    final_slips = [frappe.get_doc(SLIP, name) for name in names]
    journal_name = _linked_journal(final_slips, company)
    if not journal_name:
        _invalid("原生工资计提未将凭证关联到本批全部工资单")
    if payroll_meta.has_field("salary_slips_submitted") and not payroll.get("salary_slips_submitted"):
        payroll.db_set("salary_slips_submitted", 1)
        payroll.reload()
    return _complete_response(payroll, journal_name, final_slips, False)


def guard_salary_slip_submit(doc, method=None):
    """Keep payroll-linked slips on the complete native Payroll Entry route."""
    if doc.get("payroll_entry") and not getattr(frappe.flags, "via_payroll_entry", False):
        frappe.throw("请从工资发放提交工资条并生成月度计提", frappe.ValidationError)
