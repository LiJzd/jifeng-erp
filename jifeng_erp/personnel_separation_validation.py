"""One native separation experiment, with complete rollback and no retry.

Run once after migration in root's exclusive business-write window::

    cd /home/jifengtest/jifeng-dev/bench/sites
    /home/jifengtest/jifeng-dev/bench/env/bin/python - <<'PY'
    import json, frappe
    from jifeng_erp.personnel_separation_validation import smoke
    frappe.init(site="jifeng.localhost", sites_path="/home/jifengtest/jifeng-dev/bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        print(json.dumps(smoke(company="Jifeng ERP UI Validation",
                               other_company="Jifeng ERP Validation"),
                         ensure_ascii=False, default=str))
    finally:
        frappe.destroy()
    PY

Never use bench execute: its exception fallback may invoke a failing helper
twice. Synthetic fixtures use native inserts and real roles/User Permissions.
The production installer supplies HR Manager's scoped native source submit /
cancel grant; HR Manager performs submission, cancel, finalize and reverse.
HR User has no submit/cancel grant and is exercised as the rejected actor.
Business actions briefly use in_test=False for real Employee/source Versions.
No schema/DocPerm change, permission bypass, manual docstatus, logger patch,
force deletion, bulk task completion, commit, or payroll accounting is used.
Only hashes of full User/auth/payroll contents are exposed; no passwords or
private existing employee/payroll values are returned. No GUI/network needed.
"""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json

import frappe
from frappe.utils import add_days, get_first_day, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import (
    _callbacks, _digest, _employee_contents, _normal, _restore_callbacks,
    _tab_counts, _versions,
)


SOURCE = "Employee Separation"
SNAPSHOT = "jf_exit_snapshot"
STATUS = "jf_exit_status"
LAST_DAY = "jf_last_working_day"
REASON = "jf_reason_for_leaving"
STATE_FIELDS = {"company", "department", "designation", "branch", "status", "date_of_joining",
                "relieving_date", "user_id", "ctc", "employment_type", "grade", "reports_to",
                "create_user_permission", "resignation_letter_date", "reason_for_leaving"}
HISTORY_FIELDS = ("name", "parent", "parenttype", "parentfield", "idx", "branch",
                  "department", "designation", "from_date", "to_date")
PAYROLL_TYPES = ("Jifeng Wage Entry", "Additional Salary", "Salary Slip", "Salary Structure Assignment")


def _local_context():
    present = hasattr(frappe.local, "_realtime_log")
    saving_present = "currently_saving" in frappe.flags
    return {"realtime_present": present,
            "realtime": deepcopy(frappe.local._realtime_log) if present else None,
            "saving_present": saving_present,
            "saving": deepcopy(frappe.flags["currently_saving"]) if saving_present else None}


def _restore_local_context(state):
    if state["realtime_present"]:
        frappe.local._realtime_log = deepcopy(state["realtime"])
    elif hasattr(frappe.local, "_realtime_log"):
        del frappe.local._realtime_log
    if state["saving_present"]:
        frappe.flags["currently_saving"] = deepcopy(state["saving"])
    else:
        frappe.flags.pop("currently_saving", None)


def _audit_digest(value):
    def serialize(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError("Unsupported native audit value: " + type(item).__name__)
    return hashlib.sha256(json.dumps(value, default=serialize, sort_keys=True,
                                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _user_contents():
    """Whole User/profile/roles/User Permission/auth audit; callers expose hash only."""
    result = {"User": frappe.db.sql("SELECT * FROM `tabUser` ORDER BY name", as_dict=True),
              "User Permission": frappe.db.sql("SELECT * FROM `tabUser Permission` ORDER BY name", as_dict=True),
              "auth": frappe.db.sql("SELECT * FROM `__Auth` ORDER BY doctype,name,fieldname", as_dict=True)}
    for field in frappe.get_meta("User").get_table_fields():
        table = "`tab" + field.options.replace("`", "``") + "`"
        result[field.fieldname] = frappe.db.sql(
            "SELECT * FROM " + table + " WHERE parenttype='User' ORDER BY name", as_dict=True)
    for doctype in ("Role Profile", "Module Profile"):
        table = "`tab" + doctype.replace("`", "``") + "`"
        result[doctype] = {"parent": frappe.db.sql("SELECT * FROM " + table + " ORDER BY name", as_dict=True),
                            "children": {}}
        for field in frappe.get_meta(doctype).get_table_fields():
            child = "`tab" + field.options.replace("`", "``") + "`"
            result[doctype]["children"][field.fieldname] = frappe.db.sql(
                "SELECT * FROM " + child + " WHERE parenttype=%s AND parentfield=%s ORDER BY name",
                (doctype, field.fieldname), as_dict=True)
    return result


def _state(employee):
    doc = frappe.get_doc("Employee", employee)
    return _normal({"fields": {field: doc.get(field) for field in STATE_FIELDS},
                    "history": [{field: row.get(field) for field in HISTORY_FIELDS}
                                for row in doc.internal_work_history]})


def _payroll_contents(employee):
    """Independent full parent+child DB digest input; no wage values in evidence."""
    result = {}
    for doctype in PAYROLL_TYPES:
        table = "`tab" + doctype.replace("`", "``") + "`"
        rows = frappe.db.sql("SELECT * FROM " + table + " WHERE employee=%s ORDER BY name",
                             (employee,), as_dict=True)
        result[doctype] = {"parents": rows, "children": {}}
        names = tuple(row.name for row in rows)
        for field in frappe.get_meta(doctype).get_table_fields():
            child = "`tab" + field.options.replace("`", "``") + "`"
            values = ()
            if names:
                holders = ",".join(["%s"] * len(names))
                values = frappe.db.sql("SELECT * FROM " + child + " WHERE parenttype=%s AND parentfield=%s AND parent IN ("
                                       + holders + ") ORDER BY name", (doctype, field.fieldname, *names), as_dict=True)
            result[doctype]["children"][field.fieldname] = list(values)
    return result


def _source_graph(source):
    doc = frappe.get_doc(SOURCE, source)
    activities = [{"name": row.name, "task": row.task, "idx": row.idx} for row in doc.activities]
    task_ids = [row["task"] for row in activities]
    assert task_ids and all(task_ids) and len(set(task_ids)) == len(task_ids)
    assert {row.name for row in frappe.get_all("Task", filters={"project": doc.project}, fields=["name"])} == set(task_ids)
    project = frappe.get_doc("Project", doc.project)
    tasks = [frappe.get_doc("Task", name) for name in task_ids]
    assert project.company == doc.company and all(task.company == doc.company for task in tasks)
    return doc, project, tasks, activities


def _activities_hash(source):
    fields = sorted(field for field in set(frappe.get_meta("Employee Boarding Activity").get_valid_columns()) - {
        "creation", "modified", "modified_by", "owner", "docstatus"} if not field.startswith("_"))
    selected = ",".join("`" + field.replace("`", "``") + "`" for field in fields)
    rows = frappe.db.sql("SELECT " + selected + " FROM `tabEmployee Boarding Activity` "
        "WHERE parenttype=%s AND parentfield='activities' AND parent=%s ORDER BY idx",
        (SOURCE, source), as_dict=True)
    return _audit_digest(rows)


def _snapshot(source, before, after, actor, state="finalized"):
    """Oracle uses native DB/Doc observations, never production snapshot helpers."""
    payload = json.loads(source.get(SNAPSHOT))
    assert payload["version"] == 1 and payload["state"] == state
    assert source.get(STATUS) == ("已办理" if state == "finalized" else "已撤销")
    for section, expected in (("before", before), ("after", after)):
        assert payload[section]["fields"] == expected["fields"]
        assert payload[section]["history_hash"] == _audit_digest(expected["history"])
        assert payload[section]["modified"]
    intent = payload["source"]
    assert intent == {"name": source.name, "employee": source.employee, "company": source.company,
                      "project": source.project, "amended_from": source.amended_from or None,
                      "boarding_begins_on": str(source.boarding_begins_on),
                      "resignation_letter_date": str(source.resignation_letter_date) if source.resignation_letter_date else None,
                      "last_working_day": str(source.get(LAST_DAY)), "reason": source.get(REASON) or "",
                      "activities": [{"name": row.name, "task": row.task} for row in source.activities],
                      "activities_hash": _activities_hash(source.name)}
    _, project, tasks, activities = _source_graph(source.name)
    assert payload["project"] == {"name": project.name, "company": project.company, "status": project.status,
                                   "modified": project.modified.isoformat()}
    owners = {row["task"]: row["name"] for row in activities}
    expected_tasks = [{"activity": owners[task.name], "task": task.name, "project": task.project,
                       "company": task.company, "status": task.status, "completed_on": str(task.completed_on),
                       "modified": task.modified.isoformat()} for task in sorted(tasks, key=lambda row: row.name)]
    assert payload["tasks"] == expected_tasks
    assert payload["payroll_hash"] == _audit_digest(_payroll_contents(source.employee))
    changed = sorted(field for field in STATE_FIELDS if before["fields"][field] != after["fields"][field])
    assert payload["changed_fields"] == changed == ["reason_for_leaving", "relieving_date", "status"]
    assert before["history"] == after["history"]
    assert payload["finalized_by"] == actor and payload["finalized_at"]
    assert payload["reversal"] is None if state == "finalized" else bool(payload["reversal"])
    return payload


def smoke(company, other_company):
    """Serial native experiment. Every success/failure ends at the same rollback barrier."""
    from frappe.client import get as get_document_api
    from frappe.model import get_permitted_fields
    from jifeng_erp.personnel_separation import finalize_separation, reverse_separation

    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb"
    assert company != other_company and all(frappe.db.exists("Company", name) for name in (company, other_company))
    for doctype, extension in ((SOURCE, "EmployeeSeparationMixin"), ("Employee", "SeparationEmployeeMixin"),
                               ("Project", "SeparationProjectMixin")):
        hooks = frappe.get_hooks("extend_doctype_class").get(doctype, [])
        hooks = [hooks] if isinstance(hooks, str) else hooks
        assert "jifeng_erp.personnel_separation." + extension in hooks, "离职控制器扩展尚未迁移"
    for hook, function in (("has_permission", "separation_has_permission"),
                           ("permission_query_conditions", "separation_query_conditions")):
        installed = frappe.get_hooks(hook).get(SOURCE, [])
        installed = [installed] if isinstance(installed, str) else installed
        assert "jifeng_erp.personnel_separation." + function in installed, "离职来源读取权限守卫尚未安装"
    field = frappe.get_meta(SOURCE).get_field(SNAPSHOT)
    assert field and field.fieldtype == "Long Text" and all(field.get(flag) for flag in
            ("hidden", "read_only", "no_copy", "allow_on_submit")), "离职服务器快照字段尚未迁移"
    assert all(frappe.get_meta(SOURCE).get_field(field) for field in (LAST_DAY, REASON, STATUS))
    assert all(hasattr(getattr(frappe.db, field), "_functions") for field in
               ("before_commit", "after_commit", "before_rollback", "after_rollback"))
    tables, counts_before = _tab_counts()
    # Native User creates Contacts and default/share child rows. Audit every table,
    # and require every potentially written table here to support rollback.
    writable = ("Employee", "Employee Internal Work History", SOURCE, "Employee Boarding Activity",
                "Project", "Task", "User", "Has Role", "User Permission", "Contact", "Contact Email",
                "Contact Phone", "Dynamic Link", "DocShare", "DefaultValue", "Holiday List",
                "Holiday List Assignment", "Salary Component", "Salary Component Account",
                "Salary Structure", "Salary Detail", "Version", "Series", *PAYROLL_TYPES)
    for doctype in writable:
        assert tables["tab" + doctype] == "InnoDB", "离职验收写入对象必须支持事务回滚"
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB", "合成账号密码表必须支持事务回滚"
    ledger_before = _ledger_snapshot()
    staff_before, users_before = _digest(_employee_contents()), _digest(_user_contents())
    logs_before = set(frappe.get_all("Error Log", pluck="name"))
    callbacks_before = _callbacks()
    context_before = _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    suffix = frappe.generate_hash(length=10)
    prefix, point = "JF-QA-SEPARATION-" + suffix, "jf_separation_" + suffix
    objects, records, users, checks = [], set(), [], []
    evidence = {"roles": {"business": ["HR Manager"], "native_submission": ["HR Manager"], "rejected": ["HR User"]},
                "execution": "one direct Python call; native save/submit/cancel; no bench execute/retry",
                "field_denial_limit": "native existing permission levels only; no schema/DocPerm change manufactured",
                "legacy_limit": "no existing legacy source scanned or fake submitted source manufactured"}
    current_gate, focus, peer_hash, protected, protected_users = "begin", None, None, None, None
    _begin_rollback_only(point)

    def gate(label):
        nonlocal current_gate
        current_gate = label

    def passed():
        checks.append(current_gate)
        assert _ledger_snapshot() == ledger_before, "离职操作改变了 whole GL/SLE 内容"
        assert set(frappe.get_all("Error Log", pluck="name")) == logs_before, "直接 Doc/RPC 操作产生 Error Log"
        if focus:
            assert _digest(_employee_contents(exclude=focus.name)) == protected, "其他员工及其全部子表改变"
            assert _digest(_employee_contents(name=peer.name)) == peer_hash, "未授权同公司员工改变"
            assert _digest(_user_contents()) == protected_users, "User、密码、Profile、Role 或 User Permission 被改动"

    def remember(doc):
        objects.append(doc)
        if doc.name:
            records.add((doc.doctype, doc.name))
        return doc

    def insert(doctype, **values):
        assert tables["tab" + doctype] == "InnoDB"
        assert all(tables["tab" + field.options] == "InnoDB" for field in frappe.get_meta(doctype).get_table_fields())
        doc = remember(frappe.get_doc({"doctype": doctype, **values}))
        doc.insert()
        records.add((doctype, doc.name))
        return doc

    def permit(user, doctype, names):
        frappe.set_user("Administrator")
        for name in names:
            insert("User Permission", user=user, allow=doctype, for_value=name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user)

    def protect(employee):
        nonlocal focus, protected, protected_users
        focus = employee
        protected = _digest(_employee_contents(exclude=employee.name))
        protected_users = _digest(_user_contents())

    def native(operation):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return operation()
        finally:
            frappe.in_test = previous

    def reject(operation, error, message=None):
        nested = "jf_separation_reject_" + frappe.generate_hash(length=8)
        ledger, staff, accounts = _ledger_snapshot(), _digest(_employee_contents()), _digest(_user_contents())
        logs, callbacks = set(frappe.get_all("Error Log", pluck="name")), _callbacks()
        context = _local_context()
        frappe.db.savepoint(nested)
        try:
            try:
                operation()
            except error as exception:
                if message:
                    assert message in str(exception), "错误并非预期离职守卫"
                actual = type(exception).__name__
            else:
                raise AssertionError("验收预期拒绝，但操作成功")
        finally:
            try:
                frappe.db.rollback(save_point=nested)
            finally:
                _restore_callbacks(callbacks)
                _restore_local_context(context)
                for employee in (focus,):
                    if employee:
                        frappe.clear_document_cache("Employee", employee.name)
        assert _ledger_snapshot() == ledger and _digest(_employee_contents()) == staff
        assert _digest(_user_contents()) == accounts
        assert set(frappe.get_all("Error Log", pluck="name")) == logs
        evidence.setdefault("rejections", []).append({"gate": current_gate, "exception_type": actual})
        return actual

    def separation(employee, owner=None, **values):
        return insert(SOURCE, employee=employee.name, company=owner or employee.company,
                      boarding_begins_on=boarding_date, notify_users_by_email=0,
                      **{LAST_DAY: last_day, REASON: prefix + "正常离职验收",
                         "activities": [{"activity_name": prefix + "-handover", "begin_on": 0, "duration": 0},
                                        {"activity_name": prefix + "-clearance", "begin_on": 1, "duration": 0}],
                         **values})

    def submit(doc):
        frappe.set_user(actors["submit"])
        # A rejected submit mutates in-memory docstatus/modified before the
        # native submit permission check. Savepoint rollback only restores DB.
        doc = remember(frappe.get_doc(SOURCE, doc.name))
        native(doc.submit)
        doc.reload()
        _, project, tasks, _ = _source_graph(doc.name)
        assert doc.name in project.project_name, "原生交接项目标题没有绑定完整正式来源编号"
        remember(project)
        for task in tasks:
            remember(task)
        assert doc.docstatus == 1 and not doc.get(SNAPSHOT) and doc.get(STATUS) == "待办理"
        return doc

    def complete(doc):
        frappe.set_user(actors["local"])
        _, _, tasks, _ = _source_graph(doc.name)
        for task in tasks:
            task.status, task.completed_on = "Completed", last_day
            native(task.save)
        refreshed, project, tasks, _ = _source_graph(doc.name)
        assert project.status == "Completed" and refreshed.boarding_status == "Completed"
        assert all(task.status == "Completed" and str(task.completed_on) == last_day for task in tasks)

    def business(doc, operation):
        frappe.set_user(actors["local"])
        source_before, employee_before = set(_versions(SOURCE, doc.name)), set(_versions("Employee", doc.employee))
        response = native(lambda: operation(doc.name))
        doc.reload()
        sources = {name: data for name, data in _versions(SOURCE, doc.name).items() if name not in source_before}
        employees = {name: data for name, data in _versions("Employee", doc.employee).items() if name not in employee_before}
        assert sources and employees, "真实办理/撤销未产生来源及员工 Version"
        assert any({SNAPSHOT, STATUS} <= {row[0] for row in data.get("changed", [])} for data in sources.values())
        changed = {row[0] for data in employees.values() for row in data.get("changed", [])}
        assert {"status", "relieving_date", "reason_for_leaving"} <= changed
        assert all(not any(row[0] == "internal_work_history" for row in data.get(key, []))
                   for data in employees.values() for key in ("added", "removed", "row_changed"))
        records.update(("Version", name) for name in (*sources, *employees))
        evidence.setdefault("native_versions", []).append({"action": operation.__name__, "source": doc.name,
            "source_versions": sorted(sources), "employee_versions": sorted(employees),
            "employee_changed_fields": sorted(changed), "history_unchanged": True})
        assert response == {"name": doc.name, "exit_status": doc.get(STATUS)}
        return response

    def reverse_conflict(mutation, payroll_only=False):
        if payroll_only:
            original_staff, original_accounts = _digest(_employee_contents()), _digest(_user_contents())
            _, project, tasks, activities = _source_graph(active_source.name)
            original_graph = _digest((project.as_dict(), [task.as_dict() for task in tasks], activities))
        mutation()
        if payroll_only:
            assert _digest(_employee_contents()) == original_staff and _digest(_user_contents()) == original_accounts
            _, project, tasks, activities = _source_graph(active_source.name)
            assert _digest((project.as_dict(), [task.as_dict() for task in tasks], activities)) == original_graph
        staff, accounts = _digest(_employee_contents()), _digest(_user_contents())
        source_hash = _digest(frappe.get_doc(SOURCE, active_source.name).as_dict())
        try:
            native(lambda: reverse_separation(active_source.name))
        finally:
            assert _digest(_employee_contents()) == staff and _digest(_user_contents()) == accounts
            assert _digest(frappe.get_doc(SOURCE, active_source.name).as_dict()) == source_hash

    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        gate("native_roles_user_permissions_table_child_fields_and_workflow_facts")
        joined, boarding_date, last_day = add_days(today(), -90), add_days(today(), -8), add_days(today(), -3)
        currency = frappe.db.get_value("Company", company, "default_currency")
        gender = frappe.db.get_value("Gender", {}, "name")
        assert currency and gender
        holiday = insert("Holiday List", holiday_list_name=prefix + "-HOL", from_date=add_days(joined, -2),
                         to_date=add_days(today(), 30), holidays=[])
        profile = insert("Role Profile", role_profile=prefix + "-EMPLOYEE-PROFILE", roles=[{"role": "Employee"}])
        linked = []
        for index in range(2):
            user = insert("User", email=f"{prefix.lower()}-employee-{index}@example.invalid", first_name=prefix,
                          last_name="Linked" + str(index), enabled=1, send_welcome_email=0,
                          new_password="JF@" + frappe.generate_hash(length=24) + "9aZ",
                          role_profiles=[{"role_profile": profile.name}])
            users.append(user.name)
            linked.append(user)
        common = {"gender": gender, "date_of_birth": "1990-01-01", "date_of_joining": joined,
                  "status": "Active", "ctc": 0, "salary_currency": currency,
                  "create_user_automatically": 0, "create_user_permission": 0,
                  "resignation_letter_date": add_days(boarding_date, -1), "reason_for_leaving": None}
        history = [{"from_date": joined, "to_date": add_days(boarding_date, -1)}, {"from_date": boarding_date}]
        primary = insert("Employee", company=company, first_name=prefix + "-enabled", employee_number=prefix + "-A",
                         user_id=linked[0].name, internal_work_history=history, **common)
        disabled = insert("Employee", company=company, first_name=prefix + "-disabled", employee_number=prefix + "-B",
                          user_id=linked[1].name, internal_work_history=[dict(row) for row in history], **common)
        assert frappe.db.get_value("User", linked[1].name, "enabled") == 1
        disabled_user = frappe.get_doc("User", linked[1].name)
        disabled_user.enabled = 0
        disabled_user.save()
        assert _state(disabled.name)["fields"]["status"] == "Active" and frappe.db.get_value("User", linked[1].name, "enabled") == 0
        peer = insert("Employee", company=company, first_name=prefix + "-hidden-peer", employee_number=prefix + "-C",
                      internal_work_history=[dict(row) for row in history], **common)
        foreign = insert("Employee", company=other_company, first_name=prefix + "-foreign", employee_number=prefix + "-D",
                         internal_work_history=[dict(row) for row in history], **common)
        probe = insert("Employee", company=company, first_name=prefix + "-pending-probe", employee_number=prefix + "-G",
                       internal_work_history=[dict(row) for row in history], **common)
        actors = {}
        for kind, roles, companies in (("local", ["HR Manager"], [company]),
                                        ("submit", ["HR Manager"], [company, other_company]),
                                        ("cross", ["HR Manager"], [company, other_company]),
                                        ("denied", ["HR User"], [company])):
            user = insert("User", email=f"{prefix.lower()}-{kind}@example.invalid", first_name=prefix + "-" + kind,
                          send_welcome_email=0, roles=[{"role": role} for role in roles])
            users.append(user.name)
            actors[kind] = user.name
            permit(user.name, "Company", companies)
            permit(user.name, "Employee", [primary.name, disabled.name, foreign.name, probe.name])
        component = insert("Salary Component", salary_component=prefix + "-EARNING", salary_component_abbr="JFX" + suffix,
                           type="Earning", is_tax_applicable=0, depends_on_payment_days=0)
        structure = insert("Salary Structure", name=prefix + "-SS", company=company, currency=currency,
                           is_active="Yes", payroll_frequency="Monthly", earnings=[{
                               "salary_component": component.name, "amount": 1000, "depends_on_payment_days": 0}])
        structure.submit()
        assignment = insert("Salary Structure Assignment", employee=primary.name, company=company,
                            salary_structure=structure.name, currency=currency, from_date=joined)
        assignment.submit()
        for employee in (primary, disabled, peer, foreign, probe):
            holiday_assignment = insert("Holiday List Assignment", holiday_list=holiday.name,
                                        applicable_for="Employee", assigned_to=employee.name, from_date=joined)
            holiday_assignment.submit()
        frappe.set_user(actors["local"])
        wage = insert("Jifeng Wage Entry", employee=primary.name, company=company, salary_component=component.name,
                      operation=prefix + "-standalone", work_date=last_day, payroll_date=last_day, hours=1, hourly_rate=1)
        wage.submit()
        draft = separation(primary)
        assert draft.has_permission("read") and draft.has_permission("write")
        assert draft.has_permission("submit") and draft.has_permission("cancel")
        assert all(frappe.has_permission(SOURCE, permission) for permission in ("submit", "cancel"))
        evidence["actual_actor_roles"] = {kind: sorted(frappe.get_roles(name)) for kind, name in actors.items()}
        assert all("System Manager" not in roles for roles in evidence["actual_actor_roles"].values())
        assert all(frappe.has_permission(dt, p) for dt in ("Employee", "Project", "Task") for p in ("read", "write"))
        assert not frappe.get_doc("Employee", peer.name).has_permission("read")
        assert not frappe.get_doc("Company", other_company).has_permission("select")
        meta, columns = frappe.get_meta("Employee"), {}
        table = meta.get_field("internal_work_history")
        assert table and table.fieldtype == "Table" and table.options == "Employee Internal Work History"
        for permission in ("read", "write"):
            employee_fields = set(get_permitted_fields("Employee", permission_type=permission, ignore_virtual=True))
            child_fields = set(get_permitted_fields(table.options, parenttype="Employee", permission_type=permission, ignore_virtual=True))
            levels = set(meta.get_permlevel_access(permission_type=permission))
            assert STATE_FIELDS <= employee_fields and "internal_work_history" not in employee_fields
            assert (table.permlevel or 0) in levels and set(HISTORY_FIELDS[5:]) <= child_fields
            columns[permission] = {"columns": sorted(STATE_FIELDS), "table_permlevel": table.permlevel or 0,
                "table_native_levels": sorted(levels), "table_in_column_api": False, "child": sorted(set(HISTORY_FIELDS[5:]))}
        evidence["field_permissions"] = columns
        evidence["site_permissions"] = {dt: {"custom_perm_rows": frappe.db.count("Custom DocPerm", {"parent": dt}),
            "active_workflows": frappe.db.count("Workflow", {"document_type": dt, "is_active": 1}),
            "active_notifications": frappe.db.count("Notification", {"document_type": dt, "enabled": 1}),
            "track_changes": frappe.get_meta(dt).track_changes} for dt in (SOURCE, "Employee", "Project", "Task")}
        peer_hash = _digest(_employee_contents(name=peer.name))
        protect(primary)
        initial = _state(primary.name)
        assert frappe.db.sql("SELECT COUNT(*) FROM `__Auth` WHERE doctype='User' AND fieldname='password' "
                             "AND encrypted=0 AND name IN (%s,%s)", (linked[0].name, linked[1].name))[0][0] == 2
        assert all(frappe.get_doc("User", user.name).role_profiles and
                   "Employee" in {row.role for row in frappe.get_doc("User", user.name).roles} for user in linked)
        evidence["linked_account_baseline"] = {"native_password_hash_rows": 2, "nonempty_role_profiles": 2,
                                               "enabled_states": [1, 0], "actual_distinct_employee_users": True}
        frappe.set_user(actors["denied"])
        assert not frappe.has_permission(SOURCE, "submit") and not frappe.has_permission(SOURCE, "cancel")
        reject(lambda: native(frappe.get_doc(SOURCE, draft.name).submit), frappe.PermissionError)
        assert frappe.get_doc(SOURCE, draft.name).docstatus == 0
        evidence["source_role_permissions"] = {"HR Manager": {"submit": True, "cancel": True},
                                                "HR User": {"submit": False, "cancel": False}}
        frappe.set_user(actors["local"])
        passed()

        gate("draft_and_client_forged_server_status_snapshot_reject")
        reject(lambda: native(lambda: finalize_separation(draft.name)), frappe.ValidationError)
        def forge():
            doc = frappe.get_doc(SOURCE, draft.name)
            doc.set(SNAPSHOT, '{"version":1,"state":"finalized"}')
            doc.set(STATUS, "已办理")
            native(doc.save)
        reject(forge, (frappe.ValidationError, frappe.PermissionError))
        passed()

        gate("native_submission_creates_exact_project_activity_task_graph_without_employee_change")
        active_source = submit(draft)
        frappe.set_user(actors["local"])
        assert _state(primary.name) == initial
        assert active_source.boarding_status == "Pending"
        reject(lambda: native(lambda: reverse_separation(active_source.name)), frappe.ValidationError, "缺少可靠服务器追溯快照")
        passed()

        gate("cancelled_tasks_cannot_replace_actual_completed_handover")
        def cancelled():
            _, _, tasks, _ = _source_graph(active_source.name)
            for task in tasks:
                task.status = "Cancelled"
                native(task.save)
            _, project, _, _ = _source_graph(active_source.name)
            assert project.percent_complete == 100 and project.status == "Completed"
            native(lambda: finalize_separation(active_source.name))
        reject(cancelled, frappe.ValidationError, "完整精确任务")
        passed()

        gate("manual_project_100_percent_does_not_prove_actual_task_completion")
        def manual():
            _, project, tasks, _ = _source_graph(active_source.name)
            task_contents = _digest([task.as_dict() for task in tasks])
            # Native Manual progress returns before automatic status updates.
            # Explicit Completed is a supported native Project.save input.
            project.status, project.percent_complete_method, project.percent_complete = "Completed", "Manual", 100
            native(project.save)
            _, project, tasks, _ = _source_graph(active_source.name)
            assert project.status == "Completed" and project.percent_complete_method == "Manual" and project.percent_complete == 100
            assert all(task.status != "Completed" and not task.completed_on for task in tasks)
            assert task_contents == _digest([task.as_dict() for task in tasks]), "手动项目保存改变了实际交接任务"
            evidence["native_manual_project"] = {"status": project.status, "percent_complete_method": project.percent_complete_method,
                "percent_complete": project.percent_complete, "actual_tasks_completed": False,
                "whole_task_contents_unchanged": True, "path": "native Project.save; no bulk task-status RPC"}
            native(lambda: finalize_separation(active_source.name))
        reject(manual, frappe.ValidationError, "完整精确任务")
        passed()

        gate("completed_status_without_native_completion_date_rejects")
        def undated():
            _, _, tasks, _ = _source_graph(active_source.name)
            for task in tasks:
                task.status, task.completed_on = "Completed", None
                native(task.save)
            native(lambda: finalize_separation(active_source.name))
        reject(undated, frappe.ValidationError, "完整精确任务")
        passed()

        gate("native_individual_task_completion_records_actual_last_day")
        complete(active_source)
        assert boarding_date != last_day
        passed()

        gate("native_hr_user_cannot_cancel_submitted_source_or_finalize_valid_completed_handover")
        frappe.set_user(actors["denied"])
        assert not frappe.has_permission(SOURCE, "submit") and not frappe.has_permission(SOURCE, "cancel")
        reject(lambda: native(frappe.get_doc(SOURCE, active_source.name).cancel), frappe.PermissionError)
        assert frappe.get_doc(SOURCE, active_source.name).docstatus == 1
        reject(lambda: native(lambda: finalize_separation(active_source.name)), frappe.PermissionError)
        frappe.set_user(actors["local"])
        assert _state(primary.name) == initial
        passed()

        gate("unapproved_wages_through_last_day_block_finalization")
        def unapproved():
            insert("Jifeng Wage Entry", employee=primary.name, company=company, salary_component=component.name,
                   operation=prefix + "-draft", work_date=last_day, payroll_date=last_day, hours=1, hourly_rate=1)
            native(lambda: finalize_separation(active_source.name))
        reject(unapproved, frappe.ValidationError, "未审核计薪记录")
        passed()

        gate("native_finalize_uses_actual_last_day_preserves_enabled_user_and_history_records_versions")
        business(active_source, finalize_separation)
        finalized = _state(primary.name)
        assert finalized["fields"]["status"] == "Left" and finalized["fields"]["relieving_date"] == last_day
        assert finalized["fields"]["reason_for_leaving"] == active_source.get(REASON)
        assert finalized["fields"]["user_id"] == linked[0].name and frappe.db.get_value("User", linked[0].name, "enabled") == 1
        _snapshot(active_source, initial, finalized, actors["local"])
        frappe.set_user(actors["denied"])
        reject(lambda: native(lambda: reverse_separation(active_source.name)), frappe.PermissionError)
        frappe.set_user(actors["local"])
        assert _state(primary.name) == finalized
        passed()

        gate("repeat_finalization_has_no_employee_user_source_version_writes")
        source_hash, versions = _digest(frappe.get_doc(SOURCE, active_source.name).as_dict()), set(_versions(SOURCE, active_source.name))
        staff = _digest(_employee_contents())
        assert native(lambda: finalize_separation(active_source.name)) == {"name": active_source.name, "exit_status": "已办理"}
        assert source_hash == _digest(frappe.get_doc(SOURCE, active_source.name).as_dict())
        assert versions == set(_versions(SOURCE, active_source.name)) and staff == _digest(_employee_contents())
        passed()

        gate("native_source_get_list_snapshot_hide_unauthorized_employee_and_company")
        assert get_document_api(SOURCE, active_source.name)[SNAPSHOT] == active_source.get(SNAPSHOT)
        rows = frappe.get_list(SOURCE, filters={"name": active_source.name}, fields=["name", SNAPSHOT])
        assert len(rows) == 1 and rows[0][SNAPSHOT] == active_source.get(SNAPSHOT)
        def hidden_source():
            frappe.set_user("Administrator")
            hidden = separation(peer)
            frappe.set_user(actors["local"])
            get_document_api(SOURCE, hidden.name)
        reject(hidden_source, frappe.PermissionError)
        def foreign_source():
            frappe.set_user(actors["submit"])
            foreign_doc = submit(separation(foreign))
            frappe.set_user(actors["cross"])
            assert get_document_api(SOURCE, foreign_doc.name)["name"] == foreign_doc.name
            frappe.set_user(actors["local"])
            assert not frappe.get_list(SOURCE, filters={"name": foreign_doc.name}, fields=["name", SNAPSHOT])
            get_document_api(SOURCE, foreign_doc.name)
        reject(foreign_source, frappe.PermissionError)
        def lose_employee_visibility():
            frappe.set_user(actors["cross"])
            employee = frappe.get_doc("Employee", primary.name)
            employee.company = other_company
            native(employee.save)
            assert get_document_api(SOURCE, active_source.name)[SNAPSHOT] == active_source.get(SNAPSHOT)
            frappe.set_user(actors["local"])
            assert not frappe.get_doc("Employee", primary.name).has_permission("read")
            assert not frappe.get_doc(SOURCE, active_source.name).has_permission("read")
            assert not frappe.get_list(SOURCE, filters={"name": active_source.name}, fields=["name", SNAPSHOT])
            get_document_api(SOURCE, active_source.name)
        reject(lose_employee_visibility, frappe.PermissionError)
        frappe.set_user(actors["local"])
        passed()

        gate("submitted_client_snapshot_tamper_rejects_without_mutations")
        def submitted_forge():
            doc = frappe.get_doc(SOURCE, active_source.name)
            payload = json.loads(doc.get(SNAPSHOT))
            payload["before"]["fields"]["status"] = "Suspended"
            doc.set(SNAPSHOT, json.dumps(payload, ensure_ascii=False))
            native(doc.save)
        reject(submitted_forge, (frappe.ValidationError, frappe.PermissionError))
        def changed_activity():
            doc = frappe.get_doc(SOURCE, active_source.name)
            doc.activities[0].description = prefix + "-forged-activity"
            native(doc.save)
        reject(changed_activity, (frappe.ValidationError, frappe.PermissionError))
        passed()

        gate("subsequent_employee_ctc_and_exact_history_changes_block_reversal")
        for kind in ("ctc", "history"):
            def mutate_employee(kind=kind):
                doc = frappe.get_doc("Employee", primary.name)
                if kind == "ctc":
                    doc.ctc = 999
                else:
                    doc.internal_work_history[-1].to_date = last_day
                native(doc.save)
            reject(lambda: reverse_conflict(mutate_employee), frappe.ValidationError, "已有后续变化")
        passed()

        for kind in PAYROLL_TYPES:
            gate("native_subsequent_" + kind.lower().replace(" ", "_") + "_change_blocks_reversal")
            def mutate_payroll(kind=kind):
                if kind == "Jifeng Wage Entry":
                    native(frappe.get_doc(kind, wage.name).cancel)
                elif kind == "Additional Salary":
                    insert(kind, employee=primary.name, company=company, salary_component=component.name,
                           currency=currency, payroll_date=last_day, amount=1)
                elif kind == "Salary Structure Assignment":
                    insert(kind, employee=primary.name, company=company, salary_structure=structure.name,
                           currency=currency, from_date=add_days(joined, 1))
                else:
                    insert(kind, employee=primary.name, company=company, salary_structure=structure.name,
                           currency=currency, start_date=get_first_day(last_day), end_date=last_day,
                           payroll_frequency="Monthly", posting_date=last_day)
                assert _audit_digest(_payroll_contents(primary.name)) != json.loads(active_source.get(SNAPSHOT))["payroll_hash"]
            reject(lambda: reverse_conflict(lambda: native(mutate_payroll), payroll_only=True),
                   frappe.ValidationError, "已有后续变化")
            evidence.setdefault("payroll_conflict_native", []).append({"doctype": kind, "actual_native_mutation": True,
                "whole_employee_user_exact_project_task_contents_unchanged": True})
            passed()

        gate("subsequent_actual_task_change_blocks_reversal")
        def mutate_task():
            _, _, tasks, _ = _source_graph(active_source.name)
            tasks[0].description = prefix + "-subsequent-change"
            native(tasks[0].save)
        reject(lambda: reverse_conflict(mutate_task), frappe.ValidationError, "已有后续变化")
        passed()

        gate("finalized_source_requires_reverse_before_native_cancel_and_keeps_graph_untouched")
        frappe.set_user(actors["submit"])
        _, project, tasks, activities = _source_graph(active_source.name)
        before_graph = _digest((project.as_dict(), [task.as_dict() for task in tasks], activities))
        reject(lambda: native(frappe.get_doc(SOURCE, active_source.name).cancel), frappe.ValidationError, "请先撤销离职办理")
        _, project, tasks, activities = _source_graph(active_source.name)
        assert _digest((project.as_dict(), [task.as_dict() for task in tasks], activities)) == before_graph
        passed()

        gate("native_reverse_restores_exact_employee_status_date_reason_history_and_keeps_project_tasks")
        business(active_source, reverse_separation)
        assert _state(primary.name) == initial
        _snapshot(active_source, initial, finalized, actors["local"], state="reversed")
        assert frappe.db.get_value("User", linked[0].name, "enabled") == 1
        passed()

        gate("repeat_reverse_is_no_write_and_reusing_reversed_finalization_rejects")
        staff, accounts = _digest(_employee_contents()), _digest(_user_contents())
        source_hash, versions = _digest(frappe.get_doc(SOURCE, active_source.name).as_dict()), set(_versions(SOURCE, active_source.name))
        assert native(lambda: reverse_separation(active_source.name)) == {"name": active_source.name, "exit_status": "已撤销"}
        assert staff == _digest(_employee_contents()) and accounts == _digest(_user_contents())
        assert source_hash == _digest(frappe.get_doc(SOURCE, active_source.name).as_dict()) and versions == set(_versions(SOURCE, active_source.name))
        reject(lambda: native(lambda: finalize_separation(active_source.name)), frappe.ValidationError, "已撤销")
        passed()

        gate("reversed_source_cancel_rejects_later_employee_change")
        frappe.set_user(actors["submit"])
        def altered_then_cancel():
            employee = frappe.get_doc("Employee", primary.name)
            employee.ctc = 1234
            native(employee.save)
            before_staff, before_users = _digest(_employee_contents()), _digest(_user_contents())
            _, project, tasks, activities = _source_graph(active_source.name)
            graph = _digest((project.as_dict(), [task.as_dict() for task in tasks], activities))
            try:
                native(frappe.get_doc(SOURCE, active_source.name).cancel)
            finally:
                assert before_staff == _digest(_employee_contents()) and before_users == _digest(_user_contents())
                _, project_after, tasks_after, activities_after = _source_graph(active_source.name)
                assert graph == _digest((project_after.as_dict(), [task.as_dict() for task in tasks_after], activities_after))
        reject(altered_then_cancel, frappe.ValidationError, "已有后续变化")
        passed()

        gate("native_reversed_source_cancel_preserves_exact_project_task_links_and_employee_accounts")
        frappe.set_user(actors["submit"])
        old_doc, old_project, old_tasks, old_activities = _source_graph(active_source.name)
        source_versions, employee_versions = set(_versions(SOURCE, active_source.name)), set(_versions("Employee", primary.name))
        native(frappe.get_doc(SOURCE, active_source.name).cancel)
        active_source.reload()
        _, project, tasks, activities = _source_graph(active_source.name)
        assert active_source.docstatus == 2 and active_source.project == old_doc.project == old_project.name
        assert activities == old_activities and {task.name for task in tasks} == {task.name for task in old_tasks}
        assert project.status == "Cancelled" and all(task.status == "Cancelled" for task in tasks)
        assert _state(primary.name) == initial and active_source.get(STATUS) == "已撤销"
        new_versions = {name: data for name, data in _versions(SOURCE, active_source.name).items() if name not in source_versions}
        assert new_versions and any(["docstatus", 1, 2] in data.get("changed", []) for data in new_versions.values())
        assert employee_versions == set(_versions("Employee", primary.name))
        records.update(("Version", name) for name in new_versions)
        evidence["native_source_cancel"] = {"source": active_source.name, "project": project.name,
            "tasks": sorted(task.name for task in tasks), "source_versions": sorted(new_versions),
            "preserved_links": True, "employee_unchanged": True}
        assert get_document_api(SOURCE, active_source.name)[SNAPSHOT] == active_source.get(SNAPSHOT)
        passed()

        gate("native_disabled_linked_user_stays_disabled_during_left_and_exact_active_restoration")
        protect(disabled)
        disabled_initial = _state(disabled.name)
        assert disabled_initial["fields"]["status"] == "Active" and frappe.db.get_value("User", linked[1].name, "enabled") == 0
        frappe.set_user(actors["local"])
        disabled_source = submit(separation(disabled))
        complete(disabled_source)
        business(disabled_source, finalize_separation)
        disabled_finalized = _state(disabled.name)
        assert disabled_finalized["fields"]["status"] == "Left" and frappe.db.get_value("User", linked[1].name, "enabled") == 0
        _snapshot(disabled_source, disabled_initial, disabled_finalized, actors["local"])
        business(disabled_source, reverse_separation)
        assert _state(disabled.name) == disabled_initial and frappe.db.get_value("User", linked[1].name, "enabled") == 0
        _snapshot(disabled_source, disabled_initial, disabled_finalized, actors["local"], state="reversed")
        evidence["disabled_account"] = {"native_employee_status_before": "Active", "native_user_enabled_before": 0,
            "employee_after_finalize": "Left", "user_enabled_after_finalize": 0,
            "employee_after_reverse": "Active", "user_enabled_after_reverse": 0,
            "whole_user_contents_unchanged": _digest(_user_contents()) == protected_users}
        passed()

        gate("native_future_missing_and_pre_joining_last_days_reject_without_employee_account_changes")
        protect(probe)
        probe_initial = _state(probe.name)
        for value in (add_days(today(), 1), add_days(joined, -1), None):
            reached = []
            def bad_date(value=value):
                frappe.set_user(actors["local"])
                dated = submit(separation(probe, **{LAST_DAY: value}))
                complete(dated)
                reached.append("finalize")
                native(lambda: finalize_separation(dated.name))
            reject(bad_date, frappe.ValidationError, "最后工作日")
            assert reached == ["finalize"], "日期门必须实际到达已提交来源的办理守卫"
        passed()

        gate("inactive_employee_cannot_use_active_employee_separation_chain")
        frappe.set_user(actors["local"])
        def inactive():
            employee = frappe.get_doc("Employee", probe.name)
            employee.status = "Inactive"
            native(employee.save)
            doc = submit(separation(employee))
            complete(doc)
            native(lambda: finalize_separation(doc.name))
        reject(inactive, frappe.ValidationError, "仅支持实际在职员工")
        passed()

        gate("native_single_active_employee_per_user_and_active_report_to_constraints_remain")
        def duplicate_active():
            frappe.set_user("Administrator")
            insert("Employee", company=company, first_name=prefix + "-duplicate-user", employee_number=prefix + "-E",
                   user_id=linked[0].name, **common)
        reject(duplicate_active, frappe.DuplicateEntryError)
        passed()

        gate("native_failed_employee_save_rpc_preserves_existing_realtime_callbacks_and_saving_context")
        def active_subordinate():
            frappe.set_user("Administrator")
            insert("Employee", company=company, first_name=prefix + "-subordinate", employee_number=prefix + "-F",
                   reports_to=probe.name, **common)
            frappe.set_user(actors["local"])
            doc = submit(separation(probe))
            complete(doc)
            context, callbacks = _local_context(), _callbacks()
            managers = {field: id(getattr(frappe.db, field)) for field in callbacks}
            assert context["realtime_present"] and context["realtime"], "原生交接保存未生成可核验的此前 realtime 队列"
            assert any(getattr(function, "__name__", None) == "flush_realtime_log"
                       for function in callbacks["after_commit"])
            try:
                native(lambda: finalize_separation(doc.name))
            finally:
                assert _local_context() == context, "失败 RPC 改变旧 realtime_log/currently_saving 内容或存在性"
                assert _callbacks() == callbacks, "失败 RPC 未保留此前 callback 完整队列"
                assert managers == {field: id(getattr(frappe.db, field)) for field in callbacks}
            # Source/Employee callbacks are preserved by production before the
            # outer rejection savepoint restores this synthetic scenario.
        reject(active_subordinate, frappe.ValidationError)
        evidence["rpc_failure_context"] = {"natural_native_employee_validate_status_failure": True,
            "existing_nonempty_realtime_log_and_flush_callback_preserved": True,
            "exact_currently_saving_key_presence_and_contents_preserved": True,
            "four_callback_manager_identities_and_queues_preserved": True,
            "after_employee_sql_write_source_save_failure_verified": False,
            "fault_injection": False, "external_realtime_emitted_by_helper": False}
        frappe.set_user(actors["local"])
        passed()

        gate("native_pending_source_cancel_retains_actual_project_tasks_without_force_delete")
        protect(primary)
        frappe.set_user(actors["local"])
        old_source, old_project, old_tasks, old_activities = _source_graph(active_source.name)
        old_title = old_project.project_name
        pending = submit(separation(primary))
        _, project, tasks, activities = _source_graph(pending.name)
        assert pending.employee == old_source.employee == primary.name
        assert pending.project != old_source.project and pending.name in project.project_name
        assert old_source.name in old_title and project.project_name != old_title
        _, retained_project, retained_tasks, retained_activities = _source_graph(active_source.name)
        assert retained_project.project_name == old_title and retained_project.name == old_project.name
        assert retained_activities == old_activities and {task.name for task in retained_tasks} == {task.name for task in old_tasks}
        graph_ids = (project.name, {task.name for task in tasks}, activities)
        frappe.set_user(actors["submit"])
        native(pending.cancel)
        pending.reload()
        _, project, tasks, activities = _source_graph(pending.name)
        assert pending.docstatus == 2 and not pending.get(SNAPSHOT) and pending.get(STATUS) == "待办理"
        assert (project.name, {task.name for task in tasks}, activities) == graph_ids
        assert project.status == "Cancelled" and all(task.status == "Cancelled" for task in tasks)
        assert _state(primary.name) == initial and _state(probe.name) == probe_initial
        reject(lambda: native(frappe.get_doc(SOURCE, pending.name).cancel), frappe.ValidationError)
        evidence["native_same_employee_second_source"] = {"first_source": old_source.name, "second_source": pending.name,
            "first_project": old_project.name, "second_project": project.name, "same_employee": True,
            "first_project_title": old_title, "second_project_title": project.project_name, "company": company,
            "distinct_native_titles_contain_full_source_ids": True, "old_title_and_task_links_unchanged": True}
        passed()

        gate("native_copied_cancelled_source_amendment_keeps_original_graph_and_supports_full_hr_manager_chain")
        frappe.set_user(actors["local"])
        assert _state(primary.name) == initial and initial["fields"]["status"] == "Active"
        assert not frappe.has_permission(SOURCE, "amend")
        originals = {}
        for name in (active_source.name, pending.name):
            original, original_project, original_tasks, _ = _source_graph(name)
            assert original.docstatus == 2 and original_project.status == "Cancelled"
            assert all(task.status == "Cancelled" for task in original_tasks)
            originals[name] = _digest((original.as_dict(), original_project.as_dict(),
                                      [task.as_dict() for task in original_tasks]))

        def assert_originals_unchanged():
            for name, digest in originals.items():
                original, original_project, original_tasks, _ = _source_graph(name)
                assert digest == _digest((original.as_dict(), original_project.as_dict(),
                                          [task.as_dict() for task in original_tasks])), "修订改变旧来源或精确项目任务内容"

        original = frappe.get_doc(SOURCE, active_source.name)
        original.check_permission("read")
        assert original.get(SNAPSHOT) and original.get(STATUS) == "已撤销"
        # Preserve every no_copy field as the Desk from_amend=1 copy does.
        # Native in_test=False still resets parent/child docstatus and names.
        copied = remember(native(lambda: frappe.copy_doc(original, ignore_no_copy=True)))
        assert copied.is_new() and not copied.docstatus
        assert copied.get(SNAPSHOT) == original.get(SNAPSHOT) and copied.get(STATUS) == "已撤销"
        assert copied.activities and all(row.is_new() for row in copied.activities)
        assert [row.task for row in copied.activities] == [row.task for row in original.activities]
        # Do not clean any copied server field here. The production guard must
        # verify the readable cancelled origin and normalize the whole copy.
        assert copied.project == original.project
        copied.amended_from = original.name
        native(copied.insert)
        assert copied.docstatus == 0 and not copied.project and not copied.get(SNAPSHOT)
        assert copied.get(STATUS) == "待办理" and all(not row.task for row in copied.activities)
        amended = submit(copied)
        _, amended_project, amended_tasks, amended_activities = _source_graph(amended.name)
        assert amended.amended_from == original.name and amended.employee == primary.name and amended.company == company
        assert amended.project not in {old_project.name, pending.project}
        assert {task.name for task in amended_tasks}.isdisjoint({row.task for row in original.activities})
        assert {row.name for row in amended.activities}.isdisjoint({row.name for row in original.activities})
        amended_graph_ids = (amended_project.name, {task.name for task in amended_tasks}, amended_activities)
        assert_originals_unchanged()
        complete(amended)
        assert _state(primary.name) == initial
        assert_originals_unchanged()
        business(amended, finalize_separation)
        amended_finalized = _state(primary.name)
        assert amended_finalized["fields"]["status"] == "Left"
        _snapshot(amended, initial, amended_finalized, actors["local"])
        assert_originals_unchanged()
        business(amended, reverse_separation)
        assert _state(primary.name) == initial
        _snapshot(amended, initial, amended_finalized, actors["local"], state="reversed")
        assert_originals_unchanged()
        source_versions, employee_versions = set(_versions(SOURCE, amended.name)), set(_versions("Employee", primary.name))
        native(frappe.get_doc(SOURCE, amended.name).cancel)
        amended.reload()
        _, amended_project, amended_tasks, amended_activities = _source_graph(amended.name)
        assert amended.docstatus == 2 and amended.get(STATUS) == "已撤销" and amended.amended_from == original.name
        assert (amended_project.name, {task.name for task in amended_tasks}, amended_activities) == amended_graph_ids
        assert amended_project.status == "Cancelled" and all(task.status == "Cancelled" for task in amended_tasks)
        assert _state(primary.name) == initial and employee_versions == set(_versions("Employee", primary.name))
        cancel_versions = {name: data for name, data in _versions(SOURCE, amended.name).items() if name not in source_versions}
        assert cancel_versions and any(["docstatus", 1, 2] in data.get("changed", []) for data in cancel_versions.values())
        records.update(("Version", name) for name in cancel_versions)
        assert_originals_unchanged()
        evidence["native_copied_amended_source"] = {"original_source": original.name, "amended_source": amended.name,
            "original_project": original.project, "amended_project": amended_project.name,
            "amended_project_title": amended_project.project_name, "company": company,
            "copy_api": "frappe.copy_doc(ignore_no_copy=True) with in_test=False",
            "from_amend_semantics_preserve_old_snapshot_state_project_tasks": True,
            "copied_server_fields_normalized_by_server": True,
            "native_amended_from_cancelled_validation": True, "actor_amend_permission": False,
            "path": "explicit new-source copy and native insert; not the Desk Amend action",
            "business_actor_roles": ["HR Manager"], "original_sources_projects_tasks_full_contents_unchanged": True,
            "source_cancel_versions": sorted(cancel_versions), "employee_restored_exactly": True}
        passed()

        evidence["native_counts_before_rollback"] = {
            doctype: {"before": counts_before["tab" + doctype], "during": frappe.db.count(doctype)}
            for doctype in (SOURCE, "Employee Boarding Activity", "Project", "Task", "Employee", "Version",
                            "User", "Has Role", "User Permission", "Email Queue", "Notification Log", "Error Log", *PAYROLL_TYPES)}
    except Exception as exception:
        exception.add_note("native separation smoke failed at gate: " + current_gate)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
            for doc in objects:
                if doc.name:
                    records.add((doc.doctype, doc.name))
                records.update((row.doctype, row.name) for row in doc.get_all_children() if row.name)
            employees = [name for doctype, name in records if doctype == "Employee"]
            if employees:
                for field in frappe.get_meta("Employee").get_table_fields():
                    records.update((field.options, name) for name in frappe.get_all(field.options,
                        filters={"parenttype": "Employee", "parent": ["in", employees]}, pluck="name"))
            names = list({name for _, name in records})
            if names:
                records.update(("Version", name) for name in frappe.get_all("Version",
                               filters={"docname": ["in", names]}, pluck="name"))
        finally:
            try:
                _end_rollback_only(point)
            finally:
                _restore_callbacks(callbacks_before)
                _restore_local_context(context_before)
                frappe.in_test = original_test
                frappe.set_user(original_user)
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
            assert all(not frappe.db.exists(doctype, name) for doctype, name in records), "回滚残留合成记录/Version"
            assert _tab_counts(tables)[1] == counts_before, "回滚后全 tab 表精确计数不同"
            assert set(frappe.get_all("Error Log", pluck="name")) == logs_before, "直接 Doc/RPC 验收新增 Error Log"
            assert _digest(_employee_contents()) == staff_before, "回滚后 Employee/全部子表内容不同"
            assert _digest(_user_contents()) == users_before, "回滚后 User/Auth/Profile/roles/UP 内容不同"
            assert _ledger_snapshot() == ledger_before, "回滚后 whole GL/SLE 内容不同"
    checks.extend(("rollback_all_synthetic_sources_tasks_projects_payroll_versions_and_all_tab_counts",
                   "rollback_whole_employee_children_user_auth_roles_up_gl_sle_and_zero_error_logs"))
    return {"passed": True, "checks": checks, "gate_count": len(checks), "business_data": "rolled_back",
            "record_counts": dict(Counter(doctype for doctype, _ in records)), "evidence": evidence,
            "table_counts_before": counts_before, "table_counts_after": _tab_counts(tables)[1], "table_engines": tables,
            "employee_contents_hash_before": staff_before, "employee_contents_hash_after": _digest(_employee_contents()),
            "user_contents_hash_before": users_before, "user_contents_hash_after": _digest(_user_contents()),
            "whole_ledger_hashes_before": ledger_before, "whole_ledger_hashes_after": _ledger_snapshot(),
            "error_logs_created": 0, "legacy_persisted_cancellation_verified": False,
            "execution": "direct Python once, serial rollback-only experiment"}
