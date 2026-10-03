/* Saved Salary Slip history shared by both workspace presentations. */
window.JifengSalaryHistory = class JifengSalaryHistory {
	constructor(host, bridge) {
		this.host = host; this.bridge = bridge; this.disposed = false;
		this.revision = 0; this.detailRevision = 0; this.loading = false;
		this.exporting = false; this.exportController = null;
		this.page = 1; this.pageSize = 15; this.total = 0; this.rows = [];
		this.columns = this.columnDefinitions(); this.visibleColumns = this.columns.filter(column => !column.optional);
		this.build(); this.ready = this.initialize();
	}
	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className; return node;
	}
	button(label, action, reason) {
		const node = this.element("button", __(label), "btn btn-default btn-xs"); node.type = "button";
		if (reason) { node.disabled = true; node.title = __(reason); }
		else node.addEventListener("click", action); return node;
	}
	columnDefinitions() {
		const unknown = __("尚无已核实的旧字段来源");
		return [
			["说明", "description", unknown], ["姓名", "employee_name"], ["部门", "department"],
			["底薪", null, __("工资项目默认额不等于旧底薪；可在详情查看未折算默认额")],
			["应出勤", null, __("工资单计薪工作天数的口径尚未与旧应出勤确认")],
			["实出勤", "actual_attendance", __("计薪天数包括带薪假等，不作为实际出勤")],
			["出勤工资", "bucket:basic", __("明确分类为基本工资的已保存金额")],
			["计时", "bucket:time"], ["计件", "bucket:piece"], ["合计", "core_total", unknown],
			["房补", "bucket:housing"], ["全勤", "bucket:full_attendance"],
			["应发", "gross_pay", __("工资单已保存的总收入，未扣扣款")],
			["水电", "bucket:utilities"], ["社保", "bucket:social_insurance"], ["罚款", "bucket:fine"],
			["借款", "bucket:loan", __("明确分类的工资扣款项目，不推算借款台账")],
			["实发", "actual_paid", __("尚无已核实的付款依据；净应发不代表已付款")],
			["开始", "start_date"], ["结束", "end_date"], ["类型", "type", unknown],
			["伙食", "bucket:meal", null, true], ["交通", "bucket:transport", null, true],
			["夜宵", "bucket:night_snack", null, true], ["加班", "bucket:overtime", null, true],
			["介绍费", "bucket:introduction", null, true], ["考核", "bucket:assessment", null, true],
			["其他收入", "bucket:other_earning", null, true], ["其他扣款", "bucket:other_deduction", null, true],
			["留存工资", "bucket:retained_pay", null, true], ["住宿", "bucket:accommodation", null, true],
		].map(([label, field, title, optional]) => ({ label, field, title, optional: !!optional }));
	}
	build() {
		this.host.classList.add("jfe-salary-history"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-salary-history-toolbar");
		const company = this.element("div", undefined, "jfe-salary-history-company");
		this.companyControl = frappe.ui.form.make_control({ parent: company, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"),
				onchange: () => this.invalidate() } });
		this.companyControl.$input?.on?.("input.jifeng-salary-history change.jifeng-salary-history", () => this.invalidate());
		this.from = this.element("input"); this.from.type = "date"; this.from.value = frappe.datetime.month_start();
		this.from.setAttribute("aria-label", __("开始日期"));
		this.to = this.element("input"); this.to.type = "date"; this.to.value = frappe.datetime.get_today();
		this.to.setAttribute("aria-label", __("结束日期"));
		this.useDate = this.element("input"); this.useDate.type = "checkbox"; this.useDate.checked = true;
		const useDateLabel = this.element("label"); useDateLabel.append(this.useDate, this.element("span", __("指定日期")));
		this.dateBasis = this.element("select"); this.dateBasis.setAttribute("aria-label", __("日期口径"));
		for (const [value, label] of [["period_start", "工资期开始"], ["period_end", "工资期结束"],
			["period_overlap", "工资期与日期范围相交"], ["posting_date", "记账日"]]) {
			const option = this.element("option", __(label)); option.value = value; this.dateBasis.append(option);
		}
		this.dateBasis.value = "period_end";
		this.search = this.element("input"); this.search.type = "search"; this.search.maxLength = 140;
		this.search.placeholder = __("输入查询的关键字"); this.search.setAttribute("aria-label", __("查询关键字"));
		for (const control of [this.from, this.to, this.useDate, this.dateBasis, this.search]) {
			control.addEventListener("input", () => this.invalidate()); control.addEventListener("change", () => this.invalidate());
		}
		this.search.addEventListener("keydown", event => { if (event.key === "Enter") this.query(1); });
		this.queryButton = this.button("查询", () => this.query(1));
		this.exportButton = this.button("导出", () => this.exportHistory());
		this.exportButton.title = __("导出当前筛选的全部已保存可读工资单，列顺序使用当前列设置；需要原生导出权限。未核实金额留空，实发需付款依据。");
		toolbar.append(company, this.from, this.to, useDateLabel, this.dateBasis, this.search,
			this.exportButton, this.queryButton,
			this.button("格式1", null, "旧打印格式尚未实现"), this.button("打印", null, "旧工资历史打印尚未实现"),
			this.button("列设置", () => this.configureColumns()), this.button("分类设置", () => this.openRules()));
		const note = this.element("p", __("显示已提交工资单。未核实的旧字段留空；应发取总收入，实发需付款依据。双击行查看来源。"), "jfe-salary-history-note");
		this.status = this.element("p", __("请选择公司并查询。")); this.status.setAttribute("role", "status");
		const scroller = this.element("div", undefined, "jfe-salary-history-scroll");
		this.table = this.element("table"); this.table.setAttribute("aria-label", __("工资历史"));
		this.thead = this.element("thead"); this.tbody = this.element("tbody"); this.table.append(this.thead, this.tbody); scroller.append(this.table);
		this.previous = this.button("上页", () => this.query(this.page - 1)); this.next = this.button("下页", () => this.query(this.page + 1));
		this.count = this.element("span", "—"); this.pageSizeControl = this.element("select"); this.pageSizeControl.setAttribute("aria-label", __("每页数量"));
		for (const size of [15, 25, 50, 100]) { const option = this.element("option", size + __("条/页")); option.value = String(size); this.pageSizeControl.append(option); }
		this.pageSizeControl.value = "15";
		this.pageSizeControl.addEventListener("change", () => { this.pageSize = Number(this.pageSizeControl.value); this.invalidate(); });
		const pager = this.element("div", undefined, "jfe-salary-history-pager"); pager.append(this.previous, this.count, this.next, this.pageSizeControl);
		this.host.append(toolbar, note, this.status, scroller, pager); this.renderRows(); this.updatePager();
	}
	async initialize() {
		const company = frappe.defaults?.get_user_default?.("Company");
		if (company) await this.companyControl.set_value(company);
		return !this.disposed;
	}
	context() {
		const company = this.companyControl.get_value() || "";
		const typed = this.companyControl.$input?.val?.();
		if (typeof company !== "string" || !company.trim() || company !== company.trim() ||
			(typeof typed === "string" && typed.trim() !== company)) return null;
		return { company, from_date: this.from.value, to_date: this.to.value, use_date: !!this.useDate.checked,
			date_basis: this.dateBasis.value, search: this.search.value.trim() };
	}
	validContext(context) {
		return !!context && [context.from_date, context.to_date].every(value => /^\d{4}-\d{2}-\d{2}$/.test(value)) &&
			context.from_date <= context.to_date && ["period_start", "period_end", "period_overlap", "posting_date"].includes(context.date_basis) &&
			context.search.length <= 140 && !/[\x00-\x1f]/.test(context.search);
	}
	sameContext(left, right) {
		const keys = ["company", "from_date", "to_date", "use_date", "date_basis", "search"];
		return !!left && !!right && Object.keys(right).length === keys.length && keys.every(key => left[key] === right[key]);
	}
	invalidate() {
		if (this.disposed) return;
		this.cancelExport();
		this.revision++; this.detailRevision++; this.loading = false; this.rows = []; this.total = 0; this.page = 1; this.resultContext = null;
		this.closeDetail(); this.renderRows(); this.updatePager(); this.status.textContent = __("筛选已变化，请查询。 ");
	}
	updatePager() {
		const pages = Math.ceil(this.total / this.pageSize);
		this.count.textContent = __("第") + (this.total ? this.page : 0) + "/" + pages + __("页 · ") + this.total + __("条");
		this.previous.disabled = this.disposed || this.loading || !this.resultContext || this.page <= 1;
		this.next.disabled = this.disposed || this.loading || !this.resultContext || this.page >= pages;
		this.queryButton.disabled = this.disposed || this.loading;
		this.exportButton.disabled = this.disposed || this.loading || this.exporting || !this.validContext(this.context());
	}
	cancelExport() {
		this.exportController?.abort(); this.exportController = null; this.exporting = false;
	}
	async exportHistory() {
		if (this.disposed || this.loading || this.exporting) return;
		const context = this.context(), columns = this.visibleColumns.map(column => column.label);
		if (!this.validContext(context) || !columns.length) {
			frappe.msgprint(__("请核对公司、日期范围、查询条件和导出列。")); return;
		}
		const revision = this.revision, controller = new AbortController();
		const current = () => !this.disposed && !controller.signal.aborted && this.exportController === controller &&
			revision === this.revision && this.sameContext(context, this.context()) &&
			JSON.stringify(columns) === JSON.stringify(this.visibleColumns.map(column => column.label));
		this.exportController = controller; this.exporting = true; this.updatePager();
		this.status.textContent = __("正在导出当前筛选的全部已保存工资单…");
		let url;
		try {
			const contentType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
			const args = { context: JSON.stringify(context), columns: JSON.stringify(columns) };
			const response = await fetch("/api/method/jifeng_erp.salary_history_export.export_salary_history?" + new URLSearchParams(args),
				{ method: "GET", credentials: "same-origin", headers: { Accept: contentType }, signal: controller.signal });
			if (!current()) return;
			if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== contentType) throw new Error("Salary history export failed");
			const file = await response.blob();
			if (!current()) return;
			if (!file.size) throw new Error("Empty salary history export");
			url = URL.createObjectURL(file); const link = this.element("a");
			link.href = url; link.download = "工资历史.xlsx"; document.body.append(link); link.click(); link.remove();
			this.status.textContent = __("已导出当前筛选的全部已保存工资单；未知字段为空，应发为总收入，实发未核实。");
		} catch {
			if (current()) {
				this.rows = []; this.total = 0; this.resultContext = null; this.renderRows();
				this.status.textContent = __("导出未完成，请核对筛选、来源及导出权限后重试。");
			}
		} finally {
			if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
			if (this.exportController === controller) { this.exportController = null; this.exporting = false; this.updatePager(); }
		}
	}
	call(method, args) { return frappe.call({ method: "jifeng_erp.salary_history." + method, type: "GET", args }); }
	validRow(row) {
		if (!row || !["name", "version", "company", "employee", "employee_name", "department", "currency", "start_date", "end_date", "posting_date"].every(key => typeof row[key] === "string") ||
			!row.name || !row.version || !row.employee || !row.currency ||
			!["gross_pay", "total_deduction", "net_pay", "rounded_total", "total_working_days", "payment_days"].every(key => this.validMoney(row[key])) ||
			!["description", "type", "actual_attendance", "core_total", "actual_paid"].every(key => row[key] === null) ||
			!row.buckets || !row.classification_state || !Array.isArray(row.unresolved) || !row.unresolved.every(value => typeof value === "string") ||
			!(row.basic_default_amount === null || this.validMoney(row.basic_default_amount))) return false;
		const earnings = ["basic", "time", "piece", "housing", "full_attendance", "meal", "transport", "night_snack", "overtime", "introduction", "assessment", "other_earning"];
		const deductions = ["utilities", "social_insurance", "fine", "loan", "retained_pay", "accommodation", "other_deduction"];
		return [["earnings", earnings], ["deductions", deductions]].every(([side, keys]) =>
			["verified", "unclassified", "unreconciled"].includes(row.classification_state[side]) && keys.every(key =>
				row.classification_state[side] === "verified" ? this.validMoney(row.buckets[key]) : row.buckets[key] === null));
	}
	validMoney(value) { return typeof value === "string" && value.length <= 620 && /^-?\d+(?:\.\d+)?$/.test(value); }
	current(revision, context, pageSize) {
		return !this.disposed && revision === this.revision && pageSize === this.pageSize && this.sameContext(context, this.context());
	}
	async query(page = 1) {
		if (this.disposed || this.loading) return;
		const context = this.context(), size = this.pageSize;
		if (!this.validContext(context) || !Number.isSafeInteger(page) || page < 1 || ![15, 25, 50, 100].includes(size)) {
			frappe.msgprint(__("请核对公司、日期范围和查询条件。")); return;
		}
		this.cancelExport();
		const revision = ++this.revision; this.loading = true; this.detailRevision++; this.closeDetail(); this.updatePager();
		try {
			const response = await this.call("query_salary_history", { context, page, page_size: size }), result = response.message;
			if (!this.current(revision, context, size)) return;
			if (result?.readonly !== true || result.scope !== "salary_history" || !this.sameContext(context, result.context) ||
				result.page !== page || result.page_size !== size || !Number.isSafeInteger(result.total) || result.total < 0 ||
				result.total_pages !== Math.ceil(result.total / size) || !Array.isArray(result.rows) ||
				result.rows.length !== Math.min(size, Math.max(0, result.total - (page - 1) * size)) ||
				new Set(result.rows.map(row => row?.name)).size !== result.rows.length || result.rows.some(row => !this.validRow(row) || row.company !== context.company)) throw new Error("Invalid saved salary history");
			this.rows = result.rows; this.total = result.total; this.page = page; this.resultContext = context;
			this.renderRows(); this.status.textContent = this.total ? __("已读取已提交工资单；— 表示尚无准确来源，详情保留原生金额。") : __("当前条件没有已提交工资单。 ");
		} catch {
			if (this.current(revision, context, size)) {
				this.rows = []; this.total = 0; this.resultContext = null; this.renderRows();
				this.status.textContent = __("来源或权限无法核实，请重新查询。 ");
			}
		} finally { if (this.current(revision, context, size)) { this.loading = false; this.updatePager(); } }
	}
	cellValue(row, column) {
		if (!column.field) return null;
		return column.field.startsWith("bucket:") ? row.buckets[column.field.slice(7)] : row[column.field];
	}
	renderRows() {
		this.thead.replaceChildren(); this.tbody.replaceChildren(); const heading = this.element("tr");
		for (const column of this.visibleColumns) { const cell = this.element("th", __(column.label)); if (column.title) cell.title = column.title; heading.append(cell); }
		this.thead.append(heading);
		for (const row of this.rows) {
			const tr = this.element("tr"); tr.tabIndex = 0; tr.title = __("双击查看工资单来源");
			tr.addEventListener("dblclick", () => this.openDetail(row));
			tr.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.openDetail(row); } });
			for (const column of this.visibleColumns) { const value = this.cellValue(row, column); const td = this.element("td", value === null ? "—" : value); if (column.title) td.title = column.title; tr.append(td); }
			this.tbody.append(tr);
		}
	}
	configureColumns() {
		if (this.disposed || this.columnDialog) return;
		const dialog = new frappe.ui.Dialog({ title: __("工资历史列设置"), fields: [{ fieldname: "columns", fieldtype: "HTML" }] });
		this.columnDialog = dialog; const host = dialog.fields_dict.columns.$wrapper.get(0), inputs = [];
		for (const column of this.columns) {
			const label = this.element("label", undefined, "jfe-salary-history-column"); const input = this.element("input"); input.type = "checkbox";
			input.checked = this.visibleColumns.includes(column); label.append(input, this.element("span", __(column.label))); host.append(label); inputs.push([column, input]);
		}
		dialog.set_primary_action(__("应用"), () => {
			if (this.disposed || this.columnDialog !== dialog) return;
			const selected = inputs.filter(([, input]) => input.checked).map(([column]) => column);
			if (!selected.length) { frappe.msgprint(__("请至少保留一列。")); return; }
			this.cancelExport(); this.visibleColumns = selected; this.renderRows(); this.updatePager(); dialog.hide();
		});
		dialog.on_hide = () => { if (this.columnDialog === dialog) this.columnDialog = null; }; dialog.show();
	}
	openRules() {
		if (this.disposed) return; const context = this.context();
		if (!this.validContext(context)) { frappe.msgprint(__("请先选择公司并核对查询条件。")); return; }
		this.bridge.open({ title: "工资历史分类", module: "工资", kind: "list", doctype: "Jifeng Salary History Rule",
			route: ["List", "Jifeng Salary History Rule"], filters: { company: context.company } });
	}
	validDetail(detail, row, context) {
		if (detail?.readonly !== true || detail.scope !== "salary_history" || !this.sameContext(context, detail.context) ||
			!this.validRow(detail.row) || detail.row.company !== context.company || detail.row.name !== row.name || detail.row.version !== row.version ||
			!Array.isArray(detail.earnings) || !Array.isArray(detail.deductions) || !Array.isArray(detail.sources)) return false;
		const reference = source => source && ["Additional Salary", "Jifeng Wage Entry"].includes(source.doctype) &&
			typeof source.name === "string" && !!source.name && typeof source.version === "string" && !!source.version;
		return detail.sources.every(reference) && [...detail.earnings, ...detail.deductions].every(child => child &&
			typeof child.name === "string" && typeof child.salary_component === "string" && this.validMoney(child.amount) && (child.default_amount === null || this.validMoney(child.default_amount)) &&
			Array.isArray(child.sources) && child.sources.every(reference) && (child.classification === null ||
				typeof child.classification?.rule === "string" && typeof child.classification.version === "string" && typeof child.classification.bucket === "string"));
	}
	async openDetail(row) {
		if (this.disposed || this.loading || this.detailDialog || !this.resultContext || !this.rows.includes(row) || !this.sameContext(this.resultContext, this.context())) return;
		const context = { ...this.resultContext }, revision = ++this.detailRevision, queryRevision = this.revision;
		try {
			const response = await this.call("get_salary_history_detail", { context, name: row.name, version: row.version });
			if (this.disposed || revision !== this.detailRevision || queryRevision !== this.revision || !this.rows.includes(row) || !this.sameContext(context, this.context())) return;
			if (!this.validDetail(response.message, row, context)) throw new Error("Invalid saved salary detail");
			this.showDetail(response.message);
		} catch { if (!this.disposed && revision === this.detailRevision && queryRevision === this.revision) frappe.msgprint(__("工资单来源或版本已变化，请重新查询。")); }
	}
	showDetail(detail) {
		const row = detail.row, dialog = new frappe.ui.Dialog({ title: __("工资历史详情"), size: "extra-large", fields: [{ fieldname: "detail", fieldtype: "HTML" }] });
		this.detailDialog = dialog; const host = dialog.fields_dict.detail.$wrapper.get(0);
		host.append(this.element("p", row.name + " · " + row.employee_name + " · " + row.currency));
		const totals = this.element("dl", undefined, "jfe-salary-history-totals");
		for (const [label, field] of [["总收入", "gross_pay"], ["总扣款", "total_deduction"], ["净应发（未证明付款）", "net_pay"],
			["舍入净应发", "rounded_total"], ["计薪工作天数", "total_working_days"], ["计薪天数", "payment_days"], ["基本工资未折算默认额", "basic_default_amount"]]) {
			totals.append(this.element("dt", __(label)), this.element("dd", row[field] ?? "—"));
		}
		host.append(totals);
		for (const reason of row.unresolved) host.append(this.element("p", reason));
		for (const [title, children] of [["收入项目", detail.earnings], ["扣款项目", detail.deductions]]) {
			host.append(this.element("h5", __(title))); const table = this.element("table", undefined, "jfe-salary-history-detail-table"), heading = this.element("tr");
			for (const label of ["工资项目", "已保存金额", "未折算默认额", "分类规则", "来源"]) heading.append(this.element("th", __(label))); table.append(heading);
			for (const child of children) {
				const tr = this.element("tr"); tr.append(this.element("td", child.salary_component), this.element("td", child.amount), this.element("td", child.default_amount ?? "—"),
					this.element("td", child.classification?.rule || "—"), this.element("td", child.sources.map(source => source.name).join("、") || "—")); table.append(tr);
			}
			host.append(table);
		}
		dialog.add_custom_action(__("查看原生工资单"), () => {
			if (this.disposed || this.detailDialog !== dialog || !this.sameContext(detail.context, this.context())) return;
			dialog.hide(); this.bridge.open({ title: "工资单 " + row.name, module: "工资", kind: "form", doctype: "Salary Slip", route: ["Form", "Salary Slip", row.name] });
		});
		dialog.on_hide = () => { if (this.detailDialog === dialog) this.detailDialog = null; }; dialog.show();
	}
	closeDetail() { this.detailDialog?.hide(); }
	isDirty() { return false; }
	getCloseBlock() { return ""; }
	destroy() {
		this.cancelExport(); this.disposed = true; this.revision++; this.detailRevision++; this.closeDetail(); this.columnDialog?.hide();
		this.companyControl.$input?.off?.(".jifeng-salary-history");
	}
};
