frappe.ui.form.on("Jifeng Wage Entry", {
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
