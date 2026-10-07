/* Order plating colors use the shared dictionary transport and dialog guards. */
window.JifengPlatingColor = class JifengPlatingColor extends window.JifengOrderDictionary {
	constructor(host) { super(host, "镀色", "产品镀色"); }
	canCreate() { return true; }
	updateAddButton() { super.updateAddButton(); if (this.deletion && this.addButton) this.addButton.disabled = true; }
	async openEditor(name) { if (!this.deletion) return super.openEditor(name); }
	async initialize() {
		const result = await super.initialize();
		if (result) this.limits.textContent = __("编号由服务器分配；删除保留永久操作记录。简拼、条码生成与打印暂不可用。");
		return result;
	}

	build() {
		this.dictionaryApi = "jifeng_erp.plating_color";
		this.dictionaryOptions = { ...this.dictionaryOptions,
			editorFields: ["legacy_number", "code", "value", "color_system", "remarks", "inactive", "abbreviation"],
			editorLabels: { code: "代码", color_system: "色系" },
			editorFieldOptions: { code: { placeholder: "代码生成条码时用" } } };
		super.build(); this.host.classList.add("jfe-plating-color");
		this.deleteReview = this.element("div"); this.host.append(this.deleteReview);
	}

	columnLabels() { return ["代码", "编号", "名称", "色系", "备注", "简拼", "停用", "操作", "日期", "修改", "修改日期", "修改", "删除"]; }

	validDictionaryRow(row) {
		return super.validDictionaryRow(row) && ["code", "color_system"].every((field) => row[field] == null || typeof row[field] === "string");
	}

	numberPolicy(archive) { return { read_only: true, value: archive.name ? archive.legacy_number ?? null : archive.number_preview ?? null,
		description: archive.name ? __("已保存的编号") : __("保存时自动生成；暂定编号仅供参考") }; }

	renderRow(row) {
		const tr = this.element("tr");
		for (const field of ["code", "legacy_number", "value", "color_system", "remarks", "abbreviation"]) tr.append(this.element("td", row[field] ?? ""));
		const inactive = this.element("td"), check = this.element("input"); check.type = "checkbox"; check.checked = !!Number(row.inactive); check.disabled = true;
		check.setAttribute("aria-label", __("停用")); inactive.append(check); tr.append(inactive);
		for (const field of ["owner", "creation", "modified_by", "version"]) tr.append(this.element("td", row[field] ?? ""));
		const operation = this.element("td"); operation.append(this.button(this.readyArchive?.can_write ? "修改" : "查看", () => this.openEditor(row.name))); tr.append(operation);
		const deletion = this.element("td");
		const button = this.button("删除", () => this.confirmDelete(row));
		button.disabled = row.can_delete !== true || !row.creation || !!this.deletion;
		deletion.append(button); tr.append(deletion); this.tbody.append(tr);
	}

	isDirty(state = this.editor) { return state?.deleted ? false : super.isDirty(state); }
	getCloseBlock() {
		if (this.deletion) return this.deletion.confirming ? "正在确认删除，请先完成确认。" :
			this.deletion.busy ? "正在删除或核对镀色，请稍候。" : "删除结果尚未确认，请先核对删除记录。";
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
		const candidate = state.candidate;
		if (result.request_id !== candidate.request_id || result.company !== candidate.company || result.dictionary_type !== this.type ||
			!result.name || typeof result.creation !== "string" || !result.creation || typeof result.version !== "string" || !result.version) return true;
		state.unresolved = false; state.deleted = true; state.conflict = null;
		state.review.replaceChildren(this.element("p", __("该新建请求曾成功保存的镀色已删除。原草稿不会重复保存；如需另建，请点新增取得新请求。")));
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
		frappe.confirm(__("确认删除所选镀色？创建回执和删除记录将永久保留。已有业务引用的镀色不能删除。"),
			() => { state.confirming = false; return this.deleteColor(state); },
			() => { if (this.deletion === state) { this.deletion = null; this.updateAddButton(); } });
	}

	deletionReply(state, result) {
		return !!(result && ["deleted", "not_deleted"].includes(result.status) &&
			Object.entries(state.payload).every(([field, value]) => result[field] === value));
	}
	showDeletion(state) {
		this.deleteReview.replaceChildren();
		if (this.deletion !== state || !state.unresolved) return;
		this.deleteReview.append(this.element("p", __("删除结果尚未确认；保留本次请求，只读核对，不会自动重复删除。")),
			this.button("核对删除结果", () => this.resolveDelete(state)));
	}
	async deleteColor(state) {
		if (this.disposed || this.deletion !== state || state.busy || state.unresolved || state.confirming) return;
		state.busy = true;
		try {
			const response = await frappe.call({ method: this.dictionaryApi + ".delete_dictionary", type: "POST",
				args: { payload: state.payload }, freeze: true });
			if (response.message?.status !== "deleted" || !this.deletionReply(state, response.message)) { state.unresolved = true; return; }
			this.finishDelete(state, "镀色已删除，删除记录已保留");
		} catch (error) {
			if (this.definiteFailure(error)) this.finishDelete(state, "删除未完成，请重新查询核对；原记录保留");
			else state.unresolved = true;
		} finally { state.busy = false; this.showDeletion(state); this.updateAddButton(); }
	}
	finishDelete(state, message) {
		if (this.deletion !== state) return;
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
			this.finishDelete(state, response.message.status === "deleted" ? "已核对删除记录；未重复删除" : "已核对原镀色仍存在；未重复删除，请重新查询");
		} catch { /* Permission or unknown outcomes keep the exact held request. */ }
		finally { state.busy = false; this.showDeletion(state); this.updateAddButton(); }
	}
};
