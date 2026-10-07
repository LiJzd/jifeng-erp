"""Page the complete authorized mold, plate and modeling archive projections."""

import re

import frappe

from jifeng_erp import plate_modeling as M
from jifeng_erp import tool_archive as T


MAX_PAGE_SIZE = 100
DOCTYPES = {**T.DOCTYPES, "modeling": M.DOCTYPE}


def _positive_integer(value, label, maximum):
    if isinstance(value, str):
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
            frappe.throw(f"请输入有效{label}")
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        frappe.throw(f"请输入有效{label}")
    return value


def _records(kind, company, search, include_inactive):
    if kind == "modeling":
        return M._list_records(company, search, include_meta=True)
    return T.list_records(kind, company, search, include_inactive)


@frappe.whitelist(methods=["GET"])
def query_archives(record_type, company, search="", include_inactive=False, page=1, page_size=MAX_PAGE_SIZE):
    """Count only complete visible DTOs and recheck off-page sources too.

    The second full native projection detects a changed scope, source, link,
    image or field permission during this request. It is not an atomic snapshot.
    Existing single-record save/receipt APIs and archive numbering are unchanged.
    """
    T._login()
    if not isinstance(record_type, str) or record_type not in DOCTYPES:
        frappe.throw("请选择模具、版种或版种建模档案")
    T._company(company)
    page = _positive_integer(page, "页码", 2_147_483_647)
    page_size = _positive_integer(page_size, "每页条数", MAX_PAGE_SIZE)
    include_inactive = T._normalize("mold", "scrapped", include_inactive)
    if record_type == "modeling" and include_inactive:
        frappe.throw("版种建模没有报废状态筛选")
    doctype = DOCTYPES[record_type]
    fields_before = {permission: T._columns(doctype, permission) for permission in ("read", "write")}
    rows = _records(record_type, company, search, include_inactive)
    verified = _records(record_type, company, search, include_inactive)
    if rows != verified or any(T._columns(doctype, permission) != fields for permission, fields in fields_before.items()):
        frappe.throw("档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
    T._company(company)
    if not frappe.has_permission(doctype, "read"):
        frappe.throw("没有读取档案权限", frappe.PermissionError)
    total = len(rows)
    total_pages = (total + page_size - 1) // page_size
    effective_page = min(page, max(1, total_pages))
    start = (effective_page - 1) * page_size
    return {"record_type": record_type, "company": company, "search": search,
            "include_inactive": include_inactive, "requested_page": page,
            "page": effective_page, "page_size": page_size, "total": total,
            "total_pages": total_pages, "rows": rows[start:start + page_size]}
