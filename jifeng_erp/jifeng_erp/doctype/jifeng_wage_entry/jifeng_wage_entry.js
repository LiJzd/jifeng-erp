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
    const timestamp = value => {
        if (typeof value !== "string") return null;
        const parts = value.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?$/);
        return parts ? `${parts[1]}T${parts[2]}.${(parts[3] || "").padEnd(6, "0")}` : null;
    };
    const closeClassification = frm => {
        const owner = frm._jifeng_classification_owner;
        if (!owner) return;
        frm._jifeng_classification_owner = null;
        owner.observer?.disconnect();
        owner.dialog?.hide();
    };
    const readClassification = async frm => {
        const held = frm.doc, name = held.name, version = held.modified;
        const identity = JSON.stringify([held.company, held.employee, held.docstatus]);
        if (![1, 2].includes(held.docstatus) || frm.is_new() || frm.is_dirty() || !timestamp(version)) return;
        closeClassification(frm);
        const owner = frm._jifeng_classification_owner = { doc: held };
        const current = () => {
            if (frm._jifeng_classification_owner !== owner || frm.doc !== held || window.cur_frm !== frm ||
                frm.is_dirty() || held.name !== name || held.modified !== version ||
                JSON.stringify([held.company, held.employee, held.docstatus]) !== identity) return false;
            const route = frappe.get_route();
            if (route[0] !== "Form" || route[1] !== "Jifeng Wage Entry" || route[2] !== name) return false;
            const element = frm.wrapper?.[0];
            if (!element?.isConnected || !element.getClientRects().length) return false;
            try { return !window.frameElement || !!window.frameElement.getClientRects().length; }
            catch { return false; }
        };
        if (!current()) { closeClassification(frm); return; }
        // Native forms are also hosted in tabs. Leaving and returning to the
        // same tab must not revive its previous request or dialog.
        const watched = new Set();
        for (const origin of [frm.wrapper[0], window.frameElement]) {
            for (let node = origin; node?.nodeType === 1; node = node.parentElement) watched.add(node);
        }
        owner.observer = new MutationObserver(records => {
            // An ancestor may be removed and reattached before this callback.
            // The recorded removal invalidates the old request permanently.
            const left = records.some(record => record.type === "attributes" ||
                record.type === "childList" && [...record.removedNodes].some(node => watched.has(node)));
            if (left && frm._jifeng_classification_owner === owner) closeClassification(frm);
        });
        for (const node of watched) owner.observer.observe(node, { childList: true,
            ...(["BODY", "HTML"].includes(node.tagName) ? {} : { attributes: true, attributeFilter: ["hidden", "style"] }) });
        try {
            const response = await frappe.call({ method: "jifeng_erp.wage_personnel_classification.read_classification",
                type: "GET", silent: true, args: { name, version } });
            if (!current()) return;
            const value = response.message;
            if (!value || value.readonly !== true || value.scope !== "wage_personnel_classification" ||
                value.name !== name || timestamp(value.version) !== timestamp(version) || value.docstatus !== held.docstatus ||
                value.classification_basis !== "first_native_submission" ||
                !["已核实", "档案未记录", "无权核实", "历史未记录"].includes(value.status) ||
                ![null, "临时", "长期"].includes(value.personnel_type) ||
                (value.status === "已核实" && (!value.personnel_type || !timestamp(value.recorded_at))) ||
                (value.recorded_at !== null && !timestamp(value.recorded_at))) throw new Error("invalid classification");
            const dialog = owner.dialog = new frappe.ui.Dialog({ title: "审核时人员类型", fields: [
                { fieldname: "status", fieldtype: "Data", label: "核实状态", read_only: 1 },
                { fieldname: "personnel_type", fieldtype: "Data", label: "人员类型", read_only: 1 },
                { fieldname: "recorded_at", fieldtype: "Datetime", label: "核实时间", read_only: 1 },
            ] });
            dialog.set_values({ status: value.status, personnel_type: value.personnel_type || "未记录",
                recorded_at: value.recorded_at });
            dialog.on_hide = () => { if (frm._jifeng_classification_owner === owner) closeClassification(frm); };
            dialog.show();
        } catch {
            if (current()) frappe.msgprint("人员类型读取未完成，请核对原工资和人员档案的读取权限后重试。");
        } finally {
            if (frm._jifeng_classification_owner === owner && !owner.dialog) closeClassification(frm);
        }
    };
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
            closeClassification(frm);
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
            if ([1, 2].includes(frm.doc.docstatus) && !frm.is_new()) {
                frm.add_custom_button("查看审核时人员类型", () => readClassification(frm), "集沣 ERP");
            }
        },
    });
})();
