app_name = "jifeng_erp"
app_title = "集沣 ERP 扩展"
app_publisher = "Jifeng"
app_description = "订单、委外回货与成本试算"
app_email = "erp@example.invalid"
app_license = "GPL-3.0-or-later"
required_apps = ["erpnext", "hrms"]
app_include_css = ["/assets/jifeng_erp/css/jifeng_legacy.css"]
doctype_js = {"Sales Order": "public/js/jifeng_sales_order.js"}

after_install = "jifeng_erp.setup.install"
after_migrate = "jifeng_erp.setup.install"
scheduler_events = {"cron": {"*/15 * * * *": ["jifeng_erp.sync.scheduled_sync"]}}
doc_events = {
    "Additional Salary": {"validate": "jifeng_erp.payroll.validate_wage_additional_salary"},
    "Journal Entry": {
        "validate": "jifeng_erp.accounting.validate_expense_journal_entry",
        "before_cancel": "jifeng_erp.accounting.validate_expense_journal_entry_cancel",
    },
    "Job Card": {"before_cancel": "jifeng_erp.payroll.validate_job_card_cancel"},
    "Employee": {"on_update": "jifeng_erp.jifeng_erp.doctype.jifeng_employee_supplement.jifeng_employee_supplement.refresh_employee_supplement"},
    "Sales Order": {
        "on_update_after_submit": "jifeng_erp.workflow.refresh_sales_order_flows",
        "on_cancel": "jifeng_erp.workflow.refresh_sales_order_flows",
    },
    "Work Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Subcontracting Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Subcontracting Receipt": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Material Request": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Receipt": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Invoice": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Stock Entry": {"validate": "jifeng_erp.setup.validate_flow_links"},
}
