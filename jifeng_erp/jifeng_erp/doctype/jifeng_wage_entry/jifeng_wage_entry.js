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
            work_order: card.work_order || null, flow_order: flow, stock_uom: card.stock_uom });
    },
    refresh(frm) {
        frm.set_intro("每单对应一名员工的一道工序。多人计件由主管先审核工资分配；分配生成的计薪记录保持原份额、工价和来源。审核后可生成工资附加项草稿，工资负责人提交后进入工资单。", "blue");
        const allocated = Boolean(frm.doc.wage_allocation);
        for (const field of ["employee", "company", "job_card", "work_order", "flow_order", "operation",
            "work_date", "payroll_date", "salary_component", "quantity", "stock_uom", "piece_rate",
            "hours", "hourly_rate", "extra_amount"]) frm.set_df_property(field, "read_only", allocated ? 1 : 0);
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button("生成或查看工资附加项", async () => {
                const response = await frm.call("make_additional_salary");
                frappe.set_route("Form", "Additional Salary", response.message);
            });
        }
    },
});
