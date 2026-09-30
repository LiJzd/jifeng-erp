"""Journaled persistent native separation fixture for an isolated QA site.

Call prepare/cleanup once from an initialized bench-env Python process, never
bench execute (its exception fallback can repeat mutations). Browser actions
must use the exact returned source. inspect reads only; record_ui/reconcile
write only the private journal. Cancelled sources/projects/tasks and the
inactive synthetic Employee are retained with native audit history.
"""

from copy import deepcopy
import json
from pathlib import Path
import re

import frappe
from frappe.utils import add_days, cint, now, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import (
    _callbacks, _digest, _employee_contents, _normal, _restore_callbacks, _tab_counts,
)
from jifeng_erp.personnel_separation_validation import (
    LAST_DAY, REASON, SNAPSHOT, SOURCE, STATUS, _activities_hash, _audit_digest,
    _payroll_contents, _source_graph, _state, _user_contents,
)


LOGIN_FIELDS = ("last_login", "last_active", "last_ip")
STAGES = {"finalized", "reversed", "cancelled"}
IMMUTABLE = ("fixture_type", "fixture_version", "prefix", "company", "browser_user",
             "created_at", "created_by", "employee", "source", "project", "tasks", "holiday_list", "holiday_assignment",
             "last_day", "reason", "records", "employee_before", "employee_modified_before", "source_intent",
             "graph_before", "protected_before", "payroll_before", "counts_before")


def _path(data):
    match = re.fullmatch(r"JF-SEP-BROWSER-([a-f0-9]{12})", data.get("prefix", ""))
    if not match or data.get("fixture_type") != "separation_browser" or data.get("fixture_version") != 1:
        raise ValueError("不是受支持的离职浏览器验收夹具")
    root = Path(frappe.get_site_path("private", "jifeng_qa")).resolve()
    path = root / ("separation-browser-" + match[1] + ".json")
    if data.get("artifact_path") and Path(data["artifact_path"]).resolve() != path:
        raise ValueError("离职验收日志路径与夹具身份不一致")
    return path


def _journal(data):
    path = _path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["artifact_path"], data["updated_at"] = str(path), now()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _load(data):
    if isinstance(data, str):
        data = json.loads(data)
    canonical = json.loads(_path(data).read_text(encoding="utf-8"))
    for field in IMMUTABLE:
        if data.get(field) != canonical.get(field):
            raise ValueError("调用参数与私有日志中的离职夹具身份或基线不一致: " + field)
    return canonical


def _accounts(browser_user):
    contents = _user_contents()
    login = {}
    for index, row in enumerate(contents["User"]):
        if row["name"] == browser_user:
            login = {field: _normal(row.get(field)) for field in LOGIN_FIELDS}
            row = row.copy()
            for field in LOGIN_FIELDS:
                row.pop(field, None)
            # Replacing only this copied row keeps the complete auth/child audit.
            contents["User"][index] = row
            break
    return {"hash": _digest(contents), "browser_login_metadata": login,
            "allowed_login_fields": list(LOGIN_FIELDS)}


def _protected(data, before=False):
    accounts = _accounts(data["browser_user"])
    return {"ledger": _ledger_snapshot(),
            "other_employees_hash": _digest(_employee_contents(exclude=None if before else data["employee"])),
            "accounts_hash": accounts["hash"],
            "browser_login_metadata": accounts["browser_login_metadata"]}


def _assert_protected(data):
    actual = _protected(data)
    expected = data["protected_before"]
    for field in ("ledger", "other_employees_hash", "accounts_hash"):
        assert actual[field] == expected[field], "离职夹具之外的库存/总账、员工或账户内容变化: " + field
    return actual


def _intent(source):
    return _normal({"name": source.name, "employee": source.employee, "company": source.company,
        "project": source.project, "amended_from": source.amended_from or None,
        "boarding_begins_on": source.boarding_begins_on,
        "resignation_letter_date": source.resignation_letter_date,
        "last_working_day": source.get(LAST_DAY), "reason": source.get(REASON) or "",
        "activities": [{"name": row.name, "task": row.task} for row in source.activities],
        "activities_hash": _activities_hash(source.name)})


def _graph(source):
    doc, project, tasks, activities = _source_graph(source)
    owners = {row["task"]: row["name"] for row in activities}
    return _normal({"project": {"name": project.name, "company": project.company,
                      "status": project.status, "modified": project.modified},
        "tasks": [{"activity": owners[task.name], "task": task.name, "project": task.project,
                   "company": task.company, "status": task.status, "completed_on": task.completed_on,
                   "modified": task.modified} for task in sorted(tasks, key=lambda row: row.name)]})


def _record(doc):
    identity_fields = {
        "Employee": ("first_name", "employee_number", "company", "user_id", "date_of_joining"),
        SOURCE: ("employee", "company", "project", "boarding_begins_on", "resignation_letter_date",
                 LAST_DAY, REASON, "notify_users_by_email", "amended_from"),
        "Project": ("project_name", "company"), "Task": ("subject", "project", "company"),
        "Holiday List": ("holiday_list_name", "from_date", "to_date", "holidays"),
        "Holiday List Assignment": ("applicable_for", "assigned_to", "holiday_list", "from_date", "employee_company"),
    }
    return _normal({"doctype": doc.doctype, "name": doc.name, "owner": doc.owner,
        "creation": doc.creation, "identity": {field: doc.get(field) for field in identity_fields[doc.doctype]}})


def _owned_state(data):
    result = []
    for record in data["records"]:
        row = {"doctype": record["doctype"], "name": record["name"],
               "exists": bool(frappe.db.exists(record["doctype"], record["name"]))}
        if row["exists"]:
            doc = frappe.get_doc(record["doctype"], record["name"])
            row.update(record=_record(doc), docstatus=cint(doc.docstatus), status=doc.get("status"),
                       modified=_normal(doc.modified))
            if doc.doctype == SOURCE:
                row.update(exit_status=doc.get(STATUS), snapshot=doc.get(SNAPSHOT),
                           activities=_intent(doc)["activities"], boarding_status=doc.boarding_status)
            elif doc.doctype == "Employee":
                row["employee_state"] = _state(doc.name)
            elif doc.doctype == "Task":
                row["completed_on"] = _normal(doc.completed_on)
        result.append(row)
    return result


def _validate_owned(data):
    expected = {("Employee", data["employee"]), (SOURCE, data["source"]),
                ("Project", data["project"]), *(("Task", name) for name in data["tasks"]),
                ("Holiday List", data["holiday_list"]), ("Holiday List Assignment", data["holiday_assignment"])}
    assert len(data["records"]) == len(expected) == 7, "离职夹具只允许五项业务对象及独立假日表、员工专属分配"
    assert {(row["doctype"], row["name"]) for row in data["records"]} == expected, "日志中的精确保留对象不一致"
    for record in data["records"]:
        assert frappe.db.exists(record["doctype"], record["name"]), "离职夹具保留对象缺失"
        assert _record(frappe.get_doc(record["doctype"], record["name"])) == record, "离职夹具所有权或业务绑定变化"
    source, project, tasks, activities = _source_graph(data["source"])
    assert source.project == data["project"] and {row.task for row in source.activities} == set(data["tasks"])
    assert len(activities) == 2 and _intent(source) == data["source_intent"], "离职来源的精确交接活动变化"
    employee = frappe.get_doc("Employee", data["employee"])
    assert employee.employee_number == data["prefix"] and employee.first_name == data["prefix"]
    assert not employee.user_id, "浏览器夹具不允许关联或创建员工账号"
    assert _audit_digest(_payroll_contents(employee.name)) == data["payroll_before"], "验收员工出现后续工资内容变化"
    assert not frappe.db.exists("Employee Onboarding", {"project": data["project"]})
    assert set(frappe.get_all(SOURCE, filters={"project": data["project"]}, pluck="name")) == {data["source"]}
    assignment = frappe.get_doc("Holiday List Assignment", data["holiday_assignment"])
    assert (assignment.applicable_for, assignment.assigned_to, assignment.holiday_list) == \
        ("Employee", data["employee"], data["holiday_list"]), "假日分配不是本夹具员工专属配置"
    assert cint(assignment.docstatus) == (2 if data["cleanup_complete"] else 1), "员工专属假日分配生命周期不一致"
    assert set(frappe.get_all("Holiday List Assignment", filters={"applicable_for": "Employee",
        "assigned_to": data["employee"]}, pluck="name")) == {data["holiday_assignment"]}, "验收员工出现日志外的假日配置"
    assert frappe.db.get_value("Holiday List", data["holiday_list"], "holiday_list_name") == data["prefix"] + "-HOL"
    return source, project, tasks


def _snapshot(data, source, state):
    payload = json.loads(source.get(SNAPSHOT) or "null")
    assert isinstance(payload, dict) and set(payload) == {"version", "state", "source", "before", "after",
        "changed_fields", "project", "tasks", "payroll_hash", "finalized_by", "finalized_at", "reversal"}, "离职服务器快照结构不正确"
    assert payload["version"] == 1 and payload["state"] == state
    assert payload["source"] == data["source_intent"]
    before = data["employee_before"]
    expected_after = deepcopy(before["fields"])
    expected_after.update(status="Left", relieving_date=data["last_day"], reason_for_leaving=data["reason"])
    assert payload["before"] == {"fields": before["fields"], "history_hash": _audit_digest(before["history"]),
                                "modified": data["employee_modified_before"]}
    assert payload["after"]["fields"] == expected_after
    assert payload["after"]["history_hash"] == _audit_digest(before["history"]) and payload["after"]["modified"]
    assert payload["changed_fields"] == ["reason_for_leaving", "relieving_date", "status"]
    assert payload["project"] == data["graph_before"]["project"] and payload["tasks"] == data["graph_before"]["tasks"]
    assert payload["payroll_hash"] == data["payroll_before"]
    assert payload["finalized_by"] == data["browser_user"] and payload["finalized_at"]
    if state == "finalized":
        assert payload["reversal"] is None
    else:
        assert isinstance(payload["reversal"], dict) and set(payload["reversal"]) == {"by", "at"}
        assert payload["reversal"]["by"] == data["browser_user"] and payload["reversal"]["at"]
    return payload


def _observe(data):
    source, project, tasks = _validate_owned(data)
    actual = _state(data["employee"])
    before = data["employee_before"]
    payload = None
    if source.get(SNAPSHOT):
        snapshot_state = json.loads(source.get(SNAPSHOT))["state"]
        payload = _snapshot(data, source, snapshot_state)
        assert source.get(STATUS) == ("已办理" if snapshot_state == "finalized" else "已撤销")
        if snapshot_state == "finalized":
            assert source.docstatus == 1 and not data["cleanup_complete"]
            assert actual["fields"] == payload["after"]["fields"] and actual["history"] == before["history"]
            assert _normal(frappe.db.get_value("Employee", data["employee"], "modified")) == payload["after"]["modified"]
            stage = "finalized"
        else:
            expected = deepcopy(before)
            if data["cleanup_complete"]:
                expected["fields"]["status"] = "Inactive"
            assert actual == expected, "撤销未精确恢复原有员工内容，或清理发生额外修改"
            stage = "cancelled" if source.docstatus == 2 else "reversed"
    else:
        assert source.get(STATUS) in (None, "", "待办理")
        expected = deepcopy(before)
        if data["cleanup_complete"]:
            expected["fields"]["status"] = "Inactive"
        assert actual == expected
        stage = "cancelled_pending" if source.docstatus == 2 else "ready"
    assert source.docstatus in (1, 2)
    if source.docstatus == 2:
        assert project.status == "Cancelled" and all(task.status == "Cancelled" for task in tasks)
        assert payload is None or payload["state"] == "reversed"
    else:
        assert _graph(source.name) == data["graph_before"], "交接项目或任务在准备之后变化"
        assert source.boarding_status == "Completed"
    protected = _assert_protected(data)
    return {"stage": stage, "employee_state": actual, "source_docstatus": cint(source.docstatus),
            "employee_exit_fields": {field: actual["fields"][field]
                                     for field in ("status", "relieving_date", "reason_for_leaving")},
            "exit_status": source.get(STATUS), "snapshot": payload, "graph": _graph(source.name),
            "protected": protected, "owned_state": _owned_state(data)}


def _begin(point):
    if getattr(frappe.db, "_disable_transaction_control", 0):
        frappe.throw("持久离职夹具不能在其他禁止提交的事务中运行")
    _begin_rollback_only(point)


def _commit(data, phase):
    data.update(commit_phase=phase, pending_state=_owned_state(data), phase=phase + "_commit_pending")
    _journal(data)
    assert frappe.db._disable_transaction_control == 1, "离职验收提交屏障状态不一致"
    frappe.db._disable_transaction_control -= 1
    try:
        frappe.db.commit()
    except Exception as exc:
        data["phase"] = phase + "_commit_unknown"
        _journal(data)
        exc.add_note("提交结果未知，先 inspect/reconcile，不能重复准备或清理: " + data["artifact_path"])
        raise


def prepare(company, browser_user="Administrator"):
    """Create one real submitted/completed native source, then commit once."""
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb" and frappe.db.exists("Company", company)
    assert frappe.db.get_value("User", browser_user, "enabled") == 1
    for doctype, permission in ((SOURCE, "write"), (SOURCE, "submit"), (SOURCE, "cancel"), ("Employee", "write")):
        assert frappe.has_permission(doctype, permission, user=browser_user), "指定浏览器用户没有原生操作权限"
    suffix = frappe.generate_hash(length=12)
    data = {"fixture_type": "separation_browser", "fixture_version": 1, "prefix": "JF-SEP-BROWSER-" + suffix,
        "company": company, "browser_user": browser_user, "created_at": now(), "created_by": frappe.session.user,
        "employee": None, "source": None, "project": None, "tasks": [], "records": [],
        "holiday_list": None, "holiday_assignment": None,
        "phase": "preparing", "cleanup_complete": False, "ui_stage_log": [],
        "last_day": add_days(today(), -3)}
    data["reason"] = data["prefix"] + " 合成离职浏览器验收"
    tables, data["counts_before"] = _tab_counts()
    for doctype in ("Employee", SOURCE, "Employee Boarding Activity", "Project", "Task", "Version", "Series",
                    "Holiday List", "Holiday List Assignment"):
        assert tables["tab" + doctype] == "InnoDB", "离职夹具写入对象必须支持回滚"
        for field in frappe.get_meta(doctype).get_table_fields():
            assert tables["tab" + field.options] == "InnoDB"
    data["protected_before"] = _protected(data, before=True)
    callbacks, original_test = _callbacks(), frappe.in_test
    point = "jf_sep_browser_prepare_" + suffix
    _journal(data)
    _begin(point)
    try:
        frappe.in_test = False
        gender = frappe.db.get_value("Gender", {}, "name")
        currency = frappe.db.get_value("Company", company, "default_currency")
        assert gender and currency
        employee = frappe.get_doc({"doctype": "Employee", "first_name": data["prefix"],
            "employee_number": data["prefix"], "company": company, "gender": gender,
            "date_of_birth": "1990-01-01", "date_of_joining": add_days(today(), -90),
            "status": "Active", "ctc": 0, "salary_currency": currency,
            "create_user_automatically": 0, "create_user_permission": 0, "user_id": None}).insert()
        data["employee"] = employee.name
        data["records"].append(_record(employee))
        _journal(data)
        data["employee_before"] = _state(employee.name)
        data["employee_modified_before"] = _normal(employee.modified)
        data["payroll_before"] = _audit_digest(_payroll_contents(employee.name))
        holiday = frappe.get_doc({"doctype": "Holiday List", "holiday_list_name": data["prefix"] + "-HOL",
            "from_date": add_days(employee.date_of_joining, -2), "to_date": add_days(today(), 30), "holidays": []}).insert()
        data["holiday_list"] = holiday.name
        data["records"].append(_record(holiday))
        _journal(data)
        assignment = frappe.get_doc({"doctype": "Holiday List Assignment", "holiday_list": holiday.name,
            "applicable_for": "Employee", "assigned_to": employee.name, "from_date": employee.date_of_joining}).insert()
        data["holiday_assignment"] = assignment.name
        data["records"].append(_record(assignment))
        _journal(data)
        assignment.submit()
        from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
        boarding_date = add_days(data["last_day"], -2)
        assert get_holiday_list_for_employee(employee.name, as_on=boarding_date) == holiday.name, \
            "原生交接假日解析未使用本夹具的员工专属分配"
        source = frappe.get_doc({"doctype": SOURCE, "employee": employee.name, "company": company,
            "boarding_begins_on": boarding_date,
            "resignation_letter_date": add_days(data["last_day"], -3),
            "notify_users_by_email": 0, LAST_DAY: data["last_day"], REASON: data["reason"],
            "activities": [{"activity_name": data["prefix"] + "-handover", "begin_on": 0, "duration": 0},
                           {"activity_name": data["prefix"] + "-clearance", "begin_on": 1, "duration": 0}]}).insert()
        data["source"] = source.name
        data["records"].append(_record(source))
        _journal(data)
        source.submit()
        source, project, tasks, _ = _source_graph(source.name)
        data["project"], data["tasks"] = project.name, sorted(task.name for task in tasks)
        assert source.name in project.project_name, "原生交接项目标题未绑定完整来源编号"
        for task in tasks:
            task.status, task.completed_on = "Completed", data["last_day"]
            task.save()
        source, project, tasks, _ = _source_graph(source.name)
        data["records"] = [_record(employee), _record(source), _record(project), *(_record(task) for task in tasks),
                           _record(holiday), _record(assignment)]
        data["source_intent"], data["graph_before"] = _intent(source), _graph(source.name)
        data["ready_at"] = now()
        _observe(data)
        for document, permissions in ((source, ("read", "write", "submit", "cancel")),
                (employee, ("read", "write")), (project, ("read", "write")),
                *((task, ("read", "write")) for task in tasks)):
            assert all(document.has_permission(permission, user=browser_user) for permission in permissions), \
                "指定浏览器用户不能操作本夹具的精确来源、员工或交接项目/任务"
        assert frappe.has_permission("Company", "select", doc=frappe.get_doc("Company", company), user=browser_user)
        assert source.docstatus == 1 and project.status == "Completed"
        assert all(task.status == "Completed" and str(task.completed_on) == data["last_day"] for task in tasks)
        data["ui_contract"] = {"route": "/app/employee-separation/" + source.name,
            "steps": ["在原生来源表单点击办理离职并确认", "核对员工 Left 和真实最后工作日",
                      "点击撤销离职办理并确认", "核对员工恢复 Active 后原生取消来源"],
            "buttons": ["办理离职", "撤销离职办理"],
            "expected_retained_objects": [{"doctype": row["doctype"], "name": row["name"]} for row in data["records"]]}
    except Exception as exc:
        try:
            _end_rollback_only(point)
        finally:
            _restore_callbacks(callbacks)
        data["phase"], data["prepare_error"] = "prepare_failed_rolled_back", str(exc)
        _journal(data)
        exc.add_note("离职浏览器准备已回滚，保留私有日志并核对，不自动重试: " + data["artifact_path"])
        raise
    finally:
        frappe.in_test = original_test
    _commit(data, "prepare")
    data["phase"] = "ready"
    _journal(data)
    return data


def inspect(data):
    """Observe exact native state and audit differences without writing."""
    frappe.only_for("System Manager")
    data = _load(data)
    result = {"prefix": data["prefix"], "phase": data["phase"], "artifact_path": data["artifact_path"],
              "cleanup_complete": data["cleanup_complete"], "owned_state": _owned_state(data)}
    try:
        result.update(_observe(data), validation_errors=[])
    except Exception as exc:
        result["validation_errors"] = [str(exc)]
        result["protected"] = _protected(data)
    counts = _tab_counts()[1]
    result["count_changes"] = {table: {"before": count, "after": counts[table]}
        for table, count in data["counts_before"].items() if counts[table] != count}
    result["login_metadata_changes"] = {field: {"before": data["protected_before"]["browser_login_metadata"].get(field),
        "after": result["protected"]["browser_login_metadata"].get(field)} for field in LOGIN_FIELDS
        if data["protected_before"]["browser_login_metadata"].get(field) != result["protected"]["browser_login_metadata"].get(field)}
    return result


def record_ui(data, stage):
    """Record a requested UI stage only after independently observing DB state."""
    frappe.only_for("System Manager")
    data = _load(data)
    assert stage in STAGES and data["phase"] in ({"ready"} | {"ui_" + name for name in STAGES})
    assert not data["cleanup_complete"], "已清理的夹具不能追加业务操作阶段"
    observed = _observe(data)
    assert observed["stage"] == stage, "实际原生状态与请求登记的浏览器阶段不一致"
    previous = data["ui_stage_log"][-1]["stage"] if data["ui_stage_log"] else "ready"
    allowed = {"ready": {"finalized"}, "finalized": {"finalized", "reversed"},
               "reversed": {"reversed", "cancelled"}, "cancelled": {"cancelled"}}
    assert stage in allowed[previous], "浏览器阶段必须按实际办理、撤销、取消的顺序登记"
    data["ui_stage_log"].append({"stage": stage, "observed_at": now(), "observed_by": frappe.session.user,
        "snapshot_hash": _digest(observed["snapshot"]), "employee_state": observed["employee_state"],
        "source_docstatus": observed["source_docstatus"], "graph": observed["graph"], "protected": observed["protected"]})
    data["phase"] = "ui_" + stage
    _journal(data)
    return data


def reconcile(data):
    """Resolve an unknown commit by exact reads; never repeat its mutation."""
    frappe.only_for("System Manager")
    data = _load(data)
    assert data["phase"].endswith(("_commit_pending", "_commit_unknown"))
    actual = _owned_state(data)
    _assert_protected(data)
    if actual == data["pending_state"]:
        _observe(data)
        data["phase"] = "ready" if data["commit_phase"] == "prepare" else "cancelled_history_retained"
    elif data["commit_phase"] == "prepare" and all(not row["exists"] for row in actual):
        data["phase"] = "prepare_failed_rolled_back"
    elif data["commit_phase"] == "cleanup" and actual == data["cleanup_before_state"]:
        data = deepcopy(data["cleanup_before_journal"])
    else:
        raise ValueError("提交结果与精确前后状态不一致，保留夹具，不自动重试")
    data.setdefault("reconciliation_log", []).append({"observed_at": now(), "observed_by": frappe.session.user,
                                                      "phase": data["phase"], "owned_state": actual})
    _journal(data)
    return data


def cleanup(data):
    """Native cancel a safe source and save its synthetic Employee Inactive."""
    frappe.only_for("System Manager")
    data = _load(data)
    assert data["phase"] in ({"ready", "cleanup_failed_rolled_back", "cancelled_history_retained"} |
                             {"ui_" + name for name in STAGES}), "提交未知须先 inspect/reconcile"
    observed = _observe(data)
    if data["cleanup_complete"]:
        return data
    assert observed["stage"] in {"ready", "reversed", "cancelled", "cancelled_pending"}, "已办理来源须先原生撤销，清理不能代办撤销"
    before, callbacks, original_test = deepcopy(data), _callbacks(), frappe.in_test
    data["cleanup_before_journal"], data["cleanup_before_state"] = before, _owned_state(data)
    point = "jf_sep_browser_cleanup_" + data["prefix"].removeprefix("JF-SEP-BROWSER-")
    _begin(point)
    try:
        frappe.in_test = False
        actions = []
        source = frappe.get_doc(SOURCE, data["source"])
        if source.docstatus == 1:
            source.cancel()
            actions.append({"doctype": SOURCE, "name": source.name, "action": "native_cancel", "at": now()})
        assignment = frappe.get_doc("Holiday List Assignment", data["holiday_assignment"])
        assert assignment.docstatus == 1
        assignment.cancel()
        actions.append({"doctype": "Holiday List Assignment", "name": assignment.name,
                        "action": "native_cancel", "at": now()})
        employee = frappe.get_doc("Employee", data["employee"])
        assert employee.status == "Active" and not employee.user_id
        employee.status = "Inactive"
        employee.save()
        actions.append({"doctype": "Employee", "name": employee.name, "action": "native_save",
                        "field": "status", "before": "Active", "after": "Inactive", "at": now()})
        data["cleanup_complete"] = True
        observed = _observe(data)
        assert observed["source_docstatus"] == 2
        data["native_action_log"] = actions
        data["retained_history_records"] = observed["owned_state"]
        data["cleanup_protected"] = observed["protected"]
        counts = _tab_counts()[1]
        data["cleanup_count_changes"] = {table: {"before": count, "after": counts[table]}
            for table, count in data["counts_before"].items() if counts[table] != count}
    except Exception as exc:
        try:
            _end_rollback_only(point)
        finally:
            _restore_callbacks(callbacks)
        data = before
        data["phase"], data["cleanup_error"] = "cleanup_failed_rolled_back", str(exc)
        _journal(data)
        exc.add_note("离职浏览器清理已回滚，保留原生来源和私有日志: " + data["artifact_path"])
        raise
    finally:
        frappe.in_test = original_test
    _commit(data, "cleanup")
    data["phase"] = "cancelled_history_retained"
    _journal(data)
    return data
