/* Shared finance dictionaries keep the four observed legacy editor fields. */
window.JifengFinanceDictionary = class JifengFinanceDictionary {
	constructor(host, type, bridge) {
		this.host = host; this.type = type; this.bridge = bridge; this.sequence = 0;
		this.start = 0; this.total = 0; this.rows = []; this.editors = new Set(); this.disposed = false;
		this.render();
		this.beforeUnload = event => { if (this.isDirty()) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener("beforeunload", this.beforeUnload);
		this.ready = Promise.resolve(this.company.set_value(frappe.defaults.get_user_default("Company") || "")).then(() => this.query(0));
	}

	element(tag, text, className) {
		const item = document.createElement(tag); if (text != null) item.textContent = __(String(text));
		if (className) item.className = className; return item;
	}
	button(label, action) {
		const item = this.element("button", label, "jfe-button"); item.type = "button";
		item.addEventListener("click", action); return item;
	}
	companyName() { return this.company.get_value() || ""; }
	scope() { return JSON.stringify([this.companyName(), this.type, this.search.value.trim(), Number(this.inactive.checked)]); }
	api(method, args, http = "GET") { return frappe.call({ method: "jifeng_erp.finance_dictionary." + method, args, type: http }).then(r => r.message); }

	render() {
		this.host.replaceChildren(); this.host.classList.add("jfe-finance-dictionary");
		const toolbar = this.element("div", null, "jfe-finance-toolbar");
		this.add = this.button("新增", () => this.open()); this.add.disabled = true;
		const companyHost = this.element("div", null, "jfe-finance-company");
		this.company = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"),
				onchange: () => { this.sequence++; this.clear(); this.query(0); } } });
		this.search = this.element("input"); this.search.type = "search"; this.search.maxLength = 200;
		this.search.placeholder = __("输入查询的关键字"); this.search.setAttribute("aria-label", __("查询关键字"));
		this.search.addEventListener("input", () => { this.sequence++; this.clear(); this.status.textContent = __("关键词已改变，请查询。"); });
		this.search.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); this.query(0); } });
		this.inactive = this.element("input"); this.inactive.type = "checkbox";
		this.inactive.addEventListener("change", () => this.query(0));
		const inactiveLabel = this.element("label", null, "jfe-finance-check"); inactiveLabel.append(this.inactive, this.element("span", "包含停用"));
		toolbar.append(this.add, companyHost, this.search, this.button("查询", () => this.query(0)), inactiveLabel);
		this.status = this.element("p", "请选择公司后查询。", "jfe-finance-status"); this.status.setAttribute("role", "status");
		const scroller = this.element("div", null, "jfe-finance-scroll"), table = this.element("table"); table.setAttribute("aria-label", __(this.type));
		const headings = this.element("tr"); for (const label of ["编号", "名称", "备注", "停用", "修改"]) headings.append(this.element("th", label));
		const head = this.element("thead"); head.append(headings); this.body = this.element("tbody"); table.append(head, this.body); scroller.append(table);
		const pager = this.element("div", null, "jfe-finance-toolbar");
		this.previous = this.button("上页", () => this.query(Math.max(0, this.start - 25)));
		this.next = this.button("下页", () => this.query(this.start + 25)); this.pageLabel = this.element("span");
		pager.append(this.previous, this.next, this.pageLabel); this.host.append(toolbar, this.status, scroller, pager); this.clear();
	}
	clear() { this.rows = []; this.total = 0; this.start = 0; this.loadedScope = null; this.body.replaceChildren(); this.add.disabled = true; this.pager(); }
	pager() {
		this.previous.disabled = !this.loadedScope || this.start <= 0;
		this.next.disabled = !this.loadedScope || this.start + 25 >= this.total;
		this.pageLabel.textContent = this.total ? `${Math.floor(this.start / 25) + 1}/${Math.ceil(this.total / 25)} · ${this.total}` : "0";
	}
	valid(row, company = this.companyName()) {
		return !!(row && typeof row.name === "string" && row.name && typeof row.modified === "string" && row.modified && row.company === company && row.dictionary_type === this.type &&
			typeof row.value === "string" && [0, 1].includes(Number(row.inactive)) && typeof row.can_write === "boolean");
	}
	async query(start = 0) {
		if (this.disposed) return;
		const company = this.companyName(), search = this.search.value.trim(), include_inactive = Number(this.inactive.checked), scope = this.scope();
		const sequence = ++this.sequence; this.clear(); if (!company) { this.status.textContent = __("请选择公司后查询。"); return; }
		this.status.textContent = __("正在查询…");
		try {
			const result = await this.api("list_dictionary", { dictionary_type: this.type, company, search, include_inactive, start, page_length: 25 });
			if (this.disposed || sequence !== this.sequence || scope !== this.scope()) return;
			if (!result || result.company !== company || result.dictionary_type !== this.type || result.search !== search ||
				Number(result.include_inactive) !== include_inactive || result.page_length !== 25 || !Number.isSafeInteger(result.total) || result.total < 0 ||
				!Number.isSafeInteger(result.start) || result.start < 0 || result.start % 25 || !Array.isArray(result.rows) ||
				result.start !== (result.total ? Math.min(start, Math.floor((result.total - 1) / 25) * 25) : 0) ||
				result.rows.length !== Math.min(25, Math.max(0, result.total - result.start)) || typeof result.can_create !== "boolean" ||
				result.rows.some(row => !this.valid(row, company) || !include_inactive && Number(row.inactive)) ||
				new Set(result.rows.map(row => row.name)).size !== result.rows.length) throw Error("Invalid finance dictionary response");
			this.rows = result.rows; this.start = result.start; this.total = result.total; this.loadedScope = scope; this.add.disabled = !result.can_create;
			for (const row of this.rows) {
				const tr = this.element("tr"); for (const field of ["legacy_number", "value", "remarks"]) tr.append(this.element("td", row[field] || ""));
				const inactive = this.element("td"), check = this.element("input"); check.type = "checkbox"; check.disabled = true; check.checked = !!Number(row.inactive);
				check.setAttribute("aria-label", __("停用")); inactive.append(check); tr.append(inactive);
				const edit = this.element("td"); edit.append(this.button(row.can_write ? "修改" : "查看", () => this.open(row.name))); tr.append(edit); this.body.append(tr);
			}
			this.pager(); this.status.textContent = __(this.total ? "查询完成" : "没有可见记录。");
		} catch { if (!this.disposed && sequence === this.sequence) this.status.textContent = __("查询失败，请重新查询。"); }
	}

	async open(name = null) {
		if (this.disposed || this.opening) return; this.opening = true;
		const active = name && [...this.editors].find(s => s.entry.name === name && !s.closed);
		if (active) { active.dialog.show(); this.opening = false; return; }
		const company = this.companyName(), args = { company, dictionary_type: this.type }; if (name) args.name = name;
		try {
			const entry = await this.api(name ? "read_dictionary" : "init_dictionary", args);
			if (this.disposed || company !== this.companyName()) return;
			if (!entry || entry.company !== company || entry.dictionary_type !== this.type || name && !this.valid(entry, company)) throw Error("Invalid finance editor scope");
			const writable = name ? !!entry.can_write : !!entry.can_create;
			const dialog = new frappe.ui.Dialog({ title: this.type + (name ? "修改" : "新增"), no_submit_on_enter: true,
				fields: [
					{ fieldname: "legacy_number", fieldtype: "Data", label: __("编号"), default: entry.legacy_number || "", read_only: 1, placeholder: __("保存时生成") },
					{ fieldname: "value", fieldtype: "Data", label: __("名称"), reqd: 1, default: entry.value || "", read_only: !writable },
					{ fieldname: "remarks", fieldtype: "Data", label: __("备注"), default: entry.remarks || "", read_only: !writable },
					{ fieldname: "inactive", fieldtype: "Check", label: __("停用"), default: Number(entry.inactive || 0), read_only: !writable }
				] });
			dialog.$wrapper.addClass("jfe-finance-editor");
			const state = { dialog, entry, company, writable, request_id: name ? null : crypto.randomUUID(), saving: false, unresolved: false, closed: false };
			this.editors.add(state); state.original = JSON.stringify(this.values(state));
			if (writable) dialog.set_primary_action(__("保存"), () => this.save(state));
			if (entry.can_create) dialog.add_custom_action(__("新增"), () => this.newEntry(state));
			dialog.add_custom_action(__("核对保存结果"), () => this.resolve(state));
			dialog.add_custom_action(__("关闭"), () => dialog.hide()); this.guard(state); dialog.show();
		} catch { if (!this.disposed) frappe.msgprint(__("读取财务资料失败，请重新打开。")); }
		finally { this.opening = false; }
	}
	newEntry(state) {
		if (state.saving || state.unresolved) { frappe.msgprint(__("保存结果尚未确认，请先核对保存结果。")); return; }
		const replace = () => { state.original = JSON.stringify(this.values(state)); state.dialog.hide(); this.open(); };
		if (JSON.stringify(this.values(state)) !== state.original) frappe.confirm(__("放弃当前未保存修改，新增一条资料？"), replace); else replace();
	}
	values(state) {
		return { legacy_number: state.dialog.get_value("legacy_number") || "", value: state.dialog.get_value("value") || "",
			remarks: state.dialog.get_value("remarks") || "", inactive: Number(!!state.dialog.get_value("inactive")) };
	}
	payload(state) {
		return { company: state.company, dictionary_type: this.type, ...this.values(state),
			...(state.entry.name ? { name: state.entry.name, modified: state.entry.modified } : { request_id: state.request_id }) };
	}
	matches(state, row) {
		const values = state.attempt; return this.valid(row, state.company) && row.name === (state.entry.name || "JF-FIN-" + state.request_id) &&
			values && (!state.entry.name || (row.legacy_number || "") === values.legacy_number) && typeof row.legacy_number === "string" && row.legacy_number &&
			["value", "remarks"].every(field => (row[field] || "") === values[field]) && Number(row.inactive) === values.inactive;
	}
	freeze(state) {
		for (const field of ["value", "remarks", "inactive"]) state.dialog.set_df_property(field, "read_only", !state.writable || state.saving || state.unresolved);
		state.dialog.get_primary_btn().prop("disabled", !state.writable || state.saving || state.unresolved);
	}
	async accepted(state, row) {
		state.entry = row; state.writable = !!row.can_write; state.unresolved = false;
		await state.dialog.set_value("legacy_number", row.legacy_number); state.original = JSON.stringify(this.values(state));
		this.query(this.start);
	}
	guard(state) {
		const hide = state.dialog.hide.bind(state.dialog);
		state.dialog.hide = () => {
			if (state.closed) return;
			if (state.saving || state.unresolved) { frappe.msgprint(__("保存结果尚未确认，请先核对保存结果。")); return; }
			const close = () => { state.closed = true; this.editors.delete(state); hide(); };
			if (JSON.stringify(this.values(state)) !== state.original) frappe.confirm(__("尚有未保存的修改，关闭并放弃修改？"), close); else close();
		};
		state.dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn().removeAttr("data-dismiss").on("click.finance", () => state.dialog.hide());
		state.dialog.$wrapper.on("keydown.finance", e => { if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); state.dialog.hide(); } });
	}
	async save(state) {
		if (!state.writable || state.saving || state.unresolved || state.closed || !state.dialog.get_values()) return;
		state.attempt = this.values(state); state.saving = true; this.freeze(state);
		try {
			const row = await this.api("save_dictionary", { payload: this.payload(state) }, "POST");
			if (!this.matches(state, row)) throw Error("Unconfirmed finance save");
			await this.accepted(state, row); frappe.show_alert({ message: __("已保存"), indicator: "green" });
		} catch (error) {
			state.unresolved = !(error && Number(error.status) >= 400 && Number(error.status) < 500);
			if (state.unresolved) frappe.msgprint(__("保存结果尚未确认，请核对保存结果后继续。"));
		} finally { state.saving = false; this.freeze(state); }
	}
	async resolve(state) {
		if (!state.unresolved || state.saving || state.closed) return;
		state.saving = true; this.freeze(state);
		try {
			const args = { company: state.company, dictionary_type: this.type };
			const method = state.entry.name ? "read_dictionary" : "read_creation";
			if (state.entry.name) args.name = state.entry.name; else args.request_id = state.request_id;
			const row = await this.api(method, args);
			if (!this.matches(state, row)) {
				if (this.valid(row, state.company) && row.name === (state.entry.name || "JF-FIN-" + state.request_id)) {
					frappe.confirm(__("服务器记录与当前草稿不同，读取服务器当前内容并放弃草稿？"), async () => {
						state.saving = true; this.freeze(state);
						try {
							await state.dialog.set_values({ legacy_number: row.legacy_number, value: row.value, remarks: row.remarks || "", inactive: Number(row.inactive) });
							await this.accepted(state, row); frappe.show_alert({ message: __("已读取服务器当前内容"), indicator: "blue" });
						} finally { state.saving = false; this.freeze(state); }
					});
				} else frappe.msgprint(__("尚未核实当前草稿已保存，请稍后再次核对。"));
				return;
			}
			await this.accepted(state, row); frappe.show_alert({ message: __("保存结果已确认"), indicator: "green" });
		} finally { state.saving = false; this.freeze(state); }
	}
	isDirty() { return [...this.editors].some(s => s.saving || s.unresolved || JSON.stringify(this.values(s)) !== s.original); }
	getCloseBlock() { return this.isDirty() ? "请先保存、核对结果或关闭财务资料编辑窗口。" : ""; }
	destroy() { this.disposed = true; this.sequence++; window.removeEventListener("beforeunload", this.beforeUnload); for (const state of this.editors) state.dialog.hide(); this.host.replaceChildren(); }
};
