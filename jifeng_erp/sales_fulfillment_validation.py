"""Serial rollback-only native sales source read experiment.

Run once through direct Python in an exclusive business-write window, never
``bench execute``. Synthetic users retain actual installed roles and scoped
User Permissions. No DocPerm grants, ignore_permissions, force, commits, GUI,
schema changes or direct ledger edits are used. Native in_test mode only permits the
installed synchronous stock repost path; this is not a background-worker test.

Draft PE/JE fixtures prove precise reverse-reference discovery without PLE/GL;
they do not prove a submitted voucher with temporarily missing ledger rows.
Repost state fixtures use normal insert followed by writes to only that job's
protocol status/docstatus and child docstatus. They never submit or run a worker.
"""

import json

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.catalog_validation import _profile_audit, _profile_restore_caller, _profile_rollback
from jifeng_erp.diagnostics import _begin_rollback_only
from jifeng_erp.fulfillment_validation import _preflight, _stamp
from jifeng_erp.inventory_validation import _finish_fixture_reposts, _ledger_snapshot
from jifeng_erp.personnel_changes_validation import _callbacks, _tab_counts
from jifeng_erp.personnel_separation_validation import _local_context, _restore_local_context
from jifeng_erp.product_dictionary_validation import _native_contents, _sql_row_hash
from jifeng_erp.sales_fulfillment_workbench import FLOW, PARENTS, TABLES, query_orders, read_order


ROLES = {
    "writer": ("Sales User", "Stock Manager", "Accounts Manager", "Purchase User", "Manufacturing User"),
    "reader": ("Sales User", "Accounts User", "Stock User", "Manufacturing User"),
    "no_flow": ("Sales User", "Accounts User", "Stock User"),
    "other": ("Sales User", "Accounts User", "Stock User", "Manufacturing User"),
}


def _repost_protocol_state(insert, voucher, status):
    """Persist an explicit RPL read-gate fixture; never submit/enqueue/repost."""
    assert voucher.doctype in {"Payment Entry", "Journal Entry"} and voucher.name
    assert status in {"Queued", "Failed", "Completed"}
    job = insert("Repost Payment Ledger", company=voucher.company, posting_date=voucher.posting_date,
                 add_manually=1, repost_vouchers=[{"voucher_type": voucher.doctype, "voucher_no": voucher.name}])
    assert job.docstatus == 0, "重过账状态验收只能从原生草稿开始"
    frappe.db.set_value(job.doctype, job.name, {"docstatus": 1, "repost_status": status})
    for row in job.repost_vouchers:
        frappe.db.set_value(row.doctype, row.name, "docstatus", 1)
    return job


def _assert_native_manufacturing_sources(result, order, raw_item, sources):
    """Require every owned WO/batch and only its exact finished source rows."""
    details = {row.name: row for row in order.items}
    expected = {work.name: (flow, work, entries) for flow, work, entries in sources}
    assert len(expected) == len(sources) == len(details), "同商品订单行必须分别保留流程单和生产工单"
    assert {work.sales_order_item for _, work, _ in sources} == set(details)
    actual = {row["document"]["name"]: row for row in result["work_orders"]}
    assert len(actual) == len(result["work_orders"]) and set(actual) == set(expected), "制造来源缺少或混入工单"
    for name, (flow, work, entries) in expected.items():
        detail = details[work.sales_order_item]
        assert (flow.company, flow.sales_order, flow.sales_order_item, flow.item_code) == (
            order.company, order.name, detail.name, detail.item_code)
        assert flow.work_order == work.name and flt(flow.planned_qty) == flt(detail.qty)
        assert (work.company, work.sales_order, work.production_item, work.jf_flow_order) == (
            order.company, order.name, detail.item_code, flow.name)
        assert work.docstatus == 1 and not work.operations, "本夹具须用已提交的无工序原生工单"
        assert flt(work.qty) == flt(detail.qty) and flt(work.produced_qty) == len(entries)
        projected = actual[name]["document"]
        for field in ("name", "company", "sales_order", "sales_order_item", "production_item", "jf_flow_order",
                      "docstatus", "status", "stock_uom"):
            assert projected[field] == work.get(field), "工单投影与准确原生来源不同"
        for field in ("qty", "produced_qty", "process_loss_qty"):
            assert flt(projected[field]) == flt(work.get(field)), "工单数量不能替代逐批成品来源"
        batches = actual[name]["manufacture_entries"]
        projected_batches = {row["document"]["name"]: row for row in batches}
        assert len(projected_batches) == len(batches) == len(entries)
        assert set(projected_batches) == {entry.name for entry in entries}, "制造批次不能遗漏或串到另一订单行"
        for entry in entries:
            assert (entry.company, entry.work_order, entry.purpose, entry.docstatus) == (
                order.company, work.name, "Manufacture", 1)
            source_finished = [row for row in entry.items if row.is_finished_item]
            source_raw = [row for row in entry.items if not row.is_finished_item]
            assert len(source_finished) == len(source_raw) == 1
            assert source_raw[0].item_code == raw_item and source_raw[0].jf_flow_order == flow.name
            assert source_finished[0].item_code == detail.item_code
            assert source_finished[0].jf_flow_order == flow.name and flt(source_finished[0].transfer_qty) == 1
            batch = projected_batches[entry.name]
            for field in ("name", "company", "work_order", "purpose", "docstatus"):
                assert batch["document"][field] == entry.get(field)
            assert flt(batch["document"]["fg_completed_qty"]) == flt(entry.fg_completed_qty) == 1
            assert len(batch["finished_items"]) == 1, "原料消耗行不能当作成品入库行"
            finished = batch["finished_items"][0]
            for field in ("name", "parent", "parenttype", "parentfield", "item_code", "is_finished_item", "jf_flow_order",
                          "uom", "stock_uom"):
                assert finished[field] == source_finished[0].get(field), "成品明细必须保留准确父项、商品与流程单"
            for field in ("qty", "transfer_qty"):
                assert flt(finished[field]) == flt(source_finished[0].get(field)) == 1


def _contents():
    result = _native_contents()
    doctypes = set(PARENTS) | {value[1] for value in TABLES.values()} | {
        "Company", "Warehouse", "Price List", "Customer", "Bin", "Repost Item Valuation",
        "Repost Payment Ledger", "Repost Accounting Ledger", "Stock Ledger Entry", "GL Entry",
        "DocType", "DocPerm", "Custom DocPerm", "Custom Field", "Property Setter", "Error Log",
        "Communication", "Email Queue", "Notification Log", "Activity Log", "BOM", "Job Card",
        "Stock Reservation Entry",
    }
    doctypes = {doctype for doctype in doctypes if frappe.db.exists("DocType", doctype)}
    for doctype in tuple(doctypes):
        doctypes.update(field.options for field in frappe.get_meta(doctype).get_table_fields())
    result.update({doctype: _sql_row_hash(frappe.db.sql("SELECT * FROM `tab" + doctype.replace("`", "``")
        + "` ORDER BY name", as_dict=True)) for doctype in sorted(doctypes)})
    result["Singles"] = _sql_row_hash(frappe.db.sql("SELECT * FROM `tabSingles` ORDER BY doctype, field", as_dict=True))
    return result


def smoke(company, other_company, item_group, stock_uom):
    """Same-item rows, manufacture/DN/SI batches, returns, payments and isolation."""
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
    from erpnext.controllers.sales_and_purchase_return import make_return_doc
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as invoice_from_delivery

    from jifeng_erp.workflow import create_sales_order_flows

    frappe.only_for("System Manager")
    assert other_company and other_company != company and frappe.db.exists("Company", other_company)
    fixture = _preflight(company, item_group, stock_uom)
    cash = frappe.db.get_value("Company", company, "default_cash_account") or frappe.db.get_value(
        "Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name")
    assert cash, "销售履行收款验收需要既有本公司现金科目"
    # Pick an empty historical SO date before inserting anything. Batch queries
    # then contain only the two exact owned roots, without querying private
    # existing orders or relying on today's population/naming-series prefix.
    batch_date = next((add_days(today(), -offset) for offset in range(1, 367)
        if not frappe.db.exists("Sales Order", {"company": company, "transaction_date": add_days(today(), -offset)})), None)
    assert batch_date, "跨客户批次验收需要一年内没有既有销售订单的日期"
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
        _finish_fixture_reposts(company, {fixture.item.name, fixture.raw.name}, {fixture.warehouse.name}, vouchers)

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

    def nested_gate(label, setup, action=None, errors=frappe.PermissionError, hidden=()):
        nonlocal current
        current = label
        saved, saved_callbacks, saved_context = _contents(), _callbacks(), _local_context()
        nested, nested_primary = "jf_sales_follow_gate_" + frappe.generate_hash(length=8), None
        _begin_rollback_only(nested)
        try:
            frappe.set_user("Administrator")
            setup()
            frappe.set_user(actors["reader"])
            if errors is None:
                read(action)
            else:
                state, queued, local = _contents(), _callbacks(), _local_context()
                try:
                    (action or (lambda: read_order(company, order.name)))()
                except errors as error:
                    assert all(name not in str(error) for name in hidden), "读取错误泄露隐藏来源标识"
                else:
                    raise AssertionError("完整销售来源或重过账状态门接受了应拒绝的查询")
                assert _contents() == state and _callbacks() == queued and _local_context() == local
        except Exception as error:
            nested_primary = error
            raise
        finally:
            def restore_nested():
                frappe.clear_cache(user=actors["reader"])
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
                frappe.set_user(actors["reader"])
                _restore_local_context(saved_context)
            _profile_audit([
                ("nested_database_and_callbacks_rollback", lambda: _profile_rollback(nested, saved_callbacks)),
                ("nested_owned_caches_actor_context_restore", restore_nested),
                ("nested_complete_sql_restored", lambda: _contents() == saved),
                ("nested_callbacks_context_restored", lambda: _callbacks() == saved_callbacks and _local_context() == saved_context),
            ], primary=nested_primary)
        passed(label)

    def restriction(doctype, allowed_name):
        insert("User Permission", user=actors["reader"], allow=doctype, for_value=allowed_name,
               apply_to_all_doctypes=0, applicable_for=doctype)
        frappe.clear_cache(user=actors["reader"])

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
        fixture.raw = insert("Item", item_code=prefix + "-raw", item_group=item_group, stock_uom=stock_uom,
            is_stock_item=1, valuation_method="Moving Average", item_defaults=[{
                "company": company, "default_warehouse": fixture.warehouse.name,
                "expense_account": fixture.accounts.expense, "buying_cost_center": fixture.cost_center}])
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
            "items": [{"item_code": item.name, "qty": qty, "basic_rate": 2,
                       "t_warehouse": fixture.warehouse.name, "expense_account": fixture.accounts.expense,
                       "cost_center": fixture.cost_center} for item, qty in ((fixture.item, 30), (fixture.raw, 10))]}))
        assert seed.docstatus == 1 and len(seed.items) == 2
        order = post(frappe.get_doc({"doctype": "Sales Order", "company": company,
            "customer": fixture.customer.name, "transaction_date": batch_date, "delivery_date": add_days(today(), 7),
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
        assert [row["sales_order"]["name"] for row in read(lambda: query_orders(company, batch_date, batch_date, order.name))] == [order.name]
        passed("real_reader_native_batches_dn_si_and_stock_invoice_are_distinct_sources_without_net_totals")

        current = "native_exact_work_order_and_manufacture_batches"
        frappe.set_user(actors["writer"])
        bom = post(frappe.get_doc({"doctype": "BOM", "company": company, "item": fixture.item.name,
            "quantity": 1, "is_default": 0, "with_operations": 0, "currency": fixture.currency,
            "items": [{"item_code": fixture.raw.name, "qty": 1, "rate": 2}]}))
        assert bom.docstatus == 1 and not bom.operations
        manufacturing_sources = []
        for detail in order.items:
            flow = remember(frappe.get_doc(FLOW, next(name for name in flows
                if frappe.db.get_value(FLOW, name, "sales_order_item") == detail.name)))
            flow.bom = bom.name
            flow.materials = []
            flow.append("materials", {"item_code": fixture.raw.name, "uom": stock_uom,
                                      "qty_per_unit": 1, "unit_price": 2})
            native(flow, "save")
            work = remember(frappe.get_doc("Work Order", flow.make_work_order()))
            records.add((work.doctype, work.name))
            work.source_warehouse, work.fg_warehouse = fixture.warehouse.name, fixture.warehouse.name
            work.skip_transfer, work.planned_start_date = 1, today() + " 06:00:00"
            native(work, "save")
            native(work, "submit")
            work.reload()
            assert work.docstatus == 1 and not work.operations and flt(work.produced_qty) == 0
            assert work.sales_order_item == detail.name and flt(work.qty) == flt(detail.qty)
            manufacturing_sources.append((flow, work, []))
        # Every stock row is generated from this fixture's native receipt/BOM;
        # no existing user stock, job-card quantity or direct ledger writes.
        assert flt(frappe.db.get_value("Bin", {"item_code": fixture.item.name,
            "warehouse": fixture.warehouse.name}, "actual_qty")) == 25
        assert flt(frappe.db.get_value("Bin", {"item_code": fixture.raw.name,
            "warehouse": fixture.warehouse.name}, "actual_qty")) == 10
        for index, (flow, work, entries) in enumerate(manufacturing_sources):
            for batch in range(2 if work.sales_order_item == first else 1):
                entry = post(_stamp(frappe.get_doc(make_stock_entry(work.name, "Manufacture", 1)),
                                    f"07:{index * 20 + batch * 10:02d}:00"))
                entries.append(entry)
                assert len(entry.items) == 2 and all(row.jf_flow_order == flow.name for row in entry.items)
                finished = next(row for row in entry.items if row.is_finished_item)
                raw = next(row for row in entry.items if not row.is_finished_item)
                ledger = frappe.get_all("Stock Ledger Entry", filters={"voucher_type": "Stock Entry",
                    "voucher_no": entry.name, "is_cancelled": 0},
                    fields=["voucher_detail_no", "item_code", "warehouse", "actual_qty"])
                assert len(ledger) == 2 and {(row.voucher_detail_no, row.item_code, row.warehouse, flt(row.actual_qty))
                    for row in ledger} == {(finished.name, fixture.item.name, fixture.warehouse.name, 1),
                                          (raw.name, fixture.raw.name, fixture.warehouse.name, -1)}
            work.reload()
            assert flt(work.produced_qty) == len(entries) and flt(work.process_loss_qty) == 0
        completed_reposts()
        assert flt(frappe.db.get_value("Bin", {"item_code": fixture.item.name,
            "warehouse": fixture.warehouse.name}, "actual_qty")) == 28
        assert flt(frappe.db.get_value("Bin", {"item_code": fixture.raw.name,
            "warehouse": fixture.warehouse.name}, "actual_qty")) == 7
        frappe.set_user(actors["reader"])
        manufactured = read()
        _assert_native_manufacturing_sources(manufactured, order, fixture.raw.name, manufacturing_sources)
        passed("native_same_item_two_exact_so_flows_work_orders_three_batches_preserve_finished_sources_and_real_stock")
        nested_gate("native_hidden_work_order_rejects_same_item_other_so_line_complete_view",
                    lambda: restriction("Work Order", manufacturing_sources[0][1].name),
                    hidden=(manufacturing_sources[1][1].name,))
        nested_gate("native_hidden_manufacture_entry_rejects_complete_view",
                    lambda: restriction("Stock Entry", seed.name),
                    hidden=tuple(entry.name for _, _, entries in manufacturing_sources for entry in entries))

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

        current = "native_draft_pe_je_precise_references_without_ledger"
        frappe.set_user(actors["writer"])
        draft_ledgers = _ledger_snapshot()
        draft_ple = _sql_row_hash(frappe.db.sql("SELECT * FROM `tabPayment Ledger Entry` ORDER BY name", as_dict=True))
        native_outstanding = frappe.db.get_value("Sales Invoice", invoices[2].name, "outstanding_amount")
        draft_pe = remember(get_payment_entry("Sales Invoice", invoices[2].name, party_amount=2,
                                              bank_account=cash, bank_amount=2))
        draft_pe.posting_date, draft_pe.reference_no, draft_pe.reference_date = today(), prefix + "-draft", today()
        native(draft_pe, "insert")
        records.add((draft_pe.doctype, draft_pe.name))
        draft_je = insert("Journal Entry", company=company, posting_date=today(), voucher_type="Journal Entry",
            accounts=[{"account": cash, "debit_in_account_currency": 2, "cost_center": fixture.cost_center},
                      {"account": fixture.accounts.receivable, "credit_in_account_currency": 2,
                       "party_type": "Customer", "party": fixture.customer.name, "reference_type": "Sales Invoice",
                       "reference_name": invoices[2].name, "cost_center": fixture.cost_center}])
        # A separate unreferenced draft is only the exact allow-list value used
        # to hide draft_je via normal User Permission, without new role grants.
        control_je = insert("Journal Entry", company=company, posting_date=today(), voucher_type="Journal Entry",
            accounts=[{"account": fixture.accounts.expense, "debit_in_account_currency": 1,
                       "cost_center": fixture.cost_center},
                      {"account": cash, "credit_in_account_currency": 1, "cost_center": fixture.cost_center}])
        for document in (draft_pe, draft_je):
            assert document.docstatus == 0
            assert not frappe.db.exists("Payment Ledger Entry", {"voucher_type": document.doctype, "voucher_no": document.name})
            assert not frappe.db.exists("Payment Ledger Entry", {"against_voucher_type": document.doctype, "against_voucher_no": document.name})
        assert _ledger_snapshot() == draft_ledgers
        assert _sql_row_hash(frappe.db.sql("SELECT * FROM `tabPayment Ledger Entry` ORDER BY name", as_dict=True)) == draft_ple
        assert frappe.db.get_value("Sales Invoice", invoices[2].name, "outstanding_amount") == native_outstanding
        frappe.set_user(actors["reader"])
        drafts = read()
        sources = {(row["doctype"], row["document"]["name"]): row["document"]
                   for row in drafts["receivables"]["voucher_sources"]}
        assert all(sources[document.doctype, document.name]["docstatus"] == 0 for document in (draft_pe, draft_je))
        assert (control_je.doctype, control_je.name) not in sources
        passed("native_draft_pe_je_without_ple_gl_are_exact_reverse_sources_and_do_not_change_invoice_outstanding")
        nested_gate("native_hidden_draft_pe_reverse_reference_rejects_complete_view",
                    lambda: restriction("Payment Entry", payment.name), hidden=(draft_pe.name,))
        nested_gate("native_hidden_draft_je_reverse_reference_rejects_complete_view",
                    lambda: restriction("Journal Entry", control_je.name), hidden=(draft_je.name,))
        for voucher in (draft_pe, draft_je):
            for status in ("Queued", "Failed", "Completed"):
                nested_gate("persisted_rpl_protocol_" + voucher.doctype + "_draft_no_ple_" + status,
                    lambda doc=voucher, value=status: _repost_protocol_state(insert, doc, value),
                    errors=None if status == "Completed" else frappe.ValidationError)

        current = "native_two_customers_share_item_and_receivable_account"
        frappe.set_user("Administrator")
        second_customer = insert("Customer", customer_name=prefix + "-second-customer", customer_type="Company",
            customer_group=fixture.customer_group, territory=fixture.territory,
            accounts=[{"company": company, "account": fixture.accounts.receivable}])
        frappe.set_user(actors["writer"])
        second_order = post(frappe.get_doc({"doctype": "Sales Order", "company": company,
            "customer": second_customer.name, "transaction_date": batch_date, "delivery_date": add_days(today(), 7),
            "selling_price_list": fixture.price_list.name, "price_list_currency": fixture.currency,
            "currency": fixture.currency, "conversion_rate": 1, "plc_conversion_rate": 1,
            "ignore_pricing_rule": 1, "disable_rounded_total": 1,
            "items": [{"item_code": fixture.item.name, "qty": 2, "rate": 17, "warehouse": fixture.warehouse.name,
                       "delivery_date": add_days(today(), 7), "jf_make_flow": 0}]}))
        second_invoice = make_sales_invoice(second_order.name)
        second_invoice.debit_to, second_invoice.update_stock = fixture.accounts.receivable, 0
        second_invoice = post(_stamp(second_invoice, "11:20:00"))
        second_payment = get_payment_entry("Sales Invoice", second_invoice.name, party_amount=4,
                                          bank_account=cash, bank_amount=4)
        second_payment.posting_date, second_payment.reference_no, second_payment.reference_date = today(), prefix + "-second", today()
        second_payment = post(second_payment)
        completed_reposts()
        frappe.set_user(actors["reader"])
        batch = read(lambda: query_orders(company, batch_date, batch_date))
        roots = {row["sales_order"]["name"]: row for row in batch}
        assert set(roots) == {order.name, second_order.name}, "批次只能包含两个预先登记的验收订单"
        for source_order, customer, invoice in ((order, fixture.customer, invoices[2]),
                                               (second_order, second_customer, second_invoice)):
            root = roots[source_order.name]
            assert root["sales_order"]["customer"] == customer.name
            assert {row["item_code"] for row in root["items"]} == {fixture.item.name}
            assert all(row["document"]["customer"] == customer.name and row["document"]["debit_to"] == fixture.accounts.receivable
                       for row in root["sales_invoices"])
            assert root["receivables"]["ledger_entries"]
            assert all(row["party"] == customer.name and row["account"] == fixture.accounts.receivable
                       for row in root["receivables"]["ledger_entries"])
            projected = next(row["document"] for row in root["sales_invoices"] if row["document"]["name"] == invoice.name)
            assert flt(projected["outstanding_amount"]) == flt(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount"))
        assert ("Payment Entry", second_payment.name) not in {(row["doctype"], row["document"]["name"])
            for row in roots[order.name]["receivables"]["voucher_sources"]}
        assert ("Payment Entry", payment.name) not in {(row["doctype"], row["document"]["name"])
            for row in roots[second_order.name]["receivables"]["voucher_sources"]}
        passed("native_two_customer_batch_preserves_independent_ple_and_invoice_state_with_shared_item_account")
        nested_gate("native_customer_user_permission_denies_exact_hidden_root",
                    lambda: restriction("Customer", fixture.customer.name),
                    action=lambda: read_order(company, second_order.name), hidden=(second_customer.name,))

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
            "no_ple_vouchers": "normal native draft PE/JE; not submitted rebuilds",
            "manufacturing_verification": "native no-operation BOM, two exact SO-line WOs, three Manufacture batches",
            "repost_verification": "persisted RPL protocol fixtures only; no submit/enqueue/worker",
            "remaining": ["旧销售跟踪操作/UI", "独立无Work Order制造入库来源", "原生字段read/mask撤权",
                          "含工序或取消的制造来源",
                          "Company仅select的真实岗位", "多币种专项", "真实重过账worker",
                          "Repost Accounting Ledger 原生状态专项", "已提交PE/JE重建期间暂缺PLE",
                          "双连接并发来源或权限变化"]}
