/* Archive actions stay separate from the read-only inventory browse. */
window.JifengInventoryArchiveList = class JifengInventoryArchiveList extends window.JifengInventoryCatalog {
	constructor(host, kind, bridge) {
		super(host, kind); this.bridge = bridge; this.statusFilter = "全部"; this.openingEditor = false;
		this.exporting = false; this.exportController = null; this.freezeExport();
	}
	call(method, args) { return frappe.call({ method: "jifeng_erp.inventory_archive_list." + method, args }); }
	build() {
		this.fields = [...this.fields, ["$owner", "操作"], ["$creation", "日期"],
			["$modified_by", "修改人"], ["$modified", "修改日期"], ["$edit", "修改"], ["$delete", "删除"]];
		super.build();
		const toolbar = this.host.firstElementChild;
		toolbar.prepend(this.button("新增", () => this.openEditor(), "primary"));
		const unavailable = this.element("div", undefined, "jfe-inventory-archive-actions");
		for (const label of ["图片模式", "列表图文", "导出", "导入", "保存", "更新简拼", "打印"]) {
			if (label === "导出") {
				this.exportButton = this.button(label, () => this.exportArchives());
				this.exportButton.title = __("导出当前公司、种类、关键词和状态的全部可见已保存档案；需导出权限。图片列为状态文字，旧导出模板尚未核实。");
				unavailable.append(this.exportButton); continue;
			}
			if (label === "导入") {
				this.importButton = this.button(label, () => this.openImport());
				this.importButton.title = __("选择单个 .xlsx 文件，按导入模板的列名准备文件，预览全部行后确认导入。");
				unavailable.append(this.importButton);
				this.importTemplateButton = this.button("下载导入模板", () => this.importer().downloadTemplate());
				unavailable.append(this.importTemplateButton); continue;
			}
			const button = this.button(label, () => {}); button.disabled = true;
			button.title = __(label === "保存" ? "请打开档案后保存。" : "此操作尚未开放。"); unavailable.append(button);
		}
		this.statusChoice = this.element("select"); this.statusChoice.setAttribute("aria-label", __("档案状态"));
		for (const label of ["全部", "在用", "停用"]) {
			const option = this.element("option", __(label)); option.value = label; this.statusChoice.append(option);
		}
		this.statusChoice.value = "全部";
		this.statusChoice.addEventListener("change", () => {
			this.statusFilter = this.statusChoice.value; this.invalidate("档案状态已改变，请查询。");
		});
		unavailable.append(this.statusChoice); toolbar.after(unavailable);
	}
	freezeExport() {
		const blocked = !this.companyScope || this.disposed || this.openingEditor || this.exporting;
		if (this.exportButton) this.exportButton.disabled = !!(blocked || this.archiveImporter?.getCloseBlock());
		if (this.importButton) this.importButton.disabled = !!(blocked || this.archiveImporter?.getCloseBlock());
		if (this.importTemplateButton) this.importTemplateButton.disabled = !!(blocked || this.archiveImporter?.getCloseBlock());
	}
	importer() { return this.archiveImporter ||= new window.JifengInventoryArchiveImport(this); }
	openImport() { return this.importer().open(); }
	renderPager() { super.renderPager(); this.freezeExport(); }
	cancelExport() {
		this.exportController?.abort(); this.exportController = null; this.exporting = false; this.freezeExport();
	}
	clearResults() { this.cancelExport(); super.clearResults(); this.archiveImporter?.scopeChanged(); }
	async exportArchives() {
		if (this.disposed || this.exporting || this.getCloseBlock()) return;
		this.changeScope();
		if (!this.companyScope) { frappe.msgprint(__("请选择公司后导出。")); return; }
		const args = { kind: this.kind, company: this.companyScope, search: this.search.value, status: this.statusFilter };
		const sequence = this.revision, controller = new AbortController();
		const current = () => this.exportController === controller && !controller.signal.aborted &&
			this.current(sequence, args) && !this.getCloseBlock();
		this.exporting = true; this.exportController = controller; this.freezeExport();
		this.status.textContent = __("正在导出当前筛选的全部已保存档案…");
		let url;
		try {
			const response = await fetch("/api/method/jifeng_erp.inventory_archive_export.export_archives?" + new URLSearchParams(args),
				{ method: "GET", credentials: "same-origin", headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }, signal: controller.signal });
			if (!current()) return;
			if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") throw new Error("Inventory export failed");
			const file = await response.blob();
			if (!current()) return;
			if (!file.size) throw new Error("Empty inventory export");
			url = URL.createObjectURL(file); const link = this.element("a");
			link.href = url; link.download = this.kind + "档案.xlsx"; document.body.append(link); link.click(); link.remove();
			this.status.textContent = __("已导出当前筛选的全部已保存档案；图片列为状态文字。");
		} catch { if (current()) this.status.textContent = __("导出未完成，请核对筛选和导出权限后重试。"); }
		finally {
			if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
			if (this.exportController === controller) { this.exportController = null; this.exporting = false; this.freezeExport(); }
		}
	}
	validArchive(row, company, name = null) {
		return super.validArchive(row, company, name) && row.audit &&
			Object.entries(row.audit).every(([field, value]) => ["owner", "creation", "modified_by", "modified"].includes(field) &&
				(value === null || typeof value === "string"));
	}
	validPage(result, args) {
		if (!result || !Number.isInteger(result.page) || result.page !== Math.min(args.page, Math.max(1, result.total_pages))) return false;
		return super.validPage(result, { ...args, page: result.page });
	}
	renderRows() {
		this.tbody.replaceChildren();
		for (const row of this.rows) {
			const tr = this.element("tr"); tr.tabIndex = 0; tr.setAttribute("aria-label", __("打开档案"));
			for (const [field] of this.fields) {
				const td = this.element("td");
				if (field === "image") this.imageCell(row.image, td);
				else if (field === "$edit") td.append(this.button("修改", () => this.openEditor(row.name)));
				else if (field === "$delete") {
					const button = this.button("删除", () => {}); button.disabled = true;
					button.title = __("删除尚未开放，请通过停用保留历史档案。"); td.append(button);
				} else td.textContent = String((field.startsWith("$") ? row.audit[field.slice(1)] : row.profile[field]) ?? "");
				tr.append(td);
			}
			tr.addEventListener("dblclick", (event) => { if (!event.target?.closest?.("button")) this.openEditor(row.name); });
			tr.addEventListener("keydown", (event) => {
				if (event.key === "Enter" && event.target === tr) { event.preventDefault(); this.openEditor(row.name); }
			});
			this.tbody.append(tr);
		}
	}
	async query(page = 1) {
		const pending = super.query(page);
		const scope = { revision: this.revision, company: this.companyScope, search: this.search.value,
			status: this.statusFilter, page: this.pageNumber(page) };
		if (scope.company && scope.page) this.pendingScope = scope;
		try { await pending; } finally { if (this.pendingScope === scope) this.pendingScope = null; }
		if (!this.disposed && this.total !== null) this.status.textContent = this.total ?
			__("查询完成，可点击修改或双击行打开档案。") : __("没有可见记录。");
	}
	async openEditor(name = null) {
		this.changeScope();
		const company = this.companyScope, sequence = this.revision;
		if (this.disposed || this.openingEditor) return;
		if (!company) { frappe.msgprint(__("请选择公司后新增或打开档案。")); return; }
		if (name && !this.rows.some((row) => row.name === name)) return;
		const current = () => !this.disposed && this.revision === sequence && company === this.companyScope &&
			company === (this.companyControl.get_value() || null);
		this.cancelExport(); this.openingEditor = true; this.freezeExport();
		try { await this.bridge.openInventory(this.kind, name, { company, isCurrent: current, listOwner: this }); }
		finally { this.openingEditor = false; this.freezeExport(); }
	}
	refreshSaved(archive) {
		const pending = this.pendingScope;
		const querying = pending && pending.revision === this.revision && pending.company === this.companyScope &&
			pending.search === this.search.value && pending.status === this.statusFilter;
		if (!this.disposed && (this.total !== null || querying) && archive.kind === this.kind && archive.profile?.company === this.companyScope) {
			return this.query(querying ? pending.page : this.page);
		}
	}
	editorCurrent(company) { return !this.disposed && company === this.companyScope && company === (this.companyControl.get_value() || null); }
	getCloseBlock() {
		const importBlock = this.archiveImporter?.getCloseBlock(); if (importBlock) return importBlock;
		if (this.openingEditor || this.bridge?.inventoryEditor?.loadingOwners?.has(this)) return __("正在读取档案，请稍候。");
		const active = this.bridge?.inventoryEditor?.active;
		if (active && [...active.values()].some((state) => state.listOwner === this && !state.closed)) return __("请先关闭档案录入窗口。");
		return "";
	}
	destroy() {
		if (this.archiveImporter && !this.archiveImporter.destroy()) return false;
		this.cancelExport(); super.destroy(); this.freezeExport(); return true;
	}
};
