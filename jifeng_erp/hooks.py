app_name = "jifeng_erp"
app_title = "集沣 ERP 扩展"
app_publisher = "Jifeng"
app_description = "订单、委外回货与成本试算"
app_email = "erp@example.invalid"
app_license = "GPL-3.0-or-later"
required_apps = ["erpnext"]
app_include_css = ["/assets/jifeng_erp/css/jifeng_legacy.css"]

after_install = "jifeng_erp.setup.install"
after_migrate = "jifeng_erp.setup.install"
scheduler_events = {"cron": {"*/15 * * * *": ["jifeng_erp.sync.scheduled_sync"]}}
doc_events = {
    "Subcontracting Receipt": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Material Request": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Stock Entry": {"validate": "jifeng_erp.setup.validate_flow_links"},
}
