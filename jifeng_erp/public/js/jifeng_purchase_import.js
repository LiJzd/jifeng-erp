/* Spreadsheet intake shares the native Purchase Order and its save/submit flow. */
frappe.ui.form.on("Purchase Order", {
    refresh(frm) {
        if (!window.JifengPurchaseImport.canEdit(frm)) return;
        frm.add_custom_button(__("批量导入采购明细"), () => {
            if (!frm.doc.company || !frm.doc.supplier || !frm.doc.transaction_date) {
                return frappe.msgprint(__("请先选择公司、供应商和采购日期。"));
            }
            const existing = frm._jifeng_purchase_import;
            if (existing && !existing.closed && existing.current()) return existing.dialog.show();
            existing?.dialog.hide();
            frm._jifeng_purchase_import = new window.JifengPurchaseImport(frm);
        }, __("集沣 ERP"));
    },
});

window.JifengPurchaseImport = class JifengPurchaseImport {
    static canEdit(frm) {
        return frm.doc?.docstatus === 0 && !frm.read_only && !frm.doc.is_subcontracted &&
            !frm.doc.is_old_subcontracting_flow && !frm.doc.has_unit_price_items &&
            !!frm.has_perm?.(frm.is_new() ? "create" : "write") &&
            !!frm.fields_dict.items?.grid?.is_editable();
    }
    constructor(frm) {
        this.frm = frm; this.document = frm.doc; this.closed = false; this.busy = false;
        this.snapshot = JSON.stringify(frm.doc); this.preview = null; this.file = null; this.revision = 0;
        this.context = { name: frm.is_new() ? null : frm.doc.name, modified: frm.is_new() ? null : frm.doc.modified };
        for (const key of ["company", "supplier", "transaction_date", "schedule_date", "currency", "conversion_rate",
            "buying_price_list", "price_list_currency", "plc_conversion_rate", "set_warehouse", "ignore_pricing_rule",
            "tax_category", "project", "cost_center"]) if (frm.doc[key] != null) this.context[key] = frm.doc[key];
        this.dialog = new frappe.ui.Dialog({ title: __("批量导入采购明细"), size: "extra-large", no_submit_on_enter: true,
            fields: [{ fieldname: "instructions", fieldtype: "HTML" },
                { fieldname: "text", fieldtype: "Small Text", label: __("从Excel粘贴（含表头）"),
                    description: __("必填列：商品编码/条码、数量、单价。可选列：单位、交货日期、仓库。"),
                    onchange: () => this.invalidate() },
                { fieldname: "file", fieldtype: "HTML" }, { fieldname: "preview", fieldtype: "HTML" }],
            primary_action_label: __("预览并校验"), primary_action: () => this.prepare() });
        this.dialog.on_hide = () => { this.closed = true; this.revision++; };
        this.dialog.fields_dict.instructions.$wrapper.get(0).append(this.element("p",
            __("可先把扫描结果录到Excel，再整批粘贴或上传。每批最多500行；原采购明细保留，新行追加。填入后核对原生金额，再保存、提交。")));
        this.picker = this.element("input"); this.picker.type = "file"; this.picker.accept = ".xlsx,.csv";
        this.picker.setAttribute("aria-label", __("上传采购明细表格"));
        this.picker.addEventListener("change", () => { this.file = this.picker.files?.[0] || null; this.invalidate(); });
        this.dialog.fields_dict.file.$wrapper.get(0).append(this.picker);
        this.host = this.dialog.fields_dict.preview.$wrapper.get(0);
        this.status = this.element("p", __("请选择文件或粘贴表格，然后预览。")); this.status.setAttribute("role", "status");
        this.grid = this.element("div"); this.grid.style.cssText = "overflow:auto;max-height:38vh";
        this.host.append(this.status, this.grid);
        this.dialog.add_custom_action(__("下载Excel模板"), () => {
            window.open("/api/method/jifeng_erp.purchase_import.download_template", "_blank", "noopener");
        });
        this.dialog.add_custom_action(__("重新预览"), () => this.prepare());
        this.dialog.show();
        this.dialog.fields_dict.text.$input?.on("input.jifeng-purchase-import", () => this.invalidate());
    }
    element(tag, text, classes) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = String(text ?? "");
        if (classes) node.className = classes;
        return node;
    }
    current() {
        return !this.closed && this.frm.doc === this.document &&
            JSON.stringify(this.frm.doc) === this.snapshot && this.constructor.canEdit(this.frm);
    }
    invalidate() {
        this.revision++; this.preview = null; this.grid.replaceChildren();
        this.status.textContent = __("表格内容已变化，请重新预览。");
        this.dialog.set_primary_action(__("预览并校验"), () => this.prepare());
    }
    freeze() {
        this.dialog.get_primary_btn().prop("disabled", this.busy);
        this.picker.disabled = this.busy;
        this.dialog.set_df_property("text", "read_only", this.busy ? 1 : 0);
    }
    async source() {
        const text = this.dialog.get_value("text") || "";
        if (text.trim() && this.file) throw Error(__("请只选择上传文件或粘贴表格中的一种。"));
        if (!this.file) {
            if (!text.trim()) throw Error(__("请选择文件或粘贴采购明细。"));
            return { text };
        }
        if (!/\.(xlsx|csv)$/i.test(this.file.name) || !this.file.size || this.file.size > 1048576) {
            throw Error(__("请选择非空且不超过1MiB的.xlsx或.csv文件。"));
        }
        const bytes = new Uint8Array(await this.file.arrayBuffer());
        let binary = "";
        for (let index = 0; index < bytes.length; index += 16384) binary += String.fromCharCode(...bytes.subarray(index, index + 16384));
        return { filename: this.file.name, content_base64: btoa(binary) };
    }
    async prepare() {
        if (this.busy || this.closed) return;
        if (!this.current()) { this.status.textContent = __("采购单已变化，请关闭导入窗口后重新打开。"); return; }
        const revision = ++this.revision; this.busy = true; this.preview = null; this.freeze();
        this.status.textContent = __("正在识别商品并校验采购明细…");
        this.dialog.set_primary_action(__("预览并校验"), () => this.prepare());
        try {
            const input = await this.source();
            if (!this.current() || revision !== this.revision) return;
            const response = await frappe.call({ method: "jifeng_erp.purchase_import.preview_import", type: "POST",
                args: { context: this.context, ...input } });
            if (!this.current() || revision !== this.revision) return;
            const result = response.message;
            if (!result || result.company !== this.context.company || result.supplier !== this.context.supplier ||
                !Array.isArray(result.rows) || result.rows.length !== result.row_count || result.row_count > 500 ||
                result.error_count !== result.rows.filter(row => !!row.error).length ||
                result.can_apply !== (!!result.row_count && result.error_count === 0)) throw Error(__("导入预览响应无效，请重新预览。"));
            this.preview = result; this.render(result);
            if (result.can_apply) this.dialog.set_primary_action(__(`填入 ${result.row_count} 行采购明细`), () => this.apply());
        } catch (error) {
            if (!this.closed) this.status.textContent = error.message || __("预览失败，请核对表格或重试。");
        } finally {
            this.busy = false;
            if (!this.closed) {
                if (!this.current()) this.status.textContent = __("采购单已变化，请关闭导入窗口后重新打开。");
                this.freeze();
            }
        }
    }
    render(result) {
        const table = this.element("table", undefined, "table table-bordered table-sm");
        const head = this.element("tr"), thead = this.element("thead"), body = this.element("tbody");
        for (const label of ["表格行", "输入编码/条码", "识别商品", "名称", "数量", "单价", "单位", "交货日期", "仓库", "校验结果"]) head.append(this.element("th", __(label)));
        thead.append(head); table.append(thead, body);
        for (const entry of result.rows) {
            const item = entry.item || {}, row = this.element("tr");
            for (const value of [entry.row_number, entry.code, item.item_code, item.item_name, item.qty, item.rate,
                item.uom, item.schedule_date, item.warehouse, entry.error || __("通过")]) row.append(this.element("td", value));
            if (entry.error) row.className = "text-danger";
            body.append(row);
        }
        this.grid.replaceChildren(table);
        this.status.textContent = result.error_count ? __(`共 ${result.row_count} 行，${result.error_count} 行有误；请修改原表格后重新预览。`) :
            __(`共 ${result.row_count} 行校验通过，填入后仍需保存采购单。`);
    }
    apply() {
        if (this.busy || !this.preview?.can_apply || !this.current()) return;
        this.busy = true; this.freeze();
        const rows = this.preview.rows;
        // All mutations below are synchronous, so double clicks cannot append twice.
        const existing = this.frm.doc.items || [];
        if (existing.length === 1 && existing[0].__islocal && !existing[0].item_code && !existing[0].qty &&
            !existing[0].rate && !existing[0].description && !existing[0].jf_flow_order &&
            !existing[0].material_request_item && !existing[0].supplier_quotation_item) this.frm.clear_table("items");
        for (const entry of rows) this.frm.add_child("items", entry.item);
        this.frm.refresh_field("items"); this.frm.dirty();
        this.frm.cscript.calculate_taxes_and_totals();
        this.preview = null; this.dialog.hide();
        frappe.show_alert({ message: __(`已填入 ${rows.length} 行，请核对金额后保存采购单。`), indicator: "green" });
    }
};
