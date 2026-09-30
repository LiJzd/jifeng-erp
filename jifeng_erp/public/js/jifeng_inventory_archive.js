/* The two stock archives keep their old field order and native Item identity. */
window.JifengInventoryArchive = class JifengInventoryArchive {
	constructor(bridge) {
		this.bridge = bridge;
		this.active = new Map();
		this.opening = new Map();
	}

	requestID() {
		if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
		const bytes = crypto.getRandomValues(new Uint8Array(16));
		bytes[6] = (bytes[6] & 15) | 64;
		bytes[8] = (bytes[8] & 63) | 128;
		const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
		return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
	}

	async open(kind, name, copyFrom) {
		const key = kind + ":" + (copyFrom ? "copy:" + copyFrom : name || "new");
		if (this.active.has(key)) { this.active.get(key).dialog.show(); return this.active.get(key).dialog; }
		if (this.opening.has(key)) return this.opening.get(key);
		const pending = this.load(kind, name, copyFrom, key).finally(() => this.opening.delete(key));
		this.opening.set(key, pending);
		return pending;
	}

	async load(kind, name, copyFrom, key) {
		const response = await frappe.call({ method: "jifeng_erp.inventory_archive." +
			(name ? "read_archive" : "init_archive"),
			args: name ? { kind, name } : { kind, copy_from: copyFrom || null }, freeze: true });
		const archive = response.message;
		if (!archive) return;
		const bindings = [], fields = [{ fieldname: "archive_fields", fieldtype: "Section Break",
			css_class: "jfe-inventory-fields" }];
		const add = (group, fieldname, label, overrides = {}) => {
			const df = archive.field_meta?.[group]?.[fieldname];
			if (!df) return;
			const controlName = group + "_" + fieldname;
			fields.push({ ...df, ...overrides, fieldname: controlName, label,
				read_only: !!(df.read_only || overrides.read_only || !archive.can_write),
				default: archive[group]?.[fieldname] ?? null });
			bindings.push({ group, fieldname, controlName, locked: !!overrides.read_only });
		};
		add("profile", "legacy_number", "编号");
		add("profile", "legacy_code", "代码");
		add("profile", "major_category", "大类");
		add("profile", "minor_category", "小类");
		add("profile", "old_number", "旧号");
		add("profile", "specification", "规格");
		if (kind === "材料") { add("profile", "shape", "形状"); add("profile", "color", "颜色"); }
		add("profile", "display_name", "名称", { description: "档案名称；业务物料名称见下方。" });
		add("profile", "location", "库位");
		if (kind === "物料") add("profile", "material", "材质");
		add("profile", "display_uom", "单位", { description: "档案单位原文；实际库存单位见下方。" });
		add("profile", "list_price", "单价", { description: "档案参考价。" });
		add("profile", "processing_price", "加工价");
		add("profile", "weight", "重量");
		add("profile", "supplier", "供应商");
		add("profile", "upper_limit", "上限");
		add("profile", "lower_limit", "下限");
		add("profile", "conversion_uom", "换算单位");
		add("profile", "conversion_value", "换算值", { description: "档案换算值，当前不参与出入库换算。" });
		add("profile", "owner_name", "负责");
		add("profile", "region", "区域");
		add("profile", "notes", "备注");
		fields.push({ fieldname: "copy_archive", fieldtype: "Check", label: "复制", default: 0,
			read_only: !archive.can_write });
		add("profile", "inactive", "停用", { description: "本公司档案状态。" });
		fields.push({ fieldname: "business_section", fieldtype: "Section Break", label: "公司与业务物料" });
		add("profile", "company", "公司", { read_only: !!archive.name });
		fields.push({ fieldname: "business_item", fieldtype: "Data", label: "业务物料编号", read_only: 1,
			default: archive.item_code || "保存后生成" },
			{ fieldname: "business_name", fieldtype: "Data", label: "业务物料名称", read_only: 1,
				default: archive.item_name || "" });
		add("item", "item_group", "业务物料组", { read_only: !!archive.name });
		add("item", "stock_uom", "业务库存单位", { read_only: !!archive.name });
		const dialog = new frappe.ui.Dialog({ title: (archive.name ? "" : "新增") + kind + "档案",
			size: "extra-large", fields, no_submit_on_enter: true });
		dialog.$wrapper.addClass("jfe-inventory-editor");
		const state = { archive, kind, key, dialog, bindings, requestId: this.requestID(),
			saving: false, unresolved: false, confirming: false, closed: false };
		this.active.set(key, state);
		if (archive.can_write) dialog.set_primary_action(archive.name ? "保存" : "新增", () => this.save(state));
		dialog.add_custom_action(__("核对保存结果"), () => this.resolve(state));
		dialog.add_custom_action(__("关闭"), () => dialog.hide());
		this.guardClose(state);
		await dialog.set_values(this.initialValues(state));
		dialog.fields_dict.copy_archive.df.onchange = () => {
			if (dialog.get_value("copy_archive")) this.copy(state);
		};
		for (const [field, type] of [["major_category", "大类"], ["minor_category", "小类"],
			["shape", "形状"], ["color", "颜色"]]) {
			const control = dialog.fields_dict["profile_" + field];
			if (!control || control.df.read_only) continue;
			$("<button type='button' class='btn btn-xs btn-default'>选择</button>")
				.on("click", () => this.selectDictionary(state, field, type)).appendTo(control.$wrapper);
		}
		state.original = JSON.stringify(this.payload(state));
		const beforeUnload = (event) => {
			if (state.saving || state.unresolved || this.isDirty(state)) { event.preventDefault(); event.returnValue = ""; }
		};
		window.addEventListener("beforeunload", beforeUnload);
		dialog.on_hide = () => { window.removeEventListener("beforeunload", beforeUnload); this.active.delete(key); };
		dialog.show();
		return dialog;
	}

	initialValues(state) {
		return Object.fromEntries(state.bindings.map(({ group, fieldname, controlName }) =>
			[controlName, state.archive[group]?.[fieldname] ?? null]));
	}

	payload(state) {
		const result = { kind: state.kind, name: state.archive.name || null,
			profile_version: state.archive.profile_version || null,
			item_version: state.archive.item_version || null,
			request_id: state.archive.name ? null : state.requestId,
			profile: {}, item: {} };
		for (const { group, fieldname, controlName, locked } of state.bindings) {
			const control = state.dialog.fields_dict[controlName];
			const df = state.archive.field_meta?.[group]?.[fieldname];
			if (!control || !df || df.read_only || control.df.read_only || locked ||
				(group === "item" && state.archive.name)) continue;
			result[group][fieldname] = control.get_value();
		}
		return result;
	}

	isDirty(state) { return JSON.stringify(this.payload(state)) !== state.original; }

	guardClose(state) {
		const hide = state.dialog.hide.bind(state.dialog);
		const close = () => { state.closed = true; hide(); };
		state.dialog.hide = () => {
			if (state.closed || state.confirming) return;
			if (state.saving || state.unresolved) {
				frappe.msgprint(__(state.saving ? "正在保存，请稍候。" : "保存结果尚未确认，请先核对保存结果。")); return;
			}
			if (!this.isDirty(state)) { close(); return; }
			state.confirming = true;
			frappe.confirm(__("尚有未保存的修改，关闭并放弃修改？"),
				() => { state.confirming = false; queueMicrotask(close); },
				() => { state.confirming = false; });
		};
		state.dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn().removeAttr("data-dismiss").on("click.inventory", () => state.dialog.hide());
		state.dialog.$wrapper.on("keydown.inventory", (event) => {
			if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); }
		});
	}

	async save(state) {
		if (state.saving || state.unresolved || !state.dialog.get_values()) return;
		state.saving = true;
		state.candidate = structuredClone(this.payload(state));
		state.dialog.get_primary_btn().prop("disabled", true);
		try {
			const response = await frappe.call({ method: "jifeng_erp.inventory_archive.save_archive",
				args: { payload: state.candidate }, freeze: true, freeze_message: __("正在保存档案…") });
			if (!response.message?.name) { state.unresolved = true; return; }
			await this.accept(state, response.message);
		} catch (error) {
			// A server exception confirms the atomic operation was rolled back.
			// Transport errors and malformed success replies require a read first.
			state.unresolved = !error?.responseJSON?.exc;
		} finally {
			state.saving = false;
			if (state.unresolved) this.freezeDraft(state);
			state.dialog.get_primary_btn().prop("disabled", state.unresolved || !state.archive.can_write);
			if (state.unresolved) frappe.msgprint(__("保存结果尚未确认。草稿已保留，请点击核对保存结果。"));
		}
	}

	async accept(state, archive) {
		state.archive = archive;
		state.unresolved = false;
		await state.dialog.set_values(this.initialValues(state));
		state.dialog.set_value("business_item", archive.item_code || "");
		state.dialog.set_value("business_name", archive.item_name || "");
		for (const { group, fieldname, controlName } of state.bindings) {
			const df = archive.field_meta?.[group]?.[fieldname];
			state.dialog.set_df_property(controlName, "read_only", !!(!df || df.read_only || !archive.can_write ||
				(group === "item" || fieldname === "company")));
		}
		state.dialog.set_df_property("copy_archive", "read_only", !archive.can_write);
		state.original = JSON.stringify(this.payload(state));
		state.dialog.set_title(__(state.kind + "档案"));
		state.dialog.get_primary_btn().text(__("保存"));
		this.refreshLists();
		frappe.show_alert({ message: __("档案已保存"), indicator: "green" });
	}

	freezeDraft(state) {
		for (const { controlName } of state.bindings) state.dialog.set_df_property(controlName, "read_only", true);
		state.dialog.set_df_property("copy_archive", "read_only", true);
	}

	refreshLists() {
		for (const cached of this.bridge.frames.values()) {
			try {
				const child = cached.iframe.contentWindow;
				if (child?.cur_list?.doctype !== "Jifeng Inventory Profile") continue;
				const positions = Array.from(child.document.querySelectorAll(".main-section,.list-view-container,.layout-main-section"))
					.map((element) => ({ element, top: element.scrollTop, left: element.scrollLeft }));
				Promise.resolve(child.cur_list.refresh()).then(() => {
					for (const { element, top, left } of positions) if (element.isConnected) {
						element.scrollTop = top; element.scrollLeft = left;
					}
				}).catch(() => {});
			} catch { /* pending frame */ }
		}
	}

	async resolve(state) {
		if (!state.unresolved || state.saving) return;
		state.saving = true;
		try {
			const existing = !!state.candidate?.name;
			const response = await frappe.call({ method: "jifeng_erp.inventory_archive." +
				(existing ? "read_archive" : "resolve_creation"),
				args: existing ? { name: state.candidate.name, kind: state.kind } :
					{ request_id: state.requestId, kind: state.kind }, freeze: true });
			const archive = existing ? response.message : response.message?.archive;
			const matches = archive?.name && Object.entries(state.candidate.profile).every(([field, value]) =>
				String(archive.profile?.[field] ?? "") === String(value ?? ""));
			if (!matches) {
				frappe.msgprint(__("尚未核对到与这份草稿一致的保存结果。请保留草稿，稍后核对；不会自动再次保存。")); return;
			}
			await this.accept(state, archive);
		} catch { /* native RPC displays errors; retain the unknown state */ }
		finally { state.saving = false; state.dialog.get_primary_btn().prop("disabled", state.unresolved || !state.archive.can_write); }
	}

	async copy(state) {
		await state.dialog.set_value("copy_archive", 0);
		if (state.saving || state.unresolved) return;
		if (!state.archive.name) { frappe.msgprint(__("先保存档案，再复制。")); return; }
		if (this.isDirty(state)) { frappe.msgprint(__("先保存当前修改，再复制。")); return; }
		try { await this.open(state.kind, null, state.archive.name); } catch { /* native RPC */ }
	}

	async selectDictionary(state, field, type) {
		if (state.saving || state.unresolved) return;
		const company = state.dialog.get_value("profile_company");
		if (!company) { frappe.msgprint(__("先选择公司。")); return; }
		let revision = 0, disposed = false;
		const dialog = new frappe.ui.Dialog({ title: state.kind + type,
			fields: [{ fieldname: "search", fieldtype: "Data", label: "查询" },
				{ fieldname: "results", fieldtype: "HTML" }] });
		const host = dialog.fields_dict.results.$wrapper;
		const load = async () => {
			const current = ++revision;
			host.empty().text(__("正在查询…"));
			try {
				const response = await frappe.call({ method: "jifeng_erp.inventory_archive.list_dictionary",
					args: { kind: state.kind, dictionary_type: type, company, search: dialog.get_value("search") || "" } });
				if (disposed || current !== revision) return;
				host.empty();
				const rows = response.message || [];
				if (!rows.length) host.text(__("没有可选记录。"));
				for (const row of rows) $("<button type='button' class='btn btn-default btn-sm jfe-dictionary-choice'></button>")
					.text(row.value).on("click", () => {
						if (state.closed || state.saving || state.unresolved ||
							state.dialog.get_value("profile_company") !== company) return;
						state.dialog.set_value("profile_" + field, row.value); dialog.hide();
					}).appendTo(host);
			} catch { if (!disposed && current === revision) host.empty().text(__("查询失败，请重新查询。")); }
		};
		dialog.fields_dict.search.df.onchange = load;
		dialog.on_hide = () => { disposed = true; revision++; };
		dialog.show();
		await load();
	}
};
