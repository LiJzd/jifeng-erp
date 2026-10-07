/* Shared A/B read-only procurement source workbench. No client-side balances. */
window.JifengProcurementFulfillment = class JifengProcurementFulfillment {
	constructor(host, bridge, moduleName = "物料") {
		this.host = host; this.bridge = bridge; this.disposed = false; this.visible = true;
		this.module = ["物料", "材料"].includes(moduleName) ? moduleName : "物料";
		this.revision = 0; this.detailRevision = 0; this.rows = []; this.detail = null;
		this.scope = null; this.build(); this.ready = Promise.resolve(true);
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
		this.host.classList.add("jfe-procurement"); this.host.replaceChildren();
		const title = this.element("h2", __("采购履行核对"));
		const note = this.element("p", __("只读核对。日期仅筛采购单，关联收货、退货、发票和付款保留全部日期。金额保持整凭证范围，不分摊到采购行；当前发票余额不是截止日余额。"), "jfe-procurement-note");
		const toolbar = this.element("div", undefined, "jfe-procurement-toolbar");
		this.controls = {};
		for (const [fieldname, label, fieldtype, options] of [["company", "公司", "Link", "Company"],
			["supplier", "供应商", "Link", "Supplier"], ["from_date", "采购日期起", "Date"], ["to_date", "采购日期止", "Date"]]) {
			const parent = this.element("div", undefined, "jfe-procurement-filter");
			const control = frappe.ui.form.make_control({ parent, render_input: true,
				df: { fieldname, label: __(label), fieldtype, options, onchange: () => this.syncScope() } });
			control.$input?.on?.("input.jifeng-procurement change.jifeng-procurement", () => this.invalidate("条件已改变，请重新查询。"));
			control.$link_clear?.on?.("click.jifeng-procurement", () => this.invalidate("条件已改变，请重新查询。"));
			this.controls[fieldname] = control; toolbar.append(parent);
		}
		this.search = this.element("input"); this.search.type = "text"; this.search.maxLength = 140;
		this.search.placeholder = __("采购单号（可选）"); this.search.setAttribute("aria-label", __("采购单号关键字"));
		this.search.addEventListener("input", () => this.invalidate("条件已改变，请重新查询。"));
		this.search.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); this.query(); } });
		toolbar.append(this.search, this.button("查询采购单", () => this.query(), true));
		this.status = this.element("p", __("请选择公司、供应商和采购日期范围后查询。"), "jfe-procurement-status");
		this.status.setAttribute("role", "status");
		this.list = this.element("div", undefined, "jfe-procurement-list");
		this.detailHost = this.element("section", undefined, "jfe-procurement-detail");
		this.detailHost.setAttribute("aria-label", __("采购履行来源明细"));
		this.host.append(title, note, toolbar, this.status, this.list, this.detailHost);
		this.scope = this.values();
	}
	validName(value) {
		return typeof value === "string" && !!value && value === value.trim() &&
			[...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value);
	}
	linkValue(control) {
		const value = control.get_value() || "";
		// Native Link maps a displayed title back to its exact stored name.
		const input = control.get_input_value?.() ?? control.$input?.val?.();
		if (typeof input === "string" && input !== value) return null;
		return this.validName(value) ? value : null;
	}
	values() {
		return Object.fromEntries([...Object.entries(this.controls).map(([key, control]) =>
			[key, ["company", "supplier"].includes(key) ? this.linkValue(control) : control.get_value() || ""]),
			["search", this.search.value || ""]]);
	}
	sameScope(left, right) { return !!left && !!right && Object.keys(right).every((key) => left[key] === right[key]); }
	syncScope() {
		if (!this.disposed && !this.sameScope(this.scope, this.values())) this.invalidate("条件已改变，请重新查询。");
	}
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
		const date = new Date(value + "T00:00:00Z");
		return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
	}
	validScope(scope) {
		return [scope.company, scope.supplier].every((value) => typeof value === "string" && !!value && value === value.trim()) &&
			this.validDate(scope.from_date) && this.validDate(scope.to_date) && scope.from_date <= scope.to_date &&
			typeof scope.search === "string" && scope.search.length <= 140;
	}
	call(method, args) { return frappe.call({ method: "jifeng_erp.procurement_fulfillment_workbench." + method, type: "GET", args }); }
	async query() {
		if (this.disposed || !this.visible) return;
		this.syncScope(); const scope = this.values(), revision = ++this.revision; this.scope = scope;
		this.detailRevision++; this.clearResults();
		if (!this.validScope(scope)) { this.status.textContent = __("请选择公司、供应商，并填写有效的采购日期起止范围。" ); return; }
		this.status.textContent = __("正在核对采购来源…");
		try {
			const response = await this.call("query_procurement_sources", { ...scope }); this.syncScope();
			if (!this.current(revision, scope)) return;
			const data = response.message;
			if (!Array.isArray(data) || data.length > 20 || new Set(data.map((row) => row?.purchase_order?.name)).size !== data.length ||
				!data.every((row) => this.validDTO(row, scope) && row.purchase_order.transaction_date >= scope.from_date &&
					row.purchase_order.transaction_date <= scope.to_date)) throw new Error("Invalid complete procurement scope");
			// Keep only the list projection. Opening a source obtains a fresh full DTO.
			this.rows = data.map((row) => ({ ...row.purchase_order })); this.renderList(revision, scope);
			this.status.textContent = __(this.rows.length ? "请选择采购单查看完整来源；没有跨单汇总。" : "所选范围内没有可见采购单。" );
		} catch {
			this.syncScope();
			if (this.current(revision, scope)) { this.clearResults(); this.status.textContent = __("未取得完整采购来源，未显示部分结果。请核对权限；范围超过20单时请缩小日期或单号范围，再手动查询。" ); }
		}
	}
	async readDetail(name) {
		if (this.disposed || !this.visible) return;
		this.syncScope();
		if (!this.rows.some((row) => row.name === name)) return;
		const scope = this.values(), revision = this.revision, detailRevision = ++this.detailRevision;
		this.detail = null; this.detailHost.replaceChildren(); this.status.textContent = __("正在读取所选采购单的完整来源…");
		try {
			const response = await this.call("read_procurement_source", { company: scope.company, purchase_order: name }); this.syncScope();
			if (!this.current(revision, scope, detailRevision)) return;
			if (!this.validDTO(response.message, scope, name)) throw new Error("Invalid exact procurement source");
			this.detail = response.message; this.renderDetail(response.message, revision, scope, detailRevision);
			this.status.textContent = __("已读取完整来源。状态和余额以本次服务端读取为准；原生单据中可继续核对。" );
		} catch {
			this.syncScope();
			if (this.current(revision, scope, detailRevision)) { this.detailRevision++; this.clearResults();
				this.status.textContent = __("不能完整读取所选采购来源，已清空全部结果。请核对权限或原生单据后重新查询。" ); }
		}
	}
	scalar(value) { return value === null || ["string", "boolean"].includes(typeof value) || typeof value === "number" && Number.isFinite(value); }
	projection(value) { return !!value && !Array.isArray(value) && typeof value === "object" && Object.values(value).every((item) => this.scalar(item)); }
	document(value, company) {
		return this.projection(value) && typeof value.name === "string" && !!value.name && typeof value.modified === "string" && !!value.modified &&
			[0, 1, 2].includes(value.docstatus) && value.company === company;
	}
	childRows(rows, doc, doctype, fieldname, unique) {
		const childtypes = { "Purchase Order": "Purchase Order Item", "Purchase Receipt": "Purchase Receipt Item", "Purchase Invoice": "Purchase Invoice Item",
			"Subcontracting Order": "Subcontracting Order Item", "Subcontracting Receipt": "Subcontracting Receipt Item",
			"Payment Entry": "Payment Entry Reference", "Journal Entry": "Journal Entry Account", "Sales Invoice": "Sales Invoice Item",
			"Sales Order": "Sales Order Item", "Delivery Note": "Delivery Note Item" };
		if (!Array.isArray(rows) || !childtypes[doctype]) return false;
		return rows.every((row) => {
			if (!this.projection(row) || typeof row.name !== "string" || !row.name || typeof row.modified !== "string" || !row.modified ||
				row.parent !== doc.name || row.parenttype !== doctype || row.parentfield !== fieldname || !Number.isSafeInteger(row.idx) || row.idx < 1 ||
				unique.has(row.name)) return false;
			unique.add(row.name); return true;
		});
	}
	validDTO(data, scope, name = null) {
		if (!data || data.readonly !== true || data.can_write !== false || data.quantities_aggregated !== false || data.amounts_allocated_to_items !== false ||
			data.company !== scope.company || data.supplier !== scope.supplier || typeof data.company_currency !== "string" || !data.company_currency ||
			!this.document(data.purchase_order, scope.company) || data.purchase_order.supplier !== scope.supplier ||
			(name !== null && data.purchase_order.name !== name) || !this.validDate(data.purchase_order.transaction_date) ||
			!this.childRows(data.items, data.purchase_order, "Purchase Order", "items", new Set())) return false;
		for (const [group, doctype] of [["purchase_receipts", "Purchase Receipt"], ["subcontracting_orders", "Subcontracting Order"],
			["subcontracting_receipts", "Subcontracting Receipt"], ["purchase_invoices", "Purchase Invoice"]]) {
			if (!Array.isArray(data[group]) || new Set(data[group].map((row) => row?.document?.name)).size !== data[group].length) return false;
			for (const source of data[group]) {
				const unique = new Set();
				if (!this.document(source.document, scope.company) || source.document.supplier !== scope.supplier || source.amount_scope !== "whole_voucher" ||
					source.amounts_allocated_to_items !== false || typeof source.valuation_currency !== "string" || !source.valuation_currency ||
					!["items", "other_items", "unlinked_items"].every((key) => this.childRows(source[key], source.document, doctype, "items", unique)) ||
					doctype === "Subcontracting Order" && !this.childRows(source.service_items, source.document, doctype, "service_items", new Set())) return false;
			}
		}
		const payables = data.payables;
		if (!payables || payables.amount_scope !== "whole_voucher" || payables.balance_basis !== "native_current_outstanding" ||
			payables.amounts_allocated_to_items !== false || !Array.isArray(payables.ledger_entries) || !Array.isArray(payables.voucher_sources) ||
			!Array.isArray(payables.other_reference_sources)) return false;
		if (new Set(payables.ledger_entries.map((row) => row?.name)).size !== payables.ledger_entries.length || payables.ledger_entries.some((row) =>
			!this.projection(row) || typeof row.name !== "string" || !row.name || row.company !== scope.company || row.party_type !== "Supplier" || row.party !== scope.supplier)) return false;
		for (const [sources, companion] of [[payables.voucher_sources, false], [payables.other_reference_sources, true]]) {
			const seen = new Set();
			for (const source of sources) {
				const allowed = companion ? ["Purchase Order", "Purchase Invoice", "Journal Entry", "Sales Order", "Sales Invoice", "Delivery Note"] :
					["Purchase Order", "Purchase Invoice", "Payment Entry", "Journal Entry"];
				const key = source?.doctype + ":" + source?.document?.name;
				if (!allowed.includes(source?.doctype) || !this.document(source.document, scope.company) || seen.has(key) ||
					companion && (source.balance_included !== false || source.amounts_allocated_to_items !== false)) return false;
				if (!companion && (["Purchase Order", "Purchase Invoice"].includes(source.doctype) && source.document.supplier !== scope.supplier ||
					source.doctype === "Payment Entry" && (source.document.party_type !== "Supplier" || source.document.party !== scope.supplier))) return false;
				seen.add(key);
				const field = source.doctype === "Payment Entry" ? "references" : source.doctype === "Journal Entry" ? "accounts" : "items";
				if (!this.childRows(source[field], source.document, source.doctype, field, new Set())) return false;
			}
		}
		return true;
	}
	display(value) { return value === null || value === undefined ? "—" : String(value); }
	labels() {
		return { name: "单号", docstatus: "审核状态", company: "公司", supplier: "供应商", customer: "客户",
			transaction_date: "采购日期", posting_date: "记账日期", due_date: "到期日", currency: "交易币种", supplier_currency: "供应商币种",
			status: "原生状态", conversion_rate: "汇率", per_received: "收货百分比", per_billed: "开票百分比", is_subcontracted: "委外标记",
			is_old_subcontracting_flow: "旧委外标记", is_return: "退货标记", return_against: "退货原单", update_stock: "更新库存",
			grand_total: "整凭证总额（交易币种）", base_grand_total: "整凭证总额（公司币种）", net_total: "整凭证净额（交易币种）",
			base_net_total: "整凭证净额（公司币种）", rounded_total: "整凭证圆整总额（交易币种）", base_rounded_total: "整凭证圆整总额（公司币种）",
			outstanding_amount: "当前整张发票余额（应付账户币种）", party_account_currency: "往来账户币种", credit_to: "应付账户", debit_to: "应收账户",
			purchase_order: "采购单", purchase_receipt: "收货单", purchase_invoice: "采购发票", subcontracting_order: "委外单", subcontracting_receipt: "委外收货单",
			reference_name: "引用单号", valuation_type: "估价类型",
			item_code: "商品", item_name: "商品名称", fg_item: "成品商品", fg_item_qty: "成品数量", qty: "原生数量", uom: "交易单位", stock_uom: "库存单位",
			stock_qty: "库存数量", conversion_factor: "单位换算系数", subcontracting_conversion_factor: "委外换算系数", received_qty: "原生已收数量",
			returned_qty: "原生退货数量", rejected_qty: "原生拒收数量", process_loss_qty: "原生损耗数量", return_qty_from_rejected_warehouse: "拒收仓退货数量",
			rate: "原生单价", base_rate: "公司币种单价", amount: "原生金额", base_amount: "公司币种金额", net_amount: "原生净额", base_net_amount: "公司币种净额",
			billed_amt: "原生已开票金额", service_cost_per_qty: "单位服务成本", rm_cost_per_qty: "单位原料成本", additional_cost_per_qty: "单位附加成本",
			warehouse: "仓库", rejected_warehouse: "拒收仓库", jf_flow_order: "流程单", total_qty: "整单数量", total: "整单总额",
			idx: "行号", party_type: "往来类型", party: "往来对象", payment_type: "付款类型",
			paid_from: "付款账户", paid_to: "收款账户", paid_from_account_currency: "付款账户币种", paid_to_account_currency: "收款账户币种",
			paid_amount: "付款额（付款账户币种）", received_amount: "收款额（收款账户币种）", base_paid_amount: "付款额（公司币种）", base_received_amount: "收款额（公司币种）",
			source_exchange_rate: "付款账户汇率", target_exchange_rate: "收款账户汇率", reference_doctype: "引用类型", reference_type: "引用类型",
			allocated_amount: "原生凭证分配额（非采购行分摊）", exchange_rate: "原生引用汇率", account: "账户", account_type: "账户类型", account_currency: "账户币种",
			debit_in_account_currency: "借方（账户币种）", credit_in_account_currency: "贷方（账户币种）", cost_center: "成本中心", voucher_type: "凭证类型",
			voucher_no: "凭证单号", against_voucher_type: "对冲凭证类型", against_voucher_no: "对冲单号", amount_in_account_currency: "金额（账户币种）",
			delinked: "解除关联", jf_expense_voucher: "费用单", multi_currency: "多币种", sales_order: "销售订单",
			delivery_note: "交货单", against_sales_order: "销售订单", against_sales_invoice: "销售发票" };
	}
	businessValue(field, value) {
		if (field === "docstatus") return __({ 0: "草稿", 1: "已提交", 2: "已取消" }[value] || "未知状态");
		if (["is_return", "is_subcontracted", "is_old_subcontracting_flow", "update_stock", "multi_currency", "delinked"].includes(field) && [0, 1, false, true].includes(value)) return __(value ? "是" : "否");
		return this.display(value);
	}
	table(rows, label, preferred = [], overrides = {}) {
		const wrap = this.element("div", undefined, "jfe-procurement-scroll"), table = this.element("table"), labels = this.labels();
		table.setAttribute("aria-label", __(label));
		const visible = new Set(rows.flatMap((row) => Object.keys(row)));
		const fields = [...new Set(preferred)].filter((field) => visible.has(field) && labels[field]);
		const head = this.element("thead"), tr = this.element("tr");
		for (const field of fields) tr.append(this.element("th", __(overrides[field] || labels[field])));
		head.append(tr); const body = this.element("tbody");
		for (const row of rows) { const line = this.element("tr"); for (const field of fields) line.append(this.element("td", this.businessValue(field, row[field]))); body.append(line); }
		table.append(head, body); wrap.append(table); return wrap;
	}
	summary(doc) {
		const summary = this.element("dl", undefined, "jfe-procurement-summary"), labels = this.labels();
		for (const field of ["name", "transaction_date", "posting_date", "docstatus", "status", "company", "supplier", "customer", "party_type", "party", "payment_type", "due_date",
			"currency", "supplier_currency", "party_account_currency", "conversion_rate", "per_received", "per_billed", "is_return", "return_against", "purchase_order", "subcontracting_receipt",
			"credit_to", "debit_to", "paid_from", "paid_to", "paid_from_account_currency", "paid_to_account_currency", "voucher_type", "jf_expense_voucher"]) {
			if (!(field in doc)) continue;
			const pair = this.element("div"); pair.append(this.element("dt", __(labels[field])), this.element("dd", this.businessValue(field, doc[field]))); summary.append(pair);
		}
		return summary;
	}
	rowTables(rows, label, kind = "items") {
		const content = this.element("div"), groups = [
			["数量与单位", ["qty", "uom", "stock_qty", "stock_uom", "conversion_factor", "fg_item", "fg_item_qty", "subcontracting_conversion_factor"]],
			["收发进度与仓库", ["received_qty", "returned_qty", "rejected_qty", "process_loss_qty", "return_qty_from_rejected_warehouse", "warehouse", "rejected_warehouse"]],
			["金额与成本", ["rate", "amount", "base_rate", "base_amount", "net_amount", "base_net_amount", "billed_amt", "service_cost_per_qty", "rm_cost_per_qty", "additional_cost_per_qty"]],
			["来源单据", ["purchase_order", "purchase_receipt", "purchase_invoice", "subcontracting_order", "subcontracting_receipt", "sales_order", "delivery_note", "against_sales_order", "against_sales_invoice", "jf_flow_order"]],
			...(kind === "references" ? [["付款引用", ["reference_doctype", "reference_name", "allocated_amount", "account", "exchange_rate"]]] : []),
			...(kind === "accounts" ? [["记账科目", ["account", "party_type", "party", "account_currency", "debit_in_account_currency", "credit_in_account_currency", "cost_center", "reference_type", "reference_name"]]] : [])
		];
		let rendered = false;
		for (const [title, fields] of groups) {
			if (!fields.some((field) => rows.some((row) => field in row))) continue;
			content.append(this.element("p", __(title), "jfe-procurement-group-title"), this.table(rows, label + "·" + title, ["idx", "item_code", "item_name", ...fields])); rendered = true;
		}
		if (!rendered) content.append(this.table(rows, label, ["idx", "item_code", "item_name"]));
		return content;
	}
	renderList(revision, scope) {
		for (const row of this.rows) {
			const line = this.element("div", undefined, "jfe-procurement-list-row");
			line.append(this.element("strong", row.name), this.element("span", row.transaction_date), this.element("span", this.display(row.status)),
				this.element("span", __("交易币种：") + this.display(row.currency)),
				this.button("查看完整来源", () => { this.syncScope(); if (this.current(revision, scope)) this.readDetail(row.name); })); this.list.append(line);
		}
	}
	nativeButton(doctype, name, revision, scope, detailRevision) {
		return this.button("打开原生凭证", () => {
			this.syncScope(); if (!this.current(revision, scope, detailRevision)) return;
			if (typeof this.bridge?.open !== "function") { this.status.textContent = __("原生凭证入口尚未就绪，请重新打开本工作台。"); return; }
			this.bridge.open({ title: this.doctypeLabel(doctype) + "：" + name, module: this.module, kind: "form", doctype, route: ["Form", doctype, name] });
		});
	}
	hasOutstanding(value) {
		if (typeof value === "number") return Number.isFinite(value) && value !== 0;
		// Preserve decimal precision: eligibility only, never a mapped amount.
		return typeof value === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) &&
			/[1-9]/.test(value.split(/[eE]/)[0]);
	}
	paymentEligible(doc) { return doc.docstatus === 1 && !doc.on_hold && this.hasOutstanding(doc.outstanding_amount); }
	async openPayment(name, revision, scope, detailRevision) {
		this.syncScope();
		if (!this.current(revision, scope, detailRevision) || !this.detail || typeof this.bridge?.openSupplierPayment !== "function") return;
		const sources = [...this.detail.purchase_invoices, ...this.detail.payables.voucher_sources.filter(source => source.doctype === "Purchase Invoice")];
		if (!sources.some(source => source.document.name === name && source.document.company === scope.company &&
			source.document.supplier === scope.supplier && this.paymentEligible(source.document))) return;
		const identity = { company: scope.company, supplier: scope.supplier, name };
		// Native PI reload/permissions/current balance own the mapping and amount.
		this.invalidate("正在打开原生收付款入口；请在原生草稿中核对、保存和审核。返回后请重新查询余额与付款来源。");
		const actionRevision = this.revision;
		const unresolved = () => {
			if (this.current(actionRevision, scope)) this.status.textContent = __("未确认原生收付款草稿是否打开，请先核对原生窗口、单据与权限。返回后重新查询，不会自动重试。");
		};
		try { if (await this.bridge.openSupplierPayment(identity) === false) unresolved(); }
		catch { unresolved(); }
	}
	doctypeLabel(doctype) {
		return { "Purchase Order": "采购单", "Purchase Receipt": "采购收货单", "Purchase Invoice": "采购发票", "Subcontracting Order": "委外采购单",
			"Subcontracting Receipt": "委外收货单", "Payment Entry": "收付款凭证", "Journal Entry": "记账凭证", "Sales Order": "销售订单",
			"Sales Invoice": "销售发票", "Delivery Note": "交货单" }[doctype] || doctype;
	}
	card(doctype, source, revision, scope, detailRevision, companion = false) {
		const card = this.element("article", undefined, "jfe-procurement-card"), doc = source.document;
		card.append(this.element("h4", this.doctypeLabel(doctype) + "：" + doc.name), this.nativeButton(doctype, doc.name, revision, scope, detailRevision));
		if (!companion && doctype === "Purchase Invoice" && this.paymentEligible(doc) && typeof this.bridge?.openSupplierPayment === "function")
			card.append(this.button("原生收付款草稿", () => this.openPayment(doc.name, revision, scope, detailRevision)));
		if (companion) card.append(this.element("p", __("其他往来关联来源；不计本供应商余额，不展示其金额。"), "jfe-procurement-note"));
		else card.append(this.element("p", __("整凭证范围；金额不分摊到当前采购行。" ) + (source.valuation_currency ? __(" 估价币种：") + source.valuation_currency : ""), "jfe-procurement-note"));
		card.append(this.summary(doc));
		const amounts = ["grand_total", "base_grand_total", "net_total", "base_net_total", "rounded_total", "base_rounded_total", "outstanding_amount", "paid_amount", "received_amount", "base_paid_amount", "base_received_amount", "total_qty", "total"];
		if (amounts.some((field) => field in doc)) card.append(this.element("h5", __("整凭证金额/数量")), this.table([doc], "整凭证金额/数量", amounts));
		for (const [key, label] of [["items", "本来源明细"], ["other_items", "其他采购单明细（不并入本采购单）"],
			["unlinked_items", "未关联采购单的明细"], ["service_items", "整张委外单服务明细"], ["references", "整张付款凭证引用"], ["accounts", "整张记账凭证账户行"]]) {
			if (!Array.isArray(source[key])) continue;
			card.append(this.element("h5", __(label)));
			card.append(source[key].length ? this.rowTables(source[key], label, key) : this.element("p", __("无此类子行。")));
		}
		return card;
	}
	renderDetail(data, revision, scope, detailRevision) {
		this.detailHost.append(this.element("h3", __("所选采购单：") + data.purchase_order.name),
			this.element("p", __("公司币种：") + data.company_currency + __("；同款不同子行、单位和币种保持独立。")),
			this.card("Purchase Order", { document: data.purchase_order, items: data.items }, revision, scope, detailRevision));
		for (const [group, label, doctype] of [["purchase_receipts", "收货及退货", "Purchase Receipt"], ["subcontracting_orders", "委外采购", "Subcontracting Order"],
			["subcontracting_receipts", "委外收货及退货", "Subcontracting Receipt"], ["purchase_invoices", "采购发票及退货发票", "Purchase Invoice"]]) {
			this.detailHost.append(this.element("h3", __(label)));
			if (!data[group].length) this.detailHost.append(this.element("p", __("没有关联凭证。")));
			for (const source of data[group]) this.detailHost.append(this.card(doctype, source, revision, scope, detailRevision));
		}
		const payables = data.payables;
		this.detailHost.append(this.element("h3", __("原生付款及当前应付核对")), this.element("p", __("当前发票余额按整张发票应付账户币种显示；付款和记账凭证保留全部引用，不推算采购行净额。"), "jfe-procurement-note"));
		for (const source of payables.voucher_sources) this.detailHost.append(this.card(source.doctype, source, revision, scope, detailRevision));
		this.detailHost.append(this.element("h4", __("原生付款分类账（不重新汇总）")));
		this.detailHost.append(payables.ledger_entries.length ? this.table(payables.ledger_entries, "原生付款分类账", ["posting_date", "voucher_type", "voucher_no", "against_voucher_type", "against_voucher_no", "account", "account_currency", "amount_in_account_currency", "amount", "delinked"], { amount: "金额（公司币种）" }) : this.element("p", __("没有关联付款分类账记录。")));
		this.detailHost.append(this.element("h4", __("其他往来关联来源（不计本供应商余额）")));
		if (!payables.other_reference_sources.length) this.detailHost.append(this.element("p", __("没有其他往来关联来源。")));
		for (const source of payables.other_reference_sources) this.detailHost.append(this.card(source.doctype, source, revision, scope, detailRevision, true));
	}
	isDirty() { return false; }
	getCloseBlock() { return ""; }
	setVisible(visible) {
		if (this.disposed) return;
		visible = Boolean(visible);
		if (this.visible && !visible) this.invalidate("已离开核对页，返回后请重新查询。");
		this.visible = visible;
	}
	destroy() {
		if (this.disposed) return;
		this.disposed = true; this.revision++; this.detailRevision++; this.clearResults();
		for (const control of Object.values(this.controls)) {
			control.$input?.off?.(".jifeng-procurement"); control.$link_clear?.off?.(".jifeng-procurement");
		}
	}
};
