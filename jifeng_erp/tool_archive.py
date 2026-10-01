"""Original mold/plate archives; single-record edits, no stock or accounting."""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from uuid import UUID, uuid4

import frappe
from frappe.utils import getdate, today

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


DOCTYPES = {"mold": "Jifeng Mold", "plate": "Jifeng Plate"}
EDIT_FIELDS = {
    "mold": ("legacy_number", "mold_no", "product_code", "quantity", "maker", "mold_date", "amount", "remark",
             "location", "scrapped", "legacy_copy", "copy_from", "item_code", "image", "scrap_date", "scrap_reason"),
    "plate": ("legacy_number", "plate_type", "product_code", "assessment_score", "maker", "quantity", "notes",
              "location", "retired", "legacy_copy", "copy_from", "plate_no", "item_code", "image", "plate_date",
              "modeler", "reference_cost", "retired_date", "retired_reason"),
}
TITLE = {"mold": "mold_no", "plate": "plate_no"}
INACTIVE = {"mold": "scrapped", "plate": "retired"}
DATE = {"mold": "mold_date", "plate": "plate_date"}
REQUEST_FIELDS = ("request_id", "request_digest")
COLLISION = "新建请求已使用或档案身份冲突，请查询核对，不自动重复保存"
_CREATING = ContextVar("jifeng_tool_archive_creation", default=None)
_SERVER = object()
_UNSET = object()


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作模具及版种档案", frappe.PermissionError)


def _kind(value):
    if not isinstance(value, str) or value not in DOCTYPES:
        frappe.throw("请选择模具或版种档案")
    return value


def _document_kind(document):
    return next((kind for kind, doctype in DOCTYPES.items() if doctype == document.doctype), None) or _kind(None)


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        frappe.throw("请选择准确的公司及档案来源")


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            frappe.throw("档案请求必须是有效对象")
    if not isinstance(value, dict):
        frappe.throw("档案请求必须是有效对象")
    return value


def _columns(doctype, permission):
    from frappe.model import get_permitted_fields

    return set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))


def _fields(doctype, fields, permission="read"):
    if not set(fields).issubset(_columns(doctype, permission)):
        frappe.throw("没有操作档案所需字段的权限", frappe.PermissionError)


def _company(value):
    _name(value)
    frappe.get_doc("Company", value).check_permission("select")
    _fields("Company", {"name"}, "select")


def _uuid(value):
    try:
        result = UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        result = None
    if result is None or result.version != 4 or str(result) != value:
        frappe.throw("新建请求必须包含有效UUID4身份")
    return value


def _value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value) if isinstance(value, Decimal) else value


def _normalize(kind, field, value):
    df = frappe.get_meta(DOCTYPES[kind]).get_field(field)
    if df.fieldtype == "Check":
        if type(value) not in (bool, int, str) or value not in (False, True, 0, 1, "0", "1"):
            frappe.throw("报废和复制勾选须为是或否")
        return int(value)
    if df.fieldtype in ("Float", "Currency"):
        try:
            if isinstance(value, bool):
                raise ValueError
            number = Decimal(str(0 if value in (None, "") else value))
            result = float(number)
            if not number.is_finite() or number < 0 or not math.isfinite(result):
                raise ValueError
            return result
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            frappe.throw("档案数量及参考金额必须为非负有限数值")
    if value is None:
        return None
    if df.fieldtype == "Date":
        if value == "":
            return None
        if not isinstance(value, (str, date)):
            frappe.throw("请填写有效档案日期")
        try:
            return getdate(value).isoformat()
        except ValueError:
            frappe.throw("请填写有效档案日期")
    if not isinstance(value, str) or df.fieldtype in ("Data", "Link", "Attach Image") and len(value) > 140:
        frappe.throw("请填写有效且长度允许的档案原文")
    # Only existing native business identifiers are trimmed. Legacy text,
    # plate quantity/type/score and multiline notes keep their original values.
    return value.strip() if field == TITLE[kind] or kind == "mold" and field == "product_code" else value


def _defaults(kind):
    values = {field: None for field in EDIT_FIELDS[kind]}
    values.update({INACTIVE[kind]: 0, "legacy_copy": 0, DATE[kind]: today()})
    values.update({"quantity": 1, "amount": 0} if kind == "mold" else {"assessment_score": "1", "reference_cost": 0})
    return values


def _digest(document, kind):
    values = {field: _normalize(kind, field, document.get(field)) for field in EDIT_FIELDS[kind]}
    # Native validators clear these fields when the archive is reactivated.
    # Canonicalize the same effective state before allocating its receipt.
    if not values[INACTIVE[kind]]:
        for field in (("scrap_date", "scrap_reason") if kind == "mold" else ("retired_date", "retired_reason")):
            values[field] = None
    if kind == "plate" and values["assessment_score"] is None:
        values["assessment_score"] = "1"
    body = {"record_type": kind, "company": document.company,
            "values": values}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _linked(document, kind, *, lock=False):
    if document.get("item_code"):
        item = frappe.get_doc("Item", document.item_code)
        item.check_permission("read")
        item.check_permission("select")
        _fields("Item", {"name"})
    for field in ("maker", "modeler") if kind == "plate" else ("maker",):
        if document.get(field):
            employee = frappe.get_doc("Employee", document.get(field))
            employee.check_permission("select")
            _fields("Employee", {"name", "company"}, "select")
            if employee.company != document.company:
                frappe.throw("制作人员必须属于当前公司")
    source, seen = document.get("copy_from"), {document.name} if document.name else set()
    while source:
        if source in seen:
            frappe.throw("复制来源不能形成循环")
        seen.add(source)
        original = frappe.get_doc(DOCTYPES[kind], source, **({"for_update": True} if lock else {}))
        original.check_permission("read")
        _fields(DOCTYPES[kind], {"name", "company", "copy_from"})
        if original.company != document.company:
            frappe.throw("复制来源必须属于同一公司")
        source = original.get("copy_from")


def _private_url(value):
    # Reuse the reviewed canonical private path parser, not its Profile binding.
    from jifeng_erp.product_archive import _private_url as canonical_private_url

    return canonical_private_url(value)


def _image_state(document, value=_UNSET):
    value = document.get("image") if value is _UNSET else value
    empty = {"state": "附件不可用" if value else "未上传", "url": ""}
    if document.is_new() or not _private_url(value):
        return empty
    if not {"name", "file_url", "is_private", "attached_to_doctype", "attached_to_name",
            "attached_to_field"}.issubset(_columns("File", "read")):
        return empty
    binding = {"file_url": value, "is_private": 1, "attached_to_doctype": document.doctype,
               "attached_to_name": document.name, "attached_to_field": "image"}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=0)
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        if any(attachment.get(field) != expected for field, expected in binding.items()):
            continue
        if attachment.has_permission("read") and attachment.is_downloadable():
            return {"state": "已上传", "url": attachment.unique_url}
    return empty


def before_insert_record(document):
    """Native forms also get a receipt; client metadata is never an authority."""
    _login()
    kind = _document_kind(document)
    _company(document.company)
    document.check_permission("read")
    document.check_permission("create")
    if kind == "plate" and document.get("assessment_score") is None:
        document.assessment_score = "1"
    digest = _digest(document, kind)
    if _CREATING.get() is document:
        _uuid(document.request_id)
        if document.request_digest != digest:
            frappe.throw("档案新建请求校验不完整")
    else:
        if any(document.get(field) not in (None, "") for field in REQUEST_FIELDS):
            frappe.throw("档案新建追溯身份只能由服务器生成")
        document.request_id, document.request_digest = str(uuid4()), digest
    document.__dict__["_jf_tool_archive_server"] = _SERVER


def validate_record(document):
    _login()
    kind = _document_kind(document)
    document.check_permission("read")
    _company(document.company)
    _fields(document.doctype, {"name", "company", TITLE[kind], INACTIVE[kind]})
    before = None if document.is_new() else document.get_doc_before_save()
    if document.is_new():
        document.check_permission("create")
        if document.__dict__.get("_jf_tool_archive_server") is not _SERVER:
            frappe.throw("档案新建追溯身份只能由服务器生成")
        _uuid(document.request_id)
        if document.request_digest != _digest(document, kind):
            frappe.throw("档案新建请求校验不完整")
        changed = {field for field in EDIT_FIELDS[kind] if document.get(field) not in (None, "")}
        changed.add("company")
    else:
        document.check_permission("write")
        if not before or any(before.get(field) != document.get(field) for field in ("company", *REQUEST_FIELDS)):
            frappe.throw("档案公司及新建追溯身份不能修改")
        changed = {field for field in EDIT_FIELDS[kind] if before.get(field) != document.get(field)}
    _fields(document.doctype, changed)
    _fields(document.doctype, changed, "write")
    for field in changed:
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该档案字段的权限", frappe.PermissionError)
    _linked(document, kind, lock=bool(document.get("copy_from")))
    if document.get("image") and (document.is_new() or not before or document.image != before.get("image")):
        if document.is_new():
            frappe.throw("请先保存档案，再上传绑定其图片字段的私有附件")
        if _image_state(document)["state"] != "已上传":
            frappe.throw("图片必须可读取和下载，并精确绑定当前档案图片字段", frappe.PermissionError)


def prevent_rename(document, old, new, merge=False):
    frappe.throw("档案主键不能重命名，请修改独立模具号或版种编号")


def _document(name, kind, *, lock=False):
    _name(name)
    document = frappe.get_doc(DOCTYPES[kind], name, **({"for_update": True} if lock else {}))
    document.check_permission("read")
    _fields(document.doctype, {"name", "modified", "company", TITLE[kind], INACTIVE[kind]})
    _company(document.company)
    return document


def _pack(document, kind, *, unscoped=False, row=None):
    existing = not document.is_new()
    readable, writable = _columns(document.doctype, "read"), _columns(document.doctype, "write")
    fields = {"company", *EDIT_FIELDS[kind]}
    if existing:
        projection = sorted(({"name", "modified", *REQUEST_FIELDS} | fields) & readable)
        if row is None:
            rows = frappe.get_list(document.doctype, filters={"name": document.name}, fields=projection, limit_page_length=1)
            row = rows[0] if rows else None
        if not row or not set(projection).issubset(row):
            frappe.throw("没有读取档案完整投影所需字段的权限", frappe.PermissionError)
        if row["company"] != document.company or row["name"] != document.name:
            frappe.throw("档案来源已改变，请重新读取")
    else:
        row = {field: document.get(field) for field in fields & readable}
    can_write = (frappe.has_permission(document.doctype, "create") if unscoped else
                 document.has_permission("write" if existing else "create"))
    editable = set(EDIT_FIELDS[kind]) | ({"company"} if not existing else set())
    values, metadata = {}, {}
    for field in fields & readable:
        df = document.meta.get_field(field)
        values[field] = _value(row[field])
        metadata[field] = {"fieldtype": df.fieldtype, "options": df.options or "", "reqd": bool(df.reqd),
            "read_only": bool(df.read_only or field not in editable or field not in writable or not can_write
                              or field == "image" and not existing), "description": df.description or ""}
    states = {}
    if "image" in values:
        states["image"] = _image_state(document, values["image"])
        if values["image"] and states["image"]["state"] != "已上传":
            values.pop("image")
    return {"record_type": kind, "name": document.name if existing else None,
            "version": _value(row.get("modified")) if existing else None, "values": values,
            "field_meta": metadata, "image_states": states, "can_write": bool(can_write),
            "can_select": bool(existing and not document.get(INACTIVE[kind]) and document.has_permission("select")),
            **{field: row.get(field) for field in REQUEST_FIELDS if existing and field in readable}}


@frappe.whitelist()
def init_record(record_type, company=None):
    _login()
    kind = _kind(record_type)
    if not all(frappe.has_permission(DOCTYPES[kind], permission) for permission in ("read", "create")):
        frappe.throw("没有新建模具或版种档案权限", frappe.PermissionError)
    document = frappe.new_doc(DOCTYPES[kind])
    document.company = None
    for field, value in _defaults(kind).items():
        document.set(field, value)
    for field in REQUEST_FIELDS:
        document.set(field, None)
    if company not in (None, ""):
        _company(company)
        document.company = company
        document.check_permission("create")
    _fields(document.doctype, {"company", TITLE[kind], INACTIVE[kind]})
    return _pack(document, kind, unscoped=not company)


@frappe.whitelist()
def read_record(name, record_type):
    _login()
    kind = _kind(record_type)
    return _pack(_document(name, kind), kind)


@frappe.whitelist()
def list_records(record_type, company, search="", include_inactive=False):
    _login()
    kind = _kind(record_type)
    _company(company)
    include_inactive = _normalize(kind, INACTIVE[kind], include_inactive)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效档案关键词")
    if not frappe.has_permission(DOCTYPES[kind], "read"):
        frappe.throw("没有读取档案权限", frappe.PermissionError)
    required = {"name", "modified", "company", TITLE[kind], INACTIVE[kind]}
    _fields(DOCTYPES[kind], required)
    readable = _columns(DOCTYPES[kind], "read")
    filters = {"company": company, **({INACTIVE[kind]: 0} if not include_inactive else {})}
    search_fields = {"legacy_number", TITLE[kind], "product_code", "location", "plate_type"} & readable
    rows = frappe.get_list(DOCTYPES[kind], filters=filters,
        fields=sorted(({"company", *EDIT_FIELDS[kind], *REQUEST_FIELDS} | required) & readable),
        order_by="modified desc, name asc", limit_page_length=101,
        **({"or_filters": [[field, "like", "%" + search + "%"] for field in sorted(search_fields)]} if search else {}))
    if len(rows) > 100:
        frappe.throw("档案记录较多，请输入关键词缩小查询范围")
    result = []
    for row in rows:
        if not required.issubset(row):
            frappe.throw("没有读取档案完整投影所需字段的权限", frappe.PermissionError)
        document = frappe.get_doc(DOCTYPES[kind], row.name)
        if not document.has_permission("read") or document.company != company or row.company != company:
            continue
        if not include_inactive and document.get(INACTIVE[kind]):
            continue
        result.append(_pack(document, kind, row=row))
    return result


def _receipt(request_id, kind, company):
    _fields(DOCTYPES[kind], {"name", "company", *REQUEST_FIELDS})
    rows = frappe.get_list(DOCTYPES[kind], filters={"request_id": request_id, "company": company}, fields=["name"], limit_page_length=1)
    if not rows:
        return None
    document = _document(rows[0].name, kind)
    if document.company != company or document.request_id != request_id:
        frappe.throw("档案来源已变化，请重新核对")
    return document


@frappe.whitelist()
def resolve_creation(request_id, record_type, company):
    _login()
    kind = _kind(record_type)
    _company(company)
    document = _receipt(_uuid(request_id), kind, company)
    return {"status": "found", "archive": _pack(document, kind)} if document else {"status": "not_found"}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_record(payload):
    _login()
    payload = _object(payload)
    kind = _kind(payload.get("record_type"))
    if not set(payload).issubset({"record_type", "name", "version", "request_id", "values"}):
        frappe.throw("档案请求包含不支持的参数")
    incoming = _object(payload.get("values"))
    if not set(incoming).issubset({"company", *EDIT_FIELDS[kind]}):
        frappe.throw("档案请求包含不支持或只读字段")
    _fields(DOCTYPES[kind], incoming)
    _fields(DOCTYPES[kind], set(incoming) - ({"company"} if payload.get("name") else set()), "write")
    updates = {field: _normalize(kind, field, value) for field, value in incoming.items() if field != "company"}
    if payload.get("name") not in (None, ""):
        if "request_id" in payload:
            frappe.throw("已有档案不能改写新建请求身份")
        document = _document(payload["name"], kind, lock=True)
        document.check_permission("write")
        if not same_version(document.modified, payload.get("version")):
            frappe.throw("档案已由其他操作修改，请重新读取", frappe.TimestampMismatchError)
        if "company" in incoming and incoming["company"] != document.company:
            frappe.throw("档案公司不能修改")
    else:
        if payload.get("version") not in (None, ""):
            frappe.throw("新建档案不能携带旧版本")
        _company(incoming.get("company"))
        request_id = _uuid(payload.get("request_id"))
        document = frappe.new_doc(DOCTYPES[kind])
        document.company = incoming["company"]
        values = _defaults(kind) | updates
        if kind == "plate" and values["assessment_score"] is None:
            values["assessment_score"] = "1"
        for field, value in values.items():
            document.set(field, value)
        document.request_id, document.request_digest = request_id, _digest(document, kind)
        previous = _receipt(request_id, kind, document.company)
        if previous:
            if previous.request_digest != document.request_digest:
                frappe.throw(COLLISION)
            return _pack(previous, kind)
        document.check_permission("create")
    for field, value in updates.items():
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该档案字段权限", frappe.PermissionError)
        document.set(field, value)
    if updates.get("image"):
        if document.is_new():
            frappe.throw("请先保存档案，再上传绑定其图片字段的私有附件")
        if _image_state(document)["state"] != "已上传":
            frappe.throw("图片必须可读取和下载，并精确绑定当前档案图片字段", frappe.PermissionError)
    # Defaults, link permissions and fixed identity are repeated by native
    # controllers. Unknown existing columns and child row identities stay loaded.
    context = _CREATING.set(document) if document.is_new() else None
    messages_before = list(getattr(frappe.local, "message_log", None) or [])
    collision = False
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
    return _pack(document, kind)


@frappe.whitelist()
def list_image_attachments(name, record_type, fieldname="image"):
    _login()
    kind = _kind(record_type)
    if fieldname != "image":
        frappe.throw("请选择档案图片字段")
    document = _document(name, kind)
    document.check_permission("write")
    _fields(document.doctype, {"image"})
    _fields(document.doctype, {"image"}, "write")
    if document.meta.get_field("image").read_only:
        frappe.throw("没有修改档案图片的权限", frappe.PermissionError)
    _fields("File", {"name", "file_name", "file_url", "creation", "is_private", "attached_to_doctype",
                     "attached_to_name", "attached_to_field"})
    binding = {"is_private": 1, "attached_to_doctype": document.doctype,
               "attached_to_name": document.name, "attached_to_field": "image"}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="creation desc, name asc", limit_page_length=101)
    if len(names) > 100:
        frappe.throw("档案图片附件较多，请从附件管理核对，不自动复传")
    result = []
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        if any(attachment.get(field) != value for field, value in binding.items()) or not _private_url(attachment.file_url):
            continue
        if attachment.has_permission("read") and attachment.is_downloadable():
            result.append({"name": attachment.name, "file_name": attachment.file_name, "file_url": attachment.file_url,
                           "unique_url": attachment.unique_url, "creation": _value(attachment.creation)})
    return result
