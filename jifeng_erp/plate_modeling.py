"""Ordinary company-scoped modeling records; reference texts never post accounts.

Creation receipts are scoped to this DocType and company. An absent read-only
receipt is not permission to repeat a write. Files require the exact saved
record/field binding; preview unique_url is never a stored attachment URL.
"""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import today

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


DOCTYPE = "Jifeng Plate Modeling"
RAW_FIELDS = ("legacy_number", "model_number", "product_code", "reference_unit_price",
              "modeler_text", "model_date", "remarks", "legacy_copy")
LINKS = {"item_code": "Item", "plate": "Jifeng Plate", "expense_voucher": "Jifeng Expense Voucher"}
SOURCE_FIELDS = {"item_code": {"name"}, "plate": {"name", "company"},
                 "expense_voucher": {"name", "company", "docstatus"}}
EDIT_FIELDS = (*RAW_FIELDS, *LINKS, "design_image")
FIELDS = ("company", *EDIT_FIELDS)
REQUEST_FIELDS = ("request_id", "request_digest")
AUDIT_FIELDS = ("creation", "owner", "modified_by")
REQUIRED_READ = {"name", "modified", "company"}
COLLISION = "新建请求已使用或身份冲突，请查询核对，不自动重复保存"
_CREATING = ContextVar("jifeng_plate_modeling_creation", default=None)
_SERVER = object()
_UNSET = object()


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作版种建模", frappe.PermissionError)


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        frappe.throw("请选择准确的记录")


def _company(value):
    _name(value)
    frappe.get_doc("Company", value).check_permission("select")


def _columns(permission, doctype=DOCTYPE):
    return set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))


def _fields(fields, permission, doctype=DOCTYPE):
    if not set(fields).issubset(_columns(permission, doctype)):
        frappe.throw("没有操作所需字段的权限", frappe.PermissionError)


def _uuid(value):
    try:
        identity = UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        identity = None
    if not identity or identity.version != 4 or str(identity) != value:
        frappe.throw("新建请求身份无效")
    return value


def _normalize(field, value):
    if field == "legacy_copy":
        if type(value) not in (bool, int, str) or value not in (False, True, 0, 1, "0", "1"):
            frappe.throw("复制勾选须为是或否")
        return int(value)
    if value in (None, ""):
        return None
    if field == "model_date":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        try:
            valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        except ValueError:
            valid = False
        if not valid:
            frappe.throw("请选择有效日期")
        return value
    if not isinstance(value, str) or len(value) > 140:
        frappe.throw("请填写不超过一百四十个字符的有效原文")
    if field in LINKS:
        _name(value)
    if field == "reference_unit_price":
        try:
            number = Decimal(value)
        except InvalidOperation:
            number = None
        if number is None or not number.is_finite() or number < 0:
            frappe.throw("参考单价必须为空或有限非负数值")
    return value


def _values(document):
    return {field: _normalize(field, document.get(field)) for field in EDIT_FIELDS}


def _digest(company, values):
    body = {"doctype": DOCTYPE, "company": company, "fields": values}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _source(field, name, company, *, selecting=False):
    _name(name)
    source = frappe.get_doc(LINKS[field], name, **({"for_update": True} if selecting else {}))
    source.check_permission("read")
    _fields(SOURCE_FIELDS[field], "read", LINKS[field])
    if field != "item_code" and source.company != company:
        frappe.throw("关联来源必须属于当前公司", frappe.PermissionError)
    if selecting:
        source.check_permission("select")
        if field == "expense_voucher" and source.docstatus != 1:
            frappe.throw("请选择已提交的实际费用凭证")
    return source


def _source_state(field, name, company):
    if not name:
        return True, "未关联"
    try:
        source = _source(field, name, company)
    except (frappe.PermissionError, frappe.DoesNotExistError):
        return False, "来源不可用"
    if field == "expense_voucher":
        return True, {0: "未提交", 1: "已提交", 2: "已取消"}.get(source.docstatus, "来源不可用")
    return True, "已关联"


def _private_url(value):
    if not isinstance(value, str) or not value.startswith("/private/files/") or value != value.strip():
        return False
    if any(ord(char) < 32 for char in value) or "\\" in value:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    filename = unquote(parts.path.removeprefix("/private/files/"))
    return bool(filename and filename not in (".", "..") and not any(char in filename for char in ("/", "\\", "%"))
                and not any(ord(char) < 32 for char in filename)
                and not (parts.scheme or parts.netloc or parts.query or parts.fragment))


def _image_state(document, value=_UNSET):
    value = document.design_image if value is _UNSET else value
    empty = {"url": "", "state": "未上传" if not value else "附件不可用"}
    if not value or document.is_new() or not _private_url(value):
        return empty
    if not {"name", "file_url", "is_private", "attached_to_doctype", "attached_to_name", "attached_to_field"}.issubset(_columns("read", "File")):
        return empty
    binding = {"file_url": value, "is_private": 1, "attached_to_doctype": DOCTYPE,
               "attached_to_name": document.name, "attached_to_field": "design_image"}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=0)
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        if all(attachment.get(field) == expected for field, expected in binding.items()):
            if attachment.has_permission("read") and attachment.is_downloadable():
                return {"url": attachment.unique_url, "state": "已上传"}
    return empty


def _document(name, *, lock=False):
    _name(name)
    document = frappe.get_doc(DOCTYPE, name, **({"for_update": True} if lock else {}))
    document.check_permission("read")
    _fields(REQUIRED_READ, "read")
    _company(document.company)
    return document


def before_insert_record(document):
    _login()
    _company(document.company)
    document.check_permission("read")
    document.check_permission("create")
    _fields(REQUIRED_READ, "read")
    changed = {"company", "legacy_copy", *(field for field in EDIT_FIELDS if document.get(field) not in (None, ""))}
    _fields(changed, "read")
    _fields(changed, "write")
    digest = _digest(document.company, _values(document))
    if _CREATING.get() is document:
        _uuid(document.request_id)
        if document.request_digest != digest:
            frappe.throw("新建请求校验不完整")
    else:
        if any(document.get(field) not in (None, "") for field in REQUEST_FIELDS):
            frappe.throw("新建追溯身份只能由服务器生成")
        document.request_id, document.request_digest = str(uuid4()), digest
    document.__dict__["_jf_modeling_server"] = _SERVER


def validate_record(document):
    _login()
    _company(document.company)
    _fields(REQUIRED_READ, "read")
    document.check_permission("read")
    if document.is_new():
        document.check_permission("create")
        if document.__dict__.get("_jf_modeling_server") is not _SERVER:
            frappe.throw("新建追溯身份只能由服务器生成")
        _uuid(document.request_id)
        if document.request_digest != _digest(document.company, _values(document)):
            frappe.throw("新建请求校验不完整")
        changed = {"company", "legacy_copy", *(field for field in EDIT_FIELDS if document.get(field) not in (None, ""))}
    else:
        document.check_permission("write")
        before = document.get_doc_before_save()
        if not before or any(before.get(field) != document.get(field) for field in ("name", "company", *REQUEST_FIELDS)):
            frappe.throw("记录身份、公司及新建追溯身份不能修改")
        changed = {field for field in EDIT_FIELDS if before.get(field) != document.get(field)}
    _fields(changed, "read")
    _fields(changed, "write")
    for field in changed:
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该字段的权限", frappe.PermissionError)
        if field in EDIT_FIELDS:
            document.set(field, _normalize(field, document.get(field)))
        if field in LINKS and document.get(field):
            _source(field, document.get(field), document.company, selecting=True)
        if field == "design_image" and document.design_image:
            if document.is_new():
                frappe.throw("请先保存建模记录，再上传设计原图")
            if _image_state(document)["state"] != "已上传":
                frappe.throw("设计原图必须是可读取和下载、精确绑定当前记录字段的私有附件", frappe.PermissionError)


def prevent_rename(document, old, new, merge=False):
    _login()
    frappe.throw("建模记录主键不能重命名，请修改原文编号")


def _public(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _projection(document):
    readable = _columns("read")
    rows = frappe.get_list(DOCTYPE, filters={"name": document.name}, fields=[field for field in
        ("name", "modified", *FIELDS, *AUDIT_FIELDS) if field in readable], limit_page_length=1)
    if not rows or not REQUIRED_READ.issubset(rows[0]):
        frappe.throw("没有读取该建模记录的权限", frappe.PermissionError)
    row = rows[0]
    if row["name"] != document.name or row["company"] != document.company or not same_version(document.modified, _public(row["modified"])):
        frappe.throw("记录来源已变化，请重新核对")
    return row


def _pack(document, *, unscoped=False, values=None, include_meta=True):
    existing = not document.is_new()
    document.check_permission("read") if not unscoped else _read_permission()
    _fields(REQUIRED_READ, "read")
    if not unscoped:
        _company(document.company)
    if values is None:
        values = _projection(document) if existing else {field: document.get(field) for field in FIELDS if field in _columns("read")}
    result = {field: _public(values[field]) for field in ("name", *FIELDS, *AUDIT_FIELDS) if field in values}
    result.update(name=values.get("name") if existing else None,
                  version=_public(values.get("modified")) if existing else None)
    for field in LINKS:
        if field in result:
            visible, state = _source_state(field, result[field], document.company)
            result[field + "_state"] = state
            if not visible:
                result.pop(field)
    if "design_image" in result:
        state = _image_state(document, result["design_image"])
        result["image_state"] = state
        if state["state"] == "附件不可用":
            result.pop("design_image")
    if include_meta:
        writable = _columns("write")
        can_write = (frappe.has_permission(DOCTYPE, "create") if unscoped else
                     document.has_permission("write" if existing else "create"))
        meta = {}
        for field in FIELDS:
            if field in values:
                df = document.meta.get_field(field)
                meta[field] = {"fieldtype": df.fieldtype, "options": df.options or "", "reqd": bool(df.reqd),
                               "description": df.description or "", "read_only": bool(df.read_only or not can_write
                                   or field not in writable or existing and field == "company" or not existing and field == "design_image"
                                   or field in LINKS and (field not in result or not frappe.has_permission(LINKS[field], "read")
                                       or not frappe.has_permission(LINKS[field], "select")))}
        result.update(field_meta=meta, can_write=bool(can_write and (set(EDIT_FIELDS) & writable & set(result)
                      if existing else {"company", "legacy_copy"}.issubset(writable))))
    return result


def _read_permission():
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有读取版种建模的权限", frappe.PermissionError)


@frappe.whitelist()
def init_record(company=None):
    _login()
    document = frappe.new_doc(DOCTYPE)
    document.company = None
    for field in (*EDIT_FIELDS, *REQUEST_FIELDS):
        document.set(field, today() if field == "model_date" else 0 if field == "legacy_copy" else None)
    if company not in (None, ""):
        _company(company)
        document.company = company
        document.check_permission("create")
    return _pack(document, unscoped=not document.company)


@frappe.whitelist()
def read_record(name):
    _login()
    return _pack(_document(name))


def _list_records(company, search="", *, include_meta=False):
    _login()
    _company(company)
    _read_permission()
    _fields(REQUIRED_READ, "read")
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效关键词")
    readable = _columns("read")
    options = {"or_filters": [[field, "like", "%" + search + "%"] for field in RAW_FIELDS
                              if field in readable and field not in ("legacy_copy", "model_date", "reference_unit_price")]} if search else {}
    if search and not options["or_filters"]:
        frappe.throw("没有读取关键词查询字段的权限", frappe.PermissionError)
    rows = frappe.get_list(DOCTYPE, filters={"company": company}, fields=[field for field in
        ("name", "modified", *FIELDS, *AUDIT_FIELDS) if field in readable], order_by="model_date desc, name asc", limit_page_length=0, **options)
    if len({row.get("name") for row in rows}) != len(rows):
        frappe.throw("记录来源或读取权限已变化，请重新查询", frappe.PermissionError)
    result = []
    for row in rows:
        if not REQUIRED_READ.issubset(row):
            frappe.throw("没有读取建模记录所需字段的权限", frappe.PermissionError)
        document = frappe.get_doc(DOCTYPE, row["name"])
        if not document.has_permission("read") or document.company != company or row["company"] != company:
            continue
        if not same_version(document.modified, _public(row["modified"])):
            frappe.throw("记录来源已变化，请重新查询")
        if any(_public(document.get(field)) != _public(value) for field, value in row.items() if field != "modified"):
            frappe.throw("记录来源已变化，请重新查询")
        result.append(_pack(document, values=row, include_meta=include_meta))
    return result


@frappe.whitelist()
def list_records(company, search=""):
    """Preserve the array projection for existing callers without truncation."""
    return _list_records(company, search)


def _receipt(request_id, company):
    _read_permission()
    _fields(REQUIRED_READ | set(REQUEST_FIELDS), "read")
    rows = frappe.get_list(DOCTYPE, filters={"request_id": request_id, "company": company}, fields=["name"], limit_page_length=1)
    if not rows:
        return None
    document = _document(rows[0]["name"])
    if document.request_id != request_id or document.company != company:
        frappe.throw("记录来源已变化，请重新核对")
    return document


@frappe.whitelist()
def resolve_creation(request_id, company):
    _login()
    _uuid(request_id)
    _company(company)
    document = _receipt(request_id, company)
    return {"status": "found", "archive": _pack(document)} if document else {"status": "not_found"}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_record(payload):
    _login()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("保存请求必须是有效对象")
    if not isinstance(payload, dict) or not set(payload).issubset({"name", "version", "request_id", *FIELDS}):
        frappe.throw("保存请求包含不支持的参数")
    updates = {field: _normalize(field, payload[field]) for field in EDIT_FIELDS if field in payload}
    _fields(REQUIRED_READ | set(updates), "read")
    _fields(updates, "write")
    if payload.get("name") not in (None, ""):
        if "request_id" in payload:
            frappe.throw("已有记录不能改写新建请求身份")
        document = _document(payload["name"], lock=True)
        document.check_permission("write")
        if not same_version(document.modified, payload.get("version")):
            frappe.throw("记录已有其他修改，请重读核对后保存", frappe.TimestampMismatchError)
        if "company" in payload and payload["company"] != document.company:
            frappe.throw("记录公司不能修改")
    else:
        if payload.get("version") not in (None, ""):
            frappe.throw("新增记录不能携带旧版本")
        request_id = _uuid(payload.get("request_id"))
        company = payload.get("company")
        _company(company)
        values = {field: updates.get(field, today() if field == "model_date" else 0 if field == "legacy_copy" else None) for field in EDIT_FIELDS}
        digest = _digest(company, values)
        previous = _receipt(request_id, company)
        if previous:
            if previous.request_digest != digest:
                frappe.throw(COLLISION)
            return _pack(previous)
        document = frappe.new_doc(DOCTYPE)
        document.company = company
        for field, value in values.items():
            document.set(field, value)
        document.request_id, document.request_digest = request_id, digest
        document.check_permission("create")
    for field, value in updates.items():
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该字段的权限", frappe.PermissionError)
        document.set(field, value)
    messages_before = list(getattr(frappe.local, "message_log", None) or [])
    collision = False
    context = _CREATING.set(document) if document.is_new() else None
    try:
        try:
            document.save()
        except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
            collision = True
    finally:
        if context is not None:
            _CREATING.reset(context)
    if collision:
        frappe.local.message_log = messages_before
        frappe.throw(COLLISION)
    return _pack(document)


@frappe.whitelist()
def list_image_attachments(name):
    """Only exact candidates; a caller must explicitly adopt file_url afterward."""
    _login()
    document = _document(name)
    document.check_permission("write")
    _fields({"design_image"}, "read")
    _fields({"design_image"}, "write")
    if document.meta.get_field("design_image").read_only:
        frappe.throw("没有操作设计原图的权限", frappe.PermissionError)
    _fields({"name", "file_name", "file_url", "creation"}, "read", "File")
    binding = {"is_private": 1, "attached_to_doctype": DOCTYPE, "attached_to_name": document.name, "attached_to_field": "design_image"}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="creation desc, name asc", limit_page_length=101)
    if len(names) > 100:
        frappe.throw("设计原图附件较多，请从附件管理核对")
    result = []
    for file_id in names:
        try:
            attachment = frappe.get_doc("File", file_id)
        except frappe.DoesNotExistError:
            continue
        if all(attachment.get(field) == value for field, value in binding.items()) and _private_url(attachment.file_url):
            if attachment.has_permission("read") and attachment.is_downloadable():
                result.append({"name": attachment.name, "file_name": attachment.file_name, "file_url": attachment.file_url,
                               "unique_url": attachment.unique_url, "creation": _public(attachment.creation)})
    return result
