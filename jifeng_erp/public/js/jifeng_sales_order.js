frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1 || frm.doc.status === "Closed") return;
        frm.add_custom_button("生成集沣生产流程单", async () => {
            const result = await frappe.call({
                method: "jifeng_erp.workflow.create_sales_order_flows",
                args: { sales_order: frm.doc.name },
            });
            const names = result.message || [];
            if (names.length === 1) {
                frappe.set_route("Form", "Jifeng Flow Order", names[0]);
            } else if (names.length) {
                frappe.set_route("List", "Jifeng Flow Order", { sales_order: frm.doc.name });
            }
        }, "集沣 ERP");
    },
});
