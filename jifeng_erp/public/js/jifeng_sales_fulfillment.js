/* Shared A/B sales source view. Native units, currencies and balances stay separate. */
window.JifengSalesFulfillment = class JifengSalesFulfillment {
	constructor(host, bridge) {
		this.host = host; this.bridge = bridge; this.module = "销售";
		this.disposed = false; this.visible = true; this.revision = 0; this.detailRevision = 0;
		this.rows = []; this.detail = null; this.scope = null; this.build(); this.ready = Promise.resolve(true);
	}
	element(tag, text, className) {
		const node = document.createElement(tag);
		if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className;
		return node;
	}
	button(label, action, primary = false) {
		const node = this.element("button", __(label), "jfe-procurement-button" + (primary ? " primary" : ""));
		node.type = "button"; node.addEventListener("click", action); return node;
	}
	build() {
		// Reuse the shared read-only source table layout; no procurement data rules.
		this.host.classList.add("jfe-procurement", "jfe-sales-fulfillment"); this.host.replaceChildren();
		const toolbar = this.element("div", undefined, "jfe-procurement-toolbar"); this.controls = {};
		for (const [fieldname, label, fieldtype, options] of [["company", "公司", "Link", "Company"],
			["from_date", "订单日期起", "Date"], ["to_date", "订单日期止", "Date"]]) {
			const parent = this.element("div", undefined, "jfe-procurement-filter");
			const control = frappe.ui.form.make_control({ parent, render_input: true,
				df: { fieldname, label: __(label), fieldtype, options, onchange: () => this.syncScope() } });
			control.$input?.on?.("input.jifeng-sales-fulfillment change.jifeng-sales-fulfillment", () => this.invalidate("条件已改变，请重新查询。"));
			control.$link_clear?.on?.("click.jifeng-sales-fulfillment", () => this.invalidate("条件已改变，请重新查询。"));
			this.controls[fieldname] = control; toolbar.append(parent);
		}
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("销售订单号（可选）"); this.search.setAttribute("aria-label", __("销售订单号关键字"));
		this.search.addEventListener("input", () => this.invalidate("条件已改变，请重新查询。"));
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(); } });
		toolbar.append(this.search, this.button("查询销售订单", () => this.query(), true));
		this.status = this.element("p", __("请选择公司和订单日期范围后查询。"), "jfe-procurement-status"); this.status.setAttribute("role", "status");
		this.list = this.element("div", undefined, "jfe-procurement-list");
		this.detailHost = this.element("section", undefined, "jfe-procurement-detail"); this.detailHost.setAttribute("aria-label", __("销售履行来源明细"));
		this.host.append(this.element("h2", __("销售履行核对")), this.element("p",
			__("只读核对。日期仅筛销售订单；关联制造、发货、发票及收款保留全部日期。生产完成不等于发货；交货单与更新库存的发票分别展示，不相加。当前发票余额不是截止日余额。"), "jfe-procurement-note"),
			toolbar, this.status, this.list, this.detailHost);
		this.scope = this.values();
	}
	validName(value) { return typeof value === "string" && !!value && value === value.trim() && [...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value); }
	linkValue(control) {
		const value = control.get_value() || "", input = control.get_input_value?.() ?? control.$input?.val?.();
		return typeof input === "string" && input !== value ? null : this.validName(value) ? value : null;
	}
	values() {
		return { company: this.linkValue(this.controls.company), from_date: this.controls.from_date.get_value() || "",
			to_date: this.controls.to_date.get_value() || "", search: this.search.value || "" };
	}
	sameScope(left, right) { return !!left && !!right && Object.keys(right).every((key) => left[key] === right[key]); }
	syncScope() { if (!this.disposed && !this.sameScope(this.scope, this.values())) this.invalidate("条件已改变，请重新查询。"); }
	clearResults() { this.rows = []; this.detail = null; this.list.replaceChildren(); this.detailHost.replaceChildren(); }
	invalidate(message) {
		if (this.disposed) return;
		this.scope = this.values(); this.revision++; this.detailRevision++; this.clearResults(); this.status.textContent = __(message);
	}
	current(revision, scope, detailRevision = null) {
		return !this.disposed && this.visible && revision === this.revision && this.sameScope(scope, this.values()) &&
			(detailRevision === null || detailRevision === this.detailRevision);
	}
	validDate(value) {
		if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
		const date = new Date(value + "T00:00:00Z"); return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
	}
	validScope(scope) {
		return this.validName(scope.company) && this.validDate(scope.from_date) && this.validDate(scope.to_date) &&
			scope.from_date <= scope.to_date && typeof scope.search === "string" && scope.search.length <= 140;
	}
	call(method, args) { return frappe.call({ method: "jifeng_erp.sales_fulfillment_workbench." + method, type: "GET", args }); }
	async query() {
		if (this.disposed || !this.visible) return;
		this.syncScope(); const scope = this.values(), revision = ++this.revision; this.scope = scope; this.detailRevision++; this.clearResults();
		if (!this.validScope(scope)) { this.status.textContent = __("请选择公司，并填写有效的订单日期起止范围。"); return; }
		this.status.textContent = __("正在核对销售订单来源…");
		try {
			const response = await this.call("query_orders", { ...scope }); this.syncScope();
			if (!this.current(revision, scope)) return;
			const data = response.message;
			if (!Array.isArray(data) || data.length > 20 || new Set(data.map((row) => row?.sales_order?.name)).size !== data.length ||
				!data.every((row) => this.validDTO(row, scope) && row.sales_order.transaction_date >= scope.from_date && row.sales_order.transaction_date <= scope.to_date))
				throw new Error("Invalid complete sales scope");
			this.rows = data.map((row) => ({ ...row.sales_order })); this.renderList(revision, scope);
			this.status.textContent = __(this.rows.length ? "请选择销售订单查看完整来源；没有跨单汇总。" : "所选范围内没有可见销售订单。");
		} catch {
			this.syncScope(); if (this.current(revision, scope)) { this.clearResults();
				this.status.textContent = __("未取得完整销售履行来源，未显示部分结果。请核对权限；范围超过20单时请缩小日期或单号范围，再手动查询。"); }
		}
	}
	async readDetail(name) {
		if (this.disposed || !this.visible) return;
		this.syncScope(); if (!this.rows.some((row) => row.name === name)) return;
		const scope = this.values(), revision = this.revision, detailRevision = ++this.detailRevision;
		this.detail = null; this.detailHost.replaceChildren(); this.status.textContent = __("正在读取所选销售订单的完整来源…");
		try {
			const response = await this.call("read_order", { company: scope.company, sales_order: name }); this.syncScope();
			if (!this.current(revision, scope, detailRevision)) return;
			if (!this.validDTO(response.message, scope, name)) throw new Error("Invalid exact sales source");
			this.detail = response.message; this.renderDetail(this.detail, revision, scope, detailRevision);
			this.status.textContent = __("已读取完整来源。状态和当前余额以本次服务端读取为准；原生单据中可继续核对。");
		} catch {
			this.syncScope(); if (this.current(revision, scope, detailRevision)) { this.detailRevision++; this.clearResults();
				this.status.textContent = __("不能完整读取所选销售履行来源，已清空全部结果。请核对权限或原生单据后重新查询。"); }
		}
	}
	scalar(value) { return value === null || ["string", "boolean"].includes(typeof value) || typeof value === "number" && Number.isFinite(value); }
	projection(value) { return !!value && !Array.isArray(value) && typeof value === "object" && Object.values(value).every((item) => this.scalar(item)); }
	document(value, company) {
		return this.projection(value) && this.validName(value.name) && typeof value.modified === "string" && !!value.modified &&
			[0, 1, 2].includes(value.docstatus) && value.company === company;
	}
	childRows(rows, doc, doctype) {
		if (!Array.isArray(rows)) return false;
		const names = new Set();
		return rows.every((row) => {
			if (!this.projection(row) || !this.validName(row.name) || typeof row.modified !== "string" || !row.modified ||
				row.parent !== doc.name || row.parenttype !== doctype || row.parentfield !== "items" || !Number.isSafeInteger(row.idx) || row.idx < 1 ||
				!this.validName(row.item_code) || names.has(row.name)) return false;
			names.add(row.name); return true;
		});
	}
	validDTO(data, scope, name = null) {
		if (!data || data.readonly !== true || data.can_write !== false || data.quantities_aggregated !== false || data.company !== scope.company ||
			!this.document(data.sales_order, scope.company) || !this.validName(data.sales_order.customer) || !this.validDate(data.sales_order.transaction_date) ||
			(name !== null && data.sales_order.name !== name) || !this.childRows(data.items, data.sales_order, "Sales Order")) return false;
		const order = data.sales_order, items = new Map(data.items.map((row) => [row.name, row])), flows = new Map(), flowItems = new Set();
		if (!Array.isArray(data.flows)) return false;
		for (const flow of data.flows) {
			if (!this.document(flow, scope.company) || flow.sales_order !== order.name || !items.has(flow.sales_order_item) ||
				!this.validName(flow.item_code) || flows.has(flow.name) || flowItems.has(flow.sales_order_item)) return false;
			flows.set(flow.name, flow); flowItems.add(flow.sales_order_item);
		}
		const manufacture = (source, work = null) => this.document(source?.document, scope.company) && source.document.purpose === "Manufacture" &&
			(work ? source.document.work_order === work.name : !source.document.work_order) && this.childRows(source.finished_items, source.document, "Stock Entry") &&
			source.finished_items.length > 0 && source.finished_items.every((row) => [1, true].includes(row.is_finished_item) && (work ? row.item_code === work.production_item &&
				row.jf_flow_order === work.jf_flow_order : flows.has(row.jf_flow_order) && row.item_code === flows.get(row.jf_flow_order).item_code));
		if (!Array.isArray(data.work_orders) || !Array.isArray(data.independent_manufacture_entries)) return false;
		const workNames = new Set(), entryNames = new Set();
		for (const source of data.work_orders) {
			const work = source?.document;
			if (!this.document(work, scope.company) || work.sales_order !== order.name || !items.has(work.sales_order_item) ||
				!this.validName(work.production_item) || workNames.has(work.name) || !Array.isArray(source.manufacture_entries)) return false;
			workNames.add(work.name);
			for (const entry of source.manufacture_entries) {
				if (!manufacture(entry, work) || entryNames.has(entry.document.name)) return false; entryNames.add(entry.document.name);
			}
		}
		for (const source of data.independent_manufacture_entries) {
			if (!manufacture(source) || entryNames.has(source.document.name)) return false; entryNames.add(source.document.name);
		}
		for (const [key, doctype] of [["delivery_notes", "Delivery Note"], ["sales_invoices", "Sales Invoice"]]) {
			if (!Array.isArray(data[key])) return false;
			const seen = new Set();
			for (const source of data[key]) {
				if (!this.document(source?.document, scope.company) || source.document.customer !== order.customer || seen.has(source.document.name) ||
					!this.childRows(source.items, source.document, doctype)) return false;
				seen.add(source.document.name);
			}
		}
		const receivables = data.receivables;
		if (!receivables || !Array.isArray(receivables.ledger_entries) || !Array.isArray(receivables.voucher_sources)) return false;
		const ledgerNames = new Set(), voucherNames = new Set();
		for (const row of receivables.ledger_entries) {
			if (!this.projection(row) || !this.validName(row.name) || row.company !== scope.company || row.party_type !== "Customer" ||
				row.party !== order.customer || ledgerNames.has(row.name)) return false; ledgerNames.add(row.name);
		}
		for (const source of receivables.voucher_sources) {
			const key = source?.doctype + ":" + source?.document?.name;
			if (!["Sales Order", "Sales Invoice", "Payment Entry", "Journal Entry"].includes(source?.doctype) ||
				!this.document(source.document, scope.company) || voucherNames.has(key) ||
				(["Sales Order", "Sales Invoice"].includes(source.doctype) && source.document.customer !== order.customer) ||
				(source.doctype === "Payment Entry" && (source.document.party_type !== "Customer" || source.document.party !== order.customer))) return false;
			voucherNames.add(key);
		}
		return true;
	}
	display(value) { return value === null || value === undefined ? "—" : String(value); }
	labels() {
		return { name: "单号", idx: "行号", company: "公司", customer: "客户", docstatus: "审核状态", status: "原生状态", transaction_date: "订单日期",
			posting_date: "记账日期", delivery_date: "交货日期", due_date: "到期日", currency: "交易币种", party_account_currency: "应收账户币种",
			per_delivered: "原生发货百分比", per_billed: "原生开票百分比", delivery_status: "原生发货状态", billing_status: "原生开票状态",
			item_code: "商品", item_name: "商品名称", qty: "原生数量", uom: "交易单位", stock_qty: "库存数量", stock_uom: "库存单位",
			conversion_factor: "单位换算系数", delivered_qty: "原生已发数量", billed_amt: "原生已开票金额", returned_qty: "原生退货数量",
			jf_make_flow: "生成流程单", jf_flow_order: "流程单", sales_order: "销售订单", against_sales_order: "销售订单", sales_order_item: "订单明细",
			so_detail: "订单明细", source_changed: "来源已变更", source_status: "来源状态", source_change_note: "来源变更说明", planned_qty: "流程计划库存量",
			source_qty: "源订单交易量", source_item_code: "来源商品", source_uom: "源订单交易单位快照", source_stock_uom: "源订单库存单位快照",
			source_conversion_factor: "源订单单位换算系数快照", work_order: "生产工单", production_item: "生产商品", produced_qty: "原生已产数量",
			process_loss_qty: "原生损耗数量", planned_start_date: "计划开始", planned_end_date: "计划结束", actual_start_date: "实际开始", actual_end_date: "实际结束",
			purpose: "库存凭证用途", stock_entry_type: "库存凭证类型", fg_completed_qty: "整张制造凭证原生完成数量", transfer_qty: "库存转移数量", is_finished_item: "成品行", is_return: "退货标记",
			return_against: "退货原单", per_returned: "原生退货百分比", update_stock: "发票更新库存", debit_to: "应收账户",
			outstanding_amount: "当前整张发票余额（应收账户币种）", update_outstanding_for_self: "更新本发票余额", delivery_note: "交货单",
			against_sales_invoice: "销售发票", dn_detail: "交货原明细", si_detail: "发票原明细", sales_invoice_item: "原发票明细",
			party_type: "往来类型", party: "往来对象", payment_type: "收付款类型", paid_from: "付款账户", paid_to: "收款账户",
			paid_from_account_currency: "付款账户币种", paid_to_account_currency: "收款账户币种", voucher_type: "凭证类型", voucher_no: "凭证单号",
			against_voucher_type: "对冲凭证类型", against_voucher_no: "对冲单号", account: "账户", account_currency: "账户币种",
			amount: "原生分类账金额（公司币种）", amount_in_account_currency: "原生分类账金额（账户币种）", delinked: "解除关联", multi_currency: "多币种" };
	}
	businessValue(field, value, context = null) {
		if (["so_detail", "sales_order_item"].includes(field) && typeof value === "string") {
			const item = this.detail?.items?.find((row) => row.name === value);
			if (item) {
				const description = [__("第 ") + item.idx + __(" 行"), __("商品：") + item.item_code];
				if (!context?.source_changed) for (const [key, label] of [["uom", "交易单位："], ["stock_uom", "库存单位："]])
					if (key in item && item[key] !== null && item[key] !== "") description.push(__(label) + this.display(item[key]));
				return description.join(" · ") + __("（明细标识：") + value + "）";
			}
		}
		if (field === "docstatus") return __({ 0: "草稿", 1: "已提交", 2: "已取消" }[value] || "未知状态");
		if (["is_return", "update_stock", "delinked", "source_changed", "is_finished_item", "jf_make_flow", "multi_currency", "update_outstanding_for_self"].includes(field) && [0, 1, false, true].includes(value)) return __(value ? "是" : "否");
		return this.display(value);
	}
	table(rows, label, preferred, overrides = {}) {
		const wrap = this.element("div", undefined, "jfe-procurement-scroll"), table = this.element("table"), labels = this.labels(); table.setAttribute("aria-label", __(label));
		const fields = [...new Set(preferred)].filter((field) => labels[field] && rows.some((row) => field in row));
		const head = this.element("thead"), tr = this.element("tr"), body = this.element("tbody");
		for (const field of fields) tr.append(this.element("th", __(overrides[field] || labels[field]))); head.append(tr);
		for (const row of rows) { const line = this.element("tr"); for (const field of fields) line.append(this.element("td", this.businessValue(field, row[field], row))); body.append(line); }
		table.append(head, body); wrap.append(table); return wrap;
	}
	summary(doc) {
		const result = this.element("dl", undefined, "jfe-procurement-summary"), labels = this.labels();
		for (const field of ["name", "company", "customer", "docstatus", "status", "transaction_date", "delivery_date", "posting_date", "due_date", "currency", "party_account_currency",
			"per_delivered", "per_billed", "delivery_status", "billing_status", "source_changed", "source_status", "source_change_note", "sales_order", "sales_order_item",
			"item_code", "source_item_code", "production_item", "work_order", "jf_flow_order", "stock_uom", "purpose", "stock_entry_type", "is_return", "return_against", "per_returned", "update_stock", "update_outstanding_for_self", "debit_to",
			"party_type", "party", "payment_type", "paid_from", "paid_to", "paid_from_account_currency", "paid_to_account_currency", "voucher_type", "multi_currency"]) {
			if (!(field in doc)) continue;
			const pair = this.element("div"); pair.append(this.element("dt", __(labels[field])), this.element("dd", this.businessValue(field, doc[field], doc))); result.append(pair);
		}
		return result;
	}
	rowTables(rows, label) {
		const content = this.element("div");
		for (const [title, fields] of [["数量与单位", ["qty", "uom", "stock_qty", "stock_uom", "conversion_factor", "transfer_qty", "is_finished_item"]],
			["原生进度", ["delivery_date", "delivered_qty", "returned_qty", "billed_amt", "jf_make_flow"]],
			["准确来源", ["sales_order", "against_sales_order", "so_detail", "delivery_note", "dn_detail", "against_sales_invoice", "si_detail", "sales_invoice_item", "jf_flow_order"]]]) {
			if (!fields.some((field) => rows.some((row) => field in row))) continue;
			content.append(this.element("p", __(title), "jfe-procurement-group-title"), this.table(rows, label + "·" + title, ["idx", "name", "item_code", "item_name", ...fields], { name: "明细标识" }));
		}
		if (!content.children.length) content.append(this.table(rows, label, ["idx", "name", "item_code", "item_name"], { name: "明细标识" }));
		return content;
	}
	doctypeLabel(doctype) { return { "Sales Order": "销售订单", "Jifeng Flow Order": "流程单", "Work Order": "生产工单", "Stock Entry": "制造入库凭证",
		"Delivery Note": "交货单", "Sales Invoice": "销售发票", "Payment Entry": "收付款凭证", "Journal Entry": "记账凭证" }[doctype] || doctype; }
	nativeButton(doctype, name, revision, scope, detailRevision) {
		return this.button("打开原生凭证", () => {
			this.syncScope(); if (!this.current(revision, scope, detailRevision)) return;
			if (typeof this.bridge?.open !== "function") { this.status.textContent = __("原生凭证入口尚未就绪，请重新打开本工作台。"); return; }
			this.bridge.open({ title: this.doctypeLabel(doctype) + "：" + name, module: this.module, kind: "form", doctype, route: ["Form", doctype, name] });
		});
	}
	card(doctype, source, revision, scope, detailRevision) {
		const card = this.element("article", undefined, "jfe-procurement-card"), doc = source.document;
		card.append(this.element("h4", this.doctypeLabel(doctype) + "：" + doc.name), this.nativeButton(doctype, doc.name, revision, scope, detailRevision), this.summary(doc));
		const fields = doctype === "Jifeng Flow Order" ? ["planned_qty", "stock_uom", "source_qty", "source_uom", "source_stock_uom", "source_conversion_factor"] :
			["qty", "stock_uom", "produced_qty", "process_loss_qty", "fg_completed_qty", "planned_start_date", "planned_end_date", "actual_start_date", "actual_end_date", "outstanding_amount"];
		if (fields.some((field) => field in doc)) card.append(this.table([doc], "原生数量、日期或当前余额", fields));
		if (doctype === "Stock Entry") card.append(this.element("p", __("整张凭证完成数量与本订单成品明细分别显示，不按当前明细求和或分摊。"), "jfe-procurement-note"));
		const rows = source.items || source.finished_items;
		if (Array.isArray(rows)) card.append(rows.length ? this.rowTables(rows, "本订单准确来源明细") : this.element("p", __("没有此类子行。")));
		return card;
	}
	renderList(revision, scope) {
		for (const row of this.rows) {
			const line = this.element("div", undefined, "jfe-procurement-list-row"); line.append(this.element("strong", row.name), this.element("span", row.transaction_date),
				this.element("span", __("客户：") + row.customer), this.element("span", this.display(row.status)), this.element("span", __("交易币种：") + this.display(row.currency)),
				this.button("查看完整来源", () => { this.syncScope(); if (this.current(revision, scope)) this.readDetail(row.name); })); this.list.append(line);
		}
	}
	renderDetail(data, revision, scope, detailRevision) {
		const add = (doctype, source) => this.detailHost.append(this.card(doctype, source, revision, scope, detailRevision));
		this.detailHost.append(this.element("h3", __("所选销售订单：") + data.sales_order.name), this.element("p", __("同款不同订单子行、单位和币种保持独立；没有推算净发货量或应收合计。"), "jfe-procurement-note"));
		add("Sales Order", { document: data.sales_order, items: data.items });
		this.detailHost.append(this.element("h3", __("流程单与生产工单")), this.element("p", __("原生工单已产数量与制造入库凭证分别核对；工序完成不是成品库存或发货。"), "jfe-procurement-note"));
		for (const flow of data.flows) add("Jifeng Flow Order", { document: flow });
		for (const work of data.work_orders) { add("Work Order", work); for (const entry of work.manufacture_entries) add("Stock Entry", entry); }
		this.detailHost.append(this.element("h3", __("无生产工单的制造入库")));
		if (!data.independent_manufacture_entries.length) this.detailHost.append(this.element("p", __("没有关联凭证。")));
		for (const entry of data.independent_manufacture_entries) add("Stock Entry", entry);
		for (const [key, title, doctype] of [["delivery_notes", "交货及退货（独立来源）", "Delivery Note"], ["sales_invoices", "销售发票及退货发票（独立来源）", "Sales Invoice"]]) {
			this.detailHost.append(this.element("h3", __(title))); if (!data[key].length) this.detailHost.append(this.element("p", __("没有关联凭证。")));
			for (const source of data[key]) add(doctype, source);
		}
		this.detailHost.append(this.element("h3", __("原生收款及当前应收核对")), this.element("p",
			__("当前余额属于整张发票，按应收账户币种显示。关联收款和记账凭证可能还引用其他同客户订单或发票，保留来源，不分摊为本订单净额。"), "jfe-procurement-note"));
		for (const source of data.receivables.voucher_sources) add(source.doctype, source);
		this.detailHost.append(this.element("h4", __("原生收款分类账（不重新汇总）")));
		this.detailHost.append(data.receivables.ledger_entries.length ? this.table(data.receivables.ledger_entries, "原生收款分类账", ["posting_date", "due_date", "voucher_type", "voucher_no", "against_voucher_type", "against_voucher_no", "account", "account_currency", "amount_in_account_currency", "amount", "delinked"]) : this.element("p", __("没有关联收款分类账记录。")));
	}
	isDirty() { return false; }
	getCloseBlock() { return ""; }
	setVisible(visible) {
		if (this.disposed) return; visible = Boolean(visible);
		if (this.visible && !visible) this.invalidate("已离开核对页，返回后请重新查询。"); this.visible = visible;
	}
	destroy() {
		if (this.disposed) return; this.disposed = true; this.revision++; this.detailRevision++; this.clearResults();
		for (const control of Object.values(this.controls)) { control.$input?.off?.(".jifeng-sales-fulfillment"); control.$link_clear?.off?.(".jifeng-sales-fulfillment"); }
	}
};
