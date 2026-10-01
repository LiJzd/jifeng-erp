"""One serial, direct Python native category experiment, always rollback.

After root migrates the two ordinary DocTypes, call smoke(company,
other_company,item_group,stock_uom) once in an exclusive write window. Do not
use bench execute or retry an unknown outcome. This helper never commits,
changes DocPerm/schema, downloads, performs GUI actions or writes a ledger.
Only the small audit utilities are reused; no other smoke suite is invoked.
"""

import json
from uuid import UUID, uuid4

import frappe
from frappe.model import get_permitted_fields

from jifeng_erp.catalog_validation import (
    _profile_audit, _profile_restore_caller, _profile_rollback, _version_has,
)
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import (
    _callbacks, _restore_callbacks, _tab_counts, _versions,
)
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_category import (
    COLLISION, DOCTYPES, EDIT_FIELDS, FIELDS, IDENTITY, REQUEST_FIELDS,
    init_record, list_records, read_record, resolve_creation, save_record,
)
from jifeng_erp.product_dictionary_validation import (
    _document_snapshot, _native_contents as _common_contents, _sql_row_hash,
)


PROFILE = "Jifeng Product Profile"


def _native_contents():
    """Full physical rows and installed children; no Document.as_dict oracle."""
    result = _common_contents()
    doctypes = set(DOCTYPES.values())
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    return result


def smoke(company, other_company, item_group, stock_uom):
    """Real roles, raw layers, native version/receipt and exact SQL rollback."""
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb", "原生全表回滚审计需要当前 MariaDB 站点"
    assert other_company and other_company != company
    for doctype, name in (("Company", company), ("Company", other_company),
                          ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "类别验收缺少指定公司、商品分组或单位"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    for kind, doctype in DOCTYPES.items():
        meta = frappe.get_meta(doctype)
        assert meta.track_changes and not meta.is_submittable and not meta.istable
        assert not meta.allow_rename
        assert {field.fieldname for field in meta.fields if field.unique} == {"request_id"}
        assert all(meta.get_field(field).set_only_once for field in IDENTITY[kind])
        assert all(all(meta.get_field(field).get(attr) for attr in ("hidden", "read_only", "no_copy", "print_hide"))
                   for field in REQUEST_FIELDS)
        assert not any(row.get("delete") for row in meta.permissions)
    detail_meta = frappe.get_meta(DOCTYPES["detail"])
    assert all(detail_meta.get_field(field).fieldtype == "Data" for field in EDIT_FIELDS["detail"] if field != "inactive")
    assert detail_meta.get_field("conversion_value").default in (None, "")
    assert detail_meta.get_field("company").read_only

    tables, counts_before = _tab_counts()
    contents_before, ledger_before = _native_contents(), _ledger_snapshot()
    changed_types = {"Item", PROFILE, "User", "User Permission", "Version", "Series", "DocShare",
                     "DefaultValue", "Email Queue", "Notification Log", "Communication", *DOCTYPES.values()}
    for doctype in ("Item", PROFILE, "User", *DOCTYPES.values()):
        changed_types.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    assert all(tables["tab" + doctype] == "InnoDB" for doctype in changed_types)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    prefix = "JF-QA-PROD-CATEGORY-" + frappe.generate_hash(length=10)
    point = "jf_product_category_" + frappe.generate_hash(length=8)
    users, objects, records, checks, actual_roles = [], [], set(), [], {}
    evidence, current, primary, protected = {}, "fixture_setup", None, None
    _begin_rollback_only(point)

    def native(action):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = previous

    def remember(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        return document

    def insert(doctype, **values):
        document = remember(frappe.get_doc({"doctype": doctype, **values}))
        native(document.insert)
        records.add((document.doctype, document.name))
        records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
        return document

    def actor(label, roles, companies, parent_name=None):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid",
            first_name=prefix + "-" + label, send_welcome_email=0, roles=[{"role": role} for role in roles])
        users.append(user.name)
        for allowed in companies:
            insert("User Permission", user=user.name, allow="Company", for_value=allowed, apply_to_all_doctypes=1)
        if parent_name:
            insert("User Permission", user=user.name, allow=DOCTYPES["category"], for_value=parent_name,
                   apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        actual_roles[label] = sorted(frappe.get_roles(user.name))
        return user.name

    def payload(kind="category", category=None, **changes):
        values = {"record_type": kind, "company": company, "request_id": str(uuid4()), "inactive": 0}
        if kind == "category":
            values.update(legacy_code=" 原代码 ", major_name=" " + prefix + " 大类 ",
                          owner_name=" 负责原文 ", region=" 区域原文 ")
        else:
            values.update(category=category, minor_name=" " + prefix + " 小类 ", material=" 未映射材质 ",
                          display_uom=" 原式单位 ", conversion_uom_text=" 未确定方向单位 ", conversion_value=None)
        return values | changes

    def save(values):
        result = native(lambda: save_record(values))
        records.add((DOCTYPES[result["record_type"]], result["name"]))
        return result

    def update(archive, **changes):
        return save({"record_type": archive["record_type"], "name": archive["name"],
                     "version": archive["version"], **changes})

    def stable_contents():
        return {key: value for key, value in _native_contents().items()
                if key not in {*DOCTYPES.values(), "Version", "Series"}}

    def rejected(action, errors=frappe.ValidationError, *, text=None, hidden=(), atomic=False):
        nested = "jf_category_reject_" + frappe.generate_hash(length=8)
        before, callbacks, context, ledger = _native_contents(), _callbacks(), _local_context(), _ledger_snapshot()
        messages = len(getattr(frappe.local, "message_log", None) or [])
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                queued = json.dumps((getattr(frappe.local, "message_log", None) or [])[messages:], ensure_ascii=False)
                if text:
                    assert text in str(error), ("wrong native rejection", type(error).__name__, str(error))
                for value in hidden:
                    assert value not in str(error) and value not in queued, "隐藏类别身份泄露"
                if text == COLLISION:
                    assert error.__context__ is None, "底层 unique 异常上下文泄露"
                if atomic:
                    assert _native_contents() == before, "类别 API 失败未自行完整回滚"
                    assert _callbacks() == callbacks and _local_context() == context, "失败破坏既有回调或原生上下文"
            else:
                raise AssertionError("类别接受了本应拒绝的操作")
        finally:
            frappe.db.rollback(save_point=nested)
            _restore_callbacks(callbacks)
            _restore_local_context(context)
        assert _native_contents() == before and _ledger_snapshot() == ledger, "拒绝夹具回滚未恢复完整内容"

    def passed():
        checks.append(current)
        assert _ledger_snapshot() == ledger_before
        if protected is not None:
            assert stable_contents() == protected, "类别操作改变了员工、账户、Item、Profile或其它独立档案"

    try:
        frappe.set_user("Administrator")
        actors = {"stock": actor("stock", ["Stock Manager"], [company]),
                  "manufacturing": actor("manufacturing", ["Manufacturing Manager", "Manufacturing User"], [company]),
                  "system": actor("system", ["System Manager"], [company])}
        outsider = actor("outside", ["Stock Manager"], [other_company])
        clerk = actor("clerk", ["Stock User"], [company])
        business_item = insert("Item", item_code=prefix + "-item", item_name="独立业务商品",
                               item_group=item_group, stock_uom=stock_uom, is_stock_item=1)
        business_profile = insert(PROFILE, item_code=business_item.name, company=company,
                                  major_category="旧独立原文大类", material="旧独立原文材质", list_price=7)
        item_before = _document_snapshot("Item", business_item.name)
        profile_before = _document_snapshot(PROFILE, business_profile.name)
        protected = stable_contents()
        assert all("System Manager" not in actual_roles[label] for label in ("stock", "manufacturing", "outside", "clerk"))

        current = "real_three_manager_roles_fields_create_read_write_native_versions"
        mains, originals = {}, {}
        evidence["native_field_permissions"] = {}
        for label, user in actors.items():
            frappe.set_user(user)
            evidence["native_field_permissions"][label] = {}
            for kind, doctype in DOCTYPES.items():
                readable = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
                writable = set(get_permitted_fields(doctype, permission_type="write", ignore_virtual=True))
                required = set(FIELDS[kind]) - ({"company"} if kind == "detail" else set())
                assert set(FIELDS[kind]).issubset(readable) and required.issubset(writable)
                assert frappe.has_permission(doctype, "create") and frappe.has_permission(doctype, "select")
                evidence["native_field_permissions"][label][kind] = {
                    field: int(frappe.get_meta(doctype).get_field(field).permlevel or 0) for field in FIELDS[kind]}
            blank = native(lambda: init_record("category"))
            assert blank["company"] is None and blank["name"] is None and blank["can_write"]
            assert native(lambda: init_record("category", company))["company"] == company
            originals[label] = payload(major_name=" " + prefix + "-" + label + " ")
            initial = save(originals[label])
            version_count = len(_versions(DOCTYPES["category"], initial["name"]))
            mains[label] = update(initial, region=" 真实原生更新区域 " + prefix)
            assert len(_versions(DOCTYPES["category"], initial["name"])) > version_count
            assert _version_has(frappe.get_doc(DOCTYPES["category"], initial["name"]), "region")
        passed()

        current = "independent_two_layers_raw_values_derived_company_duplicate_text_no_stock_mapping"
        frappe.set_user(actors["stock"])
        main = mains["stock"]
        detail_input = payload("detail", main["name"], request_id=originals["stock"]["request_id"])
        detail_input.pop("company")
        detail = save(detail_input)
        sibling = save(payload("detail", main["name"]))
        other_parent = save(payload(major_name=" 第二大类 " + prefix))
        other_detail = save(payload("detail", other_parent["name"]))
        assert detail["company"] == company and detail["category"] == main["name"]
        assert native(lambda: resolve_creation(detail_input["request_id"], "detail", company,
                                               main["name"]))["archive"]["name"] == detail["name"]
        assert native(lambda: resolve_creation(detail_input["request_id"], "category", company))["archive"]["name"] == main["name"]
        assert detail["name"] != sibling["name"] and detail["minor_name"] == sibling["minor_name"]
        assert detail["material"] == detail_input["material"] and detail["display_uom"] == detail_input["display_uom"]
        initialized = native(lambda: init_record("detail", category=main["name"]))
        assert initialized["company"] == company and initialized["conversion_value"] is None
        assert initialized["field_meta"]["company"]["read_only"]
        assert _document_snapshot("Item", business_item.name) == item_before
        assert _document_snapshot(PROFILE, business_profile.name) == profile_before
        passed()

        current = "nullable_conversion_reference_preserves_zero_negative_precision_large_and_signed_zero"
        values = (None, "0", "-2.5", "1.23456789012345678901234567890123456789", "1E+400", "-0.000")
        for value in values:
            detail = update(detail, conversion_value=value)
            assert detail["conversion_value"] == value
            assert frappe.db.get_value(DOCTYPES["detail"], detail["name"], "conversion_value") == value
            assert native(lambda: read_record(detail["name"], "detail"))["conversion_value"] == value
            listed = native(lambda: list_records("detail", company, main["name"], prefix))
            assert next(row for row in listed if row["name"] == detail["name"])["conversion_value"] == value
        detail = update(detail, conversion_value=None)
        assert frappe.db.sql("SELECT conversion_value FROM `tabJifeng Product Category Detail` WHERE name=%s",
                             (detail["name"],))[0][0] is None
        evidence["conversion_storage"] = "nullable Data with finite Decimal text validation; empty, zero, precision and signed zero remain separate"
        passed()

        current = "both_record_native_form_insert_server_uuid_digest_and_serializable_identity_guard"
        direct = insert(DOCTYPES["category"], company=company, major_name=" 原生主项 " + prefix, inactive=0)
        direct_detail = insert(DOCTYPES["detail"], category=direct.name, company=company,
                               minor_name=" 原生明细 " + prefix, conversion_value="-3.50", inactive=0)
        for document in (direct, direct_detail):
            fresh = frappe.get_doc(document.doctype, document.name)
            assert UUID(fresh.request_id).version == 4 and len(fresh.request_digest) == 64
            assert "_jf_category_server" not in fresh.as_json()
        assert frappe.db.get_value(DOCTYPES["detail"], direct_detail.name, "conversion_value") == "-3.50"
        passed()

        current = "new_receipt_and_private_token_metadata_forgery_rejected_without_partial_save"
        for field, value in (("request_id", str(uuid4())), ("request_digest", "0" * 64), ("_jf_category_server", "server")):
            forged = remember(frappe.get_doc({"doctype": DOCTYPES["category"], "company": company,
                                             "major_name": "伪造" + prefix, "inactive": 0, field: value}))
            # Non-persisted arbitrary JSON keys are inert; a fake marker cannot
            # substitute a real before_insert server token. Receipt fields deny.
            if field == "_jf_category_server":
                forged.set("__islocal", 1)
                rejected(lambda forged=forged: forged.run_method("validate"))
            else:
                rejected(forged.insert)
        rejected(lambda: save_record(payload(request_digest="0" * 64)), atomic=True)
        passed()

        current = "original_uuid_receipt_tracks_editable_names_and_native_primary_key_rename_is_rejected"
        replayed = save(originals["stock"])
        assert replayed["name"] == main["name"] and replayed["region"] == main["region"]
        snapshot = _native_contents()
        assert save(originals["stock"]) == replayed and _native_contents() == snapshot
        renamed = update(mains["manufacturing"], major_name=" 修改显示名称 " + prefix, legacy_code=" 修改原文代码 ")
        resolved = native(lambda: resolve_creation(originals["manufacturing"]["request_id"], "category", company))
        assert resolved["archive"] == renamed and save(originals["manufacturing"]) == renamed
        frappe.set_user("Administrator")
        for doctype, name in ((DOCTYPES["category"], main["name"]), (DOCTYPES["detail"], detail["name"])):
            for force in (False, True):
                rejected(lambda doctype=doctype, name=name, force=force:
                         frappe.rename_doc(doctype, name, name + "-R", force=force), text="类别主键不能重命名")
        frappe.set_user(actors["stock"])
        passed()

        current = "stale_version_and_every_main_detail_identity_change_rejected_atomically"
        stale = detail
        detail = update(detail, material=" 后续原文 " + prefix)
        rejected(lambda: update(stale, minor_name="过期覆盖"), frappe.TimestampMismatchError, atomic=True)
        for archive, changes in ((main, {"company": other_company}), (detail, {"company": other_company}),
                                 (detail, {"category": other_parent["name"]}), (detail, {"request_id": str(uuid4())})):
            rejected(lambda archive=archive, changes=changes: update(archive, **changes), atomic=True)
        passed()

        current = "native_controller_locks_company_parent_and_receipt_identity"
        for doctype, name, field, value in ((DOCTYPES["category"], main["name"], "company", other_company),
                                           (DOCTYPES["detail"], detail["name"], "category", other_parent["name"]),
                                           (DOCTYPES["detail"], detail["name"], "request_digest", "0" * 64)):
            document = frappe.get_doc(doctype, name)
            document.set(field, value)
            rejected(document.save, (frappe.ValidationError, frappe.PermissionError))
        passed()

        current = "all_initial_fields_and_scope_conflicts_cannot_reuse_a_same_type_uuid"
        for kind, source in (("category", originals["manufacturing"]), ("detail", detail_input)):
            if kind == "detail":
                source = payload("detail", other_parent["name"])
                save(source)
            for field in EDIT_FIELDS[kind]:
                replacement = 1 if field == "inactive" else "-7.5" if field == "conversion_value" else "冲突原文"
                rejected(lambda source=source, field=field, replacement=replacement:
                         save_record(source | {field: replacement}), text=COLLISION, atomic=True)
            if kind == "detail":
                rejected(lambda source=source: save_record(source | {"category": main["name"]}),
                         text=COLLISION, atomic=True)
        frappe.set_user("Administrator")
        rejected(lambda: save_record(originals["stock"] | {"company": other_company}), text=COLLISION, atomic=True)
        frappe.set_user(actors["stock"])
        passed()

        current = "inactive_parent_hides_selection_denies_new_details_but_history_edit_and_original_receipt_remain"
        parent_input = payload(major_name=" 停用大类 " + prefix)
        pending_parent = save(parent_input)
        pending_input = payload("detail", pending_parent["name"])
        pending_detail = save(pending_input)
        pending_parent = update(pending_parent, inactive=1)
        assert not pending_parent["can_select"]
        assert native(lambda: list_records("detail", company, pending_parent["name"], prefix)) == []
        history = native(lambda: list_records("detail", company, pending_parent["name"], prefix, True))
        assert history and all(not row["can_select"] for row in history)
        pending_detail = update(pending_detail, material=" 停用父项后的独立历史原文 ")
        assert pending_detail["can_write"] and not pending_detail["can_select"]
        snapshot = _native_contents()
        assert save(pending_input)["name"] == pending_detail["name"]
        assert native(lambda: resolve_creation(pending_input["request_id"], "detail", company,
                                               pending_parent["name"]))["status"] == "found"
        assert _native_contents() == snapshot
        rejected(lambda: init_record("detail", category=pending_parent["name"]))
        rejected(lambda: save_record(payload("detail", pending_parent["name"])), atomic=True)
        passed()

        current = "inactive_detail_filters_only_new_selection_and_keeps_native_readable_history"
        other_detail = update(other_detail, inactive=1)
        assert not other_detail["can_select"]
        rows = native(lambda: list_records("detail", company, other_parent["name"], prefix))
        assert other_detail["name"] not in {row["name"] for row in rows}
        assert native(lambda: read_record(other_detail["name"], "detail"))["inactive"] == 1
        assert other_detail["name"] in {row["name"] for row in native(lambda:
            list_records("detail", company, other_parent["name"], prefix, True))}
        passed()

        current = "actual_company_user_permission_guards_main_detail_lists_reads_receipts_and_creation"
        frappe.set_user("Administrator")
        foreign_input = payload(company=other_company, major_name=" 外公司 " + prefix)
        foreign_parent = save(foreign_input)
        foreign_detail = save(payload("detail", foreign_parent["name"], company=other_company))
        frappe.set_user(actors["stock"])
        rejected(lambda: init_record("category", other_company), frappe.PermissionError)
        rejected(lambda: list_records("category", other_company), frappe.PermissionError)
        rejected(lambda: read_record(foreign_parent["name"], "category"), frappe.PermissionError)
        rejected(lambda: read_record(foreign_detail["name"], "detail"), frappe.PermissionError)
        rejected(lambda: resolve_creation(foreign_input["request_id"], "category", other_company), frappe.PermissionError)
        rejected(lambda: save_record(payload(company=other_company)), frappe.PermissionError, atomic=True)
        rejected(lambda: save_record(payload("detail", main["name"], company=other_company)), atomic=True)
        frappe.set_user(outsider)
        rejected(lambda: list_records("category", company), frappe.PermissionError)
        passed()

        current = "actual_parent_user_permission_hides_sibling_detail_and_receipt_without_identity_leak"
        frappe.set_user("Administrator")
        scoped = actor("scoped", ["Stock Manager"], [company], main["name"])
        protected = stable_contents()
        hidden_token = frappe.get_doc(DOCTYPES["category"], other_parent["name"]).request_id
        hidden_digest = frappe.get_doc(DOCTYPES["category"], other_parent["name"]).request_digest
        frappe.set_user(scoped)
        assert {row["name"] for row in native(lambda: list_records("category", company, search=prefix))} == {main["name"]}
        assert native(lambda: resolve_creation(hidden_token, "category", company)) == {"status": "not_found"}
        rejected(lambda: read_record(other_parent["name"], "category"), frappe.PermissionError)
        rejected(lambda: read_record(other_detail["name"], "detail"), frappe.PermissionError)
        rejected(lambda: init_record("detail", category=other_parent["name"]), frappe.PermissionError)
        rejected(lambda: save_record(payload(request_id=hidden_token)), (frappe.PermissionError, frappe.ValidationError),
                 hidden=(other_parent["name"], hidden_digest), atomic=True)
        passed()

        current = "invalid_check_uuid_titles_conversion_and_request_metadata_reject_whole_transaction"
        frappe.set_user(actors["stock"])
        for changes in ({"major_name": " "}, {"major_name": "x" * 141}, {"inactive": "yes"},
                        {"inactive": 1.0}, {"request_id": "not-a-uuid"}, {"version": "old"},
                        {"request_digest": "0" * 64}):
            rejected(lambda changes=changes: save_record(payload(**changes)), atomic=True)
        for value in (True, "nan", "Infinity", float("inf"), [], "9" * 141):
            rejected(lambda value=value: save_record(payload("detail", main["name"], conversion_value=value)), atomic=True)
        passed()

        current = "read_list_init_and_unknown_receipt_never_allocate_series_or_native_versions"
        snapshot = _native_contents()
        native(lambda: init_record("category", company))
        native(lambda: init_record("detail", category=main["name"]))
        native(lambda: read_record(main["name"], "category"))
        native(lambda: list_records("detail", company, main["name"], prefix, True))
        assert native(lambda: resolve_creation(str(uuid4()), "category", company)) == {"status": "not_found"}
        assert _native_contents() == snapshot
        passed()

        current = "explicit_delete_denial_preserves_parent_detail_and_item_profile_exact_rows"
        frappe.set_user("Administrator")
        for doctype, name in ((DOCTYPES["category"], mains["system"]["name"]), (DOCTYPES["detail"], direct_detail.name)):
            rejected(lambda doctype=doctype, name=name: frappe.delete_doc(doctype, name),
                     frappe.ValidationError, text="删除尚未实现")
            assert frappe.db.exists(doctype, name)
        assert _document_snapshot("Item", business_item.name) == item_before
        assert _document_snapshot(PROFILE, business_profile.name) == profile_before
        passed()

        current = "stock_user_and_guest_receive_no_extra_category_or_detail_permissions"
        for user in (clerk, "Guest"):
            frappe.set_user(user)
            for kind, doctype in DOCTYPES.items():
                assert not frappe.has_permission(doctype, "read")
                rejected(lambda kind=kind: init_record(kind, company, main["name"] if kind == "detail" else None), frappe.PermissionError)
                rejected(lambda kind=kind: list_records(kind, company, main["name"] if kind == "detail" else None), frappe.PermissionError)
            rejected(lambda: read_record(main["name"], "category"), frappe.PermissionError)
            rejected(lambda: resolve_creation(originals["stock"]["request_id"], "category", company), frappe.PermissionError)
            rejected(lambda: save_record(payload()), frappe.PermissionError, atomic=True)
        passed()
    except Exception as error:
        primary = error
        error.add_note("产品类别原生验收失败门槛：" + current)
        error.add_note("已通过门槛：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:
        def collect():
            records.update((doc.doctype, doc.name) for doc in objects if doc.name)

        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks_before)

        def restore():
            _profile_restore_caller(original_user, original_test, users, records, callbacks_before, context_before)

        _profile_audit([
            ("collect_exact_fixture_records", collect),
            ("native_database_rollback_and_only_new_cleanup_callbacks", rollback),
            ("invalidate_caches_then_restore_caller_queues_and_context", restore),
            ("fixture_records_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("whole_native_category_children_employee_user_auth_roles_up_item_profile_version_series_hashes_restored",
             lambda: _native_contents() == contents_before),
            ("all_table_counts_and_error_logs_restored", lambda: _tab_counts(tables)[1] == counts_before),
            ("whole_gl_sle_hashes_restored", lambda: _ledger_snapshot() == ledger_before),
            ("original_callback_and_local_context_restored", lambda: _callbacks() == callbacks_before and _local_context() == context_before),
        ], primary=primary)
    checks.append("all_fixtures_native_versions_whole_sql_hashes_series_counts_and_context_rolled_back")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "actual_roles": actual_roles, "evidence": evidence,
            "hashes_before": contents_before, "hashes_after": _native_contents(),
            "table_counts_before": counts_before, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(),
            "gui_verified": False, "concurrency": "one serial transaction; not raced",
            "remaining": ["整格批量保存", "删除", "旧类别返回键和持久ID", "换算方向和业务计算", "历史映射或回填", "真实字段permlevel变化"]}
