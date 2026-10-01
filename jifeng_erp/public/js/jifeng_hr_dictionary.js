/* Personnel dictionaries retain raw archive text and native company permissions. */
window.JifengHRDictionary = class JifengHRDictionary {
	constructor(bridge) {
		this.bridge = bridge;
		this.active = new Map();
		this.opening = new Map();
	}

	async open(type, name, company) {
		const key = name || type + ":new:" + (company || "");
		if (this.active.has(key)) { this.active.get(key).dialog.show(); return this.active.get(key).dialog; }
		if (this.opening.has(key)) return this.opening.get(key);
		const pending = this.load(type, name, company, key).finally(() => this.opening.delete(key));
		this.opening.set(key, pending);
		return pending;
	}

	async load(type, name, company, key) {
		const response = await frappe.call({ method: "jifeng_erp.hr_dictionary." +
			(name ? "read_dictionary" : "init_dictionary"),
			args: name ? { name, dictionary_type: type } : { dictionary_type: type, company: company || null }, freeze: true });
		const archive = response.message;
		if (!archive) return;
		const fields = ["company", "dictionary_type", "value", "inactive"].flatMap((fieldname) => {
			const df = archive.field_meta?.[fieldname];
			return df ? [{ ...df, fieldname, label: { company: "公司", dictionary_type: "类别", value: "名称", inactive: "停用" }[fieldname],
				default: archive[fieldname] ?? null,
				read_only: !!(df.read_only || !archive.can_write || fieldname === "dictionary_type" ||
					(archive.name && fieldname !== "inactive")) }] : [];
		});
		const dialog = new frappe.ui.Dialog({ title: (archive.name ? "" : "新增") + "人事" + type,
			fields, size: "large", no_submit_on_enter: true });
		const state = { key, type, archive, dialog, saving: false, unresolved: false, closed: false, confirming: false };
		this.active.set(key, state);
		if (archive.can_write) dialog.set_primary_action(archive.name ? "保存" : "新增", () => this.save(state));
		dialog.add_custom_action(__("核对保存结果"), () => this.resolve(state));
		dialog.add_custom_action(__("关闭"), () => dialog.hide());
		this.guardClose(state);
		await dialog.set_values(this.values(archive));
		state.original = JSON.stringify(this.payload(state));
		const beforeUnload = (event) => {
			if (state.saving || state.unresolved || this.isDirty(state)) { event.preventDefault(); event.returnValue = ""; }
		};
		window.addEventListener("beforeunload", beforeUnload);
		dialog.on_hide = () => { window.removeEventListener("beforeunload", beforeUnload); this.active.delete(key); };
		dialog.show();
		return dialog;
	}

	values(archive) {
		return Object.fromEntries(["company", "dictionary_type", "value", "inactive"].filter(
			(field) => archive.field_meta?.[field]).map((field) => [field, archive[field] ?? null]));
	}

	payload(state) {
		const result = { name: state.archive.name || null, version: state.archive.version || null };
		for (const field of ["company", "dictionary_type", "value", "inactive"]) {
			const control = state.dialog.fields_dict[field];
			if (control && state.archive.field_meta?.[field]) result[field] = control.get_value();
		}
		return result;
	}

	isDirty(state) { return JSON.stringify(this.payload(state)) !== state.original; }

	matches(candidate, archive) {
		return !!(archive?.name && archive.version && archive.field_meta && (!candidate.name || candidate.name === archive.name) &&
			archive.company === candidate.company && archive.dictionary_type === candidate.dictionary_type &&
			archive.value === (typeof candidate.value === "string" ? candidate.value.trim() : candidate.value) &&
			Number(archive.inactive) === Number(candidate.inactive));
	}

	guardClose(state) {
		const nativeHide = state.dialog.hide.bind(state.dialog);
		const close = () => { state.closed = true; nativeHide(); };
		state.dialog.hide = () => {
			if (state.closed || state.confirming) return;
			if (state.saving || state.unresolved) {
				frappe.msgprint(__(state.unresolved ? "保存结果尚未确认，请先核对保存结果。" : "正在保存，请稍候。")); return;
			}
			if (!this.isDirty(state)) { close(); return; }
			state.confirming = true;
			frappe.confirm(__("尚有未保存的修改，关闭并放弃修改？"),
				() => { state.confirming = false; queueMicrotask(close); }, () => { state.confirming = false; });
		};
		state.dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn().removeAttr("data-dismiss").on("click.hr-dictionary", () => state.dialog.hide());
		state.dialog.$wrapper.on("keydown.hr-dictionary", (event) => {
			if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); }
		});
	}

	async save(state) {
		if (state.closed || state.saving || state.unresolved || !state.archive.can_write || !state.dialog.get_values()) return;
		state.saving = true;
		state.candidate = structuredClone(this.payload(state));
		this.freeze(state, true);
		try {
			const response = await frappe.call({ method: "jifeng_erp.hr_dictionary.save_dictionary",
				args: { payload: state.candidate }, freeze: true, freeze_message: __("正在保存人事词典…") });
			if (!this.matches(state.candidate, response.message)) { state.unresolved = true; return; }
			await this.accept(state, response.message);
		} catch (error) { state.unresolved = !error?.responseJSON?.exc; }
		finally {
			state.saving = false;
			this.freeze(state, state.unresolved);
			if (state.unresolved) frappe.msgprint(__("保存结果尚未确认。草稿已保留，请点击核对保存结果。"));
		}
	}

	freeze(state, busy) {
		for (const [field, control] of Object.entries(state.dialog.fields_dict)) {
			const df = state.archive.field_meta?.[field];
			if (!df) continue;
			state.dialog.set_df_property(field, "read_only", !!(busy || df.read_only || !state.archive.can_write ||
				field === "dictionary_type" || (state.archive.name && field !== "inactive")));
		}
		state.dialog.get_primary_btn().prop("disabled", busy || !state.archive.can_write);
	}

	async accept(state, archive) {
		state.archive = archive;
		state.unresolved = false;
		await state.dialog.set_values(this.values(archive));
		state.original = JSON.stringify(this.payload(state));
		state.dialog.set_title(__("人事" + state.type));
		state.dialog.get_primary_btn().text(__("保存"));
		for (const entry of this.bridge.frames.values()) {
			try {
				const child = entry.iframe.contentWindow;
				if (child?.cur_list?.doctype !== "Jifeng HR Dictionary") continue;
				const positions = Array.from(child.document.querySelectorAll(".main-section,.list-view-container,.layout-main-section"))
					.map((element) => ({ element, top: element.scrollTop, left: element.scrollLeft }));
				Promise.resolve(child.cur_list.refresh()).then(() => {
					for (const { element, top, left } of positions) if (element.isConnected) {
						element.scrollTop = top; element.scrollLeft = left;
					}
				}).catch(() => {});
			} catch { /* pending frame */ }
		}
		frappe.show_alert({ message: __("人事词典已保存"), indicator: "green" });
	}

	async resolve(state) {
		if (state.closed || !state.unresolved || state.saving || !state.candidate) return;
		state.saving = true;
		try {
			const candidate = state.candidate;
			const response = await frappe.call({ method: "jifeng_erp.hr_dictionary." +
				(candidate.name ? "read_dictionary" : "resolve_creation"),
				args: candidate.name ? { name: candidate.name, dictionary_type: state.type } :
					{ company: candidate.company, dictionary_type: state.type, value: candidate.value }, freeze: true });
			const archive = candidate.name ? response.message : response.message?.archive;
			if (!this.matches(candidate, archive)) {
				frappe.msgprint(__("尚未核对到与草稿一致的结果。请保留草稿，稍后核对。")); return;
			}
			await this.accept(state, archive);
		} catch { /* preserve the held draft and use only read calls for verification */ }
		finally { state.saving = false; this.freeze(state, state.unresolved); }
	}

	choose({ type, company, current, getValue, apply, onHide }) {
		if (!company || !current()) return;
		let revision = 0, disposed = false, selecting = false;
		const dialog = new frappe.ui.Dialog({ title: "人事" + type,
			fields: [{ fieldname: "search", fieldtype: "Data", label: "关键词" }, { fieldname: "results", fieldtype: "HTML" }],
			no_submit_on_enter: true });
		const host = dialog.fields_dict.results.$wrapper;
		const load = async () => {
			if (disposed || selecting) return;
			if (!current()) { host.empty().text(__("公司或档案状态已变化，请关闭后重新选择。")); return; }
			const sequence = ++revision;
			host.empty().text(__("正在查询…"));
			try {
				const response = await frappe.call({ method: "jifeng_erp.hr_dictionary.list_dictionary",
					args: { dictionary_type: type, company, search: dialog.get_value("search") || "" } });
				if (disposed || sequence !== revision || !current()) return;
				host.empty();
				const rows = response.message || [];
				if (!rows.length) host.text(__("没有可选记录。"));
				for (const row of rows) $("<button type='button' class='btn btn-default btn-sm jfe-dictionary-choice'></button>")
					.text(row.value).on("click", async () => {
						if (disposed || selecting || !current()) return;
						const held = getValue?.();
						selecting = true;
						host.find("button").prop("disabled", true);
						try {
							const response = await frappe.call({ method: "jifeng_erp.hr_dictionary.read_dictionary",
								args: { name: row.name, dictionary_type: type } });
							const archive = response.message;
							if (disposed || sequence !== revision || !current() || (getValue && getValue() !== held)) return;
							if (!archive || archive.company !== company || archive.dictionary_type !== type ||
								Number(archive.inactive) || typeof archive.value !== "string") {
								frappe.msgprint(__("该记录已不可选，请重新查询。")); return;
							}
							if (await apply(archive.value)) dialog.hide();
						} catch { /* denied or failed reads never replace an archive value */ }
						finally { selecting = false; if (!disposed) host.find("button").prop("disabled", false); }
					}).appendTo(host);
			} catch { if (!disposed && sequence === revision && current()) host.empty().text(__("查询失败，请重新查询。")); }
		};
		dialog.fields_dict.search.df.onchange = load;
		dialog.set_primary_action(__("查询"), load);
		dialog.add_custom_action(__("关闭"), () => dialog.hide());
		dialog.on_hide = () => { disposed = true; revision++; onHide?.(dialog); };
		dialog.show();
		load();
		return dialog;
	}
};
