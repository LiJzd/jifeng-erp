"""One real-role order-dictionary experiment in the designated local clone.

After an explicit targeted DocType reload, call smoke directly once. This
helper never migrates, commits, downloads, changes permissions/schema or opens
a browser. Do not use bench execute's retry fallback. Every fixture and native
Version/counter is rolled back, with complete persisted hashes and all table
counts audited. Only the isolated local clone below is accepted.
"""

import json
from pathlib import Path
from uuid import uuid4

import frappe

from jifeng_erp.catalog_validation import (
    _profile_audit,
    _profile_restore_caller,
    _profile_rollback,
    _version_has,
)
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import (
    _callbacks,
    _restore_callbacks,
    _tab_counts,
    _versions,
)
from jifeng_erp.personnel_separation_validation import (
    _local_context,
    _restore_local_context,
)
from jifeng_erp.product_dictionary_validation import (
    _document_snapshot,
    _native_contents as _base_contents,
    _sql_row_hash,
)
from jifeng_erp.order_dictionary import (
    AUTO_MODE,
    COLLISION,
    DOCTYPE,
    EDIT_FIELDS,
    IDENTITY,
    REQUEST_FIELDS,
    TYPES,
    _columns,
    _series_key,
    init_dictionary,
    list_dictionary,
    read_dictionary,
    resolve_creation,
    save_dictionary,
)


ISOLATED_SITE = "jifeng-plating-native-isolated.localhost"
ISOLATED_DATABASE = "jf_plap_isolated_20261002"


def _native_doctypes():
    """Installed Customer/Sales Order parents and every actual child type."""
    selected = {"Customer", "Sales Order"}
    pending = list(selected)
    while pending:
        for field in frappe.get_meta(pending.pop()).get_table_fields():
            assert (
                not field.is_virtual and not frappe.get_meta(field.options).is_virtual
            ), "订单词典保护快照不能遗漏虚拟子表"
            if field.options not in selected:
                selected.add(field.options)
                pending.append(field.options)
    return selected


def _native_rows():
    return {
        doctype: _sql_row_hash(
            frappe.db.sql(
                "SELECT * FROM `tab" + doctype.replace("`", "``") + "` ORDER BY name",
                as_dict=True,
            )
        )
        for doctype in sorted(_native_doctypes())
    }


def _protected_metadata():
    """Complete DocType parents/children and overrides for native business children."""
    result = {}
    for protected in sorted(_native_doctypes()):
        metadata = _document_snapshot("DocType", protected)
        metadata["overrides"] = {}
        for doctype, field in (
            ("Custom Field", "dt"),
            ("Property Setter", "doc_type"),
            ("Custom DocPerm", "parent"),
        ):
            metadata["overrides"][doctype] = _sql_row_hash(
                frappe.db.sql(
                    "SELECT * FROM `tab"
                    + doctype
                    + "` WHERE `"
                    + field
                    + "`=%s ORDER BY name",
                    (protected,),
                    as_dict=True,
                )
            )
        result[protected] = metadata
    return result


def _protected():
    return {"contents": _native_rows(), "metadata": _protected_metadata()}


def _contents():
    result = _base_contents()
    result.update(_native_rows())
    result[DOCTYPE] = _sql_row_hash(
        frappe.db.sql("SELECT * FROM `tab" + DOCTYPE + "` ORDER BY name", as_dict=True)
    )
    for doctype in ("Error Log", "Data Import Log"):
        result[doctype] = _sql_row_hash(
            frappe.db.sql(
                "SELECT * FROM `tab" + doctype + "` ORDER BY name", as_dict=True
            )
        )
    return result


def smoke(company, other_company):
    """Real sales/system managers, Sales User and scoped receipt/paging checks."""
    frappe.only_for("System Manager")
    assert (
        frappe.local.site == ISOLATED_SITE and frappe.conf.db_name == ISOLATED_DATABASE
    ), "订单类型验收仅允许指定本地隔离克隆"
    assert (
        frappe.db.db_type == "mariadb"
        and company
        and other_company
        and company != other_company
    )
    for name in (company, other_company):
        assert frappe.db.exists("Company", name), "验收指定公司不存在"
    meta = frappe.get_meta(DOCTYPE)
    assert meta.track_changes and not meta.is_submittable
    assert all(meta.get_field(field).set_only_once for field in IDENTITY)
    assert {field.fieldname for field in meta.fields if field.unique} == {"request_id"}
    assert (
        meta.get_field("legacy_number").read_only
        and meta.get_field("abbreviation").read_only
    )
    assert meta.get_field("dictionary_type").options.splitlines() == list(TYPES)
    assert meta.get_field("dictionary_type").default == "订单类型"
    assert TYPES == {"订单类型": "type", "订单要求": "requirement"}
    assert all(
        len(_series_key(scope, kind)) < 94
        for scope in (company, other_company, "集" * 140)
        for kind in TYPES
    )
    # Verify installed native fields against their source without changing them.
    source = json.loads(
        Path(
            frappe.get_app_path(
                "erpnext", "selling", "doctype", "customer", "customer.json"
            )
        ).read_text(encoding="utf-8")
    )
    source_type = next(
        field for field in source["fields"] if field["fieldname"] == "customer_type"
    )
    native_type = frappe.get_meta("Customer").get_field("customer_type")
    assert (
        native_type.fieldtype == source_type["fieldtype"] == "Select"
        and native_type.options == source_type["options"]
    )
    order_source = json.loads(
        Path(
            frappe.get_app_path(
                "erpnext", "selling", "doctype", "sales_order", "sales_order.json"
            )
        ).read_text(encoding="utf-8")
    )
    order_field = next(
        field for field in order_source["fields"] if field["fieldname"] == "order_type"
    )
    native_order = frappe.get_meta("Sales Order").get_field("order_type")
    assert native_order.fieldtype == order_field[
        "fieldtype"
    ] and native_order.options == order_field.get("options")
    for field in (*REQUEST_FIELDS, "numbering_mode"):
        assert all(
            meta.get_field(field).get(attr)
            for attr in ("hidden", "read_only", "no_copy", "print_hide")
        )
    tables, counts_before = _tab_counts()
    before, ledger_before, native_meta_before = (
        _contents(),
        _ledger_snapshot(),
        _protected_metadata(),
    )
    protected_before = _protected()
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    prefix = "JF-QA-ORD-D-" + frappe.generate_hash(length=10)
    point = "jf_order_dictionary_" + frappe.generate_hash(length=8)
    users, objects, records, checks, actual_roles = [], [], set(), [], {}
    evidence, current, primary = {}, "fixture_setup", None
    _begin_rollback_only(point)

    def native(action):
        prior = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = prior

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((document.doctype, document.name))
        return document

    def actor(label, roles, companies, dictionary_name=None):
        user = insert(
            "User",
            email=prefix.lower() + "-" + label + "@example.invalid",
            first_name=prefix + "-" + label,
            send_welcome_email=0,
            roles=[{"role": role} for role in roles],
        )
        users.append(user.name)
        for allowed in companies:
            insert(
                "User Permission",
                user=user.name,
                allow="Company",
                for_value=allowed,
                apply_to_all_doctypes=1,
            )
        if dictionary_name:
            insert(
                "User Permission",
                user=user.name,
                allow=DOCTYPE,
                for_value=dictionary_name,
                apply_to_all_doctypes=1,
            )
        frappe.clear_cache(user=user.name)
        actual_roles[label] = sorted(frappe.get_roles(user.name))
        return user.name

    def payload(**changes):
        return {
            "request_id": str(uuid4()),
            "company": company,
            "dictionary_type": "订单类型",
            "numbering_mode": AUTO_MODE,
            "value": " " + prefix + "-名称 ",
            "remarks": " 原文备注\n第二行 " + prefix,
            "inactive": 0,
            **changes,
        }

    def save(values):
        archive = native(lambda: save_dictionary(values))
        records.add((DOCTYPE, archive["name"]))
        return archive

    def update(archive, **changes):
        return save(
            {
                field: archive[field]
                for field in ("name", "version", *IDENTITY, *EDIT_FIELDS)
                if field in archive
            }
            | changes
        )

    def rejected(action, errors=frappe.ValidationError, *, text=None, atomic=False):
        nested = "jf_order_dictionary_reject_" + frappe.generate_hash(length=8)
        contents, callbacks, context = _contents(), _callbacks(), _local_context()
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                if text:
                    assert text in str(error), "原生拒绝原因与要求不符"
                if atomic:
                    assert _contents() == contents, "API拒绝未完整回滚"
                    assert _callbacks() == callbacks and _local_context() == context, (
                        "API拒绝改变既有事务上下文"
                    )
            else:
                raise AssertionError("订单类型接受本应拒绝的操作")
        finally:
            frappe.db.rollback(save_point=nested)
            _restore_callbacks(callbacks)
            _restore_local_context(context)

    def passed():
        checks.append(current)
        assert _ledger_snapshot() == ledger_before
        assert _protected() == protected_before, (
            "订单词典操作改变了原生 Customer/Sales Order 父子内容或元数据"
        )

    try:
        frappe.set_user("Administrator")
        managers = {
            "sales_manager": actor("sales-manager", ["Sales Manager"], [company]),
            "system_manager": actor("system-manager", ["System Manager"], [company]),
        }
        clerk = actor("sales-user", ["Sales User"], [company])
        outsider = actor("outside", ["Sales Manager"], [other_company])
        assert "System Manager" not in actual_roles["sales-manager"]
        assert (
            "Sales Manager" not in actual_roles["sales-user"]
            and "System Manager" not in actual_roles["sales-user"]
        )

        current = "actual_sales_and_system_manager_create_edit_native_versions"
        archives, originals = {}, {}
        for label, user in managers.items():
            frappe.set_user(user)
            assert {*IDENTITY, *EDIT_FIELDS}.issubset(
                _columns("read") & _columns("write")
            )
            empty = native(lambda: init_dictionary("订单类型"))
            assert (
                empty["company"] is None
                and empty["can_write"]
                and empty["number_preview"] is None
            )
            originals[label] = payload(value=" " + prefix + "-" + label + " ")
            archive = save(originals[label])
            assert (
                archive["value"] == originals[label]["value"]
                and archive["remarks"] == originals[label]["remarks"]
            )
            assert (
                archive["abbreviation"] in (None, "")
                and archive["field_meta"]["abbreviation"]["read_only"]
            )
            changed = update(archive, remarks=" 原生修改\n" + prefix)
            assert _version_has(frappe.get_doc(DOCTYPE, changed["name"]), "remarks")
            archives[label] = changed
        passed()

        current = "advisory_preview_server_number_company_scoped_counter"
        frappe.set_user(managers["sales_manager"])
        counters = _sql_row_hash(
            frappe.db.sql("SELECT * FROM `tabSeries` ORDER BY name", as_dict=True)
        )
        first_preview = native(lambda: init_dictionary("订单类型", company))
        assert (
            first_preview["field_meta"]["legacy_number"]["read_only"]
            and first_preview["number_preview_is_provisional"]
        )
        assert (
            _sql_row_hash(
                frappe.db.sql("SELECT * FROM `tabSeries` ORDER BY name", as_dict=True)
            )
            == counters
        )
        numbered = save(payload(value=prefix + "-numbered"))
        assert numbered["legacy_number"] == first_preview["number_preview"]
        assert not numbered["number_preview_is_provisional"]
        frappe.set_user("Administrator")
        other_preview = native(lambda: init_dictionary("订单类型", other_company))
        foreign = save(payload(company=other_company, value=prefix + "-foreign"))
        assert foreign["legacy_number"] == other_preview["number_preview"]
        assert _series_key(company, "订单类型") != _series_key(
            other_company, "订单类型"
        )
        passed()

        current = "native_form_receipt_allocation_without_client_metadata"
        direct = insert(
            DOCTYPE,
            company=company,
            dictionary_type="订单类型",
            value=prefix + "-direct",
            remarks="",
            inactive=0,
        )
        assert (
            direct.request_id
            and direct.request_digest
            and direct.numbering_mode == AUTO_MODE
            and direct.legacy_number
        )
        for field, value in (
            ("request_id", str(uuid4())),
            ("request_digest", "f" * 64),
            ("numbering_mode", AUTO_MODE),
            ("legacy_number", "001"),
            ("abbreviation", "JP"),
        ):
            rejected(
                lambda field=field, value=value: insert(
                    DOCTYPE,
                    company=company,
                    dictionary_type="订单类型",
                    value=prefix + "-forged",
                    inactive=0,
                    **{field: value},
                )
            )
        passed()

        current = (
            "readonly_receipt_after_later_rename_and_identical_retry_preserve_state"
        )
        frappe.set_user(managers["sales_manager"])
        previous = archives["sales_manager"]
        renamed = update(previous, value=" " + prefix + "-改名 ", remarks="")
        contents, version_count = _contents(), len(_versions(DOCTYPE, renamed["name"]))
        assert save(originals["sales_manager"]) == renamed
        assert (
            native(
                lambda: resolve_creation(
                    originals["sales_manager"]["request_id"], "订单类型", company
                )
            )["archive"]
            == renamed
        )
        assert native(lambda: resolve_creation(str(uuid4()), "订单类型", company)) == {
            "status": "not_found"
        }
        assert (
            _contents() == contents
            and len(_versions(DOCTYPE, renamed["name"])) == version_count
        )
        rejected(
            lambda: save_dictionary(
                originals["sales_manager"] | {"value": "不同初始名称"}
            ),
            text=COLLISION,
            atomic=True,
        )
        archives["sales_manager"] = renamed
        passed()

        current = "locked_stale_update_rejected_without_new_version"
        rejected(
            lambda: update(previous, value="过时覆盖"),
            frappe.TimestampMismatchError,
            text="重读核对",
            atomic=True,
        )
        assert (
            native(lambda: read_dictionary(renamed["name"]))["value"]
            == renamed["value"]
        )
        passed()

        current = "native_and_api_immutable_identity_number_abbreviation"
        frappe.set_user("Administrator")
        for field, value in (
            ("company", other_company),
            ("request_id", str(uuid4())),
            ("request_digest", "f" * 64),
            ("legacy_number", "X"),
            ("abbreviation", "JP"),
        ):
            document = frappe.get_doc(DOCTYPE, renamed["name"])
            document.set(field, value)
            rejected(document.save, text="不能修改")
        for changes in (
            {"legacy_number": renamed["legacy_number"]},
            {"abbreviation": ""},
            {"request_digest": "forged"},
        ):
            rejected(
                lambda changes=changes: update(renamed, **changes),
                text="不支持的参数",
                atomic=True,
            )
        rejected(
            lambda: update(renamed, company=other_company), text="不能修改", atomic=True
        )
        passed()

        current = "actual_sales_user_reads_without_create_write_delete_export"
        frappe.set_user(clerk)
        assert frappe.has_permission(DOCTYPE, "read") and frappe.has_permission(
            DOCTYPE, "select"
        )
        assert all(
            not frappe.has_permission(DOCTYPE, action)
            for action in ("create", "write", "delete", "export")
        )
        assert not native(lambda: init_dictionary("订单类型", company))["can_write"]
        assert not native(lambda: read_dictionary(renamed["name"]))["can_write"]
        assert native(lambda: list_dictionary("订单类型", company, prefix))["rows"]
        rejected(
            lambda: save_dictionary(payload()), frappe.PermissionError, atomic=True
        )
        rejected(
            lambda: update(renamed, value="clerk edit"),
            frappe.PermissionError,
            atomic=True,
        )
        assert (
            native(
                lambda: resolve_creation(
                    originals["sales_manager"]["request_id"], "订单类型", company
                )
            )["status"]
            == "found"
        )
        passed()

        current = "actual_company_user_permission_guards_all_endpoints"
        frappe.set_user(managers["sales_manager"])
        rejected(
            lambda: list_dictionary("订单类型", other_company), frappe.PermissionError
        )
        rejected(lambda: read_dictionary(foreign["name"]), frappe.PermissionError)
        rejected(
            lambda: resolve_creation(str(uuid4()), "订单类型", other_company),
            frappe.PermissionError,
        )
        rejected(
            lambda: save_dictionary(payload(company=other_company)),
            frappe.PermissionError,
            atomic=True,
        )
        frappe.set_user(outsider)
        rejected(lambda: list_dictionary("订单类型", company), frappe.PermissionError)
        passed()

        current = "dictionary_user_permission_hides_rows_and_receipt"
        frappe.set_user("Administrator")
        scoped = actor("dictionary-scoped", ["Sales User"], [company], renamed["name"])
        hidden_token = frappe.get_doc(DOCTYPE, numbered["name"]).request_id
        frappe.set_user(scoped)
        listed = native(
            lambda: list_dictionary("订单类型", company, prefix, include_inactive=True)
        )
        assert {row["name"] for row in listed["rows"]} == {renamed["name"]}
        assert native(lambda: resolve_creation(hidden_token, "订单类型", company)) == {
            "status": "not_found"
        }
        rejected(lambda: read_dictionary(numbered["name"]), frappe.PermissionError)
        passed()

        current = "complete_native_103_rows_paging_and_empty_filter_clamping"
        frappe.set_user(managers["sales_manager"])
        paging_prefix = prefix + "-PAGE-"
        fixtures = [
            save(payload(value=paging_prefix + str(index).zfill(3)))
            for index in range(103)
        ]
        first = native(
            lambda: list_dictionary(
                "订单类型", company, paging_prefix, start=0, page_length=100
            )
        )
        last = native(
            lambda: list_dictionary(
                "订单类型", company, paging_prefix, start=100, page_length=100
            )
        )
        beyond = native(
            lambda: list_dictionary(
                "订单类型", company, paging_prefix, start=99999, page_length=100
            )
        )
        empty = native(
            lambda: list_dictionary(
                "订单类型", company, prefix + "-MISSING", start=100, page_length=25
            )
        )
        assert (
            first["total"] == last["total"] == 103
            and len(first["rows"]) == 100
            and len(last["rows"]) == 3
        )
        assert (
            first["rows"][0]["name"] == fixtures[0]["name"]
            and last["rows"][-1]["name"] == fixtures[-1]["name"]
        )
        assert (
            beyond["start"] == 100
            and beyond["rows"] == last["rows"]
            and empty["start"] == 0
            and empty["total"] == 0
        )
        assert (
            first["company"] == company
            and first["dictionary_type"] == "订单类型"
            and first["search"] == paging_prefix
            and first["include_inactive"] == 0
        )
        evidence["paging"] = {
            "total": 103,
            "first": 100,
            "last": 3,
            "native_batches": "complete twice-checked serial projections",
        }
        passed()

        current = "inactive_filter_and_native_version_history"
        stopped = update(fixtures[0], inactive=1)
        assert _version_has(frappe.get_doc(DOCTYPE, stopped["name"]), "inactive")
        assert (
            native(lambda: list_dictionary("订单类型", company, paging_prefix))["total"]
            == 102
        )
        assert (
            native(
                lambda: list_dictionary(
                    "订单类型", company, paging_prefix, include_inactive=True
                )
            )["total"]
            == 103
        )
        assert (
            native(lambda: read_dictionary(stopped["name"]))["value"]
            == fixtures[0]["value"]
        )
        passed()

        current = "all_new_order_dictionary_types_sales_manager_lifecycle_and_versions"
        frappe.set_user(managers["sales_manager"])
        new_types = tuple(
            dictionary_type
            for dictionary_type in TYPES
            if dictionary_type != "订单类型"
        )
        for dictionary_type in new_types:
            original = save(
                payload(
                    dictionary_type=dictionary_type,
                    value=prefix + "-EXT-LIFECYCLE",
                    remarks=" 原始订单词典备注 " + prefix,
                )
            )
            assert (
                original["dictionary_type"] == dictionary_type
                and original["inactive"] == 0
            )
            assert original["abbreviation"] in (None, "")
            version_count = len(_versions(DOCTYPE, original["name"]))
            changed = update(original, remarks=" 修改订单词典备注 " + prefix)
            assert _version_has(frappe.get_doc(DOCTYPE, changed["name"]), "remarks")
            rejected(
                lambda original=original: update(original, remarks="过时备注覆盖"),
                frappe.TimestampMismatchError,
                text="重读核对",
                atomic=True,
            )
            inactive = update(changed, inactive=1)
            assert _version_has(frappe.get_doc(DOCTYPE, inactive["name"]), "inactive")
            assert len(_versions(DOCTYPE, inactive["name"])) == version_count + 2
            assert (
                native(
                    lambda inactive=inactive, dictionary_type=dictionary_type: (
                        read_dictionary(inactive["name"], dictionary_type)
                    )
                )
                == inactive
            )
            assert (
                native(
                    lambda dictionary_type=dictionary_type: list_dictionary(
                        dictionary_type, company, prefix + "-EXT-LIFECYCLE"
                    )
                )["total"]
                == 0
            )
            listed = native(
                lambda dictionary_type=dictionary_type: list_dictionary(
                    dictionary_type,
                    company,
                    prefix + "-EXT-LIFECYCLE",
                    include_inactive=True,
                )
            )
            assert {row["name"] for row in listed["rows"]} == {inactive["name"]}
        passed()

        current = "two_types_company_counters_and_identical_names_are_independent"
        series_keys = {
            (scope, dictionary_type): _series_key(scope, dictionary_type)
            for scope in (company, other_company)
            for dictionary_type in TYPES
        }
        assert len(set(series_keys.values())) == 4

        def current_counters():
            return {
                key: int(
                    frappe.db.get_value("Series", series, "current", order_by="name")
                    or 0
                )
                for key, series in series_keys.items()
            }

        matrix, matrix_payloads = {}, {}
        matrix_value = prefix + "-TYPE-MATRIX"
        for scope, user in (
            (company, managers["sales_manager"]),
            (other_company, outsider),
        ):
            frappe.set_user(user)
            for dictionary_type in TYPES:
                before_counters = current_counters()
                preview = native(
                    lambda dictionary_type=dictionary_type, scope=scope: (
                        init_dictionary(dictionary_type, scope)
                    )
                )
                assert (
                    preview["number_preview_is_provisional"]
                    and current_counters() == before_counters
                )
                values = payload(
                    company=scope,
                    dictionary_type=dictionary_type,
                    value=matrix_value,
                    remarks=" 同名词条独立备注 " + prefix,
                )
                archive = save(values)
                assert archive["legacy_number"] == preview["number_preview"]
                expected_counters = dict(before_counters)
                expected_counters[(scope, dictionary_type)] += 1
                assert current_counters() == expected_counters
                listed = native(
                    lambda dictionary_type=dictionary_type, scope=scope: (
                        list_dictionary(dictionary_type, scope, matrix_value)
                    )
                )
                assert listed["total"] == 1 and {
                    row["name"] for row in listed["rows"]
                } == {archive["name"]}
                assert all(
                    row["company"] == scope
                    and row["dictionary_type"] == dictionary_type
                    for row in listed["rows"]
                )
                (
                    matrix[(scope, dictionary_type)],
                    matrix_payloads[(scope, dictionary_type)],
                ) = archive, values
        assert len({archive["name"] for archive in matrix.values()}) == 4
        evidence["order_dictionary_types"] = {
            "types": list(TYPES),
            "counter_scopes": len(series_keys),
            "separate_same_name_records": len(matrix),
        }
        passed()

        current = (
            "explicit_type_identity_and_cross_type_receipts_never_reuse_or_allocate"
        )
        frappe.set_user(managers["sales_manager"])
        source_values = matrix_payloads[(company, "订单要求")]
        source_archive = matrix[(company, "订单要求")]
        contents = _contents()
        assert save(source_values) == source_archive and _contents() == contents
        for dictionary_type in TYPES:
            if dictionary_type != "订单要求":
                assert native(
                    lambda dictionary_type=dictionary_type: resolve_creation(
                        source_values["request_id"], dictionary_type, company
                    )
                ) == {"status": "not_found"}
                rejected(
                    lambda dictionary_type=dictionary_type: save_dictionary(
                        source_values | {"dictionary_type": dictionary_type}
                    ),
                    text=COLLISION,
                    atomic=True,
                )
        assert _contents() == contents
        assert (
            native(
                lambda: resolve_creation(
                    source_values["request_id"], "订单要求", company
                )
            )["archive"]
            == source_archive
        )
        for dictionary_type in new_types:
            archive = matrix[(company, dictionary_type)]
            rejected(
                lambda archive=archive: read_dictionary(archive["name"], "订单类型"),
                text="不一致",
            )
            rejected(
                lambda archive=archive: update(archive, dictionary_type="订单类型"),
                text="不能修改",
                atomic=True,
            )
            document = frappe.get_doc(DOCTYPE, archive["name"])
            document.dictionary_type = "订单类型"
            rejected(document.save, text="不能修改")
        passed()

        current = "new_types_real_sales_user_quiet_readonly_and_company_restrictions"
        frappe.set_user(clerk)
        for dictionary_type in new_types:
            archive = matrix[(company, dictionary_type)]
            messages = list(getattr(frappe.local, "message_log", None) or [])
            empty = native(
                lambda dictionary_type=dictionary_type: init_dictionary(dictionary_type)
            )
            scoped_empty = native(
                lambda dictionary_type=dictionary_type: init_dictionary(
                    dictionary_type, company
                )
            )
            read = native(
                lambda archive=archive, dictionary_type=dictionary_type: (
                    read_dictionary(archive["name"], dictionary_type)
                )
            )
            listed = native(
                lambda dictionary_type=dictionary_type: list_dictionary(
                    dictionary_type, company, matrix_value
                )
            )
            assert (
                not empty["can_write"]
                and not scoped_empty["can_write"]
                and not read["can_write"]
            )
            assert (
                listed["total"] == 1
                and list(getattr(frappe.local, "message_log", None) or []) == messages
            )
            rejected(
                lambda dictionary_type=dictionary_type: save_dictionary(
                    payload(dictionary_type=dictionary_type)
                ),
                frappe.PermissionError,
                atomic=True,
            )
            rejected(
                lambda archive=archive: update(archive, remarks="只读员工修改"),
                frappe.PermissionError,
                atomic=True,
            )
        frappe.set_user(managers["sales_manager"])
        for dictionary_type in new_types:
            foreign_archive, foreign_values = (
                matrix[(other_company, dictionary_type)],
                matrix_payloads[(other_company, dictionary_type)],
            )
            for action in (
                lambda dictionary_type=dictionary_type: init_dictionary(
                    dictionary_type, other_company
                ),
                lambda dictionary_type=dictionary_type: list_dictionary(
                    dictionary_type, other_company
                ),
                lambda dictionary_type=dictionary_type, foreign_archive=foreign_archive: (
                    read_dictionary(foreign_archive["name"], dictionary_type)
                ),
                lambda dictionary_type=dictionary_type, foreign_values=foreign_values: (
                    resolve_creation(
                        foreign_values["request_id"], dictionary_type, other_company
                    )
                ),
            ):
                rejected(action, frappe.PermissionError)
            rejected(
                lambda dictionary_type=dictionary_type: save_dictionary(
                    payload(company=other_company, dictionary_type=dictionary_type)
                ),
                frappe.PermissionError,
                atomic=True,
            )
        assert _protected_metadata() == native_meta_before
        passed()

        current = "invalid_names_flags_types_uuid_parameters_rejected_atomically"
        for changes in (
            {"value": " "},
            {"value": "x" * 141},
            {"remarks": 1},
            {"inactive": 1.0},
            {"inactive": "yes"},
            {"dictionary_type": "Company"},
            {"request_id": "not-uuid"},
            {"version": "old"},
        ):
            rejected(
                lambda changes=changes: save_dictionary(payload(**changes)), atomic=True
            )
        for start in (-1, True, 1.5, "-1"):
            rejected(
                lambda start=start: list_dictionary("订单类型", company, start=start)
            )
        passed()

        current = "deletion_blocked_for_administrator_and_guest_denied"
        frappe.set_user("Administrator")
        rejected(lambda: frappe.delete_doc(DOCTYPE, direct.name), text="删除尚未实现")
        frappe.set_user("Guest")
        for action in (
            lambda: init_dictionary("订单类型"),
            lambda: list_dictionary("订单类型", company),
            lambda: read_dictionary(renamed["name"]),
            lambda: resolve_creation(str(uuid4()), "订单类型", company),
            lambda: save_dictionary(payload()),
        ):
            rejected(action, frappe.PermissionError)
        passed()
    except Exception as error:
        primary = error
        error.add_note("订单类型原生验收失败门槛：" + current)
        error.add_note("已通过门槛：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:

        def collect():
            records.update(
                (document.doctype, document.name)
                for document in objects
                if document.name
            )

        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks_before)

        def restore():
            _profile_restore_caller(
                original_user,
                original_test,
                users,
                records,
                callbacks_before,
                context_before,
            )

        _profile_audit(
            [
                ("collect_exact_new_fixtures", collect),
                ("native_rollback_and_new_cleanup_callbacks", rollback),
                ("invalidate_caches_restore_original_caller_context", restore),
                (
                    "all_new_fixtures_absent",
                    lambda: all(
                        not frappe.db.exists(doctype, name) for doctype, name in records
                    ),
                ),
                (
                    "whole_customer_sales_order_and_other_native_contents_unchanged",
                    lambda: _contents() == before,
                ),
                (
                    "complete_native_customer_sales_order_parent_and_child_metadata_unchanged",
                    lambda: _protected_metadata() == native_meta_before,
                ),
                (
                    "native_customer_sales_order_parent_child_contents_and_metadata_unchanged",
                    lambda: _protected() == protected_before,
                ),
                (
                    "all_table_counts_unchanged",
                    lambda: _tab_counts(tables)[1] == counts_before,
                ),
                ("whole_gl_sle_unchanged", lambda: _ledger_snapshot() == ledger_before),
                (
                    "original_callbacks_and_local_context_restored",
                    lambda: (
                        _callbacks() == callbacks_before
                        and _local_context() == context_before
                    ),
                ),
            ],
            primary=primary,
        )
    checks.append("complete_rollback_hash_counts_ledgers_callbacks_and_context")
    return {
        "passed": True,
        "gate_count": len(checks),
        "checks": checks,
        "business_data": "rolled_back",
        "actual_roles": actual_roles,
        "evidence": evidence,
        "hashes_before": before,
        "hashes_after": _contents(),
        "native_customer_sales_order_unchanged": _protected() == protected_before,
        "table_counts_before": counts_before,
        "table_counts_after": _tab_counts(tables)[1],
        "whole_ledger_before": ledger_before,
        "whole_ledger_after": _ledger_snapshot(),
        "customer_sales_order_metadata_before": native_meta_before,
        "customer_sales_order_metadata_after": _protected_metadata(),
        "gui_verified": False,
        "concurrency": "serial rollback experiment; no concurrent/load test",
        "remaining": [
            "旧编号算法",
            "更新简拼",
            "旧删除操作",
            "旧打印及预览",
            "前端操作验收",
        ],
    }
