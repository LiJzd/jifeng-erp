"""Supervisor-approved employee shares of one native operation completion."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import frappe

from jifeng_erp.payroll import nonnegative, wage_amounts
from jifeng_erp.piece_workers import bind_piece_worker


ALLOCATION = "Jifeng Wage Allocation"
QUANTITY_STEP = Decimal("0.000001")
SOURCE_FIELDS = (
    "company", "job_card", "work_order", "flow_order", "operation", "operation_id",
    "item_code", "stock_uom", "currency", "work_date", "source_completed_qty", "source_signature",
)
WAGE_FIELDS = (
    "company", "job_card", "work_order", "flow_order", "operation", "work_date",
    "payroll_date", "salary_component", "employee", "quantity", "piece_rate",
    "hours", "hourly_rate", "extra_amount", "piece_amount", "time_amount", "total_amount",
    "stock_uom", "wage_allocation", "wage_allocation_employee",
    "piece_rate_reference", "piece_rate_reference_version",
)
NUMERIC_WAGE_FIELDS = {
    "quantity", "piece_rate", "hours", "hourly_rate", "extra_amount",
    "piece_amount", "time_amount", "total_amount",
}


def _get(document, field, default=None):
    if isinstance(document, dict):
        return document.get(field, default)
    return getattr(document, field, default)


def _decimal(value, label="数量"):
    return nonnegative(value, label)


def _quantity(value, label="数量"):
    raw = _decimal(value, label)
    rounded = raw.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)
    if raw != rounded:
        frappe.throw(f"{label}最多填写六位小数")
    return rounded


def _datetime(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def employee_time_details(logs):
    """Read closed individual intervals; native group output is not an employee share."""
    details = {}
    for row in logs or []:
        employee = _get(row, "employee")
        if not employee:
            continue
        person = details.setdefault(employee, {"hours": Decimal(0), "complete": True,
                                               "overlapping": False, "intervals": []})
        start, end = _get(row, "from_time"), _get(row, "to_time")
        if not start or not end:
            person["complete"] = False
            continue
        start, end = _datetime(start), _datetime(end)
        if end < start:
            frappe.throw("报工开始时间不能晚于结束时间")
        person["intervals"].append((start, end))
        person["hours"] += Decimal(str((end - start).total_seconds())) / Decimal(3600)
    for person in details.values():
        latest_end = None
        for start, end in sorted(person.pop("intervals")):
            if latest_end and start < latest_end:
                person["overlapping"] = True
            latest_end = max(latest_end, end) if latest_end else end
        person["hours"] = person["hours"].quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)
    return details


def source_snapshot(card, work):
    """Stable identity and time snapshots also detect edits after source approval."""
    identity = {
        "company": card.company, "job_card": card.name, "work_order": card.work_order,
        "flow_order": _get(work, "jf_flow_order") or None, "operation": card.operation,
        "operation_id": _get(card, "operation_id") or None,
        "item_code": _get(card, "finished_good") or card.production_item,
        "stock_uom": card.stock_uom, "work_date": str(card.posting_date),
        "currency": frappe.db.get_value("Company", card.company, "default_currency"),
        "source_completed_qty": str(_quantity(card.total_completed_qty)),
    }
    logs = [{field: str(_get(row, field) or "") for field in
             ("name", "employee", "from_time", "to_time", "time_in_mins", "completed_qty", "operation")}
            for row in card.time_logs or []]
    sub_operations = [{field: str(_get(row, field) or "") for field in
                       ("name", "sub_operation", "completed_qty", "completed_time", "status")}
                      for row in _get(card, "sub_operations", []) or []]
    payload = {"identity": identity, "logs": sorted(logs, key=lambda row: row["name"]),
               "sub_operations": sorted(sub_operations, key=lambda row: row["name"]),
               "corrective": bool(_get(card, "is_corrective_job_card")), "docstatus": card.docstatus}
    identity["source_signature"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return identity


def load_source(job_card, *, lock=False):
    card = frappe.get_doc("Job Card", job_card, for_update=lock)
    card.check_permission("read")
    if card.docstatus != 1:
        frappe.throw("工资分配需要已提交的报工单")
    if _get(card, "is_corrective_job_card"):
        frappe.throw("纠正报工的计件薪资口径尚未确定，请使用正常报工单分配")
    if not card.work_order:
        frappe.throw("工资分配需要关联生产工单的报工单")
    work = frappe.get_doc("Work Order", card.work_order)
    work.check_permission("read")
    if work.company != card.company or work.docstatus != 1:
        frappe.throw("报工来源必须是同公司已提交的生产工单")
    operation_row = next((row for row in work.operations
                          if row.name == _get(card, "operation_id")), None)
    if not operation_row or operation_row.operation != card.operation:
        frappe.throw("报工单的工序行与生产工单不一致")
    flow_name = _get(work, "jf_flow_order")
    if flow_name:
        flow = frappe.get_doc("Jifeng Flow Order", flow_name)
        flow.check_permission("read")
        if flow.company != card.company:
            frappe.throw("流程单公司与报工公司不一致")
    item = frappe.get_doc("Item", _get(card, "finished_good") or card.production_item)
    item.check_permission("read")
    if not card.stock_uom or item.stock_uom != card.stock_uom:
        frappe.throw("报工数量单位与来源商品库存单位不一致")
    if _quantity(card.total_completed_qty) <= 0:
        frappe.throw("没有已完成数量的报工单不能分配计件份额")
    return card, work, source_snapshot(card, work), employee_time_details(card.time_logs)


def _active_allocations(job_card, exclude=None):
    return frappe.db.sql(
        """SELECT name, docstatus FROM `tabJifeng Wage Allocation`
           WHERE job_card = %s AND docstatus < 2 AND name != %s FOR UPDATE""",
        (job_card, exclude or ""), as_dict=True,
    )


def _active_wages(job_card, exclude=None):
    return frappe.db.sql(
        """SELECT name, quantity, hours, wage_allocation, wage_allocation_employee, docstatus
           FROM `tabJifeng Wage Entry` WHERE job_card = %s AND docstatus < 2
           AND name != %s FOR UPDATE""", (job_card, exclude or ""), as_dict=True,
    )


def _bind_source(allocation, snapshot):
    for field in SOURCE_FIELDS:
        current, expected = _get(allocation, field), snapshot[field]
        if field == "source_completed_qty":
            matches = current in (None, "") or _quantity(current) == _quantity(expected)
        else:
            matches = current in (None, "") or str(current or "") == str(expected or "")
        if not matches:
            frappe.throw("分配来源已变化或被修改，请重新核对报工单后建立分配")
        setattr(allocation, field, expected)


def validate_allocation(allocation, *, approved_history=None):
    _, _, snapshot, people = load_source(allocation.job_card, lock=True)
    _bind_source(allocation, snapshot)
    if _active_allocations(allocation.job_card, _get(allocation, "name")):
        frappe.throw("该报工单已有未取消的工资分配，不能重复分配")
    if any(_quantity(row.quantity) > 0 and not row.wage_allocation
           for row in _active_wages(allocation.job_card)):
        frappe.throw("该报工单已有独立计件记录，请先取消或删除后再统一分配")
    from frappe.utils import getdate

    if getdate(allocation.payroll_date) < getdate(allocation.work_date):
        frappe.throw("计薪日期不能早于报工日期")
    component = frappe.get_doc("Salary Component", allocation.salary_component)
    component.check_permission("read")
    if component.type != "Earning" or component.disabled:
        frappe.throw("请选择启用的收入类工资项目")
    before = getattr(allocation, "get_doc_before_save", lambda: None)()
    history = approved_history or before
    from jifeng_erp.wage_rate_references import guard_approved_allocation_quotes

    guard_approved_allocation_quotes(allocation, history)
    approved_rows = {row.name: row for row in history.employees} if (
        history and history.docstatus == 1 and allocation.docstatus == 1
        and history.company == allocation.company) else {}
    seen, total_qty, total_amount = set(), Decimal(0), Decimal(0)
    for row in allocation.employees or []:
        if not row.employee or row.employee in seen:
            frappe.throw("每位员工在同一分配单中只能填写一行")
        seen.add(row.employee)
        if row.employee not in people:
            frappe.throw("分配员工必须在来源报工单的时间记录中")
        employee = frappe.get_doc("Employee", row.employee)
        employee.check_permission("read")
        if employee.company != allocation.company or employee.status != "Active":
            frappe.throw("分配员工必须是当前公司的在职员工")
        row.employee_name = employee.employee_name
        row.quantity = _quantity(row.quantity, "计件份额")
        if row.quantity and _get(row, "piece_rate") in (None, ""):
            frappe.throw("有计件份额的员工需要由主管填写计件工价")
        row.piece_rate = _decimal(_get(row, "piece_rate"), "计件工价")
        from jifeng_erp.wage_rate_references import validate_allocation_reference

        validate_allocation_reference(allocation, row, approved_history=history)
        prior = approved_rows.get(row.name)
        approved_row = prior if (prior and prior.employee == row.employee
                                 and _decimal(prior.quantity) == row.quantity
                                 and _decimal(prior.piece_rate) == row.piece_rate) else None
        bind_piece_worker(row, allocation.company, row.employee, row.quantity, row.piece_rate,
                          historical=approved_row)
        person = people[row.employee]
        row.hours = person["hours"]
        row.source_hours_complete = int(person["complete"] and not person["overlapping"])
        if _get(row, "include_time_pay"):
            if not row.source_hours_complete:
                frappe.throw("计时工资需要该员工完整且不重叠的报工起止时间")
            if _get(row, "hourly_rate") in (None, ""):
                frappe.throw("勾选计时工资后需要由主管填写计时单价")
        row.wage_hours = row.hours if _get(row, "include_time_pay") else Decimal(0)
        row.hourly_rate = _decimal(_get(row, "hourly_rate"), "计时单价")
        row.extra_amount = _decimal(_get(row, "extra_amount"), "附加费")
        row.piece_amount, row.time_amount, row.total_amount = wage_amounts(
            row.quantity, row.piece_rate, row.wage_hours, row.hourly_rate, row.extra_amount)
        total_qty += row.quantity
        total_amount += row.total_amount
    allocation.allocated_qty = total_qty
    allocation.total_amount = total_amount
    if not allocation.employees:
        frappe.throw("请填写员工计件份额")
    if allocation.docstatus == 1 and total_qty != _quantity(allocation.source_completed_qty):
        frappe.throw("各人计件份额合计必须等于来源报工单的已完成数量")


def wage_values(allocation, row):
    values = {field: _get(allocation, field) for field in
              ("company", "job_card", "work_order", "flow_order", "operation", "work_date",
               "payroll_date", "salary_component", "stock_uom")}
    values.update({field: _get(row, field) for field in
                   ("employee", "quantity", "piece_rate", "piece_rate_reference", "piece_rate_reference_version", "hourly_rate", "extra_amount",
                    "piece_amount", "time_amount", "total_amount")})
    values.update(hours=_get(row, "wage_hours", 0), wage_allocation=allocation.name,
                  wage_allocation_employee=row.name)
    return values


def _same_wage_values(wage, expected):
    for field in WAGE_FIELDS:
        actual = _get(wage, field)
        value = expected[field]
        if field in NUMERIC_WAGE_FIELDS:
            equal = _decimal(actual) == _decimal(value)
        else:
            equal = str(actual or "") == str(value or "")
        if not equal:
            frappe.throw("计薪记录必须与已审核工资分配的员工、份额、工价、工时、金额和来源一致")


def validate_allocated_wage(wage):
    allocation = frappe.get_doc(ALLOCATION, wage.wage_allocation)
    allocation.check_permission("read")
    source_name = allocation.job_card
    _, _, snapshot, _ = load_source(source_name, lock=True)
    allocation = frappe.get_doc(ALLOCATION, wage.wage_allocation, for_update=True)
    allocation.check_permission("read")
    if allocation.job_card != source_name:
        frappe.throw("工资分配来源已变化，请重新读取分配")
    if allocation.docstatus != 1:
        frappe.throw("计薪记录需要已审核的工资分配")
    row = next((row for row in allocation.employees
                if row.name == _get(wage, "wage_allocation_employee")), None)
    if not row:
        frappe.throw("计薪记录缺少准确的工资分配员工行")
    _bind_source(allocation, snapshot)
    _same_wage_values(wage, wage_values(allocation, row))
    if _decimal(row.total_amount) <= 0:
        frappe.throw("零金额分配行不需要生成计薪记录")
    if frappe.db.sql(
        """SELECT name FROM `tabJifeng Wage Entry` WHERE wage_allocation = %s
           AND wage_allocation_employee = %s AND docstatus < 2 AND name != %s FOR UPDATE""",
        (allocation.name, row.name, _get(wage, "name") or ""),
    ):
        frappe.throw("该工资分配员工行已有有效计薪记录，不能重复生成")


def validate_wage_source(wage):
    """Keep the existing single-person path, with a locked completion allowance."""
    before_save = getattr(wage, "get_doc_before_save", lambda: None)()
    if before_save and _get(before_save, "wage_allocation") and (
        _get(wage, "wage_allocation") != before_save.wage_allocation
        or _get(wage, "wage_allocation_employee") != before_save.wage_allocation_employee
    ):
        frappe.throw("分配生成的计薪记录不能移除或替换工资分配来源")
    if _get(wage, "wage_allocation"):
        validate_allocated_wage(wage)
        return
    if _get(wage, "wage_allocation_employee"):
        frappe.throw("计薪记录缺少工资分配来源")
    if not _get(wage, "job_card"):
        return
    card = frappe.get_doc("Job Card", wage.job_card, for_update=True)
    card.check_permission("read")
    # Timing-only manual records retain their existing path; this rule protects piece shares.
    current = _quantity(wage.quantity, "计件数量")
    if not current:
        return
    people = {row.employee for row in card.time_logs if row.employee}
    if len(people) > 1:
        frappe.throw("多人报工的计件工资请先填写并审核工资分配")
    if _get(card, "is_corrective_job_card"):
        frappe.throw("纠正报工的计件薪资口径尚未确定")
    if _active_allocations(card.name):
        frappe.throw("该报工单已有工资分配，请使用分配生成的计薪记录")
    used = sum((_quantity(row.quantity) for row in _active_wages(card.name, _get(wage, "name"))), Decimal(0))
    if used + current > _quantity(card.total_completed_qty):
        frappe.throw("该报工单的有效计件数量合计不能超过已完成数量")


def generate_wages(allocation):
    allocation.check_permission("read")
    if allocation.docstatus != 1:
        frappe.throw("请先审核工资分配")
    approved = frappe.get_doc(ALLOCATION, allocation.name, for_update=True)
    approved.check_permission("read")
    if approved.docstatus != 1:
        frappe.throw("请先审核工资分配")
    validate_allocation(allocation, approved_history=approved)
    names = []
    for row in allocation.employees:
        if _decimal(row.total_amount) <= 0:
            continue
        existing = frappe.db.sql(
            """SELECT name FROM `tabJifeng Wage Entry` WHERE wage_allocation = %s
               AND wage_allocation_employee = %s AND docstatus < 2 FOR UPDATE""",
            (allocation.name, row.name), as_dict=True,
        )
        if len(existing) > 1:
            frappe.throw("该员工分配行存在重复计薪记录，请先核对")
        if existing:
            wage = frappe.get_doc("Jifeng Wage Entry", existing[0].name)
            wage.check_permission("read")
            _same_wage_values(wage, wage_values(allocation, row))
        else:
            wage = frappe.get_doc({"doctype": "Jifeng Wage Entry", **wage_values(allocation, row),
                                   "notes": "来源于已审核的员工工资分配"}).insert()
        row.wage_entry = wage.name
        row.db_update()
        names.append(wage.name)
    return names


def validate_allocation_cancel(allocation):
    load_source(allocation.job_card, lock=True)
    active = frappe.db.sql(
        """SELECT name FROM `tabJifeng Wage Entry` WHERE wage_allocation = %s
           AND docstatus < 2 FOR UPDATE""", (allocation.name,),
    )
    if active:
        frappe.throw("请先按工资条及附加薪资的原生顺序取消计薪记录，或删除计薪草稿，再取消分配")


def guard_job_card_allocation(doc, method=None):
    """Submitted allocations hold their source while wages may still be drafts."""
    original = frappe.get_doc("Job Card", doc.name, for_update=True)
    rows = _active_allocations(doc.name)
    approved = [row for row in rows if row.docstatus == 1]
    approved_wages = [row for row in _active_wages(doc.name) if row.docstatus == 1]
    if not approved and not approved_wages:
        return
    if method == "before_cancel":
        frappe.throw("报工单已有审核工资或分配，请先取消工资及分配")
    work = frappe.get_doc("Work Order", original.work_order)
    if source_snapshot(doc, work) != source_snapshot(original, work):
        frappe.throw("报工单已有审核工资或分配，不能修改计薪来源；请先取消工资及分配")


@frappe.whitelist()
def get_allocation_source(job_card):
    frappe.only_for(["HR User", "HR Manager", "System Manager"])
    _, _, snapshot, people = load_source(job_card)
    employees = []
    for name, person in people.items():
        employee = frappe.get_doc("Employee", name)
        employee.check_permission("read")
        employees.append({"employee": name, "employee_name": employee.employee_name,
                          "hours": float(person["hours"]), "source_hours_complete": int(
                              person["complete"] and not person["overlapping"])})
    return {**snapshot, "employees": employees}
