import frappe
from frappe.model.document import Document

from jifeng_erp.wage_allocation import (
    generate_wages,
    validate_allocation,
    validate_allocation_cancel,
)
from jifeng_erp.wage_workbench import prepare_allocation_amendment
from jifeng_erp.wage_rate_references import guard_cancelled_allocation_snapshot


class JifengWageAllocation(Document):
    def before_insert(self):
        prepare_allocation_amendment(self)

    def validate(self):
        validate_allocation(self)

    def on_submit(self):
        generate_wages(self)

    def before_cancel(self):
        validate_allocation_cancel(self)
        guard_cancelled_allocation_snapshot(self)

    @frappe.whitelist()
    def make_wages(self):
        frappe.only_for(["HR Manager", "System Manager"])
        self.check_permission("write")
        frappe.get_doc("Job Card", self.job_card, for_update=True)
        self.reload()
        return generate_wages(self)
