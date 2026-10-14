"""Related inventory grids with atomic, traceable batch saves and selections."""

from contextvars import ContextVar
import hashlib
import json

import frappe

from jifeng_erp.archive_versions import same_version
from jifeng_erp.inventory_archive import (
    DICTIONARY,
    _columns,
    _company,
    _kind,
    _login,
    _object,
    _permission,
    _updates,
    _uuid,
    _value,
)
from jifeng_erp.personnel_separation import _atomic


MAJOR_FIELDS = ("legacy_code", "value", "owner_name", "region", "pinyin", "inactive")
MINOR_FIELDS = ("value", "material", "display_uom", "conversion_uom_text", "conversion_value",
                "inactive", "notes", "pinyin")
IDENTITY_FIELDS = {"name", "company", "archive_kind", "dictionary_type", "parent_dictionary", "modified"}
REQUIRED_READ = IDENTITY_FIELDS | {"value", "inactive"}
AUDIT_FIELDS = ("owner", "creation", "modified_by")
RECEIPT = "Jifeng Inventory Category Batch"
MAX_BATCH_ROWS = 500
_RECEIPT_WRITE = ContextVar("jifeng_inventory_category_batch", default=None)


def _installed():
    meta = frappe.get_meta(DICTIONARY)
    if any(not meta.get_field(field) for field in (*MAJOR_FIELDS, *MINOR_FIELDS, "parent_dictionary")):
        frappe.throw("库存类别关联字段尚未安装，请先完成版本安装")
    return meta


def _read_permission():
    _login()
    _installed()
    if not frappe.has_permission(DICTIONARY, "read") or not REQUIRED_READ.issubset(_columns(DICTIONARY, "read")):
        frappe.throw("没有读取库存类别所需字段的权限", frappe.PermissionError)


def _scope(kind, company):
    _read_permission()
    _kind(kind)
    _company(company)


def _name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        frappe.throw("请选择准确的库存类别记录")
    return value


def _record(name, kind, company, expected_type=None, *, lock=False):
    _name(name)
    document = frappe.get_doc(DICTIONARY, name, for_update=lock)
    _permission(document, "read")
    if (document.archive_kind, document.company) != (kind, company):
        frappe.throw("库存类别与所选公司或档案种类不匹配")
    if expected_type and document.dictionary_type != expected_type:
        frappe.throw("库存类别层级不匹配")
    return document


def _meta_fields(fields, *, can_write):
    meta, readable, writable = _installed(), _columns(DICTIONARY, "read"), _columns(DICTIONARY, "write")
    return {
        field: {
            "fieldtype": meta.get_field(field).fieldtype,
            "label": meta.get_field(field).label,
            "read_only": bool(not can_write or field not in writable or meta.get_field(field).read_only),
        }
        for field in fields if field in readable
    }


def _pack(document):
    fields = MAJOR_FIELDS if document.dictionary_type == "大类" else MINOR_FIELDS
    can_write = bool(document.has_permission("write"))
    readable = _columns(DICTIONARY, "read")
    return {
        **{field: _value(document.get(field)) for field in (*IDENTITY_FIELDS, *fields, *AUDIT_FIELDS) if field in readable},
        "version": _value(document.modified),
        "can_write": can_write,
        "can_delete": bool(document.has_permission("delete")),
        "field_meta": _meta_fields(fields, can_write=can_write),
    }


def _parent(child, kind, company):
    if not child.parent_dictionary:
        frappe.throw("小类尚未关联大类，请先明确类别归属")
    return _record(child.parent_dictionary, kind, company, "大类")


def _integer(value, minimum, maximum, label):
    try:
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError
        result = int(value)
        if str(result) != str(value) or not minimum <= result <= maximum:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        frappe.throw(label + "无效")
    return result


@frappe.whitelist()
def init_grid(kind, company=None):
    _read_permission()
    _kind(kind)
    if company:
        _company(company)
        prototype = frappe.new_doc(DICTIONARY)
        prototype.company, prototype.archive_kind, prototype.dictionary_type = company, kind, "大类"
        can_create = bool(prototype.has_permission("create"))
    else:
        can_create = bool(frappe.has_permission(DICTIONARY, "create"))
    return {
        "kind": kind,
        "company": company or None,
        "can_create": can_create,
        "can_export": bool(frappe.has_permission(DICTIONARY, "export")),
        "major_meta": _meta_fields(MAJOR_FIELDS, can_write=can_create),
        "minor_meta": _meta_fields(MINOR_FIELDS, can_write=can_create),
    }


@frappe.whitelist()
def query_grid(kind, company, category=None, search="", offset=0, page_size=100, unlinked=False):
    _scope(kind, company)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效类别关键词")
    offset = _integer(offset, 0, 1000000, "类别页码")
    page_size = _integer(page_size, 1, 500, "每页条数")
    filters = {"archive_kind": kind, "company": company, "dictionary_type": "大类"}
    record_type, parent = "major", None
    if unlinked not in (False, True, 0, 1, "0", "1"):
        frappe.throw("未关联筛选无效")
    unlinked = unlinked in (True, 1, "1")
    if unlinked and category:
        frappe.throw("未关联小类不能同时筛选所属大类")
    if unlinked:
        filters.update(dictionary_type="小类", parent_dictionary=["is", "not set"])
        record_type = "minor"
    if category:
        parent = _record(category, kind, company, "大类")
        filters.update(dictionary_type="小类", parent_dictionary=category)
        record_type = "minor"
    options = {"or_filters": [[field, "like", "%" + search + "%"]
        for field in ("value", "legacy_code", "pinyin") if field in _columns(DICTIONARY, "read")]} if search else {}
    names = frappe.get_list(DICTIONARY, filters=filters, pluck="name", order_by="creation asc, name asc",
                            limit_start=offset, limit_page_length=page_size + 1, **options)
    rows = []
    for name in names[:page_size]:
        document = _record(name, kind, company, filters["dictionary_type"])
        if parent and document.parent_dictionary != parent.name:
            frappe.throw("小类关联已变化，请重新查询")
        rows.append(_pack(document))
    return {"kind": kind, "company": company, "record_type": record_type, "category": category or None,
            "category_version": _value(parent.modified) if parent else None, "unlinked": unlinked,
            "rows": rows, "offset": offset, "has_more": len(names) > page_size}


@frappe.whitelist()
def select_category(kind, company, name, version, category_version):
    """Resolve a selected child again before filling the editable archive draft."""
    _scope(kind, company)
    child = _record(name, kind, company, "小类")
    parent = _parent(child, kind, company)
    _permission(child, "select")
    _permission(parent, "select")
    if child.inactive or parent.inactive:
        frappe.throw("类别已停用，不能用于新的档案选择")
    if not same_version(child.modified, version) or not same_version(parent.modified, category_version):
        frappe.throw("类别已变化，请重新查询并选择")
    needed = {"value", "material", "display_uom", "conversion_uom_text", "conversion_value", "owner_name", "region"}
    if not needed.issubset(_columns(DICTIONARY, "read")):
        frappe.throw("没有读取类别带入字段的权限", frappe.PermissionError)
    return {
        "kind": kind, "company": company, "name": child.name, "category": parent.name,
        "version": _value(child.modified), "category_version": _value(parent.modified),
        "profile": {
            "major_category": parent.value,
            "minor_category": child.value,
            "material": child.material,
            "display_uom": child.display_uom,
            "conversion_uom": child.conversion_uom_text,
            "conversion_value": child.conversion_value,
            "owner_name": parent.owner_name,
            "region": parent.region,
        },
    }


@frappe.whitelist()
def query_selection(kind, company, search="", offset=0, page_size=100):
    """The archive chooser shows related child and parent fields in one table."""
    _scope(kind, company)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效类别关键词")
    offset = _integer(offset, 0, 1000000, "类别页码")
    page_size = _integer(page_size, 1, 500, "每页条数")
    needed = {"legacy_code", "material", "display_uom", "conversion_uom_text",
              "conversion_value", "owner_name", "region", "pinyin"}
    if not needed.issubset(_columns(DICTIONARY, "read")):
        frappe.throw("没有读取类别词典所需字段的权限", frappe.PermissionError)
    filters = {"company": company, "archive_kind": kind, "dictionary_type": "小类", "inactive": 0,
               "parent_dictionary": ["is", "set"]}
    # Search child fields here. Parent search can select that parent on the grid;
    # no unrestricted SQL join bypasses native list or record permissions.
    options = {"or_filters": [[field, "like", "%" + search + "%"]
               for field in ("value", "pinyin", "material", "display_uom")]} if search else {}
    names = frappe.get_list(DICTIONARY, filters=filters, pluck="name", order_by="creation asc, name asc",
                            limit_start=offset, limit_page_length=page_size + 1, **options)
    rows, parents = [], {}
    for name in names[:page_size]:
        child = _record(name, kind, company, "小类")
        parent_name = child.parent_dictionary
        if not parent_name or child.inactive:
            continue
        if parent_name not in parents:
            parent = frappe.get_doc(DICTIONARY, parent_name)
            parents[parent_name] = parent
        parent = parents[parent_name]
        if not all(document.has_permission(permission) for document in (child, parent)
                   for permission in ("read", "select")):
            continue
        if (parent.company, parent.archive_kind, parent.dictionary_type) != (company, kind, "大类") or parent.inactive:
            continue
        rows.append({"name": child.name, "version": _value(child.modified), "category": parent.name,
                     "category_version": _value(parent.modified), "legacy_code": parent.legacy_code,
                     "minor_category": child.value, "major_category": parent.value,
                     "material": child.material, "display_uom": child.display_uom,
                     "conversion_uom": child.conversion_uom_text, "conversion_value": child.conversion_value,
                     "owner_name": parent.owner_name, "region": parent.region, "pinyin": child.pinyin})
    return {"kind": kind, "company": company, "rows": rows, "offset": offset, "has_more": len(names) > page_size}


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _receipt(request_id, kind, company, digest):
    name = frappe.db.get_value(RECEIPT, {"request_id": request_id}, "name")
    if not name:
        return None
    receipt = frappe.get_doc(RECEIPT, name)
    if (receipt.actor, receipt.company, receipt.archive_kind, receipt.request_id) != (
        frappe.session.user, company, kind, request_id
    ):
        frappe.throw("没有核对这次类别保存的权限", frappe.PermissionError)
    if receipt.request_digest != digest:
        frappe.throw("同一类别保存身份不能用于不同内容")
    result = json.loads(receipt.result_json)
    # Permissions can be revoked after a write. Do not expose record identities
    # from the historical receipt when the caller can no longer read that row.
    for row in result["rows"]:
        if row["operation"] != "delete":
            if not frappe.db.get_value(DICTIONARY, {"name": row["name"]}, "name"):
                continue
            _record(row["name"], kind, company)
    return result


def validate_batch_receipt(document):
    expected = _RECEIPT_WRITE.get()
    values = (document.request_id, document.actor, document.company, document.archive_kind,
              document.request_digest, document.result_json)
    if not document.is_new() or expected != values or document.name not in (None, document.request_id):
        frappe.throw("类别保存追溯只能由服务生成", frappe.PermissionError)


def prevent_receipt_change(*args, **kwargs):
    frappe.throw("类别保存追溯不能删除或改名", frappe.PermissionError)


@frappe.whitelist()
def resolve_batch(request_id, kind, company, digest):
    """Read a committed receipt. A missing result never triggers a write retry."""
    _scope(kind, company)
    _uuid(request_id)
    if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        frappe.throw("类别保存核对身份无效")
    result = _receipt(request_id, kind, company, digest)
    return result or {"status": "not_found", "request_id": request_id, "digest": digest}


def _batch(payload):
    payload = _object(payload, "类别保存")
    if set(payload) != {"kind", "company", "request_id", "rows"}:
        frappe.throw("类别保存参数不完整或含不支持内容")
    _scope(payload["kind"], payload["company"])
    _uuid(payload["request_id"])
    rows = payload["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_BATCH_ROWS:
        frappe.throw("每次类别保存须包含 1 至 500 行修改")
    ids, names = set(), set()
    for row in rows:
        if not isinstance(row, dict) or not set(row).issubset({"row_id", "name", "version", "record_type",
                "operation", "values", "category", "category_version", "parent_row_id"}):
            frappe.throw("类别保存行包含不支持内容")
        _uuid(row.get("row_id"))
        if row["row_id"] in ids:
            frappe.throw("类别保存不能重复同一行身份")
        ids.add(row["row_id"])
        if row.get("record_type") not in ("major", "minor") or row.get("operation") not in ("upsert", "delete", "associate"):
            frappe.throw("类别保存层级或操作无效")
        if row.get("name"):
            _name(row["name"])
            if row["name"] in names:
                frappe.throw("类别保存不能重复修改同一记录")
            names.add(row["name"])
        if row["operation"] == "delete":
            if not row.get("name") or row.get("values"):
                frappe.throw("删除须为已有类别且不能同时修改内容")
        elif row["operation"] == "associate":
            if row["record_type"] != "minor" or not row.get("name") or row.get("values") or not row.get("category"):
                frappe.throw("关联须为已有未关联小类与准确大类")
        else:
            values = _object(row.get("values"), "类别字段")
            allowed = MAJOR_FIELDS if row["record_type"] == "major" else MINOR_FIELDS
            if not values or not set(values).issubset(allowed):
                frappe.throw("类别字段包含不支持或只读内容")
        if row["record_type"] == "major":
            if any(row.get(key) for key in ("category", "category_version", "parent_row_id")):
                frappe.throw("大类不能关联上级类别")
        elif bool(row.get("category")) == bool(row.get("parent_row_id")):
            frappe.throw("小类须明确一个所属大类")
        if row.get("category"):
            _name(row["category"])
        if row.get("parent_row_id"):
            _uuid(row["parent_row_id"])
            if row.get("category_version"):
                frappe.throw("新增大类引用不能携带已有大类版本")
        if not row.get("name") and row.get("version"):
            frappe.throw("新增类别不能携带已有记录版本")
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 1024 * 1024:
            raise ValueError
        digest = _digest(payload)
    except (ValueError, TypeError, OverflowError):
        frappe.throw("类别保存内容无效或过大")
    return payload, digest


@frappe.whitelist()
def preview_batch(payload):
    """Generate an exact digest before POST; this never saves a row or receipt."""
    payload, digest = _batch(payload)
    return {"request_id": payload["request_id"], "kind": payload["kind"], "company": payload["company"],
            "digest": digest, "row_count": len(payload["rows"])}


@frappe.whitelist()
def preview_pinyin(kind, company, rows):
    _scope(kind, company)
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except (TypeError, ValueError):
            frappe.throw("简拼请求无效")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_BATCH_ROWS:
        frappe.throw("每次简拼更新须包含 1 至 500 行")
    if not {"value", "pinyin"}.issubset(_columns(DICTIONARY, "read") & _columns(DICTIONARY, "write")):
        frappe.throw("没有读取或修改简拼字段的权限", frappe.PermissionError)
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"row_id", "value"}:
            frappe.throw("简拼请求字段无效")
        _uuid(row["row_id"])
        if not isinstance(row["value"], str) or not 1 <= len(row["value"]) <= 140:
            frappe.throw("简拼请求名称无效")
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        frappe.throw("简拼组件尚未安装，请先完成版本安装")
    return {"kind": kind, "company": company,
            "rows": [{"row_id": row["row_id"], "value": row["value"],
                      "pinyin": "".join(lazy_pinyin(row["value"], style=Style.FIRST_LETTER)).upper()}
                     for row in rows]}


def _prepare(payload):
    """Lock and check every original version before saving any row."""
    kind, company, rows = payload["kind"], payload["company"], payload["rows"]
    references = {row["name"] for row in rows if row.get("name")}
    references.update(row["category"] for row in rows if row.get("category"))
    documents = {name: _record(name, kind, company, lock=True) for name in sorted(references)}
    prepared = {}
    for row in rows:
        expected_type = "大类" if row["record_type"] == "major" else "小类"
        if row.get("name"):
            document = documents[row["name"]]
            if document.dictionary_type != expected_type or not same_version(document.modified, row.get("version")):
                frappe.throw("类别已有变化，请重新查询后保存", frappe.TimestampMismatchError)
            _permission(document, "delete" if row["operation"] == "delete" else "write")
        else:
            document = frappe.new_doc(DICTIONARY)
            _updates(document, {"company": company, "archive_kind": kind, "dictionary_type": expected_type},
                     {"company", "archive_kind", "dictionary_type"})
            _permission(document, "create")
        if row["record_type"] == "minor" and row.get("category"):
            parent = documents[row["category"]]
            if parent.dictionary_type != "大类" or not same_version(parent.modified, row.get("category_version")):
                frappe.throw("所属大类已有变化，请重新查询后保存", frappe.TimestampMismatchError)
            if row["operation"] == "associate" and document.parent_dictionary:
                frappe.throw("小类已有关联，不能覆盖归属")
            if row.get("name") and row["operation"] != "associate" and document.parent_dictionary != parent.name:
                frappe.throw("小类归属已有变化，请重新查询")
            if (not row.get("name") or row["operation"] == "associate") and parent.inactive:
                frappe.throw("大类已停用，不能新增小类")
        if row["operation"] == "upsert":
            allowed = MAJOR_FIELDS if row["record_type"] == "major" else MINOR_FIELDS
            _updates(document, row["values"], allowed)
        prepared[row["row_id"]] = (row, document)
    deleted_names = {row["name"] for row in rows if row["operation"] == "delete"}
    for row, document in prepared.values():
        if row["record_type"] != "minor":
            continue
        if row.get("parent_row_id"):
            target = prepared.get(row["parent_row_id"])
            if (not target or target[0]["record_type"] != "major" or target[0].get("name")
                    or target[0]["operation"] != "upsert" or row.get("name")):
                frappe.throw("新增小类须关联这次保存中的新增大类")
        if row.get("category") in deleted_names and row["operation"] != "delete":
            frappe.throw("不能同时删除大类并保存其小类")
    return prepared


@frappe.whitelist(methods=["POST"])
@_atomic
def save_batch(payload):
    payload, digest = _batch(payload)
    existing = _receipt(payload["request_id"], payload["kind"], payload["company"], digest)
    if existing:
        return existing
    prepared = _prepare(payload)
    results = []
    # Parents precede new children. Native link checks run normally on deletion,
    # with children deleted first; no force/ignore_links/permission override.
    order = sorted(prepared.values(), key=lambda entry: (
        entry[0]["operation"] == "delete",
        entry[0]["record_type"] == ("major" if entry[0]["operation"] == "delete" else "minor"),
    ))
    for row, document in order:
        if row["operation"] == "delete":
            frappe.delete_doc(DICTIONARY, document.name)
        else:
            if row["record_type"] == "minor" and (not row.get("name") or row["operation"] == "associate"):
                parent_name = row.get("category") or prepared[row["parent_row_id"]][1].name
                _updates(document, {"parent_dictionary": parent_name}, {"parent_dictionary"})
            document.save()
        results.append({"row_id": row["row_id"], "name": document.name,
                        "operation": row["operation"], "version": _value(document.modified)})
    result = {"status": "applied", "request_id": payload["request_id"], "digest": digest,
              "kind": payload["kind"], "company": payload["company"], "rows": results}
    receipt = frappe.new_doc(RECEIPT)
    receipt.name = receipt.request_id = payload["request_id"]
    receipt.actor, receipt.company, receipt.archive_kind = frappe.session.user, payload["company"], payload["kind"]
    receipt.request_digest, receipt.result_json = digest, json.dumps(result, ensure_ascii=False)
    token = _RECEIPT_WRITE.set((receipt.request_id, receipt.actor, receipt.company, receipt.archive_kind,
                               receipt.request_digest, receipt.result_json))
    try:
        # This append-only receipt has no client create/write permission. Its
        # unique UUID is the final atomic commit gate for concurrent duplicates.
        receipt.insert(ignore_permissions=True)
    finally:
        _RECEIPT_WRITE.reset(token)
    return result
