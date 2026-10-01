"""One serial native product vocabulary experiment; always roll back everything.

After migration, invoke directly once in an exclusive business-write window::

    from jifeng_erp.product_dictionary_validation import smoke
    result = smoke(company="Jifeng ERP UI Validation",
                   other_company="Jifeng ERP Validation",
                   item_group="Products", stock_uom="Nos")

Do not use bench execute, whose exception fallback can invoke a failed helper
twice. This helper never commits, migrates, downloads, changes DocPerm or writes
ledger rows. Synthetic users receive existing roles and scoped User Permissions.
Native failures propagate after every rollback audit; no automatic retry.
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
    _callbacks, _digest, _restore_callbacks, _tab_counts, _versions,
)
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_dictionary import (
    COLLISION, DOCTYPE, FIELDS, IDENTITY, REQUEST_FIELDS, TYPES,
    init_dictionary, list_dictionary, read_dictionary, resolve_creation, save_dictionary,
)


PROFILE = "Jifeng Product Profile"


def _native_contents():
    """Compare full installed rows internally; only opaque hashes leave here."""
    doctypes = {DOCTYPE, PROFILE, "Item", "Employee", "Jifeng Employee Supplement",
                "User", "Role Profile", "User Permission", "DocShare", "Version", "Series", "File"}
    doctypes = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result = {doctype: _digest(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)}
    result["__Auth"] = _digest(frappe.db.sql("SELECT * FROM `__Auth` ORDER BY doctype, name, fieldname", as_dict=True))
    return result


def smoke(company, other_company, item_group, stock_uom):
    """Real manager roles, mutable raw words, native Version, scoped receipts."""
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb", "全表回滚审计需要当前 MariaDB 站点"
    assert other_company and other_company != company
    for doctype, name in (("Company", company), ("Company", other_company),
                          ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "产品词典验收缺少指定公司、分组或单位"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    meta = frappe.get_meta(DOCTYPE)
    assert meta.track_changes and not meta.is_submittable
    assert {field.fieldname for field in meta.fields if field.unique} == {"request_id"}
    assert all(meta.get_field(field).set_only_once for field in IDENTITY)
    assert not meta.get_field("value").set_only_once
    assert all(frappe.get_meta(PROFILE).get_field(field).fieldtype == "Data" for field in TYPES.values())
    for field in REQUEST_FIELDS:
        assert all(meta.get_field(field).get(attr) for attr in ("hidden", "read_only", "no_copy", "print_hide"))
    tables, counts_before = _tab_counts()
    before, ledger_before = _native_contents(), _ledger_snapshot()
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    prefix = "JF-QA-PROD-DICT-" + frappe.generate_hash(length=10)
    point = "jf_product_dictionary_" + frappe.generate_hash(length=8)
    users, objects, records, checks, actual_roles = [], [], set(), [], {}
    evidence, current, primary = {}, "fixture_setup", None
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
        return document

    def actor(label, roles, companies, dictionary_name=None):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid",
            first_name=prefix + "-" + label, send_welcome_email=0, roles=[{"role": role} for role in roles])
        users.append(user.name)
        for allowed in companies:
            insert("User Permission", user=user.name, allow="Company", for_value=allowed, apply_to_all_doctypes=1)
        if dictionary_name:
            insert("User Permission", user=user.name, allow=DOCTYPE, for_value=dictionary_name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        actual_roles[label] = sorted(frappe.get_roles(user.name))
        return user.name

    def payload(kind="风格", **changes):
        return {"request_id": str(uuid4()), "company": company, "dictionary_type": kind,
                "legacy_number": "001", "value": " " + prefix + "-" + kind + " ",
                "remarks": " 原文备注\n第二行 " + prefix, "abbreviation": " 手工简拼 " + prefix,
                "inactive": 0, **changes}

    def save(values):
        archive = native(lambda: save_dictionary(values))
        records.add((DOCTYPE, archive["name"]))
        return archive

    def update(archive, **changes):
        return save({field: archive[field] for field in ("name", "version", *FIELDS) if field in archive} | changes)

    def rejected(action, errors=frappe.ValidationError, *, text=None, hidden=(), atomic=False):
        nested = "jf_product_dictionary_reject_" + frappe.generate_hash(length=8)
        contents, callbacks, context, ledger = _native_contents(), _callbacks(), _local_context(), _ledger_snapshot()
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
                    assert value not in str(error) and value not in queued, "隐藏词条身份泄露"
                if text == COLLISION:
                    assert error.__context__ is None, "原生unique异常上下文泄露"
                    assert "新建请求身份" not in queued, "原生unique内部UI消息泄露"
                if atomic:
                    assert _native_contents() == contents, "API自身未完整回滚"
                    assert _callbacks() == callbacks and _local_context() == context, "API失败破坏既有事务上下文"
            else:
                raise AssertionError("产品词典接受了本应拒绝的操作")
        finally:
            frappe.db.rollback(save_point=nested)
            _restore_callbacks(callbacks)
            _restore_local_context(context)
        assert _ledger_snapshot() == ledger, "拒绝操作改变了 GL/SLE"

    def passed():
        checks.append(current)
        assert _ledger_snapshot() == ledger_before

    try:
        frappe.set_user("Administrator")
        actors = {
            "stock": actor("stock", ["Stock Manager"], [company]),
            "manufacturing": actor("manufacturing", ["Manufacturing Manager", "Manufacturing User"], [company]),
            "system": actor("system", ["System Manager"], [company]),
        }
        outsider = actor("outside", ["Stock Manager"], [other_company])
        clerk = actor("clerk", ["Stock User"], [company])
        sales = actor("sales", ["Sales User"], [company])
        assert all("System Manager" not in actual_roles[label] for label in ("stock", "manufacturing", "outside", "clerk", "sales"))

        current = "real_three_manager_roles_native_field_permissions_creation_and_version"
        originals, archives = {}, {}
        evidence["native_field_permissions"] = {}
        for label, user in actors.items():
            frappe.set_user(user)
            readable = set(get_permitted_fields(DOCTYPE, permission_type="read", ignore_virtual=True))
            writable = set(get_permitted_fields(DOCTYPE, permission_type="write", ignore_virtual=True))
            assert set(FIELDS).issubset(readable & writable)
            evidence["native_field_permissions"][label] = {field: int(meta.get_field(field).permlevel or 0) for field in FIELDS}
            initialized = native(lambda: init_dictionary("风格"))
            assert initialized["company"] is None and initialized["name"] is None and initialized["version"] is None
            assert initialized["can_write"] and not initialized["field_meta"]["value"]["read_only"]
            originals[label] = payload(value=" " + prefix + "-" + label + " ")
            archive = save(originals[label])
            assert archive["value"] == originals[label]["value"]
            count = len(_versions(DOCTYPE, archive["name"]))
            changed = update(archive, remarks="正式角色修改-" + prefix)
            assert len(_versions(DOCTYPE, archive["name"])) > count
            assert _version_has(frappe.get_doc(DOCTYPE, changed["name"]), "remarks")
            archives[label] = changed
        passed()

        current = "three_types_independent_raw_fields_duplicate_names_numbers_and_company"
        frappe.set_user(actors["stock"])
        words = {kind: save(payload(kind)) for kind in TYPES}
        same = save(payload())
        assert same["name"] != words["风格"]["name"] and same["legacy_number"] == words["风格"]["legacy_number"]
        assert same["value"] == words["风格"]["value"]
        for kind, archive in words.items():
            assert archive["dictionary_type"] == kind and archive["legacy_number"] == "001"
            assert archive["abbreviation"].startswith(" 手工简拼 ") and "\n" in archive["remarks"]
            rows = native(lambda kind=kind: list_dictionary(kind, company, prefix))
            assert any(row["name"] == archive["name"] and row["version"] == archive["version"] for row in rows)
        frappe.set_user("Administrator")
        foreign = save(payload(company=other_company))
        assert foreign["name"] != words["风格"]["name"]
        passed()

        current = "native_form_server_generated_uuid_and_original_creation_digest"
        frappe.set_user(actors["stock"])
        direct = insert(DOCTYPE, company=company, dictionary_type="风格", value=prefix + "-native", inactive=0)
        assert UUID(direct.request_id).version == 4 and len(direct.request_digest) == 64
        document = frappe.get_doc(DOCTYPE, direct.name)
        document.value, document.remarks, document.abbreviation = prefix + "-native-renamed", "原生备注", "自定简拼"
        native(document.save)
        assert document.request_id == direct.request_id and document.request_digest == direct.request_digest
        assert _version_has(document, "value") and _version_has(document, "abbreviation")
        passed()

        current = "native_form_and_api_server_metadata_injection_rejected"
        for field, value in (("request_id", str(uuid4())), ("request_digest", "f" * 64)):
            rejected(lambda field=field, value=value: insert(DOCTYPE, company=company, dictionary_type="品牌",
                value=prefix + "-forged", inactive=0, **{field: value}), text="只能由服务器生成")
        rejected(lambda: save_dictionary(payload() | {"request_digest": "client-forged"}), text="不支持的参数", atomic=True)
        passed()

        current = "mutable_names_keep_profile_raw_history_and_shared_item_unchanged"
        frappe.set_user("Administrator")
        item = insert("Item", item_code=prefix + "-ITEM", item_name=prefix + "-ITEM", item_group=item_group,
                      stock_uom=stock_uom, is_stock_item=0)
        profile = insert(PROFILE, item_code=item.name, company=company,
                         **{field: words[kind]["value"] for kind, field in TYPES.items()})
        profile_before = _digest(profile.as_dict())
        item_before = _digest(frappe.get_doc("Item", item.name).as_dict())
        frappe.set_user(actors["stock"])
        old = words["风格"]
        original_receipt = tuple(frappe.get_doc(DOCTYPE, old["name"]).get(field) for field in REQUEST_FIELDS)
        changed = update(old, value=prefix + "-改名", legacy_number="不同原文编号", remarks="改名备注", abbreviation="原样手改", inactive=1)
        assert changed["name"] == old["name"] and changed["version"] != old["version"]
        assert tuple(frappe.get_doc(DOCTYPE, old["name"]).get(field) for field in REQUEST_FIELDS) == original_receipt
        assert _version_has(frappe.get_doc(DOCTYPE, old["name"]), "value")
        assert _digest(frappe.get_doc(PROFILE, profile.name).as_dict()) == profile_before
        assert _digest(frappe.get_doc("Item", item.name).as_dict()) == item_before
        words["风格"] = changed
        passed()

        current = "inactive_management_filter_preserves_raw_text_and_reread_version"
        assert not any(row["name"] == changed["name"] for row in native(lambda: list_dictionary("风格", company, prefix)))
        listed = native(lambda: list_dictionary("风格", company, prefix, include_inactive=True))
        selected = next(row for row in listed if row["name"] == changed["name"])
        reread = native(lambda: read_dictionary(changed["name"], "风格"))
        assert selected["version"] == reread["version"] and selected["value"] == reread["value"]
        assert reread["inactive"] == 1 and not reread["field_meta"]["value"]["read_only"]
        words["风格"] = update(changed, inactive=0)
        passed()

        current = "unknown_create_receipt_after_rename_is_read_only_without_reverting"
        original = originals["stock"]
        archive = archives["stock"]
        renamed = update(archive, value=prefix + "-后续改名")
        contents, count = _native_contents(), len(_versions(DOCTYPE, renamed["name"]))
        assert save(original) == renamed
        found = native(lambda: resolve_creation(original["request_id"], "风格", company))
        assert found["status"] == "found" and found["archive"] == renamed
        assert native(lambda: resolve_creation(str(uuid4()), "风格", company)) == {"status": "not_found"}
        assert _native_contents() == contents and len(_versions(DOCTYPE, renamed["name"])) == count
        rejected(lambda: save_dictionary(original | {"value": "不同初始内容"}), text=COLLISION, atomic=True)
        archives["stock"] = renamed
        passed()

        current = "explicit_version_lock_rejects_stale_write_without_partial_versions"
        rejected(lambda: update(archive, value="过时覆盖"), frappe.TimestampMismatchError, text="重读核对", atomic=True)
        refreshed = update(renamed, remarks="最新版本保存")
        assert native(lambda: read_dictionary(refreshed["name"]))["version"] == refreshed["version"]
        archives["stock"] = refreshed
        passed()

        current = "native_and_api_company_type_creation_identity_immutable"
        frappe.set_user("Administrator")
        target = words["风格"]
        for field, value in (("company", other_company), ("dictionary_type", "来源")):
            rejected(lambda field=field, value=value: update(target, **{field: value}), text="不能修改", atomic=True)
        for field, value in (("company", other_company), ("dictionary_type", "品牌"),
                             ("request_id", str(uuid4())), ("request_digest", "f" * 64)):
            document = frappe.get_doc(DOCTYPE, target["name"])
            document.set(field, value)
            rejected(document.save, text="不能修改")
        rejected(lambda: update(target, request_id=str(uuid4())), text="不能改写新建请求身份", atomic=True)
        rejected(lambda: read_dictionary(target["name"], "来源"), text="类型与当前")
        passed()

        current = "actual_company_user_permission_guards_list_read_resolve_and_create"
        frappe.set_user(actors["stock"])
        rejected(lambda: list_dictionary("风格", other_company), frappe.PermissionError)
        rejected(lambda: read_dictionary(foreign["name"]), frappe.PermissionError)
        rejected(lambda: resolve_creation(frappe.get_doc(DOCTYPE, foreign["name"]).request_id, "风格", other_company), frappe.PermissionError)
        rejected(lambda: save_dictionary(payload(company=other_company)), frappe.PermissionError, atomic=True)
        frappe.set_user(outsider)
        rejected(lambda: list_dictionary("风格", company), frappe.PermissionError)
        passed()

        current = "actual_dictionary_user_permission_hides_records_and_receipts"
        frappe.set_user("Administrator")
        scoped = actor("scoped", ["Stock Manager"], [company], words["风格"]["name"])
        hidden_name = words["品牌"]["name"]
        hidden_token = frappe.get_doc(DOCTYPE, hidden_name).request_id
        hidden_digest = frappe.get_doc(DOCTYPE, hidden_name).request_digest
        frappe.set_user(scoped)
        assert native(lambda: list_dictionary("品牌", company, prefix)) == []
        assert native(lambda: resolve_creation(hidden_token, "品牌", company)) == {"status": "not_found"}
        rejected(lambda: read_dictionary(hidden_name), frappe.PermissionError)
        hidden_payload = payload("品牌", request_id=hidden_token)
        rejected(lambda: save_dictionary(hidden_payload), (frappe.PermissionError, frappe.ValidationError),
                 hidden=(hidden_name, hidden_digest), atomic=True)
        evidence["hidden_collision"] = "native User Permission denial or sanitized unique conflict; no identity disclosed"
        passed()

        current = "invalid_text_flags_uuid_type_and_stale_creation_version_rejected"
        frappe.set_user(actors["stock"])
        for changes in ({"dictionary_type": "类别"}, {"value": " "}, {"value": "x" * 141},
                        {"legacy_number": "x" * 141}, {"remarks": 1}, {"abbreviation": 1},
                        {"inactive": "yes"}, {"inactive": 1.0}, {"request_id": "not-a-uuid"}, {"version": "old"}):
            rejected(lambda changes=changes: save_dictionary(payload(**changes)), atomic=True)
        passed()

        current = "delete_explicitly_unimplemented_without_removing_referenced_or_unused_word"
        frappe.set_user("Administrator")
        for name in (words["风格"]["name"], direct.name):
            rejected(lambda name=name: frappe.delete_doc(DOCTYPE, name),
                     frappe.ValidationError, text="删除尚未实现")
            assert frappe.db.exists(DOCTYPE, name)
        passed()

        current = "stock_user_sales_user_and_guest_gain_no_dictionary_access"
        for user in (clerk, sales, "Guest"):
            frappe.set_user(user)
            assert not frappe.has_permission(DOCTYPE, "read")
            rejected(lambda: init_dictionary("风格"), frappe.PermissionError)
            rejected(lambda: list_dictionary("风格", company), frappe.PermissionError)
            rejected(lambda: read_dictionary(words["风格"]["name"]), frappe.PermissionError)
            rejected(lambda: resolve_creation(originals["stock"]["request_id"], "风格", company), frappe.PermissionError)
            rejected(lambda: save_dictionary(payload()), frappe.PermissionError, atomic=True)
        passed()
    except Exception as error:
        primary = error
        error.add_note("产品词典原生验收失败门槛：" + current)
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
            ("whole_native_employee_user_auth_roles_up_profile_item_children_version_series_hashes_restored", lambda: _native_contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts_before),
            ("whole_gl_sle_hashes_restored", lambda: _ledger_snapshot() == ledger_before),
            ("original_callback_and_local_context_restored", lambda: _callbacks() == callbacks_before and _local_context() == context_before),
        ], primary=primary)
    checks.append("all_fixtures_native_versions_whole_hashes_counts_and_caller_context_rolled_back")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "actual_roles": actual_roles, "evidence": evidence, "hashes_before": before, "hashes_after": _native_contents(),
            "table_counts_before": counts_before, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(),
            "gui_verified": False, "concurrency": "serial rollback experiment; not raced",
            "remaining": ["旧删除操作", "自动简拼更新", "旧编号预填序列", "打印/预览/导入/导出及经典列表客户端"]}
