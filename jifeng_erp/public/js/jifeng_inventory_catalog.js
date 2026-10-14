/* Read-only inventory browse. Column order: legacy-readonly-gui-report-20261002.md. */
window.JifengInventoryCatalog = class JifengInventoryCatalog {
	constructor(host, kind) {
		if (!["物料", "材料"].includes(kind)) throw new Error("Invalid inventory kind");
		this.host = host; this.kind = kind; this.statusFilter = "在用"; this.companyScope = null;
		this.disposed = false; this.revision = 0; this.detailRevision = 0; this.rows = [];
		this.page = 1; this.pageSize = 100; this.total = null; this.totalPages = 0;
		this.detail = null;
		this.fields = kind === "物料" ? [["legacy_number", "编号"], ["old_number", "旧编号"], ["major_category", "大类"],
			["minor_category", "小类"], ["display_name", "名称"], ["specification", "规格"], ["material", "材质"],
			["location", "库位"], ["display_uom", "单位"], ["list_price", "单价"], ["processing_price", "加工价"], ["weight", "重量"],
			["upper_limit", "上限"], ["lower_limit", "下限"], ["conversion_uom", "换算单位"], ["conversion_value", "换算值"],
			["supplier", "供应商"], ["notes", "备注"], ["inactive", "停用"], ["image", "图片"]] :
			[["legacy_number", "编号"], ["display_name", "名称"], ["specification", "规格"], ["shape", "形状"], ["color", "颜色"],
			["minor_category", "小类"], ["display_uom", "单位"], ["list_price", "单价"], ["weight", "重量"],
			["processing_price", "加工价"], ["location", "库位"], ["upper_limit", "上限"], ["lower_limit", "下限"],
			["conversion_uom", "换算单位"], ["conversion_value", "换算值"], ["supplier", "供应商"], ["notes", "备注"],
			["inactive", "停用"], ["image", "图片"]];
		this.build(); this.ready = Promise.resolve(true);
	}
	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className;
		return node;
	}
	button(label, action, className = "") {
		const node = this.element("button", __(label), "jfe-inventory-catalog-button " + className);
		node.type = "button"; node.addEventListener("click", action); return node;
	}
	call(method, args) { return frappe.call({ method: "jifeng_erp.inventory_catalog." + method, args }); }
	build() {
		this.host.classList.add("jfe-inventory-catalog"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-inventory-catalog-toolbar");
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("输入查询的关键字"); this.search.setAttribute("aria-label", __("查询关键字"));
		this.search.addEventListener("input", () => this.invalidate("关键字已改变，请查询。"));
		this.search.addEventListener("keydown", (event) => {
			if (event.key === "Enter") { event.preventDefault(); this.query(1); }
		});
		const companyHost = this.element("div", undefined, "jfe-inventory-catalog-company");
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), onchange: () => this.changeScope() } });
		this.companyControl.$input?.on?.("input.jifeng-inventory-catalog-company change.jifeng-inventory-catalog-company", () => this.changeScope());
		toolbar.append(this.search, this.button("查询", () => this.query(1), "primary"), companyHost);
		this.status = this.element("p", __("请选择公司后查询。"), "jfe-inventory-catalog-status"); this.status.setAttribute("role", "status");
		this.scroller = this.element("div", undefined, "jfe-inventory-catalog-scroll");
		const table = this.element("table", undefined, "jfe-inventory-catalog-table"); table.setAttribute("aria-label", __(this.kind + "浏览"));
		const head = this.element("thead"), headings = this.element("tr");
		for (const [_field, label] of this.fields) headings.append(this.element("th", __(label)));
		head.append(headings); this.tbody = this.element("tbody"); table.append(head, this.tbody); this.scroller.append(table);
		const pager = this.element("div", undefined, "jfe-inventory-catalog-pager");
		this.previous = this.button("上页", () => this.query(this.page - 1));
		this.pageLabel = this.element("span", "1", "jfe-inventory-catalog-current"); this.pageLabel.setAttribute("aria-label", __("当前页"));
		this.next = this.button("下页", () => this.query(this.page + 1));
		this.goto = this.element("input"); this.goto.type = "text"; this.goto.inputMode = "numeric"; this.goto.maxLength = 16;
		this.goto.value = "1"; this.goto.setAttribute("aria-label", __("前往页码"));
		this.goto.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.goToPage(); } });
		this.count = this.element("span", __("请选择公司后查询。"), "jfe-inventory-catalog-count");
		pager.append(this.previous, this.pageLabel, this.next, this.button("前往", () => this.goToPage()), this.goto, this.element("span", __("页")), this.count);
		this.host.append(toolbar, this.status, this.scroller, pager); this.renderPager();
	}
	changeScope() {
		if (this.disposed) return;
		const next = this.companyControl.get_value() || null;
		if (next === this.companyScope) return;
		this.companyScope = next; this.invalidate("公司已改变，请查询。");
	}
	invalidate(message) {
		this.revision++; this.closeDetail(); this.clearResults();
		this.status.textContent = __(message); this.page = 1; this.goto.value = "1"; this.renderPager();
	}
	clearResults() {
		this.rows = []; this.total = null; this.totalPages = 0; this.tbody.replaceChildren(); this.renderPager();
	}
	renderPager() {
		this.pageLabel.textContent = this.total === null ? "—" : String(this.page); this.previous.disabled = this.total === null || this.page <= 1;
		this.next.disabled = this.total === null || this.page >= this.totalPages;
		this.count.textContent = this.total === null ? __("尚未查询") : __("共") + " " + this.total + " " + __("条");
	}
	pageNumber(value) {
		if (typeof value === "string") {
			if (!/^[1-9][0-9]{0,15}$/.test(value) || String(Number(value)) !== value) return null;
			value = Number(value);
		}
		return Number.isSafeInteger(value) && value >= 1 ? value : null;
	}
	goToPage() { return this.query(this.goto.value); }
	async query(page = 1) {
		if (this.disposed) return;
		this.changeScope();
		const requestedPage = this.pageNumber(page), company = this.companyScope, search = this.search.value || "", status = this.statusFilter;
		const sequence = ++this.revision, scroll = { top: this.scroller.scrollTop, left: this.scroller.scrollLeft };
		this.closeDetail(); this.clearResults();
		if (!company || requestedPage === null || !Number.isSafeInteger(this.pageSize) || this.pageSize < 1 || this.pageSize > 100) {
			this.status.textContent = __(!company ? "请选择公司后查询。" : "请输入合法页码后查询。"); return;
		}
		this.status.textContent = __("正在查询…");
		const args = { kind: this.kind, company, search, status, page: requestedPage, page_size: this.pageSize };
		try {
			const response = await this.call("query_archives", args);
			this.changeScope();
			if (!this.current(sequence, args)) return;
			const result = response.message;
			if (!this.validPage(result, args)) throw new Error("Incomplete inventory page");
			this.rows = result.rows; this.page = result.page; this.total = result.total; this.totalPages = result.total_pages;
			this.goto.value = String(this.page); this.renderRows(); this.renderPager();
			this.scroller.scrollTop = scroll.top; this.scroller.scrollLeft = scroll.left;
			this.status.textContent = this.total ? __("查询完成，双击行或按回车查看只读档案。") : __("没有可见记录。");
		} catch {
			this.changeScope();
			if (this.current(sequence, args)) { this.clearResults(); this.status.textContent = __("查询未完成，请重新查询或输入合法页码。"); }
		}
	}
	current(sequence, args) {
		return !this.disposed && sequence === this.revision && args.kind === this.kind && args.company === this.companyScope &&
			args.company === (this.companyControl.get_value() || null) &&
			args.search === this.search.value && args.status === this.statusFilter;
	}
	validPage(result, args) {
		if (!Number.isSafeInteger(args.page) || args.page < 1 || !Number.isSafeInteger(args.page_size) || args.page_size < 1 || args.page_size > 100 ||
			!result || result.readonly !== true || result.can_write !== false ||
			!["kind", "company", "search", "status", "page", "page_size"].every((field) => result[field] === args[field]) ||
			!Number.isSafeInteger(result.page) || !Number.isSafeInteger(result.page_size) ||
			!Number.isSafeInteger(result.total) || result.total < 0 ||
			!Number.isSafeInteger(result.total_pages) || result.total_pages < 0 ||
			args.page > Math.max(1, result.total_pages) || !Array.isArray(result.rows)) return false;
		const size = BigInt(args.page_size), total = BigInt(result.total);
		const pages = total === 0n ? 0n : (total - 1n) / size + 1n, start = (BigInt(args.page) - 1n) * size;
		if (result.total_pages !== Number(pages) || start > BigInt(Number.MAX_SAFE_INTEGER)) return false;
		const offset = Number(start);
		if (!Number.isSafeInteger(offset)) return false;
		const length = Math.min(args.page_size, Math.max(0, result.total - offset));
		return result.rows.length === length && new Set(result.rows.map((row) => row?.name)).size === result.rows.length &&
			result.rows.every((row) => this.validArchive(row, args.company) &&
				(args.status === "全部" || Boolean(row.profile.inactive) === (args.status === "停用")));
	}
	validArchive(row, company, name = null) {
		if (!row || row.readonly !== true || row.can_write !== false || row.kind !== this.kind || row.company !== company ||
			typeof row.name !== "string" || !row.name || name && row.name !== name || !row.profile || !row.item ||
			!row.field_meta?.profile || !row.field_meta.item || typeof row.profile_version !== "string" || !row.profile_version ||
			typeof row.item_version !== "string" || !row.item_version || typeof row.item_code !== "string" || !row.item_code ||
			row.profile.company !== company || row.profile.archive_kind !== this.kind || row.profile.item_code !== row.item_code ||
			row.item.item_code !== row.item_code || ![0, 1, false, true].includes(row.profile.inactive)) return false;
		if (!["company", "archive_kind", "item_code", "inactive"].every((field) => row.field_meta.profile[field]) || !row.field_meta.item.item_code) return false;
		for (const group of ["profile", "item"]) {
			if (Object.entries(row[group]).some(([field, value]) => !row.field_meta[group][field] || row.field_meta[group][field].read_only !== true ||
				value !== null && (!["string", "number", "boolean"].includes(typeof value) || typeof value === "number" && !Number.isFinite(value)))) return false;
		}
		const image = row.image;
		const sourceValid = image && (image.source_doctype === "Item" && image.source_name === row.item_code && image.label === "关联业务商品图片" ||
			image.source_doctype === "Jifeng Inventory Profile" && image.source_name === row.name && image.label === "档案图片");
		return !!sourceValid && image.source_field === "image" && ["未上传", "无字段读取权限", "附件不可用", "已上传"].includes(image.state) &&
			typeof image.url === "string" && (image.state === "已上传" ? this.safePreview(image.url) : image.url === "");
	}
	safePreview(url) {
		if (typeof url !== "string" || !/^\/private\/files\/[^/?#\\\x00-\x1f]+\?fid=[^&#\\\x00-\x1f]+$/.test(url)) return false;
		try {
			let path = url.split("?")[0].slice("/private/files/".length);
			for (let index = 0; index < 3; index++) {
				path = decodeURIComponent(path);
				if (!path || /[\/\\?#\x00-\x1f]/.test(path) || path === "." || path === "..") return false;
			}
			return true;
		} catch { return false; }
	}
	imageCell(image, target) {
		target.title = __(image.label) + " · " + image.source_doctype + " / " + image.source_name + " / image";
		if (image.state === "已上传" && this.safePreview(image.url)) {
			const img = this.element("img"); img.src = image.url; img.alt = __(image.label); target.append(img);
		} else target.textContent = __(image.state);
	}
	renderRows() {
		this.tbody.replaceChildren();
		for (const row of this.rows) {
			const tr = this.element("tr"); tr.tabIndex = 0; tr.setAttribute("aria-label", __("查看只读档案"));
			for (const [field] of this.fields) {
				const td = this.element("td");
				if (field === "image") this.imageCell(row.image, td);
				else td.textContent = String(row.profile[field] ?? "");
				tr.append(td);
			}
			tr.addEventListener("dblclick", () => this.readDetail(row.name));
			tr.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.readDetail(row.name); } });
			this.tbody.append(tr);
		}
	}
	async readDetail(name) {
		this.changeScope();
		if (this.disposed || !this.companyScope || !this.rows.some((row) => row.name === name)) return;
		this.closeDetail();
		const sequence = this.detailRevision, listRevision = this.revision, company = this.companyScope;
		const dialog = new frappe.ui.Dialog({ title: __(this.kind + "浏览 · 只读档案"), size: "extra-large", no_submit_on_enter: true,
			fields: [{ fieldname: "inventory_detail", fieldtype: "HTML" }] });
		dialog.$wrapper.addClass("jfe-inventory-catalog-detail");
		const body = dialog.fields_dict.inventory_detail.$wrapper.get(0);
		const state = { dialog, body, closed: false }; this.detail = state;
		const hide = dialog.hide.bind(dialog);
		dialog.hide = () => { if (state.closed) return; state.closed = true; state.body.replaceChildren(); hide();
			if (this.detail === state) { this.detail = null; this.detailRevision++; } };
		dialog.on_hide = () => { state.closed = true; state.body.replaceChildren(); if (this.detail === state) { this.detail = null; this.detailRevision++; } };
		dialog.add_custom_action(__("关闭"), () => dialog.hide());
		body.replaceChildren(this.element("p", __("正在读取…"))); dialog.show();
		const args = { name, kind: this.kind, company };
		try {
			const response = await this.call("read_archive", args);
			this.changeScope();
			if (this.disposed || state.closed || this.detail !== state || sequence !== this.detailRevision ||
				listRevision !== this.revision || company !== this.companyScope || company !== (this.companyControl.get_value() || null)) return;
			if (!this.validArchive(response.message, company, name)) throw new Error("Incomplete archive detail");
			this.renderDetail(state, response.message);
		} catch {
			this.changeScope();
			if (!this.disposed && !state.closed && this.detail === state && sequence === this.detailRevision &&
				listRevision === this.revision && company === this.companyScope && company === (this.companyControl.get_value() || null)) body.replaceChildren(this.element("p", __("档案未能读取，请关闭后重试。")));
		}
		return dialog;
	}
	renderDetail(state, row) {
		state.body.replaceChildren();
		state.body.append(this.element("p", __("公司") + "：" + row.company + " · " + __("关联业务商品") + "：" + row.item_code));
		const fields = this.element("dl", undefined, "jfe-inventory-catalog-detail-fields");
		for (const [field, label] of this.fields) {
			fields.append(this.element("dt", __(label)));
			const value = this.element("dd"); if (field === "image") this.imageCell(row.image, value);
			else value.textContent = String(row.profile[field] ?? ""); fields.append(value);
		}
		state.body.append(fields);
	}
	closeDetail() { this.detailRevision++; this.detail?.dialog.hide(); }
	isDirty() { return false; }
	getCloseBlock() { return ""; }
	dispose() {
		if (this.disposed) return;
		this.disposed = true; this.revision++; this.closeDetail(); this.clearResults();
		this.host.replaceChildren();
	}
	destroy() { this.dispose(); }
};
