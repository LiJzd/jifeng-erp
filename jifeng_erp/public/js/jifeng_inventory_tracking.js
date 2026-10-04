/* Shared inventory tracking: saved archives and native cutoff balances. */
window.JifengInventoryTracking = class JifengInventoryTracking extends window.JifengInventoryCatalog {
	constructor(host, kind, bridge) {
		super(host, kind); this.bridge = bridge; this.visible = true; this.loading = false;
		this.mode = "列表"; this.request = null; this.resultScope = null; this.ready = this.initialize();
	}
	build() {
		this.host.classList.add("jfe-inventory-tracking"); this.host.replaceChildren();
		this.fields = [["warehouse", "仓库"], ["legacy_number", "编号"], ["stock_qty", "库存"],
			["valuation_rate", "单价"], ["stock_value", "金额"], ["weight", "重量"], ["total_weight", "总重量"],
			["major_category", "大类"], ["minor_category", "小类"], ["material", "材质"], ["stock_uom", "单位"],
			["display_name", "名称"], ["specification", "规格"], ["location", "库位"], ["notes", "备注"]];
		if (this.kind === "材料") this.fields = [["warehouse", "仓库"], ["legacy_number", "编号"], ["stock_qty", "库存"],
			["valuation_rate", "单价"], ["stock_value", "金额"], ["weight", "重量"], ["total_weight", "总重量"],
			["minor_category", "小类"], ["specification", "规格"], ["shape", "形状"], ["color", "颜色"],
			["display_name", "名称"], ["location", "库位"], ["old_number", "旧编号"], ["owner_name", "负责"], ["region", "区域"], ["supplier", "供应商"]];
		const toolbar = this.element("div", undefined, "jfe-inventory-tracking-toolbar");
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("输入查询的关键字"); this.search.setAttribute("aria-label", __("查询关键字"));
		this.search.addEventListener("input", () => this.invalidate());
		this.search.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.query(1); } });
		this.queryButton = this.button("查询", () => this.query(1), "primary");
		const holders = ["company", "warehouse", "date"].map(() => this.element("div", undefined, "jfe-inventory-tracking-filter"));
		this.companyControl = this.makeControl(holders[0], "company", "Link", "公司", "Company", () => this.companyChanged());
		this.warehouseControl = this.makeControl(holders[1], "warehouse", "Link", "仓库（可选）", "Warehouse", () => this.invalidate());
		this.warehouseControl.get_query = () => ({ filters: { company: this.linkValue(this.companyControl) || "", is_group: 0 } });
		this.dateControl = this.makeControl(holders[2], "as_of", "Date", "截止日期", null, () => this.invalidate());
		toolbar.append(this.search, this.queryButton, ...holders);
		const modes = this.element("div", undefined, "jfe-inventory-tracking-modes");
		this.modeButtons = {};
		for (const mode of ["列表", "图片"]) {
			const button = this.button(mode + "模式", () => this.setMode(mode)); this.modeButtons[mode] = button; modes.append(button);
		}
		this.note = this.element("p", __("库存、单价和金额按截止日库存账簿展示，金额为公司本币。重量保留档案原值；总重量换算尚未确认，留空。"), "jfe-inventory-tracking-note");
		this.status = this.element("p", __("请选择公司后查询。"), "jfe-inventory-tracking-status");
		this.status.setAttribute("role", "status"); this.status.setAttribute("aria-live", "polite");
		this.scopeLabel = this.element("p", undefined, "jfe-inventory-tracking-scope");
		this.scroller = this.element("div", undefined, "jfe-inventory-tracking-scroll");
		this.table = this.element("table"); this.table.setAttribute("aria-label", __(this.kind + "跟踪列表"));
		const head = this.element("thead"), titles = this.element("tr");
		for (const [field, label] of this.fields) { const th = this.element("th", __(label)); th.dataset.field = field; titles.append(th); }
		titles.append(this.element("th", __("来源"))); head.append(titles); this.tbody = this.element("tbody");
		this.table.append(head, this.tbody); this.scroller.append(this.table);
		this.cards = this.element("div", undefined, "jfe-inventory-tracking-cards"); this.cards.hidden = true;
		const pager = this.element("div", undefined, "jfe-inventory-tracking-pager");
		this.previous = this.button("上页", () => this.query(this.page - 1));
		this.pageLabel = this.element("span", "—"); this.pageLabel.setAttribute("aria-label", __("当前页"));
		this.next = this.button("下页", () => this.query(this.page + 1));
		this.goto = this.element("input"); this.goto.type = "text"; this.goto.inputMode = "numeric"; this.goto.maxLength = 16;
		this.goto.value = "1"; this.goto.setAttribute("aria-label", __("前往页码"));
		this.goto.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.query(this.goto.value); } });
		this.count = this.element("span", __("尚未查询"));
		pager.append(this.previous, this.pageLabel, this.next, this.button("前往", () => this.query(this.goto.value)), this.goto, this.element("span", __("页")), this.count);
		this.host.append(toolbar, modes, this.note, this.status, this.scopeLabel, this.scroller, this.cards, pager); this.renderPager();
	}
	makeControl(parent, fieldname, fieldtype, label, options, action) {
		const control = frappe.ui.form.make_control({ parent, render_input: true,
			df: { fieldname, fieldtype, label: __(label), ...(options ? { options } : {}), onchange: action } });
		control.$input?.on?.("input.jifeng-inventory-tracking change.jifeng-inventory-tracking", action);
		// Native Link's clear button only changes the input and focuses it.
		control.$link_clear?.on?.("click.jifeng-inventory-tracking", action); return control;
	}
	async initialize() {
		const company = frappe.defaults?.get_user_default?.("Company"), date = frappe.datetime?.get_today?.();
		if (this.validName(company)) await this.companyControl.set_value(company);
		if (this.validDate(date)) await this.dateControl.set_value(date);
		this.renderPager(); return !this.disposed;
	}
	validName(value) { return typeof value === "string" && !!value && value === value.trim() && [...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value); }
	validDate(value) {
		if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
		const [y, m, d] = value.split("-").map(Number), leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
		return y > 0 && m >= 1 && m <= 12 && d >= 1 && d <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
	}
	linkValue(control, optional = false) {
		const value = control?.get_value() || "", input = control?.get_input_value?.() ?? control?.$input?.val?.();
		if (input !== undefined && input !== value) return null;
		return !value && optional ? "" : (this.validName(value) ? value : null);
	}
	context() {
		const company = this.linkValue(this.companyControl), warehouse = this.linkValue(this.warehouseControl, true), as_of = this.dateControl.get_value();
		const search = this.search.value || "";
		if (!company || warehouse === null || !this.validDate(as_of) || typeof search !== "string" || [...search].length > 140 || /[\x00-\x1f\x7f]/.test(search)) return null;
		return { kind: this.kind, company, warehouse, as_of, search };
	}
	companyChanged() { this.invalidate(); this.warehouseControl?.set_value(""); }
	changeScope() { /* Every query compares current native controls directly. */ }
	invalidate(message = "筛选已变化，请重新查询。") {
		if (this.disposed) return;
		this.revision++; this.loading = false; const request = this.request; this.request = null; request?.abort?.();
		this.clearResults(); this.page = 1; if (this.goto) this.goto.value = "1";
		if (this.status) this.status.textContent = __(message); this.renderPager();
	}
	clearResults() {
		this.rows = []; this.total = null; this.totalPages = 0; this.resultScope = null;
		this.tbody?.replaceChildren(); this.cards?.replaceChildren(); if (this.scopeLabel) this.scopeLabel.textContent = ""; this.renderPager();
	}
	renderPager() {
		if (!this.pageLabel) return;
		this.pageLabel.textContent = this.total === null ? "—" : String(this.page);
		this.previous.disabled = !this.visible || this.loading || this.total === null || this.page <= 1;
		this.next.disabled = !this.visible || this.loading || this.total === null || this.page >= this.totalPages;
		this.count.textContent = this.total === null ? __("尚未查询") : __("共") + " " + this.total + " " + __("条");
		if (this.queryButton) this.queryButton.disabled = !this.visible || !!this.loading;
	}
	sameContext(a, b) { return !!a && !!b && ["kind", "company", "warehouse", "as_of", "search"].every(key => a[key] === b[key]); }
	current(sequence, args) { return !this.disposed && this.visible && sequence === this.revision && this.sameContext(args, this.context()); }
	keys(object, required) { return !!object && typeof object === "object" && !Array.isArray(object) && Object.keys(object).length === required.length && required.every(key => Object.hasOwn(object, key)); }
	decimal(value) { return typeof value === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$/.test(value); }
	validRow(row, args) {
		if (!this.keys(row, ["profile_name", "profile_version", "item_code", "item_version", "warehouse", "warehouse_version", "stock_uom", "stock_qty", "valuation_rate", "stock_value", "profile", "image", "total_weight"]) ||
			!["profile_name", "item_code", "warehouse", "stock_uom"].every(key => this.validName(row[key])) ||
			!["profile_version", "item_version", "warehouse_version"].every(key => typeof row[key] === "string" && !!row[key]) ||
			!["stock_qty", "valuation_rate", "stock_value"].every(key => this.decimal(row[key])) || row.total_weight !== null || args.warehouse && row.warehouse !== args.warehouse) return false;
		const fields = ["legacy_number", "display_name", "major_category", "minor_category", "material", "specification", "location", "notes", "weight", "display_uom", "shape", "color", "old_number", "owner_name", "region", "supplier"];
		if (!row.profile || typeof row.profile !== "object" || Array.isArray(row.profile) || Object.entries(row.profile).some(([key, value]) =>
			!fields.includes(key) || value !== null && (key === "weight" ? !this.decimal(value) : typeof value !== "string"))) return false;
		const image = row.image;
		return this.keys(image, ["source_doctype", "source_name", "source_field", "label", "url", "state"]) && image.source_doctype === "Item" &&
			image.source_name === row.item_code && image.source_field === "image" && image.label === "关联业务商品图片" &&
			["未上传", "无字段读取权限", "附件不可用", "已上传"].includes(image.state) &&
			(image.state === "已上传" ? this.safePreview(image.url) : image.url === "");
	}
	validPage(result, args) {
		if (!this.keys(result, ["kind", "company", "search", "warehouse", "as_of", "currency", "basis", "page", "page_size", "total", "total_pages", "rows", "readonly", "can_write"]) ||
			result.readonly !== true || result.can_write !== false || !this.sameContext(result, args) || !this.validName(result.currency) ||
			result.page !== args.page || result.page_size !== args.page_size || !Number.isSafeInteger(result.total) || result.total < 0 ||
			!Number.isSafeInteger(args.page) || args.page < 1 || !Number.isSafeInteger(args.page_size) || args.page_size < 1 || args.page_size > 100 ||
			!Number.isSafeInteger(result.total_pages) || args.page > Math.max(1, result.total_pages) || !Array.isArray(result.rows) ||
			!this.keys(result.basis, ["stock_qty", "valuation_rate", "stock_value", "stock_uom", "profile_weight", "profile_display_uom", "total_weight"]) ||
			!Object.values(result.basis).every(value => typeof value === "string" && !!value)) return false;
		const total = BigInt(result.total), size = BigInt(args.page_size), offset = (BigInt(args.page) - 1n) * size;
		if (offset > BigInt(Number.MAX_SAFE_INTEGER) || result.total_pages !== Number(total === 0n ? 0n : (total - 1n) / size + 1n) ||
			result.rows.length !== Math.min(args.page_size, Math.max(0, result.total - Number(offset)))) return false;
		return result.rows.every(row => this.validRow(row, args)) && new Set(result.rows.map(row => JSON.stringify([row.profile_name, row.warehouse]))).size === result.rows.length;
	}
	async query(page = 1) {
		if (this.disposed || !this.visible || this.loading) return;
		const scope = this.context(), requestedPage = this.pageNumber(page); this.clearResults();
		if (!scope || !requestedPage) { this.status.textContent = __("请选择有效的公司、截止日期和页码，仓库须为明确的叶仓。") ; return; }
		const args = { ...scope, page: requestedPage, page_size: this.pageSize }, sequence = ++this.revision;
		this.loading = true; this.renderPager(); this.status.textContent = __("正在查询库存跟踪…");
		try {
			const requestArgs = { ...args }; if (!requestArgs.warehouse) delete requestArgs.warehouse;
			this.request = frappe.call({ method: "jifeng_erp.inventory_tracking.query_tracking", type: "GET", args: requestArgs });
			const response = await this.request; if (!this.current(sequence, args)) return;
			if (!this.validPage(response?.message, args)) throw new Error("Invalid tracking scope");
			const result = response.message; this.rows = result.rows; this.page = result.page; this.total = result.total; this.totalPages = result.total_pages;
			this.resultScope = scope; this.goto.value = String(this.page);
			this.scopeLabel.textContent = [scope.company, scope.as_of, scope.warehouse || __("公司叶仓"), result.currency].join(" · ");
			this.renderRows(result.basis); this.status.textContent = this.total ? __("查询完成。") : __("没有可见的库存历史记录。");
		} catch { if (this.current(sequence, args)) { this.clearResults(); this.status.textContent = __("查询未完成，请核对筛选条件和完整来源权限后重试。"); } }
		finally { if (!this.disposed && sequence === this.revision) { if (!this.sameContext(scope, this.context())) this.clearResults(); this.loading = false; this.request = null; this.renderPager(); } }
	}
	cellValue(row, field) { return ["warehouse", "stock_qty", "valuation_rate", "stock_value", "stock_uom", "total_weight"].includes(field) ? row[field] : row.profile[field]; }
	tooltip(row, field, basis) {
		if (field === "weight") return basis.profile_weight;
		if (field === "stock_qty") return basis.stock_qty + " · " + __("库存单位：") + row.stock_uom;
		if (field === "stock_uom") return basis.stock_uom + (Object.hasOwn(row.profile, "display_uom") ? " · " + __("档案单位：") + (row.profile.display_uom ?? "—") : "");
		return basis[field] || "";
	}
	sourceButtons(row) {
		const node = this.element("div", undefined, "jfe-inventory-tracking-sources");
		for (const [doctype, name, label] of [["Jifeng Inventory Profile", row.profile_name, "档案"], ["Item", row.item_code, "商品"], ["Warehouse", row.warehouse, "仓库"]])
			node.append(this.button(label, () => this.openSource(row, doctype, name, label)));
		return node;
	}
	renderRows(basis) {
		this.tbody.replaceChildren(); this.cards.replaceChildren(); this.scroller.hidden = this.mode !== "列表"; this.cards.hidden = this.mode !== "图片";
		for (const row of this.rows) {
			if (this.mode === "列表") {
				const tr = this.element("tr");
				for (const [field] of this.fields) { const td = this.element("td", this.cellValue(row, field) ?? "—"); td.title = this.tooltip(row, field, basis); tr.append(td); }
				const source = this.element("td"); source.append(this.sourceButtons(row)); tr.append(source); this.tbody.append(tr);
			} else {
				const card = this.element("article"); card.append(this.element("h3", (row.profile.legacy_number ?? "—") + " · " + row.warehouse));
				const image = this.element("div", undefined, "jfe-inventory-tracking-image"); this.imageCell(row.image, image); for (const img of image.querySelectorAll?.("img") || []) img.loading = "lazy"; card.append(image);
				const fields = this.element("dl"); for (const [field, label] of [["warehouse", "仓库"], ["legacy_number", "编号"], ["stock_qty", "库存"]]) { fields.append(this.element("dt", __(label))); const value = this.element("dd", this.cellValue(row, field) ?? "—"); value.title = this.tooltip(row, field, basis); fields.append(value); }
				card.append(fields, this.sourceButtons(row)); this.cards.append(card);
			}
		}
	}
	setMode(mode) {
		if (this.disposed || !this.visible || !["列表", "图片"].includes(mode) || mode === this.mode) return;
		this.mode = mode; this.scroller.hidden = mode !== "列表"; this.cards.hidden = mode !== "图片";
		for (const [key, button] of Object.entries(this.modeButtons)) button.setAttribute("aria-pressed", String(key === mode));
		this.invalidate("模式已切换，请重新查询。");
	}
	openSource(row, doctype, name, label) {
		if (!this.visible || this.loading || !this.rows.includes(row) || !this.sameContext(this.resultScope, this.context()) || !this.bridge?.open) return;
		this.invalidate("已打开来源；返回后请重新查询库存跟踪。");
		this.bridge.open({ title: label + "：" + name, module: this.kind, kind: "form", doctype, route: ["Form", doctype, name] });
	}
	setVisible(value) {
		if (this.disposed || this.visible === !!value) return;
		this.visible = !!value; if (!this.visible) this.invalidate("页面已离开，请重新查询。"); this.renderPager();
	}
	dispose() {
		if (this.disposed) return;
		this.invalidate(); this.disposed = true; for (const control of [this.companyControl, this.warehouseControl, this.dateControl]) {
			control.$input?.off?.(".jifeng-inventory-tracking"); control.$link_clear?.off?.(".jifeng-inventory-tracking");
		}
		this.host.replaceChildren(); this.host.classList.remove("jfe-inventory-tracking");
	}
};
