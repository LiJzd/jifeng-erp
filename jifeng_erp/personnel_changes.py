"""Exact, reversible native personnel changes, without global history deletion.

Install ``jf_history_snapshot`` separately on Employee Promotion and Employee
Transfer: Long Text, hidden/read_only/no_copy/print_hide/allow_on_submit = 1.
Register EmployeePromotionMixin/EmployeeTransferMixin with extend_doctype_class.
Register personnel_change_has_permission and the two query-condition helpers.
The mixins retain native submission and Employee.save; they replace native
on_cancel, whose history deletion is not scoped to an Employee.

This first safe chain supports the six job properties below, revised promotion
CTC, and same-ID company transfer. Account/permission and other employee property
changes, new Employee IDs, and untracked legacy cancellation require a separate
review/migration. Snapshot JSON is metadata, never a Dynamic Link backlink.
"""

from datetime import date, datetime
from decimal import Decimal
import json

import frappe
from frappe.model import get_permitted_fields


SNAPSHOT = "jf_history_snapshot"
JOB_FIELDS = {"department", "designation", "branch", "employment_type", "grade", "reports_to"}
WATCH_FIELDS = {"company", "department", "designation", "branch", "status", "date_of_joining",
                "relieving_date", "user_id"}
HISTORY_FIELDS = ("branch", "department", "designation", "from_date", "to_date")
_SERVER = object()
TAMPER = "人事追溯快照只能由服务器生成，不能修改"
LEGACY = "旧人事单据缺少可靠追溯快照，请先人工核对迁移"
CHANGED = "员工或精确工作历史已有后续变更，不能自动取消本单"
FIELD_DENIED = "没有读取或修改所涉员工字段的权限"
SOURCE_TYPES = {"Employee Promotion": "promotion_details", "Employee Transfer": "transfer_details"}
SOURCE_KEYS = {"doctype", "name", "employee", "company", "date", "details", "new_company",
               "create_new_employee_id", "current_ctc", "revised_ctc"}
HISTORY_KEYS = {"name", "parent", "parenttype", "parentfield", "idx", *HISTORY_FIELDS}


def _value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _fields(employee, names):
    return {name: _value(employee.get(name)) for name in sorted(names)}


def _history(employee):
    result = []
    for row in employee.get("internal_work_history") or []:
        if (not row.name or row.parent != employee.name or row.parenttype != "Employee"
                or row.parentfield != "internal_work_history"):
            frappe.throw("员工工作历史缺少可靠的精确父子标识，请先人工核对迁移")
        result.append({"name": row.name, "parent": row.parent, "parenttype": row.parenttype,
                       "parentfield": row.parentfield, "idx": row.idx,
                       **{field: _value(row.get(field)) for field in HISTORY_FIELDS}})
    if len({row["name"] for row in result}) != len(result):
        frappe.throw("员工工作历史明细标识重复，请先人工核对迁移")
    return result


def _permitted_employee_fields(permission, user=None):
    # Both column/fieldname APIs exclude Table (NO_VALUE_FIELDS). Check the
    # parent Table's permlevel explicitly against native metadata role levels.
    permitted = set(get_permitted_fields("Employee", user=user, permission_type=permission, ignore_virtual=True))
    meta = frappe.get_meta("Employee")
    field = meta.get_field("internal_work_history")
    levels = set(meta.get_permlevel_access(permission_type=permission, user=user))
    # Match native get_permitted_fieldnames' read-sharing level-0 rule. Full
    # Employee read below still checks this exact document, not just any share.
    if permission == "read" and 0 not in levels and frappe.share.get_shared("Employee", user, rights=["read"], limit=1):
        levels.add(0)
    if (field and field.fieldtype == "Table" and field.options == "Employee Internal Work History"
            and (field.permlevel or 0) in levels):
        permitted.add("internal_work_history")
    return permitted


def _field_permissions(names):
    for permission in ("read", "write"):
        permitted = _permitted_employee_fields(permission)
        child_fields = set(get_permitted_fields("Employee Internal Work History", parenttype="Employee",
                                               permission_type=permission, ignore_virtual=True))
        if not set(names).issubset(permitted) or not set(HISTORY_FIELDS).issubset(child_fields):
            frappe.throw(FIELD_DENIED, frappe.PermissionError)


def _parse_snapshot(raw, doctype, name):
    """Accept only the server schema; neither reads nor reversal trust extra keys."""
    def require(valid):
        if not valid:
            raise ValueError("Invalid server personnel snapshot")

    snapshot = json.loads(raw)
    require(isinstance(snapshot, dict) and set(snapshot) == {
        "version", "state", "source", "before", "after", "changed_fields", "added_history_ids", "to_date_changes"})
    require(snapshot["version"] == 1 and snapshot["state"] in ("submitted", "cancelled"))
    source = snapshot["source"]
    require(isinstance(source, dict) and set(source) == SOURCE_KEYS)
    require(source["doctype"] == doctype and source["name"] == name and source["employee"])
    require(isinstance(source["details"], list))
    selected = set()
    for detail in source["details"]:
        require(isinstance(detail, dict) and set(detail) == {"name", "fieldname", "current", "new"})
        require(detail["fieldname"] in JOB_FIELDS and detail["fieldname"] not in selected)
        require(isinstance(detail["current"], str) and isinstance(detail["new"], str))
        selected.add(detail["fieldname"])
    expected_fields = WATCH_FIELDS | selected | ({"ctc"} if doctype == "Employee Promotion" else set())
    for section in ("before", "after"):
        require(isinstance(snapshot[section], dict) and set(snapshot[section]) == {"fields", "history"})
        require(isinstance(snapshot[section]["fields"], dict) and set(snapshot[section]["fields"]) == expected_fields)
        require(isinstance(snapshot[section]["history"], list))
        ids = set()
        for row in snapshot[section]["history"]:
            require(isinstance(row, dict) and set(row) == HISTORY_KEYS)
            require(isinstance(row["name"], str) and row["name"] and row["name"] not in ids)
            require(row["parent"] == source["employee"] and row["parenttype"] == "Employee")
            require(row["parentfield"] == "internal_work_history" and isinstance(row["idx"], int))
            ids.add(row["name"])
    require(isinstance(snapshot["changed_fields"], list) and set(snapshot["changed_fields"]).issubset(expected_fields))
    require(isinstance(snapshot["added_history_ids"], list))
    require(all(isinstance(value, str) for value in snapshot["added_history_ids"]))
    require(isinstance(snapshot["to_date_changes"], list))
    require(all(isinstance(row, dict) and set(row) == {"name", "before", "after"}
                for row in snapshot["to_date_changes"]))
    return snapshot


def _readable_fields(user):
    permitted = _permitted_employee_fields("read", user)
    child = set(get_permitted_fields("Employee Internal Work History", parenttype="Employee", user=user,
                                    permission_type="read", ignore_virtual=True))
    return permitted if set(HISTORY_FIELDS).issubset(child) else set()


def _source_fields_readable(document, permitted):
    required = WATCH_FIELDS | {"internal_work_history"}
    if document.doctype == "Employee Promotion":
        required |= {"ctc"}
    required |= {row.get("fieldname") for row in document.get(SOURCE_TYPES[document.doctype]) or []}
    raw = document.get(SNAPSHOT)
    if raw:
        try:
            snapshot = _parse_snapshot(raw, document.doctype, document.name)
            if snapshot["source"]["employee"] != document.get("employee"):
                return False
            required |= set(snapshot["before"]["fields"]) | set(snapshot["after"]["fields"])
        except (AssertionError, KeyError, TypeError, ValueError):
            return False
    return required.issubset(permitted)


def personnel_change_has_permission(doc, ptype, user=None, debug=False):
    """Deny source data access if it would reveal unreadable Employee values.

    True is an additional necessary condition, never a native role/User Permission
    grant. Hidden Long Text is still returned by source APIs, so guard the whole
    source, including print/export and legacy property current values.
    """
    if doc.doctype not in SOURCE_TYPES or ptype not in {"read", "select", "print", "email", "export", "report"}:
        return True
    if not doc.get("employee"):
        return bool(doc.is_new() and not doc.get(SNAPSHOT) and not doc.get(SOURCE_TYPES[doc.doctype]))
    if not _source_fields_readable(doc, _readable_fields(user)):
        return False
    try:
        employee = frappe.get_doc("Employee", doc.employee)
        return bool(employee.has_permission("read", user=user))
    except frappe.PermissionError:
        return False


def _query_conditions(doctype, user):
    """Filter source list/export, which do not invoke each document's read hook.

    Native Employee get_list + full read are both required. Internal source/child
    probes inspect only the server schema and field names; only readable source
    names reach the SQL condition. This intentionally favors safety over a global
    SQL JSON-schema rule and does not return snapshots or hidden employee IDs.
    """
    permitted = _readable_fields(user)
    required = WATCH_FIELDS | {"internal_work_history"} | ({"ctc"} if doctype == "Employee Promotion" else set())
    if not required.issubset(permitted):
        return "1=0"
    try:
        employees = frappe.get_list("Employee", fields=["name"], user=user, limit_page_length=0)
    except frappe.PermissionError:
        return "1=0"
    allowed = []
    for row in employees:
        employee = frappe.get_doc("Employee", row.name)
        if employee.has_permission("read", user=user):
            allowed.append(row.name)
    if not allowed:
        return "1=0"
    sources = frappe.get_all(doctype, filters={"employee": ["in", allowed]},
                             fields=["name", "employee", SNAPSHOT], limit_page_length=0)
    if not sources:
        return "1=0"
    # One batched child probe also covers legacy source details with no snapshot.
    details = frappe.get_all("Employee Property History", filters={"parenttype": doctype,
        "parentfield": SOURCE_TYPES[doctype], "parent": ["in", [row.name for row in sources]]},
        fields=["parent", "fieldname"], limit_page_length=0)
    grouped = {}
    for row in details:
        grouped.setdefault(row.parent, []).append(row)
    names = []
    for row in sources:
        row.doctype = doctype
        row[SOURCE_TYPES[doctype]] = grouped.get(row.name, [])
        if _source_fields_readable(row, permitted):
            names.append(frappe.db.escape(row.name))
    return f"`tab{doctype}`.`name` in ({','.join(names)})" if names else "1=0"


def promotion_query_conditions(user=None, doctype=None):
    return _query_conditions("Employee Promotion", user)


def transfer_query_conditions(user=None, doctype=None):
    return _query_conditions("Employee Transfer", user)


def _employee(name):
    employee = frappe.get_doc("Employee", name, for_update=True)
    employee.check_permission("read")
    employee.check_permission("write")
    return employee


def _intent(document):
    return {
        "doctype": document.doctype, "name": document.name, "employee": document.employee,
        "company": document.company, "date": _value(document.get(document._change_date)),
        "details": [{"name": row.name, "fieldname": row.fieldname,
                     "current": "" if row.get("current") is None else str(row.current),
                     "new": "" if row.get("new") is None else str(row.new)}
                    for row in document.get(document._change_details) or []],
        "new_company": document.get("new_company"),
        "create_new_employee_id": int(bool(document.get("create_new_employee_id"))),
        "current_ctc": _value(document.get("current_ctc")),
        "revised_ctc": _value(document.get("revised_ctc")),
    }


def _delta(before, after):
    previous = {row["name"]: row for row in before["history"]}
    current = {row["name"]: row for row in after["history"]}
    if ([row["name"] for row in after["history"] if row["name"] in previous]
            != [row["name"] for row in before["history"]]):
        frappe.throw("原生提交改动了既有工作历史标识或顺序，不能建立安全追溯")
    dates = []
    for name, old in previous.items():
        new = current[name]
        if any(old[field] != new[field] for field in old if field != "to_date"):
            frappe.throw("原生提交改动了既有工作历史内容，不能建立安全追溯")
        if old["to_date"] != new["to_date"]:
            dates.append({"name": name, "before": old["to_date"], "after": new["to_date"]})
    return (sorted(name for name in before["fields"] if before["fields"][name] != after["fields"][name]),
            [row["name"] for row in after["history"] if row["name"] not in previous], dates)


class _PersonnelChangeMixin:
    def _installed(self):
        field = self.meta.get_field(SNAPSHOT)
        if not (field and field.fieldtype == "Long Text" and field.hidden and field.read_only
                and field.no_copy and field.allow_on_submit):
            frappe.throw("请先安装人事单据只读追溯快照字段")

    def _guard_snapshot(self):
        self._installed()
        stored = None if self.is_new() else frappe.db.get_value(self.doctype, self.name, SNAPSHOT)
        if (self.get(SNAPSHOT) or "") != (stored or ""):
            frappe.throw(TAMPER)
        return stored

    def _decode(self):
        raw = self._guard_snapshot()
        try:
            snapshot = _parse_snapshot(raw or "null", self.doctype, self.name)
            if snapshot["state"] != "submitted":
                raise ValueError("Snapshot is not submitted")
        except (AssertionError, KeyError, TypeError, ValueError):
            frappe.throw(LEGACY)
        return snapshot

    def _check_intent(self, snapshot, fetched=False):
        source = snapshot["source"]
        if fetched:
            # Frappe _validate_links refreshes fetch_from on a cancelling (2)
            # source. Same-ID transfer therefore refetches the NEW company;
            # promotion current_ctc=0 also refetches via fetch_if_empty. Only
            # those exact saved/current values may normalize to the saved source.
            if self.company in (source["company"], snapshot["after"]["fields"]["company"]):
                self.company = source["company"]
            if self.doctype == "Employee Promotion":
                allowed = (source["current_ctc"], snapshot["after"]["fields"].get("ctc"))
                if _value(self.get("current_ctc")) in allowed:
                    self.current_ctc = source["current_ctc"]
        if _intent(self) != source:
            frappe.throw("人事单据来源或已审核变更内容与服务器快照不一致")

    def before_validate(self):
        self._guard_snapshot()
        method = getattr(super(), "before_validate", None)
        if method:
            method()

    def before_update_after_submit(self):
        raw = self._guard_snapshot()
        if raw:
            self._check_intent(self._decode())
        method = getattr(super(), "before_update_after_submit", None)
        if method:
            method()

    def before_submit(self):
        self.check_permission("submit")
        if self._guard_snapshot():
            frappe.throw("本单已有服务器追溯快照，不能重复提交")
        if self.get("create_new_employee_id"):
            frappe.throw("本批安全人事追溯暂不支持创建新员工编号，请使用单独核对迁移流程")
        # Preserve native future-date and any other installed pre-submit checks.
        method = getattr(super(), "before_submit", None)
        if method:
            method()
        employee = _employee(self.employee)
        if not self.get(self._change_date):
            frappe.throw("人事变更必须填写原生生效日期")
        if employee.company != self.company:
            frappe.throw("人事变更公司与实际员工公司不一致")
        selected = set()
        meta = frappe.get_meta("Employee")
        for row in self.get(self._change_details) or []:
            field = meta.get_field(row.fieldname)
            if not field:
                frappe.throw("人事变更包含无效员工属性")
            _field_permissions({row.fieldname})
            if row.fieldname not in JOB_FIELDS:
                frappe.throw("本批安全人事追溯仅支持岗位属性，其他员工属性须单独核对")
            if row.fieldname in selected:
                frappe.throw("人事变更员工属性不能重复")
            selected.add(row.fieldname)
        watched = WATCH_FIELDS | selected
        if self.doctype == "Employee Promotion":
            watched |= {"ctc"}
        _field_permissions(watched | {"internal_work_history"})
        for row in self.get(self._change_details) or []:
            old = _value(employee.get(row.fieldname))
            row.current = "" if old is None else str(old)
        if self.doctype == "Employee Promotion":
            self.current_ctc = employee.ctc
        target_company = self.get("new_company") or employee.company
        for company in {employee.company, target_company}:
            frappe.get_doc("Company", company).check_permission("select")
        prospective = frappe.get_doc(employee.as_dict())
        for row in self.get(self._change_details) or []:
            prospective.set(row.fieldname, row.get("new") or None)
        if self.doctype == "Employee Transfer" and target_company != employee.company:
            prospective.company = target_company
            prospective.date_of_joining = self.get(self._change_date)
        prospective.check_permission("read")
        prospective.check_permission("write")
        if prospective.department:
            department = frappe.get_doc("Department", prospective.department)
            department.check_permission("select")
            if department.get("company") and department.company != target_company:
                frappe.throw("目标部门必须属于目标员工公司")
        self.flags.jf_personnel_pending_submit = (
            _SERVER, {"fields": _fields(employee, watched), "history": _history(employee)}, watched, _intent(self))

    def on_submit(self):
        pending = self.flags.get("jf_personnel_pending_submit")
        if not (pending and pending[0] is _SERVER):
            frappe.throw("人事提交缺少服务器锁定的员工原值快照")
        _, before, watched, intent = pending
        if _intent(self) != intent:
            frappe.throw("人事提交内容在服务器快照后发生变化")
        super().on_submit()  # Native update_employee_work_history(cancel=False) and native Employee.save.
        employee = _employee(self.employee)
        _field_permissions(watched | {"internal_work_history"})
        after = {"fields": _fields(employee, watched), "history": _history(employee)}
        changed, added, dates = _delta(before, after)
        snapshot = {"version": 1, "state": "submitted", "source": intent,
                    "before": before, "after": after, "changed_fields": changed,
                    "added_history_ids": added, "to_date_changes": dates}
        # Parent has already been written on_submit. This one server-owned
        # metadata field is persisted in the same transaction; native source
        # save_version runs after on_submit and sees its value in this object.
        self.db_set(SNAPSHOT, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), update_modified=False)
        self.flags.pop("jf_personnel_pending_submit", None)

    def _cancel_state(self, snapshot):
        employee = _employee(snapshot["source"]["employee"])
        names = set(snapshot["after"]["fields"])
        _field_permissions(names | {"internal_work_history"})
        if (_fields(employee, names) != snapshot["after"]["fields"]
                or _history(employee) != snapshot["after"]["history"]):
            frappe.throw(CHANGED)
        # The JSON must describe an exact reversible native delta, even if a
        # historical administrative migration wrote a malformed snapshot.
        expected = _delta(snapshot["before"], snapshot["after"])
        if expected != (snapshot["changed_fields"], snapshot["added_history_ids"], snapshot["to_date_changes"]):
            frappe.throw(LEGACY)
        restored = frappe.get_doc(employee.as_dict())
        for name in snapshot["changed_fields"]:
            restored.set(name, snapshot["before"]["fields"][name])
        restored.check_permission("read")
        restored.check_permission("write")
        for company in {employee.company, restored.company}:
            frappe.get_doc("Company", company).check_permission("select")
        return employee

    def before_cancel(self):
        self.check_permission("read")
        self.check_permission("cancel")
        snapshot = self._decode()
        self._check_intent(snapshot, fetched=True)
        self._cancel_state(snapshot)
        method = getattr(super(), "before_cancel", None)
        if method:
            method()
        self.flags.jf_personnel_pending_cancel = (_SERVER, snapshot)

    def on_cancel(self):
        pending = self.flags.get("jf_personnel_pending_cancel")
        if not (pending and pending[0] is _SERVER):
            frappe.throw("人事取消缺少服务器锁定的精确追溯校验")
        snapshot = self._decode()
        if snapshot != pending[1]:
            frappe.throw(TAMPER)
        self._check_intent(snapshot)
        employee = self._cancel_state(snapshot)
        added = set(snapshot["added_history_ids"])
        employee.set("internal_work_history", [row for row in employee.internal_work_history if row.name not in added])
        old_dates = {change["name"]: change["before"] for change in snapshot["to_date_changes"]}
        for row in employee.internal_work_history:
            if row.name in old_dates:
                row.to_date = old_dates[row.name]
        for name in snapshot["changed_fields"]:
            employee.set(name, snapshot["before"]["fields"][name])
        employee.save()  # Native row update/removal is scoped to this Employee's child table.
        restored = _employee(employee.name)
        if (_fields(restored, set(snapshot["before"]["fields"])) != snapshot["before"]["fields"]
                or _history(restored) != snapshot["before"]["history"]):
            frappe.throw("员工原生保存未精确恢复本单变更，请回滚后人工核对")
        snapshot["state"] = "cancelled"
        self.db_set(SNAPSHOT, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), update_modified=False)
        self.flags.pop("jf_personnel_pending_cancel", None)
        # Do not call native on_cancel: it deletes matching rows across parents.


class EmployeePromotionMixin(_PersonnelChangeMixin):
    _change_details = "promotion_details"
    _change_date = "promotion_date"


class EmployeeTransferMixin(_PersonnelChangeMixin):
    _change_details = "transfer_details"
    _change_date = "transfer_date"
