function jifeng_cost_preview_plan_input(frm) {
    frm.set_df_property("planned_qty", "read_only", Boolean(frm.doc.flow_order));
    frm.set_df_property("planned_qty", "description", frm.doc.flow_order
        ? "按关联流程单已核对的库存数量试算，保存或核算时重新读取；回货分类也须使用库存单位。"
        : "手工试算请填写商品库存数量；回货分类也须使用库存单位。");
}

frappe.ui.form.on("Jifeng Cost Preview", {
    async flow_order(frm) {
        jifeng_cost_preview_plan_input(frm);
        const name = frm.doc.flow_order;
        if (!name) return;
        const flow = await frappe.db.get_doc("Jifeng Flow Order", name);
        if (frm.doc.flow_order !== name) return;
        if (!["stock_v1", "manual_verified_v1"].includes(flow.quantity_basis) || !flow.stock_uom) {
            frappe.msgprint("所选流程单计划数量的库存单位尚未核对，请先打开流程单确认计划库存单位和数量。");
            return;
        }
        // Keep existing batch classifications and fee selections when a Flow
        // link changes, while using its inventory-unit plan for the trial.
        if ((frm.doc.batches || []).length) {
            await frm.set_value("planned_qty", flow.planned_qty);
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
        jifeng_cost_preview_plan_input(frm);
        const basis = frm.doc.cost_basis || (frm.doc.receipt_cost_loaded_at ? "历史实际费用口径待确认" : "流程估算");
        frm.set_intro(`费用口径：${basis}。实际口径保存或重新核算时重读当前已提交来源，撤销后净费用随之更新；无待保存修改时，请使用“重新核算当前实际费用”。正式制造损耗后补产，按有效来源核对新增投入，流程目标库存量保留原值。估算口径保留人工费用。试算不产生库存或财务凭证。`, "blue");
        if (!frm.is_new() && frm.doc.flow_order) {
            frm.add_custom_button("读取已提交制造入库批次", async () => {
                const flowOrder = frm.doc.flow_order, previewName = frm.doc.name;
                const response = await frappe.call({
                    method: "jifeng_erp.workflow.manufacture_batch_candidates",
                    args: { flow_order: flowOrder },
                });
                if (frm.doc.name !== previewName || frm.doc.flow_order !== flowOrder) return;
                const candidates = response.message || [];
                if (!candidates.length) {
                    frappe.msgprint("没有已提交且关联本流程单的终成品制造入库。现有批次未更改；来源已撤销的批次请核对后删除。");
                    return;
                }
                const existing = new Map((frm.doc.batches || [])
                    .filter(row => row.source_manufacture).map(row => [row.source_manufacture, row]));
                const manualReferences = new Set((frm.doc.batches || [])
                    .filter(row => !row.source_manufacture && row.reference_no).map(row => row.reference_no));
                let added = 0, bound = 0, conflicts = 0;
                const changed = [], escape = value => frappe.utils.escape_html(String(value));
                for (const source of candidates) {
                    const row = existing.get(source.manufacture);
                    if (row) {
                        if (row.receipt_type !== "首次回货" || Number(row.received_qty) !== Number(source.qty)
                            || Number(row.lost_qty || 0) < Number(source.lost_qty || 0))
                            changed.push(`${escape(source.manufacture)}：实际入库 ${source.inventory_qty}，确认损耗 ${source.lost_qty}，覆盖量 ${source.qty} ${escape(source.stock_uom)}`);
                        continue;
                    }
                    if (manualReferences.has(source.manufacture)) {
                        const manual = (frm.doc.batches || []).filter(row => !row.source_manufacture
                            && row.reference_no === source.manufacture);
                        if (manual.length === 1 && !manual[0].source_receipt && !manual[0].source_receipt_item
                            && manual[0].receipt_type === "首次回货"
                            && Number(manual[0].received_qty) === Number(source.qty)
                            && Number(manual[0].lost_qty || 0) >= Number(source.lost_qty)) {
                            // Reading this exact document may bind an existing
                            // matching manual batch; keep all classifications.
                            manual[0].source_manufacture = source.manufacture;
                            existing.set(source.manufacture, manual[0]);
                            frm.dirty();
                            bound++;
                        } else conflicts++;
                        continue;
                    }
                    const child = frm.add_child("batches", {
                        receipt_type: "首次回货", reference_no: source.manufacture,
                        source_manufacture: source.manufacture, received_qty: source.qty,
                        lost_qty: source.lost_qty, pending_qty: source.inventory_qty,
                    });
                    existing.set(source.manufacture, child);
                    added++;
                }
                frm.refresh_field("batches");
                frappe.msgprint(`新增 ${added} 条制造入库批次${bound ? `；${bound} 条同单号手工批次的覆盖量和损耗吻合，已绑定来源并保留分类` : ""}${conflicts ? `；${conflicts} 条单号与未绑定制造来源的批次相同但数量、类型或来源未吻合，已跳过，请核对原批次后重新读取` : ""}。同一制造单的终成品分行合并，确认损耗只计一次，其余先列为待检；请核对合格、返工、待检分类后保存。已过账实际费用口径不能遗漏有确认损耗的制造批次。本操作不会自动保存或入账。${changed.length ? `<br><br>以下来源或分类需重新核对，已有人工分类未覆盖：<br>${changed.join("<br>")}` : ""}`);
            });
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
                    const rows = reader === "receipt_costs" ? result.rows : [...result.issues, ...result.receipts, ...(result.manufactures || []), ...(result.wages || [])];
                    if (!rows.length && !frm.doc.receipt_cost_loaded_at) {
                        frappe.msgprint("没有已提交且关联本流程单的所选费用来源，费用未更改。"); return;
                    }
                    const amount = value => format_currency(value || 0, result.currency);
                    message += rows.length ? `本次材料费 ${amount(result.totals.material)}、加工费 ${amount(result.totals.processing)}、其他费 ${amount(result.totals.other)}。` :
                        "此前带入的所选费用已无有效来源，材料费、加工费和其他费将清零。";
                    if (reader === "posted_flow_costs") {
                        const sources = [
                            ...result.issues.map(row => row.source_material_issue
                                ? `领料退库 ${row.issue}（原领料 ${row.source_material_issue}）`
                                : `物料出库 ${row.issue}`),
                            ...result.receipts.map(row => `委外回货 ${row.receipt}`),
                            ...(result.manufactures || []).map(row => `制造 ${row.manufacture}（工单 ${row.work_order}）`),
                            ...(result.wages || []).map(row => `工资计提 ${row.journal_entry}（计薪 ${row.wage_entry}）`),
                        ];
                        message += `本次来源：${[...new Set(sources)].map(value => frappe.utils.escape_html(value)).join("、") || "无"}。制造只计凭证耗料与附加费，内部转料和成品入库价值不重复相加；计薪记录须已纳入有效工资计提才计入加工费，付款不再累加费用。`;
                        const lossSources = (result.manufacture_batches || []).filter(row => Number(row.lost_qty));
                        if (lossSources.length) {
                            const escape = value => frappe.utils.escape_html(String(value));
                            message += `<br>已确认制造损耗：${lossSources.map(row => `${escape(row.manufacture)} ${escape(row.lost_qty)} ${escape(row.stock_uom)}`).join("、")}。请先读取制造入库批次并核对分类；保存会核对来源覆盖量和损耗，清空来源不能消除已确认损耗。`;
                        }
                        const coverage = (result.manufacture_batches || []).reduce((sum, row) => sum + Number(row.qty), 0);
                        if (coverage > Number(frm.doc.planned_qty))
                            message += `<br>流程目标库存量 ${Number(frm.doc.planned_qty)}，本次有效制造投入覆盖量 ${coverage}；损耗补产须全部绑定准确制造批次，保存时核对实际入库没有扩大目标。`;
                    }
                    const valuation = result.receipt_valuation;
                    const receiptRows = reader === "receipt_costs" ? result.rows : result.receipts;
                    if (valuation && (receiptRows || []).length) {
                        message += `<br>回货库存核对：成品入库净价值 ${amount(valuation.inventory_value_change)}，收回耗料及工费合计（含内部半成品）${amount(valuation.valuation_cost_basis)}，入库估价差额 ${amount(valuation.valuation_difference)}。`;
                        const differences = (receiptRows || []).filter(row => Number(row.valuation_difference));
                        if (differences.length) {
                            const escape = value => frappe.utils.escape_html(String(value ?? ""));
                            message += `<div class="table-responsive"><table class="table table-bordered table-condensed"><thead><tr><th>回货单</th><th>商品</th><th>入库净量</th><th>成品入库净价值</th><th>耗料及工费合计</th><th>入库估价差额</th></tr></thead><tbody>${differences.map(row =>
                                `<tr><td>${escape(row.receipt)}</td><td>${escape(row.item_code)}</td><td>${escape(row.inventory_qty_change)} ${escape(row.stock_uom)}</td><td>${amount(row.inventory_value_change)}</td><td>${amount(row.valuation_cost_basis)}</td><td>${amount(row.valuation_difference)}</td></tr>`
                            ).join("")}</tbody></table></div>材料、加工和其他费用按实际来源计入；估价差额单独供库存核对。`;
                        }
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
            frm.add_custom_button("带入已过账实际费用", () => chooseCosts("load_posted_costs", "带入流程库存、委外回货与工资计提的已过账实际费用", "posted_flow_costs"));
            if (["委外回货实际", "流程已过账实际"].includes(frm.doc.cost_basis)) {
                const reader = frm.doc.cost_basis === "委外回货实际" ? "receipt_costs" : "posted_flow_costs";
                frm.add_custom_button("重新核算当前实际费用", () => chooseCosts(
                    "recalculate_actual_costs", `按当前${frm.doc.cost_basis}口径重新核算`, reader));
            }
        }
    }
});
