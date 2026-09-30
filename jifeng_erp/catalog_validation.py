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
    checks, users, fixtures = [], [], []
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
            record.save()
            assert _version_has(record, field), "缺少档案参考值修改历史"
            assert record.docstatus == 0
        checks.append("three_native_catalogs_save_update_and_version")

        # File metadata references safe synthetic remote URLs; File never writes
        # bytes for these URLs. Download permission is still the native guard.
        own_url = "https://" + prefix.lower() + ".example.invalid/own.png"
        foreign_url = "https://" + prefix.lower() + ".example.invalid/foreign.png"
        own_file = insert("File", file_name="own.png", file_url=own_url, is_private=1,
                          attached_to_doctype=document.doctype, attached_to_name=document.name)
        insert("File", file_name="foreign.png", file_url=foreign_url, is_private=1,
               attached_to_doctype=foreign_profile.doctype, attached_to_name=foreign_profile.name)
        main.image = own_url
        main.save()
        document.save()
        main_mold.image = foreign_url
        main_mold.save()
        main_plate.image = foreign_url
        main_plate.save()

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
            return current.save()

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
            role_profile.save()
            role_mold = mold(code, own_employee.name)
            role_plate = plate(code, own_employee.name, own_employee.name)
            assert _version_has(role_profile, "notes")
            for record, field in ((role_mold, "remark"), (role_plate, "notes")):
                record.set(field, "真实角色修改")
                record.save()
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

        frappe.set_user(stock_user.name)
        product_rows = _report(REPORTS[0], company, main.name)
        assert len(product_rows) == 1 and product_rows[0]["document"] == document.name
        assert product_rows[0]["image_url"] == own_file.unique_url and product_rows[0]["image_status"] == "已上传"
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
        rejected("hidden_related_material_report_denied", lambda: _report(REPORTS[1], company, main.name),
                 frappe.PermissionError)
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
            "concurrency": "serial rollback experiment; not raced"}
