"""Classic inventory archives, with native Item creation and scoped supplements.

Old display names, units and conversions are independent archive values. Only
creation writes Item; subsequent archive edits never modify shared Item data.
"""

from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from uuid import UUID

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


PROFILE = "Jifeng Inventory Profile"
DICTIONARY = "Jifeng Inventory Dictionary"
KINDS = ("物料", "材料")
PROFILE_EDIT = {
    "legacy_number", "legacy_code", "major_category", "minor_category", "old_number",
    "specification", "display_name", "location", "material", "shape", "color", "display_uom",
    "list_price", "processing_price", "weight", "supplier", "upper_limit", "lower_limit",
    "conversion_uom", "conversion_value", "owner_name", "region", "notes", "inactive",
}
PROFILE_READ = PROFILE_EDIT | {"company", "archive_kind", "item_code"}
ITEM_CREATE = {"item_group", "stock_uom"}
PROFILE_PROTECTED = {"company", "archive_kind", "item_code", "request_id", "request_digest"}
_CREATING = ContextVar("jifeng_inventory_creation", default=None)


def _value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _object(value, label):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            frappe.throw(label + "必须是有效对象")
    if not isinstance(value, dict):
        frappe.throw(label + "必须是对象")
    return value


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作库存档案", frappe.PermissionError)


def _kind(kind):
    if kind not in KINDS:
        frappe.throw("请选择物料或材料档案")
    return kind


def _company(company):
    if not isinstance(company, str) or not company.strip():
        frappe.throw("请选择公司")
    frappe.get_doc("Company", company).check_permission("select")


def _columns(doctype, permission):
    return set(get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True))


def _permission(document, permission, role_only=False):
    _login()
    if role_only:
        if not frappe.has_permission(document.doctype, permission):
            frappe.throw("没有创建该档案的权限", frappe.PermissionError)
    else:
        document.check_permission(permission)


def _uuid(value):
    if not isinstance(value, str):
        frappe.throw("新建请求必须包含有效的 UUID4 身份")
    try:
        result = UUID(value)
    except (ValueError, AttributeError):
        frappe.throw("新建请求必须包含有效的 UUID4 身份")
    if result.version != 4 or str(result) != value:
        frappe.throw("新建请求必须包含有效的 UUID4 身份")
    return result


def _normalize(value, field):
    if field.fieldtype == "Check":
        if type(value) not in (bool, int, str) or value not in (False, True, 0, 1, "0", "1"):
            frappe.throw("请选择有效勾选状态：" + field.label)
        return int(value)
    if value in (None, ""):
        return None
    if field.fieldtype in ("Float", "Currency", "Int"):
        try:
            if isinstance(value, bool):
                raise ValueError
            number = Decimal(str(value))
            if not number.is_finite() or field.fieldtype == "Int" and number != int(number):
                raise ValueError
            normalized = int(number) if field.fieldtype == "Int" else float(number)
            if not math.isfinite(normalized):
                raise ValueError
            return normalized
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            frappe.throw("请输入有效数值：" + field.label)
    if not isinstance(value, str):
        frappe.throw("请输入有效文本：" + field.label)
    return value


def _normalized(doctype, values, allowed):
    values = _object(values, "档案字段")
    if not set(values).issubset(allowed):
        frappe.throw("库存档案包含不支持或只读的字段")
    meta = frappe.get_meta(doctype)
    result = {}
    for name, value in values.items():
        field = meta.get_field(name)
        if not field:
            frappe.throw("库存档案字段尚未安装")
        result[name] = _normalize(value, field)
    return result


def _updates(document, values, allowed):
    values = _normalized(document.doctype, values, allowed)
    readable, writable = _columns(document.doctype, "read"), _columns(document.doctype, "write")
    for name, value in values.items():
        field = document.meta.get_field(name)
        if field.read_only or name not in readable & writable:
            frappe.throw("没有读取或修改该档案字段的权限", frappe.PermissionError)
        if field.fieldtype == "Link" and value:
            frappe.get_doc(field.options, value).check_permission("select")
        document.set(name, value)


def _latest(document, supplied, label):
    if not same_version(document.modified, supplied):
        frappe.throw(label + "已被修改，请重新打开后保存", frappe.TimestampMismatchError)


def _documents(name, kind, lock=False):
    _login()
    _kind(kind)
    if not isinstance(name, str) or not name:
        frappe.throw("请选择库存档案")
    profile = frappe.get_doc(PROFILE, name)
    _permission(profile, "read")
    _company(profile.company)
    if profile.archive_kind != kind:
        frappe.throw("库存档案种类不匹配")
    # Always lock native Item before its profile, including competing saves.
    item = frappe.get_doc("Item", profile.item_code, for_update=lock)
    _permission(item, "read")
    if lock:
        profile = frappe.get_doc(PROFILE, name, for_update=True)
        _permission(profile, "read")
        _company(profile.company)
        if profile.item_code != item.name or profile.archive_kind != kind:
            frappe.throw("库存档案来源已改变，请重新打开")
    return profile, item


def _field_meta(document, names, allowed):
    readable, writable = _columns(document.doctype, "read"), _columns(document.doctype, "write")
    fields, values = {}, {}
    for name in sorted(names & readable):
        field = document.meta.get_field(name)
        if not field:
            continue
        fields[name] = {"fieldtype": field.fieldtype, "options": field.options or "",
                        "description": field.description or "",
                        "reqd": bool(field.reqd),
                        "read_only": bool(field.read_only or name not in allowed or name not in writable)}
        values[name] = _value(document.get(name))
    return fields, values


def _pack(profile, item, unscoped=False):
    existing = not profile.is_new()
    linked = not item.is_new()
    editable = PROFILE_EDIT | ({"company"} if not existing else set())
    names = PROFILE_READ - ({"shape", "color"} if profile.archive_kind == "物料" else {"material"})
    profile_meta, profile_values = _field_meta(profile, names, editable)
    item_meta, item_values = _field_meta(item, ITEM_CREATE, ITEM_CREATE if not linked else set())
    source_visible = "item_code" in _columns(PROFILE, "read") & _columns("Item", "read")
    if not source_visible:
        profile_meta.pop("item_code", None)
        profile_values.pop("item_code", None)
    permitted = (lambda doc, perm: frappe.has_permission(doc.doctype, perm)) if unscoped else (
        lambda doc, perm: doc.has_permission(perm))
    can_create = not existing and permitted(profile, "create") and permitted(item, "create")
    can_link = (not existing and permitted(profile, "create") and permitted(item, "read")
                and source_visible)
    for meta in item_meta.values():
        if linked or not can_create:
            meta["read_only"] = True
            meta["reqd"] = False
    return {
        "name": profile.name if existing else None, "kind": profile.archive_kind,
        "item_code": item.name if linked and source_visible else None,
        "existing_item": item.name if linked and not existing and source_visible else None,
        "item_name": item.item_name if "item_name" in _columns("Item", "read") else None,
        "profile_version": _value(profile.modified) if existing else None,
        "item_version": _value(item.modified) if linked else None,
        "profile": profile_values, "item": item_values,
        "field_meta": {"profile": profile_meta, "item": item_meta},
        "can_create_item": bool(can_create), "can_link_item": bool(can_link),
        "can_write": bool(permitted(profile, "write") and permitted(item, "read") if existing
                          else can_link if linked else can_create or can_link),
    }


def _linked_item(name, lock=False):
    if not isinstance(name, str) or not name.strip():
        frappe.throw("请选择已有业务物料")
    item = frappe.get_doc("Item", name, for_update=lock)
    _permission(item, "read")
    if "item_code" not in _columns(PROFILE, "read") & _columns("Item", "read"):
        frappe.throw("没有读取业务物料编号的权限", frappe.PermissionError)
    # Native Item is the common first lock for both profile edits and bindings.
    # A current read sees a binding committed after an RR snapshot was opened.
    if frappe.db.get_value(PROFILE, {"item_code": item.name}, "name", for_update=lock):
        frappe.throw("该业务物料已有库存档案，请使用已有档案入口")
    return item


@frappe.whitelist()
def init_archive(kind, company=None, copy_from=None, existing_item=None):
    _login()
    _kind(kind)
    profile, item = frappe.new_doc(PROFILE), frappe.new_doc("Item")
    profile.company, profile.archive_kind = None, kind
    profile.conversion_value, profile.inactive = 1, 0
    _permission(profile, "create", role_only=True)
    if not (frappe.has_permission("Item", "create") or frappe.has_permission("Item", "read")):
        frappe.throw("没有读取或创建业务物料的权限", frappe.PermissionError)
    if existing_item and copy_from:
        frappe.throw("复制与关联已有业务物料不能同时使用")
    if copy_from:
        source, source_item = _documents(copy_from, kind)
        if company and company != source.company:
            frappe.throw("复制来源必须属于所选公司")
        company = source.company
        readable = _columns(PROFILE, "read")
        for field in PROFILE_EDIT & readable - {"legacy_number", "legacy_code", "old_number", "inactive"}:
            profile.set(field, source.get(field))
        for field in ITEM_CREATE & _columns("Item", "read"):
            item.set(field, source_item.get(field))
    if company:
        _company(company)
        profile.company = company
        _permission(profile, "create")
    if existing_item:
        if not company:
            frappe.throw("请先选择公司再关联业务物料")
        item = _linked_item(existing_item)
        readable = _columns("Item", "read")
        if "item_name" in readable:
            profile.display_name = item.item_name
        if "stock_uom" in readable:
            profile.display_uom = item.stock_uom
    return _pack(profile, item, unscoped=not company)


@frappe.whitelist()
def read_archive(name, kind, company=None):
    profile, item = _documents(name, kind)
    if company is not None:
        _company(company)
        if profile.company != company:
            frappe.throw("档案不属于当前公司，请重新查询", frappe.PermissionError)
    return _pack(profile, item)


def _digest(kind, profile_values, item_values, existing_item=None):
    body = {"kind": kind, "profile": _normalized(PROFILE, profile_values, PROFILE_EDIT | {"company"}),
            "item": _normalized("Item", item_values, ITEM_CREATE)}
    if existing_item:
        body["existing_item"] = existing_item
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _creation_name(request_id):
    return frappe.db.get_value(PROFILE, {"request_id": request_id}, "name")


@frappe.whitelist()
def resolve_creation(request_id, kind):
    _login()
    _kind(kind)
    _uuid(request_id)
    name = _creation_name(request_id)
    if not name:
        # Require the profile read role even when a token has no visible result.
        if not frappe.has_permission(PROFILE, "read"):
            frappe.throw("没有核对库存档案的权限", frappe.PermissionError)
        return {"status": "not_found"}
    profile, item = _documents(name, kind)
    return {"status": "found", "archive": _pack(profile, item)}


def _reuse_creation(name, kind, digest):
    profile, item = _documents(name, kind)
    if profile.request_digest != digest:
        frappe.throw("该新建请求已用于不同内容，请先核对原保存结果")
    return _pack(profile, item)


@frappe.whitelist(methods=["POST"])
@_atomic
def save_archive(payload):
    _login()
    payload = _object(payload, "库存档案请求")
    if not set(payload).issubset({"kind", "name", "profile_version", "item_version", "request_id", "profile", "item",
                                "existing_item"}):
        frappe.throw("库存档案请求包含不支持的参数")
    kind = _kind(payload.get("kind"))
    profile_values = _object(payload.get("profile", {}), "库存档案字段")
    item_values = _object(payload.get("item", {}), "原生库存字段")
    if payload.get("name"):
        if item_values or payload.get("request_id") or payload.get("existing_item"):
            frappe.throw("已有档案不能改写共享商品或新建请求身份")
        profile, item = _documents(payload["name"], kind, lock=True)
        _permission(profile, "write")
        _latest(profile, payload.get("profile_version"), "库存档案")
        _latest(item, payload.get("item_version"), "关联商品")
        if "company" in profile_values:
            if profile_values["company"] != profile.company:
                frappe.throw("库存档案不能修改公司")
            profile_values = {key: value for key, value in profile_values.items() if key != "company"}
        _updates(profile, profile_values, PROFILE_EDIT)
        profile.save()
        return _pack(profile, item)

    request_uuid = _uuid(payload.get("request_id"))
    linked_name = payload.get("existing_item")
    if linked_name is not None and (not isinstance(linked_name, str) or not linked_name.strip()):
        frappe.throw("请选择有效的已有业务物料")
    if payload.get("profile_version") or (payload.get("item_version") and not linked_name):
        frappe.throw("新档案不能携带已有档案版本")
    if linked_name and item_values:
        frappe.throw("关联已有业务物料不能改写共享商品")
    digest = _digest(kind, profile_values, item_values, linked_name)
    name = _creation_name(str(request_uuid))
    if name:
        return _reuse_creation(name, kind, digest)
    profile, item = frappe.new_doc(PROFILE), frappe.new_doc("Item")
    _permission(profile, "create", role_only=True)
    _company(profile_values.get("company"))
    profile.company, profile.archive_kind = profile_values["company"], kind
    profile.conversion_value, profile.inactive = 1, 0
    _updates(profile, profile_values, PROFILE_EDIT | {"company"})
    _permission(profile, "create")
    if linked_name:
        item = _linked_item(linked_name, lock=True)
        _latest(item, payload.get("item_version"), "关联商品")
    else:
        _permission(item, "create", role_only=True)
        if not all(isinstance(item_values.get(field), str) and item_values[field].strip() for field in ITEM_CREATE):
            frappe.throw("请明确选择系统商品分组及真实库存单位")
        _updates(item, item_values, ITEM_CREATE)
        item.item_code = "JF-INV-" + request_uuid.hex
        item.item_name, item.is_stock_item = profile.display_name, 1
        item.opening_stock = 0
        item.standard_rate = 0
        _permission(item, "create")
        item.save()
    profile.item_code, profile.request_id, profile.request_digest = item.name, str(request_uuid), digest
    context = _CREATING.set(profile)
    try:
        profile.save()
    finally:
        _CREATING.reset(context)
    return _pack(profile, item)


def validate_profile(profile):
    """Also enforce identity and semantic constraints on native Desk saves."""
    _login()
    _kind(profile.archive_kind)
    if profile.is_new():
        if _CREATING.get() is not profile:
            frappe.throw("请从物料或材料档案入口联合新建或关联业务物料")
        _uuid(profile.request_id)
        if not isinstance(profile.request_digest, str) or len(profile.request_digest) != 64:
            frappe.throw("库存档案新建请求校验不完整")
    else:
        before = profile.get_doc_before_save()
        if not before or any(before.get(field) != profile.get(field) for field in PROFILE_PROTECTED):
            frappe.throw("库存档案公司、种类、商品及新建请求身份不能修改")
    _company(profile.company)
    frappe.get_doc("Item", profile.item_code).check_permission("read")
    wrong_fields = ("shape", "color") if profile.archive_kind == "物料" else ("material",)
    if any(profile.get(field) not in (None, "") for field in wrong_fields):
        frappe.throw("物料与材料专属字段不能混用")
    for field in ("legacy_number", "display_name"):
        if not isinstance(profile.get(field), str) or not profile.get(field).strip():
            frappe.throw("请填写编号及名称")
    for field in ("list_price", "processing_price", "weight", "upper_limit", "lower_limit", "conversion_value"):
        number = _normalize(profile.get(field), profile.meta.get_field(field))
        if field == "conversion_value" and (number is None or number <= 0):
            frappe.throw("换算值必须大于零")
        if number is not None and number < 0:
            frappe.throw("档案参考数量及金额不能小于零")
        profile.set(field, number)
    if (profile.upper_limit is not None and profile.lower_limit is not None
            and profile.upper_limit < profile.lower_limit):
        frappe.throw("上限不能小于下限")
    profile.inactive = _normalize(profile.inactive, profile.meta.get_field("inactive"))
    for field, doctype in (("supplier", "Supplier"), ("conversion_uom", "UOM")):
        if profile.get(field):
            frappe.get_doc(doctype, profile.get(field)).check_permission("select")


def _dictionary_type(kind, dictionary_type):
    _kind(kind)
    if dictionary_type not in ("大类", "小类", "形状", "颜色"):
        frappe.throw("请选择有效词典类型")
    if kind == "物料" and dictionary_type in ("形状", "颜色"):
        frappe.throw("形状及颜色仅用于材料词典")


def validate_dictionary(document):
    _login()
    _dictionary_type(document.archive_kind, document.dictionary_type)
    _company(document.company)
    if not document.is_new():
        before = document.get_doc_before_save()
        if not before or any(before.get(field) != document.get(field)
                             for field in ("company", "archive_kind", "dictionary_type")):
            frappe.throw("词典公司、档案种类及类型不能修改")
    if not isinstance(document.value, str) or not document.value.strip():
        frappe.throw("请填写词典内容")
    document.value = document.value.strip()
    document.inactive = _normalize(document.inactive, document.meta.get_field("inactive"))
    document.dictionary_key = hashlib.sha256(json.dumps(
        [document.company, document.archive_kind, document.dictionary_type, document.value],
        ensure_ascii=False).encode()).hexdigest()


@frappe.whitelist()
def list_dictionary(kind, dictionary_type, company, search=""):
    _login()
    _dictionary_type(kind, dictionary_type)
    _company(company)
    if not frappe.has_permission(DICTIONARY, "read") or not {"value", "inactive"}.issubset(_columns(DICTIONARY, "read")):
        frappe.throw("没有读取该词典的权限", frappe.PermissionError)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效词典关键词")
    filters = {"company": company, "archive_kind": kind, "dictionary_type": dictionary_type, "inactive": 0}
    if search:
        filters["value"] = ["like", "%" + search + "%"]
    names = frappe.get_list(DICTIONARY, filters=filters, pluck="name", order_by="value asc", limit_page_length=100)
    rows = []
    for name in names:
        document = frappe.get_doc(DICTIONARY, name)
        if document.has_permission("read"):
            rows.append({"name": document.name, "value": document.value, "inactive": document.inactive})
    return rows


@frappe.whitelist(methods=["POST"])
@_atomic
def save_dictionary(payload):
    _login()
    payload = _object(payload, "词典请求")
    if not set(payload).issubset({"name", "version", "kind", "company", "dictionary_type", "value", "inactive"}):
        frappe.throw("词典请求包含不支持的参数")
    _dictionary_type(payload.get("kind"), payload.get("dictionary_type"))
    _company(payload.get("company"))
    if payload.get("name"):
        document = frappe.get_doc(DICTIONARY, payload["name"], for_update=True)
        _permission(document, "read")
        _permission(document, "write")
        _latest(document, payload.get("version"), "词典")
        if (document.company, document.archive_kind, document.dictionary_type) != (
                payload["company"], payload["kind"], payload["dictionary_type"]):
            frappe.throw("词典公司、档案种类及类型不能修改")
    else:
        document = frappe.new_doc(DICTIONARY)
        _updates(document, {"company": payload["company"], "archive_kind": payload["kind"],
                            "dictionary_type": payload["dictionary_type"]},
                 {"company", "archive_kind", "dictionary_type"})
        document.inactive = 0
        _permission(document, "create")
    _updates(document, {field: payload[field] for field in ("value", "inactive") if field in payload}, {"value", "inactive"})
    document.save()
    return {"name": document.name, "value": document.value, "inactive": document.inactive,
            "version": _value(document.modified)}
