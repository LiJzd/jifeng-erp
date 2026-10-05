// Shared A/B source contract and DOM models; no browser, database or network.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this.value = ""; this._text = ""; this.classList = { add() {} }; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Source data must not be HTML"); }
	append(...nodes) { this.children.push(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(key, value) { this.attributes[key] = value; }
	addEventListener(name, fn) { this.events.set(name, fn); }
	all(tag) { return this.children.flatMap((child) => [...(child.tag === tag ? [child] : []), ...child.all(tag)]); }
}
const calls = [], opened = [], controls = [];
const frappe = { call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
	calls.push({ options, resolve: (message) => resolve({ message }), reject }); return promise;
}, ui: { form: { make_control({ df }) { const handlers = {}, control = { value: "", typed: "", df, handlers, get_value() { return this.value; },
	get_input_value() { return this.title_value_map?.[this.typed] || this.typed; },
	$input: { val() { return control.typed; }, on(events, callback) { for (const event of events.split(" ")) handlers[event.split(".")[0]] = callback; }, off() { control.detached = true; } },
	$link_clear: { on(events, callback) { control.clearHandler = callback; }, off() { control.clearHandler = null; } },
	clear() { this.typed = ""; this.clearHandler?.(); },
	type(value, notify = true) { this.typed = value; if (notify) handlers.input?.(); },
	set(value, notify = true) { this.value = this.typed = value; if (notify) df.onchange(); } }; controls.push(control); return control; } } } };
const window = {}, document = { createElement: (tag) => new Element(tag) };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_procurement_fulfillment.js"), "utf8"), {
	window, document, frappe, __: (value) => value, localStorage: { setItem() { throw new Error("Do not persist business DTO"); }, getItem() { throw new Error("Do not load business DTO"); } }
});
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_classic_bridge.js"), "utf8"), { window, document, frappe, __: (value) => value });
const clone = (value) => JSON.parse(JSON.stringify(value));
const doc = (name, extra = {}) => ({ name, company: "JF", modified: "2026-10-04T14:15:16.123456", docstatus: 1, ...extra });
function row(doctype, parent, name, extra = {}, field = "items") {
	return { name, parent, parenttype: doctype, parentfield: field, idx: 1, modified: "2026-10-04T14:15:16.123456", ...extra };
}
function fixture(name = "PO-A") {
	const order = doc(name, { supplier: "SUP", transaction_date: "2026-10-01", currency: "USD", grand_total: "9007199254740993.000000000001", base_grand_total: "63050394783186951.000000000007", status: "To Receive and Bill" });
	const items = [row("Purchase Order", name, name + "-first", { item_code: "SAME", qty: "10.00", uom: "Pair", stock_qty: "20.00", stock_uom: "Nos", conversion_factor: "2", rate: "0.00", amount: "0.00", received_qty: "4", returned_qty: "0" }),
		row("Purchase Order", name, name + "-second", { item_code: "SAME", qty: "5", uom: "Set", stock_qty: "15", stock_uom: "Nos", conversion_factor: "3", rate: "2.00", amount: "10.00", idx: 2 })];
	const invoice = { document: doc("PI-A", { supplier: "SUP", currency: "USD", party_account_currency: "USD", posting_date: "2026-11-10", outstanding_amount: "17.250000000001", grand_total: "42.00", base_grand_total: "294.00", status: "Unpaid" }),
		items: [row("Purchase Invoice", "PI-A", "PI-exact-child", { item_code: "SAME", qty: "6", uom: "Pair", stock_qty: "12", stock_uom: "Nos", po_detail: name + "-first", purchase_order: name })],
		other_items: [row("Purchase Invoice", "PI-A", "PI-other-child", { item_code: "SAME", purchase_order: "PO-OTHER", po_detail: "PO-OTHER-exact" })],
		unlinked_items: [row("Purchase Invoice", "PI-A", "PI-unlinked-child", { item_code: "FREIGHT", purchase_order: null, po_detail: null })], valuation_currency: "USD", amount_scope: "whole_voucher", amounts_allocated_to_items: false };
	const returned = { document: doc("PR-RETURN", { supplier: "SUP", posting_date: "2026-09-20", currency: "USD", is_return: 1, return_against: "PR-OLD", docstatus: 2 }),
		items: [row("Purchase Receipt", "PR-RETURN", "RETURN-child", { item_code: "SAME", qty: "-2.00", uom: "Pair", stock_qty: "-4.00", stock_uom: "Nos", purchase_order: name, purchase_order_item: name + "-first", purchase_receipt_item: "PR-ORIGIN-CHILD" })],
		other_items: [], unlinked_items: [], amount_scope: "whole_voucher", amounts_allocated_to_items: false, valuation_currency: "USD" };
	const scr = { document: doc("SCR-A", { supplier: "SUP", posting_date: "2026-11-20", is_return: 0, return_against: null }), items: [row("Subcontracting Receipt", "SCR-A", "SCR-child", { item_code: "SAME", qty: "3", stock_uom: "Nos", purchase_order: name, purchase_order_item: name + "-second", rm_cost_per_qty: "7.25", reference_name: "SCR-MATERIAL-TECH-ID" })], other_items: [], unlinked_items: [], valuation_currency: "CNY", amount_scope: "whole_voucher", amounts_allocated_to_items: false };
	return { company: "JF", company_currency: "CNY", supplier: "SUP", purchase_order: order, items,
		purchase_receipts: [returned], purchase_invoices: [invoice], subcontracting_orders: [], subcontracting_receipts: [scr],
		payables: { amount_scope: "whole_voucher", balance_basis: "native_current_outstanding", amounts_allocated_to_items: false,
			ledger_entries: [{ name: "PLE-A", company: "JF", party_type: "Supplier", party: "SUP", posting_date: "2026-11-10", account_currency: "USD", amount: "294.00", amount_in_account_currency: "42.00", voucher_type: "Purchase Invoice", voucher_no: "PI-A", against_voucher_type: "Purchase Order", against_voucher_no: name, delinked: 0 }],
			voucher_sources: [{ doctype: "Purchase Order", document: clone(order), items: clone(items) }, { doctype: "Purchase Invoice", document: clone(invoice.document), items: [...clone(invoice.items), ...clone(invoice.other_items), ...clone(invoice.unlinked_items)] },
				{ doctype: "Payment Entry", document: doc("PE-A", { party_type: "Supplier", party: "SUP", payment_type: "Pay", paid_from_account_currency: "CNY", paid_to_account_currency: "USD", paid_amount: "173.25", received_amount: "24.75" }), references: [row("Payment Entry", "PE-A", "PE-reference", { reference_doctype: "Purchase Invoice", reference_name: "PI-A", allocated_amount: "24.75" }, "references")] },
				{ doctype: "Journal Entry", document: doc("JE-A", { posting_date: "2026-11-13" }), accounts: [row("Journal Entry", "JE-A", "JE-account", { account: "AP", party_type: "Supplier", party: "SUP", reference_type: "Purchase Invoice", reference_name: "PI-A", account_currency: "USD", debit_in_account_currency: "24.75" }, "accounts")] }],
			other_reference_sources: [{ doctype: "Sales Invoice", document: doc("SI-OTHER", { customer: "CUSTOMER-OTHER", currency: "EUR" }), items: [row("Sales Invoice", "SI-OTHER", "SI-other-child", { item_code: "SAME", sales_order: "SO-OTHER", so_detail: "SO-OTHER-child" })], balance_included: false, amounts_allocated_to_items: false }] },
		readonly: true, can_write: false, quantities_aggregated: false, amounts_allocated_to_items: false };
}
async function make() {
	const bench = new window.JifengProcurementFulfillment(new Element(), { open: (spec) => opened.push(spec) }); await bench.ready;
	for (const [key, value] of Object.entries({ company: "JF", supplier: "SUP", from_date: "2026-10-01", to_date: "2026-10-31" })) bench.controls[key].set(value);
	return bench;
}
async function query(bench, data = [fixture()]) { const pending = bench.query(), call = calls.at(-1); call.resolve(data); await pending; return call; }
async function detail(bench, data = fixture(), name = "PO-A") { const pending = bench.readDetail(name), call = calls.at(-1); call.resolve(data); await pending; return call; }
let checks = 0;
async function check(name, fn) { await fn(); checks++; }
async function main() {
	await check("initial no automatic or unscoped query", async () => { const before = calls.length, bench = new window.JifengProcurementFulfillment(new Element(), {}); await bench.ready; await bench.query();
		assert.equal(calls.length, before); assert.match(bench.status.textContent, /公司.*供应商/); assert.equal(bench.isDirty(), false); assert.equal(bench.getCloseBlock(), ""); });
	const bench = await make();
	await check("unconfirmed company or supplier text cannot query the previously selected ID", async () => {
		for (const field of ["company", "supplier"]) {
			const view = await make(); await query(view); const before = calls.length, selected = view.controls[field].get_value();
			view.controls[field].type("OTHER-unconfirmed", false); await view.query();
			assert.equal(view.controls[field].get_value(), selected); assert.equal(view.values()[field], null);
			assert.equal(calls.length, before); assert.equal(view.rows.length, 0); assert.match(view.status.textContent, /公司.*供应商/);
		}
	});
	await check("native title mapping keeps the exact selected company and supplier IDs", async () => {
		const view = await make();
		for (const [field, title] of [["company", "公司标题"], ["supplier", "供应商标题"]]) {
			const control = view.controls[field]; control.title_value_map = { [title]: control.get_value() }; control.type(title, false);
		}
		const call = await query(view); assert.equal(call.options.args.company, "JF"); assert.equal(call.options.args.supplier, "SUP");
		assert.equal(view.rows[0].name, "PO-A"); await detail(view); assert.equal(view.detail.purchase_order.name, "PO-A");
	});
	await check("silent unconfirmed Link text discards a pending list response", async () => {
		for (const field of ["company", "supplier"]) {
			const view = await make(), pending = view.query(), call = calls.at(-1);
			view.controls[field].type("OTHER-unconfirmed", false); call.resolve([fixture()]); await pending;
			assert.equal(view.rows.length, 0); assert.equal(view.list.children.length, 0); assert.equal(view.scope[field], null);
		}
	});
	await check("silent unconfirmed Link text discards a pending detail response", async () => {
		for (const field of ["company", "supplier"]) {
			const view = await make(); await query(view); const pending = view.readDetail("PO-A"), call = calls.at(-1);
			view.controls[field].type("OTHER-unconfirmed", false); call.resolve(fixture()); await pending;
			assert.equal(view.rows.length, 0); assert.equal(view.detail, null); assert.equal(view.detailHost.children.length, 0);
		}
	});
	await check("unconfirmed Link text blocks held list and native source buttons", async () => {
		for (const field of ["company", "supplier"]) {
			const view = await make(); await query(view); await detail(view);
			const listButton = view.list.all("button")[0], sourceButton = view.detailHost.all("button")[0];
			const beforeCalls = calls.length, beforeOpened = opened.length;
			view.controls[field].type("OTHER-unconfirmed", false); sourceButton.events.get("click")(); listButton.events.get("click")();
			assert.equal(calls.length, beforeCalls); assert.equal(opened.length, beforeOpened); assert.equal(view.rows.length, 0); assert.equal(view.detail, null);
		}
	});
	await check("raw input fallback also rejects an unconfirmed Link without an input getter", async () => {
		const view = await make(); delete view.controls.supplier.get_input_value;
		view.controls.supplier.type("OTHER-unconfirmed", false); const before = calls.length; await view.query(); assert.equal(calls.length, before);
	});
	await check("native Link clear removes list/details immediately without input/change and rejects late reads", async () => {
		for (const field of ["company", "supplier"]) {
			const view = await make(); await query(view); await detail(view); const before = calls.length;
			const selected = view.controls[field].get_value(); view.controls[field].clear();
			assert.equal(view.controls[field].get_value(), selected); assert.equal(view.values()[field], null);
			assert.equal(view.rows.length, 0); assert.equal(view.detail, null);
			assert.equal(view.list.children.length, 0); assert.equal(view.detailHost.children.length, 0); assert.equal(calls.length, before);
			view.controls[field].set(field === "company" ? "JF" : "SUP");
			const pending = view.query(), call = calls.at(-1); view.controls[field].clear(); call.resolve([fixture()]); await pending;
			assert.equal(view.rows.length, 0); view.destroy(); assert.ok(Object.values(view.controls).every(c => c.clearHandler === null));
		}
	});
	await check("exact GET request and separate fresh details", async () => { bench.search.value = "PO-"; const call = await query(bench); assert.equal(call.options.type, "GET");
		assert.equal(call.options.method, "jifeng_erp.procurement_fulfillment_workbench.query_procurement_sources");
		assert.equal(JSON.stringify(call.options.args), JSON.stringify({ company: "JF", supplier: "SUP", from_date: "2026-10-01", to_date: "2026-10-31", search: "PO-" }));
		assert.equal(bench.detail, null); assert.equal(bench.rows.length, 1); assert.equal("items" in bench.rows[0], false);
		const read = await detail(bench); assert.equal(read.options.type, "GET"); assert.equal(read.options.method, "jifeng_erp.procurement_fulfillment_workbench.read_procurement_source");
		assert.equal(JSON.stringify(read.options.args), JSON.stringify({ company: "JF", purchase_order: "PO-A" })); });
	await check("row identity, units and exact strings without aggregates", async () => { const text = bench.detailHost.textContent;
		for (const value of ["Pair", "Set", "20.00", "15", "0.00", "9007199254740993.000000000001", "17.250000000001", "CNY", "USD"]) assert.ok(text.includes(value), value);
		assert.equal(bench.detail.items.length, 2); assert.equal(bench.detail.items[0].name, "PO-A-first"); assert.equal(bench.detail.items[1].name, "PO-A-second");
		const stock = bench.detailHost.all("article")[0].all("table").find((table) => table.attributes["aria-label"] === "本来源明细·数量与单位");
		assert.equal(stock.all("tbody")[0].children.length, 2); assert.match(text, /当前整张发票余额/); assert.match(text, /不重新汇总/); });
	await check("cross dates, cancelled returns, other and unlinked sources remain", async () => { const text = bench.detailHost.textContent;
		for (const value of ["2026-11-10", "2026-09-20", "2026-11-20", "-2.00", "PR-OLD", "PO-OTHER", "其他采购单明细", "未关联采购单的明细", "估价币种：CNY", "CUSTOMER-OTHER", "不计本供应商余额", "已取消"]) assert.ok(text.includes(value), value); });
	await check("native subcontract service rows remain distinct from stock receipts", async () => {
		const data = fixture(); data.subcontracting_orders = [{ document: doc("SCO-A", { supplier: "SUP", transaction_date: "2026-10-02", supplier_currency: "USD", purchase_order: "PO-A" }),
			items: [row("Subcontracting Order", "SCO-A", "SCO-stock-child", { item_code: "SAME", purchase_order_item: "PO-A-second", qty: "3", stock_uom: "Nos", conversion_factor: "1" })], other_items: [], unlinked_items: [],
			service_items: [row("Subcontracting Order", "SCO-A", "SCO-service-child", { item_code: "SERVICE", purchase_order_item: "PO-A-first", qty: "2", rate: "0.00", amount: "0.00" }, "service_items")],
			valuation_currency: "USD", amount_scope: "whole_voucher", amounts_allocated_to_items: false }];
		await detail(bench, data); const text = bench.detailHost.textContent; assert.equal(bench.detail.subcontracting_orders[0].items[0].name, "SCO-stock-child");
		assert.equal(bench.detail.subcontracting_orders[0].service_items[0].name, "SCO-service-child"); assert.ok(text.includes("SERVICE"));
		assert.ok(text.includes("整张委外单服务明细")); assert.ok(text.includes("供应商币种")); });
	await check("business groups hide technical IDs and retain readable Chinese state", async () => { const text = bench.detailHost.textContent;
		for (const value of ["PO-A-first", "PO-A-second", "PI-exact-child", "SCO-service-child", "SCR-MATERIAL-TECH-ID", "2026-10-04T14:15:16.123456", "读取版本", "父表字段", "准确ID", "parenttype", "parentfield", "0草稿/1提交/2取消"]) assert.equal(text.includes(value), false, value);
		for (const label of ["审核状态", "已提交", "已取消", "行号", "数量与单位", "收发进度与仓库", "金额与成本", "来源单据"]) assert.ok(text.includes(label), label);
		assert.ok(bench.detailHost.all("table").every((table) => table.all("th").length <= 13)); });
	await check("native route precise and filter return context retained", async () => { const filters = JSON.stringify(bench.values());
		const card = bench.detailHost.all("article").find((node) => node.textContent.includes("收付款凭证：PE-A")); card.all("button")[0].events.get("click")();
		assert.equal(JSON.stringify(opened.at(-1).route), JSON.stringify(["Form", "Payment Entry", "PE-A"])); assert.equal(opened.at(-1).kind, "form"); assert.equal(JSON.stringify(bench.values()), filters); });
	await check("actual bridge mount and source return preserve both material modules", async () => {
		for (const module of ["物料", "材料"]) {
			const tabs = [], bridge = { workspace: { addTab: (tab) => tabs.push(tab) }, open: window.JifengClassicBridge.prototype.open };
			const entry = window.JifengClassicBridge.prototype.mount.call(bridge, new Element(), { procurementFulfillment: true, module });
			const view = entry.workbench; await entry.ready;
			for (const [key, value] of Object.entries({ company: "JF", supplier: "SUP", from_date: "2026-10-01", to_date: "2026-10-31" })) view.controls[key].set(value);
			await query(view); await detail(view); const filters = JSON.stringify(view.values());
			view.detailHost.all("article")[0].all("button")[0].events.get("click")();
			assert.equal(tabs.at(-1).module, module); assert.equal(tabs.at(-1).spec.module, module);
			assert.equal(tabs.at(-1).key, "formal:" + module + ":采购单：PO-A");
			assert.equal(JSON.stringify(tabs.at(-1).spec.route), JSON.stringify(["Form", "Purchase Order", "PO-A"]));
			assert.equal(JSON.stringify(view.values()), filters); entry.stop(); assert.equal(view.disposed, true);
		}
		assert.equal(new window.JifengProcurementFulfillment(new Element(), {}).module, "物料");
		assert.equal(new window.JifengProcurementFulfillment(new Element(), {}, "采购履行").module, "物料");
	});
	await check("optional fields not filled from base or other currency", async () => { const data = fixture(); delete data.purchase_order.grand_total;
		delete data.payables.voucher_sources[0].document.grand_total; await detail(bench, data);
		const card = bench.detailHost.all("article")[0]; assert.equal(card.textContent.includes("9007199254740993.000000000001"), false); assert.ok(card.textContent.includes("63050394783186951.000000000007")); });
	await check("source text cannot create executable HTML", async () => { const data = fixture(); data.items[0].item_name = '<svg onload="alert(1)">原文</svg>'; await detail(bench, data);
		assert.ok(bench.detailHost.textContent.includes(data.items[0].item_name)); assert.equal(bench.detailHost.all("svg").length, 0); });
	await check("empty source result differs from permission failure", async () => { await query(bench, []); assert.match(bench.status.textContent, /没有可见采购单/);
		await query(bench); const pending = bench.readDetail("PO-A"); calls.at(-1).reject(new Error("private error ID")); await pending;
		assert.equal(bench.rows.length, 0); assert.equal(bench.detail, null); assert.equal(bench.detailHost.children.length, 0); assert.match(bench.status.textContent, /清空全部/); assert.equal(bench.status.textContent.includes("private error"), false); });
	await check("date validation blocks normalized dates and reverse range", async () => { for (const [start, end] of [["2026-02-30", "2026-03-01"], ["2026-11-01", "2026-10-01"], ["2026-1-1", "2026-10-01"], ["", "2026-10-01"]]) {
		bench.controls.from_date.set(start); bench.controls.to_date.set(end); const before = calls.length; await bench.query(); assert.equal(calls.length, before); }
		bench.controls.from_date.set("2026-10-01"); bench.controls.to_date.set("2026-10-31"); });
	await check("entire batch rejected if any exact scope or child identity wrong", async () => { for (const mutate of [(data) => { data.supplier = "OTHER"; }, (data) => { data.purchase_invoices[0].items[0].parenttype = "Sales Invoice"; },
		(data) => { data.purchase_invoices[0].other_items[0].name = data.purchase_invoices[0].items[0].name; }, (data) => { data.payables.other_reference_sources[0].balance_included = true; },
		(data) => { data.purchase_invoices[0].amounts_allocated_to_items = true; }, (data) => { data.purchase_order.transaction_date = "2026-11-01"; },
		(data) => { data.payables.voucher_sources[0].document.supplier = "OTHER"; }, (data) => { data.payables.voucher_sources[2].document.party = "OTHER"; }]) {
		const data = fixture("PO-B"); mutate(data); await query(bench, [fixture(), data]); assert.equal(bench.rows.length, 0); assert.equal(bench.list.children.length, 0); assert.match(bench.status.textContent, /部分结果/); } });
	await check("no truncation or duplicate PO silently accepted", async () => { await query(bench, Array.from({ length: 21 }, (_, index) => fixture("PO-" + index))); assert.equal(bench.rows.length, 0);
		await query(bench, [fixture(), fixture()]); assert.equal(bench.rows.length, 0); });
	await check("exact fresh identity mismatch clears list and details", async () => { await query(bench); await detail(bench, fixture("PO-B")); assert.equal(bench.rows.length, 0); assert.equal(bench.detail, null); });
	await check("silent criteria change never paints older response", async () => { const pending = bench.query(), call = calls.at(-1); bench.controls.company.set("OTHER", false); call.resolve([fixture()]); await pending;
		assert.equal(bench.rows.length, 0); assert.equal(bench.scope.company, "OTHER"); bench.controls.company.set("JF"); });
	await check("old list click clears silent supplier change instead of querying", async () => { await query(bench); const old = bench.list.all("button")[0], before = calls.length;
		bench.controls.supplier.set("OTHER", false); old.events.get("click")(); assert.equal(bench.rows.length, 0); assert.equal(calls.length, before); bench.controls.supplier.set("SUP"); });
	await check("out of order queries cannot restore prior source", async () => { const first = bench.query(), older = calls.at(-1), second = bench.query(), newer = calls.at(-1);
		newer.resolve([fixture("PO-B")]); await second; older.resolve([fixture()]); await first; assert.equal(bench.rows[0].name, "PO-B"); });
	await check("out of order exact detail reads remain selected identity", async () => { await query(bench, [fixture(), fixture("PO-B")]); const first = bench.readDetail("PO-A"), older = calls.at(-1), second = bench.readDetail("PO-B"), newer = calls.at(-1);
		newer.resolve(fixture("PO-B")); await second; older.resolve(fixture()); await first; assert.equal(bench.detail.purchase_order.name, "PO-B"); });
	await check("input clearing removes data without an automatic request", async () => { const before = calls.length; bench.search.value = "different"; bench.search.events.get("input")(); assert.equal(bench.rows.length, 0); assert.equal(bench.detail, null); assert.equal(calls.length, before); });
	await check("pending list hidden and returned cannot repaint", async () => { const pending = bench.query(), call = calls.at(-1), filters = JSON.stringify(bench.values()); bench.setVisible(false); bench.setVisible(true);
		call.resolve([fixture()]); await pending; assert.equal(bench.rows.length, 0); assert.equal(JSON.stringify(bench.values()), filters); assert.match(bench.status.textContent, /返回后请重新查询/); });
	await check("pending detail leaving route clears all data and stale native clicks", async () => { await query(bench); await detail(bench); const oldButton = bench.detailHost.all("button")[0], pending = bench.readDetail("PO-A"), call = calls.at(-1), before = opened.length;
		bench.setVisible(false); oldButton.events.get("click")(); assert.equal(opened.length, before); assert.equal(bench.rows.length, 0); bench.setVisible(true); call.resolve(fixture()); await pending; assert.equal(bench.detail, null); });
	await check("hidden workbench cannot read and visibility restore no auto query", async () => { const before = calls.length; bench.setVisible(false); await bench.query(); await bench.readDetail("PO-A"); bench.setVisible(true); assert.equal(calls.length, before); });
	await check("destroy blocks late replies and releases handlers", async () => { const pending = bench.query(), call = calls.at(-1); bench.destroy(); call.resolve([fixture()]); await pending; bench.setVisible(true); await bench.query();
		assert.equal(bench.rows.length, 0); assert.equal(bench.disposed, true); assert.ok(Object.values(bench.controls).every((control) => control.detached)); });
	assert.ok(calls.every((call) => call.options.type === "GET"));
	console.log(`procurement fulfillment: ${checks} meaningful contract/DOM models passed; no GUI/native/network proof`);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
