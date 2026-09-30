frappe.ui.form.on("Production Plan", {
    refresh(frm) {
        if (frm.doc.docstatus !== 0 || !frm.has_perm("write")) return;
        frm.add_custom_button("调入流程单计划", () => {
            if (!frm.doc.company) return frappe.msgprint("请先选择公司");
            const company = frm.doc.company;
            const documentName = frm.doc.name;
            const picker = new frappe.ui.form.MultiSelectDialog({
                doctype: "Jifeng Flow Order",
                target: frm,
                setters: { item_code: null },
                get_query: () => ({ filters: { company, bom: ["is", "set"], source_changed: 0 } }),
                async action(names) {
                    if (!names.length) return frappe.msgprint("请选择流程单");
                    const result = await frappe.call({
                        method: "jifeng_erp.production_instruction.flow_plan_rows",
                        args: { company, flow_names: names }, freeze: true,
                    });
                    if (frm.doc.name !== documentName || frm.doc.company !== company || frm.doc.docstatus !== 0) {
                        return frappe.msgprint("当前计划已变化，请重新调入");
                    }
                    const existing = new Set((frm.doc.po_items || []).map(row => row.jf_flow_order));
                    let count = 0;
                    for (const row of result.message || []) {
                        if (existing.has(row.jf_flow_order)) continue;
                        frm.add_child("po_items", row);
                        existing.add(row.jf_flow_order);
                        count++;
                    }
                    frm.refresh_field("po_items");
                    if (count) frm.dirty();
                    picker.dialog.hide();
                    frappe.show_alert({ message: `已调入 ${count} 条计划，请核对数量、仓库和排期后保存`, indicator: "green" });
                },
            });
        }, "集沣生产指令");
        frm.add_custom_button("统一调计划日期", () => {
            if (!(frm.doc.po_items || []).length) return frappe.msgprint("请先添加生产明细");
            frappe.prompt([{ fieldname: "date", fieldtype: "Datetime", label: "计划开工时间", reqd: 1 }],
                async values => {
                    for (const row of frm.doc.po_items) {
                        await frappe.model.set_value(row.doctype, row.name, "planned_start_date", values.date);
                    }
                    frm.refresh_field("po_items");
                }, "统一调计划日期", "应用到本单明细");
        }, "集沣生产指令");
    },
});
