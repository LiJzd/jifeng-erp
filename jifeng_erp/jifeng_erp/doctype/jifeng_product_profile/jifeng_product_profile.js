frappe.ui.form.on("Jifeng Product Profile", {
    refresh(frm) {
        if (!frm.is_new() && frm.doc.bom) {
            frm.add_custom_button("从BOM更新物料与工序", async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.call("load_bom");
                await frm.reload_doc();
            });
        }
    }
});
