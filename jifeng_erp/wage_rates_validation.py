"""Actual native historical quote/reference acceptance in one isolated clone.

Call smoke once directly after root's two owned DocTypes have been reloaded.
No connection, commit, schema reload, network or browser is performed here.
An independent fresh full-database snapshot remains a required external gate.
The single-transaction NOWAIT path is exercised without claiming concurrency.
"""

from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path
from uuid import uuid4

import frappe
from frappe.utils import add_days, today

from jifeng_erp.catalog_validation import (
    _profile_audit,
    _profile_restore_caller,
    _profile_rollback,
    _version_has,
)
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.order_customer_selection_validation import (
    _permission_response_state,
    _rows,
)
from jifeng_erp.order_operations_validation import _doctypes, _physical_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _document_snapshot, _sql_row_hash
from jifeng_erp.wage_rates import SCOPE, WAGE, choose_piece_rate, query_piece_rates
from jifeng_erp.wage_workbench import batch_status, get_entry_source, save_drafts


ISOLATED_SITE = "jifeng-plating-native-isolated.localhost"
ISOLATED_DATABASE = "jf_plap_isolated_20261002"
VALIDATION_COMPANIES = {"Jifeng ERP UI Validation", "Jifeng ERP Validation"}
ALLOCATION = "Jifeng Wage Allocation"
CHILD = "Jifeng Wage Allocation Employee"
REFERENCES = ("piece_rate_reference", "piece_rate_reference_version")
PROTECTED = {
    WAGE,
    ALLOCATION,
    "Additional Salary",
    "Salary Slip",
    "Salary Structure Assignment",
    "Employee",
    "Jifeng Employee Supplement",
    "Company",
    "Salary Component",
    "Item",
    "BOM",
    "Operation",
    "Workstation",
    "Work Order",
    "Job Card",
    "Jifeng Flow Order",
    "Jifeng Product Profile",
    "Customer",
    "Jifeng Customer Profile",
    "Sales Order",
    "Sales Invoice",
    "Delivery Note",
    "Stock Entry",
    "Stock Ledger Entry",
    "GL Entry",
    "Account",
    "Payment Ledger Entry",
    "Payment Entry",
    "UOM",
}
AUDIT = PROTECTED | {
    "User",
    "User Permission",
    "DocShare",
    "Version",
    "DocType",
    "Property Setter",
    "Custom Field",
    "Custom DocPerm",
    "Comment",
    "Activity Log",
    "Deleted Document",
    "File",
    "Email Queue",
    "Communication",
    "Notification Log",
    "Error Log",
}


def _contents():
    result = {doctype: _rows(doctype) for doctype in sorted(_doctypes(AUDIT))}
    for table in ("__Auth", "tabSeries", "__global_search"):
        result[table] = _sql_row_hash(
            frappe.db.sql("SELECT * FROM `" + table + "` ORDER BY 1", as_dict=True)
        )
    return result


def _protected(records=()):
    return {
        doctype: {
            "contents": _rows(doctype, records),
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
        for doctype in sorted(_doctypes(PROTECTED))
    }


def _sources():
    paths = {
        "history service": frappe.get_app_path("jifeng_erp", "wage_rates.py"),
        "reference validation": frappe.get_app_path(
            "jifeng_erp", "wage_rate_references.py"
        ),
        "native entry": frappe.get_app_path(
            "jifeng_erp",
            "jifeng_erp",
            "doctype",
            "jifeng_wage_entry",
            "jifeng_wage_entry.py",
        ),
        "allocation source": frappe.get_app_path("jifeng_erp", "wage_allocation.py"),
        "workbench": frappe.get_app_path("jifeng_erp", "wage_workbench.py"),
    }
    return {
        label: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for label, path in paths.items()
    }


def _stamp(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _restore_response(state):
    for field, item in state["local"].items():
        if item["present"]:
            setattr(frappe.local, field, deepcopy(item["value"]))
        elif hasattr(frappe.local, field):
            delattr(frappe.local, field)
    if state["error_message_present"]:
        frappe.flags.error_message = deepcopy(state["error_message"])
    else:
        frappe.flags.pop("error_message", None)


def _cancel_contents(document):
    """Independent complete persisted approval contents, excluding cancel audit.

    No business-field allowlist is shared with the guard under test. Native
    cancellation may change status, modifier and modification timestamps only.
    """
    assert document.doctype in {WAGE, ALLOCATION}
    audit = {"docstatus", "modified", "modified_by"}
    parents = frappe.db.sql(
        "SELECT * FROM `tab" + document.doctype + "` WHERE name=%s",
        (document.name,),
        as_dict=True,
    )
    assert len(parents) == 1
    result = {
        "parent": _sql_row_hash(
            [{key: value for key, value in parents[0].items() if key not in audit}]
        )
    }
    for field in document.meta.get_table_fields():
        rows = frappe.db.sql(
            "SELECT * FROM `tab"
            + field.options.replace("`", "``")
            + "` WHERE parent=%s AND parenttype=%s AND parentfield=%s ORDER BY name",
            (document.name, document.doctype, field.fieldname),
            as_dict=True,
        )
        result[field.fieldname] = _sql_row_hash(
            [
                {key: value for key, value in row.items() if key not in audit}
                for row in rows
            ]
        )
    return result


def smoke(company, other_company, item_group, stock_uom):
    assert (
        frappe.local.site == ISOLATED_SITE and frappe.conf.db_name == ISOLATED_DATABASE
    )
    assert frappe.db.db_type == "mariadb"
    assert (
        int(frappe.conf.maintenance_mode or 0)
        == int(frappe.conf.pause_scheduler or 0)
        == 1
    )
    frappe.only_for("System Manager")
    assert {company, other_company} == VALIDATION_COMPANIES and company != other_company
    for doctype, name in (
        ("Company", company),
        ("Company", other_company),
        ("Item Group", item_group),
        ("UOM", stock_uom),
    ):
        assert frappe.db.exists(doctype, name)
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    assert SCOPE == "wage_piece_rates"
    for doctype in (WAGE, CHILD):
        for name in REFERENCES:
            field = frappe.get_meta(doctype).get_field(name)
            assert (
                field
                and field.fieldtype == "Data"
                and field.no_copy
                and field.read_only
            )
            assert not field.allow_on_submit and not field.unique
    physical_before = _physical_snapshot()
    assert len(physical_before) == 931
    contents_before, protected_before, ledger_before, sources_before = (
        _contents(),
        _protected(),
        _ledger_snapshot(),
        _sources(),
    )
    callbacks_before, context_before = _callbacks(), _local_context()
    response_before = _permission_response_state()
    original_user, original_test = frappe.session.user, frappe.in_test
    original_roles = sorted(frappe.get_roles(original_user))
    transaction_control = frappe.db._disable_transaction_control
    prefix = "JF-QA-RATES-" + frappe.generate_hash(length=10)
    point = "jf_rates_" + frappe.generate_hash(length=8)
    objects, users, records, checks, roles, changed_meta = [], [], set(), [], {}, set()
    evidence, primary, current, physical_after = {}, None, "fixture_setup", None
    _begin_rollback_only(point)

    def native(action):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = previous

    def savedocs_cancel(document, changes=None, mutate=None, redacted=False):
        """Call the real JSON Cancel endpoint with isolated native response state."""
        from frappe.desk.form.save import savedocs

        frappe.is_whitelisted(savedocs)
        assert "POST" in frappe.allowed_http_methods_for_whitelisted_func[savedocs]
        original_response = frappe.local.response
        original_contents = deepcopy(original_response)
        response_state = _permission_response_state()
        monitor = getattr(frappe.local, "monitor", None)
        monitor_data = deepcopy(monitor.data) if monitor else None
        source_document = frappe.get_doc(document.doctype, document.name)
        qualification_fields = (
            "piece_worker_qualification_source",
            "piece_worker_qualification_version",
        )
        redaction = None
        if redacted:
            assert frappe.session.user == manager
            assert 1 not in source_document.get_permlevel_access("read")
            rows = (
                [source_document]
                if document.doctype == WAGE
                else source_document.employees
            )
            protected_snapshots = sum(
                bool(row.get(field)) for row in rows for field in qualification_fields
            )
            native(source_document.apply_fieldlevel_read_permissions)
            assert all(
                field not in row.__dict__
                for row in rows
                for field in qualification_fields
            )
            redaction = {
                "doctype": document.doctype,
                "name": document.name,
                "hidden_fields": list(qualification_fields),
                "nonempty_original_values": protected_snapshots,
            }
        payload = source_document.as_dict()
        if redacted:
            payload_rows = (
                [payload] if document.doctype == WAGE else payload["employees"]
            )
            assert all(
                row.get(field) is None
                for row in payload_rows
                for field in qualification_fields
            )
        payload.update(changes or {})
        if mutate:
            mutate(payload)
        try:
            frappe.local.response = frappe._dict(docs=[])
            native(lambda: savedocs(doc=frappe.as_json(payload), action="Cancel"))
            assert len(frappe.response.docs) == 1
            assert frappe.response.docs[0].name == document.name
            assert frappe.response.docs[0].doctype == document.doctype
            assert frappe.response.docs[0].docstatus == 2
            assert frappe.response.docinfo.name == document.name
            if redacted:
                response_rows = (
                    [frappe.response.docs[0]]
                    if document.doctype == WAGE
                    else frappe.response.docs[0].employees
                )
                assert all(
                    row.get(field) is None
                    for row in response_rows
                    for field in qualification_fields
                )
                evidence.setdefault("native_redacted_cancel_payloads", []).append(
                    redaction
                )
        finally:
            frappe.local.response = original_response
            _restore_response(response_state)
            if monitor:
                monitor.data = monitor_data
            assert (
                frappe.local.response is original_response
                and frappe.local.response == original_contents
            )

    def normal_cancel(document):
        before = _cancel_contents(document)
        savedocs_cancel(document, redacted=True)
        document.reload()
        assert document.docstatus == 2 and _cancel_contents(document) == before

    def remember(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        records.update(
            (row.doctype, row.name) for row in document.get_all_children() if row.name
        )
        return document

    def insert(doctype, **values):
        document = remember(frappe.get_doc({"doctype": doctype, **values}))
        native(document.insert)
        return remember(document)

    def pure(action):
        before, callbacks, local, messages = (
            _contents(),
            _callbacks(),
            _local_context(),
            _permission_response_state(),
        )
        result = native(action)
        assert _contents() == before, "历史工价查询/选择写入了持久数据"
        assert _callbacks() == callbacks and _local_context() == local
        assert _permission_response_state() == messages, (
            "成功工价查询/过滤产生了权限提示"
        )
        return result

    def rejected(action, errors=frappe.ValidationError, on_error=None):
        before, callbacks, local = _contents(), _callbacks(), _local_context()
        previous_user, previous_test = frappe.session.user, frappe.in_test
        nested = "jf_rates_reject_" + frappe.generate_hash(length=8)
        failure = None
        _begin_rollback_only(nested)
        try:
            try:
                native(action)
            except errors as error:
                if on_error:
                    on_error(error)
            else:
                raise AssertionError("本应拒绝的工价请求被接受：" + current)
        except Exception as error:
            failure = error
            raise
        finally:

            def rollback():
                frappe.set_user("Administrator")
                _profile_rollback(nested, callbacks)

            _profile_audit(
                [
                    ("reject_native_savepoint_rollback", rollback),
                    (
                        "reject_restore_caller_callbacks",
                        lambda: _profile_restore_caller(
                            previous_user, previous_test, [], set(), callbacks, local
                        ),
                    ),
                    ("reject_all_contents_restored", lambda: _contents() == before),
                ],
                primary=failure,
            )

    def actor(label, selected_role, scope=company, permission=None):
        user = insert(
            "User",
            email=prefix.lower() + "-" + label + "@example.invalid",
            first_name=prefix + "-" + label,
            send_welcome_email=0,
            roles=[{"role": selected_role}],
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
                apply_to_all_doctypes=0,
                applicable_for="Job Card",
            )
        frappe.clear_cache(user=user.name)
        roles[label] = sorted(frappe.get_roles(user.name))
        assert selected_role in roles[label] and "System Manager" not in roles[label]
        return user.name

    def passed(label):
        assert _protected(records) == protected_before, "改变了既有业务行或元数据"
        assert _ledger_snapshot() == ledger_before and _sources() == sources_before
        assert label not in checks
        checks.append(label)

    def clear_metadata(doctypes=None):
        for doctype in doctypes or changed_meta:
            frappe.clear_cache(doctype=doctype)
            for user in {*users, original_user, "Administrator"}:
                parent = ALLOCATION if doctype == CHILD else ""
                frappe.cache.delete_value(f"masked_fields::{doctype}::{parent}::{user}")

    def tightened(doctype, field, prop, value, action):
        before, callbacks, local = _contents(), _callbacks(), _local_context()
        previous_user, previous_test = frappe.session.user, frappe.in_test
        nested = "jf_rates_tighten_" + frappe.generate_hash(length=8)
        failure = None
        _begin_rollback_only(nested)
        try:
            frappe.set_user("Administrator")
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
                property_type="Check",
                is_system_generated=0,
            )
            changed_meta.add(doctype)
            clear_metadata({doctype})
            frappe.set_user(clerk)
            action()
        except Exception as error:
            failure = error
            raise
        finally:

            def rollback():
                frappe.set_user("Administrator")
                _profile_rollback(nested, callbacks)

            def restore():
                clear_metadata({doctype})
                _profile_restore_caller(
                    previous_user, previous_test, [], set(), callbacks, local
                )

            _profile_audit(
                [
                    ("tighten_exact_native_rollback", rollback),
                    ("tighten_restore_metadata_and_context", restore),
                    ("tighten_contents_unchanged", lambda: _contents() == before),
                ],
                primary=failure,
            )

    try:
        frappe.set_user("Administrator")
        gender = frappe.db.get_value("Gender", {}, "name")
        warehouses = {
            scope: frappe.db.get_value(
                "Warehouse", {"company": scope, "is_group": 0, "disabled": 0}, "name"
            )
            for scope in VALIDATION_COMPANIES
        }
        assert gender and all(warehouses.values())
        employees = [
            insert(
                "Employee",
                first_name=prefix + "-EMP-" + str(index),
                company=company,
                gender=gender,
                date_of_birth="1990-01-01",
                date_of_joining=add_days(today(), -30),
                status="Active",
            )
            for index in range(2)
        ]
        foreign_employee = insert(
            "Employee",
            first_name=prefix + "-FOREIGN",
            company=other_company,
            gender=gender,
            date_of_birth="1990-01-01",
            date_of_joining=add_days(today(), -30),
            status="Active",
        )
        for person in [*employees, foreign_employee]:
            insert(
                "Jifeng Employee Supplement", employee=person.name, wage_method="计件"
            )
        component = insert(
            "Salary Component",
            salary_component=prefix + "-PAY",
            salary_component_abbr="R" + prefix[-8:],
            type="Earning",
            depends_on_payment_days=0,
            is_tax_applicable=0,
        )
        alternate_component = insert(
            "Salary Component",
            salary_component=prefix + "-PAY2",
            salary_component_abbr="S" + prefix[-8:],
            type="Earning",
            depends_on_payment_days=0,
            is_tax_applicable=0,
        )
        station = insert(
            "Workstation", workstation_name=prefix + "-WS", production_capacity=1
        )
        operation = insert("Operation", name=prefix + "-OP", workstation=station.name)
        alternate_operation = insert(
            "Operation", name=prefix + "-OP2", workstation=station.name
        )
        alternate_uom = insert("UOM", uom_name=prefix + "-UNIT", must_be_whole_number=0)
        settings = frappe.get_single("Manufacturing Settings")
        settings.disable_capacity_planning = 1
        native(settings.save)

        def materials(label, unit):
            return [
                insert(
                    "Item",
                    item_code=prefix + "-" + label + "-" + kind,
                    item_group=item_group,
                    stock_uom=unit,
                )
                for kind in ("RAW", "FG")
            ]

        raw, finished = materials("MAIN", stock_uom)
        alternate_raw, alternate_finished = materials("UNIT", alternate_uom.name)

        def bom(
            scope,
            selected_operation,
            selected_raw=raw,
            selected_finished=finished,
            unit=stock_uom,
        ):
            document = insert(
                "BOM",
                company=scope,
                item=selected_finished.name,
                quantity=1,
                is_default=0,
                with_operations=1,
                transfer_material_against="Work Order",
                items=[{"item_code": selected_raw.name, "qty": 1, "rate": 1}],
                operations=[
                    {
                        "operation": selected_operation.name,
                        "workstation": station.name,
                        "time_in_mins": 1,
                    }
                ],
            )
            native(document.submit)
            return document, selected_raw, selected_finished, unit

        main_bom = bom(company, operation)
        second_bom = bom(company, alternate_operation)
        unit_bom = bom(
            company, operation, alternate_raw, alternate_finished, alternate_uom.name
        )
        foreign_bom = bom(other_company, operation)

        def card(label, hour, people, selected=main_bom, scope=company, day=None):
            day = day or today()
            selected_bom, selected_raw, selected_finished, unit = selected
            finished_item = frappe.get_doc("Item", selected_bom.item)
            raw_item = frappe.get_doc("Item", selected_raw.name)
            assert finished_item.name == selected_finished.name
            assert finished_item.stock_uom == unit
            flow = insert(
                "Jifeng Flow Order",
                company=scope,
                item_code=selected_finished.name,
                planned_qty=5,
                stock_uom=finished_item.stock_uom,
                bom=selected_bom.name,
                materials=[
                    {
                        "item_code": selected_raw.name,
                        "uom": raw_item.stock_uom,
                        "qty_per_unit": 1,
                        "unit_price": 1,
                    }
                ],
            )
            work = remember(frappe.get_doc("Work Order", native(flow.make_work_order)))
            work.source_warehouse = work.fg_warehouse = warehouses[scope]
            work.skip_transfer = 1
            work.planned_start_date = day + f" {hour:02d}:00:00"
            native(work.save)
            native(work.submit)
            names = frappe.get_all(
                "Job Card",
                filters={
                    "work_order": work.name,
                    "operation": selected_bom.operations[0].operation,
                },
                pluck="name",
            )
            assert len(names) == 1, label
            source = remember(frappe.get_doc("Job Card", names[0]))
            source.posting_date = day
            native(source.save)
            native(
                lambda: source.start_timer(
                    start_time=day + f" {hour:02d}:00:00",
                    employees=[{"employee": person.name} for person in people],
                )
            )
            source.reload()
            native(
                lambda: source.complete_job_card(
                    qty=5,
                    for_quantity=5,
                    pending_qty=0,
                    process_loss_qty=0,
                    end_time=day + f" {hour + 1:02d}:00:00",
                )
            )
            source.reload()
            native(source.submit)
            assert source.stock_uom == finished_item.stock_uom
            return remember(source)

        past = add_days(today(), -2)
        history_card = card("history", 8, employees[:1], day=past)
        history_card_two = card("second employee", 10, employees[1:], day=past)
        other_operation_card = card(
            "other operation", 12, employees[:1], selected=second_bom, day=past
        )
        other_unit_card = card(
            "other unit", 14, employees[:1], selected=unit_bom, day=past
        )
        foreign_card = card(
            "foreign",
            16,
            [foreign_employee],
            selected=foreign_bom,
            scope=other_company,
            day=past,
        )
        current_card = card("current", 8, employees[:1])
        team_card = card("team", 12, employees)
        field_team_card = card("field-team", 16, employees)
        manager, clerk = actor("manager", "HR Manager"), actor("clerk", "HR User")
        readonly_actor = actor("accounts", "Accounts User")
        restricted = actor(
            "hidden-history", "HR User", permission=("Job Card", current_card.name)
        )
        foreign_actor = actor("foreign", "HR User", scope=other_company)

        def wage(
            source,
            person=employees[0],
            qty="0.001",
            rate=0,
            extra=1,
            day=past,
            scope=company,
            submit=True,
        ):
            document = insert(
                WAGE,
                company=scope,
                employee=person.name,
                operation=source.operation,
                job_card=source.name,
                work_date=day,
                payroll_date=max(day, today()),
                quantity=qty,
                stock_uom=source.stock_uom,
                piece_rate=rate,
                extra_amount=extra,
                salary_component=component.name,
                notes=prefix,
            )
            if submit:
                native(document.submit)
            return remember(document)

        frappe.set_user(manager)
        assert not frappe.has_permission("Job Card", "write", doc=current_card)
        assert not frappe.has_permission("Operation", "write")
        history = [wage(history_card, rate="2.5"), wage(history_card, rate=0)]
        for _ in range(200):
            history.append(wage(history_card))
        other_person = wage(history_card_two, person=employees[1], rate=4)
        other_operation = wage(other_operation_card, rate=8)
        other_unit = wage(other_unit_card, rate=9)
        future = wage(history_card, rate=6, day=add_days(today(), 1))
        draft = wage(history_card, rate=7, submit=False)
        cancelled = wage(history_card, rate=10)
        native(cancelled.cancel)
        frappe.set_user("Administrator")
        foreign = wage(
            foreign_card, person=foreign_employee, rate=11, scope=other_company
        )
        frappe.set_user(manager)

        def context(source=current_card, person=employees[0], day=None, scope=company):
            identity = pure(lambda: get_entry_source(source.name, scope, person.name))
            return {
                "company": scope,
                "employee": person.name,
                "operation": identity["operation"],
                "work_date": day or today(),
                "stock_uom": identity["stock_uom"],
                "job_card": source.name,
                "source_modified": identity["source_modified"],
                "work_order_modified": identity["work_order_modified"],
            }

        target_context = context()

        def listing(target=None, **kwargs):
            return pure(lambda: query_piece_rates(target or target_context, **kwargs))

        def choice(document, target=None, **values):
            return pure(
                lambda: choose_piece_rate(
                    target or target_context,
                    document.name,
                    values.get("version", _stamp(document.modified)),
                    values.get("piece_rate", document.piece_rate),
                )
            )

        current = "pure_hr_manager_and_hr_user_complete_202_history_zero_rate_no_writes_or_permission_messages"
        for user in (manager, clerk):
            frappe.set_user(user)
            assert frappe.has_permission(WAGE, "read") and frappe.has_permission(
                WAGE, "create"
            )
            first_page, third_page = (
                listing(page_size=100),
                listing(page=3, page_size=100),
            )
            assert first_page["scope"] == SCOPE and first_page["readonly"]
            assert (
                first_page["total"] == 202
                and first_page["total_pages"] == 3
                and len(third_page["rows"]) == 2
            )
            assert set(first_page["rows"][0]) == {
                "name",
                "version",
                "work_date",
                "stock_uom",
                "piece_rate",
                "company",
                "employee",
                "operation",
            }
            assert choice(history[1])["piece_rate"] == "0"
            assert Decimal(choice(history[0])["piece_rate"]) == Decimal("2.5")
        evidence["history_rows"] = 202
        passed(current)

        current = "exact_employee_operation_unit_date_company_and_approval_scope"
        visible = {
            row["name"]
            for page in (1, 2, 3)
            for row in listing(page=page, page_size=100)["rows"]
        }
        assert visible == {document.name for document in history}
        for document in (
            other_person,
            other_operation,
            other_unit,
            future,
            draft,
            cancelled,
            foreign,
        ):
            rejected(lambda selected=document: choice(selected), frappe.PermissionError)
        for field, value in (
            ("operation", alternate_operation.name),
            ("stock_uom", alternate_uom.name),
            ("company", other_company),
        ):
            rejected(
                lambda key=field, selected=value: listing(
                    {**target_context, key: selected}
                ),
                frappe.PermissionError,
            )
        passed(current)

        current = (
            "historical_jobcard_user_permission_filters_without_identity_or_prompt"
        )
        frappe.set_user(restricted)
        assert frappe.get_list(
            WAGE,
            filters={"name": history[0].name},
            fields=["name"],
            limit_page_length=1,
        )
        result = listing()
        assert result["total"] == 0 and result["rows"] == []
        assert history[0].name not in str(result) and history_card.name not in str(
            result
        )
        rejected(lambda: choice(history[0]), frappe.PermissionError)
        frappe.set_user(foreign_actor)
        rejected(listing, frappe.PermissionError)
        frappe.set_user(readonly_actor)
        rejected(listing, frappe.PermissionError)
        frappe.set_user(manager)
        passed(current)

        current = "exact_history_version_price_and_current_source_version_rejections"
        rejected(lambda: choice(history[0], version="2000-01-01T00:00:00"))
        rejected(lambda: choice(history[0], piece_rate="2.500001"))
        rejected(
            lambda: listing(
                {**target_context, "source_modified": "2000-01-01T00:00:00"}
            )
        )
        rejected(
            lambda: listing(
                {**target_context, "work_order_modified": "2000-01-01T00:00:00"}
            )
        )
        passed(current)

        def entry_values(source=current_card, qty=1, quote=history[0]):
            return {
                "company": company,
                "employee": employees[0].name,
                "operation": source.operation,
                "job_card": source.name,
                "work_date": today(),
                "payroll_date": today(),
                "quantity": qty,
                "stock_uom": source.stock_uom,
                "piece_rate": quote.piece_rate,
                "salary_component": component.name,
                "extra_amount": 1,
                "piece_rate_reference": quote.name,
                "piece_rate_reference_version": _stamp(quote.modified),
            }

        current = "native_draft_reference_save_version_and_submit_snapshot"
        values = entry_values()
        quoted = insert(
            WAGE,
            **{key: value for key, value in values.items() if key not in REFERENCES},
        )
        quoted.piece_rate_reference, quoted.piece_rate_reference_version = (
            values[REFERENCES[0]],
            values[REFERENCES[1]],
        )
        native(quoted.save)
        quoted.reload()
        assert _version_has(quoted, "piece_rate_reference") and _version_has(
            quoted, "piece_rate_reference_version"
        )
        assert quoted.piece_rate_reference == history[0].name and Decimal(
            str(quoted.piece_amount)
        ) == Decimal("2.5")
        native(quoted.submit)
        assert quoted.docstatus == 1
        evidence["native_single_reference"] = {
            "target": quoted.name,
            "source": history[0].name,
            "source_version": values[REFERENCES[1]],
        }
        passed(current)

        current = "native_manual_price_change_with_old_reference_rejected"
        quote_draft = insert(WAGE, **entry_values(qty="0.5"))

        def change_price():
            document = frappe.get_doc(WAGE, quote_draft.name)
            document.piece_rate = Decimal("3")
            document.save()

        rejected(change_price)
        assert Decimal(
            str(frappe.get_doc(WAGE, quote_draft.name).piece_rate)
        ) == Decimal("2.5")
        passed(current)

        current = "source_version_change_rejects_new_reference_with_original_snapshot"
        old_version = _stamp(history[0].modified)
        document = frappe.get_doc(WAGE, history[0].name)
        # No update-after-submit field is forced. Native notes may be immutable;
        # a native same-value save still creates a later modified timestamp.
        document.notes = history[0].notes
        native(document.save)
        history[0].reload()
        assert _stamp(history[0].modified) != old_version
        rejected(
            lambda: insert(
                WAGE,
                **{
                    **entry_values(qty="0.1"),
                    "piece_rate_reference_version": old_version,
                },
            )
        )
        passed(current)

        current = (
            "twenty_row_workbench_last_reference_failure_rolls_back_first_nineteen"
        )
        identity = context()
        rows = []
        for _ in range(19):
            rows.append(
                {
                    "work_date": today(),
                    "operation": operation.name,
                    "job_card": "",
                    "source_modified": "",
                    "work_order_modified": "",
                    "quantity": 0,
                    "stock_uom": stock_uom,
                    "piece_rate": 0,
                    "hours": 1,
                    "hourly_rate": 1,
                    "extra_amount": 1,
                    "notes": prefix,
                }
            )
        rows.append(
            {
                "work_date": today(),
                "operation": operation.name,
                "job_card": current_card.name,
                "source_modified": identity["source_modified"],
                "work_order_modified": identity["work_order_modified"],
                "quantity": "0.01",
                "stock_uom": stock_uom,
                "piece_rate": history[0].piece_rate,
                "piece_rate_reference": history[0].name,
                "piece_rate_reference_version": "2000-01-01T00:00:00",
                "hours": 0,
                "hourly_rate": 0,
                "extra_amount": 1,
                "notes": prefix,
            }
        )
        request = {
            "request_id": str(uuid4()),
            "company": company,
            "employee": employees[0].name,
            "payroll_date": today(),
            "salary_component": component.name,
            "rows": rows,
        }

        def last_reference_insert_failed(error):
            frames = []
            traceback = error.__traceback__
            while traceback:
                frames.append(traceback.tb_frame)
                traceback = traceback.tb_next
            workbench_frames = [
                frame
                for frame in frames
                if frame.f_globals.get("__name__") == "jifeng_erp.wage_workbench"
                and frame.f_code.co_name == "save_drafts"
            ]
            assert len(workbench_frames) == 1
            local = workbench_frames[0].f_locals
            prepared = local["prepared"]
            assert len(prepared) == 20 and local["doc"] is prepared[-1]
            assert all(
                document.name and not document.is_new() for document in prepared[:19]
            )
            assert prepared[-1].is_new() and prepared[-1].flags.in_insert
            assert all(
                not document.job_card and not document.piece_rate_reference
                for document in prepared[:19]
            )
            assert prepared[-1].piece_rate_reference == history[0].name
            assert prepared[-1].piece_rate_reference_version == "2000-01-01T00:00:00"
            assert any(
                frame.f_globals.get("__name__") == "jifeng_erp.wage_rate_references"
                and frame.f_code.co_name == "validate_wage_reference"
                and frame.f_locals.get("wage") is prepared[-1]
                for frame in frames
            )
            evidence["twenty_row_failure_observed"] = {
                "source": "native exception traceback save_drafts prepared/doc",
                "completed_native_inserts": 19,
                "failing_row": 20,
                "failing_row_still_new": True,
                "failure_inside_reference_validation": True,
            }

        rejected(lambda: save_drafts(request), on_error=last_reference_insert_failed)
        assert not frappe.get_all(
            WAGE,
            filters={"workbench_request_key": ["like", request["request_id"] + ":%"]},
        )
        passed(current)

        current = "approved_allocation_two_person_exact_individual_quote_snapshots"
        allocation = insert(
            ALLOCATION,
            job_card=team_card.name,
            payroll_date=today(),
            salary_component=component.name,
            employees=[
                {
                    "employee": person.name,
                    "quantity": quantity,
                    "piece_rate": quote.piece_rate,
                    "piece_rate_reference": quote.name,
                    "piece_rate_reference_version": _stamp(quote.modified),
                }
                for person, quantity, quote in zip(
                    employees, (2, 3), (history[0], other_person), strict=True
                )
            ],
        )
        native(allocation.submit)
        allocation.reload()
        assert allocation.docstatus == 1 and Decimal(
            str(allocation.allocated_qty)
        ) == Decimal(5)
        assert [row.piece_rate_reference for row in allocation.employees] == [
            history[0].name,
            other_person.name,
        ]
        original_generated = native(allocation.make_wages)
        assert len(original_generated) == 2
        for name in original_generated:
            remember(frappe.get_doc(WAGE, name))
            native(lambda selected=name: frappe.delete_doc(WAGE, selected))
        passed(current)

        current = "whitelisted_json_cancel_rejects_financial_source_and_all_row_tampering_with_and_without_references"
        timing = insert(
            WAGE,
            company=company,
            employee=employees[0].name,
            operation=operation.name,
            work_date=today(),
            payroll_date=today(),
            salary_component=component.name,
            quantity=0,
            piece_rate=0,
            hours=1,
            hourly_rate=5,
            extra_amount=1,
        )
        native(timing.submit)
        assert timing.docstatus == 1 and Decimal(str(timing.total_amount)) == Decimal(6)
        assert (
            not timing.piece_rate_reference and not timing.piece_rate_reference_version
        )
        receipt_request = {
            **request,
            "request_id": str(uuid4()),
            "rows": [
                {
                    **rows[-1],
                    "piece_rate_reference_version": _stamp(history[0].modified),
                }
            ],
        }
        receipt_result = native(lambda: save_drafts(receipt_request))
        assert not receipt_result["reused"] and len(receipt_result["wages"]) == 1
        receipt = remember(frappe.get_doc(WAGE, receipt_result["wages"][0]["name"]))
        native(receipt.submit)
        receipt.reload()
        assert receipt.docstatus == 1
        assert receipt.workbench_request_key == receipt_request["request_id"] + ":000"
        assert (
            receipt.workbench_request_fingerprint
            == receipt_result["wages"][0]["fingerprint"]
        )
        assert len(receipt.workbench_request_fingerprint) == 64
        replacement_key = str(uuid4()) + ":000"
        replacement_fingerprint = hashlib.sha256(
            (prefix + "-replacement").encode()
        ).hexdigest()
        wage_changes = {
            "hours": 2,
            "hourly_rate": 7,
            "extra_amount": 3,
            "piece_amount": 9,
            "time_amount": 9,
            "total_amount": 11,
            "salary_component": alternate_component.name,
            "payroll_date": add_days(today(), 1),
            "job_card": history_card.name,
            "operation": alternate_operation.name,
            "quantity": 2,
            "piece_rate": 3,
            "employee_name": prefix + "-OTHER-NAME",
            "workbench_request_key": replacement_key,
            "workbench_request_fingerprint": replacement_fingerprint,
            "amended_from": cancelled.name,
        }
        for target in (quoted, timing):
            for field, value in wage_changes.items():
                rejected(
                    lambda selected=target, key=field, replacement=value: (
                        savedocs_cancel(selected, {key: replacement})
                    )
                )
        for field, value in {
            "workbench_request_key": replacement_key,
            "workbench_request_fingerprint": replacement_fingerprint,
        }.items():
            for replacement in ("", value):
                rejected(
                    lambda key=field, changed=replacement: savedocs_cancel(
                        receipt, {key: changed}
                    )
                )
        mixed_allocation = insert(
            ALLOCATION,
            job_card=field_team_card.name,
            payroll_date=today(),
            salary_component=component.name,
            employees=[
                {
                    "employee": employees[0].name,
                    "quantity": 2,
                    "piece_rate": 0,
                    "extra_amount": 1,
                    "piece_rate_reference": history[1].name,
                    "piece_rate_reference_version": _stamp(history[1].modified),
                },
                {
                    "employee": employees[1].name,
                    "quantity": 3,
                    "piece_rate": 4,
                    "extra_amount": 1,
                },
            ],
        )
        native(mixed_allocation.submit)
        mixed_allocation.reload()
        assert mixed_allocation.employees[0].piece_rate_reference
        assert not mixed_allocation.employees[1].piece_rate_reference
        mixed_generated = native(mixed_allocation.make_wages)
        assert len(mixed_generated) == 2
        for name in mixed_generated:
            remember(frappe.get_doc(WAGE, name))
            native(lambda selected=name: frappe.delete_doc(WAGE, selected))
        for target in (allocation, mixed_allocation):
            assert not frappe.get_all(
                WAGE, filters={"wage_allocation": target.name, "docstatus": ["<", 2]}
            )
            for field, value in {
                "total_amount": 999,
                "allocated_qty": 4,
                "payroll_date": add_days(today(), 1),
                "salary_component": alternate_component.name,
                "source_completed_qty": 9,
                "job_card": current_card.name,
                "work_order": current_card.work_order,
                "amended_from": (
                    mixed_allocation if target is allocation else allocation
                ).name,
            }.items():
                rejected(
                    lambda selected=target, key=field, replacement=value: (
                        savedocs_cancel(selected, {key: replacement})
                    )
                )
            for index in range(2):
                row_changes = {
                    "include_time_pay": 1,
                    "hours": 2,
                    "wage_hours": 2,
                    "hourly_rate": 7,
                    "extra_amount": 3,
                    "piece_amount": 9,
                    "time_amount": 9,
                    "total_amount": 999,
                    "quantity": 4,
                    "piece_rate": 9,
                    "wage_entry": timing.name,
                    "employee": employees[1 - index].name,
                    "employee_name": prefix + "-OTHER-NAME",
                }
                for field, value in row_changes.items():

                    def mutate_row(
                        payload, position=index, key=field, replacement=value
                    ):
                        payload["employees"][position][key] = replacement

                    rejected(
                        lambda selected=target, mutation=mutate_row: savedocs_cancel(
                            selected, mutate=mutation
                        )
                    )

                def remove_row(payload, position=index):
                    payload["employees"].pop(position)

                def replace_row(payload, position=index):
                    payload["employees"][position]["name"] = (
                        prefix + "-REPLACEMENT-" + str(position)
                    )

                rejected(
                    lambda selected=target, mutation=remove_row: savedocs_cancel(
                        selected, mutate=mutation
                    )
                )
                rejected(
                    lambda selected=target, mutation=replace_row: savedocs_cancel(
                        selected, mutate=mutation
                    )
                )

            def add_row(payload):
                extra = deepcopy(payload["employees"][0])
                extra["name"] = None
                extra["__islocal"] = 1
                extra["idx"] = 3
                payload["employees"].append(extra)

            rejected(lambda selected=target: savedocs_cancel(selected, mutate=add_row))
        normal_cancel(timing)
        normal_cancel(mixed_allocation)
        normal_cancel(receipt)
        recovered = pure(
            lambda: batch_status(
                receipt_request["request_id"], company, employees[0].name
            )
        )
        assert len(recovered["wages"]) == 1
        assert recovered["wages"][0] == {
            **receipt_result["wages"][0],
            "docstatus": 2,
        }
        assert frappe.get_doc(WAGE, quoted.name).docstatus == 1
        assert frappe.get_doc(ALLOCATION, allocation.name).docstatus == 1
        evidence["cancel_endpoint"] = "frappe.desk.form.save.savedocs"
        evidence["cancel_payload"] = (
            "native JSON action=Cancel; referenced and manual timing wages; all referenced/manual allocation rows"
        )
        evidence["normal_cancel_full_persisted_financial_contents_preserved"] = True
        evidence[
            "cancel_batch_receipt_clear_replace_rejected_and_recovery_preserved"
        ] = True
        passed(current)

        current = "source_cancel_does_not_recompute_approved_quote_or_allocation_generated_wage"
        quoted_snapshot = _document_snapshot(WAGE, quoted.name)
        native(history[0].cancel)
        native(other_person.cancel)
        rejected(lambda: choice(history[0]), frappe.PermissionError)
        rejected(
            lambda: insert(WAGE, **entry_values(qty="0.1")), frappe.PermissionError
        )
        assert _document_snapshot(WAGE, quoted.name) == quoted_snapshot
        generated = native(allocation.make_wages)
        assert len(generated) == 2 and generated == native(allocation.make_wages)
        generated_wages = [remember(frappe.get_doc(WAGE, name)) for name in generated]
        for target, row in zip(generated_wages, allocation.employees, strict=True):
            assert target.piece_rate_reference == row.piece_rate_reference
            assert (
                target.piece_rate_reference_version == row.piece_rate_reference_version
            )
            assert Decimal(str(target.piece_rate)) == Decimal(str(row.piece_rate))
            native(target.submit)
            normal_cancel(target)
        allocation.reload()
        normal_cancel(allocation)
        normal_cancel(quoted)
        passed(current)

        def amend(document):
            revised = frappe.copy_doc(document)
            revised.docstatus = 0
            for child in revised.get_all_children():
                child.docstatus = 0
            revised.amended_from = document.name
            remember(revised)
            native(revised.insert)
            return remember(revised)

        current = "native_amendment_clears_wage_and_allocation_history_references"
        revised_wage = amend(receipt)
        assert (
            not revised_wage.piece_rate_reference
            and not revised_wage.piece_rate_reference_version
        )
        assert not revised_wage.workbench_request_key
        assert not revised_wage.workbench_request_fingerprint
        revised_allocation = amend(allocation)
        assert all(
            not row.piece_rate_reference and not row.piece_rate_reference_version
            for row in revised_allocation.employees
        )
        assert all(not row.wage_entry for row in revised_allocation.employees)
        native(revised_wage.submit)
        native(revised_allocation.submit)
        revised_wage.reload()
        revised_allocation.reload()
        amended_generated = native(revised_allocation.make_wages)
        assert len(amended_generated) == 2
        for name in amended_generated:
            remember(frappe.get_doc(WAGE, name))
            native(lambda selected=name: frappe.delete_doc(WAGE, selected))
        for target, original, replacement in (
            (revised_wage, receipt.name, quoted.name),
            (revised_allocation, allocation.name, mixed_allocation.name),
        ):
            assert target.docstatus == 1 and target.amended_from == original
            assert original != replacement
            for value in ("", replacement):
                rejected(
                    lambda selected=target, changed=value: savedocs_cancel(
                        selected, {"amended_from": changed}
                    )
                )
            normal_cancel(target)
            assert target.amended_from == original
        for doctype in (WAGE, ALLOCATION):
            assert any(
                item["doctype"] == doctype and item["nonempty_original_values"]
                for item in evidence["native_redacted_cancel_payloads"]
            ), "缺少原生隐藏资格摘要取消后保留的实际证据"
        evidence["cancel_amendment_source_clear_replace_rejected"] = [WAGE, ALLOCATION]
        evidence["native_fieldlevel_redacted_cancel_preserves_all_sql_values"] = True
        passed(current)

        current = "strict_native_field_readonly_and_parent_child_mask_denials"
        tightened(
            WAGE,
            "piece_rate",
            "read_only",
            1,
            lambda: rejected(listing, frappe.PermissionError),
        )
        tightened(
            WAGE,
            "piece_rate",
            "mask",
            1,
            lambda: rejected(listing, frappe.PermissionError),
        )
        tightened(
            "Job Card Time Log",
            "employee",
            "mask",
            1,
            lambda: rejected(listing, frappe.PermissionError),
        )

        def blocked_child_reference():
            rejected(
                lambda: insert(
                    ALLOCATION,
                    job_card=field_team_card.name,
                    payroll_date=today(),
                    salary_component=component.name,
                    employees=[
                        {
                            "employee": employees[0].name,
                            "quantity": 1,
                            "piece_rate": 0,
                            "extra_amount": 1,
                            "piece_rate_reference": history[1].name,
                            "piece_rate_reference_version": _stamp(history[1].modified),
                        }
                    ],
                ),
                frappe.PermissionError,
            )

        tightened(CHILD, "piece_rate_reference", "mask", 1, blocked_child_reference)
        frappe.set_user(manager)
        passed(current)
    except Exception as error:
        primary = error
        error.add_note("历史工价原生验收失败门槛：" + current)
        error.add_note("已通过门槛：" + ",".join(checks))
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
            clear_metadata()
            frappe.clear_document_cache(
                "Manufacturing Settings", "Manufacturing Settings"
            )
            _profile_restore_caller(
                original_user,
                original_test,
                users,
                records,
                callbacks_before,
                context_before,
            )
            _restore_response(response_before)

        def audit_physical():
            nonlocal physical_after
            physical_after = _physical_snapshot()
            return physical_after == physical_before

        _profile_audit(
            [
                ("collect_exact_native_fixture_parents_and_children", collect),
                ("rollback_all_fixture_transactions_and_owned_callbacks", rollback),
                ("restore_owned_meta_masks_user_document_cache_and_caller", restore),
                (
                    "all_owned_fixture_rows_absent",
                    lambda: all(not frappe.db.exists(dt, name) for dt, name in records),
                ),
                (
                    "all_business_auth_versions_series_metadata_unchanged",
                    lambda: (
                        _contents() == contents_before
                        and _protected() == protected_before
                    ),
                ),
                ("whole_gl_sle_unchanged", lambda: _ledger_snapshot() == ledger_before),
                ("native_source_files_unchanged", lambda: _sources() == sources_before),
                (
                    "full_931_contents_structure_indexes_engines_and_sequences_unchanged",
                    audit_physical,
                ),
                (
                    "original_roles_context_callbacks_and_transaction_control_restored",
                    lambda: (
                        _callbacks() == callbacks_before
                        and _local_context() == context_before
                        and frappe.session.user == original_user
                        and frappe.in_test == original_test
                        and sorted(frappe.get_roles(original_user)) == original_roles
                        and frappe.db._disable_transaction_control
                        == transaction_control
                        and _permission_response_state() == response_before
                    ),
                ),
                (
                    "owned_role_cache_invalidated",
                    lambda: all(
                        frappe.cache.hget("roles", user) is None for user in users
                    ),
                ),
            ],
            primary=primary,
        )
    checks.append(
        "complete_physical_931_business_auth_metadata_indexes_sequences_roles_callbacks_rollback"
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
        "whole_ledger_before": ledger_before,
        "whole_ledger_after": _ledger_snapshot(),
        "gui_verified": False,
        "http_transport_verified": False,
        "multi_connection_lock_verified": False,
        "schema_changed": False,
        "concurrency": "serial isolated rollback only; separate two-connection lock probe and fresh full snapshot required",
    }
