"""One serial native Accounts User payable experiment; direct Python, rollback.

Prepare/run only in an exclusive business-write window. No bench execute,
commit, schema change, new DocPerm, remote download or background repost job.
Fixtures use native PI/PE/return/expense controllers and existing master rows.
User Permission and Property Setter fixtures only restrict access. Repost state
fixtures are explicitly persisted protocol states, not a worker acceptance.
Only small rollback/audit utilities are reused; no other smoke suite is called.
"""

from decimal import Decimal
from unittest.mock import patch

import frappe
from frappe.utils import add_days, getdate, today

from jifeng_erp.accounting import CREDIT, SOURCE
from jifeng_erp.catalog_original_validation import _rollback
from jifeng_erp.catalog_validation import _profile_audit, _profile_restore_caller
from jifeng_erp.credit_expense_validation import _contents as _credit_contents
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _restore_callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_dictionary_validation import _sql_row_hash
from jifeng_erp.supplier_reconciliation import MONEY_FIELDS, PLE, REPORT, query_payables


def _contents():
    """Full SQL contents, including auth/Series and all installed child rows."""
    result = _credit_contents()
    doctypes = {"Purchase Invoice", "Purchase Order", "Advance Payment Ledger Entry",
                "Repost Payment Ledger", "Repost Accounting Ledger", "Property Setter", "Permission Log",
                "DocPerm", "Custom DocPerm", "DocField", "Custom Field", "DocType", "Report", "Custom Role"}
    doctypes = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    result["Singles"] = _sql_row_hash(frappe.db.sql(
        "SELECT * FROM `tabSingles` ORDER BY doctype, field", as_dict=True))
    return result


def smoke(company, item_group, stock_uom, other_company, foreign_currency="USD"):
    """Native AP cutoff projection and source/field access, with exact cleanup."""
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import make_debit_note
    from erpnext.accounts.utils import is_immutable_ledger_enabled
    from frappe.desk import query_report
    from frappe.model import get_permitted_fields

    frappe.only_for("System Manager")
    assert frappe.db.db_type == "mariadb" and company != other_company
    assert not frappe.conf.get("read_from_replica")
    assert not frappe.form_dict.get("posting_date"), "取消验收不得继承其他请求的取消日期"
    for doctype, name in (("Company", company), ("Company", other_company),
                          ("Item Group", item_group), ("UOM", stock_uom), ("Currency", foreign_currency)):
        assert frappe.db.exists(doctype, name), "请提供既有公司、商品分组、单位和外币"
    assert not frappe.db.get_value("Item Group", item_group, "is_group")
    currency = frappe.db.get_value("Company", company, "default_currency")
    assert currency and currency != foreign_currency
    assert frappe.get_meta(SOURCE).get_field("supplier")
    assert frappe.get_meta("Journal Entry").get_field("jf_expense_voucher")
    assert query_report.get_report_doc(REPORT).add_total_row, "真实 AP 合计行门要求标准原生合计设置"

    def choose(doctype, filters):
        names = frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=1, order_by="name")
        assert names, "验收需要既有本币费用、应付、现金银行明细科目及成本中心"
        return names[0]

    common = {"company": company, "is_group": 0, "disabled": 0, "account_currency": currency}
    payable = choose("Account", {**common, "root_type": "Liability", "account_type": "Payable"})
    expense = choose("Account", {**common, "root_type": "Expense"})
    cash = choose("Account", {**common, "root_type": "Asset", "account_type": ["in", ["Cash", "Bank"]]})
    center = choose("Cost Center", {"company": company, "is_group": 0})
    supplier_group = choose("Supplier Group", {"is_group": 0})
    tables, counts = _tab_counts()
    changed = {SOURCE, "Purchase Invoice", "Payment Entry", "Journal Entry", "GL Entry", PLE,
               "Advance Payment Ledger Entry", "Supplier", "Item", "Account", "User", "User Permission",
               "Property Setter", "Permission Log", "Repost Payment Ledger", "Series", "Version", "DocShare",
               "DefaultValue", "Email Queue", "Notification Log", "Communication"}
    changed = {doctype for doctype in changed if doctype == "Series" or frappe.db.exists("DocType", doctype)}
    for doctype in tuple(changed - {"Series"}):
        changed.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    assert all(tables["tab" + doctype] == "InnoDB" for doctype in changed)
    assert frappe.db.sql("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                         "AND TABLE_NAME='__Auth'")[0][0] == "InnoDB"
    before, ledger_before = _contents(), _ledger_snapshot()
    callbacks, context = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    transaction_depth = frappe.db._disable_transaction_control
    logs = set(frappe.get_all("Error Log", pluck="name"))
    prefix = "JF-QA-AP-" + frappe.generate_hash(length=10)
    point = "jf_supplier_payables_" + frappe.generate_hash(length=8)
    cutoff, invoice_date = add_days(today(), -1), add_days(today(), -5)
    objects, records, users, checks, changed_meta, preexisting = [], set(), [], [], set(), set()
    observed, evidence = [], {}
    current, primary = "fixtures", None
    _begin_rollback_only(point)

    def native(action):
        old = frappe.in_test
        try:
            frappe.in_test = False
            return action()
        finally:
            frappe.in_test = old

    def track(document):
        objects.append(document)
        if document.name and (document.doctype, document.name) not in preexisting:
            records.add((document.doctype, document.name))
        records.update((child.doctype, child.name) for child in document.get_all_children() if child.name)
        return document

    def insert(doctype, **values):
        document = frappe.get_doc({"doctype": doctype, **values})
        objects.append(document)
        native(document.insert)
        return track(document)

    def actor(label, role, allowed_company):
        roles = (role,) if isinstance(role, str) else role
        user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid", first_name=prefix,
                      send_welcome_email=0, roles=[{"role": name} for name in roles])
        users.append(user.name)
        insert("User Permission", user=user.name, allow="Company", for_value=allowed_company,
               apply_to_all_doctypes=1)
        frappe.clear_cache(user=user.name)
        return user.name

    original_run = query_report.run

    def observe(*args, **kwargs):
        result = original_run(*args, **kwargs)
        observed.append(result)
        return result

    def read(*, party=None, date=None, account=None):
        old_sql, old_ledgers = _contents(), _ledger_snapshot()
        observed.clear()
        try:
            with patch.object(query_report, "run", observe):
                result = native(lambda: query_payables(company, party or supplier.name, date or cutoff, account))
        finally:
            assert _contents() == old_sql, "应付只读查询不得改变任何完整持久行"
            assert _ledger_snapshot() == old_ledgers
        assert len(observed) == 1
        report = observed[0]
        assert report["add_total_row"] and report["columns"][0]["fieldtype"] == "Date"
        assert report["result"] and isinstance(report["result"][-1], list)
        assert report["result"][-1][0] == ""
        native_rows = report["result"][:-1]
        actual = {(row["voucher_type"], row["voucher_no"], row["payable_account"]): row for row in result["rows"]}
        assert len(actual) == len(native_rows)
        for row in native_rows:
            projected = actual[row["voucher_type"], row["voucher_no"], row["party_account"]]
            assert projected["currency"] == row["currency"] == currency
            assert projected["posting_date"] == str(getdate(row["posting_date"]))
            assert all(Decimal(projected[field]) == Decimal(str(row[field])) for field in MONEY_FIELDS)
        assert not {"chart", "message", "report_summary", "bill_no", "supplier_name"}.intersection(result)
        return result

    def amount(result, document, field="outstanding"):
        rows = [row for row in result["rows"] if row["voucher_type"] == document.doctype and row["voucher_no"] == document.name]
        assert len(rows) == 1
        return Decimal(rows[0][field])

    def invoice(party, account=payable, invoice_currency=None, rate=20):
        document = insert("Purchase Invoice", company=company, supplier=party.name,
            posting_date=invoice_date, set_posting_time=1, posting_time="00:00:00",
            due_date=add_days(invoice_date, 30), bill_no=prefix,
            currency=invoice_currency or currency, conversion_rate=2 if invoice_currency else 1,
            credit_to=account, update_stock=0, disable_rounded_total=1,
            items=[{"item_code": item.name, "qty": 6, "rate": rate, "uom": stock_uom,
                    "conversion_factor": 1, "expense_account": expense, "cost_center": center}])
        native(document.submit)
        assert str(getdate(document.posting_date)) == invoice_date, "原生发票未保留显式验收记账日"
        return track(document)

    def payment(source, value, date):
        document = native(lambda: get_payment_entry(source.doctype, source.name, party_amount=value,
            bank_account=cash, bank_amount=value))
        objects.append(document)
        document.posting_date, document.reference_no, document.reference_date = date, prefix, date
        native(document.insert)
        native(document.submit)
        assert str(getdate(document.posting_date)) == date, "原生付款未保留显式验收记账日"
        return track(document)

    def clear_fixture_cache():
        for doctype in changed_meta:
            frappe.clear_cache(doctype=doctype)
            for user in {*users, original_user, "Administrator"}:
                frappe.cache.delete_value(f"masked_fields::{doctype}::::{user}")
        for user in users:
            frappe.clear_cache(user=user)
        for doctype, name in records:
            frappe.clear_document_cache(doctype, name)

    def restrict(doctype, name):
        insert("User Permission", user=clerk, allow=doctype, for_value=name,
               apply_to_all_doctypes=0, applicable_for=doctype)
        frappe.clear_cache(user=clerk)

    def field_restriction(doctype, field, property_name="permlevel"):
        setter_name = f"{doctype}-{field}-{property_name}"
        if frappe.db.exists("Property Setter", setter_name):
            preexisting.add(("Property Setter", setter_name))
        insert("Property Setter", doctype_or_field="DocField", doc_type=doctype, field_name=field,
               property=property_name, value="1", property_type="Int", is_system_generated=0)
        changed_meta.add(doctype)
        frappe.clear_cache(doctype=doctype)
        frappe.set_user(clerk)
        if property_name == "permlevel":
            assert field not in get_permitted_fields(doctype, permission_type="read")
        else:
            meta = frappe.get_meta(doctype)
            assert meta.get_field(field).mask and meta.get_field(field).permlevel not in meta.get_permlevel_access("mask")

    def rejected_gate(label, setup, action=None, errors=frappe.PermissionError, hidden=()):
        nonlocal current
        current = label
        old, old_callbacks, old_context = _contents(), _callbacks(), _local_context()
        nested = "jf_ap_reject_" + frappe.generate_hash(length=8)
        nested_primary = None
        _begin_rollback_only(nested)
        try:
            frappe.set_user("Administrator")
            native(setup)
            frappe.set_user(clerk)
            after_setup = _contents()
            if errors is None:
                (action or read)()
            else:
                try:
                    (action or read)()
                except errors as error:
                    assert not any(name in str(error) for name in hidden), "拒绝消息不能披露隐藏来源名称"
                else:
                    raise AssertionError("应付完整权限/状态门接受了应拒绝的查询")
            assert _contents() == after_setup
        except Exception as error:
            nested_primary = error
            raise
        finally:
            _profile_audit([
                ("nested_rollback", lambda: _rollback(nested, old_callbacks)),
                ("nested_cache_restore", clear_fixture_cache),
                ("nested_caller", lambda: frappe.set_user(clerk)),
                ("nested_callbacks", lambda: _restore_callbacks(old_callbacks)),
                ("nested_context", lambda: _restore_local_context(old_context)),
                ("nested_sql_restored", lambda: _contents() == old),
            ], primary=nested_primary)
        checks.append(label)

    def passed():
        checks.append(current)
        assert set(frappe.get_all("Error Log", pluck="name")) == logs

    try:
        frappe.set_user("Administrator")
        clerk = actor("reader", "Accounts User", company)
        # Native PI item-details loading requires Item.read. Accounts Manager
        # alone does not have it; use the standard purchasing/finance writer
        # combination, keeping the API reader a pure Accounts User.
        manager = actor("writer", ("Accounts Manager", "Purchase User"), company)
        outside = actor("outside", "Accounts User", other_company)
        item = insert("Item", item_code=prefix + "-service", item_group=item_group, stock_uom=stock_uom,
                      is_stock_item=0, is_purchase_item=1)
        supplier = insert("Supplier", supplier_name=prefix + "-supplier", supplier_type="Company",
            supplier_group=supplier_group, default_currency=currency, accounts=[{"company": company, "account": payable}])
        foreign_supplier = insert("Supplier", supplier_name=prefix + "-foreign", supplier_type="Company",
            supplier_group=supplier_group, default_currency=foreign_currency)
        foreign_account = insert("Account", company=company, account_name=prefix + "-foreign-payable",
            parent_account=frappe.db.get_value("Account", payable, "parent_account"),
            is_group=0, root_type="Liability", account_type="Payable", account_currency=foreign_currency)
        frappe.set_user(manager)
        pi = invoice(supplier)
        first = payment(pi, 40, add_days(today(), -4))
        second = payment(pi, 30, add_days(today(), -3))
        future = payment(pi, 10, add_days(today(), 1))
        returned = native(lambda: make_debit_note(pi.name))
        objects.append(returned)
        returned.posting_date = add_days(today(), -2)
        returned.set_posting_time, returned.posting_time = 1, "00:00:00"
        returned.set("items", [returned.items[0]])
        returned.items[0].qty = -1
        returned.disable_rounded_total = 1
        native(returned.insert)
        native(returned.submit)
        assert str(getdate(returned.posting_date)) == add_days(today(), -2)
        track(returned)
        foreign_pi = invoice(foreign_supplier, foreign_account.name, foreign_currency, rate=10)
        source = insert(SOURCE, company=company, posting_date=invoice_date, cost_center=center,
            payment_method=CREDIT, expense_account=expense, payable_account=payable,
            supplier=supplier.name, amount=80, summary=prefix + "-expense", arrears_reference=999)
        native(source.submit)
        source = frappe.get_doc(SOURCE, source.name)
        journal = track(frappe.get_doc("Journal Entry", source.journal_entry))
        other_source = insert(SOURCE, company=company, posting_date=invoice_date, cost_center=center,
            payment_method=CREDIT, expense_account=expense, payable_account=payable,
            supplier=supplier.name, amount=25, summary=prefix + "-permission-control")
        native(other_source.submit)
        frappe.set_user(clerk)

        current = "real_accounts_user_company_select_and_source_financial_read_without_grants"
        assert "Accounts Manager" not in frappe.get_roles()
        assert frappe.get_doc("Company", company).has_permission("select")
        assert frappe.get_doc("Supplier", supplier.name).has_permission("read")
        assert frappe.get_doc("Currency", currency).has_permission("read")
        assert all(frappe.get_doc(dt, name).has_permission("read") for dt, name in (
            ("Purchase Invoice", pi.name), ("Payment Entry", first.name), ("Journal Entry", journal.name), (SOURCE, source.name)))
        passed()

        current = "real_native_total_cutoff_two_partial_payments_return_future_and_expense_je"
        baseline = read()
        assert amount(baseline, pi) == 30
        assert amount(baseline, pi, "invoiced") == 120
        assert amount(baseline, journal) == 80
        assert Decimal(str(frappe.db.get_value("Purchase Invoice", pi.name, "outstanding_amount"))) != 30
        selected = read(account=payable)
        assert selected["rows"] == baseline["rows"]
        passed()

        current = "foreign_payable_native_base_projection_without_selected_foreign_currency"
        foreign = read(party=foreign_supplier.name)
        assert amount(foreign, foreign_pi) == 120
        assert foreign["currency"] == currency
        passed()
        rejected_gate("explicit_foreign_account_refuses_native_account_currency_switch", lambda: None,
            lambda: read(party=foreign_supplier.name, account=foreign_account.name), frappe.ValidationError)
        rejected_gate("outside_company_native_scope_is_hidden", lambda: None,
            lambda: (frappe.set_user(outside), read())[1])
        rejected_gate("hidden_payment_source_refuses_partial_invoice_balance",
            lambda: restrict("Payment Entry", second.name), hidden=(first.name, future.name))
        ledger_name = frappe.db.get_value(PLE, {"voucher_type": "Purchase Invoice", "voucher_no": pi.name}, "name")
        rejected_gate("hidden_payment_ledger_refuses_native_permission_filtered_balance",
            lambda: restrict(PLE, ledger_name))
        rejected_gate("hidden_payable_account_refuses_partial_account_scope",
            lambda: restrict("Account", cash), hidden=(payable, foreign_account.name))
        rejected_gate("unreadable_invoice_counterpart_cannot_be_authorized_by_readable_payment",
            lambda: restrict("Purchase Invoice", foreign_pi.name), hidden=(pi.name, returned.name))
        rejected_gate("hidden_managed_expense_reverse_source_refuses_journal_balance",
            lambda: restrict(SOURCE, other_source.name), hidden=(source.name,))

        for doctype, field in ((PLE, "amount"), ("Purchase Invoice", "grand_total"), ("Account", "account_currency")):
            rejected_gate("native_field_level_revocation_" + doctype + "_" + field,
                lambda dt=doctype, name=field: field_restriction(dt, name))
        rejected_gate("native_masked_invoice_amount_refuses_aliased_ap_balance",
            lambda: field_restriction("Purchase Invoice", "grand_total", "mask"))

        def repost_state(status):
            job = insert("Repost Payment Ledger", company=company, posting_date=invoice_date, add_manually=1,
                repost_vouchers=[{"voucher_type": "Purchase Invoice", "voucher_no": pi.name}])
            # Never submit: on_submit publishes a Redis job outside SQL rollback.
            # This persisted protocol-state fixture only validates the read gate.
            frappe.db.set_value(job.doctype, job.name, {"docstatus": 1, "repost_status": status})
            for row in job.repost_vouchers:
                frappe.db.set_value(row.doctype, row.name, "docstatus", 1)

        for status in ("Queued", "Failed"):
            rejected_gate("native_persisted_repost_state_" + status,
                lambda value=status: repost_state(value), errors=frappe.ValidationError)
        rejected_gate("native_persisted_completed_repost_state_is_readable",
            lambda: repost_state("Completed"), errors=None)

        current = "cancelled_sources_follow_installed_immutable_ledger_cutoff_semantics"
        immutable = bool(is_immutable_ledger_enabled())
        frappe.set_user(manager)
        for document in (future, second, first, returned, pi):
            native(lambda doc=document: frappe.get_doc(doc.doctype, doc.name).cancel())
        assert all(frappe.db.get_value(doc.doctype, doc.name, "docstatus") == 2
                   for doc in (future, second, first, returned, pi))
        frappe.set_user(clerk)
        cancelled = read()
        if immutable:
            assert cancelled["rows"] == baseline["rows"]
            assert amount(cancelled, pi) == 30
        else:
            assert not any(row["voucher_no"] == pi.name for row in cancelled["rows"])
        assert amount(cancelled, journal) == 80
        passed()
        evidence = {"roles": ["Accounts User"], "native_total_first_type": "Date",
            "native_total_first_cell": "", "cutoff_invoice_outstanding": 30, "expense_outstanding": 80,
            "foreign_base_outstanding": 120, "currency": currency, "immutable_ledger": immutable,
            "company_native_select": True, "company_select_only_native_verified": False,
            "repost_worker_started": False, "source_ids_synthetic": True}
    except Exception as error:
        primary = error
        error.add_note("供应商应付原生失败门：" + current)
        error.add_note("已通过门：" + repr(checks))
        raise
    finally:
        def collect():
            for document in objects:
                if document.name and (document.doctype, document.name) not in preexisting:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)

        _profile_audit([
            ("collect_fixture_parent_children", collect),
            ("native_savepoint_and_only_added_rollback_callbacks", lambda: _rollback(point, callbacks)),
            ("clear_native_permission_and_document_cache", clear_fixture_cache),
            ("restore_caller_callbacks_context", lambda: _profile_restore_caller(
                original_user, original_test, users, records, callbacks, context)),
            ("fresh_sql_fixture_parent_children_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("whole_sql_auth_series_metadata_financial_contents_restored", lambda: _contents() == before),
            ("all_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger_before),
            ("original_callbacks_context_restored", lambda: _callbacks() == callbacks and _local_context() == context),
            ("transaction_control_depth_restored", lambda: frappe.db._disable_transaction_control == transaction_depth),
            ("zero_added_error_logs", lambda: set(frappe.get_all("Error Log", pluck="name")) == logs),
        ], primary=primary)
    checks.append("fresh_full_sql_auth_series_ledgers_callbacks_context_rollback")
    return {"passed": True, "checks": checks, "gate_count": len(checks), "business_data": "rolled_back",
            "evidence": evidence, "hashes_before": before, "hashes_after": _contents(),
            "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "gui_verified": False, "concurrency_verified": False,
            "remaining": ["Company 仅 select 的真实角色场景", "两个连接的并发变更", "真实重过账后台 worker", "旧对账页面适配"]}
