/* Original product fields with explicit native Item provenance and private image bindings. */
window.JifengProductArchive = class JifengProductArchive {
	constructor(host, bridge = null) {
		this.host = host; this.disposed = false; this.revision = 0; this.opening = false; this.editor = null; this.rows = [];
		this.bridge = bridge; this.visible = true; this.exportRequest = null;
		this.companyScope = null; this.changingCompany = false;
		this.page = 1; this.pageSize = 100; this.total = null; this.totalPages = 0; this.pendingQuery = null;
		this.fields = [["legacy_number", "编号"], ["legacy_code", "代码"], ["major_category", "大类"], ["minor_category", "小类"],
			["old_number", "旧号"], ["brand", "品牌"], ["material", "材质"], ["shared_version", "共版"], ["patent_number", "专利号"],
			["display_name", "名称"], ["source", "来源"], ["designer", "设计"], ["pattern_maker", "打版"], ["modeler", "建模"],
			["style", "风格"], ["owner_name", "负责"], ["region", "区域"], ["display_uom", "单位"], ["list_price", "单价"],
			["blank_price", "毛胚价"], ["weight", "重量"], ["silver_weight", "银重"], ["stone_weight", "石重"], ["inactive", "停用"],
			["copy", "复制"], ["legacy_remarks", "备注"], ["notes", "注意事项"], ["profile_image", "产品图片"], ["patent_image", "专利图片"]];
		this.labels = Object.fromEntries(this.fields); this.build();
		this.beforeUnload = (event) => { if (this.isDirty() || this.getCloseBlock()) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener("beforeunload", this.beforeUnload); this.ready = Promise.resolve(true);
	}
	element(tag, text, className) {
		const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className; return node;
	}
	button(label, action, reason) {
		const node = this.element("button", __(label), "btn btn-default btn-sm"); node.type = "button";
		if (reason) { node.disabled = true; node.title = __(reason); } else node.addEventListener("click", action);
		return node;
	}
	call(method, args, write = false) {
		return frappe.call({ method: "jifeng_erp.product_archive." + method, args,
			...(write ? { freeze: true, freeze_message: __("正在保存产品档案…") } : {}) });
	}
	build() {
		this.host.classList.add("jfe-product-archive"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-product-archive-toolbar");
		const companyHost = this.element("div", undefined, "jfe-product-archive-company");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), onchange: () => this.changeScope() } });
		this.companyControl.$input.on("input.jfe-product-archive-company change.jfe-product-archive-company", () => this.changeScope());
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("代码、名称、供应商货号或规格"); this.search.setAttribute("aria-label", __("关键词"));
		this.search.addEventListener("input", () => this.resetList("关键词已改变，请查询。"));
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(); } });
		this.includeInactive = this.element("input"); this.includeInactive.type = "checkbox";
		this.includeInactive.addEventListener("change", () => { this.resetList("停用条件已改变，请查询。"); return this.query(1); });
		const inactive = this.element("label", undefined, "jfe-product-archive-inactive"); inactive.append(this.includeInactive, this.element("span", __("包含停用")));
		this.exportButton = this.button("导出", () => this.exportArchives());
		this.exportButton.title = __("导出当前公司、关键词及包含停用条件的全部已保存可见产品档案；需要完整字段读取及导出权限。采用本系统当前28字段模板，两图仅导状态文字；旧导出模板尚未核实。");
		this.cancelExportButton = this.button("取消导出", () => this.cancelExport("导出已取消。"));
		toolbar.append(this.button("新增", () => this.openEditor()), this.exportButton, this.cancelExportButton, this.button("导入", null, "导入暂不可用"),
			companyHost, this.search, this.button("查询", () => this.query()), inactive, this.button("打印", null, "打印暂不可用"),
			this.button("批量传图", null, "批量传图暂不可用；保存后可逐张上传"));
		this.status = this.element("p", __("请选择公司后查询，或新增产品档案。"), "jfe-product-archive-status"); this.status.setAttribute("role", "status");
		this.scroller = this.element("div", undefined, "jfe-product-archive-scroll");
		const table = this.element("table", undefined, "jfe-product-archive-table"); table.setAttribute("aria-label", __("产品档案列表"));
		const thead = this.element("thead"), headings = this.element("tr");
		for (const label of ["编号", "代码", "大类", "小类", "名称", "单位", "备注", "停用", "产品图片", "专利图片", "操作"]) headings.append(this.element("th", __(label)));
		thead.append(headings); this.tbody = this.element("tbody"); table.append(thead, this.tbody); this.scroller.append(table);
		const pager = this.element("div", undefined, "jfe-product-archive-toolbar"); pager.setAttribute("aria-label", __("产品档案分页"));
		this.previous = this.button("上页", () => this.query(this.page - 1)); this.next = this.button("下页", () => this.query(this.page + 1));
		this.pageLabel = this.element("span", "—"); this.pageLabel.setAttribute("aria-label", __("当前页"));
		this.goto = this.element("input"); this.goto.type = "text"; this.goto.inputMode = "numeric"; this.goto.maxLength = 10;
		this.goto.value = "1"; this.goto.setAttribute("aria-label", __("前往页码"));
		this.goto.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(this.goto.value); } });
		this.pageSizeControl = this.element("select"); this.pageSizeControl.setAttribute("aria-label", __("每页条数"));
		for (const size of [25, 50, 100]) { const option = this.element("option", String(size)); option.value = String(size); this.pageSizeControl.append(option); }
		this.pageSizeControl.value = String(this.pageSize);
		this.pageSizeControl.addEventListener("change", () => {
			const size = Number(this.pageSizeControl.value);
			if (![25, 50, 100].includes(size)) { this.pageSizeControl.value = String(this.pageSize); return; }
			this.pageSize = size; this.resetList("每页条数已改变，请查询。"); return this.query(1);
		});
		this.count = this.element("span", __("尚未查询"));
		pager.append(this.previous, this.pageLabel, this.next, this.goto, this.button("前往", () => this.query(this.goto.value)),
			this.element("span", __("每页")), this.pageSizeControl, this.element("span", __("条")), this.count);
		this.host.append(toolbar, this.status, this.scroller, pager); this.renderPager();
	}
	resetList(message) {
		this.cancelExport();
		this.revision++; this.pendingQuery = null; this.page = 1; this.total = null; this.totalPages = 0; this.rows = [];
		this.tbody.replaceChildren(); this.goto.value = "1"; this.renderPager(); this.status.textContent = __(message);
	}
	renderPager() {
		if (!this.previous) return;
		this.freezeExport();
		this.previous.disabled = this.pendingQuery !== null || this.total === null || this.page <= 1;
		this.next.disabled = this.pendingQuery !== null || this.total === null || this.page >= this.totalPages;
		this.pageLabel.textContent = this.total === null ? "—" : this.page + " / " + Math.max(1, this.totalPages);
		this.count.textContent = this.total === null ? __("尚未查询") : __("共") + " " + this.total + " " + __("条");
	}
	pageNumber(value) {
		if (typeof value === "string") { if (!/^[1-9][0-9]{0,9}$/.test(value)) return null; value = Number(value); }
		return Number.isInteger(value) && value >= 1 && value <= 2147483647 ? value : null;
	}
	setVisible(visible) {
		this.visible = !!visible; if (!this.visible) this.cancelExport(); this.freezeExport();
	}
	freezeExport() {
		if (this.exportButton) this.exportButton.disabled = !!(this.disposed || !this.visible || !this.companyScope ||
			this.changingCompany || this.opening || this.editor && !this.editor.closed || this.exportRequest);
		if (this.cancelExportButton) this.cancelExportButton.hidden = !this.exportRequest;
	}
	cancelExport(message = null) {
		const request = this.exportRequest; this.exportRequest = null; request?.controller.abort(); this.freezeExport();
		if (message && request && !this.disposed && this.visible) this.status.textContent = __(message);
	}
	currentExport(request) {
		const args = request.args;
		return this.exportRequest === request && !request.controller.signal.aborted && !this.disposed && this.visible &&
			!this.changingCompany && !this.opening && !(this.editor && !this.editor.closed) && !this.getCloseBlock() &&
			this.revision === request.revision && args.company === this.companyScope && args.company === (this.companyControl.get_value() || null) &&
			args.search === this.search.value && args.include_inactive === Number(this.includeInactive.checked);
	}
	async exportArchives() {
		if (this.disposed || !this.visible || this.exportRequest || this.changingCompany) return;
		const block = this.getCloseBlock();
		if (block || this.editor && !this.editor.closed) { frappe.msgprint(__(block || "请先关闭产品档案录入窗口后导出。")); return; }
		if ((this.companyControl.get_value() || null) !== this.companyScope && !await this.changeScope()) return;
		if (this.disposed || !this.visible || this.exportRequest || this.changingCompany || this.opening || this.editor && !this.editor.closed) return;
		if (!this.companyScope) { frappe.msgprint(__("请选择公司后导出。")); return; }
		const args = { company: this.companyScope, search: this.search.value, include_inactive: Number(this.includeInactive.checked) };
		const request = { args, revision: this.revision, controller: new AbortController() }; this.exportRequest = request; this.freezeExport();
		this.status.textContent = __("正在导出当前筛选的全部已保存产品档案…");
		let url;
		try {
			const response = await fetch("/api/method/jifeng_erp.product_archive_export.export_archives?" + new URLSearchParams(args),
				{ method: "GET", credentials: "same-origin", headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }, signal: request.controller.signal });
			if (!this.currentExport(request)) return;
			if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") throw new Error("Product export failed");
			const file = await response.blob();
			if (!this.currentExport(request)) return;
			if (!file.size) throw new Error("Empty product export");
			url = URL.createObjectURL(file); const link = this.element("a");
			link.href = url; link.download = "产品档案.xlsx"; document.body.append(link); link.click(); link.remove();
			this.status.textContent = __("已导出当前筛选的全部已保存产品档案；采用本系统当前字段模板，两图为状态文字。");
		} catch { if (this.currentExport(request)) this.status.textContent = __("导出未完成，请核对筛选及完整字段读取、导出权限后重试。"); }
		finally {
			if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
			if (this.exportRequest === request) { this.exportRequest = null; this.freezeExport(); }
		}
	}
	async changeScope() {
		if (this.changingCompany || this.disposed) return false;
		const next = this.companyControl.get_value() || null;
		if (next === this.companyScope) return true;
		const blocked = this.getCloseBlock();
		if (!blocked) this.resetList("公司已改变，请查询。");
		if (!blocked && (!this.editor || this.editor.closed)) { this.companyScope = next; this.freezeExport(); return true; }
		this.changingCompany = true;
		try { await this.companyControl.set_value(this.companyScope); } finally { this.changingCompany = false; }
		if (blocked) { frappe.msgprint(__(blocked)); return false; }
		return this.discardFor(async () => {
			this.closeEditor(); this.companyScope = next; this.resetList("公司已改变，请查询。");
			this.changingCompany = true;
			try { await this.companyControl.set_value(next); } finally { this.changingCompany = false; }
		});
	}
	async query(page = 1) {
		if (this.disposed || this.changingCompany) return;
		if ((this.companyControl.get_value() || null) !== this.companyScope && !await this.changeScope()) return;
		if (this.disposed || (this.companyControl.get_value() || null) !== this.companyScope) return;
		this.cancelExport();
		const company = this.companyScope, search = this.search.value || "", inactive = !!this.includeInactive.checked, sequence = ++this.revision;
		const requestedPage = this.pageNumber(page), args = { company, search, include_inactive: Number(inactive), page: requestedPage, page_size: this.pageSize };
		if (!company || requestedPage === null) { this.pendingQuery = null; this.renderPager(); this.status.textContent = __(!company ? "请选择公司后查询。" : "请输入合法页码后查询。"); return; }
		this.pendingQuery = sequence; this.renderPager();
		const scroll = { top: this.scroller.scrollTop, left: this.scroller.scrollLeft }; this.status.textContent = __("正在查询…");
		try {
			const response = await this.call("query_archives", args);
			if ((this.companyControl.get_value() || null) !== this.companyScope) await this.changeScope();
			if (!this.currentQuery(sequence, args)) return;
			const result = response.message; if (!this.validPage(result, args)) throw new Error("Wrong list scope");
			this.rows = result.rows; this.page = result.page; this.total = result.total; this.totalPages = result.total_pages; this.goto.value = String(this.page);
			this.renderRows(); this.renderPager(); this.scroller.scrollTop = scroll.top; this.scroller.scrollLeft = scroll.left;
			this.status.textContent = this.total ? (result.page === requestedPage ? __("查询完成") : __("页码超出当前范围，已返回最后一页")) + " · " + this.rows.length + __("条") : __("没有可见记录。");
		} catch { if ((this.companyControl.get_value() || null) !== this.companyScope) await this.changeScope();
			if (this.currentQuery(sequence, args)) this.status.textContent = __("查询失败，当前列表未更新，请重新查询。"); }
		finally { if (this.pendingQuery === sequence) { this.pendingQuery = null; this.renderPager(); } }
	}
	currentQuery(sequence, args) {
		return !this.disposed && sequence === this.revision && args.company === this.companyScope && args.company === (this.companyControl.get_value() || null) &&
			args.search === this.search.value && args.include_inactive === Number(this.includeInactive.checked) && args.page_size === this.pageSize;
	}
	validPage(result, args) {
		if (!result || !["company", "search", "include_inactive", "page_size"].every((field) => result[field] === args[field]) ||
			result.requested_page !== args.page || !Number.isSafeInteger(result.total) || result.total < 0 ||
			!Number.isSafeInteger(result.total_pages) || result.total_pages !== Math.ceil(result.total / args.page_size) ||
			result.page !== Math.min(args.page, Math.max(1, result.total_pages)) || !Array.isArray(result.rows)) return false;
		const length = Math.min(args.page_size, Math.max(0, result.total - (result.page - 1) * args.page_size));
		return result.rows.length === length && new Set(result.rows.map((row) => row?.name)).size === result.rows.length &&
			result.rows.every((row) => this.validArchive(row, args.company) && typeof row.name === "string" && !!row.name &&
				(args.include_inactive || !Number(row.profile.inactive)));
	}
	renderRows() {
		this.tbody.replaceChildren();
		for (const row of this.rows) {
			const tr = this.element("tr");
			for (const field of ["legacy_number", "legacy_code", "major_category", "minor_category", "display_name", "display_uom", "legacy_remarks"]) {
				const cell = this.element("td"), original = row.profile[field];
				const linked = ({ legacy_code: [row.item_code, "商品编码"], display_name: [row.item.item_name, "商品名称"], display_uom: [row.item.stock_uom, "库存单位"] })[field];
				if ((original === null || original === undefined || original === "") && linked && linked[0] !== null && linked[0] !== undefined) {
					cell.textContent = field === "display_uom" ? __(String(linked[0])) : String(linked[0]); cell.title = __(linked[1]);
				} else cell.textContent = original ?? "";
				tr.append(cell);
			}
			tr.append(this.element("td", row.profile.inactive ? __("是") : __("否")));
			for (const field of ["profile_image", "patent_image"]) { const td = this.element("td"), state = row.image_states[field];
				if (state?.state === "已上传" && this.safePreview(state.url)) { const img = this.element("img"); img.src = state.url; img.alt = __(this.labels[field]); td.append(img); }
				else if (field === "profile_image" && row.business_item_image?.state === "已上传" && this.safeBusinessPreview(row.business_item_image.url)) {
					const img = this.element("img"); img.src = row.business_item_image.url; img.alt = __("商品图片"); td.append(img, this.element("div", __("商品图片"))); td.title = __("关联商品的图片");
				}
				else td.textContent = state?.state || ""; tr.append(td);
			}
			const action = this.element("td"); action.append(this.button(row.can_write ? "修改" : "查看", () => this.openEditor(row.name))); tr.append(action); this.tbody.append(tr);
		}
	}
	validArchive(archive, company = null, name = null) {
		return !!(archive?.profile && archive.item && archive.field_meta?.profile && archive.field_meta.item && archive.image_states &&
			typeof archive.can_write === "boolean" && typeof archive.can_create_item === "boolean" && typeof archive.can_link_item === "boolean" &&
			(!company || archive.profile.company === company) && (!name || archive.name === name) &&
			(archive.name ? typeof archive.profile_version === "string" && !!archive.profile_version && typeof archive.item_version === "string" && !!archive.item_version &&
				!!archive.item_code && archive.profile.item_code === archive.item_code && archive.item.item_code === archive.item_code : !archive.profile_version &&
				(archive.existing_item ? archive.existing_item === archive.item_code && archive.profile.item_code === archive.item_code && archive.item.item_code === archive.item_code &&
					typeof archive.item_version === "string" && !!archive.item_version : !archive.item_code && !archive.profile.item_code && !archive.item.item_code && !archive.item_version)) &&
			["company", "item_code", "inactive"].every((field) => field in archive.profile && archive.field_meta.profile[field]));
	}
	async openEditor(name = null) {
		if (this.disposed || this.opening || this.changingCompany) return;
		this.cancelExport();
		if ((this.companyControl.get_value() || null) !== this.companyScope && (!await this.changeScope() || name)) return;
		if (this.disposed || (this.companyControl.get_value() || null) !== this.companyScope) return;
		if (this.editor && !this.editor.closed) { this.editor.dialog.show(); return this.editor.dialog; }
		this.opening = true; this.freezeExport(); const sequence = this.revision;
		try {
			const company = this.companyScope;
			const response = await this.call(name ? "read_archive" : "init_archive", name ? { name } : { company });
			const archive = response.message;
			if ((this.companyControl.get_value() || null) !== this.companyScope) await this.changeScope();
			if (this.disposed || sequence !== this.revision || company !== this.companyScope || !this.changingCompany && company !== (this.companyControl.get_value() || null) || !this.validArchive(archive, company, name) || !name && archive.name) throw new Error("Wrong archive scope");
			const dialog = new frappe.ui.Dialog({ title: __(name ? "产品档案" : "产品档案新增"), size: "extra-large", no_submit_on_enter: true,
				fields: [{ fieldname: "archive_layout", fieldtype: "HTML" }] });
			dialog.$wrapper.addClass("jfe-product-archive-editor");
			const state = { archive, dialog, requestId: name ? null : this.requestID(), company: archive.profile.company || null,
				closed: false, busy: false, confirming: false, unresolved: false, candidate: null, conflict: null, picker: null,
				imageValues: {}, upload: null, uploadUnresolved: false, controls: {}, itemControls: {}, selection: {} };
			this.editor = state; this.buildEditor(state); await this.applyArchive(state, archive);
			if ((this.companyControl.get_value() || null) !== this.companyScope) await this.changeScope();
			if (this.disposed || sequence !== this.revision || company !== this.companyScope || !this.changingCompany && company !== (this.companyControl.get_value() || null)) throw new Error("Wrong archive scope");
			this.guardClose(state);
			dialog.show(); this.freeze(state);
			if (!archive.name) this.openCategoryPicker(state).catch(() => { state.message.textContent = __("类别选择窗未能打开，请点击选择类别重试。"); });
			return dialog;
		} catch {
			if ((this.companyControl.get_value() || null) !== this.companyScope) await this.changeScope();
			const failed = this.editor;
			if (failed && !failed.nativeHide) { failed.closed = true; failed.dialog.hide(); this.editor = null; }
			if (!this.disposed) this.status.textContent = __("产品档案未能打开，请重试。");
		}
		finally { this.opening = false; this.freezeExport(); }
	}
	buildEditor(state) {
		const host = state.dialog.fields_dict.archive_layout.$wrapper.get(0); host.replaceChildren();
		state.message = this.element("p", "", "jfe-product-archive-editor-message"); state.message.setAttribute("role", "status");
		state.scope = this.element("p", "", "jfe-product-archive-scope");
		state.form = this.element("div", undefined, "jfe-product-archive-fields"); state.cells = {};
		for (const [field, label] of this.fields) {
			const cell = this.element("div", undefined, "jfe-product-archive-field jfe-product-archive-field-" + field);
			cell.append(this.element("label", __(label))); state.cells[field] = cell;
			if (["profile_image", "patent_image"].includes(field)) this.buildImage(state, field, cell);
			else {
				const input = this.element(field === "notes" ? "textarea" : "input");
				if (field !== "notes") input.type = ["inactive", "copy"].includes(field) ? "checkbox" : "text";
				input.setAttribute("aria-label", __(label));
				if (!["notes", "legacy_remarks"].includes(field) && input.type !== "checkbox") input.maxLength = 140;
				if (field === "copy") { input.disabled = true; input.title = __("复制暂不可用"); }
				else { input.addEventListener("input", () => this.freeze(state)); input.addEventListener("change", () => this.freeze(state)); }
				if (["legacy_code", "major_category", "minor_category"].includes(field)) input.readOnly = true;
				state.controls[field] = input; cell.append(input);
				if (field === "major_category") { state.categoryButton = this.button("选择类别", () => this.openCategoryPicker(state)); cell.append(state.categoryButton); }
			}
			state.form.append(cell);
		}
		state.supplierSection = this.element("section", undefined, "jfe-product-archive-supplier"); state.supplierSection.append(this.element("strong", __("供应商与规格")));
		const supplierGrid = this.element("div", undefined, "jfe-product-archive-business-grid"); state.extraControls = {};
		for (const [field, label, type, options] of [["supplier_product_code", "供应商货号", "Data", ""], ["supplier", "供应商", "Link", "Supplier"], ["specification", "规格", "Small Text", ""]]) {
			state.extraControls[field] = this.nativeControl(supplierGrid, field, label, type, options, () => this.freeze(state)); this.labels[field] = label;
		}
		state.supplierSection.append(supplierGrid);
		state.business = this.element("section", undefined, "jfe-product-archive-business");
		state.business.append(this.element("strong", __("首次保存的业务商品设置")), this.element("p", __("实际库存使用这里明确选择的商品名称、明细分组和库存单位。上方档案名称、材质及单位按原文保存。")));
		const businessGrid = this.element("div", undefined, "jfe-product-archive-business-grid");
		state.companyControl = this.nativeControl(businessGrid, "company", "公司", "Link", "Company", () => this.changeEditorCompany(state));
		const modeCell = this.element("div"), modeLabel = this.element("label", __("业务商品来源"));
		state.modeControl = this.element("select"); state.modeControl.setAttribute("aria-label", __("业务商品来源"));
		for (const [value, label] of [["new_item", "新建业务商品"], ["existing_item", "关联已有业务商品"]]) { const option = this.element("option", __(label)); option.value = value; state.modeControl.append(option); }
		state.modeControl.addEventListener("change", () => this.changeSource(state)); modeCell.append(modeLabel, state.modeControl); businessGrid.append(modeCell);
		state.existingControl = this.nativeControl(businessGrid, "existing_item", "已有业务商品", "Link", "Item", () => this.selectExisting(state));
		for (const [field, label, type, options] of [["item_name", "业务商品名称", "Data", ""], ["item_group", "业务商品明细分组", "Link", "Item Group"], ["stock_uom", "实际库存单位", "Link", "UOM"]]) {
			state.itemControls[field] = this.nativeControl(businessGrid, field, label, type, options, () => this.freeze(state));
		}
		state.business.append(businessGrid);
		state.linkedBusiness = this.element("section", undefined, "jfe-product-archive-linked-business");
		state.review = this.element("div", undefined, "jfe-product-archive-review");
		const footer = this.element("div", undefined, "jfe-product-archive-editor-footer");
		state.newButton = this.button("新增", () => this.newDraft(state)); state.saveButton = this.button("保存", () => this.save(state));
		state.closeButton = this.button("关闭", () => state.dialog.hide());
		state.recipeButton = this.button("工艺与材料", () => this.openRecipe(state));
		state.resolveButton = this.button("核对保存结果", () => this.resolve(state)); state.adoptButton = this.button("采用已核对当前值", () => this.acceptCurrent(state));
		footer.append(state.newButton, state.saveButton, state.closeButton, state.recipeButton, state.resolveButton, state.adoptButton);
		host.append(state.scope, state.message, state.form, state.linkedBusiness, state.supplierSection, state.business, state.review, footer);
	}
	renderLinkedBusiness(state) {
		const archive = state.archive, host = state.linkedBusiness; host.replaceChildren(); host.hidden = !archive.name;
		if (!archive.name) return;
		host.append(this.element("strong", __("关联商品")));
		const grid = this.element("div", undefined, "jfe-product-archive-business-grid");
		for (const [field, label] of [["item_code", "商品编码"], ["item_name", "商品名称"], ["stock_uom", "库存单位"]]) {
			if (!archive.field_meta.item[field] || !(field in archive.item)) continue;
			const cell = this.element("div"); cell.append(this.element("label", __(label)), this.element("div", field === "stock_uom" ? __(String(archive.item[field] ?? "")) : archive.item[field] ?? "")); grid.append(cell);
		}
		if (archive.business_item_image) {
			const cell = this.element("div"), image = archive.business_item_image; cell.append(this.element("label", __("商品图片")));
			const preview = this.element("div", undefined, "jfe-product-archive-image-preview");
			if (image.state === "已上传" && this.safeBusinessPreview(image.url)) { const img = this.element("img"); img.src = image.url; img.alt = __("商品图片"); preview.append(img); }
			else preview.textContent = image.state || __("未上传");
			cell.append(preview); grid.append(cell);
		}
		host.append(grid);
	}
	nativeControl(host, fieldname, label, fieldtype, options, onchange) {
		const cell = this.element("div"); host.append(cell);
		return frappe.ui.form.make_control({ parent: cell, render_input: true, df: { fieldname, fieldtype, options, label: __(label), onchange } });
	}
	async setNative(state, control, value) {
		state.settingNative = true;
		try { await control.set_value(value); } finally { state.settingNative = false; }
	}
	async applyArchive(state, archive, held = null) {
		state.archive = archive; state.company = archive.profile.company || null;
		state.selection = { category: archive.profile.category || null, category_detail: archive.profile.category_detail || null,
			major_category: archive.profile.major_category || null, minor_category: archive.profile.minor_category || null };
		state.imageValues = Object.fromEntries(["profile_image", "patent_image"].filter((field) => field in archive.profile).map((field) => [field, archive.profile[field]]));
		for (const [field, input] of Object.entries(state.controls)) {
			if (input.type === "checkbox") input.checked = field === "copy" ? false : !!Number(archive.profile[field]);
			else input.value = archive.profile[field] ?? "";
			state.cells[field].hidden = field !== "copy" && !archive.field_meta.profile[field];
			input.title = (archive.field_meta.profile[field]?.description || input.title || "").replace("原生数值字段", "参考价格和重量");
		}
		state.mode = archive.existing_item || !archive.can_create_item && archive.can_link_item ? "existing_item" : "new_item"; state.modeControl.value = state.mode;
		// Only named controls are touched; native Layout's automatic Sections are never treated as values.
		state.settingNative = true;
		const pending = [state.companyControl.set_value(state.company), state.existingControl.set_value(archive.existing_item || null),
			...Object.entries(state.itemControls).map(([field, control]) => control.set_value(archive.item[field] ?? null)),
			...Object.entries(state.extraControls).map(([field, control]) => control.set_value(archive.profile[field] ?? null))];
		state.nativeReady = Promise.all(pending).finally(() => { state.settingNative = false; this.freeze(state); });
		await state.nativeReady;
		state.baseProfile = this.profileValues(state); state.baseSelection = structuredClone(state.selection);
		state.baseItem = Object.fromEntries(Object.entries(state.itemControls).map(([field, control]) => [field, control.get_value() ?? ""]));
		state.original = JSON.stringify(this.draft(state));
		if (held) {
			for (const [field, value] of Object.entries(held.profile)) if (state.controls[field] && archive.field_meta.profile[field]) {
				if (field === "inactive") state.controls[field].checked = !!Number(value); else state.controls[field].value = value ?? "";
			}
			state.settingNative = true;
			try { await Promise.all(Object.entries(held.profile).filter(([field]) => state.extraControls[field] && archive.field_meta.profile[field]).map(([field, value]) => state.extraControls[field].set_value(value))); }
			finally { state.settingNative = false; }
			state.selection = held.selection; this.showSelection(state);
			if (!archive.existing_item) { state.settingNative = true;
				state.nativeReady = Promise.all(Object.entries(held.item).filter(([field]) => archive.field_meta.item[field]).map(([field, value]) => state.itemControls[field].set_value(value)))
					.finally(() => { state.settingNative = false; this.freeze(state); });
				await state.nativeReady;
			}
		}
		state.business.hidden = !!archive.name; state.scope.textContent = __("公司：") + (state.company || __("请选择")) +
			(archive.name ? __("；业务商品：") + archive.item_code : "");
		state.message.textContent = (String(archive.profile.legacy_remarks || "").includes("\n") ? __("原备注含换行，单行显示；未改动时保留原文。 ") : "") +
			__("编号、旧号可自行填写；代码不自动生成。参考价格和重量留空时保存为0。复制、导入及打印暂不可用。图片请先保存档案后上传，上传后保存图片变更。关闭录入窗口后可导出已保存档案。");
		state.review.replaceChildren(); this.renderLinkedBusiness(state); this.renderImages(state); this.freeze(state);
	}
	profileValues(state) {
		const values = Object.fromEntries(Object.entries(state.controls).filter(([field]) => field !== "copy" && state.archive.field_meta.profile[field] &&
			!state.archive.field_meta.profile[field].read_only && !["legacy_code", "major_category", "minor_category"].includes(field)).map(([field, input]) => [field, field === "inactive" ? Number(input.checked) : input.value]));
		for (const [field, control] of Object.entries(state.extraControls)) if (state.archive.field_meta.profile[field] && !state.archive.field_meta.profile[field].read_only) values[field] = control.get_value() ?? "";
		return values;
	}
	draft(state) {
		return { company: state.company, mode: state.mode, existing_item: state.archive.existing_item || null, profile: this.profileValues(state),
			selection: state.selection, images: state.imageValues, item: state.archive.name || state.mode === "existing_item" ? {} :
				Object.fromEntries(Object.entries(state.itemControls).filter(([field]) => state.archive.field_meta.item[field] && !state.archive.field_meta.item[field].read_only).map(([field, control]) => [field, control.get_value() ?? ""])) };
	}
	payload(state) {
		const result = { profile: {}, item: {} }, current = this.profileValues(state);
		if (state.archive.name) Object.assign(result, { name: state.archive.name, profile_version: state.archive.profile_version, item_version: state.archive.item_version });
		else Object.assign(result, { request_id: state.requestId, ...(state.archive.existing_item ? { existing_item: state.archive.existing_item, item_version: state.archive.item_version } : {}) });
		if (!state.archive.name) result.profile.company = state.company;
		for (const [field, value] of Object.entries(current)) if (!state.archive.name || value !== state.baseProfile[field]) result.profile[field] = value;
		if (!state.archive.name || state.selection.category !== state.baseSelection.category || state.selection.category_detail !== state.baseSelection.category_detail) {
			for (const field of ["category", "category_detail"]) if (state.archive.field_meta.profile[field] && !state.archive.field_meta.profile[field].read_only) result.profile[field] = state.selection[field];
		}
		for (const field of ["profile_image", "patent_image"]) if (state.archive.name && state.archive.field_meta.profile[field] && !state.archive.field_meta.profile[field].read_only &&
			field in state.imageValues && state.imageValues[field] !== state.archive.profile[field]) result.profile[field] = state.imageValues[field];
		if (!state.archive.name && state.mode === "new_item") result.item = this.draft(state).item;
		return result;
	}
	isDirty(state = this.editor) { return !!(state && !state.closed && state.original !== JSON.stringify(this.draft(state))); }
	getCloseBlock() {
		const state = this.editor;
		if (this.opening || state?.busy || state?.upload?.open) return "正在读取、保存或上传产品档案，请稍候。";
		if (state?.unresolved) return "保存结果尚未确认，请先核对保存结果。";
		if (state?.uploadUnresolved) return "图片上传结果尚未确认，请先核对上传结果。";
		return state?.confirming ? "正在确认产品草稿，请稍候。" : "";
	}
	freeze(state) {
		this.freezeExport();
		if (!state || state.closed) return;
		const locked = !!(state.busy || state.unresolved || state.upload?.open || state.uploadUnresolved || state.confirming);
		for (const [field, input] of Object.entries(state.controls)) input.disabled = field === "copy" || locked || !state.archive.can_write ||
			!state.archive.field_meta.profile[field] || !!state.archive.field_meta.profile[field].read_only || ["legacy_code", "major_category", "minor_category"].includes(field);
		const categoryFields = ["category", "category_detail", "major_category", "minor_category"];
		state.categoryButton.disabled = locked || !state.archive.can_write || !state.company || categoryFields.some((field) => !state.archive.field_meta.profile[field]) ||
			["category", "category_detail"].some((field) => state.archive.field_meta.profile[field].read_only);
		state.saveButton.disabled = locked || !state.archive.can_write;
		state.newButton.disabled = locked; state.closeButton.disabled = locked;
		state.recipeButton.hidden = !state.archive.name; state.recipeButton.disabled = locked || !this.bridge;
		state.resolveButton.hidden = !state.unresolved; state.resolveButton.disabled = state.busy || state.confirming;
		state.adoptButton.hidden = !state.unresolved || !state.conflict; state.adoptButton.disabled = state.busy || state.confirming;
		state.modeControl.disabled = locked || !!state.archive.name;
		this.nativeFlags(state.companyControl, { read_only: locked || !!state.archive.name, reqd: !state.archive.name });
		this.nativeFlags(state.existingControl, { read_only: locked || state.mode !== "existing_item" || !!state.archive.name, reqd: !state.archive.name && state.mode === "existing_item" });
		for (const [field, control] of Object.entries(state.itemControls)) { const df = state.archive.field_meta.item[field];
			this.nativeFlags(control, { read_only: locked || !!state.archive.name || state.mode !== "new_item" || !df || df.read_only || !state.archive.can_create_item,
				hidden: !df, reqd: !state.archive.name && state.mode === "new_item" });
		}
		for (const [field, control] of Object.entries(state.extraControls)) { const df = state.archive.field_meta.profile[field];
			this.nativeFlags(control, { hidden: !df, read_only: locked || !state.archive.can_write || !df || df.read_only });
		}
		for (const [field, panel] of Object.entries(state.images || {})) {
			const df = state.archive.field_meta.profile[field]; panel.cell.hidden = !df;
			panel.uploadButton.disabled = locked || !state.archive.name || !state.archive.can_write || !df || df.read_only;
			panel.clearButton.disabled = panel.uploadButton.disabled;
			panel.resolveButton.hidden = !state.uploadUnresolved || state.upload?.field !== field; panel.resolveButton.disabled = state.busy || state.confirming;
		}
	}
	nativeFlags(control, values) {
		let changed = false;
		for (const [field, value] of Object.entries(values)) if (control.df[field] !== value) { control.df[field] = value; changed = true; }
		if (changed) control.refresh?.();
	}
	async discardFor(action, state = this.editor) {
		if (this.disposed || state?.closed) return false;
		const block = this.getCloseBlock(); if (block) { frappe.msgprint(__(block)); return false; }
		if (!this.isDirty(state)) { await action(); return true; }
		state.confirming = true; this.freeze(state);
		const yes = await new Promise((resolve) => frappe.confirm(__("尚有未保存的修改，放弃修改并继续？"), () => resolve(true), () => resolve(false)));
		state.confirming = false; this.freeze(state); if (yes && !this.disposed && !state.closed) { await action(); return true; } return false;
	}
	guardClose(state) {
		state.nativeHide = state.dialog.hide.bind(state.dialog);
		state.dialog.hide = () => this.discardFor(() => this.closeEditor(), state);
		state.dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn().removeAttr("data-dismiss").on("click.product-archive", () => state.dialog.hide());
		state.dialog.$wrapper.on("keydown.product-archive", (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); } });
		state.dialog.on_hide = () => { state.closed = true; this.closePicker(state); if (this.editor === state) this.editor = null; };
	}
	closeEditor() { const state = this.editor; if (!state || state.closed) return; state.closed = true; this.closePicker(state); state.upload?.uploader?.dialog.hide(); state.nativeHide(); this.editor = null; this.freezeExport(); }
	requestID() {
		if (crypto.randomUUID) return crypto.randomUUID(); const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
		const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
		return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
	}
	async newDraft(state) {
		return this.discardFor(async () => {
			state.busy = true; this.freeze(state);
			try { const response = await this.call("init_archive", { company: state.company });
				if (!this.validArchive(response.message, state.company) || response.message.name) throw new Error("Wrong new scope");
				this.closePicker(state); state.requestId = this.requestID(); state.unresolved = false; state.conflict = null; state.candidate = null;
				await this.applyArchive(state, response.message); state.dialog.set_title(__("产品档案新增"));
			} catch { state.message.textContent = __("新增档案未能打开，原草稿已保留，请重试。"); }
			finally { state.busy = false; this.freeze(state); }
			if (!state.archive.name) this.openCategoryPicker(state);
		}, state);
	}
	async changeEditorCompany(state) {
		if (state.settingNative || state.closed || state.archive.name) return;
		const desired = state.companyControl.get_value() || null; if (desired === state.company) return;
		await this.setNative(state, state.companyControl, state.company);
		return this.discardFor(async () => {
			state.busy = true; this.freeze(state);
			try { const response = await this.call("init_archive", { company: desired });
				if (!this.validArchive(response.message, desired) || response.message.name) throw new Error("Wrong company scope");
				this.closePicker(state); state.requestId = this.requestID(); await this.applyArchive(state, response.message);
			} catch { state.message.textContent = __("公司切换失败，原草稿已保留，请重试。"); }
			finally { state.busy = false; this.freeze(state); }
			if (state.company === desired) this.openCategoryPicker(state);
		}, state);
	}
	async changeSource(state) {
		if (state.closed || state.archive.name || this.getCloseBlock()) { state.modeControl.value = state.mode; return; }
		const next = state.modeControl.value;
		if (next === state.mode) return;
		if (next === "existing_item" && state.archive.can_link_item) {
			state.newItemDraft = Object.fromEntries(Object.entries(state.itemControls).map(([field, control]) => [field, control.get_value() ?? ""]));
			state.mode = next; this.freeze(state); return;
		}
		if (next === "new_item" && state.archive.can_create_item) return this.setSource(state, null);
		state.modeControl.value = state.mode;
	}
	async selectExisting(state) {
		if (state.settingNative || state.closed || state.archive.name || state.mode !== "existing_item") return;
		const selected = state.existingControl.get_value(); if (!selected || selected === state.archive.existing_item) return;
		await this.setNative(state, state.existingControl, state.archive.existing_item || null); return this.setSource(state, selected);
	}
	async setSource(state, existingItem) {
		if (state.closed || state.archive.name || this.getCloseBlock()) return false;
		if (!state.company) { state.message.textContent = __("请先选择公司，再确定业务商品来源。"); state.modeControl.value = state.mode; return false; }
		if (existingItem ? !state.archive.can_link_item : !state.archive.can_create_item) return false;
		const original = state.original;
		const held = { profile: this.profileValues(state), selection: structuredClone(state.selection), item: existingItem ? {} : state.newItemDraft || this.draft(state).item };
		state.busy = true; this.freeze(state);
		try { const response = await this.call("init_archive", { company: state.company, existing_item: existingItem });
			const archive = response.message;
			if (!this.validArchive(archive, state.company) || archive.name || (archive.existing_item || null) !== existingItem) throw new Error("Wrong Item source");
			await this.applyArchive(state, archive, held); state.original = original; return true;
		} catch { state.message.textContent = __("业务商品来源未能确认，原草稿已保留，请重试。"); state.modeControl.value = state.mode; return false; }
		finally { state.busy = false; this.freeze(state); }
	}
	definiteFailure(error) { return new Set(["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError", "UniqueValidationError"]).has(error?.responseJSON?.exc_type); }
	completeResult(state, archive) {
		const candidate = state.candidate;
		return !!(candidate && this.validArchive(archive, candidate.profile.company || state.company, candidate.name) && archive.name &&
			(!candidate.existing_item || archive.item_code === candidate.existing_item) &&
			(candidate.name || archive.creation_mode === (candidate.existing_item ? "existing_item" : "new_item")) &&
			Object.keys(candidate.profile).every((field) => field in archive.profile && archive.field_meta.profile[field]) &&
			Object.keys(candidate.item).every((field) => field in archive.item && archive.field_meta.item[field]));
	}
	equalField(state, archive, group, field, value) {
		const stored = archive[group][field], df = state.archive.field_meta[group][field];
		if (df?.fieldtype === "Check") return Number(value) === Number(stored);
		if (["Float", "Currency"].includes(df?.fieldtype)) { const before = value === "" || value === null ? 0 : Number(value), after = typeof stored === "number" || typeof stored === "string" && stored !== "" ? Number(stored) : NaN;
			return Number.isFinite(before) && before >= 0 && Number.isFinite(after) && before === after; }
		return (value ?? "") === (stored ?? "");
	}
	matches(state, archive) {
		if (!this.completeResult(state, archive) || state.candidate.name && archive.profile_version === state.candidate.profile_version) return false;
		return ["profile", "item"].every((group) => Object.entries(state.candidate[group]).every(([field, value]) => this.equalField(state, archive, group, field, value)));
	}
	async save(state) {
		if (state.closed || this.getCloseBlock() || !state.archive.can_write || this.disposed) return;
		await state.nativeReady;
		if (!state.company) { state.message.textContent = __("请选择公司。"); return; }
		if (!state.archive.name && state.mode === "existing_item" && !state.archive.existing_item) { state.message.textContent = __("请明确关联已有业务商品。"); return; }
		if (!state.archive.name && state.mode === "new_item" && (!state.archive.can_create_item || Object.values(this.draft(state).item).length !== 3 || Object.values(this.draft(state).item).some((value) => typeof value !== "string" || !value.trim()))) {
			state.message.textContent = __("请明确业务商品名称、明细分组及实际库存单位。"); return;
		}
		if (state.archive.name && !this.isDirty(state)) return;
		state.candidate = structuredClone(this.payload(state)); state.conflict = null; state.review.replaceChildren(); state.busy = true; this.freeze(state);
		try { const response = await this.call("save_archive", { payload: state.candidate }, true);
			if (this.matches(state, response.message)) await this.accept(state, response.message, "产品档案已保存");
			else { state.unresolved = true; this.showConflict(state, response.message); }
		} catch (error) { state.unresolved = !this.definiteFailure(error); }
		finally { state.busy = false; this.freeze(state); if (state.unresolved) state.message.textContent = __("保存结果尚未确认，草稿已保留，请核对保存结果。"); }
	}
	async accept(state, archive, message) {
		const refresh = { revision: this.revision, page: this.page, company: this.companyScope, search: this.search.value,
			include_inactive: Number(this.includeInactive.checked), page_size: this.pageSize };
		state.unresolved = false; state.conflict = null; state.candidate = null; state.requestId = null;
		await this.applyArchive(state, archive); state.original = JSON.stringify(this.draft(state));
		state.dialog.set_title(__("产品档案")); state.message.textContent = __(message);
		if (this.companyScope === archive.profile.company && this.currentQuery(refresh.revision, refresh)) this.query(refresh.page);
		frappe.show_alert({ message: __(message), indicator: "green" });
	}
	showConflict(state, archive) {
		if (!this.completeResult(state, archive)) return; state.conflict = archive;
		state.review.append(this.element("p", __("服务器当前值与保留草稿或版本不同，请核对后明确采用当前值；采用操作不会再次保存。")));
		const table = this.element("table"), heading = this.element("tr"); for (const label of ["字段", "保留草稿", "服务器当前值"]) heading.append(this.element("th", __(label))); table.append(heading);
		for (const group of ["profile", "item"]) for (const [field, value] of Object.entries(state.candidate[group])) {
			const row = this.element("tr"), label = this.labels[field] || ({ company: "公司", category: "大类选择", category_detail: "小类选择", item_name: "业务商品名称", item_group: "业务商品明细分组", stock_uom: "实际库存单位" })[field];
			const categoryName = field === "category" ? "major_category" : field === "category_detail" ? "minor_category" : null;
			row.append(this.element("td", __(label)), this.element("td", ["profile_image", "patent_image"].includes(field) ? value ? __("待保存图片") : __("清空图片") : categoryName ? state.selection[categoryName] : value),
				this.element("td", ["profile_image", "patent_image"].includes(field) ? archive.image_states[field]?.state || "" : categoryName ? archive.profile[categoryName] : archive[group][field])); table.append(row);
		}
		state.review.append(table);
	}
	async resolve(state) {
		if (state.closed || state.busy || state.confirming || !state.unresolved || !state.candidate) return;
		state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try { const response = await this.call(state.candidate.name ? "read_archive" : "resolve_creation", state.candidate.name ? { name: state.candidate.name } : { request_id: state.candidate.request_id, company: state.company });
			const archive = state.candidate.name ? response.message : response.message?.status === "found" ? response.message.archive : null;
			if (this.matches(state, archive)) await this.accept(state, archive, "已核对保存结果");
			else if (this.completeResult(state, archive)) this.showConflict(state, archive);
			else state.message.textContent = __("未核对到完整可见结果，请保留草稿稍后核对；不会重复保存。");
		} catch { state.message.textContent = __("核对失败，草稿已保留，请稍后核对。"); }
		finally { state.busy = false; this.freeze(state); }
	}
	acceptCurrent(state) {
		if (state.closed || state.busy || state.confirming || !state.unresolved || !state.conflict) return;
		const archive = state.conflict; state.confirming = true; this.freeze(state);
		frappe.confirm(__("放弃保留草稿并采用刚核对的服务器当前值？此操作不会再次保存。"), async () => {
			state.confirming = false; if (state.closed || state.conflict !== archive) return; state.busy = true; this.freeze(state);
			try { await this.accept(state, archive, "已采用当前值；未重复保存"); } finally { state.busy = false; this.freeze(state); }
		}, () => { state.confirming = false; this.freeze(state); });
	}
	async openRecipe(state) {
		if (!state.archive.name || !this.bridge) return;
		return this.discardFor(() => {
			const name = state.archive.name; this.closeEditor();
			this.bridge.open({ title: "工艺与材料：" + name, module: "产品", kind: "form", doctype: "Jifeng Product Profile", route: ["Form", "Jifeng Product Profile", name] });
		}, state);
	}
	showSelection(state) {
		for (const field of ["major_category", "minor_category"]) state.controls[field].value = state.selection[field] ?? "";
		this.freeze(state);
	}
	categoryWritable(state) {
		return state.archive.can_write && ["category", "category_detail", "major_category", "minor_category"].every((field) => state.archive.field_meta.profile[field]) &&
			["category", "category_detail"].every((field) => !state.archive.field_meta.profile[field].read_only);
	}
	closePicker(state) { if (state.picker && !state.picker.closed) { state.picker.closed = true; state.picker.revision++; state.picker.dialog.hide(); } state.picker = null; }
	async openCategoryPicker(state) {
		if (state.closed || state.busy || state.unresolved || state.upload?.open || state.uploadUnresolved || !this.categoryWritable(state)) return;
		if (state.picker && !state.picker.closed) { state.picker.dialog.show(); return state.picker.dialog; }
		const dialog = new frappe.ui.Dialog({ title: __("产品类别"), size: "extra-large", fields: [{ fieldname: "category_layout", fieldtype: "HTML" }] });
		const picker = { dialog, company: state.company, revision: 0, closed: false, rows: [], selected: null };
		state.picker = picker; const host = dialog.fields_dict.category_layout.$wrapper.get(0);
		picker.message = this.element("p", state.company ? __("单击选中，双击采用类别；采用后可关闭选择窗继续录入。") : __("请先在产品档案中选择公司，再选择类别。"));
		const toolbar = this.element("div", undefined, "jfe-product-archive-picker-toolbar");
		picker.search = this.element("input"); picker.search.type = "text"; picker.search.maxLength = 140; picker.search.setAttribute("aria-label", __("类别关键词")); picker.search.placeholder = __("关键词");
		picker.search.addEventListener("input", () => { picker.revision++; picker.selected = null; picker.adopt.disabled = true; });
		picker.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.loadCategories(state, picker); } });
		toolbar.append(this.element("span", __("公司：") + (state.company || __("请选择"))), picker.search, this.button("查询", () => this.loadCategories(state, picker)));
		const scroller = this.element("section", undefined, "jfe-product-archive-picker-list"), table = this.element("table"), thead = this.element("thead"), headings = this.element("tr");
		for (const label of ["代码", "小类", "大类", "材质", "负责", "区域", "简拼"]) headings.append(this.element("th", __(label)));
		thead.append(headings); picker.body = this.element("tbody"); table.append(thead, picker.body); scroller.append(table);
		const footer = this.element("div", undefined, "jfe-product-archive-editor-footer");
		picker.adopt = this.button("采用所选类别", () => this.applyChoice(state, picker, picker.selected)); picker.adopt.disabled = true;
		footer.append(picker.adopt, this.button("仅选大类", () => this.chooseMajorOnly(state, picker)), this.button("清空类别选择", () => {
			if (!this.pickerValid(state, picker)) return;
			state.selection = { category: null, category_detail: null, major_category: null, minor_category: null }; this.showSelection(state);
			picker.message.textContent = __("类别选择已清空，可关闭选择窗继续录入。");
		}), this.button("关闭", () => this.closePicker(state)));
		host.append(picker.message, toolbar, scroller, footer);
		dialog.on_hide = () => { picker.closed = true; picker.revision++; picker.majorDialog?.hide(); if (state.picker === picker) state.picker = null; };
		dialog.show(); if (state.company) await this.loadCategories(state, picker); return dialog;
	}
	pickerValid(state, picker) {
		return !state.closed && !picker.closed && state.picker === picker && state.company === picker.company && !state.busy && !state.unresolved && !state.uploadUnresolved && !state.upload?.open && this.categoryWritable(state);
	}
	async loadCategories(state, picker) {
		if (!this.pickerValid(state, picker) || !picker.company) return;
		const revision = ++picker.revision, search = picker.search.value || "";
		picker.selected = null; picker.adopt.disabled = true; picker.message.textContent = __("正在查询类别…");
		try {
			const response = await this.call("list_category_choices", { company: picker.company, search });
			if (!this.pickerValid(state, picker) || revision !== picker.revision || search !== picker.search.value) return;
			if (!Array.isArray(response.message) || response.message.some((row) => typeof row?.category !== "string" || !row.category ||
				typeof row.category_detail !== "string" || !row.category_detail || typeof row.major_name !== "string" || typeof row.minor_name !== "string" || row.abbreviation !== "")) throw new Error("Incomplete category choice");
			picker.rows = response.message; picker.body.replaceChildren();
			for (const row of picker.rows) {
				const tr = this.element("tr"); tr.tabIndex = 0;
				for (const field of ["category_code", "minor_name", "major_name", "material", "owner_name", "region", "abbreviation"]) tr.append(this.element("td", row[field]));
				const select = () => { if (!this.pickerValid(state, picker)) return; picker.selected = row; picker.adopt.disabled = false;
					for (const node of picker.body.children) node.setAttribute("aria-selected", String(node === tr)); };
				tr.addEventListener("click", select); tr.addEventListener("dblclick", () => this.applyChoice(state, picker, row));
				tr.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); } });
				picker.body.append(tr);
			}
			picker.message.textContent = picker.rows.length ? __("单击选中，双击采用类别；仅带入大类、小类名称。") : __("没有可选小类；可仅选大类或关闭选择窗继续录入。");
		} catch { if (!picker.closed && revision === picker.revision) picker.message.textContent = __("类别查询失败，请重试。"); }
	}
	applyChoice(state, picker, row) {
		if (!this.pickerValid(state, picker) || !picker.rows.includes(row)) return;
		state.selection = { category: row.category, category_detail: row.category_detail, major_category: row.major_name, minor_category: row.minor_name };
		this.showSelection(state); picker.message.textContent = __("已采用：") + row.major_name + " / " + row.minor_name + __("；可关闭选择窗继续录入。");
	}
	chooseMajorOnly(state, picker) {
		if (!this.pickerValid(state, picker) || !picker.company) return;
		if (picker.majorDialog) { picker.majorDialog.show(); return; }
		const chooser = new frappe.ui.Dialog({ title: __("仅选大类"), fields: [{ fieldname: "category", fieldtype: "Link", options: "Jifeng Product Category", label: __("大类"), reqd: true }],
			primary_action_label: __("采用大类"), primary_action: async () => {
				const values = chooser.get_values(); if (!values || !this.pickerValid(state, picker)) return;
				state.busy = true; this.freeze(state); chooser.get_primary_btn().prop("disabled", true);
				try {
					const response = await frappe.call({ method: "jifeng_erp.product_category.read_record", args: { name: values.category, record_type: "category" } }), row = response.message;
					if (state.closed || picker.closed || state.company !== picker.company || row?.name !== values.category || row.record_type !== "category" ||
						row.company !== picker.company || row.inactive !== 0 || row.can_select !== true || typeof row.major_name !== "string") throw new Error("Wrong major scope");
					state.selection = { category: row.name, category_detail: null, major_category: row.major_name, minor_category: null };
					this.showSelection(state); picker.message.textContent = __("已仅采用大类：") + row.major_name; chooser.hide();
				} catch { picker.message.textContent = __("大类未能确认，原类别选择保持不变，请重试。"); }
				finally { state.busy = false; this.freeze(state); chooser.get_primary_btn().prop("disabled", false); }
			} });
		chooser.fields_dict.category.get_query = () => ({ filters: { company: picker.company, inactive: 0 } });
		chooser.on_hide = () => { if (picker.majorDialog === chooser) picker.majorDialog = null; }; picker.majorDialog = chooser; chooser.show();
	}
	buildImage(state, field, cell) {
		state.images ||= {}; const preview = this.element("div", undefined, "jfe-product-archive-image-preview");
		const status = this.element("p", __("未上传"));
		const uploadButton = this.button("上传图片", () => this.uploadImage(state, field));
		const clearButton = this.button("清空图片", () => { if (clearButton.disabled || state.closed) return; state.imageValues[field] = null; this.renderImages(state); this.freeze(state); });
		const resolveButton = this.button("核对上传结果", () => this.resolveUpload(state));
		const candidates = this.element("div", undefined, "jfe-product-archive-upload-candidates"); cell.append(preview, status, uploadButton, clearButton, resolveButton, candidates);
		state.images[field] = { cell, preview, status, uploadButton, clearButton, resolveButton, candidates };
	}
	safePreview(url) { return typeof url === "string" && /^\/private\/files\/[^?#\\\x00-\x1f]+(?:\?[^#\\\x00-\x1f]*)?$/.test(url); }
	safeBusinessPreview(value) {
		if (typeof value !== "string" || value !== value.trim()) return false;
		const match = /^\/(?:private\/)?files\/([^?#/\\\x00-\x1f]+)(?:\?[^#\\\x00-\x1f]*)?$/.exec(value);
		if (!match) return false;
		try {
			const url = new URL(value, window.location.origin);
			if (url.origin !== window.location.origin || url.hash) return false;
			const filename = decodeURIComponent(match[1]);
			return !!filename && filename !== "." && filename !== ".." && !/[/%\\\x00-\x1f]/.test(filename);
		} catch { return false; }
	}
	canonicalImage(url) { return typeof url === "string" && url.length <= 140 && /^\/private\/files\/[^?#\\\x00-\x1f]+$/.test(url); }
	renderImages(state) {
		for (const [field, panel] of Object.entries(state.images)) {
			panel.preview.replaceChildren(); const info = state.archive.image_states[field];
			const pending = field in state.imageValues && state.imageValues[field] !== state.archive.profile[field];
			if (!pending && info?.state === "已上传" && this.safePreview(info.url)) { const img = this.element("img"); img.src = info.url; img.alt = __(this.labels[field]); panel.preview.append(img); }
			else panel.preview.append(this.element("span", __(pending ? state.imageValues[field] ? "图片待保存" : "清空待保存" : "图片区域")));
			panel.status.textContent = pending ? __("请保存图片变更。") : !state.archive.name ? __("先保存档案，再上传图片。") : info?.state || __("未上传");
		}
	}
	attachmentRows(rows) {
		if (!Array.isArray(rows) || rows.some((row) => !row?.name || typeof row.file_name !== "string" || !this.canonicalImage(row.file_url) || !this.safePreview(row.unique_url))) throw new Error("Incomplete attachment list");
		return rows;
	}
	async uploadImage(state, field) {
		const panel = state.images[field]; if (state.closed || panel.uploadButton.disabled || this.getCloseBlock()) return;
		if (this.isDirty(state)) { state.message.textContent = __("请先保存当前修改，再上传图片。"); return; }
		state.busy = true; this.freeze(state);
		try {
			const response = await this.call("list_image_attachments", { name: state.archive.name, fieldname: field });
			const baseline = new Set(this.attachmentRows(response.message).map((row) => row.name));
			if (!frappe.ui.FileUploader) await frappe.require("file_uploader.bundle.js");
			if (state.closed || this.disposed) return;
			const upload = { field, baseline, open: true, started: false, known: false, candidates: [] }; state.upload = upload;
			const uploader = new frappe.ui.FileUploader({ doctype: "Jifeng Product Profile", docname: state.archive.name, fieldname: field,
				make_attachments_public: 0, allow_toggle_private: false, allow_multiple: false, disable_file_browser: true,
				allow_web_link: false, allow_google_drive: false, allow_toggle_optimize: false,
				restrictions: { allowed_file_types: ["image/*"], max_number_of_files: 1 }, dialog_title: __(this.labels[field]),
				on_success: (file) => {
					if (state.closed || state.upload !== upload || !file?.name || file.doctype !== "File" || Number(file.is_private) !== 1 || file.attached_to_doctype !== "Jifeng Product Profile" ||
						file.attached_to_name !== state.archive.name || file.attached_to_field !== field || !this.canonicalImage(file.file_url)) return;
					upload.known = true; state.uploadUnresolved = false; state.imageValues[field] = file.file_url;
					state.message.textContent = __("图片已上传，请保存图片变更。"); panel.candidates.replaceChildren(); this.renderImages(state); this.freeze(state);
				} });
			upload.uploader = uploader;
			const nativeUpload = uploader.upload_files.bind(uploader);
			uploader.upload_files = () => {
				if (upload.started) { state.message.textContent = __("本次上传已发起，请核对结果后继续。"); return Promise.resolve(); }
				if (!uploader.uploader?.files?.length) return nativeUpload();
				upload.started = true; uploader.dialog.get_primary_btn().prop("disabled", true);
				// Native upload_files may resolve before the server reply. Only on_success confirms it.
				try {
					const pending = nativeUpload();
					return pending?.catch ? pending.catch(() => { state.uploadUnresolved = true; this.freeze(state); }) : pending;
				} catch { state.uploadUnresolved = true; this.freeze(state); return Promise.resolve(); }
			};
			const hide = uploader.dialog.hide.bind(uploader.dialog);
			const finish = () => {
				if (!upload.open || state.upload !== upload) return;
				upload.open = false;
				if (upload.started && !upload.known) {
					state.uploadUnresolved = true; state.message.textContent = __("图片上传结果尚未确认，请核对上传结果。");
				}
				this.freeze(state);
			};
			// Bootstrap X/backdrop closes the modal without calling Dialog.hide.
			// The native hidden lifecycle handles every close path and keeps its callback.
			const onHide = uploader.dialog.on_hide;
			uploader.dialog.on_hide = function (...args) {
				try { onHide?.apply(this, args); } finally { finish(); }
			};
			uploader.dialog.hide = () => { finish(); hide(); };
		} catch {
			if (state.upload && !state.upload.started) { state.upload.open = false; state.upload = null; }
			state.message.textContent = __("图片上传窗口未能打开，当前图片保持原状，请重试。");
		}
		finally { state.busy = false; this.freeze(state); }
	}
	async resolveUpload(state) {
		if (state.closed || state.busy || state.confirming || !state.uploadUnresolved || !state.upload) return;
		const upload = state.upload, panel = state.images[upload.field]; state.busy = true; this.freeze(state); panel.candidates.replaceChildren();
		try {
			const response = await this.call("list_image_attachments", { name: state.archive.name, fieldname: upload.field });
			if (state.closed || state.upload !== upload) return;
			upload.candidates = this.attachmentRows(response.message).filter((row) => !upload.baseline.has(row.name));
			if (!upload.candidates.length) { panel.candidates.append(this.element("p", __("暂未核对到新增图片，请稍后核对；不会重复上传。"))); return; }
			panel.candidates.append(this.element("p", __("请选择要采用的已核对图片，采用后还需保存图片变更。")));
			for (const row of upload.candidates) { const candidate = this.element("div"), img = this.element("img"); img.src = row.unique_url; img.alt = row.file_name;
				candidate.append(img, this.element("span", row.file_name), this.element("small", row.creation ?? ""), this.button("采用这张图片", () => {
					if (state.closed || state.busy || state.confirming || state.upload !== upload || !state.uploadUnresolved || !upload.candidates.includes(row)) return;
					state.confirming = true; this.freeze(state);
					frappe.confirm(__("采用这张已核对图片作为待保存修改？此操作不会再次上传。"), () => {
						state.confirming = false; if (state.closed || state.upload !== upload) return;
						state.imageValues[upload.field] = row.file_url; state.uploadUnresolved = false; upload.known = true;
						panel.candidates.replaceChildren(); state.message.textContent = __("已采用核对图片，请保存图片变更。"); this.renderImages(state); this.freeze(state);
					}, () => { state.confirming = false; this.freeze(state); });
				})); panel.candidates.append(candidate);
			}
		} catch { panel.candidates.append(this.element("p", __("上传结果核对失败，请稍后核对；不会重复上传。"))); }
		finally { state.busy = false; this.freeze(state); }
	}
	destroy() {
		this.disposed = true; this.revision++; window.removeEventListener("beforeunload", this.beforeUnload);
		this.cancelExport();
		if (this.editor && !this.editor.closed) this.closeEditor();
	}
};
