/* Shared inventory tracking: saved archives and native cutoff balances. */
window.JifengInventoryTracking = class JifengInventoryTracking extends window.JifengInventoryCatalog {
	constructor(host, kind, bridge) {
		super(host, kind); this.bridge = bridge; this.visible = true; this.loading = false;
		this.mode = "列表"; this.request = null; this.resultScope = null; this.ready = this.initialize();
	}
	build() {
		this.host.classList.add("jfe-inventory-tracking"); this.host.replaceChildren();
		this.columnFilters = []; this.columnSort = null; this.columnPopup = null;
		this.detailKeydown = event => {
			if (event.key === "Escape" && (this.columnPopup || this.detail)) {
				event.preventDefault(); event.stopPropagation();
				if (this.columnPopup) this.closeColumnPopup(true); else this.closeDetail(true);
			}
		};
		this.host.addEventListener("keydown", this.detailKeydown);
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
		this.columnHeaders = new Map();
		for (const [field, label] of this.fields) {
			const th = this.element("th"); th.dataset.field = field; th.setAttribute("aria-sort", "none");
			const button = this.button(label, () => this.openColumnMenu(field, button), "jfe-inventory-tracking-column");
			button.setAttribute("aria-label", __(label) + __("列菜单")); button.setAttribute("aria-haspopup", "menu");
			th.append(button); titles.append(th); this.columnHeaders.set(field, { th, button, label });
		}
		titles.append(this.element("th", __("来源"))); head.append(titles); this.tbody = this.element("tbody");
		this.table.append(head, this.tbody); this.scroller.append(this.table);
		this.cards = this.element("div", undefined, "jfe-inventory-tracking-cards"); this.cards.hidden = true;
		const pager = this.element("div", undefined, "jfe-inventory-tracking-pager");
		this.previous = this.button("上页", () => this.query(this.page - 1));
		this.pageLabel = this.element("span", "—"); this.pageLabel.setAttribute("aria-label", __("当前页"));
		this.next = this.button("下页", () => this.query(this.page + 1));
		this.goto = this.element("input"); this.goto.type = "text"; this.goto.inputMode = "numeric"; this.goto.maxLength = 16;
		this.goto.value = "1"; this.goto.setAttribute("aria-label", __("前往页码"));
		this.goto.addEventListener("input", () => this.closeDetail(false));
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
		this.closeColumnPopup(false);
		this.revision++; this.loading = false; const request = this.request; this.request = null; request?.abort?.();
		this.clearResults(); this.page = 1; if (this.goto) this.goto.value = "1";
		if (this.status) this.status.textContent = __(message); this.renderPager();
	}
	clearResults() {
		this.closeDetail(false);
		this.rows = []; this.total = null; this.totalPages = 0; this.resultScope = null; this.resultColumns = null; this.resultCurrency = null;
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
	columnContext() { const scope = this.context(); return scope && { ...scope, column_filters: JSON.parse(JSON.stringify(this.columnFilters)), sort: this.columnSort && { ...this.columnSort } }; }
	sameColumnContext(a, b) { return this.sameContext(a, b) && this.sameData(a.column_filters || [], b.column_filters || []) && this.sameData(a.sort ?? null, b.sort ?? null); }
	current(sequence, args) { return !this.disposed && this.visible && sequence === this.revision && this.sameColumnContext(args, this.columnContext()); }
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
		const extended = Object.hasOwn(args, "column_filters");
		if (!this.keys(result, ["kind", "company", "search", "warehouse", "as_of", "currency", "basis", "page", "page_size", "total", "total_pages", "rows", "readonly", "can_write", ...(extended ? ["filter_protocol", "column_filters", "sort"] : [])]) ||
			extended && (result.filter_protocol !== 1 || !this.sameColumnContext(result, args)) ||
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
		this.closeColumnPopup(false);
		const scope = this.context(), requestedPage = this.pageNumber(page); this.clearResults();
		if (!scope || !requestedPage) { this.status.textContent = __("请选择有效的公司、截止日期和页码，仓库须为明确的叶仓。") ; return; }
		const extended = this.columnFilters.length > 0 || this.columnSort !== null;
		const args = { ...scope, ...(extended ? { column_filters: JSON.parse(JSON.stringify(this.columnFilters)), sort: this.columnSort && { ...this.columnSort } } : {}), page: requestedPage, page_size: this.pageSize }, sequence = ++this.revision;
		this.loading = true; this.renderPager(); this.status.textContent = __("正在查询库存跟踪…");
		try {
			const requestArgs = { ...args }; if (!requestArgs.warehouse) delete requestArgs.warehouse;
			if (extended) { requestArgs.column_filters = JSON.stringify(args.column_filters); requestArgs.sort = JSON.stringify(args.sort); }
			this.request = frappe.call({ method: "jifeng_erp.inventory_tracking.query_tracking", type: extended ? "POST" : "GET", args: requestArgs });
			const response = await this.request; if (!this.current(sequence, args)) return;
			if (!this.validPage(response?.message, args)) throw new Error("Invalid tracking scope");
			const result = response.message; this.rows = result.rows; this.page = result.page; this.total = result.total; this.totalPages = result.total_pages;
			this.resultScope = scope; this.resultColumns = this.columnContext(); this.resultCurrency = result.currency; this.goto.value = String(this.page);
			this.scopeLabel.textContent = [scope.company, scope.as_of, scope.warehouse || __("公司叶仓"), result.currency].join(" · ");
			this.renderRows(result.basis); this.status.textContent = this.total ? __("查询完成。") : __("没有可见的库存历史记录。");
		} catch { if (this.current(sequence, args)) { this.clearResults(); this.status.textContent = __("查询未完成，请核对筛选条件和完整来源权限后重试。"); } }
		finally { if (!this.disposed && sequence === this.revision) { if (!this.sameColumnContext(args, this.columnContext())) this.clearResults(); this.loading = false; this.request = null; this.renderPager(); } }
	}
	cellValue(row, field) { return ["warehouse", "stock_qty", "valuation_rate", "stock_value", "stock_uom", "total_weight"].includes(field) ? row[field] : row.profile[field]; }
	columnOperators() { return [["eq", "等于"], ["ne", "不等于"], ["starts", "开头是"], ["not_starts", "开头不是"], ["ends", "结尾是"], ["not_ends", "结尾不是"], ["contains", "包含"], ["not_contains", "不包含"], ["in", "在里面"], ["not_in", "不在里面"]]; }
	validColumnValue(text) { return typeof text === "string" && [...text].length <= 2000 && !/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(text); }
	validColumnConditions(filters, sort) {
		const names = this.fields.map(([field]) => field), seen = new Set();
		return Array.isArray(filters) && filters.length <= names.length && JSON.stringify(filters).length <= 65536 && filters.every(condition => {
			if (!this.keys(condition, ["field", "operator", "value"]) || !names.includes(condition.field) || seen.has(condition.field)) return false;
			seen.add(condition.field);
			return ["in", "not_in"].includes(condition.operator) ? Array.isArray(condition.value) && condition.value.length <= 200 &&
				condition.value.every(item => item === null || this.validColumnValue(item)) && new Set(condition.value.map(item => JSON.stringify(item))).size === condition.value.length :
				this.columnOperators().slice(0, 8).some(([operator]) => operator === condition.operator) && this.validColumnValue(condition.value);
		}) && (sort === null || this.keys(sort, ["field", "direction"]) && names.includes(sort.field) && ["asc", "desc"].includes(sort.direction));
	}
	renderColumnMarkers() {
		for (const [field, header] of this.columnHeaders) {
			const filtered = this.columnFilters.some(condition => condition.field === field), direction = this.columnSort?.field === field ? this.columnSort.direction : "";
			header.button.dataset.filtered = String(filtered); header.button.dataset.direction = direction;
			header.button.setAttribute("aria-label", __(header.label) + __("列菜单") + (filtered ? __("，已筛选") : "") + (direction ? __(direction === "asc" ? "，升序" : "，降序") : ""));
			header.th.setAttribute("aria-sort", direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none");
		}
	}
	applyColumnCondition(field, condition, order = this.columnSort) {
		if (this.disposed || !this.visible || !this.columnHeaders.has(field)) return;
		const filters = this.columnFilters.filter(item => item.field !== field);
		if (condition) filters.push({ field, operator: condition.operator, value: condition.value });
		filters.sort((a, b) => this.fields.findIndex(([name]) => name === a.field) - this.fields.findIndex(([name]) => name === b.field));
		if (!this.validColumnConditions(filters, order)) {
			if (this.columnPopup?.error) this.columnPopup.error.textContent = __("筛选条件过长或无效，请减少选择的值或缩短文本。");
			return;
		}
		this.closeColumnPopup(true);
		if (this.sameData(filters, this.columnFilters) && this.sameData(order, this.columnSort)) return;
		this.columnFilters = JSON.parse(JSON.stringify(filters)); this.columnSort = order && { ...order };
		this.renderColumnMarkers(); this.invalidate(); return this.query(1);
	}
	setColumnSort(field, direction) {
		const condition = this.columnFilters.find(item => item.field === field);
		return this.applyColumnCondition(field, condition || null, { field, direction });
	}
	columnPopupCurrent(state) {
		return !this.disposed && this.visible && this.columnPopup === state && !state.closed && state.revision === this.revision &&
			this.sameColumnContext(state.scope, this.columnContext());
	}
	closeColumnPopup(restoreFocus = true) {
		const state = this.columnPopup; this.columnPopup = null;
		if (!state) return;
		state.closed = true; state.request?.abort?.(); state.request = null; state.layer.remove();
		if (state.outside) this.host.removeEventListener("pointerdown", state.outside);
		for (const [node, inert] of state.background || []) node.inert = inert;
		state.trigger?.setAttribute("aria-expanded", "false");
		if (restoreFocus && this.visible && !this.disposed) state.trigger?.focus?.();
	}
	makeColumnPopup(field, trigger, dialog = false) {
		this.closeColumnPopup(false); this.closeDetail(false);
		const layer = this.element("section", undefined, "jfe-inventory-tracking-column-popup" + (dialog ? " dialog" : " menu"));
		const state = { field, trigger, layer, scope: this.columnContext(), revision: this.revision, request: null, requestRevision: 0, closed: false, controls: [], background: [] };
		layer.setAttribute("role", dialog ? "dialog" : "menu"); layer.setAttribute("aria-label", __(this.columnHeaders.get(field).label) + __(dialog ? "筛选" : "列菜单"));
		if (dialog) {
			layer.setAttribute("aria-modal", "true"); state.background = Array.from(this.host.children, node => [node, node.inert]); for (const [node] of state.background) node.inert = true;
			if (layer.style) layer.style.top = ((this.host.scrollTop || 0) + 24) + "px";
		}
		else if (trigger?.getBoundingClientRect && this.host.getBoundingClientRect) {
			const anchor = trigger.getBoundingClientRect(), host = this.host.getBoundingClientRect();
			layer.style.left = Math.max(0, Math.min(anchor.left - host.left + this.host.scrollLeft, this.host.clientWidth - 220)) + "px";
			layer.style.top = Math.max(0, anchor.bottom - host.top + this.host.scrollTop) + "px";
		}
		layer.addEventListener("keydown", event => {
			if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); this.closeColumnPopup(true); return; }
			const controls = state.controls.filter(control => !control.disabled), index = controls.indexOf(event.target);
			if (dialog && event.key === "Tab" || !dialog && ["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
				event.preventDefault();
				const next = event.key === "Home" ? 0 : event.key === "End" ? controls.length - 1 :
					(index + (event.key === "ArrowUp" || event.shiftKey ? -1 : 1) + controls.length) % controls.length;
				controls[next]?.focus?.();
			}
		});
		if (!dialog) {
			state.outside = event => { if (this.columnPopup === state && !layer.contains?.(event.target) && !trigger?.contains?.(event.target)) this.closeColumnPopup(true); };
			this.host.addEventListener("pointerdown", state.outside);
		}
		this.columnPopup = state; trigger?.setAttribute("aria-expanded", "true"); this.host.append(layer); return state;
	}
	openColumnMenu(field, trigger = this.columnHeaders.get(field)?.button) {
		if (this.disposed || !this.visible || this.loading || !this.columnHeaders.has(field)) return;
		const state = this.makeColumnPopup(field, trigger), filtered = this.columnFilters.find(condition => condition.field === field);
		const action = (label, callback) => { const button = this.button(label, callback); button.setAttribute("role", "menuitem"); state.controls.push(button); state.layer.append(button); return button; };
		action("升序排序", () => this.setColumnSort(field, "asc")); action("降序排序", () => this.setColumnSort(field, "desc"));
		const clear = action("清空筛选", () => this.applyColumnCondition(field, null)); clear.disabled = !filtered;
		const text = action("文本筛选" + (filtered && !["in", "not_in"].includes(filtered.operator) ? " ✓" : ""), () => {
			if (state.submenu) return;
			state.submenu = this.element("div", undefined, "jfe-inventory-tracking-column-submenu"); state.layer.append(state.submenu);
			for (const [operator, label] of [...this.columnOperators().filter(([operator]) => !["in", "not_in", "not_starts", "not_ends"].includes(operator)), [filtered?.operator || "contains", "自定义筛选"]]) {
				const button = this.button(label, () => this.openTextFilter(field, operator, trigger)); button.setAttribute("role", "menuitem"); state.controls.push(button); state.submenu.append(button);
			}
			state.submenu.children[0]?.focus?.();
		}); text.setAttribute("aria-haspopup", "menu");
		action("设置筛选", () => this.openValueFilter(field, trigger)); state.controls[0].focus?.();
	}
	filterDialog(field, trigger, title) {
		const state = this.makeColumnPopup(field, trigger, true), header = this.element("header");
		header.append(this.element("h2", __(title)), this.element("span", __("显示行：") + __(this.columnHeaders.get(field).label))); state.layer.append(header);
		state.error = this.element("p", undefined, "jfe-inventory-tracking-column-error"); state.error.setAttribute("role", "status");
		state.footer = this.element("footer"); state.cancel = this.button("取消", () => this.closeColumnPopup(true));
		return state;
	}
	openTextFilter(field, operator = "contains", trigger = this.columnHeaders.get(field)?.button) {
		if (this.disposed || !this.visible || this.loading || !this.columnHeaders.has(field)) return;
		const previous = this.columnFilters.find(condition => condition.field === field), state = this.filterDialog(field, trigger, "自定义筛选");
		state.operator = this.element("select"); state.operator.setAttribute("aria-label", __("筛选运算符"));
		for (const [value, label] of this.columnOperators()) { const option = this.element("option", __(label)); option.value = value; state.operator.append(option); }
		state.operator.value = operator; state.input = this.element("textarea"); state.input.maxLength = 2000; state.input.setAttribute("aria-label", __("筛选值"));
		state.input.value = previous ? Array.isArray(previous.value) ? previous.value.filter(value => value !== null).join("\n") : previous.value : "";
		const hint = this.element("p", __("在里面/不在里面：每行输入一个完整值。空值请使用设置筛选。"));
		state.confirm = this.button("确定", () => {
			const value = ["in", "not_in"].includes(state.operator.value) ? state.input.value.split(/\r?\n/) : state.input.value;
			const condition = { field, operator: state.operator.value, value };
			if (!this.validColumnConditions([condition], null)) { state.error.textContent = __("请检查筛选值；每列最多 200 个值，每个值最多 2000 字，值不能重复。"); return; }
			this.applyColumnCondition(field, condition);
		}, "primary");
		state.layer.append(state.operator, state.input, hint); state.footer.append(state.confirm, state.cancel); state.layer.append(state.error, state.footer);
		state.controls = [state.operator, state.input, state.confirm, state.cancel]; state.input.focus?.(); return state;
	}
	openValueFilter(field, trigger = this.columnHeaders.get(field)?.button) {
		if (this.disposed || !this.visible || this.loading || !this.columnHeaders.has(field)) return;
		const previous = this.columnFilters.find(condition => condition.field === field), state = this.filterDialog(field, trigger, "筛选");
		state.allSelected = previous?.operator !== "in"; state.selected = new Set(["in", "not_in"].includes(previous?.operator) ? previous.value.map(value => JSON.stringify(value)) : []);
		const label = this.element("label"); state.all = this.element("input"); state.all.type = "checkbox"; state.all.checked = state.allSelected;
		state.all.addEventListener("change", () => { state.allSelected = state.all.checked; state.selected.clear(); this.renderValueChecks(state); });
		label.append(state.all, this.element("span", __("全选"))); state.list = this.element("div", undefined, "jfe-inventory-tracking-value-list");
		state.pager = this.element("div", undefined, "jfe-inventory-tracking-pager");
		state.previous = this.button("上页", () => this.queryValueOptions(state, state.page - 1)); state.next = this.button("下页", () => this.queryValueOptions(state, state.page + 1)); state.count = this.element("span");
		state.pager.append(state.previous, state.count, state.next);
		state.confirm = this.button("确定", () => {
			if (!this.columnPopupCurrent(state) || state.loading || !state.total) return;
			const values = [...state.selected].map(value => JSON.parse(value));
			this.applyColumnCondition(field, state.allSelected && !values.length ? null : { field, operator: state.allSelected ? "not_in" : "in", value: values });
		}, "primary");
		state.layer.append(label, this.element("p", __("选项来自完整查询范围，其他列条件继续生效。选择跨选项页保留；全选作用于所有选项。")), state.list, state.pager);
		state.footer.append(state.confirm, state.cancel); state.layer.append(state.error, state.footer); state.controls = [state.all, state.previous, state.next, state.confirm, state.cancel];
		state.confirm.disabled = true; state.all.disabled = true; state.previous.disabled = true; state.next.disabled = true;
		state.cancel.focus?.();
		if (!state.scope) { state.error.textContent = __("请选择有效的公司和截止日期后再读取选项。"); state.ready = Promise.resolve(); }
		else state.ready = this.queryValueOptions(state, 1);
		return state;
	}
	validValueOptions(result, state, page) {
		if (!this.keys(result, ["kind", "company", "search", "warehouse", "as_of", "currency", "filter_protocol", "column_filters", "sort", "field", "full_scope", "source_total", "matched_total", "page", "page_size", "total", "total_pages", "values", "readonly", "can_write"]) ||
			!this.sameColumnContext(result, state.scope) || result.filter_protocol !== 1 || result.field !== state.field || result.full_scope !== true || result.readonly !== true || result.can_write !== false || !this.validName(result.currency) ||
			![result.source_total, result.matched_total, result.total, result.total_pages].every(value => Number.isSafeInteger(value) && value >= 0) || result.matched_total > result.source_total || result.total > result.matched_total ||
			result.page !== page || result.page_size !== 100 || page > Math.max(1, result.total_pages) || !Array.isArray(result.values)) return false;
		const total = BigInt(result.total), offset = (BigInt(page) - 1n) * 100n;
		return offset <= BigInt(Number.MAX_SAFE_INTEGER) && result.total_pages === Number(total === 0n ? 0n : (total - 1n) / 100n + 1n) &&
			result.values.length === Math.min(100, Math.max(0, result.total - Number(offset))) &&
			result.values.every(value => value === null || this.validColumnValue(value)) && new Set(result.values.map(value => JSON.stringify(value))).size === result.values.length;
	}
	async queryValueOptions(state, requestedPage) {
		const page = this.pageNumber(requestedPage); if (!page || !this.columnPopupCurrent(state)) return;
		state.request?.abort?.(); const sequence = ++state.requestRevision; state.loading = true; state.values = []; state.total = null; state.totalPages = 0; state.count.textContent = ""; state.controls = [state.cancel];
		state.list.replaceChildren(); state.confirm.disabled = true; state.all.disabled = true; state.previous.disabled = true; state.next.disabled = true; state.error.textContent = __("正在读取筛选选项…");
		const args = { ...state.scope, field: state.field, page, page_size: 100, column_filters: JSON.stringify(state.scope.column_filters), sort: JSON.stringify(state.scope.sort) };
		if (!args.warehouse) delete args.warehouse;
		try {
			state.request = frappe.call({ method: "jifeng_erp.inventory_tracking.query_tracking_values", type: "POST", args }); const response = await state.request;
			if (!this.columnPopupCurrent(state) || sequence !== state.requestRevision) return;
			if (!this.validValueOptions(response?.message, state, page)) throw new Error("Invalid tracking values");
			state.values = response.message.values; state.page = page; state.total = response.message.total; state.totalPages = response.message.total_pages;
			state.error.textContent = state.total ? "" : __("没有可选择的值。"); state.count.textContent = __("第") + " " + page + " " + __("页") + " · " + __("共") + " " + state.total + " " + __("个值");
			state.previous.disabled = page <= 1; state.next.disabled = page >= state.totalPages; state.confirm.disabled = !state.total; state.all.disabled = !state.total;
			this.renderValueChecks(state);
		} catch {
			if (this.columnPopupCurrent(state) && sequence === state.requestRevision) {
				this.clearResults(); state.list.replaceChildren(); state.error.textContent = __("选项未能完整读取，请取消并核对来源权限或使用文本筛选。");
			}
		} finally { if (this.columnPopupCurrent(state) && sequence === state.requestRevision) { state.request = null; state.loading = false; } }
	}
	renderValueChecks(state) {
		state.list.replaceChildren(); const controls = [];
		for (const value of state.values || []) {
			const label = this.element("label"), checkbox = this.element("input"), key = JSON.stringify(value); checkbox.type = "checkbox";
			checkbox.checked = state.allSelected ? !state.selected.has(key) : state.selected.has(key);
			checkbox.addEventListener("change", () => {
				const chosen = state.allSelected ? !checkbox.checked : checkbox.checked;
				if (chosen && !state.selected.has(key) && state.selected.size >= 200) { checkbox.checked = !checkbox.checked; state.error.textContent = __("每列最多选择或排除 200 个值。"); return; }
				if (chosen) state.selected.add(key); else state.selected.delete(key); state.error.textContent = "";
				state.all.checked = state.allSelected && state.selected.size === 0; state.all.indeterminate = state.selected.size > 0;
			});
			label.append(checkbox, this.element("span", value === null ? __("（空值）") : value === "" ? __("（空字符串）") : value)); state.list.append(label); controls.push(checkbox);
		}
		state.all.checked = state.allSelected && state.selected.size === 0; state.all.indeterminate = state.selected.size > 0;
		state.controls = [state.all, ...controls, state.previous, state.next, state.confirm, state.cancel];
	}
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
				for (const [field] of this.fields) { const td = this.element("td"); this.trackingCell(row, field, td); td.title = this.tooltip(row, field, basis); tr.append(td); }
				const source = this.element("td"); source.append(this.sourceButtons(row)); tr.append(source); this.tbody.append(tr);
			} else {
				const card = this.element("article"); card.append(this.element("h3", (row.profile.legacy_number ?? "—") + " · " + row.warehouse));
				const image = this.element("div", undefined, "jfe-inventory-tracking-image"); this.imageCell(row.image, image); for (const img of image.querySelectorAll?.("img") || []) img.loading = "lazy"; card.append(image);
				const fields = this.element("dl"); for (const [field, label] of [["warehouse", "仓库"], ["legacy_number", "编号"], ["stock_qty", "库存"]]) { fields.append(this.element("dt", __(label))); const value = this.element("dd"); this.trackingCell(row, field, value); value.title = this.tooltip(row, field, basis); fields.append(value); }
				card.append(fields, this.sourceButtons(row)); this.cards.append(card);
			}
		}
	}
	trackingCell(row, field, target) {
		const value = this.cellValue(row, field);
		if (["legacy_number", "stock_qty"].includes(field) && value !== null && value !== undefined && value !== "") {
			const mode = field === "legacy_number" ? "archive" : "records";
			const button = this.button(String(value), () => this.readTrackingDetail(row, mode, button), "jfe-inventory-tracking-link");
			button.setAttribute("aria-label", __(field === "legacy_number" ? "查看只读档案" : "查看库存跟踪记录") + "：" + String(value)); target.append(button);
		} else target.textContent = String(value ?? "—");
	}
	sameData(left, right) {
		if (left === right) return true;
		if (!left || !right || typeof left !== "object" || typeof right !== "object" || Array.isArray(left) !== Array.isArray(right)) return false;
		const keys = Object.keys(left);
		return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && this.sameData(left[key], right[key]));
	}
	detailArgs(row) {
		return { ...this.resultScope, warehouse: row.warehouse, profile_name: row.profile_name, item_code: row.item_code,
			profile_version: row.profile_version, item_version: row.item_version, warehouse_version: row.warehouse_version };
	}
	detailCurrent(state) {
		return !this.disposed && this.visible && this.detail === state && !state.closed && state.sequence === this.detailRevision &&
			state.listRevision === this.revision && this.sameContext(state.parentScope, this.context()) && this.rows.includes(state.row) &&
			this.sameData(state.row, state.snapshot);
	}
	validDetailScope(result, state, keys) {
		return this.keys(result, keys) && result.readonly === true && result.can_write === false &&
			this.sameContext(result, state.args) && this.validName(result.currency) && result.currency === state.currency && this.validRow(result.row, state.args) &&
			this.sameData(result.row, state.snapshot);
	}
	validTrackingArchive(result, state) {
		if (!this.validDetailScope(result, state, ["kind", "company", "search", "warehouse", "as_of", "currency", "row", "archive", "balances", "readonly", "can_write"]) ||
			!this.keys(result.archive, ["name", "kind", "company", "item_code", "profile_version", "item_version", "profile", "item", "field_meta", "image", "readonly", "can_write"]) ||
			!this.validArchive(result.archive, state.args.company, state.args.profile_name) ||
			!["item_code", "profile_version", "item_version"].every(key => result.archive[key] === state.args[key]) ||
			!this.sameData(result.archive.image, result.row.image) || !this.keys(result.archive.field_meta, ["profile", "item"]) || !Array.isArray(result.balances)) return false;
		return result.balances.every(row => this.validRow(row, { ...state.args, warehouse: "" }) &&
			["profile_name", "profile_version", "item_code", "item_version", "stock_uom"].every(key => row[key] === result.row[key]) &&
			this.sameData(row.profile, result.row.profile) && this.sameData(row.image, result.row.image)) &&
			new Set(result.balances.map(row => row.warehouse)).size === result.balances.length &&
			result.balances.filter(row => this.sameData(row, result.row)).length === 1;
	}
	validTrackingRecords(result, state, page) {
		if (!this.validDetailScope(result, state, ["kind", "company", "search", "warehouse", "as_of", "currency", "row", "page", "page_size", "total", "total_pages", "rows", "readonly", "can_write"]) ||
			result.page !== page || result.page_size !== 100 || !Number.isSafeInteger(result.total) || result.total < 0 ||
			!Number.isSafeInteger(result.total_pages) || !Array.isArray(result.rows) || page > Math.max(1, result.total_pages)) return false;
		const total = BigInt(result.total), size = 100n, offset = (BigInt(page) - 1n) * size;
		if (offset > BigInt(Number.MAX_SAFE_INTEGER) || result.total_pages !== Number(total === 0n ? 0n : (total - 1n) / size + 1n) ||
			result.rows.length !== Math.min(100, Math.max(0, result.total - Number(offset)))) return false;
		return result.rows.every(row => this.keys(row, ["name", "modified", "posting_datetime", "voucher_type", "voucher_no", "actual_qty", "qty_after_transaction", "valuation_rate", "stock_value", "stock_value_difference", "stock_uom", "is_adjustment_entry", "company"]) &&
			["name", "modified", "posting_datetime", "voucher_type", "voucher_no", "stock_uom", "company"].every(key => this.validName(row[key])) &&
			["actual_qty", "qty_after_transaction", "valuation_rate", "stock_value", "stock_value_difference"].every(key => this.decimal(row[key])) &&
			row.company === state.args.company && row.stock_uom === state.row.stock_uom && [0, 1].includes(row.is_adjustment_entry)) &&
			new Set(result.rows.map(row => row.name)).size === result.rows.length;
	}
	async readTrackingDetail(row, mode, trigger) {
		if (this.disposed || !this.visible || this.loading || !["archive", "records"].includes(mode) || !this.rows.includes(row) ||
			!this.sameContext(this.resultScope, this.context())) return;
		this.closeColumnPopup(false);
		this.closeDetail(false);
		const layer = this.element("section", undefined, "jfe-inventory-tracking-detail " + (mode === "archive" ? "archive" : "records"));
		const title = this.kind + (mode === "archive" ? "档案查询" : "跟踪记录");
		layer.setAttribute("role", "dialog"); layer.setAttribute("aria-label", __(title)); layer.setAttribute("aria-modal", String(mode === "archive"));
		const header = this.element("header"), heading = this.element("h2", __(title));
		const body = this.element("div", undefined, "jfe-inventory-tracking-detail-body"); body.setAttribute("aria-live", "polite");
		const close = this.button("关闭", () => this.closeDetail(true)); close.setAttribute("aria-label", __("关闭") + __(title));
		header.append(heading, close); layer.append(header, body);
		const state = { layer, body, close, trigger, mode, row, snapshot: JSON.parse(JSON.stringify(row)), args: this.detailArgs(row),
			parentScope: { ...this.resultScope }, currency: this.resultCurrency, listRevision: this.revision, sequence: this.detailRevision, request: null, requestRevision: 0, closed: false,
			background: mode === "archive" ? Array.from(this.host.children, node => [node, node.inert]) : [] };
		this.detail = state;
		for (const [node] of state.background) node.inert = true;
		layer.addEventListener("keydown", event => {
			if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); this.closeDetail(true); }
			if (event.key === "Tab" && mode === "archive") { event.preventDefault(); close.focus(); }
		});
		this.host.append(layer); this.host.classList.add("has-tracking-detail"); close.focus?.();
		await this.queryTrackingDetail(state, 1);
	}
	async queryTrackingDetail(state, requestedPage) {
		const page = this.pageNumber(requestedPage);
		if (!page || !this.detailCurrent(state)) return;
		state.request?.abort?.(); const requestRevision = ++state.requestRevision;
		state.body.replaceChildren(this.element("p", __("正在读取…"))); state.loading = true;
		const args = { ...state.args, ...(state.mode === "records" ? { page, page_size: 100 } : {}) };
		try {
			state.request = frappe.call({ method: "jifeng_erp.inventory_tracking." + (state.mode === "archive" ? "read_tracking_archive" : "query_tracking_records"), type: "GET", args });
			const response = await state.request;
			if (!this.detailCurrent(state) || requestRevision !== state.requestRevision) return;
			if (!(state.mode === "archive" ? this.validTrackingArchive(response?.message, state) : this.validTrackingRecords(response?.message, state, page))) throw new Error("Invalid tracking detail");
			if (state.mode === "archive") this.renderTrackingArchive(state, response.message); else this.renderTrackingRecords(state, response.message);
		} catch {
			if (this.detailCurrent(state) && requestRevision === state.requestRevision) state.body.replaceChildren(this.element("p", __("详情未能读取，请关闭后重新查询并核对完整来源权限。")));
		} finally {
			if (this.detailCurrent(state) && requestRevision === state.requestRevision) { state.request = null; state.loading = false; }
		}
	}
	appendProfile(target, archive) {
		const fields = this.element("dl", undefined, "jfe-inventory-tracking-detail-fields");
		for (const [field, value] of Object.entries(archive.profile)) {
			const label = archive.field_meta.profile[field].label;
			fields.append(this.element("dt", __(typeof label === "string" && label ? label : field)), this.element("dd", value ?? "—"));
		}
		target.append(fields);
		const image = this.element("div", undefined, "jfe-inventory-tracking-image"); this.imageCell(archive.image, image); target.append(image);
	}
	detailTable(columns, rows) {
		const scroll = this.element("div", undefined, "jfe-inventory-tracking-detail-scroll"), table = this.element("table");
		const head = this.element("thead"), titles = this.element("tr"), body = this.element("tbody");
		for (const [, label] of columns) titles.append(this.element("th", __(label))); head.append(titles);
		for (const row of rows) { const tr = this.element("tr"); for (const [field] of columns) tr.append(this.element("td", field === "voucher_type" ? __(row[field]) : row[field] ?? "—")); body.append(tr); }
		table.append(head, body); scroll.append(table); return scroll;
	}
	renderTrackingArchive(state, result) {
		state.body.replaceChildren(this.element("p", result.company + " · " + result.as_of + " · " + result.currency));
		this.appendProfile(state.body, result.archive);
		state.body.append(this.element("h3", __("截止日库存")), this.detailTable([["warehouse", "仓库"], ["stock_qty", "库存"], ["valuation_rate", "单价"], ["stock_value", "金额"], ["stock_uom", "库存单位"]], result.balances));
	}
	renderTrackingRecords(state, result) {
		state.body.replaceChildren(this.element("p", __("按截止日展示原生库存流水；数量变动与变动后余额分别展示。旧去向、退回等列规则待确认。")));
		const layout = this.element("div", undefined, "jfe-inventory-tracking-record-layout"), ledger = this.element("div"), summary = this.element("aside");
		ledger.append(this.detailTable([["voucher_no", "单号"], ["voucher_type", "来源类型"], ["posting_datetime", "时间"], ["valuation_rate", "单价"],
			["actual_qty", "原生数量变动"], ["qty_after_transaction", "本次余数"], ["stock_value", "库存价值"], ["stock_value_difference", "价值变动"], ["stock_uom", "库存单位"], ["is_adjustment_entry", "仅价值调整"]], result.rows));
		const pager = this.element("div", undefined, "jfe-inventory-tracking-pager");
		const previous = this.button("上页", () => this.queryTrackingDetail(state, result.page - 1)), next = this.button("下页", () => this.queryTrackingDetail(state, result.page + 1));
		previous.disabled = result.page <= 1; next.disabled = result.page >= result.total_pages;
		pager.append(previous, this.element("span", __("第") + " " + result.page + " " + __("页") + " · " + __("共") + " " + result.total + " " + __("条")), next); ledger.append(pager);
		const fields = this.element("dl", undefined, "jfe-inventory-tracking-detail-fields");
		fields.append(this.element("dt", __("仓库")), this.element("dd", result.row.warehouse));
		for (const [field, label] of this.fields.filter(([field]) => Object.hasOwn(result.row.profile, field))) fields.append(this.element("dt", __(label)), this.element("dd", result.row.profile[field] ?? "—"));
		const image = this.element("div", undefined, "jfe-inventory-tracking-image"); this.imageCell(result.row.image, image); summary.append(fields, image); layout.append(ledger, summary); state.body.append(layout);
	}
	closeDetail(restoreFocus = true) {
		this.detailRevision++; const state = this.detail; this.detail = null;
		if (!state) return;
		state.closed = true; state.requestRevision++; state.request?.abort?.(); state.request = null;
		state.body.replaceChildren(); state.layer.remove(); for (const [node, inert] of state.background) node.inert = inert;
		this.host.classList.remove("has-tracking-detail");
		if (restoreFocus && this.visible && !this.disposed) state.trigger?.focus?.();
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
		this.host.removeEventListener("keydown", this.detailKeydown);
		this.host.replaceChildren(); this.host.classList.remove("jfe-inventory-tracking");
	}
};
