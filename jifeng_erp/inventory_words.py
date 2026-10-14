"""The observed material-shape directory, using the existing inventory records.

Three-digit numbering, abbreviation scope and format 1 are candidate policies;
the original write/print semantics still need an authenticated observation.
"""

import hashlib
from html import escape
import json
import re

import frappe

from jifeng_erp import inventory_category as category
from jifeng_erp.archive_versions import same_version
from jifeng_erp.inventory_archive import DICTIONARY, _company, _login, _updates, _uuid, _value
from jifeng_erp.inventory_archive_images import _columns
from jifeng_erp.personnel_separation import _atomic


TYPE, KIND = "形状", "材料"
MODE = "automatic_v1"
ALIASES = {"legacy_number": "legacy_code", "value": "value", "remarks": "notes",
           "abbreviation": "pinyin", "inactive": "inactive"}
READ = {"name", "modified", "company", "archive_kind", "dictionary_type", "value", "inactive"}
MAX_ROWS = 500


def _type(dictionary_type):
    if dictionary_type != TYPE:
        frappe.throw("请选择材料形状")


def _scope(company=None):
    _login()
    if not frappe.has_permission(DICTIONARY, "read") or not READ.issubset(_columns(DICTIONARY)):
        frappe.throw("没有读取材料形状的权限", frappe.PermissionError)
    if company is not None:
        _company(company)


def _record(name, company=None, *, lock=False):
    if not isinstance(name, str) or not name or name != name.strip():
        frappe.throw("请选择准确的材料形状")
    doc = frappe.get_doc(DICTIONARY, name, for_update=lock)
    doc.check_permission("read")
    if doc.archive_kind != KIND or doc.dictionary_type != TYPE or company is not None and doc.company != company:
        frappe.throw("材料形状与当前公司不一致", frappe.PermissionError)
    _scope(doc.company)
    return doc


def _meta(can_write):
    meta = frappe.get_meta(DICTIONARY)
    readable, writable = _columns(DICTIONARY), _columns(DICTIONARY, "write")
    return {alias: {"fieldtype": meta.get_field(field).fieldtype,
                    "read_only": bool(alias == "legacy_number" or not can_write or field not in writable
                                      or meta.get_field(field).read_only),
                    "reqd": alias == "value"}
            for alias, field in ALIASES.items() if field in readable and meta.get_field(field)}


def _pack(doc):
    readable = _columns(DICTIONARY)
    write = bool(doc.has_permission("write"))
    return {"name": doc.name, "version": _value(doc.modified), "company": doc.company,
            "dictionary_type": TYPE, "numbering_mode": MODE, "can_write": write,
            "can_delete": bool(doc.has_permission("delete")), "field_meta": _meta(write),
            **{alias: _value(doc.get(field)) for alias, field in ALIASES.items() if field in readable}}


def _series(company):
    return "JF-INV-SHAPE-N-" + hashlib.sha256(company.encode()).hexdigest() + "-"


def _maximum_code(company):
    # The API's sequence lock serializes its writers; legacy codes are retained.
    codes = frappe.get_all(DICTIONARY, filters={"company": company, "archive_kind": KIND,
                           "dictionary_type": TYPE}, pluck="legacy_code")
    return max((int(code) for code in codes if isinstance(code, str) and re.fullmatch(r"[0-9]{1,140}", code)), default=0)


def _next_number(company, allocate=False):
    current = int(frappe.db.get_value("Series", _series(company), "current", order_by="name") or 0)
    if allocate:
        from frappe.model.naming import getseries

        current = int(getseries(_series(company), 3)) - 1
    next_number = max(current, _maximum_code(company)) + 1
    text = str(next_number).zfill(3)
    if len(text) > 140 or next_number > 2**63 - 1:
        frappe.throw("形状编号已超过字段长度，请先核对编号规则")
    if allocate:
        frappe.db.set_value("Series", _series(company), "current", next_number, update_modified=False)
    return text


@frappe.whitelist()
def init_dictionary(dictionary_type=TYPE, company=None, numbering_mode=MODE):
    _type(dictionary_type)
    _scope(company)
    if numbering_mode != MODE:
        frappe.throw("形状编号方式无效")
    doc = frappe.new_doc(DICTIONARY)
    doc.company, doc.archive_kind, doc.dictionary_type = company, KIND, TYPE
    writable = bool(doc.has_permission("create"))
    can_number = {"legacy_code", "value", "inactive"}.issubset(_columns(DICTIONARY) & _columns(DICTIONARY, "write"))
    return {"company": company, "dictionary_type": TYPE, "numbering_mode": MODE,
            "can_write": writable and can_number, "field_meta": _meta(writable), "inactive": 0,
            "number_preview": _next_number(company) if company and writable and can_number else None,
            "can_pinyin": bool(frappe.has_permission(DICTIONARY, "write") and
                               {"value", "pinyin"}.issubset(_columns(DICTIONARY) & _columns(DICTIONARY, "write"))),
            "can_print": bool(frappe.has_permission(DICTIONARY, "print"))}


@frappe.whitelist()
def read_dictionary(name, dictionary_type=TYPE):
    _type(dictionary_type)
    return _pack(_record(name))


@frappe.whitelist()
def query_dictionary(company, dictionary_type=TYPE, search="", offset=0):
    _type(dictionary_type)
    _scope(company)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效形状关键词")
    try:
        parsed_offset = int(offset)
    except (ValueError, TypeError, OverflowError):
        frappe.throw("形状页码无效")
    if isinstance(offset, bool) or str(offset) != str(parsed_offset) or not 0 <= parsed_offset <= 100000:
        frappe.throw("形状页码无效")
    readable = _columns(DICTIONARY)
    filters = {"company": company, "archive_kind": KIND, "dictionary_type": TYPE}
    or_filters = {field: ["like", "%" + search + "%"] for field in ("legacy_code", "value", "pinyin", "notes")
                  if field in readable} if search else None
    names = frappe.get_list(DICTIONARY, filters=filters, or_filters=or_filters, pluck="name",
                           order_by="legacy_code desc,name desc", limit_start=parsed_offset, limit_page_length=101)
    return {"company": company, "dictionary_type": TYPE, "offset": parsed_offset, "has_more": len(names) > 100,
            "rows": [_pack(_record(name, company)) for name in names[:100]]}


def _payload(payload):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("形状请求无效")
    if not isinstance(payload, dict) or not set(payload).issubset({"company", "dictionary_type", "request_id",
            "name", "version", "numbering_mode", "value", "remarks", "inactive", "operation", "rows"}):
        frappe.throw("形状请求包含不支持内容")
    _type(payload.get("dictionary_type"))
    _scope(payload.get("company"))
    if not payload.get("company"):
        frappe.throw("请选择公司")
    _uuid(payload.get("request_id"))
    operation = payload.get("operation", "upsert")
    if operation not in ("upsert", "delete", "pinyin"):
        frappe.throw("形状操作无效")
    if operation == "pinyin":
        if set(payload) != {"company", "dictionary_type", "request_id", "operation", "rows"}:
            frappe.throw("简拼请求字段无效")
        if not isinstance(payload["rows"], list) or not 1 <= len(payload["rows"]) <= MAX_ROWS:
            frappe.throw("一次简拼更新须包含1至500条形状，不会截断更新")
        names = set()
        for row in payload["rows"]:
            if (not isinstance(row, dict) or set(row) != {"name", "version", "abbreviation"}
                    or not isinstance(row["name"], str) or not row["name"] or row["name"] in names):
                frappe.throw("简拼请求行无效")
            names.add(row["name"])
            if not isinstance(row["abbreviation"], str) or len(row["abbreviation"]) > 140:
                frappe.throw("简拼原文无效")
    elif operation == "delete":
        if set(payload) != {"company", "dictionary_type", "request_id", "operation", "name", "version"}:
            frappe.throw("删除请求字段无效")
    else:
        if "rows" in payload or payload.get("numbering_mode", MODE) != MODE:
            frappe.throw("形状保存字段无效")
        if not payload.get("name") and (payload.get("version") or not isinstance(payload.get("value"), str) or not payload["value"].strip()):
            frappe.throw("请填写形状名称")
        if payload.get("name") and not any(field in payload for field in ("value", "remarks", "inactive")):
            frappe.throw("没有待保存的形状修改")
        for field in ("value", "remarks"):
            if field in payload and (payload[field] is not None and not isinstance(payload[field], str)
                                     or len(payload[field] or "") > (140 if field == "value" else 65535)):
                frappe.throw("形状原文无效")
        if "value" in payload:
            if not payload["value"] or not payload["value"].strip():
                frappe.throw("请填写形状名称")
            payload = {**payload, "value": payload["value"].strip()}
        if "inactive" in payload and (type(payload["inactive"]) not in (bool, int, str)
                                      or payload["inactive"] not in (False, True, 0, 1, "0", "1")):
            frappe.throw("请选择有效停用状态")
    digest = hashlib.sha256(json.dumps({"namespace": "inventory_shape_v1", **payload}, ensure_ascii=False,
                                      sort_keys=True, allow_nan=False).encode()).hexdigest()
    return payload, digest


@frappe.whitelist()
def preview_change(payload):
    payload, digest = _payload(payload)
    return {"request_id": payload["request_id"], "company": payload["company"], "dictionary_type": TYPE, "digest": digest}


def _existing(payload, digest):
    result = category._receipt(payload["request_id"], KIND, payload["company"], digest)
    if result and result.get("dictionary_type") != TYPE:
        frappe.throw("保存回执种类不一致")
    return result


def _store_receipt(payload, digest, rows):
    result = {"status": "applied", "request_id": payload["request_id"], "digest": digest, "company": payload["company"],
              "kind": KIND, "dictionary_type": TYPE, "rows": rows}
    receipt = frappe.new_doc(category.RECEIPT)
    receipt.name = receipt.request_id = payload["request_id"]
    receipt.actor, receipt.company, receipt.archive_kind = frappe.session.user, payload["company"], KIND
    receipt.request_digest, receipt.result_json = digest, json.dumps(result, ensure_ascii=False)
    token = category._RECEIPT_WRITE.set((receipt.request_id, receipt.actor, receipt.company, receipt.archive_kind,
                                       receipt.request_digest, receipt.result_json))
    try:
        receipt.insert(ignore_permissions=True)  # Existing protected server-only receipt, never a business permission bypass.
    finally:
        category._RECEIPT_WRITE.reset(token)
    return result


def _read_result(result):
    archives = []
    for row in result["rows"]:
        if row["operation"] == "delete":
            continue
        if not frappe.db.exists(DICTIONARY, row["name"]):
            continue
        doc = _record(row["name"], result["company"])
        archive = _pack(doc)
        if same_version(doc.modified, row["version"]):
            archive["change_request_id"] = result["request_id"]
        archives.append(archive)
    return {**{key: result[key] for key in ("status", "request_id", "digest", "company", "dictionary_type")},
            "archives": archives, "deleted": [row["name"] for row in result["rows"] if row["operation"] == "delete"]}


@frappe.whitelist()
def resolve_change(request_id, company, digest, dictionary_type=TYPE):
    _type(dictionary_type)
    _scope(company)
    _uuid(request_id)
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        frappe.throw("保存核对摘要无效")
    result = _existing({"request_id": request_id, "company": company}, digest)
    return _read_result(result) if result else {"status": "not_found", "request_id": request_id}


@frappe.whitelist(methods=["POST"])
@_atomic
def save_dictionary(payload):
    payload, digest = _payload(payload)
    previous = _existing(payload, digest)
    if previous:
        return _read_result(previous)
    operation = payload.get("operation", "upsert")
    rows = payload["rows"] if operation == "pinyin" else [payload]
    prepared = []
    for row in rows:
        if row.get("name"):
            doc = _record(row["name"], payload["company"], lock=True)
            doc.check_permission("delete" if operation == "delete" else "write")
            if not same_version(doc.modified, row.get("version")):
                frappe.throw("形状已被修改，请重新查询", frappe.TimestampMismatchError)
        else:
            doc = frappe.new_doc(DICTIONARY)
            doc.company, doc.archive_kind, doc.dictionary_type, doc.inactive = payload["company"], KIND, TYPE, 0
            doc.check_permission("create")
        if operation != "delete":
            fields = {"pinyin": row["abbreviation"]} if operation == "pinyin" else {
                ALIASES[field]: row[field] for field in ("value", "remarks", "inactive") if field in row}
            if not set(fields).issubset(_columns(DICTIONARY) & _columns(DICTIONARY, "write")):
                frappe.throw("没有读取或修改形状字段的权限", frappe.PermissionError)
            _updates(doc, fields, set(ALIASES.values()) - {"legacy_code"})
        prepared.append(doc)
    result_rows = []
    for doc in prepared:
        if operation == "delete":
            frappe.delete_doc(DICTIONARY, doc.name)
        else:
            if doc.is_new():
                if "legacy_code" not in _columns(DICTIONARY) & _columns(DICTIONARY, "write"):
                    frappe.throw("没有读取或分配形状编号的权限", frappe.PermissionError)
                _updates(doc, {"legacy_code": _next_number(payload["company"], allocate=True)}, {"legacy_code"})
            doc.save()
        result_rows.append({"name": doc.name, "version": _value(doc.modified), "operation": "delete" if operation == "delete" else "upsert"})
    return _read_result(_store_receipt(payload, digest, result_rows))


@frappe.whitelist()
def preview_pinyin(company, dictionary_type=TYPE):
    _type(dictionary_type)
    _scope(company)
    if not {"value", "pinyin"}.issubset(_columns(DICTIONARY) & _columns(DICTIONARY, "write")):
        frappe.throw("没有更新形状简拼的字段权限", frappe.PermissionError)
    names = frappe.get_list(DICTIONARY, filters={"company": company, "archive_kind": KIND, "dictionary_type": TYPE},
                           pluck="name", order_by="name", limit_page_length=MAX_ROWS + 1)
    if len(names) > MAX_ROWS:
        frappe.throw("形状超过500条，请先核对批量更新范围；不会截断更新")
    from pypinyin import Style, lazy_pinyin

    rows = []
    for name in names:
        doc = _record(name, company)
        if doc.has_permission("write"):
            rows.append({"name": name, "version": _value(doc.modified),
                         "abbreviation": "".join(lazy_pinyin(doc.value, style=Style.FIRST_LETTER)).upper()})
    return {"company": company, "dictionary_type": TYPE, "rows": rows}


@frappe.whitelist()
def print_dictionary(company, rows, dictionary_type=TYPE):
    _type(dictionary_type)
    _scope(company)
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except (ValueError, TypeError):
            frappe.throw("打印来源无效")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        frappe.throw("打印须包含1至500条准确形状")
    fields = ("legacy_code", "value", "pinyin", "notes", "inactive")
    if not set(fields).issubset(_columns(DICTIONARY)):
        frappe.throw("没有读取打印字段的权限", frappe.PermissionError)
    body, names = [], set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"name", "version"}
                or not isinstance(row["name"], str) or not row["name"] or row["name"] in names):
            frappe.throw("打印来源无效")
        names.add(row["name"])
        doc = _record(row["name"], company)
        doc.check_permission("print")
        if not same_version(doc.modified, row["version"]):
            frappe.throw("打印来源已有修改，请先刷新", frappe.TimestampMismatchError)
        body.append("<tr>" + "".join("<td>" + escape(("是" if doc.inactive else "否") if field == "inactive" else str(doc.get(field) or ""))
                                    + "</td>" for field in fields) + "</tr>")
    html = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>材料形状</title>"
            "<style>body{font:14px sans-serif}table{width:100%;border-collapse:collapse}th,td{border:1px solid #777;padding:5px;white-space:pre-wrap;overflow-wrap:anywhere}"
            "@page{size:A4;margin:15mm}</style><h2>材料形状</h2><table><thead><tr>"
            "<th>编号</th><th>名称</th><th>简拼</th><th>备注</th><th>停用</th></tr></thead><tbody>" + "".join(body) + "</tbody></table></html>")
    if len(html.encode()) > 1024 * 1024:
        frappe.throw("打印内容超过本次限额")
    return {"company": company, "dictionary_type": TYPE, "html": html, "row_count": len(rows)}
