"""Export the complete saved product filter set with the current field template.

The legacy file template is unknown. Profile reference values remain literal;
the two exact private Profile image bindings contribute availability text only.
"""

from decimal import Decimal, InvalidOperation
from io import BytesIO

import frappe
from openpyxl import Workbook

from jifeng_erp import inventory_catalog as C
from jifeng_erp import product_archive as P
from jifeng_erp.archive_versions import same_version
from jifeng_erp.tool_archive_export import CONTENT_TYPE, _freeze, _fresh_snapshot


MAX_EXPORT_ROWS = 1_048_575
MAX_IMAGE_ATTACHMENTS = 1000
COLUMNS = (
    ("legacy_number", "编号"), ("legacy_code", "代码"), ("major_category", "大类"),
    ("minor_category", "小类"), ("old_number", "旧号"), ("brand", "品牌"),
    ("material", "材质"), ("shared_version", "共版"), ("patent_number", "专利号"),
    ("display_name", "名称"), ("source", "来源"), ("designer", "设计"),
    ("pattern_maker", "打版"), ("modeler", "建模"), ("style", "风格"),
    ("owner_name", "负责"), ("region", "区域"), ("display_uom", "单位"),
    ("list_price", "单价"), ("blank_price", "毛胚价"), ("weight", "重量"),
    ("silver_weight", "银重"), ("stone_weight", "石重"), ("inactive", "停用"),
    ("legacy_remarks", "备注"), ("notes", "注意事项"),
    ("profile_image", "产品图片"), ("patent_image", "专利图片"),
)
PROFILE_FIELDS = P.REQUIRED_PROFILE | {field for field, _ in COLUMNS}
FILE_FIELDS = {"name", "modified", "file_url", "is_private", "attached_to_doctype",
               "attached_to_name", "attached_to_field"}
SOURCES = (P.PROFILE, "Item", "Company", "File")


def _fields(doctype, required):
    # The inventory reader also checks native mask access without its cache.
    # A fixed export template must never silently turn a hidden column blank.
    if not required.issubset(C._columns(doctype)):
        frappe.throw("没有完整读取产品导出模板字段的权限", frappe.PermissionError)
    return sorted(required)


def _scope(company, search, include_inactive):
    P._login()
    P._company(company)
    for permission in ("read", "export"):
        if not frappe.has_permission(P.PROFILE, permission):
            frappe.throw("没有读取或导出产品档案的权限", frappe.PermissionError)
    if not frappe.has_permission("Item", "read"):
        frappe.throw("没有读取产品档案来源商品的权限", frappe.PermissionError)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效产品档案关键词")
    inactive = P._normalize(P.PROFILE, "inactive", include_inactive)
    fields = _fields(P.PROFILE, PROFILE_FIELDS)
    _fields("Item", P.REQUIRED_ITEM)
    filters = {"company": company, **({} if inactive else {"inactive": 0})}
    search_fields = ("legacy_number", "legacy_code", "display_name", "old_number", "major_category", "minor_category")
    return inactive, fields, {"filters": filters, "order_by": "name asc",
        **({"or_filters": [[field, "like", "%" + search + "%"] for field in search_fields]} if search else {})}


def _permission_state():
    user, state = frappe.session.user, []
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


def _current(document, row):
    document.check_permission("read")
    _fields(document.doctype, set(row))
    for field, value in row.items():
        current = document.get(field)
        df = document.meta.get_field(field)
        if field == "modified":
            unchanged = same_version(current, P._value(value))
        elif df and df.fieldtype in ("Float", "Currency") and current is not None and value is not None:
            try:
                unchanged = Decimal(str(current)) == Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                unchanged = False
        else:
            unchanged = P._value(current) == P._value(value)
        if not unchanged:
            frappe.throw("产品档案来源或字段已改变，请重新查询")


def _image(profile, field, value):
    if value is not None and not isinstance(value, str):
        frappe.throw("产品图片来源字段不完整，请重新查询")
    if not value:
        return "未上传", ()
    if not P._private_url(value):
        return "附件不可用", ()
    _fields("File", FILE_FIELDS)
    binding = {"file_url": value, "is_private": 1, "attached_to_doctype": P.PROFILE,
               "attached_to_name": profile.name, "attached_to_field": field}
    names = frappe.get_all("File", filters=binding, pluck="name", order_by="name",
                           limit_page_length=MAX_IMAGE_ATTACHMENTS + 1)
    if len(names) > MAX_IMAGE_ATTACHMENTS or len(names) != len(set(names)):
        frappe.throw("产品图片来源无法完整核对，请重新查询")
    sources, available = [], False
    for name in names:
        attachment = frappe.get_doc("File", name)
        if not attachment.has_permission("read"):
            sources.append((name, False))
            continue
        if any(attachment.get(key) != expected for key, expected in binding.items()):
            frappe.throw("产品图片关联已改变，请重新查询")
        downloadable = attachment.is_downloadable()
        available = downloadable or available
        sources.append((name, _freeze(attachment.as_dict()), downloadable))
    _fields("File", FILE_FIELDS)
    return "已上传" if available else "附件不可用", _freeze((names, sources))


def _read_row(row, company, include_inactive):
    if not PROFILE_FIELDS.issubset(row):
        frappe.throw("产品档案导出来源字段不完整，请重新查询", frappe.PermissionError)
    P._name(row["name"])
    profile, item = P._documents(row["name"])
    profile.check_permission("export")
    _current(profile, row)
    if (row["company"] != company or profile.company != company
            or not include_inactive and (row["inactive"] or profile.inactive)):
        frappe.throw("产品档案导出范围已改变，请重新查询")
    item_rows = frappe.get_list("Item", filters={"name": row["item_code"]},
        fields=_fields("Item", P.REQUIRED_ITEM), limit_page_length=1)
    if (len(item_rows) != 1 or not P.REQUIRED_ITEM.issubset(item_rows[0])
            or item.name != row["item_code"] or item_rows[0]["name"] != item.name
            or item.get("item_code") != item.name):
        frappe.throw("产品档案关联商品来源不完整", frappe.PermissionError)
    _current(item, item_rows[0])
    values = {field: P._value(row[field]) for field, _ in COLUMNS if field not in P.IMAGES}
    images = []
    for field in sorted(P.IMAGES):
        state, guard = _image(profile, field, row[field])
        values[field] = state
        images.append((field, guard))
    return _freeze((profile.as_dict(), item.as_dict(), item_rows, images)), values


def _checked(company, search, include_inactive):
    inactive, fields, query = _scope(company, search, include_inactive)
    rows = frappe.get_list(P.PROFILE, fields=fields, limit_page_length=MAX_EXPORT_ROWS + 1, **query)
    if len(rows) > MAX_EXPORT_ROWS:
        frappe.throw("产品档案超过Excel行数限制，请缩小筛选范围后完整导出")
    if len({row.get("name") for row in rows}) != len(rows):
        frappe.throw("产品档案导出来源重复，请重新查询")
    checked = [_read_row(row, company, inactive) for row in rows]
    # Later source loads can revoke an earlier row's fields or document access.
    # Recheck every source and the whole visible set before serialization.
    for row, view in zip(rows, checked):
        if view != _read_row(row, company, inactive):
            frappe.throw("产品档案来源或权限已改变，请重新查询")
    _scope(company, search, include_inactive)
    if _freeze(rows) != _freeze(frappe.get_list(P.PROFILE, fields=fields,
            limit_page_length=MAX_EXPORT_ROWS + 1, **query)):
        frappe.throw("产品档案导出范围已改变，请重新查询")
    return _freeze(rows), _freeze(checked), [view[1] for view in checked]


def _text(field, value):
    if value is None:
        return ""
    fieldtype = "Data" if field in P.IMAGES else frappe.get_meta(P.PROFILE).get_field(field).fieldtype
    if fieldtype == "Check":
        if type(value) not in (bool, int) or value not in (0, 1):
            frappe.throw("产品档案包含无法完整导出的勾选状态")
        value = str(int(value))
    elif fieldtype in ("Float", "Currency"):
        if type(value) not in (str, int, float, Decimal):
            frappe.throw("产品档案包含无法完整导出的数值")
        try:
            finite = Decimal(str(value)).is_finite()
        except (InvalidOperation, ValueError):
            finite = False
        if not finite:
            frappe.throw("产品档案包含无法完整导出的数值")
        value = str(value)
    if not isinstance(value, str) or any(not (character in "\t\n\r" or " " <= character <= "\ud7ff"
            or "\ue000" <= character <= "\ufffd" or "\U00010000" <= character <= "\U0010ffff") for character in value):
        frappe.throw("产品档案包含无法完整导出的文本")
    if len(value.encode("utf-16-le")) // 2 > 32767:
        frappe.throw("产品档案包含超过Excel单元格限制的文本")
    return value


def _xlsx(values):
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "产品档案"
        for index, row in enumerate([[label for _, label in COLUMNS]] + [
                [_text(field, view[field]) for field, _ in COLUMNS] for view in values], 1):
            for column, value in enumerate(row, 1):
                cell = sheet.cell(index, column)
                cell.value = value
                cell.data_type, cell.number_format = "s", "@"
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    finally:
        workbook.close()


@frappe.whitelist(methods=["GET"])
def export_archives(company, search="", include_inactive=False):
    _scope(company, search, include_inactive)
    permissions = _permission_state()
    checked = _checked(company, search, include_inactive)
    content = _xlsx(checked[2])
    # A fresh primary snapshot observes commits before its first read. This is
    # a bounded recheck, not an atomic guarantee against subsequent changes.
    with _fresh_snapshot():
        if permissions != _permission_state():
            frappe.throw("产品档案字段或权限已改变，请重新查询", frappe.PermissionError)
        if checked != _checked(company, search, include_inactive):
            frappe.throw("产品档案来源或可见范围已改变，请重新查询")
        if permissions != _permission_state():
            frappe.throw("产品档案字段或权限已改变，请重新查询", frappe.PermissionError)
    frappe.response.update({"type": "download", "content_type": CONTENT_TYPE, "filename": "产品档案.xlsx",
        "display_content_as": "attachment", "filecontent": content})
