"""One real-role customer archive experiment in the designated isolated clone.

Invoke smoke directly once after the targeted DocType reload. Never use bench
execute's retry fallback. No commit, migration, DocPerm change, browser or
network work is performed. Synthetic fixtures, restricting Property Setters,
native Versions and counters are rolled back and the caller is restored.
"""

from copy import deepcopy
import json
from uuid import uuid4

import frappe

from jifeng_erp.catalog_validation import (
    _native_without_error_log, _profile_audit, _profile_restore_caller, _profile_rollback,
)
from jifeng_erp.customer_archive import (
    init_archive, query_archives, read_archive, read_customer, read_dictionary,
    resolve_creation, save_archive,
)
from jifeng_erp.customer_type import AUTO_MODE, DOCTYPE as DICTIONARY, save_dictionary
from jifeng_erp.customer_type_validation import _customer_metadata
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _tab_counts, _versions
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import (
    _document_snapshot, _native_contents, _sql_row_hash,
)


PROFILE = "Jifeng Customer Profile"
ISOLATED_SITE = "jifeng-plating-native-isolated.localhost"
ISOLATED_DATABASE = "jf_plap_isolated_20261002"
KINDS = ("销售客户", "订单客户")
TYPED_LINKS = {
    "销售客户": {"category": "客户分类", "grade": "客户等级", "customer_kind": "客户类型"},
    "订单客户": {"category": "订单客户分类", "grade": "订单客户级别"},
}
NATIVE = {
    "Customer", "Contact", "Address", "Dynamic Link", "Customer Group", "Territory",
    "Company", "Account", "Sales Order", "Sales Invoice", "Payment Entry", "Journal Entry",
    "GL Entry", "Stock Ledger Entry", "Payment Ledger Entry",
}


def _rows(doctypes, exclude_customers=()):
    """Complete installed parent/child hashes; no row values leave this helper."""
    selected = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(selected):
        selected.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result = {}
    for doctype in sorted(selected):
        table = "`tab" + doctype.replace("`", "``") + "`"
        where, values = "", ()
        if exclude_customers and doctype == "Customer":
            where = " WHERE name NOT IN (" + ",".join(["%s"] * len(exclude_customers)) + ")"
            values = tuple(sorted(exclude_customers))
        elif exclude_customers and doctype in {
            field.options for field in frappe.get_meta("Customer").get_table_fields()
        }:
            where = " WHERE NOT (COALESCE(parenttype,'')='Customer' AND COALESCE(parent,'') IN (" + ",".join(["%s"] * len(exclude_customers)) + "))"
            values = tuple(sorted(exclude_customers))
        result[doctype] = _sql_row_hash(frappe.db.sql("SELECT * FROM " + table + where + " ORDER BY name",
                                                   values, as_dict=True))
    return result


def _protected(exclude_customers=()):
    return {"native": _rows(NATIVE, exclude_customers), "dictionaries": _rows({DICTIONARY}),
            "customer_metadata": _customer_metadata()}


def _contents():
    result = _native_contents()
    result.update(_rows(NATIVE | {PROFILE, DICTIONARY, "Error Log", "Property Setter", "DocField",
                                  "DocType", "Custom Field", "Custom DocPerm"}))
    return result


def smoke(company, other_company):
    """Native permissions, typed links, receipts and complete rollback audit."""
    frappe.only_for("System Manager")
    assert frappe.local.site == ISOLATED_SITE and frappe.conf.db_name == ISOLATED_DATABASE, \
        "客户档案验收仅允许指定本地隔离克隆"
    assert frappe.db.db_type == "mariadb" and company and other_company and company != other_company
    for name in (company, other_company):
        assert frappe.db.exists("Company", name), "验收指定公司不存在"
    meta = frappe.get_meta(PROFILE)
    assert meta.track_changes and not meta.is_submittable
    assert all(meta.get_field(field).set_only_once for field in ("company", "archive_kind", "customer"))
    assert meta.get_field("request_id").unique and meta.get_field("binding_key").unique
    assert meta.get_field("legacy_number").read_only
    assert meta.get_field("archive_kind").options.splitlines() == list(KINDS)
    tables, counts_before = _tab_counts()
    before, native_before, ledger_before = _contents(), _protected(), _ledger_snapshot()
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    point = "jf_customer_archive_" + frappe.generate_hash(length=8)
    prefix = "JF-QA-CUST-A-" + frappe.generate_hash(length=10)
    users, objects, records, checks, roles, changed_meta = [], [], set(), [], {}, set()
    current, primary = "native_fixture_setup", None
    _begin_rollback_only(point)

    def native(action):
        return _native_without_error_log(action, current)

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((doctype, document.name))
        return document

    def actor(label, selected_roles, owner_company=company, scope=None):
        assert selected_roles and "System Manager" not in selected_roles
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid",
            first_name=prefix + label, send_welcome_email=0,
            roles=[{"role": role} for role in selected_roles])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=owner_company, apply_to_all_doctypes=1)
        if scope:
            insert("User Permission", user=user.name, allow=scope[0], for_value=scope[1], apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        roles[label] = sorted(frappe.get_roles(user.name))
        assert set(selected_roles).issubset(roles[label]) and "System Manager" not in roles[label]
        return user.name

    def payload(customer=None, kind=KINDS[0], owner_company=company, label="关联", **fields):
        result = {"kind": kind, "company": owner_company, "request_id": str(uuid4()),
            "profile": {"customer": customer or "", "display_name": " " + prefix + "-" + label + " ",
                "legacy_code": "00001234", "mobile": " 合成手机原文 ", "contact_name": "合成联系",
                "tax_number": "合成税号", "company_text": "合成公司原文", "region": "合成区域",
                "province": "合成省份", "district": "合成地区", "salesperson": "合成导购",
                "legal_person": "合成法人", "address": " 合成地址\n第二行 ", "notes": " 原文备注\n第二行 ",
                "inactive": 0, **fields}}
        if customer:
            result["customer_version"] = native(lambda: read_customer(customer, kind, owner_company))["version"]
        result["dictionary_versions"] = {field: native(lambda field=field:
            read_dictionary(result["profile"][field], field, kind, owner_company))["version"]
            for field in TYPED_LINKS[kind] if result["profile"].get(field)}
        return result

    def create(request):
        result = native(lambda: save_archive(request))
        records.add((PROFILE, result["name"]))
        return result

    def edit(view, **fields):
        return {"kind": view["kind"], "company": view["company"], "name": view["name"],
            "profile_version": view["profile_version"], "customer_version": view["customer_version"],
            "dictionary_versions": dict(view["dictionary_versions"]), "profile": fields}

    def pure(action):
        state, callbacks, context = _contents(), _callbacks(), _local_context()
        result = native(action)
        assert _contents() == state and _callbacks() == callbacks and _local_context() == context
        return result

    def protected(action):
        state = _protected()
        result = native(action)
        assert _protected() == state, "档案操作改变了共享客户、联系人、地址、客户分组或原生财务内容"
        return result

    def rejected(action, errors=frappe.PermissionError):
        state, callbacks, context = _contents(), _callbacks(), _local_context()
        try:
            native(action)
        except errors as error:
            assert not any("回滚清理失败" in note for note in getattr(error, "__notes__", []))
        else:
            raise AssertionError("客户档案接受了应拒绝的请求：" + current)
        assert _contents() == state and _callbacks() == callbacks and _local_context() == context

    def passed(label):
        checks.append(label)
        assert _ledger_snapshot() == ledger_before

    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        customers = [insert("Customer", customer_name=prefix + "-原生" + str(index), customer_type="Company")
                     for index in range(4)]
        dictionaries = {}
        for owner_company in (company, other_company):
            for dictionary_type in {value for kinds in TYPED_LINKS.values() for value in kinds.values()}:
                row = native(lambda owner_company=owner_company, dictionary_type=dictionary_type:
                    save_dictionary({"request_id": str(uuid4()), "company": owner_company,
                        "dictionary_type": dictionary_type, "numbering_mode": AUTO_MODE,
                        "value": prefix + "-词典-" + dictionary_type, "remarks": "合成词典", "inactive": 0}))
                dictionaries[(owner_company, dictionary_type)] = row
                records.add((DICTIONARY, row["name"]))
        manager = actor("manager", ["Sales Manager"])
        clerk = actor("reader", ["Sales User"])
        master = actor("master", ["Sales Manager", "Sales Master Manager"])
        pure_master = actor("pure-master", ["Sales Master Manager"])
        native_creator = actor("native-create", ["Sales Manager", "Sales User"])
        outsider = actor("outside", ["Sales Manager"], other_company)

        current = "sales_manager_explicit_link_and_profile_only_native_edit"
        frappe.set_user(manager)
        assert frappe.has_permission(PROFILE, "create") and not frappe.has_permission("Customer", "create")
        defaults = pure(lambda: init_archive(KINDS[0], company))
        assert defaults["can_link_customer"] and not defaults["can_create_customer"]
        assert defaults["number_reference_only"] and defaults["field_meta"]["legacy_number"]["read_only"]
        request = payload(customers[0].name)
        first = protected(lambda: create(request))
        assert first["profile"]["mobile"] == request["profile"]["mobile"] and first["profile"]["legacy_code"] == "00001234"
        assert first["profile"]["legacy_number"] and first["dictionary_versions"] == {}
        assert not first["number_reference_only"] and first["numbering_mode"] == AUTO_MODE
        initial = first
        first = protected(lambda: save_archive(edit(first, notes=" 只改档案\n原文 ", inactive=1)))
        assert first["profile"]["inactive"] == 1 and _versions(PROFILE, first["name"])
        rejected(lambda: save_archive(payload(label="主管无原生创建权")))
        passed("real_sales_manager_links_and_edits_profile_without_native_customer_create_or_mutation")

        current = "stale_identity_receipt_and_uuid_guards"
        rejected(lambda: save_archive(edit(initial, notes="过时覆盖")), frappe.TimestampMismatchError)
        for field in ("customer_version", "profile_version"):
            stale = edit(first, notes="过时双版本")
            stale[field] = "2000-01-01T00:00:00"
            rejected(lambda stale=stale: save_archive(stale), frappe.TimestampMismatchError)
        for fields in ({"customer": customers[1].name}, {"company": other_company}, {"archive_kind": KINDS[1]},
                       {"legacy_number": "伪造"}, {"request_id": str(uuid4())}):
            rejected(lambda fields=fields: save_archive(edit(first, **fields)), frappe.ValidationError)
        assert pure(lambda: resolve_creation(request["request_id"], KINDS[0], company))["archive"] == first
        assert pure(lambda: save_archive(request)) == first
        changed = deepcopy(request)
        changed["profile"]["notes"] = "同UUID不同内容"
        rejected(lambda: save_archive(changed), frappe.ValidationError)
        rejected(lambda: resolve_creation(request["request_id"], KINDS[1], company))
        rejected(lambda: resolve_creation("not-a-uuid", KINDS[0], company), frappe.ValidationError)
        passed("native_versions_identity_server_number_current_receipt_digest_and_cross_kind_uuid_are_guarded")

        current = "typed_company_dictionary_links_and_stale_versions"
        typed = {}
        for kind in KINDS:
            links = {field: dictionaries[(company, dictionary_type)]["name"]
                     for field, dictionary_type in TYPED_LINKS[kind].items()}
            view = protected(lambda kind=kind, links=links: create(payload(customers[1].name, kind=kind, label=kind, **links)))
            assert set(view["dictionary_versions"]) == set(links)
            for field, dictionary_type in TYPED_LINKS[kind].items():
                source = pure(lambda field=field, kind=kind: read_dictionary(links[field], field, kind, company))
                assert source["dictionary_type"] == dictionary_type and source["company"] == company
                assert source["version"] == view["dictionary_versions"][field]
            typed[kind] = view
        for field, dictionary_type in TYPED_LINKS[KINDS[0]].items():
            bad = dictionaries[(other_company, dictionary_type)]["name"]
            rejected(lambda field=field, bad=bad: save_archive(edit(typed[KINDS[0]], **{field: bad})))
        wrong = dictionaries[(company, "订单客户分类")]["name"]
        rejected(lambda: save_archive(edit(typed[KINDS[0]], category=wrong)))
        for field in ("discount", "customer_kind"):
            rejected(lambda field=field: save_archive(edit(typed[KINDS[1]], **{field: "不支持"})), frappe.ValidationError)
        stale = edit(typed[KINDS[0]], notes="词典版本过时")
        stale["dictionary_versions"]["category"] = "2000-01-01T00:00:00"
        rejected(lambda: save_archive(stale), frappe.TimestampMismatchError)
        passed("five_typed_dictionary_links_are_kind_company_and_complete_version_scoped")

        current = "inactive_dictionary_is_history_not_a_new_selection"
        category = dictionaries[(company, "客户分类")]
        stopped = native(lambda: save_dictionary({"name": category["name"], "version": category["version"],
            "company": company, "dictionary_type": "客户分类", "value": category["value"],
            "remarks": category["remarks"], "inactive": 1}))
        history = pure(lambda: read_archive(typed[KINDS[0]]["name"], KINDS[0], company))
        assert history["profile"]["category"] == category["name"]
        assert history["dictionary_versions"]["category"] == stopped["version"]
        typed[KINDS[0]] = protected(lambda: save_archive(edit(history, notes="保留停用词典历史只改备注")))
        rejected(lambda: save_archive(payload(customers[3].name, label="停用新选", category=category["name"])),
                 frappe.ValidationError)
        passed("inactive_typed_link_remains_readable_and_unchanged_on_history_edit_but_cannot_be_newly_selected")

        current = "readonly_sales_user_company_and_customer_scopes"
        denied_request = payload(customers[2].name, label="只读拒写")
        frappe.set_user(clerk)
        assert frappe.has_permission("Customer", "create") and not frappe.has_permission(PROFILE, "create")
        messages = list(getattr(frappe.local, "message_log", None) or [])
        assert not pure(lambda: init_archive(KINDS[0], company))["can_write"]
        assert list(getattr(frappe.local, "message_log", None) or []) == messages
        assert not pure(lambda: read_archive(first["name"], KINDS[0], company))["can_write"]
        rejected(lambda: save_archive(denied_request))
        rejected(lambda: save_archive(payload(label="只读岗位不能联合创建")))
        rejected(lambda: save_archive(edit(first, notes="只读拒改")))
        page = pure(lambda: query_archives(KINDS[0], company, prefix, status="全部", page=1, page_size=1))
        assert page["total"] == 2 and len(page["rows"]) == 1 and page["total_pages"] == 2
        assert len(pure(lambda: query_archives(KINDS[0], company, prefix, status="全部", page=2, page_size=1))["rows"]) == 1
        assert pure(lambda: query_archives(KINDS[0], company, prefix, status="停用"))["total"] == 1
        frappe.set_user(outsider)
        for action in (lambda: init_archive(KINDS[0], company), lambda: read_archive(first["name"], KINDS[0], company),
                       lambda: query_archives(KINDS[0], company, prefix), lambda: read_customer(customers[0].name, KINDS[0], company),
                       lambda: resolve_creation(request["request_id"], KINDS[0], company)):
            rejected(action)
        frappe.set_user("Administrator")
        scoped = actor("customer-scoped", ["Sales User"], scope=("Customer", customers[0].name))
        frappe.set_user(scoped)
        assert {row["name"] for row in pure(lambda: query_archives(KINDS[0], company, prefix, status="全部"))["rows"]} == {first["name"]}
        rejected(lambda: read_archive(typed[KINDS[0]]["name"], KINDS[0], company))
        passed("sales_user_reads_without_writes_quiet_capabilities_paging_inactive_and_native_company_customer_permissions")

        current = "native_customer_and_profile_joint_creation"
        for label, user, kind in (("master", master, KINDS[0]), ("native-create", native_creator, KINDS[1])):
            frappe.set_user(user)
            defaults = pure(lambda kind=kind: init_archive(kind, company))
            assert defaults["can_create_customer"] and frappe.has_permission("Customer", "create")
            joint_request = payload(kind=kind, label="联合-" + label)
            joint_request["customer_create"] = {key: value for key, value in defaults["customer_defaults"].items()
                if key in {"customer_group", "territory", "customer_type", "naming_series"}}
            state = _protected()
            joint = create(joint_request)
            records.add(("Customer", joint["customer"]))
            assert _protected({joint["customer"]}) == state
            customer = frappe.get_doc("Customer", joint["customer"])
            assert not customer.disabled and customer.customer_name.strip() == joint_request["profile"]["display_name"].strip()
            for field, value in joint_request["customer_create"].items():
                assert customer.get(field) == value, "联合新建改变了已核对的原生客户默认字段"
            assert not customer.tax_id and not customer.mobile_no and not customer.customer_primary_contact and not customer.customer_primary_address
            assert joint["profile"]["mobile"] == joint_request["profile"]["mobile"]
            source = _document_snapshot("Customer", joint["customer"])
            repeated = _contents()
            assert create(joint_request)["name"] == joint["name"] and _contents() == repeated
            joint = protected(lambda: save_archive(edit(joint, notes="联合新建后只改档案")))
            assert _document_snapshot("Customer", joint["customer"]) == source
        same_name = payload(kind=KINDS[1], label="重名保持原生规则")
        same_name["profile"]["display_name"] = joint_request["profile"]["display_name"]
        state = _protected()
        another = create(same_name)
        records.add(("Customer", another["customer"]))
        assert another["customer"] != joint["customer"] and _protected({another["customer"]}) == state
        linked = payload(customers[2].name, kind=KINDS[1], label="关联混新建")
        linked["customer_create"] = {}
        rejected(lambda: save_archive(linked), frappe.ValidationError)
        passed("actual_native_create_roles_create_one_customer_and_profile_preserve_native_defaults_contacts_and_atomic_retries")

        current = "native_master_role_does_not_grant_typed_dictionary_access"
        frappe.set_user(pure_master)
        assert frappe.has_permission("Customer", "create") and not frappe.has_permission(DICTIONARY, "read")
        rejected(lambda: read_dictionary(dictionaries[(company, "客户分类")]["name"], "category", KINDS[0], company))
        passed("pure_sales_master_customer_create_does_not_grant_customer_dictionary_reads")

        current = "second_profile_native_failure_rolls_back_joint_customer"
        frappe.set_user("Administrator")
        nested = "jf_customer_archive_required_" + frappe.generate_hash(length=8)
        contents, callbacks, context = _contents(), _callbacks(), _local_context()
        _begin_rollback_only(nested)
        try:
            assert not frappe.db.exists("Property Setter", PROFILE + "-notes-reqd"), "不覆盖既有档案必填设置"
            insert("Property Setter", doctype_or_field="DocField", doc_type=PROFILE, field_name="notes",
                property="reqd", value="1", property_type="Int", is_system_generated=0)
            changed_meta.add(PROFILE)
            frappe.clear_cache(doctype=PROFILE)
            frappe.set_user(native_creator)
            assert frappe.get_meta(PROFILE).get_field("notes").reqd
            bad = payload(kind=KINDS[1], label="原生第二文档失败", notes="")
            rejected(lambda: save_archive(bad), frappe.MandatoryError)
            assert pure(lambda: resolve_creation(bad["request_id"], KINDS[1], company)) == {"status": "not_found"}
        finally:
            frappe.set_user("Administrator")
            _profile_rollback(nested, callbacks)
            frappe.clear_cache(doctype=PROFILE)
            _profile_restore_caller(native_creator, True, [], set(), callbacks, context)
        assert _contents() == contents
        passed("real_native_profile_required_field_failure_restores_new_customer_children_series_versions_and_context")

        current = "native_typed_dictionary_value_mask"
        frappe.set_user("Administrator")
        nested = "jf_customer_archive_mask_" + frappe.generate_hash(length=8)
        contents, callbacks, context = _contents(), _callbacks(), _local_context()
        _begin_rollback_only(nested)
        try:
            setter_name = DICTIONARY + "-value-mask"
            assert not frappe.db.exists("Property Setter", setter_name), "不覆盖既有词典mask设置"
            insert("Property Setter", doctype_or_field="DocField", doc_type=DICTIONARY, field_name="value",
                property="mask", value="1", property_type="Int", is_system_generated=0)
            changed_meta.add(DICTIONARY)
            frappe.clear_cache(doctype=DICTIONARY)
            frappe.set_user(manager)
            dictionary_meta = frappe.get_meta(DICTIONARY)
            assert dictionary_meta.get_field("value").mask and 0 not in dictionary_meta.get_permlevel_access("mask")
            rejected(lambda: read_dictionary(dictionaries[(company, "客户分类")]["name"], "category", KINDS[0], company))
            rejected(lambda: read_archive(typed[KINDS[0]]["name"], KINDS[0], company))
            rejected(lambda: save_archive(edit(typed[KINDS[0]], notes="masked词典拒写")))
        finally:
            frappe.set_user("Administrator")
            _profile_rollback(nested, callbacks)
            frappe.clear_cache(doctype=DICTIONARY)
            _profile_restore_caller(manager, True, [], set(), callbacks, context)
        assert _contents() == contents
        passed("native_property_setter_mask_and_actual_role_refuse_typed_value_without_permission_bypass")

        current = "profile_link_mask_never_restores_hidden_dictionary_label_or_version"
        frappe.set_user("Administrator")
        nested = "jf_customer_archive_link_mask_" + frappe.generate_hash(length=8)
        contents, callbacks, context = _contents(), _callbacks(), _local_context()
        _begin_rollback_only(nested)
        try:
            assert not frappe.db.exists("Property Setter", PROFILE + "-category-mask"), "不覆盖既有分类mask设置"
            insert("Property Setter", doctype_or_field="DocField", doc_type=PROFILE, field_name="category",
                property="mask", value="1", property_type="Int", is_system_generated=0)
            changed_meta.add(PROFILE)
            frappe.clear_cache(doctype=PROFILE)
            frappe.set_user(manager)
            profile_meta = frappe.get_meta(PROFILE)
            assert profile_meta.get_field("category").mask and 0 not in profile_meta.get_permlevel_access("mask")

            def no_hidden_category(view):
                assert all("category" not in view[key] for key in
                           ("profile", "field_meta", "dictionary_values", "dictionary_versions")), \
                    "隐藏分类不得经关联词典恢复名称、编号或版本"

            masked = pure(lambda: read_archive(typed[KINDS[0]]["name"], KINDS[0], company))
            no_hidden_category(masked)
            assert set(masked["dictionary_versions"]) == {"grade", "customer_kind"}
            listing = pure(lambda: query_archives(KINDS[0], company, prefix, status="全部"))
            for view in listing["rows"]:
                no_hidden_category(view)
            rejected(lambda: read_dictionary(dictionaries[(company, "客户分类")]["name"], "category", KINDS[0], company))
            rejected(lambda: save_archive(edit(masked, notes="不能携带隐藏关联保存")))
        finally:
            frappe.set_user("Administrator")
            _profile_rollback(nested, callbacks)
            frappe.clear_cache(doctype=PROFILE)
            _profile_restore_caller(manager, True, [], set(), callbacks, context)
        assert _contents() == contents
        passed("real_profile_link_mask_omits_source_value_and_version_from_detail_and_list_and_blocks_hidden_binding_save")
    except Exception as error:
        primary = error
        error.add_note("客户档案原生验收失败门槛：" + current)
        error.add_note("已通过门槛：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:
        def collect():
            records.update((document.doctype, document.name) for document in objects if document.name)

        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks_before)

        def restore():
            for doctype in changed_meta:
                frappe.clear_cache(doctype=doctype)
                for user in {*users, original_user, "Administrator"}:
                    frappe.cache.delete_value(f"masked_fields::{doctype}::::{user}")
            _profile_restore_caller(original_user, original_test, users, records, callbacks_before, context_before)

        _profile_audit([
            ("collect_exact_fixture_records", collect),
            ("native_rollback_and_only_new_cleanup_callbacks", rollback),
            ("invalidate_owned_caches_restore_caller", restore),
            ("fixture_records_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("full_native_parent_children_metadata_auth_series_versions_restored", lambda: _contents() == before),
            ("customer_contacts_addresses_groups_and_native_financial_rows_restored", lambda: _protected() == native_before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts_before),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger_before),
            ("caller_callback_queues_and_local_context_restored", lambda: _callbacks() == callbacks_before and _local_context() == context_before),
        ], primary=primary)
    checks.append("complete_isolated_native_rollback_hashes_counts_ledgers_callbacks_and_context")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
        "actual_roles": roles, "hashes_before": before, "hashes_after": _contents(),
        "table_counts_before": counts_before, "table_counts_after": _tab_counts(tables)[1],
        "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(),
        "customer_metadata_unchanged": _customer_metadata() == native_before["customer_metadata"],
        "gui_verified": False, "concurrency": "serial isolated rollback experiment",
        "remaining": ["真实浏览器档案操作", "双连接并发", "历史导入与旧编号规则"]}
