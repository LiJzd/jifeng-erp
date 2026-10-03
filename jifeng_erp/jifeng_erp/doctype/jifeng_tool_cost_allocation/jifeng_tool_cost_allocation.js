frappe.ui.form.on("Jifeng Tool Cost Allocation", {
    refresh(frm) {
        frm.set_query("expense_voucher", () => ({ filters: { company: frm.doc.company, docstatus: 1 } }));
        if (!frm.is_new()) {
            frm.add_custom_button(__("查看实际结算"), () => frm.call("settlement_details").then(result => {
                const source = result.message && result.message.source;
                if (!source) return;
                frappe.msgprint({ title: __("原生整笔费用结算"), message: $("<div>").append(
                    $("<p>").text(__("币种") + "：" + source.currency),
                    $("<p>").text(__("实际发生") + "：" + source.incurred_amount),
                    $("<p>").text(__("实际已付") + "：" + source.paid_amount),
                    $("<p>").text(__("尚未付款") + "：" + source.unpaid_amount),
                    $("<p>").text(__("部分付款没有逐工具来源，逐工具已付与未付保持未知。"))
                ).html() });
            }));
        }
        if (frm.doc.docstatus === 1 && frm.perm.some(permission => permission.cancel)) {
            frm.add_custom_button(__("撤销费用分摊"), () => frappe.prompt({ fieldname: "reason", fieldtype: "Small Text",
                label: __("撤销原因"), reqd: 1 }, values => frm.call("cancel_with_reason", { reason: values.reason })
                    .then(() => frm.reload_doc()), __("仅解除工具归属；费用与付款保持"), __("确认撤销")));
        }
    }
});
frappe.ui.form.on("Jifeng Tool Cost Allocation Item", {
    tool_type(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const doctypes = { "模具": "Jifeng Mold", "版种": "Jifeng Plate", "建模": "Jifeng Plate Modeling" };
        frappe.model.set_value(cdt, cdn, { tool_doctype: doctypes[row.tool_type] || "", tool_name: "" });
    }
});
