frappe.ui.form.on("Jifeng Cost Preview", {
    async flow_order(frm) {
        const name = frm.doc.flow_order;
        if (!name || (frm.doc.batches || []).length) return;
        const flow = await frappe.db.get_doc("Jifeng Flow Order", name);
        if (frm.doc.flow_order !== name) return;
        await frm.set_value({ company: flow.company, item_code: flow.item_code,
            flow_order_no: flow.name, external_order_no: flow.online_order_id,
            sales_order: flow.sales_order,
            planned_qty: flow.planned_qty, material_amount: flow.material_amount,
            processing_amount: flow.processing_amount, other_amount: 0,
            receipt_cost_sources: null, receipt_cost_loaded_at: null });
    },
    refresh(frm) {
        frm.set_intro("按预计最终合格数量分摊的试算；后续损耗和追加费用会改变结果。保存不产生库存或财务凭证。", "blue");
        if (!frm.is_new() && frm.doc.flow_order) {
            frm.add_custom_button("读取已提交委外回货批次", async () => {
                const flowOrder = frm.doc.flow_order;
                const previewName = frm.doc.name;
                const response = await frappe.call({
                    method: "jifeng_erp.workflow.receipt_batch_candidates",
                    args: { flow_order: flowOrder },
                });
                if (frm.doc.name !== previewName || frm.doc.flow_order !== flowOrder) return;
                const candidates = response.message || [];
                if (!candidates.length) {
                    frappe.msgprint("没有已提交且关联本流程单的委外回货明细。现有批次未更改。");
                    return;
                }
                const existing = new Set((frm.doc.batches || []).map((row) => row.source_receipt_item).filter(Boolean));
                const manualReferences = new Set((frm.doc.batches || [])
                    .filter((row) => !row.source_receipt_item && row.reference_no)
                    .map((row) => row.reference_no));
                let added = 0;
                let manualConflicts = 0;
                for (const candidate of candidates) {
                    if (existing.has(candidate.receipt_item)) continue;
                    if (manualReferences.has(candidate.receipt)) {
                        manualConflicts++;
                        continue;
                    }
                    frm.add_child("batches", {
                        receipt_type: "首次回货",
                        reference_no: candidate.receipt,
                        source_receipt: candidate.receipt,
                        source_receipt_item: candidate.receipt_item,
                        received_qty: candidate.qty,
                        lost_qty: candidate.lost_qty || 0,
                        pending_qty: candidate.qty - (candidate.lost_qty || 0),
                    });
                    existing.add(candidate.receipt_item);
                    added++;
                }
                frm.refresh_field("batches");
                frappe.msgprint(`新增 ${added} 条回货批次${manualConflicts ? `；${manualConflicts} 条与手工回货单号相同，已跳过` : ""}。凭证中的工艺损耗已带入，其余数量先列为待检；请核对首次/返工类型及合格、返工、损耗、待检数量后保存。本操作不会自动保存或入账。`);
            });
            frm.add_custom_button("读取流程单预计费用", async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.call("load_flow_costs");
                await frm.reload_doc();
            });
            frm.add_custom_button("带入已过账实际费用", async () => {
                if (frm.is_dirty()) await frm.save();
                const response = await frappe.call({
                    method: "jifeng_erp.workflow.posted_flow_costs",
                    args: { flow_order: frm.doc.flow_order },
                });
                const result = response.message || { issues: [], receipts: [], totals: {} };
                if (!result.issues.length && !result.receipts.length) {
                    if (!frm.doc.receipt_cost_loaded_at) {
                        frappe.msgprint("没有已提交且关联本流程单的物料出库或委外收货，费用未更改。");
                        return;
                    }
                    frappe.confirm("此前带入的已过账费用已无有效来源。重新汇总后，材料费、加工费和其他费将清零。确认继续？", async () => {
                        await frm.call("load_posted_costs");
                        await frm.reload_doc();
                    });
                    return;
                }
                const amount = (value) => format_currency(value || 0, result.currency);
                const message = `将已过账凭证的费用带入试算：物料出库 ${result.issues.length} 条，材料费 ${amount(result.totals.issue_material)}；委外收货 ${result.receipts.length} 条，材料费 ${amount(result.totals.receipt_material)}、加工费 ${amount(result.totals.processing)}、其他费 ${amount(result.totals.other)}。材料费合计 ${amount(result.totals.material)}。尚未过账的后续费用不包含在内；已有回货批次将重新试算。若批次追加加工费已计入委外收货，请先清零该笔追加费，避免重复计算。执行时会重新汇总当前已提交凭证。确认继续？`;
                frappe.confirm(message, async () => {
                    await frm.call("load_posted_costs");
                    await frm.reload_doc();
                });
            });
        }
    }
});
