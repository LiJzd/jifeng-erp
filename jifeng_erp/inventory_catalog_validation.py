"""Serial native inventory browse experiment; synthetic fixtures always roll back.

Run directly once in an exclusive business-write window, never bench execute.
There is no commit mode, migration, DocPerm override, browser or file download.
Image binding and field-mask adversarial models remain separate from this run.
"""

import json
from decimal import Decimal
from uuid import uuid4

import frappe

from jifeng_erp.catalog_validation import (
    _native_without_error_log, _profile_audit, _profile_restore_caller, _profile_rollback,
)
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_archive import save_archive
from jifeng_erp.inventory_catalog import PROFILE, list_archives, read_archive
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _native_contents, _sql_row_hash


def _contents():
    result = _native_contents()
    doctypes = {PROFILE, "Jifeng Inventory Dictionary", "Item Price", "Error Log"}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    return result


def smoke(company, other_company, item_group, stock_uom):
    """Actual Stock User/Manager, company and exact Item/Profile UP scopes."""
    frappe.only_for("System Manager")
    assert company and other_company and company != other_company
    for doctype, name in (("Company", company), ("Company", other_company),
                          ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "库存浏览验收引用必须为既有资料"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    assert frappe.get_meta(PROFILE).get_field("item_code").unique
    tables, counts_before = _tab_counts()
    before, ledger_before = _contents(), _ledger_snapshot()
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    prefix = "JF-QA-INV-CATALOG-" + frappe.generate_hash(length=10)
    point = "jf_inventory_catalog_" + frappe.generate_hash(length=8)
    users, objects, records, checks, roles = [], [], set(), [], {}
    current, primary = "fixture_setup", None

    def native(action):
        return _native_without_error_log(action, current)

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((doctype, document.name))
        return document

    def archive(label, kind="物料", inactive=0, owner_company=company):
        values = {"company": owner_company, "legacy_number": prefix + "-" + label,
                  "legacy_code": prefix + "-同一原文代码", "display_name": " 原文名称 " + label,
                  "display_uom": "对（独立原文，不作换算）", "specification": " 规格原文\n第二行 ",
                  "location": " 原库位 ", "list_price": 17.5, "processing_price": 2.3,
                  "conversion_value": 9, "inactive": inactive}
        values.update({"material": "旧材质"} if kind == "物料" else {"shape": "旧形状", "color": "旧颜色"})
        result = native(lambda: save_archive({"kind": kind, "request_id": str(uuid4()), "profile": values,
                                             "item": {"item_group": item_group, "stock_uom": stock_uom}}))
        records.update(((PROFILE, result["name"]), ("Item", result["item_code"])))
        return result

    def actor(label, role, owner_company=company, scope=None):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix + label,
                      send_welcome_email=0, roles=[{"role": role}])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=owner_company, apply_to_all_doctypes=1)
        if scope:
            insert("User Permission", user=user.name, allow=scope[0], for_value=scope[1], apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        roles[label] = sorted(frappe.get_roles(user.name))
        assert "System Manager" not in roles[label]
        return user.name

    def passed(label):
        checks.append(label)
        assert _ledger_snapshot() == ledger_before, "库存浏览验收改变了完整 GL/SLE"

    def query(kind="物料", owner_company=company, **options):
        state, callbacks, context = _contents(), _callbacks(), _local_context()
        result = native(lambda: list_archives(kind, owner_company, search=prefix, **options))
        assert _contents() == state and _callbacks() == callbacks and _local_context() == context
        assert all(row["readonly"] and not row["can_write"] for row in result)
        return result

    def detail(view, kind="物料", owner_company=company):
        state, callbacks, context = _contents(), _callbacks(), _local_context()
        result = native(lambda: read_archive(view["name"], kind, owner_company))
        assert _contents() == state and _callbacks() == callbacks and _local_context() == context
        assert result["readonly"] and not result["can_write"]
        return result

    def rejected(action, error=frappe.PermissionError):
        state, callbacks, context = _contents(), _callbacks(), _local_context()
        try:
            native(action)
        except error:
            pass
        else:
            raise AssertionError("库存浏览接受了应拒绝的读取请求")
        assert _contents() == state and _callbacks() == callbacks and _local_context() == context

    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        first, second = archive("物料A"), archive("物料B")
        material, stopped = archive("材料A", "材料"), archive("材料停用", "材料", 1)
        foreign = archive("异公司", owner_company=other_company)
        assert first["item_code"] != second["item_code"]
        disabled_item = frappe.get_doc("Item", first["item_code"])
        objects.append(disabled_item)
        disabled_item.disabled = 1
        native(disabled_item.save)
        clerk = actor("clerk", "Stock User")
        manager = actor("manager", "Stock Manager")
        item_reader = actor("item", "Stock User", scope=("Item", first["item_code"]))
        profile_reader = actor("profile", "Stock User", scope=(PROFILE, first["name"]))
        outsider = actor("other", "Stock User", owner_company=other_company)
        passed("native_two_kinds_same_legacy_code_distinct_items_and_scoped_actors")

        current = "stock_user_readonly_source_projection"
        frappe.set_user(clerk)
        assert frappe.has_permission(PROFILE, "read") and frappe.has_permission("Item", "read")
        assert not frappe.has_permission(PROFILE, "write") and not frappe.has_permission(PROFILE, "create")
        frappe.get_doc("Company", company).check_permission("select")
        rows = query()
        assert {row["name"] for row in rows} == {first["name"], second["name"]}
        view = detail(first)
        assert view["item_code"] == first["item_code"] and view["item"]["stock_uom"] == stock_uom
        assert view["item"]["disabled"] == 1 and not view["profile"]["inactive"]
        assert view["profile"]["display_uom"] == "对（独立原文，不作换算）"
        assert view["profile"]["legacy_code"] == prefix + "-同一原文代码"
        assert view["profile"]["specification"] == " 规格原文\n第二行 "
        assert Decimal(str(view["profile"]["list_price"])) == Decimal("17.5")
        assert "stock_qty" not in view["item"] and "image" not in view["item"]
        assert view["image"]["source_doctype"] == "Item" and view["image"]["url"] == ""
        assert all(meta["read_only"] for group in view["field_meta"].values() for meta in group.values())
        passed("real_stock_user_company_select_and_projected_raw_values")

        current = "separate_kinds_and_explicit_profile_status"
        assert [row["name"] for row in query("材料")] == [material["name"]]
        assert [row["name"] for row in query("材料", status="停用")] == [stopped["name"]]
        assert {row["name"] for row in query("材料", status="全部")} == {material["name"], stopped["name"]}
        assert detail(stopped, "材料")["profile"]["inactive"] == 1
        rejected(lambda: read_archive(first["name"], "材料", company))
        passed("real_kind_status_and_inactive_historical_detail_are_explicit")

        current = "manager_browse_stays_readonly"
        frappe.set_user(manager)
        assert frappe.has_permission(PROFILE, "write")
        assert query() and detail(first)["can_write"] is False
        passed("real_stock_manager_has_write_role_but_browse_returns_only_readonly_actions")

        current = "company_user_permission_isolation"
        for user, hidden_company, hidden in ((clerk, other_company, foreign), (outsider, company, first)):
            frappe.set_user(user)
            rejected(lambda: list_archives("物料", hidden_company, prefix))
            rejected(lambda: read_archive(hidden["name"], "物料", hidden_company))
        passed("real_company_user_permission_guards_query_and_read_without_hidden_source_names")

        current = "item_user_permission_identity"
        frappe.set_user(item_reader)
        assert [row["name"] for row in query()] == [first["name"]]
        assert detail(first)["item_code"] == first["item_code"]
        rejected(lambda: read_archive(second["name"], "物料", company))
        assert query("材料", status="全部") == []
        passed("real_item_user_permission_applies_to_native_profile_and_item_projections")

        current = "profile_user_permission_identity"
        frappe.set_user(profile_reader)
        assert [row["name"] for row in query()] == [first["name"]]
        rejected(lambda: read_archive(second["name"], "物料", company))
        passed("real_profile_user_permission_hides_other_records_even_when_item_is_readable")

        current = "guest_and_invalid_query"
        frappe.set_user("Guest")
        rejected(lambda: list_archives("物料", company, prefix))
        rejected(lambda: read_archive(first["name"], "物料", company))
        frappe.set_user(clerk)
        rejected(lambda: list_archives("物料", company, prefix, "未知"), frappe.ValidationError)
        passed("guest_and_invalid_status_denied_without_write_side_effects")
    except Exception as error:
        primary = error
        error.add_note("库存浏览原生验收失败门槛：" + current)
        error.add_note("已通过门槛：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:
        def collect():
            records.update((document.doctype, document.name) for document in objects if document.name)

        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks_before)

        def restore():
            _profile_restore_caller(original_user, original_test, users, records, callbacks_before, context_before)

        _profile_audit([
            ("collect_exact_fixture_records", collect),
            ("native_rollback_and_only_new_cleanup_callbacks", rollback),
            ("restore_caller_and_invalidate_owned_caches", restore),
            ("fixture_records_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("full_inventory_item_children_price_profile_file_user_auth_series_error_hashes_restored", lambda: _contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts_before),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger_before),
            ("caller_queues_and_local_context_restored", lambda: _callbacks() == callbacks_before and _local_context() == context_before),
        ], primary=primary)
    checks.append("full_native_sql_rows_children_counts_ledgers_and_caller_context_rolled_back")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "actual_roles": roles, "contents_before": before, "contents_after": _contents(),
            "table_counts_before": counts_before, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(),
            "gui_verified": False, "remaining": ["旧物料/材料浏览操作与UI", "实际Item图片HTTP/下载",
                "Company只有select的真实岗位", "原生字段read/mask撤权", "双连接并发权限或来源变化"]}
