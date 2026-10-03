"""One native, rollback-only payroll-history experiment in the fixed clone.

Invoke smoke directly once after the caller has reloaded only the new Rule
DocType. This module never connects, reloads schema, migrates, commits, sends
mail, downloads or controls a browser. It creates real Salary Slips through
HRMS insert/submit and a temporary reader role whose native permissions, users,
metadata tightenings and every synthetic payroll record are rolled back.

The caller must independently compare a fresh full database snapshot afterwards.
This serial driver does not claim HTTP, browser or concurrent-writer coverage.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.utils import add_days, getdate, today

from jifeng_erp import salary_history as history
from jifeng_erp import salary_history_classification as classification
from jifeng_erp.catalog_validation import (
    _profile_audit, _profile_restore_caller, _profile_rollback, _version_has,
)
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.order_customer_selection_validation import _permission_response_state, _rows
from jifeng_erp.order_operations_validation import _doctypes, _physical_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _sql_row_hash
from jifeng_erp.wage_permissions import PERMISSION_FIELDS


ISOLATED_SITE = "jifeng-plating-native-isolated.localhost"
ISOLATED_DATABASE = "jf_plap_isolated_20261002"
VALIDATION_COMPANIES = {"Jifeng ERP UI Validation", "Jifeng ERP Validation"}
RULE = classification.DOCTYPE
READABLE = {"Company", "Employee", "Currency", "Salary Component", "Salary Slip",
            "Additional Salary", "Jifeng Wage Entry", RULE}
AUDIT = READABLE | {
    "Salary Detail", "Salary Structure", "Salary Structure Assignment", "Payroll Settings",
    "Holiday List", "Holiday List Assignment", "Jifeng Employee Supplement", "Letter Head",
    "Role", "User", "User Permission", "Has Role", "DocShare", "Version", "DocType",
    "DocField", "DocPerm", "Custom DocPerm", "Property Setter", "Custom Field",
    "Comment", "Activity Log", "Deleted Document", "Email Queue", "Communication",
    "Notification Log", "Error Log", "File",
}


def _contents():
    selected = _doctypes({doctype for doctype in AUDIT if frappe.db.exists("DocType", doctype)})
    result = {doctype: _rows(doctype) for doctype in sorted(selected) if not frappe.get_meta(doctype).issingle}
    result["tabSingles"] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tabSingles` ORDER BY doctype, field", as_dict=True))
    for table in ("__Auth", "tabSeries", "__global_search"):
        result[table] = _sql_row_hash(frappe.db.sql("SELECT * FROM `" + table + "` ORDER BY 1", as_dict=True))
    return result


def _sources():
    paths = {
        "history": frappe.get_app_path("jifeng_erp", "salary_history.py"),
        "classification": frappe.get_app_path("jifeng_erp", "salary_history_classification.py"),
        "validation": frappe.get_app_path("jifeng_erp", "salary_history_validation.py"),
        "rule_controller": frappe.get_app_path("jifeng_erp", "jifeng_erp", "doctype",
                                               "jifeng_salary_history_rule", "jifeng_salary_history_rule.py"),
        "rule_schema": frappe.get_app_path("jifeng_erp", "jifeng_erp", "doctype",
                                           "jifeng_salary_history_rule", "jifeng_salary_history_rule.json"),
        "salary_slip_controller": frappe.get_app_path("hrms", "payroll", "doctype", "salary_slip", "salary_slip.py"),
        "salary_detail_schema": frappe.get_app_path("hrms", "payroll", "doctype", "salary_detail", "salary_detail.json"),
    }
    return {label: hashlib.sha256(Path(path).read_bytes()).hexdigest() for label, path in paths.items()}


def _stamp(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _restore_response(state):
    for name, entry in state["local"].items():
        if entry["present"]:
            setattr(frappe.local, name, deepcopy(entry["value"]))
        elif hasattr(frappe.local, name):
            delattr(frappe.local, name)
    if state["error_message_present"]:
        frappe.flags.error_message = deepcopy(state["error_message"])
    else:
        frappe.flags.pop("error_message", None)


def _digest(value):
    """Diagnostics expose presence/counts/digests, never native message values."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def _fixture_dates(companies):
    """Select 202 existing-company fiscal dates before creating any fixture."""
    from erpnext.accounts.utils import get_fiscal_year

    current_day = getdate(today())
    year_start, year_end = date(current_day.year, 1, 1), date(current_day.year, 12, 31)
    main_day, future_day = getdate(add_days(current_day, -20)), getdate(add_days(current_day, 20))
    for fiscal in frappe.get_all("Fiscal Year", filters={"disabled": 0},
            fields=["name", "year_start_date", "year_end_date"], order_by="year_start_date asc, name asc"):
        first = max(getdate(fiscal.year_start_date), year_start)
        last_allowed = min(getdate(fiscal.year_end_date), year_end)
        last = getdate(add_days(first, 201))
        if last > last_allowed:
            continue
        coverage = {}
        for company in sorted(companies):
            checked = [get_fiscal_year(date=day, company=company, as_dict=True,
                       verbose=0, raise_on_missing=False) for day in (first, last, main_day, future_day)]
            if not all(checked) or checked[0].name != checked[1].name:
                break
            coverage[company] = {"bulk": checked[0].name, "main": checked[2].name, "future": checked[3].name}
        if len(coverage) == len(companies):
            return _stamp(first), _stamp(main_day), {
                "calendar_year": current_day.year, "first": _stamp(first), "last": _stamp(last),
                "continuous_daily_periods": 202, "main_day": _stamp(main_day),
                "future_day": _stamp(future_day), "company_native_fiscal_years": coverage,
            }
    raise AssertionError("既有公司财年没有覆盖本年度202个连续日期及原生验收主期间；禁止新增财年或伪造工资单")


def _public_dto(result, *, detail=False):
    """Independent public-key assertions; private data is never returned."""
    row_keys = history.SLIP_FIELDS - {"modified", "docstatus", "exchange_rate"} | {
        "version", *history.UNKNOWN_FIELDS, "buckets", "basic_default_amount",
        "classification_state", "unresolved",
    }
    detail_keys = {"name", "idx", "salary_component", "amount", "default_amount",
                   "do_not_include_in_total", "statistical_component", "depends_on_payment_days",
                   "classification", "sources"}
    assert set(result) == ({"readonly", "scope", "context", "row", "earnings", "deductions", "sources"}
                           if detail else {"readonly", "scope", "context", "page", "page_size", "total", "total_pages", "rows"})
    assert result["readonly"] is True and result["scope"] == "salary_history"
    assert set(result["context"]) == history.CONTEXT_FIELDS
    for row in [result["row"]] if detail else result["rows"]:
        assert set(row) == row_keys
        assert all(row[field] is None for field in history.UNKNOWN_FIELDS)
        assert all(isinstance(row[field], str) and Decimal(row[field]).is_finite()
                   for field in ("gross_pay", "total_deduction", "net_pay", "rounded_total", "total_working_days", "payment_days"))
        assert row["basic_default_amount"] is None or isinstance(row["basic_default_amount"], str)
        assert set(row["buckets"]) == classification.EARNING_BUCKETS | classification.DEDUCTION_BUCKETS
        assert all(value is None or isinstance(value, str) and Decimal(value).is_finite() for value in row["buckets"].values())
    if detail:
        for table in ("earnings", "deductions"):
            for child in result[table]:
                assert set(child) == detail_keys
                assert isinstance(child["amount"], str) and Decimal(child["amount"]).is_finite()
                assert child["default_amount"] is None or isinstance(child["default_amount"], str)
                if child["classification"]:
                    assert set(child["classification"]) == {"rule", "version", "bucket", "mode", "effective_from"}
                for source in child["sources"]:
                    assert set(source) == {"doctype", "name", "version"}
        assert all(set(source) == {"doctype", "name", "version"} for source in result["sources"])
    serialized = json.dumps(result, ensure_ascii=False)
    assert all(field not in serialized for field in ("bank_account_no", "bank_ac_no", "bank_name", "account_name", "payment_account"))
    return result


def smoke(company, other_company, stock_uom="Nos"):
    """Native payroll, permissions, source changes and 202 saved slips; rollback."""
    assert frappe.local.site == ISOLATED_SITE and frappe.conf.db_name == ISOLATED_DATABASE
    assert frappe.db.db_type == "mariadb"
    assert int(frappe.conf.maintenance_mode or 0) == int(frappe.conf.pause_scheduler or 0) == 1
    frappe.only_for("System Manager")
    assert {company, other_company} == VALIDATION_COMPANIES and company != other_company
    assert all(frappe.db.exists("Company", name) for name in VALIDATION_COMPANIES)
    assert frappe.db.exists("UOM", stock_uom) and frappe.db.exists("DocType", RULE)
    rule_meta = frappe.get_meta(RULE)
    assert rule_meta.is_submittable and rule_meta.track_changes
    assert rule_meta.get_field("binding_key").unique
    assert history.BATCH_SIZE == 200
    physical_before = _physical_snapshot()
    assert len(physical_before) == 932, "仅在已经定向加载新 Rule 的固定 932 对象隔离站验收"
    contents_before, ledger_before, source_before = _contents(), _ledger_snapshot(), _sources()
    original_user, original_test = frappe.session.user, frappe.in_test
    cached_roles = frappe.cache.hget("roles", original_user)
    # A preceding failed run may have cached a rolled-back temporary Role for
    # Administrator. Establish the oracle from native persisted roles first.
    frappe.cache.hdel("roles", original_user)
    original_roles = sorted(frappe.get_roles(original_user))
    if original_user == "Administrator":
        assert original_roles == sorted(frappe.get_all("Role", pluck="name"))
    role_oracle = {"cache_present": cached_roles is not None,
                   "cache_matches_persisted_roles": cached_roles is None or sorted(cached_roles) == original_roles,
                   "cache_before_sha256": _digest(sorted(cached_roles) if cached_roles is not None else None),
                   "persisted_roles_sha256": _digest(original_roles)}
    first, main_day, date_evidence = _fixture_dates(VALIDATION_COMPANIES)
    callbacks_before, context_before, response_before = _callbacks(), _local_context(), _permission_response_state()
    transaction_control = frappe.db._disable_transaction_control
    prefix = "JF-QA-SAL-HIST-" + frappe.generate_hash(length=10)
    point = "jf_salary_history_" + frappe.generate_hash(length=8)
    objects, users, records, owned, changed_meta = [], [], set(), set(), set()
    checks, evidence, roles = [], {"fixture_dates": date_evidence, "caller_role_oracle": role_oracle}, {}
    primary, current, physical_after = None, "fixture_setup", None
    _begin_rollback_only(point)

    def native(action):
        prior = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = prior

    def track(document, created=True):
        objects.append((document, created))
        if document.name:
            records.add((document.doctype, document.name))
            if created:
                owned.add((document.doctype, document.name))
        for row in document.get_all_children():
            if row.name:
                records.add((row.doctype, row.name))
                if created:
                    owned.add((row.doctype, row.name))
        return document

    def insert(doctype, **values):
        document = track(frappe.get_doc({"doctype": doctype, **values}))
        native(document.insert)
        return track(document)

    def clear_metadata(doctypes=None):
        for doctype in doctypes or changed_meta:
            frappe.clear_cache(doctype=doctype)
            for actor in {*users, original_user, "Administrator"}:
                for parent in ("", "Salary Slip" if doctype == "Salary Detail" else ""):
                    frappe.cache.delete_value(f"masked_fields::{doctype}::{parent}::{actor}")

    def passed(label):
        assert label not in checks
        assert _ledger_snapshot() == ledger_before and _sources() == source_before
        checks.append(label)

    def expect(action, errors=frappe.ValidationError):
        try:
            native(action)
        except errors:
            return
        raise AssertionError("工资历史验收接受了必须拒绝的操作：" + current)

    def experiment(label, action):
        """Every permission/source mutation is its own reversible experiment."""
        before, callbacks, local, messages = _contents(), _callbacks(), _local_context(), _permission_response_state()
        actor, prior_test = frappe.session.user, frappe.in_test
        nested = "jf_salary_case_" + frappe.generate_hash(length=8)
        failure = None
        _begin_rollback_only(nested)
        try:
            return action()
        except Exception as error:
            failure = error
            error.add_note("工资历史隔离子实验：" + label)
            raise
        finally:
            def rollback():
                frappe.set_user("Administrator")
                _profile_rollback(nested, callbacks)
            def restore():
                clear_metadata()
                _profile_restore_caller(actor, prior_test, users, records, callbacks, local)
                _restore_response(messages)
            _profile_audit([(label + ":rollback", rollback), (label + ":restore", restore),
                            (label + ":contents", lambda: _contents() == before)], primary=failure)

    def rejected(action, errors=frappe.ValidationError):
        return experiment(current, lambda: expect(action, errors))

    def pure(action):
        before, callbacks, local = _contents(), _callbacks(), _local_context()
        result = native(action)
        assert _contents() == before, "工资历史 GET 改变了持久数据"
        assert _callbacks() == callbacks and _local_context() == local, "工资历史 GET 改变了调用方上下文"
        return result

    def context(search=prefix, *, selected_company=company, basis="period_overlap", start=first, end=None, use_date=False):
        return {"company": selected_company, "from_date": start, "to_date": end or today(),
                "use_date": use_date, "date_basis": basis, "search": search}

    def query(ctx=None, **pages):
        return _public_dto(pure(lambda: history.query_salary_history(ctx or context(), **pages)))

    def detail(slip, ctx=None):
        return _public_dto(pure(lambda: history.get_salary_history_detail(
            ctx or context(), slip.name, _stamp(frappe.db.get_value("Salary Slip", slip.name, "modified")))), detail=True)

    def actor(label, selected_roles, scope=company, employee=None, employee_applicable=None, source_permission=None):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid",
                      first_name=prefix + "-" + label, send_welcome_email=0,
                      roles=[{"role": role} for role in selected_roles])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=scope, apply_to_all_doctypes=1)
        if employee:
            insert("User Permission", user=user.name, allow="Employee", for_value=employee,
                   apply_to_all_doctypes=int(not employee_applicable), applicable_for=employee_applicable)
        if source_permission:
            insert("User Permission", user=user.name, allow=source_permission[0],
                   for_value=source_permission[1], apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        roles[label] = sorted(frappe.get_roles(user.name))
        assert "System Manager" not in roles[label]
        return user.name

    def savedocs_cancel(document, changes=None):
        from frappe.desk.form.save import savedocs
        frappe.is_whitelisted(savedocs)
        assert "POST" in frappe.allowed_http_methods_for_whitelisted_func[savedocs]
        original_response, messages = frappe.local.response, _permission_response_state()
        monitor = getattr(frappe.local, "monitor", None)
        monitor_data = deepcopy(monitor.data) if monitor else None
        payload = frappe.get_doc(document.doctype, document.name).as_dict()
        payload.update(changes or {})
        try:
            frappe.local.response = frappe._dict(docs=[])
            native(lambda: savedocs(doc=frappe.as_json(payload), action="Cancel"))
            assert len(frappe.response.docs) == 1 and frappe.response.docs[0].docstatus == 2
            assert frappe.db.get_value(document.doctype, document.name, "docstatus") == 2
        finally:
            frappe.local.response = original_response
            _restore_response(messages)
            if monitor:
                monitor.data = monitor_data

    def tighten(doctype, field, prop, action, value="1", property_type=None):
        def run():
            frappe.set_user("Administrator")
            assert not frappe.db.exists("Property Setter", doctype + "-" + field + "-" + prop)
            insert("Property Setter", doctype_or_field="DocField", doc_type=doctype, field_name=field,
                   property=prop, value=str(value),
                   property_type=property_type or ("Int" if prop == "permlevel" else "Check"), is_system_generated=0)
            changed_meta.add(doctype)
            clear_metadata({doctype})
            frappe.set_user(reader)
            return action()
        return experiment(current, run)

    def structure(scope, currency, label):
        document = insert("Salary Structure", name=prefix + "-SS-" + label, company=scope,
                          currency=currency, is_active="Yes", payroll_frequency="Daily",
                          earnings=[{"salary_component": basic.name, "amount": 1000, "depends_on_payment_days": 0}],
                          deductions=[{"salary_component": deduction.name, "amount": 50, "depends_on_payment_days": 0}])
        native(document.submit)
        return document

    def employee(label, scope, structure_doc, *, bank=False):
        document = insert("Employee", first_name=prefix + "-" + label, company=scope, gender=gender,
                          date_of_birth="1990-01-01", date_of_joining=first, status="Active",
                          bank_ac_no=prefix + "-PRIVATE-BANK" if bank else None)
        assignment = insert("Salary Structure Assignment", employee=document.name, company=scope,
                            salary_structure=structure_doc.name, currency=structure_doc.currency, from_date=first)
        native(assignment.submit)
        holiday_assignment = insert("Holiday List Assignment", naming_series="HR-HLA-.YYYY.-",
                                    holiday_list=holiday.name, applicable_for="Employee", assigned_to=document.name, from_date=first)
        native(holiday_assignment.submit)
        return document

    def slip(person, day):
        document = insert("Salary Slip", employee=person.name, company=person.company,
                          posting_date=day, start_date=day, end_date=day, payroll_frequency="Daily",
                          currency=frappe.db.get_value("Company", person.company, "default_currency"), exchange_rate=1)
        native(document.submit)
        document.reload()
        assert document.docstatus == 1 and getdate(document.start_date) == getdate(day) == getdate(document.end_date)
        assert document.earnings and document.deductions
        return track(document)

    def wage(person, day):
        document = insert("Jifeng Wage Entry", company=person.company, employee=person.name,
                          operation=prefix + "-MANUAL", work_date=day, payroll_date=day,
                          salary_component=mixed.name, stock_uom=stock_uom, quantity=2, piece_rate=40,
                          hours=3, hourly_rate=10, extra_amount=10)
        native(document.submit)
        additional = track(frappe.get_doc("Additional Salary", native(document.make_additional_salary)))
        native(additional.submit)
        assert Decimal(str(additional.amount)) == Decimal("120")
        return document, additional

    def rule(component, bucket, mode="component", extra="", effective=first, historical=True):
        document = insert(RULE, company=company, salary_component=component.name,
                          parentfield="earnings" if component.type == "Earning" else "deductions",
                          bucket=bucket, classification_mode=mode, extra_bucket=extra, effective_from=effective)
        if historical:
            native(lambda: document.publish_historical("已核对原生保存工资项目、准确来源及结束日期"))
            document.reload()
            assert document.historical_publication == 1 and _version_has(document, "publication_reason")
        else:
            native(document.submit)
        return track(document)

    def mutate_while_read(action):
        """A real native mutation at the second scan boundary, never mock rows."""
        original_list, broad_scans, mutated = frappe.get_list, 0, False
        def observed(doctype, *args, **kwargs):
            nonlocal broad_scans, mutated
            filters = kwargs.get("filters") or {}
            if doctype == "Salary Slip" and "name" not in filters and kwargs.get("limit_start", 0) == 0:
                broad_scans += 1
                if broad_scans == 2:
                    previous = frappe.session.user
                    try:
                        frappe.set_user("Administrator")
                        native(action)
                        mutated = True
                    finally:
                        frappe.set_user(previous)
            return original_list(doctype, *args, **kwargs)
        with patch.object(frappe, "get_list", observed):
            expect(lambda: history.query_salary_history(context()), (frappe.ValidationError, frappe.PermissionError))
        assert broad_scans >= 2 and mutated, "来源变更门必须完成真实原生变更，再由查询拒绝"

    try:
        frappe.set_user("Administrator")
        gender = frappe.db.get_value("Gender", {}, "name")
        assert gender, "需要现有 Gender 字典"
        currency = frappe.db.get_value("Company", company, "default_currency")
        other_currency = frappe.db.get_value("Company", other_company, "default_currency")
        assert currency and other_currency
        settings = track(frappe.get_single("Payroll Settings"), created=False)
        settings.email_salary_slip_to_employee = 0
        settings.payroll_based_on = "Leave"
        settings.include_holidays_in_total_working_days = 1
        native(settings.save)
        frappe.clear_document_cache("Payroll Settings", "Payroll Settings")
        basic = insert("Salary Component", salary_component=prefix + "-BASIC", salary_component_abbr="B" + prefix[-10:],
                       type="Earning", depends_on_payment_days=0, is_tax_applicable=0)
        mixed = insert("Salary Component", salary_component=prefix + "-MIXED", salary_component_abbr="M" + prefix[-10:],
                       type="Earning", depends_on_payment_days=0, is_tax_applicable=0)
        deduction = insert("Salary Component", salary_component=prefix + "-UTIL", salary_component_abbr="D" + prefix[-10:],
                           type="Deduction", depends_on_payment_days=0)
        holiday = insert("Holiday List", holiday_list_name=prefix + "-HOL", from_date=first,
                         to_date=add_days(today(), 90), holidays=[])
        main_structure = structure(company, currency, "MAIN")
        foreign_structure = structure(other_company, other_currency, "FOREIGN")
        bank_employee = employee("BANK", company, main_structure, bank=True)
        plain_employee = employee("PLAIN", company, main_structure)
        foreign_employee = employee("FOREIGN", other_company, foreign_structure)
        bulk_employee = employee("BULK", company, main_structure)
        for person in (bank_employee, plain_employee):
            insert("Jifeng Employee Supplement", employee=person.name, wage_method="计件")
        main_wage, main_as = wage(bank_employee, main_day)
        bank_slip, plain_slip = slip(bank_employee, main_day), slip(plain_employee, main_day)
        slip(foreign_employee, main_day)
        assert any(row.additional_salary == main_as.name and Decimal(str(row.amount)) == Decimal("120") for row in bank_slip.earnings)
        assert Decimal(str(bank_slip.gross_pay)) == Decimal("1120") and Decimal(str(bank_slip.net_pay)) == Decimal("1070")
        assert bank_slip.bank_account_no and not plain_slip.bank_account_no
        passed("native_salary_structure_assignments_holidays_mixed_wage_as_and_submitted_slips")

        # This temporary role is separate from every existing business role.
        # Clone current native perms before adding its explicit read/select row;
        # no existing role's source or export rights are broadened.
        reader_role = insert("Role", role_name=prefix + "-READ", desk_access=1)
        for doctype in sorted(READABLE):
            if not frappe.db.exists("Custom DocPerm", {"parent": doctype}):
                for original in frappe.get_meta(doctype).permissions:
                    values = {field: original.get(field) for field in PERMISSION_FIELDS if original.get(field) is not None}
                    insert("Custom DocPerm", parent=doctype, parenttype="DocType", parentfield="permissions", **values)
            insert("Custom DocPerm", parent=doctype, parenttype="DocType", parentfield="permissions",
                   role=reader_role.name, permlevel=0,
                   **{right: int(right in {"read", "select"}) for right in frappe.permissions.std_rights
                      if frappe.get_meta("Custom DocPerm").get_field(right)})
            changed_meta.add(doctype)
        clear_metadata()
        reader = actor("reader", ["HR User", reader_role.name])
        manager = actor("manager", ["HR Manager", reader_role.name])
        employee_reader = actor("employee", ["HR User", reader_role.name], employee=plain_employee.name)
        foreign_reader = actor("foreign", ["HR User", reader_role.name], scope=other_company)
        hidden_manager = actor("hidden-manager", ["HR Manager", reader_role.name], employee=plain_employee.name)
        child_hidden_reader = actor("child-hidden", ["HR User", reader_role.name], employee=plain_employee.name,
                                    employee_applicable="Salary Slip")
        different_currency = frappe.db.get_value("Currency", {"name": ["!=", currency]}, "name")
        assert different_currency, "需要第二个现有 Currency 字典值做真实选择权限负例"
        currency_reader = actor("currency-denied", ["HR User", reader_role.name],
                                source_permission=("Currency", different_currency))
        component_reader = actor("component-denied", ["HR User", reader_role.name],
                                 source_permission=("Salary Component", basic.name))
        evidence["fixture_permission_roles"] = roles
        current = "missing_classification_native_query"
        frappe.set_user(reader)
        missing = query()
        assert missing["total"] == 2
        assert all(row["classification_state"] == {"earnings": "unclassified", "deductions": "unclassified"}
                   and all(value is None for value in row["buckets"].values()) for row in missing["rows"])
        assert sorted(Decimal(row["net_pay"]) for row in missing["rows"]) == [Decimal("950"), Decimal("1070")]
        passed("missing_classification_preserves_native_net_and_leaves_old_paid_fields_unknown")

        current = "explicit_rules_and_complete_mixed_classification_query"
        frappe.set_user(manager)
        basic_rule = rule(basic, "basic")
        rule(deduction, "utilities")
        rule(mixed, "other_earning", "wage_breakdown", "other_earning")
        frappe.set_user(reader)
        complete = query()
        by_name = {row["name"]: row for row in complete["rows"]}
        row = by_name[bank_slip.name]
        assert row["classification_state"] == {"earnings": "verified", "deductions": "verified"}
        assert {key: Decimal(row["buckets"][key]) for key in ("basic", "piece", "time", "other_earning", "utilities")} == {
            "basic": Decimal("1000"), "piece": Decimal("80"), "time": Decimal("30"), "other_earning": Decimal("10"), "utilities": Decimal("50")}
        expanded = detail(bank_slip)
        assert {(source["doctype"], source["name"]) for source in expanded["sources"]} == {
            ("Additional Salary", main_as.name), ("Jifeng Wage Entry", main_wage.name)}
        detail(plain_slip)
        serialized = json.dumps([complete, expanded], ensure_ascii=False)
        assert prefix + "-PRIVATE-BANK" not in serialized
        evidence["bank_value_dto_cases"] = {"populated_native_bank": True, "empty_native_bank": True, "public_keys_only": True}
        passed("classified_saved_totals_mixed_exact_sources_and_no_bank_data_or_payment_claim")

        current = "company_and_employee_user_permissions"
        frappe.set_user(foreign_reader)
        rejected(lambda: history.query_salary_history(context()), frappe.PermissionError)
        assert query(context(selected_company=other_company))["total"] == 1
        frappe.set_user(employee_reader)
        scoped = query()
        assert scoped["total"] == 1 and scoped["rows"][0]["name"] == plain_slip.name
        rejected(lambda: history.get_salary_history_detail(context(), bank_slip.name, _stamp(bank_slip.modified)), frappe.PermissionError)
        frappe.set_user(child_hidden_reader)
        rejected(lambda: history.get_salary_history_detail(context(), bank_slip.name, _stamp(bank_slip.modified)), frappe.PermissionError)
        passed(current)

        current = "native_currency_and_salary_component_selection_user_permissions"
        frappe.set_user(currency_reader)
        assert not frappe.has_permission("Currency", "select", doc=frappe.get_doc("Currency", currency), throw=False)
        rejected(lambda: history.get_salary_history_detail(context(), bank_slip.name, _stamp(bank_slip.modified)), frappe.PermissionError)
        frappe.set_user(component_reader)
        assert not frappe.has_permission("Salary Component", "select", doc=frappe.get_doc("Salary Component", mixed.name), throw=False)
        rejected(lambda: history.get_salary_history_detail(context(), bank_slip.name, _stamp(bank_slip.modified)), frappe.PermissionError)
        passed(current)

        current = "native_rule_duplicates_immutability_and_savedocs_cancel_guard"
        frappe.set_user(manager)
        rejected(lambda: insert(RULE, company=company, salary_component=basic.name, parentfield="earnings",
                                bucket="time", classification_mode="component", effective_from=first),
                 (frappe.DuplicateEntryError, frappe.UniqueValidationError, frappe.ValidationError))
        def modify_rule():
            document = frappe.get_doc(RULE, basic_rule.name)
            document.bucket = "time"
            native(document.save)
        rejected(modify_rule)
        rejected(lambda: savedocs_cancel(basic_rule), frappe.PermissionError)
        frappe.set_user("Administrator")
        rejected(lambda: savedocs_cancel(basic_rule))
        rejected(lambda: savedocs_cancel(basic_rule, {"bucket": "time"}))
        rejected(lambda: frappe.delete_doc(RULE, basic_rule.name))
        rejected(lambda: frappe.rename_doc(RULE, basic_rule.name, prefix + "-RENAMED", force=True))
        evidence["cancel_endpoint"] = "frappe.desk.form.save.savedocs"
        passed(current)

        current = "future_rule_guard_includes_user_permission_hidden_submitted_periods"
        def hidden_period():
            frappe.set_user("Administrator")
            future_slip = slip(bank_employee, add_days(today(), 20))
            frappe.set_user(hidden_manager)
            assert not frappe.get_list("Salary Slip", filters={"name": future_slip.name}, fields=["name"])
            rejected(lambda: rule(basic, "time", effective=add_days(today(), 10), historical=False))
        experiment(current, hidden_period)
        frappe.set_user(manager)
        rejected(lambda: rule(basic, "time", effective=add_days(today(), -1), historical=False))
        future_rule = rule(basic, "time", effective=add_days(today(), 10), historical=False)
        frappe.set_user("Administrator")
        experiment("unused_future_rule_native_cancel", lambda: savedocs_cancel(future_rule))
        passed(current)

        for doctype, field, prop in (("Salary Slip", "earnings", "permlevel"),
                                    ("Salary Detail", "amount", "permlevel"),
                                    ("Salary Detail", "amount", "mask"),
                                    ("Salary Slip", "employee", "mask"),
                                    ("Company", "default_currency", "mask")):
            current = "native_parent_child_or_header_field_denial:" + doctype + ":" + field + ":" + prop
            tighten(doctype, field, prop, lambda: expect(lambda: history.query_salary_history(context()), frappe.PermissionError))
            passed(current)

        for doctype, field, prop, value in (("Salary Slip", "earnings", "fieldtype", "Data"),
                                           ("Salary Detail", "salary_component", "options", "Employee")):
            current = "native_saved_schema_contract_denial:" + doctype + ":" + field
            tighten(doctype, field, prop, lambda: expect(lambda: history.query_salary_history(context()), frappe.PermissionError),
                    value=value, property_type="Data")
            passed(current)

        for doctype, field in ((RULE, "bucket"), ("Additional Salary", "ref_docname"), ("Jifeng Wage Entry", "piece_amount")):
            current = "native_rule_or_source_mask_keeps_side_unknown:" + doctype + ":" + field
            def masked_sources():
                response = detail(bank_slip)
                assert response["row"]["classification_state"]["earnings"] == "unclassified"
                assert all(response["row"]["buckets"][key] is None for key in classification.EARNING_BUCKETS)
                assert response["row"]["net_pay"] is not None and response["row"]["actual_paid"] is None
                assert response["sources"] == []
                affected = [child for child in response["earnings"] if child["salary_component"] == mixed.name]
                assert affected and all(child["sources"] == [] and child["classification"] is None for child in affected)
            tighten(doctype, field, "mask", masked_sources)
            passed(current)

        frappe.set_user("Administrator")
        letter = insert("Letter Head", letter_head_name=prefix + "-LETTER", source="HTML", content="<p>工资历史验收</p>", is_default=0)
        for label, action in (
            ("salary_slip_native_update", lambda: _save_field("Salary Slip", bank_slip.name, "letter_head", letter.name)),
            ("salary_component_native_update", lambda: _save_field("Salary Component", mixed.name, "description", "验收中合法修改来源说明")),
            ("additional_salary_native_update", lambda: _save_field("Additional Salary", main_as.name, "disabled", 1)),
            ("unused_rule_native_cancel", lambda: savedocs_cancel(future_rule)),
            ("salary_slip_native_cancel", lambda: savedocs_cancel(plain_slip)),
        ):
            current = "source_change_between_complete_scans:" + label
            frappe.set_user(reader)
            experiment(current, lambda action=action: mutate_while_read(action))
            passed(current)
        for doctype, field in (("Company", "default_currency"), ("Salary Detail", "amount")):
            current = "source_field_rights_change_between_complete_scans:" + doctype
            def change_mask(doctype=doctype, field=field):
                insert("Property Setter", doctype_or_field="DocField", doc_type=doctype, field_name=field,
                       property="mask", value="1", property_type="Check", is_system_generated=0)
                changed_meta.add(doctype)
                clear_metadata({doctype})
                assert frappe.get_meta(doctype).get_field(field).mask
            frappe.set_user(reader)
            experiment(current, lambda: mutate_while_read(change_mask))
            passed(current)

        current = "immutable_native_salary_period_detail_and_wage_business_fields_reject_updates"
        frappe.set_user("Administrator")
        rejected(lambda: _save_field("Salary Slip", bank_slip.name, "end_date", add_days(main_day, 1)))
        rejected(lambda: _save_field("Salary Detail", bank_slip.earnings[0].name, "amount", 999999))
        rejected(lambda: _save_field("Jifeng Wage Entry", main_wage.name, "extra_amount", 999999))
        passed(current)

        current = "duplicate_additional_salary_same_wage_native_guard"
        def duplicate_as():
            payload = frappe.get_doc("Additional Salary", main_as.name).as_dict()
            for field in ("name", "creation", "modified", "owner", "modified_by"):
                payload.pop(field, None)
            payload["docstatus"] = 0
            native(frappe.get_doc(payload).insert)
        rejected(duplicate_as)
        evidence["duplicate_wage_different_as"] = "native Additional Salary validator rejects duplicate source before it can be persisted"
        passed(current)

        current = "duplicate_salary_detail_same_additional_salary"
        def duplicate_details():
            frappe.set_user("Administrator")
            day = add_days(main_day, 1)
            duplicate_wage, duplicate_source = wage(bank_employee, day)
            document = insert("Salary Slip", employee=bank_employee.name, company=company, posting_date=day,
                              start_date=day, end_date=day, payroll_frequency="Daily", currency=currency, exchange_rate=1)
            matching = [child for child in document.earnings if child.additional_salary == duplicate_source.name]
            assert len(matching) == 1
            row = matching[0].as_dict()
            for field in ("name", "creation", "modified", "owner", "modified_by", "parent", "parenttype", "parentfield", "idx", "docstatus"):
                row.pop(field, None)
            document.append("earnings", row)
            try:
                native(document.save)
                native(document.submit)
            except frappe.ValidationError:
                evidence["duplicate_detail_same_as"] = "native Salary Slip controller rejected duplicate details"
                return
            document.reload()
            actual = [child for child in document.earnings if child.additional_salary == duplicate_source.name]
            if len(actual) < 2:
                assert len(actual) == 1
                evidence["duplicate_detail_same_as"] = "native Salary Slip controller normalized duplicate details to one source"
                return
            frappe.set_user(reader)
            response = detail(document)
            assert response["row"]["classification_state"]["earnings"] == "unclassified"
            assert all(response["row"]["buckets"][key] is None for key in classification.EARNING_BUCKETS)
            duplicate_rows = [child for child in response["earnings"] if child["salary_component"] == mixed.name]
            assert len(duplicate_rows) >= 2 and all(child["classification"] is None and child["sources"] == [] for child in duplicate_rows)
            assert not any(source["name"] in {duplicate_source.name, duplicate_wage.name} for source in response["sources"])
            evidence["duplicate_detail_same_as"] = "native saved duplicate source leaves entire earning side unclassified"
        experiment(current, duplicate_details)
        passed(current)

        current = "native_202_saved_salary_slips_and_200_scan_batches"
        frappe.set_user("Administrator")
        bulk_slips = [slip(bulk_employee, add_days(first, index)) for index in range(202)]
        assert len({document.name for document in bulk_slips}) == 202
        assert frappe.db.count("Salary Slip", {"employee": bulk_employee.name, "docstatus": 1}) == 202
        bulk_reader = actor("bulk", ["HR User", reader_role.name], employee=bulk_employee.name)
        frappe.set_user(bulk_reader)
        scanned, original_list = [], frappe.get_list
        def observe_batches(doctype, *args, **kwargs):
            result = original_list(doctype, *args, **kwargs)
            if doctype == "Salary Slip" and "name" not in (kwargs.get("filters") or {}):
                scanned.append({"start": kwargs.get("limit_start", 0), "limit": kwargs.get("limit_page_length"), "returned": len(result)})
            return result
        with patch.object(frappe, "get_list", observe_batches):
            pages = [query(context(search=bulk_employee.name), page=index, page_size=100) for index in (1, 2, 3)]
        assert [len(page["rows"]) for page in pages] == [100, 100, 2]
        assert all(page["total"] == 202 and page["total_pages"] == 3 for page in pages)
        assert {row["name"] for page in pages for row in page["rows"]} == {document.name for document in bulk_slips}
        assert any(item == {"start": 0, "limit": 200, "returned": 200} for item in scanned)
        assert any(item["start"] == 200 and item["limit"] == 200 and item["returned"] == 2 for item in scanned)
        evidence["real_salary_slip_paging"] = {"saved_submitted_slips": 202, "page_counts": [100, 100, 2], "scan_batches": scanned}
        passed(current)

        current = "explicit_date_basis_and_stale_detail_version"
        selected = query(context(search=bulk_employee.name, start=first, end=first, basis="period_end", use_date=True))
        assert selected["total"] == 1
        rejected(lambda: history.get_salary_history_detail(context(search=bulk_employee.name), bulk_slips[0].name,
                                                           "2000-01-01 00:00:00"))
        passed(current)
    except Exception as error:
        primary = error
        error.add_note("工资历史正式验收失败门槛：" + current)
        error.add_note("已通过门槛：" + ",".join(checks))
        raise
    finally:
        def collect():
            for document, created in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                    if created:
                        owned.add((document.doctype, document.name))
                for child in document.get_all_children():
                    if child.name:
                        records.add((child.doctype, child.name))
                        if created:
                            owned.add((child.doctype, child.name))
        def rollback():
            frappe.set_user("Administrator")
            _profile_rollback(point, callbacks_before)
        def restore():
            clear_metadata()
            frappe.clear_document_cache("Payroll Settings", "Payroll Settings")
            # Native Role.after_insert invalidates Admin once; subsequent native
            # permission checks cache the temporary Role again. Rollback does
            # not invoke Role.after_delete, so invalidate only these role keys.
            for actor in {original_user, "Administrator"}:
                frappe.cache.hdel("roles", actor)
            _profile_restore_caller(original_user, original_test, users, records, callbacks_before, context_before)
            _restore_response(response_before)
        def physical():
            nonlocal physical_after
            physical_after = _physical_snapshot()
            return physical_after == physical_before
        def caller_restored():
            actual = {"callbacks": _callbacks(), "local_context": _local_context(),
                      "user": frappe.session.user, "in_test": frappe.in_test,
                      "roles": sorted(frappe.get_roles(original_user)),
                      "transaction_control": frappe.db._disable_transaction_control,
                      "permission_response": _permission_response_state()}
            expected = {"callbacks": callbacks_before, "local_context": context_before,
                        "user": original_user, "in_test": original_test, "roles": original_roles,
                        "transaction_control": transaction_control, "permission_response": response_before}
            differences = {key: {"unchanged": actual[key] == expected[key],
                                "before_sha256": _digest(expected[key]), "after_sha256": _digest(actual[key])}
                           for key in expected}
            added, removed = set(actual["roles"]) - set(original_roles), set(original_roles) - set(actual["roles"])
            differences["roles"].update(added_count=len(added), removed_count=len(removed),
                added_sha256=_digest(sorted(added)), removed_sha256=_digest(sorted(removed)))
            evidence["caller_restore_diagnostics"] = differences
            if not all(value["unchanged"] for value in differences.values()):
                raise AssertionError("调用方受管状态恢复失败：" + json.dumps(differences, sort_keys=True, ensure_ascii=False))
            return True
        _profile_audit([
            ("collect_exact_native_owned_rows", collect),
            ("rollback_native_fixtures_permissions_and_callbacks", rollback),
            ("restore_metadata_masks_users_settings_documents_and_caller", restore),
            ("all_owned_fixture_rows_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in owned)),
            ("all_auth_business_versions_permissions_settings_series_unchanged", lambda: _contents() == contents_before),
            ("all_stock_and_gl_content_unchanged", lambda: _ledger_snapshot() == ledger_before),
            ("all_source_files_unchanged", lambda: _sources() == source_before),
            ("complete_932_physical_contents_schema_indexes_engines_unchanged", physical),
            ("caller_roles_context_callbacks_transaction_control_restored", caller_restored),
        ], primary=primary)
    checks.append("complete_932_business_auth_metadata_settings_roles_and_callbacks_rollback")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "evidence": evidence,
            "native_source_sha256": source_before, "physical_before": physical_before, "physical_after": physical_after,
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(), "business_data": "rolled_back",
            "schema_changed": False, "gui_verified": False, "http_transport_verified": False,
            "multi_connection_lock_verified": False,
            "concurrency": "serial native source mutations only; concurrent HRMS writers require a separate probe"}


def _save_field(doctype, name, field, value):
    """Mutations deliberately invoke the real controller, including native guards."""
    document = frappe.get_doc(doctype, name)
    original_modified = _stamp(document.modified)
    document.set(field, value)
    document.save()
    document.reload()
    actual = document.get(field)
    assert (getdate(actual) == getdate(value) if field in ("start_date", "end_date") else actual == value)
    assert _stamp(document.modified) != original_modified
