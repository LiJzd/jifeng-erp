"""Rollback-only native SO -> DN -> SI provenance acceptance.

Run once in an exclusive business-write window, after migrating the source
fields/hooks. Do not use ``bench execute``: its exception fallback can evaluate
the function a second time. The direct Python contract is one invocation::

    cd /home/jifengtest/jifeng-dev/bench
    ./env/bin/python - <<'PY'
    import json
    import frappe
    from jifeng_erp.fulfillment_validation import smoke
    frappe.init(site="jifeng.localhost", sites_path="sites")
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        result = smoke(company="Jifeng ERP UI Validation",
                       item_group="Products", stock_uom="Nos")
        print(json.dumps(result, ensure_ascii=False, default=str))
    finally:
        frappe.destroy()
    PY

Failure propagates after the same rollback/audit as success. This helper never
commits, migrates, changes DocPerm, enables ignore_permissions/force, changes
selling settings, or writes a stock/accounting ledger directly. Standard role
assignments and User Permissions belong only to the synthetic fixture users.
"""

from collections import Counter

import frappe
from frappe.utils import add_days, flt, today

from jifeng_erp.diagnostics import _begin_rollback_only, _end_rollback_only
from jifeng_erp.inventory_validation import _finish_fixture_reposts, _ledger_snapshot, _stock


FLOW = "Jifeng Flow Order"
ROLES = {
    "traced": ("Sales User", "Stock Manager", "Accounts Manager"),
    "sales": ("Sales User",),
    "accounts": ("Accounts User",),
    # Native Delivery User reads DN; Projects User selects SO without reading
    # it. Accounts Manager creates/submits SI, Accounts User reads its Item.
    # Native Manager roles do not implicitly include their User counterparts.
    # No role here reads a Flow or the full SO.
    "selector": ("Accounts Manager", "Accounts User", "Projects User", "Delivery User"),
}
AUDIT_TYPES = (
    "Item", "Item Default", "UOM Conversion Detail", "Warehouse", "Customer",
    "Price List", "User", "Has Role", "User Permission", "DocShare", "Version",
    "Sales Order", "Sales Order Item", "Delivery Note", "Delivery Note Item",
    "Sales Invoice", "Sales Invoice Item", "Sales Taxes and Charges", "Sales Team",
    FLOW, "Jifeng Material Line", "Jifeng Operation Line", "Stock Entry",
    "Stock Entry Detail", "Bin", "Repost Item Valuation", "File",
    "Payment Ledger Entry", "GL Entry", "Stock Ledger Entry", "Communication",
    "Email Queue", "Notification Log",
)


def _counts():
    return {doctype: frappe.db.count(doctype) for doctype in AUDIT_TYPES if frappe.db.table_exists(doctype)}


def _preflight(company, item_group, stock_uom):
    for doctype, name in (("Company", company), ("Item Group", item_group), ("UOM", stock_uom)):
        if not frappe.db.exists(doctype, name):
            frappe.throw("发货追溯验收缺少指定公司、商品分组或库存单位")
    for doctype in ("Delivery Note Item", "Sales Invoice Item"):
        field = frappe.get_meta(doctype).get_field("jf_flow_order")
        if not (field and field.options == FLOW and field.read_only and field.no_copy):
            frappe.throw("请先迁移发货、发票的只读 no_copy 流程来源字段")
    events = frappe.get_hooks("doc_events")
    for doctype in ("Delivery Note", "Sales Invoice"):
        hooks = events.get(doctype, {}).get("before_validate", [])
        hooks = [hooks] if isinstance(hooks, str) else hooks
        if "jifeng_erp.fulfillment.validate_fulfillment_flow" not in hooks:
            frappe.throw("发货、发票的流程来源钩子尚未安装")
    if frappe.flags.dont_execute_stock_reposts or frappe.flags.through_repost_item_valuation:
        frappe.throw("验收须由独立进程执行，不能跳过或嵌套原生库存重算")
    other = frappe.db.get_value("Company", {"name": ["!=", company]}, "name")
    if not other:
        frappe.throw("错公司来源验收需要另一已存在公司；本 helper 不创建公司或科目表")
    company_doc = frappe.get_doc("Company", company)
    accounts = {}
    for key, field, condition in (
        ("income", "default_income_account", {"root_type": "Income"}),
        ("expense", "default_expense_account", {"account_type": "Cost of Goods Sold"}),
        ("receivable", "default_receivable_account", {"account_type": "Receivable"}),
    ):
        accounts[key] = company_doc.get(field) or frappe.db.get_value(
            "Account", {"company": company, "is_group": 0, **condition}, "name")
    cost_center = company_doc.cost_center or frappe.db.get_value(
        "Cost Center", {"company": company, "is_group": 0}, "name")
    group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if not all(accounts.values()) or not cost_center or not group or not territory or not company_doc.default_currency:
        frappe.throw("验收需要本币、收入/销售成本/应收科目、明细成本中心、客户分组和地区")
    return frappe._dict(company=company, other_company=other, currency=company_doc.default_currency,
                        accounts=frappe._dict(accounts), cost_center=cost_center,
                        customer_group=group, territory=territory)


def _stamp(document, clock):
    document.posting_date = today()
    document.posting_time = clock
    document.set_posting_time = 1
    document.ignore_pricing_rule = 1
    document.disable_rounded_total = 1
    return document


def _voucher_entries(doctype, document):
    return frappe.get_all(doctype, filters={"voucher_type": document.doctype, "voucher_no": document.name},
                          fields=["*"])


def _assert_invoice(invoice, original, flow, qty):
    row = invoice.items[0]
    assert invoice.docstatus == 1 and invoice.update_stock == 0
    assert len(invoice.items) == 1 and (row.jf_flow_order or None) == flow
    assert row.dn_detail == original.items[0].name and row.delivery_note == original.name
    assert row.so_detail == original.items[0].so_detail
    assert row.sales_order == original.items[0].against_sales_order
    assert flt(row.qty) == qty and flt(row.rate) == flt(original.items[0].rate)
    assert not _voucher_entries("Stock Ledger Entry", invoice), "从 DN 开 SI 不应重复过账库存"
    ledger = _voucher_entries("GL Entry", invoice)
    assert ledger and round(sum(flt(row.debit) - flt(row.credit) for row in ledger), 6) == 0


def smoke(company, item_group, stock_uom):
    """Exercise native roles, exact child chains, returns/cancels, then roll back.

    Administrator/System Manager access authorizes the experiment, not the
    business gates: positive transactions execute as actual synthetic users.
    All negative gates require the intended exception/message, not any native
    failure. Nothing in this function is an automatic retry.
    """
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
    from erpnext.controllers.sales_and_purchase_return import make_return_doc
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

    from jifeng_erp.workflow import create_sales_order_flows

    frappe.only_for("System Manager")
    fixture = _preflight(company, item_group, stock_uom)
    original_user, original_test = frappe.session.user, frappe.in_test
    original_repost = frappe.flags.through_repost_item_valuation
    original_max_writes = frappe.db.MAX_WRITES_PER_TRANSACTION
    before, before_counts = _ledger_snapshot(), _counts()
    point = "jf_fulfill_" + frappe.generate_hash(length=10)
    prefix = "JF-QA-FULFILL-" + frappe.generate_hash(length=10)
    objects, records, users, checks, evidence = [], set(), [], [], {}
    invoices, deliveries, orders, vouchers = [], [], [], set()
    current_gate = "begin"
    _begin_rollback_only(point)

    def gate(label):
        nonlocal current_gate
        current_gate = label

    def passed(label=None):
        checks.append(label or current_gate)

    def remember(document):
        objects.append(document)
        if document.name:
            records.add((document.doctype, document.name))
        return document

    def insert(doctype, **fields):
        document = remember(frappe.get_doc({"doctype": doctype, **fields}))
        document.insert()
        records.add((document.doctype, document.name))
        return document

    def native_action(document, action):
        # Native synchronous repost sets a process flag and raises the write
        # limit. Preserve its caller's state after the complete native action,
        # so the next ordinary voucher is not accidentally treated as a repost.
        repost_flag = frappe.flags.through_repost_item_valuation
        write_limit = frappe.db.MAX_WRITES_PER_TRANSACTION
        try:
            return getattr(document, action)()
        finally:
            frappe.flags.through_repost_item_valuation = repost_flag
            frappe.db.MAX_WRITES_PER_TRANSACTION = write_limit

    def post(document, kind):
        # Match savedocs: a mapper mutates an initially blank controller, while
        # Desk sends its final values back as a new document dictionary. Native
        # DN.__init__ builds its return status_updater from is_return, so this
        # reconstruction is required before inserting a mapped return.
        document = frappe.get_doc(document.as_dict())
        remember(document)
        document.insert()
        records.add((document.doctype, document.name))
        native_action(document, "submit")
        document.reload()
        kind.append(document)
        vouchers.add((document.doctype, document.name))
        return document

    def reject(label, operation, error, text, hidden=()):
        gate(label)
        nested = "jf_fulfill_denied_" + frappe.generate_hash(length=8)
        ledger = _ledger_snapshot()
        frappe.db.savepoint(nested)
        try:
            try:
                operation()
            except error as exception:
                message = str(exception)
                assert text in message, (label, "wrong rejection", message)
                assert all(name not in message for name in hidden), "错误泄露了不可读流程单编号"
            else:
                raise AssertionError(label + " did not reject")
        finally:
            frappe.db.rollback(save_point=nested)
        assert _ledger_snapshot() == ledger, label + " changed GL/SLE"
        passed()

    def order(rows):
        return post(frappe.get_doc({"doctype": "Sales Order", "company": company,
            "customer": fixture.customer.name, "delivery_date": add_days(today(), 7),
            "selling_price_list": fixture.price_list.name, "price_list_currency": fixture.currency,
            "currency": fixture.currency, "conversion_rate": 1, "plc_conversion_rate": 1,
            "ignore_pricing_rule": 1, "disable_rounded_total": 1,
            "items": [{"item_code": fixture.item.name, "qty": qty, "rate": rate,
                       "delivery_date": add_days(today(), 7), "warehouse": fixture.warehouse.name,
                       "jf_make_flow": int(traced), "jf_requirements": prefix + "要求"}
                      for qty, rate, traced in rows]}), orders)

    def dn(source, child, qty, clock="08:00:00"):
        document = make_delivery_note(source.name, kwargs={"filtered_children": [child]})
        assert len(document.items) == 1 and document.items[0].so_detail == child
        # SO Item has no Flow field to copy. Native new_doc can still populate
        # a Link from a sole User Permission, including readonly/no_copy Links.
        # Preserve that actual input: the source guard must reject a hidden
        # ancestor rather than silently replacing an unrelated default.
        mapped_flow = document.items[0].jf_flow_order
        if mapped_flow:
            assert frappe.db.exists("User Permission", {
                "user": frappe.session.user, "allow": FLOW, "for_value": mapped_flow,
            }), "映射草稿的流程默认值不是本次原生用户权限默认值"
            evidence.setdefault("native_mapped_link_defaults", []).append({
                "source": source.name, "source_child": child, "default_flow": mapped_flow,
            })
        document.flags.qa_native_mapped_qty = flt(document.items[0].qty)
        document.items[0].qty = qty
        return _stamp(document, clock)

    def si(source, clock="10:00:00"):
        document = make_sales_invoice(source.name)
        assert not document.items[0].jf_flow_order, "no_copy 字段不能由 mapper 直接复制"
        document.debit_to = fixture.accounts.receivable
        document.update_stock = 0
        return _stamp(document, clock)

    def returned(source, qty=1, clock="12:00:00"):
        document = make_sales_return(source.name) if source.doctype == "Sales Invoice" else make_return_doc(
            "Delivery Note", source.name)
        assert len(document.items) == 1 and not document.items[0].jf_flow_order
        document.items[0].qty = -qty
        document.items[0].warehouse = fixture.warehouse.name
        if document.doctype == "Sales Invoice":
            document.update_stock = 0
        return _stamp(document, clock)

    def changed(document, **fields):
        document.items[0].update(fields)
        return remember(document).insert()

    def balance(qty, value):
        _stock(fixture.item.name, fixture.warehouse.name, today(), qty, value)
        bin_qty = frappe.db.get_value("Bin", {
            "item_code": fixture.item.name, "warehouse": fixture.warehouse.name}, "actual_qty")
        assert flt(bin_qty) == qty, "原生 Bin 实存量与 SLE 不一致"

    try:
        frappe.in_test = True  # Native Repost Item Valuation.on_submit executes synchronously.
        frappe.set_user("Administrator")
        gate("fixture_masters_and_native_role_permissions")
        fixture.warehouse = insert("Warehouse", company=company, warehouse_name=prefix)
        fixture.item = insert("Item", item_code=prefix, item_group=item_group, stock_uom=stock_uom,
            is_stock_item=1, valuation_method="Moving Average", item_defaults=[{
                "company": company, "default_warehouse": fixture.warehouse.name,
                "income_account": fixture.accounts.income, "expense_account": fixture.accounts.expense,
                "buying_cost_center": fixture.cost_center, "selling_cost_center": fixture.cost_center}])
        fixture.wrong_item = insert("Item", item_code=prefix + "-WRONG", item_group=item_group,
                                    stock_uom=stock_uom, is_stock_item=1)
        fixture.customer = insert("Customer", customer_name=prefix, customer_type="Company",
                                  customer_group=fixture.customer_group, territory=fixture.territory)
        fixture.price_list = insert("Price List", price_list_name=prefix, currency=fixture.currency,
                                    selling=1, enabled=1)
        fixture.actors = {}
        for kind, roles in ROLES.items():
            user = insert("User", email=f"{prefix.lower()}-{kind}@example.invalid", first_name="发货原生验收",
                          send_welcome_email=0, roles=[{"role": role} for role in roles])
            fixture.actors[kind] = user.name
            users.append(user.name)
            insert("User Permission", user=user.name, allow="Company", for_value=company, apply_to_all_doctypes=1)
        for kind in ("sales", "accounts", "selector"):
            frappe.set_user(fixture.actors[kind])
            assert not frappe.has_permission(FLOW, "read") and not frappe.has_permission(FLOW, "create")
        frappe.set_user(fixture.actors["traced"])
        for doctype in ("Sales Order", "Delivery Note", "Sales Invoice"):
            assert all(frappe.has_permission(doctype, permission) for permission in ("read", "create", "submit"))
        assert frappe.has_permission(FLOW, "read") and frappe.has_permission(FLOW, "create")
        evidence["native_roles"] = {kind: list(roles) for kind, roles in ROLES.items()}
        passed()

        gate("native_stock_seed")
        seed = post(frappe.get_doc({"doctype": "Stock Entry", "company": company,
            "stock_entry_type": "Material Receipt", "posting_date": add_days(today(), -1),
            "posting_time": "06:00:00", "set_posting_time": 1,
            "items": [{"item_code": fixture.item.name, "qty": 30, "basic_rate": 2,
                       "t_warehouse": fixture.warehouse.name, "expense_account": fixture.accounts.expense,
                       "cost_center": fixture.cost_center}]}), [])
        balance(30, 60)
        passed()

        gate("two_same_item_so_rows_have_distinct_exact_flows")
        traced = order([(4, 11, True), (3, 17, True)])
        flow_names = create_sales_order_flows(traced.name)
        assert len(flow_names) == 2 and len(set(flow_names)) == 2
        assert create_sales_order_flows(traced.name) == flow_names
        flows = {frappe.get_doc(FLOW, name).sales_order_item: frappe.get_doc(FLOW, name) for name in flow_names}
        for document in flows.values():
            remember(document)
        first, second = (row.name for row in traced.items)
        flow_a, flow_b = flows[first], flows[second]
        assert flow_a.item_code == flow_b.item_code == fixture.item.name
        assert flow_a.sales_order == flow_b.sales_order == traced.name
        assert flow_a.planned_qty == 4 and flow_b.planned_qty == 3
        evidence["same_item_source_rows"] = {first: flow_a.name, second: flow_b.name}
        passed()

        # The existing source_changed flag is a review marker, not a new blanket
        # shipping freeze. Native prices/valuation must remain their own values.
        gate("source_changed_review_marker_preserves_native_shipping")
        traced.items[0].jf_packaging = prefix + "修改包装"
        traced.save()
        flow_a.reload()
        assert flow_a.source_changed and flow_a.source_packaging == traced.items[0].jf_packaging
        assert flow_a.packaging != flow_a.source_packaging
        passed()

        reject("same_item_so_child_cannot_keep_another_flow", lambda: changed(
            dn(traced, first, 1), so_detail=second, jf_flow_order=flow_a.name),
            frappe.ValidationError, "销售明细流程单与精确来源不一致")
        reject("wrong_item_source_rejected", lambda: changed(dn(traced, first, 1),
            item_code=fixture.wrong_item.name), frappe.ValidationError, "销售来源明细商品")
        reject("conflicting_client_flow_rejected", lambda: changed(dn(traced, first, 1),
            jf_flow_order=flow_b.name), frappe.ValidationError, "销售明细流程单与精确来源不一致")
        frappe.set_user("Administrator")
        def wrong_company():
            document = dn(traced, first, 1)
            document.company = fixture.other_company
            return remember(document).insert()
        reject("wrong_company_source_rejected", wrong_company, frappe.ValidationError, "属于同一公司")

        # Deliberately inconsistent duplicate source identity is a fixture made
        # through native Flow.insert, using the same server constructor flag as
        # create_sales_order_flows. A nested savepoint removes it immediately.
        def duplicate_source():
            duplicate = frappe.get_doc({"doctype": FLOW, "company": company,
                "item_code": fixture.item.name, "planned_qty": 4, "sales_order": traced.name,
                "sales_order_item": first, "source_key": prefix + "-duplicate-source"})
            duplicate.flags.from_sales_order = True
            remember(duplicate).insert()
            return remember(dn(traced, first, 1)).insert()
        reject("two_flows_on_one_exact_so_row_are_ambiguous", duplicate_source,
               frappe.ValidationError, "关联了多个流程单")

        frappe.set_user(fixture.actors["sales"])
        reject("ordinary_sales_cannot_discover_hidden_flow", lambda: remember(
            dn(traced, first, 1)).insert(), frappe.PermissionError, "没有完整读取销售来源单据或流程单的权限",
            hidden=flow_names)
        frappe.set_user("Administrator")
        insert("User Permission", user=fixture.actors["traced"], allow=FLOW,
               for_value=flow_b.name, apply_to_all_doctypes=1)
        frappe.set_user(fixture.actors["traced"])
        assert not frappe.get_doc(FLOW, flow_a.name).has_permission("read")
        assert frappe.get_doc(FLOW, flow_b.name).has_permission("read")
        reject("native_flow_user_permission_denies_hidden_source", lambda: remember(
            dn(traced, first, 1)).insert(), frappe.PermissionError, "没有完整读取销售来源单据或流程单的权限",
            hidden=flow_names)
        # Remove only the synthetic restriction through the native API; the
        # positive path still uses the user's unchanged real DocPerm roles.
        frappe.set_user("Administrator")
        restriction = frappe.db.get_value("User Permission", {
            "user": fixture.actors["traced"], "allow": FLOW, "for_value": flow_b.name}, "name")
        frappe.delete_doc("User Permission", restriction)
        frappe.clear_cache(user=fixture.actors["traced"])
        frappe.set_user(fixture.actors["traced"])

        gate("partial_dn_first_batch_exact_flow_native_qty_rate_stock")
        batch_a1 = post(dn(traced, first, 2, "08:00:00"), deliveries)
        assert batch_a1.items[0].jf_flow_order == flow_a.name and batch_a1.items[0].rate == 11
        assert sum(flt(row.actual_qty) for row in _voucher_entries("Stock Ledger Entry", batch_a1)) == -2
        traced.reload()
        assert traced.items[0].delivered_qty == 2 and traced.items[1].delivered_qty == 0
        balance(28, 56)
        passed()
        gate("partial_dn_second_batch_same_exact_so_row")
        remaining = dn(traced, first, 2, "08:30:00")
        assert remaining.flags.qa_native_mapped_qty == 2
        batch_a2 = post(remaining, deliveries)
        assert batch_a2.items[0].jf_flow_order == flow_a.name and batch_a2.items[0].so_detail == first
        assert batch_a2.items[0].rate == 11
        passed()
        gate("same_item_second_so_row_keeps_its_own_flow")
        batch_b = post(dn(traced, second, 3, "09:00:00"), deliveries)
        assert batch_b.items[0].jf_flow_order == flow_b.name and batch_b.items[0].rate == 17
        traced.reload()
        assert [flt(row.delivered_qty) for row in traced.items] == [4, 3]
        balance(23, 46)
        passed()

        reject("si_dn_child_must_belong_to_exact_dn", lambda: changed(si(batch_a1),
            dn_detail=batch_b.items[0].name), frappe.ValidationError, "明细不属于所选来源单据")
        reject("si_upstream_so_child_cannot_cross_same_item_rows", lambda: changed(si(batch_a1),
            so_detail=second), frappe.ValidationError, "精确来源链不一致")
        frappe.set_user(fixture.actors["accounts"])
        reject("ordinary_accounts_cannot_discover_traced_si_flow", lambda: remember(si(batch_a1)).insert(),
               frappe.PermissionError, "没有完整读取销售来源单据或流程单的权限", hidden=flow_names)
        frappe.set_user(fixture.actors["traced"])
        for index, (source, expected_flow) in enumerate(((batch_a1, flow_a.name), (batch_a2, flow_a.name),
                                                       (batch_b, flow_b.name))):
            gate("native_dn_to_si_exact_chain_" + str(index + 1))
            ledger = _ledger_snapshot()
            invoice = post(si(source, f"10:{index * 10:02d}:00"), invoices)
            _assert_invoice(invoice, source, expected_flow, flt(source.items[0].qty))
            assert _ledger_snapshot()["Stock Ledger Entry"] == ledger["Stock Ledger Entry"]
            passed()
        invoice_a1, invoice_a2, invoice_b = invoices

        reject("si_return_missing_original_child_does_not_guess_item", lambda: changed(returned(invoice_a1),
            sales_invoice_item=None), frappe.ValidationError, "必须提供精确原明细")
        reject("si_return_wrong_original_child_rejected", lambda: changed(returned(invoice_a1),
            sales_invoice_item=invoice_b.items[0].name), frappe.ValidationError, "明细不属于所选来源单据")
        gate("native_partial_si_credit_exact_original_child_no_stock")
        ledger = _ledger_snapshot()
        credit_a1 = post(returned(invoice_a1, 1, "12:00:00"), invoices)
        assert credit_a1.return_against == invoice_a1.name
        assert credit_a1.items[0].sales_invoice_item == invoice_a1.items[0].name
        _assert_invoice(credit_a1, batch_a1, flow_a.name, -1)
        assert _ledger_snapshot()["Stock Ledger Entry"] == ledger["Stock Ledger Entry"]
        passed()

        reject("dn_return_missing_original_child_does_not_guess_item", lambda: changed(returned(batch_b),
            dn_detail=None), frappe.ValidationError, "必须提供精确原明细")
        reject("dn_return_wrong_original_child_rejected", lambda: changed(returned(batch_b),
            dn_detail=batch_a1.items[0].name), frappe.ValidationError, "明细不属于所选来源单据")
        gate("native_partial_dn_return_exact_original_child_and_stock")
        return_b = post(returned(batch_b, 1, "12:30:00"), deliveries)
        assert return_b.return_against == batch_b.name and return_b.items[0].dn_detail == batch_b.items[0].name
        assert return_b.items[0].jf_flow_order == flow_b.name and return_b.items[0].qty == -1
        assert return_b.items[0].rate == batch_b.items[0].rate
        assert sum(flt(row.actual_qty) for row in _voucher_entries("Stock Ledger Entry", return_b)) == 1
        balance(24, 48)
        passed()
        gate("native_credit_from_return_dn_keeps_historical_flow_without_si_return_against")
        credit_b = post(si(return_b, "13:00:00"), invoices)
        assert credit_b.is_return and not credit_b.return_against
        assert credit_b.update_billed_amount_in_sales_order
        _assert_invoice(credit_b, return_b, flow_b.name, -1)
        traced.reload()
        assert traced.items[1].returned_qty == 1 and traced.items[1].billed_amt == 34
        passed()

        # Same Item as both traced rows, but this independent native SO is
        # explicitly untracked. Item equality must never make a Flow context.
        frappe.set_user(fixture.actors["sales"])
        gate("ordinary_sales_role_so_and_dn_without_flow")
        ordinary = order([(1, 5, False), (1, 6, False)])
        assert not frappe.db.exists(FLOW, {"sales_order": ordinary.name})
        plain_a = post(dn(ordinary, ordinary.items[0].name, 1, "14:00:00"), deliveries)
        plain_b = post(dn(ordinary, ordinary.items[1].name, 1, "14:10:00"), deliveries)
        assert not plain_a.items[0].jf_flow_order and not plain_b.items[0].jf_flow_order
        assert plain_a.items[0].rate == 5 and plain_b.items[0].rate == 6
        passed()
        frappe.set_user(fixture.actors["accounts"])
        gate("ordinary_accounts_user_native_dn_to_si_without_flow")
        plain_invoice = post(si(plain_a, "15:00:00"), invoices)
        _assert_invoice(plain_invoice, plain_a, None, 1)
        assert not frappe.has_permission(FLOW, "read")
        passed()
        frappe.set_user(fixture.actors["selector"])
        gate("ordinary_accounts_selector_only_so_parent_is_native_noop")
        assert frappe.has_permission("Sales Order", "select")
        assert not frappe.get_doc("Sales Order", ordinary.name).has_permission("read")
        assert frappe.get_doc("Delivery Note", plain_b.name).has_permission("read")
        selector_invoice = post(si(plain_b, "15:10:00"), invoices)
        _assert_invoice(selector_invoice, plain_b, None, 1)
        assert not frappe.has_permission(FLOW, "read")
        evidence["untraced_selector_parent"] = "native SO.select true / SO.read false; DN -> SI submitted"
        passed()

        frappe.set_user(fixture.actors["traced"])
        reject("so_child_from_another_parent_rejected", lambda: changed(
            frappe.copy_doc(plain_a), against_sales_order=ordinary.name, so_detail=first), frappe.ValidationError,
            "明细不属于所选来源单据")

        gate("later_so_flow_does_not_rebind_persisted_untraced_dn_si")
        ordinary.reload()
        ordinary.items[0].jf_make_flow = 1
        ordinary.save()
        late_names = create_sales_order_flows(ordinary.name)
        assert len(late_names) == 1
        late_flow = remember(frappe.get_doc(FLOW, late_names[0]))
        plain_a.reload()
        plain_invoice.reload()
        assert not plain_a.items[0].jf_flow_order and not plain_invoice.items[0].jf_flow_order
        passed()
        frappe.set_user(fixture.actors["selector"])
        gate("historical_untraced_si_credit_ignores_later_hidden_so_flow")
        assert not frappe.get_doc("Sales Order", ordinary.name).has_permission("read")
        assert not frappe.get_doc(FLOW, late_flow.name).has_permission("read")
        plain_credit = post(returned(plain_invoice, 1, "16:00:00"), invoices)
        assert plain_credit.return_against == plain_invoice.name
        assert plain_credit.items[0].sales_invoice_item == plain_invoice.items[0].name
        _assert_invoice(plain_credit, plain_a, None, -1)
        passed()
        frappe.set_user(fixture.actors["sales"])
        gate("historical_untraced_dn_return_ignores_later_hidden_so_flow")
        plain_return = post(returned(plain_a, 1, "16:30:00"), deliveries)
        assert plain_return.return_against == plain_a.name
        assert plain_return.items[0].dn_detail == plain_a.items[0].name
        assert not plain_return.items[0].jf_flow_order
        passed()

        frappe.set_user(fixture.actors["traced"])

        reject("native_dn_cannot_cancel_before_linked_si", lambda: native_action(frappe.get_doc(
            "Delivery Note", batch_a2.name), "cancel"), frappe.ValidationError, invoice_a2.name)
        # Reload before each cancel: native status updates changed parents since
        # they were first submitted. No force or ignore_links is used.
        for document in reversed(invoices):
            gate("native_cancel_si_" + document.name)
            current = frappe.get_doc(document.doctype, document.name)
            native_action(current, "cancel")
            assert frappe.db.get_value(document.doctype, document.name, "docstatus") == 2
            passed("native_cancel_sales_invoice")
        for document in reversed(deliveries):
            gate("native_cancel_dn_" + document.name)
            current = frappe.get_doc(document.doctype, document.name)
            native_action(current, "cancel")
            assert frappe.db.get_value(document.doctype, document.name, "docstatus") == 2
            passed("native_cancel_delivery_note")
        gate("native_cancel_restores_seed_stock_and_order_quantities")
        _finish_fixture_reposts(company, [fixture.item.name], [fixture.warehouse.name], vouchers)
        balance(30, 60)
        for source in orders:
            source.reload()
            assert all(flt(row.delivered_qty) == 0 and flt(row.billed_amt) == 0 for row in source.items)
        passed()
        for document in reversed(orders):
            gate("native_cancel_so_" + document.name)
            native_action(frappe.get_doc(document.doctype, document.name), "cancel")
            assert frappe.db.get_value(document.doctype, document.name, "docstatus") == 2
            passed("native_cancel_sales_order")
        gate("so_cancellation_marks_flow_source_for_review")
        for flow in [*flows.values(), late_flow]:
            flow.reload()
            assert flow.source_changed and flow.source_status == "Cancelled"
        passed()
        gate("native_cancel_seed_and_finish_only_fixture_reposts")
        native_action(frappe.get_doc(seed.doctype, seed.name), "cancel")
        _finish_fixture_reposts(company, [fixture.item.name], [fixture.warehouse.name], vouchers)
        balance(0, 0)
        pending = frappe.get_all("Repost Item Valuation", filters={"item_code": fixture.item.name,
            "warehouse": fixture.warehouse.name, "status": ["in", ["Queued", "In Progress", "Failed"]]}, pluck="name")
        assert not pending, "存在未完成的本次夹具原生库存重算"
        passed()
        evidence["native_submitted_documents"] = {
            "Sales Order": len(orders), "Delivery Note": len(deliveries),
            "Sales Invoice": len(invoices), FLOW: len(flows) + 1, "Stock Entry": 1,
        }
    except Exception as exception:
        exception.add_note("fulfillment smoke failed at gate: " + current_gate)
        raise
    finally:
        # Collect even partially inserted documents, generated children, native
        # valuation jobs and attached metadata before rolling back the savepoint.
        try:
            frappe.set_user("Administrator")
            for document in objects:
                if document.name:
                    records.add((document.doctype, document.name))
                records.update((row.doctype, row.name) for row in document.get_all_children() if row.name)
            order_names = [name for doctype, name in records if doctype == "Sales Order"]
            if order_names:
                records.update((FLOW, name) for name in frappe.get_all(
                    FLOW, filters={"sales_order": ["in", order_names]}, pluck="name"))
            if fixture.get("item"):
                records.update(("Bin", name) for name in frappe.get_all(
                    "Bin", filters={"item_code": fixture.item.name}, pluck="name"))
            item_name = fixture.item.name if fixture.get("item") else None
            records.update(("Repost Item Valuation", row.name) for row in frappe.get_all(
                "Repost Item Valuation", filters={"company": company},
                fields=["name", "item_code", "voucher_type", "voucher_no"])
                if (item_name and row.item_code == item_name)
                or (row.voucher_type, row.voucher_no) in vouchers)
            own_names = list({name for _, name in records})
            if own_names:
                records.update(("File", name) for name in frappe.get_all(
                    "File", filters={"attached_to_name": ["in", own_names]}, pluck="name"))
                records.update(("Version", name) for name in frappe.get_all(
                    "Version", filters={"docname": ["in", own_names]}, pluck="name"))
        finally:
            try:
                _end_rollback_only(point)
            finally:
                frappe.in_test = original_test
                frappe.flags.through_repost_item_valuation = original_repost
                frappe.db.MAX_WRITES_PER_TRANSACTION = original_max_writes
                frappe.set_user(original_user)
                for user in users:
                    frappe.clear_cache(user=user)
                for doctype, name in records:
                    frappe.clear_document_cache(doctype, name)
            assert all(not frappe.db.exists(doctype, name) for doctype, name in records), "回滚后残留夹具数据"
            assert _counts() == before_counts, "回滚后业务/主档/通知/库存重算表记录数有差异"
            assert _ledger_snapshot() == before, "回滚后 whole GL/SLE hash 有差异"
    checks.extend(("rollback_all_tracked_business_master_and_auxiliary_rows",
                   "rollback_entire_gl_and_sle_hashes"))
    return {"passed": True, "checks": checks, "gate_count": len(checks),
            "business_data": "rolled_back", "tracked_records": dict(Counter(dt for dt, _ in records)),
            "whole_ledger_hashes_before": before, "whole_ledger_hashes_after": _ledger_snapshot(),
            "table_counts_before": before_counts, "table_counts_after": _counts(),
            "evidence": evidence, "execution": "direct Python; one call; serial rollback experiment"}
