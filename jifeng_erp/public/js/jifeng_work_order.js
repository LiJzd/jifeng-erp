frappe.ui.form.on("Work Order", {
    setup(frm) {
        frm.set_query("jf_department", "operations", () => ({
            filters: { company: frm.doc.company, disabled: 0, is_group: 0 },
        }));
    },
});
