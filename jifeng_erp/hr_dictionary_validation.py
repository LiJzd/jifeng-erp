"""Serial native HR vocabulary acceptance, always rolled back; no GUI or commits."""

import frappe
from frappe.utils import today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.hr_dictionary import (
    DOCTYPE, FIELDS, init_dictionary, list_dictionary, read_dictionary, resolve_creation, save_dictionary,
)
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import (
    _callbacks, _digest, _restore_callbacks, _tab_counts, _versions,
)
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context


def _native_contents():
    """Full rows/installed children are compared internally; output only hashes."""
    doctypes = {DOCTYPE, "Employee", "Jifeng Employee Supplement", "Version", "User", "User Permission", "Series"}
    for doctype in tuple(doctypes):
        if frappe.db.exists("DocType", doctype):
            doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result = {doctype: _digest(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
                                         + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)}
    result["__Auth"] = _digest(frappe.db.sql("SELECT * FROM `__Auth` ORDER BY doctype, name, fieldname", as_dict=True))
    return result


def smoke(company, other_company):
    """Root invokes once after schema migration, in its exclusive native window."""
    frappe.only_for("System Manager")
    assert company != other_company
    assert all(frappe.db.exists("Company", name) for name in (company, other_company))
    meta = frappe.get_meta(DOCTYPE)
    assert meta.track_changes and meta.get_field("dictionary_key").unique
    assert all(meta.get_field(field).set_only_once for field in ("company", "dictionary_type", "value"))
    tables, counts = _tab_counts()
    before, ledger_before = _native_contents(), _ledger_snapshot()
    callbacks_before, context_before = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    prefix, point = "JF-QA-HR-DICT-" + frappe.generate_hash(length=10), "jf_hr_dictionary_" + frappe.generate_hash(length=8)
    users, documents, checks, actual_roles = [], [], [], {}
    current = "fixture_setup"
    _begin_rollback_only(point)

    def native(action):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = previous

    def insert(doctype, **values):
        doc = frappe.get_doc({"doctype": doctype, **values})
        documents.append(doc)
        return doc.insert()

    def actor(kind, role, companies, dictionary_name=None):
        user = insert("User", email=prefix.lower() + "-" + kind + "@example.invalid", first_name=prefix + "-" + kind,
                      send_welcome_email=0, roles=[{"role": role}])
        users.append(user.name)
        for allowed in companies:
            insert("User Permission", user=user.name, allow="Company", for_value=allowed, apply_to_all_doctypes=1)
        if dictionary_name:
            insert("User Permission", user=user.name, allow=DOCTYPE, for_value=dictionary_name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        actual_roles[kind] = sorted(frappe.get_roles(user.name))
        return user.name

    def payload(dictionary_type, value=None, company_name=company):
        return {"company": company_name, "dictionary_type": dictionary_type, "value": value or prefix + "-" + dictionary_type, "inactive": 0}

    def update(archive, **values):
        return save_dictionary({field: archive[field] for field in ("name", "version", *FIELDS)} | values)

    def rejected(action, errors=frappe.ValidationError, atomic=False, text=None, forbidden=None):
        local_point = "jf_hr_dictionary_rejection"
        contents, callbacks, context, ledger = _native_contents(), _callbacks(), _local_context(), _ledger_snapshot()
        frappe.db.savepoint(local_point)
        try:
            native(action)
        except errors as error:
            if text:
                assert text in str(error), "rejection did not match the expected HR dictionary guard"
            if forbidden:
                assert forbidden not in str(error), "rejection leaked an unreadable dictionary identity"
            if atomic:
                # Prove the API's own rollback before a fixture savepoint can
                # conceal partial records, versions or native naming changes.
                assert _native_contents() == contents
                assert _callbacks() == callbacks and _local_context() == context
        else:
            raise AssertionError("Invalid HR dictionary operation was accepted")
        finally:
            frappe.db.rollback(save_point=local_point)
            _restore_callbacks(callbacks)
            _restore_local_context(context)
        assert _ledger_snapshot() == ledger

    def passed(label):
        checks.append(label)
        assert _ledger_snapshot() == ledger_before

    try:
        frappe.set_user("Administrator")
        manager = actor("manager", "HR Manager", [company])
        reader = actor("reader", "HR User", [company])
        outsider = actor("outside", "HR User", [other_company])
        accountant = actor("accounts", "Accounts User", [company])
        assert all("System Manager" not in roles for roles in actual_roles.values())

        current = "three_persistent_types_and_distinct_company_identity"
        frappe.set_user(manager)
        initialized = native(lambda: init_dictionary("专长"))
        assert initialized["company"] is None and initialized["name"] is None and initialized["version"] is None
        assert initialized["can_write"] and initialized["inactive"] == 0
        entries = {kind: native(lambda kind=kind: save_dictionary(payload(kind))) for kind in ("专长", "文化", "民族")}
        for kind, archive in entries.items():
            assert set(archive).issuperset({"name", *FIELDS, "version", "field_meta", "can_write"})
            assert archive["dictionary_type"] == kind and archive["can_write"] and archive["inactive"] == 0
            assert archive["field_meta"]["value"]["read_only"] and isinstance(archive["version"], str)
            assert native(lambda kind=kind: list_dictionary(kind, company, prefix)) == [{
                "name": archive["name"], "value": archive["value"], "inactive": 0}]
            documents.append(frappe.get_doc(DOCTYPE, archive["name"]))
        frappe.set_user("Administrator")
        foreign = native(lambda: save_dictionary(payload("专长", entries["专长"]["value"], other_company)))
        documents.append(frappe.get_doc(DOCTYPE, foreign["name"]))
        assert foreign["name"] != entries["专长"]["name"]
        passed(current)

        current = "native_manager_creation_validation_and_company_scope"
        frappe.set_user(manager)
        direct = native(lambda: insert(DOCTYPE, company=company, dictionary_type="专长", value=prefix + "-native", inactive=0))
        assert direct.dictionary_key and direct.value == prefix + "-native"
        native(lambda: setattr(direct, "inactive", 1))
        versions_before = len(_versions(DOCTYPE, direct.name))
        native(direct.save)
        assert len(_versions(DOCTYPE, direct.name)) > versions_before
        rejected(lambda: insert(DOCTYPE, company=company, dictionary_type="Religion", value=prefix + "-invalid", inactive=0))
        rejected(lambda: insert(DOCTYPE, company=company, dictionary_type="民族", value=" ", inactive=0))
        rejected(lambda: save_dictionary(payload("专长") | {"dictionary_key": "client-forged"}), atomic=True)
        rejected(lambda: save_dictionary(payload("专长") | {"inactive": "非法Check"}), atomic=True)
        rejected(lambda: insert(DOCTYPE, company=other_company, dictionary_type="民族", value=prefix + "-cross", inactive=0), frappe.PermissionError)
        passed(current)

        current = "personnel_raw_text_is_not_changed_by_dictionary_actions"
        gender = frappe.db.get_value("Gender", {}, "name")
        assert gender
        common = {"gender": gender, "date_of_birth": "1990-01-01", "date_of_joining": today(), "status": "Active"}
        referenced = insert("Employee", first_name=prefix + "-referenced", company=company, **common)
        raw = insert("Employee", first_name=prefix + "-raw", company=company, **common)
        archives = [insert("Jifeng Employee Supplement", employee=referenced.name,
                           specialty=entries["专长"]["value"], education_level=entries["文化"]["value"], ethnicity=entries["民族"]["value"]),
                    insert("Jifeng Employee Supplement", employee=raw.name,
                           specialty=" 既有专长原文未登记 " + prefix, education_level="既有文化原文-" + prefix, ethnicity="既有民族原文-" + prefix)]
        people_before = {key: value for key, value in _native_contents().items() if key.startswith("Employee") or key.startswith("Jifeng Employee") or key.startswith("Jifeng Legacy")}
        fields_before = [{field: doc.get(field) for field in ("specialty", "education_level", "ethnicity")} for doc in archives]
        frappe.set_user(manager)
        original = entries["专长"]
        versions_before = len(_versions(DOCTYPE, original["name"]))
        stopped = native(lambda: update(original, inactive=1))
        assert len(_versions(DOCTYPE, original["name"])) > versions_before, "启停未生成原生Version"
        assert native(lambda: list_dictionary("专长", company, prefix)) == []
        assert native(lambda: list_dictionary("专长", company, original["value"], include_inactive=True)) == [{
            "name": stopped["name"], "value": stopped["value"], "inactive": 1}]
        assert native(lambda: read_dictionary(original["name"], "专长"))["inactive"] == 1
        restarted = native(lambda: update(stopped, inactive=0))
        entries["专长"] = restarted
        assert restarted["inactive"] == 0
        assert fields_before == [{field: frappe.get_doc(doc.doctype, doc.name).get(field) for field in ("specialty", "education_level", "ethnicity")} for doc in archives]
        assert people_before == {key: value for key, value in _native_contents().items() if key in people_before}
        passed(current)

        current = "native_uniqueness_immutable_identity_and_response_version"
        rejected(lambda: save_dictionary(payload("专长")), atomic=True, text="词条已存在或身份冲突", forbidden=entries["专长"]["name"])
        rejected(lambda: update(original, inactive=1), frappe.TimestampMismatchError, atomic=True)
        latest = entries["专长"]
        again = native(lambda: update(latest, inactive=1))
        entries["专长"] = native(lambda: update(again, inactive=0))
        for field, value in (("company", other_company), ("dictionary_type", "文化"), ("value", "不允许改词条-" + prefix)):
            # For another-company identity tampering, use Administrator so the
            # invariant is proved independently of a Company scope rejection.
            frappe.set_user("Administrator")
            rejected(lambda field=field, value=value: update(entries["专长"], **{field: value}), atomic=True,
                     text="公司、类型及内容不能修改")
            document = frappe.get_doc(DOCTYPE, entries["专长"]["name"])
            document.set(field, value)
            rejected(document.save, text="公司、类型及内容不能修改")
        document = frappe.get_doc(DOCTYPE, entries["专长"]["name"])
        document.dictionary_key = "client-forged-key"
        native(document.save)
        assert document.dictionary_key != "client-forged-key"
        entries["专长"] = native(lambda: read_dictionary(document.name))
        passed(current)

        current = "permission_hidden_duplicate_attempt_does_not_disclose_identity"
        frappe.set_user("Administrator")
        limited_manager = actor("limited-manager", "HR Manager", [company], entries["专长"]["name"])
        frappe.set_user(limited_manager)
        assert native(lambda: resolve_creation(company, "文化", entries["文化"]["value"])) == {"status": "not_found"}
        # Native self User Permissions can reject new creation before its
        # unique index is reached. Either native denial or a sanitized unique
        # collision must preserve privacy and leave no partial records.
        rejected(lambda: save_dictionary(payload("文化")), (frappe.PermissionError, frappe.ValidationError), atomic=True,
                 forbidden=entries["文化"]["name"])
        passed(current)

        current = "hr_user_read_select_without_private_supplement_or_write"
        frappe.set_user(reader)
        assert not frappe.has_permission("Jifeng Employee Supplement", "read")
        assert not frappe.has_permission(DOCTYPE, "create") and not frappe.has_permission(DOCTYPE, "write")
        blank = native(lambda: init_dictionary("民族"))
        assert not blank["can_write"] and blank["company"] is None
        read = native(lambda: read_dictionary(entries["专长"]["name"]))
        assert not read["can_write"] and "dictionary_key" not in read
        assert native(lambda: list_dictionary("专长", company, prefix)) == [{"name": read["name"], "value": read["value"], "inactive": 0}]
        rejected(lambda: save_dictionary(payload("专长", prefix + "-unauthorized-create")), frappe.PermissionError, atomic=True)
        rejected(lambda: update(read, inactive=1), frappe.PermissionError, atomic=True)
        passed(current)

        current = "company_and_dictionary_user_permissions_before_identity_return"
        rejected(lambda: list_dictionary("专长", other_company), frappe.PermissionError)
        rejected(lambda: read_dictionary(foreign["name"]), frappe.PermissionError)
        rejected(lambda: resolve_creation(other_company, "专长", foreign["value"]), frappe.PermissionError)
        frappe.set_user(outsider)
        rejected(lambda: list_dictionary("专长", company), frappe.PermissionError)
        frappe.set_user("Administrator")
        scoped = actor("scoped", "HR User", [company], entries["专长"]["name"])
        frappe.set_user(scoped)
        assert native(lambda: list_dictionary("文化", company, prefix)) == []
        assert native(lambda: resolve_creation(company, "文化", entries["文化"]["value"])) == {"status": "not_found"}
        rejected(lambda: read_dictionary(entries["文化"]["name"]), frappe.PermissionError)
        passed(current)

        current = "unknown_creation_resolution_is_read_only_and_permission_scoped"
        frappe.set_user(reader)
        snapshot = _native_contents()
        found = native(lambda: resolve_creation(company, "民族", entries["民族"]["value"]))
        assert found["status"] == "found" and found["archive"]["name"] == entries["民族"]["name"]
        assert native(lambda: resolve_creation(company, "民族", prefix + "-not-created")) == {"status": "not_found"}
        assert _native_contents() == snapshot
        passed(current)

        current = "delete_refused_for_referenced_and_unused_words_including_administrator"
        frappe.set_user("Administrator")
        unused = native(lambda: save_dictionary(payload("民族", prefix + "-unused")))
        documents.append(frappe.get_doc(DOCTYPE, unused["name"]))
        for name in (entries["民族"]["name"], unused["name"]):
            rejected(lambda name=name: frappe.delete_doc(DOCTYPE, name), text="停用保留历史，不能删除")
            assert frappe.db.exists(DOCTYPE, name)
        passed(current)

        current = "guest_and_non_hr_read_rejected"
        for user in (accountant, "Guest"):
            frappe.set_user(user)
            rejected(lambda: list_dictionary("专长", company), frappe.PermissionError)
            rejected(lambda: read_dictionary(entries["专长"]["name"]), frappe.PermissionError)
            rejected(lambda: resolve_creation(company, "专长", entries["专长"]["value"]), frappe.PermissionError)
        passed(current)
    except Exception as error:
        error.add_note("人事词典原生验收失败门槛: " + current)
        raise
    finally:
        try:
            frappe.set_user("Administrator")
            _end_rollback_only(point)
        finally:
            _restore_callbacks(callbacks_before)
            _restore_local_context(context_before)
            frappe.in_test = original_test
            frappe.set_user(original_user)
            for user in users:
                frappe.clear_cache(user=user)
            for doc in documents:
                if doc.name:
                    frappe.clear_document_cache(doc.doctype, doc.name)
        assert _native_contents() == before
        assert _tab_counts(tables)[1] == counts
        assert _ledger_snapshot() == ledger_before
    checks.append("all_employee_supplement_children_dictionary_versions_accounts_counts_gl_sle_rolled_back")
    return {"passed": True, "business_data": "rolled_back", "gate_count": len(checks), "checks": checks,
            "actual_roles": actual_roles, "hashes_before": before, "hashes_after": _native_contents(),
            "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger_before, "whole_ledger_after": _ledger_snapshot(), "gui_verified": False}
