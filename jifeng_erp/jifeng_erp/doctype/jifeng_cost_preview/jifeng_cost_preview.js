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
                    frappe.msgprint("没有已提交且关联本流程单的物料出库或委外收货，费用未更改。");
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
