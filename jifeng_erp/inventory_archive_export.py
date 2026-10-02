"""Export saved inventory archives, never list pages or native source IDs.

The old export template is unknown. These XLSX columns use the verified 20/19
data labels; images are authorized availability text, without URLs or bytes.
"""

from decimal import Decimal, InvalidOperation
from io import BytesIO

import frappe
from openpyxl import Workbook

from jifeng_erp import inventory_catalog as C
from jifeng_erp.archive_versions import same_version
from jifeng_erp.tool_archive_export import CONTENT_TYPE, _freeze, _fresh_snapshot


MAX_EXPORT_ROWS = 1_048_575  # Excel's row limit, excluding the header.
COLUMNS = {
    "物料": (("legacy_number", "编号"), ("old_number", "旧编号"), ("major_category", "大类"),
        ("minor_category", "小类"), ("display_name", "名称"), ("specification", "规格"), ("material", "材质"),
        ("location", "库位"), ("display_uom", "单位"), ("list_price", "单价"), ("processing_price", "加工价"),
        ("weight", "重量"), ("upper_limit", "上限"), ("lower_limit", "下限"), ("conversion_uom", "换算单位"),
        ("conversion_value", "换算值"), ("supplier", "供应商"), ("notes", "备注"), ("inactive", "停用"), ("image", "图片")),
    "材料": (("legacy_number", "编号"), ("display_name", "名称"), ("specification", "规格"), ("shape", "形状"),
        ("color", "颜色"), ("minor_category", "小类"), ("display_uom", "单位"), ("list_price", "单价"),
        ("weight", "重量"), ("processing_price", "加工价"), ("location", "库位"), ("upper_limit", "上限"),
        ("lower_limit", "下限"), ("conversion_uom", "换算单位"), ("conversion_value", "换算值"),
        ("supplier", "供应商"), ("notes", "备注"), ("inactive", "停用"), ("image", "图片")),
}
SOURCES = (C.PROFILE, "Item", "Supplier", "Company", "File")


def _permission_state():
    user = frappe.session.user
    state = []
    for doctype, filters, fields in (
        ("User", {"name": user}, ["name", "enabled", "user_type"]),
        ("Has Role", {"parenttype": "User", "parent": user}, ["*"]),
        ("User Permission", {"user": user}, ["*"]),
        ("DocPerm", {"parent": ["in", SOURCES]}, ["*"]),
        ("Custom DocPerm", {"parent": ["in", SOURCES]}, ["*"]),
        ("DocType", {"name": ["in", SOURCES]}, ["*"]),
        ("DocField", {"parent": ["in", SOURCES]}, ["*"]),
        ("Custom Field", {"dt": ["in", SOURCES]}, ["*"]),
        ("Property Setter", {"doc_type": ["in", SOURCES]}, ["*"]),
        ("DocShare", {"share_doctype": ["in", SOURCES]}, ["*"]),
    ):
        rows = frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0)
        if doctype == "DocShare":
            rows = [row for row in rows if row.get("user") == user or row.get("everyone")]
        state.append((doctype, tuple(sorted((_freeze(row) for row in rows), key=repr))))
    return _freeze((user, state))


def _scope(kind, company, search, status):
    C._scope(kind, company)
    if not isinstance(status, str) or status not in C.STATUSES:
        frappe.throw("请选择在用、停用或全部档案")
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效档案关键词")
    if not frappe.has_permission(C.PROFILE, "export"):
        frappe.throw("没有导出库存档案权限", frappe.PermissionError)
    fields = C._fields(C.PROFILE, C.PROFILE_FIELDS, C.PROFILE_REQUIRED)
    search_fields = C.SEARCH_FIELDS & set(fields)
    if search and not search_fields:
        frappe.throw("没有读取关键词查询字段的权限", frappe.PermissionError)
    filters = {"company": company, "archive_kind": kind}
    if status != "全部":
        filters["inactive"] = int(status == "停用")
    return fields, {"filters": filters, "order_by": "name asc",
        **({"or_filters": [[field, "like", "%" + search + "%"] for field in sorted(search_fields)]} if search else {})}


def _supplier(name):
    """A projected display name, never Supplier.name or a full-doc fallback."""
    fields = C._fields("Supplier", {"supplier_name"}, {"name", "modified"})
    rows = frappe.get_list("Supplier", filters={"name": name}, fields=fields, limit_page_length=1)
    if len(rows) != 1 or not {"name", "modified"}.issubset(rows[0]):
        frappe.throw("没有完整读取供应商来源权限", frappe.PermissionError)
    row = rows[0]
    document = frappe.get_doc("Supplier", name)
    if row["name"] != name or not C._current(document, row, {"name"}):
        frappe.throw("供应商来源或权限已改变，请重新查询", frappe.PermissionError)
    if "supplier_name" in row and document.get("supplier_name") != row["supplier_name"]:
        frappe.throw("供应商来源已改变，请重新查询")
    return row.get("supplier_name"), _freeze(dict(document.as_dict())), document, row


def _checked(kind, company, search, status):
    fields, query = _scope(kind, company, search, status)
    # A separate bound supports >1000 saved archives. Overflow refuses the
    # entire file, and never exports a silent prefix or a current UI page.
    rows = frappe.get_list(C.PROFILE, fields=fields, limit_page_length=MAX_EXPORT_ROWS + 1, **query)
    if len(rows) > MAX_EXPORT_ROWS:
        frappe.throw("档案超过Excel行数限制，请缩小筛选范围后完整导出")
    if len({row.get("name") for row in rows}) != len(rows):
        frappe.throw("档案来源或可见范围已改变，请重新查询")
    packed, sources, suppliers, profiles, exported = [], [], [], [], []
    for row in rows:
        loaded = C._load(row, kind, company, status)
        if not loaded:
            continue
        profile, item, item_row = loaded
        profile.check_permission("export")
        profiles.append(profile)
        view, guard = C._pack(profile, item, row, item_row, kind, company)
        values = {field: view["profile"].get(field) for field, _ in COLUMNS[kind] if field != "image"}
        values["image"] = view["image"]["state"]
        if values.get("supplier"):
            display, state, document, projected = _supplier(values["supplier"])
            values["supplier"] = display
            suppliers.append((document, projected))
            sources.append(("Supplier", row["name"], state, _freeze(projected)))
        sources.append((C.PROFILE, row["name"], _freeze(profile.as_dict()), _freeze(item.as_dict()), _freeze(guard)))
        packed.append((view, guard))
        exported.append(values)
    current_suppliers = [frappe.get_doc("Supplier", document.name) for document, _ in suppliers]
    C._verify_output(packed, kind, company)
    for (document, projected), current in zip(suppliers, current_suppliers):
        readable = C._columns("Supplier")
        if (not current.has_permission("read") or not set(projected).issubset(readable)
                or not same_version(current.modified, C._value(projected["modified"]))
                or _freeze(current.as_dict()) != _freeze(document.as_dict())):
            frappe.throw("供应商来源或权限已改变，请重新查询", frappe.PermissionError)
    _scope(kind, company, search, status)
    for profile in profiles:
        profile.check_permission("read")
        profile.check_permission("export")
    return _freeze(rows), _freeze(sources), exported


def _text(field, value):
    if value is None:
        return ""
    fieldtype = "Data" if field in ("supplier", "image") else frappe.get_meta(C.PROFILE).get_field(field).fieldtype
    if fieldtype == "Check":
        if type(value) not in (bool, int) or value not in (0, 1):
            frappe.throw("档案包含无法完整导出的勾选状态，请先核对原文")
        value = str(int(value))
    elif fieldtype in ("Float", "Currency"):
        if type(value) not in (str, int, float, Decimal):
            frappe.throw("档案包含无法完整导出的数值，请先核对原文")
        try:
            finite = Decimal(str(value)).is_finite()
        except (InvalidOperation, ValueError):
            finite = False
        if not finite:
            frappe.throw("档案包含无法完整导出的数值，请先核对原文")
        value = str(value)
    if not isinstance(value, str) or any(not (character in "\t\n\r" or " " <= character <= "\ud7ff"
            or "\ue000" <= character <= "\ufffd" or "\U00010000" <= character <= "\U0010ffff") for character in value):
        frappe.throw("档案包含无法完整导出的文本，请先核对原文")
    if len(value.encode("utf-16-le")) // 2 > 32767:
        frappe.throw("档案包含超过Excel单元格限制的文本，请先核对原文")
    return value


def _xlsx(kind, values):
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = kind + "档案"
        rows = [[label for _, label in COLUMNS[kind]]]
        rows.extend([_text(field, row.get(field)) for field, _ in COLUMNS[kind]] for row in values)
        for row_number, row in enumerate(rows, 1):
            for column_number, value in enumerate(row, 1):
                cell = sheet.cell(row_number, column_number)
                cell.value = value
                cell.data_type, cell.number_format = "s", "@"
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    finally:
        workbook.close()


@frappe.whitelist(methods=["GET"])
def export_archives(kind, company, search="", status="全部"):
    _scope(kind, company, search, status)
    permissions = _permission_state()
    checked = _checked(kind, company, search, status)
    content = _xlsx(kind, checked[2])
    # Fresh primary read-only snapshot observes commits before its first read.
    # Publish only after its complete comparison and successful cleanup.
    with _fresh_snapshot():
        if permissions != _permission_state():
            frappe.throw("档案字段或权限已改变，请重新查询", frappe.PermissionError)
        if checked != _checked(kind, company, search, status):
            frappe.throw("档案来源或可见范围已改变，请重新查询")
        if permissions != _permission_state():
            frappe.throw("档案字段或权限已改变，请重新查询", frappe.PermissionError)
    frappe.response.update({"type": "download", "content_type": CONTENT_TYPE, "filename": kind + "档案.xlsx",
        "display_content_as": "attachment", "filecontent": content})
