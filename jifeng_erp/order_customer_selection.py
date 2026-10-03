"""Read-only customer choices for draft native Sales Orders.

The returned native Customer ID is an explicit choice, not a persisted archive
snapshot. Ordinary Sales Order editing, pricing and saving remain native.
"""

from datetime import datetime
import json
import re

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version


SCOPE = "order_customer"
KIND = "订单客户"
PROFILE = "Jifeng Customer Profile"
CUSTOMER = "Customer"
ORDER = "Sales Order"
PROFILE_FIELDS = {
    "name",
    "modified",
    "company",
    "archive_kind",
    "customer",
    "legacy_number",
    "display_name",
    "inactive",
}
CUSTOMER_FIELDS = {"name", "modified", "disabled"}
ORDER_FIELDS = {"name", "modified", "company", "docstatus", "customer"}
BATCH_SIZE = 200
MAX_SAFE_INT = 9007199254740991


class _UnreadableSource(Exception):
    """Internal classification only; never add a permission message to a response."""


def _deny():
    frappe.throw(
        "没有选择订单客户所需的订单、档案或原生客户权限", frappe.PermissionError
    )


def _name(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 140
        or value != value.strip()
    ):
        frappe.throw("请选择准确的公司、订单、档案和原生客户")
    return value


def _timestamp(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            frappe.throw("客户选择来源版本不完整")
        return value.isoformat()
    if not isinstance(value, str) or not same_version(value, value):
        frappe.throw("客户选择来源版本不完整")
    return datetime.fromisoformat(value).isoformat()


def _expected_version(value):
    if not isinstance(value, str):
        frappe.throw("客户选择来源版本不完整")
    return _timestamp(value)


def _context(value):
    if frappe.session.user == "Guest":
        _deny()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            frappe.throw("订单客户选择上下文无效")
    if not isinstance(value, dict) or set(value) != {"company", "name", "version"}:
        frappe.throw("订单客户选择上下文无效")
    result = dict(value)
    _name(result["company"])
    if result["name"] is None:
        if result["version"] is not None:
            frappe.throw("新订单不得指定已保存版本")
    else:
        _name(result["name"])
        result["version"] = _expected_version(result["version"])
    return result


def _columns(doctype, permission="read"):
    fields = set(
        get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True)
    )
    if permission == "read" and frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(
            meta.get_permlevel_access(permission_type="mask", user=frappe.session.user)
        )
        fields = {
            name
            for name in fields
            if not (
                (field := meta.get_field(name))
                and field.get("mask")
                and (field.permlevel or 0) not in levels
            )
        }
    return fields


def _fields(doctype, required, permission="read"):
    columns = _columns(doctype, permission)
    if not set(required).issubset(columns):
        _deny()
    return columns


def _quiet_access(document, permissions):
    if not all(
        frappe.has_permission(document.doctype, permission, doc=document, throw=False)
        for permission in permissions
    ):
        raise _UnreadableSource


def _projection(document, fields, *, filtered=False):
    """Require the exact permission-filtered native projection, not get_doc alone."""
    _fields(document.doctype, fields)
    if filtered:
        _quiet_access(document, ("read",))
    else:
        document.check_permission("read")
    rows = frappe.get_list(
        document.doctype,
        filters={"name": document.name},
        fields=sorted(fields),
        limit_page_length=1,
    )
    if not rows and filtered:
        raise _UnreadableSource
    if len(rows) != 1 or not set(fields).issubset(rows[0]):
        _deny()
    result = dict(rows[0])
    for field in fields:
        equal = (
            same_version(document.get(field), _timestamp(result[field]))
            if field == "modified"
            else document.get(field) == result[field]
        )
        if not equal:
            frappe.throw("订单客户选择来源已改变，请重新查询")
    if filtered:
        _quiet_access(document, ("read",))
    else:
        document.check_permission("read")
    _fields(document.doctype, fields)
    return result


def _customer_field(document):
    _fields(ORDER, {"customer"})
    _fields(ORDER, {"customer"}, "write")
    field = document.meta.get_field("customer")
    if (
        not field
        or field.fieldtype != "Link"
        or field.options != CUSTOMER
        or field.read_only
        or field.get("read_only_depends_on")
        or (field.get("set_only_once") and not document.is_new())
    ):
        _deny()


def _target(context):
    # Real native checks see the requested company, including User Permissions.
    frappe.get_doc("Company", context["company"]).check_permission("select")
    if context["name"] is None:
        document = frappe.new_doc(ORDER)
        document.company = context["company"]
        document.docstatus = 0
        document.check_permission("create")
        document.check_permission("write")
        _customer_field(document)
        document.check_permission("create")
        document.check_permission("write")
        return {"company": context["company"], "name": None, "version": None}
    document = frappe.get_doc(ORDER, context["name"])
    document.check_permission("read")
    document.check_permission("write")
    _customer_field(document)
    row = _projection(document, ORDER_FIELDS)
    if row["company"] != context["company"]:
        _deny()
    if row["docstatus"] != 0:
        frappe.throw("只能给未提交的销售订单选择客户")
    if not same_version(row["modified"], context["version"]):
        frappe.throw("销售订单已改变，请重新读取后选择客户")
    document.check_permission("write")
    return {
        "company": row["company"],
        "name": row["name"],
        "version": _timestamp(row["modified"]),
        "customer": row["customer"],
        "docstatus": row["docstatus"],
    }


def _source(doctype, name, fields):
    document = frappe.get_doc(doctype, _name(name))
    _quiet_access(document, ("read", "select"))
    row = _projection(document, fields, filtered=True)
    _quiet_access(document, ("select",))
    return document, row


def _boolean(value):
    if type(value) not in (int, bool) or value not in (0, 1):
        frappe.throw("订单客户停用状态无法核对")
    return bool(value)


def _choice(name, company):
    columns = _fields(PROFILE, PROFILE_FIELDS)
    fields = PROFILE_FIELDS | ({"legacy_code"} if "legacy_code" in columns else set())
    profile_document, profile = _source(PROFILE, name, fields)
    if profile["company"] != company or profile["archive_kind"] != KIND:
        _deny()
    customer_document, customer = _source(
        CUSTOMER, profile["customer"], CUSTOMER_FIELDS
    )
    inactive, disabled = _boolean(profile["inactive"]), _boolean(customer["disabled"])
    for field in ("legacy_number", "display_name"):
        if not isinstance(profile[field], str) or not profile[field]:
            frappe.throw("订单客户编号或名称不完整")
    result = {
        "name": profile["name"],
        "company": profile["company"],
        "kind": profile["archive_kind"],
        "profile_version": _timestamp(profile["modified"]),
        "customer": customer["name"],
        "customer_version": _timestamp(customer["modified"]),
        "legacy_number": profile["legacy_number"],
        "display_name": profile["display_name"],
        "can_select": not inactive and not disabled,
    }
    if "legacy_code" in profile:
        if profile["legacy_code"] is not None and not isinstance(
            profile["legacy_code"], str
        ):
            frappe.throw("订单客户代码无法核对")
        result["legacy_code"] = profile["legacy_code"]
    # Linked-source reads must not grant access to a now-masked profile field.
    for document, readable in (
        (profile_document, fields),
        (customer_document, CUSTOMER_FIELDS),
    ):
        _quiet_access(document, ("read", "select"))
        _fields(document.doctype, readable)
    return result


def _page(value, maximum):
    if (
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]*", value)
        and len(value) <= 16
    ):
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        frappe.throw("请输入有效页码或每页条数")
    return value


def _scan(query):
    names, seen = [], set()
    while True:
        batch = frappe.get_list(
            PROFILE,
            **query,
            pluck="name",
            order_by="name asc",
            limit_start=len(names),
            limit_page_length=BATCH_SIZE,
        )
        if len(batch) != len(set(batch)) or seen.intersection(batch):
            frappe.throw("订单客户查询范围已改变，请重新查询")
        for name in batch:
            _name(name)
        names.extend(batch)
        seen.update(batch)
        if len(batch) < BATCH_SIZE:
            return names


def _guard_target(context, previous):
    if previous != _target(context):
        frappe.throw("销售订单上下文已改变，请重新查询客户")


def _query_access():
    _fields(PROFILE, PROFILE_FIELDS)
    _fields(CUSTOMER, CUSTOMER_FIELDS)
    for doctype in (PROFILE, CUSTOMER):
        if not all(
            frappe.has_permission(doctype, permission, throw=False)
            for permission in ("read", "select")
        ):
            _deny()


def _classify(names, company):
    result = {}
    for name in names:
        try:
            result[name] = _choice(name, company)
        except _UnreadableSource:
            # Never retain or return hidden full-document values in this state.
            result[name] = None
    return result


def _selected(name, company):
    try:
        return _choice(name, company)
    except _UnreadableSource:
        _deny()


@frappe.whitelist(methods=["GET"])
def query_customers(context, search="", page=1, page_size=25):
    """Return only readable, selectable and active exact customer associations."""
    context = _context(context)
    target = _target(context)
    page, page_size = _page(page, MAX_SAFE_INT), _page(page_size, 100)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入不超过140字的订单客户关键词")
    columns = _fields(PROFILE, PROFILE_FIELDS)
    _query_access()
    query = {
        "filters": {"company": context["company"], "archive_kind": KIND, "inactive": 0}
    }
    if search:
        searchable = {"legacy_number", "display_name"} | (
            {"legacy_code"} if "legacy_code" in columns else set()
        )
        query["or_filters"] = {
            field: ["like", "%" + search + "%"] for field in sorted(searchable)
        }
    names = _scan(query)
    classifications = _classify(names, context["company"])
    if names != _scan(query):
        frappe.throw("订单客户查询范围已改变，请重新查询")
    _guard_target(context, target)
    if classifications != _classify(names, context["company"]):
        frappe.throw("订单客户或关联客户已改变，请重新查询")
    # A later source may alter the target permissions; do not return stale authority.
    _guard_target(context, target)
    _query_access()
    choices = [
        choice for choice in classifications.values() if choice and choice["can_select"]
    ]
    total = len(choices)
    total_pages = (total + page_size - 1) // page_size
    page = min(page, max(1, total_pages))
    start = (page - 1) * page_size
    return {
        "scope": SCOPE,
        "company": context["company"],
        "search": search,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "rows": choices[start : start + page_size],
    }


@frappe.whitelist(methods=["GET"])
def choose_customer(context, profile, profile_version, customer_version, customer):
    """Reread the two exact versions; never save an order, party or profile."""
    context = _context(context)
    target = _target(context)
    _name(customer)
    profile_version, customer_version = (
        _expected_version(profile_version),
        _expected_version(customer_version),
    )
    choice = _selected(profile, context["company"])
    if (
        choice["customer"] != customer
        or not same_version(choice["profile_version"], profile_version)
        or not same_version(choice["customer_version"], customer_version)
    ):
        frappe.throw("订单客户档案或原生客户已改变，请重新查询并选择")
    if not choice["can_select"]:
        frappe.throw("停用的订单客户档案或原生客户不能选择")
    _guard_target(context, target)
    if choice != _selected(profile, context["company"]):
        frappe.throw("订单客户档案或原生客户已改变，请重新查询并选择")
    _guard_target(context, target)
    return {"scope": SCOPE, **choice}
