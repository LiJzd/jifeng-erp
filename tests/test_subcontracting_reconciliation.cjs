// Isolated component contract test; this is not UI or server evidence.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");

class Element {
	constructor(tag) { this.tag = tag; this.children = []; this.events = new Map(); this.classes = new Set();
		this.classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) };
		this.attrs = {}; this._text = ""; this.disabled = false; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Reconciliation source data must use text nodes"); }
	append(...items) { this.children.push(...items); }
	replaceChildren(...items) { this._text = ""; this.children = items; }
	setAttribute(name, value) { this.attrs[name] = value; }
	addEventListener(name, callback) { this.events.set(name, callback); }
	click() { if (!this.disabled) this.events.get("click")?.(); }
	descendants(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}

const clone = value => JSON.parse(JSON.stringify(value));
function harness() {
	const calls = [], controls = [], opened = [], windows = {};
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
	const context = vm.createContext({ window: windows, document: { createElement: tag => new Element(tag) }, frappe,
		__: value => value, localStorage: forbidden, sessionStorage: forbidden });
	vm.runInContext(fs.readFileSync("jifeng_erp/public/js/jifeng_subcontracting_reconciliation.js", "utf8"), context);
	return { calls, controls, opened, windows, context, frappe, bridge: {
		open: spec => opened.push(clone(spec)), openSupplierPayment: async identity => opened.push({ payment: clone(identity) }),
	} };
}

const emptyResponse = (extra = {}) => ({ company: "QA公司", supplier: "SUP-PLATING", supplier_kind: "电镀厂",
	business_kind: "plating", from_date: "2026-10-01", to_date: "2026-10-08", flow_order: null,
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

(async () => {
	for (const test of [testNativeQueryCarriesExplicitSourceScopeAndDoesNotPersistDrafts,
		testSourceLinksOpenNativeVoucherAndMixedPaymentDoesNotInventAmount, testFilterChangeAbortsOutstandingRead]) await test();
	console.log("3 subcontracting reconciliation component tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
