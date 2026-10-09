"""Prepare one approved operation's wages for native monthly payroll.

Only the wages already linked by the approved allocation are processed. Native
submit and Additional Salary creation retain their validation and permissions;
no salary item is submitted and no payment is made here.
"""

import hashlib
import json
import re
from decimal import Decimal

import frappe

from jifeng_erp.archive_versions import same_version
from jifeng_erp.flow_quantity import check_quantity_fields
from jifeng_erp.payroll import nonnegative
from jifeng_erp.wage_allocation import (
    ALLOCATION, SOURCE_FIELDS, WAGE_FIELDS, _same_wage_values,
    load_source, production_quantities, resolve_wage_work_source, validate_allocation, wage_values,
)


WAGE = "Jifeng Wage Entry"
SALARY = "Additional Salary"
ROW_TYPE = "Jifeng Wage Allocation Employee"
ROW_FIELDS = ("name", "employee", "employee_name", "quantity", "piece_rate",
              "piece_amount", "hours", "include_time_pay", "wage_hours", "hourly_rate",
              "time_amount", "extra_amount", "total_amount", "wage_entry",
              "piece_rate_reference", "piece_rate_reference_version")
SALARY_FIELDS = ("name", "modified", "docstatus", "employee", "company",
                 "salary_component", "currency", "amount", "payroll_date", "is_recurring",
                 "overwrite_salary_structure_amount", "ref_doctype", "ref_docname", "disabled")


def _invalid(message):
    frappe.throw(message, frappe.ValidationError)


def _fields(doctype, fields, *, parenttype=None):
    try:
        check_quantity_fields(doctype, fields, parenttype=parenttype)
    except frappe.PermissionError:
        frappe.throw("没有完整读取本工资分配及计薪来源字段的权限", frappe.PermissionError)


def _text(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value if value is not None else "")


def _money(value):
    return format(nonnegative(value, "工资金额"), "f")


def _sql(query, values):
    try:
        return frappe.db.sql(query, values, as_dict=True)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError) as exc:
        raise type(exc)("本工序工资正在更新，请重新读取状态后再办理") from None


def _doc(doctype, name, *, children=()):
    # Existing native saves can lock a wage or child before its source. Fail
    # without waiting rather than introduce the inverse source/child cycle.
    found = _sql(f"SELECT name FROM `tab{doctype}` WHERE name=%s FOR UPDATE NOWAIT", (name,))
    if len(found) != 1:
        _invalid("本工序工资来源缺失，请核对原单")
    for child, field in children:
        _sql(f"SELECT name FROM `tab{child}` WHERE parent=%s AND parenttype=%s "
             "AND parentfield=%s ORDER BY name FOR UPDATE NOWAIT", (name, doctype, field))
    doc = frappe.get_doc(doctype, name, for_update=True)
    doc.check_permission("read")
    return doc


def _version(allocation, supplied):
    if not same_version(allocation.modified, supplied):
        _invalid("工资分配版本已变化，请刷新后重新核对")


def _scope(name, version, *, _with_work_source=False):
    if not isinstance(name, str) or not name or name != name.strip():
        _invalid("请选择准确的已审核工资分配")
    # Permission before locking, then a current read of the exact same source.
    first = frappe.get_doc(ALLOCATION, name)
    first.check_permission("read")
    _fields(ALLOCATION, (*SOURCE_FIELDS, "modified", "docstatus", "payroll_date",
                         "salary_component", "allocated_qty", "total_amount", "employees"))
    _fields(ROW_TYPE, ROW_FIELDS, parenttype=ALLOCATION)
    table = frappe.get_meta(ALLOCATION).get_field("employees")
    if not table or table.fieldtype != "Table" or table.options != ROW_TYPE:
        _invalid("工资分配员工表定义不一致")
    card_name = first.job_card
    # Exact child origins lock their Flow/Plan before native WO/card scopes.
    # Existing ordinary sources retain their current scope and field checks.
    advisory_card = frappe.get_doc("Job Card", card_name)
    advisory_card.check_permission("read")
    work_source = (resolve_wage_work_source(frappe.get_doc("Work Order", advisory_card.work_order),
                  card=advisory_card, lock=True) if advisory_card.work_order else None)
    card = _doc("Job Card", card_name, children=(
        ("Job Card Time Log", "time_logs"), ("Job Card Operation", "sub_operations")))
    work = None
    if card.work_order:
        work = work_source.work if work_source else _doc("Work Order", card.work_order, children=(("Work Order Operation", "operations"),))
    allocation = _doc(ALLOCATION, name, children=((ROW_TYPE, "employees"),))
    _version(allocation, version)
    if allocation.docstatus != 1 or allocation.job_card != card_name:
        _invalid("仅可集中办理当前已审核且来源未变的工资分配")
    row_ids = [row.name for row in allocation.employees or []]
    if not row_ids or len(set(row_ids)) != len(row_ids) or any(not value for value in row_ids):
        _invalid("工资分配缺少准确且唯一的员工行")
    for employee in sorted({row.employee for row in allocation.employees}):
        _doc("Employee", employee)
    company = _doc("Company", allocation.company)
    if company.default_currency != allocation.currency:
        _invalid("分配币种与当前公司本位币不一致，请按原单核对")
    if (not work or work.company != allocation.company or work.docstatus != 1
            or ((work_source.flow_order if work_source else work.get("jf_flow_order")) or "") != (allocation.get("flow_order") or "")
            or not any(row.name == card.operation_id and row.operation == card.operation
                       for row in work.operations or [])):
        _invalid("分配的当前工单、流程或工序来源已变化，请按原单核对")
    flow_name = work_source.flow_order if work_source else work.get("jf_flow_order")
    if flow_name:
        flow = _doc("Jifeng Flow Order", flow_name)
        if flow.company != allocation.company:
            _invalid("分配的当前流程公司已变化，请按原单核对")
    item = _doc("Item", card.get("finished_good") or card.production_item)
    if item.stock_uom != card.stock_uom:
        _invalid("分配的当前商品库存单位已变化，请按原单核对")
    _doc("Salary Component", allocation.salary_component)
    # Validate a copy. Never fill missing historical fields or reprice the
    # already approved allocation as a side effect of reading/processing it.
    checked = frappe.get_doc(allocation.as_dict())
    validate_allocation(checked, approved_history=allocation)
    frozen_fields = (*SOURCE_FIELDS, "payroll_date", "salary_component", "allocated_qty", "total_amount")
    if any(_text(allocation.get(field)) != _text(checked.get(field))
           for field in frozen_fields if field not in ("source_completed_qty", "allocated_qty", "total_amount")):
        _invalid("已审核分配的来源不完整或已变化，请按原单核对")
    for field in ("source_completed_qty", "allocated_qty", "total_amount"):
        if nonnegative(allocation.get(field), field) != nonnegative(checked.get(field), field):
            _invalid("已审核分配的数量或金额已变化，请按原单核对")
    checked_rows = {row.name: row for row in checked.employees}
    for row in allocation.employees:
        expected = wage_values(allocation, row)
        _same_wage_values(frappe._dict(expected), wage_values(checked, checked_rows[row.name]))
    _, _, source, _ = load_source(card_name, lock=True)
    if work_source:
        work_source.finish()
    result = allocation, production_quantities(card), source
    return (*result, work_source) if _with_work_source else result


def _salaries(wage, currency):
    names = _sql("""SELECT name FROM `tabAdditional Salary`
        WHERE ref_doctype='Jifeng Wage Entry' AND ref_docname=%s
        ORDER BY name FOR UPDATE NOWAIT""", (wage.name,))
    result, active = [], []
    for record in names:
        salary = _doc(SALARY, record.name)
        _fields(SALARY, SALARY_FIELDS)
        if salary.docstatus not in (0, 1, 2):
            _invalid("工资附加项状态异常，请按原单核对")
        if salary.docstatus < 2:
            expected = (wage.employee, wage.company, wage.salary_component, currency,
                        _text(wage.payroll_date), nonnegative(wage.total_amount, "金额"))
            actual = (salary.employee, salary.company, salary.salary_component, salary.currency,
                      _text(salary.payroll_date), nonnegative(salary.amount, "金额"))
            if (wage.docstatus != 1 or expected != actual or salary.disabled
                    or salary.is_recurring or salary.overwrite_salary_structure_amount):
                _invalid("有效工资附加项与本分配计薪不一致，请按原单核对")
            active.append(salary)
        result.append(salary)
    if len(active) > 1:
        _invalid("本分配计薪存在重复有效工资附加项，请按原单核对")
    return result, active[0] if active else None


def _salary_candidate(wage, currency):
    # Use the same effective assignment rule as the existing single-wage
    # method. Lock/recheck its exact record before any wage submission.
    names = _sql("""SELECT name, from_date FROM `tabSalary Structure Assignment`
        WHERE employee=%s AND company=%s AND docstatus=1 AND from_date<=%s
        ORDER BY from_date DESC, name FOR UPDATE NOWAIT""",
        (wage.employee, wage.company, wage.payroll_date))
    if not names:
        _invalid("本分配员工在计薪日期前缺少已审核工资结构分配")
    if len(names) > 1 and _text(names[0].from_date) == _text(names[1].from_date):
        _invalid("员工最新工资结构分配日期不唯一，请按原单核对")
    assignment = _doc("Salary Structure Assignment", names[0].name)
    _fields("Salary Structure Assignment", ("name", "modified", "employee", "company",
                                             "docstatus", "from_date", "currency"))
    if (assignment.docstatus != 1 or assignment.employee != wage.employee
            or assignment.company != wage.company or assignment.currency != currency
            or _text(assignment.from_date) > _text(wage.payroll_date)):
        _invalid("工资结构分配的员工、公司、日期或币种与本分配不一致")
    salary = frappe.get_doc({"doctype": SALARY, "employee": wage.employee, "company": wage.company,
        "salary_component": wage.salary_component, "currency": currency, "amount": wage.total_amount,
        "payroll_date": wage.payroll_date, "is_recurring": 0, "overwrite_salary_structure_amount": 0,
        "ref_doctype": WAGE, "ref_docname": wage.name})
    return salary, assignment


def _collect(name, version, *, processing=False):
    allocation, quantities, source, work_source = _scope(name, version, _with_work_source=True)
    wages = _sql("""SELECT name FROM `tabJifeng Wage Entry` WHERE wage_allocation=%s
                    ORDER BY name FOR UPDATE NOWAIT""", (allocation.name,))
    rows = {row.name: row for row in allocation.employees}
    by_row, history, documents, salary_history = {}, {}, {}, {}
    _fields(WAGE, ("name", "modified", "docstatus", *WAGE_FIELDS))
    for record in wages:
        wage = _doc(WAGE, record.name)
        origin = wage.wage_allocation_employee
        if wage.wage_allocation != allocation.name or origin not in rows or wage.docstatus not in (0, 1, 2):
            _invalid("本分配计薪存在错误归属或状态，请按原单核对")
        history.setdefault(origin, []).append(wage)
        if wage.docstatus == 2:
            # A cancelled wage cannot still own an active salary item. Do not
            # hide historical anomalies behind a valid replacement wage.
            salary_history[wage.name] = _salaries(wage, allocation.currency)[0]
        if wage.docstatus < 2:
            if origin in by_row:
                _invalid("本分配员工行存在重复有效计薪，请按原单核对")
            by_row[origin] = wage
            documents[origin] = wage
    dto, manifest, assignments, candidates = [], [], {}, {}
    blocked, can_process = [], True
    for row in allocation.employees:
        total = nonnegative(row.total_amount, "金额")
        wage = by_row.get(row.name)
        if total == 0:
            if wage:
                _invalid("零金额员工行存在有效计薪，请按原单核对")
            dto.append({"allocation_employee": row.name, "employee": row.employee,
                "employee_name": row.employee_name, "quantity": _money(row.quantity),
                "piece_rate": _money(row.piece_rate), "piece_amount": _money(row.piece_amount),
                "time_amount": _money(row.time_amount), "extra_amount": _money(row.extra_amount),
                "total_amount": "0", "wage_entry": None, "wage_status": "零金额无需计薪",
                "additional_salary": None, "additional_salary_status": "无需生成", "cancelled_salaries": []})
            manifest.append({"row": row.name, "wages": [doc.as_dict() for doc in history.get(row.name, [])],
                "salaries": [salary.as_dict() for doc in history.get(row.name, [])
                             for salary in salary_history.get(doc.name, [])], "assignment": None})
            continue
        if not wage or row.wage_entry != wage.name:
            _invalid("分配员工行缺少准确生成的有效计薪记录；请按原分配核对，不自动重建")
        _same_wage_values(wage, wage_values(allocation, row))
        salaries, active = _salaries(wage, allocation.currency)
        salary_history[wage.name] = salaries
        canceled = [salary.name for salary in salaries if salary.docstatus == 2]
        if not active and canceled:
            blocked.append("有计薪记录仅剩已取消工资附加项，请按原逐单入口明确核对，不自动重发")
        elif not active:
            candidate, assignment = _salary_candidate(wage, allocation.currency)
            candidates[row.name], assignments[row.name] = candidate, assignment
            if not candidate.has_permission("create"):
                can_process = False
        if wage.docstatus == 0 and (not wage.has_permission("write") or not wage.has_permission("submit")):
            can_process = False
        dto.append({"allocation_employee": row.name, "employee": row.employee,
            "employee_name": row.employee_name, "quantity": _money(row.quantity),
            "piece_rate": _money(row.piece_rate), "piece_amount": _money(row.piece_amount),
            "time_amount": _money(row.time_amount), "extra_amount": _money(row.extra_amount),
            "total_amount": _money(total), "wage_entry": wage.name,
            "wage_status": "草稿待审核" if wage.docstatus == 0 else "已审核",
            "additional_salary": active.name if active else None,
            "additional_salary_status": ("草稿待工资负责人审核" if active.docstatus == 0 else "已审核") if active else
                ("已取消，请逐单核对" if canceled else "尚未生成"), "cancelled_salaries": canceled})
        manifest.append({"row": row.name, "wages": [doc.as_dict() for doc in history[row.name]],
                         "salaries": [salary.as_dict() for doc in history[row.name]
                                      for salary in salary_history.get(doc.name, [])],
                         "assignment": assignments[row.name].as_dict() if row.name in assignments else None})
    if not allocation.has_permission("write") or not allocation.has_permission("submit"):
        can_process = False
    result = {"allocation": allocation.name, "version": _text(allocation.modified),
        "company": allocation.company, "currency": allocation.currency,
        "source_completed_qty": _money(allocation.source_completed_qty),
        "allocated_qty": _money(allocation.allocated_qty), "total_amount": _money(allocation.total_amount),
        "production_quantities": quantities, "rows": dto, "can_process": can_process and not blocked,
        "blocked_reason": "；".join(dict.fromkeys(blocked))}
    proof = {"user": frappe.session.user, "allocation": allocation.as_dict(),
             "source": source, "quantities": quantities, "manifest": manifest}
    result["snapshot"] = hashlib.sha256(json.dumps(proof, default=_text, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if processing and not result["can_process"]:
        if blocked:
            _invalid(result["blocked_reason"])
        frappe.throw("没有完整审核本分配计薪或新增工资附加项的权限", frappe.PermissionError)
    if work_source:
        work_source.finish()
    return result, allocation, documents, candidates


@frappe.whitelist(methods=["GET"])
def inspect_allocation_processing(allocation, expected_version):
    """Current, complete read before explicit approval or after an unknown POST."""
    return _collect(allocation, expected_version)[0]


@frappe.whitelist(methods=["POST"])
def process_allocation_wages(allocation, expected_version, snapshot):
    if not isinstance(snapshot, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot):
        _invalid("请先读取本分配的完整办理明细后明确确认")
    before, parent, wages, candidates = _collect(allocation, expected_version, processing=True)
    if before["snapshot"] != snapshot:
        _invalid("人员、份额、工资或附加项状态已变化，请重新读取后确认")
    submitted, created = [], []
    for origin in sorted(wages):
        wage = wages[origin]
        if wage.docstatus == 0:
            wage.check_permission("write")
            wage.check_permission("submit")
            wage.submit()
            submitted.append(wage.name)
        if origin in candidates:
            candidates[origin].check_permission("create")
            created.append(wage.make_additional_salary())
    # Re-read exact membership, values, permissions and effective salaries.
    # Any native callback drift raises inside the same request transaction.
    after, _, _, _ = _collect(parent.name, expected_version)
    if (after["production_quantities"] != before["production_quantities"]
            or [(r["allocation_employee"], r["employee"], r["quantity"], r["piece_rate"], r["total_amount"], r["wage_entry"])
                for r in after["rows"]] !=
               [(r["allocation_employee"], r["employee"], r["quantity"], r["piece_rate"], r["total_amount"], r["wage_entry"])
                for r in before["rows"]]
            or any(row["wage_entry"] and (row["wage_status"] != "已审核" or not row["additional_salary"])
                   for row in after["rows"])):
        _invalid("原生办理后的来源或结果不一致，请核对原单；本次事务不保存")
    return {"status": "completed", "allocation": parent.name, "version": after["version"],
            "submitted_wages": submitted, "created_salaries": created,
            "already_completed": not submitted and not created, "current": after}
