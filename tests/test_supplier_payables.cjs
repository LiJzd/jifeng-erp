// Execute the shared component against its real DTO. No GUI, HTTP or ledger proof.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
	constructor(tag) {
		this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this._text = "";
		this.classes = new Set(); this.classList = { add: value => this.classes.add(value), remove: value => this.classes.delete(value) };
	}
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Source and money must use text nodes"); }
	append(...nodes) { this.children.push(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, callback) { this.events.set(name, callback); }
	click() { if (!this.disabled) this.events.get("click")?.(); }
	descendants(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
const plain = value => JSON.parse(JSON.stringify(value));
const source = (extra = {}) => ({ voucher_type: "Purchase Invoice", voucher_no: "PI-0001", posting_date: "2026-09-10",
	due_date: null, payable_account: "应付账款 - JF", currency: "CNY", invoiced: "9007199254740993.00000000000000000001",
	paid: "0", credit_note: "-0.0000", outstanding: "9007199254740993.017", ...extra });
const response = (rows = [source()], extra = {}) => ({ company: "集沣", supplier: "SUP-0001", report_date: "2026-09-30",
	currency: "CNY", amount_basis: "company_currency", payable_account: null,
	accounts: ["应付账款 - JF", "外币应付 - JF"], rows, source_report: "Accounts Payable", ...extra });
function harness(defaultCompany = "集沣") {
	const calls = [], controls = [], pages = [], window = {};
	const frappe = { defaults: { get_user_default: () => defaultCompany }, ui: { form: { make_control({ parent, df }) {
		const control = { df, parent, value: "", typed: "", title_value_map: {}, events: new Map(),
			get_value() { return df.fieldtype === "Date" ? (this.dateMap?.[this.typed] || this.typed) : this.value; },
			get_input_value() { return this.title_value_map[this.typed] || this.typed; },
			async set_value(value) { this.value = value; this.typed = value; df.onchange(); },
			input(value) { this.typed = value; for (const action of this.events.values()) action(); } };
		control.$input = { val: () => control.typed, on: (events, action) => control.events.set(events, action),
			off: () => control.events.clear() };
		controls.push(control); return control;
	} } }, call(options) {
		let resolve, reject;
		const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
		const request = { options: plain(options), resolve: message => resolve({ message }), reject, aborted: false };
		promise.abort = () => { request.aborted = true; }; calls.push(request); return promise;
	} };
	const forbiddenStorage = new Proxy({}, { get() { throw new Error("Payable data cannot enter browser storage"); } });
	const context = vm.createContext({ window, document: { createElement: tag => new Element(tag) }, frappe,
		__: value => value, localStorage: forbiddenStorage, sessionStorage: forbiddenStorage, indexedDB: forbiddenStorage });
	vm.runInContext(fs.readFileSync("jifeng_erp/public/js/jifeng_supplier_payables.js", "utf8"), context);
	const bridge = { open: spec => pages.push(plain(spec)) };
	return { calls, controls, pages, window, bridge, context };
}
async function make(h = harness()) {
	const host = new Element("div"), view = new h.window.JifengSupplierPayables(host, h.bridge);
	await view.ready;
	return { ...h, host, view };
}
async function select(model, account = "") {
	await model.view.supplierControl.set_value("SUP-0001");
	await model.view.dateControl.set_value("2026-09-30");
	await model.view.accountControl.set_value(account);
}
async function populate(model, data = response()) {
	const pending = model.view.query(); model.calls.at(-1).resolve(data); await pending;
}
const tests = [];
function test(name, body) { tests.push([name, body]); }
test("native controls require explicit supplier/date and company-scoped payable account", async () => {
	const model = await make();
	assert.equal(model.calls.length, 0); assert.equal(model.view.dateControl.get_value(), "");
	assert.deepEqual(model.controls.map(control => [control.df.fieldtype, control.df.options || null]),
		[["Link", "Company"], ["Link", "Supplier"], ["Date", null], ["Link", "Account"]]);
	assert.deepEqual(plain(model.view.accountControl.get_query()), { filters: { company: "集沣", account_type: "Payable", is_group: 0 } });
	await model.view.query(); assert.equal(model.calls.length, 0); assert.match(model.view.status.textContent, /请选择/);
	await select(model); const pending = model.view.query(); const request = model.calls.at(-1);
	assert.deepEqual(request.options, { method: "jifeng_erp.supplier_reconciliation.query_payables", type: "GET",
		args: { company: "集沣", supplier: "SUP-0001", report_date: "2026-09-30" } });
	request.resolve(response()); await pending;
});
test("DTO money preserves precision, zeros, negative values and exponents without totals", async () => {
	const model = await make(); await select(model);
	const values = [source(), source({ voucher_type: "Payment Entry", voucher_no: "PE-0002", due_date: "2026-10-10",
		invoiced: "0E-9", paid: "1E+28", credit_note: "-30.000000000000000001", outstanding: "-1E-28" })];
	await populate(model, response(values));
	const cells = model.view.tbody.descendants("td"); assert.equal(cells.length, 20, "native total tail is not fabricated by this DTO view");
	for (let index = 0; index < values.length; index++)
		assert.deepEqual(cells.slice(index * 10 + 6, index * 10 + 10).map(cell => cell.textContent),
			[values[index].invoiced, values[index].paid, values[index].credit_note, values[index].outstanding]);
	assert.equal(cells[3].textContent, "—"); assert.equal(cells[13].textContent, "2026-10-10");
	assert.match(model.view.scopeLabel.textContent, /CNY/); assert.equal(model.host.descendants("tfoot").length, 0);
});
test("all scope mismatches reject the entire response and never retain previous amounts", async () => {
	for (const extra of [{ company: "别的公司" }, { supplier: "OTHER" }, { report_date: "2026-09-29" },
		{ payable_account: "应付账款 - JF" }, { amount_basis: "party_currency" }, { source_report: "Other" },
		{ accounts: [] }, { accounts: ["应付账款 - JF", "应付账款 - JF"] }, { currency: "" }, { hidden_data: "extra" }]) {
		const model = await make(); await select(model); await populate(model);
		const pending = model.view.query(); assert.equal(model.view.tbody.descendants("td").length, 0, "refresh clears values before the network response");
		model.calls.at(-1).resolve(response([source()], extra)); await pending;
		assert.equal(model.view.tbody.descendants("td").length, 0); assert.match(model.view.status.textContent, /查询未完成/);
	}
});
test("row source/date/account/currency/money contract is indivisible", async () => {
	for (const bad of [source({ voucher_type: "Unknown" }), source({ voucher_no: "" }), source({ posting_date: "2026-10-01" }),
		source({ posting_date: "2026-02-30" }), source({ due_date: "" }), source({ payable_account: "OTHER" }),
		source({ currency: "USD" }), source({ invoiced: 42 }), source({ paid: "NaN" }), source({ credit_note: "Infinity" }),
		source({ outstanding: " 0 " }), source({ extra_amount: "secret" }), { ...source(), due_date: undefined }]) {
		const model = await make(); await select(model); await populate(model, response([source(), bad]));
		assert.equal(model.view.tbody.descendants("td").length, 0, "even the good row cannot become a partial balance");
	}
});
test("optional account selects a single exact returned payable scope", async () => {
	const model = await make(); await select(model, "应付账款 - JF");
	const pending = model.view.query(); assert.equal(model.calls.at(-1).options.args.payable_account, "应付账款 - JF");
	model.calls.at(-1).resolve(response([source()], { payable_account: "应付账款 - JF", accounts: ["应付账款 - JF"] })); await pending;
	assert.equal(model.view.tbody.descendants("tr").length, 1);
	await populate(model, response([source()], { payable_account: "应付账款 - JF" }));
	assert.equal(model.view.tbody.descendants("td").length, 0, "selected account must not receive all-account totals or rows");
});
test("malformed or multiple selections never widen into an all-account query", async () => {
	for (const controlName of ["companyControl", "supplierControl", "accountControl"]) {
		const model = await make(); await select(model);
		model.view[controlName].value = ["one", "two"]; model.view[controlName].typed = ["one", "two"];
		await model.view.query(); assert.equal(model.calls.length, 0);
	}
	const model = await make(); await select(model, "应付账款 - JF"); model.view.accountControl.typed = "";
	await model.view.query(); assert.equal(model.calls.length, 0, "uncommitted clearing cannot silently widen its stored account");
});
test("company changes clear optional account and every prior amount immediately", async () => {
	const model = await make(); await select(model, "应付账款 - JF");
	await populate(model, response([source()], { payable_account: "应付账款 - JF", accounts: ["应付账款 - JF"] }));
	await model.view.companyControl.set_value("新公司");
	assert.equal(model.view.accountControl.get_value(), ""); assert.equal(model.view.tbody.descendants("td").length, 0);
	assert.equal(model.view.scopeLabel.textContent, ""); assert.equal(model.view.accountControl.get_query().filters.company, "新公司");
});
test("native Link title mapping and localized Date parsing retain exact stored identities", async () => {
	const model = await make(); await select(model);
	model.view.supplierControl.title_value_map["供应商名称"] = "SUP-0001"; model.view.supplierControl.typed = "供应商名称";
	model.view.dateControl.dateMap = { "30/09/2026": "2026-09-30" }; model.view.dateControl.typed = "30/09/2026";
	await populate(model);
	assert.equal(model.calls.at(-1).options.args.supplier, "SUP-0001"); assert.equal(model.calls.at(-1).options.args.report_date, "2026-09-30");
	model.view.supplierControl.input("未选择的名称"); await model.view.query();
	assert.equal(model.calls.length, 1, "unsynced Link input cannot reuse its old stored selection");
});
test("invalid or implicit cutoff cannot request a stale or unbounded ledger", async () => {
	for (const cutoff of ["", "2026-02-29", "2026-2-01", "2026-02-30", "0000-01-01", "2026-13-01"] ) {
		const model = await make(); await select(model); await model.view.dateControl.set_value(cutoff);
		await model.view.query(); assert.equal(model.calls.length, 0);
	}
	const model = await make(); await select(model); await model.view.dateControl.set_value("2024-02-29");
	const pending = model.view.query(); model.calls.at(-1).resolve(response([], { report_date: "2024-02-29" })); await pending;
	assert.match(model.view.status.textContent, /没有应付来源/);
});
test("each native filter input drops old rows and late responses", async () => {
	for (const controlName of ["companyControl", "supplierControl", "dateControl", "accountControl"]) {
		const model = await make(); await select(model); await populate(model);
		const pending = model.view.query(), request = model.calls.at(-1);
		model.view[controlName].input(controlName === "dateControl" ? "2026-09-29" : "changed");
		assert.equal(model.view.tbody.descendants("td").length, 0); assert.equal(request.aborted, true);
		request.resolve(response()); await pending;
		assert.equal(model.view.tbody.descendants("td").length, 0); assert.equal(model.view.loading, false);
	}
});
test("a later request wins regardless of completion order", async () => {
	const model = await make(); await select(model);
	const first = model.view.query(), old = model.calls.at(-1);
	await model.view.dateControl.set_value("2026-09-29");
	const second = model.view.query(), latest = model.calls.at(-1);
	latest.resolve(response([source({ outstanding: "7.000" })], { report_date: "2026-09-29" })); await second;
	old.resolve(response()); await first;
	assert.equal(model.view.tbody.descendants("td")[9].textContent, "7.000");
	assert.equal(model.view.queryButton.disabled, false);
});
test("a denied or malformed query clears previous values and stays distinct from an empty result", async () => {
	const model = await make(); await select(model); await populate(model);
	const denied = model.view.query(); assert.equal(model.view.tbody.descendants("td").length, 0);
	model.calls.at(-1).reject(new Error("private backend details")); await denied;
	assert.match(model.view.status.textContent, /查询未完成/); assert.ok(!model.host.textContent.includes("private backend details"));
	await populate(model, response([])); assert.match(model.view.status.textContent, /没有应付来源/);
	const malformed = model.view.query(); model.calls.at(-1).resolve(null); await malformed;
	assert.match(model.view.status.textContent, /查询未完成/); assert.equal(model.view.scopeLabel.textContent, "");
});
test("source names render as text and open only the exact native Form before clearing amounts", async () => {
	for (const type of ["Purchase Invoice", "Payment Entry", "Journal Entry", "Purchase Order"]) {
		const model = await make(); await select(model);
		const name = '<img src=x onerror="bad()">'; await populate(model, response([source({ voucher_type: type, voucher_no: name })]));
		const filters = plain(model.view.filters()); const button = model.view.tbody.descendants("button")[0];
		assert.equal(button.textContent, name); assert.equal(model.host.descendants("img").length, 0); button.click();
		assert.deepEqual(model.pages[0].route, ["Form", type, name]); assert.equal(model.pages[0].doctype, type);
		assert.deepEqual(plain(model.view.filters()), filters); assert.equal(model.view.tbody.descendants("td").length, 0);
		button.click(); assert.equal(model.pages.length, 1, "detached previous source actions cannot read cached amounts");
		const pending = model.view.query(); model.calls.at(-1).reject(new Error("source permission revoked")); await pending;
		assert.match(model.view.status.textContent, /查询未完成/);
	}
});
test("unsynchronized filters cannot open a previously visible source", async () => {
	const model = await make(); await select(model); await populate(model);
	const button = model.view.tbody.descendants("button")[0]; model.view.supplierControl.typed = "OTHER";
	button.click(); assert.equal(model.pages.length, 0);
});
test("native vouchers with the same name in different DocTypes open distinct workspace tabs", async () => {
	const h = harness();
	vm.runInContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), h.context);
	const tabs = new Map(), bridge = Object.create(h.window.JifengClassicBridge.prototype);
	bridge.workspace = { addTab(tab) { if (!tabs.has(tab.key)) tabs.set(tab.key, plain(tab)); } };
	const model = await make({ ...h, bridge }); await select(model);
	const types = ["Purchase Invoice", "Payment Entry", "Journal Entry", "Purchase Order"];
	for (const type of types) {
		await populate(model, response([source({ voucher_type: type, voucher_no: "SHARED-0001" })]));
		model.view.tbody.descendants("button")[0].click();
	}
	assert.equal(tabs.size, types.length, "another DocType must not re-use the first voucher's native frame");
	assert.deepEqual([...tabs.values()].map(tab => tab.spec.route), types.map(type => ["Form", type, "SHARED-0001"]));
	await populate(model, response([source({ voucher_no: "SHARED-0001" })]));
	model.view.tbody.descendants("button")[0].click();
	assert.equal(tabs.size, types.length, "the same native voucher should keep its existing workspace tab");
});
test("duplicate query clicks cannot issue another request", async () => {
	const model = await make(); await select(model);
	const pending = model.view.query(); await model.view.query(); model.view.queryButton.click();
	assert.equal(model.calls.length, 1); model.calls[0].resolve(response()); await pending;
});
test("destroy clears money, detaches filter handlers and rejects late query/source actions", async () => {
	const model = await make(); await select(model); await populate(model);
	const button = model.view.tbody.descendants("button")[0], pending = model.view.query(), request = model.calls.at(-1);
	model.view.destroy(); model.view.destroy(); assert.equal(model.host.textContent, ""); assert.equal(request.aborted, true);
	request.resolve(response()); await pending; button.click(); await model.view.query();
	assert.equal(model.host.textContent, ""); assert.equal(model.pages.length, 0); assert.equal(model.calls.length, 2);
	assert.ok(model.controls.every(control => control.events.size === 0)); assert.equal(model.view.rows.length, 0);
	assert.equal(model.view.isDirty(), false); assert.equal(model.view.getCloseBlock(), "");
});
test("tab/route exit clears money and pending response; return requires a fresh permission read", async () => {
	const model = await make(); await select(model); await populate(model);
	const filters = plain(model.view.filters()), pending = model.view.query(), request = model.calls.at(-1);
	model.view.setVisible(false); model.view.setVisible(false);
	assert.equal(model.view.tbody.descendants("td").length, 0); assert.equal(request.aborted, true);
	assert.deepEqual(plain(model.view.filters()), filters);
	await model.view.query(); assert.equal(model.calls.length, 2, "hidden tabs cannot query the ledger");
	model.view.setVisible(true); assert.equal(model.calls.length, 2, "showing a tab cannot re-use results or query silently");
	request.resolve(response()); await pending; assert.equal(model.view.tbody.descendants("td").length, 0);
	const fresh = model.view.query(); model.calls.at(-1).reject(new Error("role revoked during native voucher navigation")); await fresh;
	assert.match(model.view.status.textContent, /查询未完成/); assert.equal(model.view.tbody.descendants("td").length, 0);
});
test("A/B instances share one component and cannot share result state", async () => {
	const h = harness(), a = await make(h), b = await make(h); await select(a); await select(b);
	const first = a.view.query(), firstCall = h.calls.at(-1), second = b.view.query(), secondCall = h.calls.at(-1);
	secondCall.resolve(response([source({ outstanding: "-2.5000" })])); await second;
	firstCall.resolve(response()); await first; a.view.destroy();
	assert.equal(a.host.textContent, ""); assert.equal(b.view.tbody.descendants("td")[9].textContent, "-2.5000");
	assert.ok(h.calls.every(call => call.options.type === "GET" && call.options.method === "jifeng_erp.supplier_reconciliation.query_payables"));
});
(async () => {
	for (const [name, body] of tests) { try { await body(); } catch (error) { error.message = name + ": " + error.message; throw error; } }
	console.log(tests.length + " supplier payables models passed, 0 skipped (DOM/DTO only; no GUI/HTTP/ledger execution)");
})().catch(error => { console.error(error); process.exitCode = 1; });
