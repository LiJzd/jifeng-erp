/* Saved payroll settlement analysis shared by the A and B workspaces. */
window.JifengSalaryAnalysis = class JifengSalaryAnalysis {
	static mount(container) { return new this(container); }
	constructor(container) {
		this.container = container; this.disposed = false; this.revision = 0;
	this.build(); this.initialize();
	}
	el(tag, text, cls) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (cls) node.className = cls;
		return node;
	}
	button(text, action) {
		const node = this.el("button", __(text), "btn btn-default btn-sm");
		node.type = "button"; node.addEventListener("click", action); return node;
	}
	build() {
		this.container.replaceChildren(); this.container.classList.add("jfe-salary-analysis");
		const toolbar = this.el("div", undefined, "jfe-salary-analysis-toolbar");
		const companyHost = this.el("div");
		this.company = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", options: "Company", label: __("公司") } });
		const employeeHost = this.el("div");
		this.employee = frappe.ui.form.make_control({ parent: employeeHost, render_input: true,
			df: { fieldname: "employee", fieldtype: "Link", options: "Employee", label: __("员工（可选）") } });
		const currencyHost = this.el("div");
		this.currency = frappe.ui.form.make_control({ parent: currencyHost, render_input: true,
			df: { fieldname: "currency", fieldtype: "Link", options: "Currency", label: __("币种（混合币种时必选）") } });
		this.from = this.el("input"); this.from.type = "date"; this.from.value = frappe.datetime.month_start();
		this.from.setAttribute("aria-label", __("开始日期"));
		this.to = this.el("input"); this.to.type = "date"; this.to.value = frappe.datetime.get_today();
		this.to.setAttribute("aria-label", __("结束日期"));
		this.basis = this.el("select"); this.basis.setAttribute("aria-label", __("日期口径"));
		for (const [value, label] of [["period_end", "工资期结束"], ["period_start", "工资期开始"],
			["period_overlap", "工资期与日期相交"], ["posting_date", "记账日"]]) {
			const option = this.el("option", __(label)); option.value = value; this.basis.append(option);
		}
		this.search = this.el("input"); this.search.type = "search"; this.search.maxLength = 140;
		this.search.placeholder = __("工资条编号或姓名关键词"); this.search.setAttribute("aria-label", __("查询关键词"));
		this.status = this.el("p", __("请选择公司并查询。"), "jfe-salary-analysis-status"); this.status.setAttribute("role", "status");
		this.queryButton = this.button("查询", () => this.query());
		this.printButton = this.button("打印", () => this.output("print"));
		this.exportButton = this.button("导出", () => this.output("export"));
		toolbar.append(companyHost, employeeHost, currencyHost, this.from, this.to, this.basis, this.search, this.queryButton, this.printButton, this.exportButton);
		this.container.append(toolbar, this.el("p", __("仅读取已提交工资条。净应发不代表已付款；工序金额只归属于已核实的工资附加薪资来源。"), "jfe-salary-analysis-note"), this.status);
		this.totals = this.el("section", undefined, "jfe-salary-analysis-totals");
		this.charts = this.el("div", undefined, "jfe-salary-analysis-charts");
		this.sources = this.el("section", undefined, "jfe-salary-analysis-sources");
		this.container.append(this.totals, this.charts, this.sources);
		this.search.addEventListener("keydown", event => { if (event.key === "Enter") this.query(); });
		for (const field of [this.from, this.to, this.basis, this.search]) {
			field.addEventListener("input", () => this.invalidate()); field.addEventListener("change", () => this.invalidate());
		}
		for (const control of [this.company, this.employee, this.currency])
			control.$input?.on?.("input.jifeng-salary-analysis change.jifeng-salary-analysis", () => this.invalidate());
		this.updateOutputButtons();
	}
	invalidate() {
		if (this.disposed) return;
		this.cancelOutput(); this.result = null;
		this.revision++; this.loading = false; this.queryButton.disabled = false;
		this.totals.replaceChildren(); this.charts.replaceChildren(); this.sources.replaceChildren();
		this.status.textContent = __("筛选已变化，请重新查询。");
		this.updateOutputButtons();
	}
	async initialize() {
		const value = frappe.defaults?.get_user_default?.("Company");
		if (value) await this.company.set_value(value);
		if (!this.disposed) this.company.$input?.trigger?.("change");
	}
	context() {
		const company = this.company.get_value() || "", typed = this.company.$input?.val?.();
		const employee = this.employee.get_value() || "", employeeTyped = this.employee.$input?.val?.();
		const currency = this.currency.get_value() || "", currencyTyped = this.currency.$input?.val?.();
		if (!company.trim() || company !== company.trim() || (typeof typed === "string" && typed.trim() !== company) ||
			(employee && (employee !== employee.trim() || (typeof employeeTyped === "string" && employeeTyped.trim() !== employee))) ||
			(currency && (currency !== currency.trim() || (typeof currencyTyped === "string" && currencyTyped.trim() !== currency)))) return null;
		return { company, from_date: this.from.value, to_date: this.to.value, use_date: true,
			date_basis: this.basis.value, search: this.search.value.trim(), employee, currency };
	}
	valid(context) {
		return !!context && /^\d{4}-\d{2}-\d{2}$/.test(context.from_date) && /^\d{4}-\d{2}-\d{2}$/.test(context.to_date) &&
			context.from_date <= context.to_date && ["period_start", "period_end", "period_overlap", "posting_date"].includes(context.date_basis) &&
			context.search.length <= 140 && !/[\x00-\x1f]/.test(context.search);
	}
	async query() {
		if (this.disposed || this.loading) return;
		const context = this.context();
		if (!this.valid(context)) { frappe.msgprint(__("请核对公司、日期和员工筛选。")); return; }
		this.cancelOutput(); this.result = null;
		const revision = ++this.revision; this.loading = true; this.queryButton.disabled = true;
		this.updateOutputButtons();
		this.status.textContent = __("正在读取已保存的工资条与计薪来源…");
		this.totals.replaceChildren(); this.charts.replaceChildren(); this.sources.replaceChildren();
		try {
			const response = await frappe.call({ method: "jifeng_erp.salary_analysis.query_salary_analysis",
				type: "GET", args: { context: JSON.stringify(context) } });
			if (this.disposed || revision !== this.revision) return;
			const result = response.message;
			if (result?.readonly !== true || result.scope !== "salary_analysis") throw new Error("Invalid salary analysis");
			if (result.requires_currency === true) {
				if (!this.sameContext(result.context, context) || !Array.isArray(result.currencies) || result.currencies.length < 2 || !result.currencies.every(value => typeof value === "string")) throw new Error("Invalid currency selection response");
				this.status.textContent = __("当前范围含多种币种，未进行跨币种合计。请在币种筛选中选择一个币种后查询。"); return;
			}
			if (!this.validResult(result, context)) throw new Error("Invalid salary analysis");
			this.result = result; this.render(result); this.status.textContent = __("已读取") + " " + result.salary_slips + __(" 张已提交工资条 · 币种：") + (result.currency || __("无数据")) + "。";
		} catch {
			if (!this.disposed && revision === this.revision) this.status.textContent = __("来源或权限无法完整核实，请缩小范围或重新查询。");
		} finally {
			if (!this.disposed && revision === this.revision) { this.loading = false; this.queryButton.disabled = false; this.updateOutputButtons(); }
		}
	}
	validMoney(value) { return typeof value === "string" && /^-?\d+(?:\.\d+)?$/.test(value); }
	validChart(rows) { return Array.isArray(rows) && rows.every(row => row && typeof row.name === "string" && this.validMoney(row.amount) && /^\d+(?:\.\d+)?$/.test(row.share_percent)); }
	sameContext(actual, expected) {
		return actual && ["company", "from_date", "to_date", "use_date", "date_basis", "search", "employee", "currency"].every(key => actual[key] === expected[key]);
	}
	validResult(result, context) {
		return this.sameContext(result.context, context) && typeof result.currency === "string" &&
			typeof result.result_version === "string" && /^[0-9a-f]{64}$/.test(result.result_version) &&
			Number.isSafeInteger(result.salary_slips) && result.salary_slips >= 0 && result.settlements &&
			["gross_pay", "total_deduction", "net_pay", "rounded_total"].every(key => this.validMoney(result.settlements[key])) &&
			this.validChart(result.department) && this.validChart(result.group) && this.validChart(result.operation) &&
			Array.isArray(result.slips) && result.slips.length === result.salary_slips && new Set(result.slips.map(row => row?.name)).size === result.salary_slips &&
			result.slips.every(row => row && ["name", "version", "employee", "employee_name", "department", "currency", "start_date", "end_date"].every(key => typeof row[key] === "string") &&
				row.currency === result.currency && ["gross_pay", "total_deduction", "net_pay", "rounded_total"].every(key => this.validMoney(row[key]))) &&
			Array.isArray(result.sources) && result.sources.every(source => source && ["salary_slip", "salary_slip_version", "employee", "employee_name", "operation", "wage_entry", "wage_entry_version", "additional_salary", "additional_salary_version"].every(key => typeof source[key] === "string") && this.validMoney(source.amount)) &&
			Array.isArray(result.notes) && result.notes.every(note => typeof note === "string");
	}
	updateOutputButtons() {
		const enabled = !this.disposed && !this.loading && !this.outputting && !!this.result && this.sameContext(this.result.context, this.context());
		this.printButton.disabled = !enabled; this.exportButton.disabled = !enabled;
	}
	cancelOutput() {
		this.outputController?.abort(); this.outputController = null; this.outputting = false;
		this.printFrame?.remove(); this.printFrame = null;
	}
	async output(kind) {
		if (!["export", "print"].includes(kind) || this.disposed || this.loading || this.outputting || !this.result) return;
		const context = this.context(), result = this.result;
		if (!this.valid(context) || !this.sameContext(result.context, context)) { this.invalidate(); return; }
		const revision = this.revision, controller = new AbortController();
		const current = () => !this.disposed && !controller.signal.aborted && this.outputController === controller &&
			revision === this.revision && this.result === result && this.sameContext(context, this.context());
		this.cancelOutput(); this.outputController = controller; this.outputting = true; this.updateOutputButtons();
		this.status.textContent = __(kind === "export" ? "正在导出当前筛选的工资结算、图表和来源…" : "正在核对工资来源和打印权限…");
		let url;
		try {
			const contentType = kind === "export" ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "text/html";
			const args = { context: JSON.stringify(context), result_version: result.result_version };
			const response = await fetch("/api/method/jifeng_erp.salary_analysis_output." + kind + "_salary_analysis?" + new URLSearchParams(args),
				{ method: "GET", credentials: "same-origin", headers: { Accept: contentType }, signal: controller.signal });
			if (!current()) return;
			if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== contentType) throw new Error("Salary analysis output failed");
			if (kind === "export") {
				const file = await response.blob(); if (!current()) return;
				if (!file.size) throw new Error("Empty salary analysis export");
				url = URL.createObjectURL(file); const link = this.el("a");
				link.href = url; link.download = "工资历史分析.xlsx"; document.body.append(link); link.click(); link.remove();
				this.status.textContent = __("已导出当前筛选的结算、三图和准确来源；净应发不表示已付款。");
			} else {
				const html = await response.text(); if (!current()) return;
				if (!html.startsWith("<!doctype html>")) throw new Error("Invalid salary analysis print");
				await this.printHtml(html, controller, current);
				if (current()) this.status.textContent = __("已打开当前筛选的工资分析打印；请在打印窗口选择打印机或保存为PDF。");
			}
		} catch {
			if (current()) {
				this.result = null; this.totals.replaceChildren(); this.charts.replaceChildren(); this.sources.replaceChildren();
				this.printFrame?.remove(); this.printFrame = null;
				this.status.textContent = __("输出未完成，来源可能变化或打印／导出权限不足，请重新查询后重试。");
			}
		} finally {
			if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
			if (this.outputController === controller) { this.outputController = null; this.outputting = false; this.updateOutputButtons(); }
		}
	}
	async printHtml(html, controller, current) {
		const frame = this.el("iframe"); frame.setAttribute("title", __("工资分析打印")); frame.setAttribute("aria-hidden", "true");
		frame.setAttribute("sandbox", "allow-same-origin allow-modals");
		frame.style.cssText = "position:fixed;left:-10000px;top:0;width:1122px;height:800px;border:0";
		this.printFrame = frame;
		await new Promise((resolve, reject) => {
			const finish = error => {
				clearTimeout(timer); controller.signal.removeEventListener("abort", abort);
				frame.onload = null; frame.onerror = null; error ? reject(error) : resolve();
			};
			const abort = () => finish(new Error("Print cancelled"));
			const timer = setTimeout(() => finish(new Error("Print load timeout")), 10000);
			controller.signal.addEventListener("abort", abort, { once: true });
			frame.onload = () => finish(); frame.onerror = () => finish(new Error("Print load failed"));
			frame.srcdoc = html; document.body.append(frame);
		});
		if (!current()) { frame.remove(); return; }
		frame.contentWindow.addEventListener("afterprint", () => { frame.remove(); if (this.printFrame === frame) this.printFrame = null; }, { once: true });
		frame.contentWindow.focus(); frame.contentWindow.print();
	}
	render(result) {
		this.totals.replaceChildren(this.el("h2", __("工资结算") + (result.currency ? " · " + result.currency : "")));
		const dl = this.el("dl", undefined, "jfe-salary-analysis-total-grid");
		for (const [label, field] of [["总收入", "gross_pay"], ["总扣款", "total_deduction"], ["净应发（未证明付款）", "net_pay"], ["舍入净应发", "rounded_total"]]) {
			dl.append(this.el("dt", __(label)), this.el("dd", result.settlements[field]));
		}
		this.totals.append(dl);
		this.charts.replaceChildren(this.chart("部门收入占比", result.department),
			this.chart("分组收入占比", result.group), this.chart("工序计薪金额占比", result.operation));
		for (const note of result.notes) this.sources.append(this.el("p", __(note), "jfe-salary-analysis-note"));
		const heading = this.el("h2", __("可打开的工资条与工序来源")); this.sources.append(heading);
		const table = this.el("table", undefined, "jfe-salary-analysis-source-table");
		const head = this.el("tr"); for (const label of ["工资条", "员工", "工序", "工序金额", "计薪记录"]) head.append(this.el("th", __(label)));
		table.append(head);
		for (const source of result.sources) {
			const row = this.el("tr");
			row.append(this.linkCell(source.salary_slip, "Salary Slip"), this.el("td", source.employee_name || source.employee),
				this.el("td", source.operation), this.el("td", source.amount), this.linkCell(source.wage_entry, "Jifeng Wage Entry"));
			table.append(row);
		}
		if (!result.sources.length) { const empty = this.el("tr"); empty.append(this.el("td", __("当前范围没有已核实的工序计薪来源。"))); table.append(empty); }
		this.sources.append(table);
	}
	linkCell(name, doctype) {
		const cell = this.el("td"), link = this.el("button", name, "btn btn-link btn-xs");
		link.type = "button"; link.addEventListener("click", () => frappe.set_route("Form", doctype, name)); cell.append(link); return cell;
	}
	chart(title, rows) {
		const section = this.el("section", undefined, "jfe-salary-analysis-chart"); section.append(this.el("h2", __(title)));
		const list = this.el("ol");
		for (const row of rows) {
			const item = this.el("li"), label = this.el("div", undefined, "jfe-salary-analysis-chart-label");
			label.append(this.el("span", row.name), this.el("b", row.amount + " · " + row.share_percent + "%"));
			const track = this.el("div", undefined, "jfe-salary-analysis-chart-track"), bar = this.el("span");
			bar.style.width = Math.min(100, Math.max(0, Number(row.share_percent))) + "%"; track.append(bar); item.append(label, track); list.append(item);
		}
		if (!rows.length) list.append(this.el("li", __("当前范围没有已提交工资条。")));
		section.append(list); return section;
	}
	destroy() {
		this.cancelOutput(); this.disposed = true; this.revision++; this.container.replaceChildren();
		for (const control of [this.company, this.employee, this.currency]) control.$input?.off?.(".jifeng-salary-analysis");
	}
};
