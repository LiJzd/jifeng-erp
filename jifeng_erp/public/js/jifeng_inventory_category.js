/* Related inventory categories; both grids contribute to one atomic save. */
window.JifengInventoryCategory = class JifengInventoryCategory {
	constructor(host, kind) {
		this.host = host; this.kind = kind; this.company = null; this.disposed = false;
		this.records = new Map(); this.rows = { major: [], minor: [] }; this.pages = { major: 0, minor: 0 };
		this.revisions = { major: 0, minor: 0, scope: 0 }; this.selected = null; this.busy = false; this.unresolved = false;
		this.labels = { legacy_code: "代码", value: "名称", owner_name: "负责", region: "区域", pinyin: "简拼", material: "材质",
			display_uom: "单位", conversion_uom_text: "换算单位", conversion_value: kind === "材料" ? "换算" : "换算值", inactive: "停用", notes: "备注",
			owner: "操作", creation: "日期", modified_by: "修改", modified: "修改时间" };
		this.columns = { major: ["legacy_code", "value", "owner_name", kind === "材料" ? "pinyin" : "region", "inactive"],
			minor: kind === "材料" ? ["value", "material", "display_uom", "conversion_value"] :
				["value", "material", "display_uom", "conversion_uom_text", "conversion_value", "inactive", "owner", "creation", "modified_by", "modified"] };
		this.optional = ["notes", "pinyin"]; this.build();
		this.beforeUnload = event => { if (this.isDirty() || this.getCloseBlock()) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener("beforeunload", this.beforeUnload); this.ready = this.initialize();
	}
	static uuid() {
		if (crypto.randomUUID) return crypto.randomUUID();
		const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128;
		const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
		return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
	}
	element(tag, text, className) {
		const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className; return node;
	}
	button(text, action) {
		const button = this.element("button", __(text), "btn btn-default btn-sm"); button.type = "button";
		button.addEventListener("click", action); return button;
	}
	call(method, args, write = false) {
		return frappe.call({ method: "jifeng_erp.inventory_category." + method, args,
			...(write ? { freeze: true, freeze_message: __("正在保存类别…") } : {}) });
	}
	build() {
		this.host.classList.add("jfe-inventory-category"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-inventory-category-toolbar");
		this.exportButton = this.button("导出", () => this.exportFile()); this.importButton = this.button("导入", () => this.importFile());
		this.saveButton = this.button("保存", () => this.save()); this.pinyinButton = this.button("更新简拼", () => this.updatePinyin());
		const companyHost = this.element("div", undefined, "jfe-inventory-category-company");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), reqd: true, onchange: () => this.changeCompany() } });
		this.search = this.element("input"); this.search.type = "search"; this.search.placeholder = __("关键词");
		this.search.setAttribute("aria-label", __("类别关键词"));
		this.search.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.query(); } });
		this.queryButton = this.button("查询", () => this.query());
		toolbar.append(this.exportButton, this.importButton, this.saveButton, this.pinyinButton, companyHost, this.search, this.queryButton, this.button("⚙", () => this.chooseColumns()));
		this.status = this.element("p", __("正在读取类别…"), "jfe-inventory-category-status"); this.status.setAttribute("role", "status");
		const grids = this.element("div", undefined, "jfe-inventory-category-grids"); this.panels = {};
		for (const type of ["major", "minor"]) {
			const panel = this.element("section", undefined, "jfe-inventory-category-panel"), heading = this.element("div", undefined, "jfe-inventory-category-heading");
			heading.append(this.element("strong", __(type === "major" ? "大类" : "小类")));
			const add = this.button("＋", () => this.addRow(type)); add.setAttribute("aria-label", __(type === "major" ? "新增大类" : "新增小类")); heading.append(add);
			const scroller = this.element("div", undefined, "jfe-inventory-category-scroll"), table = this.element("table"), head = this.element("thead"), body = this.element("tbody");
			table.append(head, body); scroller.append(table); const footer = this.element("div", undefined, "jfe-inventory-category-paging");
			const previous = this.button("上一页", () => this.load(type, Math.max(0, this.pages[type] - 1))), next = this.button("下一页", () => this.load(type, this.pages[type] + 1)), hint = this.element("span");
			footer.append(previous, hint, next); panel.append(heading, scroller, footer); grids.append(panel);
			this.panels[type] = { panel, head, body, scroller, add, previous, next, hint, hasMore: false };
		}
		const tools = this.element("div", undefined, "jfe-inventory-category-tools");
		this.resolveButton = this.button("核对保存结果", () => this.resolve()); this.discardButton = this.button("放弃修改", () => this.discard());
		this.unlinkedButton = this.button("待关联小类", () => this.showUnlinked()); tools.append(this.resolveButton, this.discardButton, this.unlinkedButton);
		this.host.append(toolbar, this.status, grids, tools); this.freeze();
	}
	async initialize() {
		try {
			const reply = await this.call("init_grid", { kind: this.kind }); if (this.disposed || reply.message?.kind !== this.kind) return false;
			this.meta = reply.message; this.status.textContent = __("请选择公司。"); this.freeze();
			const defaultCompany = frappe.defaults?.get_user_default?.("Company");
			if (typeof defaultCompany === "string" && defaultCompany) await this.companyControl.set_value(defaultCompany);
			return true;
		} catch { if (!this.disposed) this.status.textContent = __("类别页面未能加载，请重新打开。"); return false; }
	}
	async setCompany(value) { this.settingCompany = true; try { await this.companyControl.set_value(value); } finally { this.settingCompany = false; } }
	async changeCompany() {
		if (this.settingCompany || this.disposed) return;
		const desired = this.companyControl.get_value() || null; if (desired === this.company) return;
		if (this.getCloseBlock() || this.isDirty()) { await this.setCompany(this.company); frappe.msgprint(__(this.getCloseBlock() || "请先保存或放弃当前修改，再切换公司。")); return; }
		this.company = desired; this.revisions.scope++; this.revisions.major++; this.revisions.minor++;
		this.records.clear(); this.rows = { major: [], minor: [] }; this.selected = null; this.pages = { major: 0, minor: 0 }; this.meta = null;
		for (const panel of Object.values(this.panels)) { panel.loading = false; panel.hasMore = false; }
		this.render(); this.freeze(); if (!desired) return; const revision = this.revisions.scope;
		try {
			const reply = await this.call("init_grid", { kind: this.kind, company: desired });
			if (!this.currentScope(desired, revision) || reply.message?.company !== desired || reply.message?.kind !== this.kind) return;
			this.meta = reply.message; this.freeze(); await this.query();
		} catch { if (this.currentScope(desired, revision)) this.status.textContent = __("公司类别未能加载，请重新查询。"); }
	}
	currentScope(company, revision = this.revisions.scope) { return !this.disposed && this.company === company && this.revisions.scope === revision; }
	remember(row, type, parentKey = null, parentVersion = null) {
		let record = this.records.get(row.name);
		if (record && this.rowDirty(record)) { if (record.source.version !== row.version) record.conflict = true; return record; }
		record = { key: row.name, rowId: window.JifengInventoryCategory.uuid(), type, parentKey, parentVersion, source: structuredClone(row), values: structuredClone(row),
			meta: row.field_meta || {}, canWrite: !!row.can_write, canDelete: !!row.can_delete, blank: false, deleted: false };
		this.records.set(record.key, record); return record;
	}
	rowDirty(record) {
		if (record.deleted || record.association) return true; if (!record.source) return !record.blank && !!record.touched;
		return Object.keys(record.meta).some(field => !record.meta[field].read_only && String(record.values[field] ?? "") !== String(record.source[field] ?? ""));
	}
	isDirty() { return Array.from(this.records.values()).some(record => this.rowDirty(record)); }
	getCloseBlock() {
		if (this.unresolved) return "类别保存结果尚未确认，请先核对保存结果。"; if (this.busy) return "正在处理类别，请稍候。";
		if (this.confirming || this.chooserOpen) return "请先关闭类别选择窗口。"; return "";
	}
	async query() { if (this.busy || this.unresolved || !this.company || !this.meta) return; this.querySearch = this.search.value || ""; await this.load("major", 0); }
	async load(type, page = 0) {
		if (this.busy || this.unresolved || this.disposed || !this.company || !this.meta) return;
		const parentKey = type === "minor" ? this.selected : null, parent = this.records.get(parentKey);
		if (type === "minor" && !parent?.source) { this.rows.minor = []; this.pages.minor = 0; this.panels.minor.loading = this.panels.minor.hasMore = false; this.renderPanel("minor"); this.freeze(); return; }
		const revision = ++this.revisions[type], company = this.company; this.panels[type].loading = true; this.freeze(); this.panels[type].hint.textContent = __("正在查询…");
		try {
			const reply = await this.call("query_grid", { kind: this.kind, company, category: parent?.source?.name || null, search: type === "major" ? this.querySearch || "" : "", offset: page * 100, page_size: 100 }), data = reply.message;
			if (!this.currentScope(company) || revision !== this.revisions[type] || this.busy || this.unresolved || type === "minor" && this.selected !== parentKey) return;
			if (data?.kind !== this.kind || data.company !== company || data.record_type !== type || data.category !== (parent?.source?.name || null) || !Array.isArray(data.rows)) throw new Error("Wrong category scope");
			this.rows[type] = data.rows.map(row => this.remember(row, type, parentKey, data.category_version).key);
			this.pages[type] = page; this.panels[type].hasMore = !!data.has_more;
			if (type === "major") { const visible = this.visibleRows("major"); if (!visible.includes(this.selected)) this.selected = visible.find(key => !this.records.get(key).deleted) || null; this.rows.minor = []; this.renderPanel("minor"); this.renderPanel("major"); await this.load("minor", 0); }
			else this.renderPanel("minor"); this.updateStatus(); this.freeze();
		} catch { if (this.currentScope(company) && revision === this.revisions[type]) this.panels[type].hint.textContent = __("查询失败，请重新查询。"); }
		finally { if (!this.disposed && revision === this.revisions[type]) { this.panels[type].loading = false; this.freeze(); } }
	}
	visibleRows(type) {
		const keys = [...this.rows[type]];
		for (const record of this.records.values()) if (record.type === type && !record.blank && (!record.source || this.rowDirty(record)) &&
			(type === "major" || record.parentKey === this.selected) && !keys.includes(record.key)) keys.push(record.key); return keys;
	}
	newRow(type, blank = false) {
		if (!this.company || !this.meta?.can_create || type === "minor" && !this.selected) return null;
		const parent = this.records.get(this.selected); if (type === "minor" && (parent?.deleted || Number(parent?.values.inactive))) return null;
		const key = window.JifengInventoryCategory.uuid(), meta = this.meta[type + "_meta"];
		const record = { key, rowId: key, type, parentKey: type === "minor" ? this.selected : null, parentVersion: type === "minor" ? parent?.source?.version || null : null,
			source: null, meta, values: Object.fromEntries(Object.keys(meta).map(field => [field, field === "inactive" ? 0 : field === "conversion_value" ? "1" : ""])),
			canWrite: true, canDelete: true, deleted: false, blank, touched: !blank }; this.records.set(key, record); return record;
	}
	addRow(type) { if (this.getCloseBlock()) return; const record = this.newRow(type); if (!record) return; if (type === "major") this.select(record.key); this.renderPanel(type); this.updateStatus(); this.freeze(); }
	select(key) {
		if (this.getCloseBlock() || this.selected === key || this.records.get(key)?.deleted) return;
		this.selected = key; this.revisions.minor++; this.rows.minor = []; this.renderPanel("minor"); this.highlightSelection(); this.load("minor", 0); this.freeze();
	}
	highlightSelection() { for (const row of this.panels.major.body.children) row.setAttribute("aria-selected", String(row.dataset.key === this.selected)); }
	render() { this.renderPanel("major"); this.renderPanel("minor"); }
	renderPanel(type) {
		const panel = this.panels[type], scroll = { top: panel.scroller.scrollTop, left: panel.scroller.scrollLeft }; panel.head.replaceChildren(); panel.body.replaceChildren(); const heading = this.element("tr");
		heading.append(this.element("th", "", "jfe-inventory-row-number"));
		for (const field of this.columns[type]) heading.append(this.element("th", __(field === "value" ? type === "major" ? "大类" : "小类" : this.labels[field])));
		heading.append(this.element("th", __("删除"))); panel.head.append(heading); for (const key of this.visibleRows(type)) this.renderRow(this.records.get(key), panel.body);
		if (!this.busy && !this.unresolved && this.meta?.can_create && (type === "major" || this.selected)) {
			let blank = Array.from(this.records.values()).find(record => record.blank && record.type === type && (type === "major" || record.parentKey === this.selected));
			if (!blank) blank = this.newRow(type, true); if (blank) this.renderRow(blank, panel.body);
		}
		panel.scroller.scrollTop = scroll.top; panel.scroller.scrollLeft = scroll.left; panel.hint.textContent = type === "minor" && !this.selected ? __("请选择左侧大类") : __("第") + (this.pages[type] + 1) + __("页");
		panel.hint.parentElement && (panel.hint.parentElement.hidden = this.pages[type] === 0 && !panel.hasMore); this.highlightSelection();
	}
	renderRow(record, body) {
		const tr = this.element("tr"); tr.dataset.key = record.key; if (record.deleted) tr.classList.add("is-deleted"); if (record.blank) tr.classList.add("is-blank");
		const rowNumber = this.element("td", record.blank ? "" : this.pages[record.type] * 100 + body.children.length + 1, "jfe-inventory-row-number"); tr.append(rowNumber);
		if (record.type === "major") tr.addEventListener("click", () => this.select(record.key));
		for (const field of this.columns[record.type]) {
			const td = this.element("td"), meta = record.meta?.[field]; if (!meta) td.textContent = record.values[field] ?? "";
			else {
				const input = this.element("input"); input.type = field === "inactive" ? "checkbox" : "text"; input.dataset.rowKey = record.key; input.dataset.field = field;
				input.setAttribute("aria-label", __(field === "value" ? record.type === "major" ? "大类" : "小类" : this.labels[field]));
				if (input.type === "checkbox") input.checked = !!Number(record.values[field]); else input.value = record.values[field] ?? "";
				input.addEventListener(input.type === "checkbox" ? "change" : "input", () => {
					if (!this.editable(record, field)) return; record.values[field] = input.type === "checkbox" ? Number(input.checked) : input.value;
					record.blank = false; record.touched = true; rowNumber.textContent = this.pages[record.type] * 100 + Array.from(body.children).indexOf(tr) + 1;
					tr.classList.remove("is-blank"); this.updateStatus(); this.freeze();
				}); td.append(input);
			} tr.append(td);
		}
		const deletion = this.element("td"), button = this.button(record.deleted ? "撤销" : "×", () => this.toggleDelete(record)); button.dataset.deleteKey = record.key;
		button.setAttribute("aria-label", __(record.deleted ? "撤销删除" : "删除")); button.addEventListener("click", event => event.stopPropagation()); deletion.append(button); tr.append(deletion); body.append(tr);
	}
	editable(record, field) { return !this.getCloseBlock() && !this.panels[record.type].loading && record.canWrite && !record.deleted && !record.association && record.meta[field] && !record.meta[field].read_only; }
	freeze() {
		const locked = !!this.getCloseBlock(), scoped = !!(this.company && this.meta); if (!this.panels) return;
		this.saveButton.disabled = locked || !this.isDirty(); this.resolveButton.disabled = this.busy || !this.unresolved; this.discardButton.disabled = locked || !this.isDirty();
		this.queryButton.disabled = locked || !scoped; this.exportButton.disabled = locked || !scoped || !this.meta.can_export; this.importButton.disabled = locked || !scoped;
		this.pinyinButton.disabled = locked || !scoped || !Array.from(this.records.values()).some(record => record.canWrite && record.meta.pinyin && !record.meta.pinyin.read_only && !record.blank);
		this.unlinkedButton.disabled = locked || !scoped || !this.selected;
		for (const [type, panel] of Object.entries(this.panels)) {
			panel.add.disabled = locked || !this.meta?.can_create || type === "minor" && (!this.selected || this.records.get(this.selected)?.deleted || Number(this.records.get(this.selected)?.values.inactive));
			panel.previous.disabled = locked || !scoped || this.pages[type] === 0 || type === "minor" && !this.selected; panel.next.disabled = locked || !scoped || !panel.hasMore || type === "minor" && !this.selected;
			for (const input of panel.body.querySelectorAll("input[data-row-key]")) { const record = this.records.get(input.dataset.rowKey); input.disabled = !record || !this.editable(record, input.dataset.field); }
			for (const button of panel.body.querySelectorAll("button[data-delete-key]")) { const record = this.records.get(button.dataset.deleteKey); button.disabled = locked || !record?.canDelete || record.blank || record.association; }
		}
	}
	updateStatus() {
		const changed = Array.from(this.records.values()).filter(record => this.rowDirty(record));
		this.status.textContent = this.unresolved ? __("保存结果尚未确认，请核对保存结果。") : this.busy ? __("正在处理类别…") : changed.length ? changed.length + __("行未保存") : this.company ? __("点击大类查看小类，空白行可新增。") : __("请选择公司。");
	}
	toggleDelete(record) { if (this.getCloseBlock() || !record.canDelete || record.blank || record.association) return; record.deleted = !record.deleted; this.render(); this.updateStatus(); this.freeze(); }
	collectPayload() {
		const rows = [];
		for (const record of this.records.values()) {
			if (!this.rowDirty(record) || record.deleted && !record.source) continue;
			const row = { row_id: record.rowId, record_type: record.type, operation: record.deleted ? "delete" : record.association ? "associate" : "upsert" };
			if (record.source) Object.assign(row, { name: record.source.name, version: record.source.version });
			if (row.operation === "upsert") {
				row.values = Object.fromEntries(Object.keys(record.meta).filter(field => !record.meta[field].read_only && (!record.source || String(record.values[field] ?? "") !== String(record.source[field] ?? ""))).map(field => [field, record.values[field] ?? null]));
				if (!record.source && !String(record.values.value || "").trim()) throw new Error("请填写新增类别名称。");
			}
			if (record.type === "minor") {
				const parent = this.records.get(record.parentKey); if (!parent) throw new Error("小类缺少所属大类，请重新查询。");
				if (parent.deleted && !record.deleted) throw new Error("不能保存已删除大类的小类。");
				if (parent.source) Object.assign(row, { category: parent.source.name, category_version: record.parentVersion });
				else { if (parent.blank || !String(parent.values.value || "").trim()) throw new Error("请先填写小类所属大类名称。"); row.parent_row_id = parent.rowId; }
			} rows.push(row);
		}
		if (rows.length > 500) throw new Error("每次最多保存500行，请分批修改。"); return { kind: this.kind, company: this.company, request_id: window.JifengInventoryCategory.uuid(), rows };
	}
	validResult(result) {
		const candidate = this.candidate;
		if (!candidate || result?.status !== "applied" || result.request_id !== candidate.payload.request_id || result.digest !== candidate.digest || result.kind !== this.kind || result.company !== candidate.payload.company || !Array.isArray(result.rows)) return false;
		const expected = new Map(candidate.payload.rows.map(row => [row.row_id, row])); if (result.rows.length !== expected.size) return false;
		for (const row of result.rows) {
			const source = expected.get(row.row_id);
			if (!source || source.operation !== row.operation || typeof row.name !== "string" || !row.name || source.name && source.name !== row.name || row.operation !== "delete" && typeof row.version !== "string") return false;
			expected.delete(row.row_id);
		} return expected.size === 0;
	}
	async save() {
		if (this.getCloseBlock() || !this.isDirty()) return; let payload;
		try { payload = this.collectPayload(); } catch (error) { frappe.msgprint(__(error.message)); return; }
		if (!payload.rows.length) { this.discard(false); return; } this.busy = true; this.freeze(); this.updateStatus(); let posted = false;
		try {
			const preview = (await this.call("preview_batch", { payload })).message;
			if (preview?.request_id !== payload.request_id || preview.company !== payload.company || preview.kind !== payload.kind || typeof preview.digest !== "string" || preview.row_count !== payload.rows.length) throw new Error("Invalid category preview");
			this.candidate = { payload: structuredClone(payload), digest: preview.digest }; posted = true;
			const result = (await this.call("save_batch", { payload }, true)).message; if (!this.validResult(result)) this.unresolved = true; else this.accept(result);
		} catch (error) { this.unresolved = posted && !error?.responseJSON?.exc; }
		finally { this.busy = false; this.freeze(); this.updateStatus(); if (this.unresolved) frappe.msgprint(__("保存结果尚未确认。修改已保留，请核对保存结果。")); else if (posted && !this.isDirty()) await this.query(); }
	}
	accept(result) {
		this.revisions.major++; this.revisions.minor++; this.panels.major.loading = this.panels.minor.loading = false;
		for (const row of result.rows) {
			const record = Array.from(this.records.values()).find(item => item.rowId === row.row_id); if (!record) continue;
			if (row.operation === "delete") { this.records.delete(record.key); continue; }
			const oldKey = record.key; this.records.delete(oldKey); record.key = row.name; record.source = { ...structuredClone(record.values), name: row.name, version: row.version };
			record.deleted = record.association = record.touched = record.blank = false; this.records.set(record.key, record); if (this.selected === oldKey) this.selected = record.key;
			for (const child of this.records.values()) if (child.parentKey === oldKey) { child.parentKey = record.key; child.parentVersion = row.version; }
		}
		for (const record of [...this.records.values()]) if (record.deleted && !record.source || record.blank) this.records.delete(record.key);
		this.unresolved = false; this.candidate = null; frappe.show_alert({ message: __("类别已保存"), indicator: "green" });
	}
	async resolve() {
		if (!this.unresolved || this.busy || !this.candidate) return; this.busy = true; this.freeze();
		try {
			const payload = this.candidate.payload, result = (await this.call("resolve_batch", { request_id: payload.request_id, kind: payload.kind, company: payload.company, digest: this.candidate.digest })).message;
			if (this.validResult(result)) this.accept(result); else frappe.msgprint(__("尚未核对到这次保存结果，请保留修改并稍后核对。"));
		} catch { /* Resolving never repeats the POST. */ }
		finally { this.busy = false; this.freeze(); this.updateStatus(); if (!this.unresolved) await this.query(); }
	}
	discard(confirm = true) {
		if (this.getCloseBlock()) return;
		const discard = () => { this.records.clear(); this.rows = { major: [], minor: [] }; this.selected = null; this.render(); this.freeze(); this.query(); };
		if (!confirm || !this.isDirty()) { discard(); return; } this.confirming = true; this.freeze();
		frappe.confirm(__("放弃两张表中全部未保存的修改？"), () => { this.confirming = false; discard(); }, () => { this.confirming = false; this.freeze(); });
	}
	chooseColumns() {
		if (this.getCloseBlock()) return;
		const dialog = new frappe.ui.Dialog({ title: __("列选项"), fields: this.optional.map(field => ({ fieldname: field, fieldtype: "Check", label: __(this.labels[field]), default: Number(this.columns.minor.includes(field)) })),
			primary_action_label: __("确定"), primary_action: () => { const values = dialog.get_values(); if (!values) return;
				this.columns.minor = this.columns.minor.filter(field => !this.optional.includes(field)); this.columns.minor.push(...this.optional.filter(field => values[field])); this.renderPanel("minor"); this.freeze(); dialog.hide(); } }); dialog.show();
	}
	async updatePinyin() {
		if (this.getCloseBlock() || !this.company) return;
		const records = Array.from(this.records.values()).filter(record => !record.deleted && !record.blank && record.values.value && record.canWrite && record.meta.pinyin && !record.meta.pinyin.read_only && !record.association);
		if (!records.length) return; if (records.length > 500) { frappe.msgprint(__("每次最多更新500行简拼。")); return; } this.busy = true; this.freeze(); this.updateStatus();
		try {
			const data = (await this.call("preview_pinyin", { kind: this.kind, company: this.company, rows: records.map(record => ({ row_id: record.rowId, value: record.values.value })) })).message;
			if (data?.company !== this.company || data.kind !== this.kind || !Array.isArray(data.rows)) return;
			for (const row of data.rows) { const record = records.find(item => item.rowId === row.row_id); if (record && record.values.value === row.value && typeof row.pinyin === "string") { record.values.pinyin = row.pinyin; record.touched = true; } } this.render();
		} catch { /* Native RPC shows failures and the current drafts remain. */ }
		finally { this.busy = false; this.freeze(); this.updateStatus(); }
	}
	async showUnlinked() {
		if (this.getCloseBlock() || !this.selected || !this.records.get(this.selected)?.source) return;
		const parentKey = this.selected, parent = this.records.get(parentKey), company = this.company; this.chooserOpen = true; this.freeze();
		const dialog = new frappe.ui.Dialog({ title: __("待关联小类"), fields: [{ fieldname: "results", fieldtype: "HTML" }] });
		const host = dialog.fields_dict.results.$wrapper.get(0); let closed = false, revision = 0, page = 0;
		dialog.on_hide = () => { closed = true; this.chooserOpen = false; this.freeze(); };
		const load = async () => {
			const request = ++revision; host.replaceChildren(this.element("p", __("正在查询…")));
			try {
				const data = (await this.call("query_grid", { kind: this.kind, company, unlinked: 1, offset: page * 100 })).message;
				if (closed || this.disposed || request !== revision || this.company !== company) return; host.replaceChildren(); if (!data?.rows?.length) host.append(this.element("p", __("没有待关联小类。")));
				for (const row of data.rows || []) {
					const line = this.element("div", undefined, "jfe-inventory-unlinked-row"), action = this.button("关联当前大类", () => {
						if (closed || this.busy || this.unresolved || this.company !== company || !row.can_write) return;
						const record = this.remember(row, "minor", parentKey, parent.source.version); record.association = true; record.blank = false; dialog.hide(); this.renderPanel("minor"); this.updateStatus(); this.freeze();
					}); action.disabled = !row.can_write; line.append(this.element("span", row.value), action); host.append(line);
				} const previous = this.button("上一页", () => { page--; load(); }), next = this.button("下一页", () => { page++; load(); }); previous.disabled = page === 0; next.disabled = !data.has_more; host.append(previous, next);
			} catch { if (!closed && request === revision) host.replaceChildren(this.element("p", __("查询失败，请重新打开。"))); }
		}; dialog.show(); await load();
	}
	async exportFile() { return window.JifengInventoryCategoryIO.exportFile(this); }
	async importFile() { return window.JifengInventoryCategoryIO.importFile(this); }
	setVisible(visible) { this.visible = visible; }
	destroy() { this.disposed = true; this.revisions.scope++; this.revisions.major++; this.revisions.minor++; window.removeEventListener("beforeunload", this.beforeUnload); }
};
