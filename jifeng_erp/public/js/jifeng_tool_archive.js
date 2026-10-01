/* Classic mold/plate entry, with native field permissions and exact private files. */
window.JifengToolArchive = class JifengToolArchive {
	constructor(host, recordType, bridge = null) {
		if (!["mold", "plate", "modeling"].includes(recordType)) throw new Error("Unknown archive type");
		this.host = host; this.recordType = recordType; this.bridge = bridge; this.rows = []; this.revision = 0;
		this.modeling = recordType === "modeling"; this.imageField = this.modeling ? "design_image" : "image"; this.imageLabel = this.modeling ? "设计原图" : "档案图片";
		this.disposed = false; this.opening = false; this.editor = null; this.companyScope = null; this.changingCompany = false;
		this.title = this.modeling ? "版种建模" : recordType === "mold" ? "模具录入" : "版种录入"; this.doctype = this.modeling ? "Jifeng Plate Modeling" : recordType === "mold" ? "Jifeng Mold" : "Jifeng Plate";
		this.identity = this.modeling ? "model_number" : recordType === "mold" ? "mold_no" : "plate_no"; this.inactive = this.modeling ? null : recordType === "mold" ? "scrapped" : "retired";
		this.fields = this.modeling ? [["legacy_number", "编号"], ["model_number", "建模号"], ["product_code", "产品编号"], ["reference_unit_price", "单价"],
			["modeler_text", "建模"], ["model_date", "日期"], ["remarks", "备注"], ["legacy_copy", "复制"]] : recordType === "mold" ? [["legacy_number", "编号"], ["mold_no", "模具"], ["product_code", "产品"], ["quantity", "数量"],
			["maker", "压模"], ["mold_date", "日期"], ["amount", "金额"], ["remark", "说明"], ["location", "库位"], ["scrapped", "报废"], ["legacy_copy", "复制"]] :
			[["legacy_number", "编号"], ["plate_type", "类型"], ["product_code", "产品"], ["assessment_score", "考核分"], ["maker", "制版"],
				["quantity", "数量"], ["notes", "说明"], ["location", "库位"], ["retired", "报废"], ["legacy_copy", "复制"]];
		this.labels = { ...Object.fromEntries(this.fields), company: "公司", plate_no: "业务版种编号", item_code: "关联商品", copy_from: "复制来源",
			scrap_date: "报废日期", scrap_reason: "报废原因", retired_date: "报废日期", retired_reason: "报废原因", image: this.imageLabel, plate: "关联版种档案", expense_voucher: "实际费用来源" };
		this.build(); this.beforeUnload = (event) => { if (this.isDirty() || this.getCloseBlock()) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener("beforeunload", this.beforeUnload); this.ready = Promise.resolve(true);
	}
	element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text ?? ""); if (className) node.className = className; return node; }
	button(label, action, reason) { const node = this.element("button", __(label), "btn btn-default btn-sm"); node.type = "button";
		if (reason) { node.disabled = true; node.title = __(reason); } else node.addEventListener("click", action); return node; }
	async call(method, args, write = false) {
		let wire = method === "save_record" ? args : { record_type: this.recordType, ...args };
		if (this.modeling) { wire = { ...args }; delete wire.include_inactive; delete wire.fieldname;
			if (method === "save_record") { const payload = args.payload, values = { ...payload.values }; if ("image" in values) { values.design_image = values.image; delete values.image; }
				wire.payload = { ...values, ...(payload.name ? { name: payload.name, version: payload.version } : { request_id: payload.request_id }) }; }
		}
		const response = await frappe.call({ method: "jifeng_erp." + (this.modeling ? "plate_modeling." : "tool_archive.") + method, args: wire,
			...(write ? { type: "POST", freeze: true, freeze_message: __("正在保存档案…") } : {}) });
		if (!this.modeling || method === "list_image_attachments") return response;
		if (method === "resolve_creation") return { ...response, message: response.message?.status === "found" ? { status: "found", archive: this.adaptModeling(response.message.archive, { requestId: args.request_id, company: args.company }) } : response.message };
		return { ...response, message: Array.isArray(response.message) ? response.message.map((row) => this.adaptModeling(row)) : this.adaptModeling(response.message) };
	}
	adaptModeling(raw, receipt = null) {
		if (!raw || typeof raw !== "object") return null; const allowed = ["company", ...this.fields.map(([field]) => field), "item_code", "plate", "expense_voucher"];
		const values = Object.fromEntries(allowed.filter((field) => field in raw).map((field) => [field, raw[field]])); if ("design_image" in raw) values.image = raw.design_image;
		const metadata = { ...raw.field_meta }; if (metadata.design_image) { metadata.image = metadata.design_image; delete metadata.design_image; }
		return { record_type: "modeling", name: raw.name, version: raw.version, values, field_meta: metadata, image_states: raw.image_state ? { image: raw.image_state } : {},
			can_write: raw.can_write === true, can_select: false, receipt_scope: receipt, link_states: { item_code: raw.item_code_state, plate: raw.plate_state, expense_voucher: raw.expense_voucher_state } };
	}
	build() {
		this.host.classList.add("jfe-tool-archive"); this.host.replaceChildren(); const toolbar = this.element("div", undefined, "jfe-tool-toolbar"), companyHost = this.element("div");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), onchange: () => this.changeScope() } });
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140; this.search.placeholder = __("关键词"); this.search.setAttribute("aria-label", __("关键词"));
		this.search.addEventListener("input", () => { this.revision++; this.status.textContent = __("关键词已改变，请查询。"); });
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(); } });
		this.includeInactive = this.element("input"); this.includeInactive.type = "checkbox"; this.includeInactive.addEventListener("change", () => this.query());
		const inactive = this.element("label"); inactive.hidden = this.modeling; inactive.append(this.includeInactive, this.element("span", __("包含报废")));
		toolbar.append(this.button("新增", () => this.openEditor()), this.button("导出", null, "导出暂不可用"), this.button("导入", null, "导入暂不可用"),
			companyHost, this.search, this.button("查询", () => this.query()), inactive, this.button("打印", null, "打印暂不可用"));
		this.status = this.element("p", __("请选择公司后查询，或新增档案。"), "jfe-tool-status"); this.status.setAttribute("role", "status");
		this.scroller = this.element("div", undefined, "jfe-tool-scroll"); const table = this.element("table", undefined, "jfe-tool-table"), head = this.element("tr"), thead = this.element("thead");
		for (const label of [...this.fields.map((row) => row[1]), this.imageLabel, "操作"]) head.append(this.element("th", __(label)));
		thead.append(head); this.tbody = this.element("tbody"); table.append(thead, this.tbody); this.scroller.append(table); this.host.append(toolbar, this.status, this.scroller);
	}
	async changeScope() {
		if (this.changingCompany || this.disposed) return; const next = this.companyControl.get_value() || null; if (next === this.companyScope) return;
		this.changingCompany = true; try { await this.companyControl.set_value(this.companyScope); } finally { this.changingCompany = false; }
		return this.discardFor(async () => { this.closeEditor(); this.companyScope = next; this.rows = []; this.tbody.replaceChildren(); this.revision++;
			this.changingCompany = true; try { await this.companyControl.set_value(next); } finally { this.changingCompany = false; } await this.query(); });
	}
	async query() {
		if (this.disposed) return; const company = this.companyScope, search = this.search.value, inactive = !!this.includeInactive.checked, sequence = ++this.revision;
		if (!company) { this.status.textContent = __("请选择公司后查询。"); return; }
		const scroll = [this.scroller.scrollTop, this.scroller.scrollLeft]; this.status.textContent = __("正在查询…");
		try { const response = await this.call("list_records", { company, search, include_inactive: inactive ? 1 : 0 });
			if (this.disposed || sequence !== this.revision || company !== this.companyScope || search !== this.search.value || inactive !== !!this.includeInactive.checked) return;
			if (!Array.isArray(response.message) || response.message.some((row) => !this.validArchive(row, company) || !row.name)) throw new Error("Wrong list scope");
			this.rows = response.message; this.renderRows(); [this.scroller.scrollTop, this.scroller.scrollLeft] = scroll;
			this.status.textContent = this.rows.length ? __("查询完成") + " · " + this.rows.length + __("条") : __("没有可见记录。");
		} catch { if (!this.disposed && sequence === this.revision) this.status.textContent = __("查询未完成，原列表已保留；记录超过100条时请缩小关键词后查询。"); }
	}
	renderRows() { this.tbody.replaceChildren(); for (const row of this.rows) { const tr = this.element("tr");
		for (const [field] of this.fields) tr.append(this.element("td", field === "legacy_copy" || field === this.inactive ? row.values[field] ? __("是") : __("否") : row.values[field] ?? ""));
		const picture = this.element("td"), info = row.image_states.image;
		if (info?.state === "已上传" && this.safePreview(info.url)) { const img = this.element("img"); img.src = info.url; img.alt = __(this.imageLabel); picture.append(img); } else picture.textContent = info?.state || "";
		const action = this.element("td"); action.append(this.button(row.can_write ? "修改" : "查看", () => this.openEditor(row.name))); tr.append(picture, action); this.tbody.append(tr); } }
	validArchive(archive, company = null, name = null) { return !!(archive?.record_type === this.recordType && archive.values && archive.field_meta && archive.image_states &&
		typeof archive.can_write === "boolean" && typeof archive.can_select === "boolean" && (!company || archive.values.company === company) && (!name || archive.name === name) &&
		(archive.name ? typeof archive.name === "string" && typeof archive.version === "string" && !!archive.version : !archive.version) &&
		(this.modeling ? ["company"] : ["company", this.identity, this.inactive]).every((field) => field in archive.values && (this.modeling || archive.field_meta[field]))); }
	async openEditor(name = null) {
		if (this.disposed || this.opening) return; if (this.editor && !this.editor.closed) { this.editor.dialog.show(); return this.editor.dialog; }
		this.opening = true; const company = this.companyScope, sequence = this.revision;
		try { const response = await this.call(name ? "read_record" : "init_record", name ? { name } : { company });
			if (this.disposed || sequence !== this.revision || company !== this.companyScope || !this.validArchive(response.message, company, name) || !response.message.field_meta.company || !name && response.message.name) throw new Error("Wrong editor scope");
			const dialog = new frappe.ui.Dialog({ title: __(this.title + (name ? "" : "新增")), size: "extra-large", no_submit_on_enter: true, fields: [{ fieldname: "archive_layout", fieldtype: "HTML" }] });
			dialog.$wrapper.addClass("jfe-tool-editor"); const state = { dialog, archive: response.message, requestId: name ? null : this.requestID(), closed: false,
				busy: false, confirming: false, unresolved: false, candidate: null, conflict: null, controls: {}, nativeControls: {}, cells: {}, upload: null, uploadUnresolved: false };
			this.editor = state; this.buildEditor(state); await this.applyArchive(state, response.message); this.guardClose(state); dialog.show(); this.freeze(state); return dialog;
		} catch { const failed = this.editor; if (failed && !failed.nativeHide) { failed.closed = true; failed.dialog.hide(); this.editor = null; }
			if (!this.disposed) this.status.textContent = __("档案未能打开，请重试。"); } finally { this.opening = false; }
	}
	buildEditor(state) {
		const host = state.dialog.fields_dict.archive_layout.$wrapper.get(0); host.replaceChildren(); state.message = this.element("p", "", "jfe-tool-message"); state.message.setAttribute("role", "status");
		state.scope = this.element("p"); state.form = this.element("div", undefined, "jfe-tool-fields");
		for (const [field, label] of this.fields) { const cell = this.element("div", undefined, "jfe-tool-field"); cell.append(this.element("label", __(label))); state.cells[field] = cell;
			if (field === "maker") { state.nativeControls[field] = this.nativeControl(cell, field, "", "Link", "Employee", () => this.freeze(state), true);
				state.nativeControls[field].get_query = () => ({ filters: { company: state.company } }); }
			else { const input = this.element("input"); input.type = [this.inactive, "legacy_copy"].includes(field) ? "checkbox" : "text"; input.setAttribute("aria-label", __(label));
				if (!["remark", "notes"].includes(field) && input.type === "text") input.maxLength = 140;
				input.readOnly = ["legacy_number", "mold_date", "model_date"].includes(field); input.addEventListener("input", () => this.freeze(state)); input.addEventListener("change", () => this.freeze(state));
				state.controls[field] = input; cell.append(input); }
			if (field === "plate_type") cell.append(this.button("选择类型", null, "类型词典暂不可选，可填写类型原文"));
			state.form.append(cell); }
		state.layout = this.element("div", undefined, "jfe-tool-layout"); state.imageCell = this.element("div", undefined, "jfe-tool-image"); this.buildImage(state); state.layout.append(state.form, state.imageCell);
		const extra = this.element("section", undefined, "jfe-tool-extra"); extra.append(this.element("strong", __("业务关联与原文维护")),
			this.element("p", __(this.modeling ? "编号、建模号、产品编号各自保存，不自动生成旧编号或匹配商品、员工。费用来源仅作追溯，当前状态不代表已付款。" : this.recordType === "mold" ? "上方模具是业务模具号，必须填写且唯一；编号原文在这里维护，不自动带入。" : "业务版种编号必须明确填写且唯一；类型、产品、编号原文各自保存，不自动带入。")));
		const grid = this.element("div", undefined, "jfe-tool-extra-grid"); state.companyControl = this.nativeControl(grid, "company", "公司", "Link", "Company", () => this.changeEditorCompany(state));
		const additional = this.modeling ? [["legacy_number", "编号原文", "Data", ""], ["item_code", "关联商品", "Link", "Item"], ["plate", "关联版种档案", "Link", "Jifeng Plate"], ["expense_voucher", "实际费用来源", "Link", "Jifeng Expense Voucher"]] : [["legacy_number", "编号原文", "Data", ""], ...(this.recordType === "plate" ? [["plate_no", "业务版种编号", "Data", ""]] : []),
			["item_code", "关联商品", "Link", "Item"], ["copy_from", "复制来源", "Link", this.doctype],
			[this.recordType === "mold" ? "scrap_date" : "retired_date", "报废日期", "Date", ""], [this.recordType === "mold" ? "scrap_reason" : "retired_reason", "报废原因", "Small Text", ""]];
		for (const [field, label, type, options] of additional) { state.nativeControls[field] = this.nativeControl(grid, field, label, type, options, () => this.freeze(state));
			if (["copy_from", "plate", "expense_voucher"].includes(field)) state.nativeControls[field].get_query = () => ({ filters: { company: state.company, ...(field === "expense_voucher" ? { docstatus: 1 } : {}) } }); }
		state.linkStatus = this.element("p"); extra.append(grid, state.linkStatus, this.element("p", __(this.modeling ? "单价是参考原文，可留空，不计算费用、库存或工资。复制仅记录勾选，不自动复制；设计原图先存档后上传。" : "复制仅保留勾选和明确来源，不自动复制。取消报废会清空报废日期和原因。金额是档案参考值，不生成费用或库存；图片先存档后上传。")));
		state.review = this.element("div", undefined, "jfe-tool-review"); const footer = this.element("div", undefined, "jfe-tool-footer");
		state.newButton = this.button("新增", () => this.newDraft(state)); state.saveButton = this.button("保存", () => this.save(state)); state.closeButton = this.button("关闭", () => state.dialog.hide());
		state.nativeButton = this.button("历史字段", () => this.openNative(state)); state.resolveButton = this.button("核对保存结果", () => this.resolve(state)); state.adoptButton = this.button("采用已核对当前值", () => this.acceptCurrent(state));
		footer.append(state.newButton, state.saveButton, state.closeButton, state.nativeButton, state.resolveButton, state.adoptButton); host.append(state.scope, state.message, state.layout, extra, state.review, footer);
	}
	nativeControl(host, fieldname, label, fieldtype, options, onchange, onlyInput = false) { const cell = this.element("div"); host.append(cell);
		return frappe.ui.form.make_control({ parent: cell, render_input: true, only_input: onlyInput, df: { fieldname, label: __(label), fieldtype, options, onchange } }); }
	nativeFlags(control, values) { let changed = false; for (const [field, value] of Object.entries(values)) if (control.df[field] !== value) { control.df[field] = value; changed = true; } if (changed) control.refresh?.(); }
	async applyArchive(state, archive) {
		state.archive = archive; state.company = archive.values.company || null; state.imageValue = archive.values.image;
		for (const [field, input] of Object.entries(state.controls)) { state.cells[field].hidden = !archive.field_meta[field];
			if (input.type === "checkbox") input.checked = !!Number(archive.values[field]); else input.value = archive.values[field] ?? ""; }
		state.settingNative = true; try { await state.companyControl.set_value(state.company); await Promise.all(Object.entries(state.nativeControls).map(([field, control]) => control.set_value(archive.values[field] ?? null))); }
		finally { state.settingNative = false; }
		state.scope.textContent = __("公司：") + (state.company || __("请选择")) + (archive.name ? __("；档案：") + archive.name : "");
		state.message.textContent = ["remark", "notes", "remarks"].some((field) => /[\r\n]/.test(archive.values[field] || "")) ? __("说明含换行，单行显示；未改动时保留原文。") : "";
		state.linkStatus.textContent = this.modeling ? __("费用来源当前状态：") + (archive.link_states?.expense_voucher || __("不可见")) : "";
		state.baseValues = this.currentValues(state); state.original = JSON.stringify(this.draft(state)); state.review.replaceChildren(); this.renderImage(state); this.freeze(state);
	}
	currentValues(state) { const values = {}; for (const [field, input] of Object.entries(state.controls)) if (!["legacy_number", "mold_date", "model_date"].includes(field) && state.archive.field_meta[field] && !state.archive.field_meta[field].read_only)
		values[field] = input.type === "checkbox" ? Number(input.checked) : input.value;
		for (const [field, control] of Object.entries(state.nativeControls)) if (state.archive.field_meta[field] && !state.archive.field_meta[field].read_only &&
			(!this.isRetirementField(field) || state.controls[this.inactive]?.checked)) values[field] = control.get_value() ?? ""; return values; }
	isRetirementField(field) { return ["scrap_date", "scrap_reason", "retired_date", "retired_reason"].includes(field); }
	draft(state) { return { company: state.company, values: this.currentValues(state), image: state.imageValue }; }
	payload(state) { const result = { record_type: this.recordType, values: {} }, current = this.currentValues(state);
		if (state.archive.name) Object.assign(result, { name: state.archive.name, version: state.archive.version }); else { result.request_id = state.requestId; result.values.company = state.company; }
		for (const [field, value] of Object.entries(current)) if (!state.archive.name || value !== state.baseValues[field]) result.values[field] = value;
		if (state.archive.name && state.archive.field_meta.image && !state.archive.field_meta.image.read_only && state.imageValue !== state.archive.values.image) result.values.image = state.imageValue;
		return result; }
	isDirty(state = this.editor) { return !!(state && !state.closed && state.original !== JSON.stringify(this.draft(state))); }
	getCloseBlock() { const state = this.editor; if (this.opening || state?.busy || state?.upload?.open) return "正在读取、保存或上传档案，请稍候。";
		if (state?.unresolved) return "保存结果尚未确认，请先核对保存结果。"; if (state?.uploadUnresolved) return "图片上传结果尚未确认，请先核对上传结果。"; return state?.confirming ? "正在确认草稿，请稍候。" : ""; }
	freeze(state) {
		if (!state || state.closed) return; const locked = !!(state.busy || state.unresolved || state.upload?.open || state.uploadUnresolved || state.confirming);
		if (this.modeling && state.baseValues && state.nativeControls.expense_voucher) state.linkStatus.textContent =
			(state.nativeControls.expense_voucher.get_value() ?? "") !== (state.baseValues.expense_voucher ?? "") ? __("费用来源变更待保存，保存后显示状态。") : __("费用来源当前状态：") + (state.archive.link_states?.expense_voucher || __("不可见"));
		for (const [field, input] of Object.entries(state.controls)) input.disabled = locked || !state.archive.can_write || !state.archive.field_meta[field] || !!state.archive.field_meta[field].read_only || ["legacy_number", "mold_date", "model_date"].includes(field);
		if (state.controls.legacy_number) state.controls.legacy_number.value = state.nativeControls.legacy_number.get_value() ?? "";
		for (const [field, control] of Object.entries(state.nativeControls)) { const df = state.archive.field_meta[field];
			const inactiveField = this.isRetirementField(field), inactive = !!state.controls[this.inactive]?.checked;
			this.nativeFlags(control, { hidden: !df || inactiveField && !inactive, read_only: locked || !state.archive.can_write || !df || !!df.read_only || inactiveField && !inactive,
				reqd: !!df?.reqd || inactiveField && field.endsWith("_date") && inactive }); }
		this.nativeFlags(state.companyControl, { read_only: locked || !!state.archive.name || !state.archive.can_write, reqd: true });
		state.saveButton.disabled = locked || !state.archive.can_write; state.newButton.disabled = locked; state.closeButton.disabled = locked;
		state.nativeButton.hidden = !state.archive.name; state.nativeButton.disabled = locked || !this.bridge;
		state.resolveButton.hidden = !state.unresolved; state.resolveButton.disabled = state.busy || state.confirming;
		state.adoptButton.hidden = !state.unresolved || !state.conflict; state.adoptButton.disabled = state.busy || state.confirming;
		const df = state.archive.field_meta.image; state.imageCell.hidden = !df; state.image.uploadButton.disabled = locked || !state.archive.name || !state.archive.can_write || !df || !!df.read_only;
		state.image.clearButton.disabled = state.image.uploadButton.disabled; state.image.resolveButton.hidden = !state.uploadUnresolved; state.image.resolveButton.disabled = state.busy || state.confirming;
	}
	async discardFor(action, state = this.editor) {
		if (this.disposed || state?.closed) return false; const block = this.getCloseBlock(); if (block) { frappe.msgprint(__(block)); return false; }
		if (!this.isDirty(state)) { await action(); return true; } state.confirming = true; this.freeze(state);
		const yes = await new Promise((resolve) => frappe.confirm(__("尚有未保存的修改，放弃修改并继续？"), () => resolve(true), () => resolve(false)));
		state.confirming = false; this.freeze(state); if (yes && !this.disposed && !state.closed) { await action(); return true; } return false;
	}
	guardClose(state) { state.nativeHide = state.dialog.hide.bind(state.dialog); state.dialog.hide = () => this.discardFor(() => this.closeEditor(), state);
		state.dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false }); state.dialog.get_close_btn().removeAttr("data-dismiss").on("click.tool-archive", () => state.dialog.hide());
		state.dialog.$wrapper.on("keydown.tool-archive", (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); } });
		state.dialog.on_hide = () => { state.closed = true; if (this.editor === state) this.editor = null; }; }
	closeEditor() { const state = this.editor; if (!state || state.closed) return; state.closed = true; state.upload?.uploader?.dialog.hide(); state.nativeHide(); this.editor = null; }
	requestID() { if (crypto.randomUUID) return crypto.randomUUID(); const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
		const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join(""); return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-"); }
	async newDraft(state) { return this.discardFor(async () => { state.busy = true; this.freeze(state);
		try { const response = await this.call("init_record", { company: state.company }); if (!this.validArchive(response.message, state.company) || response.message.name) throw new Error("Wrong new scope");
			state.requestId = this.requestID(); state.unresolved = false; state.conflict = null; state.candidate = null; state.upload = null; await this.applyArchive(state, response.message); state.dialog.set_title(__(this.title + "新增"));
		} catch { state.message.textContent = __("新增档案未能打开，原草稿已保留，请重试。"); } finally { state.busy = false; this.freeze(state); } }, state); }
	async changeEditorCompany(state) {
		if (state.settingNative || state.closed || state.archive.name) return; const desired = state.companyControl.get_value() || null; if (desired === state.company) return;
		state.settingNative = true; try { await state.companyControl.set_value(state.company); } finally { state.settingNative = false; }
		return this.discardFor(async () => { state.busy = true; this.freeze(state);
			try { const response = await this.call("init_record", { company: desired }); if (!this.validArchive(response.message, desired) || response.message.name) throw new Error("Wrong company");
				state.requestId = this.requestID(); await this.applyArchive(state, response.message); } catch { state.message.textContent = __("公司切换失败，原草稿已保留。"); } finally { state.busy = false; this.freeze(state); } }, state);
	}
	completeResult(state, archive) { const candidate = state.candidate; return !!(candidate && this.validArchive(archive, candidate.values.company || state.company, candidate.name) && archive.name &&
		(candidate.name || (this.modeling ? archive.receipt_scope?.requestId === candidate.request_id && archive.receipt_scope.company === state.company : archive.request_id === candidate.request_id)) && Object.keys(candidate.values).every((field) => field in archive.values && archive.field_meta[field])); }
	equalField(state, archive, field, value) { const stored = archive.values[field], type = state.archive.field_meta[field]?.fieldtype;
		if (type === "Check") return Number(value) === Number(stored);
		if (["Float", "Currency"].includes(type)) { const before = value === "" || value === null ? 0 : Number(value), after = typeof stored === "number" || typeof stored === "string" && stored !== "" ? Number(stored) : NaN;
			return Number.isFinite(before) && before >= 0 && Number.isFinite(after) && before === after; }
		if (!this.modeling && (field === this.identity || this.recordType === "mold" && field === "product_code")) return typeof value === "string" && value.trim() === stored;
		return (value ?? "") === (stored ?? ""); }
	matches(state, archive) { return this.completeResult(state, archive) && (!state.candidate.name || archive.version !== state.candidate.version) && Object.entries(state.candidate.values).every(([field, value]) => this.equalField(state, archive, field, value)); }
	definiteFailure(error) { return new Set(["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError", "UniqueValidationError"]).has(error?.responseJSON?.exc_type); }
	async save(state) {
		if (state.closed || this.getCloseBlock() || !state.archive.can_write || this.disposed) return; if (!state.company) { state.message.textContent = __("请选择公司。"); return; }
		if (state.archive.name && !this.isDirty(state)) return; state.candidate = structuredClone(this.payload(state)); state.conflict = null; state.review.replaceChildren(); state.busy = true; this.freeze(state);
		let writeAnswered = false;
		try { const response = await this.call("save_record", { payload: state.candidate }, true); writeAnswered = true; let archive = response.message;
			// Modeling DTO omits its UUID. Confirm only through the exact read-only receipt.
			if (this.modeling && !state.candidate.name && this.validArchive(archive, state.company) && archive.name) { const check = await this.call("resolve_creation", { request_id: state.candidate.request_id, company: state.company });
				if (check.message?.status === "found" && check.message.archive?.name === archive.name && check.message.archive?.version === archive.version) archive = check.message.archive; }
			if (this.matches(state, archive)) await this.accept(state, archive, "档案已保存"); else { state.unresolved = true; this.showConflict(state, archive); } }
		catch (error) { state.unresolved = writeAnswered || !this.definiteFailure(error); if (!state.unresolved) state.message.textContent = __("保存未完成，草稿已保留，请核对填写内容后重试。"); }
		finally { state.busy = false; this.freeze(state); if (state.unresolved) state.message.textContent = __("保存结果尚未确认，草稿已保留，请核对保存结果。"); }
	}
	async accept(state, archive, message) { await this.applyArchive(state, archive); state.unresolved = false; state.conflict = null; state.candidate = null; state.requestId = null;
		state.dialog.set_title(__(this.title)); state.message.textContent = __(message); if (this.companyScope === archive.values.company) this.query(); frappe.show_alert({ message: __(message), indicator: "green" }); }
	showConflict(state, archive) { if (!this.completeResult(state, archive)) return; state.conflict = archive; state.review.replaceChildren(this.element("p", __("当前值与草稿或版本不同，请核对后明确采用；采用不会再次保存。")));
		const table = this.element("table"), heading = this.element("tr"); for (const label of ["字段", "保留草稿", "服务器当前值"]) heading.append(this.element("th", __(label))); table.append(heading);
		for (const [field, value] of Object.entries(state.candidate.values)) { const row = this.element("tr"); row.append(this.element("td", __(this.labels[field] || field)),
			this.element("td", field === "image" ? value ? __("待保存图片") : __("清空图片") : value), this.element("td", field === "image" ? archive.image_states.image?.state || "" : archive.values[field])); table.append(row); } state.review.append(table); }
	async resolve(state) { if (state.closed || state.busy || state.confirming || !state.unresolved || !state.candidate) return; state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try { const response = await this.call(state.candidate.name ? "read_record" : "resolve_creation", state.candidate.name ? { name: state.candidate.name } : { request_id: state.candidate.request_id, company: state.company });
			const archive = state.candidate.name ? response.message : response.message?.status === "found" ? response.message.archive : null;
			if (this.matches(state, archive)) await this.accept(state, archive, "已核对保存结果"); else if (this.completeResult(state, archive)) this.showConflict(state, archive);
			else state.message.textContent = __("未核对到完整可见结果，请保留草稿稍后核对；不会重复保存。"); } catch { state.message.textContent = __("核对失败，草稿已保留，请稍后核对。"); } finally { state.busy = false; this.freeze(state); } }
	acceptCurrent(state) { if (state.closed || state.busy || state.confirming || !state.unresolved || !state.conflict) return; const archive = state.conflict; state.confirming = true; this.freeze(state);
		frappe.confirm(__("放弃保留草稿并采用刚核对的当前值？此操作不会再次保存。"), async () => { state.confirming = false; if (state.closed || state.conflict !== archive) return;
			state.busy = true; this.freeze(state); try { await this.accept(state, archive, "已采用当前值；未重复保存"); } finally { state.busy = false; this.freeze(state); } }, () => { state.confirming = false; this.freeze(state); }); }
	async openNative(state) { if (!state.archive.name || !this.bridge) return; return this.discardFor(() => { const name = state.archive.name; this.closeEditor();
		this.bridge.open({ title: this.title + "历史字段：" + name, module: this.recordType === "mold" ? "模具" : "版种", kind: "form", doctype: this.doctype, route: ["Form", this.doctype, name] }); }, state); }
	buildImage(state) { const cell = state.imageCell; cell.append(this.element("label", __(this.imageLabel))); const preview = this.element("div", undefined, "jfe-tool-preview"), status = this.element("p"), candidates = this.element("div", undefined, "jfe-tool-candidates");
		const uploadButton = this.button("添加", () => this.uploadImage(state)), clearButton = this.button("清空", () => { state.imageValue = null; this.renderImage(state); this.freeze(state); }), resolveButton = this.button("核对上传结果", () => this.resolveUpload(state));
		cell.append(preview, status, uploadButton, clearButton, resolveButton, candidates); state.image = { preview, status, candidates, uploadButton, clearButton, resolveButton }; }
	safePreview(url) { return typeof url === "string" && /^\/private\/files\/[^?#\\\x00-\x1f]+(?:\?[^#\\\x00-\x1f]*)?$/.test(url); }
	canonicalImage(url) { return typeof url === "string" && url.length <= 140 && /^\/private\/files\/[^?#\\\x00-\x1f]+$/.test(url); }
	renderImage(state) { const panel = state.image, info = state.archive.image_states.image, pending = state.imageValue !== state.archive.values.image; panel.preview.replaceChildren();
		if (!pending && info?.state === "已上传" && this.safePreview(info.url)) { const img = this.element("img"); img.src = info.url; img.alt = __(this.imageLabel); panel.preview.append(img); }
		else panel.preview.append(this.element("span", __(pending ? state.imageValue ? "图片待保存" : "清空待保存" : "图片区域")));
		panel.status.textContent = pending ? __("请保存图片变更。") : !state.archive.name ? __("先保存档案，再上传图片。") : info?.state || __("未上传"); }
	attachmentRows(rows) { if (!Array.isArray(rows) || rows.some((row) => !row?.name || typeof row.file_name !== "string" || !this.canonicalImage(row.file_url) || !this.safePreview(row.unique_url))) throw new Error("Incomplete attachment list"); return rows; }
	async uploadImage(state) {
		if (state.closed || state.image.uploadButton.disabled || this.getCloseBlock()) return; if (this.isDirty(state)) { state.message.textContent = __("请先保存当前修改，再上传图片。"); return; }
		state.busy = true; this.freeze(state);
		try { const response = await this.call("list_image_attachments", { name: state.archive.name, fieldname: "image" }); const baseline = new Set(this.attachmentRows(response.message).map((row) => row.name));
			if (!frappe.ui.FileUploader) await frappe.require("file_uploader.bundle.js"); if (state.closed || this.disposed) return;
			const upload = { baseline, open: true, started: false, known: false, candidates: [] }; state.upload = upload;
			const uploader = new frappe.ui.FileUploader({ doctype: this.doctype, docname: state.archive.name, fieldname: this.imageField, make_attachments_public: 0, allow_toggle_private: false,
				allow_multiple: false, disable_file_browser: true, allow_web_link: false, allow_google_drive: false, allow_toggle_optimize: false,
				restrictions: { allowed_file_types: ["image/*"], max_number_of_files: 1 }, dialog_title: __(this.imageLabel), on_success: (file) => {
					if (state.closed || state.upload !== upload || !file?.name || file.doctype !== "File" || Number(file.is_private) !== 1 || file.attached_to_doctype !== this.doctype ||
						file.attached_to_name !== state.archive.name || file.attached_to_field !== this.imageField || !this.canonicalImage(file.file_url)) return;
					upload.known = true; state.uploadUnresolved = false; state.imageValue = file.file_url; state.message.textContent = __("图片已上传，请保存图片变更。"); state.image.candidates.replaceChildren(); this.renderImage(state); this.freeze(state);
				} }); upload.uploader = uploader;
			const nativeUpload = uploader.upload_files.bind(uploader); uploader.upload_files = () => {
				if (upload.started) { state.message.textContent = __("本次上传已发起，请核对结果后继续。"); return Promise.resolve(); }
				if (!uploader.uploader?.files?.length) return nativeUpload(); upload.started = true; uploader.dialog.get_primary_btn().prop("disabled", true);
				try { const pending = nativeUpload(); return pending?.catch ? pending.catch(() => { state.uploadUnresolved = true; this.freeze(state); }) : pending; }
				catch { state.uploadUnresolved = true; this.freeze(state); return Promise.resolve(); }
			};
			const hide = uploader.dialog.hide.bind(uploader.dialog), finish = () => { if (!upload.open || state.upload !== upload) return; upload.open = false;
				if (upload.started && !upload.known) { state.uploadUnresolved = true; state.message.textContent = __("图片上传结果尚未确认，请核对上传结果。"); } this.freeze(state); };
			// Preserve native hidden lifecycle: Bootstrap X/backdrop bypasses hide().
			const onHide = uploader.dialog.on_hide; uploader.dialog.on_hide = function (...args) { try { onHide?.apply(this, args); } finally { finish(); } };
			uploader.dialog.hide = () => { finish(); hide(); };
		} catch { if (state.upload && !state.upload.started) { state.upload.open = false; state.upload = null; } state.message.textContent = __("图片上传窗口未能打开，当前图片保持原状，请重试。"); }
		finally { state.busy = false; this.freeze(state); }
	}
	async resolveUpload(state) {
		if (state.closed || state.busy || state.confirming || !state.uploadUnresolved || !state.upload) return; const upload = state.upload, panel = state.image; state.busy = true; this.freeze(state); panel.candidates.replaceChildren();
		try { const response = await this.call("list_image_attachments", { name: state.archive.name, fieldname: "image" }); if (state.closed || state.upload !== upload) return;
			upload.candidates = this.attachmentRows(response.message).filter((row) => !upload.baseline.has(row.name));
			if (!upload.candidates.length) { panel.candidates.append(this.element("p", __("暂未核对到新增图片，请稍后核对；不会重复上传。"))); return; }
			panel.candidates.append(this.element("p", __("请选择要采用的已核对图片，采用后还需保存图片变更。")));
			for (const row of upload.candidates) { const candidate = this.element("div"), img = this.element("img"); img.src = row.unique_url; img.alt = row.file_name;
				candidate.append(img, this.element("span", row.file_name), this.element("small", row.creation ?? ""), this.button("采用这张图片", () => {
					if (state.closed || state.busy || state.confirming || state.upload !== upload || !state.uploadUnresolved || !upload.candidates.includes(row)) return; state.confirming = true; this.freeze(state);
					frappe.confirm(__("采用这张已核对图片作为待保存修改？此操作不会再次上传。"), () => { state.confirming = false; if (state.closed || state.upload !== upload) return;
						state.imageValue = row.file_url; state.uploadUnresolved = false; upload.known = true; panel.candidates.replaceChildren(); state.message.textContent = __("已采用核对图片，请保存图片变更。"); this.renderImage(state); this.freeze(state);
					}, () => { state.confirming = false; this.freeze(state); }); })); panel.candidates.append(candidate); }
		} catch { panel.candidates.append(this.element("p", __("上传结果核对失败，请稍后核对；不会重复上传。"))); } finally { state.busy = false; this.freeze(state); }
	}
	destroy() { this.disposed = true; this.revision++; window.removeEventListener("beforeunload", this.beforeUnload); if (this.editor && !this.editor.closed) this.closeEditor(); }
};
