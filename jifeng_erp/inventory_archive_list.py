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
    result, projection = {}, None
    for start in range(0, len(rows), catalog.SCAN_BATCH_SIZE):
        # Only fully checked visible archives authorize this audit lookup. A
        # small exact IN batch never substitutes an unbounded SQL page limit.
        names = [row["name"] for row in rows[start:start + catalog.SCAN_BATCH_SIZE]]
        catalog._scope(kind, company)
        if catalog._columns(catalog.PROFILE) != readable:
            frappe.throw("档案字段或权限已变化，请重新查询", frappe.PermissionError)
        audit = frappe.get_list(catalog.PROFILE,
            filters={"name": ["in", names], "company": company, "archive_kind": kind},
            fields=fields, order_by="name asc", limit_page_length=len(names))
        if len(audit) != len(names) or {row.get("name") for row in audit} != set(names):
            frappe.throw("档案来源或权限已变化，请重新查询", frappe.PermissionError)
        for row in audit:
            keys = set(row)
            if (not {"name", "modified"}.issubset(keys) or not keys.issubset(fields)
                    or row["name"] in result or (projection is not None and keys != projection)):
                frappe.throw("档案来源字段投影已变化，请重新查询", frappe.PermissionError)
            projection = keys
            result[row["name"]] = row
        if catalog._columns(catalog.PROFILE) != readable:
            frappe.throw("档案字段或权限已变化，请重新查询", frappe.PermissionError)
    for row in rows:
        if row["name"] not in result or not same_version(result[row["name"]]["modified"], catalog._value(row["modified"])):
            frappe.throw("档案来源已变化，请重新查询", frappe.PermissionError)
    return result, AUDIT_FIELDS & readable


@frappe.whitelist()
def query_archives(kind, company, search="", status="全部", page=1, page_size=catalog.LIMIT):
    """Count all guarded visible sources, including native audit projection.

    Full scans and source rereads are request-local guards, not atomic database
    isolation. Saving a record may shrink the last page, which is clamped here.
    """
    page = catalog._page_integer(page, "页码", catalog.MAX_SAFE_INTEGER)
    page_size = catalog._page_integer(page_size, "每页条数", catalog.LIMIT)
    packed = catalog._query_sources(kind, company, search, status)
    rows = [guard["row"] for _view, guard in packed]
    audit, audit_fields = _audit_rows(rows, kind, company)
    for view, _guard in packed:
        native_audit = audit[view["name"]]
        # A native get_list projection may omit an otherwise permitted
        # column; never recover it from the full document.
        view["audit"] = {field: catalog._value(native_audit[field])
                         for field in sorted(audit_fields & set(native_audit))}

    fresh_audit, fresh_fields = _audit_rows(rows, kind, company)
    if (fresh_fields != audit_fields
            or any({field: catalog._value(value) for field, value in row.items()}
                   != {field: catalog._value(value) for field, value in fresh_audit[name].items()}
                   for name, row in audit.items())):
        frappe.throw("档案操作信息或权限已变化，请重新查询", frappe.PermissionError)

    audit_sources = [frappe.get_doc(catalog.PROFILE, view["name"]) for view, _ in packed]
    views = catalog._verify_output(packed, kind, company)
    catalog._scope(kind, company)
    readable = catalog._columns(catalog.PROFILE)
    if rows and AUDIT_FIELDS & readable != audit_fields:
        frappe.throw("档案操作信息或权限已变化，请重新查询", frappe.PermissionError)
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
