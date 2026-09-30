from decimal import Decimal

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

        self._validate_batch_sources()

        if self.flow_order:
            flow = frappe.get_doc("Jifeng Flow Order", self.flow_order)
            flow.check_permission("read")
            if flow.company != self.company or flow.item_code != self.item_code:
                frappe.throw("试算单与流程单的公司、商品必须一致")
            self.flow_order_no = flow.name
            if flow.sales_order:
                if self.sales_order and self.sales_order != flow.sales_order:
                    frappe.throw("试算单销售订单与流程单来源不一致")
                self.sales_order = flow.sales_order

    def _validate_batch_sources(self):
        seen = set()
        for row in self.batches:
            if not row.source_receipt and not row.source_receipt_item:
                continue
            if not self.flow_order or not row.source_receipt or not row.source_receipt_item:
                frappe.throw("已关联的回货批次必须包含流程单、委外收货单和明细行")
            if row.source_receipt_item in seen:
                frappe.throw("同一委外收货明细不能重复带入试算")
            seen.add(row.source_receipt_item)
            receipt = frappe.get_doc("Subcontracting Receipt", row.source_receipt)
            receipt.check_permission("read")
            if receipt.docstatus != 1 or receipt.company != self.company or receipt.is_return:
                frappe.throw("来源委外收货单未提交、已退货或公司不一致")
            if frappe.db.exists("Subcontracting Receipt", {
                "return_against": receipt.name, "docstatus": 1,
            }):
                frappe.throw("来源委外收货单已有退货，请人工核对并删除来源批次后重新录入净回货数量")
            detail = frappe.db.get_value("Subcontracting Receipt Item", row.source_receipt_item,
                                         ["parent", "item_code", "jf_flow_order", "qty", "rejected_qty", "process_loss_qty"], as_dict=True)
            source_qty = sum((Decimal(str(detail.get(field) or 0)) for field in (
                "qty", "rejected_qty", "process_loss_qty",
            )), Decimal(0)) if detail else Decimal(0)
            if not detail or (detail.parent, detail.item_code, detail.jf_flow_order) != (
                receipt.name, self.item_code, self.flow_order,
            ) or source_qty != Decimal(str(row.received_qty or 0)):
                frappe.throw("来源委外收货明细与试算批次的商品、流程单或数量不一致")

    @frappe.whitelist()
    def load_flow_costs(self):
        self.check_permission("write")
        flow = frappe.get_doc("Jifeng Flow Order", self.flow_order)
        flow.check_permission("read")
        if self.batches:
            frappe.throw("已有回货批次，请核对费用后手动调整，避免覆盖当前试算")
        self.company, self.item_code, self.planned_qty = flow.company, flow.item_code, flow.planned_qty
        self.flow_order_no, self.external_order_no = flow.name, flow.online_order_id
        self.sales_order = flow.sales_order
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

    @frappe.whitelist()
    def load_posted_costs(self):
        """Explicitly refresh trial costs from posted material issues and receipts."""
        self.check_permission("write")
        frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
        self.reload()
        if not self.flow_order:
            frappe.throw("请先关联流程单")

        from jifeng_erp.workflow import posted_flow_costs

        result = posted_flow_costs(self.flow_order)
        if not result["issues"] and not result["receipts"] and not self.receipt_cost_loaded_at:
            frappe.throw("没有已提交且关联本流程单的物料出库或委外收货，费用未更改")
        self.material_amount = result["totals"]["material"]
        self.processing_amount = result["totals"]["processing"]
        self.other_amount = result["totals"]["other"]
        sources = {row["issue"] for row in result["issues"]} | {
            row["receipt"] for row in result["receipts"]
        }
        self.receipt_cost_sources = "\n".join(sorted(sources)) or None
        self.receipt_cost_loaded_at = now()
        self.save()
        return result
