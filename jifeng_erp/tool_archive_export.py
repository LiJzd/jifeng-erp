"""Export saved tool archives through their existing authorized projections."""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import math

import frappe
from openpyxl import Workbook

from jifeng_erp import plate_modeling as M
from jifeng_erp import tool_archive as T
from jifeng_erp.archive_versions import same_version


CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
COLUMNS = {
    "mold": (("legacy_number", "编号"), ("mold_no", "模具"), ("product_code", "产品"),
             ("quantity", "数量"), ("maker", "压模"), ("mold_date", "日期"), ("amount", "金额"),
             ("remark", "说明"), ("location", "库位"), ("scrapped", "报废"), ("legacy_copy", "复制")),
    "plate": (("legacy_number", "编号"), ("plate_type", "类型"), ("product_code", "产品"),
              ("assessment_score", "考核分"), ("maker", "制版"), ("quantity", "数量"),
              ("notes", "说明"), ("location", "库位"), ("retired", "报废"), ("legacy_copy", "复制")),
    "modeling": (("legacy_number", "编号"), ("model_number", "建模号"), ("product_code", "产品编号"),
                 ("reference_unit_price", "单价"), ("modeler_text", "建模"), ("model_date", "日期"),
                 ("remarks", "备注"), ("legacy_copy", "复制")),
}
TITLES = {"mold": "模具", "plate": "版种", "modeling": "版种建模"}
DOCTYPES = {**T.DOCTYPES, "modeling": M.DOCTYPE}


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


@contextmanager
def _fresh_snapshot():
    from frappe.database import get_db

    original, conf = frappe.local.db, frappe.local.conf
    settings = {"socket": conf.db_socket, "host": conf.db_host, "port": conf.db_port,
                "user": conf.db_user, "password": conf.db_password, "cur_db_name": conf.db_name}
    if (conf.db_type != "mariadb" or not all(isinstance(value, str) and value for value in
            (frappe.local.site, settings["user"], settings["password"], settings["cur_db_name"]))
            or settings["user"].casefold() == "root"
            or any(getattr(original, key, None) != value for key, value in settings.items())):
        frappe.throw("无法核实当前站点的只读导出连接，请重新查询")
    fresh = get_db(**settings)
    if fresh is original:
        frappe.throw("无法建立独立的只读导出连接，请重新查询")
    try:
        frappe.local.db = fresh
        fresh.begin(read_only=True)
        yield
    finally:
        try:
            fresh.rollback()
        finally:
            try:
                fresh.close()
            finally:
                frappe.local.db = original


def _permission_state():
    user = frappe.session.user
    # DTOs also check link/file visibility. Freeze all of their native permission
    # inputs; never read User credentials or authentication records.
    doctypes = [*DOCTYPES.values(), "Company", "Item", "Employee", "File", "Jifeng Expense Voucher"]
    state = []
    for doctype, filters, fields in (
        ("User", {"name": user}, ["name", "enabled", "user_type"]),
        ("Has Role", {"parenttype": "User", "parent": user}, ["*"]),
        ("User Permission", {"user": user}, ["*"]),
        ("DocPerm", {"parent": ["in", doctypes]}, ["*"]),
        ("Custom DocPerm", {"parent": ["in", doctypes]}, ["*"]),
        ("DocType", {"name": ["in", doctypes]}, ["*"]),
        ("DocField", {"parent": ["in", doctypes]}, ["*"]),
        ("Custom Field", {"dt": ["in", doctypes]}, ["*"]),
        ("Property Setter", {"doc_type": ["in", doctypes]}, ["*"]),
        ("DocShare", {"share_doctype": ["in", doctypes]}, ["*"]),
    ):
        rows = frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0)
        if doctype == "DocShare":
            rows = [row for row in rows if row.get("user") == user or row.get("everyone")]
        state.append((doctype, tuple(sorted((_freeze(row) for row in rows), key=repr))))
    return _freeze((user, state))


def _scope(kind, company, search, include_inactive):
    T._login()
    if not isinstance(kind, str) or kind not in DOCTYPES:
        frappe.throw("请选择模具、版种或版种建模档案")
    T._company(company)
    if not isinstance(search, str) or len(search) > 140:
        frappe.throw("请输入有效档案关键词")
    include_inactive = T._normalize("mold", "scrapped", include_inactive)
    if kind == "modeling" and include_inactive:
        frappe.throw("版种建模没有报废状态筛选")
    if not frappe.has_permission(DOCTYPES[kind], "read"):
        frappe.throw("没有读取档案权限", frappe.PermissionError)
    required = M.REQUIRED_READ if kind == "modeling" else {"name", "modified", "company", T.TITLE[kind], T.INACTIVE[kind]}
    T._fields(DOCTYPES[kind], required)
    readable = T._columns(DOCTYPES[kind], "read")
    search_fields = ({field for field in M.RAW_FIELDS if field not in
                      ("legacy_copy", "model_date", "reference_unit_price")} if kind == "modeling" else
                     {"legacy_number", T.TITLE[kind], "product_code", "location", "plate_type"}) & readable
    if search and not search_fields:
        frappe.throw("没有读取档案筛选字段的权限", frappe.PermissionError)
    return include_inactive, readable, {
        "filters": {"company": company, **({T.INACTIVE[kind]: 0} if kind != "modeling" and not include_inactive else {})},
        "order_by": "model_date desc, name asc" if kind == "modeling" else "modified desc, name asc",
        **({"or_filters": [[field, "like", "%" + search + "%"] for field in sorted(search_fields)]} if search else {}),
    }


def _checked(kind, company, query):
    # Candidate IDs are internal only. Every returned value comes from the
    # existing DTO, which uses get_list and native masked field projections.
    names = frappe.get_all(DOCTYPES[kind], fields=["name"], limit_page_length=0, **query)
    names = [row["name"] for row in names]
    if len(names) != len(set(names)) or len(names) > 1048575:
        frappe.throw("档案全集无法完整导出，请缩小筛选范围")
    checked = []
    for name in names:
        try:
            document = M._document(name) if kind == "modeling" else T._document(name, kind)
            if document.company != company:
                frappe.throw("档案来源已改变，请重新查询")
            dto = M._pack(document) if kind == "modeling" else T._pack(document, kind)
        except frappe.PermissionError:
            continue
        if not same_version(document.modified, dto["version"]):
            frappe.throw("档案来源已改变，请重新查询")
        document.check_permission("export")
        values = dto if kind == "modeling" else dto["values"]
        exported = {field: values.get(field) for field, _ in COLUMNS[kind]}
        checked.append((name, _freeze(document.as_dict()), exported))
    if not checked and not frappe.has_permission(DOCTYPES[kind], "export"):
        frappe.throw("没有导出档案权限", frappe.PermissionError)
    return names, checked


def _text(kind, field, value):
    if value is None:
        return ""
    fieldtype = frappe.get_meta(DOCTYPES[kind]).get_field(field).fieldtype
    if fieldtype == "Check":
        if type(value) is not bool and not (type(value) is int and value in (0, 1)):
            frappe.throw("档案包含无法完整导出的勾选状态，请先核对原文")
        value = str(int(value))
    elif fieldtype in ("Float", "Currency") and type(value) in (int, float):
        if not math.isfinite(value):
            frappe.throw("档案包含无法完整导出的数值，请先核对原文")
        value = str(value)
    if not isinstance(value, str) or len(value) > 32767 or any(
        not (character in "\t\n\r" or " " <= character <= "\ud7ff"
             or "\ue000" <= character <= "\ufffd" or "\U00010000" <= character <= "\U0010ffff")
        for character in value
    ):
        frappe.throw("档案包含无法完整导出的文本，请先核对原文")
    return value


def _xlsx(kind, checked):
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = TITLES[kind] + "档案"
        rows = [[label for _, label in COLUMNS[kind]]]
        rows.extend([_text(kind, field, values.get(field)) for field, _ in COLUMNS[kind]] for _, _, values in checked)
        for row_number, values in enumerate(rows, 1):
            for column_number, value in enumerate(values, 1):
                cell = sheet.cell(row_number, column_number)
                cell.value = value
                cell.data_type, cell.number_format = "s", "@"
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    finally:
        workbook.close()


@frappe.whitelist(methods=["GET"])
def export_archives(record_type, company, search="", include_inactive=False):
    kind = record_type
    include_inactive, readable, query = _scope(kind, company, search, include_inactive)
    permissions = _permission_state()
    names, checked = _checked(kind, company, query)
    content = _xlsx(kind, checked)
    # A fresh primary read-only transaction sees commits made before its first
    # consistent read. Commits after that point are outside this guarantee.
    with _fresh_snapshot():
        if permissions != _permission_state():
            frappe.throw("档案字段或权限已改变，请重新查询", frappe.PermissionError)
        _, current_readable, current_query = _scope(kind, company, search, include_inactive)
        current_names, current = _checked(kind, company, current_query)
        if readable != current_readable or query != current_query or names != current_names or checked != current:
            frappe.throw("档案来源或可见范围已改变，请重新查询")
        if permissions != _permission_state():
            frappe.throw("档案字段或权限已改变，请重新查询", frappe.PermissionError)
    frappe.response.update({"type": "download", "content_type": CONTENT_TYPE,
        "filename": TITLES[kind] + "档案.xlsx", "display_content_as": "attachment", "filecontent": content})
