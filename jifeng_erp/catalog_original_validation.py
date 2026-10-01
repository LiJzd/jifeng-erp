"""Small native Product Catalog experiment; direct Python once, always rollback.

smoke(company, other_company, item_group, stock_uom) exercises the report's
independent original columns and exact private image authorization. It does
not call the older catalog smoke suites, change DocPerm/schema, bypass native
permissions, fetch images, commit, or perform GUI/HTTP download acceptance.
"""

from pathlib import Path

import frappe
from frappe.utils.file_manager import get_content_hash

from jifeng_erp.catalog_reports import ORIGINAL_FIELDS, PROFILE, product_catalog
from jifeng_erp.catalog_validation import _profile_audit, _profile_fixture_png, _profile_restore_caller
from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import (
    _callbacks, _digest, _employee_contents, _restore_callbacks, _tab_counts,
)
from jifeng_erp.personnel_separation_validation import _local_context, _user_contents


REPORT = "Jifeng Product Catalog"


def _contents():
    """Hash inputs only; never return existing private values to the caller."""
    result = {}
    for doctype in ("Item", PROFILE, "File", "Version"):
        table = "`tab" + doctype.replace("`", "``") + "`"
        result[doctype] = {"parent": frappe.db.sql("SELECT * FROM " + table + " ORDER BY name", as_dict=True),
                           "children": {}}
        for field in frappe.get_meta(doctype).get_table_fields():
            child = "`tab" + field.options.replace("`", "``") + "`"
            result[doctype]["children"][field.fieldname] = frappe.db.sql(
                "SELECT * FROM " + child + " WHERE parenttype=%s AND parentfield=%s ORDER BY name",
                (doctype, field.fieldname), as_dict=True)
    return result


def _rollback(point, previous):
    """Native File cleanup runs only after confirmed database rollback."""
    added, failures = {}, []
    for field in ("before_rollback", "after_rollback"):
        current = list(getattr(frappe.db, field)._functions)
        if current[:len(previous[field])] != previous[field]:
            failures.append(field + ":caller callback prefix changed")
            added[field] = []
        else:
            added[field] = current[len(previous[field]):]

    def cleanup(action, label):
        try:
            action()
        except Exception as error:
            failures.append(label + ":" + type(error).__name__)

    try:
        for callback in added["before_rollback"]:
            cleanup(callback, "before_rollback")
        try:
            _end_rollback_only(point)
        except Exception as error:
            failures.append("database_rollback:" + type(error).__name__)
        else:
            for callback in added["after_rollback"]:
                cleanup(callback, "after_rollback")
    finally:
        _restore_callbacks(previous)
    if failures:
        raise AssertionError("产品原文报告验收清理失败：" + ",".join(failures))


def smoke(company, other_company, item_group, stock_uom):
    """Root runs once in its exclusive rollback-only direct Python window."""
    from frappe.desk.query_report import run

    frappe.only_for("System Manager")
    assert company != other_company
    for doctype, name in (("Company", company), ("Company", other_company),
                          ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "provide existing acceptance companies, item group and UOM"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    assert frappe.db.exists("Report", REPORT)
    meta = frappe.get_meta(PROFILE)
    assert all(meta.get_field(field) for field in (*ORIGINAL_FIELDS, "profile_image")), "original fields not migrated"

    original_user, original_test = frappe.session.user, frappe.in_test
    tables, counts = _tab_counts()
    changed_types = {"Item", PROFILE, "File", "Version", "User", "User Permission", "Series", "DocShare",
                     "DefaultValue", "Email Queue", "Notification Log", "Communication"}
    for doctype in ("Item", PROFILE, "User"):
        changed_types.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    assert all(tables["tab" + doctype] == "InnoDB" for doctype in changed_types)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    data_before, users_before = _digest(_contents()), _digest(_user_contents())
    employees_before, ledger_before = _digest(_employee_contents()), _ledger_snapshot()
    logs_before = set(frappe.get_all("Error Log", pluck="name"))
    callbacks_before, context_before = _callbacks(), _local_context()
    suffix = frappe.generate_hash(length=10)
    prefix, point = "JF-QA-ORIG-REPORT-" + suffix, "jf_original_report_" + suffix
    roots = [Path(frappe.get_site_path(*parts)) for parts in (("private", "files"), ("public", "files"))]

    def paths():
        return {str(path.resolve()) for root in roots for path in root.glob(prefix + "*")}

    assert not paths(), "random fixture prefix already exists on disk"
    checks, records, objects, files, users, generated = [], set(), [], [], [], set()
    current, primary, evidence = "fixtures", None, {}
    _begin_rollback_only(point)
    try:
        frappe.set_user("Administrator")
        frappe.in_test = True

        def insert(doctype, **values):
            document = frappe.get_doc({"doctype": doctype, **values})
            objects.append(document)
            document.insert()
            records.add((doctype, document.name))
            records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            return document

        def item(kind):
            return insert("Item", item_code=prefix + "-" + kind, item_name="业务商品 " + kind,
                          item_group=item_group, stock_uom=stock_uom, is_stock_item=1)

        def upload(document, field, kind):
            content = _profile_fixture_png(prefix + "-" + kind)
            assert not frappe.db.exists("File", {"content_hash": get_content_hash(content)}), "fixture cannot reuse a business blob"
            attachment = insert("File", file_name=prefix + "-" + kind + ".png", content=content,
                is_private=1, attached_to_doctype=document.doctype, attached_to_name=document.name, attached_to_field=field)
            files.append(attachment)
            assert attachment.file_url.startswith("/private/files/" + prefix)
            assert attachment.get_content() == content and Path(attachment.get_full_path()).is_file()
            assert attachment.on_rollback in list(frappe.db.after_rollback._functions)
            document.set(field, attachment.file_url)
            previous = frappe.in_test
            try:
                frappe.in_test = False
                document.save()
            finally:
                frappe.in_test = previous
            return attachment

        main_item, empty_item, foreign_item = [item(kind) for kind in ("main", "empty", "foreign")]
        business_file = upload(main_item, "image", "business")
        # The second Item shares a business image, while its independent
        # original fields and Profile image are empty.
        empty_item.image = business_file.file_url
        empty_item.save()
        originals = dict(legacy_number="000007", legacy_code="代码/原文", display_name="名称原文",
                         display_uom="对（不是UOM）", legacy_remarks="第一行\n第二行")
        profile = insert(PROFILE, item_code=main_item.name, company=company, old_number="旧号仍独立",
                         list_price=11, **originals)
        blank = insert(PROFILE, item_code=empty_item.name, company=company)
        foreign = insert(PROFILE, item_code=foreign_item.name, company=other_company, display_name="外公司合成原文")
        archive_file = upload(profile, "profile_image", "archive")
        alias = insert("File", file_name=prefix + "-alias.png", file_url=archive_file.file_url,
            is_private=1, attached_to_doctype=PROFILE, attached_to_name=profile.name, attached_to_field="patent_image")
        files.append(alias)
        assert alias.name != archive_file.name and alias.file_url == archive_file.file_url
        actor = insert("User", email=prefix.lower() + "@example.invalid", first_name="原文报告验收",
                       send_welcome_email=0, roles=[{"role": "Stock Manager"}])
        users.append(actor.name)
        insert("User Permission", user=actor.name, allow="Company", for_value=company, apply_to_all_doctypes=1)
        frappe.clear_cache(user=actor.name)
        frappe.set_user(actor.name)
        assert "Stock Manager" in frappe.get_roles() and "System Manager" not in frappe.get_roles()
        evidence["roles"] = sorted(frappe.get_roles())
        evidence["gui_verified"] = evidence["http_image_download_verified"] = False
        evidence["field_permlevel_revocation"] = "model-tested; native metadata and DocPerm unchanged"

        def read_state():
            return (_digest(_contents()), _digest(_user_contents()), _digest(_employee_contents()),
                    _ledger_snapshot(), _callbacks(), _local_context())

        def read(item_code=None):
            before = read_state()
            filters = {"company": company, **({"item_code": item_code} if item_code else {})}
            # Establish actual core access before the wrapper, which logs
            # unexpected report failures. Permission insufficiency stays a
            # native rejection with zero manufactured Error Logs.
            product_catalog(filters)
            result = run(REPORT, filters=filters, ignore_prepared_report=True)
            assert not result.get("prepared_report")
            assert before == read_state(), "report read mutated native data or context"
            assert set(frappe.get_all("Error Log", pluck="name")) == logs_before
            return result

        def deny(filters):
            before = read_state()
            try:
                product_catalog(filters)
            except frappe.PermissionError:
                pass
            else:
                raise AssertionError("restricted report unexpectedly readable")
            assert before == read_state(), "report denial mutated native data or context"
            assert set(frappe.get_all("Error Log", pluck="name")) == logs_before

        current = "stock_manager_business_and_original_columns_exact"
        result = read(main_item.name)
        rows = [row for row in result["result"] if isinstance(row, dict)]
        assert len(rows) == 1 and rows[0]["document"] == profile.name
        row = rows[0]
        assert (row["product_name"], row["stock_uom"]) == (main_item.item_name, stock_uom)
        assert {field: row[field] for field in ORIGINAL_FIELDS} == originals
        assert row["old_number"] == "旧号仍独立" and not row.get("profile_image")
        assert {*ORIGINAL_FIELDS, "profile_image_url", "profile_image_status"}.issubset(
            {column["fieldname"] for column in result["columns"]})
        checks.append(current)

        current = "independent_private_profile_image_exact_unique_url"
        exact = [frappe.get_doc("File", name) for name in frappe.get_all("File", filters={
            "file_url": archive_file.file_url, "is_private": 1, "attached_to_doctype": PROFILE,
            "attached_to_name": profile.name, "attached_to_field": "profile_image"}, pluck="name")]
        allowed = {file.unique_url for file in exact if file.has_permission("read") and file.is_downloadable()}
        assert allowed and row["profile_image_url"] in allowed and row["profile_image_status"] == "已上传"
        business_urls = {frappe.get_doc("File", name).unique_url for name in frappe.get_all(
            "File", filters={"file_url": business_file.file_url}, pluck="name")
            if frappe.get_doc("File", name).is_downloadable()}
        assert row["image_url"] in business_urls and row["image_url"] != row["profile_image_url"]
        assert frappe.db.get_value(PROFILE, profile.name, "profile_image") == archive_file.file_url
        checks.append(current)

        current = "blank_original_fields_and_image_do_not_fall_back_to_item"
        blank_rows = [row for row in read(empty_item.name)["result"] if isinstance(row, dict)]
        assert len(blank_rows) == 1 and blank_rows[0]["document"] == blank.name
        assert all(blank_rows[0][field] in (None, "") for field in ORIGINAL_FIELDS)
        assert blank_rows[0]["profile_image_url"] == "" and blank_rows[0]["profile_image_status"] == "未上传"
        assert blank_rows[0]["image_url"] in business_urls
        checks.append(current)

        current = "native_file_read_restriction_does_not_use_readable_wrong_field_alias"
        frappe.set_user("Administrator")
        assert alias.folder and frappe.db.exists("File", alias.folder)
        for name in (alias.name, alias.folder):
            insert("User Permission", user=actor.name, allow="File", for_value=name,
                   apply_to_all_doctypes=0, applicable_for="File")
        frappe.clear_cache(user=actor.name)
        frappe.set_user(actor.name)
        assert frappe.get_doc(PROFILE, profile.name).has_permission("read")
        assert frappe.get_doc("File", alias.name).has_permission("read")
        assert all(not frappe.get_doc("File", file.name).has_permission("read")
                   and frappe.get_doc("File", file.name).is_downloadable() for file in exact)
        restricted = [row for row in read(main_item.name)["result"] if isinstance(row, dict)][0]
        assert restricted["profile_image_url"] == "" and restricted["profile_image_status"] == "无附件权限"
        assert archive_file.file_url not in str(restricted) and alias.name not in str(restricted)
        assert all(file.name not in str(restricted) for file in exact)
        evidence["file_restriction"] = "File.read denied by File-specific UP; Profile.read and is_downloadable remain true"
        checks.append(current)

        current = "native_company_and_hidden_item_permissions"
        # Native query_report logs internally raised report errors. These
        # denial probes call the same report core directly, without logger
        # patches; they are not a claim about HTTP or the query wrapper.
        deny({"company": other_company})
        assert not frappe.get_doc(PROFILE, foreign.name).has_permission("read")
        frappe.set_user("Administrator")
        insert("User Permission", user=actor.name, allow="Item", for_value=main_item.name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=actor.name)
        frappe.set_user(actor.name)
        deny({"company": company, "item_code": empty_item.name})
        assert set(frappe.get_all("Error Log", pluck="name")) == logs_before
        checks.append(current)
        checks.append("all_report_reads_and_denials_preserve_items_profiles_files_versions_accounts_and_ledger")
        evidence["local_png_sizes"] = [business_file.file_size, archive_file.file_size]
        evidence["denial_entry_point"] = "product_catalog core; positive reads use native query_report.run"
    except Exception as error:
        primary = error
        error.add_note("original product report native failed at gate: " + current)
        raise
    finally:
        def collect():
            frappe.set_user("Administrator")
            generated.update(paths())
            generated.update(str(Path(file.get_full_path()).resolve()) for file in files)
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            urls = [file.file_url for file in files]
            if urls:
                records.update(("File", name) for name in frappe.get_all("File", filters={"file_url": ["in", urls]}, pluck="name"))

        _profile_audit([
            ("collect_exact_file_paths_and_alias_rows", collect),
            ("native_database_then_file_callback_rollback", lambda: _rollback(point, callbacks_before)),
            ("restore_caller_after_cache_invalidation", lambda: _profile_restore_caller(
                original_user, original_test, users, records, callbacks_before, context_before)),
            ("all_fixture_rows_absent", lambda: all(not frappe.db.exists(kind, name) for kind, name in records)),
            ("all_tab_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("all_catalog_item_file_version_content_restored", lambda: _digest(_contents()) == data_before),
            ("all_employee_and_children_restored", lambda: _digest(_employee_contents()) == employees_before),
            ("all_user_auth_roles_up_restored", lambda: _digest(_user_contents()) == users_before),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger_before),
            ("exact_generated_file_paths_absent", lambda: not paths() and all(not Path(path).exists() for path in generated)),
            ("caller_queues_restored", lambda: _callbacks() == callbacks_before),
            ("caller_local_context_restored", lambda: _local_context() == context_before),
            ("zero_error_logs_added", lambda: set(frappe.get_all("Error Log", pluck="name")) == logs_before),
        ], primary=primary)
    checks.extend(("rollback_all_native_counts_catalog_item_accounts_versions_ledgers",
                   "rollback_native_file_cleanup_and_caller_context"))
    return {"passed": True, "business_data": "rolled_back", "gate_count": len(checks), "checks": checks,
            "evidence": evidence, "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "data_hash_before": data_before, "data_hash_after": _digest(_contents()),
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(),
            "filesystem": "native File.on_rollback; exact generated paths absent", "remote_downloads": False,
            "gui_verified": False, "http_image_download_verified": False}
