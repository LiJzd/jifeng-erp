"""Native wage drafts from the classic date/person/operation entry workflow."""

import hashlib
import json
import uuid

import frappe
from frappe.utils import getdate
from jifeng_erp.payroll import nonnegative


WAGE = "Jifeng Wage Entry"
REQUEST_TOKEN = object()
RESTORE_TOKEN = object()
INPUT_FIELDS = {"request_id", "company", "employee", "payroll_date", "salary_component", "rows"}
ROW_FIELDS = {"work_date", "operation", "job_card", "source_modified", "work_order_modified",
              "quantity", "stock_uom", "piece_rate", "hours", "hourly_rate", "extra_amount", "notes"}
EDIT_FIELDS = ("company", "employee", "operation", "work_date", "payroll_date", "salary_component",
               "quantity", "stock_uom", "piece_rate", "hours", "hourly_rate", "extra_amount", "notes")
SOURCE_FIELDS = ("company", "operation", "operation_id", "work_order", "stock_uom", "posting_date", "is_corrective_job_card")
SOURCE_TABLES = {("Job Card", "time_logs"): "Job Card Time Log",
                 ("Work Order", "operations"): "Work Order Operation"}


def _request_id(value):
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        frappe.throw("录入请求标识无效，请重新打开工资录入")
    if parsed.version != 4 or str(parsed) != str(value):
        frappe.throw("录入请求标识无效，请重新打开工资录入")
    return str(parsed)


def _fields(document, names, permission="read"):
    if frappe.session.user == "Administrator":
        return
    permitted = set(document.meta.get_permitted_fieldnames(permission_type=permission))
    if not set(names).issubset(permitted):
        frappe.throw("没有读取或填写本次工资所需字段的权限", frappe.PermissionError)


def _child_fields(document, table, names):
    if frappe.session.user == "Administrator":
        return
    # Native permitted value fields omit Table. Check the parent Table's
    # actual permission level separately, then retain child field checks.
    field = document.meta.get_field(table)
    levels = set(document.meta.get_permlevel_access(permission_type="read"))
    if 0 not in levels and frappe.share.get_shared(
            document.doctype, frappe.session.user, rights=["read"], limit=1):
        levels.add(0)
    if not (field and field.fieldtype == "Table"
            and field.options == SOURCE_TABLES.get((document.doctype, table))
            and (field.permlevel or 0) in levels):
        frappe.throw("没有读取本次工序来源明细的权限", frappe.PermissionError)
    child = frappe.get_meta(field.options)
    permitted = set(child.get_permitted_fieldnames(parenttype=document.doctype, permission_type="read"))
    if not set(names).issubset(permitted):
        frappe.throw("没有读取本次工序来源明细的权限", frappe.PermissionError)


def _person(company, employee, *, lock=False, active=True):
    frappe.get_doc("Company", company).check_permission("read")
    person = frappe.get_doc("Employee", employee, for_update=lock)
    person.check_permission("read")
    _fields(person, ("company", "status", "employee_name"))
    if active and (person.company != company or person.status != "Active"):
        frappe.throw("计薪员工必须是当前公司的在职员工")
    return person


def _source(job_card, company, employee, *, lock=False):
    card = frappe.get_doc("Job Card", job_card, for_update=lock)
    card.check_permission("read")
    _fields(card, SOURCE_FIELDS)
    _child_fields(card, "time_logs", ("employee",))
    if card.docstatus != 1 or card.company != company:
        frappe.throw("请选择当前公司已提交的工序报工单")
    if card.is_corrective_job_card:
        frappe.throw("纠正报工的薪资口径尚未确定，请使用正常报工单录入")
    if not any(row.employee == employee for row in card.time_logs):
        frappe.throw("所选工序报工单中没有这位员工的报工记录")
    work = None
    if card.work_order:
        work = frappe.get_doc("Work Order", card.work_order, for_update=lock)
        work.check_permission("read")
        _fields(work, ("company", "jf_flow_order"))
        _child_fields(work, "operations", ("operation",))
        if work.company != company or work.docstatus != 1:
            frappe.throw("来源生产工单必须是当前公司已提交的单据")
        if not any(row.name == card.operation_id and row.operation == card.operation
                   for row in work.operations):
            frappe.throw("报工单工序与来源工单不一致")
        if work.jf_flow_order:
            flow = frappe.get_doc("Jifeng Flow Order", work.jf_flow_order)
            flow.check_permission("read")
            _fields(flow, ("company",))
            if flow.company != company:
                frappe.throw("流程单公司与计薪公司不一致")
    frappe.get_doc("Operation", card.operation).check_permission("select")
    return card, work


@frappe.whitelist()
def get_entry_source(job_card, company, employee):
    """One explicitly selected source, never an inferred employee completion list."""
    if not frappe.has_permission(WAGE, "create"):
        frappe.throw("没有新增计薪记录的权限", frappe.PermissionError)
    _person(company, employee)
    card, work = _source(job_card, company, employee)
    return {"job_card": card.name, "source_modified": str(card.modified),
            "work_order_modified": str(work.modified) if work else "",
            "operation": card.operation, "stock_uom": card.stock_uom,
            "work_order": card.work_order or "", "flow_order": work.jf_flow_order if work else "",
            "source_date": str(card.posting_date)}


def _batch_rows(request_id, company, employee):
    query = {"workbench_request_key": ["like", request_id + ":%"]}
    # Existence is not absence merely because a later permission change hides
    # an earlier saved batch. Never expose names from this internal scan.
    all_names = {row.name for row in frappe.db.sql(
        """SELECT name FROM `tabJifeng Wage Entry`
           WHERE workbench_request_key LIKE %s FOR UPDATE""", (request_id + ":%",), as_dict=True)}
    listed = frappe.get_list(WAGE, filters=query,
                             fields=["name", "company", "employee", "workbench_request_key",
                                     "workbench_request_fingerprint", "docstatus"],
                             order_by="workbench_request_key asc", limit_page_length=21)
    if {row.name for row in listed} != all_names:
        frappe.throw("无法完整读取原工资请求，请恢复读取权限后核对保存结果", frappe.PermissionError)
    rows = []
    for row in listed:
        doc = frappe.get_doc(WAGE, row.name, for_update=True)
        doc.check_permission("read")
        _fields(doc, ("company", "employee", "workbench_request_key", "workbench_request_fingerprint"))
        if doc.company != company or doc.employee != employee:
            frappe.throw("录入请求与原有员工或公司不一致")
        rows.append({"name": doc.name, "docstatus": doc.docstatus,
                     "key": doc.workbench_request_key, "fingerprint": doc.workbench_request_fingerprint})
    deleted_keys = {snapshot["workbench_request_key"] for snapshot in _deleted_requests(request_id)}
    if not deleted_keys.issubset({row["key"] for row in rows}):
        frappe.throw("原工资请求含已删除的草稿，请由工资负责人核对原删除记录；不能按原请求重新录入")
    return rows


def _deleted_requests(request_id):
    """Inspect native tombstones internally without exposing their private data."""
    deleted = frappe.db.sql("""SELECT data FROM `tabDeleted Document`
        WHERE deleted_doctype = %s AND data LIKE %s FOR UPDATE""",
        (WAGE, "%" + request_id + ":%"), as_dict=True)
    snapshots = []
    for row in deleted:
        try:
            snapshot = json.loads(row.data)
        except (ValueError, TypeError):
            frappe.throw("原工资请求的删除记录需要核对，请联系工资负责人")
        if not isinstance(snapshot, dict):
            frappe.throw("原工资请求的删除记录需要核对，请联系工资负责人")
        if snapshot.get("doctype") == WAGE and str(snapshot.get("workbench_request_key") or "").startswith(request_id + ":"):
            snapshots.append(snapshot)
    return snapshots


@frappe.whitelist()
def batch_status(request_id, company, employee):
    """The employee lock waits for an earlier batch before judging an unknown result."""
    request_id = _request_id(request_id)
    _person(company, employee, lock=True, active=False)
    return {"wages": _batch_rows(request_id, company, employee)}


@frappe.whitelist(methods=["POST"])
def save_drafts(payload):
    if not frappe.has_permission(WAGE, "create"):
        frappe.throw("没有新增计薪记录的权限", frappe.PermissionError)
    payload = frappe.parse_json(payload)
    if not isinstance(payload, dict) or set(payload) != INPUT_FIELDS:
        frappe.throw("工资录入请求字段不完整或包含未支持字段")
    request_id = _request_id(payload["request_id"])
    rows = payload["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
        frappe.throw("每次请保存一至二十道工序")
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_FIELDS:
            frappe.throw("请完整填写每道工序及其来源核对信息")
        if not row["operation"] or not row["work_date"]:
            frappe.throw("每道录入需要工作日期和工序")
        if getdate(payload["payroll_date"]) < getdate(row["work_date"]):
            frappe.throw("计薪日期不能早于工作日期")
    sources = [row["job_card"] for row in rows if row["job_card"]]
    if len(sources) != len(set(sources)):
        frappe.throw("同一批录入中，每个工序报工单只能填写一次")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                           allow_nan=False).encode()).hexdigest()
    # This also serializes two POSTs for an identical request before the native
    # unique key and source locks. No commit belongs inside this API.
    _person(payload["company"], payload["employee"], lock=True)
    existing = _batch_rows(request_id, payload["company"], payload["employee"])
    if existing:
        expected_keys = [request_id + f":{index:03d}" for index in range(len(rows))]
        if [row["key"] for row in existing] != expected_keys or any(
                row["fingerprint"] != fingerprint for row in existing):
            frappe.throw("录入请求内容已变化，请先核对原先保存结果")
        return {"wages": existing, "reused": True}
    component = frappe.get_doc("Salary Component", payload["salary_component"])
    component.check_permission("read")
    _fields(component, ("type", "disabled"))
    if component.type != "Earning" or component.disabled:
        frappe.throw("请选择启用的收入类工资项目")
    resolved = {}
    for name in sorted(sources):
        resolved[name] = _source(name, payload["company"], payload["employee"], lock=True)
    prepared = []
    for index, row in enumerate(rows):
        values = {field: payload[field] for field in ("company", "employee", "payroll_date", "salary_component")}
        values.update({field: row[field] for field in EDIT_FIELDS if field in row})
        frappe.get_doc("Operation", row["operation"]).check_permission("select")
        if row["job_card"]:
            card, work = resolved[row["job_card"]]
            if str(card.modified) != row["source_modified"] or (
                    str(work.modified) if work else "") != row["work_order_modified"]:
                frappe.throw("工序来源已变化，请重新选择并核对该道报工单")
            if row["operation"] != card.operation or row["stock_uom"] != card.stock_uom:
                frappe.throw("工序和计件单位必须与所选报工单一致")
            values.update(job_card=card.name, work_order=card.work_order or None,
                          flow_order=work.jf_flow_order if work else None)
        elif nonnegative(row["quantity"], "计件数量") or nonnegative(row["piece_rate"], "计件工价"):
            frappe.throw("计件录入必须明确选择已提交的工序报工单")
        elif row["source_modified"] or row["work_order_modified"]:
            frappe.throw("没有报工单的计时录入不能携带来源核对信息")
        doc = frappe.get_doc({"doctype": WAGE, **values,
                              "workbench_request_key": request_id + f":{index:03d}",
                              "workbench_request_fingerprint": fingerprint})
        doc.check_permission("create")
        _fields(doc, (*EDIT_FIELDS, "job_card", "work_order", "flow_order"), permission="write")
        doc.flags.workbench_request_token = REQUEST_TOKEN
        prepared.append(doc)
    frappe.db.savepoint("jifeng_wage_workbench")
    try:
        # All original native controller validations, permission checks and
        # source quantity locks run inside one transaction, including the last
        # row. A failed row leaves no partial batch for the next attempt.
        for doc in prepared:
            doc.insert()
    except Exception:
        frappe.db.rollback(save_point="jifeng_wage_workbench")
        raise
    return {"wages": [{"name": doc.name, "docstatus": doc.docstatus,
                        "key": doc.workbench_request_key, "fingerprint": fingerprint}
                       for doc in prepared], "reused": False}


def guard_request_identity(doc):
    before = getattr(doc, "get_doc_before_save", lambda: None)()
    key = getattr(doc, "workbench_request_key", None) or ""
    fingerprint = getattr(doc, "workbench_request_fingerprint", None) or ""
    if not key and not fingerprint:
        # Blank hidden Data controls may submit an empty string. Native unique
        # indexes need NULL for unrelated ordinary wage drafts.
        doc.workbench_request_key = None
        doc.workbench_request_fingerprint = None
    if before:
        if key != (getattr(before, "workbench_request_key", None) or "") or fingerprint != (
                getattr(before, "workbench_request_fingerprint", None) or ""):
            frappe.throw("工资录入请求标识不能移除或替换")
    elif (key or fingerprint) and getattr(doc.flags, "workbench_request_token", None) is not REQUEST_TOKEN and (
            getattr(doc.flags, "workbench_restore_token", None) is not RESTORE_TOKEN):
        frappe.throw("工资录入请求标识只能由正式工资操作台生成")


def authorize_native_restore(doc):
    """Check the original identity before Frappe's format naming replaces name."""
    key = getattr(doc, "workbench_request_key", None) or ""
    if not key or getattr(doc.flags, "workbench_request_token", None) is REQUEST_TOKEN:
        return
    if not getattr(doc.flags, "from_restore", False):
        return  # validate reports a forged key through its normal guard.
    if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
        frappe.throw("只有工资原生恢复权限的负责人可以恢复原录入请求", frappe.PermissionError)
    if not frappe.has_permission("Deleted Document", "read"):
        frappe.throw("没有核对原工资删除记录的权限", frappe.PermissionError)
    doc.check_permission("read")
    identity = ("name", *EDIT_FIELDS, "job_card", "work_order", "flow_order",
                "workbench_request_key", "workbench_request_fingerprint")
    if not any(all((getattr(doc, field, None) or "") == (snapshot.get(field) or "") for field in identity)
               for snapshot in _deleted_requests(key.split(":", 1)[0])):
        frappe.throw("工资恢复必须与原删除单据的编号、请求和内容一致")
    doc.flags.workbench_restore_token = RESTORE_TOKEN
