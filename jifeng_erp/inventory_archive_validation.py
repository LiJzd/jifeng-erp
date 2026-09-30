"""Native archive acceptance, exclusively synthetic and always rolled back.

Run once from initialized direct Python after migrating, in an exclusive write
window. This helper has no commit mode and never changes DocPerm or controllers.
It records actual Stock Manager Item creation rights instead of granting them.
"""

from copy import deepcopy
from uuid import uuid4

import frappe

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_archive import (
    DICTIONARY, PROFILE, init_archive, list_dictionary, read_archive, resolve_creation,
    save_archive, save_dictionary,
)
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import (
    _callbacks, _digest, _restore_callbacks, _tab_counts, _versions,
)
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context


def _contents():
    """Audit full native Item data, including every installed child table."""
    tables = {"Item", PROFILE, DICTIONARY, "Item Price", "Version"}
    tables.update(field.options for field in frappe.get_meta("Item").get_table_fields())
    return {doctype: _digest(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
                                         + "` ORDER BY name", as_dict=True)) for doctype in sorted(tables)}


def smoke(company, other_company, item_group, stock_uom, alternate_uom=None):
    """No browser, file bytes, downloads, permission overrides or commit option."""
    frappe.only_for("System Manager")
    assert company != other_company, "验收需两家不同公司"
    alternate_uom = alternate_uom or stock_uom
    for doctype, name in (("Company", company), ("Company", other_company), ("Item Group", item_group),
                          ("UOM", stock_uom), ("UOM", alternate_uom)):
        assert frappe.db.exists(doctype, name), "验收引用必须为既有资料"
    assert not frappe.db.get_value("Item Group", item_group, "is_group"), "请选择明细商品分组"
    for doctype in (PROFILE, DICTIONARY):
        meta = frappe.get_meta(doctype)
        assert meta.track_changes and not meta.is_submittable
    assert frappe.get_meta(PROFILE).get_field("request_id").unique
    assert frappe.get_meta(PROFILE).get_field("item_code").unique
    prefix = "JF-QA-INVENTORY-" + frappe.generate_hash(length=10)
    point = "jf_inventory_archive_" + frappe.generate_hash(length=8)
    original_user, original_test = frappe.session.user, frappe.in_test
    callbacks_before, context_before = _callbacks(), _local_context()
    tables, counts = _tab_counts()
    content_before, ledger_before = _contents(), _ledger_snapshot()
    logs_before = set(frappe.get_all("Error Log", pluck="name"))
    records, users, checks, evidence = set(), [], [], {}
    current = "initialization"

    def native(operation):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return operation()
        finally:
            frappe.in_test = previous

    def new_payload(kind="物料", owner_company=company):
        return {"kind": kind, "request_id": str(uuid4()),
                "profile": {"company": owner_company, "legacy_number": prefix + "-编号",
                            "legacy_code": prefix + "-代码", "display_name": prefix + "-名称",
                            "display_uom": "独立原文单位", "conversion_value": 1, "inactive": 0},
                "item": {"item_group": item_group, "stock_uom": stock_uom}}

    def create(payload):
        result = native(lambda: save_archive(payload))
        records.update(((PROFILE, result["name"]), ("Item", result["item_code"])))
        return result

    def update_payload(view, values):
        return {"name": view["name"], "kind": view["kind"], "profile_version": view["profile_version"],
                "item_version": view["item_version"], "profile": values}

    def passed(label):
        checks.append(label)
        assert _ledger_snapshot() == ledger_before, "档案验收改变了完整 GL/SLE"
        assert set(frappe.get_all("Error Log", pluck="name")) == logs_before

    def reject(operation, errors, atomic=True):
        before, callbacks, context = _contents(), _callbacks(), _local_context()
        try:
            try:
                native(operation)
            except errors:
                pass
            else:
                raise AssertionError("原生操作接受了应拒绝的档案请求")
            assert _contents() == before, "拒绝请求留下 Item、档案、价格表或 Version 改动"
            assert _ledger_snapshot() == ledger_before
            if atomic:
                assert _callbacks() == callbacks and _local_context() == context, "联合失败未恢复回调及保存上下文"
        finally:
            # Native Desk rejection does not run the API transaction decorator.
            # Database state was compared before this local-context cleanup.
            if not atomic:
                _restore_callbacks(callbacks)
                _restore_local_context(context)

    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        initial_prices = frappe.db.count("Item Price")
        material_request, stone_request = new_payload(), new_payload("材料")
        material_request["profile"]["material"] = "旧材质"
        stone_request["profile"].update(shape="旧形状", color="旧颜色")
        material, stone = create(material_request), create(stone_request)
        assert material["item_code"] != material["profile"]["legacy_code"]
        assert material["profile"]["legacy_code"] != material["profile"]["legacy_number"]
        assert frappe.get_doc("Item", material["item_code"]).is_stock_item
        assert material["item"]["stock_uom"] == stock_uom
        assert material["profile"]["display_uom"] == "独立原文单位"
        assert stone["profile"]["shape"] == "旧形状" and "material" not in stone["profile"]
        assert frappe.db.count("Item Price") == initial_prices
        passed("native_two_kinds_create_independent_item_identity_without_prices_or_ledgers")

        current = "native_item_preserved"
        native_item_before = frappe.get_doc("Item", material["item_code"]).as_dict()
        item_tables_before = _contents()
        material = native(lambda: save_archive(update_payload(material, {
            "display_name": "仅改档案名称", "display_uom": "仅改档案单位", "conversion_uom": alternate_uom,
            "conversion_value": 8, "location": "原文库位", "owner_name": "原文负责", "inactive": 1,
            "list_price": 12.5, "processing_price": 2.5, "weight": 1.25, "upper_limit": 8, "lower_limit": 2})))
        assert frappe.get_doc("Item", material["item_code"]).as_dict() == native_item_before
        assert all(_contents()[doctype] == item_tables_before[doctype]
                   for doctype in item_tables_before if doctype not in (PROFILE, "Version"))
        assert _versions(PROFILE, material["name"]), "实际原生保存缺少修改历史"
        evidence["native_item_preservation"] = {"all_fields": True, "all_installed_child_tables": True,
                                                "uoms_defaults_valuation": True, "item_price_unchanged": True}
        passed("profile_edit_preserves_all_native_item_fields_children_prices_and_versions")

        current = "creation_reuse"
        before_replay = _contents()
        repeated = native(lambda: save_archive(material_request))
        assert repeated["name"] == material["name"] and _contents() == before_replay
        assert resolve_creation(material_request["request_id"], "物料")["archive"]["name"] == material["name"]
        changed = deepcopy(material_request)
        changed["profile"]["notes"] = "不同载荷"
        reject(lambda: save_archive(changed), frappe.ValidationError)
        passed("creation_reuse_unchanged_digest_and_readonly_resolution")

        current = "versions_and_copy"
        for field in ("profile_version", "item_version"):
            stale = update_payload(material, {"notes": "过期版本不能保存"})
            stale[field] = "2000-01-01T00:00:00"
            reject(lambda stale=stale: save_archive(stale), frappe.TimestampMismatchError)
        copied = init_archive("物料", copy_from=material["name"])
        assert not copied["name"] and copied["profile"]["company"] == company
        assert all(not copied["profile"].get(field) for field in ("legacy_number", "legacy_code", "old_number", "inactive"))
        assert copied["profile"]["material"] == material["profile"]["material"]
        assert copied["item"]["stock_uom"] == stock_uom
        passed("both_native_versions_reject_stale_edits_and_copy_clears_identity")

        current = "joint_rollback"
        invalid = new_payload()
        invalid["profile"]["conversion_value"] = 0
        reject(lambda: save_archive(invalid), frappe.ValidationError)
        assert not frappe.db.exists(PROFILE, {"request_id": invalid["request_id"]})
        passed("second_document_validation_failure_rolls_back_native_item_callbacks_and_versions")

        current = "native_bypass"
        direct = frappe.get_doc({"doctype": PROFILE, "company": company, "archive_kind": "物料",
            "item_code": material["item_code"], "legacy_number": "伪造编号", "display_name": "伪造档案",
            "conversion_value": 1, "request_id": str(uuid4()), "request_digest": "a" * 64})
        reject(direct.insert, frappe.ValidationError, atomic=False)
        for field, value in (("company", other_company), ("archive_kind", "材料"), ("request_id", str(uuid4())),
                             ("item_code", stone["item_code"]), ("request_digest", "b" * 64)):
            def tamper(field=field, value=value):
                document = frappe.get_doc(PROFILE, material["name"])
                document.set(field, value)
                return document.save()
            reject(tamper, frappe.ValidationError, atomic=False)
        reject(lambda: frappe.delete_doc(PROFILE, material["name"]), frappe.ValidationError, atomic=False)
        reject(lambda: save_archive(update_payload(material, {"shape": "跨类字段"})), frappe.ValidationError)
        reject(lambda: save_archive(update_payload(stone, {"material": "跨类字段"})), frappe.ValidationError)
        passed("native_profile_creation_identity_tamper_deletion_and_cross_kind_fields_denied")

        current = "dictionaries"
        dictionary = native(lambda: save_dictionary({"kind": "材料", "company": company,
                             "dictionary_type": "形状", "value": prefix + "圆形", "inactive": 0}))
        records.add((DICTIONARY, dictionary["name"]))
        assert list_dictionary("材料", "形状", company, prefix)[0]["name"] == dictionary["name"]
        assert list_dictionary("材料", "形状", other_company, prefix) == []
        for field, value in (("company", other_company), ("archive_kind", "物料"), ("dictionary_type", "颜色")):
            def dictionary_tamper(field=field, value=value):
                document = frappe.get_doc(DICTIONARY, dictionary["name"])
                document.set(field, value)
                return document.save()
            reject(dictionary_tamper, frappe.ValidationError, atomic=False)
        passed("persistent_dictionary_company_kind_type_isolation_and_native_form_guards")

        current = "real_roles"
        actors = {}
        for key, role in (("manager", "Stock Manager"), ("clerk", "Stock User")):
            actor = frappe.get_doc({"doctype": "User", "email": prefix.lower() + "-" + key + "@example.invalid",
                "first_name": prefix + "-" + key, "send_welcome_email": 0, "roles": [{"role": role}]}).insert()
            users.append(actor.name)
            records.add(("User", actor.name))
            actors[key] = actor.name
            restriction = frappe.get_doc({"doctype": "User Permission", "user": actor.name,
                "allow": "Company", "for_value": company, "apply_to_all_doctypes": 1}).insert()
            records.add(("User Permission", restriction.name))
            frappe.clear_cache(user=actor.name)
        foreign_request = new_payload(owner_company=other_company)
        foreign = create(foreign_request)
        frappe.set_user(actors["manager"])
        evidence["stock_manager_roles"] = sorted(frappe.get_roles())
        assert "System Manager" not in evidence["stock_manager_roles"]
        view = read_archive(material["name"], "物料")
        assert view["can_write"]
        material = native(lambda: save_archive(update_payload(view, {"notes": "真实库存主管更新"})))
        reject(lambda: read_archive(foreign["name"], "物料"), frappe.PermissionError)
        reject(lambda: resolve_creation(foreign_request["request_id"], "物料"), frappe.PermissionError)
        evidence["stock_manager_item_create"] = bool(frappe.has_permission("Item", "create"))
        evidence["stock_manager_profile_create"] = bool(frappe.has_permission(PROFILE, "create"))
        if evidence["stock_manager_item_create"] and evidence["stock_manager_profile_create"]:
            role_created = create(new_payload())
            assert role_created["name"] and role_created["item_code"]
            passed("real_stock_manager_joint_creation_when_both_native_create_rights_present")
        else:
            reject(lambda: save_archive(new_payload()), frappe.PermissionError)
            passed("real_stock_manager_missing_native_create_rights_reject_without_item_or_profile")
        reject(lambda: init_archive("物料", company=other_company), frappe.PermissionError)
        passed("real_stock_manager_update_company_scope_and_resolution_recheck")

        current = "native_item_manager_creation_combination"
        frappe.set_user("Administrator")
        assert frappe.db.exists("Role", "Item Manager"), "原生商品管理角色必须存在"
        creator = frappe.get_doc("User", actors["manager"])
        creator.append("roles", {"role": "Item Manager"})
        native(creator.save)
        frappe.clear_cache(user=creator.name)
        frappe.set_user(creator.name)
        evidence["inventory_creator_roles"] = sorted(frappe.get_roles())
        assert "System Manager" not in evidence["inventory_creator_roles"]
        assert frappe.has_permission("Item", "create") and frappe.has_permission(PROFILE, "create")
        created = create(new_payload("材料"))
        assert created["name"] and created["item_code"]
        passed("real_stock_manager_and_item_manager_joint_creation_without_permission_overrides")

        current = "clerk_and_guest"
        frappe.set_user(actors["clerk"])
        view = read_archive(material["name"], "物料")
        assert not view["can_write"]
        reject(lambda: save_archive(update_payload(view, {"notes": "无权修改"})), frappe.PermissionError)
        assert list_dictionary("材料", "形状", company, prefix)
        reject(lambda: save_dictionary({"kind": "材料", "company": company, "dictionary_type": "颜色", "value": "无权写"}),
               frappe.PermissionError)
        frappe.set_user("Guest")
        for operation in (lambda: init_archive("物料"), lambda: read_archive(material["name"], "物料"),
                          lambda: resolve_creation(material_request["request_id"], "物料"),
                          lambda: save_archive(new_payload()), lambda: list_dictionary("材料", "形状", company)):
            reject(operation, frappe.PermissionError)
        passed("real_stock_user_readonly_and_guest_denials")
    except Exception as error:
        error.add_note("native inventory archive smoke failed at gate: " + current)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
        finally:
            try:
                _end_rollback_only(point)
            finally:
                _restore_callbacks(callbacks_before)
                _restore_local_context(context_before)
                frappe.in_test = original_test
                frappe.set_user(original_user)
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
            assert all(not frappe.db.exists(doctype, name) for doctype, name in records)
            assert _tab_counts(tables)[1] == counts
            assert _contents() == content_before
            assert _ledger_snapshot() == ledger_before
            assert set(frappe.get_all("Error Log", pluck="name")) == logs_before
    checks.append("all_fixtures_versions_prices_callbacks_and_full_ledgers_rolled_back")
    return {"passed": True, "business_data": "rolled_back", "gate_count": len(checks), "checks": checks,
            "evidence": evidence, "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "contents_before": content_before, "contents_after": _contents(), "whole_ledger_before": ledger_before,
            "whole_ledger_after": _ledger_snapshot(), "gui_verified": False, "concurrency_verified": False}
