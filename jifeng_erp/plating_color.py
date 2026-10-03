"""Company-scoped plating colors independent of native Item and Sales Order.

Code and color system are preserved as original text. Numbering is this system's
independent rule; abbreviation generation, barcode generation and legacy printing
remain unavailable because their rules have not been evidenced.
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


DOCTYPE = "Jifeng Plating Color"
TYPES = {"镀色": "color"}
EDIT_FIELDS = ("code", "value", "color_system", "remarks", "inactive")
IDENTITY = ("company", "dictionary_type")
AUTO_MODE = "automatic_v1"
FIELDS = (
    *IDENTITY,
    "legacy_number",
    "code",
    "value",
    "color_system",
    "remarks",
    "abbreviation",
    "inactive",
    "numbering_mode",
)
REQUEST_FIELDS = ("request_id", "request_digest")
REQUIRED_READ = {"name", "modified", *IDENTITY, "value", "inactive", "numbering_mode"}
AUDIT_FIELDS = ("creation", "owner", "modified_by")
_CREATING = ContextVar("jifeng_plating_color_creation", default=None)
_SERVER = object()
COLLISION = "新建请求已使用或身份冲突，请查询核对，不自动重复保存"
CHANGED = "镀色档案来源已变化，请重新核对"
BATCH_LENGTH = 200
MAX_SAFE_INT = 9007199254740991


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作镀色档案", frappe.PermissionError)


def _type(dictionary_type):
    if not isinstance(dictionary_type, str) or dictionary_type not in TYPES:
        frappe.throw("请选择镀色档案类型")


def _company(company):
    if not isinstance(company, str) or not company or company != company.strip():
        frappe.throw("请选择公司")
    frappe.get_doc("Company", company).check_permission("select")


def _name(name):
    if not isinstance(name, str) or not name or name != name.strip():
        frappe.throw("请选择准确的镀色档案词条编号")


def _columns(permission):
    permitted = set(
        get_permitted_fields(DOCTYPE, permission_type=permission, ignore_virtual=True)
    )
    if frappe.session.user == "Administrator":
        return permitted
    # Native mask permission is separate from read permlevel. Read its current
    # metadata and role access rather than the independent masked-fields cache.
    meta = frappe.get_meta(DOCTYPE)
    levels = set(meta.get_permlevel_access("mask", parenttype=None))
    masked = {
        df.fieldname
        for df in meta.fields
        if df.get("mask") and (df.permlevel or 0) not in levels
    }
    return permitted - masked


def _fields(fields, permission):
    if not set(fields).issubset(_columns(permission)):
        frappe.throw("没有操作镀色档案所需字段的权限", frappe.PermissionError)


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
        if type(value) not in (bool, int, str) or value not in (
            False,
            True,
            0,
            1,
            "0",
            "1",
        ):
            frappe.throw("停用须为是或否")
        return int(value)
    if field != "value" and value in (None, ""):
        return value  # Preserve optional original text, including empty text.
    if not isinstance(value, str) or field == "value" and not value.strip():
        frappe.throw("请填写有效镀色档案字段原文；名称须非空")
    if field != "remarks" and len(value) > 140:
        frappe.throw("镀色档案代码、名称及色系不能超过一百四十个字符")
    return value


def _values(document):
    return {field: _normalize(field, document.get(field)) for field in EDIT_FIELDS}


def _mode(value):
    if value not in (None, "", AUTO_MODE):
        frappe.throw("请选择有效镀色档案编号模式")
    return AUTO_MODE


def _series_key(company, dictionary_type):
    return (
        "JF-PL-C-N-"
        + hashlib.sha256(company.encode()).hexdigest()
        + "-"
        + TYPES[dictionary_type]
        + "-"
    )


def _number_preview(company, dictionary_type):
    current = frappe.db.get_value(
        "Series", _series_key(company, dictionary_type), "current", order_by="name"
    )
    return str(int(current or 0) + 1).zfill(3)


def _digest(company, dictionary_type, values, numbering_mode=AUTO_MODE):
    body = {
        "company": company,
        "dictionary_type": dictionary_type,
        "numbering_mode": _mode(numbering_mode),
        "fields": {
            field: _normalize(
                field, values.get(field, 0 if field == "inactive" else None)
            )
            for field in EDIT_FIELDS
        },
    }
    return hashlib.sha256(
        json.dumps(
            body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def before_insert_dictionary(document):
    """Direct native Forms and the API share the same server allocation policy."""
    _login()
    _company(document.company)
    _type(document.dictionary_type)
    document.check_permission("create")
    if document.get("legacy_number") not in (None, ""):
        frappe.throw("自动编号只能由服务器生成，不能指定编号")
    if document.get("abbreviation") not in (None, ""):
        frappe.throw("镀色档案简拼为只读原文，尚未实现更新")
    if _CREATING.get() is document:
        _mode(document.get("numbering_mode"))
        _uuid(document.request_id)
        if document.request_digest != _digest(
            document.company, document.dictionary_type, _values(document)
        ):
            frappe.throw("镀色档案新建请求校验不完整")
    else:
        if any(
            document.get(field) not in (None, "")
            for field in (*REQUEST_FIELDS, "numbering_mode")
        ):
            frappe.throw("镀色档案新建追溯身份只能由服务器生成")
        document.numbering_mode = AUTO_MODE
        document.request_id = str(uuid4())
        document.request_digest = _digest(
            document.company, document.dictionary_type, _values(document)
        )
    populated = {
        field for field in EDIT_FIELDS if document.get(field) not in (None, "")
    }
    _fields({*IDENTITY, "value", "inactive", *populated}, "read")
    _fields({*IDENTITY, "value", "inactive", *populated}, "write")
    from frappe.model.naming import getseries

    document.legacy_number = getseries(
        _series_key(document.company, document.dictionary_type), 3
    )
    document.__dict__["_jf_plating_color_number"] = (_SERVER, document.legacy_number)
    document.__dict__["_jf_plating_color_server"] = _SERVER


def validate_dictionary(document):
    _login()
    _company(document.company)
    _type(document.dictionary_type)
    if document.get("numbering_mode") != AUTO_MODE:
        frappe.throw("镀色档案编号模式只能由服务器生成，不能修改")
    _fields(REQUIRED_READ, "read")
    if document.is_new():
        document.check_permission("create")
        if document.__dict__.get("_jf_plating_color_server") is not _SERVER:
            frappe.throw("镀色档案新建追溯身份只能由服务器生成")
        _uuid(document.request_id)
        if document.request_digest != _digest(
            document.company, document.dictionary_type, _values(document)
        ):
            frappe.throw("镀色档案新建请求校验不完整")
        generated = document.__dict__.get("_jf_plating_color_number")
        if (
            not generated
            or generated[0] is not _SERVER
            or generated[1] != document.legacy_number
        ):
            frappe.throw("自动编号只能由服务器生成，不能指定编号")
        if document.get("abbreviation") not in (None, ""):
            frappe.throw("镀色档案简拼为只读原文，尚未实现更新")
        changed = {
            field for field in EDIT_FIELDS if document.get(field) not in (None, "")
        }
        changed.update((*IDENTITY, "value", "inactive"))
    else:
        document.check_permission("write")
        before = document.get_doc_before_save()
        immutable = (
            *IDENTITY,
            *REQUEST_FIELDS,
            "numbering_mode",
            "legacy_number",
            "abbreviation",
        )
        if not before or any(
            before.get(field) != document.get(field) for field in immutable
        ):
            frappe.throw("镀色档案公司、类型、编号、简拼及新建追溯身份不能修改")
        changed = {
            field for field in EDIT_FIELDS if before.get(field) != document.get(field)
        }
    _fields(changed, "read")
    _fields(changed, "write")
    for field in changed:
        # This fixed readonly identity is initialized only after the server
        # allocation marker, UUID and digest were verified above. It is never
        # an editable field, and existing documents keep immutable identity.
        if document.is_new() and field == "dictionary_type":
            continue
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该镀色档案字段的权限", frappe.PermissionError)
    for field in changed & set(EDIT_FIELDS):
        document.set(field, _normalize(field, document.get(field)))


def prevent_delete(document):
    _login()
    frappe.throw("镀色档案删除尚未实现，请保留词条核对")


def _version(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _current(document, row):
    if not REQUIRED_READ.issubset(row):
        frappe.throw("没有读取镀色档案来源投影的权限", frappe.PermissionError)
    if any(
        document.get(field) != row[field] for field in ("name", *IDENTITY)
    ) or not same_version(document.modified, _version(row["modified"])):
        frappe.throw(CHANGED)


def _projection(document):
    document.check_permission("read")
    _fields(REQUIRED_READ, "read")
    readable = _columns("read")
    requested = ["name", "modified", *FIELDS, *AUDIT_FIELDS]
    listed = frappe.get_list(
        DOCTYPE,
        filters={"name": document.name},
        fields=[field for field in requested if field in readable],
        limit_page_length=1,
    )
    if not listed:
        frappe.throw("没有读取该镀色档案的权限", frappe.PermissionError)
    _current(document, listed[0])
    # Full native documents are guards, never a source for omitted columns.
    return listed[0]


def _pack(document, *, unscoped=False):
    existing = not document.is_new()
    if existing:
        values = _projection(document)
    else:
        if not (
            frappe.has_permission(DOCTYPE, "read")
            if unscoped
            else document.has_permission("read")
        ):
            frappe.throw("没有读取镀色档案的权限", frappe.PermissionError)
        _fields(REQUIRED_READ, "read")
        values = {
            field: document.get(field) for field in FIELDS if field in _columns("read")
        }
    writable = _columns("write")
    # A capability probe must not enqueue a denial dialog for a valid reader.
    # Frappe's wrapper maps throw=False to permissions.print_logs=False.
    can_write = frappe.has_permission(
        DOCTYPE,
        "create" if unscoped or not existing else "write",
        doc=None if unscoped else document,
        throw=False,
    )
    editable = set(EDIT_FIELDS) if existing else {"company", *EDIT_FIELDS}
    meta = {}
    for field in FIELDS:
        if field not in values:
            continue
        df = document.meta.get_field(field)
        meta[field] = {
            "fieldtype": df.fieldtype,
            "options": df.options or "",
            "reqd": bool(df.reqd),
            "read_only": bool(
                df.read_only
                or field not in editable
                or field not in writable
                or not can_write
            ),
            "description": df.description or "",
        }
    preview = (
        _number_preview(document.company, document.dictionary_type)
        if not existing and document.company and "legacy_number" in values
        else None
    )
    return {
        "name": values.get("name") if existing else None,
        **{
            field: values[field]
            for field in (*FIELDS, *AUDIT_FIELDS)
            if field in values
        },
        "version": _version(values.get("modified")) if existing else None,
        "number_preview": preview,
        "number_preview_is_provisional": preview is not None,
        "field_meta": meta,
        "can_write": bool(
            can_write
            and (
                editable & writable & set(values)
                if existing
                else {*IDENTITY, "value", "inactive"}.issubset(writable)
            )
        ),
    }


@frappe.whitelist()
def init_dictionary(dictionary_type, company=None, numbering_mode=AUTO_MODE):
    _login()
    _type(dictionary_type)
    document = frappe.new_doc(DOCTYPE)
    document.company, document.dictionary_type = None, dictionary_type
    document.numbering_mode = _mode(numbering_mode)
    for field in ("legacy_number", "abbreviation", *REQUEST_FIELDS, *EDIT_FIELDS):
        document.set(field, 0 if field == "inactive" else None)
    if company not in (None, ""):
        _company(company)
        document.company = company
        # Sales User may inspect the dialog read-only, but cannot create.
        document.check_permission("read")
    return _pack(document, unscoped=not document.company)


def _page(value, label):
    if (
        isinstance(value, str)
        and len(value) <= 16
        and value.isascii()
        and value.isdecimal()
    ):
        value = int(value)
    if type(value) is not int or value < 0 or value > MAX_SAFE_INT:
        frappe.throw(label + "须为非负安全整数")
    return value


def _scan(filters, options, readable, company, dictionary_type, include_inactive):
    rows, seen, start = [], set(), 0
    requested = ["name", "modified", *FIELDS, *AUDIT_FIELDS]
    while True:
        listed = frappe.get_list(
            DOCTYPE,
            filters=filters,
            fields=[field for field in requested if field in readable],
            order_by="value asc, name asc",
            limit_start=start,
            limit_page_length=BATCH_LENGTH,
            **options,
        )
        for row in listed:
            if not REQUIRED_READ.issubset(row):
                frappe.throw("没有读取镀色档案来源投影的权限", frappe.PermissionError)
            if (
                row["name"] in seen
                or row["company"] != company
                or row["dictionary_type"] != dictionary_type
                or not include_inactive
                and row["inactive"]
            ):
                frappe.throw(CHANGED)
            seen.add(row["name"])
            try:
                document = frappe.get_doc(DOCTYPE, row["name"])
            except frappe.DoesNotExistError:
                frappe.throw(CHANGED)
            _current(document, row)
            if document.has_permission("read"):
                rows.append(
                    {
                        field: row[field]
                        for field in ("name", *FIELDS, *AUDIT_FIELDS)
                        if field in row
                    }
                    | {"version": _version(row["modified"])}
                )
        if len(listed) < BATCH_LENGTH:
            break
        start += len(listed)
    return rows


@frappe.whitelist()
def list_dictionary(
    dictionary_type, company, search="", include_inactive=False, start=0, page_length=25
):
    _login()
    _type(dictionary_type)
    _company(company)
    include_inactive = _normalize("inactive", include_inactive)
    start, page_length = _page(start, "分页起点"), _page(page_length, "分页条数")
    if page_length not in (25, 50, 100):
        frappe.throw("分页条数须为二十五、五十或一百")
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效镀色档案关键词")
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有读取镀色档案的权限", frappe.PermissionError)
    _fields(REQUIRED_READ, "read")
    readable = _columns("read")
    filters = {"company": company, "dictionary_type": dictionary_type}
    if not include_inactive:
        filters["inactive"] = 0
    options = {}
    if search:
        options["or_filters"] = [
            [field, "like", "%" + search + "%"]
            for field in (
                "legacy_number",
                "code",
                "value",
                "color_system",
                "remarks",
                "abbreviation",
            )
            if field in readable
        ]
    rows = _scan(filters, options, readable, company, dictionary_type, include_inactive)
    # Detect additions, removals, permission/source changes and offset drift.
    # This is two serial native projections, not an atomic DB snapshot.
    if rows != _scan(
        filters, options, readable, company, dictionary_type, include_inactive
    ) or readable != _columns("read"):
        frappe.throw(CHANGED)
    total = len(rows)
    start = min(start, ((total - 1) // page_length) * page_length) if total else 0
    return {
        "rows": rows[start : start + page_length],
        "total": total,
        "start": start,
        "page_length": page_length,
        "company": company,
        "dictionary_type": dictionary_type,
        "search": search,
        "include_inactive": include_inactive,
    }


@frappe.whitelist()
def read_dictionary(name, dictionary_type):
    _login()
    _name(name)
    _type(dictionary_type)
    document = frappe.get_doc(DOCTYPE, name)
    document.check_permission("read")
    _fields(REQUIRED_READ, "read")
    _company(document.company)
    _type(document.dictionary_type)
    if document.dictionary_type != dictionary_type:
        frappe.throw("词条类型与当前镀色档案不一致")
    return _pack(document)


def _receipt(request_id, dictionary_type, company):
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有核对镀色档案的权限", frappe.PermissionError)
    _fields(REQUIRED_READ | set(REQUEST_FIELDS), "read")
    receipt_fields = {"name", "modified", *IDENTITY, *REQUEST_FIELDS}
    listed = frappe.get_list(
        DOCTYPE,
        filters={
            "request_id": request_id,
            "company": company,
            "dictionary_type": dictionary_type,
        },
        fields=sorted(receipt_fields),
        limit_page_length=1,
    )
    if not listed:
        return None
    if not receipt_fields.issubset(listed[0]):
        frappe.throw("没有读取镀色档案新建回执投影的权限", frappe.PermissionError)
    document = frappe.get_doc(DOCTYPE, listed[0]["name"])
    document.check_permission("read")
    if (
        (document.request_id, document.company, document.dictionary_type)
        != (request_id, company, dictionary_type)
        or any(
            document.get(field) != listed[0][field]
            for field in ("name", *IDENTITY, *REQUEST_FIELDS)
        )
        or not same_version(document.modified, _version(listed[0]["modified"]))
    ):
        frappe.throw(CHANGED)
    return document


@frappe.whitelist()
def resolve_creation(request_id, dictionary_type, company):
    """Read-only receipt lookup; not_found does not authorize a retry."""
    _login()
    _uuid(request_id)
    _type(dictionary_type)
    _company(company)
    document = _receipt(request_id, dictionary_type, company)
    return (
        {"status": "found", "archive": _pack(document)}
        if document
        else {"status": "not_found"}
    )


@frappe.whitelist(methods=["POST"])
@_atomic
def save_dictionary(payload):
    _login()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("镀色档案请求必须是有效对象")
    allowed = {
        "name",
        "version",
        "request_id",
        *IDENTITY,
        *EDIT_FIELDS,
        "numbering_mode",
    }
    if not isinstance(payload, dict) or not set(payload).issubset(allowed):
        frappe.throw("镀色档案请求包含不支持的参数；编号及简拼为只读")
    if not set(IDENTITY).issubset(payload):
        frappe.throw("请选择公司及镀色档案类型")
    _company(payload["company"])
    _type(payload["dictionary_type"])
    _mode(payload.get("numbering_mode"))
    updates = {
        field: _normalize(field, payload[field])
        for field in EDIT_FIELDS
        if field in payload
    }
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
            frappe.throw(
                "镀色档案已由其他操作修改，请重读核对后保存",
                frappe.TimestampMismatchError,
            )
        if tuple(document.get(field) for field in IDENTITY) != tuple(
            payload[field] for field in IDENTITY
        ):
            frappe.throw("镀色档案公司、类型及新建追溯身份不能修改")
    else:
        if payload.get("version") not in (None, ""):
            frappe.throw("新增词条不能携带旧版本")
        request_id = _uuid(payload.get("request_id"))
        values = {
            field: updates.get(field, 0 if field == "inactive" else None)
            for field in EDIT_FIELDS
        }
        digest = _digest(payload["company"], payload["dictionary_type"], values)
        previous = _receipt(request_id, payload["dictionary_type"], payload["company"])
        if previous:
            if previous.request_digest != digest:
                frappe.throw(COLLISION)
            return _pack(previous)
        document = frappe.new_doc(DOCTYPE)
        document.numbering_mode = AUTO_MODE
        for field in IDENTITY:
            document.set(field, payload[field])
        for field, value in values.items():
            document.set(field, value)
        document.legacy_number, document.abbreviation = None, None
        document.request_id, document.request_digest = request_id, digest
        document.check_permission("create")
        _fields({*IDENTITY, "value", "inactive"}, "write")
    for field, value in updates.items():
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改该镀色档案字段的权限", frappe.PermissionError)
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
