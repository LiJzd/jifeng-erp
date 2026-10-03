from frappe.model.document import Document

from jifeng_erp import tool_cost_sources as sources


class JifengToolCostAllocationItem(Document):
    def validate_share(self, share):
        sources.prevent_allocation_share(self, share)
