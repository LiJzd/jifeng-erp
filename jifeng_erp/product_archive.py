"""Original product archives with explicit, atomic native Item provenance.

Only creation may create Item. Archive edits preserve shared Item and recipe
tables. Category links are a new explicit association, not recovered legacy
selection keys; selecting them snapshots only the two category names.
"""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


PROFILE = "Jifeng Product Profile"
CATEGORY = "Jifeng Product Category"
DETAIL = "Jifeng Product Category Detail"
IMAGES = {"profile_image", "patent_image"}
CATEGORY_FIELDS = {"category", "category_detail", "major_category", "minor_category"}
PROFILE_EDIT = {"legacy_number", "old_number", "supplier_product_code", "supplier", "specification",
                "brand", "material", "shared_version", "patent_number",
                "display_name", "source", "designer", "pattern_maker", "modeler", "style", "owner_name", "region",
                "display_uom", "list_price", "blank_price", "weight", "silver_weight", "stone_weight", "inactive",
                "legacy_remarks", "notes", *IMAGES, "category", "category_detail"}
PROFILE_READ = PROFILE_EDIT | {"company", "item_code", "legacy_code", "major_category", "minor_category"}
ITEM_CREATE = {"item_name", "item_group", "stock_uom"}
ITEM_READ = ITEM_CREATE | {"item_code"}
RECEIPT = {"request_id", "request_digest", "creation_mode"}
PROTECTED = {"company", "item_code", *RECEIPT}
REQUIRED_PROFILE = {"name", "modified", "company", "item_code", "inactive"}
REQUIRED_ITEM = {"name", "modified", "item_code"}
COLLISION = "新建请求已使用或来源冲突，请只读核对，不自动重复保存"
LIST_PAGE_SIZE = 100
_CREATING = ContextVar("jifeng_product_archive_creation", default=None)
_SERVER = object()


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作产品档案", frappe.PermissionError)


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        frappe.throw("请选择准确的公司、档案或业务商品")


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            frappe.throw("产品档案请求必须是有效对象")
    if not isinstance(value, dict):
        frappe.throw("产品档案请求必须是对象")
    return value


def _uuid(value):
    if not isinstance(value, str):
        frappe.throw("新建请求必须包含有效 UUID4 身份")
    try:
        result = UUID(value)
    except (ValueError, AttributeError):
        frappe.throw("新建请求必须包含有效 UUID4 身份")
    if result.version != 4 or str(result) != value:
        frappe.throw("新建请求必须包含有效 UUID4 身份")
    return value


def _company(value):
    _name(value)
    frappe.get_doc("Company", value).check_permission("select")


def _columns(doctype, permission):
    columns = set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))
    if permission != "read" or frappe.session.user == "Administrator":
        return columns
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access("mask", parenttype=None))
    return columns - {df.fieldname for df in meta.fields if df.get("mask") and (df.permlevel or 0) not in levels}


def _fields(doctype, fields, permission):
    if not set(fields).issubset(_columns(doctype, permission)):
        frappe.throw("没有读取或修改产品档案所需字段的权限", frappe.PermissionError)


def _value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value) if isinstance(value, Decimal) else value


def _normalize(doctype, fieldname, value):
    field = frappe.get_meta(doctype).get_field(fieldname)
    if not field:
        frappe.throw("产品档案字段尚未安装")
    if field.fieldtype == "Check":
        if type(value) not in (bool, int, str) or value not in (False, True, 0, 1, "0", "1"):
            frappe.throw("停用须为是或否")
        return int(value)
    if value in (None, ""):
        return None
    if field.fieldtype in ("Float", "Currency"):
        try:
            if isinstance(value, bool):
                raise ValueError
            number = Decimal(str(value))
            result = float(number)
            if not number.is_finite() or number < 0 or not math.isfinite(result):
                raise ValueError
            return result
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            frappe.throw("产品档案参考值必须是非负有限数值")
    if not isinstance(value, str) or field.fieldtype in ("Data", "Link", "Attach Image") and len(value) > 140:
        frappe.throw("请填写有效且长度允许的产品档案原文")
    return value


def _normalized(doctype, values, allowed):
    values = _object(values)
    if not set(values).issubset(allowed):
        frappe.throw("产品档案请求包含不支持或只读字段")
    return {name: _normalize(doctype, name, value) for name, value in values.items()}


def _digest(mode, profile, item, existing_item):
    body = {"mode": mode, "profile": profile, "item": item, "existing_item": existing_item}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _latest(document, version):
    if not same_version(document.modified, version):
        frappe.throw("产品档案或关联商品已改变，请重新读取后保存", frappe.TimestampMismatchError)


def _documents(name, lock=False):
    _login()
    _name(name)
    profile = frappe.get_doc(PROFILE, name)
    profile.check_permission("read")
    _fields(PROFILE, REQUIRED_PROFILE, "read")
    _company(profile.company)
    source_company = profile.company
    item = frappe.get_doc("Item", profile.item_code, **({"for_update": True} if lock else {}))
    item.check_permission("read")
    _fields("Item", REQUIRED_ITEM, "read")
    if lock:
        # Item is always the first write lock, including competing bindings.
        profile = frappe.get_doc(PROFILE, name, for_update=True)
        profile.check_permission("read")
        _fields(PROFILE, REQUIRED_PROFILE, "read")
        _company(profile.company)
        if profile.item_code != item.name or profile.company != source_company:
            frappe.throw("产品档案业务来源已变化，请重新读取")
    return profile, item


def _unbound_item(name, lock=False):
    _name(name)
    item = frappe.get_doc("Item", name, **({"for_update": True} if lock else {}))
    item.check_permission("read")
    _fields("Item", REQUIRED_ITEM, "read")
    # Existence only, never expose an unreadable Profile or its company/name.
    if frappe.db.get_value(PROFILE, {"item_code": item.name}, "name", for_update=lock):
        frappe.throw("该业务商品已有产品档案，请使用已有档案入口")
    return item


def _image_state(profile, fieldname, value=None):
    """Authorize only the loaded exact binding; never use another URL alias."""
    value = profile.get(fieldname) if value is None else value
    empty = {"url": "", "state": "未上传" if not value else "附件不可用"}
    if not value or profile.is_new() or not isinstance(value, str):
        return empty
    if not _private_url(value):
        return empty
    binding = {"file_url": value, "is_private": 1, "attached_to_doctype": PROFILE,
               "attached_to_name": profile.name, "attached_to_field": fieldname}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=0)
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        if any(attachment.get(field) != expected for field, expected in binding.items()):
            continue
        if attachment.has_permission("read") and attachment.is_downloadable():
            return {"url": attachment.unique_url, "state": "已上传"}
    return empty


def _private_url(value):
    return _local_image_url(value, "/private/files/")


def _local_image_url(value, prefix):
    if not isinstance(value, str) or not value.startswith(prefix) or value != value.strip():
        return False
    if any(ord(char) < 32 for char in value) or "\\" in value:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    filename = unquote(parts.path.removeprefix(prefix))
    return bool(filename and filename not in (".", "..") and not any(char in filename for char in ("/", "\\", "%"))
                and not any(ord(char) < 32 for char in filename)
                and not (parts.scheme or parts.netloc or parts.query or parts.fragment))


def _business_image_state(item):
    """Read the current Item attachment without replacing private archive images.

    Native image ingestion attaches File to Item without a field. Permit that
    document binding, or the native image field, only when Item.image still
    points at the exact local File URL. Other fields and remote URLs never gain
    a download URL through this DTO.
    """
    value = item.get("image")
    empty = {"url": "", "state": "未上传" if not value else "附件不可用"}
    if not value or item.is_new():
        return empty
    private = _local_image_url(value, "/private/files/")
    if not private and not _local_image_url(value, "/files/"):
        return empty
    binding = {"file_url": value, "is_private": int(private), "attached_to_doctype": "Item",
               "attached_to_name": item.name}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name", limit_page_length=0)
    for name in names:
        try:
            attachment = frappe.get_doc("File", name)
        except frappe.DoesNotExistError:
            continue
        if any(attachment.get(field) != expected for field, expected in binding.items()):
            continue
        if attachment.get("attached_to_field") not in (None, "", "image"):
            continue
        if attachment.has_permission("read") and attachment.is_downloadable():
            return {"url": attachment.unique_url, "state": "已上传"}
    return empty


@frappe.whitelist()
def list_image_attachments(name, fieldname):
    """Read-only upload reconciliation; caller chooses an exact new File ID."""
    _login()
    if not isinstance(fieldname, str) or fieldname not in IMAGES:
        frappe.throw("请选择产品图片或专利图片字段")
    profile, _ = _documents(name)
    profile.check_permission("write")
    _fields(PROFILE, {fieldname}, "read")
    _fields(PROFILE, {fieldname}, "write")
    if profile.meta.get_field(fieldname).read_only:
        frappe.throw("没有操作该图片字段权限", frappe.PermissionError)
    _fields("File", {"name", "file_name", "file_url", "creation"}, "read")
    binding = {"is_private": 1, "attached_to_doctype": PROFILE, "attached_to_name": profile.name,
               "attached_to_field": fieldname}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="creation desc, name asc", limit_page_length=101)
    if len(names) > 100:
        frappe.throw("当前字段的私有图片附件较多，请从原生附件管理核对，不自动分页或复传")
    result = []
    for file_id in names:
        try:
            attachment = frappe.get_doc("File", file_id)
        except frappe.DoesNotExistError:
            continue
        if any(attachment.get(field) != value for field, value in binding.items()) or not _private_url(attachment.file_url):
            continue
        if attachment.has_permission("read") and attachment.is_downloadable():
            result.append({"name": attachment.name, "file_name": attachment.file_name,
                           "file_url": attachment.file_url, "unique_url": attachment.unique_url,
                           "creation": _value(attachment.creation)})
    return result


def validate_image(profile, fieldname, value):
    if profile.is_new():
        frappe.throw("请先保存产品档案，再上传绑定其字段的私有图片")
    if _image_state(profile, fieldname, value)["state"] != "已上传":
        frappe.throw("图片必须是可读取和下载、精确绑定当前档案字段的私有附件", frappe.PermissionError)


def validate_images(profile):
    before = profile.get_doc_before_save() if not profile.is_new() else None
    for field in IMAGES:
        value = profile.get(field)
        if not value:
            continue
        # Legacy patent external URLs remain stored when untouched, but never
        # gain a DTO download URL. New/replacement patent images are strict.
        if field == "patent_image" and before and before.get(field) == value:
            continue
        validate_image(profile, field, value)


def _category_selection(profile, category, detail):
    _fields(PROFILE, CATEGORY_FIELDS, "read")
    _fields(PROFILE, CATEGORY_FIELDS, "write")
    for field in CATEGORY_FIELDS:
        if profile.meta.get_field(field).read_only:
            frappe.throw("没有修改产品类别关联及原文的权限", frappe.PermissionError)
    if not category:
        if detail:
            frappe.throw("请选择明细所属的大类")
        return None, None
    _name(category)
    parent = frappe.get_doc(CATEGORY, category, for_update=True)
    parent.check_permission("read")
    _fields(CATEGORY, {"name", "company", "major_name", "inactive"}, "read")
    parent.check_permission("select")
    if parent.company != profile.company or parent.inactive:
        frappe.throw("新选择的大类须属于档案公司且未停用")
    minor = None
    if detail:
        _name(detail)
        child = frappe.get_doc(DETAIL, detail, for_update=True)
        child.check_permission("read")
        _fields(DETAIL, {"name", "company", "category", "minor_name", "inactive"}, "read")
        child.check_permission("select")
        if child.company != profile.company or child.category != parent.name or child.inactive:
            frappe.throw("新选择的小类须属于当前大类和公司且未停用")
        minor = child.minor_name
    return parent.major_name, minor


def _category_updates(profile, values):
    if not ({"category", "category_detail"} & set(values)):
        return
    category = values.get("category", profile.get("category"))
    detail = values.get("category_detail", profile.get("category_detail"))
    if category != profile.get("category") and "category_detail" not in values:
        detail = None
    if category == profile.get("category") and detail == profile.get("category_detail"):
        return
    major, minor = _category_selection(profile, category, detail)
    for field, value in (("category", category), ("category_detail", detail), ("major_category", major), ("minor_category", minor)):
        profile.set(field, value)


def validate_record(profile):
    """Native Form guard; historical unlinked classification stays writable."""
    _login()
    if profile.is_new():
        if profile.__dict__.get("_jf_product_archive_server") is not _SERVER:
            frappe.throw("产品档案新建追溯身份只能由服务器生成")
        _uuid(profile.request_id)
    else:
        before = profile.get_doc_before_save()
        if not before or any(before.get(field) != profile.get(field) for field in PROTECTED):
            frappe.throw("产品档案公司、业务来源及新建追溯身份不能修改")
    before = profile.get_doc_before_save() if not profile.is_new() else None
    changed = not before and bool(profile.get("category") or profile.get("category_detail")) or before and any(
        before.get(field) != profile.get(field) for field in ("category", "category_detail"))
    if changed:
        major, minor = _category_selection(profile, profile.get("category"), profile.get("category_detail"))
        profile.major_category, profile.minor_category = major, minor
    elif before and (profile.get("category") or profile.get("category_detail")) and any(
            before.get(field) != profile.get(field) for field in ("major_category", "minor_category")):
        frappe.throw("已关联类别原文仅能通过明确改选或清选类别修改")


def before_insert_profile(profile):
    _login()
    _company(profile.company)
    profile.check_permission("read")
    profile.check_permission("create")
    _fields(PROFILE, REQUIRED_PROFILE, "read")
    _fields(PROFILE, {"company", "item_code"}, "write")
    context = _CREATING.get()
    if context and context["profile"] is profile:
        if any(profile.get(field) != context[field] for field in RECEIPT):
            frappe.throw("产品档案新建请求校验不完整")
    else:
        if any(profile.get(field) not in (None, "") for field in RECEIPT):
            frappe.throw("产品档案新建追溯身份只能由服务器生成")
        values = {field: _normalize(PROFILE, field, profile.get(field)) for field in PROFILE_EDIT}
        profile.request_id, profile.creation_mode = str(uuid4()), "native_existing_item"
        profile.request_digest = _digest(profile.creation_mode, {"company": profile.company, **values}, {}, profile.item_code)
    profile.__dict__["_jf_product_archive_server"] = _SERVER


def _meta_values(document, fields, editable, can_write):
    readable, writable = _columns(document.doctype, "read"), _columns(document.doctype, "write")
    meta, values = {}, {}
    for field in sorted(fields & readable):
        df = document.meta.get_field(field)
        if df:
            values[field] = _value(document.get(field))
            meta[field] = {"fieldtype": df.fieldtype, "options": df.options or "", "reqd": bool(df.reqd),
                           "read_only": bool(df.read_only or field not in editable or field not in writable or not can_write),
                           "description": (df.description or "") + (
                               " 原生数值字段空输入会保存为零；档案参考值不参与库存换算。"
                               if df.fieldtype in ("Currency", "Float") else "")}
    return meta, values


def _pack(profile, item, unscoped=False):
    existing, linked = not profile.is_new(), not item.is_new()
    permission = (lambda doc, p: frappe.has_permission(doc.doctype, p)) if unscoped else (lambda doc, p: doc.has_permission(p))
    can_profile = permission(profile, "write" if existing else "create")
    can_create = not existing and can_profile and permission(item, "create") and permission(item, "read")
    can_link = not existing and can_profile and permission(item, "read")
    editable = PROFILE_EDIT | ({"company"} if not existing else set())
    pmeta, pvalues = _meta_values(profile, PROFILE_READ, editable, can_profile)
    imeta, ivalues = _meta_values(item, ITEM_READ, ITEM_CREATE if not linked else set(), can_create)
    image_states = {}
    for field in IMAGES & set(pvalues):
        state = _image_state(profile, field)
        image_states[field] = state
        # Return neither an unavailable private URL nor a fid display URL as
        # a writable stored value. Authorized upload values remain canonical.
        if state["state"] == "已上传" or not profile.get(field):
            pvalues[field] = profile.get(field)
        else:
            pvalues.pop(field)
        if not existing:
            pmeta[field]["read_only"] = True
    for field in ("legacy_code", "major_category", "minor_category"):
        if field in pmeta:
            pmeta[field]["read_only"] = True
    result = {"name": profile.name if existing else None, "item_code": item.name if linked else None,
            "existing_item": item.name if linked and not existing else None,
            "profile_version": _value(profile.modified) if existing else None,
            "item_version": _value(item.modified) if linked else None,
            "creation_mode": profile.get("creation_mode") if existing and "creation_mode" in _columns(PROFILE, "read") else None,
            "profile": pvalues, "item": ivalues, "field_meta": {"profile": pmeta, "item": imeta},
            "image_states": image_states, "can_create_item": bool(can_create), "can_link_item": bool(can_link),
            "can_write": bool(can_profile and permission(item, "read") if existing or linked else can_create or can_link)}
    # An absent key means the image field is unreadable, not an empty image.
    # The stored Item.image value is never an editable archive field.
    if "image" in _columns("Item", "read"):
        result["business_item_image"] = _business_image_state(item)
    return result


@frappe.whitelist()
def init_archive(company=None, existing_item=None):
    _login()
    if not frappe.has_permission(PROFILE, "read") or not frappe.has_permission(PROFILE, "create"):
        frappe.throw("没有新建产品档案权限", frappe.PermissionError)
    _fields(PROFILE, REQUIRED_PROFILE, "read")
    profile, item = frappe.new_doc(PROFILE), frappe.new_doc("Item")
    profile.company, profile.item_code = None, None
    for field in PROFILE_READ - {"company", "item_code"} | RECEIPT:
        profile.set(field, 0 if field == "inactive" else None)
    for field in ITEM_READ:
        item.set(field, None)
    if company not in (None, ""):
        _company(company)
        profile.company = company
        profile.check_permission("create")
    if existing_item:
        if not company:
            frappe.throw("请先明确公司再关联已有业务商品")
        item = _unbound_item(existing_item)
        profile.item_code = item.name
        profile.check_permission("create")
    elif not frappe.has_permission("Item", "read") and not frappe.has_permission("Item", "create"):
        frappe.throw("没有读取或创建业务商品权限", frappe.PermissionError)
    return _pack(profile, item, unscoped=not company)


@frappe.whitelist()
def read_archive(name):
    return _pack(*_documents(name))


@frappe.whitelist()
def list_category_choices(company, search=""):
    """Flat readable child choices; legacy generated IDs/abbreviations unknown."""
    _login()
    _company(company)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效类别查询关键词")
    parent_fields = {"name", "company", "inactive", "legacy_code", "major_name", "owner_name", "region"}
    child_fields = {"name", "company", "inactive", "category", "minor_name", "material"}
    for doctype, fields in ((CATEGORY, parent_fields), (DETAIL, child_fields)):
        if not frappe.has_permission(doctype, "read") or not frappe.has_permission(doctype, "select"):
            frappe.throw("没有读取或选择产品类别权限", frappe.PermissionError)
        _fields(doctype, fields, "read")
    scope = {"company": company, "inactive": 0}
    # Do not cap/filter parents by the keyword first: a matching child can
    # belong to any readable parent. Native permission-filtered queries remain
    # the source of every returned scalar; loaded docs only recheck provenance.
    listed = frappe.get_list(CATEGORY, filters=scope, fields=sorted(parent_fields),
                             order_by="name asc", limit_page_length=0)
    if any(not parent_fields.issubset(row) for row in listed):
        frappe.throw("没有读取类别选择所需字段权限", frappe.PermissionError)
    parents = {row.name: row for row in listed if row.company == company and not row.inactive}
    if not parents:
        return []
    options = {}
    if search:
        matches = frappe.get_list(CATEGORY, filters={**scope, "name": ["in", list(parents)]}, fields=["name"],
            or_filters=[[field, "like", "%" + search + "%"] for field in ("legacy_code", "major_name", "owner_name", "region")],
            limit_page_length=0)
        match_names = [row.name for row in matches if row.name in parents]
        clauses = [[field, "like", "%" + search + "%"] for field in ("minor_name", "material")]
        if match_names:
            clauses.append(["category", "in", match_names])
        options["or_filters"] = clauses
    children = frappe.get_list(DETAIL, filters={**scope, "category": ["in", list(parents)]}, fields=sorted(child_fields),
        order_by="minor_name asc, name asc", limit_page_length=101, **options)
    if len(children) > 100:
        frappe.throw("类别选择记录较多，请输入更具体关键词后查询")
    result, checked = [], {}
    for row in children:
        if not child_fields.issubset(row):
            frappe.throw("没有读取类别选择所需字段权限", frappe.PermissionError)
        original = parents.get(row.category)
        if not original or row.company != company or row.inactive:
            continue
        if row.category not in checked:
            try:
                parent = frappe.get_doc(CATEGORY, row.category)
            except frappe.DoesNotExistError:
                checked[row.category] = None
            else:
                checked[row.category] = parent if all(parent.get(field) == original[field] for field in parent_fields) and (
                    parent.has_permission("read") and parent.has_permission("select")) else None
        parent = checked[row.category]
        if parent is None:
            continue
        try:
            child = frappe.get_doc(DETAIL, row.name)
        except frappe.DoesNotExistError:
            continue
        if any(child.get(field) != row[field] for field in child_fields) or not (
                child.has_permission("read") and child.has_permission("select")):
            continue
        result.append({"category": original.name, "category_detail": row.name, "category_code": original.legacy_code,
            "major_name": original.major_name, "minor_name": row.minor_name, "material": row.material,
            "owner_name": original.owner_name, "region": original.region, "abbreviation": ""})
    return result


def _search_options(search, readable, item_readable=None):
    """Use real readable identities for both the archive list and its export."""
    text = {"item_code", "supplier_product_code", "specification", "legacy_number", "legacy_code", "display_name",
            "old_number", "major_category", "minor_category"} & readable
    if search and not text:
        frappe.throw("没有读取关键词查询字段的权限", frappe.PermissionError)
    if not search:
        return {}
    options = {"or_filters": [[field, "like", "%" + search + "%"] for field in sorted(text)]}
    item_readable = _columns("Item", "read") if item_readable is None else item_readable
    if "item_code" in readable and frappe.has_permission("Item", "read") and "item_name" in item_readable:
        # Item is the current business-name source. The final Profile query
        # still applies company, activity and native document permissions.
        matches = frappe.get_list("Item", filters={}, fields=["name"],
                                  or_filters=[["item_name", "like", "%" + search + "%"]],
                                  limit_page_length=0)
        if matches:
            options["or_filters"].append(["item_code", "in", [row.name for row in matches]])
    return options


def _list_archives(company, search, include_inactive):
    _login()
    _company(company)
    if not frappe.has_permission(PROFILE, "read"):
        frappe.throw("没有读取产品档案权限", frappe.PermissionError)
    _fields(PROFILE, REQUIRED_PROFILE, "read")
    include_inactive = _normalize(PROFILE, "inactive", include_inactive)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效查询关键词")
    readable = _columns(PROFILE, "read")
    filters = {"company": company, **({} if include_inactive else {"inactive": 0})}
    options = _search_options(search, readable)
    rows = frappe.get_list(PROFILE, filters=filters, fields=sorted((PROFILE_READ | REQUIRED_PROFILE) & readable),
                           order_by="name asc", limit_page_length=0, **options)
    if len({row.get("name") for row in rows}) != len(rows):
        frappe.throw("产品档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
    result = []
    for row in rows:
        if not REQUIRED_PROFILE.issubset(row):
            frappe.throw("没有读取产品档案来源字段权限", frappe.PermissionError)
        profile, item = _documents(row.name)
        if profile.company != company or row.company != company or not include_inactive and profile.inactive:
            continue
        for field in set(row) & (PROFILE_READ | REQUIRED_PROFILE):
            current, original = profile.get(field), row[field]
            df = profile.meta.get_field(field)
            if field == "modified":
                unchanged = same_version(current, _value(original))
            elif df and df.fieldtype in ("Float", "Currency") and current is not None and original is not None:
                try:
                    unchanged = Decimal(str(current)) == Decimal(str(original))
                except (InvalidOperation, ValueError, TypeError):
                    unchanged = False
            else:
                unchanged = _value(current) == _value(original)
            if not unchanged:
                frappe.throw("产品档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
        # Native list field projection remains the source of scalar values.
        packed = _pack(profile, item)
        packed["profile"] = {field: packed["profile"][field] if field in IMAGES else _value(row[field])
                             for field in PROFILE_READ & set(row) & set(packed["profile"])}
        packed["profile_version"] = _value(row.modified)
        result.append(packed)
    return result


@frappe.whitelist()
def list_archives(company, search="", include_inactive=False):
    """Retain the array contract for callers without the former 100-row cap."""
    return _list_archives(company, search, include_inactive)


def _page_integer(value, label, maximum):
    if isinstance(value, str):
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
            frappe.throw(f"请输入有效{label}")
        value = int(value)
    if type(value) is not int or not 1 <= value <= maximum:
        frappe.throw(f"请输入有效{label}")
    return value


@frappe.whitelist()
def query_archives(company, search="", include_inactive=False, page=1, page_size=LIST_PAGE_SIZE):
    """Page complete authorized DTOs, with a request-local source recheck.

    Native list projections remain authoritative. Count only fully checked
    Profile/Item sources, then re-read the whole set, including off-page rows,
    before returning a count or page. This is not an atomic database snapshot.
    """
    _login()
    _company(company)
    page = _page_integer(page, "页码", 2_147_483_647)
    page_size = _page_integer(page_size, "每页条数", LIST_PAGE_SIZE)
    include_inactive = _normalize(PROFILE, "inactive", include_inactive)
    fields_before = {(doctype, permission): _columns(doctype, permission)
                     for doctype in (PROFILE, "Item") for permission in ("read", "write")}
    views = _list_archives(company, search, include_inactive)
    verified = _list_archives(company, search, include_inactive)
    if views != verified or any(_columns(*key) != fields for key, fields in fields_before.items()):
        frappe.throw("产品档案来源或读取权限已变化，请重新查询", frappe.PermissionError)
    _company(company)
    if not frappe.has_permission(PROFILE, "read"):
        frappe.throw("没有读取产品档案权限", frappe.PermissionError)
    total = len(views)
    total_pages = (total + page_size - 1) // page_size
    effective_page = min(page, max(1, total_pages))
    start = (effective_page - 1) * page_size
    return {"company": company, "search": search, "include_inactive": include_inactive,
            "requested_page": page, "page": effective_page, "page_size": page_size,
            "total": total, "total_pages": total_pages, "rows": views[start:start + page_size]}


def _receipt(request_id, company):
    if not frappe.has_permission(PROFILE, "read"):
        frappe.throw("没有核对产品档案权限", frappe.PermissionError)
    _fields(PROFILE, REQUIRED_PROFILE | RECEIPT, "read")
    rows = frappe.get_list(PROFILE, filters={"request_id": request_id, "company": company}, fields=["name"], limit_page_length=1)
    if not rows:
        return None
    profile, item = _documents(rows[0].name)
    if profile.request_id != request_id or profile.company != company:
        frappe.throw("产品新建来源已改变，请重新核对")
    return profile, item


@frappe.whitelist()
def resolve_creation(request_id, company):
    _login()
    _uuid(request_id)
    _company(company)
    documents = _receipt(request_id, company)
    return {"status": "found", "archive": _pack(*documents)} if documents else {"status": "not_found"}


def _updates(document, values):
    _fields(document.doctype, values, "read")
    _fields(document.doctype, values, "write")
    if document.doctype == PROFILE:
        _category_updates(document, values)
    for field, value in values.items():
        if document.meta.get_field(field).read_only:
            frappe.throw("没有修改产品档案字段权限", frappe.PermissionError)
        if field in ("category", "category_detail"):
            continue
        if field in IMAGES and value and value != document.get(field):
            validate_image(document, field, value)
        if document.doctype == PROFILE and field == "supplier" and value:
            frappe.get_doc("Supplier", value).check_permission("select")
        if document.doctype == "Item" and field in ("item_group", "stock_uom") and value:
            linked = frappe.get_doc("Item Group" if field == "item_group" else "UOM", value)
            linked.check_permission("select")
            if field == "item_group" and linked.is_group:
                frappe.throw("请选择业务商品明细分组")
        document.set(field, value)


@frappe.whitelist(methods=["POST"])
@_atomic
def save_archive(payload):
    _login()
    payload = _object(payload)
    if not set(payload).issubset({"name", "profile_version", "item_version", "request_id", "profile", "item", "existing_item"}):
        frappe.throw("产品档案请求包含不支持的参数")
    profile_values = _normalized(PROFILE, payload.get("profile", {}), PROFILE_EDIT | {"company"})
    item_values = _normalized("Item", payload.get("item", {}), ITEM_CREATE)
    if payload.get("name"):
        if item_values or "request_id" in payload or "existing_item" in payload:
            frappe.throw("已有产品档案不能改写共享商品或新建来源")
        profile, item = _documents(payload["name"], lock=True)
        profile.check_permission("write")
        _latest(profile, payload.get("profile_version"))
        _latest(item, payload.get("item_version"))
        if "company" in profile_values:
            if profile_values.pop("company") != profile.company:
                frappe.throw("已保存产品档案不能改绑公司")
        _updates(profile, profile_values)
        profile.save()
        return _pack(*_documents(profile.name))

    request_id = _uuid(payload.get("request_id"))
    company = profile_values.get("company")
    _company(company)
    existing_item = payload.get("existing_item")
    mode = "existing_item" if existing_item not in (None, "") else "new_item"
    if payload.get("profile_version") or mode == "new_item" and payload.get("item_version"):
        frappe.throw("新建产品档案不能携带旧版本")
    if mode == "existing_item":
        _name(existing_item)
        if item_values:
            frappe.throw("关联已有商品不能改写共享商品字段")
    digest = _digest(mode, profile_values, item_values, existing_item if mode == "existing_item" else None)
    previous = _receipt(request_id, company)
    if previous:
        profile, item = previous
        if profile.request_digest != digest or profile.creation_mode != mode:
            frappe.throw(COLLISION)
        return _pack(profile, item)
    if not frappe.has_permission(PROFILE, "create"):
        frappe.throw("没有创建产品档案权限", frappe.PermissionError)
    profile = frappe.new_doc(PROFILE)
    profile.company, profile.item_code = company, None
    for field in ("category", "category_detail", "major_category", "minor_category", "legacy_code", *IMAGES):
        profile.set(field, None)
    _fields(PROFILE, {"company", "item_code"}, "read")
    _fields(PROFILE, {"company", "item_code"}, "write")
    populated = {field for field in PROFILE_EDIT if profile.get(field) not in (None, "")}
    _fields(PROFILE, populated, "read")
    _fields(PROFILE, populated, "write")
    if mode == "existing_item":
        item = _unbound_item(existing_item, lock=True)
        _latest(item, payload.get("item_version"))
    else:
        if not frappe.has_permission("Item", "create"):
            frappe.throw("没有创建业务商品权限", frappe.PermissionError)
        if not all(isinstance(item_values.get(field), str) and item_values[field].strip() for field in ITEM_CREATE):
            frappe.throw("请明确业务商品名称、明细分组及真实库存单位")
        item = frappe.new_doc("Item")
        _updates(item, item_values)
        _fields("Item", REQUIRED_ITEM, "read")
        _fields("Item", {"item_code", "is_stock_item", "opening_stock", "standard_rate"}, "write")
        item.item_code, item.is_stock_item = "JF-PROD-" + UUID(request_id).hex, 1
        item.opening_stock, item.standard_rate = 0, 0
        item.check_permission("create")
        item.check_permission("read")
    # Existing Item locks precede category parent/child locks, matching edits.
    # New Items have no persisted source lock; validate all choices before save.
    _updates(profile, profile_values)
    profile.item_code = item.name if mode == "existing_item" else item.item_code
    profile.request_id, profile.request_digest, profile.creation_mode = request_id, digest, mode
    profile.check_permission("create")
    messages = list(getattr(frappe.local, "message_log", None) or [])
    collision = False
    try:
        if mode == "new_item":
            item.save()
            profile.item_code = item.name
        context = _CREATING.set({"profile": profile, "request_id": request_id, "request_digest": digest, "creation_mode": mode})
        try:
            profile.save()
        finally:
            _CREATING.reset(context)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        collision = True
    if collision:
        frappe.local.message_log = messages
        frappe.throw(COLLISION)
    # Frappe numeric serialization may persist None as zero without changing
    # the in-memory object. Return the actual saved rows and native timestamps.
    return _pack(*_documents(profile.name))
