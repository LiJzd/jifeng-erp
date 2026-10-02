frappe.ui.form.on("Jifeng Flow Order", {
    setup(frm) {
        frm.set_query("target_warehouse", () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0 } }));
    },
    refresh(frm) {
        if (frm.is_new()) return;
        const call = async (method, doctype) => {
            if (frm.is_dirty()) await frm.save();
            const r = await frm.call(method);
            await frm.reload_doc();
            if (doctype && r.message) frappe.set_route("Form", doctype, r.message);
        };
        if (frm.doc.profile) frm.add_custom_button("更新档案物料与工序", () => call("load_profile"));
        frm.add_custom_button("生成采购申请草稿", () => call("make_request", "Material Request"));
        frm.add_custom_button("生成按单配料出库草稿", () => {
            frappe.prompt([{
                fieldname: "warehouse", label: "领料来源仓库", fieldtype: "Link",
                options: "Warehouse", reqd: 1,
                get_query: () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0 } }),
            }], async (values) => {
                if (frm.is_dirty()) await frm.save();
                const result = await frm.call("make_material_issue", { warehouse: values.warehouse });
                if (result.message) frappe.set_route("Form", "Stock Entry", result.message);
            }, "选择领料仓库", "生成草稿");
        });
        frm.add_custom_button("生成生产工单草稿", () => call("make_work_order", "Work Order"));
        frm.add_custom_button("查看本流程生产工单", () =>
            frappe.set_route("List", "Work Order", { jf_flow_order: frm.doc.name }));
        frm.add_custom_button("打开成本试算", () => call("make_preview", "Jifeng Cost Preview"));
        if (!["stock_v1", "manual_verified_v1"].includes(frm.doc.quantity_basis) || !frm.doc.stock_uom) {
            frm.set_intro("本单计划数量的库存单位尚未确认。请先核对商品库存单位和数量，再生成采购、生产或成本试算。", "orange");
            frm.add_custom_button("确认计划库存单位", async () => {
                if (frm.is_dirty()) return frappe.msgprint("请先保存或放弃页面中的其他修改，再核对计划单位。");
                const response = await frm.call("get_stock_plan_context");
                const context = response.message;
                if (frm.is_dirty() || !context || context.name !== frm.doc.name ||
                    context.version !== String(frm.doc.modified) || !context.stock_uom) {
                    return frappe.msgprint("流程单或商品单位已变化，请重新读取后核对。");
                }
                const dialog = new frappe.ui.Dialog({
                    title: "确认计划库存单位",
                    fields: [
                        { fieldname: "stock_uom", fieldtype: "Data", label: "商品库存单位", read_only: 1,
                            default: context.stock_uom },
                        { fieldname: "planned_qty", fieldtype: "Float", label: "核对后的计划库存数量", reqd: 1,
                            precision: 9, description: "请按上方库存单位填写；不要直接沿用单位未明确的原数量。" },
                        { fieldname: "note", fieldtype: "Small Text", label: "数量与单位核对说明", reqd: 1 },
                    ],
                    primary_action_label: "确认并保存计划",
                    primary_action: async (values) => {
                        if (dialog.confirming) return;
                        if (frm.is_dirty() || frm.doc.name !== context.name || String(frm.doc.modified) !== context.version) {
                            dialog.hide(); return frappe.msgprint("流程单已变化，请重新读取后核对。");
                        }
                        dialog.confirming = true; dialog.get_primary_btn().prop("disabled", true);
                        try {
                            const result = await frm.call("confirm_stock_plan", {
                                expected_version: context.version, stock_uom: context.stock_uom,
                                planned_qty: values.planned_qty, note: values.note,
                            });
                            if (!result.message || result.message.name !== context.name) throw new Error("Missing plan confirmation");
                            dialog.hide(); await frm.reload_doc();
                        } catch {
                            // A missing response never authorizes another write.
                            dialog.hide();
                            try {
                                await frm.reload_doc();
                                frappe.msgprint("请核对流程单当前数量、单位和核对记录。未确认保存结果前，请勿再次提交。");
                            } catch {
                                frappe.msgprint("保存结果暂时无法读取。请恢复连接后重新打开流程单，核对数量、单位和核对记录，再决定下一步；请勿重复提交。");
                            }
                        }
                    },
                });
                dialog.show();
            });
        }
        if (frm.doc.source_changed) {
            const pending = !["stock_v1", "manual_verified_v1"].includes(frm.doc.quantity_basis) || !frm.doc.stock_uom;
            frm.set_intro(pending ? "来源单位尚未明确，请先确认计划库存单位和数量，再核对源订单变更。" :
                "源订单有变更，请核对计划数量、采购和加工单后标记已核对。", "orange");
            frm.add_custom_button("标记源单已核对", () => call("acknowledge_source"));
        }
        frm.add_custom_button("查看实际委外费用", async () => {
            const r = await frappe.call({ method: "jifeng_erp.workflow.receipt_costs", args: { flow_order: frm.doc.name } });
            const result = r.message || { rows: [], totals: {} };
            const rows = result.rows;
            if (!rows.length) return frappe.msgprint("没有已提交且关联本流程单的委外收货明细。");
            const esc = frappe.utils.escape_html;
            const money = (x) => format_currency(x, result.currency);
            const table = rows.map(x => `<tr><td>${esc(x.receipt)}</td><td>${esc(String(x.qty))}</td><td>${money(x.material)}</td><td>${money(x.processing)}</td><td>${money(x.other)}</td></tr>`).join("");
            frappe.msgprint({ title: "实际委外费用（含已提交退货冲减）", message: `<table class="table"><thead><tr><th>凭证</th><th>数量</th><th>材料费</th><th>加工费</th><th>其他费</th></tr></thead><tbody>${table}<tr><th colspan="2">合计</th><td>${money(result.totals.material)}</td><td>${money(result.totals.processing)}</td><td>${money(result.totals.other)}</td></tr></tbody></table>` });
        });
        frm.add_custom_button("查看物料库存", async () => {
            const r = await frappe.call({ method: "jifeng_erp.workflow.stock_availability", args: { flow_order: frm.doc.name } });
            const esc = (x) => frappe.utils.escape_html(String(x ?? ""));
            const rows = (r.message || []).map(x => `<tr><td>${esc(x.item_code)}</td><td>${esc(x.warehouse)}</td><td>${esc(x.actual_qty)}</td><td>${esc(x.projected_qty)}</td><td>${esc(x.stock_uom)}</td></tr>`).join("");
            frappe.msgprint({ title: "物料库存（库存单位）", message: rows ? `<table class="table"><tr><th>物料</th><th>仓库</th><th>实际库存</th><th>预计库存</th><th>单位</th></tr>${rows}</table>` : "可访问仓库中暂无这些物料的库存记录。" });
        });
    }
});
