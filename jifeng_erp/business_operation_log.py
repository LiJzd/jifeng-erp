"""Permission-scoped business operation history from persisted ERP facts."""

import json

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import get_datetime


# Deliberately limited to company-owned business documents. Sensitive account,
# access-control and server audit doctypes are not business operation sources.
MODULE_SOURCES = {
    "物料": ("Material Request", "Purchase Order", "Purchase Receipt", "Stock Entry", "Stock Reconciliation"),
    "材料": ("Material Request", "Purchase Order", "Purchase Receipt", "Stock Entry", "Stock Reconciliation"),
    "产品": ("Jifeng Product Profile", "Jifeng Sample Order", "Quotation"),
    "订单": ("Jifeng External Order", "Sales Order", "Jifeng Flow Order"),
    "生产": ("Production Plan", "Work Order", "Job Card", "Stock Entry"),
    "人事": ("Employee", "Employee Promotion", "Employee Transfer", "Employee Separation"),
    "工资": ("Jifeng Wage Entry", "Jifeng Wage Allocation", "Additional Salary",
            "Salary Structure Assignment", "Salary Slip", "Payroll Entry"),
    "模具": ("Jifeng Mold",),
    "版种": ("Jifeng Plate", "Jifeng Plate Modeling"),
    "发镀": ("Purchase Order", "Subcontracting Order", "Subcontracting Receipt", "Quality Inspection"),
    "外发": ("Purchase Order", "Subcontracting Order", "Subcontracting Receipt", "Quality Inspection"),
    "销售": ("Sales Order", "Delivery Note", "Sales Invoice", "POS Invoice", "Jifeng Cost Preview"),
    "财务": ("Payment Entry", "Journal Entry", "Purchase Invoice", "Sales Invoice", "Jifeng Expense Voucher"),
}
MODULE_SOURCES["总经理"] = tuple(dict.fromkeys(
    doctype for module, doctypes in MODULE_SOURCES.items() for doctype in doctypes
))

MAX_DAYS = 366
MAX_PAGE = 20
MAX_PAGE_SIZE = 50
CHUNK_SIZE = 100
MAX_ROWS_PER_SOURCE = 2000
ALLOWED_ACTIONS = {"创建", "修改", "提交", "取消"}


def _parse_datetime(value, label, *, end=False):
    if not isinstance(value, str) or len(value.strip()) < 16:
        frappe.throw(f"{label}必须包含日期和时间", frappe.ValidationError)
    try:
        parsed = get_datetime(value.strip())
    except (TypeError, ValueError):
        frappe.throw(f"{label}不是有效的日期时间", frappe.ValidationError)
    if end and len(value.strip()) == 16:
        parsed = parsed.replace(second=59, microsecond=999999)
    return parsed


def _scope(from_date, to_date, module, company):
    if frappe.session.user == "Guest":
        frappe.throw("请先登录后查看业务操作日志", frappe.PermissionError)
    if module not in MODULE_SOURCES:
        frappe.throw("请选择有业务来源的模块", frappe.ValidationError)
    start = _parse_datetime(from_date, "开始时间")
    end = _parse_datetime(to_date, "结束时间", end=True)
    if start > end:
        frappe.throw("开始时间不能晚于结束时间", frappe.ValidationError)
    if (end - start).total_seconds() > MAX_DAYS * 86400:
        frappe.throw("查询跨度不能超过 366 天", frappe.ValidationError)
    if not frappe.has_permission("Company", "read"):
        frappe.throw("没有读取公司范围的权限", frappe.PermissionError)

    companies = frappe.get_list("Company", fields=["name"], order_by="name", limit_page_length=0)
    allowed = set()
    for row in companies:
        company_doc = frappe.get_doc("Company", row.name)
        if frappe.has_permission("Company", "select", doc=company_doc, throw=False):
            allowed.add(row.name)
    if company:
        if company not in allowed:
            frappe.throw("没有查看该公司业务日志的权限", frappe.PermissionError)
        selected = {company}
    else:
        selected = allowed
    return start, end, selected


def _page_number(value, label, maximum):
    try:
        number = int(value or 1)
    except (TypeError, ValueError):
        frappe.throw(f"{label}必须是有效整数", frappe.ValidationError)
    if number < 1 or number > maximum:
        frappe.throw(f"{label}超出允许范围", frappe.ValidationError)
    return number


def _readable_fields(doctype, cache):
    """Return the native read-permitted, unmasked field projection."""
    if doctype not in cache:
        permitted = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
        meta = frappe.get_meta(doctype)
        if frappe.session.user != "Administrator":
            mask_levels = set(meta.get_permlevel_access(permission_type="mask", user=frappe.session.user))
            permitted -= {
                field.fieldname for field in meta.fields
                if field.get("mask") and (field.permlevel or 0) not in mask_levels
            }
        cache[doctype] = permitted
    return cache[doctype]


def _source_document(doctype, name, companies, fields_cache):
    """Revalidate source read and field-level access before emitting a row."""
    try:
        readable = _readable_fields(doctype, fields_cache)
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        return None
    if not frappe.get_meta(doctype).has_field("company") or not {"name", "company"}.issubset(readable):
        return None
    try:
        # Company is filtered before loading/checking the source document so
        # foreign-company identities cannot enter permission error messages.
        rows = frappe.get_list(doctype, filters={"name": name, "company": ["in", sorted(companies)]},
                               fields=["name", "company"], limit_page_length=1)
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        return None
    if len(rows) != 1 or rows[0].company not in companies:
        return None
    try:
        doc = frappe.get_doc(doctype, name)
    except (frappe.DoesNotExistError, frappe.ValidationError):
        return None
    # check_permission() logs hidden identifiers in _server_messages on some
    # Frappe versions. A quiet native check avoids exposing denied Version refs.
    if not frappe.has_permission(doctype, "read", doc=doc, throw=False):
        return None
    return rows[0]


def _version_action(data, allow_status_change=True):
    """Return only actions proven by the native Version payload, never field values."""
    try:
        payload = json.loads(data or "{}") if isinstance(data, str) else data or {}
    except (TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    changed = payload.get("changed") or []
    for item in changed:
        if isinstance(item, dict):
            field = item.get("field")
            old, new = item.get("old_value"), item.get("new_value")
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            field, old, new = item[0], item[1], item[2]
        else:
            continue
        if field != "docstatus":
            continue
        old, new = str(old), str(new)
        if not allow_status_change:
            # Do not mislabel a hidden status transition as a generic edit.
            if len(changed) == 1:
                return None, None
            continue
        if old == "0" and new == "1":
            return "提交", "原生版本记录确认状态从草稿变为已提交"
        if old == "1" and new == "2":
            return "取消", "原生版本记录确认状态从已提交变为已取消"
    if changed or payload.get("added") or payload.get("removed") or payload.get("row_changed"):
        return "修改", "原生版本记录确认单据内容发生变更"
    return None, None


def _date_filter(start, end):
    return ["between", [start, end]]


def _event(event_id, doctype, doc, happened_at, actor, action, note, module):
    return {
        "event_id": event_id,
        "source_doctype": doctype,
        "source_name": doc.name,
        "happened_at": str(happened_at),
        "actor": actor or "未记录",
        "action": action,
        "module": module,
        "note": note,
    }


def _matches_actor(actor, operator):
    return not operator or operator.casefold() in str(actor or "").casefold()


def _read_created(module, doctypes, companies, start, end, document_name, operator, action,
                  target_count, fields_cache):
    events = []
    if action and action != "创建":
        return events
    for doctype in doctypes:
        try:
            meta = frappe.get_meta(doctype)
            required = {"name", "company", "owner", "creation"}
            readable = _readable_fields(doctype, fields_cache)
            if (not meta.has_field("company") or not required.issubset(readable)
                    or not frappe.has_permission(doctype, "read", throw=False)):
                continue
            filters = {"company": ["in", sorted(companies)], "creation": _date_filter(start, end)}
            if document_name:
                filters["name"] = document_name
            if operator:
                filters["owner"] = ["like", f"%{operator}%"]
            rows = frappe.get_list(doctype, filters=filters, fields=sorted(required),
                                   order_by="creation desc, name desc", limit_page_length=target_count)
        except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
            continue
        for row in rows:
            if not _matches_actor(row.owner, operator):
                continue
            source = _source_document(doctype, row.name, companies, fields_cache)
            if not source:
                continue
            events.append(_event("create:" + doctype + ":" + row.name, doctype, source,
                                 row.creation, row.owner, "创建", "单据创建时间及创建人来自原生记录", module))
    return events


def _read_versions(module, doctypes, companies, start, end, document_name, operator, action,
                   target_count, fields_cache):
    """Read bounded timeline facts only through readable parent documents.

    Frappe's native document timeline is scoped by permission to its parent; it
    does not require a business user to have global Version read permission.
    Version payload fields are used only internally to classify an event. Each
    referenced parent is rechecked for read permission and company scope before
    any event is returned, and source docstatus must be readable before status
    transitions are named.
    """
    events = []
    action_filter = action
    if action_filter == "创建":
        return events, True, False
    available = True
    truncated = False
    for doctype in doctypes:
        checked = {}
        source_events = 0
        offset = 0
        filters = {
            "ref_doctype": doctype,
            "creation": _date_filter(start, end),
            **({"docname": document_name} if document_name else {}),
        }
        while offset < MAX_ROWS_PER_SOURCE and source_events < target_count:
            chunk_size = min(CHUNK_SIZE, MAX_ROWS_PER_SOURCE - offset)
            try:
                versions = frappe.get_all("Version", filters=filters,
                    fields=["name", "docname", "owner", "creation", "data"],
                    order_by="creation desc, name desc", limit_start=offset, limit_page_length=chunk_size)
            except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
                available = False
                break
            for version in versions:
                if version.docname not in checked:
                    source = _source_document(doctype, version.docname, companies, fields_cache)
                    checked[version.docname] = (source, source is not None and
                                                "docstatus" in _readable_fields(doctype, fields_cache))
                source, status_readable = checked[version.docname]
                if not source:
                    continue
                event_action, note = _version_action(version.data, allow_status_change=status_readable)
                if (event_action and (not action_filter or event_action == action_filter)
                        and _matches_actor(version.owner, operator)):
                    events.append(_event("version:" + version.name, doctype, source,
                                         version.creation, version.owner, event_action, note, module))
                    source_events += 1
            offset += len(versions)
            if len(versions) < chunk_size:
                break
            if offset >= MAX_ROWS_PER_SOURCE and len(versions) == chunk_size and source_events < target_count:
                truncated = True
    return events, available, truncated


@frappe.whitelist(methods=["GET"])
def query_operation_log(from_date, to_date, module, document_name="", operator="", action="",
                       company=None, page=1, page_size=25):
    """Return a bounded, read-only timeline from permitted source documents."""
    start, end, companies = _scope(from_date, to_date, module, company)
    page = _page_number(page, "页码", MAX_PAGE)
    page_size = _page_number(page_size, "每页条数", MAX_PAGE_SIZE)
    document_name = str(document_name or "").strip()
    if len(document_name) > 140:
        frappe.throw("单号不能超过 140 个字符", frappe.ValidationError)
    operator = str(operator or "").strip()
    if len(operator) > 140:
        frappe.throw("操作人筛选不能超过 140 个字符", frappe.ValidationError)
    action = str(action or "").strip()
    if action and action not in ALLOWED_ACTIONS:
        frappe.throw("请选择有效的操作内容", frappe.ValidationError)
    if not companies:
        return {"rows": [], "page": page, "page_size": page_size, "has_more": False,
                "module": module, "history_available": True, "truncated": False}

    doctypes = MODULE_SOURCES[module]
    target_count = min(page * page_size + 1, MAX_ROWS_PER_SOURCE)
    fields_cache = {}
    rows = _read_created(module, doctypes, companies, start, end, document_name, operator, action,
                         target_count, fields_cache)
    version_rows, history_available, truncated = _read_versions(
        module, doctypes, companies, start, end, document_name, operator, action, target_count, fields_cache)
    rows.extend(version_rows)
    rows.sort(key=lambda row: (row["happened_at"], row["event_id"]), reverse=True)
    offset = (page - 1) * page_size
    page_rows = rows[offset:offset + page_size]
    return {"rows": page_rows, "page": page, "page_size": page_size,
            "has_more": len(rows) > offset + page_size, "module": module,
            "history_available": history_available, "truncated": truncated}
