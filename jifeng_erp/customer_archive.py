"""Company customer overlays. Native parties, contacts and accounts stay native.

New parties use the configured Customer naming rule. Display names never identify
an existing party. Numbers are this system's independent company/module sequence.
"""

from contextvars import ContextVar
from datetime import date, datetime
import hashlib
import json
import re
from uuid import UUID

import frappe
from frappe.model import get_permitted_fields
from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


PROFILE = "Jifeng Customer Profile"
CUSTOMER = "Customer"
DICTIONARY = "Jifeng Customer Type"
KINDS = ("销售客户", "订单客户")
DICT_TYPES = {"销售客户": {"category": "客户分类", "grade": "客户等级", "customer_kind": "客户类型"},
              "订单客户": {"category": "订单客户分类", "grade": "订单客户级别"}}
TEXT_FIELDS = {"legacy_code", "display_name", "region", "province", "district", "salesperson",
               "contact_name", "tax_number", "company_text", "mobile", "legal_person", "address", "notes"}
IDENTITY = {"company", "archive_kind", "customer"}
SERVER_FIELDS = {"legacy_number", "numbering_mode", "binding_key", "request_id", "request_digest"}
REQUIRED_READ = IDENTITY | {"name", "modified", "legacy_number", "display_name", "inactive", "numbering_mode"}
CUSTOMER_CREATE = {"customer_group", "territory", "customer_type", "naming_series"}
AUTO_MODE = "automatic_v1"
MAX_SAFE_INT = 9007199254740991
BATCH_LENGTH = 200
_CREATING = ContextVar("jifeng_customer_archive_creation", default=None)
_SERVER = object()


def _deny():
    frappe.throw("没有完整读取客户档案及关联客户、词典的权限", frappe.PermissionError)


def _kind(kind):
    if kind not in KINDS:
        frappe.throw("请选择有效客户档案种类")
    return kind


def _scope(kind, company, unscoped=False):
    if frappe.session.user == "Guest":
        _deny()
    _kind(kind)
    if unscoped and company in (None, ""):
        return None
    if not isinstance(company, str) or not company.strip() or company != company.strip():
        frappe.throw("请选择有效公司")
    document = frappe.get_doc("Company", company)
    document.check_permission("select")
    return document


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            frappe.throw("客户档案请求必须是有效对象")
    if not isinstance(value, dict):
        frappe.throw("客户档案请求必须是对象")
    return value


def _uuid(value):
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        frappe.throw("新建请求必须包含有效UUID4身份")
    return value


def _timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            frappe.throw("客户档案来源时间不完整")
    if not isinstance(value, datetime) or value.tzinfo is not None:
        frappe.throw("客户档案来源时间不完整")
    return value.isoformat()


def _value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return type(value).__name__, repr(value)


def _state(document):
    values = document.as_dict() if callable(getattr(document, "as_dict", None)) else dict(document)
    return _freeze({key: value for key, value in values.items() if not key.startswith("__")})


def _columns(doctype, permission):
    names = set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))
    if permission == "read" and frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
        names = {name for name in names if not ((field := meta.get_field(name)) and
                 field.get("mask") and (field.permlevel or 0) not in levels)}
    return names


def _capability(doctype, permission, document=None):
    # throw=False suppresses Frappe permission log/msgprint side effects.
    return bool(frappe.has_permission(doctype, permission, doc=document, throw=False))


def _editable(kind):
    return TEXT_FIELDS | {"inactive"} | set(DICT_TYPES[kind]) | ({"discount"} if kind == "销售客户" else set())


def _projection(document, fields):
    """Exact native list projection; omitted fields never come from get_doc."""
    fields = set(fields)
    rows = frappe.get_list(document.doctype, filters={"name": document.name},
                           fields=sorted(fields), limit_page_length=1)
    if len(rows) != 1 or not fields.issubset(rows[0]):
        _deny()
    row = rows[0]
    for field in fields:
        equal = (same_version(document.get(field), _value(row[field])) if field in ("modified", "creation")
                 and document.get(field) is not None else document.get(field) == row[field])
        if not equal:
            frappe.throw("客户档案关联来源或投影已改变，请重新打开")
    document.check_permission("read")
    if not fields.issubset(_columns(document.doctype, "read")):
        _deny()
    return row


def _source(doctype, name, required, lock=False):
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        frappe.throw("请显式选择关联来源")
    document = frappe.get_doc(doctype, name, for_update=lock)
    document.check_permission("read")
    document.check_permission("select")
    if not required.issubset(_columns(doctype, "read")):
        _deny()
    # Native list hooks and User Permissions participate even in exact reads.
    _projection(document, required)
    document.check_permission("select")
    return document


def _customer(name, lock=False):
    return _source(CUSTOMER, name, {"name", "modified", "disabled"}, lock)


def _dictionary(name, field, kind, company, lock=False):
    if field not in DICT_TYPES[kind]:
        frappe.throw("请选择有效客户词典字段")
    document = _source(DICTIONARY, name,
        {"name", "modified", "company", "dictionary_type", "value", "inactive"}, lock)
    if document.company != company or document.dictionary_type != DICT_TYPES[kind][field]:
        _deny()
    return document


def _dictionaries(document, lock=False):
    return {field: _dictionary(document.get(field), field, document.archive_kind, document.company, lock)
            for field in DICT_TYPES[document.archive_kind] if document.get(field)}


def _load(name, kind, company, lock=False):
    _scope(kind, company)
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        frappe.throw("请选择客户档案")
    document = frappe.get_doc(PROFILE, name, for_update=lock)
    document.check_permission("read")
    if document.company != company or document.archive_kind != kind or not REQUIRED_READ.issubset(_columns(PROFILE, "read")):
        _deny()
    fields = (_editable(kind) | IDENTITY | {"legacy_number", "creation", "owner", "modified_by"}) & _columns(PROFILE, "read")
    document.__dict__["_jf_customer_archive_projection"] = _projection(document, fields | REQUIRED_READ)
    return document, _customer(document.customer, lock), _dictionaries(document, lock)


def _series_key(company, kind):
    return "JF-CUST-P-N-" + hashlib.sha256(company.encode()).hexdigest() + ("-sales-" if kind == "销售客户" else "-order-")


def _number_preview(company, kind):
    current = frappe.db.get_value("Series", _series_key(company, kind), "current", order_by="name")
    return str(int(current or 0) + 1).zfill(3)


def _binding(company, kind, customer):
    return hashlib.sha256(json.dumps([company, kind, customer], ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _customer_controls(profile, defaults=False, unscoped=False):
    customer = frappe.new_doc(CUSTOMER)
    readable, writable = _columns(CUSTOMER, "read"), _columns(CUSTOMER, "write")
    profile_create = _capability(PROFILE, "create", None if unscoped else profile)
    can_create = bool(profile_create and _capability(CUSTOMER, "create", customer) and
        {"name", "modified", "disabled", "customer_name"}.issubset(readable) and "customer_name" in writable)
    can_link = bool(profile_create and _capability(CUSTOMER, "read") and
        {"name", "modified", "disabled"}.issubset(readable))
    fields, values = {}, {}
    if defaults and profile.company:
        for name in sorted(CUSTOMER_CREATE & readable):
            field = customer.meta.get_field(name)
            if not field:
                continue
            value = customer.get(name)
            if field.fieldtype == "Link" and value:
                linked = frappe.get_doc(field.options, value)
                linked.check_permission("select")
                if name == "customer_group" and linked.get("is_group"):
                    frappe.throw("默认客户分组必须是具体分组，请核对原生默认设置")
            if field.fieldtype == "Select" and value not in (None, "") and value not in (field.options or "").split("\n"):
                frappe.throw("原生客户默认选项已变化，请核对设置")
            values[name] = value
            fields[name] = {"fieldtype": field.fieldtype, "options": field.options or "", "reqd": bool(field.reqd),
                            "read_only": bool(not can_create or field.read_only or name not in writable)}
    return {"can_create_customer": can_create, "can_link_customer": can_link,
            "customer_defaults": values, "customer_field_meta": fields}


def _pack(document, customer=None, dictionaries=None, listing=False, unscoped=False):
    existing = not document.is_new()
    readable, writable = _columns(PROFILE, "read"), _columns(PROFILE, "write")
    if not REQUIRED_READ.issubset(readable):
        _deny()
    can_write = _capability(PROFILE, "write" if existing else "create", None if unscoped else document) and not listing
    visible = _editable(document.archive_kind) | IDENTITY | {"legacy_number"}
    editable = _editable(document.archive_kind) | ({"customer"} if not existing else set())
    fields, values = {}, {}
    for name in sorted(visible & readable):
        field = document.meta.get_field(name)
        if not field:
            frappe.throw("客户档案字段尚未安装")
        projection = document.__dict__.get("_jf_customer_archive_projection", {}) if existing else None
        if existing and name not in projection:
            _deny()
        values[name] = _value(projection[name] if existing else document.get(name))
        fields[name] = {"fieldtype": field.fieldtype, "options": field.options or "", "reqd": bool(field.reqd),
                       "read_only": bool(field.read_only or not can_write or name not in editable or name not in writable),
                       "description": field.description or ""}
    # A readable dictionary never grants access to a masked profile link.
    # Full linked documents guard consistency, but do not fill omitted fields.
    dictionaries = {field: source for field, source in (dictionaries or {}).items() if field in values}
    result = {"name": document.name if existing else None, "kind": document.archive_kind, "company": document.company,
        "profile": values, "field_meta": fields, "can_write": bool(can_write),
        "profile_version": _timestamp(document.modified) if existing else None,
        "customer": customer.name if customer else None, "customer_version": _timestamp(customer.modified) if customer else None,
        "customer_disabled": bool(customer.disabled) if customer else None,
        "dictionary_versions": {field: _timestamp(source.modified) for field, source in dictionaries.items()},
        "dictionary_values": {field: source.value for field, source in dictionaries.items()},
        "creation": _timestamp(document.creation) if existing and "creation" in readable else None,
        "owner": document.owner if existing and "owner" in readable else None,
        "modified_by": document.modified_by if existing and "modified_by" in readable else None,
        "numbering_mode": AUTO_MODE, "number_reference_only": not existing}
    result.update(_customer_controls(document, defaults=not existing, unscoped=unscoped))
    return result


def _guard(document, customer, dictionaries, view):
    states = (_state(document), _state(customer), {field: _state(source) for field, source in dictionaries.items()})
    current, linked, words = _load(document.name, document.archive_kind, document.company)
    if states != (_state(current), _state(linked), {field: _state(source) for field, source in words.items()}):
        frappe.throw("客户档案或关联来源已改变，请重新打开")
    if not set(view["profile"]).issubset(_columns(PROFILE, "read")):
        _deny()
    if view != _pack(current, linked, words, listing=not view["can_write"]):
        frappe.throw("客户档案字段或权限已改变，请重新打开")


def _normalize(values, allowed):
    values = _object(values)
    if not set(values).issubset(allowed):
        frappe.throw("客户档案包含不支持或只读字段")
    result = {}
    for name, value in values.items():
        if name == "inactive":
            if type(value) not in (bool, int, str) or value not in (False, True, 0, 1, "0", "1"):
                frappe.throw("请选择有效停用状态")
            result[name] = int(value)
        elif value is None or isinstance(value, str):
            field = frappe.get_meta(PROFILE).get_field(name)
            if not field:
                frappe.throw("客户档案字段尚未安装")
            limit = int(field.length or 140) if field.fieldtype in ("Data", "Link", "Select") else 65535
            if isinstance(value, str) and len(value) > limit:
                frappe.throw("客户档案原文过长：" + field.label)
            result[name] = value
        else:
            frappe.throw("客户档案字段必须是原文文本")
    return result


def _updates(document, values):
    allowed = _editable(document.archive_kind) | ({"customer"} if document.is_new() else set())
    values = _normalize(values, allowed | IDENTITY | {"legacy_number"})
    for name, value in values.items():
        if name in IDENTITY | {"legacy_number"} and not (name == "customer" and document.is_new()):
            if value != document.get(name):
                frappe.throw("客户档案归属、客户和服务器编号不能修改")
            continue
        field = document.meta.get_field(name)
        if name not in _columns(PROFILE, "read") & _columns(PROFILE, "write") or field.read_only:
            _deny()
        document.set(name, value)


def _latest(document, version):
    if not same_version(document.modified, version):
        frappe.throw("客户档案或关联来源已修改，请重新打开后保存", frappe.TimestampMismatchError)


def _versions(document, supplied, lock=False, previous=None):
    supplied = _object(supplied)
    sources = _dictionaries(document, lock)
    if not set(sources).issubset(_columns(PROFILE, "read")):
        _deny()
    if set(supplied) != set(sources):
        frappe.throw("请提供所有实际所选客户词典的版本")
    for field, source in sources.items():
        _latest(source, supplied[field])
        if source.inactive and (not previous or previous.get(field) != source.name):
            frappe.throw("停用客户词典不能用于新的选择")
    return sources


def _customer_values(values):
    values = _object(values)
    if not set(values).issubset(CUSTOMER_CREATE):
        frappe.throw("原生客户新建参数包含不支持的字段")
    normalized = {}
    for name, value in values.items():
        field = frappe.get_meta(CUSTOMER).get_field(name)
        if not field or value is not None and (not isinstance(value, str) or len(value) > int(field.length or 140)):
            frappe.throw("请输入有效原生客户字段")
        if name not in _columns(CUSTOMER, "read") & _columns(CUSTOMER, "write") or field.read_only:
            _deny()
        if field.fieldtype == "Link" and value:
            linked = frappe.get_doc(field.options, value)
            linked.check_permission("select")
            if name == "customer_group" and linked.get("is_group"):
                frappe.throw("请选择具体原生客户分组")
        if field.fieldtype == "Select" and value not in (None, "") and value not in (field.options or "").split("\n"):
            frappe.throw("请选择有效原生客户选项")
        normalized[name] = value
    return normalized


def _create_customer(document, values):
    customer = frappe.new_doc(CUSTOMER)
    customer.check_permission("create")
    if not {"name", "modified", "disabled", "customer_name"}.issubset(_columns(CUSTOMER, "read")) or "customer_name" not in _columns(CUSTOMER, "write"):
        _deny()
    for name, value in values.items():
        customer.set(name, value)
    # Validate effective native defaults too, then run all normal native hooks.
    _customer_values({name: customer.get(name) for name in CUSTOMER_CREATE if customer.meta.get_field(name)})
    customer.customer_name, customer.disabled = document.display_name, 0
    try:
        customer.save()
    except frappe.DuplicateEntryError as error:
        if len(error.args) < 2 or error.args[0] != CUSTOMER or error.args[1] != customer.name:
            raise
        frappe.throw("原生客户编号冲突，请核对命名设置或显式选择已有客户；未自动关联")
    return customer


@frappe.whitelist()
def init_archive(kind, company=None):
    _scope(kind, company, unscoped=True)
    document = frappe.new_doc(PROFILE)
    document.company, document.archive_kind, document.inactive = company or None, kind, 0
    document.numbering_mode = AUTO_MODE
    if not _capability(PROFILE, "read", document if company else None):
        _deny()
    if company:
        document.legacy_number = _number_preview(company, kind)
    view = _pack(document, unscoped=not bool(company))
    _scope(kind, company, unscoped=True)
    if view != _pack(document, unscoped=not bool(company)):
        frappe.throw("客户档案字段或权限已改变，请重新新增")
    return view


@frappe.whitelist()
def read_customer(name, kind, company):
    _scope(kind, company)
    profile = frappe.new_doc(PROFILE)
    profile.company, profile.archive_kind = company, kind
    profile.check_permission("create")
    source = _customer(name)
    before = _state(source)
    current = _customer(name)
    _scope(kind, company)
    profile.check_permission("create")
    if before != _state(current):
        frappe.throw("关联客户已改变，请重新选择")
    return {"name": source.name, "version": _timestamp(source.modified), "disabled": bool(source.disabled), "kind": kind, "company": company}


@frappe.whitelist()
def read_dictionary(name, field, kind, company):
    _scope(kind, company)
    if not _capability(PROFILE, "read") or field not in _columns(PROFILE, "read"):
        _deny()
    source = _dictionary(name, field, kind, company)
    current = _dictionary(name, field, kind, company)
    _scope(kind, company)
    if _state(source) != _state(current):
        frappe.throw("客户词典已改变，请重新选择")
    if field not in _columns(PROFILE, "read"):
        _deny()
    return {"name": source.name, "version": _timestamp(source.modified), "company": source.company,
            "dictionary_type": source.dictionary_type, "value": source.value, "inactive": bool(source.inactive)}


@frappe.whitelist()
def read_archive(name, kind, company):
    document, customer, sources = _load(name, kind, company)
    view = _pack(document, customer, sources)
    _guard(document, customer, sources, view)
    return view


def _page(value, maximum):
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) and len(value) <= 16:
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        frappe.throw("请输入有效页码或每页条数")
    return value


def _scan(query):
    names, start = [], 0
    while True:
        batch = frappe.get_list(PROFILE, **query, limit_start=start, limit_page_length=BATCH_LENGTH)
        if len(batch) != len(set(batch)) or set(names) & set(batch):
            frappe.throw("客户档案查询范围已改变，请重新查询")
        names.extend(batch)
        if len(batch) < BATCH_LENGTH:
            return names
        start += len(batch)


@frappe.whitelist()
def query_archives(kind, company, search="", status="在用", page=1, page_size=100):
    _scope(kind, company)
    page, page_size = _page(page, MAX_SAFE_INT), _page(page_size, 100)
    if status not in ("在用", "停用", "全部") or not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效关键词及档案状态")
    if not _capability(PROFILE, "read") or not REQUIRED_READ.issubset(_columns(PROFILE, "read")):
        _deny()
    filters = {"company": company, "archive_kind": kind}
    if status != "全部":
        filters["inactive"] = int(status == "停用")
    query = {"filters": filters, "pluck": "name", "order_by": "modified desc, name asc"}
    if search:
        search_fields = {"legacy_number", "legacy_code", "display_name", "mobile", "contact_name"} & _columns(PROFILE, "read")
        query["or_filters"] = {field: ["like", "%" + search + "%"] for field in sorted(search_fields)}
    names, checked = _scan(query), []
    for name in names:
        try:
            document, customer, sources = _load(name, kind, company)
        except frappe.PermissionError:
            continue
        checked.append((document, customer, sources, _pack(document, customer, sources, listing=True)))
    if names != _scan(query):
        frappe.throw("客户档案查询范围已改变，请重新查询")
    for document, customer, sources, view in checked:
        _guard(document, customer, sources, view)
    # A later row can change permissions of an earlier row: final source reads
    # must recheck all emitted fields and linked facts before totals/pagination.
    _scope(kind, company)
    for document, customer, sources, view in checked:
        document.check_permission("read")
        customer.check_permission("read")
        customer.check_permission("select")
        if not set(view["profile"]).issubset(_columns(PROFILE, "read")):
            _deny()
        for source in sources.values():
            source.check_permission("read")
            source.check_permission("select")
            if not {"name", "modified", "company", "dictionary_type", "value", "inactive"}.issubset(_columns(DICTIONARY, "read")):
                _deny()
        if not {"name", "modified", "disabled"}.issubset(_columns(CUSTOMER, "read")):
            _deny()
        if view != _pack(document, customer, sources, listing=True):
            frappe.throw("客户档案字段或权限已改变，请重新查询")
    total = len(checked)
    total_pages = (total + page_size - 1) // page_size
    page = min(page, max(1, total_pages))
    start = (page - 1) * page_size
    return {"kind": kind, "company": company, "search": search, "status": status, "page": page,
        "page_size": page_size, "total": total, "total_pages": total_pages,
        "readonly": True, "rows": [row[3] for row in checked[start:start + page_size]]}


@frappe.whitelist()
def resolve_creation(request_id, kind, company):
    _scope(kind, company)
    _uuid(request_id)
    if not _capability(PROFILE, "read"):
        _deny()
    name = frappe.db.get_value(PROFILE, {"request_id": request_id}, "name")
    return {"status": "found", "archive": read_archive(name, kind, company)} if name else {"status": "not_found"}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_archive(payload):
    payload = _object(payload)
    if not set(payload).issubset({"kind", "company", "name", "profile_version", "customer_version", "request_id",
                                "profile", "customer_create", "dictionary_versions"}):
        frappe.throw("客户档案请求包含不支持的参数")
    kind, company = payload.get("kind"), payload.get("company")
    _scope(kind, company)
    values = _object(payload.get("profile", {}))
    if payload.get("name"):
        if payload.get("request_id") or "customer_create" in payload:
            frappe.throw("已有客户档案不能改写新建请求身份")
        document, customer, _ = _load(payload["name"], kind, company, lock=True)
        document.check_permission("write")
        _latest(document, payload.get("profile_version"))
        _latest(customer, payload.get("customer_version"))
        previous = {field: document.get(field) for field in DICT_TYPES[kind]}
        customer_before = _state(customer)
        _updates(document, values)
        sources = _versions(document, payload.get("dictionary_versions", {}), lock=True, previous=previous)
        dictionary_before = {field: _state(source) for field, source in sources.items()}
        document.save()
        if _state(_customer(customer.name)) != customer_before or dictionary_before != {
                field: _state(source) for field, source in _dictionaries(document).items()}:
            frappe.throw("关联客户或词典在保存期间已改变，请重新打开")
    else:
        if payload.get("profile_version"):
            frappe.throw("新档案不能携带已有档案版本")
        request_id = _uuid(payload.get("request_id"))
        normalized = _normalize(values, _editable(kind) | {"customer"})
        linked = bool(normalized.get("customer"))
        if linked and "customer_create" in payload:
            frappe.throw("关联已有客户不能同时新建客户")
        if not linked and payload.get("customer_version"):
            frappe.throw("新建客户不能携带已有客户版本")
        customer_values = None if linked else _customer_values(payload.get("customer_create", {}))
        versions = _object(payload.get("dictionary_versions", {}))
        body = {"kind": kind, "company": company, "profile": normalized,
                "customer_create": customer_values, "customer_version": payload.get("customer_version"),
                "dictionary_versions": versions}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        name = frappe.db.get_value(PROFILE, {"request_id": request_id}, "name")
        if name:
            document, customer, _ = _load(name, kind, company)
            if document.request_digest != digest:
                frappe.throw("该新建请求已用于不同内容，请核对原保存结果")
        else:
            document = frappe.new_doc(PROFILE)
            document.company, document.archive_kind, document.inactive = company, kind, 0
            document.numbering_mode = AUTO_MODE
            document.check_permission("create")
            _updates(document, normalized)
            if not isinstance(document.display_name, str) or not document.display_name.strip():
                frappe.throw("请填写客户档案名称")
            sources = _versions(document, versions, lock=True)
            if linked:
                customer = _customer(document.customer, lock=True)
                _latest(customer, payload.get("customer_version"))
                if customer.disabled:
                    frappe.throw("停用原生客户不能关联新客户档案")
            else:
                customer = _create_customer(document, customer_values)
                if "customer" not in _columns(PROFILE, "read") & _columns(PROFILE, "write"):
                    _deny()
                document.customer = customer.name
                # Native save timestamps are strings; database reads return
                # datetimes. Guard the persisted party, not transient values.
                customer = _customer(customer.name, lock=True)
            document.request_id, document.request_digest = request_id, digest
            customer_before = _state(customer)
            dictionary_before = {field: _state(source) for field, source in sources.items()}
            context = _CREATING.set(document)
            try:
                document.save()
            finally:
                _CREATING.reset(context)
            if _state(_customer(customer.name)) != customer_before or dictionary_before != {
                    field: _state(source) for field, source in _dictionaries(document).items()}:
                frappe.throw("关联客户或词典在保存期间已改变，请重新打开")
    document, customer, sources = _load(document.name, kind, company)
    view = _pack(document, customer, sources)
    _guard(document, customer, sources, view)
    return view


def before_insert_profile(document):
    _scope(document.archive_kind, document.company)
    document.check_permission("create")
    if _CREATING.get() is not document or document.get("legacy_number") not in (None, "") or document.get("binding_key") not in (None, ""):
        frappe.throw("请从客户档案入口新建，编号和关联身份只能由服务器生成")
    from frappe.model.naming import getseries
    document.legacy_number = getseries(_series_key(document.company, document.archive_kind), 3)
    document.binding_key = _binding(document.company, document.archive_kind, document.customer)
    document.__dict__["_jf_customer_archive_server"] = (_SERVER, document.legacy_number, document.binding_key)


def validate_profile(document):
    _scope(document.archive_kind, document.company)
    before = None if document.is_new() else document.get_doc_before_save()
    if document.is_new():
        document.check_permission("create")
        generated = document.__dict__.get("_jf_customer_archive_server")
        if _CREATING.get() is not document or generated != (_SERVER, document.legacy_number, document.binding_key):
            frappe.throw("客户档案新建身份只能由服务器生成")
        _uuid(document.request_id)
        if not isinstance(document.request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", document.request_digest):
            frappe.throw("客户档案新建追溯身份不完整")
    else:
        document.check_permission("write")
        if not before or any(before.get(field) != document.get(field) for field in IDENTITY | SERVER_FIELDS):
            frappe.throw("客户档案公司、种类、客户、编号和新建身份不能修改")
    if document.numbering_mode != AUTO_MODE or document.binding_key != _binding(document.company, document.archive_kind, document.customer):
        frappe.throw("客户档案关联身份不完整，请核对迁移")
    customer = _customer(document.customer, lock=True)
    if document.is_new() and customer.disabled:
        frappe.throw("停用原生客户不能关联新客户档案")
    duplicate = frappe.db.get_value(PROFILE, {"binding_key": document.binding_key}, "name")
    if duplicate and duplicate != document.name:
        frappe.throw("该公司和模块已有关联此原生客户的档案，请打开已有档案")
    required = REQUIRED_READ | ({*IDENTITY} if document.is_new() else set())
    if not required.issubset(_columns(PROFILE, "read")):
        _deny()
    allowed = _editable(document.archive_kind)
    for forbidden in {"discount", "customer_kind"} - allowed:
        if document.get(forbidden) not in (None, ""):
            frappe.throw("订单客户档案不支持客户类型和折扣字段")
    changed = {field for field in allowed if (not before and document.get(field) not in (None, "")) or
               before and before.get(field) != document.get(field)}
    if document.is_new():
        changed |= IDENTITY | {"display_name", "inactive"}
    for field in changed:
        meta = document.meta.get_field(field)
        if field not in _columns(PROFILE, "read") & _columns(PROFILE, "write") or meta.read_only:
            _deny()
    values = _normalize({field: document.get(field) for field in allowed}, allowed)
    if not isinstance(values.get("display_name"), str) or not values["display_name"].strip():
        frappe.throw("请填写客户档案名称")
    for field, source in _dictionaries(document, lock=True).items():
        if source.inactive and (not before or before.get(field) != source.name):
            frappe.throw("停用客户词典不能用于新的选择")
    for field, value in values.items():
        document.set(field, value)


def prevent_delete(document):
    frappe.throw("客户档案删除尚未实现，请保留记录核对")
