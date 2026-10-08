frappe.ui.form.on("Jifeng External Order", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button("生成流程单", async () => {
            if (frm.is_dirty()) await frm.save();
            const r = await frm.call("make_flows");
            await frm.reload_doc();
            if (r.message && r.message.length === 1) frappe.set_route("Form", "Jifeng Flow Order", r.message[0]);
        });
        frm.add_custom_button("显式更新流程单要求", () => frappe.confirm(
            "将按商品行的定制/工艺要求和包装要求更新准确关联的流程单；行内留空时使用订单抬头要求。已有有效下游业务单据的流程单需先处理原单据。请先核对每一行。",
            async () => {
                if (frm.is_dirty()) await frm.save();
                const r = await frm.call("apply_requirements");
                frappe.msgprint(`已将各商品行要求更新到 ${r.message || 0} 张关联流程单`);
            }
        ));
    }
});
