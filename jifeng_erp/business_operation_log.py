"""Permission-scoped business operation history from persisted ERP facts."""

from datetime import datetime, time
import json

import frappe
from frappe.utils import getdate


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
MAX_ROWS_PER_SOURCE = 1000


def _scope(from_date, to_date, module, company):
    if frappe.session.user == "Guest":
        frappe.throw("请先登录后查看业务操作日志", frappe.PermissionError)
    if module not in MODULE_SOURCES:
        frappe.throw("请选择有业务来源的模块", frappe.ValidationError)
    try:
        start, end = getdate(from_date), getdate(to_date)
    except (TypeError, ValueError):
        frappe.throw("请选择有效的开始和结束日期", frappe.ValidationError)
    if start > end:
        frappe.throw("开始日期不能晚于结束日期", frappe.ValidationError)
    if (end - start).days >= MAX_DAYS:
        frappe.throw("查询跨度不能超过 366 天", frappe.ValidationError)
    if not frappe.has_permission("Company", "read"):
        frappe.throw("没有读取公司范围的权限", frappe.PermissionError)

    companies = frappe.get_list("Company", fields=["name"], order_by="name", limit_page_length=0)
    allowed = {row.name for row in companies}
    if company:
        if company not in allowed:
            frappe.throw("没有查看该公司业务日志的权限", frappe.PermissionError)
        selected = {company}
    else:
        selected = allowed
    if not selected:
        return start, end, set()
    # Company user-permission scope is checked explicitly, not inferred from a
    # submitted filter or from a Version/Comment row.
    for name in selected:
        frappe.get_doc("Company", name).check_permission("select")
    return start, end, selected


def _page_number(value, label, maximum):
    try:
        number = int(value or 1)
    except (TypeError, ValueError):
        frappe.throw(f"{label}必须是有效整数", frappe.ValidationError)
    if number < 1 or number > maximum:
        frappe.throw(f"{label}超出允许范围", frappe.ValidationError)
    return number


def _source_document(doctype, name, companies):
    """Revalidate the full source read and company scope before emitting a row."""
    try:
        doc = frappe.get_doc(doctype, name)
        doc.check_permission("read")
    except (frappe.PermissionError, frappe.DoesNotExistError):
        return None
    if not doc.meta.has_field("company") or doc.get("company") not in companies:
        return None
    return doc


def _version_action(data):
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
        if old == "0" and new == "1":
            return "提交", "原生版本记录确认状态从草稿变为已提交"
        if old == "1" and new == "2":
            return "取消", "原生版本记录确认状态从已提交变为已取消"
    if changed or payload.get("added") or payload.get("removed") or payload.get("row_changed"):
        return "修改", "原生版本记录确认单据内容发生变更"
    return None, None


def _date_filter(start, end):
    return ["between", [datetime.combine(start, time.min), datetime.combine(end, time.max)]]


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


def _read_created(module, doctypes, companies, start, end, document_name, target_count):
    events = []
    for doctype in doctypes:
        try:
            meta = frappe.get_meta(doctype)
            if not meta.has_field("company") or not frappe.has_permission(doctype, "read"):
                continue
            filters = {"company": ["in", sorted(companies)], "creation": _date_filter(start, end)}
            if document_name:
                filters["name"] = document_name
            rows = frappe.get_list(doctype, filters=filters, fields=["name", "owner", "creation", "company"],
                                   order_by="creation desc, name desc", limit_page_length=target_count)
        except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
            continue
        for row in rows:
            doc = _source_document(doctype, row.name, companies)
            if not doc:
                continue
            events.append(_event("create:" + doctype + ":" + row.name, doctype, doc,
                                 row.creation, row.owner, "创建", "单据创建时间及创建人来自原生记录", module))
    return events


def _read_versions(module, doctypes, companies, start, end, document_name, target_count):
    """Read bounded native Version facts, then expose only readable company sources.

    Version is a system timeline doctype and may not itself be readable by a
    business role. This bounded lookup is constrained to the requested module,
    date and optional exact voucher number; no Version fields are returned until
    its source document's native read permission and Company scope are rechecked.
    """
    events = []
    available = True
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
                    checked[version.docname] = _source_document(doctype, version.docname, companies)
                doc = checked[version.docname]
                if not doc:
                    continue
                action, note = _version_action(version.data)
                if action:
                    events.append(_event("version:" + version.name, doctype, doc,
                                         version.creation, version.owner, action, note, module))
                    source_events += 1
            offset += len(versions)
            if len(versions) < chunk_size:
                break
    return events, available


@frappe.whitelist(methods=["GET"])
def query_operation_log(from_date, to_date, module, document_name="", company=None, page=1, page_size=25):
    """Return a bounded, read-only timeline from permitted source documents."""
    start, end, companies = _scope(from_date, to_date, module, company)
    page = _page_number(page, "页码", MAX_PAGE)
    page_size = _page_number(page_size, "每页条数", MAX_PAGE_SIZE)
    document_name = str(document_name or "").strip()
    if len(document_name) > 140:
        frappe.throw("单号不能超过 140 个字符", frappe.ValidationError)
    if not companies:
        return {"rows": [], "page": page, "page_size": page_size, "has_more": False,
                "module": module, "history_available": True}

    doctypes = MODULE_SOURCES[module]
    target_count = min(page * page_size + 1, MAX_ROWS_PER_SOURCE)
    rows = _read_created(module, doctypes, companies, start, end, document_name, target_count)
    version_rows, history_available = _read_versions(
        module, doctypes, companies, start, end, document_name, target_count)
    rows.extend(version_rows)
    rows.sort(key=lambda row: (row["happened_at"], row["event_id"]), reverse=True)
    offset = (page - 1) * page_size
    page_rows = rows[offset:offset + page_size]
    return {"rows": page_rows, "page": page, "page_size": page_size,
            "has_more": len(rows) > offset + page_size, "module": module,
            "history_available": history_available}
