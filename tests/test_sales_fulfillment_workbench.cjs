// Shared source/DOM contracts, without browser, database or network.
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
const calls = [], opened = [];
const frappe = { call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
	calls.push({ options, resolve: (message) => resolve({ message }), reject }); return promise;
}, ui: { form: { make_control({ df }) { const handlers = {}, control = { value: "", typed: "", df, get_value() { return this.value; },
	get_input_value() { return this.title_value_map?.[this.typed] || this.typed; },
	$input: { val() { return control.typed; }, on(events, callback) { for (const event of events.split(" ")) handlers[event.split(".")[0]] = callback; }, off() { control.detached = true; } },
	$link_clear: { on(events, callback) { control.clearHandler = callback; }, off() { control.clearHandler = null; } },
	type(value, notify = true) { this.typed = value; if (notify) handlers.input?.(); },
	clear() { this.typed = ""; this.clearHandler?.(); }, set(value, notify = true) { this.value = this.typed = value; if (notify) df.onchange(); }
}; return control; } } } };
const window = {}, document = { createElement: (tag) => new Element(tag) };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_sales_fulfillment.js"), "utf8"), {
	window, document, frappe, __: (value) => value,
	localStorage: { setItem() { throw new Error("No business DTO persistence"); }, getItem() { throw new Error("No business DTO persistence"); } }
});
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_classic_bridge.js"), "utf8"), { window, document, frappe, __: (value) => value });
const clone = (value) => JSON.parse(JSON.stringify(value));
const doc = (name, extra = {}) => ({ name, company: "JF", modified: "2026-10-04T14:15:16.123456", docstatus: 1, ...extra });
const row = (doctype, parent, name, extra = {}) => ({ name, parent, parenttype: doctype, parentfield: "items", idx: 1,
	modified: "2026-10-04T14:15:16.123456", item_code: "SAME", ...extra });
function fixture(name = "SO-A", customer = "CUST") {
	const order = doc(name, { customer, transaction_date: "2026-10-01", delivery_date: "2026-12-15", currency: "EUR", status: "To Deliver and Bill", per_delivered: "30.000000000001", per_billed: "15" });
	const items = [row("Sales Order", name, name + "-first", { qty: "10.00", uom: "Pair", stock_qty: "20.00", stock_uom: "Nos", conversion_factor: "2", delivered_qty: "3", billed_amt: "0.00" }),
		row("Sales Order", name, name + "-second", { idx: 2, qty: "5", uom: "Set", stock_qty: "15", stock_uom: "Nos", conversion_factor: "3", delivered_qty: "0", billed_amt: "9007199254740993.000000000001" })];
	const flows = items.map((item, index) => doc(name + "-FLOW-" + index, { sales_order: name, sales_order_item: item.name, item_code: "SAME", source_item_code: "ORIGINAL-SAME", source_changed: 0, work_order: index ? null : name + "-WO", planned_qty: item.stock_qty,
		stock_uom: item.stock_uom, source_qty: item.qty, source_uom: item.uom, source_stock_uom: item.stock_uom, source_conversion_factor: item.conversion_factor }));
	const work = doc(name + "-WO", { sales_order: name, sales_order_item: items[0].name, production_item: "SAME", jf_flow_order: flows[0].name, qty: "20", produced_qty: "6", stock_uom: "Nos", status: "In Process" });
	const manufacture = { document: doc(name + "-SE", { purpose: "Manufacture", stock_entry_type: "Manufacture-EXACT", work_order: work.name, posting_date: "2026-11-03", fg_completed_qty: "6" }),
		finished_items: [row("Stock Entry", name + "-SE", name + "-SE-child", { qty: "6", transfer_qty: "6", stock_uom: "Nos", is_finished_item: 1, jf_flow_order: flows[0].name })] };
	const independent = { document: doc(name + "-SE-INDEPENDENT", { purpose: "Manufacture", work_order: null, posting_date: "2026-09-10", docstatus: 2, fg_completed_qty: "100" }),
		finished_items: [row("Stock Entry", name + "-SE-INDEPENDENT", name + "-SE-I-1", { qty: "2", stock_uom: "Nos", is_finished_item: true, jf_flow_order: flows[0].name }),
			row("Stock Entry", name + "-SE-INDEPENDENT", name + "-SE-I-2", { idx: 2, qty: "3", stock_uom: "Nos", is_finished_item: 1, jf_flow_order: flows[1].name })] };
	const delivery = { document: doc(name + "-DN-RETURN", { customer, is_return: 1, return_against: name + "-DN-OLD", posting_date: "2026-09-20", docstatus: 2, currency: "EUR" }),
		items: [row("Delivery Note", name + "-DN-RETURN", name + "-DN-child", { qty: "-2.00", uom: "Pair", stock_qty: "-4", stock_uom: "Nos", against_sales_order: name, so_detail: items[0].name })] };
	const invoice = { document: doc(name + "-SI", { customer, posting_date: "2026-11-10", currency: "EUR", party_account_currency: "USD", outstanding_amount: "17.250000000001", update_stock: 1, update_outstanding_for_self: 0, status: "Unpaid", is_return: 0 }),
		items: [row("Sales Invoice", name + "-SI", name + "-SI-child", { qty: "5", uom: "Set", stock_qty: "15", stock_uom: "Nos", sales_order: name, so_detail: items[1].name })] };
	return { company: "JF", sales_order: order, items, flows, work_orders: [{ document: work, manufacture_entries: [manufacture] }],
		independent_manufacture_entries: [independent], delivery_notes: [delivery], sales_invoices: [invoice],
		receivables: { ledger_entries: [{ name: name + "-PLE", company: "JF", party_type: "Customer", party: customer, posting_date: "2026-11-10", due_date: "2026-12-20", account_currency: "USD", amount: "294.00", amount_in_account_currency: "42.00", voucher_type: "Sales Invoice", voucher_no: name + "-SI", against_voucher_type: "Sales Order", against_voucher_no: name, delinked: 0 }],
			voucher_sources: [{ doctype: "Sales Order", document: clone(order) }, { doctype: "Sales Invoice", document: clone(invoice.document) },
				{ doctype: "Payment Entry", document: doc(name + "-PE", { party_type: "Customer", party: customer, payment_type: "Receive", paid_from_account_currency: "USD", paid_to_account_currency: "CNY", posting_date: "2026-11-12" }) },
				{ doctype: "Journal Entry", document: doc(name + "-JE", { posting_date: "2026-11-13" }) }] }, readonly: true, can_write: false, quantities_aggregated: false };
}
async function make() {
	const view = new window.JifengSalesFulfillment(new Element(), { open: (spec) => opened.push(spec) }); await view.ready;
	for (const [key, value] of Object.entries({ company: "JF", from_date: "2026-10-01", to_date: "2026-10-31" })) view.controls[key].set(value);
	return view;
}
async function query(view, data = [fixture()]) { const pending = view.query(), call = calls.at(-1); call.resolve(data); await pending; return call; }
async function detail(view, data = fixture()) { const pending = view.readDetail(view.rows[0].name), call = calls.at(-1); call.resolve(data); await pending; return call; }
let checks = 0;
async function check(_label, action) { await action(); checks++; }
async function main() {
	await check("query and detail use exact GET contracts and no hidden filters", async () => {
		const view = await make(); view.search.value = "SO-"; const listCall = await query(view);
		assert.deepEqual(clone(listCall.options), { method: "jifeng_erp.sales_fulfillment_workbench.query_orders", type: "GET", args: { company: "JF", from_date: "2026-10-01", to_date: "2026-10-31", search: "SO-" } });
		assert.equal(view.detail, null); const detailCall = await detail(view);
		assert.deepEqual(clone(detailCall.options.args), { company: "JF", sales_order: "SO-A" }); assert.equal(detailCall.options.method, "jifeng_erp.sales_fulfillment_workbench.read_order");
	});
	await check("per-line units and decimal text preserve source scope without date pruning", async () => {
		const view = await make(); await query(view); await detail(view); const text = view.detailHost.textContent;
		for (const value of ["Pair", "Set", "Nos", "9007199254740993.000000000001", "17.250000000001", "2026-09-20", "2026-11-10", "-2.00", "USD", "EUR", "CNY", "已取消"]) assert.ok(text.includes(value), value);
		assert.ok(view.host.textContent.includes("生产完成不等于发货")); assert.ok(text.includes("当前整张发票余额（应收账户币种）")); assert.ok(text.includes("不分摊为本订单净额"));
		assert.equal(view.detail.items.length, 2); assert.equal(view.detail.independent_manufacture_entries[0].finished_items.length, 2);
	});
	await check("same product source references distinguish exact order rows and their native units", async () => {
		const view = await make(); await query(view); await detail(view);
		const order = view.detailHost.all("article")[0], firstTable = order.all("table")[0];
		assert.deepEqual(firstTable.all("th").slice(0, 3).map((cell) => cell.textContent), ["行号", "明细标识", "商品"]);
		assert.ok(firstTable.textContent.includes("SO-A-first")); assert.ok(firstTable.textContent.includes("SO-A-second"));
		const first = "第 1 行 · 商品：SAME · 交易单位：Pair · 库存单位：Nos（明细标识：SO-A-first）";
		const second = "第 2 行 · 商品：SAME · 交易单位：Set · 库存单位：Nos（明细标识：SO-A-second）";
		assert.equal(view.businessValue("so_detail", "SO-A-first"), first); assert.equal(view.businessValue("sales_order_item", "SO-A-second"), second);
		const cards = view.detailHost.all("article");
		assert.ok(cards.find((card) => card.textContent.includes("交货单：SO-A-DN-RETURN")).textContent.includes(first));
		assert.ok(cards.find((card) => card.textContent.includes("销售发票：SO-A-SI")).textContent.includes(second));
		assert.ok(cards.find((card) => card.textContent.includes("流程单：SO-A-FLOW-0")).textContent.includes(first));
		assert.ok(cards.find((card) => card.textContent.includes("流程单：SO-A-FLOW-1")).textContent.includes(second));
		delete view.detail.items[0].uom; delete view.detail.items[0].stock_uom;
		assert.equal(view.businessValue("so_detail", "SO-A-first"), "第 1 行 · 商品：SAME（明细标识：SO-A-first）");
		assert.equal(view.businessValue("so_detail", "OTHER-ORDER-CHILD"), "OTHER-ORDER-CHILD");
	});
	await check("persisted business dates source item entry type and invoice balance flag are visible", async () => {
		const view = await make(); await query(view); await detail(view); const text = view.detailHost.textContent;
		for (const value of ["交货日期2026-12-15", "来源商品ORIGINAL-SAME", "库存凭证类型Manufacture-EXACT", "更新本发票余额否", "2026-12-20"]) assert.ok(text.includes(value), value);
		const ledger = view.detailHost.all("table").find((table) => table.attributes["aria-label"] === "原生收款分类账");
		assert.deepEqual(ledger.all("th").slice(0, 2).map((cell) => cell.textContent), ["记账日期", "到期日"]);
		assert.ok(ledger.textContent.includes("2026-12-20"));
	});
	await check("changed flow displays only native unit snapshots and never current SO defaults", async () => {
		const view = await make(), data = fixture(); Object.assign(data.flows[0], { source_changed: 1, planned_qty: "25", stock_uom: "Kg", source_qty: "10",
			source_uom: "Box", source_stock_uom: "Kg", source_conversion_factor: "2.500000000001" });
		for (const field of ["stock_uom", "source_uom", "source_stock_uom", "source_conversion_factor"]) delete data.flows[1][field]; data.flows[1].source_changed = 1;
		await query(view, [data]); await detail(view, data);
		const first = view.detailHost.all("article").find((card) => card.textContent.includes("流程单：SO-A-FLOW-0"));
		const second = view.detailHost.all("article").find((card) => card.textContent.includes("流程单：SO-A-FLOW-1"));
		const snapshot = first.all("table")[0]; assert.deepEqual(snapshot.all("th").map((cell) => cell.textContent),
			["流程计划库存量", "库存单位", "源订单交易量", "源订单交易单位快照", "源订单库存单位快照", "源订单单位换算系数快照"]);
		assert.deepEqual(snapshot.all("td").map((cell) => cell.textContent), ["25", "Kg", "10", "Box", "Kg", "2.500000000001"]);
		assert.ok(!first.textContent.includes("Pair")); assert.ok(!first.textContent.includes("Nos"));
		assert.ok(!second.textContent.includes("Set")); assert.ok(!second.textContent.includes("Nos"));
		assert.deepEqual(second.all("table")[0].all("th").map((cell) => cell.textContent), ["流程计划库存量", "源订单交易量"]);
		assert.ok(second.textContent.includes("明细标识：SO-A-second"));
	});
	await check("independent manufacture whole quantity is not a sum of selected order rows", async () => {
		const view = await make(); await query(view); await detail(view);
		const card = view.detailHost.all("article").find((entry) => entry.textContent.includes("制造入库凭证：SO-A-SE-INDEPENDENT"));
		const whole = card.all("table")[0], selected = card.all("table").find((table) => table.attributes["aria-label"] === "本订单准确来源明细·数量与单位");
		assert.deepEqual(whole.all("th").map((cell) => cell.textContent), ["整张制造凭证原生完成数量"]);
		assert.deepEqual(whole.all("td").map((cell) => cell.textContent), ["100"]);
		assert.ok(selected.textContent.includes("SO-A-SE-I-1")); assert.ok(selected.textContent.includes("SO-A-SE-I-2"));
		assert.equal(view.detail.independent_manufacture_entries[0].finished_items[0].qty, "2");
		assert.equal(view.detail.independent_manufacture_entries[0].finished_items[1].qty, "3");
		assert.equal(whole.textContent.includes("5"), false); assert.ok(card.textContent.includes("不按当前明细求和或分摊"));
	});
	await check("optional masked fields are not recovered or invented as zero", async () => {
		const view = await make(), data = fixture(); delete data.items[0].billed_amt; delete data.items[1].billed_amt; delete data.sales_order.currency;
		await query(view, [data]); await detail(view, data); assert.equal(view.detail.sales_order.currency, undefined);
		const tables = view.detailHost.all("table"); assert.ok(tables.every((table) => !table.textContent.includes("原生已开票金额")));
	});
	await check("two customers retain independent current sources", async () => {
		const view = await make(); await query(view, [fixture(), fixture("SO-B", "OTHER-CUST")]); assert.equal(view.rows.length, 2);
		const pending = view.readDetail("SO-B"); calls.at(-1).resolve(fixture("SO-B", "OTHER-CUST")); await pending; assert.equal(view.detail.sales_order.customer, "OTHER-CUST");
	});
	await check("hidden or failed source returns no partial business rows or private error", async () => {
		const view = await make(); await query(view); await detail(view); const pending = view.readDetail("SO-A"); calls.at(-1).reject(new Error("private source ID")); await pending;
		assert.equal(view.rows.length, 0); assert.equal(view.detail, null); assert.equal(view.detailHost.children.length, 0); assert.ok(!view.status.textContent.includes("private"));
	});
	await check("wrong precise scope in one order rejects the entire batch", async () => {
		for (const mutate of [(d) => { d.company = "OTHER"; }, (d) => { d.items[0].parenttype = "Purchase Order"; }, (d) => { d.flows[0].sales_order_item = "wrong-child"; },
			(d) => { d.flows[1].sales_order_item = d.flows[0].sales_order_item; }, (d) => { d.work_orders[0].document.sales_order = "OTHER"; },
			(d) => { d.independent_manufacture_entries[0].document.purpose = "Repack"; }, (d) => { d.independent_manufacture_entries[0].finished_items[0].jf_flow_order = "OTHER"; },
			(d) => { d.sales_invoices[0].document.customer = "OTHER"; }, (d) => { d.receivables.ledger_entries[0].party = "OTHER"; },
			(d) => { d.receivables.voucher_sources[2].document.party_type = "Supplier"; }, (d) => { d.receivables.voucher_sources[3].doctype = "Purchase Invoice"; },
			(d) => { d.quantities_aggregated = true; }, (d) => { d.sales_order.transaction_date = "2026-11-01"; }]) {
			const view = await make(), invalid = fixture("SO-B"); mutate(invalid); await query(view, [fixture(), invalid]);
			assert.equal(view.rows.length, 0); assert.equal(view.list.children.length, 0); assert.ok(view.status.textContent.includes("部分结果"));
		}
	});
	await check("duplicates and over-limit are rejected instead of truncated", async () => {
		for (const data of [[fixture(), fixture()], Array.from({ length: 21 }, (_, index) => fixture("SO-" + index))]) { const view = await make(); await query(view, data); assert.equal(view.rows.length, 0); }
		const view = await make(), data = fixture(); data.receivables.voucher_sources.push(clone(data.receivables.voucher_sources[0])); await query(view, [data]); assert.equal(view.rows.length, 0);
	});
	await check("source text is plain text and not interpreted as HTML", async () => {
		const view = await make(), data = fixture(); data.items[0].item_name = "<img src=x onerror=alert(1)>"; await query(view, [data]); await detail(view, data);
		assert.ok(view.detailHost.textContent.includes(data.items[0].item_name));
	});
	await check("native buttons open exact authorized source identities", async () => {
		const view = await make(); await query(view); await detail(view); const start = opened.length;
		for (const button of view.detailHost.all("button")) button.events.get("click")(); const specs = opened.slice(start);
		assert.ok(specs.some((spec) => spec.doctype === "Stock Entry" && spec.route[2] === "SO-A-SE-INDEPENDENT"));
		assert.ok(specs.some((spec) => spec.doctype === "Payment Entry" && spec.route[2] === "SO-A-PE"));
		assert.ok(specs.every((spec) => spec.module === "销售" && spec.kind === "form" && spec.route[0] === "Form" && spec.route[1] === spec.doctype));
	});
	await check("invalid and unselected identifiers never query detail", async () => {
		const view = await make(); await query(view); const before = calls.length; await view.readDetail("UNKNOWN"); assert.equal(calls.length, before);
		const pending = view.readDetail("SO-A"); calls.at(-1).resolve(fixture("SO-B")); await pending; assert.equal(view.rows.length, 0); assert.equal(view.detail, null);
	});
	await check("real calendar dates and reverse ranges prevent requests", async () => {
		for (const [from, to] of [["2026-02-30", "2026-03-01"], ["2026-11-01", "2026-10-01"], ["2026-1-1", "2026-10-01"], ["", "2026-10-01"]]) {
			const view = await make(); view.controls.from_date.set(from); view.controls.to_date.set(to); const before = calls.length; await view.query(); assert.equal(calls.length, before);
		}
	});
	await check("Link typed text cannot reuse previous company ID", async () => {
		const view = await make(); view.controls.company.type("unconfirmed"); const before = calls.length; await view.query(); assert.equal(calls.length, before);
		view.controls.company.set("JF"); view.controls.company.title_value_map = { "公司标题": "JF" }; view.controls.company.type("公司标题", false); await query(view); assert.equal(view.rows.length, 1);
		delete view.controls.company.get_input_value; view.controls.company.type("unconfirmed", false); await view.query(); assert.equal(view.rows.length, 0);
	});
	await check("pending responses and held buttons reject silent Link text changes", async () => {
		const view = await make(), pending = view.query(), old = calls.at(-1); view.controls.company.type("unconfirmed", false); old.resolve([fixture()]); await pending; assert.equal(view.rows.length, 0);
		view.controls.company.set("JF"); await query(view); await detail(view); const button = view.detailHost.all("button")[0], before = opened.length;
		view.controls.company.type("unconfirmed", false); button.events.get("click")(); assert.equal(opened.length, before); assert.equal(view.detail, null);
	});
	await check("Link clearing invalidates without relying on onchange", async () => {
		const view = await make(); await query(view); await detail(view); const before = calls.length; view.controls.company.clear();
		assert.equal(view.values().company, null); assert.equal(view.rows.length, 0); assert.equal(view.detail, null); assert.equal(calls.length, before);
	});
	await check("late query and detail responses cannot override latest selection", async () => {
		const view = await make(), first = view.query(), older = calls.at(-1), second = view.query(), newer = calls.at(-1);
		newer.resolve([fixture("SO-B")]); await second; older.resolve([fixture()]); await first; assert.equal(view.rows[0].name, "SO-B");
		await query(view, [fixture(), fixture("SO-B")]); const a = view.readDetail("SO-A"), old = calls.at(-1), b = view.readDetail("SO-B"), fresh = calls.at(-1);
		fresh.resolve(fixture("SO-B")); await b; old.resolve(fixture()); await a; assert.equal(view.detail.sales_order.name, "SO-B");
	});
	await check("criteria edits clear stale results and never auto-query", async () => {
		const view = await make(); await query(view); await detail(view); const before = calls.length; view.search.value = "different"; view.search.events.get("input")();
		assert.equal(view.rows.length, 0); assert.equal(view.detail, null); assert.equal(calls.length, before);
	});
	await check("route leave preserves criteria but clears data and blocks held source buttons", async () => {
		const view = await make(); await query(view); await detail(view); const button = view.detailHost.all("button")[0], before = opened.length, values = JSON.stringify(view.values());
		const pending = view.readDetail("SO-A"), old = calls.at(-1); view.setVisible(false); button.events.get("click")(); assert.equal(opened.length, before);
		view.setVisible(true); old.resolve(fixture()); await pending; assert.equal(view.detail, null); assert.equal(view.rows.length, 0); assert.equal(JSON.stringify(view.values()), values);
		assert.ok(view.status.textContent.includes("返回后请重新查询"));
	});
	await check("hidden or destroyed views never issue requests or restore results", async () => {
		const view = await make(); view.setVisible(false); const before = calls.length; await view.query(); await view.readDetail("SO-A"); assert.equal(calls.length, before);
		view.setVisible(true); const pending = view.query(), old = calls.at(-1); view.destroy(); old.resolve([fixture()]); await pending;
		assert.equal(view.rows.length, 0); assert.ok(Object.values(view.controls).every((control) => control.detached && control.clearHandler === null));
	});
	await check("both hosts mount the one sales component and honor shared lifecycle", async () => {
		const bridge = { disposed: false }, entry = window.JifengClassicBridge.prototype.mount.call(bridge, new Element(), { salesFulfillment: true, module: "销售" });
		const view = entry.workbench; assert.ok(view instanceof window.JifengSalesFulfillment); await entry.ready;
		for (const [key, value] of Object.entries({ company: "JF", from_date: "2026-10-01", to_date: "2026-10-31" })) view.controls[key].set(value);
		await query(view); await detail(view); view.setVisible(false); assert.equal(view.detail, null); assert.equal(view.rows.length, 0);
		entry.stop(); assert.equal(view.disposed, true);
	});
	assert.ok(calls.every((call) => call.options.type === "GET"));
	console.log(`sales fulfillment: ${checks} meaningful contract/DOM models passed; no GUI/native/network proof`);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
