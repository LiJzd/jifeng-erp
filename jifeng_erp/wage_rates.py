"""Explicit, permission-filtered historical piece-rate choices; never defaults.

GETs neither lock nor write. The private reference resolver is called only after
the native target/source authorization and current Job Card locking. A requested
history lock is NOWAIT, so source cancellation cannot form a lock wait cycle.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json

import frappe
from frappe.model import get_permitted_fields
from frappe.model.document import Document

from jifeng_erp.archive_versions import same_version


SCOPE = "wage_piece_rates"
WAGE = "Jifeng Wage Entry"
BATCH_SIZE = 200
MAX_SAFE_INT = 9007199254740991
CONTEXT_FIELDS = {
    "company",
    "employee",
    "operation",
    "work_date",
    "stock_uom",
    "job_card",
    "source_modified",
    "work_order_modified",
}
WAGE_FIELDS = {
    "name",
    "modified",
    "docstatus",
    "company",
    "employee",
    "operation",
    "work_date",
    "stock_uom",
    "piece_rate",
    "quantity",
    "job_card",
    "work_order",
    "flow_order",
}
CARD_FIELDS = {
    "name",
    "modified",
    "docstatus",
    "company",
    "operation",
    "operation_id",
    "work_order",
    "stock_uom",
    "posting_date",
    "is_corrective_job_card",
}
WORK_FIELDS = {"name", "modified", "docstatus", "company", "jf_flow_order"}
EMPLOYEE_FIELDS = {"name", "modified", "company", "status", "employee_name"}
TARGET_FIELDS = {
    "company",
    "employee",
    "operation",
    "work_date",
    "stock_uom",
    "job_card",
    "piece_rate",
}
CHILD_TYPES = {
    ("Job Card", "time_logs"): "Job Card Time Log",
    ("Work Order", "operations"): "Work Order Operation",
}


class _Unavailable(Exception):
    """No private identity or native permission message escapes this classifier."""


class _Changed(Exception):
    pass


def _deny():
    frappe.throw(
        "没有读取或选择本次历史计件工价所需的权限或有效来源", frappe.PermissionError
    )


def _changed():
    frappe.throw("历史工价或当前报工来源已改变，请重新查询并核对")


def _name(value):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 140
    ):
        raise ValueError
    return value


def _stamp(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.isoformat()
    if not isinstance(value, str) or not same_version(value, value):
        raise ValueError
    return datetime.fromisoformat(value).isoformat()


def _day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if (
        not isinstance(value, str)
        or len(value) != 10
        or date.fromisoformat(value).isoformat() != value
    ):
        raise ValueError
    return value


def _amount(value):
    if (
        isinstance(value, bool)
        or value is None
        or not isinstance(value, (str, int, float, Decimal))
    ):
        raise ValueError
    text = str(value)
    if len(text) > 100 or not text or text != text.strip():
        raise ValueError
    try:
        result = Decimal(text)
    except InvalidOperation as error:
        raise ValueError from error
    # Currency comes from a finite native numeric column. Bound exponent
    # expansion as well as rejecting NaN/Infinity and negative values.
    if (
        not result.is_finite()
        or result < 0
        or abs(result.as_tuple().exponent) > 308
        or result.adjusted() > 308
    ):
        raise ValueError
    return "0" if not result else format(result, "f")


def _context(value):
    if frappe.session.user == "Guest":
        _deny()
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict) or set(value) != CONTEXT_FIELDS:
            raise ValueError
        result = dict(value)
        for field in ("company", "employee", "operation", "stock_uom", "job_card"):
            _name(result[field])
        result["work_date"] = _day(result["work_date"])
        if not isinstance(result["source_modified"], str) or not isinstance(
            result["work_order_modified"], str
        ):
            raise ValueError
        result["source_modified"] = _stamp(result["source_modified"])
        if result["work_order_modified"] != "":
            result["work_order_modified"] = _stamp(result["work_order_modified"])
        return result
    except (ValueError, TypeError, OverflowError):
        frappe.throw("历史工价查询上下文无效，请重新选择报工来源")


def _columns(doctype, permission="read", parenttype=None):
    columns = set(
        get_permitted_fields(
            doctype,
            parenttype=parenttype,
            permission_type=permission,
            ignore_virtual=True,
        )
    )
    if frappe.session.user != "Administrator":
        meta = frappe.get_meta(doctype)
        levels = set(
            meta.get_permlevel_access(
                permission_type="mask", parenttype=parenttype, user=frappe.session.user
            )
        )
        columns = {
            name
            for name in columns
            if not (
                (field := meta.get_field(name))
                and field.get("mask")
                and (field.permlevel or 0) not in levels
            )
        }
    return columns


def _fields(doctype, names, permission="read", parenttype=None):
    if not set(names).issubset(_columns(doctype, permission, parenttype)):
        raise _Unavailable


def _access(document, *permissions):
    if not all(
        frappe.has_permission(document.doctype, permission, doc=document, throw=False)
        for permission in permissions
    ):
        raise _Unavailable


def _load(doctype, name, *, for_update=False):
    try:
        return frappe.get_doc(doctype, name, for_update=for_update)
    except (frappe.DoesNotExistError, frappe.PermissionError) as error:
        raise _Unavailable from error


def _normal(field, value):
    if field == "modified":
        return _stamp(value)
    if field in {"work_date", "posting_date"}:
        return _day(value)
    if field in {"piece_rate", "quantity"}:
        return _amount(value)
    if field in {"work_order", "flow_order", "jf_flow_order"}:
        return value or ""
    return value


def _projection(document, fields):
    _access(document, "read")
    _fields(document.doctype, fields)
    rows = frappe.get_list(
        document.doctype,
        filters={"name": document.name},
        fields=sorted(fields),
        limit_page_length=1,
    )
    if not rows or len(rows) != 1 or not fields.issubset(rows[0]):
        raise _Unavailable
    try:
        row = {field: _normal(field, rows[0][field]) for field in fields}
        actual = {field: _normal(field, document.get(field)) for field in fields}
    except (ValueError, TypeError, OverflowError) as error:
        raise _Unavailable from error
    if actual != row:
        raise _Changed
    _access(document, "read")
    _fields(document.doctype, fields)
    return row


def _children(document, table, value_field):
    field = document.meta.get_field(table)
    child_type = CHILD_TYPES[document.doctype, table]
    levels = set(document.meta.get_permlevel_access(permission_type="read"))
    if 0 not in levels and frappe.share.get_shared(
        document.doctype, frappe.session.user, rights=["read"], limit=1
    ):
        levels.add(0)
    masked = (
        field
        and field.get("mask")
        and (field.permlevel or 0)
        not in set(document.meta.get_permlevel_access(permission_type="mask"))
    )
    if (
        not field
        or field.fieldtype != "Table"
        or field.options != child_type
        or (field.permlevel or 0) not in levels
        or masked
    ):
        raise _Unavailable
    fields = {"name", "parent", "parenttype", "parentfield", value_field}
    _fields(child_type, fields, parenttype=document.doctype)
    rows = []
    while True:
        batch = frappe.get_list(
            child_type,
            parent_doctype=document.doctype,
            filters={
                "parent": document.name,
                "parenttype": document.doctype,
                "parentfield": table,
            },
            fields=sorted(fields),
            order_by="name asc",
            limit_start=len(rows),
            limit_page_length=BATCH_SIZE,
        )
        if any(not fields.issubset(row) for row in batch):
            raise _Unavailable
        rows.extend(dict(row) for row in batch)
        if len(batch) < BATCH_SIZE:
            break
    actual = [
        {name: child.get(name) for name in fields} for child in document.get(table)
    ]
    actual.sort(key=lambda row: row["name"])
    if rows != actual or len({row["name"] for row in rows}) != len(rows):
        raise _Unavailable
    _fields(child_type, fields, parenttype=document.doctype)
    return rows


def _link(doctype, name):
    document = _load(doctype, name)
    _access(document, "select")
    if document.name != name:
        raise _Unavailable


def _person(company, employee):
    _link("Company", company)
    person = _projection(_load("Employee", employee), EMPLOYEE_FIELDS)
    if person["company"] != company or person["status"] != "Active":
        raise _Unavailable
    return person


def _source(job_card, company, employee):
    """Same native source invariants as wage_workbench, with quiet projections."""
    card_doc = _load("Job Card", job_card)
    card = _projection(card_doc, CARD_FIELDS)
    logs = _children(card_doc, "time_logs", "employee")
    if (
        card["docstatus"] != 1
        or card["company"] != company
        or card["is_corrective_job_card"]
        or not any(row["employee"] == employee for row in logs)
    ):
        raise _Unavailable
    work, operations, flow = None, [], None
    if card["work_order"]:
        work_doc = _load("Work Order", card["work_order"])
        work = _projection(work_doc, WORK_FIELDS)
        operations = _children(work_doc, "operations", "operation")
        if (
            work["docstatus"] != 1
            or work["company"] != company
            or not any(
                row["name"] == card["operation_id"]
                and row["operation"] == card["operation"]
                for row in operations
            )
        ):
            raise _Unavailable
        if work["jf_flow_order"]:
            flow = _projection(
                _load("Jifeng Flow Order", work["jf_flow_order"]),
                {"name", "modified", "company"},
            )
            if flow["company"] != company:
                raise _Unavailable
    _link("Operation", card["operation"])
    _link("UOM", card["stock_uom"])
    return {
        "card": card,
        "logs": logs,
        "work": work,
        "operations": operations,
        "flow": flow,
    }


def _scope(context, *, new_target):
    person = _person(context["company"], context["employee"])
    source = _source(context["job_card"], context["company"], context["employee"])
    card, work = source["card"], source["work"]
    if (
        card["operation"] != context["operation"]
        or card["stock_uom"] != context["stock_uom"]
    ):
        raise _Unavailable
    if (
        card["modified"] != context["source_modified"]
        or (work["modified"] if work else "") != context["work_order_modified"]
    ):
        raise _Changed
    if new_target:
        document = frappe.new_doc(WAGE)
        for field in TARGET_FIELDS - {"piece_rate"}:
            document.set(field, context[field])
        document.docstatus = 0
        _access(document, "create", "write")
        _fields(WAGE, TARGET_FIELDS)
        _fields(WAGE, TARGET_FIELDS, "write")
        for name in TARGET_FIELDS:
            field = document.meta.get_field(name)
            if not field or field.read_only or field.get("read_only_depends_on"):
                raise _Unavailable
        _access(document, "create", "write")
    _fields(WAGE, WAGE_FIELDS)
    if not frappe.has_permission(WAGE, "read", throw=False):
        raise _Unavailable
    return {"person": person, "source": source}


def _filters(context):
    return {
        "company": context["company"],
        "employee": context["employee"],
        "operation": context["operation"],
        "stock_uom": context["stock_uom"],
        "docstatus": 1,
        "quantity": [">", 0],
        "job_card": ["!=", ""],
        "work_date": ["<=", context["work_date"]],
    }


def _candidate(context, name, *, for_update=False):
    document = _load(WAGE, name, for_update=for_update)
    row = _projection(document, WAGE_FIELDS)
    if (
        row["docstatus"] != 1
        or not row["job_card"]
        or Decimal(row["quantity"]) <= 0
        or row["work_date"] > context["work_date"]
        or any(
            row[field] != context[field]
            for field in ("company", "employee", "operation", "stock_uom")
        )
    ):
        raise _Unavailable
    proof = _source(row["job_card"], row["company"], row["employee"])
    card, work, flow = proof["card"], proof["work"], proof["flow"]
    if (
        card["operation"] != row["operation"]
        or card["stock_uom"] != row["stock_uom"]
        or row["work_order"] != (work["name"] if work else "")
        or row["flow_order"] != (flow["name"] if flow else "")
    ):
        raise _Unavailable
    dto = {
        field: row[field]
        for field in (
            "name",
            "work_date",
            "stock_uom",
            "piece_rate",
            "company",
            "employee",
            "operation",
        )
    }
    dto["version"] = row["modified"]
    return {"row": dto, "wage": row, "source": proof}


def _scan(context):
    rows, seen = [], set()
    while True:
        batch = frappe.get_list(
            WAGE,
            filters=_filters(context),
            fields=sorted(WAGE_FIELDS),
            order_by="work_date desc, name asc",
            limit_start=len(rows),
            limit_page_length=BATCH_SIZE,
        )
        for row in batch:
            if not WAGE_FIELDS.issubset(row) or row["name"] in seen:
                raise _Changed
            seen.add(row["name"])
            try:
                candidate = _candidate(context, row["name"])
                listed = {field: _normal(field, row[field]) for field in WAGE_FIELDS}
                if candidate["wage"] != listed:
                    raise _Changed
            except _Unavailable:
                candidate = None
            rows.append({"name": row["name"], "candidate": candidate})
        if len(batch) < BATCH_SIZE:
            return rows


def _integer(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError
    if isinstance(value, str) and (
        not value.isascii() or not value.isdecimal() or len(value) > 16
    ):
        raise ValueError
    value = int(value)
    if not 1 <= value <= maximum:
        raise ValueError
    return value


@frappe.whitelist(methods=["GET"])
def query_piece_rates(context, page=1, page_size=25):
    """All selectable history is rechecked before slicing a stable result page."""
    context = _context(context)
    try:
        page, page_size = _integer(page, MAX_SAFE_INT), _integer(page_size, 100)
    except ValueError:
        frappe.throw("历史工价页码或每页数量无效")
    try:
        before = _scope(context, new_target=True)
        first = _scan(context)
        if _scope(context, new_target=True) != before:
            raise _Changed
        second = _scan(context)
        if first != second or _scope(context, new_target=True) != before:
            raise _Changed
        rows = [item["candidate"]["row"] for item in second if item["candidate"]]
        start = (page - 1) * page_size
        return {
            "scope": SCOPE,
            "readonly": True,
            "page": page,
            "page_size": page_size,
            "total": len(rows),
            "total_pages": (len(rows) + page_size - 1) // page_size,
            "rows": rows[start : start + page_size],
        }
    except _Unavailable:
        _deny()
    except _Changed:
        _changed()


def _expected(source, version, piece_rate):
    try:
        if not isinstance(version, str):
            raise ValueError
        return _name(source), _stamp(version), _amount(piece_rate)
    except (ValueError, TypeError, OverflowError):
        frappe.throw("请选择准确的历史计件工价、完整版本和工价")


def _choose(context, source, version, piece_rate, *, for_update=False):
    filters = {**_filters(context), "name": source}
    rows = frappe.get_list(
        WAGE, filters=filters, fields=sorted(WAGE_FIELDS), limit_page_length=1
    )
    if len(rows) != 1 or not WAGE_FIELDS.issubset(rows[0]):
        raise _Unavailable
    # Quiet permission/field/user-permission checks precede any source lock.
    before = _projection(_load(WAGE, source), WAGE_FIELDS)
    if before != {field: _normal(field, rows[0][field]) for field in WAGE_FIELDS}:
        raise _Changed
    if before["modified"] != version or Decimal(before["piece_rate"]) != Decimal(
        piece_rate
    ):
        raise _Changed
    if for_update:
        locked = frappe.db.sql(
            "SELECT name FROM `tabJifeng Wage Entry` WHERE name=%s FOR UPDATE NOWAIT",
            (source,),
        )
        if len(locked) != 1 or locked[0][0] != source:
            raise _Changed
    first = _candidate(context, source, for_update=for_update)
    second = _candidate(context, source, for_update=for_update)
    if first != second or second["wage"] != before:
        raise _Changed
    return {"scope": SCOPE, "readonly": True, **second["row"]}


@frappe.whitelist(methods=["GET"])
def choose_piece_rate(context, source, version, piece_rate):
    context = _context(context)
    source, version, piece_rate = _expected(source, version, piece_rate)
    try:
        before = _scope(context, new_target=True)
        result = _choose(context, source, version, piece_rate)
        if _scope(context, new_target=True) != before:
            raise _Changed
        return result
    except _Unavailable:
        _deny()
    except _Changed:
        _changed()


def resolve_reference(document, source, version, piece_rate, for_update=False):
    """Validate native-authorized targets without imposing new-record authority.

    The caller has already authorized the actual document and validated/locked
    its current Job Card via validate_wage_source. No caller-supplied permission
    flag is accepted. Locked source conflicts propagate; there is no retry.
    """
    if (
        not isinstance(document, Document)
        or document.doctype != WAGE
        or frappe.session.user == "Guest"
        or not isinstance(for_update, bool)
    ):
        _deny()
    source, version, piece_rate = _expected(source, version, piece_rate)
    try:
        context = {
            field: document.get(field)
            for field in CONTEXT_FIELDS - {"source_modified", "work_order_modified"}
        }
        for field in ("company", "employee", "operation", "stock_uom", "job_card"):
            _name(context[field])
        context["work_date"] = _day(context["work_date"])
        current = _source(context["job_card"], context["company"], context["employee"])
        context["source_modified"] = current["card"]["modified"]
        context["work_order_modified"] = (
            current["work"]["modified"] if current["work"] else ""
        )
        before = _scope(context, new_target=False)
        result = _choose(context, source, version, piece_rate, for_update=for_update)
        if _scope(context, new_target=False) != before:
            raise _Changed
        return result
    except (ValueError, TypeError, OverflowError, _Unavailable):
        _deny()
    except _Changed:
        _changed()
