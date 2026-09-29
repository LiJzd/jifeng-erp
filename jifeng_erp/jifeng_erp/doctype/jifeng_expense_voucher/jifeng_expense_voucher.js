frappe.ui.form.on("Jifeng Expense Voucher", {
    setup(frm) {
        frm.set_query("cost_center", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
        frm.set_query("expense_account", () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0, root_type: "Expense" } }));
        frm.set_query("payment_account", () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0, root_type: "Asset", account_type: ["in", ["Cash", "Bank"]] } }));
    },
    refresh(frm) {
        if (!frm.doc.journal_entry) return;
        frm.add_custom_button("查看总账凭证", () => frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry));
    },
});
