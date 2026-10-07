frappe.ui.form.on("Jifeng Cost Preview", {
    async flow_order(frm) {
        const name = frm.doc.flow_order;
        if (!name || (frm.doc.batches || []).length) return;
        const flow = await frappe.db.get_doc("Jifeng Flow Order", name);
        if (frm.doc.flow_order !== name) return;
        if (!["stock_v1", "manual_verified_v1"].includes(flow.quantity_basis) || !flow.stock_uom) {
            frappe.msgprint("所选流程单计划数量的库存单位尚未核对，请先打开流程单确认计划库存单位和数量。");
            return;
        }
        const values = { company: flow.company, item_code: flow.item_code,
            flow_order_no: flow.name, external_order_no: flow.online_order_id,
            sales_order: flow.sales_order, planned_qty: flow.planned_qty };
        // Changing a Link must not erase an actual or ambiguous legacy basis.
        if (!frm.doc.receipt_cost_loaded_at && !["委外回货实际", "流程已过账实际"].includes(frm.doc.cost_basis))
            Object.assign(values, { material_amount: flow.material_amount,
                processing_amount: flow.processing_amount, other_amount: 0,
                receipt_cost_sources: null, receipt_cost_loaded_at: null });
        await frm.set_value(values);
    },
    refresh(frm) {
        const basis = frm.doc.cost_basis || (frm.doc.receipt_cost_loaded_at ? "历史实际费用口径待确认" : "流程估算");
        frm.set_intro(`费用口径：${basis}。实际口径保存或重新核算时重读当前已提交来源，撤销后净费用随之更新；无待保存修改时，请使用“重新核算当前实际费用”。估算口径保留人工费用。试算不产生库存或财务凭证。`, "blue");
        if (!frm.is_new() && frm.doc.flow_order) {
            frm.add_custom_button("读取已提交委外回货批次", async () => {
                const flowOrder = frm.doc.flow_order;
                const previewName = frm.doc.name;
                const response = await frappe.call({
                    method: "jifeng_erp.workflow.receipt_batch_candidates",
                    args: { flow_order: flowOrder },
                });
                if (frm.doc.name !== previewName || frm.doc.flow_order !== flowOrder) return;
                const candidates = response.message || [];
                if (!candidates.length) {
                    frappe.msgprint("没有已提交且关联本流程单的委外回货明细。现有批次未更改。");
                    return;
                }
                const existing = new Map((frm.doc.batches || [])
                    .filter((row) => row.source_receipt_item).map((row) => [row.source_receipt_item, row]));
                const manualReferences = new Set((frm.doc.batches || [])
                    .filter((row) => !row.source_receipt_item && row.reference_no)
                    .map((row) => row.reference_no));
                let added = 0;
                let manualConflicts = 0;
                let fullyReturned = 0;
                const changed = [];
                for (const candidate of candidates) {
                    if (existing.has(candidate.receipt_item)) {
                        const row = existing.get(candidate.receipt_item);
                        if (Number(row.received_qty) !== Number(candidate.qty) || Number(row.lost_qty || 0) < Number(candidate.lost_qty || 0)) {
                            changed.push(`${frappe.utils.escape_html(candidate.receipt)}：净回货 ${candidate.qty}，已退回 ${candidate.returned_qty || 0}，最低损耗 ${candidate.lost_qty || 0}`);
                        }
                        continue;
                    }
                    if (Number(candidate.qty) === 0) {
                        fullyReturned++;
                        continue;
                    }
                    if (manualReferences.has(candidate.receipt)) {
                        manualConflicts++;
                        continue;
                    }
                    const addedRow = frm.add_child("batches", {
                        receipt_type: "首次回货",
                        reference_no: candidate.receipt,
                        source_receipt: candidate.receipt,
                        source_receipt_item: candidate.receipt_item,
                        received_qty: candidate.qty,
                        lost_qty: candidate.lost_qty || 0,
                        pending_qty: candidate.qty - (candidate.lost_qty || 0),
                    });
                    existing.set(candidate.receipt_item, addedRow);
                    added++;
                }
                frm.refresh_field("batches");
                frappe.msgprint(`新增 ${added} 条净回货批次${manualConflicts ? `；${manualConflicts} 条与手工回货单号相同，已跳过` : ""}${fullyReturned ? `；${fullyReturned} 条已全额退回，未新增` : ""}。已扣除已提交退货，来源工艺损耗保留，其余先列为待检；请核对首次/返工类型和数量分类后保存。本操作不会自动保存或入账。${changed.length ? `<br><br>以下已有批次的来源数量已变化，分类未覆盖；请调整数量，净回货为零的批次请删除：<br>${changed.join("<br>")}` : ""}`);
            });
            const chooseCosts = async (method, title, reader = null) => {
                const name = frm.doc.name, flowOrder = frm.doc.flow_order;
                const current = () => frm.doc.name === name && frm.doc.flow_order === flowOrder;
                let message = `${title}；所选实际口径以后每次保存会重新汇总当前已提交凭证。`;
                if (reader) {
                    const response = await frappe.call({ method: `jifeng_erp.workflow.${reader}`, args: { flow_order: flowOrder } });
                    if (!current()) return;
                    const result = response.message;
                    const rows = reader === "receipt_costs" ? result.rows : [...result.issues, ...result.receipts, ...(result.manufactures || [])];
                    if (!rows.length && !frm.doc.receipt_cost_loaded_at) {
                        frappe.msgprint("没有已提交且关联本流程单的所选费用来源，费用未更改。"); return;
                    }
                    const amount = value => format_currency(value || 0, result.currency);
                    message += rows.length ? `本次材料费 ${amount(result.totals.material)}、加工费 ${amount(result.totals.processing)}、其他费 ${amount(result.totals.other)}。` :
                        "此前带入的所选费用已无有效来源，材料费、加工费和其他费将清零。";
                    if (reader === "posted_flow_costs") {
                        const sources = [
                            ...result.issues.map(row => `物料出库 ${row.issue}`),
                            ...result.receipts.map(row => `委外回货 ${row.receipt}`),
                            ...(result.manufactures || []).map(row => `普通制造 ${row.manufacture}（工单 ${row.work_order}）`),
                        ];
                        message += `本次来源：${[...new Set(sources)].map(value => frappe.utils.escape_html(value)).join("、") || "无"}。普通制造只计凭证耗料与附加费，WIP移库和成品入库价值不重复相加。`;
                    }
                    message += "不包含尚未过账费用；若批次追加加工费已计入委外收货，请先清零该笔追加费，避免重复计算。";
                } else {
                    message = "读取流程单预计费用并选择流程估算口径；以后保存保留人工费用，不会自动带入已过账费用。";
                }
                if (!frm.doc.cost_basis && frm.doc.receipt_cost_loaded_at)
                    message += "历史实际费用口径尚未确认，本操作将明确选择费用口径。";
                message += frm.is_dirty() ? "当前未保存的批次分类和其它输入将一起按正常权限保存，基础费用由所选来源带入。" : "执行时重新读取当前费用来源并保存试算。";
                message += "确认继续？";
                const confirmed = await new Promise(resolve => frappe.confirm(message, () => resolve(true), () => resolve(false)));
                if (!confirmed || !current()) return;
                await frm.call(method, { preserve_input: frm.is_dirty() });
                if (current()) await frm.reload_doc();
            };
            frm.add_custom_button("读取流程单预计费用", () => chooseCosts("load_flow_costs", "读取流程单预计费用"));
            frm.add_custom_button("带入委外回货实际费用", () => chooseCosts("load_receipt_costs", "仅带入委外回货实际费用", "receipt_costs"));
            frm.add_custom_button("带入已过账实际费用", () => chooseCosts("load_posted_costs", "带入流程物料出库、普通制造与委外回货的已过账实际费用", "posted_flow_costs"));
            if (["委外回货实际", "流程已过账实际"].includes(frm.doc.cost_basis)) {
                const reader = frm.doc.cost_basis === "委外回货实际" ? "receipt_costs" : "posted_flow_costs";
                frm.add_custom_button("重新核算当前实际费用", () => chooseCosts(
                    "recalculate_actual_costs", `按当前${frm.doc.cost_basis}口径重新核算`, reader));
            }
        }
    }
});
