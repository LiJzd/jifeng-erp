"""Company-scoped finance labels, separate from native accounting accounts."""

from contextvars import ContextVar
from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version


DOCTYPE = "Jifeng Finance Dictionary"
TYPES = ("收款银行", "付款银行", "费用科目")
EDIT_FIELDS = {"value", "remarks", "inactive"}
IDENTITY = {"company", "dictionary_type", "request_id"}
FIELDS = ("name", "modified", "company", "dictionary_type", "legacy_number", "value", "remarks", "inactive", "account")
READ_FIELDS = {*FIELDS, "request_id"}
ACCOUNT_FIELDS = {"name", "company", "root_type", "account_type", "is_group", "account_currency", "disabled"}
_mapping_document = ContextVar("jifeng_finance_mapping_document", default=None)
_number_marker = object()


def _deny():
    frappe.throw("没有读取或维护财务词典所需的权限", frappe.PermissionError)


def _login():
    if frappe.session.user == "Guest":
        _deny()


def _columns(doctype, permission="read"):
    fields = set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))
    if frappe.session.user == "Administrator":
        return fields
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access("mask", parenttype=None))
    return fields - {df.fieldname for df in meta.fields if df.get("mask") and (df.permlevel or 0) not in levels}


def _fields(doctype, fields, permission="read"):
    if not set(fields).issubset(_columns(doctype, permission)):
        _deny()


def _text(value, label, *, optional=False, data=True):
    if optional and value is None:
        return ""
    if not isinstance(value, str) or (not optional and not value.strip()) or (data and len(value) > 140):
        frappe.throw("请填写有效的" + label)
    return value


def _type(value):
    if value not in TYPES:
        frappe.throw("请选择明确的财务词典类型")
    return value


def _uuid(value):
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if not parsed or parsed.version != 4 or str(parsed) != value:
        frappe.throw("新建请求身份必须是标准 UUID4")
    return value


def _name(request_id):
    return "JF-FIN-" + _uuid(request_id)


def _flag(value, label, *, query=False):
    if query and value in ("0", "1"):
        value = int(value)
    if type(value) not in (int, bool) or value not in (0, 1):
        frappe.throw(label + "必须明确为零或一")
    return int(value)


def _integer(value, maximum, label, *, minimum=0):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10:
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        frappe.throw(label + "必须是范围内的整数")
    return value


def _company(name):
    name = _text(name, "公司")
    if name != name.strip():
        frappe.throw("请选择准确的公司")
    company = frappe.get_doc("Company", name)
    company.check_permission("read")
    company.check_permission("select")
    _fields("Company", {"name"})
    return company


def _account(name, company, dictionary_type, *, active=False, lock=False):
    account = frappe.get_doc("Account", _text(name, "记账科目"), for_update=lock)
    account.check_permission("read")
    account.check_permission("select")
    _fields("Account", ACCOUNT_FIELDS)
    _fields("Company", {"default_currency"})
    if (not company.default_currency or account.company != company.name or account.is_group
            or account.account_currency != company.default_currency):
        frappe.throw("关联科目必须是当前公司本币的明细科目")
    if dictionary_type == "费用科目":
        valid = account.root_type == "Expense"
    else:
        valid = account.root_type == "Asset" and account.account_type == "Bank"
    if not valid or (active and account.disabled):
        frappe.throw("请选择类型正确的记账科目" if not active else "请选择启用且类型正确的记账科目")
    return account


def _editable(fields):
    meta = frappe.get_meta(DOCTYPE)
    return all((df := meta.get_field(field)) and not df.read_only and not df.set_only_once for field in fields)


def _can_create():
    return bool(frappe.has_permission(DOCTYPE, "create", throw=False)
                and (IDENTITY | EDIT_FIELDS).issubset(_columns(DOCTYPE, "create")) and _editable(EDIT_FIELDS))


def _can_map_account(document):
    # Account is intentionally read-only in the ordinary editor. Its independent
    # mapping endpoint checks write access, without treating read_only as a grant.
    return bool(document.has_permission("write") and "account" in _columns(DOCTYPE, "write")
                and frappe.has_permission("Account", "read", throw=False)
                and frappe.has_permission("Account", "select", throw=False)
                and ACCOUNT_FIELDS.issubset(_columns("Account"))
                and "default_currency" in _columns("Company"))


def _scope(document, dictionary_type, company):
    if document.company != company.name or document.dictionary_type != dictionary_type:
        frappe.throw("财务词典不属于当前公司和类型")


def _dto(document):
    document.check_permission("read")
    _fields(DOCTYPE, READ_FIELDS)
    result = {field: document.get(field) for field in FIELDS}
    if isinstance(result["modified"], datetime):
        result["modified"] = result["modified"].isoformat()
    result["can_write"] = bool(document.has_permission("write") and EDIT_FIELDS.issubset(_columns(DOCTYPE, "write"))
                               and _editable(EDIT_FIELDS))
    result["can_create"] = _can_create()
    result["can_map_account"] = _can_map_account(document)
    return result


def _read(name, dictionary_type, company, *, lock=False):
    document = frappe.get_doc(DOCTYPE, _text(name, "词条"), for_update=lock)
    document.check_permission("read")
    _fields(DOCTYPE, READ_FIELDS)
    _scope(document, dictionary_type, company)
    if document.account:
        _account(document.account, company, dictionary_type)
    return document


def _context(dictionary_type, company):
    _login()
    dictionary_type = _type(dictionary_type)
    company = _company(company)
    if not frappe.has_permission(DOCTYPE, "read", throw=False):
        _deny()
    _fields(DOCTYPE, READ_FIELDS)
    return dictionary_type, company


def name_dictionary(document):
    # Native forms can create once; the shared workbench supplies its own UUID
    # so an unknown create outcome can be checked by a subsequent read.
    if document.request_id in (None, ""):
        document.request_id = str(uuid4())
    document.name = _name(document.request_id)


def before_insert_dictionary(document):
    _login()
    company, dictionary_type = _company(document.company), _type(document.dictionary_type)
    document.check_permission("create")
    _fields(DOCTYPE, READ_FIELDS)
    _fields(DOCTYPE, IDENTITY | EDIT_FIELDS, "create")
    if document.legacy_number not in (None, ""):
        frappe.throw("财务词典编号由服务器保存时分配，不能指定")
    if document.request_id in (None, ""):
        document.request_id = str(uuid4())
    _uuid(document.request_id)
    from frappe.model.naming import getseries

    # A short key avoids truncating long Company names. This is a new-system
    # sequence per company/type, with no inferred legacy sequence or start.
    key = "JF-FIN-" + str(TYPES.index(dictionary_type)) + "-" + sha256(company.name.encode()).hexdigest() + "-"
    document.legacy_number = getseries(key, 3 if dictionary_type == "费用科目" else 2)
    document.__dict__["_jf_finance_number"] = (_number_marker, document.legacy_number)


def validate_dictionary(document):
    _login()
    company, dictionary_type = _company(document.company), _type(document.dictionary_type)
    _fields(DOCTYPE, READ_FIELDS)
    if document.name != _name(document.request_id):
        frappe.throw("财务词典新建身份和名称不能修改")
    before = document.get_doc_before_save()
    action = "create" if document.is_new() else "write"
    document.check_permission(action)
    if not document.is_new() and (not before or any(before.get(field) != document.get(field) for field in IDENTITY)):
        frappe.throw("财务词典公司、类型及新建身份不能修改")
    if document.is_new():
        if document.__dict__.get("_jf_finance_number") != (_number_marker, document.legacy_number):
            frappe.throw("财务词典编号只能由服务器生成")
    elif document.legacy_number != before.legacy_number:
        frappe.throw("财务词典编号不能修改")
    changed = EDIT_FIELDS if document.is_new() else {field for field in EDIT_FIELDS if before.get(field) != document.get(field)}
    _fields(DOCTYPE, changed | (IDENTITY if document.is_new() else set()), action)
    if not _editable(changed):
        _deny()
    document.legacy_number = _text(document.legacy_number, "编号", optional=True)
    document.value = _text(document.value, "名称")
    document.remarks = _text(document.remarks, "备注", optional=True, data=False)
    document.inactive = _flag(document.inactive, "停用")
    prior_account = before.account if before else None
    if (document.account or None) != (prior_account or None) and _mapping_document.get() is not document:
        frappe.throw("记账科目关联请从独立设置入口维护")
    if document.account:
        _fields(DOCTYPE, {"account"}, action if _mapping_document.get() is document else "read")
        changing = _mapping_document.get() is document
        _account(document.account, company, dictionary_type, active=changing, lock=changing)


@frappe.whitelist(methods=["GET"])
def init_dictionary(dictionary_type, company):
    dictionary_type, company = _context(dictionary_type, company)
    return {"name": None, "modified": None, "company": company.name, "dictionary_type": dictionary_type,
            "legacy_number": "", "value": "", "remarks": "", "inactive": 0, "account": None,
            "can_write": False, "can_create": _can_create(), "can_map_account": False}


@frappe.whitelist(methods=["GET"])
def read_dictionary(name, dictionary_type, company):
    dictionary_type, company = _context(dictionary_type, company)
    return _dto(_read(name, dictionary_type, company))


@frappe.whitelist(methods=["GET"])
def read_dictionary_account(name, dictionary_type, company):
    """Read the explicit mapping editor's current source and native Link filters."""
    dictionary_type, company = _context(dictionary_type, company)
    entry = _dto(_read(name, dictionary_type, company))
    if not entry["can_map_account"]:
        _deny()
    _fields("Company", {"default_currency"})
    if not company.default_currency:
        frappe.throw("请先设置当前公司的本币")
    filters = {"company": company.name, "account_currency": company.default_currency,
               "is_group": 0, "disabled": 0,
               "root_type": "Expense" if dictionary_type == "费用科目" else "Asset"}
    if dictionary_type != "费用科目":
        filters["account_type"] = "Bank"
    return {"entry": entry, "account_filters": filters}


@frappe.whitelist(methods=["GET"])
def list_dictionary(dictionary_type, company, search="", include_inactive=0, start=0, page_length=25):
    dictionary_type, company = _context(dictionary_type, company)
    include_inactive = _flag(include_inactive, "包括停用", query=True)
    start, page_length = _integer(start, 1000000000, "起始行"), _integer(page_length, 100, "每页条数", minimum=1)
    if start % page_length:
        frappe.throw("起始行必须是完整分页的起点")
    if not isinstance(search, str) or len(search) > 200:
        frappe.throw("查询关键词必须是二百字以内的文本")
    search = search.strip()
    pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    options = {"or_filters": [[field, "like", pattern] for field in ("legacy_number", "value", "remarks")]} if search else {}
    filters = {"company": company.name, "dictionary_type": dictionary_type}
    if not include_inactive:
        filters["inactive"] = 0
    rows, offset = [], 0
    while True:
        listed = frappe.get_list(DOCTYPE, fields=sorted(READ_FIELDS), filters=filters, order_by="name asc",
                                 limit_start=offset, limit_page_length=200, **options)
        for row in listed:
            if not READ_FIELDS.issubset(row):
                _deny()
            try:
                document = _read(row["name"], dictionary_type, company)
                if not same_version(document.modified, str(row["modified"])):
                    frappe.throw("财务词典查询期间发生变化，请重新查询")
                rows.append(_dto(document))
            except frappe.PermissionError:
                continue
        if len(listed) < 200:
            break
        offset += len(listed)
    _fields(DOCTYPE, READ_FIELDS)
    start = min(start, (len(rows) - 1) // page_length * page_length) if rows else 0
    return {"company": company.name, "dictionary_type": dictionary_type, "search": search,
            "include_inactive": include_inactive, "start": start, "page_length": page_length,
            "total": len(rows), "rows": rows[start:start + page_length], "can_create": _can_create()}


@frappe.whitelist(methods=["GET"])
def read_creation(request_id, dictionary_type, company):
    dictionary_type, company = _context(dictionary_type, company)
    name = _name(request_id)
    rows = frappe.get_list(DOCTYPE, fields=sorted(READ_FIELDS),
                           filters={"name": name, "company": company.name, "dictionary_type": dictionary_type}, limit_page_length=1)
    if not rows:
        return None
    try:
        document = _read(name, dictionary_type, company)
        if document.request_id != request_id:
            frappe.throw("新建请求身份不匹配")
        return _dto(document)
    except frappe.PermissionError:
        return None


@frappe.whitelist(methods=["POST"])
def save_dictionary(payload):
    _login()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            frappe.throw("财务词典请求必须是有效对象")
    if not isinstance(payload, dict):
        frappe.throw("财务词典请求必须是有效对象")
    existing = bool(payload.get("name"))
    allowed = {"name", "modified", "company", "dictionary_type", "legacy_number", *EDIT_FIELDS} if existing else {"company", "dictionary_type", "request_id", "legacy_number", *EDIT_FIELDS}
    if set(payload) - allowed or not EDIT_FIELDS.issubset(payload):
        frappe.throw("请完整提供词条四项字段；记账科目关联使用独立设置入口")
    dictionary_type, company = _context(payload.get("dictionary_type"), payload.get("company"))
    if existing:
        document = _read(payload["name"], dictionary_type, company, lock=True)
        document.check_permission("write")
        if not same_version(document.modified, payload.get("modified")):
            frappe.throw("财务词典已被修改，请重新读取后保存")
        if "legacy_number" in payload and payload["legacy_number"] != document.legacy_number:
            frappe.throw("财务词典编号不能修改")
    else:
        if payload.get("legacy_number") not in (None, ""):
            frappe.throw("财务词典编号由服务器保存时分配，不能指定")
        _fields(DOCTYPE, IDENTITY | EDIT_FIELDS, "create")
        document = frappe.get_doc({"doctype": DOCTYPE, "company": company.name, "dictionary_type": dictionary_type,
                                   "request_id": _uuid(payload.get("request_id")), "account": None})
        document.check_permission("create")
    for field in EDIT_FIELDS:
        document.set(field, payload[field])
    document.save() if existing else document.insert()
    return _dto(document)


@frappe.whitelist(methods=["POST"])
def set_dictionary_account(name, dictionary_type, company, modified, account=None):
    dictionary_type, company = _context(dictionary_type, company)
    document = _read(name, dictionary_type, company, lock=True)
    document.check_permission("write")
    _fields(DOCTYPE, {"account"}, "write")
    if not same_version(document.modified, modified):
        frappe.throw("财务词典已被修改，请重新读取后保存")
    if account not in (None, ""):
        _account(account, company, dictionary_type, active=True, lock=True)
    document.account = account or None
    token = _mapping_document.set(document)
    try:
        document.save()
    finally:
        _mapping_document.reset(token)
    return _dto(document)


def require_dictionary_account(name, dictionary_type, company):
    """Future business callers must explicitly require an active mapped label."""
    dictionary_type, company = _context(dictionary_type, company)
    document = _read(name, dictionary_type, company)
    if document.inactive or not document.account:
        frappe.throw("请选择启用且已明确关联记账科目的财务词条")
    return _account(document.account, company, dictionary_type, active=True).name


@frappe.whitelist(methods=["POST"])
def delete_dictionary(name, dictionary_type, company, modified):
    dictionary_type, company = _context(dictionary_type, company)
    document = _read(name, dictionary_type, company, lock=True)
    document.check_permission("delete")
    if not same_version(document.modified, modified):
        frappe.throw("财务词典已被修改，请重新读取后删除")
    # Native delete_doc checks Link references. This endpoint is not published
    # by the shared workbench until deletion behavior has been verified.
    frappe.delete_doc(DOCTYPE, document.name)
    return {"deleted": True}
