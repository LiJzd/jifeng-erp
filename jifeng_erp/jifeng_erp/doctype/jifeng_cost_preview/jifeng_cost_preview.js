frappe.ui.form.on("Jifeng Cost Preview", {
    async flow_order(frm) {
        const name = frm.doc.flow_order;
        if (!name || (frm.doc.batches || []).length) return;
        const flow = await frappe.db.get_doc("Jifeng Flow Order", name);
        if (frm.doc.flow_order !== name) return;
        await frm.set_value({ company: flow.company, item_code: flow.item_code,
            flow_order_no: flow.name, external_order_no: flow.online_order_id,
            planned_qty: flow.planned_qty, material_amount: flow.material_amount,
            processing_amount: flow.processing_amount });
    },
    refresh(frm) {
        frm.set_intro("按预计最终合格数量分摊的试算；后续损耗和追加费用会改变结果。保存不产生库存或财务凭证。", "blue");
        if (!frm.is_new() && frm.doc.flow_order) {
            frm.add_custom_button("读取流程单预计费用", async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.call("load_flow_costs");
                await frm.reload_doc();
            });
        }
    }
});
