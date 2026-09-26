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
        frm.add_custom_button("打开成本试算", () => call("make_preview", "Jifeng Cost Preview"));
        if (frm.doc.source_changed) {
            frm.set_intro("源订单有变更，请核对计划数量、采购和加工单后标记已核对。", "orange");
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
