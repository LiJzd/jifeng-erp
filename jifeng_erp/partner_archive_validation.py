"""Serial native factory archive acceptance, synthetic and always rolled back.

Invoke once by direct initialized Python after migration in an exclusive write
window. No commit mode, DocPerm change, controller bypass, browser or download.
"""

from copy import deepcopy
import json
from uuid import uuid4

import frappe

from jifeng_erp.catalog_validation import (
    _native_without_error_log, _profile_audit, _profile_restore_caller, _profile_rollback,
)
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.partner_archive import (
    KIND, PROFILE, init_archive, query_archives, read_archive, read_supplier,
    resolve_creation, save_archive,
)
from jifeng_erp.personnel_changes_validation import _callbacks, _tab_counts, _versions
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _document_snapshot, _native_contents, _sql_row_hash


def _contents():
    result = _native_contents()
    doctypes = {PROFILE, "Supplier", "Error Log"}
    for doctype in tuple(doctypes):
        assert frappe.db.exists("DocType", doctype), "工厂验收需已迁移的真实来源"
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    return result


def smoke(company, other_company):
    frappe.only_for("System Manager")
    assert company and other_company and company != other_company
    for name in (company, other_company):
        assert frappe.db.exists("Company", name), "引用两家既有隔离公司"
    groups = frappe.get_all("Supplier Group", filters={"is_group": 0}, pluck="name", limit_page_length=1)
    assert groups, "引用既有明细供应商分组"
    meta = frappe.get_meta(PROFILE)
    assert meta.track_changes and not meta.is_submittable and meta.get_field("request_id").unique
    tables, counts_before = _tab_counts()
    before, ledger_before = _contents(), _ledger_snapshot()
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    point = "jf_partner_archive_" + frappe.generate_hash(length=8)
    prefix = "JF-QA-PARTNER-" + frappe.generate_hash(length=10)
    users, objects, records, checks, roles = [], [], set(), [], {}
    current, primary = "native_fixture_setup", None

    def native(action):
        return _native_without_error_log(action, current)

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((doctype, document.name))
        return document

    def payload(label, supplier, owner_company=company):
        return {"kind": KIND, "company": owner_company, "request_id": str(uuid4()),
                "supplier_version": read_supplier(supplier.name, KIND, owner_company)["version"],
                "profile": {"supplier": supplier.name, "legacy_number": prefix + "-" + label,
                    "display_name": " 合成 <b>工厂</b> " + label, "owner_name": "合成负责",
                    "mobile": "合成手机原文", "reception": "合成接货原文", "phone": "合成电话原文",
                    "address": " 合成地址\n第二行 ", "notes": "合成备注", "bank_name": "合成开户原文",
                    "account_name": "合成户名原文", "bank_account": "0000123400", "inactive": 0}}

    def create(request):
        result = native(lambda: save_archive(request))
        records.add((PROFILE, result["name"]))
        return result

    def edit(view, values):
        return {"kind": KIND, "company": view["company"], "name": view["name"],
                "profile_version": view["profile_version"], "supplier_version": view["supplier_version"],
                "profile": values}

    def actor(label, role, allowed_company=company, scope=None):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid",
                      first_name=prefix + label, send_welcome_email=0, roles=[{"role": role}])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=allowed_company, apply_to_all_doctypes=1)
        if scope:
            insert("User Permission", user=user.name, allow=scope[0], for_value=scope[1], apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        roles[label] = sorted(frappe.get_roles(user.name))
        assert "System Manager" not in roles[label]
        return user.name

    def pure(action):
        state, callbacks, context = _contents(), _callbacks(), _local_context()
        result = native(action)
        assert _contents() == state and _callbacks() == callbacks and _local_context() == context
        assert _ledger_snapshot() == ledger_before
        return result

    def rejected(action, errors=frappe.PermissionError):
        state, callbacks, context = _contents(), _callbacks(), _local_context()
        try:
            native(action)
        except errors as error:
            if any("回滚清理失败" in note for note in getattr(error, "__notes__", [])):
                raise
        else:
            raise AssertionError("原生工厂请求未拒绝无权、冲突或坏参数")
        assert _contents() == state and _callbacks() == callbacks and _local_context() == context
        assert _ledger_snapshot() == ledger_before

    def passed(label):
        checks.append(label)
        assert _ledger_snapshot() == ledger_before

    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        first_supplier = insert("Supplier", supplier_name=prefix + "-供应商A",
                                supplier_group=groups[0], supplier_type="Company")
        second_supplier = insert("Supplier", supplier_name=prefix + "-供应商B",
                                 supplier_group=groups[0], supplier_type="Company")
        supplier_before = _document_snapshot("Supplier", first_supplier.name)
        first_request, second_request = payload("A", first_supplier), payload("B", second_supplier)
        first, second = create(first_request), create(second_request)
        foreign = create(payload("异公司", first_supplier, other_company))
        assert first["profile"]["bank_account"] == "0000123400"
        assert first["profile"]["address"] == " 合成地址\n第二行 "
        assert first["profile"]["display_name"] == first_request["profile"]["display_name"]
        assert _document_snapshot("Supplier", first_supplier.name) == supplier_before
        passed("native_explicit_supplier_and_raw_contact_bank_text_without_supplier_or_ledger_change")

        current = "idempotent_creation_and_pure_resolution"
        state = _contents()
        assert create(first_request)["name"] == first["name"] and _contents() == state
        assert pure(lambda: resolve_creation(first_request["request_id"], KIND, company))["archive"]["name"] == first["name"]
        changed = deepcopy(first_request)
        changed["profile"]["notes"] = "同UUID不同内容"
        rejected(lambda: save_archive(changed), frappe.ValidationError)
        passed("native_same_uuid_reuse_changed_digest_rejection_and_readonly_resolution")

        current = "native_profile_edit_preserves_shared_supplier"
        first = native(lambda: save_archive(edit(first, {"notes": "只改公司工厂档案", "inactive": 1})))
        assert first["profile"]["inactive"] == 1 and _versions(PROFILE, first["name"])
        assert _document_snapshot("Supplier", first_supplier.name) == supplier_before
        for field in ("profile_version", "supplier_version"):
            stale = edit(first, {"notes": "陈旧版本"})
            stale[field] = "2000-01-01T00:00:00"
            rejected(lambda stale=stale: save_archive(stale), frappe.TimestampMismatchError)
        for field, value in (("supplier", second_supplier.name), ("company", other_company), ("request_id", str(uuid4()))):
            rejected(lambda field=field, value=value: save_archive(edit(first, {field: value})), frappe.ValidationError)
        passed("native_stop_version_history_double_version_and_fixed_company_supplier_identity")

        current = "invalid_new_request_atomic_rollback"
        invalid = payload("坏必填", first_supplier)
        invalid["profile"]["display_name"] = ""
        rejected(lambda: save_archive(invalid), frappe.ValidationError)
        assert not frappe.db.exists(PROFILE, {"request_id": invalid["request_id"]})
        passed("native_required_field_failure_has_no_archive_version_series_or_caller_side_effect")

        current = "actual_roles_and_company_supplier_profile_permissions"
        manager = actor("manager", "Purchase Manager")
        reader = actor("reader", "Purchase User")
        supplier_reader = actor("supplier", "Purchase User", scope=("Supplier", first_supplier.name))
        profile_reader = actor("profile", "Purchase User", scope=(PROFILE, second["name"]))
        outsider = actor("other", "Purchase User", allowed_company=other_company)
        frappe.set_user(manager)
        assert frappe.has_permission(PROFILE, "create") and frappe.has_permission(PROFILE, "write")
        third = create(payload("实际主管", first_supplier))
        third = native(lambda: save_archive(edit(third, {"notes": "实际采购主管保存"})))
        assert third["can_write"] and _document_snapshot("Supplier", first_supplier.name) == supplier_before
        passed("actual_purchase_manager_creates_and_updates_without_system_manager_and_supplier_mutation")

        current = "actual_reader_readonly_pages_and_detail"
        frappe.set_user(reader)
        view = pure(lambda: read_archive(second["name"], KIND, company))
        assert not view["can_write"] and all(field["read_only"] for field in view["field_meta"].values())
        page = pure(lambda: query_archives(KIND, company, prefix, page="1", page_size="1"))
        assert page["total"] == 2 and page["total_pages"] == 2 and len(page["rows"]) == 1
        next_page = pure(lambda: query_archives(KIND, company, prefix, page="2", page_size="1"))
        assert next_page["rows"][0]["name"] != page["rows"][0]["name"]
        stopped = pure(lambda: query_archives(KIND, company, prefix, status="停用"))
        assert [row["name"] for row in stopped["rows"]] == [first["name"]]
        rejected(lambda: save_archive(edit(view, {"notes": "只读用户不能保存"})))
        rejected(lambda: init_archive(KIND, company))
        passed("actual_purchase_user_readonly_raw_detail_two_exact_pages_and_inactive_history")

        current = "user_permission_identity_scopes"
        for user, expected in ((supplier_reader, {first["name"], third["name"]}), (profile_reader, {second["name"]})):
            frappe.set_user(user)
            result = pure(lambda: query_archives(KIND, company, prefix, status="全部"))
            assert {row["name"] for row in result["rows"]} == expected and result["total"] == len(expected)
        frappe.set_user(supplier_reader)
        rejected(lambda: read_archive(second["name"], KIND, company))
        frappe.set_user(profile_reader)
        rejected(lambda: read_archive(first["name"], KIND, company))
        passed("native_supplier_and_profile_user_permissions_restrict_visible_rows_total_and_read")

        current = "company_scope_and_guest_rejection"
        for user, hidden_company, hidden in ((reader, other_company, foreign), (outsider, company, second)):
            frappe.set_user(user)
            rejected(lambda: query_archives(KIND, hidden_company, prefix))
            rejected(lambda: read_archive(hidden["name"], KIND, hidden_company))
        frappe.set_user("Guest")
        rejected(lambda: query_archives(KIND, company, prefix))
        rejected(lambda: read_archive(second["name"], KIND, company))
        passed("actual_company_user_permission_and_guest_do_not_expose_other_archive_sources")

        current = "invalid_pagination_remains_pure_read"
        frappe.set_user(reader)
        for options in ({"page": True}, {"page": 1.0}, {"page": " 1"}, {"page": 0}, {"page_size": 101}, {"page": 3, "page_size": 1}):
            rejected(lambda options=options: query_archives(KIND, company, prefix, **options), frappe.ValidationError)
        passed("native_invalid_bool_float_whitespace_zero_large_size_and_outside_page_rejected")

        current = "one_save_native_supplier_and_factory_creation"
        frappe.set_user(manager)
        defaults = pure(lambda: init_archive(KIND, company))
        assert defaults["can_create_supplier"] and defaults["can_link_supplier"]
        group_values = deepcopy(defaults["supplier_defaults"])
        group_values.update(supplier_group=groups[0], supplier_type="Company")
        joint_request = {"kind": KIND, "company": company, "request_id": str(uuid4()),
                         "supplier_create": group_values,
                         "profile": {"legacy_number": prefix + "-一次新建",
                                     "display_name": prefix + "-合成联合供应商",
                                     "bank_account": "0000567800", "inactive": 0}}
        joint = create(joint_request)
        records.add(("Supplier", joint["supplier"]))
        linked = frappe.get_doc("Supplier", joint["supplier"])
        assert linked.supplier_name == joint_request["profile"]["display_name"] and not linked.disabled
        assert joint["profile"]["bank_account"] == "0000567800"
        assert _document_snapshot("Supplier", first_supplier.name) == supplier_before
        joint_supplier_before = _document_snapshot("Supplier", joint["supplier"])
        repeated_before = _contents()
        assert create(joint_request)["name"] == joint["name"] and _contents() == repeated_before
        joint = native(lambda: save_archive(edit(joint, {"notes": "只改联合新建后的档案原文"})))
        assert _document_snapshot("Supplier", joint["supplier"]) == joint_supplier_before
        passed("actual_purchase_manager_one_save_native_supplier_factory_idempotence_and_no_shared_supplier_edit")

        current = "second_document_failure_rolls_back_new_supplier"
        broken_joint = deepcopy(joint_request)
        broken_joint["request_id"] = str(uuid4())
        broken_joint["profile"].update(legacy_number="", display_name=prefix + "-合成失败供应商")
        rejected(lambda: save_archive(broken_joint), frappe.ValidationError)
        assert not frappe.db.exists(PROFILE, {"request_id": broken_joint["request_id"]})
        passed("native_second_profile_failure_rolls_back_new_supplier_children_series_and_callback_queues")
    except Exception as error:
        primary = error
        error.add_note("工厂档案原生验收失败门槛：" + current)
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
            ("full_supplier_children_profile_user_auth_series_versions_errors_restored", lambda: _contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts_before),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger_before),
            ("caller_queues_and_local_context_restored", lambda: _callbacks() == callbacks_before and _local_context() == context_before),
        ], primary=primary)
    checks.append("full_native_sources_children_counts_series_ledgers_and_caller_rolled_back")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "actual_roles": roles, "contents_before": before, "contents_after": _contents(),
            "table_counts_before": counts_before, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(),
            "gui_verified": False, "remaining": ["旧工厂实际弹层及完整GUI", "字段read/mask撤权真实岗位", "Company仅select岗位", "双连接竞争", "历史迁移/导入/打印"]}
