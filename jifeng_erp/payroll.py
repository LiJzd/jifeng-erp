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


def active_additional_salary(wage_name, exclude=None, *, for_update=False):
    if for_update:
        rows = frappe.db.sql(
            """SELECT name FROM `tabAdditional Salary` WHERE ref_doctype = 'Jifeng Wage Entry'
               AND ref_docname = %s AND docstatus < 2 AND name != %s FOR UPDATE""",
            (wage_name, exclude or ""),
        )
        return rows[0][0] if rows else None
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
    wage.check_permission("read")
    if wage.docstatus != 1:
        frappe.throw("只有已审核的计薪记录可以生成工资附加项")
    if active_additional_salary(wage.name, doc.name, for_update=True):
        frappe.throw("该计薪记录已经关联有效工资附加项，不能重复计薪")
    expected = (wage.employee, wage.company, wage.salary_component,
                str(wage.payroll_date), Decimal(str(wage.total_amount)))
    actual = (doc.employee, doc.company, doc.salary_component,
              str(doc.payroll_date), Decimal(str(doc.amount or 0)))
    if actual != expected or doc.is_recurring or doc.overwrite_salary_structure_amount:
        frappe.throw("工资附加项必须与已审核计薪记录的员工、公司、日期、科目和金额一致")


def install_salary_source_index():
    """Keep current reads within the exact payroll source's index range."""
    frappe.db.add_index(
        "Salary Detail", ["additional_salary", "parenttype", "docstatus"],
        "jf_salary_source_consumption",
    )


def validate_salary_slip_wage_sources(doc, method=None):
    """Reserve each one-time wage earning in at most one live Salary Slip.

    HRMS checks identical periods, but overlapping periods can fetch the same
    Additional Salary. Native validate has finished calculating the rows when
    this hook runs. Drafts reserve sources; cancel/delete releases them through
    the native child lifecycle, without a separate reservation ledger.
    """
    references = {}
    for field in ("earnings", "deductions"):
        for row in doc.get(field) or []:
            if row.additional_salary:
                references.setdefault(row.additional_salary, []).append((field, row))
    if not references:
        return

    try:
        protected = []
        # Source locks serialize competing slips, including transactions which
        # established a consistent-read snapshot before waiting for this lock.
        for name in sorted(references):
            source = frappe.db.get_value(
                "Additional Salary",
                {"name": name, "ref_doctype": "Jifeng Wage Entry", "is_recurring": 0},
                ["name", "ref_docname", "docstatus", "employee", "company", "payroll_date",
                 "salary_component", "type", "disabled", "overwrite_salary_structure_amount"],
                as_dict=True, for_update=True,
            )
            if not source:
                continue
            rows = references[name]
            if len(rows) != 1:
                frappe.throw("同一集沣计薪附加项不能在工资单中重复引用")
            field, row = rows[0]
            if (source.docstatus != 1 or source.disabled or not source.ref_docname
                    or source.employee != doc.employee or source.company != doc.company
                    or source.type != "Earning" or source.overwrite_salary_structure_amount
                    or field != "earnings" or row.salary_component != source.salary_component
                    or row.is_recurring_additional_salary):
                frappe.throw("工资单的集沣计薪来源已变化，请重新核对附加项")
            if not all((doc.start_date, doc.end_date, source.payroll_date)):
                frappe.throw("工资单和集沣计薪来源必须有明确的计薪日期")
            if not (frappe.utils.getdate(doc.start_date)
                    <= frappe.utils.getdate(source.payroll_date)
                    <= frappe.utils.getdate(doc.end_date)):
                frappe.throw("集沣计薪日期必须在工资单期间内")
            protected.append(name)

        if not protected:
            return
        placeholders = ", ".join(["%s"] * len(protected))
        # Native check_if_latest already locks this slip AND its children.
        # NOWAIT fails closed if a competing child is locked; a plain SELECT
        # could use an old snapshot, and waiting could form a lock cycle.
        # The migration-installed index prevents unrelated child-table scans.
        used = frappe.db.sql(
            f"""SELECT 1 FROM `tabSalary Detail`
                FORCE INDEX (`jf_salary_source_consumption`)
                WHERE additional_salary IN ({placeholders})
                  AND parenttype = 'Salary Slip' AND parentfield IN ('earnings', 'deductions')
                  AND docstatus < 2 AND parent != %s
                LIMIT 1 FOR UPDATE NOWAIT""",
            (*protected, doc.name or ""),
        )
        if used:
            # Do not disclose another employee's slip or private source names.
            frappe.throw("集沣计薪附加项已被其他有效工资单采用；请先取消或删除重复工资单")
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError) as exc:
        # The native request rolls the whole transaction back. Never retry the
        # lookup with a stale/non-locking read or silently accept a busy source.
        # Payroll Entry skips ValidationError and continues submitting slips.
        # Preserve database error types so a deadlock cannot become a skipped
        # employee after MariaDB has rolled back the batch's prior writes.
        raise type(exc)("计薪来源正在被其他工资操作使用，本次未保存；请核实后重新操作") from None


def validate_job_card_cancel(doc, method=None):
    if frappe.db.exists("Jifeng Wage Entry", {"job_card": doc.name, "docstatus": 1}):
        frappe.throw("报工单已有审核计薪记录；请先取消关联工资附加项及计薪记录")
    from jifeng_erp.wage_allocation import guard_job_card_allocation

    guard_job_card_allocation(doc, "before_cancel")
