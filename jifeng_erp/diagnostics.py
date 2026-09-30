"""Bench-only checks; never include secrets in returned values."""
import frappe


def _begin_rollback_only(point):
    """Prevent Frappe document notifications from committing smoke-test rows."""
    if not hasattr(frappe.db, "_disable_transaction_control"):
        frappe.throw("当前 Frappe 数据库接口不支持无提交冒烟测试")
    frappe.db._disable_transaction_control += 1
    try:
        frappe.db.savepoint(point)
    except Exception:
        frappe.db._disable_transaction_control -= 1
        raise


def _end_rollback_only(point):
    try:
        frappe.db.rollback(save_point=point)
    finally:
        frappe.db._disable_transaction_control -= 1


def check():
    frappe.only_for("System Manager")
    settings = frappe.get_single("Jushuitan Settings")
    flow_hook = "jifeng_erp.setup.validate_flow_links"
    doc_events = frappe.get_hooks("doc_events")
    flow_hooks_active = {
        doctype: flow_hook in doc_events.get(doctype, {}).get("validate", [])
        for doctype in (
            "Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice",
            "Subcontracting Order", "Subcontracting Receipt", "Stock Entry", "Work Order",
        )
    }
    return {
        "installed_apps": frappe.get_installed_apps(),
        "enabled": bool(settings.enabled),
        "environment": settings.environment,
        "app_key_configured": bool(settings.app_key),
        "secret_configured": bool(settings.get_password("app_secret", raise_exception=False)),
        "token_configured": bool(settings.get_password("access_token", raise_exception=False)),
        "company_configured": bool(settings.company),
        "merchant_configured": bool(settings.merchant_id),
        "receipt_link_installed": frappe.get_meta("Subcontracting Receipt Item").has_field("jf_flow_order"),
        "subcontracting_order_link_installed": frappe.get_meta("Subcontracting Order Item").has_field("jf_flow_order"),
        "procurement_links_installed": all(
            frappe.get_meta(doctype).has_field("jf_flow_order")
            for doctype in ("Material Request Item", "Purchase Order Item", "Purchase Receipt Item",
                            "Purchase Invoice Item")
        ),
        "flow_hooks_active": flow_hooks_active,
        "sales_order_fields_installed": all(frappe.get_meta("Sales Order Item").has_field(field)
                                             for field in ("jf_make_flow", "jf_requirements", "jf_packaging",
                                                           "jf_requirements_attachment")),
        "work_order_link_installed": frappe.get_meta("Work Order").has_field("jf_flow_order"),
        "material_issue_link_installed": frappe.get_meta("Stock Entry").has_field("jf_material_issue_flow"),
        "employee_supplement_installed": bool(frappe.db.exists("DocType", "Jifeng Employee Supplement")),
        "hrms_installed": "hrms" in frappe.get_installed_apps(),
        "wage_entry_installed": bool(frappe.db.exists("DocType", "Jifeng Wage Entry")),
    }


def import_site_configuration():
    """Read an existing site_config.json jushuitan object; never use CLI secret args."""
    frappe.only_for("System Manager")
    config = frappe.conf.get("jushuitan")
    if not isinstance(config, dict):
        frappe.throw("站点配置中缺少 jushuitan 对象")
    settings = frappe.get_single("Jushuitan Settings")
    allowed = {"app_key", "app_secret", "access_token", "refresh_token", "app_type", "environment",
               "merchant_id", "company", "item_group", "stock_uom", "token_expires_at", "image_hosts"}
    for name in allowed:
        if name in config:
            settings.set(name, config[name])
    # Import alone must not start scheduled network access.
    settings.save()
    return "配置已导入；敏感字段已由 Frappe 加密保存。"


def smoke_test(company, item_group, stock_uom):
    """Run on an installed test site. All created business rows roll back."""
    frappe.only_for("System Manager")
    from jifeng_erp.workflow import create_flows, receipt_costs, stock_availability
    from jifeng_erp.jushuitan import order_snapshot, record_key
    from jifeng_erp.sync import upsert_order, upsert_sku
    point = "jf_smoke"
    _begin_rollback_only(point)
    try:
        ledger_counts = {dt: frappe.db.count(dt) for dt in ("GL Entry", "Stock Ledger Entry")}
        suffix = frappe.generate_hash(length=10)
        raw = frappe.get_doc({"doctype": "Item", "item_code": "JF-RAW-" + suffix,
                              "item_group": item_group, "stock_uom": stock_uom}).insert()
        item = frappe.get_doc({"doctype": "Item", "item_code": "JF-FG-" + suffix,
                               "item_group": item_group, "stock_uom": stock_uom}).insert()
        frappe.get_doc({"doctype": "Jifeng Product Profile", "item_code": item.name, "company": company,
                        "materials": [{"item_code": raw.name, "uom": stock_uom, "qty_per_unit": 1, "unit_price": 1}]}).insert()
        order = frappe.get_doc({"doctype": "Jifeng External Order", "sync_key": "smoke-" + suffix,
                                "scope": "smoke", "company": company, "external_order_id": "smoke-" + suffix,
                                "source_status": "WaitConfirm", "items": [{"external_line_id": "1", "sku_id": item.name,
                                                                          "item_code": item.name, "qty": 100}]})
        order.flags.from_sync = True
        order.insert()
        flows = create_flows(order)
        assert create_flows(order) == flows, "Repeated conversion must reuse the flow"
        flow = frappe.get_doc("Jifeng Flow Order", flows[0])
        flow.target_warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
        flow.save()
        request = flow.make_request()
        assert flow.make_request() == request, "Repeated material request must reuse the draft"
        preview_name = flow.make_preview()
        assert flow.make_preview() == preview_name
        preview = frappe.get_doc("Jifeng Cost Preview", preview_name)
        preview.append("batches", {"receipt_type": "首次回货", "received_qty": 100, "good_qty": 50, "rework_qty": 50})
        preview.append("batches", {"receipt_type": "返工回货", "received_qty": 50, "good_qty": 48, "lost_qty": 2, "extra_processing_amount": 20})
        preview.save()
        assert float(preview.good_qty) == 98 and float(preview.total_cost) == 120
        assert float(preview.unallocated_cost) == 0
        html = frappe.get_print("Jifeng Flow Order", flow.name, print_format="Jifeng Flow Sheet")
        assert "集沣生产流程单" in html and raw.name in html
        assert frappe.db.get_value("Material Request", request, "docstatus") == 0
        assert receipt_costs(flow.name)["rows"] == []
        before_costs = (preview.material_amount, preview.processing_amount, preview.other_amount)
        try:
            preview.load_receipt_costs()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Actual costs require a posted receipt")
        preview.reload()
        assert (preview.material_amount, preview.processing_amount, preview.other_amount) == before_costs
        stock_availability(flow.name)
        settings = frappe._dict(sync_scope="smoke-" + suffix, company=company,
                                stock_uom=stock_uom, item_group=item_group,
                                auto_create_flows=1, flow_statuses="WaitConfirm", image_hosts="")
        sku = "JF-SYNC-" + suffix
        mapped = upsert_sku(settings, {"sku_id": sku, "name": "同步测试商品"})
        assert upsert_sku(settings, {"sku_id": sku, "name": "同步测试商品更新"}) == mapped
        raw_order = {"o_id": suffix, "so_id": "TEST", "co_id": "TEST", "ts": 1,
                     "status": "WaitConfirm", "items": [
                         {"oi_id": "1", "sku_id": sku, "qty": 2},
                         {"oi_id": "2", "sku_id": sku, "qty": 3}]}
        upsert_order(settings, order_snapshot(raw_order))
        upsert_order(settings, order_snapshot(raw_order))
        imported = frappe.get_doc("Jifeng External Order", record_key(settings.sync_scope, suffix))
        assert len({row.flow_order for row in imported.items}) == 2
        raw_order["ts"], raw_order["items"][0]["qty"] = 2, 4
        upsert_order(settings, order_snapshot(raw_order))
        changed = frappe.get_doc("Jifeng Flow Order", imported.items[0].flow_order)
        assert changed.source_changed and changed.planned_qty == 2 and changed.source_qty == 4
        changed.flags.from_sync = False
        changed.external_order = None
        try:
            changed.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Source identity cannot be removed manually")
        raw_order["ts"], raw_order["items"][0]["item_status"] = 3, "Cancelled"
        upsert_order(settings, order_snapshot(raw_order))
        assert frappe.db.get_value("Jifeng Flow Order", imported.items[0].flow_order, "source_status") == "Cancelled"
        assert all(frappe.db.count(dt) == count for dt, count in ledger_counts.items())
        return {"passed": True, "checks": ["flow_idempotency", "procurement_draft", "cost_preview", "rework_loss",
                "receipt_cost_query", "empty_receipt_import_guard", "stock_query", "sku_upsert", "order_upsert", "separate_lines", "source_change_guard",
                "print_render", "no_ledger_posting"],
                "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_sales_order_flows(company, customer, item_group, stock_uom):
    """Exercise submitted manual order → flow → source change/cancel; roll all rows back."""
    frappe.only_for("System Manager")
    from frappe.utils import add_days, today

    from jifeng_erp.workflow import create_sales_order_flows

    point = "jf_sales_smoke"
    _begin_rollback_only(point)
    try:
        ledger_counts = {dt: frappe.db.count(dt) for dt in ("GL Entry", "Stock Ledger Entry")}
        suffix = frappe.generate_hash(length=10)
        item = frappe.get_doc({"doctype": "Item", "item_code": "JF-SALES-" + suffix,
                               "item_group": item_group, "stock_uom": stock_uom}).insert()
        frappe.get_doc({"doctype": "Jifeng Product Profile", "item_code": item.name,
                        "company": company}).insert()
        warehouse = frappe.db.get_value(
            "Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name"
        )
        if not warehouse:
            frappe.throw("测试公司需要启用的明细仓库")
        currency = frappe.db.get_value("Company", company, "default_currency")
        price_list = frappe.db.get_value(
            "Price List", {"selling": 1, "enabled": 1, "currency": currency}, "name"
        )
        if not price_list:
            frappe.throw("测试公司需要同币种的已启用销售价格表")
        order = frappe.get_doc({
            "doctype": "Sales Order", "company": company, "customer": customer,
            "delivery_date": add_days(today(), 7),
            "selling_price_list": price_list, "price_list_currency": currency,
            "plc_conversion_rate": 1, "currency": currency, "conversion_rate": 1,
            "items": [{"item_code": item.name, "qty": 2, "rate": 10,
                       "delivery_date": add_days(today(), 7),
                       "warehouse": warehouse,
                       "jf_make_flow": 1, "jf_requirements": "测试刻字", "jf_packaging": "测试礼盒"}],
        }).insert()
        order.submit()
        names = create_sales_order_flows(order.name)
        assert create_sales_order_flows(order.name) == names and len(names) == 1
        flow = frappe.get_doc("Jifeng Flow Order", names[0])
        assert flow.sales_order == order.name and flow.sales_order_item == order.items[0].name
        assert flow.requirements == "测试刻字" and flow.packaging == "测试礼盒"
        assert flow.planned_qty == 2 and flow.source_qty == 2
        assert order.name in frappe.get_print("Jifeng Flow Order", flow.name, print_format="Jifeng Flow Sheet")
        preview = frappe.get_doc("Jifeng Cost Preview", flow.make_preview())
        assert preview.sales_order == order.name
        tampered = frappe.get_doc("Jifeng Flow Order", flow.name)
        tampered.sales_order = None
        try:
            tampered.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Sales Order source identity cannot be removed manually")

        order.items[0].jf_requirements = "测试改刻字"
        order.save()
        flow.reload()
        assert flow.source_changed and flow.requirements == "测试刻字"
        assert flow.source_requirements == "测试改刻字"
        try:
            flow.make_request()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Changed Sales Order must block new procurement")
        order.cancel()
        flow.reload()
        assert flow.source_status == "Cancelled" and flow.source_changed
        assert all(frappe.db.count(dt) == count for dt, count in ledger_counts.items())
        return {"passed": True, "checks": ["sales_flow_idempotency", "source_line_trace", "source_identity_guard",
                "requirements_snapshot", "print_source", "preview_source", "change_review",
                "change_blocks_procurement", "cancel_review", "no_ledger_posting"], "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_stock_flow(company, item_group, stock_uom):
    """Post receipt and issue for one flow material, then roll back the ledger."""
    frappe.only_for("System Manager")
    warehouse = frappe.db.get_value(
        "Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name"
    )
    if not warehouse:
        frappe.throw("测试公司需要启用的明细仓库")

    point = "jf_stock_smoke"
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        raw = frappe.get_doc({
            "doctype": "Item", "item_code": "JF-STOCK-RAW-" + suffix,
            "item_group": item_group, "stock_uom": stock_uom,
        }).insert()
        finished = frappe.get_doc({
            "doctype": "Item", "item_code": "JF-STOCK-FG-" + suffix,
            "item_group": item_group, "stock_uom": stock_uom,
        }).insert()
        flow = frappe.get_doc({
            "doctype": "Jifeng Flow Order", "company": company,
            "item_code": finished.name, "planned_qty": 5,
            "materials": [{"item_code": raw.name, "uom": stock_uom,
                           "qty_per_unit": 1, "unit_price": 10}],
        }).insert()
        receipt = frappe.get_doc({
            "doctype": "Stock Entry", "company": company,
            "stock_entry_type": "Material Receipt", "purpose": "Material Receipt",
            "items": [{"item_code": raw.name, "qty": 5, "basic_rate": 10,
                       "t_warehouse": warehouse, "jf_flow_order": flow.name}],
        }).insert()
        receipt.submit()
        issue = frappe.get_doc({
            "doctype": "Stock Entry", "company": company,
            "stock_entry_type": "Material Issue", "purpose": "Material Issue",
            "items": [{"item_code": raw.name, "qty": 2,
                       "s_warehouse": warehouse, "jf_flow_order": flow.name}],
        }).insert()
        issue.submit()
        entries = frappe.db.get_all(
            "Stock Ledger Entry",
            filters={"item_code": raw.name, "warehouse": warehouse,
                     "is_cancelled": 0},
            fields=["actual_qty"],
        )
        assert len(entries) == 2
        assert round(sum(float(row.actual_qty) for row in entries), 2) == 3
        balance = frappe.db.get_value(
            "Bin", {"item_code": raw.name, "warehouse": warehouse}, "actual_qty"
        )
        assert round(float(balance), 2) == 3
        assert receipt.items[0].jf_flow_order == flow.name
        assert issue.items[0].jf_flow_order == flow.name
        return {"passed": True,
                "checks": ["stock_receipt", "stock_issue", "stock_ledger_balance",
                           "flow_link"],
                "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_receipt_batches(company, item_group, stock_uom):
    """Submit native subcontract receipts/returns on a test site, then roll back."""
    frappe.only_for("System Manager")
    from frappe.utils import today
    from erpnext.buying.doctype.purchase_order.purchase_order import make_subcontracting_order
    from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
    from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import make_subcontracting_receipt
    from erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt import make_subcontract_return
    from jifeng_erp.workflow import receipt_batch_candidates

    warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name")
    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not warehouse or not supplier_group:
        frappe.throw("测试公司需要明细仓库和供应商分组")
    point = "jf_receipt_batches_smoke"
    _begin_rollback_only(point)
    try:
        suffix = frappe.generate_hash(length=10)
        items = []
        for kind in ("RAW", "FG", "SERVICE"):
            items.append(frappe.get_doc({
                "doctype": "Item", "item_code": f"JF-BATCH-{kind}-{suffix}",
                "item_group": item_group, "stock_uom": stock_uom,
                "is_stock_item": int(kind != "SERVICE"),
                "is_sub_contracted_item": int(kind == "FG"),
            }).insert())
        raw, finished, service = items
        supplier = frappe.get_doc({"doctype": "Supplier", "supplier_name": "JF-BATCH-SUP-" + suffix,
                                   "supplier_type": "Company", "supplier_group": supplier_group}).insert()
        supplier_warehouse = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "JF-BATCH-SUP-" + suffix,
                                             "company": company}).insert()
        rejected_warehouse = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "JF-BATCH-REJ-" + suffix,
                                             "company": company}).insert()
        bom = frappe.get_doc({"doctype": "BOM", "company": company, "item": finished.name,
                             "quantity": 1, "items": [{"item_code": raw.name, "qty": 1, "rate": 10}]}).insert()
        bom.submit()
        flow = frappe.get_doc({"doctype": "Jifeng Flow Order", "company": company,
                              "item_code": finished.name, "planned_qty": 5,
                              "materials": [{"item_code": raw.name, "uom": stock_uom,
                                             "qty_per_unit": 1, "unit_price": 10}]}).insert()
        stock = frappe.get_doc({"doctype": "Stock Entry", "company": company,
                                "stock_entry_type": "Material Receipt",
                                "items": [{"item_code": raw.name, "qty": 5, "basic_rate": 10,
                                           "t_warehouse": warehouse}]}).insert()
        stock.submit()
        purchase = frappe.get_doc({"doctype": "Purchase Order", "company": company,
                                   "supplier": supplier.name, "is_subcontracted": 1,
                                   "schedule_date": today(), "set_warehouse": warehouse,
                                   "items": [{"item_code": service.name, "qty": 5, "rate": 20,
                                              "fg_item": finished.name, "fg_item_qty": 5}]}).insert()
        purchase.submit()
        subcontract = make_subcontracting_order(purchase.name)
        subcontract.supplier_warehouse = supplier_warehouse.name
        subcontract.items[0].bom = bom.name
        subcontract.items[0].jf_flow_order = flow.name
        subcontract.insert()
        subcontract.submit()
        transfer = frappe.get_doc(make_rm_stock_entry(subcontract.name, [{
            "item_code": finished.name, "rm_item_code": raw.name, "qty": 5,
            "item_name": raw.name, "rate": 10, "stock_uom": stock_uom, "warehouse": warehouse,
        }])).insert()
        transfer.submit()
        receipt = make_subcontracting_receipt(subcontract.name)
        receipt.items[0].qty = 3
        receipt.items[0].rejected_qty = 1
        receipt.items[0].process_loss_qty = 1
        receipt.items[0].rejected_warehouse = rejected_warehouse.name
        receipt.insert()
        receipt.submit()
        assert receipt.items[0].jf_flow_order == flow.name
        assert receipt_batch_candidates(flow.name)[0]["qty"] == 5
        preview = frappe.get_doc("Jifeng Cost Preview", flow.make_preview())
        preview.append("batches", {"receipt_type": "首次回货", "reference_no": receipt.name,
                                   "source_receipt": receipt.name, "source_receipt_item": receipt.items[0].name,
                                   "received_qty": 5, "pending_qty": 4, "lost_qty": 1})
        preview.save()

        def return_goods(qty):
            returned = make_subcontract_return(receipt.name)
            returned.items[0].qty = -qty
            returned.items[0].rejected_qty = 0
            returned.items[0].process_loss_qty = 0
            returned.insert()
            returned.submit()
            assert returned.items[0].jf_flow_order == flow.name
            return returned

        partial = return_goods(2)
        candidate = receipt_batch_candidates(flow.name)[0]
        assert (candidate["qty"], candidate["lost_qty"], candidate["returned_qty"]) == (3, 1, 2)
        try:
            preview.save()
        except frappe.ValidationError as exc:
            assert "净回货数量已变化" in str(exc)
        else:
            raise AssertionError("Stale linked batch must be reconciled before save")
        preview.batches[0].received_qty = 3
        preview.batches[0].pending_qty = 2
        preview.save()
        assert preview.lost_qty == 1 and preview.pending_qty == 2 and preview.unreceived_qty == 2
        partial.cancel()
        candidate = receipt_batch_candidates(flow.name)[0]
        assert (candidate["qty"], candidate["returned_qty"]) == (5, 0)
        return {"passed": True, "checks": ["native_subcontract_chain", "accepted_rejected_loss_total",
                "source_flow_inheritance", "partial_return_net", "confirmed_loss_preserved",
                "stale_batch_save_guard", "reconciled_preview_save", "cancel_return_restores_quantity"],
                "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_payments(company, customer):
    """Submit one receipt and one supplier payment, then roll back both ledgers."""
    frappe.only_for("System Manager")
    from frappe.utils import today

    receivable = frappe.db.get_value(
        "Account", {"company": company, "account_type": "Receivable",
                    "is_group": 0, "disabled": 0}, "name"
    )
    payable = frappe.db.get_value(
        "Account", {"company": company, "account_type": "Payable",
                    "root_type": "Liability", "is_group": 0, "disabled": 0}, "name"
    )
    cash = frappe.db.get_value(
        "Account", {"company": company, "account_type": "Cash",
                    "is_group": 0, "disabled": 0}, "name"
    )
    supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
    if not all((receivable, payable, cash, supplier_group)):
        frappe.throw("测试公司需要客户/供应商往来及现金明细科目，并准备明细供应商分组")

    point = "jf_payment_smoke"
    _begin_rollback_only(point)
    try:
        supplier = frappe.get_doc({
            "doctype": "Supplier",
            "supplier_name": "JF-PAY-SUP-" + frappe.generate_hash(length=10),
            "supplier_type": "Company", "supplier_group": supplier_group,
        }).insert()
        cases = (
            ("Receive", "Customer", customer, receivable, cash, 100),
            ("Pay", "Supplier", supplier.name, cash, payable, 80),
        )
        for payment_type, party_type, party, paid_from, paid_to, amount in cases:
            payment = frappe.get_doc({
                "doctype": "Payment Entry", "payment_type": payment_type,
                "company": company, "posting_date": today(),
                "party_type": party_type, "party": party,
                "paid_from": paid_from, "paid_to": paid_to,
                "paid_amount": amount, "received_amount": amount,
            }).insert()
            payment.submit()
            entries = frappe.db.get_all(
                "GL Entry",
                filters={"voucher_type": "Payment Entry", "voucher_no": payment.name,
                         "is_cancelled": 0},
                fields=["debit", "credit"],
            )
            assert payment.docstatus == 1 and len(entries) == 2
            assert round(sum(float(row.debit) for row in entries), 2) == amount
            assert round(sum(float(row.credit) for row in entries), 2) == amount
        return {"passed": True,
                "checks": ["customer_receipt", "supplier_payment", "balanced_gl"],
                "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_expense_voucher(company):
    """Post and cancel a cash expense in a savepoint, then discard all rows."""
    frappe.only_for("System Manager")
    from frappe.utils import today

    currency = frappe.db.get_value("Company", company, "default_currency")
    expense_account = frappe.db.get_value(
        "Account", {"company": company, "root_type": "Expense", "is_group": 0,
                    "disabled": 0, "account_currency": currency}, "name"
    )
    cash_account = frappe.db.get_value(
        "Account", {"company": company, "root_type": "Asset", "account_type": "Cash",
                    "is_group": 0, "disabled": 0, "account_currency": currency}, "name"
    )
    cost_center = frappe.db.get_value(
        "Cost Center", {"company": company, "is_group": 0}, "name"
    )
    if not all((currency, expense_account, cash_account, cost_center)):
        frappe.throw("测试公司需要本币费用/现金明细科目与明细成本中心")

    point = "jf_expense_smoke"
    _begin_rollback_only(point)
    try:
        voucher = frappe.get_doc({
            "doctype": "Jifeng Expense Voucher", "company": company,
            "posting_date": today(), "store_name": "隔离测试店面",
            "legacy_number": "SMOKE-" + frappe.generate_hash(length=10),
            "counterparty_name": "隔离测试收款方", "cost_center": cost_center,
            "payment_method": "现金", "expense_account": expense_account,
            "payment_account": cash_account, "amount": 123.45,
            "summary": "费用过账与取消冒烟检查",
        }).insert()
        voucher.submit()
        journal = frappe.get_doc("Journal Entry", voucher.journal_entry)
        assert journal.docstatus == 1 and journal.jf_expense_voucher == voucher.name
        rows = frappe.db.get_all(
            "GL Entry",
            filters={"voucher_type": "Journal Entry", "voucher_no": journal.name,
                     "is_cancelled": 0},
            fields=["debit", "credit"],
        )
        assert len(rows) == 2
        assert round(sum(float(row.debit) for row in rows), 2) == 123.45
        assert round(sum(float(row.credit) for row in rows), 2) == 123.45
        try:
            journal.cancel()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("The linked Journal Entry must not be cancelled directly")
        assert frappe.db.get_value("Journal Entry", journal.name, "docstatus") == 1
        voucher.cancel()
        assert frappe.db.get_value("Journal Entry", journal.name, "docstatus") == 2
        return {"passed": True,
                "checks": ["expense_submit", "balanced_gl", "direct_cancel_guard",
                           "expense_cancel"],
                "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_work_order(flow_order):
    """On a test site, create a draft Work Order from a real flow and roll it back."""
    frappe.only_for("System Manager")
    flow = frappe.get_doc("Jifeng Flow Order", flow_order)
    if not flow.bom:
        frappe.throw("测试流程单需要已提交且启用的生产 BOM")
    if frappe.db.exists("Work Order", {"jf_flow_order": flow.name, "docstatus": ["<", 2]}):
        frappe.throw("请选择尚未生成生产工单的测试流程单")
    point = "jf_work_smoke"
    _begin_rollback_only(point)
    try:
        ledger_counts = {dt: frappe.db.count(dt) for dt in ("GL Entry", "Stock Ledger Entry")}
        work_order_name = flow.make_work_order()
        assert flow.make_work_order() == work_order_name
        work_order = frappe.get_doc("Work Order", work_order_name)
        assert work_order.docstatus == 0
        assert work_order.jf_flow_order == flow.name and work_order.production_item == flow.item_code
        assert work_order.bom_no == flow.bom and work_order.company == flow.company
        assert all(frappe.db.count(dt) == count for dt, count in ledger_counts.items())
        return {"passed": True, "checks": ["work_order_draft", "flow_link", "bom_match",
                "work_order_idempotency", "no_ledger_posting"], "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_employee_supplement(employee):
    """On a test site, save one synthetic HR extension record and roll it back."""
    frappe.only_for("System Manager")
    if frappe.db.exists("Jifeng Employee Supplement", {"employee": employee}):
        frappe.throw("请选择尚无集沣补充档案的测试员工")
    point = "jf_hr_smoke"
    _begin_rollback_only(point)
    try:
        staff = frappe.get_doc("Employee", employee)
        staff.check_permission("read")
        supplement = frappe.get_doc({
            "doctype": "Jifeng Employee Supplement", "employee": employee,
            "identity_number": "TEST-" + frappe.generate_hash(length=10),
            "wage_method": "计件", "term_type": "长期", "specialty": "测试工序",
        }).insert()
        supplement.reload()
        assert supplement.name == employee
        assert supplement.employee_name == staff.employee_name
        assert supplement.company == staff.company and supplement.department == staff.department
        assert supplement.wage_method == "计件" and supplement.term_type == "长期"
        return {"passed": True, "checks": ["employee_link", "native_profile_snapshot",
                "legacy_hr_fields"], "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)


def smoke_wage_entry(employee, salary_component, job_card=None, salary_structure=None):
    """Check approved wage → Additional Salary and optional Salary Slip, then roll back."""
    frappe.only_for("System Manager")
    from decimal import Decimal

    from frappe.utils import today

    staff = frappe.get_doc("Employee", employee)
    card = frappe.get_doc("Job Card", job_card) if job_card else None
    point = "jf_wage_smoke"
    _begin_rollback_only(point)
    try:
        ledger_counts = {dt: frappe.db.count(dt) for dt in ("GL Entry", "Stock Ledger Entry", "Salary Slip")}
        wage = frappe.get_doc({
            "doctype": "Jifeng Wage Entry", "employee": employee, "company": staff.company,
            "operation": card.operation if card else "冒烟测试工序",
            "job_card": job_card, "work_date": today(), "payroll_date": today(),
            "salary_component": salary_component, "quantity": 3, "piece_rate": 2.5,
            "hours": 1, "hourly_rate": 10, "extra_amount": 0.5,
        }).insert()
        assert Decimal(str(wage.total_amount)) == Decimal("18.00")
        wage.submit()
        if card:
            assert wage.work_order == card.work_order
        salary_name = wage.make_additional_salary()
        assert wage.make_additional_salary() == salary_name
        salary = frappe.get_doc("Additional Salary", salary_name)
        assert salary.docstatus == 0 and salary.ref_docname == wage.name
        assert float(salary.amount) == 18.0
        checks = ["wage_calculation", "approval", "draft_earning",
                  "source_reference", "idempotency"]
        if salary_structure:
            from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip

            salary.submit()
            slip = make_salary_slip(salary_structure, employee=employee, posting_date=today())
            slip.insert()
            slip.submit()
            assert slip.docstatus == 1
            assert any(row.additional_salary == salary.name
                       and Decimal(str(row.amount)) == Decimal(str(wage.total_amount))
                       for row in slip.earnings)
            assert frappe.db.count("Salary Slip") == ledger_counts["Salary Slip"] + 1
            checks.extend(["submitted_earning", "salary_slip_wage_amount"])
        else:
            assert frappe.db.count("Salary Slip") == ledger_counts["Salary Slip"]
            checks.append("no_salary_slip")
        assert all(frappe.db.count(dt) == ledger_counts[dt]
                   for dt in ("GL Entry", "Stock Ledger Entry"))
        checks.append("no_ledger_posting")
        return {"passed": True, "checks": checks,
                "business_data": "rolled_back"}
    finally:
        _end_rollback_only(point)
