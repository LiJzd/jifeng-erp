/* Material shapes reuse the established dictionary editor and draft guards. */
window.JifengInventoryWords = class JifengInventoryWords extends window.JifengProductDictionary {
	constructor(host) {
		super(host, "形状", { api: "jifeng_erp.inventory_words", title: "材料形状", editorSuffix: true,
			hideAbbreviation: true, editorFields: ["legacy_number", "value", "remarks", "inactive"] });
	}
	call(method, args, write = false) {
		return frappe.call({ method: this.dictionaryApi + "." + method, args,
			...(write ? { freeze: true, freeze_message: __("正在保存材料形状…") } : { type: "GET" }) });
	}
	build() {
		this.offset = 0; this.wordBusy = false; this.wordPending = null; this.loadedScope = null; this.currentCompany = null;
		this.host.classList.add("jfe-product-dictionary", "jfe-inventory-word"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-product-dictionary-toolbar");
		this.addButton = this.button("新增", () => this.openEditor()); this.pinyinButton = this.button("更新简拼", () => this.updatePinyin());
		const companyHost = this.element("div", undefined, "jfe-product-dictionary-company");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), reqd: true, onchange: () => this.changeScope() } });
		this.search = this.element("input"); this.search.type = "search"; this.search.maxLength = 140;
		this.search.placeholder = __("输入查询的关键字"); this.search.setAttribute("aria-label", __("形状关键词"));
		this.search.addEventListener("input", () => { this.revision++; this.updateControls(); });
		this.search.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.query(0); } });
		this.queryButton = this.button("查询", () => this.query(0));
		const format = this.element("select"); format.setAttribute("aria-label", __("打印格式"));
		const option = this.element("option", __("格式1")); option.value = "1"; format.append(option);
		this.printButton = this.button("打印", () => this.print());
		this.preview = this.element("input"); this.preview.type = "checkbox"; this.preview.checked = true;
		const previewLabel = this.element("label", undefined, "jfe-product-dictionary-inactive"); previewLabel.append(this.preview, this.element("span", __("预览")));
		toolbar.append(this.addButton, this.pinyinButton, companyHost, this.search, this.queryButton, format, this.printButton, previewLabel);
		this.status = this.element("p", __("正在读取形状…"), "jfe-product-dictionary-status"); this.status.setAttribute("role", "status");
		this.scroller = this.element("div", undefined, "jfe-product-dictionary-scroll");
		const table = this.element("table", undefined, "jfe-product-dictionary-table"); table.setAttribute("aria-label", __("材料形状"));
		const head = this.element("thead"), headings = this.element("tr");
		for (const label of ["", "编号", "名称", "简拼", "备注", "停用", "修改", "删除"]) {
			const cell = this.element("th", __(label)); cell.scope = "col"; headings.append(cell);
		}
		head.append(headings); this.tbody = this.element("tbody"); table.append(head, this.tbody); this.scroller.append(table);
		const footer = this.element("div", undefined, "jfe-product-dictionary-toolbar jfe-inventory-word-pager");
		this.previous = this.button("上页", () => this.query(Math.max(0, this.offset - 100)));
		this.next = this.button("下页", () => this.query(this.offset + 100)); this.count = this.element("span");
		this.resolveButton = this.button("核对保存结果", () => this.resolveOperation());
		footer.append(this.previous, this.next, this.count, this.resolveButton); this.host.append(toolbar, this.status, this.scroller, footer);
		this.updateControls();
	}
	async initialize() {
		try {
			const response = await this.call("init_dictionary", { dictionary_type: this.type });
			if (this.disposed || response.message?.dictionary_type !== this.type || !response.message.field_meta) return false;
			this.readyArchive = response.message; this.status.textContent = __("请选择公司。"); this.updateControls();
			const company = frappe.defaults?.get_user_default?.("Company");
			if (typeof company === "string" && company) await this.companyControl.set_value(company);
			return true;
		} catch { if (!this.disposed) this.status.textContent = __("形状页面未能加载，请重新打开。"); return false; }
	}
	getCloseBlock() { return this.wordPending ? "保存结果尚未确认，请先核对。" : this.wordBusy ? "正在读取或保存形状，请稍候。" : super.getCloseBlock(); }
	updateAddButton() { this.updateControls(); }
	updateControls() {
		const blocked = !!(this.wordBusy || this.wordPending || this.opening || this.editor?.busy || this.editor?.unresolved || this.editor?.confirming);
		const dirty = this.isDirty(), editorOpen = this.editor && !this.editor.closed;
		if (this.addButton) this.addButton.disabled = blocked || !this.company() || !this.readyArchive?.can_write;
		if (this.pinyinButton) this.pinyinButton.disabled = blocked || dirty || editorOpen || !this.company() || !this.readyArchive?.can_pinyin;
		if (this.queryButton) this.queryButton.disabled = blocked || dirty || !this.company();
		if (this.printButton) this.printButton.disabled = blocked || dirty || !this.readyArchive?.can_print || !this.rows?.length || !this.loadedScope || this.loadedScope.search !== this.search.value;
		if (this.previous) this.previous.disabled = blocked || dirty || !this.loadedScope || this.offset === 0;
		if (this.next) this.next.disabled = blocked || dirty || !this.loadedScope || !this.hasMore;
		if (this.resolveButton) this.resolveButton.disabled = this.wordBusy || !this.wordPending;
	}
	async openEditor(name) { if (!this.wordBusy && !this.wordPending) return super.openEditor(name); }
	guardClose(state) {
		super.guardClose(state);
		const onHide = state.dialog.on_hide;
		state.dialog.on_hide = () => { onHide(); if (!this.disposed) this.updateControls(); };
	}
	async changeScope() {
		if (this.restoringCompany || this.disposed) return;
		const desired = this.company(); if (desired === this.currentCompany) return;
		if (this.getCloseBlock() || this.isDirty()) {
			this.restoringCompany = true; try { await this.companyControl.set_value(this.currentCompany); } finally { this.restoringCompany = false; }
			frappe.msgprint(__(this.getCloseBlock() || "请先保存或放弃形状草稿，再切换公司。")); return;
		}
		this.currentCompany = desired; this.revision++; this.rows = []; this.offset = 0; this.loadedScope = null; this.tbody.replaceChildren(); this.updateControls();
		if (!desired) return; const sequence = this.revision;
		try {
			const response = await this.call("init_dictionary", { dictionary_type: this.type, company: desired });
			if (this.disposed || this.revision !== sequence || this.company() !== desired || response.message?.company !== desired) return;
			this.readyArchive = response.message; this.updateControls(); await this.query(0);
		} catch { if (!this.disposed && this.revision === sequence) this.status.textContent = __("形状权限读取失败，请重新选择公司。"); }
	}
	async query(offset = this.offset) {
		if (this.disposed || !this.readyArchive || this.getCloseBlock() || this.isDirty()) return;
		const company = this.company(), search = this.search.value || "", sequence = ++this.revision;
		this.loadedScope = null; this.rows = []; this.tbody.replaceChildren(); this.updateControls(); if (!company) return;
		try {
			const response = await this.call("query_dictionary", { company, dictionary_type: this.type, search, offset });
			if (this.disposed || sequence !== this.revision || this.company() !== company || this.search.value !== search) return;
			const data = response.message;
			if (data?.company !== company || data.dictionary_type !== this.type || data.offset !== offset || !Array.isArray(data.rows) ||
				data.rows.some(row => !row?.name || !row.version || row.company !== company || row.dictionary_type !== this.type)) throw new Error("Wrong shape scope");
			this.rows = data.rows; this.offset = offset; this.hasMore = data.has_more; this.loadedScope = { company, search };
			this.rows.forEach((row, index) => this.renderRow(row, index));
			this.count.textContent = __("第") + (Math.floor(offset / 100) + 1) + __("页") + " · " + (offset + this.rows.length) + (this.hasMore ? "+" : "") + __("条");
			this.status.textContent = this.rows.length ? "" : __("没有可见记录。"); this.updateControls();
		} catch { if (!this.disposed && sequence === this.revision) this.status.textContent = __("形状查询失败，请重试。"); }
	}
	renderRow(row, index) {
		const tr = this.element("tr"); tr.append(this.element("td", this.offset + index + 1));
		for (const field of ["legacy_number", "value", "abbreviation", "remarks"]) tr.append(this.element("td", row[field] ?? ""));
		const inactive = this.element("td"), check = this.element("input"); check.type = "checkbox"; check.checked = !!Number(row.inactive); check.disabled = true;
		check.setAttribute("aria-label", __("停用")); inactive.append(check); tr.append(inactive);
		const edit = this.element("td"), remove = this.element("td");
		const editButton = this.button(row.can_write ? "✎" : "查看", () => this.openEditor(row.name)); editButton.setAttribute("aria-label", __(row.can_write ? "修改" : "查看") + " " + row.value); edit.append(editButton);
		const deleteButton = this.button("×", () => this.deleteRow(row)); deleteButton.setAttribute("aria-label", __("删除") + " " + row.value); deleteButton.disabled = !row.can_delete; remove.append(deleteButton);
		tr.append(edit, remove); this.tbody.append(tr);
	}
	numberPolicy(archive) { return { read_only: true, value: archive.name ? archive.legacy_number ?? null : archive.number_preview ?? null,
		description: archive.name ? "" : __("暂定编号，以保存后编号为准。") }; }
	payload(state) {
		const payload = super.payload(state);
		if (typeof payload.value === "string") payload.value = payload.value.trim();
		if (!payload.request_id) payload.request_id = state.wordRequestId ||= this.requestID();
		return payload;
	}
	freeze(state) { super.freeze(state); this.updateControls(); }
	matches(state, archive) { return super.matches(state, archive) && archive.change_request_id === state.candidate?.request_id; }
	async accept(state, archive, message) { state.wordRequestId = null; await super.accept(state, archive, message); }
	async save(state) {
		if (state.closed || state.busy || state.unresolved || state.confirming || !state.archive.can_write || !state.dialog.get_values()) return;
		state.candidate = structuredClone(this.payload(state)); state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		let dispatched = false;
		try {
			state.digest = await this.prepare(state.candidate);
			dispatched = true; const response = await this.call("save_dictionary", { payload: state.candidate }, true);
			const archive = response.message?.archives?.[0];
			if (!this.matches(state, archive)) { state.unresolved = true; return; }
			await this.accept(state, archive, "材料形状已保存");
		} catch (error) { state.unresolved = dispatched && !this.definiteFailure(error); }
		finally { state.busy = false; this.freeze(state); if (!state.unresolved) this.query(); else frappe.msgprint(__("保存结果尚未确认，请核对保存结果。")); }
	}
	async prepare(payload) {
		const response = await this.call("preview_change", { payload }); const data = response.message;
		if (data?.request_id !== payload.request_id || data.company !== payload.company || data.dictionary_type !== this.type || !/^[0-9a-f]{64}$/.test(data.digest)) throw new Error("Incomplete shape preview");
		return data.digest;
	}
	async resolve(state) {
		if (state.closed || state.busy || state.confirming || !state.unresolved || !state.candidate || !state.digest) return;
		state.busy = true; this.freeze(state);
		try {
			const response = await this.call("resolve_change", { request_id: state.candidate.request_id, company: state.company, dictionary_type: this.type, digest: state.digest });
			const archive = response.message?.archives?.[0];
			if (response.message?.status === "applied" && this.matches(state, archive)) await this.accept(state, archive, "已核对保存结果");
			else { state.conflict = this.validArchive(state, archive) ? archive : null; state.review.replaceChildren(this.element("p", __("未核对到本次完整保存结果，请保留草稿；可核对后明确采用当前值。"))); }
		} catch { /* Keep the original request and draft; never resubmit. */ }
		finally { state.busy = false; this.freeze(state); if (!state.unresolved) this.query(); }
	}
	async deleteRow(row) {
		if (!row.can_delete || this.getCloseBlock() || this.isDirty() || row.company !== this.company()) return;
		const payload = { company: row.company, dictionary_type: this.type, request_id: this.requestID(), operation: "delete", name: row.name, version: row.version };
		frappe.confirm(__("删除该材料形状？") + " " + row.value, () => this.operation(payload));
	}
	async updatePinyin() {
		if (this.getCloseBlock() || this.isDirty() || !this.readyArchive?.can_pinyin) return;
		this.wordBusy = true; this.updateControls(); const company = this.company();
		try {
			const response = await this.call("preview_pinyin", { company, dictionary_type: this.type });
			if (this.disposed || this.company() !== company || response.message?.company !== company || response.message.dictionary_type !== this.type || !Array.isArray(response.message.rows)) return;
			if (!response.message.rows.length) { frappe.msgprint(__("没有可更新的形状。")); return; }
			this.wordBusy = false;
			await this.operation({ company, dictionary_type: this.type, request_id: this.requestID(), operation: "pinyin", rows: response.message.rows });
		} catch { frappe.msgprint(__("简拼预览未能完成，请重试。")); }
		finally { this.wordBusy = false; this.updateControls(); }
	}
	async operation(payload) {
		if (this.wordBusy || this.wordPending || this.disposed || this.company() !== payload.company || this.editor && !this.editor.closed) return;
		this.wordBusy = true; this.updateControls(); let dispatched = false;
		try {
			const digest = await this.prepare(payload); this.wordPending = { payload: structuredClone(payload), digest };
			dispatched = true; const response = await this.call("save_dictionary", { payload }, true);
			if (!this.validOperation(response.message)) throw new Error("Incomplete shape receipt");
			this.wordPending = null; frappe.show_alert({ message: __("材料形状已更新"), indicator: "green" });
		} catch (error) { if (!dispatched || this.definiteFailure(error)) this.wordPending = null; }
		finally { this.wordBusy = false; this.updateControls(); if (!this.wordPending) this.query(); else frappe.msgprint(__("操作结果尚未确认，请先核对保存结果。")); }
	}
	validOperation(result) {
		const pending = this.wordPending;
		if (!pending || result?.status !== "applied" || result.request_id !== pending.payload.request_id || result.digest !== pending.digest ||
			result.company !== pending.payload.company || result.dictionary_type !== this.type) return false;
		if (pending.payload.operation === "delete") return result.deleted?.length === 1 && result.deleted[0] === pending.payload.name;
		return result.archives?.length === pending.payload.rows.length && pending.payload.rows.every(row => result.archives.some(archive => archive.name === row.name &&
			archive.abbreviation === row.abbreviation && archive.company === pending.payload.company && archive.dictionary_type === this.type && archive.change_request_id === pending.payload.request_id));
	}
	async resolveOperation() {
		if (this.wordBusy || !this.wordPending) return;
		this.wordBusy = true; this.updateControls();
		try {
			const pending = this.wordPending, response = await this.call("resolve_change", { request_id: pending.payload.request_id,
				company: pending.payload.company, dictionary_type: this.type, digest: pending.digest });
			if (this.validOperation(response.message)) { this.wordPending = null; frappe.show_alert({ message: __("已核对保存结果"), indicator: "green" }); }
		} catch { /* A missing/unknown receipt never authorizes another POST. */ }
		finally { this.wordBusy = false; this.updateControls(); if (!this.wordPending) this.query(); }
	}
	async print() {
		if (this.getCloseBlock() || this.isDirty() || !this.rows.length || !this.loadedScope || this.loadedScope.search !== this.search.value) return;
		const company = this.company(), revision = this.revision, rows = this.rows.map(row => ({ name: row.name, version: row.version }));
		this.wordBusy = true; this.updateControls();
		try {
			const response = await this.call("print_dictionary", { company, dictionary_type: this.type, rows }); const data = response.message;
			if (this.disposed || this.revision !== revision || this.company() !== company || data?.company !== company || data.dictionary_type !== this.type || data.row_count !== rows.length || typeof data.html !== "string") return;
			const dialog = new frappe.ui.Dialog({ title: __("材料形状 · 格式1"), size: "extra-large", fields: [{ fieldname: "preview", fieldtype: "HTML" }] });
			const frame = this.element("iframe"); frame.title = __("材料形状打印预览"); frame.setAttribute("sandbox", "allow-same-origin allow-modals");
			frame.style.cssText = "width:100%;height:65vh;border:0"; frame.srcdoc = data.html;
			dialog.fields_dict.preview.$wrapper.get(0).append(frame); dialog.set_primary_action(__("打印"), () => frame.contentWindow?.print());
			if (!this.preview.checked) frame.addEventListener("load", () => frame.contentWindow?.print(), { once: true }); dialog.show();
		} catch { frappe.msgprint(__("打印预览未能完成，请重新查询后重试。")); }
		finally { this.wordBusy = false; this.updateControls(); }
	}
};
