"""One real-role, rollback-only plating-color experiment in the local clone.

Call smoke directly once after the independent targeted DocType reload. This
module never connects, migrates, commits, transfers files or retries a failed
experiment. Temporary users, permissions and tightening Property Setters use
native controllers and existing roles. Only opaque hashes leave the complete
persisted-row audits; the external runner supplies its independent snapshot.
The permanent creation/deletion lifecycle requires separate native QA;
updating this caller does not mean that this legacy experiment has been rerun.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import math
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
from jifeng_erp.order_dictionary_validation import _contents as _order_contents
from jifeng_erp.personnel_changes_validation import (
    _callbacks,
    _restore_callbacks,
    _versions,
)
from jifeng_erp.personnel_separation_validation import (
    _local_context,
    _restore_local_context,
)
from jifeng_erp.plating_color import (
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
from jifeng_erp.product_dictionary_validation import _document_snapshot, _sql_row_hash


ISOLATED_SITE = "jifeng-plating-native-isolated.localhost"
ISOLATED_DATABASE = "jf_plap_isolated_20261002"
VALIDATION_COMPANIES = {"Jifeng ERP UI Validation", "Jifeng ERP Validation"}
KIND = "镀色"


def _native_doctypes():
    selected = {"Customer", "Sales Order", "Item"}
    pending = list(selected)
    while pending:
        for field in frappe.get_meta(pending.pop()).get_table_fields():
            assert (
                not field.is_virtual and not frappe.get_meta(field.options).is_virtual
            ), "镀色保护快照不能遗漏原生虚拟子表"
            if field.options not in selected:
                selected.add(field.options)
                pending.append(field.options)
    return selected


def _rows(doctype):
    return _sql_row_hash(
        frappe.db.sql(
            "SELECT * FROM `tab" + doctype.replace("`", "``") + "` ORDER BY name",
            as_dict=True,
        )
    )


def _protected():
    result = {}
    for doctype in sorted(_native_doctypes()):
        metadata = _document_snapshot("DocType", doctype)
        overrides = {}
        for override, field in (
            ("Custom Field", "dt"),
            ("Property Setter", "doc_type"),
            ("Custom DocPerm", "parent"),
        ):
            overrides[override] = _sql_row_hash(
                frappe.db.sql(
                    "SELECT * FROM `tab"
                    + override
                    + "` WHERE `"
                    + field
                    + "`=%s ORDER BY name",
                    (doctype,),
                    as_dict=True,
                )
            )
        result[doctype] = {
            "contents": _rows(doctype),
            "metadata": metadata,
            "overrides": overrides,
        }
    return result


def _contents():
    result = _order_contents()
    for doctype in (*sorted(_native_doctypes()), DOCTYPE, "Property Setter"):
        result[doctype] = _rows(doctype)
    return result


def _wire(value):
    """Lossless native SQL scalars for private full-table equality checks."""
    if value is None or type(value) in (bool, int, str):
        return value
    if isinstance(value, (datetime, date, time)):
        return {type(value).__name__: value.isoformat()}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, timedelta):
        return {"timedelta": [value.days, value.seconds, value.microseconds]}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"bytes": bytes(value).hex()}
    if isinstance(value, float) and math.isfinite(value):
        return {"float": value.hex()}
    raise TypeError("不支持的原生快照类型：" + type(value).__name__)


def _physical_snapshot():
    """Every physical object, including globals/Auth and sequence state.

    Row hashes are sorted as a multiset, retaining duplicate rows and all
    column values without assuming every global table has a name column.
    This serial local oracle does not claim a concurrent consistent snapshot.
    """
    inventory = frappe.db.sql(
        "SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME",
        as_dict=True,
    )
    result = {}
    for item in inventory:
        name = item["TABLE_NAME"]
        assert item["TABLE_TYPE"] in ("BASE TABLE", "SEQUENCE"), (
            "镀色全物理审计不接受遗漏的数据库对象"
        )
        rows = frappe.db.sql(
            "SELECT * FROM `" + name.replace("`", "``") + "`", as_dict=True
        )
        encoded = sorted(
            json.dumps(
                {field: _wire(value) for field, value in row.items()},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for row in rows
        )
        result[name] = {
            "engine": item["ENGINE"],
            "table_type": item["TABLE_TYPE"],
            "rows": len(rows),
            "sha256": hashlib.sha256(
                json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    assert result and "__Auth" in result and "__global_search" in result
    assert "tab" + DOCTYPE in result
    return result


def _native_sources():
    """Installed source fingerprints and selected unchanged native identities."""
    result = {}
    for doctype, parts, fieldname in (
        (
            "Customer",
            ("selling", "doctype", "customer", "customer.json"),
            "customer_type",
        ),
        (
            "Sales Order",
            ("selling", "doctype", "sales_order", "sales_order.json"),
            "order_type",
        ),
        ("Item", ("stock", "doctype", "item", "item.json"), "item_code"),
    ):
        content = Path(frappe.get_app_path("erpnext", *parts)).read_bytes()
        source = json.loads(content)
        field = next(row for row in source["fields"] if row["fieldname"] == fieldname)
        installed = frappe.get_meta(doctype).get_field(fieldname)
        assert installed.fieldtype == field["fieldtype"]
        assert (installed.options or "") == (field.get("options") or "")
        result[doctype] = hashlib.sha256(content).hexdigest()
    return result


def smoke(company, other_company):
    """Actual managers, pure Sales User, native masks and complete rollback."""
    assert (
        frappe.local.site == ISOLATED_SITE and frappe.conf.db_name == ISOLATED_DATABASE
    ), "镀色验收仅允许指定本地隔离克隆"
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb"
    assert {company, other_company} == VALIDATION_COMPANIES and company != other_company
    for name in (company, other_company):
        assert frappe.db.exists("Company", name), "不创建验收公司，只使用已存在的公司"
    assert DOCTYPE == "Jifeng Plating Color" and TYPES == {KIND: "color"}
    meta = frappe.get_meta(DOCTYPE)
    assert meta.track_changes and not meta.is_submittable
    assert all(meta.get_field(field).set_only_once for field in IDENTITY)
    fixed = meta.get_field("dictionary_type")
    assert (
        fixed.fieldtype == "Select"
        and fixed.options == fixed.default == KIND
        and fixed.read_only
    )
    assert {field.fieldname for field in meta.fields if field.unique} == {"request_id"}
    for field in ("legacy_number", "abbreviation"):
        assert meta.get_field(field).read_only
    for field in ("code", "color_system"):
        df = meta.get_field(field)
        assert df.fieldtype == "Data" and not df.reqd and not df.fetch_from
    assert meta.get_field("value").reqd and not meta.get_field("value").set_only_once
    for field in (*REQUEST_FIELDS, "numbering_mode"):
        assert all(
            meta.get_field(field).get(attr)
            for attr in ("hidden", "read_only", "no_copy", "print_hide")
        )
    assert all(
        len(_series_key(scope, KIND)) == 81
        for scope in (company, other_company, "集" * 140)
    )
    physical_before = _physical_snapshot()
    assert physical_before["tab" + DOCTYPE]["rows"] == 0, "首次隔离验收要求镀色新表为空"
    contents_before, protected_before, ledger_before, sources_before = (
        _contents(),
        _protected(),
        _ledger_snapshot(),
        _native_sources(),
    )
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    prefix = "JF-QA-PL-C-" + frappe.generate_hash(length=10)
    point = "jf_plating_color_" + frappe.generate_hash(length=8)
    users, objects, records, checks, actual_roles = [], [], set(), [], {}
    evidence, current, primary, changed_meta = {}, "fixture_setup", None, False
    physical_after = None
    _begin_rollback_only(point)

    def native(action):
        prior = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = prior

    def insert(doctype, **values):
        assert doctype in {"User", "User Permission", "Property Setter", DOCTYPE}
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((document.doctype, document.name))
        return document

    def actor(label, role, scope, dictionary_name=None):
        user = insert(
            "User",
            email=prefix.lower() + "-" + label + "@example.invalid",
            first_name=prefix + "-" + label,
            send_welcome_email=0,
            roles=[{"role": role}],
        )
        users.append(user.name)
        insert(
            "User Permission",
            user=user.name,
            allow="Company",
            for_value=scope,
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
        assert role in actual_roles[label]
        if role != "System Manager":
            assert "System Manager" not in actual_roles[label]
        if role == "Sales User":
            assert "Sales Manager" not in actual_roles[label]
        return user.name

    def payload(**changes):
        return {
            "request_id": str(uuid4()),
            "company": company,
            "dictionary_type": KIND,
            "numbering_mode": AUTO_MODE,
            "code": " 0007 / 原代码 " + prefix,
            "value": " " + prefix + "-名称 ",
            "color_system": " 原色系 / 蓝银 " + prefix,
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
        nested = "jf_plating_reject_" + frappe.generate_hash(length=8)
        before, callbacks, context = _contents(), _callbacks(), _local_context()
        failure = None
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                if text:
                    assert text in str(error), "原生拒绝原因与要求不符"
                if atomic:
                    assert _contents() == before, "API拒绝未完整回滚记录及计数器"
                    assert _callbacks() == callbacks and _local_context() == context
            else:
                raise AssertionError("镀色接受了本应拒绝的操作")
        except Exception as error:
            failure = error
            raise
        finally:
            _profile_audit(
                [
                    (
                        "reject_savepoint_rollback",
                        lambda: frappe.db.rollback(save_point=nested),
                    ),
                    ("reject_restore_callbacks", lambda: _restore_callbacks(callbacks)),
                    ("reject_restore_context", lambda: _restore_local_context(context)),
                    ("reject_contents_unchanged", lambda: _contents() == before),
                ],
                primary=failure,
            )

    def passed(label):
        assert _ledger_snapshot() == ledger_before
        assert _protected() == protected_before, (
            "镀色操作改变了 Customer/Sales Order/Item 父子内容、元数据或覆盖设置"
        )
        assert _native_sources() == sources_before
        assert label not in checks
        checks.append(label)

    def clear_metadata_cache():
        frappe.clear_cache(doctype=DOCTYPE)
        for user in {*users, original_user, "Administrator"}:
            frappe.cache.delete_value(f"masked_fields::{DOCTYPE}::::{user}")

    def tightened(field, prop, action):
        nonlocal changed_meta
        before, callbacks, context = _contents(), _callbacks(), _local_context()
        prior_user, prior_test = frappe.session.user, frappe.in_test
        nested = "jf_plating_tighten_" + frappe.generate_hash(length=8)
        failure = None
        _begin_rollback_only(nested)
        try:
            frappe.set_user("Administrator")
            assert not frappe.db.exists(
                "Property Setter", DOCTYPE + "-" + field + "-" + prop
            ), "不覆盖已有字段限制"
            assert not frappe.get_meta(DOCTYPE).get_field(field).get(prop)
            insert(
                "Property Setter",
                doctype_or_field="DocField",
                doc_type=DOCTYPE,
                field_name=field,
                property=prop,
                value="1",
                property_type="Int",
                is_system_generated=0,
            )
            changed_meta = True
            clear_metadata_cache()
            frappe.set_user(manager)
            assert frappe.get_meta(DOCTYPE).get_field(field).get(prop)
            native(action)
        except Exception as error:
            failure = error
            raise
        finally:

            def rollback():
                frappe.set_user("Administrator")
                _profile_rollback(nested, callbacks)

            def restore():
                clear_metadata_cache()
                _profile_restore_caller(
                    prior_user, prior_test, [], set(), callbacks, context
                )

            _profile_audit(
                [
                    ("tightening_native_rollback", rollback),
                    ("tightening_restore_metadata_and_caller", restore),
                    ("tightening_contents_restored", lambda: _contents() == before),
                ],
                primary=failure,
            )

    try:
        frappe.set_user("Administrator")
        manager = actor("sales-manager", "Sales Manager", company)
        system = actor("system-manager", "System Manager", company)
        clerk = actor("sales-user", "Sales User", company)
        outsider = actor("outside", "Sales Manager", other_company)
        archives, originals = {}, {}

        for label, user in (("sales_manager", manager), ("system_manager", system)):
            current = label + "_native_create_original_code_color_and_versions"
            frappe.set_user(user)
            assert {*IDENTITY, *EDIT_FIELDS}.issubset(
                _columns("read") & _columns("write")
            )
            empty = native(lambda: init_dictionary(KIND))
            assert empty["company"] is None and empty["can_write"]
            assert empty["number_preview"] is None
            assert empty["field_meta"]["dictionary_type"]["read_only"]
            originals[label] = payload(value=" " + prefix + "-" + label + " ")
            archive = save(originals[label])
            assert all(
                archive[field] == originals[label][field] for field in EDIT_FIELDS
            )
            assert archive["abbreviation"] in (None, "")
            changed = update(
                archive, code=" 0008 / 修改代码 ", color_system=" 修改色系 / 原文 "
            )
            assert changed["code"] == " 0008 / 修改代码 "
            assert changed["color_system"] == " 修改色系 / 原文 "
            assert changed["version"] != archive["version"]
            document = frappe.get_doc(DOCTYPE, changed["name"])
            assert _version_has(document, "code") and _version_has(
                document, "color_system"
            )
            archives[label] = changed
            passed(current)

        current = "provisional_preview_independent_company_counter_and_81_char_series"
        frappe.set_user(manager)
        counters = _rows("Series")
        preview = native(lambda: init_dictionary(KIND, company))
        assert preview["number_preview_is_provisional"] and _rows("Series") == counters
        numbered = save(payload(value=prefix + "-numbered"))
        assert numbered["legacy_number"] == preview["number_preview"]
        assert not numbered["number_preview_is_provisional"]
        frappe.set_user(outsider)
        foreign_preview = native(lambda: init_dictionary(KIND, other_company))
        foreign = save(payload(company=other_company, value=prefix + "-foreign"))
        assert foreign["legacy_number"] == foreign_preview["number_preview"]
        assert _series_key(company, KIND) != _series_key(other_company, KIND)
        evidence["series_key_length"] = 81
        passed(current)

        current = (
            "native_form_allocates_server_identity_and_refuses_forged_new_metadata"
        )
        frappe.set_user(manager)
        direct = insert(
            DOCTYPE,
            company=company,
            dictionary_type=KIND,
            code=" 0009 ",
            value=prefix + "-direct",
            color_system=" 原生色系 ",
            remarks="",
            inactive=0,
        )
        assert direct.request_id and direct.request_digest and direct.legacy_number
        assert direct.numbering_mode == AUTO_MODE and direct.code == " 0009 "
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
                    dictionary_type=KIND,
                    value=prefix + "-forged",
                    inactive=0,
                    **{field: value},
                )
            )
        passed(current)

        current = (
            "uuid_original_digest_covers_code_color_and_retry_reads_current_receipt"
        )
        original = originals["sales_manager"]
        previous = archives["sales_manager"]
        renamed = update(previous, value=" " + prefix + "-改名 ", remarks="")
        before, version_count = _contents(), len(_versions(DOCTYPE, renamed["name"]))
        assert save(original) == renamed
        assert native(
            lambda: resolve_creation(original["request_id"], KIND, company)
        ) == {"status": "found", "archive": renamed}
        assert native(lambda: resolve_creation(str(uuid4()), KIND, company)) == {
            "status": "not_found"
        }
        assert (
            _contents() == before
            and len(_versions(DOCTYPE, renamed["name"])) == version_count
        )
        for field in ("code", "color_system"):
            rejected(
                lambda field=field: save_dictionary(
                    original | {field: original[field].strip()}
                ),
                text=COLLISION,
                atomic=True,
            )
        passed(current)

        current = "locked_stale_update_rejects_without_version_or_raw_value_overwrite"
        rejected(
            lambda: update(previous, code="旧版本覆盖"),
            frappe.TimestampMismatchError,
            text="重读核对",
            atomic=True,
        )
        assert native(lambda: read_dictionary(renamed["name"], KIND)) == renamed
        passed(current)

        current = (
            "native_form_and_api_fixed_identity_number_abbreviation_receipt_immutable"
        )
        frappe.set_user("Administrator")
        for field, value in (
            ("company", other_company),
            ("dictionary_type", "订单类型"),
            ("request_id", str(uuid4())),
            ("request_digest", "f" * 64),
            ("numbering_mode", "forged"),
            ("legacy_number", "X"),
            ("abbreviation", "JP"),
        ):
            document = frappe.get_doc(DOCTYPE, renamed["name"])
            document.set(field, value)
            rejected(document.save)
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
        rejected(lambda: update(renamed, dictionary_type="订单类型"), atomic=True)
        passed(current)

        current = "pure_sales_user_quiet_reads_and_real_native_write_denials"
        frappe.set_user(clerk)
        assert frappe.has_permission(DOCTYPE, "read") and frappe.has_permission(
            DOCTYPE, "select"
        )
        assert all(
            not frappe.has_permission(DOCTYPE, action, throw=False)
            for action in ("create", "write", "delete", "export")
        )
        messages = list(getattr(frappe.local, "message_log", None) or [])
        responses = (
            native(lambda: init_dictionary(KIND)),
            native(lambda: init_dictionary(KIND, company)),
            native(lambda: read_dictionary(renamed["name"], KIND)),
        )
        assert all(not row["can_write"] for row in responses)
        assert all(row["field_meta"]["code"]["read_only"] for row in responses)
        assert native(lambda: list_dictionary(KIND, company, prefix))["rows"]
        assert list(getattr(frappe.local, "message_log", None) or []) == messages
        rejected(
            lambda: save_dictionary(payload()), frappe.PermissionError, atomic=True
        )
        rejected(
            lambda: update(renamed, code="clerk edit"),
            frappe.PermissionError,
            atomic=True,
        )
        document = frappe.get_doc(DOCTYPE, renamed["name"])
        document.color_system = "clerk native edit"
        rejected(document.save, frappe.PermissionError)
        assert (
            native(lambda: resolve_creation(original["request_id"], KIND, company))[
                "status"
            ]
            == "found"
        )
        passed(current)

        current = "actual_company_user_permissions_guard_all_endpoints"
        frappe.set_user(manager)
        for action in (
            lambda: init_dictionary(KIND, other_company),
            lambda: list_dictionary(KIND, other_company),
            lambda: read_dictionary(foreign["name"], KIND),
            lambda: resolve_creation(str(uuid4()), KIND, other_company),
        ):
            rejected(action, frappe.PermissionError)
        rejected(
            lambda: save_dictionary(payload(company=other_company)),
            frappe.PermissionError,
            atomic=True,
        )
        frappe.set_user(outsider)
        rejected(lambda: list_dictionary(KIND, company), frappe.PermissionError)
        passed(current)

        current = "dictionary_user_permission_hides_rows_reads_and_uuid_receipt"
        frappe.set_user("Administrator")
        scoped = actor("dictionary-scoped", "Sales User", company, renamed["name"])
        hidden_token = frappe.get_doc(DOCTYPE, numbered["name"]).request_id
        frappe.set_user(scoped)
        listed = native(
            lambda: list_dictionary(KIND, company, prefix, include_inactive=True)
        )
        assert {row["name"] for row in listed["rows"]} == {renamed["name"]}
        assert native(lambda: resolve_creation(hidden_token, KIND, company)) == {
            "status": "not_found"
        }
        rejected(
            lambda: read_dictionary(numbered["name"], KIND), frappe.PermissionError
        )
        passed(current)

        current = "native_paging_code_color_keywords_company_scope_and_clamping"
        frappe.set_user(manager)
        paging_prefix = prefix + "-PAGE-"
        fixtures = [
            save(
                payload(
                    value=paging_prefix + str(index).zfill(3),
                    code="000" + str(index).zfill(3),
                    color_system="原系-" + str(index).zfill(3),
                )
            )
            for index in range(103)
        ]
        first = native(
            lambda: list_dictionary(KIND, company, paging_prefix, page_length=100)
        )
        last = native(
            lambda: list_dictionary(
                KIND, company, paging_prefix, start=100, page_length=100
            )
        )
        beyond = native(
            lambda: list_dictionary(
                KIND, company, paging_prefix, start=99999, page_length=100
            )
        )
        empty = native(
            lambda: list_dictionary(KIND, company, prefix + "-MISSING", start=100)
        )
        assert first["total"] == last["total"] == 103
        assert len(first["rows"]) == 100 and len(last["rows"]) == 3
        assert first["rows"][0]["name"] == fixtures[0]["name"]
        assert last["rows"][-1]["name"] == fixtures[-1]["name"]
        assert beyond["start"] == 100 and beyond["rows"] == last["rows"]
        assert empty["start"] == empty["total"] == 0
        for keyword in (fixtures[50]["code"], fixtures[50]["color_system"]):
            matched = native(
                lambda keyword=keyword: list_dictionary(KIND, company, keyword)
            )
            assert {row["name"] for row in matched["rows"]} == {fixtures[50]["name"]}
        assert all(
            row["company"] == company and row["dictionary_type"] == KIND
            for row in first["rows"] + last["rows"]
        )
        evidence["paging"] = {
            "total": 103,
            "first": 100,
            "last": 3,
            "source": "twice-checked serial native projections",
        }
        passed(current)

        current = "inactive_preserves_history_and_native_version"
        stopped = update(fixtures[0], inactive=1)
        assert _version_has(frappe.get_doc(DOCTYPE, stopped["name"]), "inactive")
        assert (
            native(lambda: list_dictionary(KIND, company, paging_prefix))["total"]
            == 102
        )
        assert (
            native(
                lambda: list_dictionary(
                    KIND, company, paging_prefix, include_inactive=True
                )
            )["total"]
            == 103
        )
        assert (
            native(lambda: read_dictionary(stopped["name"], KIND))["code"]
            == fixtures[0]["code"]
        )
        passed(current)

        mask_archive = save(
            payload(
                value=prefix + "-MASK-VISIBLE",
                code=prefix + "-SECRET-CODE",
                color_system=prefix + "-SECRET-COLOR",
                remarks="公开备注",
            )
        )
        for field in ("code", "color_system"):
            current = (
                "actual_"
                + field
                + "_mask_omits_dto_metadata_and_search_and_blocks_write"
            )

            def mask_checks(field=field):
                active_meta = frappe.get_meta(DOCTYPE)
                assert 0 not in active_meta.get_permlevel_access(
                    "mask", parenttype=None
                )
                for view in (
                    init_dictionary(KIND, company),
                    read_dictionary(mask_archive["name"], KIND),
                ):
                    assert field not in view and field not in view["field_meta"]
                listed = list_dictionary(KIND, company, prefix + "-MASK-VISIBLE")
                assert {row["name"] for row in listed["rows"]} == {mask_archive["name"]}
                assert all(field not in row for row in listed["rows"])
                assert list_dictionary(KIND, company, mask_archive[field])["total"] == 0
                rejected(
                    lambda: update(mask_archive, **{field: "hidden overwrite"}),
                    frappe.PermissionError,
                    atomic=True,
                )
                visible = save_dictionary(
                    {
                        "name": mask_archive["name"],
                        "version": mask_archive["version"],
                        "company": company,
                        "dictionary_type": KIND,
                        "remarks": "可见备注修改",
                    }
                )
                assert field not in visible and field not in visible["field_meta"]
                assert (
                    frappe.db.get_value(DOCTYPE, mask_archive["name"], field)
                    == mask_archive[field]
                )

            tightened(field, "mask", mask_checks)
            passed(current)

        current = "actual_readonly_code_blocks_api_and_native_form_edits"

        def readonly_checks():
            view = read_dictionary(mask_archive["name"], KIND)
            assert view["field_meta"]["code"]["read_only"]
            rejected(
                lambda: update(view, code="readonly overwrite"),
                frappe.PermissionError,
                atomic=True,
            )
            document = frappe.get_doc(DOCTYPE, mask_archive["name"])
            document.code = "readonly native overwrite"
            rejected(document.save, frappe.PermissionError)

        tightened("code", "read_only", readonly_checks)
        passed(current)

        current = "actual_native_mandatory_failure_rolls_back_series_record_version_and_context"

        def mandatory_checks():
            values = payload(remarks="")
            rejected(
                lambda: save_dictionary(values), frappe.MandatoryError, atomic=True
            )
            assert resolve_creation(values["request_id"], KIND, company) == {
                "status": "not_found"
            }

        tightened("remarks", "reqd", mandatory_checks)
        passed(current)

        current = "explicit_type_missing_invalid_read_and_input_rejection"
        frappe.set_user(manager)
        for invalid in (None, "", " 镀色", "订单类型", "订单要求"):
            for action in (
                lambda invalid=invalid: init_dictionary(invalid, company),
                lambda invalid=invalid: read_dictionary(renamed["name"], invalid),
                lambda invalid=invalid: list_dictionary(invalid, company),
                lambda invalid=invalid: resolve_creation(
                    str(uuid4()), invalid, company
                ),
            ):
                rejected(action)
            rejected(
                lambda invalid=invalid: save_dictionary(
                    payload(dictionary_type=invalid)
                ),
                atomic=True,
            )
        rejected(lambda: read_dictionary(renamed["name"]), TypeError)
        missing = payload()
        del missing["dictionary_type"]
        rejected(lambda: save_dictionary(missing), atomic=True)
        for changes in (
            {"value": " "},
            {"value": "x" * 141},
            {"code": 1},
            {"color_system": []},
            {"inactive": 1.0},
            {"request_id": "bad-uuid"},
        ):
            rejected(
                lambda changes=changes: save_dictionary(payload(**changes)), atomic=True
            )
        passed(current)

        current = "guest_endpoints_denied"
        frappe.set_user("Guest")
        for action in (
            lambda: init_dictionary(KIND),
            lambda: list_dictionary(KIND, company),
            lambda: read_dictionary(renamed["name"], KIND),
            lambda: resolve_creation(str(uuid4()), KIND, company),
            lambda: save_dictionary(payload()),
        ):
            rejected(action, frappe.PermissionError)
        passed(current)
    except Exception as error:
        primary = error
        error.add_note("镀色原生验收失败门槛：" + current)
        error.add_note("已通过门槛：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:

        def collect():
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update(
                    (row.doctype, row.name)
                    for row in document.get_all_children()
                    if row.name
                )

        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks_before)

        def restore():
            if changed_meta:
                clear_metadata_cache()
            _profile_restore_caller(
                original_user,
                original_test,
                users,
                records,
                callbacks_before,
                context_before,
            )

        def audit_physical():
            nonlocal physical_after
            physical_after = _physical_snapshot()
            return physical_after == physical_before

        _profile_audit(
            [
                ("collect_exact_new_parent_and_child_fixtures", collect),
                ("native_rollback_and_only_owned_cleanup_callbacks", rollback),
                ("invalidate_owned_caches_and_restore_original_caller", restore),
                (
                    "all_exact_fixtures_absent",
                    lambda: all(not frappe.db.exists(dt, name) for dt, name in records),
                ),
                (
                    "all_scoped_native_and_account_contents_unchanged",
                    lambda: _contents() == contents_before,
                ),
                (
                    "customer_sales_order_item_recursive_contents_metadata_overrides_unchanged",
                    lambda: _protected() == protected_before,
                ),
                (
                    "all_native_source_files_unchanged",
                    lambda: _native_sources() == sources_before,
                ),
                (
                    "all_physical_tables_rows_engines_and_counts_unchanged",
                    audit_physical,
                ),
                (
                    "whole_gl_and_sle_unchanged",
                    lambda: _ledger_snapshot() == ledger_before,
                ),
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
    checks.append(
        "complete_physical_business_auth_metadata_series_version_ledger_rollback"
    )
    return {
        "passed": True,
        "gate_count": len(checks),
        "checks": checks,
        "actual_roles": actual_roles,
        "evidence": evidence,
        "business_data": "rolled_back",
        "physical_before": physical_before,
        "physical_after": physical_after,
        "native_customer_sales_order_item_unchanged": _protected() == protected_before,
        "native_source_sha256": sources_before,
        "whole_ledger_before": ledger_before,
        "whole_ledger_after": _ledger_snapshot(),
        "gui_verified": False,
        "concurrency": "serial local rollback experiment; external snapshot required",
        "remaining": [
            "旧编号算法",
            "更新简拼",
            "条码生成",
            "旧导入打印及预览",
            "永久新建及删除回执生命周期：另由独立原生QA验收",
            "前端操作验收",
        ],
    }
