/* Create an editable, source-bound Material Receipt from a submitted issue. */
frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.jf_material_return_against && frm.has_perm("read")) {
			frm.add_custom_button(__("原领料批次/序列"), async () => {
				const response = await frappe.call({
					method: "jifeng_erp.material_returns.get_material_return_sources", type: "GET",
					args: { source_name: frm.doc.jf_material_return_against }, freeze: true,
				});
				const data = response.message;
				if (!data || data.source_name !== frm.doc.jf_material_return_against || !Array.isArray(data.rows)) return;
				const escape = value => frappe.utils.escape_html(String(value ?? ""));
				const rows = data.rows.flatMap(row => row.lots.map(lot => `<tr>
					<td>${escape(row.source_idx)}</td><td>${escape(row.item_code)}</td>
					<td>${escape(row.original_bundles.join("、"))}</td><td>${escape(lot.batch_no)}</td>
					<td>${escape(lot.serial_no)}</td><td>${escape(lot.original_qty)}</td>
					<td>${escape(lot.returned_qty)}</td><td>${escape(lot.remaining_qty)}</td>
					<td>${escape(row.stock_uom)}</td><td>${escape(row.warehouse)}</td></tr>`));
				frappe.msgprint({ title: __("原领料批次/序列"), wide: true,
					message: `<p>在退库明细原生批次/序列号字段或批次凭证选择器中，明确选择实际退回的原批次/序列号，并填写相同库存单位的数量。保存保留选择，提交时再核对剩余额度；不会自动选首批或编号。</p>
					<table class="table table-bordered"><thead><tr>
					<th>原行</th><th>物料</th><th>原批次凭证</th><th>批次</th><th>序列号</th>
					<th>原数量</th><th>已退数量</th><th>剩余数量</th><th>库存单位</th><th>原仓库</th>
					</tr></thead><tbody>${rows.join("") || '<tr><td colspan="10">原领料没有批次/序列号明细。</td></tr>'}</tbody></table>` });
			}, __("库存"));
		}
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
