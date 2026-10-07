"""Permission checked classic Employee/Supplement editing, without account actions.

The separate archive rows describe old records. Saving them never submits a
Promotion, Salary Structure Assignment, or Separation. Native Employee saves,
validation, versions and unknown fields remain intact.
"""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate

from jifeng_erp.personnel_separation import _atomic
from jifeng_erp.archive_versions import same_version


EMPLOYEE = "Employee"
SUPPLEMENT = "Jifeng Employee Supplement"
EMPLOYEE_EDIT = {
    "employee_number", "first_name", "middle_name", "last_name", "department", "designation",
    "gender", "date_of_birth", "date_of_joining", "cell_number", "bank_name", "image",
}
EMPLOYEE_READ = EMPLOYEE_EDIT | {
    "employee_name", "company", "status", "relieving_date", "reason_for_leaving",
}
SUPPLEMENT_EDIT = {
    "ethnicity", "education_level", "specialty", "dormitory", "wage_method", "term_type",
    "identity_number", "identity_image", "province", "bank_account_name", "notes",
    "legacy_base_salary", "legacy_time_value", "legacy_city", "legacy_notes_text",
    "legacy_account", "legacy_address", "legacy_marital_status", "legacy_seniority",
    "legacy_introduction", "legacy_title",
}
HISTORIES = {
    "work_history": ("Jifeng Legacy Work History", (
        "project", "work_content", "reason_for_leaving", "from_date", "to_date", "company_name", "title")),
    "education_history": ("Jifeng Legacy Education History", (
        "project", "study_content", "from_date", "to_date", "school", "title")),
    "salary_history": ("Jifeng Legacy Salary History", (
        "project", "content", "from_date", "to_date", "wage", "title")),
    "promotion_history": ("Jifeng Legacy Promotion History", (
        "project", "work_content", "from_date", "to_date", "title")),
}
FORMAL_RECORDS = {
    "Employee Promotion": "promotion_date",
    "Employee Transfer": "transfer_date",
    "Employee Separation": "jf_last_working_day",
}
LIFECYCLE = {
    "user_id", "status", "relieving_date", "reason_for_leaving", "create_user_permission",
    "create_user_automatically", "leave_approver", "expense_approver", "shift_request_approver",
}
ACCOUNT_LINKS = ("user_id", "leave_approver", "expense_approver", "shift_request_approver")
_SAVING = ContextVar("jifeng_classic_employee_save", default=None)
ACCOUNT_ERROR = "旧式档案编辑不能修改关联账号、认证、角色或用户权限，请使用正式账号管理流程"


def is_archive_save(document):
    """A server object identity, never a client flag or a separation token."""
    return _SAVING.get() is document


class ClassicPersonnelEmployeeMixin:
    def update_user(self):
        if not is_archive_save(self):
            return super().update_user()

    def update_user_permissions(self):
        if not is_archive_save(self):
            return super().update_user_permissions()

    def update_user_status(self):
        if not is_archive_save(self):
            return super().update_user_status()


def _value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _object(value, label):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            frappe.throw(label + "必须是有效对象")
    if not isinstance(value, dict):
        frappe.throw(label + "必须是对象")
    return value


def _permission(document, permission, role_only=False):
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作员工档案", frappe.PermissionError)
    if role_only:
        if not frappe.has_permission(document.doctype, permission):
            frappe.throw("没有创建该档案的权限", frappe.PermissionError)
    else:
        document.check_permission(permission)


def _columns(doctype, permission, parenttype=None):
    return set(get_permitted_fields(doctype, parenttype=parenttype,
                                   permission_type=permission, ignore_virtual=True))


def _table_allowed(fieldname, permission):
    meta = frappe.get_meta(SUPPLEMENT)
    field = meta.get_field(fieldname)
    levels = set(meta.get_permlevel_access(permission_type=permission))
    if permission == "read" and 0 not in levels and frappe.share.get_shared(
            SUPPLEMENT, frappe.session.user, rights=["read"], limit=1):
        levels.add(0)
    return bool(field and field.fieldtype == "Table" and field.options == HISTORIES[fieldname][0]
                and (field.permlevel or 0) in levels)


def _company(company):
    if not isinstance(company, str) or not company.strip():
        frappe.throw("请选择公司")
    frappe.get_doc("Company", company).check_permission("select")


def _documents(name, lock=False):
    if not isinstance(name, str) or not name:
        frappe.throw("请选择员工档案")
    employee = frappe.get_doc(EMPLOYEE, name, for_update=lock)
    _permission(employee, "read")
    supplement_name = frappe.db.get_value(SUPPLEMENT, {"employee": employee.name}, "name")
    if supplement_name:
        supplement = frappe.get_doc(SUPPLEMENT, supplement_name, for_update=lock)
        _permission(supplement, "read")
        if supplement.employee != employee.name:
            frappe.throw("补充档案来源不匹配")
    else:
        supplement = frappe.new_doc(SUPPLEMENT)
        supplement.employee = employee.name
        supplement.company, supplement.department = employee.company, employee.department
        _permission(supplement, "create")
    return employee, supplement


def _field_meta(document, names, writable, existing):
    readable = _columns(document.doctype, "read")
    editable = _columns(document.doctype, "write")
    fields, values = {}, {}
    for name in sorted(names & readable):
        field = document.meta.get_field(name)
        if not field:
            continue
        fields[name] = {
            "fieldtype": field.fieldtype, "options": field.options or "", "reqd": bool(field.reqd),
            "read_only": bool(field.read_only or name not in writable or name not in editable),
        }
        if document.doctype == EMPLOYEE and name == "company" and existing:
            fields[name]["read_only"] = True
        if name in ("image", "identity_image") and document.is_new():
            fields[name]["read_only"] = True
        values[name] = _value(document.get(name))
    return fields, values


def _formal_records(employee):
    """Readable native sources, separate from editable legacy archive rows.

    Native lists apply source User Permissions/query hooks; each loaded source
    must still be readable and belong to this Employee. Do not refill a field
    omitted by the list's field permissions from the full document.
    """
    records = []
    for doctype, date_field in FORMAL_RECORDS.items():
        if not frappe.has_permission(doctype, "read"):
            continue
        readable = _columns(doctype, "read")
        if not {"name", "employee", "docstatus"}.issubset(readable):
            continue
        fields = ["name", "employee", "docstatus"]
        optional = [date_field] + (["jf_exit_status"] if doctype == "Employee Separation" else [])
        meta = frappe.get_meta(doctype)
        fields += [field for field in optional if field in readable and meta.get_field(field)]
        try:
            listed = frappe.get_list(doctype, filters={"employee": employee.name}, fields=fields,
                                     order_by="name asc", limit_page_length=0)
        except frappe.PermissionError:
            continue
        for row in listed:
            if (not isinstance(row.get("name"), str) or not row.name
                    or row.get("employee") != employee.name or row.get("docstatus") not in (0, 1, 2)):
                continue
            try:
                source = frappe.get_doc(doctype, row.name)
                if (source.employee != employee.name or not source.has_permission("read")
                        or any(_value(source.get(field)) != _value(row.get(field))
                               for field in fields if field in row)):
                    continue
            except (frappe.PermissionError, frappe.DoesNotExistError):
                continue
            record = {"doctype": doctype, "name": row.name, "docstatus": row.docstatus}
            if date_field in row:
                record["effective_date"] = _value(row.get(date_field))
            if "jf_exit_status" in row:
                record["exit_status"] = row.get("jf_exit_status")
            records.append(record)
    return sorted(records, key=lambda row: (row.get("effective_date") or "", row["doctype"], row["name"]),
                  reverse=True)


def _pack(employee, supplement, unscoped_init=False):
    def permitted(document, permission):
        # An empty new form has no Company yet. Its editable state comes from
        # role creation rights; saving always checks the complete documents.
        return (frappe.has_permission(document.doctype, permission) if unscoped_init
                else document.has_permission(permission))

    existing = not employee.is_new()
    employee_write = EMPLOYEE_EDIT | ({"company"} if not existing else set())
    employee_meta, employee_values = _field_meta(employee, EMPLOYEE_READ, employee_write, existing)
    supplement_meta, supplement_values = _field_meta(supplement, SUPPLEMENT_EDIT, SUPPLEMENT_EDIT, existing)
    history_values, history_meta = {}, {}
    for name, (doctype, fields) in HISTORIES.items():
        if not _table_allowed(name, "read"):
            continue
        readable, writable = _columns(doctype, "read", SUPPLEMENT), _columns(doctype, "write", SUPPLEMENT)
        meta = frappe.get_meta(doctype)
        columns = {field: {
            "fieldtype": meta.get_field(field).fieldtype,
            "options": meta.get_field(field).options or "", "reqd": bool(meta.get_field(field).reqd),
            "read_only": bool(meta.get_field(field).read_only or field not in writable
                              or not _table_allowed(name, "write")),
        } for field in fields if field in readable and meta.get_field(field)}
        can_save = permitted(supplement, "create" if supplement.is_new() else "write")
        history_meta[name] = {"read_only": not can_save or not _table_allowed(name, "write"), "fields": columns}
        history_values[name] = [{"name": row.name, **{
            field: _value(row.get(field)) for field in columns}}
            for row in supplement.get(name) or []]
    employee_permission = "write" if existing else "create"
    supplement_permission = "create" if supplement.is_new() else "write"
    return {
        "name": employee.name if existing else None,
        "supplement_name": supplement.name if not supplement.is_new() else None,
        "employee_modified": _value(employee.get("modified")) if existing else None,
        "supplement_modified": _value(supplement.get("modified")) if not supplement.is_new() else None,
        "employee": employee_values, "supplement": supplement_values, "histories": history_values,
        "formal_records": _formal_records(employee) if existing else [],
        "field_meta": {"employee": employee_meta, "supplement": supplement_meta, "histories": history_meta},
        "can_write": bool(permitted(employee, employee_permission)
                          and permitted(supplement, supplement_permission)),
    }


@frappe.whitelist()
def init_archive(company=None, term_type="长期"):
    if term_type not in ("长期", "临时"):
        frappe.throw("请选择有效的人员类型")
    employee, supplement = frappe.new_doc(EMPLOYEE), frappe.new_doc(SUPPLEMENT)
    employee.status, employee.user_id, employee.create_user_automatically = "Active", None, 0
    employee.company = None
    supplement.employee, supplement.company, supplement.department = None, None, None
    if company:
        _company(company)
        employee.company = supplement.company = company
    _permission(employee, "create", role_only=not company)
    _permission(supplement, "create", role_only=not company)
    supplement.term_type, supplement.wage_method = term_type, None
    archive = _pack(employee, supplement, unscoped_init=not company)
    if term_type == "临时":
        field = archive["field_meta"]["supplement"].get("term_type")
        if not archive["can_write"] or not field or field["read_only"]:
            frappe.throw("没有新增临工档案人员类型的权限", frappe.PermissionError)
    return archive


@frappe.whitelist()
def read_archive(name):
    employee, supplement = _documents(name)
    return _pack(employee, supplement)


def _normalize(value, field):
    if value in (None, ""):
        return None
    if field.fieldtype in ("Float", "Currency", "Int"):
        try:
            if isinstance(value, bool):
                raise ValueError
            number = Decimal(str(value))
            if not number.is_finite() or field.fieldtype == "Int" and number != int(number):
                raise ValueError
            normalized = int(number) if field.fieldtype == "Int" else float(number)
            if not math.isfinite(normalized):
                raise ValueError
            return normalized
        except (InvalidOperation, ValueError, OverflowError):
            frappe.throw("请输入有效数值：" + field.label)
    if field.fieldtype == "Date":
        if not isinstance(value, str):
            frappe.throw("请输入有效日期：" + field.label)
        try:
            return getdate(value).isoformat()
        except (ValueError, TypeError, OverflowError):
            frappe.throw("请输入有效日期：" + field.label)
    if not isinstance(value, str):
        frappe.throw("请输入有效文本：" + field.label)
    if field.fieldtype == "Select" and value not in (field.options or "").split("\n"):
        frappe.throw("请选择有效选项：" + field.label)
    return value


def _updates(document, values, allowed):
    values = _object(values, "档案字段")
    if not set(values).issubset(allowed):
        frappe.throw("旧式档案包含不支持或只读的字段")
    readable, writable = _columns(document.doctype, "read"), _columns(document.doctype, "write")
    for name, value in values.items():
        field = document.meta.get_field(name)
        if not field or field.read_only or name not in readable & writable:
            frappe.throw("没有读取或修改该档案字段的权限", frappe.PermissionError)
        value = _normalize(value, field)
        if field.fieldtype == "Link" and value:
            selected = frappe.get_doc(field.options, value)
            selected.check_permission("select")
            if name == "department" and selected.get("company") and selected.company != document.company:
                frappe.throw("部门必须属于员工公司")
        if name in ("image", "identity_image") and value and value != document.get(name):
            _image(document, name, value)
        document.set(name, value)


def _image(document, fieldname, value):
    if document.is_new():
        frappe.throw("请先保存档案，再上传私有图片")
    name = frappe.db.get_value("File", {
        "file_url": value, "is_private": 1, "attached_to_doctype": document.doctype,
        "attached_to_name": document.name, "attached_to_field": fieldname,
    }, "name")
    if not name or not value.startswith("/private/files/"):
        frappe.throw("图片必须是绑定当前档案字段的私有附件")
    frappe.get_doc("File", name).check_permission("read")


def _history_updates(supplement, histories):
    histories = _object(histories, "档案历史")
    if not set(histories).issubset(HISTORIES):
        frappe.throw("旧式档案包含不支持的历史标签")
    for name, incoming in histories.items():
        doctype, fields = HISTORIES[name]
        if not isinstance(incoming, list):
            frappe.throw("档案历史必须是明细数组")
        if not _table_allowed(name, "read") or not _table_allowed(name, "write"):
            frappe.throw("没有读取或修改该档案历史的权限", frappe.PermissionError)
        readable, writable = _columns(doctype, "read", SUPPLEMENT), _columns(doctype, "write", SUPPLEMENT)
        # Whole-row removal needs every field readable/writable, including
        # future custom columns that the classic UI does not render.
        current_rows = supplement.get(name) or []
        current = {row.name: row for row in current_rows}
        if len(current) != len(current_rows) or any(
                not row.name or row.parent != supplement.name or row.parenttype != SUPPLEMENT
                or row.parentfield != name for row in current_rows):
            frappe.throw("历史明细父子标识不匹配或重复")
        kept, seen = [], set()
        for values in incoming:
            values = _object(values, "历史明细")
            if not set(values).issubset({"name", *fields}):
                frappe.throw("历史明细包含不支持的字段")
            row_name = values.get("name")
            if row_name not in (None, "") and not isinstance(row_name, str):
                frappe.throw("历史明细标识无效")
            if row_name:
                if not isinstance(row_name, str) or row_name not in current or row_name in seen:
                    frappe.throw("历史明细标识不属于当前档案或重复")
                seen.add(row_name)
                row = current[row_name]
                if (row.parent != supplement.name or row.parenttype != SUPPLEMENT or row.parentfield != name):
                    frappe.throw("历史明细父子标识不匹配")
            else:
                row = supplement.append(name, {})
            for fieldname, value in values.items():
                if fieldname == "name":
                    continue
                field = frappe.get_meta(doctype).get_field(fieldname)
                if not field or field.read_only or fieldname not in readable & writable:
                    frappe.throw("没有读取或修改该历史字段的权限", frappe.PermissionError)
                row.set(fieldname, _normalize(value, field))
            kept.append(row)
        removed = set(current) - seen
        if removed:
            business = {field.fieldname for field in frappe.get_meta(doctype).fields
                        if field.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button")}
            if not business.issubset(readable & writable):
                frappe.throw("没有删除完整历史明细的字段权限", frappe.PermissionError)
        for index, row in enumerate(kept, start=1):
            row.idx = index
        supplement.set(name, kept)


def _accounts(employee):
    users = sorted({employee.get(field) for field in ACCOUNT_LINKS if employee.get(field)})
    if not users:
        return None
    # Internal integrity oracle only: never return User/auth rows or hashes to
    # the client, never save or repair them. Lock Users before native hooks run.
    documents = [frappe.get_doc("User", name, for_update=True).as_dict() for name in users]
    permissions = frappe.get_all("User Permission", filters={"user": ["in", users]},
                                 fields=["*"], order_by="name", limit_page_length=0)
    auth = frappe.db.sql("select * from `__Auth` where doctype=%s and name in %s order by name, fieldname",
                         ("User", tuple(users)), as_dict=True)
    return hashlib.sha256(json.dumps([documents, permissions, auth], sort_keys=True,
                                    default=str, ensure_ascii=False).encode()).hexdigest()


def _latest(document, supplied, label):
    if not document.is_new() and not same_version(document.modified, supplied):
        frappe.throw(label + "已被修改，请重新打开后保存", frappe.TimestampMismatchError)


@frappe.whitelist(methods=["POST"])
@_atomic
def save_archive(payload):
    payload = _object(payload, "档案请求")
    if not set(payload).issubset({"name", "employee_modified", "supplement_modified", "employee", "supplement", "histories"}):
        frappe.throw("旧式档案请求包含不支持的参数")
    existing = bool(payload.get("name"))
    values = _object(payload.get("employee", {}), "员工字段")
    if existing:
        employee, supplement = _documents(payload["name"], lock=True)
        _permission(employee, "write")
        _latest(employee, payload.get("employee_modified"), "员工档案")
        _latest(supplement, payload.get("supplement_modified"), "补充档案")
    else:
        employee, supplement = frappe.new_doc(EMPLOYEE), frappe.new_doc(SUPPLEMENT)
        _permission(employee, "create", role_only=True)
        _permission(supplement, "create", role_only=True)
        # Native document creation applies User Permissions to Company links.
        # Resolve the explicit selection before checking either document; an
        # unrelated native default must not reject an authorized selection.
        _company(values.get("company"))
        employee.company = supplement.company = values["company"]
        supplement.employee = None
        supplement.department = employee.department
        employee.status, employee.user_id, employee.create_user_automatically = "Active", None, 0
        supplement.term_type, supplement.wage_method = "长期", None
    if existing:
        _permission(supplement, "create" if supplement.is_new() else "write")
    protected = {field: _value(employee.get(field)) for field in LIFECYCLE}
    account_before = _accounts(employee)
    if existing and "company" in values:
        if values["company"] != employee.company:
            frappe.throw("旧式档案不能修改员工公司，请使用正式调岗流程")
        values = {key: value for key, value in values.items() if key != "company"}
    _company(employee.company)
    _updates(employee, values, EMPLOYEE_EDIT | ({"company"} if not existing else set()))
    _updates(supplement, payload.get("supplement", {}), SUPPLEMENT_EDIT)
    _history_updates(supplement, payload.get("histories", {}))
    if not existing:
        supplement.company, supplement.department = employee.company, employee.department
        _permission(employee, "create")
        _permission(supplement, "create")
    if not isinstance(employee, ClassicPersonnelEmployeeMixin):
        frappe.throw("旧式员工保存保护尚未安装，请联系管理员")
    context = _SAVING.set(employee)
    try:
        employee.save()
    finally:
        _SAVING.reset(context)
    if protected != {field: _value(employee.get(field)) for field in LIFECYCLE} or account_before != _accounts(employee):
        frappe.throw(ACCOUNT_ERROR)
    supplement.employee = employee.name
    supplement.save()
    # Check again: another installed supplement hook must not alter accounts.
    if account_before != _accounts(employee):
        frappe.throw(ACCOUNT_ERROR)
    return _pack(employee, supplement)
