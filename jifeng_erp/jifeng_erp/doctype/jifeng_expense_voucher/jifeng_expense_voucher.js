frappe.ui.form.on("Jifeng Expense Voucher", {
    setup(frm) {
        frm.set_query("cost_center", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
        frm.set_query("expense_account", () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0, root_type: "Expense" } }));
        frm.set_query("payment_account", () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0, root_type: "Asset", account_type: ["in", ["Cash", "Bank"]] } }));
        frm.set_query("payable_account", () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0, root_type: "Liability", account_type: "Payable" } }));
        frm.set_query("supplier", () => ({ filters: { disabled: 0 } }));
    },
    refresh(frm) {
        if (!frm.doc.journal_entry) return;
        frm.add_custom_button("查看总账凭证", () => frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry));
        if (frm.doc.docstatus !== 1 || frm.doc.payment_method !== "赊账" || !frappe.model.can_create("Payment Entry")) return;
        frm.add_custom_button("办理付款", async () => {
            if (frm._jf_credit_dialog) {
                frm._jf_credit_dialog.show();
                return;
            }
            if (frm._jf_credit_context_pending) return;
            let source;
            frm._jf_credit_context_pending = true;
            try {
                const response = await frm.call("credit_payment_context");
                source = response.message;
            } finally {
                frm._jf_credit_context_pending = false;
            }
            if (!source) return;
            const dialog = frappe.prompt([
                { fieldname: "paid_from", label: "本次付款科目", fieldtype: "Link", options: "Account", reqd: 1,
                    get_query: () => ({ filters: { company: source.company, is_group: 0, disabled: 0,
                        root_type: "Asset", account_type: ["in", ["Cash", "Bank"]],
                        account_currency: source.paid_to_account_currency } }) },
                { fieldname: "amount", label: "本次付款金额", fieldtype: "Currency", reqd: 1,
                    default: source.outstanding_amount },
            ], (values) => {
                // Construct an unsaved native document only. Native Payment Entry
                // validates current PLE balance, party, accounts and allocation.
                frappe.new_doc("Payment Entry", {
                    company: source.company, payment_type: "Pay", party_type: source.party_type,
                    party: source.party, paid_to: source.paid_to, paid_from: values.paid_from,
                }, (payment) => {
                    // This is a prepared source document, like a native mapped
                    // Payment Entry. The generic new-doc Link triggers clear
                    // party/references when party_type initializes; native
                    // onload, save and submit validation still run normally.
                    payment.__run_link_triggers = false;
                    payment.paid_to_account_currency = source.paid_to_account_currency;
                    payment.paid_amount = values.amount;
                    payment.received_amount = values.amount;
                    const row = frappe.model.add_child(payment, "Payment Entry Reference", "references");
                    Object.assign(row, {
                        reference_doctype: source.reference_doctype, reference_name: source.reference_name,
                        total_amount: source.total_amount, outstanding_amount: source.outstanding_amount,
                        allocated_amount: values.amount,
                    });
                });
            }, "赊账费用付款", "打开付款单");
            frm._jf_credit_dialog = dialog;
            const previous_hide = dialog.on_hide;
            dialog.on_hide = () => {
                if (frm._jf_credit_dialog === dialog) frm._jf_credit_dialog = null;
                if (previous_hide) previous_hide.call(dialog);
            };
        });
    },
});
