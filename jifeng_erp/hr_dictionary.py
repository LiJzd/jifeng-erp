"""Company-scoped HR vocabulary; archived personnel text remains independent.

Only specialty, education_level and ethnicity have verified archive contracts.
Vocabulary choices never rewrite Employee or Supplement records. Identities and
text are fixed after insertion; inactivity preserves past spelling and audit.
"""

from datetime import datetime
import hashlib
import json

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


DOCTYPE = "Jifeng HR Dictionary"
TYPES = {"专长": "specialty", "文化": "education_level", "民族": "ethnicity"}
FIELDS = ("company", "dictionary_type", "value", "inactive")
IDENTITY = ("company", "dictionary_type", "value")
READ_FIELDS = set(FIELDS) | {"name", "modified"}


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作人事词典", frappe.PermissionError)


def _type(dictionary_type):
    if not isinstance(dictionary_type, str) or dictionary_type not in TYPES:
        frappe.throw("请选择专长、文化或民族词典")


def _company(company):
    if not isinstance(company, str) or not company or company != company.strip():
        frappe.throw("请选择公司")
    selected = frappe.get_doc("Company", company)
    selected.check_permission("select")
    if selected.name != company:
        frappe.throw("请选择准确的公司编号")


def _columns(permission):
    # Native get_permitted_fields includes permitted standard name/modified;
    # Meta.get_permitted_fieldnames contains only declared DocFields.
    return set(get_permitted_fields(DOCTYPE, permission_type=permission, ignore_virtual=True))


def _fields(fields, permission):
    if not set(fields).issubset(_columns(permission)):
        frappe.throw("没有操作人事词典所需字段的权限", frappe.PermissionError)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 140:
        frappe.throw("词典内容须为一至一百四十个字符")
    return value.strip()


def _check(value, label="停用"):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str) and value in ("0", "1"):
        return int(value)
    frappe.throw(label + "须为是或否")


def _version(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _key(company, dictionary_type, value):
    return hashlib.sha256(json.dumps([company, dictionary_type, value], ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _pack(document, *, unscoped=False):
    if unscoped:
        if not frappe.has_permission(DOCTYPE, "read"):
            frappe.throw("没有读取人事词典的权限", frappe.PermissionError)
    else:
        document.check_permission("read")
    _fields(READ_FIELDS, "read")
    writable = _columns("write")
    existing = not document.is_new()
    can_write = (frappe.has_permission(DOCTYPE, "create") if unscoped else
                 document.has_permission("write" if existing else "create"))
    meta = {}
    for fieldname in FIELDS:
        field = document.meta.get_field(fieldname)
        meta[fieldname] = {"fieldtype": field.fieldtype, "options": field.options or "", "reqd": bool(field.reqd),
                           "read_only": bool(field.read_only or fieldname not in writable or not can_write
                                             or existing and fieldname in IDENTITY),
                           "description": field.description or ""}
    return {"name": document.name if existing else None, **{fieldname: document.get(fieldname) for fieldname in FIELDS},
            "version": _version(document.modified) if existing else None, "field_meta": meta,
            "can_write": bool(can_write and set(("inactive",) if existing else FIELDS).issubset(writable))}


def validate_dictionary(document):
    """The same invariants apply to native forms, imports and the classic API."""
    _login()
    _company(document.company)
    _type(document.dictionary_type)
    document.value = _text(document.value)
    document.inactive = _check(document.inactive)
    if document.is_new():
        document.check_permission("create")
        _fields(FIELDS, "write")
    else:
        document.check_permission("write")
        _fields(("inactive",), "write")
        before = document.get_doc_before_save()
        if not before or any(before.get(field) != document.get(field) for field in IDENTITY):
            frappe.throw("人事词典公司、类型及内容不能修改，请停用原词条并新增")
    # Client-supplied keys cannot alter the unique business identity.
    document.dictionary_key = _key(document.company, document.dictionary_type, document.value)


def prevent_delete(document):
    _login()
    frappe.throw("人事词典请停用保留历史，不能删除")


@frappe.whitelist()
def init_dictionary(dictionary_type, company=None):
    _login()
    _type(dictionary_type)
    document = frappe.new_doc(DOCTYPE)
    # A default from the user's settings is not their explicit choice and must
    # not turn an empty classic form into a premature Company permission check.
    document.company, document.dictionary_type = None, dictionary_type
    document.value, document.inactive = None, 0
    if company not in (None, ""):
        _company(company)
        document.company = company
        document.check_permission("create")
    return _pack(document, unscoped=not document.company)


@frappe.whitelist()
def list_dictionary(dictionary_type, company, search="", include_inactive=False):
    _login()
    _type(dictionary_type)
    _company(company)
    include_inactive = _check(include_inactive, "包含停用词条")
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效词典关键词")
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有读取人事词典的权限", frappe.PermissionError)
    _fields(READ_FIELDS, "read")
    filters = {"company": company, "dictionary_type": dictionary_type}
    if not include_inactive:
        filters["inactive"] = 0
    if search:
        filters["value"] = ["like", "%" + search + "%"]
    listed = frappe.get_list(DOCTYPE, filters=filters, fields=["name", *FIELDS],
                             order_by="value asc, name asc", limit_page_length=101)
    if len(listed) > 100:
        frappe.throw("词条较多，请输入关键词后查询")
    rows = []
    for row in listed:
        document = frappe.get_doc(DOCTYPE, row.name)
        if not document.has_permission("read"):
            continue
        if document.company != company or document.dictionary_type != dictionary_type:
            continue
        if not include_inactive and document.inactive:
            continue
        if any(field not in row for field in ("name", "value", "inactive")):
            frappe.throw("没有读取人事词典所需字段的权限", frappe.PermissionError)
        # Full documents guard scope only. Never restore values that native
        # list field permissions omitted from its permitted projection.
        rows.append({field: row[field] for field in ("name", "value", "inactive")})
    return rows


@frappe.whitelist()
def read_dictionary(name, dictionary_type=None):
    _login()
    if not isinstance(name, str) or not name or name != name.strip():
        frappe.throw("请选择人事词条")
    if dictionary_type is not None:
        _type(dictionary_type)
    document = frappe.get_doc(DOCTYPE, name)
    document.check_permission("read")
    _fields(READ_FIELDS, "read")
    _company(document.company)
    _type(document.dictionary_type)
    if dictionary_type is not None and dictionary_type != document.dictionary_type:
        frappe.throw("词条类型与当前人事词典不一致")
    return _pack(document)


@frappe.whitelist()
def resolve_creation(company, dictionary_type, value):
    """Read only; invisible rows never become a receipt or an extra read grant."""
    _login()
    _company(company)
    _type(dictionary_type)
    value = _text(value)
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有读取人事词典的权限", frappe.PermissionError)
    _fields(READ_FIELDS, "read")
    key = _key(company, dictionary_type, value)
    listed = frappe.get_list(DOCTYPE, filters={"company": company, "dictionary_type": dictionary_type,
                             "dictionary_key": key}, fields=["name"], limit_page_length=1)
    if not listed:
        # This states only what the actor can see. The caller must not interpret
        # it as authorization to automatically repeat an unknown POST.
        return {"status": "not_found"}
    document = frappe.get_doc(DOCTYPE, listed[0].name)
    document.check_permission("read")
    if tuple(document.get(field) for field in IDENTITY) != (company, dictionary_type, value) or document.dictionary_key != key:
        frappe.throw("人事词典来源已变化，请重新核对")
    return {"status": "found", "archive": _pack(document)}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_dictionary(payload):
    _login()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            frappe.throw("人事词典请求必须是有效对象")
    if not isinstance(payload, dict) or not set(payload).issubset({"name", "version", *FIELDS}):
        frappe.throw("人事词典请求包含不支持的参数")
    if not set(IDENTITY).issubset(payload):
        frappe.throw("请选择公司、词典类型并填写词典内容")
    _company(payload["company"])
    _type(payload["dictionary_type"])
    value = _text(payload["value"])
    name = payload.get("name")
    if name not in (None, ""):
        if not isinstance(name, str) or name != name.strip():
            frappe.throw("请选择准确的人事词条编号")
        document = frappe.get_doc(DOCTYPE, name, for_update=True)
        document.check_permission("read")
        document.check_permission("write")
        _fields(READ_FIELDS, "read")
        _fields(("inactive",), "write")
        if not same_version(document.modified, payload.get("version")):
            frappe.throw("人事词典已由其他操作修改，请重读核对后保存", frappe.TimestampMismatchError)
        if tuple(document.get(field) for field in IDENTITY) != (payload["company"], payload["dictionary_type"], value):
            frappe.throw("人事词典公司、类型及内容不能修改，请停用原词条并新增")
    else:
        if payload.get("version") not in (None, ""):
            frappe.throw("新增词条不能携带旧版本")
        document = frappe.new_doc(DOCTYPE)
        for field in IDENTITY:
            document.set(field, value if field == "value" else payload[field])
        document.inactive = 0
        document.check_permission("create")
        _fields(FIELDS, "write")
        _fields(READ_FIELDS, "read")
    if "inactive" in payload:
        document.inactive = _check(payload["inactive"])
    collision = False
    try:
        document.save()
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        collision = True
    if collision:
        # Native unique exceptions contain internal document names/SQL values.
        # Raise outside the except block to avoid exposing their exception
        # context; retain the DB constraint and the API's full atomic rollback.
        frappe.throw("同公司同类型的词条已存在或身份冲突，请查询核对，不自动重复保存")
    return _pack(document)
