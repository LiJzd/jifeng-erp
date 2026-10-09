// Isolated component contract test; this is not UI or server evidence.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");

class Element {
	constructor(tag) { this.tag = tag; this.children = []; this.events = new Map(); this.classes = new Set();
		this.classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) };
		this.attrs = {}; this._text = ""; this.disabled = false; this.style = {};
		if (tag === "iframe") this.contentWindow = { focus() {}, print: () => { this.printed = true; }, addEventListener: (name, action) => this.events.set(name, action) }; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Reconciliation source data must use text nodes"); }
	append(...items) { this.children.push(...items); for (const item of items) if (item.tag === "iframe") Promise.resolve().then(() => item.onload?.()); }
	replaceChildren(...items) { this._text = ""; this.children = items; }
	setAttribute(name, value) { this.attrs[name] = value; }
	addEventListener(name, callback) { this.events.set(name, callback); }
	click() { if (this.tag === "a") this.downloaded = true; if (!this.disabled) this.events.get("click")?.(); }
	remove() { this.removed = true; }
	descendants(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}

const clone = value => JSON.parse(JSON.stringify(value));
function harness() {
	const calls = [], controls = [], opened = [], windows = {}, outputs = [], body = new Element("body");
	const frappe = { defaults: { get_user_default: () => "QA公司" }, datetime: { get_today: () => "2026-10-08", month_start: () => "2026-10-01" },
		ui: { form: { make_control({ parent, df }) {
			const control = { parent, df, value: "", input: "", events: new Map(),
				get_value() { return this.value; }, get_input_value() { return this.input; },
				async set_value(value) { this.value = value; this.input = value; df.onchange?.(); },
				$input: null };
			control.$input = { val: () => control.input, on: (_, fn) => control.events.set("input", fn), off: () => control.events.clear() };
			controls.push(control); return control;
		} } },
		call(options) {
			let resolve;
			const promise = new Promise(done => { resolve = done; });
			promise.abort = () => { promise.aborted = true; };
			calls.push({ options: clone(options), resolve: message => resolve({ message }), promise }); return promise;
		} };
	const forbidden = new Proxy({}, { get() { throw new Error("No local storage allowed"); } });
	const context = vm.createContext({ window: windows, document: { createElement: tag => new Element(tag), body }, frappe,
		__: value => value, localStorage: forbidden, sessionStorage: forbidden, AbortController, URLSearchParams, setTimeout, clearTimeout,
		URL: { createObjectURL: () => "blob:statement", revokeObjectURL() {} },
		fetch(url, options) { let resolve; const promise = new Promise(done => { resolve = done; }); outputs.push({ url, options, resolve }); return promise; } });
	vm.runInContext(fs.readFileSync("jifeng_erp/public/js/jifeng_subcontracting_reconciliation.js", "utf8"), context);
	return { calls, controls, opened, windows, context, frappe, outputs, body, bridge: {
		open: spec => opened.push(clone(spec)), openSupplierPayment: async identity => opened.push({ payment: clone(identity) }),
	} };
}

const emptyResponse = (extra = {}) => ({ company: "QA公司", supplier: "SUP-PLATING", supplier_kind: "电镀厂",
		business_kind: "plating", result_version: "a".repeat(64), from_date: "2026-10-01", to_date: "2026-10-08", flow_order: null,
		subcontracting_order: null, company_currency: "CNY", progress: [], receipts: [], materials: [], payables: [], payments: [], history: [],
	payable_basis: "原生AP来源", payment_basis: "正式PE来源", settlement_action: "打开原生单据", ...extra });

async function testNativeQueryCarriesExplicitSourceScopeAndDoesNotPersistDrafts() {
	const h = harness(), host = new Element("div"), view = new h.windows.JifengSubcontractingReconciliation(host, h.bridge, "plating");
	await view.ready;
	await view.controls.supplier.set_value("SUP-PLATING");
	const pending = view.query(), request = h.calls.at(-1);
	assert.deepEqual(request.options, { method: "jifeng_erp.subcontracting_reconciliation.query_reconciliation", type: "GET",
		args: { company: "QA公司", supplier: "SUP-PLATING", from_date: "2026-10-01", to_date: "2026-10-08",
			flow_order: null, subcontracting_order: null, business_kind: "plating" } });
	request.resolve(emptyResponse()); await pending;
	assert.match(view.status.textContent, /正式来源/);
	assert.match(host.textContent, /原生AP来源/);
	view.destroy();
}

async function testSourceLinksOpenNativeVoucherAndMixedPaymentDoesNotInventAmount() {
	const h = harness(), host = new Element("div"), view = new h.windows.JifengSubcontractingReconciliation(host, h.bridge, "plating");
	await view.ready; await view.controls.supplier.set_value("SUP-PLATING");
	const pending = view.query(), request = h.calls.at(-1);
	request.resolve(emptyResponse({ payables: [{ voucher_type: "Purchase Invoice", voucher_no: "PI-1", posting_date: "2026-10-02",
		invoiced: "4", paid: "0", credit_note: "0", outstanding: "4" }], payments: [
		{ voucher_no: "PE-MIXED", posting_date: "2026-10-03", currency: null, paid_amount: null, payment_scope: "混合来源，无法归入本委外范围" },
	] })); await pending;
	assert.match(host.textContent, /混合来源，无法归入本委外范围/);
	assert.equal(host.textContent.includes("PE-MIXED4"), false);
	const invoiceLink = host.descendants("button").find(button => button.textContent === "PI-1");
	assert.ok(invoiceLink); invoiceLink.click();
	assert.deepEqual(h.opened.at(-1), { title: "PI-1", module: "财务", kind: "form", doctype: "Purchase Invoice", route: ["Form", "Purchase Invoice", "PI-1"] });
	view.destroy();
}

async function testFilterChangeAbortsOutstandingRead() {
	const h = harness(), host = new Element("div"), view = new h.windows.JifengSubcontractingReconciliation(host, h.bridge, "all");
	await view.ready; await view.controls.supplier.set_value("SUP-PLATING");
	const pending = view.query(), request = h.calls.at(-1);
	await view.controls.flow_order.set_value("FLOW-2");
	assert.equal(request.promise.aborted, true);
	request.resolve(emptyResponse({ business_kind: "all" })); await pending;
	assert.equal(view.result, null); view.destroy();
}

async function testCurrentInvoiceBalanceIsLabelledAndMixedInvoiceHasNoAttributedAmount() {
	const h = harness(), host = new Element("div"), view = new h.windows.JifengSubcontractingReconciliation(host, h.bridge, "all");
	await view.ready; await view.controls.supplier.set_value("SUP-PLATING");
	const pending = view.query(), request = h.calls.at(-1);
	request.resolve(emptyResponse({ business_kind: "all", payables: [
		{ voucher_type: "Purchase Invoice", voucher_no: "PI-CLEARED", source_scope: "发票原生余额（当前；AP截至日未列示）",
			currency: "CNY", invoiced: "4", outstanding: "0", paid: null, credit_note: null },
		{ voucher_type: "Purchase Invoice", voucher_no: "PI-MIX", source_scope: "混合或缺失采购来源，无法归入本委外范围",
			invoiced: null, outstanding: null, paid: null, credit_note: null },
	] })); await pending;
	assert.match(host.textContent, /发票原生余额（当前；AP截至日未列示）/);
	assert.match(host.textContent, /混合或缺失采购来源/);
	assert.equal(host.textContent.includes("84"), false);
	assert.match(host.textContent, /金额及余额依据/);
	view.destroy();
}

async function testPendingPaymentNavigationDoesNotOverwriteChangedFilterStatus() {
	const h = harness(), host = new Element("div"), view = new h.windows.JifengSubcontractingReconciliation(host, h.bridge, "all");
	await view.ready; await view.controls.supplier.set_value("SUP-PLATING");
	const pending = view.query(), request = h.calls.at(-1);
	request.resolve(emptyResponse({ business_kind: "all" })); await pending;
	let finish;
	h.bridge.openSupplierPayment = () => new Promise(resolve => { finish = resolve; });
	const opening = view.openPayment({ voucher_type: "Purchase Invoice", voucher_no: "PI-1" });
	await view.controls.supplier.set_value("SUP-OTHER");
	finish(); await opening;
	assert.equal(view.status.textContent, "筛选已变化，请重新查询。");
	view.destroy();
}

async function testFactoryStatementOutputUsesExactQueryAndCancelsAcrossScopeChanges() {
	const h = harness(), view = new h.windows.JifengSubcontractingReconciliation(new Element("div"), h.bridge, "plating");
	await view.ready; await view.controls.supplier.set_value("SUP-PLATING");
	assert.equal(view.exportButton.disabled, true); await view.output("export"); assert.equal(h.outputs.length, 0);
	const populate = async () => { const pending = view.query(); h.calls.at(-1).resolve(emptyResponse()); await pending; };
	await populate(); assert.equal(view.exportButton.disabled, false);
	const file = type => ({ ok: true, headers: { get: () => type }, blob: async () => ({ size: 80 }),
		text: async () => "<!doctype html><html><body>电镀对账</body></html>" });
	const pending = view.output("export"), request = h.outputs.at(-1), url = new URL(request.url, "https://test.invalid");
	assert.equal(request.options.method, "GET"); assert.equal(request.options.credentials, "same-origin");
	assert.equal(url.pathname, "/api/method/jifeng_erp.subcontracting_reconciliation_output.export_reconciliation");
	assert.equal(url.searchParams.get("result_version"), "a".repeat(64));
	assert.deepEqual(JSON.parse(url.searchParams.get("context")), { ...clone(view.context()), business_kind: "plating" });
	request.resolve(file("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")); await pending;
	assert.equal(h.body.descendants("a").at(-1).download, "电镀对账.xlsx");
	assert.equal(h.body.descendants("a").at(-1).downloaded, true);
	const printing = view.output("print"); h.outputs.at(-1).resolve(file("text/html")); await printing;
	assert.equal(view.printFrame.printed, true); const frame = view.printFrame; frame.events.get("afterprint")(); assert.equal(frame.removed, true);
	const count = h.body.descendants("a").length, stale = view.output("export"), late = h.outputs.at(-1);
	await view.controls.supplier.set_value("SUP-OTHER"); late.resolve(file("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")); await stale;
	assert.equal(late.options.signal.aborted, true); assert.equal(h.body.descendants("a").length, count);
	await view.controls.supplier.set_value("SUP-PLATING"); await populate();
	const denied = view.output("export"); h.outputs.at(-1).resolve({ ok: false, headers: { get: () => "application/json" } }); await denied;
	assert.equal(view.result, null); assert.equal(view.printButton.disabled, true); assert.match(view.status.textContent, /重新查询/);
	await populate(); const closing = view.output("print"), closingRequest = h.outputs.at(-1); view.setVisible(false);
	closingRequest.resolve(file("text/html")); await closing;
	assert.equal(closingRequest.options.signal.aborted, true); assert.equal(h.body.descendants("iframe").length, 1, "leaving the page must not print late HTML");
	view.destroy();
}

(async () => {
	for (const test of [testNativeQueryCarriesExplicitSourceScopeAndDoesNotPersistDrafts,
		testSourceLinksOpenNativeVoucherAndMixedPaymentDoesNotInventAmount, testFilterChangeAbortsOutstandingRead,
		testCurrentInvoiceBalanceIsLabelledAndMixedInvoiceHasNoAttributedAmount,
		testPendingPaymentNavigationDoesNotOverwriteChangedFilterStatus,
		testFactoryStatementOutputUsesExactQueryAndCancelsAcrossScopeChanges]) await test();
	console.log("Subcontracting reconciliation and exact-scope statement output passed (DOM/protocol models only)");
})().catch(error => { console.error(error); process.exitCode = 1; });
