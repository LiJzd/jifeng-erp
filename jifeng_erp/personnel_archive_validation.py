"""Serial native classic personnel acceptance; always rolls back the whole batch.

Run smoke(company, other_company) once from an initialized direct Python process
after schema/mixins are migrated. No bench execute retry, permission override,
controller monkeypatch, account repair, or permanent fixtures. GUI is not tested.
"""

from copy import deepcopy

import frappe
from frappe.utils import today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_archive import HISTORIES, SUPPLEMENT, init_archive, read_archive, save_archive
from jifeng_erp.personnel_changes_validation import (
    _callbacks, _digest, _employee_contents, _restore_callbacks, _tab_counts, _versions,
)
from jifeng_erp.personnel_separation_validation import (
    _local_context, _restore_local_context, _user_contents,
)


def _supplements(exclude=None):
    table = "`tabJifeng Employee Supplement`"
    result = {"parent": frappe.db.sql("SELECT * FROM " + table + " ORDER BY name", as_dict=True), "children": {}}
    if exclude:
        result["parent"] = [row for row in result["parent"] if row.name != exclude]
    for field in frappe.get_meta(SUPPLEMENT).get_table_fields():
        child = "`tab" + field.options.replace("`", "``") + "`"
        result["children"][field.fieldname] = frappe.db.sql(
            "SELECT * FROM " + child + " WHERE parenttype=%s AND parentfield=%s ORDER BY name",
            (SUPPLEMENT, field.fieldname), as_dict=True)
        if exclude:
            result["children"][field.fieldname] = [row for row in result["children"][field.fieldname] if row.parent != exclude]
    return result


def smoke(company, other_company):
    """Root schedules this once in the exclusive rollback-only native window."""
    frappe.only_for("System Manager")
    assert company != other_company and all(frappe.db.exists("Company", name) for name in (company, other_company))
    assert "jifeng_erp.personnel_archive.ClassicPersonnelEmployeeMixin" in frappe.get_hooks(
        "extend_doctype_class").get("Employee", []), "classic Employee mixin not installed"
    meta = frappe.get_meta(SUPPLEMENT)
    assert meta.get_field("legacy_base_salary").fieldtype == "Currency"
    assert meta.get_field("legacy_time_value").fieldtype == "Float"
    for field, (doctype, columns) in HISTORIES.items():
        assert meta.get_field(field).fieldtype == "Table" and meta.get_field(field).options == doctype
        assert all(frappe.get_meta(doctype).get_field(column) for column in columns)
    tables, counts = _tab_counts()
    writable = {"Employee", SUPPLEMENT, "User", "User Permission", "Version", "Series", "Contact",
                "Contact Email", "Contact Phone", "Dynamic Link", "DocShare", "DefaultValue",
                "Department", "Designation", "Email Queue", "Notification Log"}
    for doctype in ("Employee", SUPPLEMENT, "User"):
        writable.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    assert all(tables["tab" + doctype] == "InnoDB" for doctype in writable)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    staff_before, users_before = _digest(_employee_contents()), _digest(_user_contents())
    supplements_before, ledger_before = _digest(_supplements()), _ledger_snapshot()
    logs_before = set(frappe.get_all("Error Log", pluck="name"))
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    prefix = "JF-QA-ARCHIVE-" + frappe.generate_hash(length=10)
    point = "jf_archive_" + frappe.generate_hash(length=10)
    checks, records, users, objects = [], set(), [], []
    current = "fixture_setup"
    _begin_rollback_only(point)

    def insert(doctype, **values):
        doc = frappe.get_doc({"doctype": doctype, **values})
        objects.append(doc)
        doc.insert()
        records.add((doctype, doc.name))
        return doc

    def permit(user, doctype, values):
        for value in values:
            insert("User Permission", user=user, allow=doctype, for_value=value, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user)

    def native(operation):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return operation()
        finally:
            frappe.in_test = previous

    def request(name, **values):
        view = read_archive(name)
        return {"name": name, "employee_modified": view["employee_modified"],
                "supplement_modified": view["supplement_modified"], **values}

    def contents():
        return (_digest(_employee_contents()), _digest(_supplements()), _digest(_user_contents()),
                _ledger_snapshot(), _tab_counts(tables)[1])

    def reject(operation, errors, text=None):
        before, callbacks, context = contents(), _callbacks(), _local_context()
        try:
            native(operation)
        except errors as error:
            if text:
                assert text in str(error), "rejected for an unexpected guard"
        else:
            raise AssertionError("operation unexpectedly succeeded")
        # The RPC's own atomic wrapper must restore state, without a helper
        # nested rollback concealing a partial Employee/Version save.
        assert contents() == before, "rejected API left partial native changes"
        assert _callbacks() == callbacks and _local_context() == context

    def passed(label):
        checks.append(label)
        assert _ledger_snapshot() == ledger_before
        assert set(frappe.get_all("Error Log", pluck="name")) == logs_before

    try:
        frappe.set_user("Administrator")
        linked = insert("User", email=prefix.lower() + "-linked@example.invalid", first_name=prefix + "-account",
                        send_welcome_email=0, roles=[{"role": "Employee"}])
        users.append(linked.name)
        department = insert("Department", department_name=prefix + "-department", company=company)
        foreign_department = insert("Department", department_name=prefix + "-foreign", company=other_company)
        designation = insert("Designation", designation_name=prefix + "-designation")
        common = {"gender": "Male", "date_of_birth": "1990-01-01", "date_of_joining": "2020-01-01", "status": "Active"}
        employee = insert("Employee", first_name=prefix + "-employee", employee_number=prefix + "-old-id",
            company=company, user_id=linked.name, department=department.name, designation=designation.name,
            ctc=99000, education=[{"school_univ": prefix + "-school", "qualification": "archive-test"}],
            external_work_history=[{"company_name": prefix + "-prior-employer", "designation": "原岗位"}], **common)
        supplement = insert(SUPPLEMENT, employee=employee.name, legacy_title="原职称", notes="原备注",
            work_history=[{"project": "原项目", "work_content": "原内容", "title": "原历史职务"}])
        peer = insert("Employee", first_name=prefix + "-peer", company=company, **common)
        foreign = insert("Employee", first_name=prefix + "-other-company", company=other_company, **common)
        linked = frappe.get_doc("User", linked.name)
        linked.enabled = 0
        linked.save()
        assert frappe.db.get_value("Employee", employee.name, "status") == "Active"
        actors = {}
        for kind, role, companies, scoped in (
                ("hr", "HR Manager", [company], True),
                ("cross", "HR Manager", [company, other_company], True),
                ("create", "HR Manager", [company], False),
                ("low", "HR User", [company], True)):
            actor = insert("User", email=prefix.lower() + "-" + kind + "@example.invalid",
                           first_name=prefix + "-" + kind, send_welcome_email=0, roles=[{"role": role}])
            users.append(actor.name)
            actors[kind] = actor.name
            permit(actor.name, "Company", companies)
            if scoped:
                permit(actor.name, "Employee", [employee.name])
        protected_users = _digest(_user_contents())
        protected_peer = _digest(_employee_contents(name=peer.name))
        protected_other = _digest(_employee_contents(exclude=employee.name))
        protected_supplements = _digest(_supplements(exclude=supplement.name))
        native_history = deepcopy(frappe.get_doc("Employee", employee.name).as_dict())
        evidence = {"prefix": prefix, "actual_roles": {key: sorted(frappe.get_roles(value)) for key, value in actors.items()},
                    "gui_verified": False, "fields_with_new_permlevels": "not manufactured",
                    "post_employee_failure": "natural invalid archive row date order; no controller fault injection"}
        assert all("System Manager" not in roles for roles in evidence["actual_roles"].values())

        current = "native_hr_manager_read_and_real_field_metadata"
        frappe.set_user(actors["hr"])
        view = read_archive(employee.name)
        assert view["can_write"] and view["supplement_name"] == supplement.name
        assert view["field_meta"]["employee"]["company"]["read_only"]
        assert view["field_meta"]["supplement"]["legacy_time_value"]["fieldtype"] == "Float"
        assert view["field_meta"]["histories"]["work_history"]["fields"]["project"]["fieldtype"] == "Data"
        passed(current)

        current = "native_old_fields_three_history_tables_and_stable_ids"
        old_id = view["histories"]["work_history"][0]["name"]
        result = native(lambda: save_archive(request(employee.name,
            employee={"first_name": prefix + "-edited", "date_of_birth": "1991-01-02"},
            supplement={"legacy_base_salary": 1800.25, "legacy_time_value": 12.5, "legacy_city": "旧城市",
                "legacy_account": "000001234", "legacy_notes_text": "独立第二备注", "legacy_address": "旧单地址",
                "legacy_marital_status": "旧原文", "legacy_introduction": "纯文本\n第二行", "term_type": "临时"},
            histories={"work_history": [{"name": old_id, "work_content": "编辑内容"}],
                "salary_history": [{"project": "旧项目", "content": "归档调薪", "wage": 2500.5,
                                    "from_date": "2020-01-01", "to_date": "2021-01-01", "title": "旧岗位"}],
                "promotion_history": [{"project": "旧项目", "work_content": "归档升职", "title": "旧升职",
                                        "from_date": "2021-01-01", "to_date": "2022-01-01"}]})))
        assert result["histories"]["work_history"][0]["name"] == old_id
        assert result["histories"]["work_history"][0]["project"] == "原项目"
        assert result["supplement"]["notes"] == "原备注" and result["supplement"]["legacy_account"] == "000001234"
        actual = frappe.get_doc("Employee", employee.name).as_dict()
        assert actual["ctc"] == native_history["ctc"]
        assert all(actual[field] == native_history[field] for field in ("education", "external_work_history", "internal_work_history"))
        assert _digest(_user_contents()) == protected_users and frappe.db.get_value("User", linked.name, "enabled") == 0
        assert _digest(_employee_contents(name=peer.name)) == protected_peer
        assert _digest(_employee_contents(exclude=employee.name)) == protected_other
        assert _digest(_supplements(exclude=supplement.name)) == protected_supplements
        passed(current)

        current = "native_versions_exist_and_no_formal_promotion_or_salary_assignment_created"
        versions = _versions("Employee", employee.name)
        supplement_versions = _versions(SUPPLEMENT, supplement.name)
        assert versions and supplement_versions, "native Version was not created"
        assert any(any(change[0] == "first_name" for change in version.get("changed", []))
                   for version in versions.values()), "native employee Version lacks the actual name change"
        assert any(any(change[0] == "legacy_base_salary" for change in version.get("changed", []))
                   for version in supplement_versions.values()), "native supplement Version lacks the archive amount change"
        evidence["employee_version_names"] = sorted(versions)
        evidence["supplement_version_names"] = sorted(supplement_versions)
        assert frappe.db.count("Employee Promotion") == counts["tabEmployee Promotion"]
        assert frappe.db.count("Salary Structure Assignment") == counts["tabSalary Structure Assignment"]
        passed(current)

        current = "native_post_employee_supplement_failure_rolls_back_both_and_versions"
        reject(lambda: save_archive(request(employee.name, employee={"first_name": prefix + "-must-rollback"},
            histories={"salary_history": [{"content": "invalid range", "from_date": "2022-01-01", "to_date": "2021-01-01"}]})),
            frappe.ValidationError, "开始日期")
        passed(current)

        current = "native_hr_user_lacks_supplement_permission_no_partial_save"
        frappe.set_user(actors["low"])
        assert frappe.has_permission("Employee", "write") and not frappe.has_permission(SUPPLEMENT, "write")
        view_user = actors["hr"]
        frappe.set_user(view_user)
        low_payload = request(employee.name, employee={"first_name": prefix + "-denied"})
        frappe.set_user(actors["low"])
        reject(lambda: save_archive(low_payload), frappe.PermissionError)
        passed(current)

        current = "native_hidden_employee_and_company_read_denied_without_snapshot_leak"
        frappe.set_user(actors["hr"])
        for name in (peer.name, foreign.name):
            reject(lambda name=name: read_archive(name), frappe.PermissionError)
        reject(lambda: init_archive(other_company), frappe.PermissionError)
        passed(current)

        current = "native_selectable_foreign_department_rejected_by_company_contract"
        frappe.set_user(actors["cross"])
        frappe.get_doc("Department", foreign_department.name).check_permission("select")
        reject(lambda: save_archive(request(employee.name, employee={"department": foreign_department.name})),
               frappe.ValidationError, "部门")
        passed(current)

        current = "native_forged_context_lifecycle_history_id_and_private_image_rejected"
        frappe.set_user(actors["hr"])
        for values in ({"user_id": linked.name}, {"status": "Left"}, {"flags": {"classic": True}}):
            reject(lambda values=values: save_archive(request(employee.name, employee=values)), frappe.ValidationError)
        reject(lambda: save_archive(request(employee.name, histories={"work_history": [{"name": "another-parent-row"}]})),
               frappe.ValidationError, "标识")
        reject(lambda: save_archive(request(employee.name, employee={"image": "/private/files/unbound-person-photo.png"})),
               frappe.ValidationError, "当前档案")
        passed(current)

        current = "native_stale_both_document_timestamps_rejected"
        for key in ("employee_modified", "supplement_modified"):
            payload = request(employee.name, employee={"first_name": prefix + "-stale"})
            payload[key] = "2000-01-01T00:00:00"
            reject(lambda payload=payload: save_archive(payload), frappe.TimestampMismatchError)
        passed(current)

        current = "native_new_archive_explicit_required_fields_defaults_and_no_user_creation"
        frappe.set_user(actors["create"])
        init = init_archive(company)
        assert init["supplement"]["term_type"] == "长期" and not init["supplement"]["wage_method"]
        reject(lambda: save_archive({"employee": {"company": company, "first_name": prefix + "-missing-birthday"}}),
               frappe.MandatoryError)
        new = native(lambda: save_archive({"employee": {"company": company, "first_name": prefix + "-new",
            "gender": "Male", "date_of_birth": "1990-01-01", "date_of_joining": today()}}))
        records.update((("Employee", new["name"]), (SUPPLEMENT, new["supplement_name"])))
        assert new["employee"]["status"] == "Active" and new["supplement"]["term_type"] == "长期"
        assert not frappe.db.get_value("Employee", new["name"], "user_id")
        assert _digest(_user_contents()) == protected_users
        passed(current)
        evidence["native_counts_during"] = {doctype: frappe.db.count(doctype) for doctype in
            ("Employee", SUPPLEMENT, "Version", "User", "Email Queue", "Notification Log", "Error Log")}
    except Exception as error:
        error.add_note("native classic archive smoke failed at gate: " + current)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            for doctype, name in list(records):
                if doctype in ("Employee", SUPPLEMENT) and frappe.db.exists(doctype, name):
                    doc = frappe.get_doc(doctype, name)
                    records.update((row.doctype, row.name) for row in doc.get_all_children() if row.name)
        finally:
            try:
                _end_rollback_only(point)
            finally:
                _restore_callbacks(callbacks_before)
                _restore_local_context(context_before)
                frappe.in_test = original_test
                frappe.set_user(original_user)
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
            assert all(not frappe.db.exists(doctype, name) for doctype, name in records)
            assert _tab_counts(tables)[1] == counts
            assert _digest(_employee_contents()) == staff_before
            assert _digest(_supplements()) == supplements_before
            assert _digest(_user_contents()) == users_before
            assert _ledger_snapshot() == ledger_before
            assert set(frappe.get_all("Error Log", pluck="name")) == logs_before
    checks.extend(("rollback_all_tab_counts_and_all_archive_employee_children", "rollback_all_accounts_auth_roles_up_ledger_zero_error_log"))
    return {"passed": True, "business_data": "rolled_back", "gate_count": len(checks), "checks": checks,
            "evidence": evidence, "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "employee_hash_before": staff_before, "employee_hash_after": _digest(_employee_contents()),
            "supplement_hash_before": supplements_before, "supplement_hash_after": _digest(_supplements()),
            "user_hash_before": users_before, "user_hash_after": _digest(_user_contents()),
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(), "gui_verified": False}
