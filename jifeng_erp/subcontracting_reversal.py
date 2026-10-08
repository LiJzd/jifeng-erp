"""Permission-scoped lookup for native subcontracting and inspection reversals."""

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate


MAX_DAYS = 366
MAX_PAGE_SIZE = 100
PROFILE = "Jifeng Partner Profile"
SOURCES = {
    "plating_order": {
        "doctype": "Subcontracting Order", "date_field": "transaction_date",
        "archive_kind": "电镀厂",
        "fields": {"name", "company", "docstatus", "transaction_date", "status", "supplier",
                   "supplier_name", "total_qty", "purchase_order"},
        "search": ("name", "supplier", "supplier_name", "purchase_order"),
    },
    "outer_order": {
        "doctype": "Subcontracting Order", "date_field": "transaction_date",
        "archive_kind": "外发工厂",
        "fields": {"name", "company", "docstatus", "transaction_date", "status", "supplier",
                   "supplier_name", "total_qty", "purchase_order"},
        "search": ("name", "supplier", "supplier_name", "purchase_order"),
    },
    "outer_receipt": {
        "doctype": "Subcontracting Receipt", "date_field": "posting_date",
        "archive_kind": "外发工厂",
        "fields": {"name", "company", "docstatus", "posting_date", "status", "supplier",
                   "supplier_name", "total_qty", "is_return", "return_against"},
        "search": ("name", "supplier", "supplier_name", "return_against"),
    },
    "plating_inspection": {
        "doctype": "Quality Inspection", "date_field": "report_date",
        "archive_kind": "电镀厂",
        "fields": {"name", "company", "docstatus", "report_date", "status", "inspection_type",
                   "reference_type", "reference_name", "item_code", "item_name"},
        "search": ("name", "reference_name", "item_code", "item_name"),
    },
}
STATUS_FILTERS = {"submitted", "cancelled", "all"}


def _fail(message, error=None):
    frappe.throw(message, error or frappe.ValidationError)


def _readable_fields(doctype):
    fields = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
    if frappe.session.user == "Administrator":
        return fields
    meta = frappe.get_meta(doctype)
    mask_levels = set(meta.get_permlevel_access(
        permission_type="mask", parenttype=None, user=frappe.session.user
    ))
    return fields - {
        field.fieldname for field in meta.fields
        if field.get("mask") and (field.permlevel or 0) not in mask_levels
    }


def _date(value, label):
    try:
        return getdate(value)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{label}无效")


def _number(value, label, maximum):
    try:
        parsed = int(value or 1)
    except (TypeError, ValueError, OverflowError):
        _fail(f"{label}无效")
    if parsed < 1 or parsed > maximum:
        _fail(f"{label}超出允许范围")
    return parsed


def _partner_class(company, supplier):
    """Classify only through one readable, unambiguous company archive binding."""
    fields = {"name", "company", "supplier", "archive_kind"}
    if not frappe.has_permission(PROFILE, "read") or not fields.issubset(_readable_fields(PROFILE)):
        return None
    filters = {"company": company, "supplier": supplier}
    # The profile's Supplier link is not unique in the schema. A count prevents
    # treating one visible row as unique when another matching row is hidden.
    if frappe.db.count(PROFILE, filters=filters) != 1:
        return None
    names = frappe.get_list(PROFILE, filters=filters, pluck="name", limit_page_length=2)
    if len(names) != 1:
        return None
    profile = frappe.get_doc(PROFILE, names[0])
    if (not frappe.has_permission(PROFILE, "read", doc=profile, throw=False)
            or profile.company != company or profile.supplier != supplier
            or profile.archive_kind not in ("电镀厂", "外发工厂")):
        return None
    return profile.archive_kind


@frappe.whitelist(methods=["GET"])
def list_subcontracting_reversals(kind, company, from_date, to_date,
                                  status="submitted", search="", page=1, page_size=50):
    """Return authorized source documents; cancellation stays in native forms."""
    if frappe.session.user == "Guest":
        _fail("请先登录后查询委外单据", frappe.PermissionError)
    spec = SOURCES.get(kind)
    if not spec:
        _fail("请选择有效的委外单据类型")
    if status not in STATUS_FILTERS:
        _fail("委外单据状态筛选无效")
    doctype = spec["doctype"]
    if not frappe.has_permission(doctype, "read"):
        _fail("没有读取所选委外单据的权限", frappe.PermissionError)
    if not company or not frappe.get_list(
        "Company", filters={"name": company}, pluck="name", limit_page_length=1
    ):
        _fail("没有查看该公司的委外单据权限", frappe.PermissionError)
    start, end = _date(from_date, "开始日期"), _date(to_date, "结束日期")
    if start > end or (end - start).days > MAX_DAYS:
        _fail("日期范围无效或超过 366 天")
    if not isinstance(search, str) or len(search) > 140 or any(ord(char) < 32 for char in search):
        _fail("关键词无效")
    page = _number(page, "页码", 100000)
    page_size = _number(page_size, "每页条数", MAX_PAGE_SIZE)
    fields = spec["fields"]
    if not fields.issubset(_readable_fields(doctype)):
        _fail("没有读取委外撤销列表完整来源字段的权限", frappe.PermissionError)

    filters = {"company": company, spec["date_field"]: ["between", [str(start), str(end)]]}
    if kind == "plating_inspection":
        # Only genuine inspection documents attached to subcontracting receipts.
        filters["reference_type"] = "Subcontracting Receipt"
    if status == "submitted":
        filters["docstatus"] = 1
    elif status == "cancelled":
        filters["docstatus"] = 2
    options = {}
    search = search.strip()
    if search:
        pattern = f"%{search}%"
        options["or_filters"] = [[doctype, field, "like", pattern] for field in spec["search"]]
    selected = frappe.get_list(
        doctype, filters=filters, fields=sorted(fields),
        order_by=f"{spec['date_field']} desc, name desc",
        limit_start=(page - 1) * page_size, limit_page_length=page_size + 1,
        **options,
    )
    has_more = len(selected) > page_size
    rows = []
    for row in selected[:page_size]:
        doc = frappe.get_doc(doctype, row.name)
        if not frappe.has_permission(doctype, "read", doc=doc, throw=False):
            continue
        if doc.company != company:
            continue
        supplier = doc.get("supplier")
        if kind == "plating_inspection":
            if doc.reference_type != "Subcontracting Receipt" or not doc.reference_name:
                continue
            receipt = frappe.get_doc("Subcontracting Receipt", doc.reference_name)
            if (not frappe.has_permission("Subcontracting Receipt", "read", doc=receipt, throw=False)
                    or receipt.company != company):
                archive_kind = None
            else:
                supplier = receipt.supplier
                archive_kind = _partner_class(company, supplier)
        else:
            archive_kind = _partner_class(company, supplier) if supplier else None
        if archive_kind and archive_kind != spec["archive_kind"]:
            continue
        source = {field: doc.get(field) for field in fields}
        source["source_doctype"] = doctype
        source["source_classification"] = archive_kind or "未分类"
        source["document_date"] = str(doc.get(spec["date_field"]) or "")
        source["docstatus_label"] = {0: "草稿", 1: "已提交", 2: "已取消"}.get(doc.docstatus, "未知")
        if kind == "outer_receipt":
            source["business_type"] = "委外收回退回" if doc.is_return else "委外收回"
        elif kind == "plating_inspection":
            source["business_type"] = "委外收回品检"
        rows.append(source)
    return {
        "rows": rows, "page": page, "page_size": page_size, "has_more": has_more,
        "kind": kind,
        "source_note": "单据按公司内唯一可读的合作方档案分类；无唯一匹配或档案不可读的来源标为未分类。",
        "action_note": "打开正式单据后使用原生取消；存在后续单据或库存/账务依赖时以原生校验结果为准。",
    }
