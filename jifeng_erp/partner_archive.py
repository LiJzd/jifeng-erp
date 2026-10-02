"""Explicit outsource factory archives; never infer Supplier classification.

Legacy contact and bank text belongs to this company archive. It neither
updates an existing Supplier nor creates bank accounts or financial vouchers.
New factories can create their normal native Supplier in the same transaction.
"""

from contextvars import ContextVar
from datetime import date, datetime
import hashlib
import json
import re
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


PROFILE = "Jifeng Partner Profile"
KIND = "外发工厂"
KINDS = (KIND, "电镀厂")
NUMBER_PREFIX = {KIND: "WF-", "电镀厂": "PL-"}
EDIT_FIELDS = {"legacy_number", "display_name", "owner_name", "mobile", "reception",
               "phone", "address", "notes", "bank_name", "account_name", "bank_account", "inactive"}
IDENTITY_FIELDS = {"company", "archive_kind", "supplier"}
REQUIRED_READ = IDENTITY_FIELDS | {"legacy_number", "display_name", "inactive"}
PROTECTED_FIELDS = IDENTITY_FIELDS | {"request_id", "request_digest"}
SUPPLIER_CREATE = {"supplier_group", "supplier_type", "naming_series"}
_CREATING = ContextVar("jifeng_partner_creation", default=None)


def _deny():
    frappe.throw("没有完整读取当前公司工厂档案及关联供应商的权限", frappe.PermissionError)


def _login():
    if frappe.session.user == "Guest":
        _deny()


def _scope(kind, company):
    _login()
    if kind not in KINDS:
        frappe.throw("请选择有效工厂档案种类")
    if not isinstance(company, str) or not company.strip() or company != company.strip():
        frappe.throw("请选择有效公司")
    document = frappe.get_doc("Company", company)
    document.check_permission("select")
    return document


def _uuid(value):
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        frappe.throw("新建请求必须包含有效UUID4身份")
    return value


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            frappe.throw("工厂档案请求必须是有效对象")
    if not isinstance(value, dict):
        frappe.throw("工厂档案请求必须是对象")
    return value


def _value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _timestamp(value):
    # Only known native timestamp fields use this conversion. Do not parse
    # bank/contact/legacy text that happens to resemble a date.
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            frappe.throw("工厂档案来源时间不完整")
    if not isinstance(value, datetime) or value.tzinfo is not None:
        frappe.throw("工厂档案来源时间不完整")
    return value.isoformat()


def _freeze(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return tuple((key, _freeze(item)) for key, item in sorted(value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return type(value).__name__, repr(value)


def _state(document):
    # __islocal/__onload are request flags, not stored source columns. Native
    # save and fresh get_doc need not carry the same transient flags.
    values = document.as_dict() if callable(getattr(document, "as_dict", None)) else dict(document)
    return _freeze({name: value for name, value in values.items() if not name.startswith("__")})


def _columns(doctype, permission):
    names = set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))
    if permission == "read" and frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
        names = {name for name in names if not ((field := meta.get_field(name)) and
                 field.get("mask") and (field.permlevel or 0) not in levels)}
    return names


def _supplier(name, lock=False):
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        frappe.throw("请显式选择关联供应商")
    document = frappe.get_doc("Supplier", name, for_update=lock)
    document.check_permission("read")
    document.check_permission("select")
    if not {"name", "disabled"}.issubset(_columns("Supplier", "read")):
        _deny()
    return document


def _load(name, kind, company, lock=False):
    _scope(kind, company)
    if not isinstance(name, str) or not name or name != name.strip():
        frappe.throw("请选择工厂档案")
    document = frappe.get_doc(PROFILE, name, for_update=lock)
    document.check_permission("read")
    if document.company != company or document.archive_kind != kind:
        _deny()
    if not REQUIRED_READ.issubset(_columns(PROFILE, "read")):
        _deny()
    supplier = _supplier(document.supplier, lock)
    return document, supplier


def _projection(document, existing, writable):
    readable = _columns(PROFILE, "read")
    if not REQUIRED_READ.issubset(readable):
        _deny()
    allowed = EDIT_FIELDS | (set() if existing else IDENTITY_FIELDS)
    fields, values = {}, {}
    for name in sorted((EDIT_FIELDS | IDENTITY_FIELDS) & readable):
        field = document.meta.get_field(name)
        if not field:
            frappe.throw("工厂档案字段尚未安装")
        fields[name] = {"fieldtype": field.fieldtype, "options": field.options or "",
                        "reqd": bool(field.reqd), "read_only": bool(field.read_only or
                        not writable or name not in allowed or name not in _columns(PROFILE, "write"))}
        values[name] = _value(document.get(name))
    return fields, values


def _pack(document, supplier=None, *, listing=False):
    if supplier and not {"name", "disabled"}.issubset(_columns("Supplier", "read")):
        _deny()
    existing = not document.is_new()
    permission = "write" if existing else "create"
    writable = bool(document.has_permission(permission)) and not listing
    fields, values = _projection(document, existing, writable)
    if listing:
        for field in fields.values():
            field["read_only"] = True
    readable = _columns(PROFILE, "read")
    result = {"name": document.name if existing else None, "kind": document.archive_kind,
            "company": document.company, "supplier": supplier.name if supplier else None,
            "profile_version": _timestamp(document.modified) if existing else None,
            "supplier_version": _timestamp(supplier.modified) if supplier else None,
            "supplier_disabled": bool(supplier.disabled) if supplier else None,
            "profile": values, "field_meta": fields, "can_write": writable,
            "creation": _timestamp(document.creation) if existing and "creation" in readable else None,
            "modified_by": document.modified_by if existing and "modified_by" in readable else None}
    if document.archive_kind == "电镀厂":
        result["owner"] = document.owner if "owner" in readable else None
    if not existing:
        result.update(_supplier_controls(document))
    return result


def _supplier_controls(profile):
    document = frappe.new_doc("Supplier")
    readable, writable = _columns("Supplier", "read"), _columns("Supplier", "write")
    can_create = bool(profile.has_permission("create") and document.has_permission("create") and
                      {"supplier_name", "name", "disabled"}.issubset(readable) and "supplier_name" in writable)
    can_link = bool(profile.has_permission("create") and frappe.has_permission("Supplier", "read") and
                    {"name", "disabled"}.issubset(readable))
    fields, defaults = {}, {}
    for name in sorted(SUPPLIER_CREATE & readable):
        field = document.meta.get_field(name)
        if not field:
            continue
        defaults[name] = document.get(name)
        if field.fieldtype == "Link" and defaults[name]:
            frappe.get_doc(field.options, defaults[name]).check_permission("select")
        fields[name] = {"fieldtype": field.fieldtype, "options": field.options or "",
                        "reqd": bool(field.reqd), "read_only": bool(not can_create or field.read_only or name not in writable)}
    return {"can_create_supplier": can_create, "can_link_supplier": can_link,
            "supplier_defaults": defaults, "supplier_field_meta": fields}


def _supplier_values(values):
    values = _object(values)
    if not set(values).issubset(SUPPLIER_CREATE):
        frappe.throw("原生供应商新建参数包含不支持的字段")
    meta = frappe.get_meta("Supplier")
    normalized = {}
    for name, value in values.items():
        field = meta.get_field(name)
        if not field or value is not None and (not isinstance(value, str) or len(value) > int(field.length or 140)):
            frappe.throw("请输入有效原生供应商字段")
        if name not in _columns("Supplier", "read") & _columns("Supplier", "write") or field.read_only:
            _deny()
        if field.fieldtype == "Link" and value:
            linked = frappe.get_doc(field.options, value)
            linked.check_permission("select")
            if name == "supplier_group" and linked.get("is_group"):
                frappe.throw("请选择具体供应商分组，不能选择汇总分组")
        if field.fieldtype == "Select" and value not in (None, "") and value not in (field.options or "").split("\n"):
            frappe.throw("请选择有效原生供应商选项")
        normalized[name] = value
    return normalized


def _create_supplier(profile, values):
    supplier = frappe.new_doc("Supplier")
    supplier.check_permission("create")
    if not {"supplier_name", "name", "disabled"}.issubset(_columns("Supplier", "read")) or "supplier_name" not in _columns("Supplier", "write"):
        _deny()
    for name, value in _supplier_values(values).items():
        supplier.set(name, value)
    # Use a fresh native save under the configured Supplier Name/Series rule.
    # A duplicate name fails atomically; it never selects an existing party.
    supplier.supplier_name = profile.display_name
    supplier.disabled = 0
    try:
        supplier.save()
    except frappe.DuplicateEntryError as error:
        if len(error.args) < 2 or error.args[0] != "Supplier" or error.args[1] != supplier.name:
            raise
        # This native name collision is a correctable rejected request. The
        # outer transaction still rolls back; never auto-link an existing party.
        frappe.throw("同名业务供应商已存在，请修改名称，或明确关联已有供应商后保存。")
    return supplier


def _guard(document, supplier, view):
    before = (_state(document), _state(supplier))
    current, linked = _load(document.name, document.archive_kind, document.company)
    if before != (_state(current), _state(linked)):
        frappe.throw("工厂档案或关联供应商已改变，请重新查询")
    readable = _columns(PROFILE, "read")
    if not set(view["profile"]).issubset(readable):
        _deny()
    # Recheck the whole projection including write flags after all native reads.
    if view != _pack(current, linked, listing=not view["can_write"]):
        frappe.throw("工厂档案字段或权限已改变，请重新打开")


def _normalize(values, allowed):
    values = _object(values)
    if not set(values).issubset(allowed):
        frappe.throw("工厂档案包含不支持或只读字段")
    result = {}
    for name, value in values.items():
        if name == "inactive":
            if type(value) not in (bool, int, str) or value not in (False, True, 0, 1, "0", "1"):
                frappe.throw("请选择有效停用状态")
            result[name] = int(value)
        elif value is None or isinstance(value, str):
            field = frappe.get_meta(PROFILE).get_field(name)
            if not field:
                frappe.throw("工厂档案字段尚未安装")
            if isinstance(value, str) and (len(value) > (int(field.length or 140)
                    if field.fieldtype in ("Data", "Link", "Select") else 65535)):
                frappe.throw("工厂档案文本过长：" + field.label)
            result[name] = value
        else:
            frappe.throw("工厂档案字段必须是原文文本")
    return result


def _updates(document, values, allowed):
    values = _normalize(values, allowed)
    readable, writable = _columns(PROFILE, "read"), _columns(PROFILE, "write")
    for name, value in values.items():
        field = document.meta.get_field(name)
        if name not in readable & writable or field.read_only:
            _deny()
        document.set(name, value)


def _latest(document, version):
    if not same_version(document.modified, version):
        frappe.throw("工厂档案或关联供应商已修改，请重新打开后保存", frappe.TimestampMismatchError)


@frappe.whitelist()
def init_archive(kind, company):
    company_document = _scope(kind, company)
    document = frappe.new_doc(PROFILE)
    document.company, document.archive_kind, document.inactive = company, kind, 0
    # Preserve the observed auto-filled editable number habit without claiming
    # the unverified old sequence or consuming native Series on a read request.
    document.legacy_number = NUMBER_PREFIX[kind] + str(uuid4())
    document.check_permission("create")
    view = _pack(document)
    company_document.check_permission("select")
    document.check_permission("create")
    if view != _pack(document):
        frappe.throw("工厂档案字段或权限已改变，请重新新增")
    return view


@frappe.whitelist()
def read_supplier(name, kind, company):
    _scope(kind, company)
    document = frappe.new_doc(PROFILE)
    document.company, document.archive_kind = company, kind
    document.check_permission("create")
    supplier = _supplier(name)
    before = _state(supplier)
    company_document = _scope(kind, company)
    current = _supplier(name)
    if before != _state(current):
        frappe.throw("供应商已修改，请重新选择")
    company_document.check_permission("select")
    document.check_permission("create")
    current.check_permission("read")
    current.check_permission("select")
    if not {"name", "disabled"}.issubset(_columns("Supplier", "read")):
        _deny()
    return {"name": supplier.name, "version": _timestamp(supplier.modified),
            "disabled": bool(supplier.disabled), "company": company, "kind": kind}


@frappe.whitelist()
def read_archive(name, kind, company):
    document, supplier = _load(name, kind, company)
    view = _pack(document, supplier)
    _guard(document, supplier, view)
    return view


def _page(value, maximum):
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}", value):
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        frappe.throw("请输入有效页码或每页条数")
    return value


def _checked_archives(kind, company, search="", status="在用"):
    """Load one complete visible filter set; callers may slice or export it."""
    _scope(kind, company)
    if status not in ("在用", "停用", "全部") or not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效关键词及档案状态")
    if not frappe.has_permission(PROFILE, "read") or not REQUIRED_READ.issubset(_columns(PROFILE, "read")):
        _deny()
    filters = {"company": company, "archive_kind": kind}
    if status != "全部":
        filters["inactive"] = int(status == "停用")
    query = {"filters": filters, "pluck": "name", "order_by": "modified desc, name asc", "limit_page_length": 1001}
    if search:
        query["or_filters"] = {field: ["like", "%" + search + "%"] for field in ("legacy_number", "display_name")}
    names = frappe.get_list(PROFILE, **query)
    if len(names) > 1000:
        frappe.throw("工厂档案超过1000条，请缩小关键词范围")
    checked = []
    for name in names:
        try:
            document, supplier = _load(name, kind, company)
        except frappe.PermissionError:
            # Unreadable Supplier-backed archives never enter the visible total.
            continue
        if status != "全部" and bool(document.inactive) != (status == "停用"):
            frappe.throw("工厂档案状态已改变，请重新查询")
        checked.append((document, supplier, _pack(document, supplier, listing=True)))
    _recheck_archives(kind, company, names, query, checked)
    return checked, names, query


def _recheck_archives(kind, company, names, query, checked):
    _scope(kind, company)
    if names != frappe.get_list(PROFILE, **query):
        frappe.throw("工厂档案范围已改变，请重新查询")
    # Membership/native query hooks run before the complete source recheck.
    for document, supplier, view in checked:
        _guard(document, supplier, view)
    if not frappe.has_permission(PROFILE, "read") or not REQUIRED_READ.issubset(_columns(PROFILE, "read")):
        _deny()
    # A later row/membership load can revoke an earlier projection's field.
    # Check every emitted field after all reads, before total and slicing.
    for document, supplier, view in checked:
        document.check_permission("read")
        supplier.check_permission("read")
        supplier.check_permission("select")
        if not set(view["profile"]).issubset(_columns(PROFILE, "read")):
            _deny()
        if view != _pack(document, supplier, listing=True):
            frappe.throw("工厂档案字段或权限已改变，请重新查询")


@frappe.whitelist()
def query_archives(kind, company, search="", status="在用", page=1, page_size=100):
    page, page_size = _page(page, 1000), _page(page_size, 100)
    checked, _, _ = _checked_archives(kind, company, search, status)
    total = len(checked)
    total_pages = (total + page_size - 1) // page_size
    if page > max(1, total_pages):
        frappe.throw("页码超过查询范围，请重新查询")
    start = (page - 1) * page_size
    return {"kind": kind, "company": company, "search": search, "status": status,
            "page": page, "page_size": page_size, "total": total, "total_pages": total_pages,
            "rows": [value[2] for value in checked[start:start + page_size]], "readonly": True}


@frappe.whitelist()
def resolve_creation(request_id, kind, company):
    _scope(kind, company)
    _uuid(request_id)
    if not frappe.has_permission(PROFILE, "read"):
        _deny()
    name = frappe.db.get_value(PROFILE, {"request_id": request_id}, "name")
    return {"status": "found", "archive": read_archive(name, kind, company)} if name else {"status": "not_found"}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_archive(payload):
    payload = _object(payload)
    if not set(payload).issubset({"kind", "company", "name", "profile_version", "supplier_version", "request_id", "profile", "supplier_create"}):
        frappe.throw("工厂档案请求包含不支持的参数")
    kind, company = payload.get("kind"), payload.get("company")
    _scope(kind, company)
    values = _object(payload.get("profile", {}))
    if payload.get("name"):
        if payload.get("request_id") or "supplier_create" in payload:
            frappe.throw("已有工厂档案不能改写新建请求身份")
        document, supplier = _load(payload["name"], kind, company, lock=True)
        document.check_permission("write")
        _latest(document, payload.get("profile_version"))
        _latest(supplier, payload.get("supplier_version"))
        _updates(document, values, EDIT_FIELDS)
        document.save()
    else:
        if payload.get("profile_version"):
            frappe.throw("新档案不能携带已有档案版本")
        request_id = _uuid(payload.get("request_id"))
        normalized = _normalize(values, EDIT_FIELDS | {"supplier"})
        linked = bool(normalized.get("supplier"))
        if linked and "supplier_create" in payload:
            frappe.throw("关联已有供应商不能同时新建供应商")
        supplier_values = None if linked else _supplier_values(payload.get("supplier_create", {}))
        if not linked and payload.get("supplier_version"):
            frappe.throw("新建供应商不能携带已有供应商版本")
        body = [kind, company, normalized]
        if not linked:
            body.append({"supplier_create": supplier_values})
        digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                 ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        name = frappe.db.get_value(PROFILE, {"request_id": request_id}, "name")
        if name:
            document, supplier = _load(name, kind, company)
            if document.request_digest != digest:
                frappe.throw("该新建请求已用于不同内容，请核对原保存结果")
        else:
            document = frappe.new_doc(PROFILE)
            document.company, document.archive_kind, document.inactive = company, kind, 0
            document.check_permission("create")
            _updates(document, normalized, EDIT_FIELDS | {"supplier"})
            if linked:
                supplier = _supplier(document.supplier, lock=True)
                if supplier.disabled:
                    frappe.throw("停用供应商不能关联新工厂档案")
                _latest(supplier, payload.get("supplier_version"))
            else:
                supplier = _create_supplier(document, supplier_values)
                if "supplier" not in _columns(PROFILE, "read") & _columns(PROFILE, "write"):
                    _deny()
                document.supplier = supplier.name
            document.request_id, document.request_digest = request_id, digest
            context = _CREATING.set(document)
            try:
                document.save()
            finally:
                _CREATING.reset(context)
    # Native save timestamps are strings; fresh database fields are datetimes.
    # Build the response and full source guard from persisted native objects,
    # retaining every stored field instead of ignoring the time columns.
    document, supplier = _load(document.name, kind, company)
    view = _pack(document, supplier)
    _guard(document, supplier, view)
    return view


def validate_profile(document):
    _scope(document.archive_kind, document.company)
    if document.is_new():
        if _CREATING.get() is not document:
            frappe.throw("请从工厂专用入口关联供应商后新建")
        _uuid(document.request_id)
        if not isinstance(document.request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", document.request_digest):
            frappe.throw("工厂档案新建请求身份不完整")
    else:
        before = document.get_doc_before_save()
        if not before or any(before.get(field) != document.get(field) for field in PROTECTED_FIELDS):
            frappe.throw("工厂档案公司、种类、供应商和新建身份不能修改")
    supplier = _supplier(document.supplier)
    if document.is_new() and supplier.disabled:
        frappe.throw("停用供应商不能关联新工厂档案")
    values = _normalize({name: document.get(name) for name in EDIT_FIELDS}, EDIT_FIELDS)
    for name in ("legacy_number", "display_name"):
        if not isinstance(values[name], str) or not values[name].strip():
            frappe.throw("请填写工厂编号和名称")
    for name, value in values.items():
        document.set(name, value)
