frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        if (window.JifengOrderCustomerPicker.canEdit(frm)) {
            frm.add_custom_button("从订单客户档案选择", () => {
                if (!frm.doc.company) return frappe.msgprint(__("请先选择订单公司。"));
                const existing = frm._jifeng_order_customer_picker;
                if (existing && !existing.closed) { existing.dialog.show(); return; }
                frm._jifeng_order_customer_picker = new window.JifengOrderCustomerPicker(frm);
            }, "集沣 ERP");
        }
        if (frm.doc.docstatus !== 1 || frm.doc.status === "Closed") return;
        frm.add_custom_button("生成集沣生产流程单", async () => {
            const result = await frappe.call({
                method: "jifeng_erp.workflow.create_sales_order_flows",
                args: { sales_order: frm.doc.name },
            });
            const names = result.message || [];
            if (names.length === 1) {
                frappe.set_route("Form", "Jifeng Flow Order", names[0]);
            } else if (names.length) {
                frappe.set_route("List", "Jifeng Flow Order", { sales_order: frm.doc.name });
            }
        }, "集沣 ERP");
    },
});

// Choice only: Native Customer remains the order party; no archive text is copied.
window.JifengOrderCustomerPicker = class JifengOrderCustomerPicker {
    static canEdit(frm) {
        return frm.doc?.docstatus === 0 && !frm.read_only &&
            !!frm.has_perm?.(frm.is_new() ? "create" : "write") &&
            !!frm.fields_dict.customer && !frm.fields_dict.customer.df.read_only;
    }
    constructor(frm) {
        this.frm = frm; this.document = frm.doc; this.closed = false; this.busy = false; this.revision = 0;
        this.target = { name: frm.doc.name, version: frm.doc.modified, customer: frm.doc.customer,
            company: frm.doc.company, fresh: frm.is_new() };
        this.context = { company: this.target.company, name: this.target.fresh ? null : this.target.name,
            version: this.target.fresh ? null : this.target.version };
        this.page = 1; this.totalPages = 0; this.rows = [];
        this.dialog = new frappe.ui.Dialog({ title: __("选择订单客户档案"), size: "large",
            fields: [{ fieldname: "search", fieldtype: "Data", label: __("关键字"), description: __("按档案编号、自编或名称查询当前公司的在用客户。") },
                { fieldname: "choices", fieldtype: "HTML" }],
            primary_action_label: __("查询"), primary_action: () => this.query(1) });
        this.dialog.on_hide = () => { this.closed = true; this.revision++; };
        this.host = this.dialog.fields_dict.choices.$wrapper.get(0);
        this.status = this.element("p", __("正在读取订单客户档案…")); this.status.setAttribute("role", "status");
        this.table = this.element("table", undefined, "table table-bordered table-hover");
        const head = this.element("tr"), thead = this.element("thead");
        for (const text of ["编号", "自编", "名称", "关联客户", "选择"]) head.append(this.element("th", __(text)));
        thead.append(head); this.body = this.element("tbody"); this.table.append(thead, this.body);
        this.previous = this.button("上页", () => this.query(this.page - 1)); this.next = this.button("下页", () => this.query(this.page + 1));
        this.count = this.element("span", "—"); const footer = this.element("div", undefined, "flex align-center gap-2");
        footer.append(this.previous, this.count, this.next);
        this.host.replaceChildren(this.element("p", this.context.company), this.status, this.table, footer);
        this.dialog.show(); this.ready = this.query(1);
    }
    element(tag, text, classes) {
        const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text ?? "");
        if (classes) node.className = classes; return node;
    }
    button(label, action) {
        const node = this.element("button", __(label), "btn btn-default btn-xs"); node.type = "button";
        node.addEventListener("click", action); return node;
    }
    current() {
        const frm = this.frm, held = this.target;
        return !this.closed && frm.doc === this.document && frm.doc.name === held.name &&
            frm.doc.modified === held.version && frm.doc.company === held.company &&
            frm.doc.customer === held.customer && frm.is_new() === held.fresh && this.constructor.canEdit(frm);
    }
    valid(row) {
        return row && row.company === this.context.company && row.kind === "订单客户" && row.can_select === true &&
            ["name", "profile_version", "customer", "customer_version", "legacy_number", "display_name"].every((key) => typeof row[key] === "string" && row[key]) &&
            (row.legacy_code == null || typeof row.legacy_code === "string");
    }
    call(method, args) { return frappe.call({ method: "jifeng_erp.order_customer_selection." + method, args, type: "GET" }); }
    freeze() {
        this.previous.disabled = this.busy || this.page <= 1; this.next.disabled = this.busy || this.page >= this.totalPages;
        for (const row of this.rows) if (row.button) row.button.disabled = this.busy || !this.current();
        this.dialog.get_primary_btn().prop("disabled", this.busy || !this.current());
    }
    async query(page = 1) {
        if (this.closed || this.busy) return;
        if (!this.current()) { this.status.textContent = __("订单、公司或客户已变化，请关闭后重新选择。"); this.freeze(); return; }
        const search = this.dialog.get_value("search") || "";
        if (typeof search !== "string" || search.length > 140) { frappe.msgprint(__("查询关键字不能超过一百四十个字符。")); return; }
        const revision = ++this.revision; this.busy = true; this.freeze();
        try {
            const response = await this.call("query_customers", { context: this.context, search, page, page_size: 25 }), result = response.message;
            if (this.closed || revision !== this.revision) return;
            if (!this.current()) { this.status.textContent = __("订单、公司或客户已变化，未采用迟到的查询结果。"); return; }
            const pages = Math.ceil(result?.total / 25), expectedPage = Math.min(page, Math.max(1, pages));
            if (result?.scope !== "order_customer" || result.company !== this.context.company || result.search !== search ||
                !Number.isSafeInteger(result.total) || result.total < 0 || result.total_pages !== pages || result.page !== expectedPage || result.page_size !== 25 ||
                !Array.isArray(result.rows) || result.rows.length !== Math.min(25, Math.max(0, result.total - (result.page - 1) * 25)) ||
                new Set(result.rows.map((row) => row?.name)).size !== result.rows.length || result.rows.some((row) => !this.valid(row))) throw new Error("Incomplete customer choices");
            this.rows = result.rows.map((source) => ({ source })); this.page = result.page; this.totalPages = pages; this.body.replaceChildren();
            for (const row of this.rows) {
                const tr = this.element("tr");
                for (const key of ["legacy_number", "legacy_code", "display_name", "customer"]) tr.append(this.element("td", row.source[key] ?? ""));
                const action = this.element("td"); row.button = this.button("选择", () => this.choose(row.source)); action.append(row.button); tr.append(action); this.body.append(tr);
            }
            this.count.textContent = __("第") + (result.total ? this.page : 0) + "/" + pages + __("页 · ") + result.total + __("条");
            this.status.textContent = result.total ? __("选择后带入关联客户；请在原生订单中核对并保存。") : __("当前公司没有可选择的在用订单客户档案。");
        } catch { if (!this.closed && revision === this.revision) this.status.textContent = __("客户档案来源或权限已变化，未替换当前结果，请重新查询。"); }
        finally { if (!this.closed && revision === this.revision) { this.busy = false; this.freeze(); } }
    }
    async choose(source) {
        if (this.closed || this.busy || !this.current() || !this.valid(source) || !this.rows.some((row) => row.source === source)) return;
        this.busy = true; this.freeze();
        try {
            const response = await this.call("choose_customer", { context: this.context, profile: source.name,
                profile_version: source.profile_version, customer_version: source.customer_version, customer: source.customer }), current = response.message;
            if (this.closed) return;
            if (!this.current()) { this.status.textContent = __("订单、公司或客户已变化，未带入已核对客户。"); return; }
            if (!this.valid(current) || current.scope !== "order_customer" ||
                ["name", "profile_version", "customer", "customer_version"].some((field) => current[field] !== source[field])) throw new Error("Changed customer choice");
            await this.frm.set_value("customer", current.customer);
            if (this.closed) return;
            if (this.frm.doc !== this.document || this.frm.doc.company !== this.target.company || this.frm.doc.customer !== current.customer ||
                this.frm.doc.name !== this.target.name || this.frm.doc.modified !== this.target.version ||
                this.frm.is_new() !== this.target.fresh || !this.constructor.canEdit(this.frm)) {
                this.status.textContent = __("带入期间订单发生变化，请核对原生订单中的客户；未自动保存。"); return;
            }
            this.dialog.hide(); frappe.show_alert({ message: __("已带入关联客户，请核对并保存订单。"), indicator: "green" });
        } catch { if (!this.closed) this.status.textContent = __("客户核对或带入未完成，请关闭选择窗口并核对原生订单中的客户和表单提示；未自动保存。"); }
        finally { if (!this.closed) { this.busy = false; this.freeze(); } }
    }
};
