/* Order vocabularies have scoped records and preserve the two observed layouts. */
window.JifengOrderDictionary = class JifengOrderDictionary extends window.JifengProductDictionary {
	constructor(host, type = "订单类型", title = type) {
		super(host, type, { api: "jifeng_erp.order_dictionary", title, hideAbbreviation: true, editorSuffix: true,
			...(type === "订单要求" ? { editorFields: ["legacy_number", "value", "remarks", "inactive"] } : {}) });
	}

	canCreate() { return this.type === "订单要求"; }
	updateAddButton() {
		if (this.addButton) this.addButton.disabled = !this.canCreate() || !this.company() || !this.readyArchive?.can_write || this.opening;
	}
	async openEditor(name) {
		if (!name && !this.canCreate()) return;
		const result = await super.openEditor(name);
		if (!this.canCreate()) this.editor?.newButton?.hide?.();
		return result;
	}
	newDraft(state) { if (this.canCreate()) super.newDraft(state); }

	build() {
		this.start = 0; this.total = 0; this.loadedScope = null;
		this.host.classList.add("jfe-product-dictionary", "jfe-order-dictionary"); this.host.replaceChildren();
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
		this.limits = this.element("p", __("名称、备注和停用可保存；更新简拼、删除、打印暂不可用。"), "jfe-product-dictionary-limits");
		this.status = this.element("p", __("正在读取权限…"), "jfe-product-dictionary-status"); this.status.setAttribute("role", "status");
		this.scroller = this.element("div", undefined, "jfe-product-dictionary-scroll");
		const table = this.element("table", undefined, "jfe-product-dictionary-table"); table.setAttribute("aria-label", __(this.dictionaryTitle));
		const thead = this.element("thead"), headings = this.element("tr");
		const labels = this.columnLabels();
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
		this.host.append(toolbar, this.limits, this.status, this.scroller, pager); this.updatePager();
	}

	pageSize() { return Number(this.pageLength.value); }
	isCompactDictionary() { return this.type === "订单要求"; }
	columnLabels() { return this.isCompactDictionary() ? ["编号", "名称", "备注", "简拼", "停用", "修改", "删除"] :
		["编号", "名称", "备注", "简拼", "停用", "操作", "日期", "修改人", "修改日期", "修改", "删除"]; }
	validDictionaryRow(row) { return !!(row?.name && typeof row.version === "string" && row.version && typeof row.value === "string" &&
		row.company === this.company() && row.dictionary_type === this.type && [0, 1].includes(Number(row.inactive))); }
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
				result.rows.some((row) => !this.validDictionaryRow(row) || !include_inactive && Number(row.inactive))) throw new Error("Incomplete order dictionary scope");
			this.rows = result.rows; this.total = result.total; this.start = result.start; this.loadedScope = scope;
			for (const row of this.rows) this.renderRow(row);
			this.updatePager(); this.scroller.scrollTop = scroll.top; this.scroller.scrollLeft = scroll.left;
			this.status.textContent = this.total ? __("查询完成") : __("没有可见记录。");
		} catch { if (!this.disposed && sequence === this.revision) this.status.textContent = __("查询失败，请重新查询。"); }
	}

	renderRow(row) {
		const tr = this.element("tr");
		const fields = ["legacy_number", "value", "remarks", "abbreviation"];
		for (const field of fields) tr.append(this.element("td", row[field] ?? ""));
		const inactive = this.element("td"), check = this.element("input"); check.type = "checkbox"; check.checked = !!Number(row.inactive); check.disabled = true;
		check.setAttribute("aria-label", __("停用")); inactive.append(check); tr.append(inactive);
		if (!this.isCompactDictionary()) {
			for (const field of ["owner", "creation", "modified_by", "version"]) tr.append(this.element("td", row[field] ?? ""));
		}
		const operation = this.element("td"); operation.append(this.button(this.readyArchive?.can_write ? "修改" : "查看", () => this.openEditor(row.name))); tr.append(operation);
		const deletion = this.element("td"); deletion.append(this.button("删除", null, "删除尚未实现")); tr.append(deletion); this.tbody.append(tr);
	}
};
