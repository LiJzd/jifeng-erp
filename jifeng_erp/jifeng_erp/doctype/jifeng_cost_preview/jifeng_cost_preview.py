import frappe
from frappe.model.document import Document
from frappe.utils import now

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
        self.other_amount = 0
        self.receipt_cost_sources = None
        self.receipt_cost_loaded_at = None
        self.save()

    @frappe.whitelist()
    def load_receipt_costs(self):
        """Explicitly replace estimated base costs with posted receipt costs."""
        self.check_permission("write")
        frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
        self.reload()
        if not self.flow_order:
            frappe.throw("请先关联流程单")

        from jifeng_erp.workflow import receipt_costs

        result = receipt_costs(self.flow_order)
        if not result["rows"]:
            frappe.throw("没有已提交且关联本流程单的委外收货明细，费用未更改")
        self.material_amount = result["totals"]["material"]
        self.processing_amount = result["totals"]["processing"]
        self.other_amount = result["totals"]["other"]
        self.receipt_cost_sources = "\n".join(sorted({row["receipt"] for row in result["rows"]}))
        self.receipt_cost_loaded_at = now()
        self.save()
        return result
