"""One real-role shared-Operation experiment in the exact offline clone.

The caller reloads only the reviewed new mapping DocType before calling smoke
once. This helper never connects, changes schema, commits, seeds business data,
downloads or invokes another validation fixture. Native controllers, existing
roles and temporary tightening Property Setters supply the actual behavior.
Every temporary row is rolled back; only opaque complete-row hashes leave here.
"""

import hashlib
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
from jifeng_erp.order_operations import (
    CHANGED,
    COLLISION,
    DOCTYPE,
    MAX_SEQUENCE,
    NATIVE,
    RECEIPT,
    SCOPE,
    init_operations,
    list_operations,
    read_operation,
    resolve_creation,
    save_operation,
)
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks
from jifeng_erp.personnel_separation_validation import (
    _local_context,
    _restore_local_context,
)
from jifeng_erp.plating_color_validation import (
    _physical_snapshot as _physical_rows,
    _wire,
)
from jifeng_erp.product_dictionary_validation import (
    _document_snapshot,
    _sql_row_hash,
)


ISOLATED_SITE = "jifeng-plating-native-isolated.localhost"
ISOLATED_DATABASE = "jf_plap_isolated_20261002"
VALIDATION_COMPANIES = {"Jifeng ERP UI Validation", "Jifeng ERP Validation"}
PROTECTED = {
    "BOM",
    "Work Order",
    "Job Card",
    "Jifeng Product Profile",
    "Jifeng Wage Entry",
    "Jifeng Wage Allocation",
    "Jifeng Flow Order",
    "Salary Slip",
    "Additional Salary",
    "Item",
    "Employee",
}


def _doctypes(roots):
    selected, pending = set(roots), list(roots)
    while pending:
        for field in frappe.get_meta(pending.pop()).get_table_fields():
            assert (
                not field.is_virtual and not frappe.get_meta(field.options).is_virtual
            )
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


def _contents():
    roots = PROTECTED | {
        NATIVE,
        DOCTYPE,
        "User",
        "User Permission",
        "DocShare",
        "Version",
        "Property Setter",
        "Comment",
        "Activity Log",
        "File",
        "Deleted Document",
    }
    result = {doctype: _rows(doctype) for doctype in sorted(_doctypes(roots))}
    for table in ("__Auth", "tabSeries", "__global_search"):
        result[table] = _sql_row_hash(
            frappe.db.sql(
                "SELECT * FROM `" + table + "` ORDER BY 1",
                as_dict=True,
            )
        )
    return result


def _protected():
    result = {}
    for doctype in sorted(_doctypes(PROTECTED)):
        result[doctype] = {
            "contents": _rows(doctype),
            "metadata": _document_snapshot("DocType", doctype),
            "overrides": {
                override: _sql_row_hash(
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
                for override, field in (
                    ("Custom Field", "dt"),
                    ("Property Setter", "doc_type"),
                    ("Custom DocPerm", "parent"),
                )
            },
        }
    return result


def _hash_rows(rows):
    return hashlib.sha256(
        json.dumps(
            [{key: _wire(value) for key, value in row.items()} for row in rows],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _physical_snapshot():
    """All physical contents, engines, SQL definitions and actual indexes.

    This is a serial experiment oracle. The external runner independently locks
    nontransactional tables and rereads the whole database in a new connection.
    """
    result = _physical_rows()
    assert "tab" + DOCTYPE in result and "tab" + NATIVE in result
    for name, item in result.items():
        quoted = "`" + name.replace("`", "``") + "`"
        item["schema_sha256"] = _hash_rows(
            frappe.db.sql(
                "SHOW CREATE TABLE " + quoted,
                as_dict=True,
            )
        )
        indexes = frappe.db.sql("SHOW INDEX FROM " + quoted, as_dict=True)
        # Optimizer estimates change after temporary transactional inserts;
        # cardinality is not an index definition or a persisted business value.
        item["indexes_sha256"] = _hash_rows(
            [
                {key: value for key, value in row.items() if key != "Cardinality"}
                for row in sorted(
                    indexes,
                    key=lambda row: (
                        row["Key_name"],
                        row["Seq_in_index"],
                    ),
                )
            ]
        )
    return result


def _native_sources():
    paths = {
        "Operation JSON": frappe.get_app_path(
            "erpnext",
            "manufacturing",
            "doctype",
            "operation",
            "operation.json",
        ),
        "Operation controller": frappe.get_app_path(
            "erpnext",
            "manufacturing",
            "doctype",
            "operation",
            "operation.py",
        ),
        "Native rename": frappe.get_app_path("frappe", "model", "rename_doc.py"),
        "Product Profile": frappe.get_app_path(
            "jifeng_erp",
            "jifeng_erp",
            "doctype",
            "jifeng_product_profile",
            "jifeng_product_profile.json",
        ),
        "Wage Entry": frappe.get_app_path(
            "jifeng_erp",
            "jifeng_erp",
            "doctype",
            "jifeng_wage_entry",
            "jifeng_wage_entry.json",
        ),
    }
    return {
        label: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for label, path in paths.items()
    }


def smoke(company, other_company):
    """Actual manufacturing users, native selectors and complete rollback."""
    assert frappe.local.site == ISOLATED_SITE
    assert frappe.conf.db_name == ISOLATED_DATABASE
    assert frappe.db.db_type == "mariadb"
    assert int(frappe.conf.maintenance_mode or 0) == 1
    assert int(frappe.conf.pause_scheduler or 0) == 1
    frappe.only_for("System Manager")
    assert {company, other_company} == VALIDATION_COMPANIES and company != other_company
    assert all(frappe.db.exists("Company", scope) for scope in VALIDATION_COMPANIES)
    assert NATIVE == "Operation" and DOCTYPE == "Jifeng Order Operation"
    assert SCOPE == "shared_operation"
    meta = frappe.get_meta(DOCTYPE)
    native_meta = frappe.get_meta(NATIVE)
    assert meta.track_changes and not meta.is_submittable
    assert native_meta.track_changes and native_meta.allow_rename
    assert not meta.has_field("company") and not native_meta.has_field("company")
    operation = meta.get_field("operation")
    assert operation.fieldtype == "Link" and operation.options == NATIVE
    assert (
        operation.unique
        and operation.reqd
        and operation.set_only_once
        and operation.read_only
    )
    assert meta.get_field("sequence").fieldtype == "Int"
    assert {field.fieldname for field in meta.fields if field.unique} == {
        "operation",
        "request_id",
    }
    assert all(
        all(
            meta.get_field(field).get(attr)
            for attr in (
                "hidden",
                "read_only",
                "no_copy",
                "print_hide",
            )
        )
        for field in RECEIPT
    )
    assert {row.role for row in meta.permissions} == {
        "Manufacturing User",
        "Manufacturing Manager",
    }
    physical_before = _physical_snapshot()
    assert physical_before["tab" + DOCTYPE]["rows"] == 0
    before, protected_before, ledger_before, sources_before = (
        _contents(),
        _protected(),
        _ledger_snapshot(),
        _native_sources(),
    )
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    original_roles = sorted(frappe.get_roles(original_user))
    transaction_control = frappe.db._disable_transaction_control
    prefix = "JF-QA-ORD-OP-" + frappe.generate_hash(length=10)
    point = "jf_order_operations_" + frappe.generate_hash(length=8)
    objects, users, records, checks, roles, evidence = [], [], set(), [], {}, {}
    primary, current, physical_after, changed_meta = None, "fixture_setup", None, set()
    _begin_rollback_only(point)

    def native(action):
        prior = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = prior

    def insert(doctype, **values):
        assert doctype in {
            "User",
            "User Permission",
            "Property Setter",
            NATIVE,
            DOCTYPE,
        }
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((document.doctype, document.name))
        return document

    def actor(label, role, scope, *, permission=None):
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
        if permission:
            insert(
                "User Permission",
                user=user.name,
                allow=permission[0],
                for_value=permission[1],
                apply_to_all_doctypes=1,
            )
        frappe.clear_cache(user=user.name)
        roles[label] = sorted(frappe.get_roles(user.name))
        assert role in roles[label] and "System Manager" not in roles[label]
        if role != "Manufacturing Manager":
            assert "Manufacturing Manager" not in roles[label]
        return user.name

    def payload(label="create", **changes):
        return {
            "operation": prefix + "-" + label,
            "sequence": 7,
            "remarks": " 原文工序备注\n第二行 " + prefix,
            "request_id": str(uuid4()),
            **changes,
        }

    def save(values):
        result = native(lambda: save_operation(values))
        records.add((NATIVE, result["name"]))
        if result["ordering_name"]:
            records.add((DOCTYPE, result["ordering_name"]))
        return result

    def update(row, **changes):
        return save(
            {
                "operation": row["name"],
                **{
                    field: row[field]
                    for field in (
                        "version",
                        "ordering_name",
                        "ordering_version",
                    )
                },
                **changes,
            }
        )

    def rejected(action, errors=frappe.ValidationError, *, text=None, atomic=False):
        nested = "jf_operation_reject_" + frappe.generate_hash(length=8)
        contents, callbacks, context = _contents(), _callbacks(), _local_context()
        failure = None
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                if text:
                    assert text in str(error)
                if atomic:
                    assert _contents() == contents, (
                        "拒绝 API 未完整回滚原生来源/排序/Version"
                    )
                    assert _callbacks() == callbacks and _local_context() == context
            else:
                raise AssertionError("共用工序接受了本应拒绝的请求")
        except Exception as error:
            failure = error
            raise
        finally:
            _profile_audit(
                [
                    (
                        "rejected_nested_rollback",
                        lambda: frappe.db.rollback(save_point=nested),
                    ),
                    (
                        "rejected_restore_callbacks",
                        lambda: _restore_callbacks(callbacks),
                    ),
                    (
                        "rejected_restore_context",
                        lambda: _restore_local_context(context),
                    ),
                    ("rejected_contents_restored", lambda: _contents() == contents),
                ],
                primary=failure,
            )

    def passed(label):
        assert _protected() == protected_before, (
            "改变了既有生产、商品或工资父子记录/元数据"
        )
        assert (
            _ledger_snapshot() == ledger_before and _native_sources() == sources_before
        )
        assert label not in checks
        checks.append(label)

    def hidden_receipt(values):
        """Native permission filtering may conceal even receipt existence.

        A filtered get_list result is missing, while a visible receipt whose
        referenced source is unreadable may raise PermissionError. Neither
        response may disclose a source projection or permit another write.
        """
        contents, callbacks, context = _contents(), _callbacks(), _local_context()
        try:
            result = native(lambda: resolve_creation(values))
        except frappe.PermissionError:
            outcome = "denied"
        else:
            assert result == {
                "scope": SCOPE,
                "request_id": values["request_id"],
                "status": "missing",
                "operation": None,
            }, "不可见回执泄露了来源或其它请求身份"
            outcome = "missing"
        assert _contents() == contents, "只读不可见回执核对改变了持久记录"
        assert _callbacks() == callbacks and _local_context() == context
        return outcome

    def clear_metadata(doctypes=None):
        for doctype in doctypes or changed_meta:
            frappe.clear_cache(doctype=doctype)
            for user in {*users, original_user, "Administrator"}:
                frappe.cache.delete_value(f"masked_fields::{doctype}::::{user}")

    def tightened(settings, action):
        contents, callbacks, context = _contents(), _callbacks(), _local_context()
        prior_user, prior_test = frappe.session.user, frappe.in_test
        nested = "jf_operation_tighten_" + frappe.generate_hash(length=8)
        failure, targets = None, {item[0] for item in settings}
        _begin_rollback_only(nested)
        try:
            frappe.set_user("Administrator")
            for doctype, field, prop, value, kind in settings:
                assert not frappe.db.exists(
                    "Property Setter", doctype + "-" + field + "-" + prop
                )
                insert(
                    "Property Setter",
                    doctype_or_field="DocField",
                    doc_type=doctype,
                    field_name=field,
                    property=prop,
                    value=str(value),
                    property_type=kind,
                    is_system_generated=0,
                )
            changed_meta.update(targets)
            clear_metadata(targets)
            frappe.set_user(manager)
            native(action)
        except Exception as error:
            failure = error
            raise
        finally:

            def rollback():
                frappe.set_user("Administrator")
                _profile_rollback(nested, callbacks)

            def restore():
                clear_metadata(targets)
                _profile_restore_caller(
                    prior_user, prior_test, [], set(), callbacks, context
                )

            _profile_audit(
                [
                    ("temporary_metadata_rollback", rollback),
                    ("temporary_metadata_cache_and_caller_restored", restore),
                    (
                        "temporary_metadata_contents_restored",
                        lambda: _contents() == contents,
                    ),
                ],
                primary=failure,
            )

    try:
        frappe.set_user("Administrator")
        manager = actor("manager", "Manufacturing Manager", company)
        operator = actor("operator", "Manufacturing User", other_company)
        sales = actor("sales", "Sales User", company)
        originals, saved = {}, {}

        for label, user in (("manager", manager), ("operator", operator)):
            current = label + "_native_creation_description_and_both_source_versions"
            frappe.set_user(user)
            preview = native(init_operations)
            assert (
                preview["scope"] == SCOPE
                and preview["can_create"]
                and preview["can_write"]
            )
            assert preview["name"] is preview["ordering_name"] is None
            assert not preview["field_meta"]["operation"]["read_only"]
            values = payload(label)
            row = save(values)
            assert row["remarks"] == values["remarks"] and row["sequence"] == 7
            assert row["request_id"] == values["request_id"]
            assert (
                row["name"] == values["operation"]
                and row["field_meta"]["operation"]["read_only"]
            )
            changed = update(row, remarks=values["remarks"] + " 修改", sequence=8)
            assert changed["version"] != row["version"]
            assert changed["ordering_version"] != row["ordering_version"]
            assert _version_has(frappe.get_doc(NATIVE, row["name"]), "description")
            assert _version_has(
                frappe.get_doc(DOCTYPE, row["ordering_name"]), "sequence"
            )
            originals[label], saved[label] = values, changed
            passed(current)

        current = "company_user_permission_does_not_partition_global_native_operations"
        for user in (manager, operator):
            frappe.set_user(user)
            assert (
                native(lambda: read_operation(saved["manager"]["name"]))["name"]
                == saved["manager"]["name"]
            )
            assert (
                native(lambda: read_operation(saved["operator"]["name"]))["name"]
                == saved["operator"]["name"]
            )
            rows = native(lambda: list_operations(page_length=100))["rows"]
            assert {saved["manager"]["name"], saved["operator"]["name"]}.issubset(
                {row["name"] for row in rows}
            )
        evidence["scope"] = (
            "Two different actual Company User Permissions read the same native Operation rows"
        )
        passed(current)

        current = (
            "native_operation_link_validation_and_actual_permission_filtered_selector"
        )
        frappe.set_user(manager)
        target = saved["manager"]["name"]
        from frappe.desk.search import search_link

        references = []
        for doctype in ("Jifeng Operation Line", "BOM Operation", "Job Card"):
            field = frappe.get_meta(doctype).get_field("operation")
            assert field.fieldtype == "Link" and field.options == NATIVE
            reference = frappe.get_doc({"doctype": doctype, "operation": target})
            # Native insert/save sets this before invoking Link validation.
            # The unpersisted selector probe uses that same legitimate action.
            reference._action = "save"
            reference._validate_links()
            found = native(
                lambda doctype=doctype: search_link(
                    NATIVE,
                    target,
                    reference_doctype=doctype,
                    link_fieldname="operation",
                )
            )
            assert target in {row["value"] for row in found}
            references.append(doctype)
        profile = frappe.new_doc("Jifeng Product Profile")
        profile.append("operations", {"operation": target, "unit_price": 3})
        profile._action = "save"
        profile._validate_links()
        frappe.get_doc(NATIVE, target).check_permission("select")
        assert (
            frappe.get_meta("Jifeng Wage Entry").get_field("operation").fieldtype
            == "Data"
        )
        assert target in {
            row["value"] for row in native(lambda: search_link(NATIVE, target))
        }
        evidence["native_references"] = {
            "checked": references + ["Jifeng Product Profile.operations"],
            "method": "real native Link validation on unpersisted documents and actual search_link",
            "wages": "existing wage operation is Data; native Operation select and search work",
            "full_production_or_payroll_created": False,
        }
        passed(current)

        current = (
            "pure_sequence_update_preserves_every_native_parent_child_and_version_row"
        )
        sub = insert(NATIVE, name=prefix + "-sub", description="子工序原文")
        source = insert(
            NATIVE,
            name=prefix + "-native",
            description="已有备注原文",
            batch_size=5,
            create_job_card_based_on_batch_size=1,
            is_corrective_operation=1,
            sub_operations=[
                {
                    "operation": sub.name,
                    "time_in_mins": 3.25,
                    "description": "子表备注原文",
                }
            ],
        )
        source_before = _document_snapshot(NATIVE, source.name)
        versions_before = _rows("Version")
        before_map = native(lambda: read_operation(source.name))
        assert before_map["ordering_name"] is before_map["ordering_version"] is None
        mapped = update(before_map, sequence=15)
        assert mapped["version"] == before_map["version"]
        assert _document_snapshot(NATIVE, source.name) == source_before
        assert frappe.get_doc(DOCTYPE, mapped["ordering_name"]).request_id is None
        # Native first inserts have no changed-field Version. A sequence-only
        # update of the mapping then generates its own native Version.
        moved = update(mapped, sequence=16)
        assert moved["version"] == mapped["version"]
        assert moved["ordering_version"] != mapped["ordering_version"]
        assert _version_has(frappe.get_doc(DOCTYPE, moved["ordering_name"]), "sequence")
        assert _document_snapshot(NATIVE, source.name) == source_before
        assert versions_before != _rows("Version")
        passed(current)

        current = (
            "remarks_only_update_preserves_map_and_native_nonrequested_fields_children"
        )
        mapping_before = _document_snapshot(DOCTYPE, moved["ordering_name"])
        before_document = frappe.get_doc(NATIVE, source.name)
        native_values = before_document.as_dict()
        changed = update(moved, remarks=" 修改已有工序备注\n原文 ")
        after_document = frappe.get_doc(NATIVE, source.name)
        for field in before_document.meta.get_valid_columns():
            if field not in {"description", "modified", "modified_by"}:
                assert after_document.get(field) == native_values.get(field)
        for field in before_document.meta.get_table_fields():
            before_children = before_document.get(field.fieldname) or []
            after_children = after_document.get(field.fieldname) or []
            assert len(before_children) == len(after_children)
            for old_child, new_child in zip(
                before_children, after_children, strict=True
            ):
                for column in frappe.get_meta(field.options).get_valid_columns():
                    if column not in {"modified", "modified_by"}:
                        assert new_child.get(column) == old_child.get(column)
                # Native Document.set_user_and_timestamp updates unchanged
                # children's audit timestamps when the parent is saved.
                assert new_child.modified == after_document.modified
                assert new_child.modified_by == after_document.modified_by
        assert _document_snapshot(DOCTYPE, moved["ordering_name"]) == mapping_before
        assert changed["ordering_version"] == moved["ordering_version"]
        assert _version_has(after_document, "description")
        passed(current)

        current = "two_stale_versions_and_absent_mapping_identity_reject_atomically"
        rejected(
            lambda: update(moved, remarks="旧原生版本覆盖"),
            frappe.TimestampMismatchError,
            text=CHANGED,
            atomic=True,
        )
        stale_order = changed | {"ordering_version": mapped["ordering_version"]}
        rejected(
            lambda: update(stale_order, sequence=9),
            frappe.TimestampMismatchError,
            text=CHANGED,
            atomic=True,
        )
        rejected(
            lambda: update(
                changed, ordering_name=None, ordering_version=None, sequence=9
            ),
            frappe.TimestampMismatchError,
            atomic=True,
        )
        passed(current)

        current = (
            "receipt_original_digest_duplicate_post_rejected_and_read_only_recovery"
        )
        original, row = originals["manager"], saved["manager"]
        receipt_before = _contents()
        resolved = native(lambda: resolve_creation(original))
        assert (
            resolved["status"] == "changed"
            and resolved["operation"]["name"] == row["name"]
        )
        assert resolved["operation"]["request_id"] == original["request_id"]
        assert (
            native(lambda: resolve_creation(payload("never-posted")))["status"]
            == "missing"
        )
        assert (
            native(
                lambda: resolve_creation(
                    original | {"remarks": original["remarks"].strip()}
                )
            )["status"]
            == "conflict"
        )
        assert _contents() == receipt_before
        rejected(lambda: save_operation(original), text=COLLISION, atomic=True)
        exact_values = payload("exact-receipt")
        exact = save(exact_values)
        assert native(lambda: resolve_creation(exact_values))["status"] == "found"
        rejected(lambda: save_operation(exact_values), text=COLLISION, atomic=True)
        rejected(
            lambda: save_operation(payload("exact-receipt")),
            text=COLLISION,
            atomic=True,
        )
        passed(current)

        current = (
            "native_rename_preserves_receipt_and_reports_changed_current_operation"
        )
        new_name = exact["name"] + "-renamed"
        # This documented native option avoids a background search rebuild
        # escaping the rollback-only experiment. Rename permissions and actual
        # Link propagation still run; global-search rebuild is not a test gate.
        native(
            lambda: frappe.rename_doc(
                NATIVE, exact["name"], new_name, rebuild_search=False
            )
        )
        records.add((NATIVE, new_name))
        assert not frappe.db.exists(NATIVE, exact["name"])
        assert (
            frappe.db.get_value(DOCTYPE, exact["ordering_name"], "operation")
            == new_name
        )
        resolved = native(lambda: resolve_creation(exact_values))
        assert (
            resolved["status"] == "changed"
            and resolved["operation"]["name"] == new_name
        )
        assert resolved["operation"]["request_id"] == exact_values["request_id"]
        rejected(lambda: save_operation(exact_values), text=COLLISION, atomic=True)
        passed(current)

        current = "normal_map_form_cannot_forge_bindings_or_receipt_and_cannot_delete"
        for field, value in (
            ("operation", source.name),
            ("request_id", str(uuid4())),
            ("request_digest", "f" * 64),
        ):
            document = frappe.get_doc(DOCTYPE, row["ordering_name"])
            document.set(field, value)
            rejected(document.save, text="不能修改")
        rejected(
            lambda: insert(DOCTYPE, operation=source.name, sequence=4),
            text="只能由共用工序入口建立",
        )
        document = frappe.get_doc(DOCTYPE, row["ordering_name"])
        document.sequence = 3
        native(document.save)
        assert _version_has(document, "sequence")
        rejected(
            lambda: frappe.delete_doc(DOCTYPE, document.name), frappe.PermissionError
        )
        frappe.set_user("Administrator")
        rejected(lambda: frappe.delete_doc(DOCTYPE, document.name), text="不能删除")
        frappe.set_user(manager)
        passed(current)

        current = "same_sequence_native_name_order_stable_paging_and_clamped_offset"
        paging = [
            save(payload("PAGE-" + str(index).zfill(3), sequence=123))
            for index in range(103)
        ]
        first = native(lambda: list_operations(page_length=100))
        second = native(lambda: list_operations(start=100, page_length=100))
        all_rows = []
        for start in range(0, first["total"], 100):
            page = native(
                lambda start=start: list_operations(start=start, page_length=100)
            )
            assert page["total"] == first["total"]
            all_rows.extend(page["rows"])
        assert (
            len({item["name"] for item in all_rows}) == len(all_rows) == first["total"]
        )
        subset = [
            item["name"]
            for item in all_rows
            if item["name"].startswith(prefix + "-PAGE-")
        ]
        assert subset == sorted(item["name"] for item in paging)
        assert first["rows"] == all_rows[:100] and second["rows"] == all_rows[100:200]
        beyond = native(lambda: list_operations(start=999999, page_length=100))
        expected_start = ((first["total"] - 1) // 100) * 100
        assert (
            beyond["start"] == expected_start
            and beyond["rows"] == all_rows[expected_start:]
        )
        evidence["paging"] = {
            "created_same_sequence": 103,
            "visible_total": first["total"],
            "source": "two serial native projections, no concurrent-snapshot claim",
        }
        passed(current)

        current = "operation_user_permission_filters_rows_and_hidden_receipt_without_projection"
        frappe.set_user("Administrator")
        scoped = actor(
            "native-scoped",
            "Manufacturing User",
            company,
            permission=(NATIVE, saved["operator"]["name"]),
        )
        frappe.set_user(scoped)
        listed = native(list_operations)
        assert {item["name"] for item in listed["rows"]} == {saved["operator"]["name"]}
        rejected(lambda: read_operation(row["name"]), frappe.PermissionError)
        evidence["operation_permission_receipt"] = hidden_receipt(original)
        rejected(lambda: update(row, sequence=1), frappe.PermissionError, atomic=True)
        passed(current)

        current = "map_user_permission_hidden_mapping_never_becomes_unmapped_fallback"
        frappe.set_user("Administrator")
        map_scoped = actor(
            "map-scoped",
            "Manufacturing User",
            company,
            permission=(DOCTYPE, saved["operator"]["ordering_name"]),
        )
        frappe.set_user(map_scoped)
        assert native(lambda: read_operation(saved["operator"]["name"]))[
            "ordering_name"
        ]
        rejected(lambda: read_operation(row["name"]), frappe.PermissionError)
        rejected(list_operations, frappe.PermissionError)
        evidence["mapping_permission_receipt"] = hidden_receipt(original)
        rejected(lambda: update(row, sequence=1), frappe.PermissionError, atomic=True)
        passed(current)

        frappe.set_user(manager)
        for doctype, field, external in (
            (NATIVE, "description", "remarks"),
            (DOCTYPE, "sequence", "sequence"),
        ):
            current = (
                "actual_"
                + external
                + "_mask_omits_projection_metadata_and_blocks_write"
            )

            def masked_checks(doctype=doctype, field=field, external=external):
                assert 0 not in frappe.get_meta(doctype).get_permlevel_access(
                    "mask", parenttype=None
                )
                for view in (init_operations(), read_operation(changed["name"])):
                    assert external not in view and external not in view["field_meta"]
                listed = list_operations(page_length=100)
                assert all(
                    external not in item and external not in item["field_meta"]
                    for item in listed["rows"]
                )
                rejected(
                    lambda: update(
                        changed,
                        **{external: 10 if external == "sequence" else "隐藏覆盖"},
                    ),
                    frappe.PermissionError,
                    atomic=True,
                )
                rejected(lambda: resolve_creation(exact_values), frappe.PermissionError)

            tightened([(doctype, field, "mask", 1, "Int")], masked_checks)
            passed(current)

        current = (
            "actual_readonly_fields_quietly_disable_editor_and_reject_both_api_writes"
        )

        def readonly_checks():
            view = read_operation(changed["name"])
            assert not view["can_write"] and not init_operations()["can_create"]
            assert view["field_meta"]["remarks"]["read_only"]
            assert view["field_meta"]["sequence"]["read_only"]
            for changes in ({"remarks": "只读覆盖"}, {"sequence": 2}):
                rejected(
                    lambda changes=changes: update(view, **changes),
                    frappe.PermissionError,
                    atomic=True,
                )
            document = frappe.get_doc(DOCTYPE, view["ordering_name"])
            document.sequence += 1
            rejected(document.save, frappe.PermissionError)

        tightened(
            [
                (NATIVE, "description", "read_only", 1, "Int"),
                (DOCTYPE, "sequence", "read_only", 1, "Int"),
            ],
            readonly_checks,
        )
        passed(current)

        current = "real_map_select_failure_rolls_back_new_native_operation_map_versions_callbacks"
        failing = payload("map-failure", sequence=2)

        def new_map_failure():
            rejected(lambda: save_operation(failing), atomic=True)
            assert not frappe.db.exists(NATIVE, failing["operation"])
            assert resolve_creation(failing)["status"] == "missing"

        tightened(
            [
                (DOCTYPE, "sequence", "fieldtype", "Select", "Data"),
                (DOCTYPE, "sequence", "options", "1", "Text"),
            ],
            new_map_failure,
        )
        passed(current)

        current = "real_map_mandatory_failure_rolls_back_prior_native_description_save"
        frappe.set_user(manager)
        unmapped = insert(
            NATIVE, name=prefix + "-unmapped-failure", description="原生备注不变"
        )
        unmapped_view = native(lambda: read_operation(unmapped.name))
        unmapped_hash = _document_snapshot(NATIVE, unmapped.name)

        def existing_map_failure():
            rejected(
                lambda: update(
                    unmapped_view, remarks="应回滚的真实原生修改", sequence=19
                ),
                frappe.MandatoryError,
                atomic=True,
            )
            assert _document_snapshot(NATIVE, unmapped.name) == unmapped_hash
            assert not frappe.db.exists(DOCTYPE, {"operation": unmapped.name})

        tightened([(DOCTYPE, "request_id", "reqd", 1, "Int")], existing_map_failure)
        passed(current)

        current = "native_total_time_recalculation_of_unrequested_source_is_rejected_atomically"
        frappe.set_user("Administrator")
        nested = "jf_operation_total_" + frappe.generate_hash(length=8)
        callbacks, context = _callbacks(), _local_context()
        frappe.db.savepoint(nested)
        try:
            # Exact owned synthetic source only: emulate a legacy inconsistent
            # aggregate without patching the native controller or API.
            frappe.db.set_value(
                NATIVE, source.name, "total_operation_time", 91.5, update_modified=False
            )
            frappe.clear_document_cache(NATIVE, source.name)
            frappe.set_user(manager)
            inconsistent = native(lambda: read_operation(source.name))
            rejected(
                lambda: update(inconsistent, remarks="请求仅改备注"),
                text="未请求的其它属性变化",
                atomic=True,
            )
            assert (
                frappe.db.get_value(NATIVE, source.name, "total_operation_time") == 91.5
            )
            seq_only = update(inconsistent, sequence=20)
            assert seq_only["version"] == inconsistent["version"]
            assert (
                frappe.db.get_value(NATIVE, source.name, "total_operation_time") == 91.5
            )
        finally:
            frappe.set_user("Administrator")
            frappe.db.rollback(save_point=nested)
            frappe.clear_document_cache(NATIVE, source.name)
            _restore_callbacks(callbacks)
            _restore_local_context(context)
            frappe.set_user(manager)
        passed(current)

        current = (
            "pure_sales_role_has_no_manufacturing_grants_and_guest_all_endpoints_denied"
        )
        for user in (sales, "Guest"):
            frappe.set_user(user)
            assert not frappe.has_permission(NATIVE, "write", throw=False)
            assert not frappe.has_permission(DOCTYPE, "write", throw=False)
            for action in (
                init_operations,
                list_operations,
                lambda: read_operation(row["name"]),
                lambda: resolve_creation(original),
                lambda: save_operation(payload("denied")),
            ):
                rejected(action, frappe.PermissionError)
        evidence["read_only_role"] = (
            "No invented read-only manufacturing role; actual Sales User/Guest are denied"
        )
        passed(current)

        current = "strict_payload_names_nonnegative_int_range_and_no_parent_sequence_injection"
        frappe.set_user(manager)
        for changes in (
            {"operation": " 名称"},
            {"operation": "x" * 141},
            {"remarks": []},
            {"sequence": True},
            {"sequence": -1},
            {"sequence": MAX_SEQUENCE + 1},
            {"sequence": "1.0"},
            {"request_id": "bad"},
            {"company": company},
            {"work_order": "forged"},
            {"operation_id": "forged"},
        ):
            rejected(
                lambda changes=changes: save_operation(payload("invalid", **changes)),
                atomic=True,
            )
        for action in (
            lambda: list_operations(start=-1),
            lambda: list_operations(page_length=30),
        ):
            rejected(action)
        defaults = payload("native-default", remarks="", sequence=0)
        created = save(defaults)
        assert created["remarks"] == created["name"] and created["sequence"] == 0
        assert native(lambda: resolve_creation(defaults))["status"] == "found"
        passed(current)
    except Exception as error:
        primary = error
        error.add_note("共用工序原生验证失败门槛：" + current)
        error.add_note("已通过门槛：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:

        def collect():
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update(
                    (child.doctype, child.name)
                    for child in document.get_all_children()
                    if child.name
                )

        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks_before)

        def restore():
            clear_metadata()
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
                ("collect_owned_native_parent_and_child_names", collect),
                (
                    "native_full_savepoint_rollback_and_owned_cleanup_callbacks",
                    rollback,
                ),
                (
                    "invalidate_owned_user_document_metadata_masks_and_restore_caller",
                    restore,
                ),
                (
                    "all_owned_fixtures_absent",
                    lambda: all(not frappe.db.exists(dt, name) for dt, name in records),
                ),
                (
                    "native_account_permission_version_and_all_selected_contents_unchanged",
                    lambda: _contents() == before,
                ),
                (
                    "all_production_product_wage_parent_child_and_metadata_protected",
                    lambda: _protected() == protected_before,
                ),
                (
                    "whole_gl_and_sle_unchanged",
                    lambda: _ledger_snapshot() == ledger_before,
                ),
                (
                    "native_controller_and_source_files_unchanged",
                    lambda: _native_sources() == sources_before,
                ),
                (
                    "all_physical_content_schema_indexes_auth_globals_sequences_equal",
                    audit_physical,
                ),
                (
                    "original_callbacks_context_and_roles_restored",
                    lambda: (
                        _callbacks() == callbacks_before
                        and _local_context() == context_before
                        and frappe.session.user == original_user
                        and frappe.in_test == original_test
                        and sorted(frappe.get_roles(original_user)) == original_roles
                        and frappe.db._disable_transaction_control
                        == transaction_control
                    ),
                ),
                (
                    "owned_fixture_role_caches_invalidated",
                    lambda: all(
                        frappe.cache.hget("roles", user) is None for user in users
                    ),
                ),
            ],
            primary=primary,
        )
    checks.append(
        "complete_physical_auth_metadata_schema_indexes_sequences_versions_rollback"
    )
    return {
        "passed": True,
        "gate_count": len(checks),
        "checks": checks,
        "actual_roles": roles,
        "evidence": evidence,
        "business_data": "rolled_back",
        "physical_before": physical_before,
        "physical_after": physical_after,
        "native_source_sha256": sources_before,
        "production_product_wage_protected": _protected() == protected_before,
        "whole_ledger_before": ledger_before,
        "whole_ledger_after": _ledger_snapshot(),
        "gui_verified": False,
        "concurrency": "serial local rollback experiment; independent fresh snapshot required",
        "remaining": [
            "真实 A/B 页面操作",
            "旧工资和全生产单据完整链路",
            "旧导入/删除/打印含义",
        ],
    }
