"""Classic archive list with separate native audit columns.

The catalog projection remains read only. Editing goes through the existing
archive service and never gains permissions from this list response.
"""

import frappe

from jifeng_erp import inventory_catalog as catalog
from jifeng_erp.archive_versions import same_version


AUDIT_FIELDS = {"owner", "creation", "modified_by", "modified"}


def _audit_rows(rows, kind, company):
    if not rows:
        return {}, set()
    readable = catalog._columns(catalog.PROFILE)
    fields = sorted(({"name", "modified"} | AUDIT_FIELDS) & readable)
    if not {"name", "modified"}.issubset(fields):
        frappe.throw("没有读取档案完整来源字段的权限", frappe.PermissionError)
    audit = frappe.get_list(catalog.PROFILE,
        filters={"name": ["in", [row["name"] for row in rows]], "company": company, "archive_kind": kind},
        fields=fields, order_by="name asc", limit_page_length=catalog.CANDIDATE_LIMIT + 1)
    if len(audit) != len(rows) or len({row["name"] for row in audit}) != len(audit):
        frappe.throw("档案来源或权限已变化，请重新查询", frappe.PermissionError)
    result = {row["name"]: row for row in audit}
    for row in rows:
        if row["name"] not in result or not same_version(result[row["name"]]["modified"], catalog._value(row["modified"])):
            frappe.throw("档案来源已变化，请重新查询", frappe.PermissionError)
    return result, AUDIT_FIELDS & readable


@frappe.whitelist()
def query_archives(kind, company, search="", status="全部", page=1, page_size=catalog.LIMIT):
    page = catalog._page_integer(page, "页码", catalog.CANDIDATE_LIMIT)
    page_size = catalog._page_integer(page_size, "每页条数", catalog.LIMIT)
    rows = catalog._candidate_rows(kind, company, search, status, catalog.CANDIDATE_LIMIT)
    audit, audit_fields = _audit_rows(rows, kind, company)
    packed = []
    for row in rows:
        loaded = catalog._load(row, kind, company, status)
        if loaded:
            profile, item, item_row = loaded
            view, guard = catalog._pack(profile, item, row, item_row, kind, company)
            native_audit = audit[row["name"]]
            values = {field: catalog._value(native_audit[field]) for field in sorted(audit_fields & set(native_audit))}
            # A native get_list projection may omit an otherwise permitted
            # column; never recover it from the full document.
            if any(catalog._value(profile.get(field)) != value for field, value in values.items()):
                frappe.throw("档案操作信息已变化，请重新查询", frappe.PermissionError)
            view["audit"] = values
            packed.append((view, guard))

    audit_sources = [frappe.get_doc(catalog.PROFILE, view["name"]) for view, _ in packed]
    views = catalog._verify_output(packed, kind, company)
    catalog._scope(kind, company)
    readable = catalog._columns(catalog.PROFILE)
    for view, profile in zip(views, audit_sources):
        if (not profile.has_permission("read") or profile.company != company or profile.archive_kind != kind
                or not same_version(profile.modified, view["profile_version"])
                or not set(view["audit"]).issubset(readable)
                or any(catalog._value(profile.get(field)) != value for field, value in view["audit"].items())):
            frappe.throw("档案操作信息或权限已变化，请重新查询", frappe.PermissionError)

    total = len(views)
    total_pages = (total + page_size - 1) // page_size
    # A saved archive may stop matching the filter on the last page.
    page = min(page, max(1, total_pages))
    start = (page - 1) * page_size
    return {"kind": kind, "company": company, "search": search, "status": status,
            "page": page, "page_size": page_size, "total": total, "total_pages": total_pages,
            "rows": views[start:start + page_size], "readonly": True, "can_write": False}
