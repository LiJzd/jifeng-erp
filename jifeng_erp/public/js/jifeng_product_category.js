/* Company-scoped category texts. One row per save; no stock conversion or batch save. */
window.JifengProductCategory = class JifengProductCategory {
	constructor(host) {
		this.host = host; this.disposed = false; this.revision = 0; this.detailRevision = 0;
		this.scopeCompany = null; this.selected = null; this.editor = null; this.opening = false;
		this.rows = { category: [], detail: [] }; this.scopeMeta = null; this.companyChanging = false;
		this.fields = { category: ["legacy_code", "major_name", "owner_name", "region", "inactive"],
			detail: ["minor_name", "material", "display_uom", "conversion_uom_text", "conversion_value", "inactive"] };
		this.labels = { legacy_code: "代码", major_name: "大类", owner_name: "负责", region: "区域", inactive: "停用",
			minor_name: "小类", material: "材质", display_uom: "单位", conversion_uom_text: "换算单位", conversion_value: "换算值" };
		this.build();
		this.beforeUnload = (event) => {
			if (this.isDirty() || this.getCloseBlock()) { event.preventDefault(); event.returnValue = ""; }
		};
		window.addEventListener("beforeunload", this.beforeUnload);
		this.ready = this.initialize();
	}

	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className;
		return node;
	}
	button(text, action, reason) {
		const node = this.element("button", __(text), "btn btn-default btn-sm"); node.type = "button";
		if (reason) { node.disabled = true; node.title = __(reason); }
		else node.addEventListener("click", action);
		return node;
	}
	build() {
		this.host.classList.add("jfe-product-category"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-product-category-toolbar");
		this.saveButton = this.button("保存", () => this.save());
		const companyHost = this.element("div", undefined, "jfe-product-category-company");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), reqd: true,
				onchange: () => this.changeCompany() } });
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("关键词"); this.search.setAttribute("aria-label", __("关键词"));
		this.search.addEventListener("input", () => { this.revision++; this.detailRevision++; this.status.textContent = __("关键词已改变，请查询。"); });
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(); } });
		this.includeInactive = this.element("input"); this.includeInactive.type = "checkbox";
		this.includeInactive.addEventListener("change", () => this.query());
		const inactiveLabel = this.element("label", undefined, "jfe-product-category-inactive");
		inactiveLabel.append(this.includeInactive, this.element("span", __("包含停用")));
		toolbar.append(this.button("导出", null, "本批未实现导出"), this.button("导入", null, "本批未实现导入"),
			this.saveButton, this.button("更新简拼", null, "简拼规则尚未核实，本批未实现"), companyHost,
			this.search, this.button("查询", () => this.query()), inactiveLabel);
		this.limits = this.element("p", __("保存仅保存当前编辑行。导入、导出、删除和更新简拼暂不可用。单位及换算值按原文维护，尚未接入库存换算。"), "jfe-product-category-limits");
		this.status = this.element("p", __("正在读取权限…"), "jfe-product-category-status"); this.status.setAttribute("role", "status");
		const grids = this.element("div", undefined, "jfe-product-category-grids"); this.panels = {};
		for (const kind of ["category", "detail"]) {
			const panel = this.element("section", undefined, "jfe-product-category-panel");
			const title = this.element("div", undefined, "jfe-product-category-panel-title");
			const titleText = this.element("strong", __(kind === "category" ? "大类" : "小类"));
			const add = this.button(kind === "category" ? "新增大类" : "新增小类", () => this.openEditor(kind));
			const edit = kind === "category" ? this.button("修改所选大类", () => this.selected && this.openEditor(kind, this.selected.name)) : null;
			title.append(titleText, add); if (edit) title.append(edit);
			const scroller = this.element("div", undefined, "jfe-product-category-scroll");
			const table = this.element("table", undefined, "jfe-product-category-table"); table.setAttribute("aria-label", __(kind === "category" ? "产品大类" : "产品小类"));
			const head = this.element("thead"), headings = this.element("tr");
			for (const field of this.fields[kind]) { const th = this.element("th", __(this.labels[field])); th.scope = "col"; headings.append(th); }
			if (kind === "detail") { const th = this.element("th", __("操作")); th.scope = "col"; headings.append(th); }
			const deletion = this.element("th", __("删除")); deletion.scope = "col"; headings.append(deletion);
			head.append(headings); const tbody = this.element("tbody"); table.append(head, tbody); scroller.append(table);
			const hint = this.element("p", __(kind === "category" ? "点击大类行查看右侧小类。" : "请先选择左侧大类。"), "jfe-product-category-hint");
			panel.append(title, hint, scroller); grids.append(panel);
			this.panels[kind] = { panel, titleText, add, edit, hint, scroller, tbody };
		}
		this.editorTools = this.element("div", undefined, "jfe-product-category-editor-tools");
		this.cancelButton = this.button("结束编辑", () => this.discardFor(() => this.clearEditor()));
		this.resolveButton = this.button("核对保存结果", () => this.resolve());
		this.adoptButton = this.button("采用已核对当前值", () => this.acceptCurrent());
		this.editorTools.append(this.cancelButton, this.resolveButton, this.adoptButton);
		this.review = this.element("div", undefined, "jfe-product-category-review");
		this.host.append(toolbar, this.limits, this.status, grids, this.editorTools, this.review); this.freeze();
	}

	async initialize() {
		try {
			const response = await this.call("init_record", { record_type: "category" });
			const archive = response.message;
			if (this.disposed) return false;
			if (!archive?.field_meta || !["company", "major_name", "inactive"].every((field) => archive.field_meta[field]) ||
				typeof archive.can_write !== "boolean" || archive.record_type !== "category" || archive.name || archive.company) throw new Error("Wrong initial scope");
			this.scopeMeta = archive; this.freeze(); this.status.textContent = __("请选择公司后查询。"); return true;
		} catch { if (!this.disposed) this.status.textContent = __("无权读取或页面未能加载，请重新打开。"); return false; }
	}
	call(method, args, write = false) {
		return frappe.call({ method: "jifeng_erp.product_category." + method, args, ...(write ? { freeze: true, freeze_message: __("正在保存当前类别行…") } : {}) });
	}
	async setCompany(value) {
		this.companyChanging = true;
		try { await this.companyControl.set_value(value); } finally { this.companyChanging = false; }
	}
	async changeCompany() {
		if (this.companyChanging || this.disposed) return;
		const desired = this.companyControl.get_value() || null;
		if (desired === this.scopeCompany) return;
		await this.setCompany(this.scopeCompany);
		if (this.disposed) return;
		await this.discardFor(async () => {
			this.clearEditor(); this.scopeCompany = desired; this.selected = null;
			this.revision++; this.detailRevision++; this.rows = { category: [], detail: [] };
			await this.setCompany(desired); this.render(); this.freeze(); await this.fetchCategories();
		});
	}
	isDirty() { return !!(this.editor && JSON.stringify(this.values(this.editor)) !== this.editor.original); }
	getCloseBlock() {
		if (this.opening || this.editor?.busy) return "正在读取或保存类别，请稍候。";
		if (this.editor?.unresolved) return "保存结果尚未确认，请先核对保存结果。";
		return this.confirming || this.editor?.confirming ? "正在确认类别草稿，请稍候。" : "";
	}
	async discardFor(action) {
		if (this.disposed) return false;
		const block = this.getCloseBlock(); if (block) { frappe.msgprint(__(block)); return false; }
		if (!this.isDirty()) { await action(); return true; }
		this.confirming = true; this.freeze();
		const discard = await new Promise((resolve) => frappe.confirm(__("当前行尚有未保存修改，放弃修改并继续？"), () => resolve(true), () => resolve(false)));
		this.confirming = false; this.freeze();
		if (discard && !this.disposed) { await action(); return true; }
		return false;
	}
	async query() {
		if (!this.scopeMeta || this.disposed) return;
		return this.discardFor(async () => { this.clearEditor(); await this.fetchCategories(); });
	}
	async fetchCategories() {
		const company = this.scopeCompany, search = this.search.value || "", inactive = !!this.includeInactive.checked;
		const sequence = ++this.revision; ++this.detailRevision;
		if (!company) { this.status.textContent = __("请选择公司后查询。"); return; }
		this.status.textContent = __("正在查询…");
		try {
			const response = await this.call("list_records", { record_type: "category", company, search, include_inactive: Number(inactive) });
			if (this.disposed || sequence !== this.revision || company !== this.scopeCompany || search !== this.search.value || inactive !== !!this.includeInactive.checked) return;
			this.validateRows(response.message, "category", company);
			this.rows.category = response.message;
			this.selected = this.rows.category.find((row) => row.name === this.selected?.name) || null;
			this.rows.detail = []; this.render(); this.freeze();
			this.status.textContent = this.rows.category.length ? __("大类查询完成") + " · " + this.rows.category.length + __("条") : __("没有可见大类。");
			if (this.selected) await this.fetchDetails();
		} catch { if (!this.disposed && sequence === this.revision) this.status.textContent = __("查询失败，当前列表未更新，请重新查询。"); }
	}
	validateRows(rows, kind, company, category) {
		if (!Array.isArray(rows) || rows.some((row) => !row?.name || typeof row.version !== "string" || !row.version || row.record_type !== kind ||
			row.company !== company || typeof row.can_select !== "boolean" || ![0, 1].includes(row.inactive) ||
			typeof row[kind === "category" ? "major_name" : "minor_name"] !== "string" || kind === "detail" && row.category !== category)) throw new Error("Wrong list scope");
	}
	async selectCategory(row) {
		if (this.disposed || row.company !== this.scopeCompany || row.record_type !== "category" ||
			!this.rows.category.some((current) => current.name === row.name && current.version === row.version) || row.name === this.selected?.name) return;
		return this.discardFor(async () => {
			this.clearEditor(); this.selected = row; this.rows.detail = []; ++this.detailRevision;
			this.render(); this.freeze(); await this.fetchDetails();
		});
	}
	async fetchDetails() {
		const category = this.selected?.name, company = this.scopeCompany, search = this.search.value || "", inactive = !!this.includeInactive.checked;
		const sequence = ++this.detailRevision;
		if (!company || !category) return;
		this.panels.detail.hint.textContent = __("正在查询所选大类的小类…");
		try {
			const response = await this.call("list_records", { record_type: "detail", company, category, search, include_inactive: Number(inactive) });
			if (this.disposed || sequence !== this.detailRevision || company !== this.scopeCompany || category !== this.selected?.name || search !== this.search.value || inactive !== !!this.includeInactive.checked) return;
			this.validateRows(response.message, "detail", company, category); this.rows.detail = response.message; this.renderKind("detail");
			this.panels.detail.hint.textContent = __("所选大类：") + (this.selected.major_name ?? "") + " · " + this.rows.detail.length + __("条小类") +
				(this.selected.can_select ? "" : __("；该大类不能用于新增选择。"));
		} catch { if (!this.disposed && sequence === this.detailRevision) this.panels.detail.hint.textContent = __("小类查询失败，请重新查询。"); }
	}
	freeze() {
		const state = this.editor, locked = !!(this.opening || this.confirming || state?.busy || state?.unresolved || state?.confirming);
		this.saveButton.disabled = locked || !state?.archive.can_write || !!(state.archive.name && !this.isDirty());
		this.panels.category.add.disabled = locked || !this.scopeCompany || !this.scopeMeta?.can_write;
		this.panels.category.edit.disabled = locked || !this.selected;
		this.panels.detail.add.disabled = locked || !this.selected?.can_select;
		this.cancelButton.disabled = locked || !state;
		this.resolveButton.disabled = !!(this.opening || this.confirming || state?.busy || state?.confirming || !state?.unresolved);
		this.adoptButton.disabled = !!(this.opening || this.confirming || state?.busy || state?.confirming || !state?.unresolved || !state?.conflict);
		this.editorTools.hidden = !state;
		if (state) for (const [field, input] of Object.entries(state.controls)) input.disabled = locked || !state.archive.can_write || !!state.archive.field_meta[field]?.read_only;
	}
	render() { this.renderKind("category"); this.renderKind("detail"); }
	renderKind(kind) {
		const panel = this.panels[kind], scroll = { top: panel.scroller.scrollTop, left: panel.scroller.scrollLeft };
		panel.tbody.replaceChildren();
		for (const row of this.rows[kind]) {
			if (this.editor?.kind === kind && this.editor.archive.name === row.name) this.renderEditor(panel.tbody);
			else this.renderRow(kind, row, panel.tbody);
		}
		if (this.editor?.kind === kind && !this.rows[kind].some((row) => row.name === this.editor.archive.name)) this.renderEditor(panel.tbody);
		panel.scroller.scrollTop = scroll.top; panel.scroller.scrollLeft = scroll.left;
		if (kind === "detail" && !this.selected) panel.hint.textContent = __("请先选择左侧大类。");
	}
	renderRow(kind, row, body) {
		const tr = this.element("tr");
		if (kind === "category") {
			tr.tabIndex = 0; tr.setAttribute("aria-selected", String(row.name === this.selected?.name));
			if (row.name === this.selected?.name) tr.classList.add("jfe-product-category-selected");
			tr.addEventListener("click", () => this.selectCategory(row));
			tr.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.selectCategory(row); } });
		}
		for (const field of this.fields[kind]) {
			const td = this.element("td");
			if (field === "inactive") { const check = this.element("input"); check.type = "checkbox"; check.checked = !!Number(row.inactive); check.disabled = true; check.setAttribute("aria-label", __("停用")); td.append(check); }
			else td.textContent = row[field] ?? "";
			tr.append(td);
		}
		if (kind === "detail") { const td = this.element("td"); td.append(this.button("修改 / 查看", () => this.openEditor(kind, row.name))); tr.append(td); }
		const deletion = this.element("td"); deletion.append(this.button("删除", null, "本批未实现可追溯删除")); tr.append(deletion); body.append(tr);
	}
	renderEditor(body) {
		const state = this.editor, tr = this.element("tr", undefined, "jfe-product-category-editing");
		for (const field of this.fields[state.kind]) {
			const td = this.element("td");
			if (state.controls[field]) td.append(state.controls[field]);
			tr.append(td);
		}
		if (state.kind === "detail") tr.append(this.element("td", __("当前编辑行")));
		tr.append(this.element("td", __("保存当前行"))); body.append(tr); state.row = tr;
	}
	validArchive(archive, kind, company, category, name = null) {
		const required = ["company", kind === "category" ? "major_name" : "minor_name", "inactive", ...(kind === "detail" ? ["category"] : [])];
		return !!(archive?.field_meta && archive.record_type === kind && archive.company === company &&
			typeof archive.can_write === "boolean" && typeof archive.can_select === "boolean" && [0, 1].includes(archive.inactive) &&
			required.every((field) => field in archive && archive.field_meta[field]) &&
			(kind !== "detail" || archive.category === category) && (!name || archive.name === name) &&
			(archive.name ? typeof archive.version === "string" && !!archive.version && typeof archive[required[1]] === "string" : !archive.version));
	}
	async openEditor(kind, name = null) {
		if (!this.scopeMeta || !this.scopeCompany || kind === "detail" && !this.selected || this.disposed) return;
		return this.discardFor(async () => {
			const company = this.scopeCompany, category = kind === "detail" ? this.selected.name : null;
			// Opening an editor supersedes earlier background queries that could change its visible parent.
			this.revision++; this.detailRevision++;
			this.opening = true; this.freeze(); let opened = false;
			try {
				const response = await this.call(name ? "read_record" : "init_record", name ? { record_type: kind, name } :
					{ record_type: kind, company, ...(category ? { category } : {}) });
				const archive = response.message;
				if (this.disposed || company !== this.scopeCompany || kind === "detail" && category !== this.selected?.name ||
					!this.validArchive(archive, kind, company, category, name) || (!name && archive.name)) throw new Error("Wrong editor scope");
				this.setEditor(archive, kind); opened = true;
				this.status.textContent = __(name ? "已打开所选行；保存仅提交本行。" : "新增行尚未保存；保存仅提交本行。");
			} catch {
				if (!this.disposed) this.status.textContent = __("类别编辑行未能打开，原草稿已保留，请重试。");
			}
			finally {
				this.opening = false;
				if (!this.disposed) {
					this.freeze();
					if (opened) {
						this.editor.row?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
						const title = this.editor.controls[kind === "category" ? "major_name" : "minor_name"];
						if (title && !title.disabled) title.focus?.();
					}
				}
			}
		});
	}
	setEditor(archive, kind, requestId = null) {
		const state = { kind, archive, company: archive.company, category: archive.category || null,
			requestId: archive.name ? null : requestId || this.requestID(), controls: {}, busy: false, unresolved: false, conflict: null, candidate: null };
		for (const field of this.fields[kind]) {
			const df = archive.field_meta[field]; if (!df) continue;
			const input = this.element("input"); input.type = field === "inactive" ? "checkbox" : "text";
			input.setAttribute("aria-label", __(this.labels[field])); input.title = __(df.description || this.labels[field]);
			if (field === "inactive") input.checked = !!Number(archive[field]);
			else { input.value = archive[field] ?? ""; input.maxLength = 140; input.required = !!df.reqd; }
			input.addEventListener("input", () => this.freeze()); input.addEventListener("change", () => this.freeze());
			state.controls[field] = input;
		}
		state.original = JSON.stringify(this.values(state)); this.editor = state;
		this.review.replaceChildren(); this.render(); this.freeze(); return state;
	}
	clearEditor() { this.editor = null; this.review.replaceChildren(); this.render(); this.freeze(); }
	values(state) {
		return Object.fromEntries(Object.entries(state.controls).filter(([field]) => state.archive.can_write && !state.archive.field_meta[field].read_only)
			.map(([field, input]) => [field, field === "inactive" ? Number(input.checked) : input.value]));
	}
	payload(state) {
		const values = this.values(state), payload = { record_type: state.kind };
		if (state.archive.name) {
			payload.name = state.archive.name; payload.version = state.archive.version;
			for (const [field, value] of Object.entries(values)) if (field === "inactive" ? Number(state.archive[field]) !== value : (state.archive[field] ?? "") !== value) payload[field] = value;
		} else Object.assign(payload, { company: state.company, request_id: state.requestId,
			...(state.kind === "detail" ? { category: state.category } : {}) }, values);
		return payload;
	}
	requestID() {
		if (crypto.randomUUID) return crypto.randomUUID();
		const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
		const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
		return [value.slice(0, 8), value.slice(8, 12), value.slice(12, 16), value.slice(16, 20), value.slice(20)].join("-");
	}
	definiteFailure(error) {
		return new Set(["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError", "UniqueValidationError"])
			.has(error?.responseJSON?.exc_type);
	}
	completeResult(state, archive) {
		return this.validArchive(archive, state.kind, state.company, state.category, state.candidate?.name) && !!archive.name &&
			this.fields[state.kind].filter((field) => field in state.candidate).every((field) => field in archive && archive.field_meta[field]);
	}
	numericTextKey(value) {
		if (value === null || value === "") return { empty: true };
		if (typeof value !== "string") return null;
		// Compare Decimal texts without floating point or expanding an exponent.
		// Unknown digit syntax stays held for review rather than guessing a value.
		const parsed = value.trim().replace(/_/g, "").match(/^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/);
		if (!parsed) return null;
		const fraction = parsed[3] ?? parsed[4] ?? "", digits = ((parsed[2] || "") + fraction).replace(/^0+/, "");
		const negative = parsed[1] === "-";
		if (!digits) return { negative, coefficient: "0", exponent: "0" };
		const coefficient = digits.replace(/0+$/, "");
		const exponent = BigInt(parsed[5] || "0") - BigInt(fraction.length) + BigInt(digits.length - coefficient.length);
		return { negative, coefficient, exponent: exponent.toString() };
	}
	sameNumberText(left, right) {
		const a = this.numericTextKey(left), b = this.numericTextKey(right);
		return !!(a && b && JSON.stringify(a) === JSON.stringify(b));
	}
	matches(state, archive) {
		if (!this.completeResult(state, archive) || state.candidate.name && archive.version === state.candidate.version) return false;
		return this.fields[state.kind].filter((field) => field in state.candidate).every((field) => field === "inactive" ?
			Number(state.candidate[field]) === Number(archive[field]) : field === "conversion_value" ?
				this.sameNumberText(state.candidate[field], archive[field]) : (state.candidate[field] ?? "") === (archive[field] ?? ""));
	}
	async save() {
		const state = this.editor;
		if (!state || this.getCloseBlock() || !state.archive.can_write || this.disposed || state.archive.name && !this.isDirty()) return;
		for (const [field, input] of Object.entries(state.controls)) if (!input.disabled && input.required && !input.value.trim()) {
			frappe.msgprint(__("请填写") + __(this.labels[field])); input.focus?.(); return;
		}
		state.candidate = structuredClone(this.payload(state)); state.conflict = null; this.review.replaceChildren(); state.busy = true; this.freeze();
		try {
			const response = await this.call("save_record", { payload: state.candidate }, true);
			if (this.matches(state, response.message)) this.accept(state, response.message, "当前行已保存");
			else { state.unresolved = true; this.showConflict(state, response.message); }
		} catch (error) { state.unresolved = !this.definiteFailure(error); }
		finally { state.busy = false; this.freeze(); if (state.unresolved) frappe.msgprint(__("保存结果尚未确认，当前行草稿已保留，请核对保存结果。")); }
	}
	accept(state, archive, message) {
		// A permission refresh may omit values: replace the old DTO rather than merge it.
		const rows = this.rows[state.kind], index = rows.findIndex((row) => row.name === archive.name);
		if (index < 0) rows.push(archive); else rows[index] = archive;
		if (state.kind === "category" && this.selected?.name === archive.name) this.selected = archive;
		this.setEditor(archive, state.kind); this.status.textContent = __(message); frappe.show_alert({ message: __(message), indicator: "green" });
		if (state.kind === "category" && this.selected?.name === archive.name) this.fetchDetails();
	}
	showConflict(state, archive) {
		if (!this.completeResult(state, archive)) return;
		state.conflict = archive;
		this.review.append(this.element("p", __("服务器当前值与保留草稿或版本不同，请核对后明确采用当前值。采用操作不会再次保存。")));
		const table = this.element("table"), headings = this.element("tr");
		for (const label of ["字段", "保留草稿", "服务器当前值"]) headings.append(this.element("th", __(label))); table.append(headings);
		for (const field of this.fields[state.kind].filter((field) => field in state.candidate)) {
			const row = this.element("tr"); row.append(this.element("td", __(this.labels[field])),
				this.element("td", field === "inactive" ? Number(state.candidate[field]) ? "是" : "否" : state.candidate[field]),
				this.element("td", field === "inactive" ? Number(archive[field]) ? "是" : "否" : archive[field])); table.append(row);
		}
		this.review.append(table);
	}
	async resolve() {
		const state = this.editor;
		if (!state?.unresolved || state.busy || state.confirming || this.confirming || this.disposed) return;
		state.busy = true; state.conflict = null; this.review.replaceChildren(); this.freeze();
		try {
			const candidate = state.candidate;
			const response = await this.call(candidate.name ? "read_record" : "resolve_creation", candidate.name ? { name: candidate.name, record_type: state.kind } :
				{ request_id: candidate.request_id, record_type: state.kind, company: state.company, ...(state.kind === "detail" ? { category: state.category } : {}) });
			const archive = candidate.name ? response.message : response.message?.status === "found" ? response.message.archive : null;
			if (this.matches(state, archive)) this.accept(state, archive, "已核对保存结果");
			else if (this.completeResult(state, archive)) this.showConflict(state, archive);
			else frappe.msgprint(__("未核对到完整可见回执，请保留草稿稍后核对；不会重复保存。"));
		} catch { /* Unknown/permission failure retains the candidate and never repeats a POST. */ }
		finally { state.busy = false; this.freeze(); }
	}
	acceptCurrent() {
		const state = this.editor;
		if (!state?.unresolved || !state.conflict || state.busy || state.confirming || this.confirming || this.disposed) return;
		const archive = state.conflict; state.confirming = true; this.freeze();
		frappe.confirm(__("放弃保留草稿并采用刚核对的服务器当前值？此操作不再提交保存。"),
			() => { state.confirming = false; if (!this.disposed && this.editor === state && state.conflict === archive) this.accept(state, archive, "已采用当前值；未重复保存"); },
			() => { state.confirming = false; this.freeze(); });
	}
	destroy() {
		this.disposed = true; this.revision++; this.detailRevision++;
		window.removeEventListener("beforeunload", this.beforeUnload);
	}
};
