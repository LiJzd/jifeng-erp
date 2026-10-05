"""Native company-currency expense accounts; no legacy numbering or renaming.

Creation uses Account.insert and native naming. Existing accounts can only be
enabled/disabled here: native account renaming has separate idle-system and
descendant-company effects and is not implemented by this service.
"""

from datetime import datetime
import json

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version


FIELDS = (
    "name", "modified", "company", "account_name", "account_number", "parent_account",
    "root_type", "account_type", "is_group", "account_currency", "disabled",
)
EDIT_FIELDS = {"disabled"}
CREATE_FIELDS = {"company", "parent_account", "account_name", "account_number", "disabled"}
BATCH_SIZE = 200


def _deny():
    frappe.throw("没有读取或维护费用科目所需的权限", frappe.PermissionError)


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


def _text(value, label, *, optional=False):
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 140:
        frappe.throw("请填写准确的" + label)
    return value


def _integer(value, maximum, label):
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10:
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        frappe.throw(label + "必须是范围内的正整数")
    return value


def _leaf_company(document):
    return (type(document.lft) is int and type(document.rgt) is int
            and document.lft >= 1 and document.rgt == document.lft + 1)


def _company(name, *, writing=False):
    document = frappe.get_doc("Company", _text(name, "公司"), for_update=writing)
    document.check_permission("read")
    document.check_permission("select")
    _fields("Company", {"name", "default_currency"})
    if not document.default_currency:
        frappe.throw("请先配置公司的默认币种")
    if writing:
        _fields("Company", {"lft", "rgt"})
        # Account.validate may create accounts in descendant companies, even
        # on an ordinary save. Hold the company tree row before checking it.
        if not _leaf_company(document):
            frappe.throw("公司树未确定或包含子公司，请从原生科目工具核对集团同步后维护")
    return document


def _parent(name, company, *, active=True):
    document = frappe.get_doc("Account", _text(name, "父费用科目"), for_update=True)
    document.check_permission("read")
    document.check_permission("select")
    _fields("Account", FIELDS)
    if document.company != company.name or document.root_type != "Expense" or not document.is_group:
        frappe.throw("父科目必须是当前公司的费用类科目组")
    if document.account_currency not in (None, "", company.default_currency):
        frappe.throw("父费用科目必须使用公司默认币种")
    if active and document.disabled:
        frappe.throw("请选择启用的父费用科目组")
    return document


def _scope(document, company):
    if (document.company != company.name or document.root_type != "Expense" or document.is_group
            or document.account_currency != company.default_currency):
        frappe.throw("请选择当前公司默认币种的费用类明细科目")


def _pack(document, *, company_writable=True):
    result = {field: document.get(field) for field in FIELDS}
    if isinstance(result["modified"], datetime):
        result["modified"] = result["modified"].isoformat()
    editable = frappe.get_meta("Account").get_field("disabled")
    result["can_write"] = bool(company_writable and document.has_permission("write") and EDIT_FIELDS.issubset(_columns("Account", "write"))
                               and editable and not editable.read_only and not editable.set_only_once)
    result["editable_fields"] = sorted(EDIT_FIELDS) if result["can_write"] else []
    return result


@frappe.whitelist(methods=["GET"])
def query_expense_accounts(company, search="", page=1, page_size=25):
    """Count/page only currently readable accounts; never expose rejected rows."""
    _login()
    if not frappe.has_permission("Account", "read", throw=False):
        _deny()
    _fields("Account", FIELDS)
    company = _company(company)
    company_writable = {"lft", "rgt"}.issubset(_columns("Company")) and _leaf_company(company)
    page, page_size = _integer(page, 1000000000, "页码"), _integer(page_size, 100, "每页条数")
    if not isinstance(search, str) or len(search) > 200:
        frappe.throw("查询关键词必须是二百字以内的文本")
    search = search.strip()
    pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    options = {"or_filters": [["account_name", "like", pattern], ["account_number", "like", pattern]]} if search else {}
    rows, start = [], 0
    while True:
        listed = frappe.get_list("Account", fields=list(FIELDS),
                                 filters={"company": company.name, "root_type": "Expense", "is_group": 0,
                                          "account_currency": company.default_currency},
                                 order_by="name asc", limit_start=start, limit_page_length=BATCH_SIZE, **options)
        for row in listed:
            if not set(FIELDS).issubset(row):
                _deny()
            document = frappe.get_doc("Account", row["name"])
            if not document.has_permission("read"):
                continue
            _scope(document, company)
            if not same_version(document.modified, str(row["modified"])):
                frappe.throw("费用科目查询期间发生变化，请重新查询")
            rows.append(_pack(document, company_writable=company_writable))
        if len(listed) < BATCH_SIZE:
            break
        start += len(listed)
    _fields("Account", FIELDS)
    offset = (page - 1) * page_size
    return {"company": company.name, "currency": company.default_currency, "search": search,
            "page": page, "page_size": page_size, "total": len(rows), "rows": rows[offset:offset + page_size]}


@frappe.whitelist(methods=["POST"])
def save_expense_account(payload):
    """One native insert/save; request failures retain Frappe's normal rollback."""
    _login()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("费用科目请求必须是有效对象")
    if not isinstance(payload, dict):
        frappe.throw("费用科目请求必须是有效对象")
    existing = bool(payload.get("name"))
    allowed = {"name", "company", "modified", "disabled"} if existing else CREATE_FIELDS
    if set(payload) - allowed:
        frappe.throw("现有科目仅可启用或停用；名称、编码及科目身份修改尚未开放" if existing else "请求含不支持的科目字段")
    disabled = payload.get("disabled", 0)
    if type(disabled) not in (int, bool) or disabled not in (0, 1):
        frappe.throw("停用必须明确为零或一")
    company = _company(payload.get("company"), writing=True)
    _fields("Account", FIELDS)
    if existing:
        if "disabled" not in payload:
            frappe.throw("请明确填写启用或停用状态")
        document = frappe.get_doc("Account", _text(payload["name"], "科目"), for_update=True)
        document.check_permission("read")
        document.check_permission("write")
        _scope(document, company)
        if not same_version(document.modified, payload.get("modified")):
            frappe.throw("费用科目已被修改，请重新读取后保存")
        _parent(document.parent_account, company, active=not disabled)
        _fields("Account", EDIT_FIELDS, "write")
        definition = frappe.get_meta("Account").get_field("disabled")
        if not definition or definition.read_only or definition.set_only_once:
            _deny()
        document.disabled = int(disabled)
        document.save()
    else:
        parent = _parent(payload.get("parent_account"), company)
        _fields("Account", CREATE_FIELDS, "create")
        document = frappe.get_doc({"doctype": "Account", "company": company.name,
                                   "parent_account": parent.name, "account_currency": company.default_currency,
                                   "root_type": "Expense", "report_type": "Profit and Loss", "is_group": 0,
                                   "account_name": _text(payload.get("account_name"), "科目名称"),
                                   "account_number": _text(payload.get("account_number"), "科目编码", optional=True),
                                   "disabled": int(disabled)})
        document.check_permission("create")
        document.insert()
    document.check_permission("read")
    _fields("Account", FIELDS)
    _scope(document, company)
    return _pack(document)
