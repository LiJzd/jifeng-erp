/* The old personnel layout writes both permission-controlled server records. */
window.JifengPersonnelArchive = class JifengPersonnelArchive {
	constructor(bridge) {
		this.bridge = bridge;
		this.opening = new Map();
		this.active = new Map();
	}

	async open(name) {
		const key = name || "new";
		if (this.active.has(key)) return this.active.get(key).dialog;
		if (this.opening.has(key)) return this.opening.get(key);
		const pending = this.load(name).finally(() => this.opening.delete(key));
		this.opening.set(key, pending);
		return pending;
	}

	async load(name) {
		const response = await frappe.call({ method: "jifeng_erp.personnel_archive." +
			(name ? "read_archive" : "init_archive"), args: name ? { name } : {}, freeze: true });
		const archive = response.message;
		if (!archive) return;
		const bindings = [];
		const fields = [{ fieldname: "company_section", fieldtype: "Section Break" }];
		const meta = archive.field_meta || {};
		const add = (group, fieldname, label, overrides = {}) => {
			const df = meta[group]?.[fieldname];
			if (!df) return;
			const controlName = group + "_" + fieldname;
			fields.push({ ...df, fieldname: controlName, label, ...overrides,
				read_only: !!(df.read_only || overrides.read_only || !archive.can_write),
				default: archive[group]?.[fieldname] ?? null });
			bindings.push({ group, fieldname, controlName, df, locked: !!overrides.read_only });
		};
		add("employee", "company", "公司", { read_only: !!archive.name });
		fields.push({ fieldname: "main_section", fieldtype: "Section Break", css_class: "jfe-personnel-fields" });
		add("employee", "department", "部门");
		add("employee", "employee_number", "编号");
		const nameMeta = meta.employee?.first_name;
		if (nameMeta) {
			fields.push({ fieldname: "archive_name", fieldtype: "Data", label: "姓名", reqd: 1,
				read_only: !archive.can_write || ["first_name", "middle_name", "last_name"].some(
					(field) => !meta.employee?.[field] || meta.employee[field].read_only),
				default: archive.employee.employee_name || archive.employee.first_name || "" });
		}
		add("supplement", "legacy_title", "职称");
		const gender = archive.employee?.gender;
		add("employee", "gender", "性别", { fieldtype: "Select", options: [
			{ value: "", label: "" }, { value: "Male", label: "男" }, { value: "Female", label: "女" },
			...(!gender || ["Male", "Female"].includes(gender) ? [] : [{ value: gender, label: __(gender) }]),
		] });
		add("supplement", "ethnicity", "民族");
		add("supplement", "education_level", "文化");
		add("supplement", "wage_method", "方式");
		add("supplement", "legacy_base_salary", "底薪");
		add("supplement", "legacy_time_value", "计时");
		add("employee", "cell_number", "手机");
		add("supplement", "specialty", "专长");
		add("employee", "bank_name", "银行");
		add("supplement", "bank_account_name", "户名");
		add("supplement", "dormitory", "宿舍");
		add("supplement", "legacy_account", "账号");
		add("employee", "date_of_birth", "生日");
		add("supplement", "identity_number", "身份证号码");
		add("supplement", "legacy_introduction", "介绍");
		add("employee", "reason_for_leaving", "离职原因", { read_only: 1 });
		add("employee", "date_of_joining", "入职日");
		add("employee", "relieving_date", "离职日", { read_only: 1 });
		add("supplement", "legacy_seniority", "工龄");
		add("supplement", "province", "省份");
		add("supplement", "legacy_city", "城市");
		add("supplement", "term_type", "类型");
		add("supplement", "legacy_address", "地址");
		add("supplement", "legacy_marital_status", "婚否");
		add("supplement", "legacy_notes_text", "备注文本");
		add("supplement", "notes", "备注");
		add("employee", "designation", "当前岗位");
		add("employee", "status", "在职状态", { read_only: 1 });
		add("employee", "image", "相片", this.uploadOptions(archive, "employee", "image"));
		add("supplement", "identity_image", "身份证图片", this.uploadOptions(archive, "supplement", "identity_image"));
		fields.push({ fieldname: "records_section", fieldtype: "Section Break" },
			{ fieldname: "record_tabs", fieldtype: "HTML" });
		const tables = {
			work_history: { label: "工作经历", columns: [["project", "项目"], ["work_content", "工作内容"],
				["reason_for_leaving", "离职原因"], ["from_date", "开始"], ["to_date", "结束"],
				["company_name", "公司"], ["title", "职务"]] },
			salary_history: { label: "调薪记录", columns: [["project", "项目"], ["content", "内容"],
				["from_date", "开始"], ["to_date", "结束"], ["wage", "薪资"], ["title", "职务"]] },
			promotion_history: { label: "升职记录", columns: [["project", "项目"], ["work_content", "工作内容"],
				["from_date", "开始"], ["to_date", "结束"], ["title", "职务"]] },
		};
		for (const [key, table] of Object.entries(tables)) {
			const tableMeta = meta.histories?.[key];
			if (!tableMeta) continue;
			fields.push({ fieldname: key, fieldtype: "Table", label: table.label,
				hidden: key !== "work_history", read_only: !!(tableMeta.read_only || !archive.can_write),
				cannot_add_rows: !!(tableMeta.read_only || !archive.can_write),
				cannot_delete_rows: !!(tableMeta.read_only || !archive.can_write),
				in_place_edit: true, data: structuredClone(archive.histories?.[key] || []),
				fields: table.columns.filter(([field]) => tableMeta.fields?.[field]).map(([fieldname, label]) => ({
					...tableMeta.fields[fieldname], fieldname, label, in_list_view: 1,
					columns: ["from_date", "to_date"].includes(fieldname) ? 1 :
						(key === "work_history" && ["project", "company_name"].includes(fieldname) ? 1 : 2),
					read_only: !!(tableMeta.fields[fieldname].read_only || tableMeta.read_only || !archive.can_write),
				})),
			});
		}
		fields.push({ fieldname: "education_history", fieldtype: "HTML", hidden: 1 });
		const dialog = new frappe.ui.Dialog({ title: archive.name ? "人事档案" : "新增人事档案",
			size: "extra-large", fields, no_submit_on_enter: true });
		dialog.$wrapper.addClass("jfe-personnel-editor");
		const state = { archive, dialog, bindings, tables, saving: false, confirming: false, closed: false };
		this.active.set(name || "new", state);
		this.renderTabs(state);
		this.guardClose(state);
		if (archive.can_write) dialog.set_primary_action(archive.name ? "保存" : "新增", () => this.save(state));
		dialog.add_custom_action(__("关闭"), () => dialog.hide());
		await dialog.set_values(this.initialValues(state));
		state.original = JSON.stringify(this.payload(state));
		const beforeUnload = (event) => {
			if (state.saving || JSON.stringify(this.payload(state)) !== state.original) {
				event.preventDefault();
				event.returnValue = "";
			}
		};
		window.addEventListener("beforeunload", beforeUnload);
		dialog.on_hide = () => {
			window.removeEventListener("beforeunload", beforeUnload);
			this.active.delete(name || "new");
		};
		dialog.show();
		return dialog;
	}

	uploadOptions(archive, group, fieldname) {
		const saved = group === "employee" ? archive.name : archive.supplement_name;
		return { read_only: !saved, description: saved ? "" : "先保存档案，再上传图片。",
			options: { doctype: group === "employee" ? "Employee" : "Jifeng Employee Supplement",
				docname: group === "employee" ? archive.name : archive.supplement_name,
				fieldname, make_attachments_public: 0,
				restrictions: { allowed_file_types: ["image/*"] } } };
	}

	initialValues(state) {
		const values = Object.fromEntries(state.bindings.map(({ group, fieldname, controlName }) =>
			[controlName, state.archive[group]?.[fieldname] ?? null]));
		if (state.dialog.fields_dict.archive_name) {
			values.archive_name = state.archive.employee.employee_name || state.archive.employee.first_name || "";
		}
		return values;
	}

	renderTabs(state) {
		const entries = [["work_history", "工作经历"], ["education_history", "教育经历"],
			["salary_history", "调薪记录"], ["promotion_history", "升职记录"]];
		const wrapper = state.dialog.fields_dict.record_tabs.$wrapper;
		wrapper.html('<div class="jfe-employee-tabs">' + entries.map(([key, label], index) =>
			'<button type="button" class="btn btn-sm ' + (index ? "btn-default" : "btn-primary") +
			'" data-record-tab="' + key + '">' + __(label) + '</button>').join("") + '</div>');
		wrapper.on("click", "[data-record-tab]", (event) => {
			const key = event.currentTarget.dataset.recordTab;
			for (const [field] of entries) {
				if (state.dialog.fields_dict[field]) state.dialog.set_df_property(field, "hidden", field !== key);
			}
			wrapper.find("button").removeClass("btn-primary").addClass("btn-default");
			$(event.currentTarget).addClass("btn-primary").removeClass("btn-default");
		});
		const education = state.dialog.fields_dict.education_history.$wrapper;
		const button = $('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("维护教育经历")).prop("disabled", !state.archive.name);
		education.append(button);
		if (!state.archive.name) education.append($('<p class="text-muted"></p>').text(__("先保存人员档案。")));
		button.on("click", () => this.bridge.openCreate({ title: "教育经历", kind: "page",
			route: ["Form", "Employee", state.archive.name] }));
	}

	payload(state) {
		const { archive, dialog } = state;
		const result = { name: archive.name || null, employee_modified: archive.employee_modified,
			supplement_modified: archive.supplement_modified, employee: {}, supplement: {}, histories: {} };
		for (const { group, fieldname, controlName } of state.bindings) {
			const control = dialog.fields_dict[controlName];
			if (!control?.df.read_only) result[group][fieldname] = control.get_value() ?? null;
		}
		const nameControl = dialog.fields_dict.archive_name;
		const originalName = archive.employee.employee_name || archive.employee.first_name || "";
		if (nameControl && !nameControl.df.read_only && (!archive.name || nameControl.get_value() !== originalName)) {
			result.employee.first_name = nameControl.get_value();
			result.employee.middle_name = "";
			result.employee.last_name = "";
		}
		for (const key of Object.keys(state.tables)) {
			const control = dialog.fields_dict[key];
			if (!control || control.df.read_only) continue;
			result.histories[key] = (control.get_value() || []).map((row) => {
				const values = row.name ? { name: row.name } : {};
				for (const df of control.df.fields) if (!df.read_only) values[df.fieldname] = row[df.fieldname] ?? null;
				return values;
			});
		}
		return result;
	}

	guardClose(state) {
		const { dialog } = state;
		const nativeHide = dialog.hide.bind(dialog);
		const close = () => { state.closed = true; nativeHide(); };
		dialog.hide = () => {
			if (state.closed || state.saving || state.confirming) return;
			if (JSON.stringify(this.payload(state)) !== state.original) {
				state.confirming = true;
				frappe.confirm(__("尚有未保存的修改，关闭并放弃修改？"),
					() => { state.confirming = false; queueMicrotask(close); },
					() => { state.confirming = false; });
			} else close();
		};
		dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		dialog.get_close_btn().removeAttr("data-dismiss").on("click.archive", () => dialog.hide());
		dialog.$wrapper.on("keydown.archive", (event) => {
			if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); dialog.hide(); }
		});
	}

	async save(state) {
		if (state.saving || !state.dialog.get_values()) return;
		state.saving = true;
		const button = state.dialog.get_primary_btn().prop("disabled", true);
		try {
			const response = await frappe.call({ method: "jifeng_erp.personnel_archive.save_archive",
				args: { payload: this.payload(state) }, freeze: true, freeze_message: __("正在保存人员档案…") });
			if (!response.message?.name) return;
			state.archive = response.message;
			await state.dialog.set_values(this.initialValues(state));
			for (const { group, fieldname, controlName, locked } of state.bindings) {
				if (["image", "identity_image"].includes(fieldname)) continue;
				const df = state.archive.field_meta?.[group]?.[fieldname];
				state.dialog.set_df_property(controlName, "read_only", !!(locked || !df || df.read_only ||
					!state.archive.can_write || (group === "employee" && fieldname === "company")));
			}
			for (const key of Object.keys(state.tables)) {
				const control = state.dialog.fields_dict[key];
				if (!control) continue;
				control.df.data = structuredClone(state.archive.histories?.[key] || []);
				control.grid.refresh();
			}
			// Uploads are bound to the now saved parent, with native File permissions.
			for (const group of ["employee", "supplement"]) {
				const field = group === "employee" ? "image" : "identity_image";
				const control = state.dialog.fields_dict[group + "_" + field];
				if (!control) continue;
				const df = state.archive.field_meta?.[group]?.[field];
				control.df.options = this.uploadOptions(state.archive, group, field).options;
				control.df.read_only = !!(!df || df.read_only || !state.archive.can_write);
				control.df.description = "";
				control.refresh();
			}
			state.original = JSON.stringify(this.payload(state));
			state.dialog.set_title(__("人事档案"));
			button.text(__("保存"));
			state.dialog.fields_dict.education_history.$wrapper.find("button").prop("disabled", false);
			state.dialog.fields_dict.education_history.$wrapper.find("p").remove();
			for (const cached of this.bridge.frames.values()) {
				try { cached.iframe.contentWindow?.cur_list?.refresh(); } catch { /* pending frame */ }
			}
			frappe.show_alert({ message: __("人员档案已保存"), indicator: "green" });
		} catch {
			// Frappe displays the server/network failure; keep this draft editable.
		} finally {
			state.saving = false;
			button.prop("disabled", false);
		}
	}
};
