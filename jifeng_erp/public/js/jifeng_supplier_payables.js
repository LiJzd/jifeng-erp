/* Read native cutoff payables in either workspace; never calculate balances. */
window.JifengSupplierPayables = class JifengSupplierPayables {
	constructor(host, bridge) {
		this.host = host; this.bridge = bridge; this.disposed = false; this.visible = true;
		this.revision = 0; this.loading = false; this.rows = []; this.resultContext = null;
		this.request = null; this.queryContext = null; this.build(); this.ready = this.initialize();
	}
	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className; return node;
	}
	button(label, action, className = "btn btn-default btn-sm") {
		const node = this.element("button", __(label), className); node.type = "button";
		node.addEventListener("click", action); return node;
	}
	control(parent, fieldname, fieldtype, label, options, action, clearAction = () => this.invalidate()) {
		const control = frappe.ui.form.make_control({ parent, render_input: true,
			df: { fieldname, fieldtype, label: __(label), ...(options ? { options } : {}),
				reqd: fieldname !== "payable_account", onchange: action } });
		control.$input?.on?.("input.jifeng-supplier-payables change.jifeng-supplier-payables", action);
		control.$link_clear?.on?.("click.jifeng-supplier-payables", clearAction);
		return control;
	}
	build() {
		this.host.classList.add("jfe-supplier-payables"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-supplier-payables-toolbar");
		const holders = ["company", "supplier", "date", "account"].map(kind =>
			this.element("div", undefined, "jfe-supplier-payables-filter jfe-supplier-payables-" + kind));
		this.companyControl = this.control(holders[0], "company", "Link", "公司", "Company", () => this.companyChanged(), () => this.companyChanged(true));
		this.supplierControl = this.control(holders[1], "supplier", "Link", "供应商", "Supplier", () => this.filterChanged());
		this.dateControl = this.control(holders[2], "report_date", "Date", "截止日期", null, () => this.filterChanged());
		this.accountControl = this.control(holders[3], "payable_account", "Link", "应付科目（可选）", "Account", () => this.filterChanged());
		this.accountControl.get_query = () => ({ filters: {
			company: this.linkValue(this.companyControl) || "", account_type: "Payable", is_group: 0,
		} });
		this.queryButton = this.button("查询", () => this.query(), "btn btn-primary btn-sm");
		toolbar.append(...holders, this.queryButton);
		this.status = this.element("p", __("请选择公司、供应商和明确的截止日期。"), "jfe-supplier-payables-status");
		this.status.setAttribute("role", "status"); this.status.setAttribute("aria-live", "polite");
		this.scopeLabel = this.element("p", undefined, "jfe-supplier-payables-scope");
		const note = this.element("p", __("按截止日期显示原生应付来源，金额按公司本币展示。采购发票可打开原生收付款草稿；金额取发票当前余额，请在原生单据中核对、保存和审核。"), "jfe-supplier-payables-note");
		const scroller = this.element("div", undefined, "jfe-supplier-payables-scroll");
		this.table = this.element("table"); this.table.setAttribute("aria-label", __("供应商应付核对"));
		const head = this.element("thead"), heading = this.element("tr");
		for (const title of ["凭证类型", "凭证号", "记账日期", "到期日期", "应付科目", "币种",
			"发票金额", "已付金额", "贷项金额", "未付余额"]) heading.append(this.element("th", __(title)));
		head.append(heading); this.tbody = this.element("tbody"); this.table.append(head, this.tbody); scroller.append(this.table);
		this.host.append(toolbar, note, this.status, this.scopeLabel, scroller);
	}
	async initialize() {
		const company = frappe.defaults?.get_user_default?.("Company");
		if (this.validName(company)) await this.companyControl.set_value(company);
		return !this.disposed;
	}
	validName(value) {
		return typeof value === "string" && !!value && value === value.trim() &&
			[...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value);
	}
	linkValue(control, optional = false) {
		const value = control.get_value() || "";
		// Native Link maps a displayed title back to its exact stored name.
		const input = control.get_input_value?.() ?? control.$input?.val?.();
		if (typeof input === "string" && input !== value) return null;
		return !value && optional ? null : (this.validName(value) ? value : null);
	}
	validDate(value) {
		if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
		const [year, month, day] = value.split("-").map(Number);
		if (!year || month < 1 || month > 12 || day < 1) return false;
		const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
		return day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
	}
	filters() {
		return { company: this.linkValue(this.companyControl), supplier: this.linkValue(this.supplierControl),
			report_date: this.dateControl.get_value() || "", payable_account: this.linkValue(this.accountControl, true) };
	}
	context() {
		const filters = this.filters();
		const accountValue = this.accountControl.get_value();
		const accountInput = this.accountControl.get_input_value?.() ?? this.accountControl.$input?.val?.();
		if (!filters.company || !filters.supplier || !this.validDate(filters.report_date) ||
			(accountValue != null && accountValue !== "" && !filters.payable_account) ||
			(accountInput != null && typeof accountInput !== "string") ||
			(typeof accountInput === "string" && accountInput && !filters.payable_account)) return null;
		return filters;
	}
	sameContext(left, right) {
		return !!left && !!right && ["company", "supplier", "report_date", "payable_account"].every(key => left[key] === right[key]);
	}
	filterChanged() {
		if (this.disposed) return false;
		// Native Link/Date validation can finish after the query click. An
		// unchanged confirmed scope must not abort that same pending read.
		const context = this.context();
		if (this.sameContext(this.loading ? this.queryContext : this.resultContext, context)) return false;
		this.invalidate(); return true;
	}
	companyChanged(force = false) {
		if (this.disposed) return;
		if (force) this.invalidate();
		else if (!this.filterChanged()) return;
		if (this.accountControl?.get_value()) {
			Promise.resolve(this.accountControl.set_value("")).catch(() => {
				if (!this.disposed) this.invalidate();
			});
		}
	}
	clearResult() {
		this.rows = []; this.resultContext = null;
		this.tbody.replaceChildren(); this.scopeLabel.textContent = "";
	}
	invalidate(message = "筛选已变化，请重新查询。") {
		if (this.disposed) return;
		this.revision++; this.loading = false; this.queryContext = null; this.clearResult();
		const request = this.request; this.request = null;
		request?.abort?.(); this.queryButton.disabled = false;
		this.status.textContent = __(message);
	}
	setVisible(visible) {
		if (this.disposed) return;
		const previous = this.visible; this.visible = !!visible;
		if (previous && !this.visible) this.invalidate("已离开应付核对，返回后请重新查询。");
	}
	validMoney(value) {
		// Decimal's finite text can include an exponent or negative zero. No
		// Number/rounding/arithmetic is performed on any of these four amounts.
		return typeof value === "string" && /^-?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?$/.test(value);
	}
	exactKeys(value, keys) {
		return value && typeof value === "object" && !Array.isArray(value) &&
			Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
	}
	validResult(result, context) {
		if (!this.exactKeys(result, ["company", "supplier", "report_date", "currency", "amount_basis",
			"payable_account", "accounts", "rows", "source_report"]) || !this.sameContext(context, result) ||
			result.amount_basis !== "company_currency" || result.source_report !== "Accounts Payable" ||
			!this.validName(result.currency) || !Array.isArray(result.accounts) || !result.accounts.length ||
			!result.accounts.every(name => this.validName(name)) || new Set(result.accounts).size !== result.accounts.length ||
			context.payable_account && (result.accounts.length !== 1 || result.accounts[0] !== context.payable_account) ||
			!Array.isArray(result.rows)) return false;
		const types = ["Purchase Invoice", "Payment Entry", "Journal Entry", "Purchase Order"];
		return result.rows.every(row => this.exactKeys(row, ["voucher_type", "voucher_no", "posting_date", "due_date",
			"payable_account", "currency", "invoiced", "paid", "credit_note", "outstanding"]) &&
			types.includes(row.voucher_type) && this.validName(row.voucher_no) &&
			this.validDate(row.posting_date) && row.posting_date <= context.report_date &&
			(row.due_date === null || this.validDate(row.due_date)) && result.accounts.includes(row.payable_account) &&
			row.currency === result.currency && ["invoiced", "paid", "credit_note", "outstanding"].every(field => this.validMoney(row[field])));
	}
	current(revision, context) {
		return !this.disposed && this.visible && revision === this.revision && this.sameContext(context, this.context());
	}
	async query() {
		if (this.disposed || !this.visible || this.loading) return;
		const context = this.context();
		this.clearResult();
		if (!context) { this.status.textContent = __("请选择单一公司、供应商和有效的截止日期，并核对应付科目。"); return; }
		const revision = ++this.revision; this.loading = true; this.queryContext = { ...context }; this.queryButton.disabled = true;
		this.status.textContent = __("正在核对供应商应付来源…");
		const args = { company: context.company, supplier: context.supplier, report_date: context.report_date };
		if (context.payable_account) args.payable_account = context.payable_account;
		try {
			this.request = frappe.call({ method: "jifeng_erp.supplier_reconciliation.query_payables", type: "GET", args });
			const response = await this.request;
			if (!this.current(revision, context)) return;
			const result = response?.message;
			if (!this.validResult(result, context)) throw new Error("Invalid native payable scope");
			this.rows = result.rows.map(row => ({ ...row })); this.resultContext = { ...context };
			this.scopeLabel.textContent = [context.company, context.supplier, context.report_date, result.currency,
				context.payable_account || __("全部应付科目")].join(" · ");
			this.renderRows();
			this.status.textContent = this.rows.length ? __("已核对应付来源，共") + this.rows.length + __("条。") : __("当前条件没有应付来源记录。");
		} catch {
			if (this.current(revision, context)) {
				this.clearResult(); this.status.textContent = __("查询未完成，请核对筛选条件及来源权限后重试。");
			}
		} finally {
			if (!this.disposed && revision === this.revision) {
				if (!this.sameContext(context, this.context())) {
					this.clearResult(); this.status.textContent = __("筛选已变化，请重新查询。");
				}
				this.loading = false; this.request = null; this.queryContext = null; this.queryButton.disabled = false;
			}
		}
	}
	sourceLabel(doctype) {
		const labels = { "Purchase Invoice": "采购发票", "Payment Entry": "收付款单", "Journal Entry": "会计凭证", "Purchase Order": "采购订单" };
		return __(labels[doctype]);
	}
	renderRows() {
		this.tbody.replaceChildren();
		for (const row of this.rows) {
			const tr = this.element("tr"), source = { doctype: row.voucher_type, name: row.voucher_no };
			const number = this.element("td");
			const open = this.element("button", row.voucher_no, "jfe-supplier-payables-source"); open.type = "button";
			open.addEventListener("click", () => this.openSource(source)); number.append(open);
			if (row.voucher_type === "Purchase Invoice" && this.bridge?.openSupplierPayment)
				number.append(this.button("原生收付款草稿", () => this.openPayment(source)));
			tr.append(this.element("td", this.sourceLabel(row.voucher_type)), number, this.element("td", row.posting_date),
				this.element("td", row.due_date ?? "—"), this.element("td", row.payable_account), this.element("td", row.currency));
			for (const field of ["invoiced", "paid", "credit_note", "outstanding"])
				tr.append(this.element("td", row[field], "jfe-supplier-payables-amount"));
			this.tbody.append(tr);
		}
	}
	openSource(source) {
		if (this.disposed || !this.visible || this.loading || !this.resultContext || !this.sameContext(this.resultContext, this.context()) ||
			!this.rows.some(row => row.voucher_type === source.doctype && row.voucher_no === source.name) || !this.bridge?.open) return;
		this.invalidate("已打开来源凭证；返回后请重新查询应付核对。");
		this.bridge.open({ title: this.sourceLabel(source.doctype) + "：" + source.name, module: "财务", kind: "form", doctype: source.doctype,
			route: ["Form", source.doctype, source.name] });
	}
	async openPayment(source) {
		if (this.disposed || !this.visible || this.loading || source.doctype !== "Purchase Invoice" ||
			!this.resultContext || !this.sameContext(this.resultContext, this.context()) ||
			!this.rows.some(row => row.voucher_type === source.doctype && row.voucher_no === source.name) ||
			!this.bridge?.openSupplierPayment) return;
		// Cutoff amounts are never inputs to native payment mapping.
		const identity = { company: this.resultContext.company, supplier: this.resultContext.supplier, name: source.name };
		this.invalidate("已打开原生收付款入口；返回后请重新查询应付核对。");
		const revision = this.revision;
		try { await this.bridge.openSupplierPayment(identity); }
		catch {
			if (!this.disposed && this.visible && revision === this.revision)
				this.status.textContent = __("原生入口未能打开，请核对原生发票和权限。返回后请重新查询。");
		}
	}
	isDirty() { return false; }
	getCloseBlock() { return ""; }
	destroy() {
		if (this.disposed) return;
		this.disposed = true; this.revision++; this.loading = false; this.queryContext = null;
		const request = this.request; this.request = null; request?.abort?.(); this.clearResult();
		for (const control of [this.companyControl, this.supplierControl, this.dateControl, this.accountControl]) {
			control.$input?.off?.(".jifeng-supplier-payables"); control.$link_clear?.off?.(".jifeng-supplier-payables");
		}
		this.queryButton.disabled = true; this.host.replaceChildren(); this.host.classList.remove("jfe-supplier-payables");
	}
};
