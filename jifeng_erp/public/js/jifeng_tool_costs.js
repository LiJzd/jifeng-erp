/* Shared native-source tool costs for both workspaces. */
window.JifengToolCosts = class JifengToolCosts {
	static types = { mold: ["模具", "Jifeng Mold", "mold_no"], plate: ["版种", "Jifeng Plate", "plate_no"], modeling: ["建模", "Jifeng Plate Modeling", "model_number"] };
	static money(value, positive = false) {
		if (typeof value !== "string" || !/^\d{1,21}(?:\.\d{1,9})?$/.test(value)) return null;
		const [whole, fraction = ""] = value.split("."), units = BigInt(whole) * 1000000000n + BigInt(fraction.padEnd(9, "0"));
		if (units >= 1000000000000000000000000000000n || positive && units <= 0n) return null;
		return { units, text: (whole.replace(/^0+(?=\d)/, "") || "0") + (fraction.replace(/0+$/, "") ? "." + fraction.replace(/0+$/, "") : "") };
	}
	static amount(units) { const whole = units / 1000000000n, fraction = (units % 1000000000n).toString().padStart(9, "0").replace(/0+$/, ""); return whole + (fraction ? "." + fraction : ""); }
	static open(scope, bridge, options = {}) {
		const dialog = new frappe.ui.Dialog({ title: __("实际费用"), size: "extra-large", no_submit_on_enter: true, fields: [{ fieldname: "cost_layout", fieldtype: "HTML" }] });
		const workbench = new this(dialog.fields_dict.cost_layout.$wrapper.get(0), scope, bridge, options); workbench.dialog = dialog;
		const hide = dialog.hide.bind(dialog); workbench.nativeHide = hide; dialog.hide = () => workbench.discardFor(() => { workbench.destroy(); options.onClose?.(workbench.filters()); });
		dialog.$wrapper.addClass("jfe-tool-cost-dialog").modal({ backdrop: "static", keyboard: false, show: false });
		dialog.get_close_btn().removeAttr("data-dismiss").on("click.tool-cost", () => dialog.hide());
		dialog.$wrapper.on("keydown.tool-cost", (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); dialog.hide(); } });
		dialog.on_hide = () => { if (!workbench.disposed) workbench.destroy(); }; dialog.show(); return workbench;
	}
	constructor(host, scope, bridge = null, options = {}) {
		if (!scope || !this.validName(scope.company) || !JifengToolCosts.types[scope.record_type] || !this.validName(scope.name)) throw new Error("Invalid tool scope");
		this.host = host; this.scope = { company: scope.company, record_type: scope.record_type, name: scope.name }; this.bridge = bridge; this.options = options;
		this.disposed = false; this.revision = 0; this.sourceRevision = 0; this.lookupRevision = 0; this.detailRevision = 0; this.busy = false; this.confirming = false; this.pending = null;
		this.source = null; this.summary = null; this.detail = null; this.items = []; this.lookupRows = []; this.build(options.filters || {});
		this.ready = this.query();
	}
	element(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text ?? ""); if (cls) node.className = cls; return node; }
	button(label, action) { const node = this.element("button", __(label), "btn btn-default btn-sm"); node.type = "button"; node.addEventListener("click", action); return node; }
	validName(value) { return typeof value === "string" && value.length > 0 && value.length <= 140 && value.trim() === value; }
	current() { return !this.disposed && (!this.options.stillCurrent || this.options.stillCurrent() === true); }
	call(method, args, write = false) { return frappe.call({ method: "jifeng_erp.tool_cost_sources." + method, type: write ? "POST" : "GET", args }); }
	toolCall(kind, method, args) { return frappe.call({ method: "jifeng_erp." + (kind === "modeling" ? "plate_modeling." : "tool_archive.") + method,
		type: "GET", args: kind === "modeling" ? args : { record_type: kind, ...args } }); }
	build(filters) {
		this.host.classList.add("jfe-tool-costs"); this.host.replaceChildren(); this.status = this.element("p", __("正在核对工具费用…"), "jfe-cost-status"); this.status.setAttribute("role", "status");
		this.summaryHost = this.element("div", undefined, "jfe-cost-summary"); this.history = this.element("div", undefined, "jfe-cost-scroll"); this.details = this.element("div", undefined, "jfe-cost-detail");
		const toolbar = this.element("div", undefined, "jfe-cost-toolbar"); this.refreshButton = this.button("查询", () => this.query()); toolbar.append(this.refreshButton);
		this.host.append(this.element("p", __("公司：") + this.scope.company + "；" + __(JifengToolCosts.types[this.scope.record_type][0]) + "：" + this.scope.name), toolbar, this.status, this.summaryHost, this.history, this.details);
		this.createPanel = this.element("section", undefined, "jfe-cost-create"); this.createPanel.append(this.element("h4", __("费用分摊")));
		const expenseHost = this.element("div"); this.expenseControl = frappe.ui.form.make_control({ parent: expenseHost, render_input: true,
			df: { fieldname: "expense_voucher", fieldtype: "Link", options: "Jifeng Expense Voucher", label: __("实际费用来源"), onchange: () => this.sourceChanged() } });
		this.expenseControl.get_query = () => ({ filters: { company: this.scope.company, docstatus: 1 } });
		this.expenseControl.$input.on("input.tool-cost change.tool-cost", () => this.sourceChanged());
		this.sourceButton = this.button("核对费用来源", () => this.readSource()); this.sourceHost = this.element("div", undefined, "jfe-cost-source");
		const sourceRow = this.element("div", undefined, "jfe-cost-toolbar"); sourceRow.append(expenseHost, this.sourceButton); this.createPanel.append(sourceRow, this.sourceHost);
		this.kind = this.element("select"); this.kind.setAttribute("aria-label", __("工具类型")); for (const [kind, [label]] of Object.entries(JifengToolCosts.types)) { const option = this.element("option", __(label)); option.value = kind; this.kind.append(option); }
		this.kind.value = JifengToolCosts.types[filters.kind] ? filters.kind : this.scope.record_type;
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140; this.search.placeholder = __("工具关键词"); this.search.setAttribute("aria-label", __("工具关键词")); this.search.value = typeof filters.search === "string" ? filters.search.slice(0, 140) : "";
		this.kind.addEventListener("change", () => this.lookupChanged()); this.search.addEventListener("input", () => this.lookupChanged()); this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.lookup(); } });
		this.lookupButton = this.button("查询工具", () => this.lookup()); this.selfButton = this.button("选择当前工具", () => this.selectTool(this.scope.record_type, this.scope.name));
		const lookupRow = this.element("div", undefined, "jfe-cost-toolbar"); lookupRow.append(this.kind, this.search, this.lookupButton, this.selfButton); this.lookupHost = this.element("div", undefined, "jfe-cost-scroll");
		this.itemHost = this.element("div", undefined, "jfe-cost-scroll"); this.total = this.element("p"); this.reason = this.element("textarea"); this.reason.maxLength = 1000; this.reason.setAttribute("aria-label", __("分摊说明")); this.reason.placeholder = __("分摊说明"); this.reason.addEventListener("input", () => this.freeze());
		this.createButton = this.button("保存分摊草稿", () => this.create()); this.resolveButton = this.button("核对保存结果", () => this.resolve()); this.cancelReason = this.element("textarea"); this.cancelReason.maxLength = 1000; this.cancelReason.setAttribute("aria-label", __("撤销说明")); this.cancelReason.placeholder = __("撤销说明");
		this.cancelButton = this.button("撤销分摊", () => this.cancel()); this.cancelPanel = this.element("div", undefined, "jfe-cost-cancel"); this.cancelPanel.append(this.cancelReason, this.cancelButton);
		this.createPanel.append(lookupRow, this.lookupHost, this.itemHost, this.total, this.reason, this.element("p", __("按实际金额填写并核对合计。保存后打开分摊单，核对并人工提交。")), this.createButton, this.resolveButton); this.host.append(this.createPanel, this.cancelPanel);
		if (typeof filters.expense_voucher === "string" && this.validName(filters.expense_voucher)) { this.settingExpense = true; Promise.resolve(this.expenseControl.set_value(filters.expense_voucher)).finally(() => { this.settingExpense = false; }); }
		this.renderItems(); this.freeze();
	}
	filters() { return { kind: this.kind.value, search: this.search.value, expense_voucher: this.expenseControl.get_value() || "" }; }
	clearAmounts(message) { this.revision++; this.sourceRevision++; this.detailRevision++; this.lookupRevision++; this.summary = null; this.detail = null; this.summaryHost.replaceChildren(); this.history.replaceChildren(); this.details.replaceChildren(); this.source = null; this.sourceHost.replaceChildren(); this.lookupRows = []; this.lookupHost.replaceChildren(); this.items = []; this.reason.value = ""; this.itemHost.replaceChildren(); this.status.textContent = __(message); this.freeze(); }
	guardContext() { if (this.current()) return true; this.revision++; this.sourceRevision++; this.lookupRevision++; this.detailRevision++; this.clearAmounts("公司或工具已改变，请关闭后重新打开实际费用。"); this.lookupRows = []; this.lookupHost.replaceChildren(); return false; }
	sourceChanged() { if (this.settingExpense) return; this.sourceRevision++; this.source = null; this.sourceHost.replaceChildren(); this.freeze(); }
	lookupChanged() { this.lookupRevision++; this.lookupRows = []; this.lookupHost.replaceChildren(); }
	validMoney(value, nullable = false) { return nullable && value === null || JifengToolCosts.money(value) !== null; }
	validSource(row, expense = null) {
		return !!(row && row.company === this.scope.company && this.validName(row.expense_voucher) && (!expense || row.expense_voucher === expense) && this.validName(row.expense_version) &&
			this.validName(row.journal_entry) && this.validName(row.journal_version) && this.validName(row.currency) && typeof row.posting_date === "string" &&
			["submitted", "cancelled"].includes(row.source_state) && ["native_cash_bank_gl", "native_payment_entry_gl_ple"].includes(row.payment_basis) &&
			["historical_amount", "incurred_amount", "paid_amount", "unpaid_amount"].every((field) => this.validMoney(row[field])) && Array.isArray(row.payments) &&
			row.payments.every((payment) => payment && this.validName(payment.name) && this.validName(payment.version) && this.validMoney(payment.actual_paid)));
	}
	validAllocation(row) {
		return !!(row && this.validName(row.name) && this.validName(row.version) && row.company === this.scope.company && [0, 1, 2].includes(row.docstatus) && typeof row.can_cancel === "boolean" &&
			this.validSource(row.source) && row.currency === row.source.currency && Array.isArray(row.items) && row.items.length && row.items.length <= 100 && row.items.every((item) =>
				item && JifengToolCosts.types[item.record_type]?.[1] === item.tool_doctype && this.validName(item.tool_name) && this.validName(item.tool_version) &&
				this.validMoney(item.historical_amount) && ["incurred_amount", "paid_amount", "unpaid_amount"].every((field) => this.validMoney(item[field], true)) &&
				["source_cancelled", "not_active", "no_payment", "fully_paid", "partial_payment_tool_unknown"].includes(item.payment_state) &&
				(item.payment_state !== "partial_payment_tool_unknown" || item.paid_amount === null && item.unpaid_amount === null && typeof item.payment_reason === "string" && !!item.payment_reason)));
	}
	validSummary(row) {
		return !!(row && row.company === this.scope.company && row.record_type === this.scope.record_type && row.name === this.scope.name && this.validName(row.currency) && typeof row.can_create === "boolean" &&
			this.validMoney(row.incurred_amount) && this.validMoney(row.paid_amount, true) && this.validMoney(row.unpaid_amount, true) && Array.isArray(row.allocations) &&
			row.allocations.every((allocation) => this.validAllocation(allocation) && allocation.currency === row.currency && allocation.items.some((item) => item.record_type === this.scope.record_type && item.tool_name === this.scope.name)));
	}
	async query() {
		if (!this.guardContext() || this.busy || this.pending) return; const revision = ++this.revision; this.detailRevision++; this.summary = null; this.detail = null; this.summaryHost.replaceChildren(); this.history.replaceChildren(); this.details.replaceChildren(); this.freeze();
		this.status.textContent = __("正在核对工具费用…");
		try { const response = await this.call("get_tool_costs", this.scope); if (!this.guardContext() || revision !== this.revision) return;
			if (!this.validSummary(response.message)) throw new Error("Wrong cost scope"); this.summary = response.message; this.renderSummary(); this.status.textContent = this.summary.allocations.length ? __("费用已核对") : __("暂无已提交的费用分摊。");
		} catch { if (this.guardContext() && revision === this.revision) this.clearAmounts("费用来源未核实，请核对单据、权限和版本后重新查询。"); } finally { this.freeze(); }
	}
	value(value) { return value === null ? __("未核实") : value; }
	appendAmounts(host, row, includeHistorical = false) { for (const [key, label] of [...(includeHistorical ? [["historical_amount", "原费用金额"]] : []), ["incurred_amount", "已发生"], ["paid_amount", "已付"], ["unpaid_amount", "未付"]]) host.append(this.element("span", __(label) + "：" + this.value(row[key]) + " " + row.currency, "jfe-cost-number")); }
	renderSummary() {
		this.summaryHost.replaceChildren(); this.appendAmounts(this.summaryHost, this.summary); this.history.replaceChildren();
		const table = this.element("table"), head = this.element("tr"); for (const label of ["分摊单", "状态", "实际费用来源", "总账凭证", "操作"]) head.append(this.element("th", __(label))); const thead = this.element("thead"); thead.append(head); const tbody = this.element("tbody");
		for (const row of this.summary.allocations) { const tr = this.element("tr"); tr.append(this.element("td", row.name), this.element("td", __(["草稿", "已提交", "已撤销"][row.docstatus])), this.element("td", row.source.expense_voucher), this.element("td", row.source.journal_entry)); const action = this.element("td"); action.append(this.button("查看", () => this.readAllocation(row.name, row.version))); tr.append(action); tbody.append(tr); }
		table.append(thead, tbody); this.history.append(table);
	}
	renderSource(host, source) {
		host.replaceChildren(); this.appendAmounts(host, source, true); const state = source.source_state === "cancelled" ? "来源已撤销" : "来源已提交";
		host.append(this.element("p", __(state) + "；" + __("日期：") + source.posting_date), this.element("p", __("费用单：") + source.expense_voucher + "；" + __("总账凭证：") + source.journal_entry));
		host.append(this.element("p", __(source.payment_basis === "native_cash_bank_gl" ? "付款依据：现金或银行总账" : "付款依据：付款单、总账及付款分类账")));
		for (const row of source.payments) host.append(this.element("p", __("付款单：") + row.name + "；" + __("本笔实际付款：") + row.actual_paid + " " + source.currency));
		const links = this.element("div", undefined, "jfe-cost-toolbar"); links.append(this.button("打开费用单", () => this.openNative("Jifeng Expense Voucher", source.expense_voucher)), this.button("打开总账凭证", () => this.openNative("Journal Entry", source.journal_entry))); host.append(links);
	}
	async readSource() {
		if (!this.guardContext() || this.busy || this.pending) return; const expense = this.expenseControl.get_value(), revision = ++this.sourceRevision; this.source = null; this.sourceHost.replaceChildren(); this.freeze();
		if (!this.validName(expense)) { this.status.textContent = __("请选择实际费用来源。"); return; }
		try { const response = await this.call("get_tool_cost_source", { context: { company: this.scope.company, expense_voucher: expense } });
			if (!this.guardContext() || revision !== this.sourceRevision || expense !== this.expenseControl.get_value()) return;
			if (!this.validSource(response.message, expense)) throw new Error("Wrong source scope"); this.source = response.message; this.renderSource(this.sourceHost, this.source); this.status.textContent = this.source.source_state === "submitted" ? __("费用来源已核对，请手工填写分摊金额。") : __("费用来源已撤销，不能新增分摊。");
		} catch { if (this.guardContext() && revision === this.sourceRevision) this.clearAmounts("费用来源未核实，请核对单据、权限和版本。"); } finally { this.freeze(); }
	}
	async readAllocation(name, version) {
		if (!this.guardContext() || this.busy || this.pending) return; const revision = ++this.detailRevision; this.detail = null; this.details.replaceChildren(); this.freeze();
		try { const response = await this.call("get_tool_cost_allocation", { name, version }); if (!this.guardContext() || revision !== this.detailRevision) return;
			if (!this.validAllocation(response.message) || response.message.name !== name || response.message.version !== version) throw new Error("Wrong allocation version"); this.detail = response.message; this.renderDetail();
		} catch { if (this.guardContext() && revision === this.detailRevision) this.clearAmounts("分摊来源或权限已变化，请重新查询后查看。"); } finally { this.freeze(); }
	}
	renderDetail() {
		const row = this.detail; this.details.replaceChildren(); this.details.append(this.element("h4", row.name + " · " + __(["草稿", "已提交", "已撤销"][row.docstatus]))); const sourceHost = this.element("div"); this.renderSource(sourceHost, row.source); this.details.append(sourceHost);
		const table = this.element("table"), thead = this.element("thead"), head = this.element("tr"); for (const label of ["类型", "工具", "分摊金额", "已发生", "已付", "未付", "付款说明"]) head.append(this.element("th", __(label))); thead.append(head); const tbody = this.element("tbody");
		for (const item of row.items) { const tr = this.element("tr"); for (const value of [JifengToolCosts.types[item.record_type][0], item.tool_label || item.tool_name, item.historical_amount, this.value(item.incurred_amount), this.value(item.paid_amount), this.value(item.unpaid_amount), item.payment_reason || ""]) tr.append(this.element("td", value)); tbody.append(tr); }
		table.append(thead, tbody); this.details.append(table, this.element("p", __("分摊说明：") + (row.reason || "")), this.button("打开分摊单", () => this.openNative("Jifeng Tool Cost Allocation", row.name)));
		if (row.docstatus === 2) this.details.append(this.element("p", __("撤销说明：") + (row.cancel_reason || "")));
	}
	toolView(raw, kind, name = null, metadata = false) {
		const modeling = kind === "modeling", values = modeling ? raw : raw?.values;
		if (!raw || !this.validName(raw.name) || name && raw.name !== name || !this.validName(raw.version) || values?.company !== this.scope.company || !modeling && raw.record_type !== kind || metadata && !raw.field_meta?.company) return null;
		const title = values[JifengToolCosts.types[kind][2]]; if (title != null && typeof title !== "string") return null; return { record_type: kind, name: raw.name, version: raw.version, label: title?.trim() ? title : raw.name };
	}
	async lookup() {
		if (!this.guardContext() || this.busy || this.pending) return; const kind = this.kind.value, search = this.search.value, revision = ++this.lookupRevision; this.lookupRows = []; this.lookupHost.replaceChildren();
		if (!JifengToolCosts.types[kind] || search.length > 140) return;
		try { const response = await this.toolCall(kind, "list_records", { company: this.scope.company, search, ...(kind !== "modeling" ? { include_inactive: 0 } : {}) });
			if (!this.guardContext() || revision !== this.lookupRevision || kind !== this.kind.value || search !== this.search.value) return;
			if (!Array.isArray(response.message)) throw new Error("Wrong tool list"); const rows = response.message.map((row) => this.toolView(row, kind)); if (rows.some((row) => !row)) throw new Error("Wrong tool scope"); this.lookupRows = rows;
			for (const row of rows) { const line = this.element("div", undefined, "jfe-cost-toolbar"); line.append(this.element("span", row.label + " · " + row.name), this.button("选择", () => this.selectTool(kind, row.name, row.version))); this.lookupHost.append(line); }
			if (!rows.length) this.lookupHost.append(this.element("p", __("没有可见工具，请核对关键词。")));
		} catch { if (this.guardContext() && revision === this.lookupRevision) { this.lookupRows = []; this.lookupHost.replaceChildren(); this.clearAmounts("工具或费用来源未核实，请核对权限并重新查询。"); } }
	}
	async selectTool(kind, name, version = null) {
		if (!this.guardContext() || this.busy || this.pending || this.selecting || this.items.length >= 100 || !JifengToolCosts.types[kind]) return;
		if (version && !this.lookupRows.some((row) => row.record_type === kind && row.name === name && row.version === version)) return;
		if (this.items.some((row) => row.record_type === kind && row.name === name)) { this.status.textContent = __("该工具已在分摊中。"); return; }
		const revision = this.lookupRevision; this.selecting = true; this.freeze();
		try { const response = await this.toolCall(kind, "read_record", { name }); if (!this.guardContext() || revision !== this.lookupRevision) return;
			const tool = this.toolView(response.message, kind, name, true); if (!tool || version && tool.version !== version) throw new Error("Changed tool version"); this.items.push({ ...tool, amount: "" }); this.renderItems();
		} catch { if (this.guardContext() && revision === this.lookupRevision) { this.lookupRows = []; this.lookupHost.replaceChildren(); this.clearAmounts("工具来源或版本未核实，请重新查询后选择。"); } } finally { this.selecting = false; this.freeze(); }
	}
	renderItems() {
		this.itemHost.replaceChildren(); const table = this.element("table"), thead = this.element("thead"), head = this.element("tr"); for (const label of ["类型", "工具", "分摊金额", "操作"]) head.append(this.element("th", __(label))); thead.append(head); const tbody = this.element("tbody");
		for (const row of this.items) { const tr = this.element("tr"), cell = this.element("td"), input = this.element("input"); input.type = "text"; input.inputMode = "decimal"; input.setAttribute("aria-label", row.label + __("分摊金额")); input.value = row.amount; row.input = input; input.addEventListener("input", () => { row.amount = input.value; this.freeze(); }); cell.append(input);
			const action = this.element("td"), remove = this.button("移除", () => { if (this.busy || this.pending || !this.current()) return; this.items = this.items.filter((item) => item !== row); this.renderItems(); }); row.remove = remove; action.append(remove); tr.append(this.element("td", __(JifengToolCosts.types[row.record_type][0])), this.element("td", row.label), cell, action); tbody.append(tr); }
		table.append(thead, tbody); this.itemHost.append(table); this.freeze();
	}
	payload() {
		if (!this.source || this.source.source_state !== "submitted" || this.source.expense_voucher !== this.expenseControl.get_value() || !this.items.length || !this.reason.value.trim()) return null;
		const amounts = this.items.map((row) => JifengToolCosts.money(row.amount, true)); if (amounts.some((amount) => !amount) || amounts.reduce((sum, amount) => sum + amount.units, 0n) !== JifengToolCosts.money(this.source.historical_amount).units) return null;
		return { context: { company: this.scope.company, expense_voucher: this.source.expense_voucher, expense_version: this.source.expense_version, reason: this.reason.value.trim() },
			items: this.items.map((row, index) => ({ record_type: row.record_type, name: row.name, version: row.version, amount: amounts[index].text })) };
	}
	freeze() {
		if (this.disposed) return; const locked = this.busy || !!this.pending || this.confirming || !this.current(), amounts = this.items.map((row) => JifengToolCosts.money(row.amount, true));
		this.total.textContent = __("分摊合计：") + (amounts.some((amount) => !amount) ? __("请核对金额") : JifengToolCosts.amount(amounts.reduce((sum, amount) => sum + amount.units, 0n))) + (this.source ? " / " + this.source.historical_amount + " " + this.source.currency : "");
		for (const row of this.items) { if (row.input) row.input.disabled = locked; if (row.remove) row.remove.disabled = locked; }
		this.refreshButton.disabled = locked; this.sourceButton.disabled = locked; this.lookupButton.disabled = locked || this.selecting; this.selfButton.disabled = locked || this.selecting; this.kind.disabled = locked; this.search.disabled = locked; this.reason.disabled = locked;
		if (!!this.expenseControl.df.read_only !== !!locked) {
			// A standalone native Link refresh restores its last model value.
			// Preserve current input when locking; typing itself must not refresh.
			this.expenseControl.set_input(this.expenseControl.get_value());
			this.expenseControl.df.read_only = !!locked;
			this.expenseControl.refresh?.();
		}
		this.createButton.disabled = locked || this.selecting || this.summary?.can_create !== true || !this.payload(); this.createButton.hidden = this.summary?.can_create === false;
		this.resolveButton.hidden = !this.pending; this.resolveButton.disabled = this.busy || this.confirming || !this.current(); this.cancelPanel.hidden = !this.detail || this.detail.docstatus !== 1 || this.detail.can_cancel !== true;
		this.cancelButton.disabled = locked; this.cancelReason.disabled = locked;
	}
	requestID() { if (crypto.randomUUID) return crypto.randomUUID(); const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128; const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join(""); return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-"); }
	async requestDigest(payload) { const canonical = (value) => Array.isArray(value) ? "[" + value.map(canonical).join(",") + "]" : value && typeof value === "object" ? "{" + Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + canonical(value[key])).join(",") + "}" : JSON.stringify(value);
		const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(payload))); return Array.from(new Uint8Array(hash), (value) => value.toString(16).padStart(2, "0")).join(""); }
	validReceipt(receipt, pending) { return receipt && this.validName(receipt.name) && this.validName(receipt.version) && receipt.company === this.scope.company && [0, 1, 2].includes(receipt.docstatus) && receipt.request_id === pending.request_id && typeof pending.request_digest === "string" && receipt.request_digest === pending.request_digest; }
	definiteFailure(error) {
		const reply = error?.responseJSON;
		if (!Number.isInteger(error?.status) || error.status < 400 || error.status >= 500 || !reply ||
			!new Set(["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError", "UniqueValidationError"]).has(reply.exc_type)) return false;
		if (["rollback_unknown", "cleanup_unknown", "rollback_failed", "cleanup_failed"].some((key) => reply[key] != null && reply[key] !== false && reply[key] !== 0)) return false;
		const details = [reply.exc, reply.exception, reply.message, reply.error, reply._error_message, reply._server_messages, reply.state, ...(Array.isArray(reply.__notes__) ? reply.__notes__ : [reply.__notes__])].filter((value) => typeof value === "string").join("\n");
		return !/(?:rollback|cleanup)[_\s-]*(?:unknown|fail(?:ed|ure)?)|(?:unknown|failed)[_\s-]*(?:rollback|cleanup)|局部回滚清理失败|完整回滚并核对|(?:database|before|after)_rollback:/i.test(details);
	}
	async create() {
		if (!this.guardContext() || this.busy || this.pending || this.confirming || this.selecting || this.summary?.can_create !== true) return; const payload = this.payload(); if (!payload) { this.status.textContent = __("请核对来源、分摊说明和合计金额。"); return; }
		const pending = { operation: "create", request_id: this.requestID() }; this.pending = pending; this.busy = true; this.freeze(); let sent = false;
		try { pending.request_digest = await this.requestDigest(payload); if (!this.guardContext() || this.pending !== pending) { if (this.pending === pending) this.pending = null; return; }
			sent = true; const response = await this.call("create_tool_cost_allocation", { ...payload, request_id: pending.request_id }, true); if (!this.guardContext() || this.pending !== pending) return;
			if (!this.validReceipt(response.message, pending)) throw new Error("Unconfirmed receipt"); this.acceptReceipt(response.message);
		} catch (error) { if (!this.disposed && this.pending === pending) { const rejected = sent && this.definiteFailure(error); if (!sent || rejected) this.pending = null;
			this.clearAmounts(rejected ? "保存已被拒绝，请核对权限和填写内容后重新查询。" : sent ? "保存结果尚未确认，请核对保存结果后继续。" : "保存未开始，请核对浏览器和当前分摊后重试。"); } } finally { this.busy = false; this.freeze(); this.openCreated(); }
	}
	acceptReceipt(receipt) { this.pending = null; this.clearAmounts("已保存分摊草稿，请打开分摊单核对并人工提交。"); this.renderItems();
		this.draftToOpen = receipt.name; this.details.replaceChildren(this.element("p", receipt.name), this.button("打开分摊单", () => this.openNative("Jifeng Tool Cost Allocation", receipt.name))); }
	openCreated() { if (this.draftToOpen && this.current() && this.bridge && !this.busy && !this.pending) { const name = this.draftToOpen; this.draftToOpen = null; this.openNative("Jifeng Tool Cost Allocation", name); } }
	async resolve() {
		if (!this.guardContext() || this.busy || this.confirming || !this.pending) return; const pending = this.pending; this.busy = true; this.freeze();
		try { const response = await this.call(pending.operation === "create" ? "resolve_tool_cost_creation" : "get_tool_cost_allocation", pending.operation === "create" ? { company: this.scope.company, request_id: pending.request_id } : { name: pending.name });
			if (!this.guardContext() || this.pending !== pending) return;
			if (pending.operation === "create") { if (!this.validReceipt(response.message, pending)) throw new Error("No exact receipt"); this.acceptReceipt(response.message); }
			else { if (!this.validAllocation(response.message) || response.message.name !== pending.name || response.message.docstatus !== 2 || response.message.cancel_reason !== pending.reason) throw new Error("No cancellation receipt"); this.pending = null; this.detail = response.message; this.renderDetail(); this.status.textContent = __("已核对分摊撤销结果。"); }
		} catch { if (!this.disposed && this.pending === pending) { this.clearAmounts("暂未核对到本次操作结果，请稍后再次核对。"); } } finally { this.busy = false; this.freeze(); this.openCreated(); }
	}
	async cancel() {
		if (!this.guardContext() || this.busy || this.pending || this.confirming || this.detail?.docstatus !== 1 || this.detail.can_cancel !== true) return;
		const reason = this.cancelReason.value.trim(), detail = this.detail; if (!reason) { this.status.textContent = __("请填写撤销说明。"); return; } this.confirming = true; this.freeze();
		const yes = await new Promise((resolve) => frappe.confirm(__("确认撤销这笔工具费用分摊？"), () => resolve(true), () => resolve(false))); this.confirming = false;
		if (!yes || !this.guardContext() || this.busy || this.pending || this.detail !== detail) { this.freeze(); return; } const pending = { operation: "cancel", name: detail.name, version: detail.version, reason }; this.pending = pending; this.busy = true; this.freeze();
		try { const response = await this.call("cancel_tool_cost_allocation", { name: pending.name, version: pending.version, reason }, true); if (!this.guardContext() || this.pending !== pending) return;
			if (!response.message || response.message.name !== pending.name || response.message.company !== this.scope.company || response.message.docstatus !== 2 || !this.validName(response.message.version)) throw new Error("Unconfirmed cancellation");
			this.pending = null; this.detail = null; this.clearAmounts("分摊已撤销，请重新查询当前费用。");
		} catch (error) { if (!this.disposed && this.pending === pending) { const rejected = this.definiteFailure(error); if (rejected) this.pending = null;
			this.clearAmounts(rejected ? "撤销已被拒绝，请核对权限和单据状态后重新查询。" : "撤销结果尚未确认，请核对保存结果后继续。"); } } finally { this.busy = false; this.freeze(); }
	}
	isDirty() { return !!(this.items.length || this.reason.value.trim()); }
	getCloseBlock() { return this.busy ? "正在保存或核对费用分摊，请稍候。" : this.pending ? "操作结果尚未确认，请先核对保存结果。" : this.confirming ? "正在确认费用分摊，请稍候。" : ""; }
	async discardFor(action) {
		if (this.disposed || this.confirming) return false; const block = this.getCloseBlock(); if (block) { frappe.msgprint(__(block)); return false; }
		if (!this.isDirty()) { await action(); return true; } this.confirming = true; this.freeze(); const yes = await new Promise((resolve) => frappe.confirm(__("尚有未保存的分摊，放弃修改并继续？"), () => resolve(true), () => resolve(false))); this.confirming = false; this.freeze();
		if (yes && !this.disposed) { this.items = []; this.reason.value = ""; await action(); return true; } return false;
	}
	openNative(doctype, name) { if (!this.guardContext() || !this.bridge || !this.validName(name)) return; return this.discardFor(() => {
		this.source = null; this.summary = null; this.detail = null; this.sourceHost.replaceChildren(); this.summaryHost.replaceChildren(); this.history.replaceChildren(); this.details.replaceChildren(); this.dialog?.hide();
		this.bridge.open({ title: __("实际费用：") + name, module: this.scope.record_type === "mold" ? "模具" : "版种", kind: "form", doctype, route: ["Form", doctype, name] }); }); }
	destroy() { if (this.disposed) return; this.disposed = true; this.revision++; this.sourceRevision++; this.lookupRevision++; this.detailRevision++; this.source = null; this.summary = null; this.detail = null; this.lookupRows = []; this.items = []; this.reason.value = ""; this.cancelReason.value = ""; this.host.replaceChildren(); this.nativeHide?.(); }
};
