"""Export the complete visible factory filter set as literal XLSX text."""

from contextlib import contextmanager
from io import BytesIO

import frappe
from openpyxl import Workbook

from jifeng_erp import partner_archive as P


CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEXT_COLUMNS = (
    ("legacy_number", "编号"), ("display_name", "名称"), ("owner_name", "负责"),
    ("mobile", "手机"), ("reception", "接货"), ("phone", "电话"), ("address", "地址"),
    ("notes", "备注"), ("bank_name", "开户"), ("account_name", "户名"), ("bank_account", "账号"),
)
AUDIT_COLUMNS = (("owner", "操作"), ("creation", "日期"),
                 ("modified_by", "修改人"), ("profile_version", "修改日期"))


@contextmanager
def _fresh_snapshot():
    from frappe.database import get_db

    original = frappe.local.db
    conf = frappe.local.conf
    settings = {"socket": conf.db_socket, "host": conf.db_host, "port": conf.db_port,
                "user": conf.db_user, "password": conf.db_password, "cur_db_name": conf.db_name}
    if (
        conf.db_type != "mariadb"
        or not all(isinstance(value, str) and value for value in
                   (frappe.local.site, settings["user"], settings["password"], settings["cur_db_name"]))
        or settings["user"].casefold() == "root"
        or any(getattr(original, key, None) != value for key, value in settings.items())
    ):
        frappe.throw("无法核实当前站点的只读导出连接，请重新查询")
    # Use the same site's primary connection, without changing the request's
    # isolation level or transaction. Never include connection settings in errors.
    fresh = get_db(**settings)
    if fresh is original:
        frappe.throw("无法建立独立的只读导出连接，请重新查询")
    try:
        frappe.local.db = fresh
        fresh.begin(read_only=True)
        yield
    finally:
        # Native rollback starts another transaction; close it immediately.
        # Cleanup errors must escape before an attachment can be published.
        try:
            fresh.rollback()
        finally:
            try:
                fresh.close()
            finally:
                frappe.local.db = original


def _permission_state():
    # Native role/meta/share checks can be cached. Compare their raw inputs
    # across the complete query and serialization, without reading User secrets.
    user = frappe.session.user
    doctypes = [P.PROFILE, "Supplier", "Company"]
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
        state.append((doctype, tuple(sorted((P._freeze(row) for row in rows), key=repr))))
    return P._freeze((user, state))


def _text(value, *, check=False):
    if value is None:
        value = ""
    elif check:
        if type(value) is not bool and not (type(value) is int and value in (0, 1)):
            frappe.throw("工厂档案包含无法完整导出的停用状态，请先核对原文")
        value = str(int(value))
    if not isinstance(value, str) or len(value) > 32767 or any(
        not (character in "\t\n\r" or " " <= character <= "\ud7ff"
             or "\ue000" <= character <= "\ufffd" or "\U00010000" <= character <= "\U0010ffff")
        for character in value
    ):
        # Do not let openpyxl silently truncate text or include a private value
        # in its IllegalCharacterError. Reject the entire in-memory export.
        frappe.throw("工厂档案包含无法完整导出的文本，请先核对原文")
    return value


def _xlsx(kind, checked):
    columns = TEXT_COLUMNS
    if kind == "电镀厂":
        columns = tuple((field, "户行" if field == "bank_name" else label) for field, label in columns)
        columns += (("inactive", "停用"),) + AUDIT_COLUMNS
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "工厂档案"
        rows = [[label for _, label in columns]]
        readable = P._columns(P.PROFILE, "read")
        for _, _, view in checked:
            values = []
            for field, _ in columns:
                if field == "profile_version":
                    value = view.get(field) if "modified" in readable else None
                elif field in {name for name, _ in AUDIT_COLUMNS}:
                    value = view.get(field)
                else:
                    value = view["profile"].get(field)
                values.append(_text(value, check=field == "inactive"))
            rows.append(values)
        for row_number, values in enumerate(rows, 1):
            for column_number, value in enumerate(values, 1):
                cell = sheet.cell(row_number, column_number)
                cell.value = value
                # Assignment normally recognizes = formulas and Excel error
                # codes. Override it after binding, preserving the exact text.
                cell.data_type = "s"
                cell.number_format = "@"
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    finally:
        workbook.close()


@frappe.whitelist(methods=["GET"])
def export_archives(kind, company, search="", status="在用"):
    P._scope(kind, company)
    permissions_before = _permission_state()
    checked, names, query = P._checked_archives(kind, company, search, status)
    if not checked and not frappe.has_permission(P.PROFILE, "export"):
        P._deny()
    for document, _, _ in checked:
        document.check_permission("export")
    readable_before = P._columns(P.PROFILE, "read")
    content = _xlsx(kind, checked)
    # RR reads on the original request connection retain its earlier snapshot.
    # This new read-only transaction sees changes committed before its first
    # consistent read; changes after that point remain outside this guarantee.
    with _fresh_snapshot():
        if permissions_before != _permission_state():
            frappe.throw("工厂档案字段或权限已改变，请重新查询", frappe.PermissionError)
        visible = {document.name for document, _, _ in checked}
        for name in names:
            if name in visible:
                continue
            try:
                P._load(name, kind, company)
            except frappe.PermissionError:
                continue
            # An omitted source can gain if_owner/controller permission while
            # raw DocPerm/Share inputs stay equal.
            frappe.throw("工厂档案可见范围已改变，请重新查询")
        P._recheck_archives(kind, company, names, query, checked)
        for document, _, _ in checked:
            current, _ = P._load(document.name, kind, company)
            current.check_permission("export")
        if permissions_before != _permission_state() or readable_before != P._columns(P.PROFILE, "read"):
            frappe.throw("工厂档案字段或权限已改变，请重新查询", frappe.PermissionError)
        if not checked and not frappe.has_permission(P.PROFILE, "export"):
            P._deny()
    # Publish only after complete authorization and successful DB cleanup.
    frappe.response.update({"type": "binary", "content_type": CONTENT_TYPE,
        "filename": kind + "档案.xlsx", "display_content_as": "attachment", "filecontent": content})
