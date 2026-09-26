frappe.ui.form.on("Jifeng External Order", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button("生成流程单", async () => {
            if (frm.is_dirty()) await frm.save();
            const r = await frm.call("make_flows");
            await frm.reload_doc();
            if (r.message && r.message.length === 1) frappe.set_route("Form", "Jifeng Flow Order", r.message[0]);
        });
        frm.add_custom_button("将要求更新到流程单", async () => {
            if (frm.is_dirty()) await frm.save();
            await frm.call("apply_requirements");
            frappe.msgprint("定制要求、包装要求和附件已更新到关联流程单");
        });
    }
});
