"""Native catalog acceptance. All fixtures and Versions are always rolled back."""

import json
from decimal import Decimal

import frappe
from frappe.utils import add_days, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot


CATALOGS = ("Jifeng Product Profile", "Jifeng Mold", "Jifeng Plate")
REPORTS = ("Jifeng Product Catalog", "Jifeng Product Material Catalog",
           "Jifeng Mold Catalog", "Jifeng Plate Catalog")


def _version_has(document, field):
    rows = frappe.get_all("Version", filters={"ref_doctype": document.doctype,
                                               "docname": document.name}, pluck="data")
    return any(any(change[0] == field for change in json.loads(data).get("changed", [])) for data in rows)


def _blocked(action, error, point):
    """A rejected native operation must not change any ledger row."""
    before = _ledger_snapshot()
    frappe.db.savepoint(point)
    try:
        try:
            action()
        except error:
            pass
        else:
            raise AssertionError("原生操作接受了应拒绝的档案或引用")
    finally:
        frappe.db.rollback(save_point=point)
    assert _ledger_snapshot() == before, "拒绝操作改变了 GL/SLE"


def _report(report, company, item=None, status=None):
    from frappe.desk.query_report import run

    filters = {"company": company}
    if item:
        filters["item_code"] = item
    if status:
        filters["status"] = status
    before = _ledger_snapshot()
    result = run(report, filters=filters, ignore_prepared_report=True)
    assert _ledger_snapshot() == before, "报告 API 改变了 GL/SLE"
    assert not result.get("prepared_report"), "必须即时执行真实报告 API"
    return [row for row in result["result"] if isinstance(row, dict)]


def smoke(company, item_group, stock_uom, other_company, alternate_uom=None):
    """Requires two existing companies and migrated reports; no commit option.

    Run this serially on a disposable acceptance site. It creates only synthetic
    master/catalog data, a BOM and URL-only File metadata (no bytes or downloads).
    It does not create manufacturing, expense, stock or accounting vouchers.
    """
    frappe.only_for("System Manager")
    if not other_company or other_company == company:
        frappe.throw("请提供另一家既有验收公司以核对公司隔离")
    for name in CATALOGS:
        meta = frappe.get_meta(name)
        if not meta.track_changes or meta.is_submittable:
            frappe.throw("三类档案须启用修改历史且不能提交")
    if not all(frappe.db.exists("Report", name) for name in REPORTS):
        frappe.throw("请先迁移四份档案报告，再运行原生验收")
    gender = frappe.db.get_value("Gender", {}, "name")
    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    alternate_uom = alternate_uom or frappe.db.get_value("UOM", {"name": ["!=", stock_uom]}, "name")
    if not gender or not supplier_group or not alternate_uom or alternate_uom == stock_uom:
        frappe.throw("验收需要原生性别、供应商明细分组及两种不同单位")
    for doctype, name in (("Company", company), ("Company", other_company),
                          ("Item Group", item_group), ("UOM", stock_uom), ("UOM", alternate_uom)):
        if not frappe.db.exists(doctype, name):
            frappe.throw("请提供已存在的公司、商品分组和单位")
    if frappe.db.get_value("Item Group", item_group, "is_group"):
        frappe.throw("验收商品分组须为明细分组")

    suffix = frappe.generate_hash(length=10)
    prefix = "JF-QA-CATALOG-" + suffix
    point = "jf_catalog_" + suffix
    original_user, original_test = frappe.session.user, frappe.in_test
    checks, users, fixtures, image_urls = [], [], [], []
    before = _ledger_snapshot()
    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.set_user("Administrator")

        def insert(doctype, **values):
            document = frappe.get_doc({"doctype": doctype, **values}).insert()
            fixtures.append((doctype, document.name))
            return document

        def item(kind):
            return insert("Item", item_code=prefix + "-" + kind, item_name="档案验收 " + kind,
                          item_group=item_group, stock_uom=stock_uom, is_stock_item=1)

        def employee(kind, owner_company):
            return insert("Employee", first_name=prefix + "-" + kind, company=owner_company,
                          gender=gender, date_of_birth="1990-01-01", date_of_joining=today(), status="Active")

        def profile(code, owner_company=company, **values):
            return insert(CATALOGS[0], item_code=code, company=owner_company, list_price=11, **values)

        def mold(code, maker=None, owner_company=company, **values):
            return insert(CATALOGS[1], company=owner_company, mold_no=prefix + "-M-" + frappe.generate_hash(length=5),
                          product_code="档案验收产品号", item_code=code, maker=maker, mold_date=today(),
                          quantity=1, amount=13, **values)

        def plate(code, maker=None, modeler=None, owner_company=company, **values):
            return insert(CATALOGS[2], company=owner_company, plate_no=prefix + "-P-" + frappe.generate_hash(length=5),
                          item_code=code, maker=maker, modeler=modeler, plate_date=today(), reference_cost=17, **values)

        main, raw, hidden = item("main"), item("raw"), item("hidden")
        own_employee, hidden_employee = employee("own", company), employee("hidden", company)
        foreign_employee = employee("foreign", other_company)
        supplier = insert("Supplier", supplier_name=prefix + "-supplier", supplier_group=supplier_group,
                          supplier_type="Company")
        operation = insert("Operation", name=prefix + "-operation")
        document = profile(main.name, supplier=supplier.name, materials=[
            {"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 2, "unit_price": 3.1,
             "supplier": supplier.name, "note": "同商品第一单位"},
            {"item_code": raw.name, "uom": alternate_uom, "qty_per_unit": 10, "unit_price": 0.01,
             "note": "同商品第二单位"}])
        main_mold = mold(main.name, own_employee.name)
        main_plate = plate(main.name, own_employee.name, own_employee.name)
        foreign_item = item("foreign")
        foreign_profile = profile(foreign_item.name, other_company)
        foreign_mold = mold(foreign_item.name, foreign_employee.name, other_company)
        foreign_plate = plate(foreign_item.name, foreign_employee.name, foreign_employee.name, other_company)
        for record, field, value in ((document, "list_price", 12), (main_mold, "amount", 14),
                                     (main_plate, "reference_cost", 18)):
            record.set(field, value)
            # Frappe test mode skips Versions unless explicitly requested.
            record.save(ignore_version=False)
            assert _version_has(record, field), "缺少档案参考值修改历史"
            assert record.docstatus == 0
        checks.append("three_native_catalogs_save_update_and_version")

        # File metadata references safe synthetic remote URLs; File never writes
        # bytes for these URLs. Download permission is still the native guard.
        own_url = "https://" + prefix.lower() + ".example.invalid/own.png"
        foreign_url = "https://" + prefix.lower() + ".example.invalid/foreign.png"
        image_urls.extend((own_url, foreign_url))
        own_file = insert("File", file_name="own.png", file_url=own_url, is_private=1,
                          attached_to_doctype=document.doctype, attached_to_name=document.name)
        insert("File", file_name="foreign.png", file_url=foreign_url, is_private=1,
               attached_to_doctype=foreign_profile.doctype, attached_to_name=foreign_profile.name)
        main.image = own_url
        main.save()
        document.save(ignore_version=False)
        main_mold.image = foreign_url
        main_mold.save(ignore_version=False)
        main_plate.image = foreign_url
        main_plate.save(ignore_version=False)

        copies = []
        for source in (document, main_mold, main_plate):
            copied = frappe.copy_doc(source)
            if source.doctype == CATALOGS[0]:
                copied.item_code = item("profile-copy").name
            elif source.doctype == CATALOGS[1]:
                copied.mold_no = prefix + "-mold-copy"
                copied.copy_from = source.name
            else:
                copied.plate_no = prefix + "-plate-copy"
            copied.insert()
            fixtures.append((copied.doctype, copied.name))
            assert copied.name != source.name and copied.docstatus == 0
            copies.append(copied)
        checks.append("native_copy_preserves_catalogs_and_mold_source")

        def rejected(label, action, error=frappe.ValidationError):
            _blocked(action, error, "jf_catalog_reject")
            checks.append(label)

        def changed(record, **values):
            current = frappe.get_doc(record.doctype, record.name)
            current.update(values)
            return current.save(ignore_version=False)

        rejected("mold_copy_cycle_denied", lambda: changed(main_mold, copy_from=copies[1].name))
        rejected("mold_foreign_copy_source_denied", lambda: changed(main_mold, copy_from=foreign_mold.name))
        rejected("mold_foreign_employee_denied", lambda: changed(main_mold, maker=foreign_employee.name))
        for field in ("maker", "modeler"):
            rejected("plate_foreign_" + field + "_denied",
                     lambda field=field: changed(main_plate, **{field: foreign_employee.name}))
        rejected("mold_early_scrap_denied", lambda: changed(main_mold, scrapped=1, scrap_date=add_days(today(), -1)))
        rejected("plate_early_retirement_denied",
                 lambda: changed(main_plate, retired=1, retired_date=add_days(today(), -1)))

        bom = insert("BOM", company=company, item=main.name, quantity=1, is_default=0,
                     items=[{"item_code": raw.name, "qty": 1, "rate": 2}])
        rejected("profile_draft_bom_denied", lambda: changed(document, bom=bom.name))
        bom.submit()
        changed(document, bom=bom.name)
        wrong_item_bom = insert("BOM", company=company, item=hidden.name, quantity=1, is_default=0,
                                items=[{"item_code": raw.name, "qty": 1, "rate": 2}])
        wrong_item_bom.submit()
        rejected("profile_wrong_item_bom_denied", lambda: changed(document, bom=wrong_item_bom.name))
        foreign_bom = insert("BOM", company=other_company, item=main.name, quantity=1, is_default=0,
                             items=[{"item_code": raw.name, "qty": 1, "rate": 2}])
        foreign_bom.submit()
        rejected("profile_foreign_bom_denied", lambda: changed(document, bom=foreign_bom.name))
        bom.is_active = 0
        bom.save()
        rejected("profile_inactive_bom_denied", lambda: changed(document, bom=bom.name))
        bom.is_active = 1
        bom.save()

        def role_user(kind, roles):
            user = insert("User", email="jf-catalog-" + kind + "-" + suffix + "@example.invalid",
                          first_name="档案原生验收", send_welcome_email=0,
                          roles=[{"role": role} for role in roles])
            users.append(user.name)
            insert("User Permission", user=user.name, allow="Company", for_value=company, apply_to_all_doctypes=1)
            return user

        stock_user = role_user("stock", ["Stock Manager"])
        production_user = role_user("production", ["Manufacturing Manager", "Manufacturing User"])
        clerk = role_user("clerk", ["Stock User"])
        for user, kind in ((stock_user, "stock"), (production_user, "production")):
            code = item(kind).name
            frappe.set_user(user.name)
            assert frappe.has_permission("Employee", "select", doc=own_employee)
            assert not frappe.has_permission("Employee", "read", doc=own_employee), "不能给档案人员额外 HR read"
            role_profile = profile(code, materials=[{"item_code": raw.name, "uom": stock_uom,
                                                     "qty_per_unit": 1, "unit_price": 2}])
            if kind == "production":
                role_profile.append("operations", {"operation": operation.name, "unit_price": 3})
            else:
                role_profile.supplier = supplier.name
            role_profile.notes = "真实角色修改"
            role_profile.save(ignore_version=False)
            role_mold = mold(code, own_employee.name)
            role_plate = plate(code, own_employee.name, own_employee.name)
            assert _version_has(role_profile, "notes")
            for record, field in ((role_mold, "remark"), (role_plate, "notes")):
                record.set(field, "真实角色修改")
                record.save(ignore_version=False)
                assert _version_has(record, field)
            for report in REPORTS:
                rows = _report(report, company, code)
                expected = role_profile.name if report in REPORTS[:2] else (
                    role_mold.name if report == REPORTS[2] else role_plate.name)
                assert rows and {row["document"] for row in rows} == {expected}
                rejected(kind + "_report_foreign_company_denied", lambda report=report:
                         _report(report, other_company), frappe.PermissionError)
            for build in (lambda: profile(hidden.name, other_company),
                          lambda: mold(code, owner_company=other_company),
                          lambda: plate(code, owner_company=other_company)):
                rejected(kind + "_foreign_company_create_denied", build, frappe.PermissionError)
            checks.append(kind + "_native_create_update_versions_select_only_and_report_api")
            frappe.set_user("Administrator")

        # Native Attach Image saves can create several File records for one URL.
        # Keep those aliases, but make this exact synthetic negative fixture
        # private and attach every alias to the unreadable foreign catalog.
        foreign_files = frappe.get_all("File", filters={"file_url": foreign_url}, pluck="name")
        assert foreign_files
        for name in foreign_files:
            attachment = frappe.get_doc("File", name)
            attachment.update({"is_private": 1, "attached_to_doctype": foreign_profile.doctype,
                               "attached_to_name": foreign_profile.name, "attached_to_field": None})
            attachment.save()

        frappe.set_user(stock_user.name)
        product_rows = _report(REPORTS[0], company, main.name)
        assert len(product_rows) == 1 and product_rows[0]["document"] == document.name
        assert own_file.is_downloadable()
        own_files = [frappe.get_doc("File", name) for name in
                     frappe.get_all("File", filters={"file_url": own_url}, pluck="name")]
        allowed_images = {attachment.unique_url for attachment in own_files if attachment.is_downloadable()}
        assert product_rows[0]["image_url"] in allowed_images and product_rows[0]["image_status"] == "已上传"
        assert all(not frappe.get_doc("File", name).is_downloadable() for name in foreign_files)
        material_rows = _report(REPORTS[1], company, main.name)
        assert len(material_rows) == 2
        expected_rows = {row.name: (row.uom, Decimal(str(row.qty_per_unit)), Decimal(str(row.unit_price)))
                         for row in document.materials}
        assert len({row["material_row"] for row in material_rows}) == 2
        for row in material_rows:
            uom, quantity, price = expected_rows[row["material_row"]]
            assert row["material_code"] == raw.name and row["uom"] == uom
            assert Decimal(str(row["reference_amount"])) == quantity * price
        for report, record in ((REPORTS[2], main_mold), (REPORTS[3], main_plate)):
            row = next(row for row in _report(report, company, main.name) if row["document"] == record.name)
            assert row["image_url"] == "" and row["image_status"] == "无附件权限"
            assert row["maker"] == own_employee.name
        assert any(row["copy_from"] == main_mold.name for row in _report(REPORTS[2], company, main.name))
        checks.append("report_api_exact_sources_duplicate_material_units_and_native_file_download_guard")

        for record, values, report, status, field in (
            (document, {"inactive": 1}, REPORTS[0], "停用", "inactive"),
            (main_mold, {"scrapped": 1, "scrap_date": today(), "scrap_reason": "验收报废"}, REPORTS[2], "报废", "scrapped"),
            (main_plate, {"retired": 1, "retired_date": today(), "retired_reason": "验收停用"}, REPORTS[3], "停用", "retired"),
        ):
            changed(record, **values)
            assert _version_has(record, field)
            assert record.name in {row["document"] for row in _report(report, company, main.name, status)}
            assert record.name not in {row["document"] for row in _report(report, company, main.name, "在用")}
        changed(main_mold, scrapped=0)
        changed(main_plate, retired=0)
        main_mold.reload()
        main_plate.reload()
        assert not main_mold.scrap_date and not main_mold.scrap_reason
        assert not main_plate.retired_date and not main_plate.retired_reason
        checks.append("native_inactive_scrap_retirement_versions_and_report_history")

        # Restrict the allowed Employee and Item through actual User Permission
        # records, including another employee in the same allowed company.
        frappe.set_user("Administrator")
        insert("User Permission", user=stock_user.name, allow="Employee", for_value=own_employee.name,
               apply_to_all_doctypes=1)
        insert("User Permission", user=stock_user.name, allow="Item", for_value=main.name,
               apply_to_all_doctypes=1)
        frappe.set_user(stock_user.name)
        for build in (lambda: mold(hidden.name, own_employee.name),
                      lambda: plate(hidden.name, own_employee.name), lambda: profile(hidden.name)):
            rejected("restricted_item_read_denied", build, frappe.PermissionError)
        for build in (lambda: mold(main.name, hidden_employee.name),
                      lambda: plate(main.name, hidden_employee.name),
                      lambda: plate(main.name, own_employee.name, hidden_employee.name)):
            rejected("restricted_same_company_employee_select_denied", build, frappe.PermissionError)
        rejected("restricted_material_item_read_denied", lambda: changed(document, materials=[
            {"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 2}]), frappe.PermissionError)
        if frappe.get_doc(document.doctype, document.name).has_permission("read"):
            rejected("hidden_related_material_report_denied", lambda: _report(REPORTS[1], company, main.name),
                     frappe.PermissionError)
            material_visibility = "readable parent; hidden related material rejected with PermissionError"
        else:
            # Native User Permissions inspect child links too and can deny the
            # entire profile. Its report must then expose no recipe rows or cost.
            assert _report(REPORTS[1], company, main.name) == []
            checks.append("native_unreadable_parent_material_report_has_zero_rows")
            material_visibility = "native parent read denied; material report returned zero rows"
        for record in (foreign_profile, foreign_mold, foreign_plate):
            rejected("foreign_catalog_document_read_denied", lambda record=record:
                     frappe.get_doc(record.doctype, record.name).check_permission("read"), frappe.PermissionError)

        for actor in (clerk.name, "Guest"):
            frappe.set_user(actor)
            for build in (lambda: profile(hidden.name), lambda: mold(main.name), lambda: plate(main.name)):
                rejected("unprivileged_catalog_create_denied", build, frappe.PermissionError)
            for doctype in CATALOGS:
                assert not frappe.has_permission(doctype, "write")
                assert not frappe.has_permission(doctype, "read")
            for report in REPORTS:
                rejected("unprivileged_report_api_denied", lambda report=report: _report(report, company),
                         frappe.PermissionError)
        assert _ledger_snapshot() == before, "档案参考值/保存/历史/复制/报告改变了 GL/SLE"
        checks.append("all_catalog_actions_preserve_entire_gl_sle_hashes")
    finally:
        frappe.set_user(original_user)
        try:
            # Include aliases created by native Item/catalog/BOM attachment hooks,
            # including ones created by later edits or before a failed gate.
            if image_urls:
                fixtures.extend(("File", name) for name in frappe.get_all(
                    "File", filters={"file_url": ["in", image_urls]}, pluck="name")
                    if ("File", name) not in fixtures)
        finally:
            try:
                _end_rollback_only(point)
            finally:
                frappe.in_test = original_test
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in fixtures:
                    frappe.clear_document_cache(doctype, name)
    assert all(not frappe.db.exists(doctype, name) for doctype, name in fixtures), "回滚后存在验收档案或主数据"
    assert not frappe.db.exists("Version", {"docname": ["in", [name for _, name in fixtures]]}), "回滚后存在验收 Version"
    assert _ledger_snapshot() == before, "回滚后 GL/SLE 有差异"
    return {"passed": True, "checks": checks, "business_data": "rolled_back", "report_api_verified": True,
            "roles": {"stock": ["Stock Manager"], "production": ["Manufacturing Manager", "Manufacturing User"]},
            "images": "URL-only metadata; native download permissions verified; no bytes written or fetched",
            "file_metadata_records_tracked": sum(doctype == "File" for doctype, _ in fixtures),
            "material_visibility": material_visibility,
            "concurrency": "serial rollback experiment; not raced"}


def _profile_fixture_png(token):
    """A tiny local PNG with unique bytes, never a remote download/dedup blob."""
    import struct
    import zlib

    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"tEXt", b"QA\x00" + token.encode())
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff")) + chunk(b"IEND", b""))


def _profile_rollback(point, previous):
    """Savepoint rollback plus only this experiment's native cleanup callbacks.

    Native rollback(save_point=...) does not run File.on_rollback. Its cleanup
    must run AFTER the fixture File aliases disappear, then the caller's queues
    are restored intact. Failure is reported; no manual filesystem delete or
    fallback commit is attempted.
    """
    from jifeng_erp.personnel_changes_validation import _restore_callbacks

    callbacks = {name: list(getattr(frappe.db, name)._functions)[len(previous[name]):]
                 for name in ("before_rollback", "after_rollback")}
    failures = []

    def cleanup(action, label):
        try:
            action()
        except Exception as error:
            failures.append(label + ":" + type(error).__name__)

    try:
        for callback in callbacks["before_rollback"]:
            cleanup(callback, "before_rollback")
        cleanup(lambda: _end_rollback_only(point), "database_rollback")
        for callback in callbacks["after_rollback"]:
            cleanup(callback, "after_rollback")
    finally:
        _restore_callbacks(previous)
    if failures:
        raise AssertionError("产品档案验收回滚清理失败：" + ",".join(failures))


def _profile_restore_caller(user, in_test, users, records, callbacks, context):
    from jifeng_erp.personnel_changes_validation import _restore_callbacks
    from jifeng_erp.personnel_separation_validation import _restore_local_context

    try:
        frappe.in_test = in_test
        frappe.set_user(user)
        for actor in users:
            frappe.clear_cache(user=actor)
        for kind, name in records:
            # Native clear_document_cache adds after_commit/after_rollback
            # callbacks. Finish all invalidations before restoring the caller.
            frappe.clear_document_cache(kind, name)
    finally:
        _restore_callbacks(callbacks)
        _restore_local_context(context)


def _profile_audit(actions, primary=None):
    """Run every rollback audit and preserve the first business failure."""
    failures = []
    for label, action in actions:
        try:
            result = action()
            if result is False:
                raise AssertionError(label)
        except Exception as error:
            error.add_note("产品原文回滚阶段：" + label)
            failures.append((label, error))
    if failures:
        original = primary if primary is not None else failures[0][1]
        for label, error in failures:
            original.add_note("产品原文回滚审计失败：" + label + ":" + type(error).__name__ + ":" + str(error))
        if primary is None:
            raise original


def delta_smoke(company, item_group, stock_uom, other_company):
    """One serial, rollback-only experiment for the six independent fields.

    After migration, call directly from the site's Python process once; do not
    use bench execute, whose error fallback may invoke a failed helper twice::

        result = delta_smoke(company="Jifeng ERP UI Validation", item_group="Products",
                             stock_uom="Nos", other_company="Jifeng ERP Validation")

    This calls native controllers/File.insert as the real catalog roles. It
    creates a tiny local PNG for each upload; no image is downloaded. Database
    rows, callback queues, local context and exact generated filesystem paths
    are audited after native rollback cleanup. HTTP byte serving and Desk image
    rendering require separate UI acceptance.
    """
    from pathlib import Path

    from jifeng_erp.personnel_changes_validation import (
        _callbacks, _digest, _tab_counts,
    )
    from jifeng_erp.personnel_separation import _atomic
    from jifeng_erp.personnel_separation_validation import _local_context

    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb", "全表回滚审计需要当前 MariaDB 站点"
    assert other_company and other_company != company
    for doctype, name in (("Company", company), ("Company", other_company),
                          ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "请提供已存在的公司、明细商品分组及单位"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    doctype = CATALOGS[0]
    original_fields = {"legacy_number": "Data", "legacy_code": "Data", "display_name": "Data",
                       "display_uom": "Data", "legacy_remarks": "Small Text", "profile_image": "Attach Image"}
    meta = frappe.get_meta(doctype)
    for name, kind in original_fields.items():
        field = meta.get_field(name)
        assert field and field.fieldtype == kind and not field.reqd and not field.fetch_from, "原文字段尚未迁移"
    assert meta.track_changes and meta.get_field("item_code").unique

    suffix = frappe.generate_hash(length=10)
    prefix, point = "JF-QA-PROFILE-" + suffix, "jf_profile_" + suffix
    original_user, original_test = frappe.session.user, frappe.in_test
    ledger_before, (tables, counts) = _ledger_snapshot(), _tab_counts()
    callbacks_before, context_before = _callbacks(), _local_context()
    item_before = _digest(frappe.get_all("Item", fields=["*"], order_by="name", limit_page_length=0))
    checks, records, users, objects, files = [], set(), [], [], []
    roots = [Path(frappe.get_site_path(*parts)) for parts in (("private", "files"), ("public", "files"))]

    def fixture_paths():
        return {str(path.resolve()) for root in roots for path in root.glob(prefix + "*")}

    assert fixture_paths() == set(), "验收前已存在本次随机前缀文件"
    evidence, current, primary = {}, "fixtures", None
    generated_paths = set()
    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.set_user("Administrator")

        def insert(kind, **values):
            document = frappe.get_doc({"doctype": kind, **values})
            objects.append(document)
            document.insert()
            records.add((kind, document.name))
            records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            return document

        def item(kind):
            return insert("Item", item_code=prefix + "-" + kind, item_name="业务商品 " + kind,
                          item_group=item_group, stock_uom=stock_uom, is_stock_item=1)

        def changed(name, **values):
            document = frappe.get_doc(doctype, name)
            document.update(values)
            return document.save(ignore_version=False)

        def rejected(label, action, error=frappe.ValidationError):
            nonlocal current
            current = label
            before, callbacks, context = _ledger_snapshot(), _callbacks(), _local_context()
            tracked_counts = _tab_counts(tables)[1]
            try:
                @_atomic
                def attempt():
                    action()
                    raise AssertionError("原生操作接受了应拒绝的产品原文或图片")
                try:
                    attempt()
                except error:
                    pass
            finally:
                # _atomic restores failed document callbacks and native saving
                # context. Do not discard pre-existing image cleanup callbacks.
                assert _callbacks() == callbacks and _local_context() == context
                assert _ledger_snapshot() == before and _tab_counts(tables)[1] == tracked_counts
            checks.append(label)

        native_items = [item(kind) for kind in ("stock", "production", "foreign", "hidden")]
        item_names = [document.name for document in native_items]
        item_fixture_hash = _digest(frappe.get_all("Item", filters={"name": ["in", item_names]},
                                   fields=["*"], order_by="name", limit_page_length=0))
        foreign = insert(doctype, item_code=native_items[2].name, company=other_company)
        foreign_file = insert("File", file_name=prefix + "-foreign.png",
                              content=_profile_fixture_png(prefix + "-foreign"), is_private=1,
                              attached_to_doctype=doctype, attached_to_name=foreign.name,
                              attached_to_field="profile_image")
        files.append(foreign_file)
        foreign_url = foreign_file.file_url
        assert foreign_url.startswith("/private/files/" + prefix)
        changed(foreign.name, profile_image=foreign_url)

        def role_user(kind, roles):
            user = insert("User", email=prefix.lower() + "-" + kind + "@example.invalid",
                          first_name="产品原文验收", send_welcome_email=0,
                          roles=[{"role": role} for role in roles])
            users.append(user.name)
            insert("User Permission", user=user.name, allow="Company", for_value=company,
                   apply_to_all_doctypes=1)
            return user

        actors = [(role_user("stock", ["Stock Manager"]), native_items[0], "stock"),
                  (role_user("production", ["Manufacturing Manager", "Manufacturing User"]),
                   native_items[1], "production")]
        documents = []
        for user, source_item, kind in actors:
            current = kind + "_native_original_fields_and_file"
            frappe.set_user(user.name)
            document = insert(doctype, item_code=source_item.name, company=company, old_number="旧号保留",
                              notes="注意事项保留", requirements="工艺要求保留", packaging="包装要求保留",
                              list_price=11, blank_price=7, weight=2)
            assert all(not document.get(name) for name in original_fields)
            assert (document.product_name, document.stock_uom, document.image) == (
                source_item.item_name, source_item.stock_uom, source_item.image)
            payload = {"legacy_number": "000017", "legacy_code": "旧代码/" + kind,
                       "display_name": "旧名称 " + kind, "display_uom": "对（原文非 UOM）",
                       "legacy_remarks": "备注第一行\n备注第二行"}
            document = changed(document.name, **payload)
            document.reload()
            assert all(document.get(name) == value for name, value in payload.items())
            assert all(_version_has(document, name) for name in payload)
            assert (document.old_number, document.notes, document.requirements, document.packaging) == (
                "旧号保留", "注意事项保留", "工艺要求保留", "包装要求保留")
            assert (Decimal(str(document.list_price)), Decimal(str(document.blank_price)),
                    Decimal(str(document.weight)), Decimal(str(document.material_unit_cost)),
                    Decimal(str(document.processing_unit_cost))) == tuple(map(Decimal, (11, 7, 2, 0, 0)))
            checks.append(kind + "_native_optional_original_text_save_and_versions_leave_business_fields_unchanged")

            attachment = insert("File", file_name=prefix + "-" + kind + ".png",
                                content=_profile_fixture_png(prefix + "-" + kind), is_private=1,
                                attached_to_doctype=doctype, attached_to_name=document.name,
                                attached_to_field="profile_image")
            files.append(attachment)
            assert attachment.file_url.startswith("/private/files/" + prefix)
            assert Path(attachment.get_full_path()).is_file()
            assert attachment.get_content() == _profile_fixture_png(prefix + "-" + kind)
            assert attachment.is_downloadable()
            attachment.check_permission("read")
            assert attachment.on_rollback in list(frappe.db.after_rollback._functions), "原生 File 未注册磁盘清理"
            document = changed(document.name, profile_image=attachment.file_url)
            assert _version_has(document, "profile_image")
            assert document.image == source_item.image
            documents.append((user, document, attachment))
            checks.append(kind + "_native_private_file_upload_exact_profile_field_binding_and_version")
            frappe.set_user("Administrator")

        user, document, attachment = documents[0]
        frappe.set_user(user.name)
        rejected("remote_image_url_rejected", lambda: changed(document.name,
                 profile_image="https://" + prefix.lower() + ".example.invalid/image.png"))
        rejected("public_image_url_rejected", lambda: changed(document.name,
                 profile_image="/files/" + prefix + "-unregistered.png"))
        rejected("private_image_without_file_rejected", lambda: changed(document.name,
                 profile_image="/private/files/" + prefix + "-unregistered.png"))
        assert not foreign_file.is_downloadable()
        rejected("foreign_profile_private_image_rejected_without_rebinding", lambda:
                 changed(document.name, profile_image=foreign_url))
        assert frappe.db.get_value("File", foreign_file.name, "attached_to_name") == foreign.name
        rejected("new_unsaved_profile_image_rejected", lambda:
                 insert(doctype, item_code=native_items[3].name, company=company,
                        profile_image=attachment.file_url))

        # Same URL is not enough: an own readable File attached to a different
        # field must not become the product image unless there is an exact alias.
        other_field = insert("File", file_name=prefix + "-other-field.png",
                             content=_profile_fixture_png(prefix + "-other-field"), is_private=1,
                             attached_to_doctype=doctype, attached_to_name=document.name,
                             attached_to_field="patent_image")
        files.append(other_field)
        assert other_field.is_downloadable()
        rejected("own_private_attachment_on_wrong_field_rejected", lambda:
                 changed(document.name, profile_image=other_field.file_url))

        cleared = changed(document.name, **{name: "" for name in original_fields})
        assert all(not cleared.get(name) for name in original_fields)
        assert (cleared.product_name, cleared.stock_uom, cleared.image) == (
            native_items[0].item_name, stock_uom, native_items[0].image)
        assert _version_has(cleared, "profile_image")
        assert Path(attachment.get_full_path()).is_file(), "清空字段不得擅自删除原生附件"
        checks.append("cleared_original_fields_keep_native_identity_and_item_fallback_with_image_history")
        rejected("company_select_user_permission_rejected", lambda:
                 insert(doctype, item_code=native_items[3].name, company=other_company), frappe.PermissionError)

        frappe.set_user("Administrator")
        profile_tables = {doctype, *(field.options for field in meta.get_table_fields())}

        def profile_rows_hash():
            return _digest({kind: frappe.get_all(kind, fields=["*"], order_by="name", limit_page_length=0)
                            for kind in sorted(profile_tables | {"Version"})})

        identity_before = profile_rows_hash()
        rejected("saved_profile_company_rebinding_rejected_even_for_administrator", lambda:
                 changed(document.name, company=other_company))
        rejected("saved_profile_item_rebinding_rejected_even_for_administrator", lambda:
                 changed(document.name, item_code=native_items[3].name))
        assert profile_rows_hash() == identity_before, "身份改绑拒绝改变了档案父子行或 Version"
        assert frappe.db.get_value(doctype, document.name, ["item_code", "company"]) == (
            native_items[0].name, company)
        checks.append("identity_rejections_keep_all_profiles_children_and_versions_unchanged")
        rejected("same_item_second_company_profile_still_unique_rejected", lambda:
                 insert(doctype, item_code=native_items[0].name, company=other_company), frappe.DuplicateEntryError)
        insert("User Permission", user=user.name, allow="Item", for_value=native_items[0].name,
               apply_to_all_doctypes=1)
        frappe.set_user(user.name)
        rejected("hidden_item_read_user_permission_rejected", lambda:
                 insert(doctype, item_code=native_items[3].name, company=company), frappe.PermissionError)
        assert _ledger_snapshot() == ledger_before
        frappe.set_user("Administrator")
        reloaded = frappe.get_all("Item", filters={"name": ["in", item_names]}, fields=["*"],
                                  order_by="name", limit_page_length=0)
        assert _digest(reloaded) == item_fixture_hash, "保存独立原文改变了共享 Item"
        checks.append("whole_fixture_items_and_gl_sle_unchanged_during_original_field_actions")
        evidence = {"roles": [frappe.get_roles(actor.name) for actor, _, _ in actors],
                    "private_file_records": len(files), "local_png_bytes": [file.file_size for file in files],
                    "native_file_rollback_callbacks_registered": all(
                        file.on_rollback in list(frappe.db.after_rollback._functions) for file in files)}
    except Exception as error:
        primary = error
        error.add_note("产品原文 delta failed at gate: " + current)
        raise
    finally:
        def collect_records():
            nonlocal generated_paths
            frappe.set_user("Administrator")
            generated_paths = fixture_paths() | {str(Path(file.get_full_path()).resolve()) for file in files}
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            urls = [file.file_url for file in files]
            if urls:
                records.update(("File", name) for name in frappe.get_all("File",
                               filters={"file_url": ["in", urls]}, pluck="name"))
        try:
            _profile_audit([
                ("collect_fixture_records_and_paths", collect_records),
                ("native_database_and_file_rollback", lambda: _profile_rollback(point, callbacks_before)),
                ("cache_invalidation_then_restore_caller", lambda: _profile_restore_caller(
                    original_user, original_test, users, records, callbacks_before, context_before)),
                ("fixture_rows_absent", lambda: all(not frappe.db.exists(kind, name) for kind, name in records)),
                ("all_native_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
                ("all_item_fields_restored", lambda: _digest(frappe.get_all(
                    "Item", fields=["*"], order_by="name", limit_page_length=0)) == item_before),
                ("whole_gl_sle_hashes_restored", lambda: _ledger_snapshot() == ledger_before),
                ("generated_private_public_file_paths_absent", lambda: fixture_paths() == set()
                    and all(not Path(path).exists() for path in generated_paths)),
                ("caller_callback_queues_restored", lambda: _callbacks() == callbacks_before),
                ("caller_realtime_and_saving_context_restored", lambda: _local_context() == context_before),
            ], primary=primary)
        except Exception as error:
            error.add_note("产品原文 delta final audit at gate: " + current)
            error.add_note("已完成业务门：" + json.dumps(checks, ensure_ascii=False))
            raise
    checks.append("rollback_all_records_versions_table_counts_item_ledgers_callbacks_and_generated_file_paths")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "evidence": evidence, "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(),
            "file_metadata_records_tracked": sum(kind == "File" for kind, _ in records),
            "filesystem": "native File rollback cleanup; exact generated paths absent after rollback",
            "remote_downloads": False, "gui_verified": False, "http_image_download_verified": False}
