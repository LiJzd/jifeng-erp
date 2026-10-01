"""Company-scoped product words with mutable text and stable creation receipts.

Only style, source and brand are covered. Product Profile Data values remain
independent; renaming or disabling a word never rewrites an archived product.
Automatic company/type numbering is a new-system policy, not the legacy rule.
Legacy deletion and automatic abbreviation generation are not implemented.
"""

from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


DOCTYPE = "Jifeng Product Dictionary"
TYPES = {"风格": "style", "来源": "source", "品牌": "brand"}
EDIT_FIELDS = ("legacy_number", "value", "remarks", "abbreviation", "inactive")
IDENTITY = ("company", "dictionary_type")
AUTO_MODE = "automatic_v1"
FIELDS = (*IDENTITY, *EDIT_FIELDS, "numbering_mode")
REQUEST_FIELDS = ("request_id", "request_digest")
REQUIRED_READ = {"name", "modified", *IDENTITY, "value", "inactive", "numbering_mode"}
AUDIT_FIELDS = ("creation", "owner", "modified_by")
_CREATING = ContextVar("jifeng_product_dictionary_creation", default=None)
_SERVER = object()
COLLISION = "新建请求已使用或身份冲突，请查询核对，不自动重复保存"


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作产品词典", frappe.PermissionError)


def _type(dictionary_type):
    if not isinstance(dictionary_type, str) or dictionary_type not in TYPES:
        frappe.throw("请选择风格、来源或品牌词典")


def _company(company):
    if not isinstance(company, str) or not company or company != company.strip():
        frappe.throw("请选择公司")
    frappe.get_doc("Company", company).check_permission("select")


def _name(name):
    if not isinstance(name, str) or not name or name != name.strip():
        frappe.throw("请选择准确的产品词条编号")


def _columns(permission):
    return set(get_permitted_fields(DOCTYPE, permission_type=permission, ignore_virtual=True))


def _fields(fields, permission):
    if not set(fields).issubset(_columns(permission)):
        frappe.throw("没有操作产品词典所需字段的权限", frappe.PermissionError)


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
    if field != "value" and value in (None, ""):
        return None
    if not isinstance(value, str) or field == "value" and not value.strip():
        frappe.throw("请填写有效产品词典原文")
    if field != "remarks" and len(value) > 140:
        frappe.throw("产品词典文本不能超过一百四十个字符")
    # Original spelling, including whitespace, is deliberately not rewritten.
    return value


def _values(document):
    return {field: _normalize(field, document.get(field)) for field in EDIT_FIELDS}


def _mode(value):
    if value in (None, ""):
        return None
    if value != AUTO_MODE:
        frappe.throw("请选择有效产品词典编号模式")
    return value


def _series_key(company, dictionary_type):
    # Isolated new-system counters; do not infer or seed from historical codes.
    return "JF-PROD-N-" + hashlib.sha256(company.encode()).hexdigest() + "-" + TYPES[dictionary_type] + "-"


def _number_preview(company, dictionary_type):
    # Advisory peek only. getseries allocates; NamingSeries.get_current_value
    # locks; NamingSeries.get_preview is a sample, not the next stored counter.
    current = frappe.db.get_value("Series", _series_key(company, dictionary_type), "current", order_by="name")
    return str(int(current or 0) + 1).zfill(3)


def _digest(company, dictionary_type, values, numbering_mode=None):
    mode = _mode(numbering_mode)
    body = {"company": company, "dictionary_type": dictionary_type,
            "fields": {field: _normalize(field, values.get(field, 0 if field == "inactive" else None))
                       for field in EDIT_FIELDS if not (mode == AUTO_MODE and field == "legacy_number")}}
    if mode:
        body["numbering_mode"] = mode
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def before_insert_dictionary(document):
    """Generate native Form receipts server-side; API context is object identity."""
    _login()
    _company(document.company)
    _type(document.dictionary_type)
    document.check_permission("create")
    if _CREATING.get() is document:
        mode = _mode(document.get("numbering_mode"))
        _uuid(document.request_id)
        if document.request_digest != _digest(document.company, document.dictionary_type, _values(document), mode):
            frappe.throw("产品词典新建请求校验不完整")
    else:
        if any(document.get(field) not in (None, "") for field in (*REQUEST_FIELDS, "numbering_mode")):
            frappe.throw("产品词典新建追溯身份只能由服务器生成")
        # Direct native new Forms follow the same allocation policy. v0 API
        # remains the explicit compatibility path for original number text.
        mode = AUTO_MODE
        document.numbering_mode = mode
        document.request_id = str(uuid4())
        document.request_digest = _digest(document.company, document.dictionary_type, _values(document), mode)
    if mode == AUTO_MODE and document.get("legacy_number") not in (None, ""):
        frappe.throw("自动编号只能由服务器生成，不能指定编号")
    populated = {field for field in EDIT_FIELDS if document.get(field) not in (None, "")}
    _fields({*IDENTITY, "value", "inactive", *populated}, "read")
    _fields({*IDENTITY, "value", "inactive", *populated}, "write")
    if mode == AUTO_MODE:
        from frappe.model.naming import getseries

        document.legacy_number = getseries(_series_key(document.company, document.dictionary_type), 3)
        document.__dict__["_jf_dictionary_number"] = (_SERVER, document.legacy_number)
    document.__dict__["_jf_dictionary_server"] = _SERVER


def validate_dictionary(document):
    """Native Form/import and classic API enforce the same scoped invariants."""
    _login()
    _company(document.company)
    _type(document.dictionary_type)
    mode = _mode(document.get("numbering_mode"))
    _fields(REQUIRED_READ, "read")
    if document.is_new():
        document.check_permission("create")
        if document.__dict__.get("_jf_dictionary_server") is not _SERVER:
            frappe.throw("产品词典新建追溯身份只能由服务器生成")
        _uuid(document.request_id)
        if document.request_digest != _digest(document.company, document.dictionary_type, _values(document), mode):
            frappe.throw("产品词典新建请求校验不完整")
        changed = {field for field in EDIT_FIELDS if document.get(field) not in (None, "")}
        if mode == AUTO_MODE:
            generated = document.__dict__.get("_jf_dictionary_number")
            if not generated or generated[0] is not _SERVER or generated[1] != document.legacy_number:
                frappe.throw("自动编号只能由服务器生成，不能指定编号")
            changed.discard("legacy_number")
        changed.update((*IDENTITY, "value", "inactive"))
    else:
        document.check_permission("write")
        before = document.get_doc_before_save()
        if not before or any(before.get(field) != document.get(field) for field in (*IDENTITY, *REQUEST_FIELDS, "numbering_mode")):
            frappe.throw("产品词典公司、类型及新建追溯身份不能修改")
        if mode == AUTO_MODE and before.legacy_number != document.legacy_number:
            frappe.throw("自动编号保存后不能修改")
        changed = {field for field in EDIT_FIELDS if before.get(field) != document.get(field)}
    _fields(changed, "read")
    _fields(changed, "write")
    for field in changed:
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该产品词典字段的权限", frappe.PermissionError)
    for field, value in _values(document).items():
        # A visible edit must not normalize an unchanged hidden optional value.
        if document.is_new() or field in changed:
            document.set(field, value)


def prevent_delete(document):
    _login()
    frappe.throw("本批产品词典删除尚未实现，请保留词条核对")


def _version(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _projection(document):
    document.check_permission("read")
    _fields(REQUIRED_READ, "read")
    readable = _columns("read")
    requested = ["name", "modified", *FIELDS, *AUDIT_FIELDS]
    listed = frappe.get_list(DOCTYPE, filters={"name": document.name},
                             fields=[field for field in requested if field in readable], limit_page_length=1)
    if not listed or not REQUIRED_READ.issubset(listed[0]):
        frappe.throw("没有读取该产品词条的权限", frappe.PermissionError)
    row = listed[0]
    if any(row[field] != document.get(field) for field in ("name", *IDENTITY)):
        frappe.throw("产品词典来源已变化，请重新核对")
    return row


def _pack(document, *, unscoped=False):
    existing = not document.is_new()
    if existing:
        values = _projection(document)
    else:
        if not (frappe.has_permission(DOCTYPE, "read") if unscoped else document.has_permission("read")):
            frappe.throw("没有读取产品词典的权限", frappe.PermissionError)
        _fields(REQUIRED_READ, "read")
        values = {field: document.get(field) for field in FIELDS if field in _columns("read")}
    writable = _columns("write")
    can_write = (frappe.has_permission(DOCTYPE, "create") if unscoped else
                 document.has_permission("write" if existing else "create"))
    editable = set(EDIT_FIELDS) if existing else set(FIELDS)
    mode = _mode(values.get("numbering_mode"))
    if mode == AUTO_MODE:
        editable.discard("legacy_number")
    meta = {}
    for field in FIELDS:
        if field not in values:
            continue
        df = document.meta.get_field(field)
        meta[field] = {"fieldtype": df.fieldtype, "options": df.options or "", "reqd": bool(df.reqd),
                       "read_only": bool(df.read_only or field not in editable or field not in writable or not can_write),
                       "description": df.description or ""}
    preview = (_number_preview(document.company, document.dictionary_type)
               if not existing and mode == AUTO_MODE and document.company and "legacy_number" in values else None)
    return {"name": values.get("name") if existing else None,
            **{field: values[field] for field in (*FIELDS, *AUDIT_FIELDS) if field in values},
            "version": _version(values.get("modified")) if existing else None,
            "number_preview": preview, "number_preview_is_provisional": preview is not None,
            "field_meta": meta, "can_write": bool(can_write and
                (editable & writable & set(values) if existing else
                 {*IDENTITY, "value", "inactive"}.issubset(writable)))}


@frappe.whitelist()
def init_dictionary(dictionary_type, company=None, numbering_mode=AUTO_MODE):
    _login()
    _type(dictionary_type)
    document = frappe.new_doc(DOCTYPE)
    document.company, document.dictionary_type = None, dictionary_type
    document.numbering_mode = _mode(numbering_mode)
    for field in EDIT_FIELDS:
        document.set(field, 0 if field == "inactive" else None)
    for field in REQUEST_FIELDS:
        document.set(field, None)
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
    include_inactive = _normalize("inactive", include_inactive)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效产品词典关键词")
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有读取产品词典的权限", frappe.PermissionError)
    _fields(REQUIRED_READ, "read")
    readable = _columns("read")
    filters = {"company": company, "dictionary_type": dictionary_type}
    if not include_inactive:
        filters["inactive"] = 0
    options = {}
    if search:
        options["or_filters"] = [[field, "like", "%" + search + "%"]
                                 for field in ("legacy_number", "value", "abbreviation") if field in readable]
    listed = frappe.get_list(DOCTYPE, filters=filters, fields=[field for field in
        ("name", "modified", *FIELDS, *AUDIT_FIELDS) if field in readable],
        order_by="value asc, name asc", limit_page_length=101, **options)
    if len(listed) > 100:
        frappe.throw("词条较多，请输入关键词后查询")
    rows = []
    for row in listed:
        if not REQUIRED_READ.issubset(row):
            frappe.throw("没有读取产品词典所需字段的权限", frappe.PermissionError)
        document = frappe.get_doc(DOCTYPE, row.name)
        if not document.has_permission("read"):
            continue
        if document.company != company or document.dictionary_type != dictionary_type:
            continue
        if row.company != company or row.dictionary_type != dictionary_type or not include_inactive and row.inactive:
            continue
        # Full documents guard scope only; omitted native list columns stay absent.
        rows.append({field: row[field] for field in ("name", *FIELDS, *AUDIT_FIELDS) if field in row}
                    | {"version": _version(row.modified)})
    return rows


@frappe.whitelist()
def read_dictionary(name, dictionary_type=None):
    _login()
    _name(name)
    if dictionary_type is not None:
        _type(dictionary_type)
    document = frappe.get_doc(DOCTYPE, name)
    document.check_permission("read")
    _fields(REQUIRED_READ, "read")
    _company(document.company)
    _type(document.dictionary_type)
    if dictionary_type is not None and document.dictionary_type != dictionary_type:
        frappe.throw("词条类型与当前产品词典不一致")
    return _pack(document)


def _receipt(request_id, dictionary_type, company):
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有核对产品词典的权限", frappe.PermissionError)
    _fields(REQUIRED_READ | set(REQUEST_FIELDS), "read")
    listed = frappe.get_list(DOCTYPE, filters={"request_id": request_id, "company": company,
        "dictionary_type": dictionary_type}, fields=["name"], limit_page_length=1)
    if not listed:
        return None
    document = frappe.get_doc(DOCTYPE, listed[0].name)
    document.check_permission("read")
    if (document.request_id, document.company, document.dictionary_type) != (request_id, company, dictionary_type):
        frappe.throw("产品词典来源已变化，请重新核对")
    return document


@frappe.whitelist()
def resolve_creation(request_id, dictionary_type, company):
    """Read only; not_found is no visible receipt, never permission to retry."""
    _login()
    _uuid(request_id)
    _type(dictionary_type)
    _company(company)
    document = _receipt(request_id, dictionary_type, company)
    return {"status": "found", "archive": _pack(document)} if document else {"status": "not_found"}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_dictionary(payload):
    _login()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("产品词典请求必须是有效对象")
    if not isinstance(payload, dict) or not set(payload).issubset({"name", "version", "request_id", *FIELDS}):
        frappe.throw("产品词典请求包含不支持的参数")
    if not set(IDENTITY).issubset(payload):
        frappe.throw("请选择公司及产品词典类型")
    _company(payload["company"])
    _type(payload["dictionary_type"])
    mode = _mode(payload.get("numbering_mode"))
    updates = {field: _normalize(field, payload[field]) for field in EDIT_FIELDS if field in payload}
    _fields(REQUIRED_READ, "read")
    _fields(updates, "read")
    _fields(updates, "write")
    if payload.get("name") not in (None, ""):
        _name(payload["name"])
        if "request_id" in payload:
            frappe.throw("已有词条不能改写新建请求身份")
        document = frappe.get_doc(DOCTYPE, payload["name"], for_update=True)
        document.check_permission("read")
        document.check_permission("write")
        if not same_version(document.modified, payload.get("version")):
            frappe.throw("产品词典已由其他操作修改，请重读核对后保存", frappe.TimestampMismatchError)
        if tuple(document.get(field) for field in IDENTITY) != tuple(payload[field] for field in IDENTITY):
            frappe.throw("产品词典公司、类型及新建追溯身份不能修改")
        mode = _mode(document.get("numbering_mode"))
        if "numbering_mode" in payload and _mode(payload["numbering_mode"]) != mode:
            frappe.throw("产品词典公司、类型及新建追溯身份不能修改")
        if mode == AUTO_MODE and "legacy_number" in updates:
            if updates["legacy_number"] != document.legacy_number:
                frappe.throw("自动编号保存后不能修改")
            updates.pop("legacy_number")
    else:
        if payload.get("version") not in (None, ""):
            frappe.throw("新增词条不能携带旧版本")
        request_id = _uuid(payload.get("request_id"))
        if mode == AUTO_MODE and "legacy_number" in payload:
            frappe.throw("自动编号只能由服务器生成，不能指定编号")
        values = {field: updates.get(field, 0 if field == "inactive" else None) for field in EDIT_FIELDS}
        digest = _digest(payload["company"], payload["dictionary_type"], values, mode)
        previous = _receipt(request_id, payload["dictionary_type"], payload["company"])
        if previous:
            if previous.request_digest != digest:
                frappe.throw(COLLISION)
            return _pack(previous)
        document = frappe.new_doc(DOCTYPE)
        document.numbering_mode = mode
        for field in IDENTITY:
            document.set(field, payload[field])
        for field, value in values.items():
            document.set(field, value)
        document.request_id, document.request_digest = request_id, digest
        document.check_permission("create")
        _fields({*IDENTITY, "value", "inactive"}, "write")
    for field, value in updates.items():
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该产品词典字段的权限", frappe.PermissionError)
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
        # Exit the native exception context before raising the generic message.
        frappe.throw(COLLISION)
    return _pack(document)
