"""Serial native modeling acceptance; direct Python once, always rollback.

Root calls smoke(company, other_company, item_group, stock_uom) after migration
in an exclusive business-write window. Never use bench execute fallback, retry
an unknown outcome, commit, GUI/HTTP, downloads, DocPerm changes, controller
patches, ignore_permissions or SQL fixture writes. SQL is an audit oracle only.

Two unique one-pixel PNGs are generated locally. Native File.on_rollback runs
only after confirmed SQL rollback. An actual tiny cash/bank expense is submitted
and cancelled to test optional source status; modeling actions must preserve the
ledger baseline at each phase. A source's submitted state never means paid.
"""

from pathlib import Path
from uuid import UUID, uuid4

import frappe
from frappe.utils import today
from frappe.utils.file_manager import get_content_hash

from jifeng_erp.catalog_original_validation import _rollback
from jifeng_erp.catalog_validation import _profile_audit, _profile_fixture_png, _profile_restore_caller, _version_has
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.jifeng_erp.report.jifeng_plate_modeling_catalog.jifeng_plate_modeling_catalog import execute as modeling_catalog
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.plate_modeling import (
    COLLISION, DOCTYPE, EDIT_FIELDS, RAW_FIELDS, REQUEST_FIELDS, _CREATING,
    init_record, list_image_attachments, list_records, read_record, resolve_creation, save_record,
)
from jifeng_erp.product_dictionary_validation import _document_snapshot, _native_contents, _sql_row_hash


SOURCE = "Jifeng Expense Voucher"
REPORT = "Jifeng Plate Modeling Catalog"


def _contents():
    """Full persisted rows and children; only opaque hashes leave this helper."""
    result = _native_contents()
    doctypes = {DOCTYPE, "Jifeng Plate", SOURCE, "Journal Entry", "Payment Entry", "GL Entry",
                "Payment Ledger Entry", "Stock Ledger Entry", "Account", "Cost Center", "Company",
                "Jifeng Wage Entry", "Jifeng Wage Allocation", "Salary Slip", "Payroll Entry",
                "DefaultValue", "Email Queue", "Notification Log", "Communication"}
    doctypes = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    return result


def smoke(company, other_company, item_group, stock_uom):
    """Real roles, scoped receipts, ordinary links, private images and cleanup."""
    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb" and company != other_company
    for doctype, name in (("Company", company), ("Company", other_company), ("Item Group", item_group), ("UOM", stock_uom)):
        assert frappe.db.exists(doctype, name), "请选择既有验收公司、明细商品分组及库存单位"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    assert frappe.db.exists("Report", REPORT), "请先迁移版种建模报告"
    meta = frappe.get_meta(DOCTYPE)
    assert meta.track_changes and not meta.is_submittable and not meta.allow_rename
    assert meta.get_field("company").set_only_once and meta.get_field("request_id").unique
    assert all(meta.get_field(field) for field in EDIT_FIELDS)
    assert all(meta.get_field(field).read_only and meta.get_field(field).hidden and meta.get_field(field).no_copy
               for field in REQUEST_FIELDS)
    assert all(meta.get_field(field).fieldtype == "Data" for field in RAW_FIELDS
               if field not in ("model_date", "legacy_copy"))

    currency = frappe.db.get_value("Company", company, "default_currency")

    def choose(doctype, filters):
        rows = frappe.get_all(doctype, filters=filters, pluck="name", order_by="name", limit_page_length=1)
        assert rows, "建模费用来源验收需要既有明细费用科目、现金或银行科目及成本中心"
        return rows[0]

    common = {"company": company, "is_group": 0, "disabled": 0, "account_currency": currency}
    expense_account = choose("Account", {**common, "root_type": "Expense"})
    cash_account = choose("Account", {**common, "root_type": "Asset", "account_type": ["in", ["Cash", "Bank"]]})
    cost_center = choose("Cost Center", {"company": company, "is_group": 0})
    tables, counts = _tab_counts()
    changed = {DOCTYPE, "Jifeng Plate", SOURCE, "Journal Entry", "Payment Entry", "GL Entry", "Payment Ledger Entry",
               "Item", "User", "User Permission", "Version", "File", "Series", "DocShare", "DefaultValue",
               "Communication", "Email Queue", "Notification Log"}
    for doctype in (DOCTYPE, "Jifeng Plate", SOURCE, "Journal Entry", "Payment Entry", "Item", "User"):
        changed.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    assert all(tables["tab" + doctype] == "InnoDB" for doctype in changed)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    before, original_ledger = _contents(), _ledger_snapshot()
    phase_ledger = original_ledger
    phase_business = None
    callbacks, context = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    creation_context = _CREATING.get()
    logs = set(frappe.get_all("Error Log", pluck="name"))
    prefix = "JF-QA-PLATE-MODELING-" + frappe.generate_hash(length=10)
    point = "jf_plate_modeling_" + frappe.generate_hash(length=8)
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

    def track(document):
        if not any(document is existing for existing in objects):
            objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
        return document

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        return track(document)

    def actor(label, roles, companies):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix,
                      send_welcome_email=0, roles=[{"role": role} for role in roles])
        users.append(user.name)
        for allowed in companies:
            insert("User Permission", user=user.name, allow="Company", for_value=allowed, apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        return user.name

    def payload(**updates):
        return {"request_id": str(uuid4()), "company": company, "legacy_number": " 007 原文 ",
                "model_number": prefix + "-建模号", "product_code": " 产品编号原文 ",
                "reference_unit_price": " 000.0000000000000007 ", "modeler_text": " 建模文字 ",
                "model_date": today(), "remarks": "原备注", "legacy_copy": 1, **updates}

    def save(request):
        view = native(lambda: save_record(request))
        records.add((DOCTYPE, view["name"]))
        return view

    def update(view, **values):
        return save({"name": view["name"], "version": view["version"], **values})

    def reject(action, errors=frappe.ValidationError, *, atomic=True):
        state, queues, local = _contents(), _callbacks(), _local_context()
        nested = "jf_plate_modeling_reject_" + frappe.generate_hash(length=8)
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                message = str(error)
                if message == COLLISION:
                    assert error.__context__ is None
            else:
                raise AssertionError("接受了应拒绝的版种建模操作")
            if atomic:
                assert _contents() == state, "API 失败未自行恢复建模、Series、Version 及其他业务行"
                assert _callbacks() == queues and _local_context() == local
            assert _CREATING.get() is creation_context
        finally:
            frappe.db.rollback(save_point=nested)
            _restore_callbacks(queues)
            _restore_local_context(local)
        assert _contents() == state and _ledger_snapshot() == phase_ledger
        return message

    def passed():
        checks.append(current)
        assert _ledger_snapshot() == phase_ledger and _CREATING.get() is creation_context
        assert set(frappe.get_all("Error Log", pluck="name")) == logs
        assert _document_snapshot("Item", item.name) == item_before
        assert _document_snapshot("Item", peer_item.name) == peer_item_before
        assert _document_snapshot("Jifeng Plate", plate.name) == plate_before
        assert _document_snapshot("Jifeng Plate", foreign_plate.name) == foreign_plate_before
        assert {doctype: _contents()[doctype] for doctype in protected} == protected
        assert {doctype: _contents()[doctype] for doctype in phase_business} == phase_business

    def source_phase():
        contents = _contents()
        doctypes = {SOURCE, "Journal Entry", "Payment Entry", "GL Entry", "Payment Ledger Entry", "Stock Ledger Entry"}
        for doctype in tuple(doctypes):
            doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
        return {doctype: contents[doctype] for doctype in doctypes}

    try:
        assert not paths()
        frappe.set_user("Administrator")
        stock = actor("stock", ["Stock Manager"], [company])
        manufacturing = actor("manufacturing", ["Manufacturing Manager"], [company])
        finance = actor("finance", ["Stock Manager", "Accounts Manager"], [company])
        outside = actor("outside", ["Stock Manager"], [other_company])
        item_scoped = actor("item-scoped", ["Stock Manager"], [company])
        clerk = actor("clerk", ["Stock User"], [company])
        item = insert("Item", item_code=prefix + "-ITEM", item_name="商品独立名称", item_group=item_group,
                      stock_uom=stock_uom, is_stock_item=1)
        peer_item = insert("Item", item_code=prefix + "-PEER", item_name="另一商品独立名称", item_group=item_group,
                           stock_uom=stock_uom, is_stock_item=1)
        plate = insert("Jifeng Plate", company=company, plate_no=prefix + "-PLATE", plate_date=today(),
                       product_code="版种独立产品原号", reference_cost=0)
        foreign_plate = insert("Jifeng Plate", company=other_company, plate_no=prefix + "-FOREIGN", plate_date=today(),
                               product_code="另一公司版种原号", reference_cost=0)
        mode = "现金" if frappe.get_doc("Account", cash_account).account_type == "Cash" else "银行转账"
        source = insert(SOURCE, company=company, posting_date=today(), cost_center=cost_center,
                        payment_method=mode, expense_account=expense_account, payment_account=cash_account,
                        amount=1, summary=prefix + "-费用来源")
        item_before, peer_item_before = (_document_snapshot("Item", name) for name in (item.name, peer_item.name))
        plate_before, foreign_plate_before = (_document_snapshot("Jifeng Plate", name) for name in (plate.name, foreign_plate.name))
        protected_types = {doctype for doctype in ("Account", "Cost Center", "Company", "Jifeng Wage Entry",
            "Jifeng Wage Allocation", "Salary Slip", "Payroll Entry") if doctype in before}
        for doctype in tuple(protected_types):
            protected_types.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
        protected = {doctype: before[doctype] for doctype in protected_types}
        phase_business = source_phase()

        current = "real_stock_manager_init_and_raw_creation_without_generated_legacy_code_or_business_posting"
        frappe.set_user(stock)
        evidence["stock_roles"] = sorted(frappe.get_roles())
        assert "Stock Manager" in evidence["stock_roles"] and "System Manager" not in evidence["stock_roles"]
        state = _contents()
        blank = native(init_record)
        assert blank["company"] is None and blank["name"] is None
        assert blank["legacy_number"] is None and blank["legacy_copy"] == 0 and blank["model_date"] == today()
        assert native(lambda: init_record(company))["company"] == company and _contents() == state
        request = payload()
        main = save(request)
        assert main["name"].startswith("JF-PLATE-M-") and main["name"] != main["model_number"]
        assert all(main[field] == request[field] for field in RAW_FIELDS)
        assert not (set(REQUEST_FIELDS) & set(main)) and "record_type" not in main and "values" not in main
        assert main["image_state"] == {"state": "未上传", "url": ""}
        assert all(main[field + "_state"] == "未关联" for field in ("item_code", "plate", "expense_voucher"))
        sibling = save(payload(model_number=prefix + "-另一建模", item_code=peer_item.name, remarks="第一行\n第二行"))
        sibling_before = _document_snapshot(DOCTYPE, sibling["name"])
        passed()

        current = "changed_only_save_real_native_version_original_precision_and_unrelated_rows_preserved"
        main = update(main, remarks="修改备注", reference_unit_price="-0", legacy_copy=0)
        assert main["reference_unit_price"] == "-0" and main["legacy_copy"] == 0
        assert _version_has(frappe.get_doc(DOCTYPE, main["name"]), "remarks")
        assert _document_snapshot(DOCTYPE, sibling["name"]) == sibling_before
        for value in ("NaN", "Infinity", "-0.01", "非数值"):
            reject(lambda value=value: update(main, reference_unit_price=value))
        passed()

        current = "uuid_replay_and_readonly_resolution_return_current_record_without_new_rows_or_series"
        state = _contents()
        assert save(request) == main
        resolution = native(lambda: resolve_creation(request["request_id"], company))
        assert resolution == {"status": "found", "archive": main}
        assert native(lambda: resolve_creation(str(uuid4()), company)) == {"status": "not_found"}
        assert _contents() == state
        assert reject(lambda: save_record(request | {"remarks": "改动创建摘要"})) == COLLISION
        passed()

        current = "natural_cross_company_uuid_unique_collision_rolls_back_native_series_and_callbacks"
        frappe.set_user("Administrator")
        collision = request | {"company": other_company}
        assert reject(lambda: save_record(collision)) == COLLISION
        assert _CREATING.get() is creation_context
        evidence["atomic_collision"] = "same UUID in another readable company reaches native UNIQUE after native naming; no patched failure"
        frappe.set_user(stock)
        passed()

        current = "explicit_item_and_same_company_plate_links_do_not_match_original_codes_or_modify_sources"
        main = update(main, item_code=item.name, plate=plate.name)
        assert main["item_code"] == item.name and main["plate"] == plate.name
        assert main["item_code_state"] == main["plate_state"] == "已关联"
        assert main["product_code"] == request["product_code"]
        reject(lambda: update(main, plate=foreign_plate.name), frappe.PermissionError)
        reject(lambda: update(main, expense_voucher=source.name), frappe.PermissionError)
        passed()

        current = "actual_expense_draft_cannot_be_newly_bound_with_real_finance_permissions"
        frappe.set_user(finance)
        assert all(frappe.has_permission(SOURCE, action) for action in ("read", "select", "submit", "cancel"))
        reject(lambda: update(main, expense_voucher=source.name))
        assert frappe.get_doc(SOURCE, source.name).docstatus == 0
        passed()

        current = "actual_submitted_expense_binding_preserves_source_journal_and_reference_text_not_payment_status"
        source = frappe.get_doc(SOURCE, source.name)
        native(source.submit)
        source = frappe.get_doc(SOURCE, source.name)
        journal = track(frappe.get_doc("Journal Entry", source.journal_entry))
        assert source.docstatus == journal.docstatus == 1 and journal.jf_expense_voucher == source.name
        assert len(journal.accounts) == 2
        assert journal.accounts[0].account == expense_account and journal.accounts[0].debit_in_account_currency == 1
        assert journal.accounts[1].account == cash_account and journal.accounts[1].credit_in_account_currency == 1
        posted = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry", "voucher_no": journal.name,
                                                     "is_cancelled": 0}, fields=["account", "debit", "credit"])
        assert sum(row.debit for row in posted) == sum(row.credit for row in posted) == 1
        assert {row.account for row in posted} == {expense_account, cash_account}
        phase_ledger = _ledger_snapshot()
        assert phase_ledger["Stock Ledger Entry"] == original_ledger["Stock Ledger Entry"]
        phase_business = source_phase()
        source_before, journal_before = _document_snapshot(SOURCE, source.name), _document_snapshot("Journal Entry", journal.name)
        main = update(main, expense_voucher=source.name, reference_unit_price="123.0000000000000007")
        assert main["expense_voucher"] == source.name and main["expense_voucher_state"] == "已提交"
        assert not {"paid", "paid_amount", "outstanding_amount", "amount"}.intersection(main)
        assert _document_snapshot(SOURCE, source.name) == source_before
        assert _document_snapshot("Journal Entry", journal.name) == journal_before
        columns, rows = native(lambda: modeling_catalog({"company": company, "search": prefix}))
        report_row = next(row for row in rows if row["name"] == main["name"])
        assert report_row["expense_voucher_state"] == "已提交"
        assert report_row["reference_unit_price"] == "123.0000000000000007"
        assert next(column for column in columns if column["fieldname"] == "reference_unit_price")["fieldtype"] == "Data"
        assert "已付款" not in str(rows) and "paid_amount" not in str(columns)
        evidence["source_status"] = "one actual cash/bank expense and exact JE submitted; optional source state only, no payment inference"
        passed()

        current = "cancelled_expense_history_remains_readable_but_new_binding_rejects_without_modeling_posts"
        native(lambda: frappe.get_doc(SOURCE, source.name).cancel())
        assert frappe.get_doc(SOURCE, source.name).docstatus == 2
        assert frappe.get_doc("Journal Entry", journal.name).docstatus == 2
        phase_ledger = _ledger_snapshot()
        assert phase_ledger["Stock Ledger Entry"] == original_ledger["Stock Ledger Entry"]
        phase_business = source_phase()
        main = native(lambda: read_record(main["name"]))
        assert main["expense_voucher"] == source.name and main["expense_voucher_state"] == "已取消"
        main = update(main, remarks="费用反审后的普通修改")
        assert main["expense_voucher"] == source.name and main["expense_voucher_state"] == "已取消"
        reject(lambda: update(sibling, expense_voucher=source.name))
        reject(lambda: save_record(payload(expense_voucher=source.name)))
        columns, rows = native(lambda: modeling_catalog({"company": company, "search": prefix}))
        assert next(row for row in rows if row["name"] == main["name"])["expense_voucher_state"] == "已取消"
        passed()

        current = "stock_without_accounts_read_masks_historical_expense_link_and_never_leaks_source_name"
        frappe.set_user(stock)
        hidden_source = native(lambda: read_record(main["name"]))
        assert "expense_voucher" not in hidden_source and hidden_source["expense_voucher_state"] == "来源不可用"
        assert source.name not in str(hidden_source) and journal.name not in str(hidden_source)
        assert hidden_source["field_meta"]["expense_voucher"]["read_only"]
        passed()

        current = "two_local_private_pngs_native_cleanup_callbacks_exact_saved_field_binding_and_unique_urls"
        frappe.set_user("Administrator")
        for label, view in (("main", main), ("sibling", sibling)):
            content = _profile_fixture_png(prefix + "-" + label)
            assert not frappe.db.exists("File", {"content_hash": get_content_hash(content)}), "cannot reuse any business blob"
            attachment = insert("File", file_name=prefix + "-" + label + ".png", content=content, is_private=1,
                attached_to_doctype=DOCTYPE, attached_to_name=view["name"], attached_to_field="design_image")
            attachments.append(attachment)
            assert attachment.on_rollback in frappe.db.after_rollback._functions
            assert Path(attachment.get_full_path()).is_file()
        frappe.set_user(finance)
        main, sibling = (update(view, design_image=attachment.file_url) for view, attachment in zip((main, sibling), attachments))
        for view, attachment in zip((main, sibling), attachments):
            assert view["design_image"] == attachment.file_url
            assert view["image_state"] == {"state": "已上传", "url": attachment.unique_url}
            reject(lambda view=view, attachment=attachment: update(view, design_image=attachment.unique_url), frappe.PermissionError)
        reject(lambda: update(main, design_image=attachments[1].file_url), frappe.PermissionError)
        reject(lambda: save_record(payload(design_image=attachments[0].file_url)))
        passed()

        current = "unknown_upload_outcome_candidates_are_readonly_exact_parent_field_and_never_adopted"
        state = _contents()
        for view, attachment in zip((main, sibling), attachments):
            candidates = native(lambda view=view: list_image_attachments(view["name"]))
            assert len(candidates) == 1 and candidates[0]["name"] == attachment.name
            assert candidates[0]["file_url"] == attachment.file_url and candidates[0]["unique_url"] == attachment.unique_url
            assert candidates[0]["file_name"] == attachment.file_name
        assert _contents() == state
        evidence["upload_recovery"] = "two separate saved records, exact design_image File IDs; readonly candidate listing, explicit adoption only"
        passed()

        current = "file_read_denial_other_parent_same_url_alias_never_authorizes_exact_design_image"
        frappe.set_user("Administrator")
        exact = attachments[0]
        alias = track(exact.create_attachment_copy(attached_to_doctype=DOCTYPE, attached_to_name=sibling["name"],
                                                   attached_to_field="design_image"))
        attachments.append(alias)
        assert alias.name != exact.name and alias.file_url == exact.file_url
        for allowed in (alias.name, alias.folder):
            insert("User Permission", user=stock, allow="File", for_value=allowed,
                   apply_to_all_doctypes=0, applicable_for="File")
        frappe.clear_cache(user=stock)
        frappe.set_user(stock)
        assert not frappe.get_doc("File", exact.name).has_permission("read")
        assert frappe.get_doc("File", alias.name).has_permission("read") and frappe.get_doc("File", alias.name).is_downloadable()
        hidden_image = native(lambda: read_record(main["name"]))
        assert "design_image" not in hidden_image and hidden_image["image_state"] == {"state": "附件不可用", "url": ""}
        assert exact.file_url not in str(hidden_image) and alias.name not in str(hidden_image)
        assert native(lambda: list_image_attachments(main["name"])) == []
        reject(lambda: update(hidden_image, design_image=exact.file_url), frappe.PermissionError)
        passed()

        current = "native_manufacturing_insert_server_receipt_fixed_identity_stale_version_and_rename_guards"
        frappe.set_user(manufacturing)
        ordinary = insert(DOCTYPE, company=company, model_number=prefix + "-NATIVE", legacy_copy=0,
                          model_date=today(), reference_unit_price="0")
        assert UUID(ordinary.request_id).version == 4 and len(ordinary.request_digest) == 64
        assert ordinary.docstatus == 0
        def native_change(field, value):
            document = frappe.get_doc(DOCTYPE, ordinary.name)
            document.set(field, value)
            document.save()
        for field, value in (("company", other_company), ("request_id", str(uuid4())), ("request_digest", "forged")):
            reject(lambda field=field, value=value: native_change(field, value),
                   (frappe.ValidationError, frappe.PermissionError), atomic=False)
        for force in (False, True):
            reject(lambda force=force: frappe.rename_doc(DOCTYPE, ordinary.name, ordinary.name + "-R", force=force),
                   (frappe.ValidationError, frappe.PermissionError), atomic=False)
        reject(lambda: insert(DOCTYPE, company=company, request_id=str(uuid4()), legacy_copy=0), atomic=False)
        def forged_native_creation():
            document = frappe.get_doc({"doctype": DOCTYPE, "company": company, "legacy_copy": 0,
                                       "request_id": str(uuid4()), "request_digest": "forged"})
            document.__dict__["_jf_modeling_server"] = True
            document.flags.from_jifeng_plate_modeling = True
            objects.append(document)
            document.insert()
        reject(forged_native_creation, atomic=False)
        frappe.set_user(finance)
        stale = main
        main = update(main, modeler_text="后续建模原文")
        reject(lambda: update(stale, modeler_text="旧版本覆盖"), frappe.TimestampMismatchError)
        reject(lambda: update(main, company=other_company))
        for field in ("request_id", "request_digest", "doctype", "flags", "_jf_modeling_server"):
            reject(lambda field=field: update(main, **{field: "forged"}))
        passed()

        current = "native_company_item_permissions_and_roles_hide_list_and_scoped_creation_receipts"
        frappe.set_user("Administrator")
        insert("User Permission", user=item_scoped, allow="Item", for_value=item.name, apply_to_all_doctypes=1)
        frappe.clear_cache(user=item_scoped)
        frappe.set_user(item_scoped)
        assert native(lambda: read_record(main["name"]))["name"] == main["name"]
        visible = {row["name"] for row in native(lambda: list_records(company, prefix))}
        assert main["name"] in visible and sibling["name"] not in visible
        reject(lambda: read_record(sibling["name"]), frappe.PermissionError, atomic=False)
        sibling_request_id = frappe.db.get_value(DOCTYPE, sibling["name"], "request_id")
        assert native(lambda: resolve_creation(sibling_request_id, company)) == {"status": "not_found"}
        frappe.set_user(outside)
        reject(lambda: read_record(main["name"]), frappe.PermissionError, atomic=False)
        reject(lambda: list_records(company, prefix), frappe.PermissionError, atomic=False)
        reject(lambda: resolve_creation(request["request_id"], company), frappe.PermissionError, atomic=False)
        for user in (clerk, "Guest"):
            frappe.set_user(user)
            reject(lambda: init_record(company), frappe.PermissionError, atomic=False)
            reject(lambda: save_record(payload()), frappe.PermissionError)
        passed()

        current = "read_init_list_receipt_candidates_and_report_leave_full_contents_versions_series_and_ledgers_unchanged"
        frappe.set_user(finance)
        state, readonly_counts = _contents(), _tab_counts(tables)[1]
        native(lambda: init_record(company))
        native(lambda: read_record(main["name"]))
        native(lambda: list_records(company, prefix))
        native(lambda: resolve_creation(request["request_id"], company))
        native(lambda: list_image_attachments(main["name"]))
        columns, rows = native(lambda: modeling_catalog({"company": company, "search": prefix}))
        row = next(row for row in rows if row["name"] == main["name"])
        assert row["image_url"] == exact.unique_url and row["image_status"] == "已上传"
        assert "design_image" not in row and not (set(REQUEST_FIELDS) & set(row))
        assert _contents() == state and _tab_counts(tables)[1] == readonly_counts
        evidence["field_permission_revocation"] = "behavioral models only; no metadata or DocPerm changed by helper"
        evidence["historical_bad_images"] = "behavioral masking/preservation only; no SQL legacy fixture fabricated"
        evidence["concurrency"] = "serial receipt/UNIQUE experiment only; no two-connection race exercised"
        passed()
    except Exception as error:
        primary = error
        error.add_note("版种建模原生失败门：" + current)
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
            ("confirmed_sql_rollback_then_only_added_native_file_callbacks", lambda: _rollback(point, callbacks)),
            ("restore_caches_caller_queues_and_context", lambda: _profile_restore_caller(
                original_user, original_test, users, records, callbacks, context)),
            ("all_exact_fixture_records_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("full_sql_modeling_plate_item_expense_je_pe_ple_file_wages_version_series_auth_restored", lambda: _contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == original_ledger),
            ("exact_native_disk_cleanup", lambda: not paths() and all(not Path(file.get_full_path()).exists() for file in attachments)),
            ("caller_queues_local_and_creation_context_restored", lambda: _callbacks() == callbacks
                and _local_context() == context and _CREATING.get() is creation_context),
            ("zero_error_logs_added", lambda: set(frappe.get_all("Error Log", pluck="name")) == logs),
        ], primary=primary)
    checks.append("rollback_full_sql_counts_two_pngs_accounts_wages_series_auth_versions_gl_sle_callbacks_and_context")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back", "evidence": evidence,
            "hashes_before": before, "hashes_after": _contents(), "table_counts_before": counts,
            "table_counts_after": _tab_counts(tables)[1], "whole_ledger_before": original_ledger,
            "whole_ledger_after": _ledger_snapshot(), "gui_verified": False, "http_image_download_verified": False,
            "concurrency_verified": False, "remaining": ["真实字段权限撤销", "实际图片下载与原式 GUI", "两连接 UUID 竞争", "旧复制勾选业务语义"]}
