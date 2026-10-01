"""Small, serial native original-product acceptance; always roll back.

Root invokes smoke(company,other_company,item_group,stock_uom) directly once
after migration in an exclusive window. Never use bench execute fallback,
commit, ignore_permissions, metadata grants, GUI or external downloads.
"""

from pathlib import Path
from uuid import uuid4

import frappe
from frappe.utils.file_manager import get_content_hash

from jifeng_erp.catalog_original_validation import _rollback
from jifeng_erp.catalog_validation import _profile_audit, _profile_fixture_png, _profile_restore_caller, _version_has
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_archive import (
    COLLISION, PROFILE, init_archive, list_archives, list_category_choices, list_image_attachments,
    read_archive, resolve_creation, save_archive,
)
from jifeng_erp.product_category_validation import _native_contents as _category_contents
from jifeng_erp.product_dictionary_validation import _document_snapshot, _sql_row_hash


def _contents():
    result = _category_contents()
    extra = {"Item Price", "Supplier"}
    extra.update(field.options for field in frappe.get_meta("Supplier").get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(extra)})
    return result


def _recipe(name):
    """Only the two native child audit timestamps may change on parent save."""
    result = {}
    for field in frappe.get_meta(PROFILE).get_table_fields():
        rows = frappe.db.sql("SELECT * FROM `tab" + field.options.replace("`", "``")
            + "` WHERE parent=%s AND parenttype=%s AND parentfield=%s ORDER BY idx,name",
            (name, PROFILE, field.fieldname), as_dict=True)
        result[field.fieldname] = _sql_row_hash([{key: value for key, value in row.items()
                                                if key not in ("modified", "modified_by")} for row in rows])
    return result


def smoke(company, other_company, item_group, stock_uom):
    frappe.only_for("System Manager")
    assert company != other_company and frappe.db.db_type == "mariadb"
    for doctype, name in (("Company", company), ("Company", other_company), ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "请提供既有验收公司、明细分组和库存单位"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    meta = frappe.get_meta(PROFILE)
    assert meta.track_changes and meta.get_field("item_code").unique and meta.get_field("request_id").unique
    assert all(meta.get_field(field) for field in ("category", "category_detail", "creation_mode", "request_digest"))
    tables, counts = _tab_counts()
    changed = {"Item", PROFILE, "Supplier", "File", "Version", "User", "User Permission", "Series", "DocShare", "DefaultValue",
               "Jifeng Product Category", "Jifeng Product Category Detail", "Email Queue", "Notification Log", "Communication"}
    for doctype in ("Item", PROFILE, "User", "Supplier"):
        changed.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    assert all(tables["tab" + doctype] == "InnoDB" for doctype in changed)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    before, ledger = _contents(), _ledger_snapshot()
    callbacks, context = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    logs = set(frappe.get_all("Error Log", pluck="name"))
    prefix = "JF-QA-PROD-ARCHIVE-" + frappe.generate_hash(length=10)
    point = "jf_product_archive_" + frappe.generate_hash(length=8)
    roots = [Path(frappe.get_site_path(*parts)) for parts in (("private", "files"), ("public", "files"))]
    records, objects, users, attachments, checks = set(), [], [], [], []
    current, primary, evidence = "fixtures", None, {}
    _begin_rollback_only(point)

    def paths():
        return {str(path.resolve()) for root in roots for path in root.glob(prefix + "*")}

    def native(action):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = previous

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((doctype, document.name))
        records.update((child.doctype, child.name) for child in document.get_all_children() if child.name)
        return document

    def actor(label, roles, companies):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix,
                      send_welcome_email=0, roles=[{"role": role} for role in roles])
        users.append(user.name)
        for allowed in companies:
            insert("User Permission", user=user.name, allow="Company", for_value=allowed, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        return user.name

    def payload(**changes):
        return {"request_id": str(uuid4()), "profile": {"company": company, "legacy_number": "007原文",
            "display_name": " 档案独立名称 ", "display_uom": " 原文单位非UOM ", "legacy_remarks": " 单行备注 ",
            "notes": "多行注意\n第二行", "inactive": 0},
            "item": {"item_name": "独立业务商品名称", "item_group": item_group, "stock_uom": stock_uom}, **changes}

    def save(values):
        result = native(lambda: save_archive(values))
        records.add((PROFILE, result["name"]))
        records.add(("Item", result["item_code"]))
        return result

    def update(view, values):
        return save({"name": view["name"], "profile_version": view["profile_version"],
                     "item_version": view["item_version"], "profile": values})

    def reject(action, errors=frappe.ValidationError, atomic=True):
        old, old_callbacks, old_context = _contents(), _callbacks(), _local_context()
        nested = "jf_product_archive_reject_" + frappe.generate_hash(length=8)
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                if str(error) == COLLISION:
                    assert error.__context__ is None
            else:
                raise AssertionError("接受了应拒绝的原式产品请求")
            if atomic:
                assert _contents() == old, "API 未自行回滚整个 Item/Profile/Version/Series 事务"
                assert _callbacks() == old_callbacks and _local_context() == old_context
        finally:
            frappe.db.rollback(save_point=nested)
            _restore_callbacks(old_callbacks)
            _restore_local_context(old_context)
        assert _contents() == old and _ledger_snapshot() == ledger

    def passed():
        checks.append(current)
        assert _ledger_snapshot() == ledger and set(frappe.get_all("Error Log", pluck="name")) == logs

    try:
        assert not paths()
        frappe.set_user("Administrator")
        # System Manager does not imply native Item.create. Use installed
        # Item Manager rights explicitly; also prove the ordinary stock path.
        system = actor("system", ["System Manager", "Item Manager"], [company, other_company])
        creator = actor("creator", ["Stock Manager", "Item Manager"], [company, other_company])
        item_scoped = actor("item-scoped", ["Stock Manager"], [company])
        stock = actor("stock", ["Stock Manager"], [company])
        outside = actor("outside", ["Stock Manager"], [other_company])
        clerk = actor("clerk", ["Stock User"], [company])
        unbound = insert("Item", item_code=prefix + "-existing", item_name="现有业务商品原名",
                         item_group=item_group, stock_uom=stock_uom, is_stock_item=1)
        sibling_item = insert("Item", item_code=prefix + "-sibling", item_name="其它商品",
                              item_group=item_group, stock_uom=stock_uom, is_stock_item=1)
        component = insert("Item", item_code=prefix + "-component", item_name="真实材料",
                           item_group=item_group, stock_uom=stock_uom, is_stock_item=1)
        supplier = insert("Supplier", supplier_name=prefix + "-supplier", supplier_type="Company", naming_series="SUP-.YYYY.-")
        parent = insert("Jifeng Product Category", company=company, major_name="明确大类", inactive=0,
                        legacy_code="只展示类别代码", owner_name="独立负责人", region="独立区域")
        child = insert("Jifeng Product Category Detail", company=company, category=parent.name,
                       minor_name="明确小类", material="不能推导的材质", display_uom="不能推导的单位", inactive=0)
        sibling = insert(PROFILE, company=company, item_code=sibling_item.name, display_name=prefix + "-其它",
                         legacy_remarks="原生多行\n不能迁移")
        sibling_before = _document_snapshot(PROFILE, sibling.name)
        item_before = _document_snapshot("Item", unbound.name)

        current = "native_stock_manager_existing_item_creation_preserves_item_and_independent_archive_values"
        frappe.set_user(stock)
        initialized = native(lambda: init_archive(company, unbound.name))
        assert initialized["can_link_item"] and initialized["item_version"]
        assert initialized["profile"]["legacy_code"] is None and initialized["profile"]["display_name"] is None
        linked_input = payload(existing_item=unbound.name, item_version=initialized["item_version"], item={})
        linked = save(linked_input)
        assert linked["creation_mode"] == "existing_item" and linked["item"]["item_name"] == unbound.item_name
        assert linked["profile"]["display_name"] == linked_input["profile"]["display_name"]
        assert _document_snapshot("Item", unbound.name) == item_before
        evidence["stock_roles"] = sorted(frappe.get_roles())
        assert "Stock Manager" in evidence["stock_roles"] and "System Manager" not in evidence["stock_roles"]
        passed()

        current = "explicit_native_item_creation_separates_business_name_unit_code_and_archive_raw_fields"
        frappe.set_user(creator)
        evidence["creator_roles"] = sorted(frappe.get_roles())
        assert "Item Manager" in evidence["creator_roles"] and "Stock Manager" in evidence["creator_roles"]
        assert "System Manager" not in evidence["creator_roles"]
        fresh_input = payload()
        prices = frappe.db.count("Item Price")
        fresh = save(fresh_input)
        assert fresh["creation_mode"] == "new_item" and fresh["item_code"].startswith("JF-PROD-")
        assert fresh["profile"]["legacy_code"] is None and fresh["profile"]["display_uom"] != stock_uom
        assert fresh["item"]["item_name"] != fresh["profile"]["display_name"]
        assert frappe.db.count("Item Price") == prices
        assert native(lambda: init_archive())["profile"]["company"] is None
        passed()

        current = "profile_updates_preserve_native_item_other_profile_templates_children_and_versions"
        document = frappe.get_doc(PROFILE, linked["name"])
        document.requirements, document.packaging = "保持工艺要求", "保持包装"
        document.append("materials", {"item_code": component.name, "uom": stock_uom, "qty_per_unit": 2, "unit_price": 3})
        native(document.save)
        records.update((row.doctype, row.name) for row in document.get_all_children())
        recipe = _recipe(document.name)
        # Standard Supplier metadata grants Stock Manager read/select; this
        # selection uses that actual role rather than assuming System Manager.
        frappe.set_user(stock)
        assert frappe.get_doc("Supplier", supplier.name).has_permission("select")
        linked = native(lambda: read_archive(document.name))
        linked = update(linked, {"display_name": "仅改档案", "notes": "新注意\n第二行", "list_price": "12.5", "inactive": 1,
                                "supplier": supplier.name, "supplier_product_code": "独立供应商货号", "specification": "独立规格\n第二行"})
        assert (linked["profile"]["supplier"], linked["profile"]["supplier_product_code"], linked["profile"]["specification"]) == (
            supplier.name, "独立供应商货号", "独立规格\n第二行")
        after_doc = frappe.get_doc(PROFILE, linked["name"])
        assert (after_doc.requirements, after_doc.packaging) == ("保持工艺要求", "保持包装")
        assert _recipe(document.name) == recipe and _document_snapshot("Item", unbound.name) == item_before
        assert _document_snapshot(PROFILE, sibling.name) == sibling_before
        assert _version_has(after_doc, "display_name")
        assert all(row.modified == after_doc.modified and row.modified_by == after_doc.modified_by for row in after_doc.get_all_children())
        frappe.set_user(system)
        passed()

        current = "uuid_readonly_resolution_returns_current_record_without_rewriting_item_profile_or_series"
        state = _contents()
        assert save(linked_input)["profile"]["display_name"] == "仅改档案"
        assert native(lambda: resolve_creation(linked_input["request_id"], company))["archive"]["name"] == linked["name"]
        assert native(lambda: resolve_creation(str(uuid4()), company)) == {"status": "not_found"}
        assert _contents() == state
        reject(lambda: save_archive(linked_input | {"profile": linked_input["profile"] | {"notes": "different"}}))
        passed()

        current = "native_post_item_save_profile_uuid_collision_rolls_back_both_tables_versions_and_callbacks"
        # Original UUID belongs to an existing-Item Profile in another company.
        # New-company query cannot find it; the new Item code is absent. Native
        # request_id UNIQUE fails only when Profile is saved after Item.save.
        impossible = payload(request_id=linked_input["request_id"])
        impossible["profile"]["company"] = other_company
        code = "JF-PROD-" + linked_input["request_id"].replace("-", "")
        assert not frappe.db.exists("Item", code)
        reject(lambda: save_archive(impossible))
        assert not frappe.db.exists("Item", code)
        evidence["atomic_failure"] = "natural Profile UUID UNIQUE conflict after native Item.save; complete SQL/queues/context audited, not HTTP failure inference"
        passed()

        current = "flat_native_category_choices_search_parent_and_child_without_writes_or_generated_ids"
        state = _contents()
        expected = {"category": parent.name, "category_detail": child.name, "category_code": parent.legacy_code,
                    "major_name": parent.major_name, "minor_name": child.minor_name, "material": child.material,
                    "owner_name": parent.owner_name, "region": parent.region, "abbreviation": ""}
        assert native(lambda: list_category_choices(company, child.minor_name)) == [expected]
        assert native(lambda: list_category_choices(company, parent.legacy_code)) == [expected]
        assert _contents() == state
        evidence["category_picker"] = "native readable parent/detail flat row; parent and child keyword paths, display-only code, no generated legacy number or abbreviation"
        passed()

        current = "explicit_category_selection_snapshots_only_names_then_history_survives_category_rename_and_stop"
        fresh = update(fresh, {"category": parent.name, "category_detail": child.name})
        assert (fresh["profile"]["major_category"], fresh["profile"]["minor_category"]) == (parent.major_name, child.minor_name)
        assert fresh["profile"]["material"] is None and fresh["profile"]["display_uom"] == fresh_input["profile"]["display_uom"]
        frappe.set_user("Administrator")
        parent = frappe.get_doc(parent.doctype, parent.name)
        parent.major_name, parent.inactive = "后来大类名称", 1
        native(parent.save)
        frappe.set_user(system)
        assert native(lambda: list_category_choices(company, child.minor_name)) == []
        fresh = update(fresh, {"notes": "普通编辑不刷新类别"})
        assert fresh["profile"]["major_category"] == "明确大类"
        reject(lambda: save_archive(payload(profile={"company": company, "category": parent.name})))
        fresh = update(fresh, {"category": None, "category_detail": None})
        assert fresh["profile"]["major_category"] is None and fresh["profile"]["minor_category"] is None
        passed()

        current = "two_private_images_require_exact_saved_profile_field_and_separate_display_and_stored_urls"
        for field in ("profile_image", "patent_image"):
            content = _profile_fixture_png(prefix + field)
            assert not frappe.db.exists("File", {"content_hash": get_content_hash(content)}), "fixture cannot reuse a business blob"
            attachment = insert("File", file_name=prefix + "-" + field + ".png", content=content,
                                is_private=1, attached_to_doctype=PROFILE, attached_to_name=fresh["name"], attached_to_field=field)
            attachments.append(attachment)
            assert attachment.on_rollback in list(frappe.db.after_rollback._functions)
            assert Path(attachment.get_full_path()).is_file()
            fresh = update(fresh, {field: attachment.file_url})
            assert fresh["profile"][field] == attachment.file_url
            assert fresh["image_states"][field] == {"url": attachment.unique_url, "state": "已上传"}
            reject(lambda field=field, attachment=attachment: update(fresh, {field: attachment.unique_url}),
                   frappe.PermissionError)
        passed()

        current = "upload_candidate_reconciliation_is_readonly_exact_private_field_and_never_adopts_automatically"
        state = _contents()
        for attachment in attachments:
            candidates = native(lambda attachment=attachment: list_image_attachments(fresh["name"], attachment.attached_to_field))
            assert len(candidates) == 1 and candidates[0]["name"] == attachment.name
            assert candidates[0]["file_url"] == attachment.file_url and candidates[0]["unique_url"] == attachment.unique_url
            assert candidates[0]["file_name"] == attachment.file_name
        assert _contents() == state
        evidence["upload_reconciliation"] = "exact candidates only; caller compares baseline File IDs and explicitly adopts, no upload replay or Profile mutation"
        passed()

        current = "native_file_read_denial_cannot_be_authorized_by_same_url_other_parent_alias"
        frappe.set_user("Administrator")
        exact = attachments[0]
        alias = exact.create_attachment_copy(attached_to_doctype=PROFILE, attached_to_name=sibling.name, attached_to_field="profile_image")
        attachments.append(alias)
        records.add(("File", alias.name))
        assert alias.name != exact.name and alias.file_url == exact.file_url
        for name in (alias.name, alias.folder):
            insert("User Permission", user=stock, allow="File", for_value=name, apply_to_all_doctypes=0, applicable_for="File")
        frappe.clear_cache(user=stock)
        frappe.set_user(stock)
        assert not frappe.get_doc("File", exact.name).has_permission("read")
        assert frappe.get_doc("File", alias.name).has_permission("read") and frappe.get_doc("File", alias.name).is_downloadable()
        hidden = native(lambda: read_archive(fresh["name"]))
        assert native(lambda: list_image_attachments(fresh["name"], "profile_image")) == []
        assert "profile_image" not in hidden["profile"] and hidden["image_states"]["profile_image"] == {"url": "", "state": "附件不可用"}
        assert exact.file_url not in str(hidden) and alias.name not in str(hidden)
        passed()

        current = "unpersisted_historical_patent_projection_has_no_download_url_or_stored_value_leak"
        # Read-only projection probe. Historical preservation is model-tested;
        # no controller is patched or SQL fabricated into a legacy record.
        legacy = frappe.get_doc(PROFILE, sibling.name)
        legacy.patent_image = "https://example.invalid/legacy.png"
        from jifeng_erp.product_archive import _pack
        projected = _pack(legacy, frappe.get_doc("Item", sibling_item.name))
        assert "patent_image" not in projected["profile"]
        assert projected["image_states"]["patent_image"] == {"url": "", "state": "附件不可用"}
        evidence["legacy_patent"] = "unpersisted projection probe; preservation model-tested, no persisted historical SQL fabrication"
        passed()

        current = "stale_versions_readonly_raw_fields_receipt_and_identity_mutation_are_rejected"
        frappe.set_user(system)
        fresh = native(lambda: read_archive(fresh["name"]))
        stale = fresh
        fresh = update(fresh, {"region": "当前区域"})
        reject(lambda: update(stale, {"region": "旧版本覆盖"}), frappe.TimestampMismatchError)
        for field in ("legacy_code", "major_category", "minor_category", "request_digest", "creation_mode"):
            reject(lambda field=field: update(fresh, {field: "客户端伪造"}))
        reject(lambda: update(fresh, {"company": other_company}))
        reject(lambda: save_archive(payload(item={"item_name": "x", "item_group": item_group})))
        reject(lambda: save_archive(payload(profile={"company": company, "profile_image": attachments[0].file_url})))
        for force in (False, True):
            reject(lambda force=force: frappe.rename_doc(PROFILE, fresh["name"], fresh["name"] + "-R", force=force),
                   atomic=False)
        passed()

        current = "native_company_and_item_permissions_hide_lists_receipts_and_forbid_extra_roles"
        frappe.set_user("Administrator")
        # Frappe checks Item links in both the Profile and its recipe children.
        # Allow this exact product and its material, while hiding fresh Item.
        for allowed_item in (unbound.name, component.name):
            insert("User Permission", user=item_scoped, allow="Item", for_value=allowed_item, apply_to_all_doctypes=1)
        frappe.clear_cache(user=item_scoped)
        frappe.set_user(item_scoped)
        assert native(lambda: read_archive(linked["name"]))["name"] == linked["name"]
        assert {row["name"] for row in native(lambda: list_archives(company, include_inactive=True))} == {linked["name"]}
        reject(lambda: read_archive(fresh["name"]), frappe.PermissionError, atomic=False)
        assert native(lambda: resolve_creation(fresh_input["request_id"], company)) == {"status": "not_found"}
        frappe.set_user(outside)
        reject(lambda: list_archives(company), frappe.PermissionError, atomic=False)
        reject(lambda: read_archive(fresh["name"]), frappe.PermissionError, atomic=False)
        reject(lambda: resolve_creation(fresh_input["request_id"], company), frappe.PermissionError, atomic=False)
        reject(lambda: list_category_choices(company), frappe.PermissionError, atomic=False)
        for user in (clerk, "Guest"):
            frappe.set_user(user)
            if user == clerk and frappe.has_permission(PROFILE, "read"):
                raise AssertionError("真实Stock User档案读取权限与预期不同，不能伪造通过")
            reject(lambda: init_archive(company), frappe.PermissionError, atomic=False)
            reject(lambda: save_archive(payload()), frappe.PermissionError)
        passed()

        current = "native_read_list_init_resolve_leave_versions_series_accounts_and_ledger_unchanged"
        frappe.set_user(system)
        state = _contents()
        native(lambda: init_archive(company))
        native(lambda: read_archive(fresh["name"]))
        native(lambda: list_archives(company, search="仅改", include_inactive=True))
        native(lambda: resolve_creation(fresh_input["request_id"], company))
        assert _contents() == state
        passed()
    except Exception as error:
        primary = error
        error.add_note("原式产品档案原生失败门：" + current)
        error.add_note("已通过门：" + repr(checks))
        raise
    finally:
        def collect():
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)

        _profile_audit([
            ("collect_exact_fixture_records", collect),
            ("native_savepoint_then_only_new_file_rollback_callbacks", lambda: _rollback(point, callbacks)),
            ("restore_caches_caller_callbacks_and_context", lambda: _profile_restore_caller(
                original_user, original_test, users, records, callbacks, context)),
            ("fixture_records_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("whole_items_profiles_children_files_versions_categories_series_auth_accounts_restored", lambda: _contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger),
            ("native_exact_file_cleanup", lambda: not paths() and all(not Path(file.get_full_path()).exists() for file in attachments)),
            ("caller_queues_and_local_context_restored", lambda: _callbacks() == callbacks and _local_context() == context),
            ("zero_error_logs_added", lambda: set(frappe.get_all("Error Log", pluck="name")) == logs),
        ], primary=primary)
    checks.append("rollback_all_native_sql_hashes_counts_files_accounts_versions_series_ledger_and_context")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "evidence": evidence, "hashes_before": before, "hashes_after": _contents(),
            "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger, "whole_ledger_after": _ledger_snapshot(),
            "gui_verified": False, "http_image_download_verified": False,
            "remaining": ["旧类别返回键及自动代码规则", "复制复选框语义", "打印导入导出批量传图", "真实字段权限撤销", "并发创建竞争"]}
