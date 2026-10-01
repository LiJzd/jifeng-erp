"""One serial direct-Python tool archive experiment, always rollback.

Root invokes smoke(company,other_company,item_group,stock_uom) after migration
in an exclusive window. No bench execute fallback, GUI, HTTP, commit, grants,
controller patches or SQL fixture writes. SQL is only a full-row audit oracle.
"""

from pathlib import Path
from uuid import UUID, uuid4

import frappe
from frappe.utils.file_manager import get_content_hash

from jifeng_erp.catalog_original_validation import _rollback
from jifeng_erp.catalog_validation import _profile_audit, _profile_fixture_png, _profile_restore_caller, _version_has
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_dictionary_validation import _document_snapshot, _native_contents, _sql_row_hash
from jifeng_erp.tool_archive import (
    COLLISION, DOCTYPES, REQUEST_FIELDS, TITLE, init_record, list_image_attachments,
    list_records, read_record, resolve_creation, save_record,
)


def _contents():
    result = _native_contents()
    doctypes = set(DOCTYPES.values())
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    return result


def smoke(company, other_company, item_group, stock_uom):
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb" and company != other_company
    for doctype, name in (("Company", company), ("Company", other_company), ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "请选择既有验收公司、明细商品分组和库存单位"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    for kind, doctype in DOCTYPES.items():
        meta = frappe.get_meta(doctype)
        assert meta.track_changes and not meta.is_submittable and not meta.allow_rename
        assert meta.get_field("company").set_only_once and meta.get_field(TITLE[kind]).unique
        assert meta.get_field("request_id").unique
        assert all(all(meta.get_field(field).get(attr) for attr in ("hidden", "read_only", "no_copy", "set_only_once"))
                   for field in REQUEST_FIELDS)
    assert frappe.get_meta(DOCTYPES["mold"]).get_field("quantity").fieldtype == "Float"
    assert frappe.get_meta(DOCTYPES["plate"]).get_field("quantity").fieldtype == "Data"

    tables, counts = _tab_counts()
    changed = {"Item", "Employee", "User", "User Permission", "Version", "File", "Series", "DocShare",
               "DefaultValue", "Communication", "Email Queue", "Notification Log", *DOCTYPES.values()}
    for dt in ("Item", "Employee", "User", *DOCTYPES.values()):
        changed.update(field.options for field in frappe.get_meta(dt).get_table_fields())
    assert all(tables["tab" + dt] == "InnoDB" for dt in changed)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    before, ledger = _contents(), _ledger_snapshot()
    callbacks, context = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    logs = set(frappe.get_all("Error Log", pluck="name"))
    prefix = "JF-QA-TOOL-ARCHIVE-" + frappe.generate_hash(length=10)
    point = "jf_tool_archive_" + frappe.generate_hash(length=8)
    roots = [Path(frappe.get_site_path(*parts)) for parts in (("private", "files"), ("public", "files"))]
    records, users, objects, attachments, checks, evidence = set(), [], [], [], [], {}
    current, primary = "fixtures", None
    _begin_rollback_only(point)

    def paths():
        return {str(path.resolve()) for root in roots for path in root.glob(prefix + "*")}

    def native(action):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = previous

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        records.add((doctype, document.name))
        records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
        return document

    def actor(label, roles, allowed):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix,
                      send_welcome_email=0, roles=[{"role": role} for role in roles])
        users.append(user.name)
        for owner in allowed:
            insert("User Permission", user=user.name, allow="Company", for_value=owner, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        return user.name

    def payload(kind, business_suffix="main", **updates):
        fields = {"company": company, "legacy_number": " 007 原文 ", "legacy_copy": 1,
                  "location": " 原库位 ", "item_code": item.name, "maker": employee.name}
        fields.update({"mold_no": " " + prefix + "-M-" + business_suffix + " ", "product_code": " 产品原号 ",
                       "mold_date": "2026-10-01", "quantity": 2.5, "amount": 12.25, "remark": "第一行\n第二行"} if kind == "mold" else {
                       "plate_no": " " + prefix + "-P-" + business_suffix + " ", "plate_type": " 类型原文 ",
                       "plate_date": "2026-10-01", "product_code": " 旧产品原号 ", "quantity": " 001.0000000000000007 ",
                       "assessment_score": "-0", "notes": "第一行\n第二行", "modeler": employee.name,
                       "reference_cost": 5.25})
        return {"record_type": kind, "request_id": str(uuid4()), "values": fields | updates}

    def save(request):
        view = native(lambda: save_record(request))
        records.add((DOCTYPES[view["record_type"]], view["name"]))
        return view

    def update(view, **values):
        return save({"record_type": view["record_type"], "name": view["name"], "version": view["version"], "values": values})

    def reject(action, errors=frappe.ValidationError, atomic=True):
        state, queues, local = _contents(), _callbacks(), _local_context()
        nested = "jf_tool_archive_reject_" + frappe.generate_hash(length=8)
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                if str(error) == COLLISION:
                    assert error.__context__ is None
            else:
                raise AssertionError("接受了应拒绝的工具档案操作")
            if atomic:
                assert _contents() == state, "API 失败未自行恢复 Tool/Series/Version/账户"
                assert _callbacks() == queues and _local_context() == local
        finally:
            frappe.db.rollback(save_point=nested)
            _restore_callbacks(queues)
            _restore_local_context(local)
        assert _contents() == state and _ledger_snapshot() == ledger

    def passed():
        checks.append(current)
        assert _ledger_snapshot() == ledger and set(frappe.get_all("Error Log", pluck="name")) == logs
        assert _document_snapshot("Item", item.name) == item_before
        assert _document_snapshot("Employee", employee.name) == employee_before
        assert _document_snapshot("Employee", hidden_employee.name) == hidden_employee_before

    try:
        assert not paths()
        frappe.set_user("Administrator")
        stock = actor("stock", ["Stock Manager"], [company])
        manufacturing = actor("manufacturing", ["Manufacturing Manager"], [company])
        outside = actor("outside", ["Stock Manager"], [other_company])
        clerk = actor("clerk", ["Stock User"], [company])
        item = insert("Item", item_code=prefix + "-ITEM", item_name="业务商品独立名称",
                      item_group=item_group, stock_uom=stock_uom, is_stock_item=1)
        employee = insert("Employee", first_name=prefix + "-employee", company=company, gender="Male",
                          date_of_birth="1990-01-01", date_of_joining="2020-01-01", status="Active")
        hidden_employee = insert("Employee", first_name=prefix + "-peer", company=company, gender="Male",
                                 date_of_birth="1990-01-01", date_of_joining="2020-01-01", status="Active")
        item_before = _document_snapshot("Item", item.name)
        employee_before = _document_snapshot("Employee", employee.name)
        hidden_employee_before = _document_snapshot("Employee", hidden_employee.name)

        current = "real_stock_manager_create_with_employee_select_only_and_original_raw_fields"
        frappe.set_user(stock)
        evidence["stock_roles"] = sorted(frappe.get_roles())
        assert "Stock Manager" in evidence["stock_roles"] and "System Manager" not in evidence["stock_roles"]
        assert frappe.get_doc("Employee", employee.name).has_permission("select")
        assert not frappe.get_doc("Employee", employee.name).has_permission("read")
        for kind in DOCTYPES:
            assert native(lambda kind=kind: init_record(kind))["values"]["company"] is None
            assert native(lambda kind=kind: init_record(kind, company))["values"]["company"] == company
        mold_request, plate_request = payload("mold"), payload("plate")
        plate_request["request_id"] = mold_request["request_id"]
        mold, plate = save(mold_request), save(plate_request)
        assert mold["values"]["legacy_number"] == " 007 原文 "
        assert mold["values"]["product_code"] == "产品原号" and mold["values"]["quantity"] == 2.5
        assert plate["values"]["quantity"] == " 001.0000000000000007 "
        assert plate["values"]["assessment_score"] == "-0" and plate["values"]["plate_type"] == " 类型原文 "
        assert plate["values"]["product_code"] == " 旧产品原号 "
        assert mold["request_id"] == plate["request_id"] and mold["name"] != plate["name"]
        assert all(len(view["request_digest"]) == 64 for view in (mold, plate))
        passed()

        current = "uuid_replay_and_resolution_preserve_current_edits_without_series_or_version_writes"
        old_mold = mold
        mold = update(mold, location="后续库位")
        assert _version_has(frappe.get_doc(DOCTYPES["mold"], mold["name"]), "location")
        state = _contents()
        assert save(mold_request) == mold
        assert native(lambda: resolve_creation(mold_request["request_id"], "mold", company))["archive"] == mold
        assert native(lambda: resolve_creation(plate_request["request_id"], "plate", company))["archive"] == plate
        assert _contents() == state
        reject(lambda: save_record(mold_request | {"values": mold_request["values"] | {"amount": 99}}))
        passed()

        current = "stale_version_company_and_forged_metadata_reject_without_partial_records"
        reject(lambda: update(old_mold, location="stale"), frappe.TimestampMismatchError)
        for view in (mold, plate):
            reject(lambda view=view: update(view, company=other_company))
            for field in ("request_id", "request_digest", "doctype", "flags", "_jf_tool_archive_server"):
                reject(lambda view=view, field=field: update(view, **{field: "forged"}))
        reject(lambda: save_record(payload("mold", "zero", quantity=0)))
        reject(lambda: save_record(payload("plate", "nan", reference_cost="NaN")))
        reject(lambda: save_record(payload("mold", "missing", company=None)))
        passed()

        current = "real_manufacturing_manager_native_insert_server_receipt_and_fixed_identity"
        frappe.set_user(manufacturing)
        evidence["manufacturing_roles"] = sorted(frappe.get_roles())
        ordinary = insert(DOCTYPES["plate"], company=company, plate_no=prefix + "-NATIVE", plate_date="2026-10-01",
                          item_code=item.name, maker=employee.name, modeler=employee.name, reference_cost=0)
        assert UUID(ordinary.request_id).version == 4 and len(ordinary.request_digest) == 64
        assert ordinary.assessment_score == "1"
        empty = save(payload("plate", "empty", assessment_score=""))
        assert empty["values"]["assessment_score"] == ""
        def native_change(field, value):
            document = frappe.get_doc(ordinary.doctype, ordinary.name)
            document.set(field, value)
            document.save()
        for field, value in (("company", other_company), ("request_id", str(uuid4())), ("request_digest", "forged")):
            reject(lambda field=field, value=value: native_change(field, value),
                   (frappe.ValidationError, frappe.PermissionError), atomic=False)
        for force in (False, True):
            reject(lambda force=force: frappe.rename_doc(ordinary.doctype, ordinary.name, ordinary.name + "-R", force=force),
                   (frappe.ValidationError, frappe.PermissionError), atomic=False)
        reject(lambda: insert(DOCTYPES["plate"], company=company, plate_no=prefix + "-FORGED", plate_date="2026-10-01",
                              request_id=str(uuid4())), atomic=False)
        passed()

        current = "copy_link_only_records_same_company_readable_source_and_blocks_transitive_cycle"
        linked = save(payload("plate", "copy", copy_from=plate["name"], legacy_number="另编号原文"))
        assert linked["values"]["legacy_number"] == "另编号原文" and linked["values"]["copy_from"] == plate["name"]
        reject(lambda: update(plate, copy_from=linked["name"]))
        reject(lambda: update(linked, copy_from=linked["name"]))
        passed()

        current = "scrap_and_retire_dates_reactivation_and_original_score_quantity_preservation"
        mold = update(mold, scrapped=1, scrap_date="2026-10-02", scrap_reason="损坏")
        plate = update(plate, retired=1, retired_date="2026-10-02", retired_reason="损坏")
        for view in (mold, plate):
            assert not view["can_select"]
        assert all(row["name"] != mold["name"] for row in native(lambda: list_records("mold", company)))
        mold, plate = update(mold, scrapped=0), update(plate, retired=0)
        assert mold["values"]["scrap_date"] is None and mold["values"]["scrap_reason"] is None
        assert plate["values"]["retired_date"] is None and plate["values"]["retired_reason"] is None
        assert plate["values"]["assessment_score"] == "-0"
        assert plate["values"]["quantity"] == " 001.0000000000000007 "
        passed()

        current = "real_private_file_native_rollback_callbacks_exact_image_binding_and_safe_url"
        frappe.set_user("Administrator")
        for kind, view in (("mold", mold), ("plate", plate)):
            content = _profile_fixture_png(prefix + "-" + kind)
            assert not frappe.db.exists("File", {"content_hash": get_content_hash(content)})
            attachment = insert("File", file_name=prefix + "-" + kind + ".png", content=content,
                is_private=1, attached_to_doctype=DOCTYPES[kind], attached_to_name=view["name"], attached_to_field="image")
            attachments.append(attachment)
            assert attachment.on_rollback in frappe.db.after_rollback._functions
            assert Path(attachment.get_full_path()).is_file()
        frappe.set_user(stock)
        mold = update(mold, image=attachments[0].file_url)
        plate = update(plate, image=attachments[1].file_url)
        for view, attachment in zip((mold, plate), attachments):
            assert view["values"]["image"] == attachment.file_url
            assert view["image_states"]["image"] == {"state": "已上传", "url": attachment.unique_url}
            reject(lambda view=view, attachment=attachment: update(view, image=attachment.unique_url), frappe.PermissionError)
        reject(lambda: save_record(payload("mold", "new-image", image=attachments[0].file_url)))
        passed()

        current = "upload_unknown_outcome_candidates_are_exact_readonly_and_do_not_adopt"
        state = _contents()
        for view, attachment in zip((mold, plate), attachments):
            candidates = native(lambda view=view: list_image_attachments(view["name"], view["record_type"]))
            assert len(candidates) == 1 and candidates[0]["name"] == attachment.name
            assert candidates[0]["file_url"] == attachment.file_url and candidates[0]["unique_url"] == attachment.unique_url
        assert _contents() == state
        passed()

        current = "file_read_denial_same_url_other_parent_alias_never_authorizes_exact_mold_image"
        frappe.set_user("Administrator")
        sibling = insert(DOCTYPES["mold"], company=company, mold_no=prefix + "-ALIAS", product_code="独立原号",
                         quantity=1, amount=0, mold_date="2026-10-01")
        exact = attachments[0]
        alias = exact.create_attachment_copy(attached_to_doctype=DOCTYPES["mold"], attached_to_name=sibling.name,
                                             attached_to_field="image")
        attachments.append(alias)
        objects.append(alias)
        records.add(("File", alias.name))
        assert alias.name != exact.name and alias.file_url == exact.file_url
        for allowed in (alias.name, alias.folder):
            insert("User Permission", user=stock, allow="File", for_value=allowed,
                   apply_to_all_doctypes=0, applicable_for="File")
        frappe.clear_cache(user=stock)
        frappe.set_user(stock)
        assert not frappe.get_doc("File", exact.name).has_permission("read")
        assert frappe.get_doc("File", alias.name).has_permission("read")
        assert frappe.get_doc("File", alias.name).is_downloadable()
        hidden = native(lambda: read_record(mold["name"], "mold"))
        assert "image" not in hidden["values"] and hidden["image_states"]["image"] == {"state": "附件不可用", "url": ""}
        assert exact.file_url not in str(hidden) and alias.name not in str(hidden)
        assert native(lambda: list_image_attachments(mold["name"], "mold")) == []
        reject(lambda: update(hidden, image=exact.file_url), frappe.PermissionError)
        passed()

        current = "native_employee_user_permission_hides_same_company_peer_without_hr_read_grant"
        frappe.set_user("Administrator")
        insert("User Permission", user=stock, allow="Employee", for_value=employee.name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=stock)
        frappe.set_user(stock)
        assert frappe.get_doc("Employee", employee.name).has_permission("select")
        assert not frappe.get_doc("Employee", hidden_employee.name).has_permission("select")
        reject(lambda: update(mold, maker=hidden_employee.name), frappe.PermissionError)
        reject(lambda: update(plate, modeler=hidden_employee.name), frappe.PermissionError)
        passed()

        current = "native_company_role_and_receipt_scope_no_hidden_archive_or_extra_grants"
        frappe.set_user(outside)
        for kind, view in (("mold", mold), ("plate", plate)):
            reject(lambda kind=kind: list_records(kind, company), frappe.PermissionError, atomic=False)
            reject(lambda kind=kind, view=view: read_record(view["name"], kind), frappe.PermissionError, atomic=False)
            reject(lambda kind=kind, view=view: resolve_creation(view["request_id"], kind, company),
                   frappe.PermissionError, atomic=False)
        for user in (clerk, "Guest"):
            frappe.set_user(user)
            reject(lambda: init_record("mold", company), frappe.PermissionError, atomic=False)
            reject(lambda: save_record(payload("mold", "no-role")), frappe.PermissionError)
        passed()

        current = "read_init_list_receipt_and_image_candidate_operations_do_not_change_sql_versions_series_ledger"
        frappe.set_user(manufacturing)
        state = _contents()
        readonly_counts = _tab_counts(tables)[1]
        for kind, view in (("mold", mold), ("plate", plate)):
            native(lambda kind=kind: init_record(kind, company))
            native(lambda kind=kind, view=view: read_record(view["name"], kind))
            native(lambda kind=kind: list_records(kind, company, search=prefix, include_inactive=True))
            native(lambda kind=kind, view=view: resolve_creation(view["request_id"], kind, company))
            native(lambda kind=kind, view=view: list_image_attachments(view["name"], kind))
        assert _contents() == state and _tab_counts(tables)[1] == readonly_counts
        passed()
        evidence["images"] = "one known image field per archive; exact private saved-parent binding and File.read/download; no HTTP download tested"
        evidence["field_permission_revocation"] = "model-tested; this helper does not change metadata or DocPerm"
        evidence["historical_bad_images"] = "model preservation and masking only; no legacy business fixture fabricated"
        evidence["concurrency"] = "serial only; UUID/copy graph competing transactions not yet exercised"
    except Exception as error:
        primary = error
        error.add_note("模具/版种原式原生失败门：" + current)
        error.add_note("已通过门：" + repr(checks))
        raise
    finally:
        def collect():
            for document in objects:
                if document.name and frappe.db.exists(document.doctype, document.name):
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)

        _profile_audit([
            ("collect_exact_fixture_records", collect),
            ("savepoint_rollback_then_only_new_native_file_cleanup", lambda: _rollback(point, callbacks)),
            ("restore_caches_caller_queues_context", lambda: _profile_restore_caller(
                original_user, original_test, users, records, callbacks, context)),
            ("all_exact_fixture_records_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("full_sql_tool_children_item_profile_employee_user_auth_roles_up_series_file_version_restored", lambda: _contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger),
            ("exact_native_disk_cleanup", lambda: not paths() and all(not Path(file.get_full_path()).exists() for file in attachments)),
            ("caller_queues_context_restored", lambda: _callbacks() == callbacks and _local_context() == context),
            ("zero_error_logs_added", lambda: set(frappe.get_all("Error Log", pluck="name")) == logs),
        ], primary=primary)
    checks.append("rollback_full_sql_counts_disk_callbacks_context_accounts_series_versions_gl_sle")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back", "evidence": evidence,
            "hashes_before": before, "hashes_after": _contents(), "table_counts_before": counts,
            "table_counts_after": _tab_counts(tables)[1], "whole_ledger_before": ledger, "whole_ledger_after": _ledger_snapshot(),
            "gui_verified": False, "http_download_verified": False}
