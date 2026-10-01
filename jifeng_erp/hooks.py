app_name = "jifeng_erp"
app_title = "集沣 ERP 扩展"
app_publisher = "Jifeng"
app_description = "订单、委外回货与成本试算"
app_email = "erp@example.invalid"
app_license = "GPL-3.0-or-later"
required_apps = ["erpnext", "hrms"]
app_include_css = ["/assets/jifeng_erp/css/jifeng_legacy.css",
                   "/assets/jifeng_erp/css/jifeng_product_dictionary.css",
                   "/assets/jifeng_erp/css/jifeng_product_category.css",
                   "/assets/jifeng_erp/css/jifeng_product_archive.css",
                   "/assets/jifeng_erp/css/jifeng_inventory_catalog.css",
                   "/assets/jifeng_erp/css/jifeng_partner_archive.css",
                   "/assets/jifeng_erp/css/jifeng_tool_archive.css"]
app_include_js = ["/assets/jifeng_erp/js/jifeng_classic_bridge.js", "/assets/jifeng_erp/js/jifeng_hr_dictionary.js",
                  "/assets/jifeng_erp/js/jifeng_personnel_archive.js",
                  "/assets/jifeng_erp/js/jifeng_inventory_archive.js",
                  "/assets/jifeng_erp/js/jifeng_inventory_catalog.js",
                  "/assets/jifeng_erp/js/jifeng_partner_archive.js",
                  "/assets/jifeng_erp/js/jifeng_product_dictionary.js",
                  "/assets/jifeng_erp/js/jifeng_product_category.js",
                  "/assets/jifeng_erp/js/jifeng_product_archive.js",
                  "/assets/jifeng_erp/js/jifeng_tool_archive.js"]
before_request = "jifeng_erp.session_init.initialize_csrf"
doctype_js = {
    "Sales Order": "public/js/jifeng_sales_order.js",
    "Production Plan": "public/js/jifeng_production_instruction.js",
    "Work Order": "public/js/jifeng_work_order.js",
    "Employee Promotion": "public/js/jifeng_personnel_change.js",
    "Employee Transfer": "public/js/jifeng_personnel_change.js",
    "Employee Separation": "public/js/jifeng_employee_separation.js",
}
override_whitelisted_methods = {
    "hrms.hr.utils.get_employee_field_property": "jifeng_erp.personnel_ui.get_employee_field_property",
}
has_permission = {
    "Employee Promotion": "jifeng_erp.personnel_changes.personnel_change_has_permission",
    "Employee Transfer": "jifeng_erp.personnel_changes.personnel_change_has_permission",
    "Employee Separation": "jifeng_erp.personnel_separation.separation_has_permission",
}
permission_query_conditions = {
    "Employee Promotion": "jifeng_erp.personnel_changes.promotion_query_conditions",
    "Employee Transfer": "jifeng_erp.personnel_changes.transfer_query_conditions",
    "Employee Separation": "jifeng_erp.personnel_separation.separation_query_conditions",
}
extend_doctype_class = {
    "Stock Entry": ["jifeng_erp.material_issue_locking.MaterialIssueLockingMixin"],
    "Production Plan": ["jifeng_erp.production_instruction.ProductionInstructionMixin"],
    "Purchase Receipt": ["jifeng_erp.purchase_returns.PurchaseReceiptMixin"],
    "Employee Promotion": ["jifeng_erp.personnel_changes.EmployeePromotionMixin"],
    "Employee Transfer": ["jifeng_erp.personnel_changes.EmployeeTransferMixin"],
    "Employee Separation": ["jifeng_erp.personnel_separation.EmployeeSeparationMixin"],
    "Employee": ["jifeng_erp.personnel_separation.SeparationEmployeeMixin",
                 "jifeng_erp.personnel_archive.ClassicPersonnelEmployeeMixin"],
    "Project": ["jifeng_erp.personnel_separation.SeparationProjectMixin"],
}

after_install = "jifeng_erp.setup.install"
after_migrate = "jifeng_erp.setup.install"
scheduler_events = {"cron": {"*/15 * * * *": ["jifeng_erp.sync.scheduled_sync"]}}
doc_events = {
    "Additional Salary": {"validate": "jifeng_erp.payroll.validate_wage_additional_salary"},
    "Journal Entry": {
        "before_validate": "jifeng_erp.accounting.lock_expense_settlement_references",
        "validate": "jifeng_erp.accounting.validate_expense_journal_entry",
        "before_update_after_submit": "jifeng_erp.accounting.validate_expense_journal_entry",
        "before_cancel": "jifeng_erp.accounting.validate_expense_journal_entry_cancel",
    },
    "Payment Entry": {"before_validate": "jifeng_erp.accounting.lock_expense_settlement_references"},
    "Job Card": {
        "before_validate": "jifeng_erp.production_department.validate_job_card_department",
        "validate": "jifeng_erp.setup.validate_flow_links",
        "before_cancel": "jifeng_erp.payroll.validate_job_card_cancel",
        "before_update_after_submit": "jifeng_erp.wage_allocation.guard_job_card_allocation",
    },
    "Employee": {"on_update": "jifeng_erp.jifeng_erp.doctype.jifeng_employee_supplement.jifeng_employee_supplement.refresh_employee_supplement"},
    "Sales Order": {
        "on_update_after_submit": "jifeng_erp.workflow.refresh_sales_order_flows",
        "on_cancel": "jifeng_erp.workflow.refresh_sales_order_flows",
    },
    "Delivery Note": {"before_validate": "jifeng_erp.fulfillment.validate_fulfillment_flow"},
    "Sales Invoice": {"before_validate": "jifeng_erp.fulfillment.validate_fulfillment_flow"},
    # Native Production Plan creates Work Orders with ignore_validate=True.
    # before_validate still runs and must preserve flow identity and quantity limits.
    "Work Order": {"before_validate": ["jifeng_erp.setup.validate_flow_links",
                                       "jifeng_erp.production_department.validate_work_order_departments"]},
    "Workstation": {"validate": "jifeng_erp.production_department.validate_default_department"},
    "Operation": {"validate": "jifeng_erp.production_department.validate_default_department"},
    "Production Plan": {"validate": "jifeng_erp.production_instruction.validate_plan"},
    "Jifeng Flow Order": {"validate": "jifeng_erp.production_instruction.validate_flow_allocation"},
    "Subcontracting Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Subcontracting Receipt": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Material Request": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Receipt": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Invoice": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Stock Entry": {"validate": "jifeng_erp.setup.validate_flow_links"},
}
