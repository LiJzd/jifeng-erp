"""Read-only inventory archives with exact native Profile and Item sources.

Archive units, reference prices and limits remain their saved values. This API
does not calculate inventory, convert units or grant a write action. The
bounded pagination counts only fully checked, readable Profile/Item sources.
"""

from datetime import date, datetime
from decimal import Decimal
import re

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version


PROFILE = "Jifeng Inventory Profile"
KINDS = ("物料", "材料")
STATUSES = ("在用", "停用", "全部")
PROFILE_FIELDS = {
    "company", "archive_kind", "item_code", "legacy_number", "legacy_code", "major_category",
    "minor_category", "old_number", "specification", "display_name", "location", "material",
    "shape", "color", "display_uom", "list_price", "processing_price", "weight", "supplier",
    "upper_limit", "lower_limit", "conversion_uom", "conversion_value", "owner_name",
    "region", "notes", "inactive",
}
ITEM_FIELDS = {"item_code", "item_name", "item_group", "stock_uom", "disabled", "image"}
PROFILE_REQUIRED = {"name", "modified", "company", "archive_kind", "item_code", "inactive"}
ITEM_REQUIRED = {"name", "modified", "item_code"}
SEARCH_FIELDS = {"legacy_number", "legacy_code", "old_number", "display_name", "specification",
                 "major_category", "minor_category"}
FILE_REQUIRED = {"name", "file_url", "is_private", "attached_to_doctype", "attached_to_name",
                 "attached_to_field"}
LIMIT = 100
CANDIDATE_LIMIT = 1000


def _value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _columns(doctype):
    readable = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
    if frappe.session.user == "Administrator":
        return readable
    meta = frappe.get_meta(doctype)
    # Frappe mask permission is independent of read permlevel. Infer from the
    # current fields/roles rather than reusing its separate masked-fields cache.
    levels = set(meta.get_permlevel_access("mask", parenttype=None))
    masked = {df.fieldname for df in meta.fields if df.get("mask") and (df.permlevel or 0) not in levels}
    return readable - masked


def _fields(doctype, fields, required):
    readable = _columns(doctype)
    if not required.issubset(readable):
        frappe.throw("没有读取库存档案完整来源字段的权限", frappe.PermissionError)
    return sorted((fields | required) & readable)


def _company(company):
    if not isinstance(company, str) or not company or company != company.strip():
        frappe.throw("请选择公司")
    # Stock User may select Company without reading its complete profile.
    frappe.get_doc("Company", company).check_permission("select")


def _scope(kind, company):
    if frappe.session.user == "Guest":
        frappe.throw("请登录后查询库存档案", frappe.PermissionError)
    if not isinstance(kind, str) or kind not in KINDS:
        frappe.throw("请选择物料或材料档案")
    for doctype in (PROFILE, "Item"):
        if not frappe.has_permission(doctype, "read"):
            frappe.throw("没有读取库存档案或来源商品的权限", frappe.PermissionError)
    _company(company)


def _current(document, row, identity):
    """Loaded documents guard permissions/identity, never fill a projection."""
    return (document.has_permission("read")
            and all(document.get(field) == row[field] for field in identity)
            and same_version(document.modified, _value(row["modified"])))


def _load(row, kind, company, status=None):
    if not PROFILE_REQUIRED.issubset(row):
        frappe.throw("没有读取库存档案来源投影的权限", frappe.PermissionError)
    if row["company"] != company or row["archive_kind"] != kind:
        return None
    if status != "全部" and status is not None and bool(row["inactive"]) != (status == "停用"):
        return None
    try:
        profile = frappe.get_doc(PROFILE, row["name"])
    except frappe.DoesNotExistError:
        return None
    if not _current(profile, row, PROFILE_REQUIRED - {"modified"}):
        return None
    item_fields = _fields("Item", ITEM_FIELDS, ITEM_REQUIRED)
    items = frappe.get_list("Item", filters={"name": row["item_code"]}, fields=item_fields,
                            limit_page_length=1)
    if not items:
        return None
    item_row = items[0]
    if not ITEM_REQUIRED.issubset(item_row):
        frappe.throw("没有读取业务商品来源投影的权限", frappe.PermissionError)
    if item_row["name"] != row["item_code"]:
        return None
    try:
        item = frappe.get_doc("Item", item_row["name"])
    except frappe.DoesNotExistError:
        return None
    if not _current(item, item_row, ITEM_REQUIRED - {"modified"}):
        return None
    return profile, item, item_row


def _item_image(item, row, selected=None):
    """Only the exact private Item/image File can authorize a display URL."""
    state = {"source_doctype": "Item", "source_name": item.name, "source_field": "image",
             "label": "关联业务商品图片", "url": "", "state": "未上传"}
    if "image" not in row or "image" not in _columns("Item"):
        state["state"] = "无字段读取权限"
        return state
    value = row["image"]
    if not value:
        return state
    state["state"] = "附件不可用"
    from jifeng_erp.product_archive import _private_url

    if not _private_url(value) or item.get("image") != value or not FILE_REQUIRED.issubset(_columns("File")):
        return state
    binding = {"file_url": value, "is_private": 1, "attached_to_doctype": "Item",
               "attached_to_name": item.name, "attached_to_field": "image"}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=LIMIT + 1)
    if len(names) > LIMIT:
        return state
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        if any(attachment.get(field) != expected for field, expected in binding.items()):
            continue
        if (FILE_REQUIRED.issubset(_columns("File")) and "image" in _columns("Item")
                and attachment.has_permission("read") and attachment.is_downloadable()):
            if selected is not None:
                selected.append(attachment)
            return {**state, "url": attachment.unique_url, "state": "已上传"}
    return state


def _projection(document, row, fields, required):
    # Recheck field permissions after loading. An omitted native list field
    # remains omitted even when the full document contains a value.
    readable = set(_fields(document.doctype, fields, required))
    values = {field: _value(row[field]) for field in sorted(fields & readable & set(row))}
    metadata = {}
    for field in values:
        df = document.meta.get_field(field)
        if df:
            metadata[field] = {"label": df.label, "fieldtype": df.fieldtype, "options": df.options or "",
                               "description": df.description or "", "read_only": True, "reqd": False}
    return values, metadata


def _pack(profile, item, row, item_row, kind, company):
    _scope(kind, company)
    if (not _current(profile, row, PROFILE_REQUIRED - {"modified"})
            or not _current(item, item_row, ITEM_REQUIRED - {"modified"})):
        frappe.throw("库存档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
    selected = []
    image = _item_image(item, item_row, selected)
    _scope(kind, company)
    if (not _current(profile, row, PROFILE_REQUIRED - {"modified"})
            or not _current(item, item_row, ITEM_REQUIRED - {"modified"})):
        frappe.throw("库存档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
    profile_fields = PROFILE_FIELDS - ({"shape", "color"} if kind == "物料" else {"material"})
    profile_values, profile_meta = _projection(profile, row, profile_fields, PROFILE_REQUIRED)
    item_values, item_meta = _projection(item, item_row, ITEM_FIELDS - {"image"}, ITEM_REQUIRED)
    view = {"name": row["name"], "kind": kind, "company": company, "item_code": item_row["name"],
            "profile_version": _value(row["modified"]), "item_version": _value(item_row["modified"]),
            "profile": profile_values, "item": item_values,
            "field_meta": {"profile": profile_meta, "item": item_meta},
            "image": image, "readonly": True, "can_write": False}
    attachment = selected[0] if selected else None
    return view, {"row": row, "item_row": item_row, "file_name": attachment.name if attachment else None,
                  "file_version": _value(attachment.modified) if attachment else None}


def _verify_output(packed, kind, company):
    """Reload all sources before checking any completed projection.

    A later row/File load may invalidate an earlier row's permissions. Nothing
    is returned until every loaded source and already projected field remains
    permitted. This request-local guard does not provide atomic DB isolation.
    """
    loaded = []
    try:
        for view, guard in packed:
            profile = frappe.get_doc(PROFILE, guard["row"]["name"])
            item = frappe.get_doc("Item", guard["item_row"]["name"])
            attachment = frappe.get_doc("File", guard["file_name"]) if guard["file_name"] else None
            loaded.append((view, guard, profile, item, attachment))
    except frappe.DoesNotExistError:
        frappe.throw("库存档案来源或读取权限已变化，请重新查询", frappe.PermissionError)

    _scope(kind, company)
    for view, guard, profile, item, attachment in loaded:
        if (not _current(profile, guard["row"], PROFILE_REQUIRED - {"modified"})
                or not _current(item, guard["item_row"], ITEM_REQUIRED - {"modified"})):
            frappe.throw("库存档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
        for document, values, fields, required in (
                (profile, view["profile"], PROFILE_FIELDS, PROFILE_REQUIRED),
                (item, view["item"], ITEM_FIELDS - {"image"}, ITEM_REQUIRED)):
            readable = set(_fields(document.doctype, fields, required))
            if (not set(values).issubset(readable)
                    or any(_value(document.get(field)) != value for field, value in values.items())):
                frappe.throw("库存档案字段或读取权限已变化，请重新查询", frappe.PermissionError)
        if attachment:
            binding = {"file_url": guard["item_row"].get("image"), "is_private": 1,
                       "attached_to_doctype": "Item", "attached_to_name": item.name,
                       "attached_to_field": "image"}
            if (not FILE_REQUIRED.issubset(_columns("File")) or "image" not in _columns("Item")
                    or item.get("image") != binding["file_url"]
                    or any(attachment.get(field) != value for field, value in binding.items())
                    or not same_version(attachment.modified, guard["file_version"])
                    or not attachment.has_permission("read") or not attachment.is_downloadable()
                    or attachment.unique_url != view["image"]["url"]):
                frappe.throw("库存档案图片或读取权限已变化，请重新查询", frappe.PermissionError)
    return [view for view, _guard in packed]


def _candidate_rows(kind, company, search, status, limit):
    _scope(kind, company)
    if not isinstance(status, str) or status not in STATUSES:
        frappe.throw("请选择在用、停用或全部档案")
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效档案关键词")
    fields = _fields(PROFILE, PROFILE_FIELDS, PROFILE_REQUIRED)
    filters = {"company": company, "archive_kind": kind}
    if status != "全部":
        filters["inactive"] = int(status == "停用")
    search_fields = SEARCH_FIELDS & set(fields)
    if search and not search_fields:
        frappe.throw("没有读取关键词查询字段的权限", frappe.PermissionError)
    options = {"or_filters": [[field, "like", "%" + search + "%"]
                              for field in sorted(search_fields)]} if search else {}
    rows = frappe.get_list(PROFILE, filters=filters, fields=fields, order_by="name asc",
                           limit_page_length=limit + 1, **options)
    if len(rows) > limit:
        frappe.throw("库存档案较多，请输入关键词缩小查询范围")
    if len({row.get("name") for row in rows}) != len(rows):
        frappe.throw("库存档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
    return rows


def _checked_views(rows, kind, company, status):
    packed = []
    for row in rows:
        loaded = _load(row, kind, company, status)
        if loaded:
            profile, item, item_row = loaded
            packed.append(_pack(profile, item, row, item_row, kind, company))
    return _verify_output(packed, kind, company)


@frappe.whitelist()
def list_archives(kind, company, search="", status="在用"):
    """Keep the existing <=100 array contract without inferred stock values."""
    rows = _candidate_rows(kind, company, search, status, LIMIT)
    return _checked_views(rows, kind, company, status)


def _page_integer(value, label, maximum):
    # HTTP form arguments may be decimal text. Do not coerce bools/floats,
    # whitespace, signed values, scientific notation or a truncated number.
    if isinstance(value, str):
        if not re.fullmatch(r"[1-9][0-9]{0,3}", value):
            frappe.throw(f"请输入有效{label}")
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        frappe.throw(f"请输入有效{label}")
    return value


@frappe.whitelist()
def query_archives(kind, company, search="", status="在用", page=1, page_size=LIMIT):
    """Count all checked visible sources, then slice; never count raw rows.

    The full bounded readable candidate set is loaded and guarded even when
    only one page is returned. This is a request-local check, not an atomic
    database snapshot. Sources unreadable at query time never enter totals.
    """
    page = _page_integer(page, "页码", CANDIDATE_LIMIT)
    page_size = _page_integer(page_size, "每页条数", LIMIT)
    rows = _candidate_rows(kind, company, search, status, CANDIDATE_LIMIT)
    views = _checked_views(rows, kind, company, status)
    total = len(views)
    total_pages = (total + page_size - 1) // page_size
    if page > max(1, total_pages):
        frappe.throw("页码超过查询结果范围，请输入合法页码或重新查询")
    start = (page - 1) * page_size
    return {"kind": kind, "company": company, "search": search, "status": status,
            "page": page, "page_size": page_size, "total": total, "total_pages": total_pages,
            "rows": views[start:start + page_size], "readonly": True, "can_write": False}


@frappe.whitelist()
def read_archive(name, kind, company):
    """The company and kind remain explicit when reopening a list record."""
    _scope(kind, company)
    if not isinstance(name, str) or not name or name != name.strip():
        frappe.throw("请选择库存档案")
    fields = _fields(PROFILE, PROFILE_FIELDS, PROFILE_REQUIRED)
    rows = frappe.get_list(PROFILE, filters={"name": name, "company": company, "archive_kind": kind},
                           fields=fields, limit_page_length=1)
    loaded = _load(rows[0], kind, company) if rows else None
    if not loaded:
        frappe.throw("没有读取当前公司档案或来源商品的权限", frappe.PermissionError)
    profile, item, item_row = loaded
    return _verify_output([_pack(profile, item, rows[0], item_row, kind, company)], kind, company)[0]
