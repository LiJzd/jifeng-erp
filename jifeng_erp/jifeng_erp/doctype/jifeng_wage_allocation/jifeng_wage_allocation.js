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
    const timestamp = value => {
        const match = typeof value === "string" && value.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?$/);
        return match ? `${match[1]}T${match[2]}.${(match[3] || "").padEnd(6, "0")}` : null;
    };
    const validProcessing = (value, name, version) => value?.allocation === name &&
        timestamp(value.version) === timestamp(version) && !!timestamp(version) &&
        /^[0-9a-f]{64}$/.test(value.snapshot || "") && Array.isArray(value.rows) && value.rows.length > 0 &&
        typeof value.can_process === "boolean" && typeof value.blocked_reason === "string" &&
        value.rows.every(row => row.allocation_employee && row.employee &&
            ["quantity", "piece_rate", "piece_amount", "time_amount", "extra_amount", "total_amount"].every(
                field => typeof row[field] === "string" && /^\d+(?:\.\d+)?$/.test(row[field])) &&
            typeof row.wage_status === "string" && typeof row.additional_salary_status === "string" &&
            Array.isArray(row.cancelled_salaries));
    const closeProcessing = frm => {
        const owner = frm._jifeng_processing_owner;
        if (!owner) return;
        frm._jifeng_processing_owner = null;
        owner.closed = true;
        owner.observer?.disconnect();
        owner.dialog?.hide();
        if (!owner.posting) owner.release?.();
    };
    const openProcessing = async frm => {
        if (frm.read_only || frm.is_new() || frm.is_dirty() || frm.doc.docstatus !== 1) {
            frappe.msgprint("请先保存并刷新已审核工资分配，再核对集中办理明细。");
            return;
        }
        if (!navigator.locks?.request) {
            frappe.msgprint("当前浏览器不能安全互斥工资办理，请使用支持安全锁的浏览器；原逐单入口仍可使用。");
            return;
        }
        const held = frm.doc, name = held.name, version = held.modified;
        const key = `jifeng:allocation-wages:${encodeURIComponent(frappe.session.user)}:${encodeURIComponent(name)}`;
        closeProcessing(frm);
        try {
            await navigator.locks.request(key, { ifAvailable: true }, async lock => {
                if (!lock) { frappe.msgprint("本分配已在另一页面核对或办理，请先完成原操作。"); return; }
                const owner = frm._jifeng_processing_owner = { doc: held, closed: false, posting: false };
                const current = () => frm._jifeng_processing_owner === owner && !owner.closed && frm.doc === held &&
                    frm.doc.name === name && timestamp(frm.doc.modified) === timestamp(version) &&
                    !frm.is_dirty() && frm.doc.docstatus === 1 && window.cur_frm === frm &&
                    JSON.stringify(frappe.get_route()) === JSON.stringify(["Form", "Jifeng Wage Allocation", name]);
                const released = new Promise(resolve => { owner.release = resolve; });
                try {
                    const response = await frappe.call({ method: "jifeng_erp.wage_allocation_processing.inspect_allocation_processing",
                        type: "GET", args: { allocation: name, expected_version: version } });
                    if (!current()) return;
                    const value = response.message;
                    if (!validProcessing(value, name, version)) throw new Error("工资办理明细回复不完整，请刷新后核对原单。");
                    const pending = localStorage.getItem(key);
                    const esc = text => frappe.utils.escape_html(String(text ?? ""));
                    const columns = [["employee_name", "员工"], ["quantity", "计件份额"], ["piece_rate", "工价"],
                        ["piece_amount", "计件金额"], ["time_amount", "计时金额"], ["extra_amount", "附加费"],
                        ["total_amount", "合计"], ["wage_entry", "计薪记录"], ["wage_status", "计薪状态"],
                        ["additional_salary", "工资附加项"], ["additional_salary_status", "附加项状态"]];
                    const quantities = value.production_quantities || {};
                    const details = `<p>原派工量：${esc(quantities.for_quantity)}；本卡完成量：${esc(value.source_completed_qty)}；工序损耗量：${esc(quantities.process_loss_qty)}；转交待派工量：${esc(quantities.pending_qty)}（${esc(quantities.stock_uom)}）。份额合计：${esc(value.allocated_qty)}；工资合计：${esc(value.total_amount)} ${esc(value.currency)}。</p>
                        <p>计件只核本卡完成量，损耗及转交待派工量不计入份额。</p>
                        <p>本操作审核以下计薪记录并生成工资附加项草稿。已有有效附加项保持，零金额行跳过；随后由工资负责人审核附加项，再进入月度工资和付款。</p>
                        ${pending ? "<p class=\"text-warning\">此前办理结果待核对。本表已重新读取实际状态，未自动重试；请核对后明确决定是否继续。</p>" : ""}
                        ${value.blocked_reason ? `<p class="text-danger">${esc(value.blocked_reason)}</p>` : ""}
                        <div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr>${columns.map(([, label]) => `<th>${esc(label)}</th>`).join("")}</tr></thead>
                        <tbody>${value.rows.map(row => `<tr>${columns.map(([field]) => `<td>${esc(row[field])}</td>`).join("")}</tr>
                            ${row.cancelled_salaries.length ? `<tr><td colspan="${columns.length}">已取消附加项：${row.cancelled_salaries.map(esc).join("、")}</td></tr>` : ""}`).join("")}</tbody></table></div>`;
                    const dialog = owner.dialog = new frappe.ui.Dialog({ title: "核对本分配工资并集中办理", size: "extra-large",
                        fields: [{ fieldname: "details", fieldtype: "HTML", options: details }],
                        primary_action_label: "确认审核计薪并生成附加项草稿",
                        primary_action: async () => {
                            if (!current() || owner.posting || owner.unknown || !value.can_process) return;
                            owner.posting = true;
                            dialog.get_primary_btn().prop("disabled", true);
                            try {
                                // Persist before POST. A lost response must survive reload/A/B switching.
                                localStorage.setItem(key, JSON.stringify({ allocation: name, version, snapshot: value.snapshot }));
                                const result = (await frappe.call({ method: "jifeng_erp.wage_allocation_processing.process_allocation_wages",
                                    type: "POST", freeze: true, freeze_message: "正在办理本分配工资…",
                                    args: { allocation: name, expected_version: version, snapshot: value.snapshot } })).message;
                                if (result?.status !== "completed" || result.allocation !== name ||
                                    !validProcessing(result.current, name, version) || !Array.isArray(result.submitted_wages) ||
                                    !Array.isArray(result.created_salaries) || typeof result.already_completed !== "boolean" ||
                                    result.current.rows.some(row => row.wage_entry && (row.wage_status !== "已审核" || !row.additional_salary)))
                                    throw new Error("办理回复不完整");
                                localStorage.removeItem(key);
                                if (current()) {
                                    dialog.hide();
                                    await frm.reload_doc();
                                    frappe.msgprint(result.already_completed ? "本分配计薪及工资附加项已经办理，没有重复生成。" :
                                        "本分配计薪已审核，工资附加项草稿已准备。请由工资负责人审核后进入月度工资。");
                                }
                            } catch {
                                owner.unknown = true;
                                if (current()) frappe.msgprint("办理结果待核对，未自动重试。请关闭本窗口，再点击“核对并集中办理工资”读取实际状态。");
                            } finally {
                                owner.posting = false;
                                if (owner.closed) owner.release();
                                // A failure never enables the old confirmation for another POST.
                            }
                        },
                    });
                    dialog.on_hide = () => { owner.closed = true; owner.observer?.disconnect(); if (!owner.posting) owner.release(); };
                    if (typeof MutationObserver !== "undefined" && frm.wrapper?.[0]) {
                        const watched = new Set();
                        for (const origin of [frm.wrapper[0], window.frameElement])
                            for (let node = origin; node?.nodeType === 1; node = node.parentElement) watched.add(node);
                        owner.observer = new MutationObserver(records => {
                            if (records.some(record => record.type === "attributes" || record.type === "childList" &&
                                [...record.removedNodes].some(node => watched.has(node)))) closeProcessing(frm);
                        });
                        for (const node of watched) owner.observer.observe(node, { childList: true,
                            ...(["BODY", "HTML"].includes(node.tagName) ? {} : { attributes: true, attributeFilter: ["hidden", "style"] }) });
                    }
                    dialog.show();
                    dialog.get_primary_btn().prop("disabled", !value.can_process);
                    await released;
                } finally {
                    owner.observer?.disconnect();
                    if (frm._jifeng_processing_owner === owner) frm._jifeng_processing_owner = null;
                }
            });
        } catch (error) {
            frappe.msgprint(error?._server_messages ? "完整办理明细未读取，请核对原单和权限后重读。" :
                error.message || "工资办理未读取，请刷新后核对。");
        }
    };
    const showSourceQuantities = frm => {
        const source = frm._jifeng_production_quantities;
        const escape = value => frappe.utils.escape_html(String(value));
        const fields = [["for_quantity", "派工数量"], ["total_completed_qty", "已完成量"],
            ["process_loss_qty", "工序损耗量"], ["pending_qty", "转交待派工量"]];
        const available = source?.stock_uom && fields.every(([name]) => source[name] !== null && source[name] !== undefined && source[name] !== "");
        const summary = available ? `<div class="mt-3"><strong>来源报工数量（${escape(source.stock_uom)}）</strong>
            <table class="table table-bordered table-sm mt-2 mb-1"><thead><tr>${fields.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead>
            <tbody><tr>${fields.map(([name]) => `<td>${escape(source[name])}</td>`).join("")}</tr></tbody></table>
            <span>计件份额仅核对已完成量，损耗量和转交待派工量不计入份额。</span></div>` :
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
            closeProcessing(frm);
            void refreshSourceQuantities(frm);
            if (frm.doc.docstatus === 1 && !frm.read_only && !frm.is_new() && frm.has_perm?.("write") && frm.has_perm?.("submit")) {
                frm.add_custom_button("核对并集中办理工资", () => openProcessing(frm), "工资处理");
            }
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
