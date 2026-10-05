"""Shared native Operation maintenance with a separate display-order receipt.

The display sequence never becomes a BOM/Work Order execution sequence. Native
Operation permissions, validation and naming remain authoritative. Receipts are
read-only recovery evidence; a used UUID never authorizes another creation POST.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from uuid import UUID

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.archive_versions import same_version
from jifeng_erp.personnel_separation import _atomic


NATIVE = "Operation"
DOCTYPE = "Jifeng Order Operation"
SCOPE = "shared_operation"
BATCH_LENGTH = 200
MAX_SEQUENCE = 2147483647
MAX_SAFE_INT = 9007199254740991
AUDIT = ("owner", "creation", "modified_by")
NATIVE_REQUIRED = {"name", "modified"}
ORDER_REQUIRED = {"name", "modified", "operation"}
RECEIPT = {"request_id", "request_digest"}
_SERVER = object()
_UNSET = object()
CHANGED = "共用工序来源已变化，请重新读取核对"
COLLISION = "工序或新建请求已使用，请只读核对回执，不要重复保存"


def _login():
    if frappe.session.user == "Guest":
        frappe.throw("请登录后操作共用工序", frappe.PermissionError)


def _name(value):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 140
    ):
        frappe.throw("请填写准确的工序名称，长度不能超过一百四十个字符")
    return value


def _integer(value, maximum, label):
    if (
        isinstance(value, str)
        and len(value) <= 16
        and value.isascii()
        and value.isdecimal()
    ):
        value = int(value)
    if type(value) is not int or not 0 <= value <= maximum:
        frappe.throw(label + "须为范围内的非负整数")
    return value


def _sequence(value):
    return _integer(value, MAX_SEQUENCE, "页面顺序")


def _remarks(value, operation):
    # Operation.validate fills a false/empty description with its native name.
    if value in (None, ""):
        return operation
    if not isinstance(value, str):
        frappe.throw("工序备注须为文本原文")
    return value


def _uuid(value):
    if not isinstance(value, str):
        frappe.throw("新建请求须包含有效的 UUID4")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        frappe.throw("新建请求须包含有效的 UUID4")
    if parsed.version != 4 or str(parsed) != value:
        frappe.throw("新建请求须包含有效的 UUID4")
    return value


def _payload(payload, *, creation=False):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("工序请求须为有效对象")
    allowed = (
        {"operation", "sequence", "remarks", "request_id"}
        if creation
        else {
            "operation",
            "version",
            "ordering_name",
            "ordering_version",
            "sequence",
            "remarks",
        }
    )
    if not isinstance(payload, dict) or not set(payload).issubset(allowed):
        frappe.throw("工序请求包含不支持的参数")
    if "operation" not in payload:
        frappe.throw("请填写工序名称")
    _name(payload["operation"])
    return payload


def _creation(payload):
    payload = _payload(payload, creation=True)
    return {
        "operation": payload["operation"],
        "sequence": _sequence(payload.get("sequence", 0)),
        "remarks": _remarks(payload.get("remarks"), payload["operation"]),
        "request_id": _uuid(payload.get("request_id")),
    }


def _digest(values):
    return hashlib.sha256(
        json.dumps(
            {
                "scope": SCOPE,
                **{key: values[key] for key in ("operation", "sequence", "remarks")},
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _version(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _columns(doctype, permission):
    permitted = set(
        get_permitted_fields(doctype, permission_type=permission, ignore_virtual=True)
    )
    if frappe.session.user == "Administrator":
        return permitted
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access("mask", parenttype=None))
    return permitted - {
        field.fieldname
        for field in meta.fields
        if field.get("mask") and (field.permlevel or 0) not in levels
    }


def _fields(doctype, fields, permission):
    if not set(fields).issubset(_columns(doctype, permission)):
        frappe.throw("没有操作共用工序所需字段的权限", frappe.PermissionError)


def _editable(doctype, field):
    definition = frappe.get_meta(doctype).get_field(field)
    return not definition or not definition.read_only


def _write_field(doctype, field):
    _fields(doctype, {field}, "read")
    _fields(doctype, {field}, "write")
    if not _editable(doctype, field):
        frappe.throw("没有修改该工序字段的权限", frappe.PermissionError)


def _read_access():
    for doctype, required in ((NATIVE, NATIVE_REQUIRED), (DOCTYPE, ORDER_REQUIRED)):
        if not frappe.has_permission(doctype, "read", throw=False):
            frappe.throw("没有读取共用工序及排序来源的权限", frappe.PermissionError)
        _fields(doctype, required, "read")


def _quiet(document, action):
    return bool(
        frappe.has_permission(document.doctype, action, doc=document, throw=False)
    )


def _current(document, row, required):
    if not required.issubset(row):
        frappe.throw("没有读取工序完整来源投影的权限", frappe.PermissionError)
    if document.name != row["name"] or not same_version(
        document.modified, _version(row["modified"])
    ):
        frappe.throw(CHANGED)
    for field, value in row.items():
        if field in ("modified", "creation"):
            if document.get(field) is None and value is None:
                continue
            if not same_version(document.get(field), _version(value)):
                frappe.throw(CHANGED)
        elif document.get(field) != value:
            frappe.throw(CHANGED)


def _projection(document, *, receipt=False, row=None):
    document.check_permission("read")
    required = NATIVE_REQUIRED if document.doctype == NATIVE else ORDER_REQUIRED
    requested = ["name", "modified", *AUDIT]
    requested += (
        ["description"] if document.doctype == NATIVE else ["operation", "sequence"]
    )
    if receipt:
        required = required | RECEIPT
        requested += sorted(RECEIPT)
    _fields(document.doctype, required, "read")
    readable = _columns(document.doctype, "read")
    if row is None:
        listed = frappe.get_list(
            document.doctype,
            filters={"name": document.name},
            fields=[field for field in requested if field in readable],
            limit_page_length=1,
        )
        if not listed:
            frappe.throw("没有读取该工序来源的权限", frappe.PermissionError)
        row = listed[0]
    row = {
        key: value for key, value in row.items() if key in readable and key in requested
    }
    _current(document, row, required)
    return row


def _ordering(operation, *, lock=False):
    # An unfiltered lookup is a guard only. Its values never fill a native
    # permission-filtered projection or make a hidden mapping look absent.
    name = frappe.db.get_value(
        DOCTYPE, {"operation": operation}, "name", for_update=lock
    )
    if not name:
        return None
    document = frappe.get_doc(DOCTYPE, name, for_update=lock)
    if document.operation != operation:
        frappe.throw(CHANGED)
    _projection(document)
    return document


def _field_meta(field, *, readonly, creation=False):
    doctype, native_field = {
        "operation": (NATIVE, "name"),
        "remarks": (NATIVE, "description"),
        "sequence": (DOCTYPE, "sequence"),
    }[field]
    definition = frappe.get_meta(doctype).get_field(native_field)
    return {
        "fieldtype": "Data" if field == "operation" else definition.fieldtype,
        "options": (definition.options or "") if definition else "",
        "reqd": field == "operation" or bool(definition and definition.reqd),
        "read_only": bool(readonly or definition and definition.read_only),
        "description": (
            "所有公司共用；改名须明确确认，已有 BOM 或报工引用时暂不支持。"
            if field == "operation"
            else "仅控制此页排列，不改变生产工序顺序。"
            if field == "sequence"
            else "留空时原生工序会使用工序名称作为描述。"
            if creation
            else (definition.description or "")
        ),
    }


def _pack(document, ordering=_UNSET, *, row=None):
    native = _projection(document, row=row)
    ordering = _ordering(document.name) if ordering is _UNSET else ordering
    ordered = _projection(ordering) if ordering else {}
    if ordering and ordered["operation"] != native["name"]:
        frappe.throw(CHANGED)
    native_write = _quiet(document, "write")
    can_rename = bool(
        native_write
        and frappe.get_meta(NATIVE).allow_rename
        and "name" in _columns(NATIVE, "write")
        and _editable(NATIVE, "name")
    )
    target = ordering or frappe.new_doc(DOCTYPE)
    if not ordering:
        target.operation = document.name
    order_write = native_write and _quiet(target, "write" if ordering else "create")
    can_remarks = (
        native_write
        and "description" in native
        and "description" in _columns(NATIVE, "write")
        and _editable(NATIVE, "description")
    )
    can_sequence = (
        order_write
        and "sequence" in _columns(DOCTYPE, "read")
        and (not ordering or "sequence" in ordered)
        and "sequence" in _columns(DOCTYPE, "write")
        and _editable(DOCTYPE, "sequence")
        and (bool(ordering) or "operation" in _columns(DOCTYPE, "write"))
    )
    result = {
        "scope": SCOPE,
        "name": native["name"],
        "version": _version(native["modified"]),
        "ordering_name": ordered.get("name"),
        "ordering_version": _version(ordered.get("modified")),
        "field_meta": {"operation": _field_meta("operation", readonly=not can_rename)},
        "can_write": bool(can_remarks or can_sequence),
        "can_rename": can_rename,
        **{field: _version(native[field]) for field in AUDIT if field in native},
        **{
            "ordering_" + field: _version(ordered[field])
            for field in AUDIT
            if field in ordered
        },
    }
    if "description" in native:
        result["remarks"] = native["description"]
        result["field_meta"]["remarks"] = _field_meta(
            "remarks", readonly=not can_remarks
        )
    if "sequence" in _columns(DOCTYPE, "read") and (
        not ordering or "sequence" in ordered
    ):
        result["sequence"] = ordered.get("sequence")
        result["field_meta"]["sequence"] = _field_meta(
            "sequence", readonly=not can_sequence
        )
    return result


@frappe.whitelist()
def init_operations():
    _login()
    _read_access()
    native, ordering = frappe.new_doc(NATIVE), frappe.new_doc(DOCTYPE)
    can_create = (
        _quiet(native, "create")
        and _quiet(native, "write")
        and _quiet(ordering, "create")
        and {"name", "description"}.issubset(_columns(NATIVE, "write"))
        and {"operation", "sequence"}.issubset(_columns(DOCTYPE, "write"))
        and "description" in _columns(NATIVE, "read")
        and "sequence" in _columns(DOCTYPE, "read")
        and _editable(NATIVE, "name")
        and _editable(NATIVE, "description")
        and _editable(DOCTYPE, "sequence")
    )
    result = {
        "scope": SCOPE,
        "name": None,
        "version": None,
        "ordering_name": None,
        "ordering_version": None,
        "can_write": bool(can_create),
        "can_create": bool(can_create),
        "can_rename": False,
        "field_meta": {
            "operation": _field_meta(
                "operation", readonly=not can_create, creation=True
            )
        },
    }
    for field, doctype, source in (
        ("remarks", NATIVE, "description"),
        ("sequence", DOCTYPE, "sequence"),
    ):
        if source in _columns(doctype, "read"):
            result[field] = None
            result["field_meta"][field] = _field_meta(
                field, readonly=not can_create, creation=True
            )
    return result


@frappe.whitelist()
def read_operation(operation):
    _login()
    _read_access()
    _name(operation)
    return _pack(frappe.get_doc(NATIVE, operation))


def _rename_payload(payload):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            frappe.throw("工序改名请求须为有效对象")
    fields = {"operation", "new_name", "version", "ordering_name", "ordering_version"}
    if not isinstance(payload, dict) or set(payload) != fields:
        frappe.throw("工序改名须提供准确名称和两个来源版本")
    _name(payload["operation"])
    _name(payload["new_name"])
    if payload["operation"] == payload["new_name"]:
        frappe.throw("工序名称未改变")
    if payload["ordering_name"] is not None:
        _name(payload["ordering_name"])
    return payload


def _rename_state(document, new_name):
    """Only native identity, own child parents and native self-Links may change."""
    expected = _state(document)
    expected["name"] = new_name
    for field in document.meta.fields:
        if (
            field.fieldtype == "Link"
            and field.options == NATIVE
            and expected.get(field.fieldname) == document.name
        ):
            expected[field.fieldname] = new_name
    for field in document.meta.get_table_fields():
        for row in expected[field.fieldname]:
            if row.get("parent") == document.name:
                row["parent"] = new_name
            for child in frappe.get_meta(field.options).fields:
                if (
                    child.fieldtype == "Link"
                    and child.options == NATIVE
                    and row.get(child.fieldname) == document.name
                ):
                    row[child.fieldname] = new_name
    return expected


def _guard_rename_references(operation):
    # Guard-only existence checks never return private source names. Native Link
    # rewrites would invalidate approved BOM and payroll content signatures.
    for doctype, field in (
        ("BOM Operation", "operation"),
        ("Job Card", "operation"),
        ("Job Card Time Log", "operation"),
        ("Job Card Operation", "sub_operation"),
    ):
        if frappe.db.get_value(doctype, {field: operation}, "name", for_update=True):
            frappe.throw(
                "工序已有 BOM 或报工引用，当前入口暂不支持改名；已保留原工序和历史依据"
            )


@frappe.whitelist(methods=["POST"])
@_atomic
def rename_operation(payload):
    _login()
    _read_access()
    payload = _rename_payload(payload)
    document = _native_write(payload["operation"])
    if not same_version(document.modified, payload["version"]):
        frappe.throw(CHANGED, frappe.TimestampMismatchError)
    ordering = _ordering(document.name, lock=True)
    if ordering:
        if ordering.name != payload["ordering_name"] or not same_version(
            ordering.modified, payload["ordering_version"]
        ):
            frappe.throw(CHANGED, frappe.TimestampMismatchError)
    elif (
        payload["ordering_name"] is not None or payload["ordering_version"] is not None
    ):
        frappe.throw(CHANGED, frappe.TimestampMismatchError)
    _write_field(NATIVE, "name")
    if not frappe.get_meta(NATIVE).allow_rename:
        frappe.throw("原生工序未允许改名", frappe.PermissionError)
    _guard_rename_references(document.name)
    expected = _rename_state(document, payload["new_name"])
    order_before = _state(ordering, exclude={"operation"}) if ordering else None
    # Native rename keeps modified timestamps, updates Link fields and emits its
    # own audit comment. Cache/search side effects are not SQL rollback claims.
    renamed = frappe.rename_doc(
        NATIVE, document.name, payload["new_name"], show_alert=False
    )
    if renamed != payload["new_name"]:
        frappe.throw("原生改名结果与确认名称不一致，已拒绝本次改名")
    fresh = frappe.get_doc(NATIVE, renamed, for_update=True)
    updated = _ordering(renamed, lock=True)
    if _state(fresh) != expected or not same_version(
        fresh.modified, payload["version"]
    ):
        frappe.throw("工序改名产生了未请求的其它属性变化，已拒绝本次改名")
    if ordering:
        if (
            not updated
            or updated.name != ordering.name
            or _state(updated, exclude={"operation"}) != order_before
            or not same_version(updated.modified, payload["ordering_version"])
        ):
            frappe.throw("工序排序及回执出现未请求的变化，已拒绝本次改名")
    elif updated:
        frappe.throw("工序改名出现了未请求的排序来源，已拒绝本次改名")
    return {
        "scope": SCOPE,
        "old_name": document.name,
        "new_name": renamed,
        "operation": _pack(fresh, updated),
    }


@frappe.whitelist()
def resolve_rename(payload):
    """Read a stable ordering identity; an unknown result never permits retry."""
    _login()
    _read_access()
    payload = _rename_payload(payload)
    result = {
        "scope": SCOPE,
        "old_name": payload["operation"],
        "new_name": payload["new_name"],
        "status": "unknown",
        "operation": None,
    }
    if payload["ordering_name"] is not None:
        ordering = frappe.get_doc(DOCTYPE, payload["ordering_name"])
        _projection(ordering)
        current = _pack(frappe.get_doc(NATIVE, ordering.operation), ordering)
        return result | {"status": "current", "operation": current}
    # An unmapped native name has no stable receipt. Never identify the renamed
    # source by looking up an independently existing target with the same name.
    try:
        current = read_operation(payload["operation"])
    except frappe.DoesNotExistError:
        return result
    return result | {"status": "current", "operation": current}


def _scan():
    rows, seen, start = [], set(), 0
    readable = _columns(NATIVE, "read")
    while True:
        listed = frappe.get_list(
            NATIVE,
            fields=[
                field
                for field in ("name", "modified", "description", *AUDIT)
                if field in readable
            ],
            order_by="name asc",
            limit_start=start,
            limit_page_length=BATCH_LENGTH,
        )
        for row in listed:
            if not NATIVE_REQUIRED.issubset(row):
                frappe.throw("没有读取工序完整来源投影的权限", frappe.PermissionError)
            if row["name"] in seen:
                frappe.throw(CHANGED)
            seen.add(row["name"])
            try:
                document = frappe.get_doc(NATIVE, row["name"])
            except frappe.DoesNotExistError:
                frappe.throw(CHANGED)
            _current(document, row, NATIVE_REQUIRED)
            if _quiet(document, "read"):
                rows.append(_pack(document, row=row))
        if len(listed) < BATCH_LENGTH:
            break
        start += len(listed)
    # Hidden sequence never influences sorting. Unmapped operations follow
    # visible numbered operations; ties and unnumbered rows use native names.
    return sorted(
        rows,
        key=lambda row: (
            row.get("sequence") is None,
            row.get("sequence") or 0,
            row["name"],
        ),
    )


@frappe.whitelist()
def list_operations(start=0, page_length=25):
    _login()
    _read_access()
    start = _integer(start, MAX_SAFE_INT, "分页起点")
    page_length = _integer(page_length, MAX_SAFE_INT, "分页条数")
    if page_length not in (25, 50, 100):
        frappe.throw("分页条数须为二十五、五十或一百")
    masks = (_columns(NATIVE, "read"), _columns(DOCTYPE, "read"))
    rows = _scan()
    # Two full serial native projections detect offset/source/permission drift;
    # they do not claim to provide an atomic database snapshot.
    if rows != _scan() or masks != (
        _columns(NATIVE, "read"),
        _columns(DOCTYPE, "read"),
    ):
        frappe.throw(CHANGED)
    total = len(rows)
    start = min(start, ((total - 1) // page_length) * page_length) if total else 0
    return {
        "scope": SCOPE,
        "rows": rows[start : start + page_length],
        "total": total,
        "start": start,
        "page_length": page_length,
    }


def _state(document, *, exclude=()):
    excluded = {"modified", "modified_by", *exclude}
    values = {
        field: deepcopy(document.get(field))
        for field in document.meta.get_valid_columns()
        if field not in excluded
    }
    for field in document.meta.get_table_fields():
        columns = frappe.get_meta(field.options).get_valid_columns()
        values[field.fieldname] = [
            {
                key: deepcopy(row.get(key))
                for key in columns
                if key not in {"modified", "modified_by"}
            }
            for row in document.get(field.fieldname) or []
        ]
    return values


def _native_write(operation):
    document = frappe.get_doc(NATIVE, operation, for_update=True)
    document.check_permission("read")
    document.check_permission("write")
    _projection(document)
    return document


def _server_order(document, native):
    document.__dict__["_jf_order_operation_server"] = (
        _SERVER,
        tuple(
            document.get(field)
            for field in ("operation", "sequence", "request_id", "request_digest")
        ),
        _version(native.modified),
    )


def before_insert_ordering(document):
    _login()
    marker = document.__dict__.get("_jf_order_operation_server")
    if not marker or marker[0] is not _SERVER:
        frappe.throw("工序排序及新建回执只能由共用工序入口建立")
    if marker[1] != tuple(
        document.get(field)
        for field in ("operation", "sequence", "request_id", "request_digest")
    ):
        frappe.throw("工序排序追溯身份不能指定或修改")
    document.check_permission("create")
    native = _native_write(_name(document.operation))
    if not same_version(native.modified, marker[2]):
        frappe.throw(CHANGED)
    _fields(DOCTYPE, {"operation"}, "write")
    _write_field(DOCTYPE, "sequence")
    document.sequence = _sequence(document.sequence)
    if document.request_id not in (None, ""):
        _uuid(document.request_id)
        if document.request_digest != _digest(
            {
                "operation": native.name,
                "sequence": document.sequence,
                "remarks": native.description,
            }
        ):
            frappe.throw("工序新建请求摘要不完整")
    elif document.request_digest not in (None, ""):
        frappe.throw("工序新建请求摘要不完整")


def validate_ordering(document):
    _login()
    _fields(DOCTYPE, ORDER_REQUIRED, "read")
    _native_write(_name(document.operation))
    document.sequence = _sequence(document.sequence)
    if document.is_new():
        before_insert_ordering(document)
    else:
        document.check_permission("write")
        before = document.get_doc_before_save()
        if not before or _state(before, exclude={"sequence"}) != _state(
            document, exclude={"sequence"}
        ):
            frappe.throw("工序关联、新建回执及其它排序属性不能修改")
        if before.sequence != document.sequence:
            _write_field(DOCTYPE, "sequence")


def prevent_delete(document):
    _login()
    frappe.throw("工序排序及新建回执不能删除，请保留核对")


def _receipt(request_id):
    _fields(DOCTYPE, ORDER_REQUIRED | RECEIPT, "read")
    fields = sorted(ORDER_REQUIRED | RECEIPT)
    listed = frappe.get_list(
        DOCTYPE, filters={"request_id": request_id}, fields=fields, limit_page_length=1
    )
    if not listed:
        return None
    document = frappe.get_doc(DOCTYPE, listed[0]["name"])
    _current(document, listed[0], ORDER_REQUIRED | RECEIPT)
    if document.request_id != request_id:
        frappe.throw(CHANGED)
    _projection(document, receipt=True)
    return document


@frappe.whitelist()
def resolve_creation(payload):
    """A missing receipt is unknown outcome evidence, never permission to retry."""
    _login()
    _read_access()
    values = _creation(payload)
    ordering = _receipt(values["request_id"])
    result = {
        "scope": SCOPE,
        "request_id": values["request_id"],
        "status": "missing",
        "operation": None,
    }
    if not ordering:
        return result
    if ordering.request_digest != _digest(values):
        return result | {"status": "conflict"}
    document = frappe.get_doc(NATIVE, ordering.operation)
    current = _pack(document, ordering)
    # Optional fields may be omitted in ordinary reads. Recovery needs every
    # compared value from the permission-filtered projection, never full-doc fill.
    if not {"remarks", "sequence"}.issubset(current):
        frappe.throw("没有读取工序完整核对字段的权限", frappe.PermissionError)
    changed = (current["name"], current["sequence"], current["remarks"]) != (
        values["operation"],
        values["sequence"],
        values["remarks"],
    )
    current["request_id"] = values["request_id"]
    return result | {"status": "changed" if changed else "found", "operation": current}


def _save_native(document, description, *, creation=False):
    document.description = description
    if (
        creation
        and document.is_new()
        and not document.get("sub_operations")
        and document.get("total_operation_time") is None
    ):
        # Native new_doc preserves this nullable Float as None. Operation's
        # validation sets the sum of an empty child table to exactly 0.0.
        # Normalize only that evidenced creation default before freezing it;
        # existing nullable/inconsistent totals still fail the strict guard.
        document.total_operation_time = 0.0
    excluded = {"creation", "owner"} if creation else set()
    protected = _state(document, exclude={"description", *excluded})
    document.save()
    fresh = frappe.get_doc(NATIVE, document.name, for_update=True)
    if (
        protected != _state(fresh, exclude={"description", *excluded})
        or fresh.description != description
        or not same_version(fresh.modified, _version(document.modified))
    ):
        frappe.throw("原生工序保存产生了未请求的其它属性变化，已拒绝本次保存")
    return fresh


def _save_ordering(document):
    excluded = {"name", "creation", "owner"} if document.is_new() else set()
    protected = _state(document, exclude=excluded)
    document.save()
    fresh = frappe.get_doc(DOCTYPE, document.name, for_update=True)
    if protected != _state(fresh, exclude=excluded) or not same_version(
        fresh.modified, _version(document.modified)
    ):
        frappe.throw("工序排序及回执出现未请求的变化，已拒绝本次保存")
    return fresh


@frappe.whitelist(methods=["POST"])
@_atomic
def save_operation(payload):
    _login()
    _read_access()
    # Presence, rather than a truthy UUID, selects the strict creation shape.
    raw = payload
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            frappe.throw("工序请求须为有效对象")
    creation = isinstance(raw, dict) and "request_id" in raw
    payload = _payload(raw, creation=creation)
    messages_before = list(getattr(frappe.local, "message_log", None) or [])
    collision = False
    try:
        if creation:
            values = _creation(payload)
            if _receipt(values["request_id"]):
                frappe.throw(COLLISION)
            _write_field(NATIVE, "name")
            _write_field(NATIVE, "description")
            _write_field(DOCTYPE, "sequence")
            _fields(DOCTYPE, {"operation"}, "write")
            document = frappe.new_doc(NATIVE)
            document.name = values["operation"]
            document.description = values["remarks"]
            document.check_permission("create")
            ordering = frappe.new_doc(DOCTYPE)
            ordering.operation = document.name
            ordering.sequence = values["sequence"]
            ordering.request_id = values["request_id"]
            ordering.request_digest = _digest(values)
            ordering.check_permission("create")
            document = _save_native(document, values["remarks"], creation=True)
            _server_order(ordering, document)
            ordering = _save_ordering(ordering)
        else:
            if not {"version", "ordering_name", "ordering_version"}.issubset(
                payload
            ) or not {"sequence", "remarks"}.intersection(payload):
                frappe.throw("修改工序须提供两个来源版本和要修改的字段")
            document = _native_write(payload["operation"])
            if not same_version(document.modified, payload["version"]):
                frappe.throw(CHANGED, frappe.TimestampMismatchError)
            ordering = _ordering(document.name, lock=True)
            if ordering:
                if ordering.name != payload["ordering_name"] or not same_version(
                    ordering.modified, payload["ordering_version"]
                ):
                    frappe.throw(CHANGED, frappe.TimestampMismatchError)
            elif (
                payload["ordering_name"] is not None
                or payload["ordering_version"] is not None
            ):
                frappe.throw(CHANGED, frappe.TimestampMismatchError)
            if "remarks" in payload:
                _write_field(NATIVE, "description")
                if "description" not in _projection(document):
                    frappe.throw(
                        "没有读取工序备注来源投影的权限", frappe.PermissionError
                    )
                description = _remarks(payload["remarks"], document.name)
                if description != document.description:
                    document = _save_native(document, description)
            if "sequence" in payload:
                _write_field(DOCTYPE, "sequence")
                sequence = _sequence(payload["sequence"])
                if ordering:
                    ordering.check_permission("write")
                    if "sequence" not in _projection(ordering):
                        frappe.throw(
                            "没有读取工序排序来源投影的权限", frappe.PermissionError
                        )
                    if sequence != ordering.sequence:
                        ordering.sequence = sequence
                        ordering = _save_ordering(ordering)
                else:
                    _fields(DOCTYPE, {"operation"}, "write")
                    ordering = frappe.new_doc(DOCTYPE)
                    ordering.operation, ordering.sequence = document.name, sequence
                    ordering.request_id, ordering.request_digest = None, None
                    ordering.check_permission("create")
                    _server_order(ordering, document)
                    ordering = _save_ordering(ordering)
        # Mapping callbacks must not change the native source either.
        fresh = frappe.get_doc(NATIVE, document.name, for_update=True)
        if _state(fresh) != _state(document) or not same_version(
            fresh.modified, _version(document.modified)
        ):
            frappe.throw("工序来源发生了未请求的变化，已拒绝本次保存")
        result = _pack(fresh, ordering)
        if creation:
            if not {"remarks", "sequence"}.issubset(result):
                frappe.throw("没有读取工序新建核对投影的权限", frappe.PermissionError)
            result["request_id"] = values["request_id"]
        return result
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        collision = True
    if collision:
        frappe.local.message_log = messages_before
        frappe.throw(COLLISION)
