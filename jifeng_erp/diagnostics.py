"""Bench-only checks; never include secrets in returned values."""
import frappe


def check():
    frappe.only_for("System Manager")
    settings = frappe.get_single("Jushuitan Settings")
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
    frappe.db.savepoint(point)
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
                "receipt_cost_query", "stock_query", "sku_upsert", "order_upsert", "separate_lines", "source_change_guard",
                "print_render", "no_ledger_posting"],
                "business_data": "rolled_back"}
    finally:
        frappe.db.rollback(save_point=point)
