"""Rollback-only native acceptance for sales transaction and posted invoice reports."""

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _ledger_snapshot, _read
from jifeng_erp.subcontract_validation import _denied, _invalid


def _mode(invoice_type):
    frappe.db.set_single_value("POS Settings", "invoice_type", invoice_type)
    frappe.clear_document_cache("POS Settings", "POS Settings")


def _masters(company, item_group, stock_uom):
    suffix = frappe.generate_hash(length=10)
    company_doc = frappe.get_doc("Company", company)
    accounts = {}
    for key, field, query in (
        ("income", "default_income_account", {"root_type": "Income"}),
        ("expense", "default_expense_account", {"account_type": "Cost of Goods Sold"}),
        ("cash", "default_cash_account", {"account_type": "Cash"}),
        ("receivable", "default_receivable_account", {"account_type": "Receivable"}),
    ):
        accounts[key] = company_doc.get(field) or frappe.db.get_value(
            "Account", {"company": company, "is_group": 0, **query}, "name")
    cost_center = company_doc.cost_center or frappe.db.get_value(
        "Cost Center", {"company": company, "is_group": 0}, "name")
    if not all(accounts.values()) or not cost_center:
        frappe.throw("销售验收需要公司本币及收入、销售成本、现金、应收科目和明细成本中心")
    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if not customer_group or not territory:
        frappe.throw("销售验收需要客户分组和地区")
    warehouse = frappe.get_doc({"doctype": "Warehouse", "company": company,
                                "warehouse_name": "JF-SALES-" + suffix}).insert()
    item = frappe.get_doc({"doctype": "Item", "item_code": "JF-SALES-" + suffix,
        "item_group": item_group, "stock_uom": stock_uom, "is_stock_item": 1,
        "valuation_method": "Moving Average", "item_defaults": [{"company": company,
            "default_warehouse": warehouse.name, "income_account": accounts["income"],
            "expense_account": accounts["expense"], "selling_cost_center": cost_center}]}).insert()
    customers = [frappe.get_doc({"doctype": "Customer", "customer_name": f"JF-SALES-{index}-{suffix}",
        "customer_type": "Company", "customer_group": customer_group, "territory": territory}).insert()
        for index in range(3)]
    payment = frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": "JF-SALES-CASH-" + suffix,
        "type": "Cash", "accounts": [{"company": company, "default_account": accounts["cash"]}]}).insert()
    price_list = frappe.get_doc({"doctype": "Price List", "price_list_name": "JF-SALES-PL-" + suffix,
        "currency": company_doc.default_currency, "selling": 1, "enabled": 1}).insert()
    cashier = frappe.get_doc({"doctype": "User", "email": f"jf-sales-{suffix}@example.invalid",
        "first_name": "销售原生验收", "send_welcome_email": 0, "roles": [{"role": role} for role in
        ("Sales Manager", "Accounts Manager", "Stock User", "Item Manager")]}).insert()
    profile = frappe.get_doc({"doctype": "POS Profile", "name": "JF-SALES-PROFILE-" + suffix,
        "company": company, "customer": customers[0].name, "customer_group": customer_group,
        "territory": territory, "currency": company_doc.default_currency,
        "selling_price_list": price_list.name, "warehouse": warehouse.name,
        "income_account": accounts["income"], "expense_account": accounts["expense"],
        "cost_center": cost_center, "write_off_account": accounts["expense"],
        "write_off_cost_center": cost_center, "account_for_change_amount": accounts["cash"],
        "update_stock": 1, "ignore_pricing_rule": 1, "disable_rounded_total": 1,
        "payments": [{"mode_of_payment": payment.name, "default": 1}],
        "applicable_for_users": [{"user": cashier.name, "default": 1}]}).insert()
    receipt = frappe.get_doc({"doctype": "Stock Entry", "company": company,
        "stock_entry_type": "Material Receipt", "posting_date": add_days(today(), -1),
        "posting_time": "06:00:00", "set_posting_time": 1,
        "items": [{"item_code": item.name, "qty": 40, "basic_rate": 2,
                   "t_warehouse": warehouse.name, "expense_account": accounts["expense"],
                   "cost_center": cost_center}]}).insert()
    receipt.submit()
    for field in ("so_required", "dn_required"):
        frappe.db.set_single_value("Selling Settings", field, "No")
    frappe.clear_document_cache("Selling Settings", "Selling Settings")
    frappe.db.set_single_value("POS Settings", "post_change_gl_entries", 0)
    return frappe._dict(company=company, suffix=suffix, currency=company_doc.default_currency,
        item=item, warehouse=warehouse, customer=customers[0], customers=customers,
        payment=payment, profile=profile, cashier=cashier, price_list=price_list,
        cost_center=cost_center, accounts=frappe._dict(accounts))


def _invoice(fixture, doctype="Sales Invoice", pos=False, qty=2, paid=0, duplicate=False,
             customer=None, posting_date=None, posting_time="08:00:00", currency=None, conversion_rate=1,
             update_stock=1):
    items = [{"item_code": fixture.item.name, "qty": qty, "rate": 10.123,
        "warehouse": fixture.warehouse.name, "income_account": fixture.accounts.income,
        "expense_account": fixture.accounts.expense, "cost_center": fixture.cost_center}]
    if duplicate:
        items.append({**items[0], "qty": 1})
    invoice = frappe.get_doc({"doctype": doctype, "company": fixture.company,
        "customer": customer or fixture.customer.name, "debit_to": fixture.accounts.receivable,
        "currency": currency or fixture.currency, "conversion_rate": conversion_rate,
        "selling_price_list": fixture.price_list.name, "price_list_currency": currency or fixture.currency,
        "plc_conversion_rate": conversion_rate, "posting_date": posting_date or today(),
        "posting_time": posting_time, "set_posting_time": 1, "update_stock": int(update_stock),
        "disable_rounded_total": 1, "is_pos": int(pos or doctype == "POS Invoice"),
        "is_created_using_pos": int(pos and doctype == "Sales Invoice"),
        "pos_profile": fixture.profile.name if pos or doctype == "POS Invoice" else None,
        "account_for_change_amount": fixture.accounts.cash, "ignore_pricing_rule": 1,
        "items": items, "payments": [{"mode_of_payment": fixture.payment.name, "amount": paid}] if paid else []})
    invoice.insert()
    invoice.submit()
    invoice.reload()  # The GL method can mutate payment base_amount only in memory.
    return invoice


def _opening(fixture):
    opening = frappe.get_doc({"doctype": "POS Opening Entry", "company": fixture.company,
        "pos_profile": fixture.profile.name, "user": frappe.session.user,
        "period_start_date": today() + " 00:00:00", "posting_date": today(),
        "balance_details": [{"mode_of_payment": fixture.payment.name, "opening_amount": 20}]}).insert()
    opening.submit()
    opening.reload()
    assert opening.status == "Open"
    return opening


def _closing(opening):
    from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import get_invoices

    end = today() + " 23:59:59"
    data = get_invoices(opening.period_start_date, end, opening.pos_profile, opening.user)
    closing = frappe.get_doc({"doctype": "POS Closing Entry", "company": opening.company,
        "pos_opening_entry": opening.name, "pos_profile": opening.pos_profile, "user": opening.user,
        "period_start_date": opening.period_start_date, "period_end_date": end,
        "posting_date": today(), "posting_time": "23:59:59", "set_posting_time": 1})
    for invoice in data["invoices"]:
        table, field = ("pos_invoices", "pos_invoice") if invoice.doctype == "POS Invoice" else (
            "sales_invoices", "sales_invoice")
        closing.append(table, {field: invoice.name, "posting_date": invoice.posting_date,
            "customer": invoice.customer, "is_return": invoice.is_return,
            "return_against": invoice.return_against, "grand_total": invoice.grand_total})
    closing.grand_total = sum(flt(invoice.grand_total) for invoice in data["invoices"])
    closing.net_total = sum(flt(invoice.net_total) for invoice in data["invoices"])
    closing.total_quantity = sum(flt(invoice.total_qty) for invoice in data["invoices"])
    for payment in data["payments"]:
        closing.append("payment_reconciliation", {"mode_of_payment": payment.mode_of_payment,
            "opening_amount": 0, "expected_amount": payment.amount, "closing_amount": payment.amount})
    closing.insert()
    original_test = frappe.in_test
    try:
        # Empty POS lists in native global test mode otherwise pull *all* site
        # unconsolidated POS documents. Only explicit POS lists use that branch.
        frappe.in_test = bool(closing.pos_invoices)
        closing.submit()
    finally:
        frappe.in_test = original_test
    closing.reload()
    opening.reload()
    assert closing.status == "Submitted" and opening.status == "Closed"
    return closing


def _cancel_closing(closing):
    original_test = frappe.in_test
    try:
        frappe.in_test = True
        closing.reload()
        closing.cancel()
    finally:
        frappe.in_test = original_test


def _return(invoice, qty=1, index=0):
    if invoice.doctype == "POS Invoice":
        from erpnext.accounts.doctype.pos_invoice.pos_invoice import make_sales_return
    else:
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
    document = make_sales_return(invoice.name)
    selected = document.items[index]
    document.set("items", [selected])
    selected.qty = -qty
    document.posting_date, document.posting_time, document.set_posting_time = today(), "11:00:00", 1
    document.set("payments", [])
    for field in ("paid_amount", "base_paid_amount", "change_amount", "base_change_amount",
                  "write_off_amount", "base_write_off_amount"):
        document.set(field, 0)
    document.write_off_outstanding_amount_automatically = 0
    document.calculate_taxes_and_totals()
    if document.is_pos:
        document.append("payments", {"mode_of_payment": invoice.payments[0].mode_of_payment,
            "amount": flt(document.rounded_total or document.grand_total, document.precision("grand_total"))})
    document.insert()
    document.submit()
    document.reload()
    return document


def _filters(fixture, **values):
    return {"company": fixture.company, "from_date": add_days(today(), -1), "to_date": today(),
            "customer": fixture.customer.name, **values}


def _row(report, fixture, document, **values):
    rows = [row for row in _read(report, _filters(fixture, **values)) if row["document"] == document.name]
    assert len(rows) == 1, rows
    return rows[0]


def _money(row, invoice):
    for field in ("net_total", "base_net_total", "grand_total", "base_grand_total", "paid_amount",
                  "base_paid_amount", "change_amount", "base_change_amount", "write_off_amount",
                  "base_write_off_amount"):
        assert flt(row[field]) == flt(invoice.get(field)), (field, row[field], invoice.get(field))
    gross = sum(flt(payment.amount) for payment in invoice.payments)
    base_gross = sum(flt(payment.base_amount) for payment in invoice.payments)
    assert flt(row["gross_payment_amount"], 6) == flt(gross, 6)
    assert flt(row["base_gross_payment_amount"], 6) == flt(base_gross, 6)
    assert flt(row["net_tender"], 6) == flt(gross - flt(invoice.change_amount), 6)
    assert flt(row["base_net_tender"], 6) == flt(base_gross - flt(invoice.base_change_amount), 6)
    assert row["currency"] == invoice.currency
    assert flt(row["conversion_rate"]) == flt(invoice.conversion_rate)


def _api(name, filters):
    from frappe.desk.query_report import run

    before = _ledger_snapshot()
    try:
        return run(name, filters=filters)["result"]
    finally:
        assert _ledger_snapshot() == before, "Report API changed ledger contents"


def _group(fixture):
    sources = [_invoice(fixture, "POS Invoice", qty=1, paid=20, customer=customer.name)
               for customer in fixture.customers[:2]]
    log = frappe.get_doc({"doctype": "POS Invoice Merge Log", "company": fixture.company,
        "customer": fixture.customers[2].name, "customer_group": fixture.customers[0].customer_group,
        "merge_invoices_based_on": "Customer Group", "posting_date": today(), "posting_time": "14:00:00",
        "pos_invoices": [{"pos_invoice": doc.name, "customer": doc.customer, "is_return": 0,
                          "posting_date": doc.posting_date, "grand_total": doc.grand_total} for doc in sources]}).insert()
    log.submit()
    for doc in sources:
        doc.reload()
    return log, sources


def _permission_edges(fixture, mirror, pos_sale, pos_return, direct, administrator, read_report):
    """Native list-visible source gaps must fail; optional hidden links stay private."""
    original_user = frappe.session.user
    frappe.set_user(administrator)
    user = frappe.get_doc({"doctype": "User", "email": f"jf-sales-source-{fixture.suffix}@example.invalid",
        "first_name": "销售来源权限验收", "send_welcome_email": 0, "roles": [{"role": "Sales User"}]}).insert()
    frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                   "for_value": fixture.company, "apply_to_all_doctypes": 1}).insert()
    permission = frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "POS Invoice",
        "for_value": pos_return.name, "apply_to_all_doctypes": 0, "applicable_for": "POS Invoice"}).insert()
    shares = [frappe.share.add(doctype, name, user=user.name, read=1, notify=0) for doctype, name in
              (("Sales Invoice", mirror.name), ("POS Invoice", pos_return.name))]
    try:
        frappe.set_user(user.name)
        assert frappe.get_doc("Sales Invoice", mirror.name).has_permission("read")
        assert not frappe.get_doc("POS Invoice", pos_sale.name).has_permission("read")
        _denied(lambda: _read(read_report, _filters(fixture)))
        _denied(lambda: _read(read_report, _filters(fixture, source_type="零售凭证", only_returns=1)))
    finally:
        frappe.set_user(administrator)
        for share in shares:
            share.delete()
        permission.delete()
    permission = frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Sales Invoice",
        "for_value": direct.name, "apply_to_all_doctypes": 0, "applicable_for": "Sales Invoice"}).insert()
    share = frappe.share.add("POS Invoice", pos_sale.name, user=user.name, read=1, notify=0)
    direct_share = frappe.share.add("Sales Invoice", direct.name, user=user.name, read=1, notify=0)
    try:
        frappe.set_user(user.name)
        assert frappe.get_doc("POS Invoice", pos_sale.name).has_permission("read")
        assert not frappe.get_doc("Sales Invoice", mirror.name).has_permission("read")
        row = _row(read_report, fixture, pos_sale, source_type="零售凭证")
        assert row["posted_invoice"] is None and row["has_posting_reference"] == 1
    finally:
        frappe.set_user(administrator)
        share.delete()
        direct_share.delete()
        permission.delete()
        frappe.set_user(original_user)


def _dashboard_compare(fixture, report, administrator):
    from jifeng_erp.dashboard import overview

    original_user = frappe.session.user
    before = _ledger_snapshot()
    try:
        frappe.set_user(administrator)
        filters = _filters(fixture, customer=None)
        rows = _read(report, filters)
        result = overview(company=fixture.company, from_date=filters["from_date"], to_date=filters["to_date"])
        assert flt(result["metrics"]["sales"], 6) == flt(sum(flt(row["base_net_total"]) for row in rows), 6)
    finally:
        frappe.set_user(original_user)
        assert _ledger_snapshot() == before, "Dashboard or posted ledger changed ledger contents"


def smoke(company, item_group, stock_uom, report_api=True):
    """Create only private random fixtures, exercise native actions, then roll back."""
    frappe.only_for("System Manager")
    from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_pos_reserved_qty
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
    from jifeng_erp.sales_reports import posted_sales_ledger, sales_transaction_ledger

    point, original_user, original_test = "jf_sales_reports", frappe.session.user, frappe.in_test
    _begin_rollback_only(point)
    try:
        fixture = _masters(company, item_group, stock_uom)
        _mode("Sales Invoice")
        ordinary = _invoice(fixture, posting_date=add_days(today(), -1))
        _money(_row(sales_transaction_ledger, fixture, ordinary), ordinary)
        assert not ordinary.payments and flt(_row(sales_transaction_ledger, fixture, ordinary)["net_tender"]) == 0
        source_return = _return(ordinary)
        assert _row(sales_transaction_ledger, fixture, source_return, from_date=today())["source_document"] == ordinary.name
        source_return.cancel()
        assert source_return.name not in {row["document"] for row in _read(sales_transaction_ledger, _filters(fixture))}
        frappe.set_user(fixture.cashier.name)
        opening = _opening(fixture)
        _invalid(lambda: _opening(fixture))
        direct = _invoice(fixture, pos=True, paid=30)
        _money(_row(sales_transaction_ledger, fixture, direct), direct)
        assert direct.change_amount > 0 and direct.payments[0].base_amount == direct.base_paid_amount
        direct_closing = _closing(opening)
        direct.reload()
        assert direct.pos_closing_entry == direct_closing.name
        assert _row(sales_transaction_ledger, fixture, direct)["closing_entry"] == direct_closing.name

        _mode("POS Invoice")
        pos_opening = _opening(fixture)
        pos_sale = _invoice(fixture, "POS Invoice", qty=2, paid=35, duplicate=True, posting_time="10:00:00")
        pos_return = _return(pos_sale, index=1)
        assert pos_return.items[0].pos_invoice_item == pos_sale.items[1].name
        reserved = get_pos_reserved_qty(fixture.item.name, fixture.warehouse.name)
        assert flt(reserved) == 2, reserved
        assert not frappe.get_all("Stock Ledger Entry", filters={"voucher_type": "POS Invoice", "voucher_no": pos_sale.name})
        before = _read(sales_transaction_ledger, _filters(fixture))
        originals = {(row["doc_type"], row["document"]): flt(row["base_net_total"]) for row in before}
        assert {pos_sale.name, pos_return.name} <= {row["document"] for row in before}
        assert len(_read(posted_sales_ledger, _filters(fixture))) == 2
        _dashboard_compare(fixture, posted_sales_ledger, original_user)
        closing = _closing(pos_opening)
        pos_sale.reload()
        pos_return.reload()
        assert pos_sale.consolidated_invoice and pos_return.consolidated_invoice
        after = _read(sales_transaction_ledger, _filters(fixture))
        assert {(row["doc_type"], row["document"]): flt(row["base_net_total"]) for row in after} == originals
        assert len(_read(posted_sales_ledger, _filters(fixture))) == 4
        _dashboard_compare(fixture, posted_sales_ledger, original_user)
        assert flt(get_pos_reserved_qty(fixture.item.name, fixture.warehouse.name)) == 0
        _invalid(lambda: make_sales_return(pos_sale.consolidated_invoice))
        mirror = frappe.get_doc("Sales Invoice", pos_sale.consolidated_invoice)
        assert {row.pos_invoice_item for row in mirror.items} == {row.name for row in pos_sale.items}
        _permission_edges(fixture, mirror, pos_sale, pos_return, direct, original_user, sales_transaction_ledger)
        original_child = mirror.items[0].pos_invoice_item
        try:
            frappe.db.set_value("Sales Invoice Item", mirror.items[0].name, "pos_invoice_item", "JF-MISSING-SOURCE")
            _invalid(lambda: _read(sales_transaction_ledger, _filters(fixture)))
        finally:
            frappe.db.set_value("Sales Invoice Item", mirror.items[0].name, "pos_invoice_item", original_child)
        _cancel_closing(closing)
        pos_sale.reload()
        pos_return.reload()
        assert not pos_sale.consolidated_invoice and not pos_return.consolidated_invoice
        assert len(_read(posted_sales_ledger, _filters(fixture))) == 2
        _dashboard_compare(fixture, posted_sales_ledger, original_user)
        assert {(row["doc_type"], row["document"]): flt(row["base_net_total"])
                for row in _read(sales_transaction_ledger, _filters(fixture))} == originals
        pos_return.cancel()
        pos_sale.cancel()
        assert flt(get_pos_reserved_qty(fixture.item.name, fixture.warehouse.name)) == 0

        group_log, group_sources = _group(fixture)
        group_filters = _filters(fixture, customer=None, cashier=fixture.cashier.name)
        group_rows = _read(sales_transaction_ledger, group_filters)
        assert {doc.name for doc in group_sources} <= {row["document"] for row in group_rows}
        assert group_log.consolidated_invoice not in {row["document"] for row in group_rows}
        group_invoice = frappe.get_doc("Sales Invoice", group_log.consolidated_invoice)
        assert not group_invoice.pos_profile and group_invoice.customer == fixture.customers[2].name
        group_log.reload()
        group_log.cancel()
        for source in group_sources:
            source.reload()
            source.cancel()
        pos_opening.reload()
        pos_opening.cancel()
        _cancel_closing(direct_closing)
        direct.reload()
        direct.cancel()
        opening.reload()
        opening.cancel()

        frappe.set_user(original_user)
        foreign_currency = "USD" if fixture.currency != "USD" else "CNY"
        foreign_list = frappe.get_doc({"doctype": "Price List", "price_list_name": "JF-SALES-FX-" + fixture.suffix,
            "currency": foreign_currency, "selling": 1, "enabled": 1}).insert()
        fixture.profile.reload()
        fixture.profile.currency, fixture.profile.selling_price_list = foreign_currency, foreign_list.name
        fixture.profile.save()
        fixture.price_list = foreign_list
        _mode("Sales Invoice")
        frappe.set_user(fixture.cashier.name)
        fx_opening = _opening(fixture)
        foreign = _invoice(fixture, pos=True, qty=1, paid=50, currency=foreign_currency, conversion_rate=7)
        _money(_row(sales_transaction_ledger, fixture, foreign), foreign)
        fx_closing = _closing(fx_opening)
        for role in ("Accounts User", "Accounts Manager", "Sales User", "Sales Manager"):
            frappe.set_user(original_user)
            user = frappe.get_doc({"doctype": "User", "email": f"jf-sales-{role.lower().replace(' ', '-')}-{fixture.suffix}@example.invalid",
                "first_name": "销售报表权限验收", "send_welcome_email": 0, "roles": [{"role": role}]}).insert()
            frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": "Company",
                           "for_value": company, "apply_to_all_doctypes": 1}).insert()
            frappe.set_user(user.name)
            if role.startswith("Sales"):
                assert not frappe.has_permission("Sales Invoice", "read")
                _denied(lambda: _read(sales_transaction_ledger, _filters(fixture)))
                _denied(lambda: _read(posted_sales_ledger, _filters(fixture)))
                if report_api:
                    _denied(lambda: _api("Jifeng Sales Transaction Ledger", _filters(fixture)))
                    _denied(lambda: _api("Jifeng Posted Sales Ledger", _filters(fixture)))
                if role == "Sales Manager":
                    frappe.set_user(original_user)
                    share = frappe.share.add("Sales Invoice", foreign.name, user=user.name, read=1, notify=0)
                    try:
                        frappe.set_user(user.name)
                        assert not frappe.has_permission("POS Profile", "read", doc=fixture.profile.name)
                        assert frappe.has_permission("POS Profile", "select", doc=fixture.profile.name)
                        _money(_row(sales_transaction_ledger, fixture, foreign, pos_profile=fixture.profile.name), foreign)
                        _money(_row(posted_sales_ledger, fixture, foreign, pos_profile=fixture.profile.name), foreign)
                    finally:
                        frappe.set_user(original_user)
                        share.delete()
                continue
            _money(_row(sales_transaction_ledger, fixture, foreign), foreign)
            _money(_row(posted_sales_ledger, fixture, foreign), foreign)
            if report_api:
                assert foreign.name in {row["document"] for row in _api("Jifeng Sales Transaction Ledger", _filters(fixture))}
                assert foreign.name in {row["document"] for row in _api("Jifeng Posted Sales Ledger", _filters(fixture))}
        frappe.set_user(fixture.cashier.name)
        _cancel_closing(fx_closing)
        foreign.reload()
        foreign.cancel()
        fx_opening.reload()
        fx_opening.cancel()
        frappe.set_user(original_user)
        ordinary.reload()
        ordinary.cancel()
        assert not _read(sales_transaction_ledger, _filters(fixture))
        assert not _read(posted_sales_ledger, _filters(fixture))
        frappe.set_user("Guest")
        _denied(lambda: _read(sales_transaction_ledger, _filters(fixture)))
        _denied(lambda: _read(posted_sales_ledger, _filters(fixture)))
        return {"passed": True, "business_data": "rolled_back", "checks": [
            "ordinary_si_zero_invoice_tender", "return_source_outside_dates", "cancel_si_return_excluded",
            "native_si_pos_opening_and_duplicate_open_guard", "persisted_gross_payments_reload",
            "change_deducted_once_original_and_base", "direct_pos_si_closing_link",
            "pos_reserved_before_merge_no_pos_sle", "duplicate_item_exact_return_source",
            "transaction_count_and_amount_stable_after_merge", "posted_si_appears_only_when_submitted",
            "merged_pos_reservation_released", "consolidated_si_direct_return_blocked",
            "hidden_mirror_pos_source_rejected", "hidden_pos_return_source_rejected",
            "unreadable_optional_posted_invoice_not_disclosed",
            "missing_mirror_child_source_rejected", "closing_cancel_unlinks_and_reverses_native_si",
            "pos_cancel_releases_reservation", "native_customer_group_merge_exact_provenance",
            "original_group_customers_retained", "foreign_stored_currency_and_company_values",
            "accounts_user_manager_report_access", "native_sales_only_financial_read_report_denied",
            "sales_manager_profile_select_with_exact_shared_voucher_without_profile_read",
            "dashboard_posted_sales_equals_signed_base_invoice_sum",
            "native_cancellation_excludes_all_fixture_transactions",
            "all_ledger_contents_unchanged_by_reports", "guest_denied",
        ] + (["native_report_api_role_access"] if report_api else [])}
    finally:
        frappe.set_user(original_user)
        frappe.in_test = original_test
        _end_rollback_only(point)
        for doctype in ("POS Settings", "Selling Settings"):
            frappe.clear_document_cache(doctype, doctype)
