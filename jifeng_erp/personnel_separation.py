"""Explicit Employee Separation finalization, preserving native boarding records.

Install the four jf_* fields and all three mixins/read hooks separately. This module
does not infer the last working day, settle payroll, or change global accounts.
Native Employee validation/save and native source update hooks remain active.
"""

from datetime import date, datetime
from copy import deepcopy
from decimal import Decimal
from functools import wraps
import hashlib
import json
from uuid import uuid4

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate, now_datetime, today

from jifeng_erp.personnel_changes import (
    JOB_FIELDS, WATCH_FIELDS, _field_permissions, _fields, _history,
    _permitted_employee_fields, _readable_fields, _value,
)


DOCTYPE = "Employee Separation"
SNAPSHOT = "jf_exit_snapshot"
STATUS = "jf_exit_status"
PENDING, FINALIZED, REVERSED = "待办理", "已办理", "已撤销"
EMPLOYEE_FIELDS = WATCH_FIELDS | JOB_FIELDS | {
    "ctc", "create_user_permission", "resignation_letter_date", "reason_for_leaving"}
SOURCE_READ_FIELDS = {"company", "employee_name", "department", "designation", "grade", "resignation_letter_date"}
SOURCE_INTENT_FIELDS = {"employee", "company", "project", "boarding_begins_on", "resignation_letter_date",
                        "jf_last_working_day", "jf_reason_for_leaving"}
ACTIVITY = "Employee Boarding Activity"
CHANGING_FIELDS = {"status", "relieving_date", "reason_for_leaving"}
PAYROLL_TYPES = ("Jifeng Wage Entry", "Additional Salary", "Salary Slip", "Salary Structure Assignment")
TAMPER = "离职办理状态和追溯快照只能由服务器生成，不能修改"
LEGACY = "离职单缺少可靠服务器追溯快照，请先人工核对迁移"
CONFLICT = "员工、人事历史、交接来源或工资记录已有后续变化，不能自动撤销离职"
TASK_ERROR = "离职交接必须为本来源的完整精确任务，且全部实际完成"
DENIED = "没有读取或办理该离职来源及所涉员工字段的权限"
_SERVER = object()


def _atomic(method):
    @wraps(method)
    def action(*args, **kwargs):
        checkpoint = "jf_exit_" + uuid4().hex
        queues = [(getattr(frappe.db, name), list(getattr(frappe.db, name)._functions))
                  for name in ("before_commit", "after_commit", "before_rollback", "after_rollback")]
        realtime_exists = hasattr(frappe.local, "_realtime_log")
        realtime_before = deepcopy(getattr(frappe.local, "_realtime_log", None))
        # frappe.flags is _dict: its __getattr__ returns None for missing keys,
        # so hasattr would falsely record a nonexistent saving stack as present.
        saving_exists = "currently_saving" in frappe.flags
        saving_before = deepcopy(frappe.flags.get("currently_saving"))
        frappe.db.savepoint(checkpoint)
        try:
            return method(*args, **kwargs)
        except Exception as original:
            # Native savepoint rollback does not handle callbacks. Run only our
            # rollback cleanup and restore every pre-existing queue unchanged.
            failures = []
            def cleanup(callback, stage):
                try:
                    callback()
                except Exception as error:
                    failures.append(stage + ":" + type(error).__name__)

            try:
                for callback in list(queues[2][0]._functions)[len(queues[2][1]):]:
                    cleanup(callback, "before_rollback")
                cleanup(lambda: frappe.db.rollback(save_point=checkpoint), "database_rollback")
                for callback in list(queues[3][0]._functions)[len(queues[3][1]):]:
                    cleanup(callback, "after_rollback")
            finally:
                for manager, previous in queues:
                    manager.reset()
                    for callback in previous:
                        manager.add(callback)
                if realtime_exists:
                    frappe.local._realtime_log = realtime_before
                elif hasattr(frappe.local, "_realtime_log"):
                    del frappe.local._realtime_log
                if saving_exists:
                    frappe.flags.currently_saving = saving_before
                else:
                    frappe.flags.pop("currently_saving", None)
            if failures:
                original.add_note("离职局部回滚清理失败（" + ",".join(failures) + "），调用方须完整回滚并核对")
            raise
    return action


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError("Unsupported separation audit value")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=_json_default).encode()).hexdigest()


def _employee_state(employee):
    return {"fields": _fields(employee, EMPLOYEE_FIELDS), "history_hash": _digest(_history(employee)),
            "modified": _value(employee.modified)}


def _intent(source):
    return {"name": source.name, "employee": source.employee, "company": source.company,
        "project": source.project, "boarding_begins_on": _value(source.boarding_begins_on),
        "resignation_letter_date": _value(source.resignation_letter_date),
        "last_working_day": _value(source.get("jf_last_working_day")),
        "reason": source.get("jf_reason_for_leaving") or "",
        "activities": [{"name": row.name, "task": row.task} for row in source.activities or []],
        "activities_hash": _activities_hash(source)}


def _activities_hash(source):
    # Source.save rewrites child timestamps/docstatus. Freeze identity/order and
    # all stored activity semantics, including future custom physical fields.
    fields = {field for field in frappe.get_meta(ACTIVITY).get_valid_columns() if not field.startswith("_")} - {
        "creation", "modified", "modified_by", "owner", "docstatus"}
    return _digest([{field: row.get(field) for field in sorted(fields)} for row in source.activities or []])


def _parse_snapshot(raw):
    def require(valid):
        if not valid:
            raise ValueError(LEGACY)

    data = json.loads(raw)
    require(isinstance(data, dict) and set(data) == {"version", "state", "source", "before", "after",
        "changed_fields", "project", "tasks", "payroll_hash", "finalized_by", "finalized_at", "reversal"})
    require(data["version"] == 1 and data["state"] in ("finalized", "reversed"))
    require(isinstance(data["source"], dict) and set(data["source"]) == {
        "name", "employee", "company", "project", "boarding_begins_on", "resignation_letter_date",
        "last_working_day", "reason", "activities", "activities_hash"})
    source = data["source"]
    require(all(isinstance(source[key], str) and source[key] for key in ("name", "employee", "company", "project")))
    require(isinstance(source["last_working_day"], str) and source["last_working_day"])
    require(isinstance(source["reason"], str))
    require(isinstance(data["source"]["activities"], list))
    require(all(isinstance(row, dict) and set(row) == {"name", "task"}
                for row in data["source"]["activities"]))
    activities = source["activities"]
    require(bool(activities) and all(isinstance(row[key], str) and row[key]
                                    for row in activities for key in ("name", "task")))
    require(len({row["name"] for row in activities}) == len(activities))
    require(len({row["task"] for row in activities}) == len(activities))
    def hex_hash(value):
        return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)

    require(hex_hash(source["activities_hash"]))
    require(all(source[key] is None or isinstance(source[key], str)
                for key in ("boarding_begins_on", "resignation_letter_date")))

    for state in (data["before"], data["after"]):
        require(isinstance(state, dict) and set(state) == {"fields", "history_hash", "modified"})
        require(isinstance(state["fields"], dict) and set(state["fields"]) == EMPLOYEE_FIELDS)
        require(all(value is None or isinstance(value, (str, int, float, bool)) for value in state["fields"].values()))
        require(hex_hash(state["history_hash"]))
        require(isinstance(state["modified"], str) and state["modified"])
        require(state["fields"]["company"] == source["company"])
    require(data["after"]["fields"]["status"] == "Left")
    require(data["before"]["fields"]["status"] == "Active")
    require(data["after"]["fields"]["relieving_date"] == source["last_working_day"])
    require(data["after"]["fields"]["reason_for_leaving"] == (source["reason"] or None))
    require(isinstance(data["changed_fields"], list) and all(isinstance(field, str) for field in data["changed_fields"]))
    require(len(set(data["changed_fields"])) == len(data["changed_fields"]))
    require(set(data["changed_fields"]).issubset(CHANGING_FIELDS))
    require(data["changed_fields"] == sorted(field for field in EMPLOYEE_FIELDS
        if data["before"]["fields"][field] != data["after"]["fields"][field]))
    require(data["before"]["history_hash"] == data["after"]["history_hash"])
    require(isinstance(data["project"], dict) and set(data["project"]) == {"name", "company", "status", "modified"})
    require(isinstance(data["tasks"], list) and all(isinstance(row, dict) and set(row) == {
        "activity", "task", "project", "company", "status", "completed_on", "modified"} for row in data["tasks"]))
    require(data["project"]["name"] == source["project"] and data["project"]["company"] == source["company"]
            and data["project"]["status"] == "Completed")
    require(isinstance(data["project"]["modified"], str) and data["project"]["modified"])
    require(len(data["tasks"]) == len(activities))
    require(all(isinstance(row["activity"], str) and isinstance(row["task"], str) for row in data["tasks"]))
    require(len({row["activity"] for row in data["tasks"]}) == len(activities))
    require(len({row["task"] for row in data["tasks"]}) == len(activities))
    require({row["activity"]: row["task"] for row in data["tasks"]} == {row["name"]: row["task"] for row in activities})
    require(all(row["project"] == source["project"] and row["company"] == source["company"]
                and row["status"] == "Completed" and isinstance(row["completed_on"], str) and row["completed_on"]
                and isinstance(row["modified"], str) and row["modified"]
                for row in data["tasks"]))
    require(hex_hash(data["payroll_hash"]))
    require(all(isinstance(data[key], str) and data[key] for key in ("finalized_by", "finalized_at")))
    if data["state"] == "finalized":
        require(data["reversal"] is None)
    else:
        require(isinstance(data["reversal"], dict) and set(data["reversal"]) == {"by", "at"})
        require(all(isinstance(data["reversal"][key], str) and data["reversal"][key] for key in ("by", "at")))
    return data


def _boarding_readable(source, snapshot, user):
    """Read guards never acquire locks or require historical tasks still complete."""
    if snapshot["source"]["project"] != source.project:
        return False
    project = frappe.get_doc("Project", source.project)
    if (project.company != source.company or not project.has_permission("read", user=user)
            or not _column_read("Project", {"company", "status"}, user)):
        return False
    for row in snapshot["tasks"]:
        task = frappe.get_doc("Task", row["task"])
        if (task.project != source.project or task.company != source.company
                or not task.has_permission("read", user=user)
                or not _column_read("Task", {"project", "company", "status", "completed_on"}, user)):
            return False
    return True


def _snapshot(source):
    try:
        data = _parse_snapshot(source.get(SNAPSHOT) or "null")
        if data["source"] != _intent(source):
            raise ValueError(LEGACY)
        expected = FINALIZED if data["state"] == "finalized" else REVERSED
        if source.get(STATUS) != expected:
            raise ValueError(LEGACY)
    except (KeyError, TypeError, ValueError):
        frappe.throw(LEGACY)
    return data


def _column_read(doctype, names, user=None):
    return set(names).issubset(get_permitted_fields(doctype, user=user, permission_type="read", ignore_virtual=True))


def _source_readable(user=None, snapshot=False):
    required = SOURCE_INTENT_FIELDS | ({SNAPSHOT, STATUS} if snapshot else set())
    if not _column_read(DOCTYPE, required, user):
        return False
    meta = frappe.get_meta(DOCTYPE)
    field = meta.get_field("activities")
    levels = set(meta.get_permlevel_access(permission_type="read", user=user))
    if 0 not in levels and frappe.share.get_shared(DOCTYPE, user, rights=["read"], limit=1):
        levels.add(0)
    return bool(field and field.fieldtype == "Table" and field.options == ACTIVITY
        and (field.permlevel or 0) in levels and {"name", "task"}.issubset(get_permitted_fields(
            ACTIVITY, parenttype=DOCTYPE, user=user, permission_type="read", ignore_virtual=True)))


def _source_permissions(source, write_state=False):
    if not _source_readable(snapshot=bool(source.get(SNAPSHOT))):
        frappe.throw(DENIED, frappe.PermissionError)
    if write_state and not {SNAPSHOT, STATUS}.issubset(get_permitted_fields(
            DOCTYPE, permission_type="write", ignore_virtual=True)):
        frappe.throw(DENIED, frappe.PermissionError)


def _boarding(source, completed=True, user=None, write=False):
    if not source.project or not source.activities:
        frappe.throw(TASK_ERROR)
    identities = [row.name for row in source.activities]
    tasks = [row.task for row in source.activities]
    if (not all(identities) or len(set(identities)) != len(identities)
            or not all(tasks) or len(set(tasks)) != len(tasks)):
        frappe.throw(TASK_ERROR)
    project = frappe.get_doc("Project", source.project, for_update=True)
    if not project.has_permission("read", user=user) or not _column_read("Project", {"company", "status"}, user):
        frappe.throw(DENIED, frappe.PermissionError)
    if write and (not project.has_permission("write", user=user) or "status" not in
            get_permitted_fields("Project", user=user, permission_type="write", ignore_virtual=True)):
        frappe.throw(DENIED, frappe.PermissionError)
    if project.company != source.company or (completed and project.status != "Completed"):
        frappe.throw(TASK_ERROR)
    # This project must belong only to this source, and every actual task must
    # have a precise activity owner. Internal probes never return hidden IDs.
    owners = frappe.get_all(DOCTYPE, filters={"project": source.project}, pluck="name")
    if set(owners) != {source.name} or frappe.db.exists("Employee Onboarding", {"project": source.project}):
        frappe.throw(TASK_ERROR)
    actual = frappe.get_all("Task", filters={"project": source.project}, pluck="name")
    if set(actual) != set(tasks):
        frappe.throw(TASK_ERROR)
    result = []
    for activity in sorted(source.activities, key=lambda row: row.task):
        task = frappe.get_doc("Task", activity.task, for_update=True)
        if not task.has_permission("read", user=user) or not _column_read(
                "Task", {"project", "company", "status", "completed_on"}, user):
            frappe.throw(DENIED, frappe.PermissionError)
        if write and (not task.has_permission("write", user=user) or "status" not in
                get_permitted_fields("Task", user=user, permission_type="write", ignore_virtual=True)):
            frappe.throw(DENIED, frappe.PermissionError)
        if task.project != source.project or task.company != source.company:
            frappe.throw(TASK_ERROR)
        if completed and (task.status != "Completed" or not task.completed_on or getdate(task.completed_on) > getdate(today())):
            frappe.throw(TASK_ERROR)
        result.append({"activity": activity.name, "task": task.name, "project": task.project, "company": task.company,
            "status": task.status, "completed_on": _value(task.completed_on), "modified": _value(task.modified)})
    return {"name": project.name, "company": project.company, "status": project.status,
            "modified": _value(project.modified)}, result


def _payroll_hash(employee):
    """Internal complete parent/child digest; no private amount or ID in snapshots."""
    result = {}
    for doctype in PAYROLL_TYPES:
        if not frappe.db.exists("DocType", doctype):
            continue
        parents = frappe.get_all(doctype, filters={"employee": employee}, fields=["*"],
                                 order_by="name", limit_page_length=0)
        children = {}
        names = [row.name for row in parents]
        for field in frappe.get_meta(doctype).get_table_fields():
            children[field.fieldname] = frappe.get_all(field.options, filters={"parenttype": doctype,
                "parentfield": field.fieldname, "parent": ["in", names]}, fields=["*"],
                order_by="name", limit_page_length=0) if names else []
        result[doctype] = {"parents": parents, "children": children}
    return _digest(result)


def _unapproved_wages(employee, last_day):
    if not frappe.db.exists("DocType", "Jifeng Wage Entry"):
        return
    rows = frappe.get_all("Jifeng Wage Entry", filters={"employee": employee, "docstatus": 0},
                         fields=["work_date", "payroll_date"], limit_page_length=0)
    if any(not (row.work_date or row.payroll_date) or getdate(row.work_date or row.payroll_date) <= last_day for row in rows):
        frappe.throw("请先审核或处理最后工作日及以前的未审核计薪记录；本动作不代表工资已结清")


def _locked(name):
    if not isinstance(name, str) or not name.strip():
        frappe.throw("离职来源名称必须为非空字符串")
    source = frappe.get_doc(DOCTYPE, name, for_update=True)
    source.check_permission("read")
    source.check_permission("write")
    source.check_permission("submit")  # Native update_after_submit also requires this right.
    _source_permissions(source, write_state=True)
    source._guard_exit_fields()
    if source.docstatus != 1:
        frappe.throw("离职办理必须使用已提交的正式离职来源")
    employee = frappe.get_doc("Employee", source.employee, for_update=True)
    employee.check_permission("read")
    employee.check_permission("write")
    _field_permissions(EMPLOYEE_FIELDS | {"internal_work_history"})
    if employee.company != source.company:
        frappe.throw("离职来源公司与实际员工公司不一致")
    frappe.get_doc("Company", source.company).check_permission("select")
    if not isinstance(employee, SeparationEmployeeMixin):
        frappe.throw("请先安装保留全局账号状态的员工离职扩展")
    return source, employee


def _save_employee(employee):
    employee.flags.jf_exit_account_guard = _SERVER
    try:
        employee.save()
    finally:
        employee.flags.pop("jf_exit_account_guard", None)
    employee.reload()


def _save_source(source, snapshot, status):
    source.flags.jf_exit_server_save = _SERVER
    try:
        source.set(SNAPSHOT, json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=_json_default))
        source.set(STATUS, status)
        source.save()  # Native on_update_after_submit runs; all tasks already exist.
    finally:
        source.flags.pop("jf_exit_server_save", None)
    source.reload()
    if _snapshot(source) != snapshot:
        frappe.throw(TAMPER)


def _unchanged(source, employee, snapshot):
    project, tasks = _boarding(source)
    if (_employee_state(employee) != snapshot["after"] or project != snapshot["project"]
            or tasks != snapshot["tasks"] or _payroll_hash(employee.name) != snapshot["payroll_hash"]):
        frappe.throw(CONFLICT)


@frappe.whitelist()
@_atomic
def finalize_separation(name):
    source, employee = _locked(name)
    if source.get(SNAPSHOT):
        snapshot = _snapshot(source)
        if snapshot["state"] == "reversed":
            frappe.throw("本离职办理已撤销，不能复用旧追溯快照；再次办理请建立新的正式离职来源")
        _unchanged(source, employee, snapshot)
        return {"name": source.name, "exit_status": FINALIZED}
    if source.get(STATUS) not in (None, "", PENDING) or employee.status == "Left":
        frappe.throw(LEGACY)
    if employee.status != "Active":
        frappe.throw("本批离职办理仅支持实际在职员工，其他员工状态须先人工核对")
    if not source.get("jf_last_working_day"):
        frappe.throw("请填写真实最后工作日，交接开始日期不能替代最后工作日")
    last_day = getdate(source.jf_last_working_day)
    if last_day > getdate(today()) or not employee.date_of_joining or last_day < getdate(employee.date_of_joining):
        frappe.throw("最后工作日不能在未来或早于员工入职日期")
    _unapproved_wages(employee.name, last_day)
    project, tasks = _boarding(source)
    before, payroll = _employee_state(employee), _payroll_hash(employee.name)
    intent = _intent(source)
    employee.status, employee.relieving_date = "Left", source.jf_last_working_day
    employee.reason_for_leaving = source.get("jf_reason_for_leaving") or None
    _save_employee(employee)
    after = _employee_state(employee)
    changed = sorted(field for field in EMPLOYEE_FIELDS if before["fields"][field] != after["fields"][field])
    if (not set(changed).issubset(CHANGING_FIELDS) or before["history_hash"] != after["history_hash"]
            or _payroll_hash(employee.name) != payroll):
        frappe.throw("员工原生保存产生了额外人事或工资变化，请回滚后人工核对")
    snapshot = {"version": 1, "state": "finalized", "source": intent, "before": before, "after": after,
        "changed_fields": changed, "project": project, "tasks": tasks, "payroll_hash": payroll,
        "finalized_by": frappe.session.user, "finalized_at": _value(now_datetime()), "reversal": None}
    _save_source(source, snapshot, FINALIZED)
    _unchanged(source, employee, snapshot)
    return {"name": source.name, "exit_status": FINALIZED}


@frappe.whitelist()
@_atomic
def reverse_separation(name):
    source, employee = _locked(name)
    snapshot = _snapshot(source)
    if snapshot["state"] == "reversed":
        return {"name": source.name, "exit_status": REVERSED}
    _unchanged(source, employee, snapshot)
    expected = sorted(field for field in EMPLOYEE_FIELDS
                      if snapshot["before"]["fields"][field] != snapshot["after"]["fields"][field])
    if expected != snapshot["changed_fields"] or snapshot["before"]["history_hash"] != snapshot["after"]["history_hash"]:
        frappe.throw(LEGACY)
    for field in snapshot["changed_fields"]:
        employee.set(field, snapshot["before"]["fields"][field])
    _save_employee(employee)
    if (_fields(employee, EMPLOYEE_FIELDS) != snapshot["before"]["fields"]
            or _digest(_history(employee)) != snapshot["before"]["history_hash"]):
        frappe.throw("员工原生保存没有精确恢复本单离职变更，请回滚后人工核对")
    snapshot["state"] = "reversed"
    snapshot["reversal"] = {"by": frappe.session.user, "at": _value(now_datetime())}
    _save_source(source, snapshot, REVERSED)
    if _payroll_hash(employee.name) != snapshot["payroll_hash"]:
        frappe.throw(CONFLICT)
    return {"name": source.name, "exit_status": REVERSED}


class SeparationEmployeeMixin:
    def update_user(self):
        if self.flags.get("jf_exit_account_guard") is not _SERVER:
            return super().update_user()

    def update_user_permissions(self):
        if self.flags.get("jf_exit_account_guard") is not _SERVER:
            return super().update_user_permissions()

    def update_user_status(self):
        if self.flags.get("jf_exit_account_guard") is not _SERVER:
            return super().update_user_status()


class SeparationProjectMixin:
    def before_insert(self):
        context = getattr(frappe.local, "jf_exit_project_context", None)
        if (isinstance(context, dict) and context.get("token") is _SERVER and not context.get("used")
                and self.project_name == context["native_base"] and self.company == context["company"]):
            field = self.meta.get_field("project_name")
            maximum = field.length or 140
            source_name = context["source"]
            if not isinstance(source_name, str) or not source_name or len(source_name) > maximum:
                frappe.throw("离职来源名称超过交接项目标题长度，不能创建可靠来源项目")
            suffix = " [" + source_name + "]"
            self.project_name = (context["native_base"][:maximum - len(suffix)] + suffix
                                 if len(suffix) <= maximum else source_name)
            context["used"] = True
        method = getattr(super(), "before_insert", None)
        if method:
            method()


class EmployeeSeparationMixin:
    def _guard_employee_company(self):
        if not self.employee:
            return
        employee = frappe.get_doc("Employee", self.employee)
        employee.check_permission("read")
        if not SOURCE_READ_FIELDS.issubset(_permitted_employee_fields("read")):
            frappe.throw(DENIED, frappe.PermissionError)
        if employee.company != self.company:
            frappe.throw("离职来源公司与实际员工公司不一致")
        frappe.get_doc("Company", self.company).check_permission("select")

    def _guard_exit_fields(self):
        for name in (SNAPSHOT, STATUS):
            field = self.meta.get_field(name)
            if not (field and field.read_only and field.no_copy and field.allow_on_submit):
                frappe.throw("请先安装原生离职办理服务器状态与快照字段")
        field = self.meta.get_field(SNAPSHOT)
        if field.fieldtype != "Long Text" or not field.hidden or not field.print_hide:
            frappe.throw("请先安装只读隐藏的离职追溯快照字段")
        if self.flags.get("jf_exit_server_save") is _SERVER:
            _snapshot(self)
            return
        stored = {} if self.is_new() else frappe.db.get_value(DOCTYPE, self.name, [SNAPSHOT, STATUS, "project"], as_dict=True)
        stored = stored or {}
        if (self.get(SNAPSHOT) or "") != (stored.get(SNAPSHOT) or ""):
            frappe.throw(TAMPER)
        if (self.get(STATUS) or PENDING) != (stored.get(STATUS) or PENDING):
            frappe.throw(TAMPER)
        if self.is_new() and self.project:
            frappe.throw("交接项目只能由原生离职来源创建，不能预填")
        if not self.is_new() and self.project != stored.get("project"):
            frappe.throw("交接项目只能由原生离职来源创建，不能更换")
        saved = {} if self.is_new() else {row.name: row.task for row in frappe.get_all(
            "Employee Boarding Activity", filters={"parent": self.name, "parenttype": DOCTYPE,
                "parentfield": "activities"}, fields=["name", "task"], limit_page_length=0)}
        for row in self.activities or []:
            if row.task and row.task != saved.get(row.name):
                frappe.throw("交接任务只能由原生来源活动创建，不能伪造或更换")
        if self.get(SNAPSHOT):
            _snapshot(self)  # Completed/reversed source intent stays frozen.

    def before_validate(self):
        self._guard_exit_fields()
        self._guard_employee_company()
        method = getattr(super(), "before_validate", None)
        if method:
            method()

    def before_update_after_submit(self):
        self._guard_exit_fields()
        self._guard_employee_company()
        method = getattr(super(), "before_update_after_submit", None)
        if method:
            method()

    def on_submit(self):
        self._guard_employee_company()
        previous = getattr(frappe.local, "jf_exit_project_context", None)
        context = {"token": _SERVER, "source": self.name, "employee": self.employee, "company": self.company,
                   "native_base": frappe._(DOCTYPE) + " : " + self.employee, "used": False}
        frappe.local.jf_exit_project_context = context
        try:
            super().on_submit()
            if not context["used"]:
                frappe.throw("请先安装原生离职交接项目的服务器来源命名扩展")
        finally:
            frappe.local.jf_exit_project_context = previous

    def before_cancel(self):
        self._guard_exit_fields()
        self.check_permission("read")
        self.check_permission("cancel")
        _source_permissions(self)
        current = frappe.get_doc(DOCTYPE, self.name, for_update=True)
        if (current.docstatus != 1 or _intent(current) != _intent(self)
                or current.get(SNAPSHOT) != self.get(SNAPSHOT) or current.get(STATUS) != self.get(STATUS)):
            frappe.throw(CONFLICT)
        employee = frappe.get_doc("Employee", self.employee, for_update=True)
        employee.check_permission("read")
        if employee.company != self.company or "company" not in _permitted_employee_fields("read"):
            frappe.throw(DENIED, frappe.PermissionError)
        frappe.get_doc("Company", self.company).check_permission("select")
        snapshot = _snapshot(self) if self.get(SNAPSHOT) else None
        if snapshot and snapshot["state"] == "finalized":
            frappe.throw("请先撤销离职办理，再取消正式离职来源")
        if snapshot:
            if (not (EMPLOYEE_FIELDS | {"internal_work_history"}).issubset(_readable_fields(None))
                    or _fields(employee, EMPLOYEE_FIELDS) != snapshot["before"]["fields"]
                    or _digest(_history(employee)) != snapshot["before"]["history_hash"]
                    or _payroll_hash(employee.name) != snapshot["payroll_hash"]):
                frappe.throw(CONFLICT)
        if self.project:
            graph = _boarding(self, completed=False, write=True)
            if snapshot and graph != (snapshot["project"], snapshot["tasks"]):
                frappe.throw(CONFLICT)
        elif any(row.task for row in self.activities or []):
            frappe.throw(TASK_ERROR)
        else:
            graph = None
        method = getattr(super(), "before_cancel", None)
        if method:
            method()
        self.flags.jf_exit_cancel = (_SERVER, graph)

    def on_cancel(self):
        pending = self.flags.get("jf_exit_cancel")
        if not (pending and pending[0] is _SERVER):
            frappe.throw("离职来源取消缺少服务器锁定的精确交接核对")
        if self.project:
            if _boarding(self, completed=False, write=True) != pending[1]:
                frappe.throw(CONFLICT)
            # Native Task saves keep dependency/tree/assignment callbacks. No
            # force deletion, financial reversal, or set_project_status bulk RPC.
            for row in pending[1][1]:
                task = frappe.get_doc("Task", row["task"], for_update=True)
                task.status = "Cancelled"
                task.save()
            project = frappe.get_doc("Project", self.project, for_update=True)
            project.status = "Cancelled"
            project.save()
            graph = _boarding(self, completed=False, write=True)
            if graph[0]["status"] != "Cancelled" or any(row["status"] != "Cancelled" for row in graph[1]):
                frappe.throw("原生保存没有取消精确交接项目和任务，请回滚后核对")
        elif pending[1] is not None or any(row.task for row in self.activities or []):
            frappe.throw(TASK_ERROR)
        self.flags.pop("jf_exit_cancel", None)
        # Preserve source.project and every activity.task as permanent evidence.
        # Native HRMS may refresh boarding_status from the project's percentage;
        # that progress value is not the source docstatus or personnel exit state.


def separation_has_permission(doc, ptype, user=None, debug=False):
    if doc.doctype != DOCTYPE or ptype not in {"read", "select", "write", "print", "email", "export", "report"}:
        return True
    if not doc.get("employee"):
        return bool(doc.is_new() and not doc.get(SNAPSHOT))
    if not _source_readable(user=user, snapshot=bool(doc.get(SNAPSHOT))):
        return False
    if not SOURCE_READ_FIELDS.issubset(_permitted_employee_fields("read", user)):
        return False
    if doc.get(SNAPSHOT):
        if not (EMPLOYEE_FIELDS | {"internal_work_history"}).issubset(_readable_fields(user)):
            return False
        try:
            snapshot = _snapshot(doc)
            if not _boarding_readable(doc, snapshot, user):
                return False
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
            return False
    employee = frappe.get_doc("Employee", doc.employee)
    return bool(employee.has_permission("read", user=user))


def separation_query_conditions(user=None, doctype=None):
    if not SOURCE_READ_FIELDS.issubset(_permitted_employee_fields("read", user)):
        return "1=0"
    try:
        employees = frappe.get_list("Employee", fields=["name"], user=user, limit_page_length=0)
    except frappe.PermissionError:
        return "1=0"
    allowed = [row.name for row in employees if frappe.get_doc("Employee", row.name).has_permission("read", user=user)]
    if not allowed:
        return "1=0"
    rows = frappe.get_all(DOCTYPE, filters={"employee": ["in", allowed]}, fields=["name"], limit_page_length=0)
    names = [frappe.db.escape(row.name) for row in rows
             if separation_has_permission(frappe.get_doc(DOCTYPE, row.name), "read", user=user)]
    return f"`tab{DOCTYPE}`.`name` in ({','.join(names)})" if names else "1=0"
