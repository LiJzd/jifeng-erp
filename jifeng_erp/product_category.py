"""Two company-scoped product category records, independent of stock models.

These APIs save one record at a time. They do not map Item Group/UOM, apply a
conversion, backfill Product Profile, or implement legacy grid batch saving.
Creation receipts use (record_type, UUID) identity. The two ordinary DocTypes
have independent UUID namespaces; every stored scope and initial digest stays
fixed. Conversion values are nullable finite Decimal texts, not calculations.
"""

from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


DOCTYPES = {"category": "Jifeng Product Category", "detail": "Jifeng Product Category Detail"}
EDIT_FIELDS = {
    "category": ("legacy_code", "major_name", "owner_name", "region", "inactive"),
    "detail": ("minor_name", "material", "display_uom", "conversion_uom_text", "conversion_value", "inactive"),
}
IDENTITY = {"category": ("company",), "detail": ("company", "category")}
FIELDS = {kind: (*IDENTITY[kind], *fields) for kind, fields in EDIT_FIELDS.items()}
TITLES = {"category": "major_name", "detail": "minor_name"}
REQUIRED_READ = {kind: {"name", "modified", *IDENTITY[kind], TITLES[kind], "inactive"} for kind in DOCTYPES}
REQUEST_FIELDS = ("request_id", "request_digest")
AUDIT_FIELDS = ("creation", "owner", "modified_by")
COLLISION = "新建请求已使用或身份冲突，请查询核对，不自动重复保存"
_CREATING = ContextVar("jifeng_product_category_creation", default=None)
_SERVER = object()


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作产品类别", frappe.PermissionError)


def _kind(record_type):
    if not isinstance(record_type, str) or record_type not in DOCTYPES:
        frappe.throw("请选择产品类别主项或明细")
    return record_type


def _document_kind(document):
    for kind, doctype in DOCTYPES.items():
        if document.doctype == doctype:
            return kind
    frappe.throw("不支持该类别记录类型")


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        frappe.throw("请选择准确的产品类别记录")


def _company(value):
    _name(value)
    frappe.get_doc("Company", value).check_permission("select")


def _columns(kind, permission):
    return set(get_permitted_fields(DOCTYPES[kind], permission_type=permission, ignore_virtual=True))


def _fields(kind, fields, permission):
    if not set(fields).issubset(_columns(kind, permission)):
        frappe.throw("没有操作产品类别所需字段的权限", frappe.PermissionError)


def _uuid(value):
    if not isinstance(value, str):
        frappe.throw("新建请求必须包含有效的 UUID4 身份")
    try:
        result = UUID(value)
    except (ValueError, AttributeError):
        frappe.throw("新建请求必须包含有效的 UUID4 身份")
    if result.version != 4 or str(result) != value:
        frappe.throw("新建请求必须包含有效的 UUID4 身份")
    return value


def _normalize(field, value):
    if field == "inactive":
        if type(value) not in (bool, int, str) or value not in (False, True, 0, 1, "0", "1"):
            frappe.throw("停用须为是或否")
        return int(value)
    if value in (None, "") and field not in TITLES.values():
        return None
    if field == "conversion_value":
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            frappe.throw("换算值必须为空或有限数值")
        try:
            number = Decimal(str(value))
            result = str(number)
        except (InvalidOperation, ValueError):
            frappe.throw("换算值必须为空或有限数值")
        if not number.is_finite() or len(result) > 140:
            frappe.throw("换算值必须为空或有限数值")
        return result
    if not isinstance(value, str) or field in TITLES.values() and not value.strip():
        frappe.throw("请填写有效类别原文及名称")
    if len(value) > 140:
        frappe.throw("类别原文不能超过一百四十个字符")
    return value


def _values(document, kind):
    return {field: _normalize(field, document.get(field)) for field in EDIT_FIELDS[kind]}


def _public(field, value):
    # Native Float columns force NOT NULL/default 0 and sanitize None to 0.
    # Nullable Data preserves an omitted number, precision and signed zero.
    return _normalize(field, value) if field == "conversion_value" else value


def _digest(kind, company, category, values):
    body = {"record_type": kind, "company": company, "category": category if kind == "detail" else None,
            "fields": {field: _normalize(field, values.get(field, 0 if field == "inactive" else None))
                       for field in EDIT_FIELDS[kind]}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _parent(name, company=None, *, active=False, lock=False):
    _name(name)
    parent = frappe.get_doc(DOCTYPES["category"], name, **({"for_update": True} if lock else {}))
    parent.check_permission("read")
    _fields("category", REQUIRED_READ["category"], "read")
    _company(parent.company)
    if company not in (None, "") and company != parent.company:
        frappe.throw("产品类别父项与公司不匹配")
    if active:
        parent.check_permission("select")
        if _normalize("inactive", parent.inactive):
            frappe.throw("产品大类已停用，不能新增明细或选择")
    return parent


def _scope(document, kind, *, new=False, lock=False):
    if kind == "category":
        _company(document.company)
        return None
    parent = _parent(document.category, document.company, active=new, lock=lock)
    if document.company in (None, "") and document.is_new():
        document.company = parent.company
    if document.company != parent.company:
        frappe.throw("产品类别父项与公司不匹配")
    return parent


def _document(name, kind, *, lock=False):
    _name(name)
    document = frappe.get_doc(DOCTYPES[kind], name, **({"for_update": True} if lock else {}))
    document.check_permission("read")
    _fields(kind, REQUIRED_READ[kind], "read")
    _scope(document, kind)
    return document


def before_insert_record(document):
    """Native Forms get a server receipt; API context is exact object identity."""
    _login()
    kind = _document_kind(document)
    _scope(document, kind, new=True, lock=kind == "detail")
    document.check_permission("read")
    document.check_permission("create")
    _fields(kind, REQUIRED_READ[kind], "read")
    populated = {field for field in EDIT_FIELDS[kind] if document.get(field) not in (None, "")}
    changed = {TITLES[kind], "inactive", *populated, *(field for field in IDENTITY[kind]
                  if not (kind == "detail" and field == "company"))}
    _fields(kind, changed, "read")
    _fields(kind, changed, "write")
    digest = _digest(kind, document.company, document.get("category"), _values(document, kind))
    if _CREATING.get() is document:
        _uuid(document.request_id)
        if document.request_digest != digest:
            frappe.throw("产品类别新建请求校验不完整")
    else:
        if any(document.get(field) not in (None, "") for field in REQUEST_FIELDS):
            frappe.throw("产品类别新建追溯身份只能由服务器生成")
        document.request_id, document.request_digest = str(uuid4()), digest
    document.__dict__["_jf_category_server"] = _SERVER


def validate_record(document):
    _login()
    kind = _document_kind(document)
    _fields(kind, REQUIRED_READ[kind], "read")
    document.check_permission("read")
    if document.is_new():
        _scope(document, kind, new=True)
        document.check_permission("create")
        if document.__dict__.get("_jf_category_server") is not _SERVER:
            frappe.throw("产品类别新建追溯身份只能由服务器生成")
        _uuid(document.request_id)
        if document.request_digest != _digest(kind, document.company, document.get("category"), _values(document, kind)):
            frappe.throw("产品类别新建请求校验不完整")
        changed = {field for field in EDIT_FIELDS[kind] if document.get(field) not in (None, "")}
        changed.update({TITLES[kind], "inactive", *(field for field in IDENTITY[kind]
                          if not (kind == "detail" and field == "company"))})
    else:
        document.check_permission("write")
        before = document.get_doc_before_save()
        if not before or any(before.get(field) != document.get(field) for field in (*IDENTITY[kind], *REQUEST_FIELDS)):
            frappe.throw("类别公司、父项及新建追溯身份不能修改")
        # Existing independent text remains editable under an inactive parent.
        # Parent identity is immutable; no parent lock is needed for that edit.
        _scope(document, kind)
        changed = {field for field in EDIT_FIELDS[kind] if before.get(field) != document.get(field)}
    _fields(kind, changed, "read")
    _fields(kind, changed, "write")
    for field in changed:
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该类别字段的权限", frappe.PermissionError)
    for field in changed & set(EDIT_FIELDS[kind]):
        document.set(field, _normalize(field, document.get(field)))


def prevent_delete(document):
    _login()
    frappe.throw("本批产品类别删除尚未实现，请保留记录核对")


def prevent_rename(document, old, new, merge=False):
    # Native force=True can bypass allow_rename, but before_rename still runs
    # before it rewrites linked category names without controller validation.
    _login()
    frappe.throw("类别主键不能重命名，请修改名称或代码原文字段")


def _version(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _projection(document, kind):
    document.check_permission("read")
    _fields(kind, REQUIRED_READ[kind], "read")
    readable = _columns(kind, "read")
    listed = frappe.get_list(DOCTYPES[kind], filters={"name": document.name}, fields=[field for field in
        ("name", "modified", *FIELDS[kind], *AUDIT_FIELDS) if field in readable], limit_page_length=1)
    if not listed or not REQUIRED_READ[kind].issubset(listed[0]):
        frappe.throw("没有读取该产品类别记录的权限", frappe.PermissionError)
    row = listed[0]
    if any(row[field] != document.get(field) for field in ("name", *IDENTITY[kind])):
        frappe.throw("类别来源已变化，请重新核对")
    return row


def _pack(document, kind, *, unscoped=False):
    existing = not document.is_new()
    parent = None if unscoped else _scope(document, kind)
    if existing:
        values = _projection(document, kind)
    else:
        if not (frappe.has_permission(DOCTYPES[kind], "read") if unscoped else document.has_permission("read")):
            frappe.throw("没有读取产品类别的权限", frappe.PermissionError)
        _fields(kind, REQUIRED_READ[kind], "read")
        values = {field: document.get(field) for field in FIELDS[kind] if field in _columns(kind, "read")}
    writable = _columns(kind, "write")
    can_write = (frappe.has_permission(DOCTYPES[kind], "create") if unscoped else
                 document.has_permission("write" if existing else "create"))
    editable = set(EDIT_FIELDS[kind]) if existing else set(FIELDS[kind])
    if kind == "detail":
        editable.discard("company")
    meta = {}
    for field in FIELDS[kind]:
        if field in values:
            df = document.meta.get_field(field)
            meta[field] = {"fieldtype": df.fieldtype, "options": df.options or "", "reqd": bool(df.reqd),
                           "read_only": bool(df.read_only or field not in editable or field not in writable or not can_write),
                           "description": df.description or ""}
    required_write = {TITLES[kind], "inactive", "category" if kind == "detail" else "company"}
    selectable = bool(existing and not _normalize("inactive", document.inactive) and document.has_permission("select")
                      and (not parent or not _normalize("inactive", parent.inactive) and parent.has_permission("select")))
    return {"record_type": kind, "name": values.get("name") if existing else None,
            **{field: _public(field, values[field]) for field in (*FIELDS[kind], *AUDIT_FIELDS) if field in values},
            "version": _version(values.get("modified")) if existing else None,
            "field_meta": meta, "can_select": selectable,
            "can_write": bool(can_write and (editable & writable & set(values) if existing else required_write.issubset(writable)))}


@frappe.whitelist()
def init_record(record_type, company=None, category=None):
    _login()
    kind = _kind(record_type)
    document = frappe.new_doc(DOCTYPES[kind])
    document.company = None
    for field in (*EDIT_FIELDS[kind], *REQUEST_FIELDS):
        document.set(field, 0 if field == "inactive" else None)
    if kind == "detail":
        parent = _parent(category, company, active=True)
        document.category, document.company = parent.name, parent.company
    elif category not in (None, ""):
        frappe.throw("类别主项不能携带父项")
    elif company not in (None, ""):
        _company(company)
        document.company = company
    if document.company:
        document.check_permission("create")
    return _pack(document, kind, unscoped=not document.company)


@frappe.whitelist()
def read_record(name, record_type):
    _login()
    kind = _kind(record_type)
    return _pack(_document(name, kind), kind)


@frappe.whitelist()
def list_records(record_type, company, category=None, search="", include_inactive=False):
    _login()
    kind = _kind(record_type)
    _company(company)
    include_inactive = _normalize("inactive", include_inactive)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效类别关键词")
    if not frappe.has_permission(DOCTYPES[kind], "read"):
        frappe.throw("没有读取产品类别的权限", frappe.PermissionError)
    _fields(kind, REQUIRED_READ[kind], "read")
    filters = {"company": company}
    if kind == "detail":
        parent = _parent(category, company)
        if parent.inactive and not include_inactive:
            return []
        filters["category"] = category
    elif category not in (None, ""):
        frappe.throw("类别主项不能携带父项")
    if not include_inactive:
        filters["inactive"] = 0
    readable = _columns(kind, "read")
    options = {"or_filters": [[field, "like", "%" + search + "%"] for field in EDIT_FIELDS[kind]
                              if field in readable and field not in ("inactive", "conversion_value")]} if search else {}
    listed = frappe.get_list(DOCTYPES[kind], filters=filters, fields=[field for field in
        ("name", "modified", *FIELDS[kind], *AUDIT_FIELDS) if field in readable],
        order_by=TITLES[kind] + " asc, name asc", limit_page_length=101, **options)
    if len(listed) > 100:
        frappe.throw("类别记录较多，请输入关键词后查询")
    rows = []
    for row in listed:
        if not REQUIRED_READ[kind].issubset(row):
            frappe.throw("没有读取类别所需字段的权限", frappe.PermissionError)
        document = frappe.get_doc(DOCTYPES[kind], row.name)
        if not document.has_permission("read") or document.company != company or row.company != company:
            continue
        if kind == "detail" and (document.category != category or row.category != category):
            continue
        parent = _scope(document, kind)
        if not include_inactive and (row.inactive or document.inactive or parent and parent.inactive):
            continue
        # Never replace omitted native list columns with full-document values.
        rows.append({"record_type": kind, **{field: _public(field, row[field]) for field in ("name", *FIELDS[kind], *AUDIT_FIELDS) if field in row},
                     "version": _version(row.modified), "can_select": bool(not document.inactive and document.has_permission("select")
                         and (not parent or not parent.inactive and parent.has_permission("select")))})
    return rows


def _receipt(request_id, kind, company, category):
    if not frappe.has_permission(DOCTYPES[kind], "read"):
        frappe.throw("没有核对产品类别的权限", frappe.PermissionError)
    _fields(kind, REQUIRED_READ[kind] | set(REQUEST_FIELDS), "read")
    filters = {"request_id": request_id, "company": company}
    if kind == "detail":
        filters["category"] = category
    rows = frappe.get_list(DOCTYPES[kind], filters=filters, fields=["name"], limit_page_length=1)
    if not rows:
        return None
    document = _document(rows[0].name, kind)
    if document.request_id != request_id or document.company != company or kind == "detail" and document.category != category:
        frappe.throw("类别来源已变化，请重新核对")
    return document


@frappe.whitelist()
def resolve_creation(request_id, record_type, company, category=None):
    """Resolve a type-scoped UUID; absent visibility never authorizes a retry."""
    _login()
    kind = _kind(record_type)
    _uuid(request_id)
    _company(company)
    if kind == "detail":
        _parent(category, company)
    elif category not in (None, ""):
        frappe.throw("类别主项不能携带父项")
    document = _receipt(request_id, kind, company, category)
    return {"status": "found", "archive": _pack(document, kind)} if document else {"status": "not_found"}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_record(payload):
    _login()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("类别请求必须是有效对象")
    if not isinstance(payload, dict):
        frappe.throw("类别请求必须是有效对象")
    kind = _kind(payload.get("record_type"))
    if not set(payload).issubset({"record_type", "name", "version", "request_id", *FIELDS[kind]}):
        frappe.throw("类别请求包含不支持的参数")
    updates = {field: _normalize(field, payload[field]) for field in EDIT_FIELDS[kind] if field in payload}
    _fields(kind, REQUIRED_READ[kind], "read")
    _fields(kind, updates, "read")
    _fields(kind, updates, "write")
    if payload.get("name") not in (None, ""):
        if "request_id" in payload:
            frappe.throw("已有类别不能改写新建请求身份")
        document = _document(payload["name"], kind, lock=True)
        document.check_permission("write")
        if not same_version(document.modified, payload.get("version")):
            frappe.throw("类别已由其他操作修改，请重读核对后保存", frappe.TimestampMismatchError)
        if any(field in payload and payload[field] != document.get(field) for field in IDENTITY[kind]):
            frappe.throw("类别公司、父项及新建追溯身份不能修改")
    else:
        if payload.get("version") not in (None, ""):
            frappe.throw("新增类别不能携带旧版本")
        request_id = _uuid(payload.get("request_id"))
        if kind == "detail":
            parent = _parent(payload.get("category"), payload.get("company"))
            company, category = parent.company, parent.name
        else:
            company, category = payload.get("company"), None
            _company(company)
        values = {field: updates.get(field, 0 if field == "inactive" else None) for field in EDIT_FIELDS[kind]}
        digest = _digest(kind, company, category, values)
        previous = _receipt(request_id, kind, company, category)
        if previous:
            if previous.request_digest != digest:
                frappe.throw(COLLISION)
            return _pack(previous, kind)
        if kind == "detail":
            _parent(category, company, active=True, lock=True)
        document = frappe.new_doc(DOCTYPES[kind])
        document.company = company
        if kind == "detail":
            document.category = category
        for field, value in values.items():
            document.set(field, value)
        document.request_id, document.request_digest = request_id, digest
        document.check_permission("create")
    for field, value in updates.items():
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该类别字段的权限", frappe.PermissionError)
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
    return _pack(document, kind)
