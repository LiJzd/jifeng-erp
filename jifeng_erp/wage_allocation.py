"""Supervisor-approved employee shares of one native operation completion."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe

from jifeng_erp.payroll import nonnegative, wage_amounts
from jifeng_erp.piece_workers import bind_piece_worker


ALLOCATION = "Jifeng Wage Allocation"
QUANTITY_STEP = Decimal("0.000001")
PRODUCTION_QUANTITY_FIELDS = ("for_quantity", "total_completed_qty", "process_loss_qty", "pending_qty")
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


def source_snapshot(card, work, *, _work_source=None):
    """Stable identity and time snapshots also detect edits after source approval."""
    # A source-edit guard signs an unsaved intent. Authorize and close the
    # saved Card graph separately rather than treating that intent as DB fact.
    proof = _work_source or resolve_wage_work_source(work)
    if proof:
        if not isinstance(proof, _ChildWageSource):
            frappe.throw("子装配计薪需要服务器完整来源证明")
        proof.reader.source("Job Card", card.name)
    identity = {
        "company": card.company, "job_card": card.name, "work_order": card.work_order,
        "flow_order": proof.flow_order if proof else _get(work, "jf_flow_order") or None, "operation": card.operation,
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
    if proof:
        payload["plan_subassembly"] = proof.signature
        payload["child_quantities"] = {field: str(_decimal(_get(card, field), "子报工数量"))
                                       for field in PRODUCTION_QUANTITY_FIELDS}
        proof.finish()
    identity["source_signature"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return identity


def _source_fields(doctype, fields, *, parenttype=None):
    from jifeng_erp.flow_quantity import check_quantity_fields

    try:
        check_quantity_fields(doctype, fields, parenttype=parenttype)
    except frappe.PermissionError:
        frappe.throw("没有完整读取报工计薪来源字段的权限", frappe.PermissionError)


def child_wage_source_contract():
    """Exact pure subassembly helper inputs, without stock-ledger permission."""
    from jifeng_erp.production_loss import PARENTS, TABLES
    kinds = ("Work Order", "Production Plan", "Jifeng Flow Order", "BOM", "Item", "Warehouse", "Sales Order")
    fields = {kind: set(PARENTS[kind]) for kind in kinds}
    tables = {kind: tuple((field, child, set(names)) for field, child, names in TABLES.get(kind, ()))
              for kind in kinds}
    tables["Work Order"] = tuple((field, child, names | {"source_warehouse"}
                                 if field == "required_items" else names)
                                for field, child, names in tables["Work Order"])
    fields["Company"] = {"name", "modified", "default_currency"}
    identity = {"name", "modified", "docstatus"}
    child = identity | {"parent", "parenttype", "parentfield", "idx"}
    fields["Job Card"] = identity | {"company", "work_order", "operation", "operation_id", "production_item",
        "finished_good", "stock_uom", "posting_date", "is_corrective_job_card", "track_semi_finished_goods",
        "is_subcontracted", *PRODUCTION_QUANTITY_FIELDS}
    tables["Job Card"] = (
        ("time_logs", "Job Card Time Log", child | {"employee", "from_time", "to_time", "time_in_mins", "completed_qty", "operation"}),
        ("sub_operations", "Job Card Operation", child | {"sub_operation", "completed_qty", "completed_time", "status"}),
    )
    return fields, tables


def _projection(document, fields):
    quantities = {"qty", "quantity", "required_qty", "stock_qty", "planned_qty", "completed_qty",
                  "time_in_mins", "completed_time", *PRODUCTION_QUANTITY_FIELDS}
    return tuple((field, str(_decimal(_get(document, field), "计薪来源数量").normalize()) if field in quantities
                  else _datetime(_get(document, field)).isoformat() if field in ("from_time", "to_time")
                       and _get(document, field) else str(_get(document, field) or "")) for field in fields)


WORK_ORIGIN = ("name", "company", "production_item", "stock_uom", "bom_no", "qty", "jf_flow_order",
    "production_plan", "production_plan_item", "production_plan_sub_assembly_item", "sales_order", "sales_order_item",
    "wip_warehouse", "fg_warehouse", "track_semi_finished_goods")
PLAN_PARENT_ORIGIN = ("name", "parent", "parenttype", "parentfield", "item_code", "jf_flow_order", "bom_no",
    "planned_qty", "stock_uom", "sales_order", "sales_order_item", "item_reference", "material_request",
    "material_request_item", "product_bundle_item")
PLAN_CHILD_ORIGIN = ("name", "parent", "parenttype", "parentfield", "production_item", "parent_item_code",
    "production_plan_item", "bom_no", "bom_level", "type_of_manufacturing", "qty", "uom", "stock_uom",
    "sales_order", "sales_order_item", "fg_warehouse")


def _work_origin(work):
    return (_projection(work, WORK_ORIGIN),
        tuple(sorted(_projection(row, ("name", "item_code", "required_qty", "stock_uom", "source_warehouse"))
                     for row in _get(work, "required_items", []) or [])),
        tuple(sorted(_projection(row, ("name", "operation")) for row in _get(work, "operations", []) or [])))


def _card_origin(card):
    """Business values, including physical completed/lost/pending quantities."""
    fields, tables = child_wage_source_contract()
    quantities = [_get(card, field) for field in PRODUCTION_QUANTITY_FIELDS]
    if any(value in (None, "") for value in quantities):
        frappe.throw("子装配报工的原生数量来源不完整，不能计薪")
    identity = _projection(card, sorted(fields["Job Card"] - {"modified"}))
    rows = tuple((field, tuple(sorted(_projection(row, sorted(names - {"modified"}))
                  for row in _get(card, field, []) or []))) for field, _, names in tables["Job Card"])
    return identity, rows


class _ChildWageSource:
    def __init__(self, work, scope, reader):
        self.work, self.scope, self.reader = work, scope, reader
        self.flow_order = scope["flow"].name
        # Freeze business origins only. Native completion/transferred counters,
        # status and modified timestamps remain legitimate progress updates.
        from jifeng_erp.flow_wage_cost_sources import _freeze
        parents = tuple(_projection(scope[key], fields) for key, fields in (
            ("plan", ("name", "company", "combine_sub_items")), ("row", PLAN_CHILD_ORIGIN),
            ("parent_row", PLAN_PARENT_ORIGIN), ("flow", ("name", "company", "item_code", "stock_uom", "bom",
                                                        "sales_order", "sales_order_item"))))
        boms = tuple((_projection(doc, ("name", "company", "item", "uom", "quantity")),
            tuple(sorted(_projection(row, ("name", "parent", "parenttype", "parentfield", "item_code",
                "bom_no", "stock_uom", "stock_qty")) for row in doc.get("items") or [])))
            for (kind, _), (doc, _) in sorted(reader.docs.items()) if kind == "BOM")
        self.signature = _freeze((_work_origin(work), parents, boms))

    def finish(self):
        from jifeng_erp.flow_wage_cost_sources import _freeze
        if any(_freeze(doc) != state for doc, state in self.reader.docs.values()):
            frappe.throw("子装配计薪来源在本次读取中已变化，请重新核对", frappe.PermissionError)
        self.reader.finish()


def resolve_wage_work_source(work, *, card=None, lock=False):
    """Read one exact child WO's source with a closed source/permission graph."""
    if not _get(work, "production_plan_sub_assembly_item"):
        return None
    if _get(frappe, "conf") and _get(frappe.conf, "read_from_replica"):
        frappe.throw("子装配计薪必须核对当前原始来源，不能从延迟副本读取")
    from jifeng_erp.production_loss import _authorized_read, read_work_flow_source
    from jifeng_erp.flow_wage_cost_sources import _Read
    fields, tables = child_wage_source_contract()
    loader = ((lambda kind, name: _authorized_read(kind, name) if kind != "Company"
               else frappe.get_doc(kind, name, for_update=True)) if lock else None)
    reader = _Read(fields=fields, tables=tables, document_loader=loader)
    current = reader.source("Work Order", work.name)
    if current.docstatus != 1:
        frappe.throw("子装配计薪必须来自已提交的原子工单")
    if any((_get(work, field) or "") != (_get(current, field) or "") for field in
           ("production_plan", "production_plan_item", "production_plan_sub_assembly_item")):
        frappe.throw("子装配计薪工单的准确计划来源已变化，请重新读取")
    scope = read_work_flow_source(current, read=reader.source)
    reader.source("Company", current.company)
    if card:
        current_card = reader.source("Job Card", card.name)
        if _card_origin(current_card) != _card_origin(card):
            frappe.throw("子装配报工的数量、单位、工序或人员时间来源已变化，请重新读取")
    if card and (card.work_order != current.name or card.company != current.company
            or card.production_item != current.production_item or card.stock_uom != current.stock_uom
            or _get(card, "finished_good") and card.finished_good != current.production_item
            or card.docstatus != 1 or _get(card, "is_corrective_job_card")
            or _get(card, "track_semi_finished_goods") or _get(card, "is_subcontracted")
            or len([row for row in current.operations if row.name == _get(card, "operation_id")
                    and row.operation == card.operation]) != 1):
        frappe.throw("子装配计薪必须来自普通原子工单的准确商品、库存单位和工序")
    proof = _ChildWageSource(current, scope, reader)
    proof.finish()
    return proof


def load_source(job_card, *, lock=False, _with_work_source=False):
    card = frappe.get_doc("Job Card", job_card, for_update=lock)
    card.check_permission("read")
    _source_fields("Job Card", (
        "company", "work_order", "operation", "operation_id", "production_item", "finished_good",
        "stock_uom", "posting_date", "total_completed_qty", "is_corrective_job_card", "time_logs", "sub_operations",
    ))
    for table_name, childtype, fields in (
        ("time_logs", "Job Card Time Log", ("employee", "from_time", "to_time", "time_in_mins", "completed_qty", "operation")),
        ("sub_operations", "Job Card Operation", ("sub_operation", "completed_qty", "completed_time", "status")),
    ):
        table = frappe.get_meta("Job Card").get_field(table_name)
        if not table or table.fieldtype != "Table" or table.options != childtype:
            frappe.throw("报工计薪来源明细表定义不一致，请核对原生来源")
        _source_fields(childtype, fields, parenttype="Job Card")
    if card.docstatus != 1:
        frappe.throw("工资分配需要已提交的报工单")
    if _get(card, "is_corrective_job_card"):
        frappe.throw("纠正报工的计件薪资口径尚未确定，请使用正常报工单分配")
    if not card.work_order:
        frappe.throw("工资分配需要关联生产工单的报工单")
    work = frappe.get_doc("Work Order", card.work_order)
    work_source = resolve_wage_work_source(work, card=card, lock=lock)
    if work_source:
        work = work_source.work
    work.check_permission("read")
    _source_fields("Work Order", ("company", "jf_flow_order", "operations"))
    table = frappe.get_meta("Work Order").get_field("operations")
    if not table or table.fieldtype != "Table" or table.options != "Work Order Operation":
        frappe.throw("报工计薪来源工序表定义不一致，请核对原生来源")
    _source_fields("Work Order Operation", ("operation",), parenttype="Work Order")
    if work.company != card.company or work.docstatus != 1:
        frappe.throw("报工来源必须是同公司已提交的生产工单")
    operation_row = next((row for row in work.operations
                          if row.name == _get(card, "operation_id")), None)
    if not operation_row or operation_row.operation != card.operation:
        frappe.throw("报工单的工序行与生产工单不一致")
    flow_name = work_source.flow_order if work_source else _get(work, "jf_flow_order")
    if flow_name:
        flow = frappe.get_doc("Jifeng Flow Order", flow_name)
        flow.check_permission("read")
        _source_fields("Jifeng Flow Order", ("company",))
        if flow.company != card.company:
            frappe.throw("流程单公司与报工公司不一致")
    item = frappe.get_doc("Item", _get(card, "finished_good") or card.production_item)
    item.check_permission("read")
    _source_fields("Item", ("stock_uom",))
    company = frappe.get_doc("Company", card.company)
    company.check_permission("read")
    _source_fields("Company", ("default_currency",))
    if not card.stock_uom or item.stock_uom != card.stock_uom:
        frappe.throw("报工数量单位与来源商品库存单位不一致")
    if _quantity(card.total_completed_qty) <= 0:
        frappe.throw("没有已完成数量的报工单不能分配计件份额")
    result = (card, work, source_snapshot(card, work, _work_source=work_source), employee_time_details(card.time_logs))
    if work_source:
        work_source.finish()
    return (*result, work_source) if _with_work_source else result


def production_quantities(card):
    """Read-only native quantities; loss and pending output are never piece shares."""
    _source_fields("Job Card", PRODUCTION_QUANTITY_FIELDS + ("stock_uom",))
    values = {}
    for field in PRODUCTION_QUANTITY_FIELDS:
        value = _get(card, field)
        if value in (None, ""):
            frappe.throw("报工数量来源不完整，请核对原生报工单；缺失数量不能按零计")
        # This is a display of native quantities, not a new wage precision rule.
        values[field] = str(_decimal(value, "报工数量"))
    if not _get(card, "stock_uom"):
        frappe.throw("报工数量来源缺少库存单位，请核对原生报工单")
    return {**values, "stock_uom": card.stock_uom}


def _locked_wage_rows(query, values):
    try:
        return frappe.db.sql(query, values, as_dict=True)
    except (frappe.QueryTimeoutError, frappe.QueryDeadlockError) as exc:
        # Native allocation/wage saves may already own their parent lock before
        # asking for the Job Card. Do not wait in the reverse direction here.
        # Preserve DB exception types so batch operations roll the request back.
        raise type(exc)("计薪来源正在被其他工资操作使用，本次未保存；请核实后重新操作") from None


def _active_allocations(job_card, exclude=None):
    return _locked_wage_rows(
        """SELECT name, docstatus, source_signature FROM `tabJifeng Wage Allocation`
           WHERE job_card = %s AND docstatus < 2 AND name != %s ORDER BY name FOR UPDATE NOWAIT""",
        (job_card, exclude or ""),
    )


def _active_wages(job_card, exclude=None):
    return _locked_wage_rows(
        """SELECT name, quantity, hours, wage_allocation, wage_allocation_employee, docstatus
           FROM `tabJifeng Wage Entry` WHERE job_card = %s AND docstatus < 2
           AND name != %s ORDER BY name FOR UPDATE NOWAIT""", (job_card, exclude or ""),
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
    _, _, snapshot, people, work_source = load_source(allocation.job_card, lock=True, _with_work_source=True)
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
    if work_source:
        work_source.finish()


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
    _, _, snapshot, _, work_source = load_source(source_name, lock=True, _with_work_source=True)
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
    if work_source:
        work_source.finish()


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


def _approved_job_card_source(doc):
    """Lock the source before native callbacks can prewrite its rows or totals."""
    if not _get(doc, "name") or getattr(doc, "is_new", lambda: False)():
        return None
    original = frappe.get_doc("Job Card", doc.name, for_update=True)
    if original.docstatus != 1:
        return None
    approved = [row for row in _active_allocations(doc.name) if row.docstatus == 1]
    wages = [row for row in _active_wages(doc.name) if row.docstatus == 1]
    if not approved and not wages:
        return None
    work = frappe.get_doc("Work Order", original.work_order) if original.work_order else None
    snapshot = source_snapshot(original, work)
    if any(_get(row, "source_signature") != snapshot["source_signature"] for row in approved):
        frappe.throw("报工单已有审核工资或分配，不能修改计薪来源；请先取消工资及分配")
    return _job_card_source_state(original, work), work


def _job_card_source_state(card, work):
    """Compare business values without changing the persisted signature format.

    Native timer kwargs can hold 5 while the saved Float holds 5.0, or ISO
    datetimes while the DB returns datetime objects. These are the same source.
    The approved signature is still checked against the original locked DB Doc.
    """
    identity = source_snapshot(card, work)
    identity.pop("source_signature")

    def number(value):
        try:
            return Decimal(str(value or 0))
        except (InvalidOperation, ValueError):
            return str(value or "")

    def time(value):
        return _datetime(value) if value else None

    logs = sorted(((_get(row, "name") or "", _get(row, "employee") or "",
                    time(_get(row, "from_time")), time(_get(row, "to_time")),
                    number(_get(row, "time_in_mins")), number(_get(row, "completed_qty")),
                    _get(row, "operation") or "") for row in card.time_logs or []), key=lambda row: row[0])
    operations = sorted(((_get(row, "name") or "", _get(row, "sub_operation") or "",
                          number(_get(row, "completed_qty")), number(_get(row, "completed_time")),
                          _get(row, "status") or "") for row in _get(card, "sub_operations", []) or []), key=lambda row: row[0])
    state = identity, logs, operations, bool(_get(card, "is_corrective_job_card")), card.docstatus
    if _get(work, "production_plan_sub_assembly_item"):
        return (*state, _card_origin(card))
    return state


def _assert_job_card_source(doc, before):
    if before and _job_card_source_state(doc, before[1]) != before[0]:
        frappe.throw("报工单已有审核工资或分配，不能修改计薪来源；请先取消工资及分配")


class WageSourceJobCardMixin:
    """Preserve actual hours and completion through native prewrite callbacks.

    Native methods share the request transaction and do not commit. A changed
    source raises before request success, rolling back child/totals prewrites.
    """

    def add_time_logs(self, **kwargs):
        before = _approved_job_card_source(self)
        result = super().add_time_logs(**kwargs)
        _assert_job_card_source(self, before)
        return result

    def add_time_log(self, args):
        # The older timer endpoint uses row.db_set before native doc.save too.
        before = _approved_job_card_source(self)
        result = super().add_time_log(args)
        _assert_job_card_source(self, before)
        return result

    def validate_time_logs(self, save=False):
        before = _approved_job_card_source(self) if save else None
        result = super().validate_time_logs(save=save)
        _assert_job_card_source(self, before)
        return result


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
    original_snapshot = source_snapshot(original, work)
    # Native add_time_logs can db_update child rows and db_set completion before
    # save reaches this hook. The approved allocation's immutable signature is
    # the source anchor even when a fresh DB read already contains those writes.
    if (any(not _get(row, "source_signature") or _get(row, "source_signature") != original_snapshot["source_signature"]
            for row in approved) or _job_card_source_state(doc, work) != _job_card_source_state(original, work)):
        frappe.throw("报工单已有审核工资或分配，不能修改计薪来源；请先取消工资及分配")


def _approved_work_wages(work_order):
    # Internal existence locks only: a stock/planning user need not gain HR
    # read access to be prevented from editing an approved wage's origin.
    # Do not expose hidden employee, allocation or wage identities.
    for kind in (ALLOCATION, "Jifeng Wage Entry"):
        if _locked_wage_rows(f"SELECT name FROM `tab{kind}` WHERE work_order=%s AND docstatus=1 "
                             "ORDER BY name FOR UPDATE NOWAIT", (work_order,)):
            return True
    return False


def _plan_wage_origins(plan):
    parents = {row.name: row for row in plan.get("po_items") or []}
    children = plan.get("sub_assembly_items") or []
    if len(parents) != len(plan.get("po_items") or []) or len({row.name for row in children}) != len(children):
        frappe.throw("计薪来源生产计划明细身份不唯一，请核对原单")
    return {row.name: (_projection(plan, ("name", "company", "combine_sub_items")),
                      _projection(row, PLAN_CHILD_ORIGIN),
                      _projection(parents.get(row.get("production_plan_item")) or {}, PLAN_PARENT_ORIGIN))
            for row in children}


def guard_subassembly_wage_sources(doc, method=None):
    """Freeze approved child wage business sources at WO/Plan edits themselves.

    Native manufactured/transferred/produced counters and statuses remain free
    to progress. Exact source locks are NOWAIT and acquired Flow -> Plan -> WO.
    Cancellation releases this guard only after wages AND allocations cancel.
    """
    if not doc.get("name") or getattr(doc, "is_new", lambda: False)():
        return
    kind = doc.get("doctype")
    if kind not in ("Work Order", "Production Plan"):
        frappe.throw("子装配计薪来源修改保护不支持此单据")
    original = frappe.get_doc(kind, doc.name)
    original.check_permission("read")
    cancelling = method == "before_cancel"
    if kind == "Work Order":
        # A saved draft has no approved native report/wage source yet. Its
        # normal planning edits retain the existing production validators.
        if original.docstatus != 1 or not original.get("production_plan_sub_assembly_item"):
            return
        if not cancelling and _work_origin(original) == _work_origin(doc):
            return
        proof = resolve_wage_work_source(original, lock=True)
        if _work_origin(original) != _work_origin(proof.work):
            frappe.throw("子装配计薪工单来源已变化，请重新读取")
        if _approved_work_wages(proof.work.name):
            frappe.throw("子装配工单已有审核工资或分配，不能修改或取消计薪来源；请先取消工资及分配")
        proof.finish()
        return
    before, after = _plan_wage_origins(original), _plan_wage_origins(doc)
    affected = sorted(set(before) if cancelling else
                      {name for name in set(before) | set(after) if before.get(name) != after.get(name)})
    if not affected:
        return
    from jifeng_erp.production_loss import _authorized_read
    from jifeng_erp.flow_wage_cost_sources import _Read
    fields, tables = child_wage_source_contract()
    # All root Flow locks precede this plan and its peer child WOs. Advisory
    # values are proved again against the current closed Plan source.
    flows = sorted({row.get("jf_flow_order") for row in original.get("po_items") or [] if row.get("jf_flow_order")})
    for flow in flows:
        _authorized_read("Jifeng Flow Order", flow)
    reader = _Read(fields=fields, tables=tables, document_loader=_authorized_read)
    current = reader.source("Production Plan", original.name)
    if _plan_wage_origins(current) != before:
        frappe.throw("子装配计薪生产计划来源已变化，请重新读取")
    rows = reader.records("Work Order", {"production_plan": current.name,
        "production_plan_sub_assembly_item": ["in", affected], "docstatus": ["in", [0, 1]]}, fields["Work Order"])
    # Acquire the entire affected WO range before reading individual docs.
    locked = _locked_wage_rows("SELECT name FROM `tabWork Order` WHERE production_plan=%s "
        "AND production_plan_sub_assembly_item IN %s AND docstatus<2 ORDER BY name FOR UPDATE NOWAIT",
        (current.name, tuple(affected)))
    if {row.get("name") for row in locked} != {row["name"] for row in rows}:
        frappe.throw("受影响的子装配工单范围已变化或无法完整读取，请重新核对", frappe.PermissionError)
    for row in rows:
        work = reader.source("Work Order", row["name"])
        if _approved_work_wages(work.name):
            # Prove the original child origin before imposing the wage guard.
            resolve_wage_work_source(work, lock=True).finish()
            frappe.throw("生产计划子装配已有审核工资或分配，不能修改或取消计薪来源；请先取消工资及分配")
    reader.finish()


@frappe.whitelist()
def get_allocation_source(job_card):
    frappe.only_for(["HR User", "HR Manager", "System Manager"])
    card, _, snapshot, people, work_source = load_source(job_card, _with_work_source=True)
    quantities = production_quantities(card)
    employees = []
    for name, person in people.items():
        employee = frappe.get_doc("Employee", name)
        employee.check_permission("read")
        _source_fields("Employee", ("employee_name",))
        employees.append({"employee": name, "employee_name": employee.employee_name,
                          "hours": float(person["hours"]), "source_hours_complete": int(
                              person["complete"] and not person["overlapping"])})
    if work_source:
        work_source.finish()
    return {**snapshot, "employees": employees, "production_quantities": quantities}
