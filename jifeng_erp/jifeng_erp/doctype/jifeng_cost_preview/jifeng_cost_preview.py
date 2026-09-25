import frappe
from frappe.model.document import Document

from jifeng_erp.costing import CostingError, calculate


class JifengCostPreview(Document):
    def validate(self):
        try:
            result = calculate(
                self.planned_qty,
                self.material_amount,
                self.processing_amount,
                self.other_amount,
                [row.as_dict() for row in self.batches],
            )
        except CostingError as exc:
            frappe.throw(str(exc))

        for field in (
            "good_qty", "rework_open_qty", "lost_qty", "unreceived_qty",
            "total_cost", "estimated_unit_cost", "unallocated_cost", "is_complete",
        ):
            setattr(self, field, result[field])
        for row, allocation in zip(self.batches, result["allocations"], strict=True):
            row.estimated_cost = allocation["estimated_cost"]
