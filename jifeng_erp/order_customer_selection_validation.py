"""Actual order-customer selection roles in the exact rollback-only clone.

Invoke smoke once directly; the caller mirrors reviewed source and supplies an
independent fresh database snapshot. No schema reload, connection, commit,
network work, permission expansion or another module's fixture is performed.
Selection APIs are read-only; subsequent native Sales Order saves are explicitly
separate gates and do not pretend to persist a customer-profile source snapshot.
"""

from copy import deepcopy
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
from jifeng_erp.customer_archive import (
    read_customer,
    save_archive,
)
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.order_customer_selection import (
    CUSTOMER,
    KIND,
    ORDER,
    PROFILE,
    SCOPE,
    choose_customer,
    query_customers,
)
from jifeng_erp.order_operations_validation import _doctypes, _physical_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _document_snapshot, _sql_row_hash
from jifeng_erp.workflow import create_sales_order_flows


ISOLATED_SITE = "jifeng-plating-native-isolated.localhost"
ISOLATED_DATABASE = "jf_plap_isolated_20261002"
VALIDATION_COMPANIES = {"Jifeng ERP UI Validation", "Jifeng ERP Validation"}
PROTECTED = {
    PROFILE,
    CUSTOMER,
    ORDER,
    "Jifeng Flow Order",
    "Jifeng Product Profile",
    "Item",
    "Contact",
    "Address",
    "Dynamic Link",
    "Customer Group",
    "Territory",
    "BOM",
    "Work Order",
    "Job Card",
    "Jifeng Wage Entry",
    "Jifeng Wage Allocation",
    "Salary Slip",
    "Additional Salary",
    "Payment Ledger Entry",
    "GL Entry",
    "Stock Ledger Entry",
    "Sales Invoice",
    "Delivery Note",
    "Account",
}


def _rows(doctype, records=()):
    """Exclude only exact owned synthetic rows and their exact native children."""
    clauses, values = [], []
    own_names = sorted(name for kind, name in records if kind == doctype)
    if own_names:
        clauses.append("name NOT IN (" + ",".join(["%s"] * len(own_names)) + ")")
        values.extend(own_names)
    if frappe.get_meta(doctype).istable:
        for parenttype in sorted({kind for kind, _ in records}):
            parents = sorted(name for kind, name in records if kind == parenttype)
            clauses.append(
                "NOT (COALESCE(parenttype,'')=%s AND COALESCE(parent,'') IN ("
                + ",".join(["%s"] * len(parents))
                + "))"
            )
            values.extend([parenttype, *parents])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return _sql_row_hash(
        frappe.db.sql(
            "SELECT * FROM `tab"
            + doctype.replace("`", "``")
            + "`"
            + where
            + " ORDER BY name",
            tuple(values),
            as_dict=True,
        )
    )


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


def _contents():
    roots = PROTECTED | {
        "Price List",
        "User",
        "User Permission",
        "DocShare",
        "Version",
        "Property Setter",
        "Custom Field",
        "Custom DocPerm",
        "DocType",
        "Comment",
        "Activity Log",
        "Deleted Document",
        "File",
        "Email Queue",
        "Communication",
        "Notification Log",
        "Error Log",
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


def _native_sources():
    paths = {
        "Customer JSON": frappe.get_app_path(
            "erpnext",
            "selling",
            "doctype",
            "customer",
            "customer.json",
        ),
        "Sales Order JSON": frappe.get_app_path(
            "erpnext",
            "selling",
            "doctype",
            "sales_order",
            "sales_order.json",
        ),
        "Sales Order controller": frappe.get_app_path(
            "erpnext",
            "selling",
            "doctype",
            "sales_order",
            "sales_order.py",
        ),
        "Customer Profile JSON": frappe.get_app_path(
            "jifeng_erp",
            "jifeng_erp",
            "doctype",
            "jifeng_customer_profile",
            "jifeng_customer_profile.json",
        ),
        "Order Flow": frappe.get_app_path("jifeng_erp", "workflow.py"),
    }
    return {
        label: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for label, path in paths.items()
    }


def _stamp(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _permission_response_state():
    """Actual Frappe request channels, without returning any message contents.

    Installed has_permission(throw=False) suppresses msgprint; failed native
    checks can set flags.error_message, and debug checks can append the local
    permission_debug_log. Successful selection must preserve these channels.
    """
    local = {}
    for field in ("message_log", "permission_debug_log"):
        present = hasattr(frappe.local, field)
        local[field] = {
            "present": present,
            "value": deepcopy(getattr(frappe.local, field)) if present else None,
        }
    return {
        "local": local,
        "error_message_present": "error_message" in frappe.flags,
        "error_message": deepcopy(frappe.flags.get("error_message")),
    }


def smoke(company, other_company):
    """Actual Sales User/Manager choices, native draft saves and flow provenance."""
    assert (
        frappe.local.site == ISOLATED_SITE and frappe.conf.db_name == ISOLATED_DATABASE
    )
    assert frappe.db.db_type == "mariadb"
    assert int(frappe.conf.maintenance_mode or 0) == 1
    assert int(frappe.conf.pause_scheduler or 0) == 1
    frappe.only_for("System Manager")
    assert {company, other_company} == VALIDATION_COMPANIES and company != other_company
    assert all(frappe.db.exists("Company", name) for name in VALIDATION_COMPANIES)
    assert SCOPE == "order_customer" and KIND == "订单客户"
    assert (PROFILE, CUSTOMER, ORDER) == (
        "Jifeng Customer Profile",
        "Customer",
        "Sales Order",
    )
    customer_field = frappe.get_meta(ORDER).get_field("customer")
    assert customer_field.fieldtype == "Link" and customer_field.options == CUSTOMER
    assert not customer_field.read_only and not customer_field.get(
        "read_only_depends_on"
    )
    physical_before = _physical_snapshot()
    assert len(physical_before) == 931, "只在当前 931 对象隔离站验证，不修改表结构"
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
    prefix = "JF-QA-ORDER-CUST-" + frappe.generate_hash(length=10)
    point = "jf_order_customer_" + frappe.generate_hash(length=8)
    objects, users, records, checks, roles, changed_meta = [], [], set(), [], {}, set()
    evidence, primary, current, physical_after = {}, None, "fixture_setup", None
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
            CUSTOMER,
            "Item",
            "Price List",
            "Jifeng Product Profile",
            ORDER,
        }
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((doctype, document.name))
        return document

    def actor(label, selected_roles, scope=company, permission=None):
        assert selected_roles and "System Manager" not in selected_roles
        user = insert(
            "User",
            email=prefix.lower() + "-" + label + "@example.invalid",
            first_name=prefix + "-" + label,
            send_welcome_email=0,
            roles=[{"role": role} for role in selected_roles],
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
        assert (
            set(selected_roles).issubset(roles[label])
            and "System Manager" not in roles[label]
        )
        if "Sales Manager" not in selected_roles:
            assert "Sales Manager" not in roles[label]
        return user.name

    def context(document=None, scope=company):
        return {
            "company": scope,
            "name": document.name if document else None,
            "version": _stamp(document.modified) if document else None,
        }

    def archive(customer, label, *, scope=company, kind=KIND, **values):
        request = {
            "kind": kind,
            "company": scope,
            "request_id": str(uuid4()),
            "customer_version": _stamp(frappe.get_doc(CUSTOMER, customer).modified),
            "dictionary_versions": {},
            "profile": {
                "customer": customer,
                "display_name": prefix + "-相同显示名称",
                "legacy_code": prefix + "-CODE-" + label,
                "inactive": 0,
                **values,
            },
        }
        result = native(lambda: save_archive(request))
        records.add((PROFILE, result["name"]))
        return result

    def edit_archive(view, **values):
        return native(
            lambda: save_archive(
                {
                    "kind": view["kind"],
                    "company": view["company"],
                    "name": view["name"],
                    "profile_version": view["profile_version"],
                    "customer_version": _stamp(
                        frappe.get_doc(CUSTOMER, view["customer"]).modified
                    ),
                    "dictionary_versions": dict(view["dictionary_versions"]),
                    "profile": values,
                }
            )
        )

    def pure(action):
        contents, callbacks, local = _contents(), _callbacks(), _local_context()
        response_state = _permission_response_state()
        result = native(action)
        assert _contents() == contents, "客户选择查询或核对写入了持久数据"
        assert _callbacks() == callbacks and _local_context() == local
        assert _permission_response_state() == response_state, (
            "成功客户选择产生了权限提示或请求错误状态"
        )
        return result

    def rejected(action, errors=frappe.PermissionError):
        contents, callbacks, local = _contents(), _callbacks(), _local_context()
        try:
            native(action)
        except errors:
            pass
        else:
            raise AssertionError("订单客户选择接受了本应拒绝的请求：" + current)
        assert (
            _contents() == contents
            and _callbacks() == callbacks
            and _local_context() == local
        )

    def choose(row, target=None, **changes):
        return pure(
            lambda: choose_customer(
                context() if target is None else target,
                **{
                    "profile": row["name"],
                    "profile_version": row["profile_version"],
                    "customer_version": row["customer_version"],
                    "customer": row["customer"],
                    **changes,
                },
            )
        )

    def listing(target=None, **query):
        return pure(
            lambda: query_customers(
                context() if target is None else target,
                search=prefix,
                page_size=100,
                **query,
            )
        )

    def current_choice(name, target=None):
        response = listing(target)
        return next(row for row in response["rows"] if row["name"] == name)

    def passed(label):
        assert _protected(records) == protected_before, (
            "改动了既有客户、订单、生产、工资或元数据"
        )
        assert (
            _ledger_snapshot() == ledger_before and _native_sources() == sources_before
        )
        assert label not in checks
        checks.append(label)

    def clear_metadata(doctypes=None):
        for doctype in doctypes or changed_meta:
            frappe.clear_cache(doctype=doctype)
            for user in {*users, original_user, "Administrator"}:
                frappe.cache.delete_value(f"masked_fields::{doctype}::::{user}")

    def tightened(settings, action):
        contents, callbacks, local = _contents(), _callbacks(), _local_context()
        prior_user, prior_test = frappe.session.user, frappe.in_test
        nested = "jf_order_customer_tighten_" + frappe.generate_hash(length=8)
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
            frappe.set_user(clerk)
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
                    prior_user, prior_test, [], set(), callbacks, local
                )

            _profile_audit(
                [
                    ("tightening_rollback", rollback),
                    ("tightening_restore_caches_and_caller", restore),
                    ("tightening_contents_restored", lambda: _contents() == contents),
                ],
                primary=failure,
            )

    try:
        frappe.set_user("Administrator")
        group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
        territory = frappe.db.get_value(
            "Territory", {"is_group": 0}, "name"
        ) or frappe.db.get_value("Territory", {}, "name")
        assert group and territory, "只使用现存原生客户分组和地区"
        customers = [
            insert(
                CUSTOMER,
                customer_name=prefix + "-Native-" + str(index),
                customer_type="Company",
                customer_group=group,
                territory=territory,
            )
            for index in range(4)
        ]
        archives = {
            label: archive(customers[index].name, label)
            for index, label in enumerate(("first", "second", "inactive", "disabled"))
        }
        archives["inactive"] = edit_archive(archives["inactive"], inactive=1)
        customers[3].disabled = 1
        native(customers[3].save)
        archives["foreign"] = archive(customers[0].name, "foreign", scope=other_company)
        archives["sales-kind"] = archive(
            customers[0].name, "sales-kind", kind="销售客户"
        )
        manager = actor("manager", ("Sales Manager",))
        clerk = actor("clerk", ("Sales User",))
        outsider = actor("outsider", ("Sales User",), scope=other_company)
        readonly = actor("readonly-order", ("Stock User", "Sales Master Manager"))
        flow_actor = actor("flow", ("Sales User", "Stock Manager"))

        for label, user in (("sales_user", clerk), ("sales_manager", manager)):
            current = (
                label
                + "_query_choose_precise_native_id_without_profile_create_or_writes"
            )
            frappe.set_user(user)
            assert frappe.has_permission(ORDER, "create") and frappe.has_permission(
                ORDER, "write"
            )
            if user == clerk:
                assert not frappe.has_permission(PROFILE, "create")
                rejected(lambda: read_customer(customers[0].name, KIND, company))
            result = listing()
            assert result["scope"] == SCOPE and result["company"] == company
            assert result["total"] == 2 and len(result["rows"]) == 2
            assert {row["name"] for row in result["rows"]} == {
                archives["first"]["name"],
                archives["second"]["name"],
            }
            assert len({row["display_name"] for row in result["rows"]}) == 1
            assert len({row["customer"] for row in result["rows"]}) == 2
            for row in result["rows"]:
                selected = choose(row)
                assert selected == {"scope": SCOPE, **row} and selected["can_select"]
                assert selected["customer"] == frappe.db.get_value(
                    PROFILE, row["name"], "customer"
                )
                assert "mobile" not in selected and "address" not in selected
            passed(current)

        current = "native_paging_keywords_inactive_disabled_company_and_kind_filtering"
        frappe.set_user(clerk)
        first_page = pure(
            lambda: query_customers(context(), prefix, page=1, page_size=1)
        )
        second_page = pure(
            lambda: query_customers(context(), prefix, page=2, page_size=1)
        )
        beyond = pure(lambda: query_customers(context(), prefix, page=99, page_size=1))
        assert first_page["total"] == second_page["total"] == 2
        assert first_page["rows"][0]["name"] != second_page["rows"][0]["name"]
        assert beyond == second_page
        exact_row = current_choice(archives["first"]["name"])
        exact_search = pure(
            lambda: query_customers(context(), exact_row["legacy_code"])
        )
        assert {row["name"] for row in exact_search["rows"]} == {exact_row["name"]}
        empty = pure(lambda: query_customers(context(), prefix + "-MISSING", page=99))
        assert empty["page"] == 1 and empty["total"] == empty["total_pages"] == 0
        for label in ("inactive", "disabled"):
            view = archives[label]
            rejected(
                lambda view=view: choose_customer(
                    context(),
                    view["name"],
                    view["profile_version"],
                    _stamp(frappe.get_doc(CUSTOMER, view["customer"]).modified),
                    view["customer"],
                ),
                frappe.ValidationError,
            )
        for label in ("foreign", "sales-kind"):
            view = archives[label]
            rejected(
                lambda view=view: choose_customer(
                    context(),
                    view["name"],
                    view["profile_version"],
                    view["customer_version"],
                    view["customer"],
                )
            )
        evidence["paging"] = {
            "active_selectable": 2,
            "page_size": 1,
            "inactive_disabled_foreign_wrong_kind_filtered": True,
        }
        passed(current)

        current = "two_source_versions_and_exact_mapping_id_rechecked_without_writes"
        selected_before = current_choice(archives["first"]["name"])
        for field in ("profile_version", "customer_version"):
            rejected(
                lambda field=field: choose_customer(
                    context(),
                    selected_before["name"],
                    **{
                        key: "2000-01-01T00:00:00"
                        if key == field
                        else selected_before[key]
                        for key in ("profile_version", "customer_version", "customer")
                    },
                ),
                frappe.ValidationError,
            )
        for wrong in (
            customers[1].name,
            selected_before["name"],
            selected_before["display_name"],
        ):
            rejected(
                lambda wrong=wrong: choose_customer(
                    context(),
                    selected_before["name"],
                    selected_before["profile_version"],
                    selected_before["customer_version"],
                    wrong,
                ),
                frappe.ValidationError,
            )
        frappe.set_user(manager)
        archives["first"] = edit_archive(
            archives["first"], notes="真实档案修改用于版本核对"
        )
        frappe.set_user(clerk)
        rejected(
            lambda: choose_customer(
                context(),
                selected_before["name"],
                selected_before["profile_version"],
                selected_before["customer_version"],
                selected_before["customer"],
            ),
            frappe.ValidationError,
        )
        after_profile = current_choice(archives["first"]["name"])
        frappe.set_user("Administrator")
        customer = frappe.get_doc(CUSTOMER, customers[0].name)
        customer.customer_name += "-changed"
        native(customer.save)
        frappe.set_user(clerk)
        rejected(
            lambda: choose_customer(
                context(),
                after_profile["name"],
                after_profile["profile_version"],
                after_profile["customer_version"],
                after_profile["customer"],
            ),
            frappe.ValidationError,
        )
        assert (
            choose(current_choice(archives["first"]["name"]))["customer"]
            == customers[0].name
        )
        passed(current)

        current = "actual_company_and_profile_customer_user_permissions_filter_and_deny_hidden_choices"
        frappe.set_user(outsider)
        rejected(lambda: query_customers(context(), prefix))
        rejected(
            lambda: choose_customer(
                context(),
                exact_row["name"],
                exact_row["profile_version"],
                exact_row["customer_version"],
                exact_row["customer"],
            )
        )
        foreign_rows = pure(
            lambda: query_customers(context(scope=other_company), prefix)
        )
        assert {row["name"] for row in foreign_rows["rows"]} == {
            archives["foreign"]["name"]
        }
        for doctype, name in (
            (PROFILE, archives["first"]["name"]),
            (CUSTOMER, customers[0].name),
        ):
            frappe.set_user("Administrator")
            scoped = actor(
                "scoped-" + ("profile" if doctype == PROFILE else "customer"),
                ("Sales User",),
                permission=(doctype, name),
            )
            frappe.set_user(scoped)
            visible = listing()
            assert {row["name"] for row in visible["rows"]} == {
                archives["first"]["name"]
            }
            hidden = archives["second"]
            rejected(
                lambda: choose_customer(
                    context(),
                    hidden["name"],
                    hidden["profile_version"],
                    hidden["customer_version"],
                    hidden["customer"],
                )
            )
        frappe.set_user("Administrator")
        customer_only = actor("customer-only", ("Sales User",))
        insert(
            "User Permission",
            user=customer_only,
            allow=CUSTOMER,
            for_value=customers[0].name,
            apply_to_all_doctypes=0,
            applicable_for=CUSTOMER,
        )
        frappe.clear_cache(user=customer_only)
        frappe.set_user(customer_only)
        profile_names = {archives["first"]["name"], archives["second"]["name"]}
        native_profiles = pure(
            lambda: frappe.get_list(
                PROFILE,
                filters={
                    "company": company,
                    "archive_kind": KIND,
                    "name": ["in", sorted(profile_names)],
                },
                fields=["name", "customer"],
                limit_page_length=2,
            )
        )
        assert {row["name"] for row in native_profiles} == profile_names
        assert not frappe.has_permission(
            CUSTOMER,
            "read",
            doc=frappe.get_doc(CUSTOMER, customers[1].name),
            throw=False,
        )
        quietly_filtered = listing()
        assert {row["name"] for row in quietly_filtered["rows"]} == {
            archives["first"]["name"]
        }
        assert quietly_filtered["total"] == 1
        evidence["quiet_linked_source_filter"] = {
            "native_visible_profiles": 2,
            "selectable_native_customers": 1,
            "customer_permission_applicable_for": CUSTOMER,
            "message_log_error_message_and_permission_debug_log_unchanged": True,
        }
        passed(current)

        current = "native_sales_user_saves_new_draft_then_explicit_existing_draft_customer_selection"
        frappe.set_user("Administrator")
        assert frappe.db.exists("Item Group", "Products") and frappe.db.exists(
            "UOM", "Nos"
        )
        assert not frappe.db.get_value("Item Group", "Products", "is_group")
        currency = frappe.db.get_value("Company", company, "default_currency")
        warehouse = frappe.db.get_value(
            "Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name"
        )
        assert currency and warehouse, "只使用现存公司本币及启用的明细仓库"
        item = insert(
            "Item",
            item_code=prefix + "-ITEM",
            item_name=prefix + "-合成商品",
            item_group="Products",
            stock_uom="Nos",
            is_stock_item=0,
        )
        template = insert(
            "Jifeng Product Profile",
            item_code=item.name,
            company=company,
            requirements="默认工艺保持",
            packaging="默认包装保持",
        )
        price_list = insert(
            "Price List",
            price_list_name=prefix + "-PRICE",
            currency=currency,
            selling=1,
            enabled=1,
        )

        def draft(customer, label):
            return insert(
                ORDER,
                company=company,
                customer=customer,
                delivery_date=add_days(today(), 7),
                selling_price_list=price_list.name,
                price_list_currency=currency,
                plc_conversion_rate=1,
                currency=currency,
                conversion_rate=1,
                ignore_pricing_rule=1,
                disable_rounded_total=1,
                items=[
                    {
                        "item_code": item.name,
                        "qty": 2,
                        "rate": 10,
                        "delivery_date": add_days(today(), 7),
                        "warehouse": warehouse,
                        "jf_make_flow": 1,
                        "jf_requirements": prefix + "-要求-" + label,
                        "jf_packaging": prefix + "-包装-" + label,
                    }
                ],
            )

        frappe.set_user(clerk)
        selected = choose(current_choice(archives["first"]["name"]))
        master_before = {
            _id: _document_snapshot(_type, _id)
            for _type, _id in (
                (CUSTOMER, customers[0].name),
                (CUSTOMER, customers[1].name),
                (PROFILE, archives["first"]["name"]),
                (PROFILE, archives["second"]["name"]),
            )
        }
        order = draft(selected["customer"], "FIRST")
        assert (
            order.docstatus == 0
            and frappe.get_doc(ORDER, order.name).customer == customers[0].name
        )
        source_context = context(frappe.get_doc(ORDER, order.name))
        chosen = choose(
            current_choice(archives["second"]["name"], source_context), source_context
        )
        order = frappe.get_doc(ORDER, order.name)
        order.customer, order.customer_name = chosen["customer"], None
        native(order.set_missing_values)
        native(order.save)
        order.reload()
        assert (
            order.customer == customers[1].name
            and order.customer_name == customers[1].customer_name
        )
        assert _version_has(order, "customer")
        assert all(
            _document_snapshot(_type, _id) == master_before[_id]
            for _type, _id in (
                (CUSTOMER, customers[0].name),
                (CUSTOMER, customers[1].name),
                (PROFILE, archives["first"]["name"]),
                (PROFILE, archives["second"]["name"]),
            )
        )
        assert template.item_code == item.name
        rejected(
            lambda: query_customers(source_context, prefix), frappe.ValidationError
        )
        evidence["native_draft"] = {
            "role": "Sales User",
            "created": True,
            "customer_changed_with_native_save": True,
            "native_customer_id_saved": True,
            "customer_profile_source_history_persisted": False,
        }
        passed(current)

        current = "existing_order_readonly_role_user_permission_company_and_version_context_reject"
        frappe.set_user(clerk)
        active_context = context(order)
        other_order = draft(customers[0].name, "SECOND")
        for target in (
            active_context | {"company": other_company},
            active_context | {"version": "2000-01-01T00:00:00"},
        ):
            rejected(
                lambda target=target: query_customers(target, prefix),
                (frappe.PermissionError, frappe.ValidationError),
            )
        frappe.set_user("Administrator")
        order_scoped = actor(
            "scoped-order", ("Sales User",), permission=(ORDER, order.name)
        )
        frappe.set_user(order_scoped)
        assert listing(active_context)["rows"]
        rejected(lambda: query_customers(context(other_order), prefix))
        frappe.set_user(readonly)
        assert frappe.has_permission(ORDER, "read") and not frappe.has_permission(
            ORDER, "write"
        )
        assert not frappe.has_permission(ORDER, "create")
        for target in (context(), active_context):
            rejected(lambda target=target: query_customers(target, prefix))
            row = exact_row
            rejected(
                lambda target=target: choose_customer(
                    target,
                    row["name"],
                    row["profile_version"],
                    row["customer_version"],
                    row["customer"],
                )
            )
        passed(current)

        for doctype, field, prop in (
            (ORDER, "customer", "read_only"),
            (ORDER, "customer", "mask"),
            (PROFILE, "customer", "mask"),
            (CUSTOMER, "disabled", "mask"),
        ):
            current = (
                "actual_"
                + (
                    "order"
                    if doctype == ORDER
                    else "profile"
                    if doctype == PROFILE
                    else "native_customer"
                )
                + "_"
                + field
                + "_"
                + prop
                + "_denies_selection"
            )

            def limited():
                for target in (context(), active_context):
                    rejected(lambda target=target: query_customers(target, prefix))
                    rejected(
                        lambda target=target: choose_customer(
                            target,
                            exact_row["name"],
                            exact_row["profile_version"],
                            exact_row["customer_version"],
                            exact_row["customer"],
                        )
                    )

            tightened([(doctype, field, prop, 1, "Int")], limited)
            passed(current)

        current = "optional_legacy_code_mask_omits_dto_and_search_without_restoring_hidden_data"

        def hidden_code():
            page = listing()
            assert page["total"] == 2 and all(
                "legacy_code" not in row for row in page["rows"]
            )
            row = next(
                row for row in page["rows"] if row["name"] == archives["first"]["name"]
            )
            assert "legacy_code" not in choose(row)
            secret = archives["first"]["profile"]["legacy_code"]
            assert pure(lambda: query_customers(context(), secret))["total"] == 0

        tightened([(PROFILE, "legacy_code", "mask", 1, "Int")], hidden_code)
        passed(current)

        current = "native_selected_customer_order_submit_flow_exact_line_idempotency_and_cancel_protection"
        frappe.set_user(flow_actor)
        assert frappe.has_permission(ORDER, "submit") and frappe.has_permission(
            "Jifeng Flow Order", "create"
        )
        order = frappe.get_doc(ORDER, order.name)
        native(order.submit)
        order.reload()
        submitted_context = context(order)
        rejected(
            lambda: query_customers(submitted_context, prefix), frappe.ValidationError
        )
        rejected(
            lambda: choose_customer(
                submitted_context,
                exact_row["name"],
                exact_row["profile_version"],
                exact_row["customer_version"],
                exact_row["customer"],
            ),
            frappe.ValidationError,
        )
        names = native(lambda: create_sales_order_flows(order.name))
        assert len(names) == 1
        flow = frappe.get_doc("Jifeng Flow Order", names[0])
        objects.append(flow)
        records.add((flow.doctype, flow.name))
        assert (
            flow.sales_order == order.name
            and flow.sales_order_item == order.items[0].name
        )
        assert (
            flow.company == company
            and flow.item_code == item.name
            and flow.profile == template.name
        )
        assert flow.planned_qty == flow.source_stock_qty == flow.source_qty == 2
        assert flow.requirements == order.items[0].jf_requirements
        assert flow.packaging == order.items[0].jf_packaging and not flow.source_changed
        assert pure(lambda: create_sales_order_flows(order.name)) == names
        snapshot_before = (
            flow.requirements,
            flow.packaging,
            flow.sales_order_item,
            flow.planned_qty,
        )
        order.items[0].jf_requirements += "-已改"
        native(order.save)
        flow.reload()
        assert (
            flow.source_changed
            and flow.source_requirements == order.items[0].jf_requirements
        )
        assert (
            flow.requirements,
            flow.packaging,
            flow.sales_order_item,
            flow.planned_qty,
        ) == snapshot_before
        native(order.cancel)
        flow.reload()
        assert flow.source_status == "Cancelled" and flow.source_changed
        evidence["order_flow"] = {
            "roles": ["Sales User", "Stock Manager"],
            "submitted_selected_native_customer": order.customer == customers[1].name,
            "exact_sales_order_item": True,
            "quantity_requirements_packaging_preserved": True,
            "idempotent_without_extra_write": True,
            "changed_cancelled_source_flagged": True,
            "production_or_payroll_posted": False,
        }
        passed(current)

        current = "strict_context_missing_versions_profile_id_customer_id_and_guest_rejected_without_writes"
        frappe.set_user(clerk)
        for target in (
            {"company": company},
            context() | {"name": ""},
            context() | {"version": "2000-01-01T00:00:00"},
            context() | {"extra": "unsupported"},
            context() | {"company": " " + company},
        ):
            rejected(
                lambda target=target: query_customers(target, prefix),
                frappe.ValidationError,
            )
        current_row = current_choice(archives["first"]["name"])
        for key in ("profile_version", "customer_version"):
            rejected(
                lambda key=key: choose_customer(
                    context(),
                    current_row["name"],
                    **{
                        field: None if field == key else current_row[field]
                        for field in ("profile_version", "customer_version", "customer")
                    },
                ),
                frappe.ValidationError,
            )
        for bad in ("", " " + current_row["name"]):
            rejected(
                lambda bad=bad: choose_customer(
                    context(),
                    bad,
                    current_row["profile_version"],
                    current_row["customer_version"],
                    current_row["customer"],
                ),
                frappe.ValidationError,
            )
        frappe.set_user("Guest")
        rejected(lambda: query_customers(context(), prefix))
        rejected(
            lambda: choose_customer(
                context(),
                current_row["name"],
                current_row["profile_version"],
                current_row["customer_version"],
                current_row["customer"],
            )
        )
        passed(current)
    except Exception as error:
        primary = error
        error.add_note("订单客户选择原生验收失败门槛：" + current)
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
                ("collect_owned_synthetic_native_parents_children", collect),
                ("rollback_only_fixture_transactions_and_owned_callbacks", rollback),
                ("restore_owned_metadata_mask_user_document_cache_and_caller", restore),
                (
                    "all_exact_synthetic_fixtures_absent",
                    lambda: all(not frappe.db.exists(dt, name) for dt, name in records),
                ),
                (
                    "all_selected_contents_auth_series_versions_metadata_unchanged",
                    lambda: _contents() == before,
                ),
                (
                    "native_customer_profile_order_production_wage_business_and_metadata_unchanged",
                    lambda: _protected() == protected_before,
                ),
                (
                    "whole_gl_and_sle_unchanged",
                    lambda: _ledger_snapshot() == ledger_before,
                ),
                (
                    "native_source_files_unchanged",
                    lambda: _native_sources() == sources_before,
                ),
                (
                    "complete_931_physical_contents_schema_indexes_auth_sequence_unchanged",
                    audit_physical,
                ),
                (
                    "original_user_roles_callbacks_context_transaction_control_restored",
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
                    "owned_role_cache_entries_invalidated",
                    lambda: all(
                        frappe.cache.hget("roles", user) is None for user in users
                    ),
                ),
            ],
            primary=primary,
        )
    checks.append(
        "complete_physical_931_objects_auth_metadata_indexes_sequences_roles_context_rollback"
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
        "schema_or_existing_source_fields_changed": False,
        "customer_profile_source_history_persisted": False,
        "concurrency": "serial isolated rollback experiment; independent fresh snapshot required",
        "remaining": ["真实 A/B 客户选择界面", "订单客户档案来源的持久追溯设计"],
    }
