(() => {
    const editable = frm => frm.doc?.docstatus === 0 && !frm.read_only && !!frm.has_perm?.(frm.is_new() ? "create" : "write");
    const rateTools = async () => {
        if (!window.JifengWageRateTools) await frappe.require("/assets/jifeng_erp/js/jifeng_wage_rates.js");
        if (!window.JifengWageRateTools) throw new Error("历史工价组件未加载，请刷新后重试。");
        return window.JifengWageRateTools;
    };
    const clearReferences = async frm => {
        if (!editable(frm) || !(frm.doc.employees || []).some(row => row.piece_rate_reference || row.piece_rate_reference_version)) return;
        await (await rateTools()).clearRows(frm);
    };
    const sourceSnapshot = frm => JSON.stringify([frm.doc.company, frm.doc.operation, frm.doc.stock_uom, frm.doc.work_date,
        frm.doc.source_signature, frm.doc.employees]);
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
            const request = frm._jifeng_source_request = (frm._jifeng_source_request || 0) + 1;
            await clearReferences(frm);
            if (!frm.doc.job_card || !editable(frm)) return;
            const selected = frm.doc.job_card;
            const held = frm.doc, heldName = frm.doc.name, heldVersion = frm.doc.modified, snapshot = sourceSnapshot(frm);
            const result = await frappe.call({ method: "jifeng_erp.wage_allocation.get_allocation_source",
                args: { job_card: selected }, type: "GET" });
            if (frm.doc !== held || frm.doc.name !== heldName || frm.doc.modified !== heldVersion || frm.doc.job_card !== selected || !editable(frm) ||
                frm._jifeng_source_request !== request || sourceSnapshot(frm) !== snapshot) return;
            const { employees, ...source } = result.message;
            frm.source_employees = employees;
            await frm.set_value(source);
            frm.clear_table("employees");
            for (const employee of employees) frm.add_child("employees", {
                ...employee, quantity: 0, include_time_pay: 0, wage_hours: 0, extra_amount: 0,
            });
            frm.refresh_field("employees");
        },
        company: clearReferences,
        operation: clearReferences,
        stock_uom: clearReferences,
        work_date: clearReferences,
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
    frappe.ui.form.on("Jifeng Wage Allocation Employee", {
        async call_history_rate(frm, cdt, cdn) {
            const row = locals[cdt]?.[cdn];
            if (!row || !editable(frm)) return;
            try { await (await rateTools()).open(frm, row); }
            catch { frappe.msgprint("历史工价组件加载未完成，请刷新后重试。"); }
        },
        async piece_rate(frm, cdt, cdn) {
            const row = locals[cdt]?.[cdn];
            if (row && editable(frm) && (row.piece_rate_reference || row.piece_rate_reference_version)) await (await rateTools()).clearReference(frm, row);
        },
        async employee(frm, cdt, cdn) {
            const row = locals[cdt]?.[cdn];
            if (row && editable(frm) && (row.piece_rate_reference || row.piece_rate_reference_version)) await (await rateTools()).clearReference(frm, row);
        },
    });
})();
