/* Customer profiles preserve the observed module-specific fields and native Customer identity. */
window.JifengCustomerArchive = class JifengCustomerArchive {
	constructor(host, kind = "销售客户") {
		if (!["销售客户", "订单客户"].includes(kind)) throw new Error("不支持的客户档案种类");
		this.host = host; this.kind = kind; this.sales = kind === "销售客户";
		this.companyScope = null; this.page = 1; this.pageSize = 100; this.total = this.totalPages = null;
		this.rows = []; this.revision = this.openRevision = 0; this.disposed = this.opening = false; this.editor = null;
		this.statusFilter = "在用";
		this.fields = [["legacy_number", "编号"], ["legacy_code", "代码"], ["category", "分类"], ["grade", "级别"],
			...(this.sales ? [["customer_kind", "类型"]] : []), ["display_name", "名称"], ...(this.sales ? [["discount", "折扣"]] : []),
			["region", "区域"], ["province", "省份"], ["district", "地区"], ["salesperson", "导购"], ["contact_name", "联系"],
			["tax_number", "税号"], ["company_text", "公司"], ["mobile", "手机"], ["legal_person", "法人"],
			["address", "地址"], ["notes", "备注"], ["inactive", "停用"]];
		this.listFields = this.sales ? [["legacy_number", "编号"], ["category", "分类"], ["grade", "级别"], ["customer_kind", "类型"],
			["legacy_code", "代码"], ["display_name", "名称"], ["salesperson", "导购"], ["discount", "折扣"], ["contact_name", "联系"]] :
			[["legacy_number", "编号"], ["region", "区域"], ["province", "省份"], ["district", "地区"], ["legacy_code", "代码"],
				["display_name", "名称"], ["salesperson", "导购"], ["contact_name", "联系"], ["mobile", "手机"]];
		this.dictionaryTypes = this.sales ? { category: "客户分类", grade: "客户等级", customer_kind: "客户类型" } :
			{ category: "订单客户分类", grade: "订单客户级别" };
		this.labels = Object.fromEntries(this.fields);
		this.beforeUnload = (event) => { if (this.isDirty() || this.getCloseBlock()) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener?.("beforeunload", this.beforeUnload);
		this.ready = this.render();
	}
	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className;
		return node;
	}
	button(label, action, reason) {
		const node = this.element("button", __(label), "jfe-button"); node.type = "button";
		if (reason) { node.disabled = true; node.title = __(reason); }
		else node.addEventListener("click", action);
		return node;
	}
	control(host, fieldname, label, fieldtype, options, onchange) {
		const cell = this.element("div"); host.append(cell);
		return frappe.ui.form.make_control({ parent: cell, render_input: true,
			df: { fieldname, label: __(label), fieldtype, options, onchange } });
	}
	flags(control, flags) {
		let changed = false;
		for (const [field, value] of Object.entries(flags)) if (control.df[field] !== value) { control.df[field] = value; changed = true; }
		if (changed) control.refresh?.();
	}
	call(method, args, write = false) {
		return frappe.call({ method: "jifeng_erp.customer_archive." + method, args, type: write ? "POST" : "GET", freeze: false });
	}
	async render() {
		this.host.classList.add("jfe-customer-archive"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-customer-toolbar"), company = this.element("div", undefined, "jfe-customer-company");
		this.newButton = this.button("新增", () => this.openNew());
		this.companyControl = this.control(company, "company", "当前公司", "Link", "Company", () => this.changeCompany());
		this.companyControl.$input?.on?.("input.jifeng-customer-company change.jifeng-customer-company", () => this.changeCompany());
		this.search = this.element("input"); this.search.type = "search"; this.search.maxLength = 140;
		this.search.placeholder = __("输入查询的关键字"); this.search.setAttribute("aria-label", __("查询关键字"));
		this.search.addEventListener("input", () => { this.revision++; this.clearResults(); });
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(1); } });
		const status = this.element("label", undefined, "jfe-customer-state"); status.append(this.element("span", __("状态")));
		this.statusSelect = this.element("select"); this.statusSelect.setAttribute("aria-label", __("状态"));
		for (const value of ["在用", "停用", "全部"]) { const option = this.element("option", __(value)); option.value = value; this.statusSelect.append(option); }
		this.statusSelect.value = this.statusFilter; this.statusSelect.addEventListener("change", () => this.changeStatus()); status.append(this.statusSelect);
		this.queryButton = this.button("查询", () => this.query(1));
		toolbar.append(this.newButton, company, this.search, this.queryButton, status,
			this.button("更新简拼", null, "更新简拼暂不可用"), this.button("导入", null, "导入暂不可用"), this.button("打印", null, "打印暂不可用"));
		this.status = this.element("p", __("请选择当前公司后查询。"), "jfe-customer-status"); this.status.setAttribute("role", "status");
		this.limits = this.element("p", __("部分可用：可新增、查询、修改和停用；更新简拼、删除、导入及打印暂不可用。"), "jfe-customer-status");
		this.scroller = this.element("div", undefined, "jfe-customer-scroll");
		const table = this.element("table", undefined, "jfe-customer-table"), thead = this.element("thead"), heading = this.element("tr");
		table.setAttribute("aria-label", __(this.sales ? "客户档案" : "订单客户"));
		for (const label of [...this.listFields.map(([, label]) => label), "停用", "操作", "日期", "修改人", "修改日期"]) {
			const th = this.element("th", __(label)); th.scope = "col"; heading.append(th);
		}
		for (const [label, className] of [["修改", "jfe-customer-modify"], ["删除", "jfe-customer-delete"]]) {
			const th = this.element("th", __(label), "jfe-customer-action " + className); th.scope = "col"; heading.append(th);
		}
		thead.append(heading); this.tbody = this.element("tbody"); table.append(thead, this.tbody); this.scroller.append(table);
		const pager = this.element("div", undefined, "jfe-customer-pager");
		this.previous = this.button("上页", () => this.query(this.page - 1)); this.next = this.button("下页", () => this.query(this.page + 1));
		this.pageLabel = this.element("span", "—"); this.count = this.element("span", __("尚未查询"));
		this.goto = this.element("input"); this.goto.type = "text"; this.goto.inputMode = "numeric"; this.goto.value = "1"; this.goto.setAttribute("aria-label", __("页码"));
		this.goto.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(this.goto.value); } });
		this.goButton = this.button("前往", () => this.query(this.goto.value));
		pager.append(this.previous, this.pageLabel, this.next, this.goto, this.goButton, this.count);
		this.host.append(toolbar, this.limits, this.status, this.scroller, pager); this.renderPager(); this.freezeToolbar();
	}
	freezeToolbar() {
		const locked = !!(this.disposed || this.opening || this.editor);
		this.flags(this.companyControl, { read_only: locked });
		this.statusSelect.disabled = this.search.disabled = this.queryButton.disabled = locked;
		this.newButton.disabled = !!(locked || !this.companyScope || !this.readyArchive?.can_write ||
			!this.readyArchive.can_create_customer && !this.readyArchive.can_link_customer);
		this.previous.disabled = locked || this.total === null || this.page <= 1;
		this.next.disabled = locked || this.total === null || this.page >= this.totalPages;
		this.goto.disabled = this.goButton.disabled = locked || this.total === null;
	}
	clearResults() { this.rows = []; this.total = this.totalPages = null; this.tbody?.replaceChildren(); this.renderPager(); }
	renderPager() {
		if (!this.previous) return;
		this.pageLabel.textContent = this.total === null ? "—" : this.total ? String(this.page) + "/" + this.totalPages : "0/0";
		this.count.textContent = this.total === null ? __("尚未查询") : __("共") + " " + this.total + " " + __("条");
		this.freezeToolbar();
	}
	async changeCompany() {
		if (this.settingCompany || this.disposed) return;
		const desired = this.companyControl.get_value() || null;
		if (desired === this.companyScope) return;
		if (this.editor || this.opening) {
			this.settingCompany = true;
			try { await this.companyControl.set_value(this.companyScope); } finally { this.settingCompany = false; }
			return;
		}
		this.syncCompany(); await this.permissionPending;
	}
	syncCompany() {
		if (this.disposed || this.editor || this.opening) return;
		const desired = this.companyControl.get_value() || null;
		if (desired === this.companyScope) return;
		this.companyScope = desired; this.revision++; this.openRevision++; this.permissionRevision = (this.permissionRevision || 0) + 1;
		this.readyArchive = null; this.page = 1; this.goto.value = "1"; this.clearResults();
		this.status.textContent = __(desired ? "公司已切换，请查询。" : "请选择当前公司后查询。");
		this.permissionPending = desired ? this.loadPermission(desired, this.permissionRevision) : Promise.resolve();
	}
	async loadPermission(company, sequence) {
		try {
			const response = await this.call("init_archive", { kind: this.kind, company });
			if (this.disposed || sequence !== this.permissionRevision || company !== this.companyScope || company !== (this.companyControl.get_value() || null)) return;
			if (!this.validArchive(response.message, company, null)) throw new Error("Incomplete customer capability");
			this.readyArchive = response.message;
		} catch {
			if (!this.disposed && sequence === this.permissionRevision && company === this.companyScope) this.readyArchive = null;
		} finally { if (!this.disposed && sequence === this.permissionRevision) this.freezeToolbar(); }
	}
	changeStatus() {
		const desired = this.statusSelect.value;
		if (this.disposed || this.editor || this.opening || !["在用", "停用", "全部"].includes(desired)) { this.statusSelect.value = this.statusFilter; return; }
		if (desired === this.statusFilter) return;
		this.statusFilter = desired; this.revision++; this.openRevision++; this.page = 1; this.goto.value = "1";
		this.clearResults(); this.status.textContent = __("状态已切换，请查询。");
	}
	pageNumber(value) {
		if (typeof value === "string" && /^[1-9][0-9]*$/.test(value)) value = Number(value);
		return Number.isSafeInteger(value) && value > 0 ? value : null;
	}
	current(sequence, args) {
		return !this.disposed && sequence === this.revision && args.kind === this.kind && args.company === this.companyScope &&
			args.company === (this.companyControl.get_value() || null) && args.search === this.search.value && args.status === this.statusFilter;
	}
	async query(target = 1) {
		if (this.disposed || this.editor || this.opening) return;
		this.syncCompany(); const page = this.pageNumber(target);
		if (!this.companyScope || !page) { this.revision++; this.clearResults(); this.status.textContent = __(!this.companyScope ? "请选择当前公司。" : "请输入有效页码。"); return; }
		const args = { kind: this.kind, company: this.companyScope, search: this.search.value, status: this.statusFilter, page, page_size: this.pageSize };
		const sequence = ++this.revision, scroll = { top: this.scroller.scrollTop, left: this.scroller.scrollLeft };
		this.clearResults(); this.status.textContent = __("正在查询…");
		try {
			const response = await this.call("query_archives", args);
			if (!this.current(sequence, args)) return;
			if (!this.validPage(response.message, args)) throw new Error("Incomplete customer archive page");
			Object.assign(this, { rows: response.message.rows, page: response.message.page, total: response.message.total, totalPages: response.message.total_pages });
			this.goto.value = String(this.page); this.renderRows(); this.renderPager();
			this.scroller.scrollTop = scroll.top; this.scroller.scrollLeft = scroll.left;
			this.status.textContent = this.total ? __("查询完成。") : __("没有可见记录。");
		} catch { if (this.current(sequence, args)) { this.clearResults(); this.status.textContent = __("查询未完成，请核对条件和权限后重试。"); } }
	}
	validArchive(row, company, name = undefined) {
		if (!row || row.kind !== this.kind || row.company !== company || typeof row.can_write !== "boolean" || !row.profile || !row.field_meta ||
			row.profile.company !== company || row.profile.archive_kind !== this.kind || ![0, 1, false, true].includes(row.profile.inactive) ||
			!["company", "archive_kind", "customer", "legacy_number", "display_name", "inactive"].every((field) => row.field_meta[field]) ||
			(name !== undefined && row.name !== name) || row.numbering_mode !== "automatic_v1" || typeof row.number_reference_only !== "boolean" ||
			typeof row.can_create_customer !== "boolean" || typeof row.can_link_customer !== "boolean" || !row.customer_defaults || !row.customer_field_meta || !row.dictionary_versions || !row.dictionary_values) return false;
		if (!this.sales && ("customer_kind" in row.profile || "discount" in row.profile || "customer_kind" in row.dictionary_versions)) return false;
		if (row.name !== null && (typeof row.name !== "string" || !row.name || row.number_reference_only ||
			typeof row.profile_version !== "string" || !row.profile_version || typeof row.customer_version !== "string" || !row.customer_version ||
			typeof row.customer !== "string" || !row.customer || row.profile.customer !== row.customer || typeof row.customer_disabled !== "boolean")) return false;
		if (row.name === null && (!row.number_reference_only || row.profile_version !== null || row.customer !== null || row.customer_version !== null)) return false;
		if (Object.entries(row.customer_defaults).some(([field, value]) => !["customer_group", "territory", "customer_type", "naming_series"].includes(field) ||
			!row.customer_field_meta[field] || !["Link", "Select", "Data"].includes(row.customer_field_meta[field].fieldtype) ||
			typeof row.customer_field_meta[field].read_only !== "boolean" || value !== null && typeof value !== "string")) return false;
		for (const field of Object.keys(this.dictionaryTypes)) {
			if (row.profile[field] && (typeof row.dictionary_versions[field] !== "string" || !row.dictionary_versions[field] ||
				row.field_meta[field]?.fieldtype !== "Link" || row.field_meta[field]?.options !== "Jifeng Customer Type")) return false;
		}
		if (Object.entries(row.dictionary_versions).some(([field, value]) => !(field in this.dictionaryTypes) || !row.profile[field] || typeof value !== "string" || !value)) return false;
		if (Object.keys(row.dictionary_values).length !== Object.keys(row.dictionary_versions).length ||
			Object.entries(row.dictionary_values).some(([field, value]) => !(field in row.dictionary_versions) || typeof value !== "string")) return false;
		if (![row.owner, row.creation, row.modified_by].every((value) => value === null || typeof value === "string")) return false;
		return Object.entries(row.profile).every(([field, value]) => row.field_meta[field] && typeof row.field_meta[field].read_only === "boolean" &&
			(value === null || ["string", "number", "boolean"].includes(typeof value) && (typeof value !== "number" || Number.isFinite(value))));
	}
	validPage(result, args) {
		if (!result || result.readonly !== true || !["kind", "company", "search", "status", "page_size"].every((field) => result[field] === args[field]) ||
			!Number.isSafeInteger(result.total) || result.total < 0 || result.total_pages !== Math.ceil(result.total / args.page_size) ||
			!Number.isSafeInteger(result.page) || result.page !== Math.min(args.page, Math.max(1, result.total_pages)) || !Array.isArray(result.rows)) return false;
		const length = Math.min(args.page_size, Math.max(0, result.total - (result.page - 1) * args.page_size));
		return result.rows.length === length && new Set(result.rows.map((row) => row?.name)).size === length && result.rows.every((row) =>
			row?.name && this.validArchive(row, args.company) && row.can_write === false && Object.values(row.field_meta).every((field) => field.read_only === true) &&
			(args.status === "全部" || Boolean(row.profile.inactive) === (args.status === "停用")));
	}
	renderRows() {
		this.tbody.replaceChildren();
		for (const row of this.rows) {
			const tr = this.element("tr");
			for (const [field] of this.listFields) tr.append(this.element("td", field in this.dictionaryTypes ? row.dictionary_values[field] ?? "" : row.profile[field] ?? ""));
			const cell = this.element("td"), inactive = this.element("input"); inactive.type = "checkbox"; inactive.checked = !!Number(row.profile.inactive); inactive.disabled = true;
			inactive.setAttribute("aria-label", __("停用")); cell.append(inactive); tr.append(cell);
			for (const value of [row.owner, row.creation, row.modified_by, row.profile_version]) tr.append(this.element("td", value ?? ""));
			const modify = this.element("td", undefined, "jfe-customer-action jfe-customer-modify"), deletion = this.element("td", undefined, "jfe-customer-action jfe-customer-delete");
			modify.append(this.button(this.readyArchive?.can_write ? "修改" : "查看", () => this.openExisting(row.name)));
			deletion.append(this.button("删除", null, "删除暂不可用，可在档案中正常停用")); tr.append(modify, deletion);
			tr.addEventListener("dblclick", () => this.openExisting(row.name)); this.tbody.append(tr);
		}
	}
	async openNew() { if (this.newButton.disabled) return; return this.open(null); }
	async openExisting(name) { this.syncCompany(); if (!this.rows.some((row) => row.name === name)) return; return this.open(name); }
	async open(name) {
		if (this.disposed || this.opening || this.editor) return;
		this.syncCompany(); if (!this.companyScope) return;
		const company = this.companyScope, sequence = ++this.openRevision; this.opening = true; this.freezeToolbar();
		try {
			const response = await this.call(name ? "read_archive" : "init_archive", { kind: this.kind, company, ...(name ? { name } : {}) });
			if (this.disposed || sequence !== this.openRevision || company !== this.companyScope || !this.settingCompany && company !== (this.companyControl.get_value() || null)) return;
			if (!this.validArchive(response.message, company, name)) throw new Error("Wrong customer archive scope");
			await this.buildEditor(response.message);
		} catch {
			if (this.editor && !this.editor.closed) this.closeEditor();
			if (!this.disposed && sequence === this.openRevision) this.status.textContent = __("客户档案未能打开，请核对权限后重试。");
		} finally { if (sequence === this.openRevision) this.opening = false; this.freezeToolbar(); }
	}
	requestID() { return crypto.randomUUID(); }
	editorCurrent(state) {
		return !this.disposed && !state.closed && this.editor === state && state.company === this.companyScope &&
			(this.settingCompany || state.company === (this.companyControl.get_value() || null));
	}
	async buildEditor(archive) {
		const dialog = new frappe.ui.Dialog({ title: __(this.sales ? "客户档案" : "订单客户"), size: "large", no_submit_on_enter: true,
			fields: [{ fieldname: "customer_editor", fieldtype: "HTML" }] });
		dialog.$wrapper.addClass("jfe-customer-dialog");
		const state = { dialog, archive, company: archive.company, controls: {}, customerControls: {}, dictionaryRevisions: {}, dictionaryVersions: {}, dictionaryNames: {},
			busy: false, unknown: false, closed: false, setting: false, customerRevision: 0, customerVersion: archive.customer_version,
			requestId: archive.name ? null : this.requestID(), candidate: null, conflict: null };
		this.editor = state; this.freezeToolbar();
		const body = dialog.fields_dict.customer_editor.$wrapper.get(0), form = this.element("div", undefined, "jfe-customer-editor-fields");
		state.message = this.element("p");
		for (const [field, label] of this.fields) {
			const metadata = archive.field_meta[field], dictionary = field in this.dictionaryTypes;
			state.controls[field] = this.control(form, field, label, dictionary ? "Link" : metadata?.fieldtype === "Check" ? "Check" : "Data",
				dictionary ? "Jifeng Customer Type" : "", () => dictionary ? this.changeDictionary(state, field) : this.freeze(state));
			if (metadata?.length) state.controls[field].df.length = metadata.length;
			if (metadata?.fieldtype === "Small Text") state.controls[field].df.length = 65535;
			if (dictionary) state.controls[field].get_query = () => ({ filters: { company: state.company, dictionary_type: this.dictionaryTypes[field], inactive: 0 } });
		}
		state.dictionaryStatus = this.element("p");
		const native = this.element("details", undefined, "jfe-customer-native"), nativeFields = this.element("div", undefined, "jfe-customer-native-fields");
		native.append(this.element("summary", __("业务客户")));
		const modes = [archive.can_create_customer ? "新建业务客户" : null, archive.can_link_customer || archive.name ? "关联已有客户" : null].filter(Boolean).join("\n");
		state.modeControl = this.control(nativeFields, "customer_mode", "业务来源", "Select", modes, () => this.changeMode(state));
		state.sourceMode = archive.name || !archive.can_create_customer ? "existing" : "create";
		const labels = { customer_group: "客户分组", territory: "销售区域", customer_type: "客户主体", naming_series: "编号规则" };
		for (const field of Object.keys(labels)) {
			const metadata = archive.customer_field_meta[field];
			if (metadata) state.customerControls[field] = this.control(nativeFields, field, labels[field], metadata.fieldtype, metadata.options, () => this.freeze(state));
			if (["customer_group", "territory"].includes(field) && state.customerControls[field]) state.customerControls[field].get_query = () => ({ filters: { is_group: 0 } });
		}
		state.customerControl = this.control(nativeFields, "customer", "关联客户", "Link", "Customer", () => this.changeCustomer(state));
		state.customerStatus = this.element("p"); native.append(nativeFields, state.customerStatus);
		state.nativeSection = native;
		state.review = this.element("div", undefined, "jfe-customer-review"); const footer = this.element("div", undefined, "jfe-customer-footer");
		state.newButton = this.button("新增", () => this.discardFor(async () => { this.closeEditor(); await this.openNew(); }, state));
		state.saveButton = this.button("保存", () => this.save(state)); state.closeButton = this.button("关闭", () => dialog.hide());
		state.resolveButton = this.button("核对保存结果", () => this.resolve(state)); state.adoptButton = this.button("采用已核对当前值", () => this.adopt(state));
		footer.append(state.newButton, state.saveButton, state.closeButton, state.resolveButton, state.adoptButton);
		body.append(this.element("p", __("当前公司") + "：" + state.company), state.message, form, state.dictionaryStatus, native,
			this.element("p", __("编号自动预填，以保存结果为准。")), state.review, footer);
		this.guardClose(state); await this.applyArchive(state, archive);
		if (!this.editorCurrent(state)) { this.closeEditor(); return; }
		dialog.show();
	}
	guardClose(state) {
		state.nativeHide = state.dialog.hide.bind(state.dialog);
		state.dialog.hide = () => this.discardFor(() => this.closeEditor(), state);
		state.dialog.$wrapper.modal?.({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn?.().removeAttr("data-dismiss").on("click.customer-archive", () => state.dialog.hide());
		state.dialog.$wrapper.on?.("keydown.customer-archive", (event) => {
			if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); }
		});
		state.dialog.on_hide = () => { state.closed = true; this.invalidateEditor(state); if (this.editor === state) this.editor = null; this.freezeToolbar(); };
	}
	invalidateEditor(state) { state.customerRevision++; for (const field of Object.keys(this.dictionaryTypes)) state.dictionaryRevisions[field] = (state.dictionaryRevisions[field] || 0) + 1; }
	async applyArchive(state, archive) {
		state.archive = archive; state.customerVersion = archive.customer_version; this.invalidateEditor(state); state.setting = true;
		state.sourceMode = archive.name || !archive.can_create_customer ? "existing" : "create";
		state.dictionaryVersions = { ...archive.dictionary_versions }; state.dictionaryValues = { ...archive.dictionary_values }; state.dictionaryNames = {};
		try {
			for (const [field, control] of Object.entries(state.controls)) await control.set_value(archive.profile[field] ?? "");
			await state.customerControl.set_value(archive.profile.customer ?? "");
			await state.modeControl.set_value(state.sourceMode === "create" ? "新建业务客户" : "关联已有客户");
			for (const [field, control] of Object.entries(state.customerControls)) await control.set_value(archive.customer_defaults[field] ?? "");
		} finally { state.setting = false; }
		state.displayed = Object.fromEntries(Object.entries(state.controls).map(([field, control]) => [field, control.get_value()]));
		for (const field of Object.keys(this.dictionaryTypes)) state.dictionaryNames[field] = state.controls[field].get_value() || "";
		state.original = JSON.stringify(this.draft(state)); state.review.replaceChildren(); state.conflict = null;
		state.dictionaryStatus.textContent = "";
		state.customerStatus.textContent = __(archive.name ? "已关联业务客户；本次仅保存档案资料。" : state.sourceMode === "create" ?
			"保存时新建业务客户，名称取上方名称。请核对客户分组、区域和主体。" : "请选择已有客户，不按名称自动关联。");
		state.nativeSection.open = !archive.name && (state.sourceMode === "existing" || Object.entries(state.customerControls).some(([field, control]) =>
			archive.customer_field_meta[field]?.reqd && !control.get_value()));
		this.freeze(state);
	}
	values(state) {
		const values = {};
		for (const [field, control] of Object.entries(state.controls)) {
			const metadata = state.archive.field_meta[field];
			if (field !== "legacy_number" && metadata && !metadata.read_only) values[field] = metadata.fieldtype === "Check" ? Number(control.get_value() || 0) : control.get_value() ?? "";
		}
		if (!state.archive.name && state.sourceMode === "existing" && state.archive.field_meta.customer && !state.archive.field_meta.customer.read_only)
			values.customer = state.customerControl.get_value() || "";
		return values;
	}
	customerValues(state) {
		return Object.fromEntries(Object.entries(state.customerControls).filter(([field]) => !state.archive.customer_field_meta[field]?.read_only)
			.map(([field, control]) => [field, control.get_value() || null]));
	}
	draft(state) { return { profile: this.values(state), source_mode: state.sourceMode, customer_create: state.sourceMode === "create" ? this.customerValues(state) : null }; }
	async changeMode(state) {
		if (!this.editorCurrent(state) || state.setting || state.archive.name || state.busy || state.unknown || state.confirming) return;
		const requested = state.modeControl.get_value() === "新建业务客户" ? "create" : "existing";
		if (requested === "create" && !state.archive.can_create_customer || requested === "existing" && !state.archive.can_link_customer) return;
		state.sourceMode = requested; state.customerRevision++; state.customerVersion = null;
		state.customerStatus.textContent = __(requested === "create" ? "保存时新建业务客户；已有客户请明确关联。" : "请明确选择已有客户。");
		this.freeze(state);
		if (requested === "existing" && state.customerControl.get_value()) await this.changeCustomer(state);
	}
	async changeCustomer(state) {
		if (!this.editorCurrent(state) || state.setting || state.archive.name || state.busy || state.unknown || state.confirming || state.sourceMode !== "existing") return;
		const name = state.customerControl.get_value() || "", sequence = ++state.customerRevision; state.customerVersion = null;
		state.customerStatus.textContent = __(name ? "正在核对关联客户…" : "请明确选择关联客户。"); this.freeze(state);
		if (!name) return;
		try {
			const response = await this.call("read_customer", { name, kind: this.kind, company: state.company }), result = response.message;
			if (!this.editorCurrent(state) || sequence !== state.customerRevision || name !== state.customerControl.get_value() || state.sourceMode !== "existing") return;
			if (!result || result.name !== name || result.kind !== this.kind || result.company !== state.company || typeof result.version !== "string" || !result.version || typeof result.disabled !== "boolean") throw new Error("Incomplete customer source");
			if (result.disabled) { state.customerStatus.textContent = __("停用客户不能关联新档案。"); return; }
			state.customerVersion = result.version; state.customerStatus.textContent = __("关联客户已核对。");
		} catch { if (this.editorCurrent(state) && sequence === state.customerRevision) state.customerStatus.textContent = __("关联客户未能核对，请重新选择。"); }
		finally { if (this.editorCurrent(state) && sequence === state.customerRevision) this.freeze(state); }
	}
	async changeDictionary(state, field) {
		if (!this.editorCurrent(state) || state.setting || state.busy || state.unknown || state.confirming || !(field in this.dictionaryTypes) ||
			!state.archive.can_write || !state.archive.field_meta[field] || state.archive.field_meta[field].read_only) return;
		const name = state.controls[field].get_value() || "", sequence = state.dictionaryRevisions[field] = (state.dictionaryRevisions[field] || 0) + 1;
		delete state.dictionaryVersions[field]; delete state.dictionaryValues[field]; state.dictionaryNames[field] = name;
		state.dictionaryStatus.textContent = name ? __("正在核对") + this.labels[field] + "…" : ""; this.freeze(state);
		if (!name) return;
		try {
			const response = await this.call("read_dictionary", { name, field, kind: this.kind, company: state.company }), result = response.message;
			if (!this.editorCurrent(state) || sequence !== state.dictionaryRevisions[field] || name !== state.controls[field].get_value()) return;
			if (!result || result.name !== name || result.company !== state.company || result.dictionary_type !== this.dictionaryTypes[field] ||
				typeof result.version !== "string" || !result.version || typeof result.value !== "string" || ![0, 1, false, true].includes(result.inactive)) throw new Error("Wrong customer dictionary scope");
			if (Number(result.inactive)) { state.dictionaryStatus.textContent = __("停用词条不能用于新的选择。"); return; }
			state.dictionaryVersions[field] = result.version; state.dictionaryValues[field] = result.value; state.dictionaryStatus.textContent = __("词条已核对。");
		} catch { if (this.editorCurrent(state) && sequence === state.dictionaryRevisions[field]) state.dictionaryStatus.textContent = __("词条未能核对，请重新选择。"); }
		finally { if (this.editorCurrent(state) && sequence === state.dictionaryRevisions[field]) this.freeze(state); }
	}
	dictionariesReady(state) {
		return Object.keys(this.dictionaryTypes).every((field) => !state.controls[field].get_value() ||
			state.dictionaryNames[field] === state.controls[field].get_value() && typeof state.dictionaryVersions[field] === "string" && !!state.dictionaryVersions[field]);
	}
	freeze(state) {
		this.freezeToolbar(); if (!state || state.closed) return;
		const locked = !!(state.busy || state.unknown || state.confirming);
		for (const [field, control] of Object.entries(state.controls)) {
			const metadata = state.archive.field_meta[field];
			this.flags(control, { hidden: !metadata, read_only: !!(locked || !state.archive.can_write || !metadata || metadata.read_only || field === "legacy_number"), reqd: !!metadata?.reqd });
		}
		this.flags(state.modeControl, { hidden: !!state.archive.name, read_only: !!(locked || state.archive.name || !state.archive.can_write), reqd: !state.archive.name });
		for (const [field, control] of Object.entries(state.customerControls)) {
			const metadata = state.archive.customer_field_meta[field];
			this.flags(control, { hidden: !!state.archive.name || state.sourceMode !== "create", read_only: !!(locked || state.archive.name || !state.archive.can_create_customer || metadata?.read_only), reqd: !!metadata?.reqd && !state.archive.name && state.sourceMode === "create" });
		}
		const customer = state.archive.field_meta.customer;
		this.flags(state.customerControl, { hidden: !state.archive.name && state.sourceMode === "create", read_only: !!(locked || state.archive.name || !state.archive.can_write || !customer || customer.read_only || !state.archive.can_link_customer), reqd: state.sourceMode === "existing" });
		state.saveButton.disabled = !!(locked || !state.archive.can_write || !this.dictionariesReady(state) ||
			(state.archive.name || state.sourceMode === "existing") && !state.customerVersion || !state.archive.name &&
			(state.sourceMode === "create" ? !state.archive.can_create_customer : !state.archive.can_link_customer));
		state.newButton.disabled = !!(locked || !this.readyArchive?.can_write || !this.readyArchive.can_create_customer && !this.readyArchive.can_link_customer);
		state.closeButton.disabled = locked; state.resolveButton.hidden = !state.unknown; state.resolveButton.disabled = !!(state.busy || state.confirming);
		state.adoptButton.hidden = !state.unknown || !state.conflict; state.adoptButton.disabled = !!(state.busy || state.confirming);
	}
	isDirty(state = this.editor) { return !!(state && !state.closed && state.original !== JSON.stringify(this.draft(state))); }
	getCloseBlock() {
		if (this.opening || this.editor?.busy || this.editor?.confirming) return "正在读取、保存或核对客户档案，请稍候。";
		return this.editor?.unknown ? "保存结果尚未确认，请先核对保存结果。" : "";
	}
	async discardFor(action, state = this.editor) {
		if (this.disposed || state?.closed) return false;
		const block = this.getCloseBlock(); if (block) { frappe.msgprint(__(block)); return false; }
		if (!this.isDirty(state)) { await action(); return true; }
		state.confirming = true; this.freeze(state);
		const yes = await new Promise((resolve) => frappe.confirm(__("尚有未保存的修改，放弃修改并继续？"), () => resolve(true), () => resolve(false)));
		state.confirming = false; this.freeze(state);
		if (yes && this.editorCurrent(state)) { await action(); return true; } return false;
	}
	closeEditor() {
		const state = this.editor; if (!state || state.closed) return;
		state.closed = true; this.invalidateEditor(state); state.nativeHide(); this.editor = null; this.freezeToolbar();
	}
	payload(state) {
		const values = this.values(state), result = { kind: this.kind, company: state.company, profile: {}, dictionary_versions: {} };
		for (const field of Object.keys(this.dictionaryTypes)) if (state.controls[field].get_value()) result.dictionary_versions[field] = state.dictionaryVersions[field];
		if (state.archive.name || state.sourceMode === "existing") result.customer_version = state.customerVersion;
		if (state.archive.name) {
			result.name = state.archive.name; result.profile_version = state.archive.profile_version;
			for (const [field, value] of Object.entries(values)) {
				const original = state.displayed[field];
				if (state.archive.field_meta[field]?.fieldtype === "Check" ? Number(value) !== Number(original) : (value ?? "") !== (original ?? "")) result.profile[field] = value;
			}
		} else { result.request_id = state.requestId; result.profile = values; if (state.sourceMode === "create") result.customer_create = this.customerValues(state); }
		return result;
	}
	requiredMissing(state) {
		const empty = (value) => value === null || value === undefined || typeof value === "string" && !value.trim();
		for (const [field, control] of Object.entries(state.controls)) if (state.archive.field_meta[field]?.reqd && !control.df.hidden && empty(control.get_value())) return this.labels[field];
		if (!state.archive.name && state.sourceMode === "create") for (const [field, control] of Object.entries(state.customerControls))
			if (state.archive.customer_field_meta[field]?.reqd && empty(control.get_value())) return control.df.label;
		return null;
	}
	complete(state, row) {
		return !!(state.candidate && row?.name && this.validArchive(row, state.company, state.candidate.name) &&
			Object.keys(state.candidate.profile).every((field) => field in row.profile && row.field_meta[field]));
	}
	matches(state, row) {
		if (!this.complete(state, row) || state.candidate.name && row.profile_version === state.candidate.profile_version ||
			state.candidate.customer_version && row.customer_version !== state.candidate.customer_version) return false;
		if (Object.keys(state.candidate.dictionary_versions).length !== Object.keys(row.dictionary_versions).length ||
			Object.entries(state.candidate.dictionary_versions).some(([field, version]) => row.dictionary_versions[field] !== version)) return false;
		return Object.entries(state.candidate.profile).every(([field, value]) => row.field_meta[field].fieldtype === "Check" ?
			Number(row.profile[field]) === Number(value) : (row.profile[field] ?? "") === (value ?? ""));
	}
	definiteFailure(error) {
		return ["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError",
			"UniqueValidationError", "DuplicateEntryError"].includes(error?.responseJSON?.exc_type);
	}
	async save(state) {
		if (!this.editorCurrent(state) || this.getCloseBlock() || state.saveButton.disabled || state.archive.name && !this.isDirty(state)) return;
		const missing = this.requiredMissing(state);
		if (missing) { state.message.textContent = __("请填写") + missing + "。"; state.nativeSection.open = true; return; }
		state.candidate = structuredClone(this.payload(state)); state.candidateDictionaryValues = { ...state.dictionaryValues };
		state.conflict = null; state.review.replaceChildren(); state.busy = true; this.freeze(state);
		let answered = false;
		try {
			const response = await this.call("save_archive", { payload: state.candidate }, true); answered = true; let row = response.message;
			if (!this.editorCurrent(state)) return;
			if (!state.candidate.name && this.validArchive(row, state.company) && row.name) {
				const receipt = await this.call("resolve_creation", { request_id: state.candidate.request_id, kind: this.kind, company: state.company });
				if (!this.editorCurrent(state)) return;
				if (receipt.message?.status !== "found" || receipt.message.archive?.name !== row.name || receipt.message.archive?.profile_version !== row.profile_version ||
					receipt.message.archive?.customer !== row.customer || receipt.message.archive?.customer_version !== row.customer_version) throw new Error("Unconfirmed customer creation receipt");
				row = receipt.message.archive;
			}
			if (this.matches(state, row)) await this.accept(state, row, "客户档案已保存。");
			else { state.unknown = true; state.message.textContent = __("保存结果尚未确认，请核对保存结果。"); this.showConflict(state, row); }
		} catch (error) {
			if (!this.editorCurrent(state)) return;
			state.unknown = answered || !this.definiteFailure(error);
			state.message.textContent = __(state.unknown ? "保存结果尚未确认，请核对保存结果。" : "保存未完成，草稿已保留，请核对字段或重新打开当前版本。");
		} finally { state.busy = false; if (!state.closed) this.freeze(state); }
	}
	async accept(state, row, message) {
		await this.applyArchive(state, row); if (!this.editorCurrent(state)) return;
		state.unknown = false; state.candidate = state.conflict = state.requestId = null;
		state.message.textContent = __(message); this.revision++; this.clearResults();
		this.status.textContent = __("档案已保存，关闭弹层后查询最新列表。");
	}
	showConflict(state, row) {
		if (!this.complete(state, row)) return;
		state.conflict = row; state.review.replaceChildren(this.element("p", __("服务器当前值与保留草稿不同。采用当前值只读取，不会再次保存。")));
		const table = this.element("table"), heading = this.element("tr");
		for (const label of ["字段", "保留草稿", "服务器当前值"]) heading.append(this.element("th", __(label))); table.append(heading);
		for (const [field, value] of Object.entries(state.candidate.profile)) {
			const dictionary = field in this.dictionaryTypes, tr = this.element("tr");
			tr.append(this.element("td", __(this.labels[field] || "关联客户")), this.element("td", dictionary ? state.candidateDictionaryValues[field] ?? "" : value),
				this.element("td", dictionary ? row.dictionary_values[field] ?? "" : row.profile[field])); table.append(tr);
		}
		state.review.append(table);
	}
	async resolve(state) {
		if (!this.editorCurrent(state) || state.busy || state.confirming || !state.unknown || !state.candidate) return;
		state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try {
			const args = { kind: this.kind, company: state.company, ...(state.candidate.name ? { name: state.candidate.name } : { request_id: state.candidate.request_id }) };
			const response = await this.call(state.candidate.name ? "read_archive" : "resolve_creation", args);
			if (!this.editorCurrent(state)) return;
			const row = state.candidate.name ? response.message : response.message?.status === "found" ? response.message.archive : null;
			if (this.matches(state, row)) await this.accept(state, row, "已核对保存结果。");
			else if (this.complete(state, row)) this.showConflict(state, row);
			else state.message.textContent = __("未核对到完整可见结果，请保留草稿稍后核对；不会重复保存。");
		} catch { if (this.editorCurrent(state)) state.message.textContent = __("核对失败，原草稿已保留，请稍后核对。"); }
		finally { state.busy = false; if (!state.closed) this.freeze(state); }
	}
	adopt(state) {
		if (!this.editorCurrent(state) || state.busy || state.confirming || !state.unknown || !state.conflict) return;
		const row = state.conflict; state.confirming = true; this.freeze(state);
		frappe.confirm(__("放弃保留草稿并采用刚核对的当前值？此操作不会再次保存。"), async () => {
			state.confirming = false; if (!this.editorCurrent(state) || state.conflict !== row) return;
			state.busy = true; this.freeze(state);
			try { await this.accept(state, row, "已采用服务器当前值；未重复保存。"); } finally { state.busy = false; if (!state.closed) this.freeze(state); }
		}, () => { state.confirming = false; if (!state.closed) this.freeze(state); });
	}
	destroy() {
		if (this.disposed) return;
		this.disposed = true; this.revision++; this.openRevision++; this.permissionRevision = (this.permissionRevision || 0) + 1;
		window.removeEventListener?.("beforeunload", this.beforeUnload); this.closeEditor(); this.clearResults(); this.host.replaceChildren();
	}
	dispose() { this.destroy(); }
};
