// Keep native HRMS forms and dynamic Link controls; offer supported job changes.
(() => {
	const bind = (frm) => {
		frm.events.setup_employee_property_button = (form, table) => {
			const grid = form.fields_dict[table].grid, label = __("Add Employee Property");
			if (form.doc.docstatus !== 0) {
				grid.custom_buttons[label]?.addClass("hidden");
				return;
			}
			const open = () => {
				if (!form.doc.employee) {
					frappe.msgprint(__("Please select Employee first."));
					return;
				}
				frappe.call({
					method: "jifeng_erp.personnel_ui.get_job_properties",
					args: { employee: form.doc.employee, doctype: form.doctype },
					callback: (response) => show_properties(form, table, response.message || []),
				});
			};
			grid.add_custom_button(label, open).off("click").on("click.jifeng_job_property", open);
		};
	};
	const show_properties = (frm, table, options) => {
		if (!options.length) {
			frappe.msgprint(__("没有可办理的岗位属性，请核对员工字段权限。"));
			return;
		}
		const allowed = new Set(options.map((option) => option.value));
		let selected, request = 0, requestedIdentity = null;
		const dialog = new frappe.ui.Dialog({
			title: __("变更岗位属性"),
			fields: [
				{ fieldname: "property", label: __("Select Property"), fieldtype: "Autocomplete", options, reqd: 1 },
				{ fieldname: "current", label: __("Current"), fieldtype: "Data", read_only: 1 },
				{ fieldname: "new_value", label: __("New"), fieldtype: "Data" },
			],
			primary_action_label: __("Add to Details"),
			primary_action: () => {
				const values = dialog.get_values();
				if (!values || !selected || values.property !== selected.fieldname) return;
				if ((frm.doc[table] || []).some((row) => row.fieldname === selected.fieldname)) {
					frappe.msgprint(__("Property already added"));
					return;
				}
				if (String(selected.current ?? "") === String(values.new_value ?? "")) {
					frappe.msgprint(__("Nothing to change"));
					return;
				}
				frm.add_child(table, { ...selected, new: values.new_value });
				frm.refresh_field(table);
				frm.fields_dict[table].grid.wrapper.find(".grid-add-row").hide();
				dialog.hide();
			},
		});
		dialog.get_primary_btn().attr("disabled", true);
		dialog.fields_dict.property.df.onchange = () => {
			const fieldname = dialog.get_value("property"), employee = frm.doc.employee;
			const identity = JSON.stringify([employee, fieldname]);
			// Autocomplete can fire again on blur after its first response. Keep the
			// existing Link control and the value the user has already started typing.
			if (identity === requestedIdentity) return;
			requestedIdentity = identity;
			const token = ++request;
			selected = null;
			dialog.get_primary_btn().attr("disabled", true);
			dialog.set_value("current", "");
			if (!allowed.has(fieldname)) return;
			frappe.call({
				method: "hrms.hr.utils.get_employee_field_property",
				args: { employee, fieldname },
				error: () => {
					if (token === request) requestedIdentity = null;
				},
				callback: (response) => {
					if (token !== request) return;
					if (!response.message || frm.doc.employee !== employee) {
						requestedIdentity = null;
						return;
					}
					const field = response.message;
					selected = { fieldname, property: __(field.label), current: field.value };
					dialog.set_value("current", field.value);
					dialog.replace_field("new_value", {
						fieldname: "new_value", label: __("New"), fieldtype: field.datatype,
						options: field.options || "", default: "",
					}, true);
					dialog.get_primary_btn().attr("disabled", false);
				},
			});
		};
		dialog.show();
	};
	const doctype = cur_frm.doctype;
	if (["Employee Promotion", "Employee Transfer"].includes(doctype)) {
		frappe.ui.form.on(doctype, {
			setup: bind,
			refresh: (frm) => {
				bind(frm);
				const table = doctype === "Employee Promotion" ? "promotion_details" : "transfer_details";
				frm.events.setup_employee_property_button(frm, table);
				if (doctype === "Employee Transfer") {
					frm.set_df_property("create_new_employee_id", "read_only", 1);
					frm.set_df_property("create_new_employee_id", "hidden", frm.doc.create_new_employee_id ? 0 : 1);
				}
			},
		});
	}
})();
