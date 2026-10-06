/* Current active temporary workers; shared by both workspace presentations. */
window.JifengTemporaryWorkers = class JifengTemporaryWorkers {
	constructor(host, bridge, mode = "browse") {
		if (!["archive", "browse", "wage", "details"].includes(mode)) throw new Error("Unknown temporary worker mode");
		this.host = host; this.bridge = bridge; this.mode = mode;
		this.disposed = false; this.revision = 0; this.loading = false; this.action = null;
		this.rows = []; this.resultContext = null; this.queryContext = null; this.rowButtons = [];
		this.build(); this.ready = this.initialize();
	}
	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className; return node;
	}
	button(label, action) {
		const node = this.element("button", __(label), "btn btn-default btn-sm"); node.type = "button";
		node.addEventListener("click", action); return node;
	}
	build() {
		this.host.classList.add("jfe-temporary-workers"); this.host.replaceChildren();
		this.host.style.padding = "18px";
		const toolbar = this.element("div"); Object.assign(toolbar.style, { display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "end" });
		const companyHost = this.element("div"); companyHost.style.minWidth = "240px";
		this.companyControl = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司"), reqd: 1,
				onchange: () => this.companyValidated() } });
		this.companyControl.$input?.on?.("input.jifeng-temporary-workers change.jifeng-temporary-workers", () => this.invalidate());
		this.search = this.element("input"); this.search.type = "search"; this.search.maxLength = 140;
		this.search.className = "form-control"; this.search.placeholder = __("姓名或员工编号"); this.search.setAttribute("aria-label", __("查询关键字"));
		for (const event of ["input", "change"]) this.search.addEventListener(event, () => this.invalidate());
		this.search.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.query(); } });
		this.queryButton = this.button("查询", () => this.query()); toolbar.append(companyHost, this.search, this.queryButton);
		if (this.mode === "archive") { this.newButton = this.button("新增临工档案", () => this.openNew()); toolbar.append(this.newButton); }
		const note = this.element("p", __("名单仅含当前在职、当前人员类型为临时的可读员工；不能代表该员工计薪当时的人员类型。非在职人员请到“人事 / 人事浏览”查询。"));
		const detailNote = this.element("p", __("按所选员工和公司打开过往计薪记录。"));
		this.status = this.element("p", __("请选择公司并查询当前在职临工。")); this.status.setAttribute("role", "status");
		const scroller = this.element("div"); scroller.style.overflow = "auto";
		const table = this.element("table", undefined, "table table-bordered"); table.setAttribute("aria-label", __("当前在职临工"));
		const heading = this.element("tr"); for (const label of ["员工", "员工编号", "姓名", "部门", "状态", "操作"]) heading.append(this.element("th", __(label)));
		const head = this.element("thead"); head.append(heading); this.body = this.element("tbody"); table.append(head, this.body); scroller.append(table);
		this.host.append(toolbar, note, detailNote, this.status, scroller); this.renderRows(); this.refreshActions();
	}
	async initialize() {
		const company = frappe.defaults?.get_user_default?.("Company");
		if (!this.disposed && typeof company === "string" && company) await this.companyControl.set_value(company);
		return !this.disposed;
	}
	validName(value) { return typeof value === "string" && !!value && value === value.trim() && [...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value); }
	context() {
		const company = this.companyControl.get_value(), typed = this.companyControl.$input?.val?.();
		if (!this.validName(company) || typeof typed === "string" && typed.trim() !== company) return null;
		const keyword = this.search.value.trim();
		return keyword.length <= 140 && !/[\x00-\x1f\x7f]/.test(keyword) ? { company, keyword } : null;
	}
	sameContext(left, right) { return !!left && !!right && left.company === right.company && left.keyword === right.keyword; }
	current(revision, context) { return !this.disposed && this.host.isConnected !== false && revision === this.revision && this.sameContext(context, this.context()); }
	validRow(row, company) {
		return !!row && this.validName(row.name) && row.company === company && row.status === "Active" && typeof row.employee_name === "string" &&
			["employee_version", "supplement_version"].every(field => typeof row[field] === "string" && /^[0-9a-f]{64}$/.test(row[field])) &&
			["employee_number", "department"].every(field => !Object.hasOwn(row, field) || row[field] === null || typeof row[field] === "string");
	}
	validSelection(result, context, row) {
		return result?.readonly === true && result.scope === "current_temporary_workers" && result.company === context.company &&
			result.employee === row.name && result.eligible === true && result.employee_version === row.employee_version &&
			result.supplement_version === row.supplement_version && result.classification_basis === "current_legacy_term_type" && result.historical_qualification_known === false;
	}
	companyValidated() {
		// Native Link validation can finish after a query using the same input.
		// Actual input/change events still invalidate immediately, including ABA.
		if (this.sameContext(this.context(), this.queryContext || this.resultContext)) return;
		this.invalidate();
	}
	invalidate() {
		if (this.disposed) return;
		this.revision++; this.loading = false; this.action = null; this.rows = []; this.resultContext = null; this.queryContext = null;
		this.renderRows(); this.refreshActions(); this.status.textContent = __("筛选已变化，请重新查询当前在职临工。");
	}
	refreshActions() {
		this.queryButton.disabled = this.disposed || this.loading;
		if (this.newButton) this.newButton.disabled = this.disposed || !!this.action;
		for (const button of this.rowButtons) button.disabled = this.disposed || this.loading || !!this.action;
	}
	renderRows() {
		this.body.replaceChildren(); this.rowButtons = [];
		for (const row of this.rows) {
			const tr = this.element("tr");
			for (const field of ["name", "employee_number", "employee_name", "department"]) tr.append(this.element("td", row[field] ?? "—"));
			tr.append(this.element("td", __("在职")));
			const cell = this.element("td"), label = { archive: "打开档案", browse: "查看档案", wage: "选择并进入工资录入", details: "查看计薪明细" }[this.mode];
			const button = this.button(label, () => this.select(row)); this.rowButtons.push(button); cell.append(button); tr.append(cell); this.body.append(tr);
		}
		if (!this.rows.length) {
			const tr = this.element("tr"), cell = this.element("td", __(this.resultContext ? "当前条件没有可读的在职临工。" : "尚无已核实名单，请查询。"));
			cell.colSpan = 6; tr.append(cell); this.body.append(tr);
		}
		this.refreshActions();
	}
	async query() {
		if (this.disposed || this.loading) return false;
		const context = this.context(); this.revision++; this.action = null; this.rows = []; this.resultContext = null; this.queryContext = null; this.renderRows();
		if (!context) { this.status.textContent = __("请核对公司和查询关键字。 "); return false; }
		const revision = this.revision; this.queryContext = context; this.loading = true; this.refreshActions(); this.status.textContent = __("正在查询当前在职临工…");
		try {
			const response = await frappe.call({ method: "jifeng_erp.temporary_workers.list_temporary_workers", type: "GET", args: context });
			if (!this.current(revision, context)) return false;
			const result = response.message;
			if (result?.readonly !== true || result.scope !== "current_temporary_workers" || result.company !== context.company ||
				result.classification_basis !== "current_legacy_term_type" || result.historical_qualification_known !== false || !Array.isArray(result.rows) ||
				result.rows.some(row => !this.validRow(row, context.company)) || new Set(result.rows.map(row => row.name)).size !== result.rows.length) throw new Error("Invalid temporary worker response");
			this.rows = result.rows; this.resultContext = context; this.renderRows();
			this.status.textContent = this.rows.length ? __("已读取当前在职临工。") : __("当前条件没有可读的在职临工。 "); return true;
		} catch {
			if (this.current(revision, context)) {
				this.rows = []; this.resultContext = null; this.renderRows(); this.status.textContent = __("查询失败：来源或权限无法核实，请重新查询。 ");
			}
			return false;
		} finally { if (this.current(revision, context)) { this.queryContext = null; this.loading = false; this.refreshActions(); } }
	}
	async select(row) {
		const context = this.context();
		if (this.disposed || this.loading || this.action || !this.sameContext(context, this.resultContext) || !this.rows.includes(row) || !this.validRow(row, context?.company)) return false;
		const selected = Object.freeze({ ...row }), revision = this.revision, action = {}; this.action = action; this.refreshActions();
		const stillCurrent = () => this.action === action && this.current(revision, context) && this.rows.includes(row) &&
			["name", "company", "status", "employee_version", "supplement_version"].every(field => row[field] === selected[field]);
		try {
			if (!stillCurrent()) return false;
			if (this.mode !== "wage") {
				const response = await frappe.call({ method: "jifeng_erp.temporary_workers.select_temporary_worker", type: "GET",
					args: { company: context.company, employee: selected.name, employee_version: selected.employee_version, supplement_version: selected.supplement_version } });
				if (!stillCurrent()) return false;
				if (!this.validSelection(response.message, context, selected)) throw new Error("Temporary worker selection changed");
			}
			if (this.mode === "wage") {
				const accepted = await this.bridge.selectTemporaryWorker(context.company, selected, stillCurrent);
				if (!stillCurrent()) return false;
				if (accepted !== true) { this.invalidate(); this.status.textContent = __("选择未完成，请核对来源权限或工资草稿后重新查询。 "); return false; }
				this.status.textContent = __("已选择该临工并进入工资录入，计薪方式仍按工资录入规则填写。 ");
			} else if (this.mode === "details") {
				this.bridge.open({ title: "临工明细：" + selected.name + "（" + context.company + "）", module: "工资", kind: "list", doctype: "Jifeng Wage Entry",
					route: ["List", "Jifeng Wage Entry", "List"], filters: { company: context.company, employee: selected.name }, hideCreate: true, availability: "partial" });
				this.status.textContent = __("已打开所选员工和公司的过往计薪记录。 ");
			} else {
				const opened = await this.bridge.openPersonnel(selected.name, { stillCurrent });
				if (!stillCurrent()) return false;
				if (!opened) { this.status.textContent = __("档案未打开，请核对档案权限后重试。 "); return false; }
				this.status.textContent = __("已打开所选员工档案。 ");
			}
			return true;
		} catch {
			if (stillCurrent()) { this.invalidate(); this.status.textContent = __("操作未完成，请核对来源或权限后重新查询。 "); }
			return false;
		} finally { if (this.action === action) { this.action = null; this.refreshActions(); } }
	}
	async openNew() {
		const context = this.context();
		if (this.mode !== "archive" || this.disposed || this.action) return false;
		if (!context) { frappe.msgprint(__("请选择有效公司后新建临工档案。")); return false; }
		const revision = this.revision, action = {}; this.action = action; this.refreshActions();
		const stillCurrent = () => this.action === action && this.current(revision, context);
		try {
			const opened = await this.bridge.openPersonnel(undefined, { company: context.company, term_type: "临时", stillCurrent });
			return stillCurrent() && !!opened;
		}
		catch { if (!this.disposed && this.action === action) frappe.msgprint(__("档案未打开，请核对新建权限后重试。")); return false; }
		finally { if (this.action === action) { this.action = null; this.refreshActions(); } }
	}
	// Opening the wage tab intentionally hides this cached picker; it is still current.
	setVisible(value) { this.visible = !!value; }
	isDirty() { return false; }
	getCloseBlock() { return ""; }
	destroy() {
		this.disposed = true; this.revision++; this.action = null; this.loading = false; this.rows = []; this.resultContext = null; this.queryContext = null;
		this.companyControl.$input?.off?.(".jifeng-temporary-workers"); this.refreshActions();
	}
};
