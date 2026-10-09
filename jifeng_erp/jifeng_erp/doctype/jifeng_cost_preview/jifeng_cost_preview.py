from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import now

from jifeng_erp.costing import CostingError, calculate


FLOW_ESTIMATE = "流程估算"
RECEIPT_ACTUAL = "委外回货实际"
POSTED_ACTUAL = "流程已过账实际"
_COST_BASIS_SELECTION = object()


class JifengCostPreview(Document):
    def validate(self):
        self._validate_cost_basis()
        # A new trial or a recalculation shares the Flow lock used when its
        # inventory-unit plan is confirmed. Never divide by a sales-unit qty.
        if self.flow_order:
            from jifeng_erp.workflow import stock_plan_quantity

            flow = frappe.get_doc("Jifeng Flow Order", self.flow_order, for_update=True)
            flow.check_permission("read")
            planned_qty = stock_plan_quantity(flow)
            if flow.company != self.company or flow.item_code != self.item_code:
                frappe.throw("试算单与流程单的公司、商品必须一致")
            # Linked batches and costs cover this Flow's inventory-unit plan.
            # A copied sales-unit quantity (or a stale client input) must not
            # replace that denominator. Standalone manual trials remain manual.
            self.planned_qty = planned_qty
        self._refresh_actual_costs()
        additional_input_qty = self._validate_batch_sources()
        try:
            result = calculate(
                Decimal(str(self.planned_qty)) + additional_input_qty if additional_input_qty else self.planned_qty,
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
            if stock_plan_quantity(flow) != self.planned_qty:
                frappe.throw("流程单计划库存数量已变化，请重新读取后核算")
            self.flow_order_no = flow.name
            if flow.sales_order:
                if self.sales_order and self.sales_order != flow.sales_order:
                    frappe.throw("试算单销售订单与流程单来源不一致")
                self.sales_order = flow.sales_order

    def _validate_cost_basis(self):
        basis = self.get("cost_basis") or ""
        before = self.get_doc_before_save()
        selected = getattr(self, "_cost_basis_selection", None) is _COST_BASIS_SELECTION
        if not selected:
            old_basis = (before.get("cost_basis") or "") if before else ""
            # The old saved timestamp remains authoritative even if a client
            # clears it or sends a new readonly field value with its document.
            if ((before and before.get("receipt_cost_loaded_at") and not old_basis)
                    or (not before and self.get("receipt_cost_loaded_at") and not basis)):
                frappe.throw("历史实际费用的口径尚未确认，请重新选择带入委外回货实际费用或带入已过账实际费用")
            if basis != old_basis and not (not old_basis and basis in ("", FLOW_ESTIMATE)):
                frappe.throw("费用口径只能通过读取流程单预计费用或带入实际费用操作选择")
        if basis not in ("", FLOW_ESTIMATE, RECEIPT_ACTUAL, POSTED_ACTUAL):
            frappe.throw("费用口径无效，请重新选择费用来源")
        self.cost_basis = basis or FLOW_ESTIMATE

    def _read_actual_costs(self):
        if not self.flow_order:
            frappe.throw("实际费用口径必须关联流程单")
        if self.cost_basis == RECEIPT_ACTUAL:
            from jifeng_erp.workflow import receipt_costs

            return receipt_costs(self.flow_order)
        from jifeng_erp.workflow import posted_flow_costs

        return posted_flow_costs(self.flow_order)

    def _apply_actual_costs(self, result):
        self.material_amount = result["totals"]["material"]
        self.processing_amount = result["totals"]["processing"]
        self.other_amount = result["totals"]["other"]
        if self.cost_basis == RECEIPT_ACTUAL:
            sources = {row["receipt"] for row in result["rows"]}
        else:
            sources = {row["issue"] for row in result["issues"]} | {
                row["receipt"] for row in result["receipts"]
            } | {row["manufacture"] for row in result.get("manufactures", [])} | {
                row["journal_entry"] for row in result.get("wages", [])
            }
        self.receipt_cost_sources = "\n".join(sorted(sources)) or None
        self.receipt_cost_loaded_at = now()
        self._cost_refresh_result = result

    def _refresh_actual_costs(self):
        if self.cost_basis in (RECEIPT_ACTUAL, POSTED_ACTUAL):
            # Preserve the explicitly selected scope, including a zero result
            # after all its posted sources have been cancelled.
            self._apply_actual_costs(self._read_actual_costs())

    @frappe.whitelist()
    def recalculate_actual_costs(self, preserve_input=False):
        """Reprice the saved scope even when the form has no pending edits."""
        self.check_permission("write")
        if self.is_new():
            frappe.throw("请先保存试算单并选择实际费用口径")
        self.flags.for_update = True
        frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
        self._reload_for_cost_selection(preserve_input)
        if self.cost_basis not in (RECEIPT_ACTUAL, POSTED_ACTUAL):
            frappe.throw("请先明确选择委外回货实际费用或流程已过账实际费用")
        # Normal save keeps the stored basis, validates the incoming modified
        # version and batch classifications, and rereads the posted sources.
        self.save()
        return self._cost_refresh_result

    def _reload_for_cost_selection(self, preserve_input):
        # Explicit selection can save the normal form's pending edits in the
        # same native save. Its original modified value keeps latest checks.
        pending = self.as_dict() if preserve_input else None
        self.reload()
        self.check_permission("write")
        if pending is not None:
            self.update(pending)
            self.check_permission("write")

    def _save_selected_basis(self, basis):
        self.cost_basis = basis
        self._cost_basis_selection = _COST_BASIS_SELECTION
        try:
            self.save()
        finally:
            del self._cost_basis_selection

    def _validate_batch_sources(self):
        additional_input_qty = self._validate_manufacture_batches()
        linked = [row for row in self.batches if row.source_receipt or row.source_receipt_item]
        if not linked:
            return additional_input_qty
        if not self.flow_order:
            frappe.throw("已关联的回货批次必须包含流程单、委外收货单和明细行")
        from jifeng_erp.workflow import receipt_batch_candidates

        sources = {source["receipt_item"]: source for source in receipt_batch_candidates(self.flow_order)}
        seen = set()
        for row in linked:
            if not row.source_receipt or not row.source_receipt_item:
                frappe.throw("已关联的回货批次必须包含流程单、委外收货单和明细行")
            if row.source_receipt_item in seen:
                frappe.throw("同一委外收货明细不能重复带入试算")
            seen.add(row.source_receipt_item)
            source = sources.get(row.source_receipt_item)
            if not source or (source["receipt"], source["company"], source["item_code"]) != (
                row.source_receipt, self.company, self.item_code,
            ):
                frappe.throw("来源收货明细未提交、不可读或与本公司的商品、流程单不一致")
            if not source["qty"]:
                frappe.throw("来源明细已全额退回，请删除对应回货批次")
            if Decimal(str(source["qty"])) != Decimal(str(row.received_qty or 0)):
                frappe.throw("来源净回货数量已变化，请读取来源并核对批次分类后保存")
            if Decimal(str(row.lost_qty or 0)) < source["lost_qty"]:
                frappe.throw("试算批次损耗不能少于来源凭证已确认的工艺损耗")
        return additional_input_qty

    def _validate_manufacture_batches(self):
        linked = [row for row in self.batches if row.get("source_manufacture")]
        actual_basis = self.get("cost_basis") == POSTED_ACTUAL
        if not linked and not actual_basis:
            return Decimal(0)
        if not self.flow_order:
            frappe.throw("制造入库批次必须关联流程单")
        if actual_basis:
            # Read by _refresh_actual_costs in the same complete source session.
            # Never let removing a Link or all batch rows erase posted losses.
            result = getattr(self, "_cost_refresh_result", None)
            if result is None:
                result = self._read_actual_costs()
            candidates = result["manufacture_batches"]
        else:
            from jifeng_erp.workflow import manufacture_batch_candidates

            candidates = manufacture_batch_candidates(self.flow_order)
        sources = {source["manufacture"]: source for source in candidates}
        seen = set()
        for row in linked:
            if row.source_receipt or row.source_receipt_item:
                frappe.throw("同一批次不能同时关联制造入库与委外回货")
            if row.source_manufacture in seen:
                frappe.throw("同一制造入库单不能重复带入试算")
            seen.add(row.source_manufacture)
            source = sources.get(row.source_manufacture)
            if not source or (source["company"], source["item_code"]) != (self.company, self.item_code):
                frappe.throw("来源制造单未提交、不可读或与本公司的终成品、流程单不一致；已撤销的批次请核对后删除")
            if row.receipt_type != "首次回货":
                frappe.throw("制造入库批次须按首次回货核对新产出，不能转作返工回货或待检处理")
            if Decimal(str(source["qty"])) != Decimal(str(row.received_qty or 0)):
                frappe.throw("来源制造入库量与确认损耗已变化，请读取来源并核对批次分类后保存")
            if Decimal(str(row.lost_qty or 0)) < source["lost_qty"]:
                frappe.throw("试算批次损耗不能少于制造入库单已确认的工艺损耗")
        if actual_basis:
            # A replacement manufacturing entry consumes new input while the
            # Flow's deliverable stock target stays fixed. Permit only the
            # additional input already proven by final-product ledger sources,
            # with their confirmed losses; never extend a manual trial's plan.
            coverage = sum((source["qty"] for source in candidates), Decimal(0))
            inventory = sum((source["inventory_qty"] for source in candidates), Decimal(0))
            loss = sum((source["lost_qty"] for source in candidates), Decimal(0))
            plan = Decimal(str(self.planned_qty))
            additional = max(Decimal(0), coverage - plan) if loss else Decimal(0)
            if additional and (inventory > plan or additional > loss):
                frappe.throw("制造入库已超过流程目标库存量，不能按损耗补产扩大试算数量，请核对流程计划和生产来源")
            missing = [source["manufacture"] for source in candidates
                       if (source["lost_qty"] or additional) and source["manufacture"] not in seen]
            if missing:
                frappe.throw("已过账制造单存在确认损耗，请读取制造入库批次并核对分类后再核算：" + "、".join(missing))
            if additional and any(not row.get("source_manufacture")
                    and row.receipt_type == "首次回货" and Decimal(str(row.received_qty or 0))
                    for row in self.batches):
                frappe.throw("损耗补产的新增投入须全部关联准确制造来源，不能供未绑定的首次回货批次扩量")
            return additional
        return Decimal(0)

    @frappe.whitelist()
    def load_flow_costs(self, preserve_input=False):
        self.check_permission("write")
        if self.is_new():
            frappe.throw("请先保存试算单，再明确读取流程预计费用")
        self.flags.for_update = True
        frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
        self._reload_for_cost_selection(preserve_input)
        flow = frappe.get_doc("Jifeng Flow Order", self.flow_order, for_update=True)
        flow.check_permission("read")
        from jifeng_erp.workflow import stock_plan_quantity

        planned_qty = stock_plan_quantity(flow)
        if self.batches:
            frappe.throw("已有回货批次，请核对费用后手动调整，避免覆盖当前试算")
        self.company, self.item_code, self.planned_qty = flow.company, flow.item_code, planned_qty
        self.flow_order_no, self.external_order_no = flow.name, flow.online_order_id
        self.sales_order = flow.sales_order
        self.material_amount, self.processing_amount = flow.material_amount, flow.processing_amount
        self.other_amount = 0
        self.receipt_cost_sources = None
        self.receipt_cost_loaded_at = None
        self._save_selected_basis(FLOW_ESTIMATE)

    @frappe.whitelist()
    def load_receipt_costs(self, preserve_input=False):
        """Explicitly replace estimated base costs with posted receipt costs."""
        self.check_permission("write")
        frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
        self._reload_for_cost_selection(preserve_input)
        if not self.flow_order:
            frappe.throw("请先关联流程单")

        from jifeng_erp.workflow import receipt_costs

        result = receipt_costs(self.flow_order)
        if not result["rows"] and not self.receipt_cost_loaded_at:
            frappe.throw("没有已提交且关联本流程单的委外收货明细，费用未更改")
        self.cost_basis = RECEIPT_ACTUAL
        self._apply_actual_costs(result)
        self._save_selected_basis(RECEIPT_ACTUAL)
        return self._cost_refresh_result

    @frappe.whitelist()
    def load_posted_costs(self, preserve_input=False):
        """Refresh trial costs from inventory sources and actual wage accruals."""
        self.check_permission("write")
        frappe.db.get_value(self.doctype, self.name, "name", for_update=True)
        self._reload_for_cost_selection(preserve_input)
        if not self.flow_order:
            frappe.throw("请先关联流程单")

        from jifeng_erp.workflow import posted_flow_costs

        result = posted_flow_costs(self.flow_order)
        if not any(result.get(key) for key in ("issues", "receipts", "manufactures", "wages")) and not self.receipt_cost_loaded_at:
            frappe.throw("没有已提交且关联本流程单的物料出库、制造、委外收货或工资计提，费用未更改")
        self.cost_basis = POSTED_ACTUAL
        self._apply_actual_costs(result)
        self._save_selected_basis(POSTED_ACTUAL)
        return self._cost_refresh_result
