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
    const intro = "主管填写每人的计件份额和工价；审核时份额合计须等于报工完成量。报工小时供核对，明确勾选“计入计时工资”后才计薪。审核生成各人的计薪草稿，再按工资流程审核。";
    const showSourceQuantities = frm => {
        const source = frm._jifeng_production_quantities;
        const escape = value => frappe.utils.escape_html(String(value));
        const fields = [["for_quantity", "派工数量"], ["total_completed_qty", "已完成量"],
            ["process_loss_qty", "工序损耗量"], ["pending_qty", "待完成量"]];
        const available = source?.stock_uom && fields.every(([name]) => source[name] !== null && source[name] !== undefined && source[name] !== "");
        const summary = available ? `<div class="mt-3"><strong>来源报工数量（${escape(source.stock_uom)}）</strong>
            <table class="table table-bordered table-sm mt-2 mb-1"><thead><tr>${fields.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead>
            <tbody><tr>${fields.map(([name]) => `<td>${escape(source[name])}</td>`).join("")}</tr></tbody></table>
            <span>计件份额仅核对已完成量，损耗量和待完成量不计入份额。</span></div>` :
            (frm.doc.job_card && frm._jifeng_source_summary_unavailable ? "<div class=\"mt-2\">报工数量汇总未读取，请打开来源报工单核对。</div>" : "");
        frm.set_intro(intro + summary, "blue");
    };
    const refreshSourceQuantities = async frm => {
        const request = frm._jifeng_summary_request = (frm._jifeng_summary_request || 0) + 1;
        frm._jifeng_production_quantities = null;
        frm._jifeng_source_summary_unavailable = false;
        showSourceQuantities(frm);
        if (!frm.doc.job_card) return;
        const held = frm.doc, selected = frm.doc.job_card, heldName = frm.doc.name, heldVersion = frm.doc.modified;
        const current = () => frm.doc === held && frm.doc.name === heldName && frm.doc.modified === heldVersion &&
            frm.doc.job_card === selected && frm._jifeng_summary_request === request;
        try {
            const result = await frappe.call({ method: "jifeng_erp.wage_allocation.get_allocation_source", args: { job_card: selected }, type: "GET" });
            if (!current()) return;
            frm._jifeng_production_quantities = result.message?.production_quantities || null;
            frm._jifeng_source_summary_unavailable = !frm._jifeng_production_quantities;
        } catch {
            if (!current()) return;
            frm._jifeng_source_summary_unavailable = true;
        }
        showSourceQuantities(frm);
    };
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
            frm._jifeng_summary_request = (frm._jifeng_summary_request || 0) + 1;
            frm._jifeng_production_quantities = null;
            frm._jifeng_source_summary_unavailable = false;
            showSourceQuantities(frm);
            await clearReferences(frm);
            if (!frm.doc.job_card || !editable(frm)) return;
            const selected = frm.doc.job_card;
            const held = frm.doc, heldName = frm.doc.name, heldVersion = frm.doc.modified, snapshot = sourceSnapshot(frm);
            const result = await frappe.call({ method: "jifeng_erp.wage_allocation.get_allocation_source",
                args: { job_card: selected }, type: "GET" });
            if (frm.doc !== held || frm.doc.name !== heldName || frm.doc.modified !== heldVersion || frm.doc.job_card !== selected || !editable(frm) ||
                frm._jifeng_source_request !== request || sourceSnapshot(frm) !== snapshot) return;
            const { employees, production_quantities, ...source } = result.message;
            frm._jifeng_production_quantities = production_quantities || null;
            frm._jifeng_source_summary_unavailable = !production_quantities;
            showSourceQuantities(frm);
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
            void refreshSourceQuantities(frm);
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
