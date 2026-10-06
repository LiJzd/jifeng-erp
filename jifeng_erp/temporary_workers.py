"""Current active legacy temporary-worker eligibility for wage entry selection.

Company select rights, readable Employee fields and create permission on the
exact new Wage Entry authorize this narrow predicate, as in piece_workers.
HR User needs no Company read or Supplement table grant. Only the Supplement's
current term_type and identity/version bindings are read internally; its name
and other fields are never returned. Lists are per-person read observations,
not a single-time database snapshot. Selection rechecks the exact person and
both versions; it provides no lock, historical employment, saved wage
provenance or guarantee of eligibility at later save.
"""

from datetime import datetime
import hashlib
import json

import frappe


SUPPLEMENT = "Jifeng Employee Supplement"
WAGE = "Jifeng Wage Entry"
SCOPE = "current_temporary_workers"
EMPLOYEE_FIELDS = ("name", "modified", "employee_number", "employee_name", "company", "department", "status")
REQUIRED_FIELDS = {"company", "status", "employee_name"}
SOURCE_FIELDS = ("name", "employee", "company", "term_type", "modified")
LIMIT = 1000


def _text(value, label, *, empty=False):
    if (not isinstance(value, str) or len(value) > 140 or any(ord(char) < 32 for char in value)
            or value != value.strip() or (not empty and not value)):
        frappe.throw("请填写有效的" + label)
    return value


def _changed():
    frappe.throw("当前临工资格或来源版本已变化，请重新查询后选择")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _modified(value):
    try:
        if not value:
            raise ValueError
        return datetime.fromisoformat(str(value)).isoformat(timespec="microseconds")
    except (ValueError, TypeError):
        _changed()


def _scope(company):
    if frappe.session.user == "Guest":
        frappe.throw("请登录后查询临工", frappe.PermissionError)
    _text(company, "公司")
    if not frappe.has_permission(WAGE, "create") or not frappe.has_permission("Employee", "read"):
        frappe.throw("没有查询临工计薪资格的权限", frappe.PermissionError)
    # The service uses only the company identifier; native HR User has select.
    frappe.get_doc("Company", company).check_permission("select")


def _permitted(meta):
    fields = (set(EMPLOYEE_FIELDS) if frappe.session.user == "Administrator" else
              set(meta.get_permitted_fieldnames(permission_type="read")))
    if not REQUIRED_FIELDS.issubset(fields):
        frappe.throw("没有读取临工资格所需员工字段的权限", frappe.PermissionError)
    # Standard document identifiers are supplied by the scoped native list.
    return fields | {"name", "modified"}


def _person(company, name, *, lock=False):
    _scope(company)
    person = frappe.get_doc("Employee", name, for_update=True) if lock else frappe.get_doc("Employee", name)
    person.check_permission("read")
    _permitted(person.meta)
    if person.company != company or person.status != "Active":
        return None
    # Check native User Permissions for this particular new wage, not role only.
    frappe.get_doc({"doctype": WAGE, "company": company, "employee": name}).check_permission("create")
    # A native permission callback can change employee scope. Read it again
    # after the create check, before looking up the private qualification field.
    current = frappe.get_doc("Employee", name, for_update=True) if lock else frappe.get_doc("Employee", name)
    current.check_permission("read")
    _permitted(current.meta)
    return current if current.company == company and current.status == "Active" else None


def _versions(person, source, company):
    return {
        "employee_version": _digest({"name": person.name, "company": company, "status": person.status,
            "employee_name": person.employee_name, "modified": _modified(person.modified)}),
        "supplement_version": _digest({field: (_modified(source[field]) if field == "modified" else source[field])
                                     for field in SOURCE_FIELDS}),
    }


def require_temporary_selection(company, employee, selection):
    """Recheck an explicit selection for a new batch, after its source locks.

    The caller holds Employee before taking Supplement, like native wage
    creation. This is not a grant from the client or a historical wage marker.
    Existing-request recovery and ordinary wage edits do not call this guard.
    """
    if (not isinstance(selection, dict) or set(selection) != {
            "company", "employee", "employee_version", "supplement_version"}
            or selection["company"] != company or selection["employee"] != employee):
        _changed()
    for field in ("employee_version", "supplement_version"):
        value = selection[field]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            _changed()
    _text(employee, "员工")
    person = _person(company, employee, lock=True)
    if person is None:
        _changed()
    source = frappe.db.get_value(SUPPLEMENT, {"company": company, "employee": employee},
                                list(SOURCE_FIELDS), as_dict=True, for_update=True)
    if (not source or not source.get("name") or source.get("company") != company
            or source.get("employee") != employee or source.get("term_type") != "临时"):
        _changed()
    if any(selection[field] != value for field, value in _versions(person, source, company).items()):
        _changed()


def _capture(company, keyword="", employee=None):
    _scope(company)
    permitted = _permitted(frappe.get_meta("Employee"))
    fields = [field for field in EMPLOYEE_FIELDS if field in permitted]
    query = {"company": company, "status": "Active"}
    if employee is not None:
        query["name"] = employee
    options = {"or_filters": {field: ["like", "%" + keyword + "%"]
        for field in ("employee_name", "employee_number") if field in permitted}} if keyword else {}
    listed = frappe.get_list("Employee", filters=query, fields=fields,
        order_by="employee_name asc, name asc", limit_page_length=LIMIT + 1, **options)
    if len(listed) > LIMIT:
        frappe.throw("人员过多，请输入姓名或编号关键词后查询")
    rows = []
    for candidate in listed:
        if not candidate.get("name"):
            _changed()
        try:
            person = _person(company, candidate["name"])
        except frappe.PermissionError:
            # A scoped native list row can lose individual read/create access.
            continue
        if person is None:
            continue
        allowed = _permitted(person.meta) & permitted
        if not {"name", "modified", *REQUIRED_FIELDS}.issubset(candidate):
            _changed()
        if _modified(candidate["modified"]) != _modified(person.modified):
            _changed()
        projection = {field: candidate[field] for field in EMPLOYEE_FIELDS
                      if field not in {"modified"} and field in allowed and field in candidate}
        if any(person.get(field) != value for field, value in projection.items()):
            _changed()
        # No Supplement document/list or broad table permission is exposed.
        source = frappe.db.get_value(SUPPLEMENT, {"company": company, "employee": person.name},
                                    list(SOURCE_FIELDS), as_dict=True, for_update=False)
        if (not source or source.get("term_type") != "临时" or source.get("company") != company
                or source.get("employee") != person.name):
            continue
        if not source.get("name"):
            _changed()
        rows.append({**projection, **_versions(person, source, company)})
    return rows


def _stable(company, employee):
    # Two exact reads can reject observed changes; ordinary reads provide no
    # concurrency lock and may share the native transaction's read snapshot.
    first = _capture(company, employee=employee)
    second = _capture(company, employee=employee)
    if first != second:
        _changed()
    return second


def _envelope(company):
    return {"readonly": True, "scope": SCOPE, "company": company,
            "classification_basis": "current_legacy_term_type", "historical_qualification_known": False}


@frappe.whitelist(methods=["GET"])
def list_temporary_workers(company, keyword=""):
    """Only current Active employees; not a historical/left-worker roster."""
    _text(keyword, "人员关键词", empty=True)
    return {**_envelope(company), "rows": _capture(company, keyword)}


@frappe.whitelist(methods=["GET"])
def select_temporary_worker(company, employee, employee_version, supplement_version):
    _text(employee, "员工")
    for value in (employee_version, supplement_version):
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            _changed()
    rows = _stable(company, employee)
    if len(rows) != 1 or rows[0]["name"] != employee:
        _changed()
    row = rows[0]
    if row["employee_version"] != employee_version or row["supplement_version"] != supplement_version:
        _changed()
    return {**_envelope(company), "employee": employee, "eligible": True,
            "employee_version": employee_version, "supplement_version": supplement_version}
