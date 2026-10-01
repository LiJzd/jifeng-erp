"""One serial native credit expense experiment; direct Python once, rollback.

After root migrates the new Supplier/Payable fields, call smoke(company,
other_company) in an exclusive business-write window. Never use bench execute,
retry an unknown outcome, commit, alter schema/DocPerm or bypass permissions.
Only small audit utilities are reused; no other smoke suite is called.
Supplier and actors are synthetic. Existing Company/Account/Cost Center rows
are read only. Native JE and PE perform actual GL/PLE posting and cancellation.
This is the new system's explicit credit path, not a claim about the old UI.
Cancellation's child/PLE conflict queries use complete FOR UPDATE current reads;
a preexisting MVCC snapshot must not hide a recently committed payment. This
serial helper proves the installed hook executes, not a two-connection race.
"""

from decimal import Decimal

import frappe
from frappe.utils import today

from jifeng_erp.accounting import (
    CREDIT, SOURCE, credit_reference_details, payment_context,
)
from jifeng_erp.catalog_original_validation import _rollback
from jifeng_erp.catalog_validation import _profile_audit, _profile_restore_caller, _version_has
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.jifeng_erp.report.jifeng_expense_ledger.jifeng_expense_ledger import execute as expense_ledger
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_dictionary_validation import _document_snapshot, _native_contents, _sql_row_hash


def _contents():
    """Full physical parent/child rows and auth; never a Document.as_dict oracle."""
    result = _native_contents()
    doctypes = {SOURCE, "Journal Entry", "Payment Entry", "GL Entry", "Payment Ledger Entry",
                "Stock Ledger Entry", "Supplier", "Account", "Cost Center", "Company", "DefaultValue",
                "Email Queue", "Notification Log", "Communication", "Payment Reconciliation Log",
                "Unreconcile Payment"}
    doctypes = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    return result


def smoke(company, other_company):
    """Real Accounts Manager, native partial/full payments and reverse cancellation."""
    frappe.only_for("System Manager")
    assert company != other_company and frappe.db.db_type == "mariadb"
    assert all(frappe.db.exists("Company", name) for name in (company, other_company))
    meta = frappe.get_meta(SOURCE)
    assert meta.is_submittable and meta.track_changes
    assert CREDIT in meta.get_field("payment_method").options.splitlines()
    assert all(meta.get_field(field) for field in ("supplier", "payable_account"))
    assert not meta.get_field("payment_account").reqd
    assert frappe.get_meta("Journal Entry").get_field("jf_expense_voucher").unique
    events = frappe.get_hooks("doc_events")
    for doctype in ("Payment Entry", "Journal Entry"):
        assert "jifeng_erp.accounting.lock_expense_settlement_references" in events.get(doctype, {}).get("before_validate", [])
    assert "jifeng_erp.accounting.validate_expense_journal_entry" in events.get("Journal Entry", {}).get("before_update_after_submit", [])
    currency = frappe.get_doc("Company", company).default_currency
    assert currency

    def choose(doctype, filters):
        names = frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=1, order_by="name")
        assert names, "验收需要既有本币明细费用/应付/现金银行科目及明细成本中心"
        return names[0]

    common = {"company": company, "is_group": 0, "disabled": 0, "account_currency": currency}
    expense = choose("Account", {**common, "root_type": "Expense"})
    payable = choose("Account", {**common, "root_type": "Liability", "account_type": "Payable"})
    cash = choose("Account", {**common, "root_type": "Asset", "account_type": ["in", ["Cash", "Bank"]]})
    center = choose("Cost Center", {"company": company, "is_group": 0})
    supplier_group = choose("Supplier Group", {})
    tables, counts = _tab_counts()
    changed = {SOURCE, "Journal Entry", "Payment Entry", "GL Entry", "Payment Ledger Entry", "Supplier",
               "Version", "User", "User Permission", "Series", "DocShare", "DefaultValue",
               "Email Queue", "Notification Log", "Communication"}
    for doctype in (SOURCE, "Journal Entry", "Payment Entry", "Supplier", "User"):
        changed.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    assert all(tables["tab" + doctype] == "InnoDB" for doctype in changed)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    before, ledger = _contents(), _ledger_snapshot()
    callbacks, context = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    logs = set(frappe.get_all("Error Log", pluck="name"))
    prefix = "JF-QA-CREDIT-" + frappe.generate_hash(length=10)
    point = "jf_credit_expense_" + frappe.generate_hash(length=8)
    records, objects, users, checks = set(), [], [], []
    current, primary, evidence = "fixtures", None, {}
    _begin_rollback_only(point)

    def native(action):
        previous = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = previous

    def track(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        records.update((child.doctype, child.name) for child in document.get_all_children() if child.name)
        return document

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        return track(document)

    def actor(label, role, allowed_company):
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix,
                      send_welcome_email=0, roles=[{"role": role}])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=allowed_company,
               apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        return user.name

    def reject(action, errors=frappe.ValidationError, *, atomic=False):
        old, old_callbacks, old_context = _contents(), _callbacks(), _local_context()
        nested = "jf_credit_reject_" + frappe.generate_hash(length=8)
        frappe.db.savepoint(nested)
        try:
            try:
                native(action)
            except errors as error:
                message = str(error)
            else:
                raise AssertionError("原生接受了应拒绝的费用或付款动作")
            if atomic:
                assert _contents() == old, "取消前拒绝不应改动任何持久行"
        finally:
            frappe.db.rollback(save_point=nested)
            _restore_callbacks(old_callbacks)
            _restore_local_context(old_context)
        assert _contents() == old
        return message

    def passed():
        checks.append(current)
        assert set(frappe.get_all("Error Log", pluck="name")) == logs

    def outstanding(source):
        _, _, details = credit_reference_details(frappe.get_doc(SOURCE, source))
        return Decimal(str(details["outstanding_amount"]))

    def payment(source, amount):
        source_context = payment_context(source)
        document = insert("Payment Entry", company=company, posting_date=today(), payment_type="Pay",
            party_type="Supplier", party=supplier.name, paid_from=cash, paid_to=payable,
            paid_amount=amount, received_amount=amount, source_exchange_rate=1, target_exchange_rate=1,
            reference_no=prefix + "-" + str(amount), reference_date=today(),
            references=[{"reference_doctype": "Journal Entry", "reference_name": source_context["reference_name"],
                         "total_amount": source_context["total_amount"],
                         "outstanding_amount": source_context["outstanding_amount"], "allocated_amount": amount}])
        native(document.submit)
        track(document)
        assert document.docstatus == 1
        return document

    try:
        frappe.set_user("Administrator")
        manager = actor("manager", "Accounts Manager", company)
        clerk = actor("clerk", "Accounts User", company)
        outside = actor("outside", "Accounts Manager", other_company)
        supplier = insert("Supplier", supplier_name=prefix + "-supplier", supplier_type="Company",
            supplier_group=supplier_group, default_currency=currency,
            accounts=[{"company": company, "account": payable}])
        other_supplier = insert("Supplier", supplier_name=prefix + "-other-supplier", supplier_type="Company",
            supplier_group=supplier_group, default_currency=currency, accounts=[{"company": company, "account": payable}])
        fixed = {doctype: before[doctype] for doctype in ("Account", "Cost Center", "Company")}
        supplier_before = _document_snapshot("Supplier", supplier.name)
        frappe.set_user(manager)

        current = "native_manager_permissions_and_migrated_conditional_fields_without_grants"
        assert all(frappe.has_permission(SOURCE, kind) for kind in ("read", "create", "write", "submit", "cancel"))
        assert all(frappe.has_permission(dt, kind) for dt in ("Journal Entry", "Payment Entry")
                   for kind in ("read", "create", "submit", "cancel"))
        passed()

        current = "native_credit_draft_preserves_old_reference_and_real_version"
        source = insert(SOURCE, company=company, posting_date=today(), cost_center=center, payment_method=CREDIT,
            expense_account=expense, payment_account=None, payable_account=payable, supplier=supplier.name,
            amount=120, arrears_reference=999, counterparty_name="独立旧名称", summary=prefix + "-原摘要")
        source.summary = prefix + "-修订摘要"
        native(source.save)
        assert _version_has(source, "summary")
        assert not source.journal_entry
        passed()

        current = "native_accounts_user_cannot_submit_and_outside_company_cannot_read"
        frappe.set_user(clerk)
        assert not frappe.has_permission(SOURCE, "submit")
        reject(lambda: frappe.get_doc(SOURCE, source.name).submit(), frappe.PermissionError)
        frappe.set_user(outside)
        reject(lambda: payment_context(source.name), frappe.PermissionError, atomic=True)
        assert not frappe.get_list(SOURCE, filters={"name": source.name}, pluck="name")
        frappe.set_user(manager)
        passed()

        current = "native_submit_creates_exact_expense_supplier_payable_je_and_ple"
        source = frappe.get_doc(SOURCE, source.name)
        native(source.submit)
        source = frappe.get_doc(SOURCE, source.name)
        journal = track(frappe.get_doc("Journal Entry", source.journal_entry))
        assert source.docstatus == journal.docstatus == 1
        assert journal.jf_expense_voucher == source.name and len(journal.accounts) == 2
        assert journal.accounts[0].account == expense and journal.accounts[0].debit_in_account_currency == 120
        assert journal.accounts[1].account == payable and journal.accounts[1].credit_in_account_currency == 120
        assert journal.accounts[1].party_type == "Supplier" and journal.accounts[1].party == supplier.name
        assert not journal.accounts[1].reference_type and not journal.accounts[1].reference_name
        assert source.arrears_reference == 999 and outstanding(source.name) == 120
        entries = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry", "voucher_no": journal.name,
                                                       "is_cancelled": 0}, fields=["account", "debit", "credit", "party"])
        assert sum(Decimal(str(row.debit)) for row in entries) == 120
        assert sum(Decimal(str(row.credit)) for row in entries) == 120
        assert len([row for row in entries if row.account == payable and row.party == supplier.name]) == 1
        passed()

        current = "native_managed_je_direct_edit_cancel_and_forged_flag_are_rejected"
        def direct_edit():
            document = frappe.get_doc("Journal Entry", journal.name)
            document.flags.from_jifeng_expense = True
            document.title = prefix + "-禁止直接编辑"
            document.save()

        def direct_cancel():
            document = frappe.get_doc("Journal Entry", journal.name)
            document.flags.from_jifeng_expense = True
            document.cancel()

        reject(direct_edit, atomic=True)
        reject(direct_cancel, atomic=True)
        def direct_create():
            document = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Journal Entry",
                "company": company, "posting_date": today(), "jf_expense_voucher": source.name,
                "accounts": [{"account": expense, "cost_center": center, "debit_in_account_currency": 120},
                             {"account": payable, "party_type": "Supplier", "party": supplier.name,
                              "credit_in_account_currency": 120}]})
            document.flags.from_jifeng_expense = True
            objects.append(document)
            document.insert()

        assert "只能由费用登记" in reject(direct_create)
        passed()

        current = "native_pe_je_before_validate_hook_rejects_mismatched_supplier_after_source_lock"
        def mismatched(doctype):
            if doctype == "Payment Entry":
                insert(doctype, company=company, posting_date=today(), payment_type="Pay", party_type="Supplier",
                    party=other_supplier.name, paid_from=cash, paid_to=payable, paid_amount=10, received_amount=10,
                    references=[{"reference_doctype": "Journal Entry", "reference_name": journal.name, "allocated_amount": 10}])
            else:
                insert(doctype, company=company, posting_date=today(), voucher_type="Journal Entry", accounts=[
                    {"account": payable, "party_type": "Supplier", "party": other_supplier.name,
                     "debit_in_account_currency": 10, "reference_type": "Journal Entry", "reference_name": journal.name},
                    {"account": cash, "credit_in_account_currency": 10}])

        for doctype in ("Payment Entry", "Journal Entry"):
            assert "供应商和应付科目" in reject(lambda: mismatched(doctype))
        passed()

        current = "native_readonly_context_and_expense_ledger_do_not_write_versions_or_ledgers"
        state = _contents()
        values = payment_context(source.name)
        assert values["outstanding_amount"] == 120 and "paid_from" not in values and "arrears_reference" not in values
        _, rows = expense_ledger({"company": company, "from_date": today(), "to_date": today(), "supplier": supplier.name})
        row = next(row for row in rows if row["name"] == source.name)
        assert row["supplier"] == supplier.name and row["payable_account"] == payable
        assert row["payment_account"] is None and row["arrears_reference"] == 999
        assert row["journal_entry"] == journal.name and "outstanding_amount" not in row
        assert _contents() == state
        passed()

        current = "native_partial_payment_allocates_40_and_leaves_80"
        partial = payment(source.name, 40)
        assert outstanding(source.name) == 80
        assert partial.references[0].reference_name == journal.name and partial.references[0].allocated_amount == 40
        passed()

        current = "native_partial_paid_expense_cancel_rejects_before_any_persisted_change"
        reject(lambda: frappe.get_doc(SOURCE, source.name).cancel(), atomic=True)
        assert outstanding(source.name) == 80
        passed()

        current = "native_remaining_payment_allocates_80_and_full_balance_zero"
        remaining = payment(source.name, 80)
        assert outstanding(source.name) == 0
        assert remaining.references[0].reference_name == journal.name and remaining.references[0].allocated_amount == 80
        passed()

        current = "native_fully_paid_expense_cancel_and_duplicate_payment_context_reject"
        reject(lambda: frappe.get_doc(SOURCE, source.name).cancel(), atomic=True)
        reject(lambda: payment_context(source.name), atomic=True)
        assert outstanding(source.name) == 0
        passed()

        current = "native_reverse_cancel_remaining_payment_restores_80_and_still_blocks_expense"
        native(lambda: frappe.get_doc("Payment Entry", remaining.name).cancel())
        assert outstanding(source.name) == 80
        reject(lambda: frappe.get_doc(SOURCE, source.name).cancel(), atomic=True)
        passed()

        current = "native_reverse_cancel_first_payment_restores_original_120"
        native(lambda: frappe.get_doc("Payment Entry", partial.name).cancel())
        assert outstanding(source.name) == 120
        assert all(frappe.get_doc("Payment Entry", name).docstatus == 2 for name in (partial.name, remaining.name))
        passed()

        current = "native_expense_cancel_cancels_exact_je_and_preserves_all_source_links"
        native(lambda: frappe.get_doc(SOURCE, source.name).cancel())
        cancelled = frappe.get_doc(SOURCE, source.name)
        assert cancelled.docstatus == 2 and cancelled.journal_entry == journal.name
        assert frappe.get_doc("Journal Entry", journal.name).docstatus == 2
        assert frappe.get_doc("Journal Entry", journal.name).jf_expense_voucher == source.name
        active = frappe.get_all("Payment Ledger Entry", filters={"against_voucher_type": "Journal Entry",
            "against_voucher_no": journal.name, "delinked": 0}, fields=["amount_in_account_currency"])
        assert sum(Decimal(str(row.amount_in_account_currency)) for row in active) == 0
        gl = frappe.get_all("GL Entry", filters={"voucher_type": "Journal Entry", "voucher_no": journal.name},
                            fields=["account", "debit", "credit"])
        for account in (expense, payable):
            assert sum(Decimal(str(row.debit)) - Decimal(str(row.credit)) for row in gl if row.account == account) == 0
        passed()

        current = "native_existing_cash_bank_path_remains_two_partyless_rows_and_cancels"
        mode = "现金" if frappe.get_doc("Account", cash).account_type == "Cash" else "银行转账"
        immediate = insert(SOURCE, company=company, posting_date=today(), cost_center=center, payment_method=mode,
            expense_account=expense, payment_account=cash, amount=10, summary=prefix + "-原现金银行路径")
        native(immediate.submit)
        cash_journal = track(frappe.get_doc("Journal Entry", frappe.get_doc(SOURCE, immediate.name).journal_entry))
        assert cash_journal.accounts[1].account == cash and not cash_journal.accounts[1].party
        native(lambda: frappe.get_doc(SOURCE, immediate.name).cancel())
        assert frappe.get_doc("Journal Entry", cash_journal.name).docstatus == 2
        passed()

        current = "native_supplier_and_all_existing_accounts_company_costcenters_are_unchanged"
        assert _document_snapshot("Supplier", supplier.name) == supplier_before
        assert {doctype: _contents()[doctype] for doctype in fixed} == fixed
        evidence = {"source": source.name, "journal_entry": journal.name, "partial_payment": partial.name,
                    "remaining_payment": remaining.name, "partial_outstanding": 80, "fully_paid_outstanding": 0,
                    "restored_before_source_cancel": 120, "real_native_version": True,
                    "roles": ["Accounts Manager", "Accounts User"], "old_arrears_not_posted": True}
        passed()
    except Exception as error:
        primary = error
        error.add_note("赊账费用原生失败门：" + current)
        error.add_note("已通过门：" + repr(checks))
        raise
    finally:
        def collect():
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)

        _profile_audit([
            ("collect_fixture_parent_children", collect),
            ("native_savepoint_and_only_added_rollback_callbacks", lambda: _rollback(point, callbacks)),
            ("restore_user_test_cache_callback_and_context", lambda: _profile_restore_caller(
                original_user, original_test, users, records, callbacks, context)),
            ("exact_fixture_parent_children_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("whole_gl_ple_pe_je_expense_version_supplier_account_series_auth_content_restored", lambda: _contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger),
            ("original_queues_context_restored", lambda: _callbacks() == callbacks and _local_context() == context),
            ("zero_added_error_logs", lambda: set(frappe.get_all("Error Log", pluck="name")) == logs),
        ], primary=primary)
    checks.append("rollback_all_counts_full_sql_contents_accounts_series_auth_ledgers_callbacks_context")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "evidence": evidence, "hashes_before": before, "hashes_after": _contents(),
            "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "whole_ledger_before": ledger, "whole_ledger_after": _ledger_snapshot(), "gui_verified": False,
            "remaining": ["旧赊账控件实际语义", "真实字段权限撤销", "原生GUI付款按钮"],
            "concurrency_verified": False,
            "concurrency_plan": "两个独立连接：付款持Source锁时取消等待；取消先提交后付款取锁应拒；另测native reconciliation/update_after_submit入口"}
