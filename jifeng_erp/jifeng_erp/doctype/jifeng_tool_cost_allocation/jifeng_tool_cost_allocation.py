import frappe
from frappe.model.document import Document

from jifeng_erp import tool_cost_sources as sources


class JifengToolCostAllocation(Document):
    def before_insert(self):
        sources.before_insert_allocation(self)

    def validate(self):
        sources.validate_allocation(self)

    def before_submit(self):
        sources.before_submit_allocation(self)

    def before_update_after_submit(self):
        sources.prevent_allocation_update(self)

    def before_cancel(self):
        sources.before_cancel_allocation(self)

    def on_trash(self):
        sources.prevent_allocation_delete(self)

    def before_rename(self, *args, **kwargs):
        sources.prevent_allocation_rename(self, *args, **kwargs)

    def validate_share(self, share):
        sources.prevent_allocation_share(self, share)

    def onload(self):
        if not self.is_new():
            sources._native_guard(self)

    @frappe.whitelist()
    def settlement_details(self):
        return sources.get_tool_cost_allocation(self.name, self.modified)

    @frappe.whitelist()
    def cancel_with_reason(self, reason):
        return sources.cancel_tool_cost_allocation(self.name, self.modified, reason)
