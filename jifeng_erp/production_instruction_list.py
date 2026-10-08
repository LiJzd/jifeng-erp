"""Permission-scoped list data for the native Production Plan instruction page."""

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate


DOCTYPE = "Production Plan"
CHILD_DOCTYPE = "Production Plan Item"
MAX_DAYS = 366
MAX_PAGE_SIZE = 100

PARENT_FIELDS = {
    "name", "docstatus", "company", "posting_date", "modified", "modified_by",
    "jf_instruction_no", "jf_instruction_date", "jf_instruction_amount",
    "jf_instruction_currency", "jf_instruction_remark",
}
CHILD_FIELDS = {"item_code", "planned_qty", "stock_uom"}


def _fail(message, error=None):
    frappe.throw(message, error or frappe.ValidationError)


def _readable_fields(doctype, parenttype=None):
    fields = set(get_permitted_fields(
        doctype, parenttype=parenttype, permission_type="read", ignore_virtual=True
    ))
    if frappe.session.user == "Administrator":
        return fields
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access(
        permission_type="mask", parenttype=parenttype, user=frappe.session.user
    ))
    return fields - {
        field.fieldname for field in meta.fields
        if field.get("mask") and (field.permlevel or 0) not in levels
    }


def _require_fields(doctype, required, parenttype=None):
    if not set(required).issubset(_readable_fields(doctype, parenttype)):
        _fail("没有读取生产指令完整列表所需字段的权限", frappe.PermissionError)


def _page_int(value, label, maximum):
    try:
        number = int(value or 1)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{label}无效")
    if number < 1 or number > maximum:
        _fail(f"{label}超出允许范围")
    return number


def _date(value, label):
    try:
        return getdate(value)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{label}无效")


def _validate_table():
    meta = frappe.get_meta(DOCTYPE)
    field = meta.get_field("po_items")
    if not field or field.fieldtype != "Table" or field.options != CHILD_DOCTYPE:
        _fail("生产计划明细结构不可用")
    if frappe.session.user != "Administrator":
        read_levels = set(meta.get_permlevel_access(
            permission_type="read", parenttype=None, user=frappe.session.user
        ))
        if (field.permlevel or 0) not in read_levels:
            _fail("没有读取生产指令明细的权限", frappe.PermissionError)
        levels = set(meta.get_permlevel_access(
            permission_type="mask", parenttype=None, user=frappe.session.user
        ))
        if field.get("mask") and (field.permlevel or 0) not in levels:
            _fail("没有读取生产指令明细的权限", frappe.PermissionError)


def _quantity_groups(items):
    totals = {}
    styles = set()
    for item in items or []:
        code = item.get("item_code")
        unit = item.get("stock_uom")
        if not code or not unit:
            _fail("生产计划明细缺少商品或库存单位，无法准确汇总")
        styles.add(code)
        totals[unit] = totals.get(unit, 0) + (item.get("planned_qty") or 0)
    return ([{"stock_uom": unit, "qty": qty} for unit, qty in sorted(totals.items())], len(styles))


@frappe.whitelist(methods=["GET"])
def list_production_instructions(company, from_date, to_date, exact_date=None,
                                 search="", page=1, page_size=50):
    """List readable native plans; the amount is explicitly only a reference amount."""
    if frappe.session.user == "Guest":
        _fail("请先登录后查看生产指令", frappe.PermissionError)
    if not frappe.has_permission(DOCTYPE, "read"):
        _fail("没有生产计划读取权限", frappe.PermissionError)
    if not company or not frappe.get_list(
        "Company", filters={"name": company}, pluck="name", limit_page_length=1
    ):
        _fail("没有查看该公司的生产计划权限", frappe.PermissionError)

    start, end = _date(from_date, "开始日期"), _date(to_date, "结束日期")
    if start > end or (end - start).days > MAX_DAYS:
        _fail("日期范围无效或超过 366 天")
    exact = _date(exact_date, "指定日期") if exact_date else None
    if exact and not start <= exact <= end:
        _fail("指定日期必须位于起止日期范围内")
    if not isinstance(search, str) or len(search) > 140 or any(ord(char) < 32 for char in search):
        _fail("关键词无效")
    page = _page_int(page, "页码", 100000)
    page_size = _page_int(page_size, "每页条数", MAX_PAGE_SIZE)

    _require_fields(DOCTYPE, PARENT_FIELDS)
    _validate_table()
    _require_fields(CHILD_DOCTYPE, CHILD_FIELDS, parenttype=DOCTYPE)

    filters = {"company": company, "posting_date": ["=", str(exact)] if exact else ["between", [str(start), str(end)]]}
    options = {}
    search = search.strip()
    if search:
        pattern = f"%{search}%"
        options["or_filters"] = [
            [DOCTYPE, "name", "like", pattern],
            [DOCTYPE, "jf_instruction_no", "like", pattern],
            [DOCTYPE, "jf_instruction_remark", "like", pattern],
        ]
    selected = frappe.get_list(
        DOCTYPE, filters=filters,
        fields=sorted(PARENT_FIELDS),
        order_by="posting_date desc, name desc",
        limit_start=(page - 1) * page_size,
        limit_page_length=page_size + 1,
        **options,
    )
    has_more = len(selected) > page_size
    rows = []
    for projection in selected[:page_size]:
        doc = frappe.get_doc(DOCTYPE, projection.name)
        if not frappe.has_permission(DOCTYPE, "read", doc=doc, throw=False):
            continue
        if doc.company != company:
            continue
        groups, styles = _quantity_groups(doc.get("po_items"))
        rows.append({
            "name": doc.name,
            "docstatus": doc.docstatus,
            "posting_date": str(doc.posting_date) if doc.posting_date else "",
            "instruction_no": doc.get("jf_instruction_no") or "",
            "instruction_date": str(doc.get("jf_instruction_date")) if doc.get("jf_instruction_date") else "",
            "quantity_groups": groups,
            "style_count": styles,
            "reference_amount": doc.get("jf_instruction_amount"),
            "currency": doc.get("jf_instruction_currency") or "",
            "remark": doc.get("jf_instruction_remark") or "",
            "modified": str(doc.modified) if doc.modified else "",
            "modified_by": doc.modified_by or "",
        })
    return {
        "rows": rows, "page": page, "page_size": page_size,
        "has_more": has_more,
        "amount_label": "参考金额",
        "amount_note": "指令记录字段，不代表库存估值或实际成本。",
        "style_count_note": "按不同商品编号计数；旧系统“款数”口径未核实。",
    }
