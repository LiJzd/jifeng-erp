"""Serial SDK exports of existing saved synthetic records; no fixture commit.

Run only against an explicitly checked isolated site. A fresh export snapshot
cannot see rollback-only fixtures, so this helper never creates archive rows.
Existing manager accounts may be supplied for real role checks. A temporary
Stock User with native read shares is created only for the export refusal gate,
and all of those permission fixtures are rolled back.
"""

from io import BytesIO
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

import frappe
from openpyxl import load_workbook

from jifeng_erp import plate_modeling as M
from jifeng_erp import tool_archive as T
from jifeng_erp import tool_archive_export as E
from jifeng_erp.catalog_original_validation import _rollback
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_dictionary_validation import _native_contents, _sql_row_hash


def _contents():
    result = _native_contents()
    doctypes = set(E.DOCTYPES.values())
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    for doctype in sorted(doctypes):
        result[doctype] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
            + "` ORDER BY name", as_dict=True))
    return result


def smoke(company, search, manager_users=None):
    """Read saved matching archives and emit only counts, hashes and booleans."""
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb"
    assert isinstance(search, str) and search.startswith("JF-") and 3 < len(search) <= 140
    assert frappe.db.exists("Company", company)
    managers = manager_users or {}
    assert isinstance(managers, dict) and set(managers) <= {"Stock Manager", "Manufacturing Manager", "System Manager"}
    for role, user in managers.items():
        assert frappe.db.exists("User", user) and user != "Administrator" and role in frappe.get_roles(user)
    original_user, original_test = frappe.session.user, frappe.in_test
    original_response = frappe.local.response
    callbacks, context = _callbacks(), _local_context()
    tables, counts = _tab_counts()
    before, ledger = _contents(), _ledger_snapshot()
    logs = set(frappe.get_all("Error Log", pluck="name"))
    point, began, restored = "jf_tool_export_" + uuid4().hex, False, False
    checks, evidence, names, users = [], {}, {}, []

    def export(kind):
        frappe.local.response = frappe._dict({"docs": []})
        request_db = frappe.local.db
        E.export_archives(kind, company, search, include_inactive=0 if kind == "modeling" else 1)
        assert frappe.local.db is request_db
        response = frappe.response
        assert response.type == "download" and response.content_type == E.CONTENT_TYPE
        assert response.filename == E.TITLES[kind] + "档案.xlsx" and response.display_content_as == "attachment"
        from frappe.utils.response import build_response
        built = build_response()
        assert built.mimetype == E.CONTENT_TYPE and built.get_data() == response.filecontent
        assert built.headers["Content-Disposition"].startswith("attachment")
        with ZipFile(BytesIO(response.filecontent)) as archive:
            xml = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            assert not xml.findall(".//s:f", ns) and not xml.findall(".//s:hyperlinks", ns)
            assert not any(path.startswith("xl/externalLinks/") for path in archive.namelist())
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
        for kind, doctype in E.DOCTYPES.items():
            # Independent native read projection, matching the published list
            # filter contract, with no 100-row workbench limit.
            fields = ([field for field in M.RAW_FIELDS if field not in ("legacy_copy", "model_date", "reference_unit_price")]
                      if kind == "modeling" else ["legacy_number", T.TITLE[kind], "product_code", "location", "plate_type"])
            readable = T._columns(doctype, "read")
            names[kind] = frappe.get_list(doctype, filters={"company": company}, fields=["name"],
                or_filters=[[field, "like", "%" + search + "%"] for field in fields if field in readable],
                order_by="model_date desc, name asc" if kind == "modeling" else "modified desc, name asc", limit_page_length=0)
            expected = []
            for row in names[kind]:
                dto = M.read_record(row.name) if kind == "modeling" else T.read_record(row.name, kind)
                values = dto if kind == "modeling" else dto["values"]
                expected.append(tuple(None if values.get(field) in (None, "") else str(int(values[field])) if
                    frappe.get_meta(doctype).get_field(field).fieldtype == "Check" else str(values[field])
                    for field, _ in E.COLUMNS[kind]))
            assert export(kind) == expected
            evidence[kind] = {"saved_rows": len(expected), "columns": len(E.COLUMNS[kind]), "nonempty_verified": bool(expected)}
            checks.append("admin_" + kind + "_exact_saved_dto_rows_official_builder_literal_xlsx")
        assert _contents() == before and _tab_counts(tables)[1] == counts and _ledger_snapshot() == ledger
        checks.append("readonly_admin_exports_preserve_complete_sources_accounts_permissions_counts_and_ledgers")
        for role, user in managers.items():
            frappe.set_user(user)
            for kind in E.DOCTYPES:
                assert frappe.has_permission(E.DOCTYPES[kind], "export")
                export(kind)
            checks.append("existing_" + role.replace(" ", "_").lower() + "_exports_three_kinds")
        frappe.set_user("Administrator")
        nonempty = {kind: rows[0].name for kind, rows in names.items() if rows}
        if nonempty:
            _begin_rollback_only(point)
            began = True
            user = frappe.get_doc({"doctype": "User", "email": "jf-tool-export-" + uuid4().hex + "@example.invalid",
                "first_name": "JF EXPORT SYNTHETIC", "send_welcome_email": 0, "roles": [{"role": "Stock User"}]}).insert()
            users.append(user.name)
            frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company", "for_value": company,
                "apply_to_all_doctypes": 1}).insert()
            from frappe.share import add_docshare
            for kind, name in nonempty.items():
                add_docshare(E.DOCTYPES[kind], name, user=user.name, read=1, write=0, share=0, notify=0)
            frappe.set_user(user.name)
            for kind, name in nonempty.items():
                document = M._document(name) if kind == "modeling" else T._document(name, kind)
                assert document.has_permission("read") and not document.has_permission("export")
                M.read_record(name) if kind == "modeling" else T.read_record(name, kind)
                frappe.local.response = frappe._dict({"docs": []})
                try:
                    E.export_archives(kind, company, search, include_inactive=0 if kind == "modeling" else 1)
                except frappe.PermissionError:
                    pass
                else:
                    raise AssertionError("Native read share unexpectedly granted export")
                assert "filecontent" not in frappe.response and frappe.response.get("type") != "download"
            checks.append("native_stock_user_read_shares_allow_saved_dto_but_deny_export_without_attachment")
        evidence["role_exports"] = {role: role in managers for role in ("System Manager", "Stock Manager", "Manufacturing Manager")}
        evidence["read_only_export_refusal"] = bool(nonempty)
        evidence["more_than_100_saved_rows"] = any(len(rows) > 100 for rows in names.values())
    finally:
        try:
            if began:
                _rollback(point, callbacks)
            frappe.set_user(original_user)
            for user in users:
                frappe.clear_cache(user=user)
            frappe.in_test = original_test
            frappe.local.response = original_response
            _restore_callbacks(callbacks)
            _restore_local_context(context)
            assert _contents() == before and _tab_counts(tables)[1] == counts and _ledger_snapshot() == ledger
            assert set(frappe.get_all("Error Log", pluck="name")) == logs
            assert _callbacks() == callbacks and _local_context() == context
            restored = True
        finally:
            assert restored, "原生导出验证清理未核实"
    checks.append("temporary_read_share_user_fixture_rollback_complete_sources_counts_ledgers_and_context_restored")
    return {"passed": True, "checks": checks, "gate_count": len(checks), "evidence": evidence,
            "http_builder_verified": True, "http_request_verified": False, "gui_verified": False,
            "concurrent_revocation_verified": False, "source_hashes_before": before, "source_hashes_after": _contents()}
