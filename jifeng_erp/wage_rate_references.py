"""Keep an explicit historical quote on native wage documents and employee shares."""

from decimal import Decimal, InvalidOperation

import frappe

from jifeng_erp.archive_versions import same_version


REFERENCE_FIELDS = ("piece_rate_reference", "piece_rate_reference_version")
QUOTE_FIELDS = (
    "company", "employee", "operation", "work_date", "stock_uom", "job_card",
    "quantity", "piece_rate", "work_order", "flow_order", "wage_allocation",
    "wage_allocation_employee", *REFERENCE_FIELDS,
)
CANCEL_WAGE_FIELDS = (*QUOTE_FIELDS, "employee_name", "payroll_date", "salary_component", "hours", "hourly_rate",
                     "extra_amount", "piece_amount", "time_amount", "total_amount",
                     "piece_worker_qualified", "piece_worker_qualification_status",
                     "piece_worker_qualification_source", "piece_worker_qualification_version",
                     "workbench_request_key", "workbench_request_fingerprint", "amended_from")
CANCEL_NUMBERS = {"quantity", "piece_rate", "hours", "hourly_rate", "extra_amount",
                  "piece_amount", "time_amount", "total_amount", "source_completed_qty",
                  "allocated_qty", "wage_hours"}
CANCEL_ROW_FIELDS = ("name", "employee", "employee_name", "quantity", "piece_rate", *REFERENCE_FIELDS,
                     "hours", "source_hours_complete", "include_time_pay", "wage_hours",
                     "hourly_rate", "extra_amount", "piece_amount", "time_amount", "total_amount",
                     "wage_entry", "piece_worker_qualified", "piece_worker_qualification_status",
                     "piece_worker_qualification_source", "piece_worker_qualification_version")
CANCEL_CHECKS = {"source_hours_complete", "include_time_pay", "piece_worker_qualified"}


def _get(doc, field):
    return doc.get(field) if isinstance(doc, dict) else getattr(doc, field, None)


def _has_reference(doc):
    return any(_get(doc, field) not in (None, "") for field in REFERENCE_FIELDS)


def _number(value):
    try:
        result = Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        frappe.throw("历史工价引用需要有效的数量和工价")
    if not result.is_finite() or result < 0:
        frappe.throw("历史工价引用需要有效的非负数量和工价")
    return result


def _same_quote(current, previous):
    return all(
        _number(_get(current, field)) == _number(_get(previous, field))
        if field in ("quantity", "piece_rate")
        else str(_get(current, field) or "") == str(_get(previous, field) or "")
        for field in QUOTE_FIELDS
    )


def _reference(doc):
    name, version = (_get(doc, field) for field in REFERENCE_FIELDS)
    if not _has_reference(doc):
        for field in REFERENCE_FIELDS:
            setattr(doc, field, None)
        return None
    if (not isinstance(name, str) or not name or len(name) > 140
            or name != name.strip() or not isinstance(version, str)
            or not same_version(version, version)):
        frappe.throw("历史工价引用需要完整的原计薪编号和版本，请重新调用")
    if _number(_get(doc, "quantity")) <= 0:
        frappe.throw("历史计件工价只用于有计件数量的工资行")
    _number(_get(doc, "piece_rate"))
    return name, version


def _target_fields(document, child=False):
    if frappe.session.user == "Administrator":
        return
    required = {"piece_rate", *REFERENCE_FIELDS}
    meta = frappe.get_meta("Jifeng Wage Allocation Employee") if child else document.meta
    parenttype = "Jifeng Wage Allocation" if child else None
    for permission in ("read", "write"):
        permitted = set(meta.get_permitted_fieldnames(
            parenttype=parenttype, permission_type=permission))
        if not required.issubset(permitted):
            frappe.throw("没有填写历史工价及引用字段的权限", frappe.PermissionError)
    levels = set(meta.get_permlevel_access(permission_type="mask", parenttype=parenttype,
                                          user=frappe.session.user))
    if any((field := meta.get_field(name)) and field.get("mask")
           and (field.permlevel or 0) not in levels for name in required):
        frappe.throw("没有读取历史工价及引用字段的权限", frappe.PermissionError)
    if child:
        table = document.meta.get_field("employees")
        for permission in ("read", "write"):
            levels = set(document.meta.get_permlevel_access(permission_type=permission))
            if not (table and table.fieldtype == "Table"
                    and table.options == "Jifeng Wage Allocation Employee"
                    and (table.permlevel or 0) in levels):
                frappe.throw("没有填写员工分配明细的权限", frappe.PermissionError)


def guard_approved_wage_quote(wage):
    """Keep approved quoted amounts fixed when validating submitted records."""
    before = getattr(wage, "get_doc_before_save", lambda: None)()
    if before and before.docstatus == 1 and wage.docstatus in (1, 2):
        if _has_reference(before) or _has_reference(wage):
            if not _same_quote(wage, before):
                frappe.throw("已审核工资的历史工价引用及计件口径不能修改")
        return True  # Approval preserves the quote after an original cancellation.
    return False


def validate_wage_reference(wage):
    """Run after native current Job Card / approved allocation validation locks."""
    if guard_approved_wage_quote(wage):
        return
    reference = _reference(wage)
    if not reference:
        return
    _target_fields(wage)
    if _get(wage, "wage_allocation"):
        # validate_wage_source has already matched every amount and reference
        # to the exact row of its submitted native allocation. That approval
        # remains authority when the older quoted wage changes afterwards.
        return
    from jifeng_erp.wage_rates import resolve_reference

    resolve_reference(wage, *reference, _get(wage, "piece_rate"), for_update=True)


def validate_allocation_reference(allocation, row, approved_history=None):
    """The parent has already locked and verified its current native source."""
    previous = next((old for old in approved_history.employees if old.name == row.name), None) if (
        approved_history and approved_history.docstatus == 1
        and allocation.docstatus in (1, 2)) else None
    fields = ("company", "operation", "work_date", "stock_uom", "job_card", "work_order", "flow_order")
    if previous and (_has_reference(previous) or _has_reference(row)):
        old_quote = {field: _get(approved_history, field) for field in fields}
        new_quote = {field: _get(allocation, field) for field in fields}
        for field in ("employee", "quantity", "piece_rate", *REFERENCE_FIELDS):
            old_quote[field] = _get(previous, field)
            new_quote[field] = _get(row, field)
        if not _same_quote(new_quote, old_quote):
            frappe.throw("已审核分配的历史工价引用及员工计件口径不能修改")
        return
    reference = _reference(row)
    if not reference:
        return
    _target_fields(allocation, child=True)
    from jifeng_erp.wage_allocation import wage_values
    from jifeng_erp.wage_rates import resolve_reference

    target = frappe.get_doc({"doctype": "Jifeng Wage Entry", **wage_values(allocation, row)})
    resolve_reference(target, *reference, row.piece_rate, for_update=True)


def guard_approved_allocation_quotes(allocation, approved_history=None):
    """Compare all original cited rows, including rows removed in a cancel payload."""
    before = approved_history or getattr(allocation, "get_doc_before_save", lambda: None)()
    if not before or before.docstatus != 1 or allocation.docstatus not in (1, 2):
        return
    rows = {row.name: row for row in allocation.employees or []}
    originals = {row.name: row for row in before.employees or []}
    if len(rows) != len(allocation.employees or []):
        frappe.throw("已审核分配的员工行身份不能重复")
    for name, original in originals.items():
        if _has_reference(original) and name not in rows:
            frappe.throw("已审核分配的历史工价员工行不能删除或替换")
    for name, row in rows.items():
        if _has_reference(row) and name not in originals:
            frappe.throw("已审核分配不能增加历史工价员工行")
        if _has_reference(row) or (name in originals and _has_reference(originals[name])):
            validate_allocation_reference(allocation, row, before)


def _same_cancel_fields(current, before, fields):
    for field in fields:
        actual, original = _get(current, field), _get(before, field)
        if field in CANCEL_NUMBERS:
            equal = _number(actual) == _number(original)
        elif field in CANCEL_CHECKS:
            equal = _number(int(actual) if isinstance(actual, bool) else actual) == _number(
                int(original) if isinstance(original, bool) else original)
        else:
            equal = str(actual or "") == str(original or "")
        if not equal:
            frappe.throw("取消单据不能修改已审核的工资金额、员工、资格或来源，请重新读取原单")


def guard_cancelled_wage_snapshot(wage):
    """The whitelisted savedocs Cancel path accepts JSON and skips validate."""
    before = getattr(wage, "get_doc_before_save", lambda: None)()
    if before and before.docstatus == 1 and wage.docstatus == 2:
        _same_cancel_fields(wage, before, CANCEL_WAGE_FIELDS)


def guard_cancelled_allocation_snapshot(allocation):
    before = getattr(allocation, "get_doc_before_save", lambda: None)()
    if not before or before.docstatus != 1 or allocation.docstatus != 2:
        return
    from jifeng_erp.wage_allocation import SOURCE_FIELDS

    _same_cancel_fields(allocation, before, (*SOURCE_FIELDS, "payroll_date", "salary_component",
                                            "allocated_qty", "total_amount", "amended_from"))
    originals = {row.name: row for row in before.employees or []}
    rows = {row.name: row for row in allocation.employees or []}
    if set(rows) != set(originals) or len(rows) != len(allocation.employees or []):
        frappe.throw("取消分配单不能增加、删除或替换已审核的员工行，请重新读取原单")
    for name, row in rows.items():
        _same_cancel_fields(row, originals[name], CANCEL_ROW_FIELDS)
