/* Product vocabulary workbench. Text never rewrites Product Profile or Item. */
window.JifengProductDictionary = class JifengProductDictionary {
	constructor(host, type) {
		this.host = host; this.type = type; this.revision = 0; this.disposed = false;
		this.rows = []; this.editor = null; this.opening = false; this.readyArchive = null;
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
		const button = this.element("button", __(text), "btn btn-default btn-sm");
		button.type = "button";
		if (reason) { button.disabled = true; button.title = __(reason); }
		else button.addEventListener("click", action);
		return button;
	}

	customAction(dialog, label, action, className) {
		// Native Dialog.add_custom_action appends a button and returns nothing.
		dialog.add_custom_action(__(label), action, className);
		return dialog.custom_actions.find("." + className);
	}

	build() {
		this.host.classList.add("jfe-product-dictionary");
		this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-product-dictionary-toolbar");
		this.addButton = this.button("新增", () => this.openEditor());
		this.addButton.disabled = true;
		const companyHost = this.element("div", undefined, "jfe-product-dictionary-company");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), reqd: true,
				onchange: () => this.changeScope() } });
		this.search = this.element("input");
		this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("关键词"); this.search.setAttribute("aria-label", __("关键词"));
		this.search.addEventListener("input", () => { this.revision++; this.status.textContent = __("关键词已改变，请查询。"); });
		this.search.addEventListener("keydown", (event) => {
			if (event.key === "Enter") { event.preventDefault(); this.query(); }
		});
		this.includeInactive = this.element("input"); this.includeInactive.type = "checkbox";
		this.includeInactive.addEventListener("change", () => this.query());
		const inactiveLabel = this.element("label", undefined, "jfe-product-dictionary-inactive");
		inactiveLabel.append(this.includeInactive, this.element("span", __("包含停用")));
		toolbar.append(this.addButton, this.button("更新简拼", null, "简拼更新算法尚未核实，本批未实现"), companyHost,
			this.search, this.button("查询", () => this.query()), inactiveLabel,
			this.button("格式1", null, "当前采用固定格式1，其他格式未实现"),
			this.button("打印", null, "本批未实现打印"), this.button("预览", null, "本批未实现预览"));
		this.limits = this.element("p", __("编号自动预填尚未实现，新词条编号留空待分配；删除、更新简拼、打印和预览暂不可用。"),
			"jfe-product-dictionary-limits");
		this.status = this.element("p", __("正在读取权限…"), "jfe-product-dictionary-status");
		this.status.setAttribute("role", "status");
		this.scroller = this.element("div", undefined, "jfe-product-dictionary-scroll");
		const table = this.element("table", undefined, "jfe-product-dictionary-table");
		table.setAttribute("aria-label", __("产品" + this.type));
		const thead = this.element("thead"), headings = this.element("tr");
		for (const [label, title] of [["编号", "独立原文编号，不是系统编号"], ["名称"], ["备注"], ["简拼", "输入原文，不自动生成"],
			["停用"], ["操作", "修改本系统词条；录入人来自本系统原生记录"], ["日期", "本系统原生创建时间"],
			["修改", "本系统原生最后修改人和时间"], ["删除", "本批未实现可追溯删除"]]) {
			const th = this.element("th", __(label)); th.scope = "col"; if (title) th.title = __(title); headings.append(th);
		}
		thead.append(headings); this.tbody = this.element("tbody"); table.append(thead, this.tbody);
		this.scroller.append(table); this.host.append(toolbar, this.limits, this.status, this.scroller);
	}

	async initialize() {
		try {
			const response = await frappe.call({ method: "jifeng_erp.product_dictionary.init_dictionary",
				args: { dictionary_type: this.type, numbering_mode: "automatic_v1" } });
			if (this.disposed) return false;
			if (!response.message?.field_meta || response.message.dictionary_type !== this.type) throw new Error("Incomplete scope");
			this.readyArchive = response.message;
			if (this.readyArchive.numbering_mode === "automatic_v1") this.limits.textContent = __("编号使用本系统新规则，暂定号不占号，以保存后的最终号为准；删除、更新简拼、打印和预览暂不可用。");
			this.updateAddButton(); this.status.textContent = __("请选择公司后查询。");
			return true;
		} catch { if (!this.disposed) this.status.textContent = __("无权读取或页面未能加载，请重新打开。"); return false; }
	}

	company() { return this.companyControl.get_value(); }
	updateAddButton() { this.addButton.disabled = !this.company() || !this.readyArchive?.can_write || this.opening; }
	changeScope() {
		this.revision++; this.rows = []; this.tbody.replaceChildren(); this.updateAddButton();
		if (this.readyArchive) this.query();
	}

	async query() {
		if (this.disposed || !this.readyArchive) return;
		const company = this.company(), search = this.search.value || "", include_inactive = !!this.includeInactive.checked;
		const sequence = ++this.revision;
		this.rows = []; this.tbody.replaceChildren();
		if (!company) { this.status.textContent = __("请选择公司后查询。"); return; }
		const scroll = { top: this.scroller.scrollTop, left: this.scroller.scrollLeft };
		this.status.textContent = __("正在查询…");
		try {
			const response = await frappe.call({ method: "jifeng_erp.product_dictionary.list_dictionary",
				args: { dictionary_type: this.type, company, search, include_inactive: Number(include_inactive) } });
			if (this.disposed || sequence !== this.revision || this.company() !== company || this.search.value !== search ||
				!!this.includeInactive.checked !== include_inactive) return;
			if (!Array.isArray(response.message)) throw new Error("Incomplete list");
			if (response.message.some((row) => !row?.name || typeof row.version !== "string" || typeof row.value !== "string" ||
				row.company !== company || row.dictionary_type !== this.type)) throw new Error("Wrong list scope");
			this.rows = response.message;
			for (const row of this.rows) this.renderRow(row);
			this.scroller.scrollTop = scroll.top; this.scroller.scrollLeft = scroll.left;
			this.status.textContent = this.rows.length ? __("查询完成") + " · " + this.rows.length + __("条") : __("没有可见记录。");
		} catch { if (!this.disposed && sequence === this.revision) this.status.textContent = __("查询失败，请重新查询。"); }
	}

	renderRow(row) {
		const tr = this.element("tr");
		for (const field of ["legacy_number", "value", "remarks", "abbreviation"]) tr.append(this.element("td", row[field] ?? ""));
		const inactive = this.element("td"), check = this.element("input");
		check.type = "checkbox"; check.checked = !!Number(row.inactive); check.disabled = true;
		check.setAttribute("aria-label", __("停用")); inactive.append(check); tr.append(inactive);
		const operation = this.element("td");
		operation.append(this.button(this.readyArchive?.can_write ? "修改" : "查看", () => this.openEditor(row.name)));
		if (row.owner) operation.append(this.element("small", __("录入人（本系统）：") + row.owner));
		tr.append(operation, this.element("td", row.creation ?? ""));
		const modified = this.element("td");
		if (row.modified_by) modified.append(this.element("div", row.modified_by));
		modified.append(this.element("small", row.version ?? "")); tr.append(modified);
		const deletion = this.element("td"); deletion.append(this.button("删除", null, "本批未实现可追溯删除")); tr.append(deletion);
		this.tbody.append(tr);
	}

	numberPolicy(archive) {
		if (archive.name) return { read_only: true, value: archive.legacy_number ?? null, description: __("本系统已保存的原文编号") };
		return archive.numbering_mode === "automatic_v1" ? { read_only: true, value: archive.number_preview ?? null,
			description: __("本系统新编号规则；暂定号不占号，以保存后的最终号为准") } :
			{ read_only: true, value: null, description: __("待系统分配（自动编号尚未实现）") };
	}

	requestID() {
		if (crypto.randomUUID) return crypto.randomUUID();
		const bytes = crypto.getRandomValues(new Uint8Array(16));
		bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
		const text = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
		return [text.slice(0, 8), text.slice(8, 12), text.slice(12, 16), text.slice(16, 20), text.slice(20)].join("-");
	}

	async openEditor(name) {
		if (this.disposed || this.opening) return;
		if (this.editor && !this.editor.closed) { this.editor.dialog.show(); return this.editor.dialog; }
		const company = this.company();
		if (!company || !this.readyArchive || !name && !this.readyArchive.can_write) return;
		this.opening = true; this.updateAddButton();
		let initializingState;
		try {
			const response = await frappe.call({ method: "jifeng_erp.product_dictionary." + (name ? "read_dictionary" : "init_dictionary"),
				args: name ? { name, dictionary_type: this.type } : { dictionary_type: this.type, company, numbering_mode: "automatic_v1" }, freeze: true });
			const archive = response.message;
			if (this.disposed || this.company() !== company || !archive?.field_meta || archive.company !== company || archive.dictionary_type !== this.type ||
				(name ? archive.name !== name : !!archive.name || !!archive.version)) return;
			const labels = { legacy_number: "编号", value: "名称", remarks: "备注", inactive: "停用", abbreviation: "简拼（原文维护）" };
			const fields = ["legacy_number", "value", "remarks", "inactive", "abbreviation"].flatMap((fieldname) => {
				const df = archive.field_meta[fieldname];
				return df ? [{ ...df, fieldname, label: __(labels[fieldname]), default: fieldname === "legacy_number" ? this.numberPolicy(archive).value : archive[fieldname] ?? null,
					...(fieldname === "remarks" && df.fieldtype === "Small Text" ? { fieldtype: "Data", length: 65535 } : {}),
					hidden: fieldname === "abbreviation" && !archive.name,
					...(fieldname === "legacy_number" ? { description: this.numberPolicy(archive).description } : {}),
					read_only: !!(df.read_only || !archive.can_write || fieldname === "legacy_number") }] : [];
			});
			const dialog = new frappe.ui.Dialog({ title: __("产品" + this.type), fields, size: "large", no_submit_on_enter: true });
			const scope = this.element("p", __("公司：") + company, "jfe-product-dictionary-dialog-scope");
			dialog.$body.prepend(scope);
			const state = { archive, company, dialog, requestId: archive.name ? null : this.requestID(),
				closed: false, busy: false, unresolved: false, confirming: false, candidate: null, conflict: null };
			initializingState = state;
			state.review = this.element("div", undefined, "jfe-product-dictionary-review"); dialog.$body.append(state.review);
			this.editor = state;
			dialog.set_primary_action(__("保存"), () => this.save(state));
			state.newButton = this.customAction(dialog, "新增", () => this.newDraft(state), "jfe-product-new");
			state.resolveButton = this.customAction(dialog, "核对保存结果", () => this.resolve(state), "jfe-product-resolve");
			state.acceptButton = this.customAction(dialog, "采用已核对当前值", () => this.acceptCurrent(state), "jfe-product-adopt");
			dialog.add_custom_action(__("关闭"), () => dialog.hide());
			this.guardClose(state);
			await dialog.set_values(this.values(state)); state.original = JSON.stringify(this.payload(state));
			this.freeze(state); dialog.show();
			return dialog;
		} catch {
			if (initializingState) {
				initializingState.closed = true;
				initializingState.nativeHide?.();
				initializingState.dialog.$wrapper.remove();
				if (this.editor === initializingState) this.editor = null;
			}
			if (!this.disposed) this.status.textContent = __("无法打开词条，请刷新后重试。");
		}
		finally { this.opening = false; if (!this.disposed) this.updateAddButton(); }
	}

	values(state) {
		return Object.fromEntries(["legacy_number", "value", "remarks", "inactive", "abbreviation"]
			.filter((field) => state.dialog.fields_dict[field]).map((field) => [field,
			state.archive.field_meta[field] ? field === "legacy_number" ? this.numberPolicy(state.archive).value : state.archive[field] ?? null : null]));
	}

	payload(state) {
		const result = { company: state.company, dictionary_type: this.type };
		if (state.archive.name) { result.name = state.archive.name; result.version = state.archive.version; }
		else { result.request_id = state.requestId; result.numbering_mode = "automatic_v1"; }
		for (const field of ["value", "remarks", "inactive", "abbreviation"]) {
			const df = state.archive.field_meta[field], control = state.dialog.fields_dict[field];
			if (control && df && !df.read_only && state.archive.can_write && (field !== "abbreviation" || state.archive.name)) {
				const value = control.get_value();
				// HTML text inputs flatten line breaks. An untouched historical
				// value must not be rewritten when another field is changed.
				const displayed = (text) => control.df.fieldtype === "Data" && typeof text === "string" ? text.replace(/[\r\n]/g, "") : text ?? "";
				if (!state.archive.name || (field === "inactive" ? Number(value) !== Number(state.archive[field]) : displayed(value) !== displayed(state.archive[field]))) result[field] = value;
			}
		}
		return result;
	}

	isDirty(state = this.editor) { return !!(state && !state.closed && state.original !== JSON.stringify(this.payload(state))); }
	getCloseBlock() {
		if (this.opening || this.editor?.busy) return "正在读取或保存词条，请稍候。";
		return this.editor?.unresolved ? "保存结果尚未确认，请先核对保存结果。" : "";
	}

	guardClose(state) {
		state.nativeHide = state.dialog.hide.bind(state.dialog);
		const close = () => { state.closed = true; state.nativeHide(); };
		state.dialog.hide = () => {
			if (state.closed || state.confirming) return;
			if (state.busy || state.unresolved) { frappe.msgprint(__(this.getCloseBlock())); return; }
			if (!this.isDirty(state)) { close(); return; }
			state.confirming = true;
			this.freeze(state);
			frappe.confirm(__("尚有未保存的修改，关闭并放弃修改？"),
				() => { state.confirming = false; queueMicrotask(close); }, () => { state.confirming = false; this.freeze(state); });
		};
		state.dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn().removeAttr("data-dismiss").on("click.product-dictionary", () => state.dialog.hide());
		state.dialog.$wrapper.on("keydown.product-dictionary", (event) => {
			if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); }
		});
		state.dialog.on_hide = () => { state.closed = true; if (this.editor === state) this.editor = null; };
	}

	freeze(state) {
		for (const field of ["legacy_number", "value", "remarks", "inactive", "abbreviation"]) {
			if (!state.dialog.fields_dict[field]) continue;
			const df = state.archive.field_meta[field];
			state.dialog.set_df_property(field, "hidden", !df || field === "abbreviation" && !state.archive.name);
			state.dialog.set_df_property(field, "read_only", !!(state.busy || state.unresolved || state.confirming || !df || df.read_only || !state.archive.can_write || field === "legacy_number"));
			if (field === "legacy_number") state.dialog.set_df_property(field, "description", this.numberPolicy(state.archive).description);
		}
		state.dialog.get_primary_btn().prop("disabled", state.busy || state.unresolved || state.confirming || !state.archive.can_write);
		state.newButton?.prop("disabled", state.busy || state.unresolved || state.confirming || !this.readyArchive?.can_write);
		state.resolveButton?.prop("disabled", state.busy || state.confirming || !state.unresolved);
		state.acceptButton?.prop("disabled", state.busy || state.confirming || !state.conflict);
	}

	validArchive(state, archive) {
		return !!(archive?.name && typeof archive.version === "string" && archive.version && archive.field_meta &&
			archive.company === state.company && archive.dictionary_type === this.type &&
			(!state.candidate?.name || archive.name === state.candidate.name) &&
			(archive.numbering_mode || "") === (state.candidate?.numbering_mode || state.archive.numbering_mode || ""));
	}

	matches(state, archive) {
		if (!this.validArchive(state, archive) || state.candidate.name && archive.version === state.candidate.version) return false;
		for (const field of ["value", "remarks", "inactive", "abbreviation"]) {
			if (!(field in state.candidate)) continue;
			if (!(field in archive) || !archive.field_meta[field]) return false;
			if (field === "inactive" ? Number(archive[field]) !== Number(state.candidate[field]) : (archive[field] ?? "") !== (state.candidate[field] ?? "")) return false;
		}
		return true;
	}

	definiteFailure(error) {
		const response = error?.responseJSON;
		const kind = response?.exc_type || (typeof response?.exc === "string" ? response.exc : null);
		return new Set(["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError",
			"LinkValidationError", "DataError", "UniqueValidationError"]).has(kind);
	}

	async save(state) {
		if (state.closed || state.busy || state.unresolved || state.confirming || !state.archive.can_write || !state.dialog.get_values()) return;
		state.candidate = structuredClone(this.payload(state)); state.conflict = null; state.review.replaceChildren();
		state.busy = true; this.freeze(state);
		try {
			const response = await frappe.call({ method: "jifeng_erp.product_dictionary.save_dictionary",
				args: { payload: state.candidate }, freeze: true, freeze_message: __("正在保存产品词典…") });
			if (!this.matches(state, response.message)) { state.unresolved = true; return; }
			await this.accept(state, response.message, "产品词典已保存");
		} catch (error) { state.unresolved = !this.definiteFailure(error); }
		finally {
			state.busy = false; this.freeze(state);
			if (state.unresolved) frappe.msgprint(__("保存结果尚未确认，草稿已保留，请核对保存结果。"));
		}
	}

	async accept(state, archive, message) {
		state.archive = archive; state.requestId = null; state.unresolved = false; state.conflict = null;
		state.review.replaceChildren(); await state.dialog.set_values(this.values(state));
		state.original = JSON.stringify(this.payload(state));
		if (this.company() === state.company) this.query();
		frappe.show_alert({ message: __(message), indicator: "green" });
	}

	async resolve(state) {
		if (state.closed || state.busy || state.confirming || !state.unresolved || !state.candidate) return;
		state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try {
			const candidate = state.candidate;
			const response = await frappe.call({ method: "jifeng_erp.product_dictionary." + (candidate.name ? "read_dictionary" : "resolve_creation"),
				args: candidate.name ? { name: candidate.name, dictionary_type: this.type } :
					{ request_id: candidate.request_id, dictionary_type: this.type, company: candidate.company }, freeze: true });
			const archive = candidate.name ? response.message : response.message?.status === "found" ? response.message.archive : null;
			if (this.matches(state, archive)) { await this.accept(state, archive, "已核对保存结果"); return; }
			const fields = ["value", "remarks", "inactive", "abbreviation"].filter((field) => field in candidate);
			if (!this.validArchive(state, archive) || fields.some((field) => !(field in archive) || !archive.field_meta[field])) {
				frappe.msgprint(__("未核对到完整可见回执，请保留草稿稍后核对；不会重复保存。")); return;
			}
			state.conflict = archive;
			state.review.append(this.element("p", __("服务器当前值与本次草稿或版本不同。本次保存尚未确认，可核对下表后明确采用当前值。")));
			const table = this.element("table"), header = this.element("tr");
			for (const label of ["字段", "保留草稿", "服务器当前值"]) header.append(this.element("th", __(label))); table.append(header);
			if (state.archive.field_meta.legacy_number && archive.field_meta.legacy_number && "legacy_number" in archive) {
				const row = this.element("tr");
				row.append(this.element("td", __("编号（只读）")),
					this.element("td", this.numberPolicy(state.archive).value ?? ""), this.element("td", archive.legacy_number ?? ""));
				table.append(row);
			}
			state.review.append(this.element("p", __(archive.numbering_mode === "automatic_v1" ?
				"编号方式：系统自动；新增暂定号不占号，以服务器已保存编号为准。" : "编号方式：历史原文。")));
			const labels = { value: "名称", remarks: "备注", inactive: "停用", abbreviation: "简拼" };
			for (const field of fields) {
				const row = this.element("tr"); row.append(this.element("td", __(labels[field])),
					this.element("td", field === "inactive" ? Number(candidate[field]) ? "是" : "否" : candidate[field]),
					this.element("td", field === "inactive" ? Number(archive[field]) ? "是" : "否" : archive[field])); table.append(row);
			}
			state.review.append(table);
		} catch { /* Permission/transport errors retain the held candidate; no POST fallback. */ }
		finally { state.busy = false; this.freeze(state); }
	}

	acceptCurrent(state) {
		if (state.closed || state.busy || !state.unresolved || !state.conflict || state.confirming) return;
		const archive = state.conflict; state.confirming = true;
		this.freeze(state);
		frappe.confirm(__("放弃保留草稿并采用刚核对的服务器当前值？此操作不再提交保存。"),
			async () => {
				state.confirming = false; if (state.closed || state.conflict !== archive) return;
				state.busy = true; this.freeze(state);
				try { await this.accept(state, archive, "已采用核对到的当前值；未重复保存"); }
				finally { state.busy = false; this.freeze(state); }
			}, () => { state.confirming = false; this.freeze(state); });
	}

	newDraft(state) {
		if (state.closed || state.busy || state.unresolved || state.confirming || !this.readyArchive?.can_write) return;
		const reset = async () => {
			state.busy = true; this.freeze(state);
			try {
				const response = await frappe.call({ method: "jifeng_erp.product_dictionary.init_dictionary",
					args: { dictionary_type: this.type, company: state.company, numbering_mode: "automatic_v1" }, freeze: true });
				const archive = response.message;
				if (!archive?.can_write || !archive.field_meta || archive.name || archive.version || archive.company !== state.company ||
					archive.dictionary_type !== this.type || archive.numbering_mode !== "automatic_v1") return;
				const requestId = this.requestID();
				state.archive = archive; state.requestId = requestId; state.candidate = null; state.conflict = null;
				state.review.replaceChildren(); await state.dialog.set_values(this.values(state)); state.original = JSON.stringify(this.payload(state));
			} catch { /* Failed initialization keeps the previous saved row/draft. */ }
			finally { state.busy = false; this.freeze(state); }
		};
		if (!this.isDirty(state)) return reset();
		state.confirming = true;
		this.freeze(state);
		frappe.confirm(__("当前词条尚有未保存修改，放弃并打开空白新增？"),
			() => { state.confirming = false; reset(); }, () => { state.confirming = false; this.freeze(state); });
	}

	destroy() {
		this.disposed = true; this.revision++; window.removeEventListener("beforeunload", this.beforeUnload);
		if (this.editor && !this.editor.closed) { this.editor.closed = true; this.editor.nativeHide(); }
	}
};
