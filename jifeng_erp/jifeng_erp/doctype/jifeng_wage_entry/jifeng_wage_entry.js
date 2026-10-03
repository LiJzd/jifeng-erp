(() => {
    const editable = frm => frm.doc?.docstatus === 0 && !frm.read_only && !frm.doc.wage_allocation &&
        !!frm.has_perm?.(frm.is_new() ? "create" : "write") && !!frm.fields_dict?.piece_rate &&
        !frm.fields_dict.piece_rate.df.read_only && !frm.fields_dict.piece_rate.df.hidden;
    const rateTools = async () => {
        if (!window.JifengWageRateTools) await frappe.require("/assets/jifeng_erp/js/jifeng_wage_rates.js");
        if (!window.JifengWageRateTools) throw new Error("历史工价组件未加载，请刷新后重试。");
        return window.JifengWageRateTools;
    };
    const clearReference = async frm => {
        if (!editable(frm) || !(frm.doc.piece_rate_reference || frm.doc.piece_rate_reference_version)) return;
        await (await rateTools()).clearReference(frm);
    };
    const sourceSnapshot = frm => JSON.stringify([frm.doc.company, frm.doc.employee, frm.doc.operation, frm.doc.stock_uom,
        frm.doc.work_date, frm.doc.piece_rate]);
    frappe.ui.form.on("Jifeng Wage Entry", {
        async job_card(frm) {
            const request = frm._jifeng_source_request = (frm._jifeng_source_request || 0) + 1;
            await clearReference(frm);
            if (!editable(frm) || !frm.doc.job_card) return;
            const selected = frm.doc.job_card;
            const held = frm.doc, heldName = frm.doc.name, heldVersion = frm.doc.modified, snapshot = sourceSnapshot(frm);
            const current = () => frm.doc === held && editable(frm) && frm.doc.job_card === selected &&
                frm.doc.name === heldName && frm.doc.modified === heldVersion && frm._jifeng_source_request === request && sourceSnapshot(frm) === snapshot;
            try {
                const card = await frappe.db.get_doc("Job Card", selected);
                if (!current()) return;
                let flow = null;
                if (card.work_order) {
                    const work = await frappe.db.get_doc("Work Order", card.work_order);
                    if (!current()) return;
                    flow = work.jf_flow_order || null;
                }
                await frm.set_value({ company: card.company, operation: card.operation,
                    work_order: card.work_order || null, flow_order: flow, stock_uom: card.stock_uom });
            } catch { if (current()) frappe.msgprint("报工来源读取未完成，请核对权限后重新选择。"); }
        },
        employee: clearReference,
        company: clearReference,
        operation: clearReference,
        stock_uom: clearReference,
        work_date: clearReference,
        piece_rate: clearReference,
        refresh(frm) {
            frm.set_intro("每单对应一名员工的一道工序。多人计件由主管先审核工资分配；分配生成的计薪记录保持原份额、工价和来源。审核后可生成工资附加项草稿，工资负责人提交后进入工资单。", "blue");
            const allocated = Boolean(frm.doc.wage_allocation);
            if (allocated) for (const field of ["employee", "company", "job_card", "work_order", "flow_order", "operation",
                "work_date", "payroll_date", "salary_component", "quantity", "stock_uom", "piece_rate",
                "hours", "hourly_rate", "extra_amount"]) frm.set_df_property(field, "read_only", 1);
            if (editable(frm)) {
                frm.add_custom_button("调用历史工价", async () => {
                    try { await (await rateTools()).open(frm); }
                    catch { frappe.msgprint("历史工价组件加载未完成，请刷新后重试。"); }
                }, "集沣 ERP");
            }
            if (frm.doc.docstatus === 1) {
                frm.add_custom_button("生成或查看工资附加项", async () => {
                    const response = await frm.call("make_additional_salary");
                    frappe.set_route("Form", "Additional Salary", response.message);
                });
            }
        },
    });
})();
