/* Files only prepare category drafts; the grid's Save remains the write action. */
window.JifengInventoryCategoryIO = {
	async download(workbench, method, filename) {
		if (workbench.getCloseBlock() || !workbench.company || workbench.disposed) return;
		const company = workbench.company; workbench.busy = true; workbench.freeze(); workbench.updateStatus(); let url;
		try {
			const reply = await fetch("/api/method/jifeng_erp.inventory_category_io." + method + "?" + new URLSearchParams({ kind: workbench.kind, company }), { credentials: "same-origin" });
			if (!reply.ok || reply.headers.get("Content-Type")?.split(";")[0] !== "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") throw new Error("Category download failed");
			const blob = await reply.blob(); if (workbench.disposed || workbench.company !== company || !blob.size) return;
			url = URL.createObjectURL(blob); const link = workbench.element("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
		} catch { if (!workbench.disposed) frappe.msgprint(__("类别文件未能下载，请核对权限后重试。")); }
		finally { if (url) URL.revokeObjectURL(url); workbench.busy = false; workbench.freeze(); workbench.updateStatus(); }
	},
	exportFile(workbench) { return this.download(workbench, "export_categories", workbench.kind + "类别.xlsx"); },
	async importFile(workbench) {
		if (workbench.getCloseBlock() || !workbench.company || workbench.disposed) return;
		if (workbench.isDirty()) { frappe.msgprint(__("请先保存或放弃当前修改，再导入类别。")); return; }
		const company = workbench.company, scopeRevision = workbench.revisions.scope; let closed = false, parsing = false, draft;
		const dialog = new frappe.ui.Dialog({ title: workbench.kind + "类别导入", fields: [{ fieldname: "file", fieldtype: "HTML" }],
			primary_action_label: __("加入待保存修改"), primary_action: () => {
				if (!draft || parsing || closed || !workbench.currentScope(company, scopeRevision)) return;
				this.stage(workbench, draft); dialog.hide();
			} });
		const host = dialog.fields_dict.file.$wrapper.get(0), file = workbench.element("input"), status = workbench.element("p"); file.type = "file"; file.accept = ".xlsx";
		const template = workbench.button("下载导入模板", async () => { workbench.chooserOpen = false; try { await this.download(workbench, "download_template", workbench.kind + "类别导入模板.xlsx"); } finally { if (!closed) { workbench.chooserOpen = true; workbench.freeze(); } } });
		host.append(template, file, status); workbench.chooserOpen = true; workbench.freeze(); dialog.get_primary_btn().prop("disabled", true);
		dialog.on_hide = () => { closed = true; workbench.chooserOpen = false; workbench.freeze(); };
		file.addEventListener("change", async () => {
			const selected = file.files?.[0]; draft = null; dialog.get_primary_btn().prop("disabled", true);
			if (!selected || !selected.name.toLowerCase().endsWith(".xlsx") || selected.size > 1024 * 1024) { status.textContent = __("请选择不超过1MiB的.xlsx文件。"); return; }
			parsing = true; file.disabled = true; status.textContent = __("正在读取文件…");
			try {
				const bytes = new Uint8Array(await selected.arrayBuffer()); let binary = "";
				for (let index = 0; index < bytes.length; index += 8192) binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
				const reply = await frappe.call({ method: "jifeng_erp.inventory_category_io.parse_import", args: { kind: workbench.kind, company, filename: selected.name, content_base64: btoa(binary) } });
				if (closed || !workbench.currentScope(company, scopeRevision)) return;
				if (reply.message?.kind !== workbench.kind || reply.message.company !== company || !Array.isArray(reply.message.rows)) throw new Error("Wrong import scope");
				draft = reply.message; status.textContent = draft.rows.length + __("行修改将加入两张表，请核对后保存。"); dialog.get_primary_btn().prop("disabled", !draft.rows.length);
			} catch { if (!closed) status.textContent = __("文件未能读取，请核对模板、公司和权限。"); }
			finally { parsing = false; file.disabled = false; }
		}); dialog.show();
	},
	stage(workbench, draft) {
		if (draft.kind !== workbench.kind || draft.company !== workbench.company || workbench.isDirty() || workbench.busy || workbench.unresolved) return;
		const original = { records: workbench.records, rows: workbench.rows, selected: workbench.selected };
		try {
			workbench.revisions.major++; workbench.revisions.minor++; workbench.panels.major.loading = workbench.panels.minor.loading = false;
			workbench.records = new Map(); workbench.rows = { major: [], minor: [] };
			for (const source of Object.values(draft.sources || {}).filter(source => source.dictionary_type === "大类")) workbench.remember(source, "major");
			for (const row of draft.rows.filter(row => row.record_type === "major")) {
				let record = row.name ? workbench.records.get(row.name) : workbench.newRow("major");
				if (!record) throw new Error("Missing imported parent");
				if (!row.name) { workbench.records.delete(record.key); record.key = row.row_id; workbench.records.set(record.key, record); }
				record.rowId = row.row_id; Object.assign(record.values, row.values); record.touched = true;
			}
			for (const row of draft.rows.filter(row => row.record_type === "minor")) {
				const parentKey = row.category || row.parent_row_id, parent = workbench.records.get(parentKey); if (!parent) throw new Error("Missing imported parent");
				workbench.selected = parentKey; let record = row.name ? workbench.remember(draft.sources[row.name], "minor", parentKey, row.category_version) : workbench.newRow("minor");
				if (!record) throw new Error("Missing imported child");
				if (!row.name) { workbench.records.delete(record.key); record.key = row.row_id; workbench.records.set(record.key, record); }
				record.rowId = row.row_id; record.association = row.operation === "associate"; Object.assign(record.values, row.values || {}); record.touched = true;
			}
			workbench.rows.major = Array.from(workbench.records.values()).filter(record => record.type === "major").map(record => record.key);
			workbench.selected = workbench.rows.major[0] || null; workbench.rows.minor = Array.from(workbench.records.values()).filter(record => record.type === "minor" && record.parentKey === workbench.selected).map(record => record.key);
			workbench.render(); workbench.updateStatus(); workbench.freeze();
		} catch (error) { Object.assign(workbench, original); workbench.render(); workbench.freeze(); throw error; }
	}
};
