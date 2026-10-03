// Explicit historical wage prices. This component never saves a wage document.
(() => {
    const CONTEXT_FIELDS = ["company", "employee", "operation", "work_date", "stock_uom", "job_card", "source_modified", "work_order_modified"];
    const ROW_FIELDS = ["name", "version", "work_date", "stock_uom", "piece_rate", "company", "employee", "operation"];
    const REFERENCES = ["piece_rate_reference", "piece_rate_reference_version"];
    const applying = new WeakSet();
    const translated = value => typeof __ === "function" ? __(value) : value;
    const exactFields = (value, fields) => value && typeof value === "object" && !Array.isArray(value) &&
        Object.keys(value).sort().join("\0") === [...fields].sort().join("\0");
    const decimal = value => typeof value === "string" && /^\d+(?:\.\d{1,6})?$/.test(value) && Number.isFinite(Number(value));
    const priceKey = value => {
        const text = typeof value === "number" && Number.isFinite(value) && value >= 0 ? value.toFixed(6) : value;
        if (typeof text !== "string" || !/^\d+(?:\.\d{1,6})?$/.test(text)) return null;
        const [whole, fraction = ""] = text.split(".");
        return (whole.replace(/^0+(?=\d)/, "") || "0") + "." + fraction.replace(/0+$/, "");
    };
    const equalPrice = (left, right) => priceKey(left) !== null && priceKey(left) === priceKey(right);
    const validContext = value => exactFields(value, CONTEXT_FIELDS) && CONTEXT_FIELDS.every(field => typeof value[field] === "string") &&
        CONTEXT_FIELDS.filter(field => field !== "work_order_modified").every(field => value[field]) && /^\d{4}-\d{2}-\d{2}$/.test(value.work_date);

    window.JifengWageRatePicker = class JifengWageRatePicker {
        constructor({ getContext, stillCurrent, onChoose }) {
            if (typeof getContext !== "function" || typeof stillCurrent !== "function" || typeof onChoose !== "function") throw new Error("Missing wage choice guards");
            this.getContext = getContext; this.stillCurrent = stillCurrent; this.onChoose = onChoose;
            const context = getContext();
            if (!validContext(context) || stillCurrent() !== true) throw new Error(translated("请先选择员工、工作日期和已提交的工序报工来源。"));
            this.context = { ...context }; this.contextJSON = JSON.stringify(this.context);
            this.closed = false; this.busy = false; this.revision = 0; this.rows = []; this.page = 1; this.totalPages = 0;
            this.dialog = new frappe.ui.Dialog({ title: translated("选择历史计件工价"), size: "large",
                fields: [{ fieldname: "choices", fieldtype: "HTML" }],
                primary_action_label: translated("重新查询"), primary_action: () => this.query(1) });
            this.dialog.on_hide = () => { this.closed = true; this.revision++; };
            this.host = this.dialog.fields_dict.choices.$wrapper.get(0);
            this.status = this.element("p", translated("正在读取已审核的计件工价…")); this.status.setAttribute("role", "status");
            this.table = this.element("table", undefined, "table table-bordered table-hover");
            const head = this.element("tr"), thead = this.element("thead");
            for (const label of ["原计薪记录", "工作日期", "计件单位", "计件工价", "选择"]) head.append(this.element("th", translated(label)));
            thead.append(head); this.body = this.element("tbody"); this.table.append(thead, this.body);
            this.previous = this.button("上页", () => this.query(this.page - 1)); this.next = this.button("下页", () => this.query(this.page + 1));
            this.count = this.element("span", "—"); const footer = this.element("div", undefined, "flex align-center gap-2");
            footer.append(this.previous, this.count, this.next);
            this.host.replaceChildren(this.element("p", translated("仅列当前公司、员工、工序和单位，且不晚于本次工作日期的已审核计薪记录。请明确选择一条。")),
                this.status, this.table, footer);
            this.dialog.show(); this.ready = this.query(1);
        }
        element(tag, value, classes) {
            const element = document.createElement(tag);
            if (value !== undefined) element.textContent = String(value ?? "");
            if (classes) element.className = classes;
            return element;
        }
        button(label, action) {
            const button = this.element("button", translated(label), "btn btn-default btn-xs"); button.type = "button";
            button.addEventListener("click", action); return button;
        }
        current() {
            if (this.closed) return false;
            try { const context = this.getContext(); return this.stillCurrent() === true && validContext(context) && JSON.stringify(context) === this.contextJSON; }
            catch { return false; }
        }
        valid(row) {
            return exactFields(row, ROW_FIELDS) && ROW_FIELDS.every(field => typeof row[field] === "string") && row.name && row.version &&
                /^\d{4}-\d{2}-\d{2}$/.test(row.work_date) && row.work_date <= this.context.work_date && decimal(row.piece_rate) &&
                ["company", "employee", "operation", "stock_uom"].every(field => row[field] === this.context[field]);
        }
        call(method, args) { return frappe.call({ method: "jifeng_erp.wage_rates." + method, args, type: "GET" }); }
        freeze() {
            const current = this.current();
            this.previous.disabled = this.busy || !current || this.page <= 1;
            this.next.disabled = this.busy || !current || this.page >= this.totalPages;
            for (const row of this.rows) row.button.disabled = this.busy || !current;
            this.dialog.get_primary_btn().prop("disabled", this.busy || !current);
        }
        async query(page = 1) {
            if (this.closed || this.busy) return;
            if (!this.current()) { this.status.textContent = translated("工资来源、工价或表单权限已变化，请关闭后重新选择。"); this.freeze(); return; }
            if (!Number.isSafeInteger(page) || page < 1) return;
            const revision = ++this.revision; this.busy = true; this.freeze();
            try {
                const response = await this.call("query_piece_rates", { context: this.context, page, page_size: 25 }), result = response.message;
                if (this.closed || revision !== this.revision) return;
                if (!this.current()) { this.status.textContent = translated("工资来源、工价或表单权限已变化，未采用迟到的查询结果。"); return; }
                const pages = Math.ceil(result?.total / 25), expectedPage = Math.min(page, Math.max(1, pages));
                if (!exactFields(result, ["scope", "readonly", "page", "page_size", "total", "total_pages", "rows"]) ||
                    result.scope !== "wage_piece_rates" || result.readonly !== true || !Number.isSafeInteger(result.total) || result.total < 0 ||
                    result.total_pages !== pages || result.page !== expectedPage || result.page_size !== 25 || !Array.isArray(result.rows) ||
                    result.rows.length !== Math.min(25, Math.max(0, result.total - (result.page - 1) * 25)) ||
                    new Set(result.rows.map(row => row?.name)).size !== result.rows.length || result.rows.some(row => !this.valid(row))) throw new Error("Incomplete historical wage page");
                this.page = result.page; this.totalPages = pages; this.body.replaceChildren();
                this.rows = result.rows.map(source => {
                    const tr = this.element("tr"), row = { source };
                    for (const field of ["name", "work_date", "stock_uom", "piece_rate"]) tr.append(this.element("td", source[field]));
                    const cell = this.element("td"); row.button = this.button("采用此工价", () => this.choose(source));
                    cell.append(row.button); tr.append(cell); this.body.append(tr); return row;
                });
                this.count.textContent = translated("第") + (result.total ? this.page : 0) + "/" + pages + translated("页 · ") + result.total + translated("条");
                this.status.textContent = result.total ? translated("工价仅在明确选择后带入，本窗口不自动保存工资。") : translated("当前范围没有可选择的已审核计件工价。");
            } catch { if (!this.closed && revision === this.revision) this.status.textContent = translated("历史工价来源或权限已变化，未替换当前结果，请重新查询。"); }
            finally { if (!this.closed && revision === this.revision) { this.busy = false; this.freeze(); } }
        }
        async choose(source) {
            if (this.closed || this.busy || !this.current() || !this.valid(source) || !this.rows.some(row => row.source === source)) return;
            this.busy = true; this.freeze();
            try {
                const response = await this.call("choose_piece_rate", { context: this.context, source: source.name, version: source.version, piece_rate: source.piece_rate });
                if (this.closed) return;
                if (!this.current()) { this.status.textContent = translated("工资来源、工价或表单权限已变化，未带入历史工价。"); return; }
                const result = response.message;
                if (!exactFields(result, ["scope", "readonly", ...ROW_FIELDS]) || result.scope !== "wage_piece_rates" || result.readonly !== true) throw new Error("Invalid wage choice");
                const chosen = Object.fromEntries(ROW_FIELDS.map(field => [field, result[field]]));
                if (!this.valid(chosen) || ROW_FIELDS.some(field => chosen[field] !== source[field])) throw new Error("Changed historical wage choice");
                const adopted = await this.onChoose(chosen);
                if (this.closed) return;
                if (adopted !== true || !this.current()) { this.status.textContent = translated("带入期间工资表单发生变化，请关闭后核对当前工价和引用；未自动保存。"); return; }
                this.dialog.hide(); frappe.show_alert({ message: translated("已带入历史工价，请核对工资明细后保存。"), indicator: "green" });
            } catch { if (!this.closed) this.status.textContent = translated("历史工价核对或带入未完成，请关闭后核对当前工价和引用；未自动保存。"); }
            finally { if (!this.closed) { this.busy = false; this.freeze(); } }
        }
    };

    const documentField = (frm, row) => {
        if (!row || row === frm.doc) return frm.fields_dict?.piece_rate?.df;
        const grid = frm.fields_dict?.employees?.grid;
        const field = grid?.get_field?.("piece_rate");
        return field?.df || field;
    };
    const rowExists = (frm, row) => row === frm.doc || (Array.isArray(frm.doc.employees) && frm.doc.employees.includes(row));
    const canEdit = (frm, row = frm.doc) => {
        const field = documentField(frm, row);
        return frm.doc?.docstatus === 0 && !frm.read_only && !!frm.has_perm?.(frm.is_new() ? "create" : "write") && rowExists(frm, row) &&
            !(row === frm.doc && frm.doc.wage_allocation) && !!field && !field.read_only && !field.hidden &&
            (row === frm.doc || (!frm.fields_dict.employees.df?.read_only && !frm.fields_dict.employees.df?.hidden));
    };
    const scope = (frm, row) => ({ company: frm.doc.company || "", employee: row.employee || "", operation: frm.doc.operation || "",
        work_date: frm.doc.work_date || "", stock_uom: frm.doc.stock_uom || "", job_card: frm.doc.job_card || "" });
    const setValues = (frm, row, values) => row === frm.doc ? frm.set_value(values) : frappe.model.set_value(row.doctype, row.name, values);

    window.JifengWageRateTools = {
        canEdit,
        async clearReference(frm, row = frm.doc) {
            if (applying.has(row) || !canEdit(frm, row) || !REFERENCES.some(field => row[field])) return;
            await setValues(frm, row, { piece_rate_reference: "", piece_rate_reference_version: "" });
            frappe.show_alert({ message: translated("工价或工资来源已变化，已清除历史工价引用；请核对当前手填工价。"), indicator: "orange" });
        },
        async open(frm, row = frm.doc) {
            if (!canEdit(frm, row)) { frappe.msgprint(translated("只有可编辑的工资草稿可以选择历史工价；分配生成的计薪记录保留原分配工价。")); return null; }
            if (frm.is_new() && frm.doc.amended_from) {
                frappe.msgprint(translated("请先保存修改草稿，再调用历史工价。")); return null;
            }
            const heldDocument = frm.doc, heldName = frm.doc.name, heldVersion = frm.doc.modified, heldFresh = frm.is_new();
            const heldScope = scope(frm, row), scopeJSON = JSON.stringify(heldScope);
            let expectedPrice = row.piece_rate ?? 0, expectedReference = row.piece_rate_reference || "", expectedReferenceVersion = row.piece_rate_reference_version || "";
            const stable = () => frm.doc === heldDocument && frm.doc.name === heldName && frm.doc.modified === heldVersion && frm.is_new() === heldFresh &&
                canEdit(frm, row) && JSON.stringify(scope(frm, row)) === scopeJSON;
            const current = () => stable() && equalPrice(row.piece_rate ?? 0, expectedPrice) && (row.piece_rate_reference || "") === expectedReference &&
                (row.piece_rate_reference_version || "") === expectedReferenceVersion;
            if (Object.values(heldScope).some(value => typeof value !== "string" || !value)) {
                frappe.msgprint(translated("请先选择员工、工作日期、工序和已提交报工单，并核对计件单位。")); return null;
            }
            try {
                const response = await frappe.call({ method: "jifeng_erp.wage_workbench.get_entry_source", type: "GET",
                    args: { job_card: heldScope.job_card, company: heldScope.company, employee: heldScope.employee } });
                if (!current()) return null;
                const source = response.message;
                if (!source || source.job_card !== heldScope.job_card || source.operation !== heldScope.operation || source.stock_uom !== heldScope.stock_uom ||
                    typeof source.source_modified !== "string" || !source.source_modified || typeof source.work_order_modified !== "string") throw new Error("Changed native wage source");
                const context = { ...heldScope, source_modified: source.source_modified, work_order_modified: source.work_order_modified };
                return new window.JifengWageRatePicker({ getContext: () => ({ ...scope(frm, row), source_modified: context.source_modified, work_order_modified: context.work_order_modified }),
                    stillCurrent: current, onChoose: async chosen => {
                        if (!current()) return false;
                        applying.add(row);
                        try { await setValues(frm, row, { piece_rate: chosen.piece_rate, piece_rate_reference: chosen.name, piece_rate_reference_version: chosen.version }); }
                        finally { applying.delete(row); }
                        if (!stable() || !equalPrice(row.piece_rate, chosen.piece_rate) || row.piece_rate_reference !== chosen.name || row.piece_rate_reference_version !== chosen.version) {
                            // A manual change during native triggers must retain its price and discard this quote.
                            if (canEdit(frm, row) && row.piece_rate_reference === chosen.name && row.piece_rate_reference_version === chosen.version) {
                                await window.JifengWageRateTools.clearReference(frm, row);
                            }
                            return false;
                        }
                        expectedPrice = chosen.piece_rate; expectedReference = chosen.name; expectedReferenceVersion = chosen.version;
                        return true;
                    } });
            } catch { frappe.msgprint(translated("报工来源读取未完成，请核对公司、员工、工序和表单权限后重新选择。")); return null; }
        },
        async clearRows(frm) {
            for (const row of [...(frm.doc.employees || [])]) await this.clearReference(frm, row);
        },
    };
})();
