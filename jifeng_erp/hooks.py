app_name = "jifeng_erp"
app_title = "集沣 ERP 扩展"
app_publisher = "Jifeng"
app_description = "订单、委外回货与成本试算"
app_email = "erp@example.invalid"
app_license = "GPL-3.0-or-later"
required_apps = ["erpnext"]
app_include_css = ["/assets/jifeng_erp/css/jifeng_legacy.css"]
doctype_js = {"Sales Order": "public/js/jifeng_sales_order.js"}

after_install = "jifeng_erp.setup.install"
after_migrate = "jifeng_erp.setup.install"
scheduler_events = {"cron": {"*/15 * * * *": ["jifeng_erp.sync.scheduled_sync"]}}
doc_events = {
    "Sales Order": {
        "on_update_after_submit": "jifeng_erp.workflow.refresh_sales_order_flows",
        "on_cancel": "jifeng_erp.workflow.refresh_sales_order_flows",
    },
    "Work Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Subcontracting Receipt": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Material Request": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Stock Entry": {"validate": "jifeng_erp.setup.validate_flow_links"},
}
