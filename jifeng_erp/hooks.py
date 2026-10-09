app_name = "jifeng_erp"
app_title = "集沣 ERP 扩展"
app_publisher = "Jifeng"
app_description = "订单、委外回货与成本试算"
app_email = "erp@example.invalid"
app_license = "GPL-3.0-or-later"
required_apps = ["erpnext", "hrms"]
jinja = {"methods": ["jifeng_erp.flow_printing.jifeng_flow_sheet",
                     "jifeng_erp.flow_printing.jifeng_production_instruction"]}
app_include_css = ["/assets/jifeng_erp/css/jifeng_business_operation_log.css",
                   "/assets/jifeng_erp/css/jifeng_system_information.css",
                   "/assets/jifeng_erp/css/jifeng_legacy.css",
                   "/assets/jifeng_erp/css/jifeng_salary_history.css",
                   "/assets/jifeng_erp/css/jifeng_salary_analysis.css",
                   "/assets/jifeng_erp/css/jifeng_order_operations.css",
                   "/assets/jifeng_erp/css/jifeng_product_dictionary.css",
                   "/assets/jifeng_erp/css/jifeng_product_category.css",
                   "/assets/jifeng_erp/css/jifeng_product_archive.css",
                   "/assets/jifeng_erp/css/jifeng_inventory_catalog.css",
                   "/assets/jifeng_erp/css/jifeng_inventory_tracking.css",
                   "/assets/jifeng_erp/css/jifeng_partner_archive.css",
                   "/assets/jifeng_erp/css/jifeng_customer_archive.css",
                   "/assets/jifeng_erp/css/jifeng_tool_costs.css",
                   "/assets/jifeng_erp/css/jifeng_tool_archive.css",
                   "/assets/jifeng_erp/css/jifeng_procurement_fulfillment.css",
                   "/assets/jifeng_erp/css/jifeng_supplier_payables.css",
                   "/assets/jifeng_erp/css/jifeng_finance_dictionary.css",
                   "/assets/jifeng_erp/css/jifeng_production_instruction_list.css",
                   "/assets/jifeng_erp/css/jifeng_subcontracting_reconciliation.css",
                   "/assets/jifeng_erp/css/jifeng_subcontracting_reversal.css",
                   "/assets/jifeng_erp/css/jifeng_modern.css"]
app_include_js = ["/assets/jifeng_erp/js/jifeng_classic_bridge.js", "/assets/jifeng_erp/js/jifeng_hr_dictionary.js",
                  "/assets/jifeng_erp/js/jifeng_source_reports.js",
                  "/assets/jifeng_erp/js/jifeng_personnel_archive.js",
                  "/assets/jifeng_erp/js/jifeng_inventory_archive.js",
                  "/assets/jifeng_erp/js/jifeng_inventory_catalog.js",
                  "/assets/jifeng_erp/js/jifeng_inventory_tracking.js",
                  "/assets/jifeng_erp/js/jifeng_inventory_archive_import.js",
                  "/assets/jifeng_erp/js/jifeng_inventory_archive_list.js",
                  "/assets/jifeng_erp/js/jifeng_partner_archive.js",
                  "/assets/jifeng_erp/js/jifeng_product_dictionary.js",
                  "/assets/jifeng_erp/js/jifeng_customer_type.js",
                  "/assets/jifeng_erp/js/jifeng_order_dictionary.js",
                  "/assets/jifeng_erp/js/jifeng_plating_color.js",
                  "/assets/jifeng_erp/js/jifeng_order_operations.js",
                  "/assets/jifeng_erp/js/jifeng_customer_archive.js",
                  "/assets/jifeng_erp/js/jifeng_product_category.js",
                  "/assets/jifeng_erp/js/jifeng_product_archive.js",
                  "/assets/jifeng_erp/js/jifeng_tool_costs.js",
                  "/assets/jifeng_erp/js/jifeng_tool_archive.js",
                  "/assets/jifeng_erp/js/jifeng_wage_rates.js",
                  "/assets/jifeng_erp/js/jifeng_temporary_workers.js",
                  "/assets/jifeng_erp/js/jifeng_salary_history.js",
                  "/assets/jifeng_erp/js/jifeng_salary_analysis.js",
                  "/assets/jifeng_erp/js/jifeng_procurement_fulfillment.js",
                  "/assets/jifeng_erp/js/jifeng_sales_fulfillment.js",
                  "/assets/jifeng_erp/js/jifeng_supplier_payables.js",
                  "/assets/jifeng_erp/js/jifeng_finance_dictionary.js",
                  "/assets/jifeng_erp/js/jifeng_system_information.js",
                  "/assets/jifeng_erp/js/jifeng_business_operation_log.js",
                  "/assets/jifeng_erp/js/jifeng_production_instruction_list.js",
                  "/assets/jifeng_erp/js/jifeng_subcontracting_reconciliation.js",
                  "/assets/jifeng_erp/js/jifeng_subcontracting_reversal.js",
                  "/assets/jifeng_erp/js/jifeng_workspace_core.js",
                  "/assets/jifeng_erp/js/jifeng_modern_workspace.js"]


def _content_versioned_assets(paths):
    # Desk includes these files directly; the static server caches them for days.
    from hashlib import sha256
    from pathlib import Path

    public = Path(__file__).parent / "public"
    return [path + "?v=" + sha256((public / path.removeprefix("/assets/jifeng_erp/")).read_bytes()).hexdigest()[:16]
            for path in paths]


app_include_css = _content_versioned_assets(app_include_css)
app_include_js = _content_versioned_assets(app_include_js)
before_request = "jifeng_erp.session_init.initialize_csrf"
after_request = "jifeng_erp.sample_source_report_files.after_report_file_request"
before_job = "jifeng_erp.sample_source_report_output.before_job"
doctype_js = {
	"Payroll Entry": "public/js/jifeng_payroll_entry.js",
	"Stock Entry": "public/js/jifeng_material_returns.js",
    "Jifeng Sample Order": "public/js/jifeng_sample_order.js",
    "Quotation": "public/js/jifeng_quotation_sources.js",
    "Sales Order": "public/js/jifeng_sales_order.js",
    "Production Plan": "public/js/jifeng_production_instruction.js",
    "Work Order": "public/js/jifeng_work_order.js",
    "Job Card": "public/js/jifeng_production_loss.js",
    "Employee Promotion": "public/js/jifeng_personnel_change.js",
    "Employee Transfer": "public/js/jifeng_personnel_change.js",
    "Employee Separation": "public/js/jifeng_employee_separation.js",
}
override_whitelisted_methods = {
    "erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt.make_subcontract_return":
        "jifeng_erp.subcontracting_returns.make_subcontract_return",
    "erpnext.subcontracting.doctype.subcontracting_receipt.subcontracting_receipt.make_subcontract_return_against_rejected_warehouse":
        "jifeng_erp.subcontracting_returns.make_subcontract_return_against_rejected_warehouse",
    "hrms.hr.utils.get_employee_field_property": "jifeng_erp.personnel_ui.get_employee_field_property",
    "frappe.desk.form.linked_with.get": "jifeng_erp.sample_source_queries.get",
    "frappe.desk.form.linked_with.get_submitted_linked_docs":
        "jifeng_erp.sample_source_queries.get_submitted_linked_docs",
    "frappe.desk.query_report.run": "jifeng_erp.sample_source_report_output.run",
    "frappe.desk.query_report.export_query": "jifeng_erp.sample_source_report_output.export_query",
    "frappe.core.doctype.prepared_report.prepared_report.enqueue_json_to_csv_conversion":
        "jifeng_erp.sample_source_report_output.enqueue_json_to_csv_conversion",
    "frappe.core.doctype.prepared_report.prepared_report.get_reports_in_queued_state":
        "jifeng_erp.sample_source_prepared_reports.get_reports_in_queued_state",
}
has_permission = {
    "Prepared Report": "jifeng_erp.sample_source_prepared_reports.prepared_report_has_permission",
    "Jifeng Report Artifact": "jifeng_erp.sample_source_report_artifacts.artifact_has_permission",
    "Jifeng Tool Cost Allocation": "jifeng_erp.tool_cost_sources.tool_cost_allocation_has_permission",
    "Jifeng Tool Cost Allocation Item": "jifeng_erp.tool_cost_sources.tool_cost_allocation_item_has_permission",
    "Employee Promotion": "jifeng_erp.personnel_changes.personnel_change_has_permission",
    "Employee Transfer": "jifeng_erp.personnel_changes.personnel_change_has_permission",
    "Employee Separation": "jifeng_erp.personnel_separation.separation_has_permission",
}
permission_query_conditions = {
    "Prepared Report": "jifeng_erp.sample_source_prepared_reports.prepared_report_query_conditions",
    "File": "jifeng_erp.sample_source_report_files.report_file_query_conditions",
    "Jifeng Report Artifact": "jifeng_erp.sample_source_report_artifacts.artifact_query_conditions",
    "Jifeng Tool Cost Allocation": "jifeng_erp.tool_cost_sources.tool_cost_allocation_query_conditions",
    "Jifeng Tool Cost Allocation Item": "jifeng_erp.tool_cost_sources.tool_cost_allocation_item_query_conditions",
    "Employee Promotion": "jifeng_erp.personnel_changes.promotion_query_conditions",
    "Employee Transfer": "jifeng_erp.personnel_changes.transfer_query_conditions",
    "Employee Separation": "jifeng_erp.personnel_separation.separation_query_conditions",
}
extend_doctype_class = {
    "HR Telemetry Milestone": ["jifeng_erp.hr_telemetry_compat.HRTelemetryMilestoneMixin"],
    "Prepared Report": ["jifeng_erp.sample_source_prepared_reports.SampleSourcePreparedReportMixin"],
    "File": ["jifeng_erp.sample_source_report_files.SampleSourceReportFileMixin"],
    "Report": ["jifeng_erp.sample_source_reports.SampleSourceReportMixin"],
    "Stock Entry": ["jifeng_erp.material_issue_locking.MaterialIssueLockingMixin",
                    "jifeng_erp.production_loss_progress.ProductionLossStockEntryMixin"],
    "Job Card": ["jifeng_erp.wage_allocation.WageSourceJobCardMixin",
                 "jifeng_erp.subcontracting_returns.SubcontractingJobCardQtyMixin",
                 "jifeng_erp.production_loss_progress.ProductionLossJobCardMixin"],
    "Work Order": ["jifeng_erp.production_loss_progress.ProductionLossWorkOrderMixin"],
    "Production Plan": ["jifeng_erp.production_instruction.ProductionInstructionMixin"],
    "Purchase Receipt": ["jifeng_erp.purchase_returns.PurchaseReceiptMixin"],
    "Purchase Order": ["jifeng_erp.subcontract_stage.PurchaseOrderStageMixin"],
    "Subcontracting Receipt": ["jifeng_erp.subcontracting_returns.SubcontractingReceiptReturnMixin"],
    "Serial and Batch Bundle": ["jifeng_erp.subcontracting_returns.SubcontractingReceiptMaterialReturnBundleMixin",
                                "jifeng_erp.material_returns.MaterialReturnBundleMixin"],
    "Employee Promotion": ["jifeng_erp.personnel_changes.EmployeePromotionMixin"],
    "Employee Transfer": ["jifeng_erp.personnel_changes.EmployeeTransferMixin"],
    "Employee Separation": ["jifeng_erp.personnel_separation.EmployeeSeparationMixin"],
    "Employee": ["jifeng_erp.personnel_separation.SeparationEmployeeMixin",
                 "jifeng_erp.personnel_archive.ClassicPersonnelEmployeeMixin"],
    "Project": ["jifeng_erp.personnel_separation.SeparationProjectMixin"],
}

# Frappe's shares can override permission hooks and list conditions. Sample
# source records use a final controller guard plus the serialized no-share
# binding invariant; ordinary native records retain their existing permissions.
_sample_permission_parents = (
    "Jifeng Sample Order", "Quotation", "Sales Order", "Jifeng Flow Order", "Work Order",
)
_sample_permission_children = (
    "Jifeng Sample Order Item", "Quotation Item", "Sales Order Item",
    "Pricing Rule Detail", "Sales Taxes and Charges", "Payment Schedule",
    "Quotation Lost Reason Detail", "Packed Item", "Competitor Detail",
    "Item Wise Tax Detail", "Sales Team", "Work Order Item", "Work Order Operation",
    "Work Order Additional Item", "Jifeng Material Line", "Jifeng Operation Line",
)
for _doctype in _sample_permission_parents + _sample_permission_children:
    has_permission[_doctype] = "jifeng_erp.sample_source_permissions.sample_source_has_permission"
    extend_doctype_class.setdefault(_doctype, []).append(
        "jifeng_erp.sample_source_permissions.SampleSourcePermissionMixin"
    )
for _doctype in _sample_permission_parents:
    permission_query_conditions[_doctype] = "jifeng_erp.sample_source_permissions.sample_source_query_conditions"
for _doctype in _sample_permission_children:
    permission_query_conditions[_doctype] = "jifeng_erp.sample_source_permissions.sample_child_query_conditions"

after_install = "jifeng_erp.setup.install"
after_migrate = "jifeng_erp.setup.install"
scheduler_events = {"cron": {"*/15 * * * *": ["jifeng_erp.sync.scheduled_sync"]}}
doc_events = {
    "DocShare": {"before_validate": ["jifeng_erp.tool_cost_sources.prevent_tool_cost_docshare",
                                     "jifeng_erp.sample_source_permissions.before_validate_docshare",
                                     "jifeng_erp.sample_source_prepared_reports.before_validate_docshare",
                                     "jifeng_erp.sample_source_report_files.prevent_report_file_docshare",
                                     "jifeng_erp.sample_source_report_artifacts.before_validate_artifact_share"]},
    "Prepared Report": {
        "before_validate": "jifeng_erp.sample_source_prepared_reports.before_validate_prepared_report",
        "validate_share": "jifeng_erp.sample_source_prepared_reports.validate_prepared_report_share",
        "on_trash": "jifeng_erp.ledger_report_cache.prevent_prepared_delete",
        "before_rename": "jifeng_erp.ledger_report_cache.prevent_prepared_delete",
    },
    "Report": {
        "before_validate": "jifeng_erp.ledger_report_cache.before_validate_report",
        "on_trash": "jifeng_erp.ledger_report_cache.prevent_report_delete_or_rename",
        "before_rename": "jifeng_erp.ledger_report_cache.prevent_report_delete_or_rename",
    },
    "Jifeng Sample Order": {"before_validate": "jifeng_erp.sample_source_permissions.before_validate_source_binding"},
    "Additional Salary": {"validate": "jifeng_erp.payroll.validate_wage_additional_salary"},
    "Salary Slip": {"validate": "jifeng_erp.payroll.validate_salary_slip_wage_sources",
                    "before_submit": "jifeng_erp.payroll_completion.guard_salary_slip_submit"},
    "Journal Entry": {
        "before_validate": "jifeng_erp.accounting.lock_expense_settlement_references",
        "validate": "jifeng_erp.accounting.validate_expense_journal_entry",
        "before_update_after_submit": "jifeng_erp.accounting.validate_expense_journal_entry",
        "before_cancel": "jifeng_erp.accounting.validate_expense_journal_entry_cancel",
    },
    "Payment Entry": {"before_validate": "jifeng_erp.accounting.lock_expense_settlement_references"},
    "Job Card": {
        "before_validate": "jifeng_erp.production_department.validate_job_card_department",
        "validate": ["jifeng_erp.setup.validate_flow_links", "jifeng_erp.production_loss.validate_loss_source"],
        "before_cancel": ["jifeng_erp.payroll.validate_job_card_cancel",
                          "jifeng_erp.production_loss.before_cancel_source"],
        "before_update_after_submit": ["jifeng_erp.wage_allocation.guard_job_card_allocation",
                                       "jifeng_erp.production_loss.validate_loss_source"],
    },
    "Employee": {"on_update": "jifeng_erp.jifeng_erp.doctype.jifeng_employee_supplement.jifeng_employee_supplement.refresh_employee_supplement"},
    "Sales Order": {
        "before_validate": ["jifeng_erp.sample_quotations.prepare_sample_sales_order",
                            "jifeng_erp.sample_source_permissions.before_validate_source_binding"],
        "validate": "jifeng_erp.sample_quotations.validate_sample_sales_order",
        "onload": "jifeng_erp.sample_quotations.load_sample_sales_order",
        "before_update_after_submit": "jifeng_erp.sample_quotations.guard_sample_sales_order_history",
        "before_cancel": "jifeng_erp.sample_quotations.guard_sample_sales_order_history",
        "on_update_after_submit": "jifeng_erp.workflow.refresh_sales_order_flows",
        "on_cancel": "jifeng_erp.workflow.refresh_sales_order_flows",
    },
    "Delivery Note": {"before_validate": "jifeng_erp.fulfillment.validate_fulfillment_flow",
                      "on_submit": "jifeng_erp.flow_reposting.schedule_flow_stock_reposting",
                      "on_cancel": "jifeng_erp.flow_reposting.schedule_flow_stock_reposting"},
    "Sales Invoice": {"before_validate": "jifeng_erp.fulfillment.validate_fulfillment_flow",
                      "on_submit": "jifeng_erp.flow_reposting.schedule_flow_stock_reposting",
                      "on_cancel": "jifeng_erp.flow_reposting.schedule_flow_stock_reposting"},
    # Native Production Plan creates Work Orders with ignore_validate=True.
    # before_validate still runs and must preserve flow identity and quantity limits.
    "Work Order": {"before_validate": ["jifeng_erp.setup.validate_flow_links",
                                       "jifeng_erp.production_department.validate_work_order_departments",
                                       "jifeng_erp.sample_orders.validate_sample_work_order",
                                       "jifeng_erp.sample_source_permissions.before_validate_source_binding"],
                   "validate": "jifeng_erp.production_loss.validate_loss_source",
                   "before_update_after_submit": ["jifeng_erp.sample_orders.validate_sample_work_order",
                                                  "jifeng_erp.production_loss.validate_loss_source"],
                   "before_cancel": ["jifeng_erp.sample_orders.validate_sample_work_order",
                                     "jifeng_erp.production_loss.before_cancel_source"],
                   "on_trash": "jifeng_erp.sample_orders.prevent_sample_work_order_delete",
                   "before_rename": "jifeng_erp.sample_orders.prevent_sample_work_order_rename"},
    "Quotation": {"before_validate": ["jifeng_erp.sample_quotations.validate_sample_quotation",
                                      "jifeng_erp.sample_source_permissions.before_validate_source_binding"],
                  "validate": "jifeng_erp.sample_quotations.validate_sample_quotation",
                  "before_update_after_submit": "jifeng_erp.sample_quotations.validate_sample_quotation",
                  "onload": "jifeng_erp.sample_quotations.load_sample_quotation",
                  "before_cancel": "jifeng_erp.sample_quotations.cancel_sample_quotation"},
    "Workstation": {"validate": "jifeng_erp.production_department.validate_default_department"},
    "Operation": {"validate": "jifeng_erp.production_department.validate_default_department"},
    "Production Plan": {"validate": "jifeng_erp.production_instruction.validate_plan"},
    "Jifeng Flow Order": {"before_validate": ["jifeng_erp.sample_quotations.validate_sample_flow",
                                            "jifeng_erp.sample_source_permissions.before_validate_source_binding"],
                          "validate": "jifeng_erp.production_instruction.validate_flow_allocation"},
    "Subcontracting Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Subcontracting Receipt": {"validate": "jifeng_erp.setup.validate_flow_links",
                              "on_submit": "jifeng_erp.flow_reposting.schedule_flow_stock_reposting",
                              "on_cancel": "jifeng_erp.flow_reposting.schedule_flow_stock_reposting"},
    "Material Request": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Order": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Receipt": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Purchase Invoice": {"validate": "jifeng_erp.setup.validate_flow_links"},
    "Stock Entry": {"validate": ["jifeng_erp.setup.validate_flow_links",
                                 "jifeng_erp.production_loss.validate_loss_entry"],
                    "before_cancel": "jifeng_erp.production_loss.before_cancel_loss_entry",
                    "on_submit": ["jifeng_erp.production_loss.update_loss_progress",
                                  "jifeng_erp.flow_reposting.schedule_flow_stock_reposting"],
                    "on_cancel": ["jifeng_erp.production_loss.update_loss_progress",
                                  "jifeng_erp.flow_reposting.schedule_flow_stock_reposting"]},
}
for _doctype in _sample_permission_parents + _sample_permission_children:
    doc_events.setdefault(_doctype, {})["validate_share"] = (
        "jifeng_erp.sample_source_permissions.validate_sample_source_share"
    )
