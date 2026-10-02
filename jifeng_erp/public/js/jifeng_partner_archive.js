/* Combined native Supplier creation or explicit links; old factory text stays separate. */
window.JifengPartnerArchive = class JifengPartnerArchive {
	constructor(host, kind = "外发工厂") {
		if (!["外发工厂", "电镀厂"].includes(kind)) throw new Error("不支持的工厂档案种类");
		this.host = host; this.kind = kind; this.plating = kind === "电镀厂"; this.companyScope = null; this.page = 1; this.pageSize = 100;
		this.total = null; this.totalPages = null; this.rows = []; this.revision = 0; this.openRevision = 0;
		this.disposed = false; this.opening = false; this.editor = null; this.statusFilter = "在用";
		this.exporting = false; this.exportController = null;
		this.fields = [["legacy_number", "编号"], ["display_name", "名称"], ["owner_name", "负责"], ["mobile", "手机"],
			["reception", "接货"], ["phone", "电话"], ["address", "地址"], ["notes", "备注"],
			["bank_name", this.plating ? "户行" : "开户"], ["account_name", "户名"], ["bank_account", "账号"], ["inactive", "停用"]];
		// Runtime WF_GC has 11 bound visible fields and right edit/delete icons.
		// Keep all editor/source fields, including inactive and protected audit data.
		this.listFields = this.plating ? this.fields : this.fields.filter(([field]) => field !== "inactive");
		this.labels = Object.fromEntries(this.fields);
		this.beforeUnload = (event) => { if (this.isDirty() || this.getCloseBlock()) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener?.("beforeunload", this.beforeUnload);
		this.ready = this.render();
	}
	element(tag, text, className) { const value = document.createElement(tag); if (text !== undefined) value.textContent = String(text ?? ""); if (className) value.className = className; return value; }
	button(label, action) { const value = this.element("button", __(label), "jfe-button"); value.type = "button"; value.addEventListener("click", action); return value; }
	call(method, args, write = false) { return frappe.call({ method: "jifeng_erp.partner_archive." + method, args, type: write ? "POST" : "GET", freeze: false }); }
	control(host, fieldname, label, fieldtype, options, onchange) { const cell = this.element("div"); host.append(cell);
		const control = frappe.ui.form.make_control({ parent: cell, render_input: true, df: { fieldname, label: __(label), fieldtype, options, onchange } });
		if (fieldtype === "Small Text") control.$input?.css?.("height", "36px");
		return control; }
	flags(control, flags) { let changed = false; for (const [field, value] of Object.entries(flags)) if (control.df[field] !== value) { control.df[field] = value; changed = true; } if (changed) control.refresh?.(); }
	async render() {
		this.host.classList.add("jfe-partner-archive"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-partner-toolbar"), company = this.element("div", undefined, "jfe-partner-company");
		this.companyControl = this.control(company, "company", "公司", "Link", "Company", () => this.changeCompany());
		this.newButton = this.button("新增", () => this.openNew());
		this.exportButton = this.button("导出", () => this.exportArchives());
		this.exportButton.title = __("导出当前筛选的全部可见档案，最多1000条；需导出权限。");
		this.search = this.element("input"); this.search.type = "search"; this.search.placeholder = __("输入查询的关键字"); this.search.maxLength = 140;
		this.search.addEventListener("input", () => { this.revision++; this.clearResults(); });
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(1); } });
		const statusField = this.element("label", undefined, "jfe-partner-state-filter"); statusField.append(this.element("span", __("状态")));
		this.statusSelect = this.element("select"); this.statusSelect.setAttribute("aria-label", __("状态"));
		for (const value of ["在用", "停用", "全部"]) { const option = this.element("option", __(value)); option.value = value; this.statusSelect.append(option); }
		this.statusSelect.value = this.statusFilter; this.statusSelect.addEventListener("change", () => this.changeStatus()); statusField.append(this.statusSelect);
		this.queryButton = this.button("查询", () => this.query(1)); toolbar.append(company, this.newButton, this.exportButton, this.search, statusField, this.queryButton);
		this.status = this.element("p", __("请选择公司后查询。"), "jfe-partner-status");
		this.scroller = this.element("div", undefined, "jfe-partner-scroll"); const table = this.element("table", undefined, "jfe-partner-table"), thead = this.element("thead"), heading = this.element("tr");
		for (const label of [...(this.plating ? [""] : []), ...this.listFields.map((field) => field[1]), ...(this.plating ? ["操作", "日期", "修改人", "修改日期"] : [])]) heading.append(this.element("th", __(label)));
		heading.append(this.element("th", __("修改"), "jfe-partner-action jfe-partner-modify"), this.element("th", __("删除"), "jfe-partner-action jfe-partner-delete"));
		thead.append(heading); this.tbody = this.element("tbody"); table.append(thead, this.tbody); this.scroller.append(table);
		const pager = this.element("div", undefined, "jfe-partner-pager"); this.previous = this.button("上页", () => this.query(this.page - 1));
		this.next = this.button("下页", () => this.query(this.page + 1)); this.pageLabel = this.element("span", "—");
		this.goto = this.element("input"); this.goto.type = "text"; this.goto.inputMode = "numeric"; this.goto.value = "1"; this.goto.setAttribute("aria-label", __("页码"));
		this.goto.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(this.goto.value); } });
		this.goButton = this.button("前往", () => this.query(this.goto.value)); this.count = this.element("span", __("尚未查询"));
		pager.append(this.previous, this.pageLabel, this.next, this.goto, this.goButton, this.count);
		this.scopeNotice = this.element("p", __("部分可用：可新增、查询、修改、停用和导出；删除、导入、更新简拼及打印尚未开放。"), "jfe-partner-status");
		this.host.append(toolbar, this.scopeNotice, this.status, this.scroller, pager); this.renderPager(); this.freezeStatus();
	}
	changeStatus() {
		const desired = this.statusSelect.value;
		if (this.disposed || this.editor || this.opening || !["在用", "停用", "全部"].includes(desired)) { this.statusSelect.value = this.statusFilter; return; }
		if (desired === this.statusFilter) return;
		this.statusFilter = desired; this.revision++; this.openRevision++; this.page = 1; this.goto.value = "1";
		this.clearResults(); this.status.textContent = __("状态已切换，请查询。");
	}
	freezeStatus() {
		if (this.statusSelect) this.statusSelect.disabled = !!(this.disposed || this.opening || this.editor);
		if (this.exportButton) this.exportButton.disabled = !!(!this.companyScope || this.disposed || this.opening || this.editor || this.exporting);
	}
	async changeCompany() {
		if (this.settingCompany || this.disposed) return;
		const desired = this.companyControl.get_value() || null;
		if (desired === this.companyScope) return;
		if (this.editor || this.opening) { this.settingCompany = true; try { await this.companyControl.set_value(this.companyScope); } finally { this.settingCompany = false; } return; }
		this.companyScope = desired; this.revision++; this.openRevision++; this.clearResults(); this.status.textContent = __("公司已切换，请查询。");
	}
	cancelExport() {
		if (this.exporting) this.status.textContent = __("导出已取消，请重新导出。");
		this.exportController?.abort(); this.exportController = null; this.exporting = false; this.freezeStatus();
	}
	clearResults() { this.cancelExport(); this.rows = []; this.total = this.totalPages = null; this.tbody?.replaceChildren(); this.renderPager(); }
	renderPager() {
		if (!this.previous) return;
		this.previous.disabled = this.total === null || this.page <= 1;
		this.next.disabled = this.total === null || this.page >= this.totalPages;
		this.pageLabel.textContent = this.total === null ? "—" : String(this.page);
		this.count.textContent = this.total === null ? __("尚未查询") : __("共") + " " + this.total + " " + __("条");
		this.freezeStatus();
	}
	pageNumber(value) { if (typeof value === "string" && /^[1-9][0-9]{0,3}$/.test(value)) value = Number(value); return Number.isInteger(value) && value >= 1 && value <= 1000 ? value : null; }
	async query(target = 1) {
		if (this.disposed || this.editor || this.opening) return;
		const page = this.pageNumber(target);
		if (!this.companyScope || !page) { this.revision++; this.clearResults(); this.status.textContent = __(!this.companyScope ? "请选择公司。" : "请输入有效页码。"); return; }
		const args = { kind: this.kind, company: this.companyScope, search: this.search.value, status: this.statusFilter, page, page_size: this.pageSize };
		const sequence = ++this.revision, scroll = { top: this.scroller.scrollTop, left: this.scroller.scrollLeft };
		this.clearResults(); this.status.textContent = __("正在查询…");
		try { const response = await this.call("query_archives", args);
			if (!this.current(sequence, args)) return;
			if (!this.validPage(response.message, args)) throw new Error("Incomplete archive page");
			Object.assign(this, { rows: response.message.rows, page, total: response.message.total, totalPages: response.message.total_pages });
			this.goto.value = String(page); this.renderRows(); this.renderPager();
			this.scroller.scrollTop = scroll.top; this.scroller.scrollLeft = scroll.left;
			this.status.textContent = this.total ? __("查询完成。") : __("没有可见记录。");
		} catch { if (this.current(sequence, args)) { this.clearResults(); this.status.textContent = __("查询未完成，请核对条件和权限后重试。"); } }
	}
	current(sequence, args) { return !this.disposed && sequence === this.revision && args.company === this.companyScope && args.search === this.search.value && args.status === this.statusFilter; }
	async exportArchives() {
		if (this.disposed || this.editor || this.opening || this.exporting || !this.companyScope) return;
		const args = { kind: this.kind, company: this.companyScope, search: this.search.value, status: this.statusFilter };
		const sequence = this.revision, controller = new AbortController();
		this.exporting = true; this.exportController = controller; this.freezeStatus();
		this.status.textContent = __("正在导出当前筛选结果…");
		let url;
		try {
			const response = await fetch("/api/method/jifeng_erp.partner_archive_export.export_archives?" + new URLSearchParams(args),
				{ method: "GET", credentials: "same-origin", headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }, signal: controller.signal });
			if (controller.signal.aborted || this.exportController !== controller || !this.current(sequence, args)) return;
			if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") throw new Error("Archive export failed");
			const file = await response.blob();
			if (controller.signal.aborted || this.exportController !== controller || !this.current(sequence, args) || this.editor || this.opening) return;
			if (!file.size) throw new Error("Empty archive export");
			url = URL.createObjectURL(file); const link = this.element("a");
			link.href = url; link.download = this.kind + "档案.xlsx"; document.body.append(link); link.click(); link.remove();
			this.status.textContent = __("已生成当前筛选结果的导出文件。");
		} catch { if (this.exportController === controller && this.current(sequence, args) && !controller.signal.aborted) this.status.textContent = __("导出未完成，请核对筛选和导出权限后重试。"); }
		finally {
			if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
			if (this.exportController === controller) { this.exportController = null; this.exporting = false; this.freezeStatus(); }
		}
	}
	validArchive(row, company, name = undefined) {
		if (!row || row.kind !== this.kind || row.company !== company || typeof row.can_write !== "boolean" || !row.profile || !row.field_meta ||
			row.profile.company !== company || row.profile.archive_kind !== this.kind || ![0, 1, false, true].includes(row.profile.inactive) ||
			!["company", "archive_kind", "supplier", "legacy_number", "display_name", "inactive"].every((field) => row.field_meta[field]) ||
			(name !== undefined && row.name !== name)) return false;
		if (this.plating && row.owner !== null && typeof row.owner !== "string") return false;
		if (row.name !== null && (typeof row.name !== "string" || !row.name || typeof row.profile_version !== "string" || !row.profile_version ||
			typeof row.supplier_version !== "string" || !row.supplier_version || typeof row.supplier !== "string" || !row.supplier ||
			row.profile.supplier !== row.supplier || typeof row.supplier_disabled !== "boolean")) return false;
		if (row.name === null && (row.profile_version !== null || row.supplier !== null || row.supplier_version !== null)) return false;
		if (row.name === null && (typeof row.can_create_supplier !== "boolean" || typeof row.can_link_supplier !== "boolean" || !row.supplier_defaults || !row.supplier_field_meta ||
			Object.entries(row.supplier_defaults).some(([field, value]) => !["supplier_group", "supplier_type", "naming_series"].includes(field) ||
				!row.supplier_field_meta[field] || !["Link", "Select", "Data"].includes(row.supplier_field_meta[field].fieldtype) ||
				typeof row.supplier_field_meta[field].read_only !== "boolean" || value !== null && typeof value !== "string"))) return false;
		if (![row.creation, row.modified_by].every((value) => value === null || typeof value === "string")) return false;
		return Object.entries(row.profile).every(([field, value]) => row.field_meta[field] && typeof row.field_meta[field].read_only === "boolean" &&
			(value === null || ["string", "number", "boolean"].includes(typeof value) && (typeof value !== "number" || Number.isFinite(value))));
	}
	validPage(result, args) {
		if (!result || result.readonly !== true || !["kind", "company", "search", "status", "page", "page_size"].every((field) => result[field] === args[field]) ||
			!Number.isInteger(result.total) || result.total < 0 || result.total > 1000 || result.total_pages !== Math.ceil(result.total / args.page_size) ||
			args.page > Math.max(1, result.total_pages) || !Array.isArray(result.rows)) return false;
		const length = Math.min(args.page_size, Math.max(0, result.total - (args.page - 1) * args.page_size));
		return result.rows.length === length && new Set(result.rows.map((row) => row?.name)).size === length && result.rows.every((row) =>
			row?.name && this.validArchive(row, args.company) && row.can_write === false && Object.values(row.field_meta).every((field) => field.read_only === true) &&
			(args.status === "全部" || Boolean(row.profile.inactive) === (args.status === "停用")));
	}
	renderRows() {
		this.tbody.replaceChildren();
		for (const row of this.rows) { const tr = this.element("tr");
			if (this.plating) tr.append(this.element("td", ""));
			for (const [field] of this.listFields) tr.append(this.element("td", row.profile[field] ?? ""));
			if (this.plating) tr.append(this.element("td", row.owner ?? ""), this.element("td", row.creation ?? ""), this.element("td", row.modified_by ?? ""), this.element("td", row.profile_version));
			const modify = this.element("td", undefined, "jfe-partner-action jfe-partner-modify"), deletion = this.element("td", undefined, "jfe-partner-action jfe-partner-delete"), disabledDelete = this.element("button", __("删除"), "jfe-button");
			modify.append(this.button("修改", () => this.openExisting(row.name)));
			disabledDelete.type = "button"; disabledDelete.disabled = true; disabledDelete.title = __("删除尚未开放，可在档案中正常停用。");
			deletion.append(disabledDelete); tr.append(modify, deletion);
			this.tbody.append(tr);
		}
	}
	async openNew() { return this.open(null); }
	async openExisting(name) { if (!this.rows.some((row) => row.name === name)) return; return this.open(name); }
	async open(name) {
		if (this.disposed || this.opening || this.editor || !this.companyScope) return;
		this.cancelExport();
		const company = this.companyScope, sequence = ++this.openRevision; this.opening = true; this.freezeStatus();
		try { const response = await this.call(name ? "read_archive" : "init_archive", { kind: this.kind, company, ...(name ? { name } : {}) });
			if (this.disposed || sequence !== this.openRevision || company !== this.companyScope) return;
			if (!this.validArchive(response.message, company, name)) throw new Error("Wrong archive scope");
			await this.buildEditor(response.message);
		} catch { if (!this.disposed && sequence === this.openRevision) this.status.textContent = __("工厂档案未能打开，请核对权限后重试。"); }
		finally { if (sequence === this.openRevision) this.opening = false; this.freezeStatus(); }
	}
	requestID() { return crypto.randomUUID(); }
	async buildEditor(archive) {
		const dialog = new frappe.ui.Dialog({ title: __(this.kind), size: "large", no_submit_on_enter: true, fields: [{ fieldname: "partner_editor", fieldtype: "HTML" }] });
		dialog.$wrapper.addClass("jfe-partner-dialog");
		const state = { dialog, archive, company: archive.company, controls: {}, cells: {}, supplierControls: {}, busy: false, unknown: false, closed: false,
			setting: false, supplierRevision: 0, supplierVersion: archive.supplier_version, requestId: archive.name ? null : this.requestID(), candidate: null };
		this.editor = state; this.freezeStatus();
		const body = dialog.fields_dict.partner_editor.$wrapper.get(0), scope = this.element("p", __("公司") + "：" + state.company);
		state.message = this.element("p"); const form = this.element("div", undefined, "jfe-partner-editor-fields");
		for (const [field, label] of this.fields) { const cell = this.element("div"); state.cells[field] = cell; form.append(cell);
			if (!this.plating && (field === "bank_account" || field === "inactive")) cell.className = "jfe-partner-wide-field";
			const metadata = archive.field_meta[field]; state.controls[field] = this.control(cell, field, field === "bank_name" ? "户行" : label, metadata?.fieldtype || "Data", "", () => this.freeze(state)); }
		const link = this.element("section", undefined, "jfe-partner-link"); link.append(this.element("p", __("业务供应商随新工厂一起保存，或明确关联已有供应商。银行原文保留在工厂档案。")));
		const options = [archive.can_create_supplier ? "新建业务供应商" : null, archive.can_link_supplier || archive.name ? "关联已有供应商" : null].filter(Boolean).join("\n");
		state.modeControl = this.control(link, "supplier_mode", "业务来源", "Select", options, () => this.changeMode(state));
		state.sourceMode = archive.name || !archive.can_create_supplier ? "existing" : "create";
		for (const [field, label] of [["supplier_group", "供应商分组"], ["supplier_type", "供应商类型"], ["naming_series", "编号规则"]]) {
			const metadata = archive.supplier_field_meta?.[field]; if (metadata) state.supplierControls[field] = this.control(link, field, label, metadata.fieldtype, metadata.options, () => this.freeze(state));
			if (field === "supplier_group" && state.supplierControls[field]) state.supplierControls[field].get_query = () => ({ filters: { is_group: 0 } });
		}
		state.supplierControl = this.control(link, "supplier", "关联供应商", "Link", "Supplier", () => this.changeSupplier(state));
		state.supplierStatus = this.element("p"); link.append(state.supplierStatus, this.element("p", __("新编号由本系统生成，可改为原编号。")));
		state.review = this.element("div", undefined, "jfe-partner-review"); const footer = this.element("div", undefined, "jfe-partner-footer");
		state.newButton = this.button("新增", () => { if (this.plating && state.archive.name) return; return this.discardFor(async () => { this.closeEditor(); await this.openNew(); }, state); });
		state.saveButton = this.button("保存", () => this.save(state)); state.closeButton = this.button("关闭", () => dialog.hide());
		state.resolveButton = this.button("核对保存结果", () => this.resolve(state)); state.adoptButton = this.button("采用已核对当前值", () => this.adopt(state));
		footer.append(state.newButton, state.saveButton, state.closeButton, state.resolveButton, state.adoptButton); body.append(scope, state.message, form, link, state.review, footer);
		this.guardClose(state); await this.applyArchive(state, archive); dialog.show();
	}
	guardClose(state) {
		state.nativeHide = state.dialog.hide.bind(state.dialog); state.dialog.hide = () => this.discardFor(() => this.closeEditor(), state);
		state.dialog.$wrapper.modal?.({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn?.().removeAttr("data-dismiss").on("click.partner-archive", () => state.dialog.hide());
		state.dialog.$wrapper.on?.("keydown.partner-archive", (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); } });
		state.dialog.on_hide = () => { state.closed = true; state.supplierRevision++; if (this.editor === state) this.editor = null; this.freezeStatus(); };
	}
	async applyArchive(state, archive) {
		state.archive = archive; state.supplierVersion = archive.supplier_version; state.supplierRevision++; state.setting = true;
		state.sourceMode = archive.name || !archive.can_create_supplier ? "existing" : "create";
		try { for (const [field, control] of Object.entries(state.controls)) await control.set_value(archive.profile[field] ?? ""); await state.supplierControl.set_value(archive.profile.supplier ?? "");
			await state.modeControl.set_value(state.sourceMode === "create" ? "新建业务供应商" : "关联已有供应商");
			for (const [field, control] of Object.entries(state.supplierControls)) await control.set_value(archive.supplier_defaults?.[field] ?? ""); }
		finally { state.setting = false; }
		state.original = JSON.stringify(this.draft(state)); state.review.replaceChildren(); state.conflict = null;
		state.supplierStatus.textContent = archive.name ? __(archive.supplier_disabled ? "关联供应商已停用；工厂停用另行保存。" : "关联供应商已明确保存。") :
			__(state.sourceMode === "create" ? "保存时创建新供应商，名称取上方工厂名称。分组使用系统已配置的默认值，可修改。" : "请选择已有供应商，不按工厂名称自动匹配。");
		this.freeze(state);
	}
	values(state) {
		const values = {};
		for (const [field, control] of Object.entries(state.controls)) if (state.archive.field_meta[field] && !state.archive.field_meta[field].read_only)
			values[field] = state.archive.field_meta[field].fieldtype === "Check" ? Number(control.get_value() || 0) : control.get_value() ?? "";
		if (!state.archive.name && state.sourceMode === "existing" && state.archive.field_meta.supplier && !state.archive.field_meta.supplier.read_only) values.supplier = state.supplierControl.get_value() || "";
		return values;
	}
	supplierValues(state) { return Object.fromEntries(Object.entries(state.supplierControls).filter(([field]) => !state.archive.supplier_field_meta?.[field]?.read_only)
		.map(([field, control]) => [field, control.get_value() || null])); }
	draft(state) { return { profile: this.values(state), source_mode: state.sourceMode, supplier_create: state.sourceMode === "create" ? this.supplierValues(state) : null }; }
	async changeMode(state) {
		if (state.setting || state.closed || state.archive.name || state.busy || state.unknown) return;
		const requested = state.modeControl.get_value() === "新建业务供应商" ? "create" : "existing";
		if (requested === "create" && !state.archive.can_create_supplier || requested === "existing" && !state.archive.can_link_supplier) return;
		state.sourceMode = requested; state.supplierRevision++; state.supplierVersion = null;
		state.supplierStatus.textContent = __(requested === "create" ? "保存时新建供应商，名称取工厂名称，不按名称复用已有供应商。" : "请显式选择已有供应商。");
		if (requested === "existing" && state.supplierControl.get_value()) await this.changeSupplier(state);
		this.freeze(state);
	}
	async changeSupplier(state) {
		if (state.setting || state.closed || state.archive.name || state.busy || state.unknown || state.sourceMode !== "existing") return;
		const name = state.supplierControl.get_value() || "", sequence = ++state.supplierRevision; state.supplierVersion = null;
		state.supplierStatus.textContent = __("正在核对关联供应商…"); this.freeze(state);
		if (!name) { state.supplierStatus.textContent = __("请显式选择关联供应商。"); return; }
		try { const response = await this.call("read_supplier", { name, kind: this.kind, company: state.company }); const result = response.message;
			if (state.closed || this.disposed || sequence !== state.supplierRevision || name !== state.supplierControl.get_value()) return;
			if (!result || result.name !== name || result.company !== state.company || result.kind !== this.kind || typeof result.version !== "string" || !result.version || typeof result.disabled !== "boolean") throw new Error("Incomplete supplier");
			if (result.disabled) { state.supplierStatus.textContent = __("停用供应商不能关联新工厂档案。"); return; }
			state.supplierVersion = result.version; state.supplierStatus.textContent = __("供应商已核对，工厂原文保持独立。");
		} catch { if (!state.closed && sequence === state.supplierRevision) state.supplierStatus.textContent = __("关联供应商未能核对，请重新选择。"); }
		finally { if (!state.closed && sequence === state.supplierRevision) this.freeze(state); }
	}
	freeze(state) {
		this.freezeStatus();
		if (!state || state.closed) return;
		const locked = state.busy || state.unknown || state.confirming;
		for (const [field, control] of Object.entries(state.controls)) { const metadata = state.archive.field_meta[field]; this.flags(control,
			{ hidden: !metadata, read_only: !!(locked || !state.archive.can_write || !metadata || metadata.read_only), reqd: !!metadata?.reqd }); }
		const supplier = state.archive.field_meta.supplier;
		this.flags(state.modeControl, { hidden: !!state.archive.name, read_only: !!(locked || state.archive.name), reqd: true });
		for (const [field, control] of Object.entries(state.supplierControls)) { const metadata = state.archive.supplier_field_meta?.[field]; this.flags(control,
			{ hidden: !!state.archive.name || state.sourceMode !== "create", read_only: !!(locked || state.archive.name || !state.archive.can_create_supplier || metadata?.read_only), reqd: !!metadata?.reqd }); }
		this.flags(state.supplierControl, { hidden: !state.archive.name && state.sourceMode === "create", read_only: !!(locked || state.archive.name || !state.archive.can_write || !supplier || supplier.read_only), reqd: state.sourceMode === "existing" });
		state.saveButton.disabled = !!(locked || !state.archive.can_write || (state.archive.name || state.sourceMode === "existing") && !state.supplierVersion ||
			!state.archive.name && state.sourceMode === "create" && !state.archive.can_create_supplier);
		state.newButton.disabled = !!(locked || this.plating && state.archive.name);
		state.newButton.textContent = __(this.plating && state.archive.name ? "修改" : "新增");
		state.newButton.title = this.plating && state.archive.name ? __("此按钮尚未开放；修改字段后请点保存。") : "";
		state.closeButton.disabled = !!locked;
		state.resolveButton.hidden = !state.unknown; state.resolveButton.disabled = !!(state.busy || state.confirming);
		state.adoptButton.hidden = !state.unknown || !state.conflict; state.adoptButton.disabled = !!(state.busy || state.confirming);
	}
	isDirty(state = this.editor) { return !!(state && !state.closed && state.original !== JSON.stringify(this.draft(state))); }
	getCloseBlock() { if (this.opening || this.editor?.busy || this.editor?.confirming) return "正在读取、保存或核对工厂档案，请稍候。"; return this.editor?.unknown ? "保存结果尚未确认，请先核对保存结果。" : ""; }
	async discardFor(action, state = this.editor) {
		if (this.disposed || state?.closed) return false;
		const block = this.getCloseBlock(); if (block) { frappe.msgprint(__(block)); return false; }
		if (!this.isDirty(state)) { await action(); return true; }
		state.confirming = true; this.freeze(state); const yes = await new Promise((resolve) => frappe.confirm(__("尚有未保存的修改，放弃修改并继续？"), () => resolve(true), () => resolve(false)));
		state.confirming = false; this.freeze(state); if (yes && !this.disposed && !state.closed) { await action(); return true; } return false;
	}
	closeEditor() { const state = this.editor; if (!state || state.closed) return; state.closed = true; state.supplierRevision++; state.nativeHide(); this.editor = null; this.freezeStatus(); }
	payload(state) {
		const current = this.values(state), result = { kind: this.kind, company: state.company, profile: {} };
		if (state.archive.name || state.sourceMode === "existing") result.supplier_version = state.supplierVersion;
		if (state.archive.name) { result.name = state.archive.name; result.profile_version = state.archive.profile_version;
			for (const [field, value] of Object.entries(current)) if (value !== state.archive.profile[field] && !(value === "" && state.archive.profile[field] === null)) result.profile[field] = value;
		} else { result.request_id = state.requestId; result.profile = current; if (state.sourceMode === "create") result.supplier_create = this.supplierValues(state); }
		return result;
	}
	complete(state, row) { return !!(state.candidate && row?.name && this.validArchive(row, state.company, state.candidate.name) &&
		Object.keys(state.candidate.profile).every((field) => field in row.profile && row.field_meta[field])); }
	matches(state, row) { return this.complete(state, row) && (!state.candidate.name || row.profile_version !== state.candidate.profile_version) &&
		Object.entries(state.candidate.profile).every(([field, value]) => row.field_meta[field].fieldtype === "Check" ? Number(row.profile[field]) === Number(value) : (row.profile[field] ?? "") === (value ?? "")); }
	definiteFailure(error) { return ["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError", "UniqueValidationError"].includes(error?.responseJSON?.exc_type); }
	async save(state) {
		if (state.closed || this.disposed || this.getCloseBlock() || state.saveButton.disabled || state.archive.name && !this.isDirty(state)) return;
		state.candidate = structuredClone(this.payload(state)); state.conflict = null; state.review.replaceChildren(); state.busy = true; this.freeze(state);
		let answered = false;
		try { const response = await this.call("save_archive", { payload: state.candidate }, true); answered = true; let row = response.message;
			if (!state.candidate.name && this.validArchive(row, state.company) && row.name) { const check = await this.call("resolve_creation", { request_id: state.candidate.request_id, kind: this.kind, company: state.company });
				if (check.message?.status !== "found" || check.message.archive?.name !== row.name || check.message.archive?.profile_version !== row.profile_version) throw new Error("Unconfirmed creation receipt"); row = check.message.archive; }
			if (this.matches(state, row)) await this.accept(state, row, "工厂档案已保存。"); else { state.unknown = true; this.showConflict(state, row); }
		} catch (error) { state.unknown = answered || !this.definiteFailure(error); state.message.textContent = __(state.unknown ? "保存结果尚未确认，请核对保存结果。" : "保存未完成，草稿已保留，请核对字段或重新打开当前版本。"); }
		finally { state.busy = false; this.freeze(state); }
	}
	async accept(state, row, message) { await this.applyArchive(state, row); state.unknown = false; state.candidate = null; state.conflict = null; state.requestId = null;
		state.message.textContent = __(message); this.revision++; this.clearResults(); this.status.textContent = __("档案已保存，关闭弹层后查询最新列表。"); }
	showConflict(state, row) {
		if (!this.complete(state, row)) return;
		state.conflict = row; state.review.replaceChildren(this.element("p", __("服务器当前值与保留草稿不同。采用当前值只读取，不会再次保存。")));
		const table = this.element("table"), heading = this.element("tr"); for (const label of ["字段", "保留草稿", "服务器当前值"]) heading.append(this.element("th", __(label))); table.append(heading);
		for (const [field, value] of Object.entries(state.candidate.profile)) { const tr = this.element("tr"); tr.append(this.element("td", __(this.labels[field] || "关联供应商")), this.element("td", value), this.element("td", row.profile[field])); table.append(tr); } state.review.append(table);
	}
	async resolve(state) {
		if (state.closed || state.busy || state.confirming || !state.unknown || !state.candidate) return;
		state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try { const args = { kind: this.kind, company: state.company, ...(state.candidate.name ? { name: state.candidate.name } : { request_id: state.candidate.request_id }) };
			const response = await this.call(state.candidate.name ? "read_archive" : "resolve_creation", args);
			if (state.closed || this.disposed) return;
			const row = state.candidate.name ? response.message : response.message?.status === "found" ? response.message.archive : null;
			if (this.matches(state, row)) await this.accept(state, row, "已核对保存结果。"); else if (this.complete(state, row)) this.showConflict(state, row);
			else state.message.textContent = __("未核对到完整可见结果，请保留草稿稍后核对；不会重复保存。");
		} catch { state.message.textContent = __("核对失败，原草稿已保留，请稍后核对。"); }
		finally { state.busy = false; this.freeze(state); }
	}
	adopt(state) {
		if (state.closed || state.busy || state.confirming || !state.unknown || !state.conflict) return;
		const row = state.conflict; state.confirming = true; this.freeze(state);
		frappe.confirm(__("放弃保留草稿并采用刚核对的当前值？此操作不会再次保存。"), async () => { state.confirming = false; if (state.closed || state.conflict !== row) return;
			state.busy = true; this.freeze(state); try { await this.accept(state, row, "已采用服务器当前值；未重复保存。"); } finally { state.busy = false; this.freeze(state); }
		}, () => { state.confirming = false; this.freeze(state); });
	}
	destroy() { if (this.disposed) return; this.disposed = true; this.revision++; this.openRevision++; window.removeEventListener?.("beforeunload", this.beforeUnload); this.closeEditor(); this.clearResults(); this.host.replaceChildren(); }
	dispose() { this.destroy(); }
};
