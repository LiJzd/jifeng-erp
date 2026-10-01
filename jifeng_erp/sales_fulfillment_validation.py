"""Serial rollback-only native sales source read experiment.

Run once through direct Python in an exclusive business-write window, never
``bench execute``. Synthetic users retain actual installed roles and scoped
User Permissions. No DocPerm grants, ignore_permissions, force, commits, GUI,
schema changes or direct ledger edits are used. Native in_test mode only permits the
installed synchronous stock repost path; this is not a background-worker test.
"""

import json

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.catalog_validation import _profile_audit, _profile_restore_caller, _profile_rollback
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.fulfillment_validation import _preflight, _stamp
from jifeng_erp.inventory_validation import _finish_fixture_reposts, _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context
from jifeng_erp.product_dictionary_validation import _native_contents, _sql_row_hash
from jifeng_erp.sales_fulfillment_workbench import FLOW, PARENTS, TABLES, query_orders, read_order


ROLES = {
    "writer": ("Sales User", "Stock Manager", "Accounts Manager", "Purchase User", "Manufacturing User"),
    "reader": ("Sales User", "Accounts User", "Stock User", "Manufacturing User"),
    "no_flow": ("Sales User", "Accounts User", "Stock User"),
    "other": ("Sales User", "Accounts User", "Stock User", "Manufacturing User"),
}


def _contents():
    result = _native_contents()
    doctypes = set(PARENTS) | {value[1] for value in TABLES.values()} | {
        "Company", "Warehouse", "Price List", "Customer", "Bin", "Repost Item Valuation",
        "Repost Payment Ledger", "Repost Accounting Ledger", "Stock Ledger Entry", "GL Entry",
        "DocType", "DocPerm", "Custom DocPerm", "Custom Field", "Property Setter", "Error Log",
        "Communication", "Email Queue", "Notification Log", "Activity Log",
    }
    doctypes = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    result["Singles"] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tabSingles` ORDER BY doctype, field", as_dict=True))
    return result


def smoke(company, other_company, item_group, stock_uom):
    """Same-item rows, batch DN/SI, stock SI, returns, payments and isolation."""
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
    from erpnext.controllers.sales_and_purchase_return import make_return_doc
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as invoice_from_delivery

    from jifeng_erp.workflow import create_sales_order_flows

    frappe.only_for("System Manager")
    assert other_company and other_company != company and frappe.db.exists("Company", other_company)
    fixture = _preflight(company, item_group, stock_uom)
    cash = frappe.db.get_value("Company", company, "default_cash_account") or frappe.db.get_value(
        "Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name")
    assert cash, "销售履行收款验收需要既有本公司现金科目"
    tables, counts = _tab_counts()
    before, ledger_before = _contents(), _ledger_snapshot()
    callbacks, context = _callbacks(), _local_context()
    original_user, original_test = frappe.session.user, frappe.in_test
    original_repost, original_max_writes = frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION
    prefix = "JF-QA-SALES-FOLLOW-" + frappe.generate_hash(length=10)
    point = "jf_sales_follow_" + frappe.generate_hash(length=8)
    users, objects, records, checks, roles, vouchers = [], [], set(), [], {}, set()
    current, primary = "fixture_setup", None

    def remember(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        return document

    def insert(doctype, **values):
        return remember(frappe.get_doc({"doctype": doctype, **values})).insert()

    def native(document, action):
        old_repost, old_writes = frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION
        try:
            return getattr(document, action)()
        finally:
            frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION = old_repost, old_writes

    def post(document):
        # Match Desk's rehydration of mapper output; native DN return status
        # updaters are configured by the controller's construction-time flags.
        document = remember(frappe.get_doc(document.as_dict()))
        native(document, "insert")
        records.add((document.doctype, document.name))
        native(document, "submit")
        document.reload()
        vouchers.add((document.doctype, document.name))
        return document

    def completed_reposts():
        _finish_fixture_reposts(company, {fixture.item.name}, {fixture.warehouse.name}, vouchers)

    def read(action=None):
        state, queued, local = _contents(), _callbacks(), _local_context()
        result = action() if action else read_order(company, order.name)
        assert _contents() == state and _callbacks() == queued and _local_context() == local, "只读履行查询改变了完整原生来源"
        return result

    def rejected(action, hidden=()):
        state, queued, local = _contents(), _callbacks(), _local_context()
        try:
            action()
        except frappe.PermissionError as error:
            assert all(name not in str(error) for name in hidden), "读取错误泄露隐藏来源标识"
        else:
            raise AssertionError("销售履行查询接受了不可读的完整来源")
        assert _contents() == state and _callbacks() == queued and _local_context() == local

    def passed(label):
        checks.append(label)

    _begin_rollback_only(point)
    try:
        frappe.in_test = True
        frappe.set_user("Administrator")
        fixture.warehouse = insert("Warehouse", company=company, warehouse_name=prefix)
        fixture.item = insert("Item", item_code=prefix, item_group=item_group, stock_uom=stock_uom,
            is_stock_item=1, valuation_method="Moving Average", item_defaults=[{
                "company": company, "default_warehouse": fixture.warehouse.name,
                "income_account": fixture.accounts.income, "expense_account": fixture.accounts.expense,
                "buying_cost_center": fixture.cost_center, "selling_cost_center": fixture.cost_center}])
        fixture.customer = insert("Customer", customer_name=prefix, customer_type="Company",
            customer_group=fixture.customer_group, territory=fixture.territory)
        fixture.price_list = insert("Price List", price_list_name=prefix, currency=fixture.currency, selling=1, enabled=1)
        actors = {}
        for label, assigned in ROLES.items():
            user = insert("User", email=prefix.lower() + "-" + label + "@example.invalid",
                          first_name="销售履行验收", send_welcome_email=0, roles=[{"role": role} for role in assigned])
            users.append(user.name)
            actors[label] = user.name
            insert("User Permission", user=user.name, allow="Company",
                   for_value=other_company if label == "other" else company, apply_to_all_doctypes=1)
            frappe.clear_cache(user=user.name)
            roles[label] = sorted(frappe.get_roles(user.name))
            assert "System Manager" not in roles[label]
        frappe.set_user(actors["writer"])
        seed = post(frappe.get_doc({"doctype": "Stock Entry", "company": company,
            "stock_entry_type": "Material Receipt", "posting_date": add_days(today(), -1),
            "posting_time": "06:00:00", "set_posting_time": 1,
            "items": [{"item_code": fixture.item.name, "qty": 30, "basic_rate": 2,
                       "t_warehouse": fixture.warehouse.name, "expense_account": fixture.accounts.expense,
                       "cost_center": fixture.cost_center}]}))
        assert seed.docstatus == 1
        order = post(frappe.get_doc({"doctype": "Sales Order", "company": company,
            "customer": fixture.customer.name, "transaction_date": today(), "delivery_date": add_days(today(), 7),
            "selling_price_list": fixture.price_list.name, "price_list_currency": fixture.currency,
            "currency": fixture.currency, "conversion_rate": 1, "plc_conversion_rate": 1,
            "ignore_pricing_rule": 1, "disable_rounded_total": 1,
            "items": [{"item_code": fixture.item.name, "qty": qty, "rate": rate,
                       "delivery_date": add_days(today(), 7), "warehouse": fixture.warehouse.name, "jf_make_flow": 1}
                      for qty, rate in ((6, 20), (4, 30))]}))
        flows = create_sales_order_flows(order.name)
        for name in flows:
            remember(frappe.get_doc(FLOW, name))
        assert len(flows) == 2
        first, second = (row.name for row in order.items)
        passed("native_same_item_two_so_lines_have_independent_flow_sources_and_real_roles")

        def delivery(child, qty, clock):
            document = make_delivery_note(order.name, kwargs={"filtered_children": [child]})
            assert len(document.items) == 1 and document.items[0].so_detail == child
            document.items[0].qty = qty
            return post(_stamp(document, clock))

        current = "native_batch_delivery_and_invoice"
        deliveries = [delivery(first, 2, "08:00:00"), delivery(first, 1, "08:10:00"), delivery(second, 1, "08:20:00")]
        invoices = []
        for index, source in enumerate(deliveries):
            document = invoice_from_delivery(source.name)
            document.debit_to, document.update_stock = fixture.accounts.receivable, 0
            invoices.append(post(_stamp(document, f"09:{index * 10:02d}:00")))
        direct = make_sales_invoice(order.name)
        direct.items = [row for row in direct.items if row.so_detail == second]
        assert len(direct.items) == 1
        direct.items[0].qty = 1
        direct.debit_to, direct.update_stock = fixture.accounts.receivable, 1
        direct = post(_stamp(direct, "10:00:00"))
        completed_reposts()
        frappe.set_user(actors["reader"])
        baseline = read()
        assert baseline["readonly"] and not baseline["can_write"] and not baseline["quantities_aggregated"]
        assert {row["name"] for row in baseline["items"]} == {first, second}
        assert len(baseline["delivery_notes"]) == 3 and len(baseline["sales_invoices"]) == 4
        assert [row["document"]["name"] for row in baseline["sales_invoices"] if row["document"]["update_stock"]] == [direct.name]
        assert baseline["work_orders"] == []
        assert [row["sales_order"]["name"] for row in read(lambda: query_orders(company, today(), today(), order.name))] == [order.name]
        passed("real_reader_native_batches_dn_si_and_stock_invoice_are_distinct_sources_without_net_totals")

        current = "native_exact_returns_and_receipt"
        frappe.set_user(actors["writer"])
        returned_dn = make_return_doc("Delivery Note", deliveries[2].name)
        returned_dn.items[0].qty = -1
        returned_dn = post(_stamp(returned_dn, "11:00:00"))
        returned_si = make_sales_return(invoices[0].name)
        returned_si.items[0].qty, returned_si.update_stock = -1, 0
        returned_si = post(_stamp(returned_si, "11:10:00"))
        payment = remember(get_payment_entry("Sales Invoice", invoices[1].name, party_amount=5,
                                             bank_account=cash, bank_amount=5))
        payment.posting_date, payment.reference_no, payment.reference_date = today(), prefix, today()
        native(payment, "insert")
        records.add((payment.doctype, payment.name))
        native(payment, "submit")
        completed_reposts()
        frappe.set_user(actors["reader"])
        result = read()
        dn_return = next(row for row in result["delivery_notes"] if row["document"]["name"] == returned_dn.name)
        si_return = next(row for row in result["sales_invoices"] if row["document"]["name"] == returned_si.name)
        assert dn_return["items"][0]["dn_detail"] == deliveries[2].items[0].name
        assert si_return["items"][0]["sales_invoice_item"] == invoices[0].items[0].name
        for source in invoices + [direct, returned_si]:
            actual = frappe.get_doc(source.doctype, source.name)
            projected = next(row["document"] for row in result["sales_invoices"] if row["document"]["name"] == source.name)
            assert flt(projected["outstanding_amount"]) == flt(actual.outstanding_amount) and projected["status"] == actual.status
        assert ("Payment Entry", payment.name) in {(row["doctype"], row["document"]["name"])
                                                     for row in result["receivables"]["voucher_sources"]}
        passed("native_partial_return_exact_original_details_and_payment_ledger_source_with_native_invoice_state")

        current = "native_hidden_source_and_company"
        frappe.set_user(actors["no_flow"])
        assert not frappe.has_permission(FLOW, "read")
        rejected(lambda: read_order(company, order.name), hidden=flows)
        frappe.set_user(actors["other"])
        rejected(lambda: read_order(company, order.name), hidden=flows)
        passed("real_role_and_company_restrictions_deny_complete_view_without_hidden_flow_ids")

        current = "native_flow_user_permission"
        frappe.set_user("Administrator")
        restriction = insert("User Permission", user=actors["reader"], allow=FLOW, for_value=flows[0], apply_to_all_doctypes=1)
        frappe.clear_cache(user=actors["reader"])
        frappe.set_user(actors["reader"])
        rejected(lambda: read_order(company, order.name), hidden=[flows[1]])
        frappe.set_user("Administrator")
        frappe.delete_doc("User Permission", restriction.name)
        frappe.clear_cache(user=actors["reader"])
        passed("native_exact_flow_user_permission_rejects_partial_same_item_order")

        current = "native_cancelled_sources_retained"
        frappe.set_user(actors["writer"])
        native(frappe.get_doc("Payment Entry", payment.name), "cancel")
        native(frappe.get_doc("Sales Invoice", returned_si.name), "cancel")
        native(frappe.get_doc("Delivery Note", returned_dn.name), "cancel")
        completed_reposts()
        frappe.set_user(actors["reader"])
        cancelled = read()
        assert next(row["document"]["docstatus"] for row in cancelled["sales_invoices"]
                    if row["document"]["name"] == returned_si.name) == 2
        assert next(row["document"]["docstatus"] for row in cancelled["delivery_notes"]
                    if row["document"]["name"] == returned_dn.name) == 2
        passed("native_cancelled_return_sources_keep_exact_links_without_rewriting_native_state")
    except Exception as error:
        primary = error
        error.add_note("销售履行原生失败门：" + current)
        error.add_note("已通过门：" + json.dumps(checks, ensure_ascii=False))
        raise
    finally:
        def collect():
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)

        def restore():
            frappe.flags.through_repost_item_valuation, frappe.db.MAX_WRITES_PER_TRANSACTION = original_repost, original_max_writes
            _profile_restore_caller(original_user, original_test, users, records, callbacks, context)

        _profile_audit([
            ("collect_exact_fixture_parents_children", collect),
            ("native_rollback_and_only_added_callbacks", lambda: _profile_rollback(point, callbacks)),
            ("restore_actor_context_flags_and_exact_owned_caches", restore),
            ("all_owned_parent_children_absent", lambda: all(not frappe.db.exists(dt, name) for dt, name in records)),
            ("full_business_children_auth_roles_series_metadata_ledgers_sql_restored", lambda: _contents() == before),
            ("all_tab_table_counts_restored", lambda: _tab_counts(tables)[1] == counts),
            ("whole_gl_sle_restored", lambda: _ledger_snapshot() == ledger_before),
            ("caller_callbacks_and_local_context_restored", lambda: _callbacks() == callbacks and _local_context() == context),
        ], primary=primary)
    checks.append("full_native_sql_auth_series_children_counts_ledgers_context_rollback")
    return {"passed": True, "gate_count": len(checks), "checks": checks, "business_data": "rolled_back",
            "actual_roles": roles, "contents_before": before, "contents_after": _contents(),
            "table_counts_before": counts, "table_counts_after": _tab_counts(tables)[1],
            "gui_verified": False, "concurrency_verified": False,
            "remaining": ["旧销售跟踪操作/UI", "原生工单制造入库链", "原生字段read/mask撤权",
                          "Company仅select的真实岗位", "多币种专项", "真实重过账worker", "双连接并发来源或权限变化"]}
