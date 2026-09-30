frappe.ui.form.on("Employee Separation", {
	refresh(frm) {
		const processed = frm.doc.jf_exit_status === "已办理";
		const frozen = Boolean(processed || frm.doc.jf_exit_snapshot);
		frm.set_df_property("jf_last_working_day", "read_only", frozen ? 1 : 0);
		frm.set_df_property("jf_reason_for_leaving", "read_only", frozen ? 1 : 0);
		if (frm.doc.docstatus !== 1 || !(frm.perm || []).some((permission) => permission.write)) return;
		if (frm.doc.jf_exit_status === "已撤销") return;
		const method = processed ? "reverse_separation" : "finalize_separation";
		const label = processed ? __("撤销离职办理") : __("办理离职");
		frm.add_custom_button(label, () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("请先保存最后工作日和离职原因。"));
				return;
			}
			if (!processed && !frm.doc.jf_last_working_day) {
				frappe.msgprint(__("请填写实际最后工作日。"));
				return;
			}
			frappe.confirm(processed
				? __("将核对员工及后续计薪变更，符合条件时恢复原员工状态。交接记录将保留。")
				: __("员工将按本单最后工作日登记为离职。交接记录将保留，账号状态需单独管理。"),
				() => frappe.call({
					method: `jifeng_erp.personnel_separation.${method}`,
					args: { name: frm.doc.name }, freeze: true, freeze_message: label,
					callback: (response) => {
						if (response.message) frm.reload_doc();
					},
				}));
		});
	},
});
