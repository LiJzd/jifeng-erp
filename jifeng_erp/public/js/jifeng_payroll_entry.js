(() => {
	const pendingForms = new WeakSet();
	const confirmationForms = new WeakSet();
	const method = "jifeng_erp.payroll_completion.complete_payroll_accrual";

	function validResult(result, frm) {
		return !!result && result.status === "completed" && result.payroll_entry === frm.doc.name &&
			typeof result.journal_entry === "string" && result.journal_entry.trim() !== "" &&
			typeof result.already_completed === "boolean" && Array.isArray(result.salary_slips) &&
			result.salary_slips.length > 0 && result.salary_slips.every(name => typeof name === "string" && name.trim() !== "");
	}

	frappe.ui.form.on("Payroll Entry", {
		refresh(frm) {
			if (Number(frm.doc.docstatus) !== 1 || frm.doc.status === "Queued") return;

			const button = frm.add_custom_button(__("提交工资条并计提"), () => {
				if (pendingForms.has(frm) || confirmationForms.has(frm)) return;
				confirmationForms.add(frm);
				frappe.confirm(
					__("此操作会提交本工资运行中尚未提交的工资条，并统一生成月度工资计提凭证。是否继续？"),
					async () => {
						confirmationForms.delete(frm);
						if (pendingForms.has(frm)) return;
						pendingForms.add(frm);
						button.prop("disabled", true);
						try {
							const response = await frappe.call({
								method,
								type: "POST",
								args: { payroll_entry: frm.doc.name },
								freeze: true,
								freeze_message: __("正在提交工资条并生成月度计提…"),
							});
							const result = response && response.message;
							if (!validResult(result, frm)) {
								frappe.msgprint({
									title: __("工资计提结果待核对"),
									message: __("服务器未返回可核验的工资运行和计提凭证信息。页面将重新读取；请核对实际状态后再操作。"),
									indicator: "orange",
								});
								await frm.reload_doc();
								return;
							}
							await frm.reload_doc();
							frm.add_custom_button(__("查看月度计提凭证"), () =>
								frappe.set_route("Form", "Journal Entry", result.journal_entry), __("工资处理"));
							frappe.show_alert({
								message: result.already_completed ? __("月度工资计提已完成。") : __("工资条已提交，月度工资计提已生成。"),
								indicator: "green",
							});
						} finally {
							pendingForms.delete(frm);
							button.prop("disabled", false);
						}
					},
					() => confirmationForms.delete(frm),
				);
			});
			button.addClass("btn-primary");
		},
	});
})();
