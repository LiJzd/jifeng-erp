frappe.ui.form.on("Jifeng Wage Entry", {
    async job_card(frm) {
        if (!frm.doc.job_card) return;
        const selected = frm.doc.job_card;
        const card = await frappe.db.get_doc("Job Card", selected);
        if (frm.doc.job_card !== selected) return;
        let flow = null;
        if (card.work_order) {
            const work = await frappe.db.get_doc("Work Order", card.work_order);
            if (frm.doc.job_card !== selected) return;
            flow = work.jf_flow_order || null;
        }
        await frm.set_value({ company: card.company, operation: card.operation,
            work_order: card.work_order || null, flow_order: flow });
    },
    refresh(frm) {
        frm.set_intro("每行对应一名员工的一道工序。审核后生成 HRMS 工资附加项草稿；工资负责人提交附加项后才进入工资单。", "blue");
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button("生成或查看工资附加项", async () => {
                const response = await frm.call("make_additional_salary");
                frappe.set_route("Form", "Additional Salary", response.message);
            });
        }
    },
});
