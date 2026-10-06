"""Freeze personnel classification at the first native wage approval.

Drafts contain only a current observation. Protected level-one fields retain
the evidence after approval; they are not a claim about the work date. A narrow
business projection uses the viewer's current native rights to the exact wage,
employee, company and original Supplement, rather than granting access to the
protected wage fields or to the whole HR archive.
"""

from datetime import datetime
import hashlib
import json

import frappe

from jifeng_erp.archive_versions import same_version


WAGE = "Jifeng Wage Entry"
SUPPLEMENT = "Jifeng Employee Supplement"
SNAPSHOT_FIELDS = (
    "personnel_type_at_approval", "personnel_type_snapshot_status",
    "personnel_type_snapshot_time", "personnel_type_snapshot_source",
    "personnel_type_snapshot_version",
)
SOURCE_FIELDS = ("name", "creation", "employee", "company", "term_type", "modified")
RESTORE_TOKEN = object()
SOURCES = (WAGE, SUPPLEMENT, "Employee", "Company")


def _value(document, field):
    return document.get(field) if isinstance(document, dict) else getattr(document, field, None)


def _deny():
    frappe.throw("没有读取该计薪记录人员类型及准确人事来源的权限", frappe.PermissionError)


def _changed():
    frappe.throw("人员类型或其准确来源已变化，请重新读取后核对")


def _name(value):
    if (not isinstance(value, str) or not value or len(value) > 140
            or value != value.strip() or any(ord(char) < 32 for char in value)):
        _changed()
    return value


def _timestamp(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.isoformat(timespec="microseconds")
    if not isinstance(value, str) or not same_version(value, value):
        _changed()
    return datetime.fromisoformat(value).isoformat(timespec="microseconds")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _source_identity(source):
    # Employee is the native autoname, so deleting and recreating a Supplement
    # naturally reuses its name. Creation binds this particular incarnation.
    return _digest({"name": source["name"], "creation": source["creation"]})


def _permission_state():
    """Internal raw authorization inputs; never an HR business-data bypass."""
    from jifeng_erp.tool_archive_export import _freeze

    user, state = frappe.session.user, []
    for doctype, filters, fields in (
        ("User", {"name": user}, ["name", "enabled", "user_type"]),
        ("Has Role", {"parenttype": "User", "parent": user}, ["*"]),
        ("User Permission", {"user": user}, ["*"]),
        ("DocPerm", {"parent": ["in", SOURCES]}, ["*"]),
        ("Custom DocPerm", {"parent": ["in", SOURCES]}, ["*"]),
        ("DocType", {"name": ["in", SOURCES]}, ["*"]),
        ("DocField", {"parent": ["in", SOURCES]}, ["*"]),
        ("Custom Field", {"dt": ["in", SOURCES]}, ["*"]),
        ("Property Setter", {"doc_type": ["in", SOURCES]}, ["*"]),
        ("DocShare", {"share_doctype": ["in", SOURCES]}, ["*"]),
    ):
        rows = frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0)
        if doctype == "DocShare":
            rows = [row for row in rows if row.get("user") == user or row.get("everyone")]
        state.append((doctype, tuple(sorted((_freeze(row) for row in rows), key=repr))))
    return _freeze((user, state))


def _fields_unmasked(doctype, required):
    meta = frappe.get_meta(doctype)
    if frappe.session.user == "Administrator":
        return True
    levels = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
    return not any((field := meta.get_field(name)) and field.get("mask")
                   and (field.permlevel or 0) not in levels for name in required)


def _fields_readable(doctype, required):
    return (frappe.session.user == "Administrator"
            or set(required).issubset(frappe.get_meta(doctype).get_permitted_fieldnames(permission_type="read"))
            and _fields_unmasked(doctype, required))


def _source(wage, *, lock=False):
    """Return no private observation at all when the actor lacks source rights.

    Identity enumeration is internal and limited to this exact employee. It
    detects multiple/wrong-company rows instead of guessing a matching row.
    Source locks follow the existing Job Card / Work Order / Supplement order;
    no Employee lock is acquired here.
    """
    if (not frappe.has_permission(SUPPLEMENT, "read")
            or not _fields_readable(SUPPLEMENT, {"employee", "company", "term_type"})):
        return "无权核实", None
    names = frappe.db.sql(
        "SELECT name FROM `tabJifeng Employee Supplement` WHERE employee = %s "
        "ORDER BY name LIMIT 2" + (" FOR UPDATE" if lock else ""),
        (wage.employee,), as_dict=True,
    )
    if not names:
        return "档案未记录", None
    sources = []
    for identity in names:
        source = frappe.get_doc(SUPPLEMENT, identity.name, for_update=lock)
        try:
            source.check_permission("read")
        except frappe.PermissionError:
            return "无权核实", None
        if not _fields_readable(SUPPLEMENT, {"employee", "company", "term_type"}):
            return "无权核实", None
        listed = frappe.get_list(SUPPLEMENT, filters={"name": source.name},
                                 fields=list(SOURCE_FIELDS), limit_page_length=1)
        if not listed:
            return "无权核实", None
        if len(listed) != 1 or not set(SOURCE_FIELDS).issubset(listed[0]):
            _changed()
        row = {field: _value(source, field) for field in SOURCE_FIELDS}
        candidate = dict(listed[0])
        if (any(_timestamp(row[field]) != _timestamp(candidate[field]) for field in ("modified", "creation"))
                or any(row[field] != candidate[field] for field in SOURCE_FIELDS if field not in {"modified", "creation"})):
            _changed()
        for field in ("modified", "creation"):
            row[field] = _timestamp(row[field])
        try:
            source.check_permission("read")
        except frappe.PermissionError:
            _deny()
        if not _fields_readable(SUPPLEMENT, {"employee", "company", "term_type"}):
            _changed()
        sources.append(row)
    if (len(sources) != 1 or sources[0]["employee"] != wage.employee
            or sources[0]["company"] != wage.company):
        _changed()
    row = sources[0]
    _name(row["name"])
    if row["term_type"] not in (None, "", "临时", "长期"):
        _changed()
    return ("已核实" if row["term_type"] else "档案未记录"), row


def guard_approved_classification(wage):
    """Cancel JSON skips validate; call this from both native paths."""
    before = getattr(wage, "get_doc_before_save", lambda: None)()
    if not before or before.docstatus not in (1, 2):
        return False
    # Native browser payloads omit level-one fields. Preserve authoritative
    # values for those unreadable fields, including deliberately supplied
    # values, instead of turning an omitted field into an edit or an oracle.
    for field in SNAPSHOT_FIELDS:
        if field != "personnel_type_snapshot_status" and not _fields_readable(WAGE, {field}):
            setattr(wage, field, _value(before, field))
    for field in ("company", "employee", *SNAPSHOT_FIELDS):
        actual, previous = _value(wage, field), _value(before, field)
        if field == "personnel_type_snapshot_time" and actual and previous:
            equal = _timestamp(actual) == _timestamp(previous)
        else:
            equal = (actual or "") == (previous or "")
        if not equal:
            frappe.throw("已审核工资的人员类型快照和员工公司不能修改，请重新读取原单")
    return True


def authorize_classification_restore(wage):
    """A native restore preserves an exact deleted approved/cancelled record."""
    if not getattr(wage.flags, "from_restore", False) or wage.docstatus not in (1, 2):
        return
    if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
        _deny()
    if not frappe.has_permission("Deleted Document", "read"):
        _deny()
    wage.check_permission("read")
    # Naming can change after before_insert. Compare the original saved name
    # and all existing cancellation-bound business fields before that change.
    from jifeng_erp.wage_rate_references import CANCEL_WAGE_FIELDS

    identity = ("name", "docstatus", *CANCEL_WAGE_FIELDS, *SNAPSHOT_FIELDS)
    rows = frappe.db.sql("SELECT data FROM `tabDeleted Document` "
                         "WHERE deleted_doctype = %s AND data LIKE %s FOR UPDATE",
                         (WAGE, "%" + _name(wage.name) + "%"), as_dict=True)
    for row in rows:
        try:
            original = json.loads(row.data)
        except (ValueError, TypeError):
            continue
        if (isinstance(original, dict) and original.get("doctype") == WAGE
                and all((_value(wage, field) or "") == (original.get(field) or "")
                        for field in identity if field != "personnel_type_snapshot_time")
                and ((not _value(wage, "personnel_type_snapshot_time")
                      and not original.get("personnel_type_snapshot_time"))
                     or (_value(wage, "personnel_type_snapshot_time")
                         and original.get("personnel_type_snapshot_time")
                         and _timestamp(_value(wage, "personnel_type_snapshot_time"))
                         == _timestamp(original.get("personnel_type_snapshot_time"))))):
            wage.flags.personnel_classification_restore_token = RESTORE_TOKEN
            wage.flags.personnel_classification_restored_values = {
                field: _value(wage, field) for field in SNAPSHOT_FIELDS}
            return
    frappe.throw("人员类型快照恢复必须与准确原删除工资单据及内容一致")


def bind_classification(wage):
    """Draft observation is replaced by the actual first submit actor's read."""
    if guard_approved_classification(wage):
        return  # Old blank evidence remains blank; never infer past type now.
    if getattr(wage.flags, "personnel_classification_restore_token", None) is RESTORE_TOKEN:
        # Native insert may reset level-one fields after before_insert. Only
        # the server-owned token can reapply values already matched to the
        # exact tombstone; user flags/values never authorize this branch.
        for field, value in wage.flags.personnel_classification_restored_values.items():
            setattr(wage, field, value)
        return
    if wage.docstatus == 2:
        frappe.throw("已取消工资的人员类型快照只能沿准确原生恢复保留")
    if frappe.session.user == "Guest":
        _deny()
    before = getattr(wage, "get_doc_before_save", lambda: None)()
    wage.check_permission("write" if before else "create")
    if wage.docstatus == 1:
        wage.check_permission("submit")
    if not _fields_readable(WAGE, {"company", "employee"}):
        status, source = "无权核实", None
    else:
        first = _source(wage, lock=True)
        second = _source(wage, lock=True)
        if first != second:
            _changed()
        status, source = second
    from frappe.utils import now_datetime

    values = dict.fromkeys(SNAPSHOT_FIELDS, None)
    values.update(personnel_type_snapshot_status=status, personnel_type_snapshot_time=now_datetime())
    if source:
        values.update(personnel_type_at_approval=source["term_type"] or None,
                      personnel_type_snapshot_source=_source_identity(source),
                      personnel_type_snapshot_version=_digest(source))
    for field, value in values.items():
        setattr(wage, field, value)


def _observation(name, version):
    wage = frappe.get_doc(WAGE, name)
    wage.check_permission("read")
    if (not _fields_readable(WAGE, {"company", "employee", "personnel_type_snapshot_status"})
            or not _fields_unmasked(WAGE, {"personnel_type_at_approval", "personnel_type_snapshot_time"})):
        _deny()
    if not same_version(wage.modified, version):
        _changed()
    frappe.get_doc("Company", wage.company).check_permission("select")
    employee = frappe.get_doc("Employee", wage.employee)
    employee.check_permission("read")
    if (not _fields_readable("Employee", {"company"}) or employee.company != wage.company):
        _deny()
    captured = {field: _value(wage, field) for field in SNAPSHOT_FIELDS}
    empty = not any(captured.values())
    if empty and wage.docstatus in (1, 2):
        source = None
        status = "历史未记录"
    else:
        status = captured["personnel_type_snapshot_status"]
        if status not in {"已核实", "档案未记录", "无权核实"}:
            _changed()
        _, source = _source(wage)
        if (not source or not captured["personnel_type_snapshot_source"]
                or _source_identity(source) != captured["personnel_type_snapshot_source"]):
            _deny()
        if wage.docstatus == 0 and _digest(source) != captured["personnel_type_snapshot_version"]:
            _changed()
        if status == "已核实" and captured["personnel_type_at_approval"] not in ("临时", "长期"):
            _changed()
        _timestamp(captured["personnel_type_snapshot_time"])
    wage.check_permission("read")
    employee.check_permission("read")
    if (not _fields_readable(WAGE, {"company", "employee", "personnel_type_snapshot_status"})
            or not _fields_unmasked(WAGE, {"personnel_type_at_approval", "personnel_type_snapshot_time"})
            or not _fields_readable("Employee", {"company"})):
        _deny()
    result = {"readonly": True, "scope": "wage_personnel_classification", "name": name,
              "version": _timestamp(wage.modified), "docstatus": wage.docstatus,
              "classification_basis": "current_draft" if wage.docstatus == 0 else "first_native_submission",
              "status": status, "personnel_type": captured["personnel_type_at_approval"] if source else None,
              "recorded_at": _timestamp(captured["personnel_type_snapshot_time"]) if source else None}
    # Include source observations only internally so a change during the GET
    # rejects the whole projection. A later stable type change is compatible
    # with the old, approved value; a replacement source identity is not.
    return result, source, wage.company, wage.employee


@frappe.whitelist(methods=["GET"])
def read_classification(name, version):
    """No source name/hash/version is part of this authorized business DTO."""
    if frappe.session.user == "Guest":
        _deny()
    _name(name)
    _timestamp(version)
    from jifeng_erp.tool_archive_export import _fresh_snapshot

    permissions = _permission_state()
    first = _observation(name, version)
    # Use the already installed primary read-only connection guard. A fresh
    # transaction sees commits before its first read; later commits are not
    # an atomic guarantee. Raw inputs catch in-flight changes even when native
    # role/field caches still expose the earlier permission view.
    with _fresh_snapshot():
        if permissions != _permission_state():
            _deny()
        second = _observation(name, version)
        if first != second:
            _changed()
        if permissions != _permission_state():
            _deny()
    return second[0]
