/* Create an editable, source-bound Material Receipt from a submitted issue. */
frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.stock_entry_type !== "Material Issue" ||
			frm.doc.jf_material_return_against || !frm.has_perm("read")) return;
		frm.add_custom_button(__("领料退库"), async () => {
			if (frm.__jifeng_material_return_busy) return;
			frm.__jifeng_material_return_busy = true;
			try {
				const response = await frappe.call({
					method: "jifeng_erp.material_returns.make_material_return",
					type: "POST", args: { source_name: frm.doc.name }, freeze: true,
				});
				const result = response.message;
				if (!result || typeof result.name !== "string" || result.docstatus !== 0) {
					throw new Error("Invalid material return draft response");
				}
				frappe.set_route("Form", "Stock Entry", result.name);
			} catch (error) {
				frappe.msgprint(__("未能取得可编辑的领料退库草稿。请核对原领料、库存账和已有退库凭证后再试。"));
			} finally {
				frm.__jifeng_material_return_busy = false;
			}
		}, __("库存"));
	},
});
