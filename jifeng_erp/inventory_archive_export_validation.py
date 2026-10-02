"""Validate saved synthetic exports on an explicitly isolated site.

No archive, user, permission, or ledger fixtures are created here. A fresh
snapshot must see already committed QA records. Emit counts and hashes only.
"""

from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import frappe
from openpyxl import load_workbook

from jifeng_erp import inventory_archive_export as E
from jifeng_erp import inventory_catalog as C
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_dictionary_validation import _native_contents, _sql_row_hash


def _contents():
    result = _native_contents()
    doctypes = set(E.SOURCES)
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    for doctype in sorted(doctypes):
        result[doctype] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
            + "` ORDER BY name", as_dict=True))
    return result


def _expected(kind, company, search, status):
    readable = C._columns(C.PROFILE)
    filters = {"company": company, "archive_kind": kind}
    if status != "全部":
        filters["inactive"] = int(status == "停用")
    names = frappe.get_list(C.PROFILE, filters=filters, fields=["name"], order_by="name asc", limit_page_length=0,
        or_filters=[[field, "like", "%" + search + "%"] for field in sorted(C.SEARCH_FIELDS & readable)])
    expected = []
    for row in names:
        dto = C.read_archive(row.name, kind, company)
        values = dto["profile"].copy()
        values["image"] = dto["image"]["state"]
        if values.get("supplier"):
            name = values["supplier"]
            supplier = frappe.get_doc("Supplier", name)
            supplier.check_permission("read")
            fields = sorted({"name", "supplier_name"} & C._columns("Supplier"))
            projected = frappe.get_list("Supplier", filters={"name": name}, fields=fields, limit_page_length=1)
            assert len(projected) == 1 and projected[0].name == name
            values["supplier"] = projected[0].get("supplier_name")
        expected.append(tuple(None if values.get(field) in (None, "") else str(int(values[field])) if field == "inactive"
                              else str(values[field]) for field, _ in E.COLUMNS[kind]))
    return expected


def smoke(company, search, manager_user=None, readonly_user=None, require_nonempty=True):
    """Read saved namespace records; optional existing users test actual roles."""
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb"
    assert isinstance(search, str) and search.startswith("JF-") and 3 < len(search) <= 140
    assert frappe.db.exists("Company", company)
    if manager_user:
        assert manager_user != "Administrator" and "Stock Manager" in frappe.get_roles(manager_user)
    if readonly_user:
        assert readonly_user != "Administrator" and "Stock User" in frappe.get_roles(readonly_user)
        assert "Stock Manager" not in frappe.get_roles(readonly_user)
    original_user, original_test = frappe.session.user, frappe.in_test
    original_response = frappe.local.response
    callbacks, context = _callbacks(), _local_context()
    tables, counts = _tab_counts()
    before, ledger = _contents(), _ledger_snapshot()
    logs = set(frappe.get_all("Error Log", pluck="name"))
    checks, evidence, restored = [], {}, False

    def export(kind, status):
        frappe.local.response = frappe._dict({"docs": []})
        request_db = frappe.local.db
        E.export_archives(kind, company, search, status)
        assert frappe.local.db is request_db
        response = frappe.response
        assert response.type == "download" and response.content_type == E.CONTENT_TYPE
        assert response.filename == kind + "档案.xlsx" and response.display_content_as == "attachment"
        from frappe.utils.response import build_response
        built = build_response()
        assert built.mimetype == E.CONTENT_TYPE and built.get_data() == response.filecontent
        assert built.headers["Content-Disposition"].startswith("attachment")
        with ZipFile(BytesIO(response.filecontent)) as archive:
            xml = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            assert not xml.findall(".//s:f", ns) and not xml.findall(".//s:hyperlinks", ns)
            assert not any(path.startswith(("xl/externalLinks/", "xl/media/")) for path in archive.namelist())
        workbook = load_workbook(BytesIO(response.filecontent), data_only=False)
        try:
            sheet = workbook.active
            assert tuple(cell.value for cell in sheet[1]) == tuple(label for _, label in E.COLUMNS[kind])
            assert all(cell.data_type in ("s", "inlineStr") and cell.hyperlink is None for row in sheet for cell in row)
            return list(sheet.values)[1:]
        finally:
            workbook.close()

    try:
        frappe.set_user("Administrator")
        frappe.in_test = False
        for kind in C.KINDS:
            expected = _expected(kind, company, search, "全部")
            assert not require_nonempty or expected, "需要两种已保存非空合成档案才能计入非空验收"
            for status in C.STATUSES:
                assert export(kind, status) == _expected(kind, company, search, status)
                checks.append("admin_" + kind + "_" + status + "_exact_saved_scope_official_literal_xlsx")
            evidence[kind] = {"saved_rows": len(expected), "columns": len(E.COLUMNS[kind]), "nonempty_verified": bool(expected)}
        if manager_user:
            frappe.set_user(manager_user)
            for kind in C.KINDS:
                expected = _expected(kind, company, search, "全部")
                assert not require_nonempty or expected
                assert export(kind, "全部") == expected
                checks.append("existing_stock_manager_" + kind + "_native_read_export_sources")
        if readonly_user:
            frappe.set_user(readonly_user)
            assert not frappe.has_permission(C.PROFILE, "export")
            for kind in C.KINDS:
                assert _expected(kind, company, search, "全部"), "只读岗位须先能浏览已保存来源"
                frappe.local.response = frappe._dict({"docs": []})
                try:
                    E.export_archives(kind, company, search, "全部")
                except frappe.PermissionError:
                    pass
                else:
                    raise AssertionError("只读岗位意外获得导出权限")
                assert "filecontent" not in frappe.response and frappe.response.get("type") != "download"
                checks.append("existing_stock_user_" + kind + "_browse_allowed_export_refused")
        frappe.set_user("Guest")
        frappe.local.response = frappe._dict({"docs": []})
        try:
            E.export_archives("物料", company, search, "全部")
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError("Guest意外获得导出权限")
        assert "filecontent" not in frappe.response
        checks.append("guest_refuses_without_filecontent")
    finally:
        try:
            frappe.set_user(original_user)
            frappe.in_test = original_test
            frappe.local.response = original_response
            _restore_callbacks(callbacks)
            _restore_local_context(context)
            assert _contents() == before and _tab_counts(tables)[1] == counts and _ledger_snapshot() == ledger
            assert set(frappe.get_all("Error Log", pluck="name")) == logs
            assert _callbacks() == callbacks and _local_context() == context
            restored = True
        finally:
            assert restored, "库存档案原生导出验证恢复未核实"
    checks.append("no_created_fixture_complete_sources_counts_ledgers_and_context_unchanged")
    return {"passed": True, "checks": checks, "gate_count": len(checks), "evidence": evidence,
        "manager_verified": bool(manager_user), "readonly_role_verified": bool(readonly_user),
        "http_builder_verified": True, "http_request_verified": False, "gui_verified": False,
        "concurrent_revocation_verified": False, "load_capacity_verified": False,
        "source_hashes_before": before, "source_hashes_after": _contents()}
