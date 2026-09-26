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
            "good_qty", "rework_open_qty", "pending_qty", "lost_qty", "unreceived_qty",
            "total_cost", "estimated_unit_cost", "unallocated_cost", "is_complete",
        ):
            setattr(self, field, result[field])
        for row, allocation in zip(self.batches, result["allocations"], strict=True):
            row.estimated_cost = allocation["estimated_cost"]

        if self.flow_order:
            flow = frappe.get_doc("Jifeng Flow Order", self.flow_order)
            flow.check_permission("read")
            if flow.company != self.company or flow.item_code != self.item_code:
                frappe.throw("试算单与流程单的公司、商品必须一致")
            self.flow_order_no = flow.name

    @frappe.whitelist()
    def load_flow_costs(self):
        self.check_permission("write")
        flow = frappe.get_doc("Jifeng Flow Order", self.flow_order)
        flow.check_permission("read")
        if self.batches:
            frappe.throw("已有回货批次，请核对费用后手动调整，避免覆盖当前试算")
        self.company, self.item_code, self.planned_qty = flow.company, flow.item_code, flow.planned_qty
        self.flow_order_no, self.external_order_no = flow.name, flow.online_order_id
        self.material_amount, self.processing_amount = flow.material_amount, flow.processing_amount
        self.save()
