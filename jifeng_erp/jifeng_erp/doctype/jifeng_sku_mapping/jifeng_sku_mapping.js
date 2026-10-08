frappe.ui.form.on("Jifeng SKU Mapping", {
	refresh(frm) {
		if (frm.is_new() || !frm.has_perm("write") || !frm.doc.source_image ||
			frm.doc.image_sync_status !== "失败") return;

		frm.add_custom_button(__("重试图片同步"), () => {
			frappe.confirm(__("将按当前来源图片和允许域名重新排队。继续？"), () => {
				frappe.call({
					method: "jifeng_erp.images.retry_image",
					args: { mapping_name: frm.doc.name },
					freeze: true,
					freeze_message: __("正在排队图片同步…"),
					callback(response) {
						if (!response.exc) {
							frappe.show_alert({
								message: response.message?.message || __("图片同步状态已更新。"),
								indicator: "blue",
							});
							frm.reload_doc();
						}
					},
				});
			});
		}, __("图片"));
	},
});
