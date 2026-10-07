/* Customer vocabularies keep independent records, separate from native Customer's legal types. */
window.JifengCustomerType = class JifengCustomerType extends window.JifengProductDictionary {
	constructor(host, type = "客户类型", title = type) {
		super(host, type, { api: "jifeng_erp.customer_type", title, hideAbbreviation: true, editorSuffix: true });
	}

	updateAddButton() { super.updateAddButton(); if (this.deletion && this.addButton) this.addButton.disabled = true; }
	async openEditor(name) { if (!this.deletion) return super.openEditor(name); }
	async initialize() {
		const result = await super.initialize();
		if (result) this.limits.textContent = __("编号由服务器分配；无业务引用的词条可按权限删除，操作记录永久保留。简拼、打印与预览暂不可用。");
		return result;
	}
	validDictionaryRow(row) {
		return !!(row?.name && typeof row.version === "string" && row.version &&
			row.company === this.company() && row.dictionary_type === this.type && typeof row.value === "string");
	}

	build() {
		this.start = 0; this.total = 0; this.loadedScope = null;
		this.host.classList.add("jfe-product-dictionary", "jfe-customer-type"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-product-dictionary-toolbar");
		this.addButton = this.button("新增", () => this.openEditor()); this.addButton.disabled = true;
		const companyHost = this.element("div", undefined, "jfe-product-dictionary-company");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), reqd: true,
				onchange: () => this.changeScope() } });
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("输入查询的关键字"); this.search.setAttribute("aria-label", __("查询关键字"));
		this.search.addEventListener("input", () => { this.revision++; this.status.textContent = __("关键词已改变，请查询。"); });
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(0); } });
		this.includeInactive = this.element("input"); this.includeInactive.type = "checkbox";
		this.includeInactive.addEventListener("change", () => this.query(0));
		const inactiveLabel = this.element("label", undefined, "jfe-product-dictionary-inactive");
		inactiveLabel.append(this.includeInactive, this.element("span", __("包含停用")));
		const format = this.element("select"); format.disabled = true; format.setAttribute("aria-label", __("打印格式"));
		const option = this.element("option", __("格式1")); option.value = "1"; format.append(option);
		format.title = __("打印格式尚未核实");
		const previewLabel = this.element("label", undefined, "jfe-product-dictionary-inactive"), preview = this.element("input");
		preview.type = "checkbox"; preview.checked = true; preview.disabled = true; previewLabel.append(preview, this.element("span", __("预览")));
		preview.title = __("打印预览尚未实现");
		toolbar.append(this.addButton, this.button("更新简拼", null, "简拼更新规则尚未核实"), companyHost,
			this.search, this.button("查询", () => this.query(0)), inactiveLabel, format,
			this.button("打印", null, "打印格式尚未核实"), previewLabel);
		this.limits = this.element("p", __("名称、备注和停用可保存；删除保留永久操作记录。更新简拼、打印暂不可用。"), "jfe-product-dictionary-limits");
		this.status = this.element("p", __("正在读取权限…"), "jfe-product-dictionary-status"); this.status.setAttribute("role", "status");
		this.scroller = this.element("div", undefined, "jfe-product-dictionary-scroll");
		const table = this.element("table", undefined, "jfe-product-dictionary-table"); table.setAttribute("aria-label", __(this.dictionaryTitle));
		const thead = this.element("thead"), headings = this.element("tr");
		const labels = this.isOrderDictionary() ? ["编号", "名称", "备注", "简拼", "停用", "修改", "删除"] :
			["编号", "名称", "简拼", "备注", "停用", "操作", "日期", "修改人", this.type === "客户类型" ? "修改时间" : "修改日期", "修改", "删除"];
		for (const label of labels) {
			const th = this.element("th", __(label)); th.scope = "col"; headings.append(th);
		}
		thead.append(headings); this.tbody = this.element("tbody"); table.append(thead, this.tbody); this.scroller.append(table);
		const pager = this.element("div", undefined, "jfe-product-dictionary-toolbar");
		this.previousButton = this.button("上页", () => this.query(Math.max(0, this.start - this.pageSize())));
		this.nextButton = this.button("下页", () => this.query(this.start + this.pageSize()));
		this.pageLength = this.element("select"); this.pageLength.setAttribute("aria-label", __("每页条数"));
		for (const size of [25, 50, 100]) { const item = this.element("option", String(size)); item.value = String(size); this.pageLength.append(item); }
		this.pageLength.value = "25"; this.pageLength.addEventListener("change", () => this.query(0));
		this.pageLabel = this.element("span"); pager.append(this.previousButton, this.nextButton, this.pageLength, this.pageLabel);
		this.deleteReview = this.element("div");
		this.host.append(toolbar, this.limits, this.status, this.scroller, pager, this.deleteReview); this.updatePager();
	}

	pageSize() { return Number(this.pageLength.value); }
	isOrderDictionary() { return ["订单客户分类", "订单客户级别"].includes(this.type); }
	changeScope() { this.start = 0; this.total = 0; this.loadedScope = null; this.updatePager(); super.changeScope(); }
	updatePager() {
		const page = this.total ? Math.floor(this.start / this.pageSize()) + 1 : 0;
		this.pageLabel.textContent = __("第") + page + "/" + Math.ceil(this.total / this.pageSize()) + __("页") + " · " + this.total + __("条");
		this.previousButton.disabled = !this.loadedScope || this.start <= 0;
		this.nextButton.disabled = !this.loadedScope || this.start + this.pageSize() >= this.total;
	}

	async query(start = this.start) {
		if (this.disposed || !this.readyArchive) return;
		const company = this.company(), search = this.search.value || "", include_inactive = Number(!!this.includeInactive.checked), page_length = this.pageSize();
		if (!Number.isSafeInteger(start) || start < 0 || ![25, 50, 100].includes(page_length)) return;
		const scope = JSON.stringify([company, search, include_inactive, page_length]);
		if (scope !== this.loadedScope) start = 0;
		const sequence = ++this.revision, scroll = { top: this.scroller.scrollTop, left: this.scroller.scrollLeft };
		this.rows = []; this.total = 0; this.loadedScope = null; this.tbody.replaceChildren(); this.updatePager();
		if (!company) { this.status.textContent = __("请选择公司后查询。"); return; }
		this.status.textContent = __("正在查询…");
		try {
			const response = await frappe.call({ method: this.dictionaryApi + ".list_dictionary",
				args: { dictionary_type: this.type, company, search, include_inactive, start, page_length } });
			if (this.disposed || sequence !== this.revision || scope !== JSON.stringify([this.company(), this.search.value || "", Number(!!this.includeInactive.checked), this.pageSize()])) return;
			const result = response.message;
			if (!result || result.company !== company || result.dictionary_type !== this.type || result.search !== search ||
				Number(result.include_inactive) !== include_inactive || result.page_length !== page_length || !Array.isArray(result.rows) ||
				!Number.isSafeInteger(result.start) || result.start < 0 || result.start % page_length || !Number.isSafeInteger(result.total) || result.total < 0 ||
				result.start !== (result.total ? Math.min(start, Math.floor((result.total - 1) / page_length) * page_length) : 0) ||
				result.rows.length !== Math.min(page_length, Math.max(0, result.total - result.start)) ||
				new Set(result.rows.map((row) => row?.name)).size !== result.rows.length ||
				result.rows.some((row) => !row?.name || typeof row.version !== "string" || !row.version || typeof row.value !== "string" ||
					row.company !== company || row.dictionary_type !== this.type || ![0, 1].includes(Number(row.inactive)) || !include_inactive && Number(row.inactive))) throw new Error("Incomplete customer type scope");
			this.rows = result.rows; this.total = result.total; this.start = result.start; this.loadedScope = scope;
			for (const row of this.rows) this.renderRow(row);
			this.updatePager(); this.scroller.scrollTop = scroll.top; this.scroller.scrollLeft = scroll.left;
			this.status.textContent = this.total ? __("查询完成") : __("没有可见记录。");
		} catch { if (!this.disposed && sequence === this.revision) this.status.textContent = __("查询失败，请重新查询。"); }
	}

	renderRow(row) {
		const tr = this.element("tr");
		const fields = this.isOrderDictionary() ? ["legacy_number", "value", "remarks", "abbreviation"] :
			["legacy_number", "value", "abbreviation", "remarks"];
		for (const field of fields) tr.append(this.element("td", row[field] ?? ""));
		const inactive = this.element("td"), check = this.element("input"); check.type = "checkbox"; check.checked = !!Number(row.inactive); check.disabled = true;
		check.setAttribute("aria-label", __("停用")); inactive.append(check); tr.append(inactive);
		if (!this.isOrderDictionary()) {
			for (const field of ["owner", "creation", "modified_by", "version"]) tr.append(this.element("td", row[field] ?? ""));
		}
		const operation = this.element("td"); operation.append(this.button(this.readyArchive?.can_write ? "修改" : "查看", () => this.openEditor(row.name))); tr.append(operation);
		const deletion = this.element("td"), button = this.button("删除", () => this.confirmDelete(row));
		button.disabled = row.can_delete !== true || !row.creation || !!this.deletion;
		deletion.append(button); tr.append(deletion); this.tbody.append(tr);
	}

	isDirty(state = this.editor) { return state?.deleted ? false : super.isDirty(state); }
	getCloseBlock() {
		if (this.deletion) return this.deletion.confirming ? "正在确认删除，请先完成确认。" :
			this.deletion.busy ? "正在删除或核对客户词条，请稍候。" : "删除结果尚未确认，请先核对删除记录。";
		return super.getCloseBlock();
	}
	freeze(state) {
		super.freeze(state);
		if (state.deleted) {
			for (const field of this.editorFields()) if (state.dialog.fields_dict[field]) state.dialog.set_df_property(field, "read_only", true);
			state.dialog.get_primary_btn().prop("disabled", true);
		}
	}
	async save(state) { if (!state.deleted) return super.save(state); }
	newDraftReady(state) { state.deleted = false; }
	async resolveCreationState(state, result) {
		if (result?.status !== "deleted") return false;
		if (this.disposed || state.closed || this.editor !== state) return true;
		const candidate = state.candidate;
		if (result.request_id !== candidate.request_id || result.company !== candidate.company || result.dictionary_type !== this.type ||
			!result.name || typeof result.creation !== "string" || !result.creation || typeof result.version !== "string" || !result.version) return true;
		state.unresolved = false; state.deleted = true; state.conflict = null;
		state.review.replaceChildren(this.element("p", __("该新建请求曾成功保存的客户词条已删除。原草稿不会重复保存；如需另建，请点新增取得新请求。")));
		if (this.company() === state.company) this.query();
		return true;
	}

	confirmDelete(row) {
		if (this.disposed || this.deletion || this.editor && !this.editor.closed || row.can_delete !== true ||
			!this.validDictionaryRow(row) || typeof row.creation !== "string" || !row.creation || !this.rows.includes(row)) return;
		const state = this.deletion = { confirming: true, busy: false, unresolved: false,
			payload: { name: row.name, creation: row.creation, version: row.version, company: row.company,
				dictionary_type: this.type, request_id: this.requestID() } };
		this.updateAddButton();
		frappe.confirm(__("确认删除所选客户词条？创建回执和删除记录将永久保留。已有业务引用的客户词条不能删除。"),
			() => { state.confirming = false; return this.deleteEntry(state); },
			() => { if (this.deletion === state) { this.deletion = null; this.updateAddButton(); } });
	}

	deletionReply(state, result) {
		return !!(result && ["deleted", "not_deleted"].includes(result.status) &&
			Object.entries(state.payload).every(([field, value]) => result[field] === value));
	}
	showDeletion(state) {
		this.deleteReview.replaceChildren();
		if (this.disposed || this.deletion !== state || !state.unresolved) return;
		this.deleteReview.append(this.element("p", __("删除结果尚未确认；保留本次请求，只读核对，不会自动重复删除。")),
			this.button("核对删除结果", () => this.resolveDelete(state)));
	}
	async deleteEntry(state) {
		if (this.disposed || this.deletion !== state || state.busy || state.unresolved || state.confirming) return;
		state.busy = true;
		try {
			const response = await frappe.call({ method: this.dictionaryApi + ".delete_dictionary", type: "POST",
				args: { payload: state.payload }, freeze: true });
			if (response.message?.status !== "deleted" || !this.deletionReply(state, response.message)) { state.unresolved = true; return; }
			this.finishDelete(state, "客户词条已删除，删除记录已保留");
		} catch (error) {
			if (this.definiteFailure(error)) this.finishDelete(state, "删除未完成，请重新查询核对；原记录保留");
			else state.unresolved = true;
		} finally { state.busy = false; this.showDeletion(state); this.updateAddButton(); }
	}
	finishDelete(state, message) {
		if (this.disposed || this.deletion !== state) return;
		this.deletion = null; this.deleteReview.replaceChildren();
		this.status.textContent = __(message);
		frappe.show_alert({ message: __(message), indicator: "blue" });
		if (this.company() === state.payload.company) this.query();
	}
	async resolveDelete(state = this.deletion) {
		if (this.disposed || this.deletion !== state || !state?.unresolved || state.busy) return;
		state.busy = true;
		try {
			const response = await frappe.call({ method: this.dictionaryApi + ".resolve_delete", type: "GET",
				args: { payload: state.payload }, freeze: true });
			if (!this.deletionReply(state, response.message)) return;
			this.finishDelete(state, response.message.status === "deleted" ? "已核对删除记录；未重复删除" : "已核对原客户词条仍存在；未重复删除，请重新查询");
		} catch { /* Permission or unknown outcomes keep the exact held request. */ }
		finally { state.busy = false; this.showDeletion(state); this.updateAddButton(); }
	}
};
