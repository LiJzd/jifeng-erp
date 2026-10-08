/* Native-source subcontracting reconciliation view. No local rows or arithmetic. */
window.JifengSubcontractingReconciliation = class JifengSubcontractingReconciliation {
	constructor(host, bridge, businessKind = "all") {
		this.host = host; this.bridge = bridge; this.businessKind = businessKind;
		this.disposed = false; this.visible = true; this.revision = 0; this.loading = false;
		this.result = null; this.request = null; this.controls = {};
		this.build(); this.ready = this.initialize();
	}
	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined && text !== null) node.textContent = String(text);
		if (className) node.className = className;
		return node;
	}
	button(label, action, className = "btn btn-default btn-sm") {
		const node = this.element("button", __(label), className); node.type = "button";
		node.addEventListener("click", action); return node;
	}
	control(parent, fieldname, fieldtype, label, options, onchange) {
		const control = frappe.ui.form.make_control({ parent, render_input: true,
			df: { fieldname, fieldtype, label: __(label), ...(options ? { options } : {}),
				reqd: ["company", "supplier", "from_date", "to_date"].includes(fieldname), onchange } });
		control.$input?.on?.("input.jifeng-subcontract-reconciliation change.jifeng-subcontract-reconciliation", onchange);
		this.controls[fieldname] = control; return control;
	}
	build() {
		this.host.classList.add("jfe-subcontract-reconciliation"); this.host.replaceChildren();
		const heading = this.element("div", undefined, "jfe-subcontract-reconciliation-heading");
		heading.append(this.element("h2", __("委外业务对账")),
			this.element("p", __("按供应商工厂档案分类，来源仅取已提交委外单、收退货、库存凭证和原生应付账簿。")));
		const toolbar = this.element("div", undefined, "jfe-subcontract-reconciliation-filters");
		const fields = [["company", "Link", "公司", "Company"], ["supplier", "Link", "供应商", "Supplier"],
			["from_date", "Date", "开始日期", null], ["to_date", "Date", "结束日期", null],
			["flow_order", "Link", "流程单", "Jifeng Flow Order"],
			["subcontracting_order", "Link", "委外订单", "Subcontracting Order"]];
		for (const [name, type, label, options] of fields) {
			const holder = this.element("div", undefined, "jfe-subcontract-reconciliation-filter");
			this.control(holder, name, type, label, options, () => this.invalidate()); toolbar.append(holder);
		}
		this.queryButton = this.button("查询", () => this.query(), "btn btn-primary btn-sm");
		toolbar.append(this.queryButton);
		this.status = this.element("p", __("请选择公司、供应商和日期范围后查询。"), "jfe-subcontract-reconciliation-status");
		this.status.setAttribute("role", "status"); this.status.setAttribute("aria-live", "polite");
		this.sections = this.element("div", undefined, "jfe-subcontract-reconciliation-results");
		this.notice = this.element("p", undefined, "jfe-subcontract-reconciliation-note");
		this.host.append(heading, toolbar, this.status, this.sections, this.notice);
	}
	async initialize() {
		const company = frappe.defaults?.get_user_default?.("Company");
		if (company) await this.controls.company.set_value(company);
		const today = frappe.datetime.get_today();
		await this.controls.from_date.set_value(frappe.datetime.month_start());
		await this.controls.to_date.set_value(today);
		return !this.disposed;
	}
	value(field) {
		const control = this.controls[field];
		const value = control?.get_value?.() || "";
		if (control?.df.fieldtype === "Link") {
			const typed = control.get_input_value?.() ?? control.$input?.val?.();
			if (typeof typed === "string" && typed !== value) return null;
		}
		return value;
	}
	filters() {
		return { company: this.value("company"), supplier: this.value("supplier"),
			from_date: this.value("from_date"), to_date: this.value("to_date"),
			flow_order: this.value("flow_order") || null,
			subcontracting_order: this.value("subcontracting_order") || null };
	}
	validDate(value) {
		if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
		const [year, month, day] = value.split("-").map(Number);
		const date = new Date(Date.UTC(year, month - 1, day));
		return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
	}
	context() {
		const f = this.filters();
		if (![f.company, f.supplier].every(value => typeof value === "string" && value && value === value.trim()) ||
			!this.validDate(f.from_date) || !this.validDate(f.to_date) || f.from_date > f.to_date ||
			["flow_order", "subcontracting_order"].some(name => this.value(name) === null)) return null;
		return f;
	}
	invalidate() {
		if (this.disposed) return;
		if (this.loading) this.invalidateRequest("筛选已变化，请重新查询。");
		this.revision++; this.result = null; this.sections.replaceChildren(); this.notice.textContent = "";
		this.status.textContent = __("筛选已变化，请重新查询。");
	}
	setVisible(visible) {
		this.visible = !!visible;
		if (!this.visible && this.loading) this.invalidateRequest("已离开对账页面，返回后请重新查询。");
	}
	validResponse(data, context) {
		return data && data.company === context.company && data.supplier === context.supplier &&
			data.business_kind === this.businessKind && data.from_date === context.from_date && data.to_date === context.to_date &&
			data.flow_order === context.flow_order && data.subcontracting_order === context.subcontracting_order &&
			["progress", "receipts", "materials", "payables", "payments", "history"].every(key => Array.isArray(data[key])) &&
			typeof data.company_currency === "string";
	}
	async query() {
		if (this.disposed || !this.visible || this.loading) return;
		const context = this.context(); this.sections.replaceChildren(); this.notice.textContent = ""; this.result = null;
		if (!context) { this.status.textContent = __("请选择单一公司、供应商和有效日期范围，并核对所有链接字段。"); return; }
		const revision = ++this.revision; this.loading = true; this.queryContext = context; this.queryButton.disabled = true;
		this.status.textContent = __("正在读取委外来源及原生应付账簿…");
		try {
			this.request = frappe.call({ method: "jifeng_erp.subcontracting_reconciliation.query_reconciliation", type: "GET",
				args: { ...context, business_kind: this.businessKind } });
			const response = await this.request;
			if (this.disposed || !this.visible || revision !== this.revision || this.queryContext !== context) return;
			const data = response?.message;
			if (!this.validResponse(data, context)) throw new Error("invalid reconciliation response");
			this.result = data; this.render(data);
			this.status.textContent = __("已读取正式来源。数量、金额均按来源明细展示，不汇总不同单位或币种。 ");
		} catch {
			if (!this.disposed && this.visible && revision === this.revision) {
				this.sections.replaceChildren(); this.result = null;
				this.status.textContent = __("查询未完成。请核对来源单据完整性、字段权限及原生账簿状态。");
			}
		} finally {
			if (revision === this.revision) { this.loading = false; this.queryContext = null;
				this.request = null; this.queryButton.disabled = false; }
		}
	}
	invalidateRequest(message) {
		this.revision++; const request = this.request; this.request = null; request?.abort?.();
		this.loading = false; this.queryContext = null; this.result = null; this.sections.replaceChildren();
		this.status.textContent = __(message); this.queryButton.disabled = false;
	}
	cellValue(value) {
		if (value === null || value === undefined || value === "") return "—";
		if (typeof value === "object") return "—";
		return String(value);
	}
	open(doctype, name) {
		if (!this.result || !this.visible || this.loading || !name || !this.bridge?.open) return;
		const current = this.result;
		this.bridge.open({ title: name, module: "财务", kind: "form", doctype, route: ["Form", doctype, name] });
		if (this.result === current) this.status.textContent = __("已打开原生来源单据；返回后请重新查询。");
	}
	section(title, rows, columns, options = {}) {
		const section = this.element("section", undefined, "jfe-subcontract-reconciliation-section");
		section.append(this.element("h3", title));
		if (!rows.length) { section.append(this.element("p", __("当前范围无记录。"), "jfe-subcontract-reconciliation-empty")); return section; }
		const scroller = this.element("div", undefined, "jfe-subcontract-reconciliation-scroll"), table = this.element("table");
		const head = this.element("thead"), header = this.element("tr");
		for (const [, label] of columns) header.append(this.element("th", label));
		head.append(header); const body = this.element("tbody");
		for (const row of rows) {
			const tr = this.element("tr");
			for (const [field, label] of columns) {
				const td = this.element("td");
				if (options.historyLinks && field === "voucher_no" && row[field] && row.doctype) {
					const button = this.button(row[field], () => this.open(row.doctype, row[field]), "jfe-subcontract-reconciliation-link");
					td.append(button);
				} else if (options.links?.[field] && row[field]) {
					const button = this.button(row[field], () => this.open(options.links[field], row[field]), "jfe-subcontract-reconciliation-link");
					td.append(button);
				} else if (field === "payment_action" && row.voucher_type === "Purchase Invoice") {
					const button = this.button("新建原生付款草稿", () => this.openPayment(row), "jfe-subcontract-reconciliation-link"); td.append(button);
				} else td.textContent = this.cellValue(row[field]);
				tr.append(td);
			}
			body.append(tr);
		}
		table.append(head, body); scroller.append(table); section.append(scroller); return section;
	}
	render(data) {
		this.sections.replaceChildren();
		const groups = [
			["委外进度", data.progress, [["subcontracting_order", "委外订单"], ["supplier_name", "供应商"], ["flow_order", "流程单"], ["item_code", "商品"], ["stock_uom", "单位"], ["planned_qty", "计划量"], ["accepted_qty", "净接收"], ["rejected_qty", "净拒收"], ["loss_qty", "确认损耗"], ["pending_qty", "待收" ]], { links: { subcontracting_order: "Subcontracting Order", flow_order: "Jifeng Flow Order" } }],
			["收回与库存价值（" + data.company_currency + "）", data.receipts, [["receipt", "收回/退回单"], ["entry_type", "业务类型"], ["subcontracting_order", "委外订单"], ["flow_order", "流程单"], ["item_code", "商品"], ["stock_uom", "单位"], ["accepted_qty", "接收变动"], ["rejected_qty", "拒收变动"], ["loss_qty", "损耗变动"], ["inventory_qty_change", "库存数量变动"], ["inventory_value_change", "库存价值变动"], ["inventory_value_status", "估价状态" ]], { links: { receipt: "Subcontracting Receipt", subcontracting_order: "Subcontracting Order", flow_order: "Jifeng Flow Order" } }],
			["委外原料发退（库存单位分行展示）", data.materials, [["stock_entry", "发退料凭证"], ["entry_type", "业务类型"], ["subcontracting_order", "委外订单"], ["flow_order", "流程单"], ["rm_item_code", "原料"], ["stock_uom", "库存单位"], ["issued_qty", "发出"], ["returned_qty", "退回"], ["net_qty", "净发出" ]], { links: { stock_entry: "Stock Entry", subcontracting_order: "Subcontracting Order", flow_order: "Jifeng Flow Order" } }],
			["应付与未核销（币种和余额依据逐行展示）", data.payables, [["voucher_no", "采购发票/贷项凭证"], ["posting_date", "记账日期"], ["source_scope", "金额及余额依据"], ["payable_account", "应付科目"], ["currency", "金额币种"], ["invoiced", "应付来源金额"], ["paid", "原生账簿已核销"], ["credit_note", "贷项"], ["outstanding", "原生余额"], ["payment_action", "付款入口" ]], { links: { voucher_no: "Purchase Invoice" } }],
			["正式付款（不含混合来源金额）", data.payments, [["voucher_no", "付款凭证"], ["posting_date", "记账日期"], ["payment_scope", "来源关系"], ["paid_amount", "付款金额"], ["currency", "付款账户币种" ]], { links: { voucher_no: "Payment Entry" } }],
			["已取消凭证（仅历史链接，不进入有效数量或金额）", data.history, [["doctype", "凭证类型"], ["voucher_no", "凭证编号"], ["posting_date", "日期"], ["status", "状态"]], { historyLinks: true }],
		];
		for (const [title, rows, columns, options] of groups) this.sections.append(this.section(title, rows, columns, options));
		this.notice.textContent = [data.payable_basis, data.payment_basis, data.settlement_action].filter(Boolean).join(" ");
	}
	async openPayment(row) {
		if (!this.result || !this.visible || this.loading || row.voucher_type !== "Purchase Invoice" || !this.bridge?.openSupplierPayment) return;
		const result = this.result, revision = this.revision;
		const identity = { company: this.result.company, supplier: this.result.supplier, name: row.voucher_no };
		try { await this.bridge.openSupplierPayment(identity);
			if (!this.disposed && this.visible && result === this.result && revision === this.revision)
				this.status.textContent = __("已打开原生收付款草稿入口；返回后请重新查询。"); }
		catch { if (!this.disposed && this.visible && result === this.result && revision === this.revision)
			this.status.textContent = __("原生付款入口未能打开，请查看来源发票与权限。"); }
	}
	destroy() {
		if (this.disposed) return;
		this.disposed = true; this.revision++; const request = this.request; this.request = null; request?.abort?.();
		for (const control of Object.values(this.controls))
			control.$input?.off?.(".jifeng-subcontract-reconciliation");
		this.host.replaceChildren(); this.host.classList.remove("jfe-subcontract-reconciliation");
	}
};
