frappe.pages["jifeng-order-material-workbench"].on_page_load = wrapper => {
    wrapper.orderMaterials = new JifengOrderMaterialWorkbench(wrapper);
    window.jifeng_order_material_workbench = wrapper.orderMaterials;
};
frappe.pages["jifeng-order-material-workbench"].on_page_show = wrapper => wrapper.orderMaterials.show();

class JifengOrderMaterialWorkbench {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({ parent: wrapper, title: "订单算料", single_column: true });
        this.states = {};
        this.request = 0;
        this.root = document.createElement("section");
        this.root.className = "jf-order-materials";
        this.page.main[0].appendChild(this.root);
        this.root.innerHTML = `<style>
            .jf-order-materials{padding:16px}.jf-material-filters{display:flex;align-items:end;gap:12px;flex-wrap:wrap}
            .jf-material-filters>div{min-width:150px;flex:1}.jf-material-actions{display:flex;gap:10px;align-items:center;margin:10px 0}
            .jf-material-table{overflow:auto;max-height:65vh}.jf-order-materials table{white-space:nowrap;margin:0}
            .jf-order-materials th{background:#eaf3ff;color:#1e497d;position:sticky;top:0;z-index:1}
            .jf-order-materials td,.jf-order-materials th{padding:8px 12px}.jf-order-materials tbody tr{cursor:pointer}
            .jf-order-materials tbody tr:hover{background:var(--subtle-fg)}.jf-material-note{color:var(--text-muted);margin:10px 0}
            .jf-material-totals{margin:12px 0;font-weight:600}
        </style><div class="jf-material-filters" data-material-filters></div>
        <div class="jf-material-actions">
            <button class="btn btn-primary btn-sm" data-material-query>查询</button>
            <button class="btn btn-default btn-sm" data-material-export disabled>导出</button>
            <label data-material-date-option><input type="checkbox" data-material-specified checked> 指定日期</label>
            <select class="form-control input-sm" style="width:90px" aria-label="打印格式" data-material-format><option>格式1</option></select>
            <button class="btn btn-default btn-sm" data-material-print disabled>打印</button>
        </div><p class="jf-material-note">读取已保存的本单物料分析。日期采用销售订单日期；手工及外部流程采用流程建单日期。金额为公司本币预算；双击查看本单分析和领料凭证。</p>
        <div class="jf-material-table" data-material-grid></div><div class="jf-material-totals" data-material-totals></div>
        <div data-material-status role="status"></div>`;
        this.controls = {};
        const fields = [
            { fieldname: "from_date", fieldtype: "Date", label: "开始日期", default: frappe.datetime.get_today(), reqd: 1 },
            { fieldname: "to_date", fieldtype: "Date", label: "结束日期", default: frappe.datetime.get_today(), reqd: 1 },
            { fieldname: "keyword", fieldtype: "Data", label: "输入查询关键字" },
            { fieldname: "company", fieldtype: "Link", options: "Company", label: "公司", reqd: 1 },
            { fieldname: "flow_order", fieldtype: "Link", options: "Jifeng Flow Order", label: "指定流程单" },
        ];
        for (const df of fields) {
            const parent = document.createElement("div");
            this.root.querySelector("[data-material-filters]").appendChild(parent);
            this.controls[df.fieldname] = frappe.ui.form.make_control({ parent, df, render_input: true });
            if (df.default) this.controls[df.fieldname].set_value(df.default);
        }
        this.controls.company.set_value(frappe.defaults.get_user_default("Company") || "");
        this.controls.flow_order.get_query = () => ({ filters: { company: this.value("company") } });
        this.root.querySelector("[data-material-query]").addEventListener("click", () => this.guard(() => this.query()));
        this.root.querySelector("[data-material-export]").addEventListener("click", () => this.export());
        this.root.querySelector("[data-material-print]").addEventListener("click", () => this.print());
        this.root.querySelector("[data-material-grid]").addEventListener("dblclick", event => {
            const row = event.target.closest("[data-material-index]");
            if (row) this.guard(() => this.openRow(Number(row.dataset.materialIndex)));
        });
        for (const type of ["input", "change"]) this.root.addEventListener(type, () => this.invalidate());
    }
    value(field) { return this.controls[field].get_value(); }
    currentFilters() {
        const filters = Object.fromEntries(["company", "from_date", "to_date", "keyword", "flow_order"].map(key => [key, this.value(key) || ""]));
        filters.specified_date = this.mode === "summary" || this.root.querySelector("[data-material-specified]").checked ? 1 : 0;
        return filters;
    }
    invalidate() {
        if (this.resultKey !== JSON.stringify(this.currentFilters())) {
            this.root.querySelector("[data-material-export]").disabled = true;
            this.root.querySelector("[data-material-print]").disabled = true;
            if (this.data) this.status("筛选条件已变化，请重新查询。");
        }
    }
    show() {
        const next = frappe.get_route()[1] === "summary" ? "summary" : "analysis";
        if (next === this.mode) return;
        if (this.mode) this.states[this.mode] = { filters: this.currentFilters(), data: this.data, resultKey: this.resultKey };
        this.mode = next;
        this.page.set_title(next === "summary" ? "订单算料汇总" : "订单算料");
        this.root.querySelector("[data-material-date-option]").hidden = next === "summary";
        this.root.querySelector("[data-material-format]").hidden = next === "summary";
        this.root.querySelector("[data-material-print]").hidden = next === "summary";
        const state = this.states[next];
        if (state) {
            for (const [key, value] of Object.entries(state.filters)) {
                if (this.controls[key]) this.controls[key].set_value(value);
            }
            this.root.querySelector("[data-material-specified]").checked = Boolean(state.filters.specified_date);
        }
        this.data = state?.data || null;
        this.resultKey = state?.resultKey || "";
        this.render();
    }
    status(text) { this.root.querySelector("[data-material-status]").textContent = text; }
    async guard(action) {
        try { await action(); }
        catch (error) { if (!error?._server_messages) frappe.msgprint(error.message || "操作未完成，请核对提示。"); }
    }
    async query() {
        const filters = this.currentFilters(), key = JSON.stringify(filters), request = ++this.request, mode = this.mode;
        if (!filters.company || !filters.from_date || !filters.to_date) throw new Error("请选择公司、开始日期和结束日期。");
        this.data = null;
        this.render();
        this.status("正在查询…");
        try {
            const response = await frappe.call({ method: "jifeng_erp.order_material_workbench.query", args: { filters } });
            if (request !== this.request || mode !== this.mode || key !== JSON.stringify(this.currentFilters())) return;
            this.data = response.message;
            this.resultKey = key;
            this.render();
            this.status(`共 ${this.rows().length} 行；${this.data.flows.length} 张流程单。未配料流程请打开流程单填写本单分析。`);
        } catch (error) { if (request === this.request) { this.data = null; this.render(); this.status("查询未完成，请核对提示后重新查询。"); } throw error; }
    }
    rows() { return this.data ? (this.mode === "summary" ? this.data.summary : this.data.materials) : []; }
    columns() {
        const columns = this.mode === "summary" ? [
            ["date", "日期"], ["order", "单号"], ["customer", "客户"], ["category", "类别"],
            ["material_code", "材料编号"], ["material_name", "名称"], ["specification", "规格"],
            ["shape", "形状"], ["color", "颜色"], ["stock", "库存"], ["uom", "单位"],
            ["quantity", "合计数量"], ["budget_amount", "合计金额"],
        ] : [
            ["date", "日期"], ["delivery_date", "交期"], ["customer", "客户"], ["flow_order", "流程单"],
            ["product_code", "编号"], ["group", "分组"], ["category", "类别"],
            ["material_code", "材料编号"], ["material_name", "名称"], ["material", "材质"],
            ["specification", "规格"], ["shape", "形状"], ["color", "颜色"], ["rule", "规则"],
            ["planned_qty", "订单数"], ["quantity", "数量"], ["budget_qty", "预算数"], ["budget_amount", "预算金"],
        ];
        return [...columns, ...(this.mode === "summary" ? [] : [["uom", "配方单位"]]), ["currency", "币种"], ["date_basis", "日期依据"],
            ...(this.mode === "summary" ? [] : [["qty_per_unit", "单件用量"], ["unit_price", "预算单价"], ["material_row", "分析行"]])];
    }
    cell(row, field) {
        if (this.mode === "summary" && ["date", "order", "customer", "date_basis"].includes(field)) {
            const values = [...new Set(row.sources.map(source => source[field] || "").filter(Boolean))];
            return values.length > 1 ? `多项 (${values.length})` : values[0] || "";
        }
        return row[field] ?? "";
    }
    table() {
        const esc = value => frappe.utils.escape_html(String(value));
        const columns = this.columns(), rows = this.rows();
        return `<table class="table table-bordered"><thead><tr>${columns.map(([, label]) => `<th>${esc(label)}</th>`).join("")}</tr></thead>
            <tbody>${rows.map((row, index) => `<tr data-material-index="${index}">${columns.map(([field]) => {
                const value = this.cell(row, field);
                return `<td${value === "" ? ' title="来源未提供此项数据"' : ""}>${value === "" ? "—" : esc(value)}</td>`;
            }).join("")}</tr>`).join("")}</tbody></table>`;
    }
    render() {
        this.root.querySelector("[data-material-grid]").innerHTML = this.table();
        const quantities = new Map(), amounts = new Map();
        for (const row of this.rows()) {
            quantities.set(row.uom, (quantities.get(row.uom) || 0) + Number(row.quantity));
            amounts.set(row.currency, (amounts.get(row.currency) || 0) + Number(row.budget_amount));
        }
        this.root.querySelector("[data-material-totals]").textContent = [
            ...[...quantities].map(([unit, qty]) => `合计数量：${qty.toLocaleString("zh-CN", { maximumFractionDigits: 6 })} ${unit}`),
            ...[...amounts].map(([currency, amount]) => `合计金额：${amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`),
        ].join("；") || "0 条";
        const current = Boolean(this.data && this.resultKey === JSON.stringify(this.currentFilters()));
        this.root.querySelector("[data-material-export]").disabled = !(current && this.data.can_export);
        this.root.querySelector("[data-material-print]").disabled = !(current && this.data.can_print);
    }
    ensureCurrent() {
        if (!this.data || this.resultKey !== JSON.stringify(this.currentFilters())) throw new Error("请重新查询当前筛选条件。");
    }
    async openRow(index) {
        this.ensureCurrent();
        const row = this.rows()[index];
        if (!row) return;
        if (this.mode === "summary") {
            const dialog = new frappe.ui.Dialog({ title: "汇总来源明细", size: "extra-large", fields: [{ fieldname: "sources", fieldtype: "HTML" }] });
            const host = dialog.fields_dict.sources.$wrapper[0];
            for (const source of row.sources) {
                const button = document.createElement("button");
                button.className = "btn btn-default btn-sm";
                button.style.margin = "6px";
                button.textContent = `${source.flow_order} / ${source.material_row}：${source.quantity} ${row.uom}，${source.budget_amount} ${row.currency}`;
                button.addEventListener("click", () => this.guard(() => this.detail(source.flow_order)));
                host.appendChild(button);
            }
            dialog.set_secondary_action(() => dialog.hide());
            dialog.set_secondary_action_label("关闭");
            dialog.show();
            return;
        }
        await this.detail(row.flow_order);
    }
    async detail(flow) {
        if (this.issueDialog?.is_visible) { frappe.msgprint("请先关闭当前用料明细。"); return; }
        const company = this.value("company");
        const response = await frappe.call({ method: "jifeng_erp.order_material_workbench.detail", args: { flow_order: flow, company } });
        const result = response.message, esc = value => frappe.utils.escape_html(String(value));
        const dialog = new frappe.ui.Dialog({ title: "本单用料 / 领料", size: "extra-large", fields: [
            { fieldname: "details", fieldtype: "HTML" },
            { fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: "领料来源仓库", hidden: !result.can_create_issue },
        ] });
        const host = dialog.fields_dict.details.$wrapper[0];
        host.innerHTML = `<table class="table table-bordered"><thead><tr><th>材料编号</th><th>库存单位</th><th>分析需用</th><th>已提交领料</th><th>剩余可领</th></tr></thead><tbody>${result.materials.map(row =>
            `<tr>${[row.item_code, row.stock_uom, row.required, row.issued, row.remaining].map(value => `<td>${esc(value)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
        const open = document.createElement("button");
        open.className = "btn btn-default btn-sm";
        open.textContent = "打开本单分析";
        open.addEventListener("click", () => this.openNative("Jifeng Flow Order", flow));
        host.appendChild(open);
        host.insertAdjacentHTML("beforeend", this.productionMarkup(result.production, result.materials, esc));
        for (const voucher of result.vouchers) {
            const button = document.createElement("button");
            button.className = "btn btn-default btn-sm";
            button.style.margin = "6px";
            button.textContent = `领料 ${voucher.name}（${["草稿", "已提交", "已取消"][voucher.docstatus]}）`;
            button.addEventListener("click", () => this.openNative("Stock Entry", voucher.name));
            host.appendChild(button);
        }
        if (result.production?.has_active_work_order) {
            for (const work of result.production.work_orders.filter(row => row.docstatus < 2)) {
                const button = document.createElement("button");
                button.className = "btn btn-primary btn-sm";
                button.style.margin = "6px";
                button.textContent = `打开工单 ${work.name}（${work.skip_transfer ? "直接制造" : "转入在制仓"}）`;
                button.addEventListener("click", () => this.openNative("Work Order", work.name));
                host.appendChild(button);
                for (const source of work.sources) {
                    const sourceButton = document.createElement("button");
                    sourceButton.className = "btn btn-default btn-sm";
                    sourceButton.style.margin = "6px";
                    const kind = source.purpose === "Material Transfer for Manufacture" ? "转入WIP" :
                        source.purpose === "Manufacture" ? "制造" : "制造耗用";
                    sourceButton.textContent = `${kind} ${source.name}（${["草稿", "已提交", "已取消"][source.docstatus]}${source.is_return ? "，退回" : ""}）`;
                    sourceButton.addEventListener("click", () => this.openNative("Stock Entry", source.name));
                    host.appendChild(sourceButton);
                }
            }
        }
        dialog.fields_dict.warehouse.get_query = () => ({ filters: { company, is_group: 0, disabled: 0 } });
        if (result.can_create_work_order) {
            const netIssued = result.materials.some(row => Number(row.issued) > 0);
            const createWork = document.createElement("button");
            createWork.className = "btn btn-primary btn-sm";
            createWork.style.margin = "6px";
            createWork.textContent = "生成并打开生产工单";
            createWork.disabled = netIssued;
            createWork.title = netIssued ? "有净领料时，先逐张打开原领料单；仅退回确认尚未耗用的实物后才能创建工单。" : "按已提交BOM和流程计划创建工单。";
            createWork.addEventListener("click", () => this.guard(() => this.createWorkOrder(dialog, result, company, createWork)));
            host.appendChild(createWork);
        }
        if (result.can_create_issue) {
            const issueButton = document.createElement("button");
            issueButton.className = "btn btn-default btn-sm";
            issueButton.style.margin = "6px";
            issueButton.textContent = result.production?.bom ? "手工领料出库（费用路径）" : "生成领料出库草稿";
            issueButton.addEventListener("click", () => this.guard(async () => {
                if (result.production?.bom && !await new Promise(resolve => frappe.confirm(
                    "手工领料会按费用耗用记录。若之后要用同一流程的工单制造，只能将确认尚未耗用的实物按原领料单退回；已耗用材料不能再次转入工单。继续生成手工领料草稿？",
                    () => resolve(true), () => resolve(false)))) return;
                await this.createIssue(dialog, result, company, issueButton);
            }));
            host.appendChild(issueButton);
        }
        dialog.set_secondary_action(() => dialog.hide());
        dialog.set_secondary_action_label("关闭");
        const hide = dialog.hide.bind(dialog);
        dialog.hide = () => {
            if (this.busy || this.unknown) { frappe.msgprint(this.get_close_block()); return; }
            hide();
            if (this.issueDialog === dialog) this.issueDialog = null;
        };
        dialog.show();
        this.issueDialog = dialog;
    }
    productionMarkup(production, flowMaterials, esc) {
        if (!production?.bom) return `<h4>生产工单</h4><p>本流程未配置BOM；仅显示本单用料分析和手工领料，不提供工单制造入口。</p>`;
        const flowByItem = new Map(flowMaterials.map(row => [row.item_code, row]));
        const workOrders = production.work_orders.map(work => {
            const rows = work.items.map(row => {
                const flowRow = flowByItem.get(row.item_code);
                const flowRequired = flowRow ? `${flowRow.required} ${flowRow.stock_uom}` : "配方未列出";
                return `<tr>${[row.item_code, row.stock_uom, flowRequired, row.required_qty, row.transferred_qty,
                    row.consumed_qty, row.pending_transfer, row.source_warehouse || "未指定", row.net_issue_qty].map(value => `<td>${esc(value)}</td>`).join("")}</tr>`;
            }).join("");
            const state = ["草稿", "已提交", "已取消"][work.docstatus] || "未知";
            const movement = work.skip_transfer ? "直接从工单来源仓取料制造" : "先转入在制仓，再进行制造";
            return `<h5>工单 ${esc(work.name)}（${state}，${esc(work.status)}）</h5>
                <p>BOM ${esc(work.bom_no)}；流程计划 ${esc(work.flow_planned_qty)}，工单计划 ${esc(work.planned_qty)}；已制造 ${esc(work.produced_qty)}；净转入WIP ${esc(work.transferred_qty)}；${movement}；源仓 ${esc(work.source_warehouse || "未指定")}；WIP仓 ${esc(work.wip_warehouse || "未指定")}</p>
                <table class="table table-bordered"><thead><tr><th>材料</th><th>单位</th><th>本单用料分析需求</th><th>工单BOM需求</th><th>净转入WIP</th><th>实际耗用</th><th>待转入</th><th>来源仓</th><th>费用路径净领料</th></tr></thead><tbody>${rows}</tbody></table>`;
        }).join("");
        return `<h4>生产工单（实际来源）</h4><p>BOM：${esc(production.bom)}。流程分析需求与工单BOM需求分列显示；本单预算/分析不代表实际投料。转入、耗用和产量取自工单记录；转入在制仓不作为制造消耗成本。</p>${workOrders || "<p>尚无有效工单。创建工单前，已提交净领料必须为零；仅将确认未耗用的实物逐张退回原领料单。</p>"}`;
    }
    async createWorkOrder(dialog, source, company, actionButton) {
        if (this.busy || this.unknown) return;
        this.busy = true;
        actionButton.disabled = true;
        try {
            const response = await frappe.call({ method: "jifeng_erp.order_material_workbench.create_work_order", type: "POST",
                args: { flow_order: source.flow_order, flow_modified: source.flow_modified, company } });
            this.busy = false;
            dialog.hide();
            this.openNative("Work Order", response.message.name);
        } catch (error) {
            this.busy = false;
            this.unknown = true;
            actionButton.disabled = false;
            actionButton.textContent = "核对工单结果";
            actionButton.onclick = () => this.guard(() => this.checkWorkOrder(dialog, source.flow_order, company));
            frappe.msgprint("工单创建结果尚未确认。请先核对本流程现有工单，避免重复创建。");
        }
    }
    async checkWorkOrder(dialog, flow, company) {
        if (this.busy) return;
        this.busy = true;
        try {
            const response = await frappe.call({ method: "jifeng_erp.order_material_workbench.detail", args: { flow_order: flow, company } });
            this.unknown = false;
            this.busy = false;
            dialog.hide();
            const works = response.message.production.work_orders.filter(row => row.docstatus < 2);
            if (works.length === 1) this.openNative("Work Order", works[0].name);
            else { frappe.msgprint("已核对现有工单，请重新打开本单用料/生产明细。没有唯一有效工单时不会自动重试创建。"); await this.detail(flow); }
        } finally { this.busy = false; }
    }
    async createIssue(dialog, source, company, actionButton) {
        if (this.busy || this.unknown) return;
        const warehouse = dialog.get_value("warehouse");
        if (!warehouse) throw new Error("请选择领料来源仓库。");
        this.busy = true;
        actionButton.disabled = true;
        try {
            const response = await frappe.call({ method: "jifeng_erp.order_material_workbench.create_issue", type: "POST",
                args: { flow_order: source.flow_order, flow_modified: source.flow_modified, company, warehouse } });
            this.busy = false;
            dialog.hide();
            this.openNative("Stock Entry", response.message.name);
        } catch (error) {
            this.busy = false;
            this.unknown = true;
            actionButton.disabled = false;
            actionButton.textContent = "核对领料草稿";
            actionButton.onclick = () => this.guard(() => this.checkIssue(dialog, source.flow_order, company));
            frappe.msgprint("领料操作未确认完成。请核对现有凭证后继续，避免重复生成。");
        }
    }
    async checkIssue(dialog, flow, company) {
        if (this.busy) return;
        this.busy = true;
        try {
            const response = await frappe.call({ method: "jifeng_erp.order_material_workbench.detail", args: { flow_order: flow, company } });
            this.unknown = false;
            this.busy = false;
            dialog.hide();
            const drafts = response.message.vouchers.filter(row => row.docstatus === 0);
            if (drafts.length === 1) this.openNative("Stock Entry", drafts[0].name);
            else { frappe.msgprint("已核对现有凭证，请重新打开本单用料明细检查状态。"); await this.detail(flow); }
        } finally { this.busy = false; }
    }
    openNative(doctype, name) {
        if (this.nativeForm) { frappe.msgprint("请先关闭当前单据。"); return; }
        const dialog = new frappe.ui.Dialog({ title: name, size: "extra-large", fields: [{ fieldname: "form", fieldtype: "HTML" }] });
        const frame = document.createElement("iframe");
        frame.title = name;
        frame.style.cssText = "width:100%;height:70vh;border:0";
        frame.src = frappe.utils.get_form_link(doctype, name);
        dialog.fields_dict.form.$wrapper[0].appendChild(frame);
        const hide = dialog.hide.bind(dialog);
        dialog.hide = () => {
            if (frame.contentWindow?.cur_frm?.is_dirty()) { frappe.confirm("关闭并放弃未保存的修改？", () => { hide(); frame.remove(); this.nativeForm = null; }); }
            else { hide(); frame.remove(); this.nativeForm = null; }
        };
        dialog.set_secondary_action(() => dialog.hide());
        dialog.set_secondary_action_label("关闭");
        dialog.show();
        this.nativeForm = frame;
    }
    is_dirty() { return Boolean(this.busy || this.unknown || this.nativeForm?.contentWindow?.cur_frm?.is_dirty()); }
    get_close_block() { return this.busy ? "正在生成或核对领料草稿，请稍后关闭。" : this.unknown ? "领料保存结果尚未核实，请先核对现有凭证。" : ""; }
    export() {
        this.ensureCurrent();
        if (!this.data.can_export) return;
        const quote = value => {
            let text = String(value ?? "");
            if (/^[\s]*[=+\-@]/.test(text)) text = "'" + text;
            return '"' + text.replaceAll('"', '""') + '"';
        };
        const columns = this.columns();
        const csv = [columns.map(([, label]) => quote(label)).join(","),
            ...this.rows().map(row => columns.map(([field]) => quote(this.cell(row, field))).join(","))].join("\r\n");
        const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = `${this.mode === "summary" ? "订单算料汇总" : "订单算料"}-${frappe.datetime.get_today()}.csv`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    print() {
        this.ensureCurrent();
        if (!this.data.can_print || this.mode !== "analysis") return;
        const target = window.open("", "_blank");
        if (!target) { frappe.msgprint("请允许打开打印窗口。"); return; }
        target.document.write(`<html lang="zh-CN"><head><title>订单算料 · 格式1</title><style>body{font:12px sans-serif}table{border-collapse:collapse}td,th{border:1px solid #999;padding:6px} @page{size:landscape}</style></head><body><h2>订单算料 · 格式1</h2>${this.table()}</body></html>`);
        target.document.close();
        target.focus();
        target.print();
    }
}
