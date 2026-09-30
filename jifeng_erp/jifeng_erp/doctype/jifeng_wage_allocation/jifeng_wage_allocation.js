frappe.ui.form.on("Jifeng Wage Allocation", {
    setup(frm) {
        frm.set_query("job_card", () => ({ filters: { docstatus: 1, is_corrective_job_card: 0 } }));
        frm.set_query("salary_component", () => ({ filters: { type: "Earning", disabled: 0 } }));
        frm.set_query("employee", "employees", () => ({
            filters: { company: frm.doc.company, status: "Active",
                name: ["in", (frm.source_employees || frm.doc.employees || []).map(row => row.employee)] },
        }));
    },
    async job_card(frm) {
        if (!frm.doc.job_card || frm.doc.docstatus !== 0) return;
        const selected = frm.doc.job_card;
        const result = await frappe.call({ method: "jifeng_erp.wage_allocation.get_allocation_source",
            args: { job_card: selected } });
        if (frm.doc.job_card !== selected) return;
        const { employees, ...source } = result.message;
        frm.source_employees = employees;
        await frm.set_value(source);
        frm.clear_table("employees");
        for (const employee of employees) frm.add_child("employees", {
            ...employee, quantity: 0, include_time_pay: 0, wage_hours: 0, extra_amount: 0,
        });
        frm.refresh_field("employees");
    },
    refresh(frm) {
        frm.set_intro("主管填写每人的计件份额和工价；审核时份额合计须等于报工完成量。报工小时供核对，明确勾选“计入计时工资”后才计薪。审核生成各人的计薪草稿，再按工资流程审核。", "blue");
        if (frm.doc.docstatus === 1 && (frappe.user.has_role("HR Manager") || frappe.user.has_role("System Manager"))) {
            frm.add_custom_button("生成或查看计薪记录", async () => {
                await frm.call("make_wages");
                await frm.reload_doc();
                frappe.set_route("List", "Jifeng Wage Entry", { wage_allocation: frm.doc.name });
            });
        }
    },
});
