// Execute the closed bridge action with native controller-shaped fixtures.
// No browser, HTTP, database, save, submit or actual payment is performed.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const plain = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let index = 0; index < 16; index++) await Promise.resolve(); };
const identity = { company: "集沣", supplier: "SUP-1", name: "PI-1" };
function fixture(options = {}) {
	const messages = [], opened = [], calls = [], intervals = new Set(), timeouts = new Set(), window = {};
	vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), {
		window, frappe: { msgprint: text => messages.push(text) }, __: text => text,
		setInterval: callback => { intervals.add(callback); return callback; }, clearInterval: callback => intervals.delete(callback),
		setTimeout: callback => { timeouts.add(callback); return callback; }, clearTimeout: callback => timeouts.delete(callback),
	});
	const bridge = Object.create(window.JifengClassicBridge.prototype), key = "formal:财务:原生收付款：PI-1";
	let route = ["Form", "Purchase Invoice", "PI-1"], dirty = false, read = true, create = true, reloads = 0, loadedHook;
	let prompt, resolveRequest, rejectRequest, entry;
	const permissions = { read: 1 }, hooks = {}, nativeDoc = { doctype: "Purchase Invoice", name: "PI-1", company: "集沣",
		supplier: "SUP-1", docstatus: 1, outstanding_amount: 80, on_hold: 0, ...options.doc };
	const child = { frappe: { get_route: () => route, route_hooks: hooks,
		model: { can_read: () => read, can_create: () => create },
		prompt(fields, callback) { prompt = { is_visible: true, fields, callback }; return prompt; },
	} };
	const frm = { doctype: "Purchase Invoice", docname: "PI-1", doc: { ...nativeDoc },
		get_docinfo: () => ({ permissions }), is_dirty: () => dirty,
		reload_doc() {
			reloads++; frm.doc = { ...nativeDoc }; loadedHook = hooks.after_load;
			if (!options.deferRefresh) {
				delete hooks.after_load; loadedHook?.(frm);
			}
			return options.reloadNever ? new Promise(() => {}) : Promise.resolve();
		},
	};
	frm.cscript = { frm, is_onload: false,
		make_payment_entry() {
			assert.equal(this, frm.cscript, "native controller retains its own this");
			if (options.discount && !frm.doc.__onload?.make_payment_via_journal_entry)
				child.frappe.prompt([{ fieldname: "reference_date" }], values => this.make_mapped_payment_entry({
					dt: this.frm.doc.doctype, dn: this.frm.doc.name, reference_date: values.reference_date }));
			else this.make_mapped_payment_entry(); // Native make_payment_entry does not return this request.
		},
		make_mapped_payment_entry(args) {
			args ||= { dt: this.frm.doc.doctype, dn: this.frm.doc.name }; calls.push(plain(args));
			if (options.mappingNever) return new Promise(() => {});
			return new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
		},
	};
	const originalMapper = frm.cscript.make_mapped_payment_entry, originalEntry = frm.cscript.make_payment_entry,
		originalPrompt = child.frappe.prompt;
	child.cur_frm = frm;
	entry = { iframe: { contentWindow: child, isConnected: true }, ready: Promise.resolve(true) };
	bridge.frames = new Map();
	bridge.open = spec => { opened.push(plain(spec)); if (!bridge.frames.has(key)) bridge.frames.set(key, entry); };
	const tick = async () => { for (const callback of [...intervals]) callback(); await flush(); };
	const complete = async (doctype = "Payment Entry", name = "new-payment-entry-1", ready = true) => {
		const doc = { doctype, name, company: "集沣", docstatus: 0, __islocal: 1, __unsaved: 1 };
		route = ["Form", doctype, name];
		const draft = { doctype, docname: name, doc, cscript: { is_onload: !ready }, is_dirty: () => true };
		child.cur_frm = draft;
		const hook = hooks.after_load; delete hooks.after_load; hook?.(draft);
		resolveRequest({ message: doc }); await tick(); return draft;
	};
	return { bridge, key, child, frm, entry, messages, opened, calls, hooks, permissions, originalMapper, originalEntry, originalPrompt,
		get reloads() { return reloads; }, get prompt() { return prompt; }, tick, complete,
		fail: async () => { rejectRequest(new Error("native denied")); await tick(); },
		expire: async () => { for (const callback of [...timeouts]) callback(); await tick(); },
		finishRefresh: async () => { delete hooks.after_load; loadedHook?.(frm); await tick(); },
		setDirty: value => { dirty = value; }, setRead: value => { read = value; }, setCreate: value => { create = value; },
		setRoute: value => { route = value; },
	};
}
const tests = [], test = (name, body) => tests.push([name, body]);
test("fresh PI hook gates native mapping and uses its current balance", async () => {
	const m = fixture({ deferRefresh: true }), oldHookCalls = [];
	m.hooks.after_load = form => oldHookCalls.push(form);
	const pending = m.bridge.openSupplierPayment(identity); await flush();
	assert.equal(m.reloads, 1); assert.equal(m.calls.length, 0, "reload request completion is earlier than native form ready");
	assert.match(m.bridge.closeBlock(m.key), /正在读取/);
	assert.equal(m.bridge.canClose(m.key), false);
	await m.finishRefresh(); assert.equal(oldHookCalls.length, 1); assert.equal(m.calls.length, 1);
	assert.deepEqual(m.calls[0], { dt: "Purchase Invoice", dn: "PI-1" });
	assert.deepEqual(m.opened[0].route, ["Form", "Purchase Invoice", "PI-1"]); assert.equal(m.opened[0].module, "财务");
	await m.complete(); assert.equal(await pending, true);
	assert.equal(m.bridge.closeBlock(m.key), ""); assert.equal(m.bridge.canClose(m.key), false, "mapped unsaved draft retains native dirty protection");
	assert.equal(m.frm.cscript.make_mapped_payment_entry, m.originalMapper); assert.equal(m.child.frappe.prompt, m.originalPrompt);
	assert.equal(m.frm.cscript.make_payment_entry, m.originalEntry);
});
test("identity accepts no report amount or arbitrary action and waits for exact native Form", async () => {
	const m = fixture(); assert.equal(await m.bridge.openSupplierPayment({ ...identity, outstanding: "120" }), false);
	assert.equal(await m.bridge.openSupplierPayment({ ...identity, action: "submit" }), false); assert.equal(m.opened.length, 0);
	m.child.cur_frm.docname = "PI-OTHER";
	const pending = m.bridge.openSupplierPayment(identity); await flush(); assert.equal(m.calls.length, 0); assert.equal(m.reloads, 0);
	m.bridge.frames.delete(m.key); await m.tick(); assert.equal(await pending, false);
});
test("failed native GET that never settles cannot hold the read-only opening guard forever", async () => {
	const m = fixture({ deferRefresh: true, reloadNever: true }), pending = m.bridge.openSupplierPayment(identity);
	await flush(); assert.equal(m.reloads, 1); assert.equal(m.calls.length, 0); assert.equal(m.bridge.canClose(m.key), false);
	await m.expire(); assert.equal(await pending, false); assert.equal(m.calls.length, 0);
	assert.equal(m.bridge.closeBlock(m.key), ""); assert.equal(m.bridge.canClose(m.key), true);
});
test("current permissions status hold company supplier and zero balance block mapping", async () => {
	const mutations = [m => m.setRead(false), m => m.setCreate(false), m => { m.permissions.read = 0; },
		m => { m.frm.doc.company = "OTHER"; }, m => { m.frm.doc.supplier = "OTHER"; },
		m => { m.frm.doc.docstatus = 2; }, m => { m.frm.doc.on_hold = 1; }, m => { m.frm.doc.outstanding_amount = 0; }];
	for (const mutate of mutations) {
		const m = fixture({ deferRefresh: true }), pending = m.bridge.openSupplierPayment(identity); await flush();
		mutate(m); await m.finishRefresh(); assert.equal(await pending, false); assert.equal(m.calls.length, 0);
	}
});
test("negative native balance preserves the native refund chain without a supplied amount", async () => {
	const m = fixture({ doc: { outstanding_amount: -40 } }), pending = m.bridge.openSupplierPayment(identity); await flush();
	assert.deepEqual(m.calls, [{ dt: "Purchase Invoice", dn: "PI-1" }]);
	await m.complete(); assert.equal(await pending, true);
});
test("cached draft and repeated action cannot replace or duplicate native content", async () => {
	const m = fixture(), pending = m.bridge.openSupplierPayment(identity); await flush();
	assert.equal(await m.bridge.openSupplierPayment(identity), false); assert.equal(m.calls.length, 1);
	await m.complete(); assert.equal(await pending, true);
	assert.equal(await m.bridge.openSupplierPayment(identity), false); assert.equal(m.calls.length, 1); assert.equal(m.reloads, 1);
});
test("native reference date prompt stays guarded and cancellation makes no request", async () => {
	const m = fixture({ discount: true }), pending = m.bridge.openSupplierPayment(identity); await flush();
	assert.ok(m.prompt.is_visible); assert.equal(m.calls.length, 0); assert.match(m.bridge.closeBlock(m.key), /准备收付款/);
	const firstPrompt = m.prompt; m.frm.cscript.make_payment_entry(); assert.equal(m.prompt, firstPrompt, "native button cannot create a second prompt while pending");
	assert.equal(m.child.frappe.prompt, m.originalPrompt);
	m.prompt.is_visible = false; await m.tick(); assert.equal(await pending, false);
	assert.equal(m.calls.length, 0); assert.equal(m.bridge.closeBlock(m.key), "");
	assert.equal(m.frm.cscript.make_mapped_payment_entry, m.originalMapper);
	assert.equal(m.frm.cscript.make_payment_entry, m.originalEntry);
});
test("native date callback rechecks current PI and keeps only its reference date", async () => {
	const m = fixture({ discount: true }), pending = m.bridge.openSupplierPayment(identity); await flush();
	m.prompt.is_visible = false; m.prompt.callback({ reference_date: "2026-10-06" }); await m.tick();
	assert.deepEqual(m.calls[0], { dt: "Purchase Invoice", dn: "PI-1", reference_date: "2026-10-06" });
	await m.complete(); assert.equal(await pending, true);
	const denied = fixture({ discount: true }), deniedPending = denied.bridge.openSupplierPayment(identity); await flush();
	denied.frm.doc.on_hold = 1; denied.prompt.is_visible = false; denied.prompt.callback({ reference_date: "2026-10-06" });
	await denied.tick(); assert.equal(await deniedPending, false); assert.equal(denied.calls.length, 0);
});
test("native configured Journal Entry branch is retained", async () => {
	const m = fixture({ discount: true, doc: { __onload: { make_payment_via_journal_entry: 1 } } });
	const pending = m.bridge.openSupplierPayment(identity); await flush(); assert.equal(m.prompt, undefined); assert.equal(m.calls.length, 1);
	await m.complete("Journal Entry", "new-journal-entry-1"); assert.equal(await pending, true);
});
test("request rejection never retries and cleans owned hooks only", async () => {
	const m = fixture(), pending = m.bridge.openSupplierPayment(identity); await flush();
	const otherHook = () => {}; m.hooks.after_load = otherHook;
	await m.fail(); assert.equal(await pending, false); assert.equal(m.calls.length, 1);
	assert.equal(m.hooks.after_load, otherHook); assert.equal(m.bridge.closeBlock(m.key), "");
	assert.equal(m.frm.cscript.make_mapped_payment_entry, m.originalMapper);
});
test("mappingNever ends the caller wait but keeps only its unresolved PI entry guarded", async () => {
	const m = fixture({ mappingNever: true }), pending = m.bridge.openSupplierPayment(identity); await flush();
	assert.equal(m.calls.length, 1); await m.expire(); assert.equal(await pending, false);
	assert.equal(m.entry.supplierPayment.phase, "unresolved"); assert.equal(m.entry.supplierPayment.target, undefined);
	assert.match(m.bridge.closeBlock(m.key), /尚未确认/); assert.equal(m.bridge.canClose(m.key), false);
	assert.equal(m.frm.cscript.make_payment_entry(), false);
	m.frm.cscript.make_mapped_payment_entry(); assert.equal(m.calls.length, 1);
	assert.equal(await m.bridge.openSupplierPayment(identity), false); assert.equal(m.calls.length, 1);
	assert.equal(m.hooks.after_load, undefined, "the expired native load hook is cleaned");
	assert.equal(m.child.frappe.prompt, m.originalPrompt, "no global prompt wrapper survives the call");
});
test("exact target with an unfinished Form cannot enable another native mapping", async () => {
	const m = fixture(), pending = m.bridge.openSupplierPayment(identity); await flush();
	const draft = await m.complete("Payment Entry", "new-payment-entry-1", false);
	await m.expire(); assert.equal(await pending, false); assert.equal(m.entry.supplierPayment.phase, "unresolved");
	assert.match(m.bridge.closeBlock(m.key), /尚未确认/); assert.equal(m.bridge.canClose(m.key), false);
	assert.equal(m.frm.cscript.make_payment_entry(), false); m.frm.cscript.make_mapped_payment_entry(); assert.equal(m.calls.length, 1);
	draft.cscript.is_onload = false; assert.equal(m.bridge.closeBlock(m.key), "");
	assert.equal(m.frm.cscript.make_payment_entry, m.originalEntry); assert.equal(m.frm.cscript.make_mapped_payment_entry, m.originalMapper);
	assert.equal(m.bridge.canClose(m.key), false, "the confirmed native draft is still dirty");
});
test("a late known request failure releases its local unresolved guard without retry", async () => {
	const m = fixture(), pending = m.bridge.openSupplierPayment(identity); await flush();
	await m.expire(); assert.equal(await pending, false); assert.equal(m.entry.supplierPayment.phase, "unresolved");
	await m.fail(); assert.equal(m.entry.supplierPayment, null); assert.equal(m.bridge.closeBlock(m.key), "");
	assert.equal(m.frm.cscript.make_payment_entry, m.originalEntry); assert.equal(m.frm.cscript.make_mapped_payment_entry, m.originalMapper);
	assert.equal(m.calls.length, 1);
});
test("late exact mapped form resolves unknown display guard without another mapping", async () => {
	const m = fixture(), pending = m.bridge.openSupplierPayment(identity); await flush();
	await m.expire(); await m.complete(); assert.equal(await pending, false);
	// A late frame is accepted by identity only when it actually becomes ready.
	assert.equal(m.bridge.closeBlock(m.key), ""); assert.equal(m.bridge.canClose(m.key), false); assert.equal(m.calls.length, 1);
});
test("closed loading frame and changed native route cannot perform mapping", async () => {
	const m = fixture(); let ready; m.entry.ready = new Promise(resolve => { ready = resolve; });
	const pending = m.bridge.openSupplierPayment(identity); m.bridge.frames.delete(m.key); ready(true); await m.tick();
	assert.equal(await pending, false); assert.equal(m.calls.length, 0);
	const changed = fixture({ deferRefresh: true }), second = changed.bridge.openSupplierPayment(identity); await flush();
	changed.setRoute(["Form", "Purchase Invoice", "PI-OTHER"]); await changed.finishRefresh(); await changed.expire();
	for (let count = 0; count < 300; count++) await changed.tick();
	assert.equal(await second, false); assert.equal(changed.calls.length, 0);
});
(async () => {
	for (const [name, body] of tests) { try { await body(); } catch (error) { error.message = name + ": " + error.message; throw error; } }
	console.log(tests.length + " supplier native payment bridge models passed (no GUI/HTTP/database execution)");
})().catch(error => { console.error(error); process.exitCode = 1; });
