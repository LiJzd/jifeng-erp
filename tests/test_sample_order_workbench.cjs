// Native sample Forms: no browser, network, database or commercial price calculation.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const clone = (value) => JSON.parse(JSON.stringify(value));
const calls = [], alerts = [], routes = [], registrations = {}, events = {};
const tick = () => new Promise((resolve) => setImmediate(resolve));
const persisted = new Map(), acquiredLocks = new Set(), storageFaults = { read: false, write: false, remove: false };
const localStorage = {
	getItem(key) { if (storageFaults.read) throw new Error("Storage read unavailable"); return persisted.get(key) ?? null; },
	setItem(key, value) { if (storageFaults.write) throw new Error("Storage quota exceeded"); persisted.set(key, value); },
	removeItem(key) { if (storageFaults.remove) throw new Error("Storage remove unavailable"); persisted.delete(key); }
};
const locks = { async request(name, options, callback) {
	assert.equal(options.mode, "exclusive"); assert.equal(options.ifAvailable, true);
	if (acquiredLocks.has(name)) return callback(null);
	acquiredLocks.add(name);
	try { return callback({ name }); } finally { acquiredLocks.delete(name); }
} };
let nextUUID = 0;
class Dialog {
	constructor(options) {
		this.options = options; this.properties = {}; this.values = {};
		for (const field of options.fields) {
			if (field.fieldtype === "Table") this.values[field.fieldname] = clone(field.data);
			else if (field.fieldtype !== "HTML") this.values[field.fieldname] = field.default ?? "";
		}
	}
	get_values() { return this.values; }
	set_df_property(name, property, value) { this.properties[`${name}.${property}`] = value; }
	set_primary_action(label, action) { this.primaryLabel = label; this.primary = action; }
	get_primary_btn() { return { prop: (name, value) => { this[name] = value; } }; }
	show() { this.visible = true; }
	hide() { this.visible = false; }
}
const window = { localStorage, navigator: { locks }, addEventListener: (name, listener) => { events[name] = listener; } };
const frappe = {
	session: { user: "sample-tester@example.invalid" },
	ui: { Dialog, form: { on: (type, handlers) => { registrations[type] = handlers; } } },
	call: (options) => {
		if (options.type === "POST") {
			const intent = [...persisted.values()].map((raw) => JSON.parse(raw)).find((record) => record.request_id === options.args.request_id);
			assert.ok(intent, "persistent intent exists before the POST is dispatched");
			assert.deepEqual(intent.candidate, clone(options.args));
			assert.equal(acquiredLocks.has(`jifeng-sample-request-v1:${JSON.stringify([intent.user, intent.scope.sample_order, intent.kind])}`), false, "HTTP never holds the local receipt lock");
		}
		return new Promise((resolve, reject) => calls.push({ options, resolve, reject }));
	},
	show_alert: (message) => alerts.push(clone(message)),
	set_route: (...route) => routes.push(route)
};
const crypto = { randomUUID: () => `00000000-0000-4000-8000-${String(++nextUUID).padStart(12, "0")}` };
function loadWindow(target = window, native = frappe) {
	const sandbox = { window: target, frappe: native, __: (text) => text, crypto };
	for (const file of ["jifeng_sample_order", "jifeng_quotation_sources"])
		vm.runInNewContext(fs.readFileSync(`jifeng_erp/public/js/${file}.js`, "utf8"), sandbox, { filename: file });
	return target.JifengSampleOrderUI;
}
loadWindow();
const ui = window.JifengSampleOrderUI, source = window.JifengQuotationSources;
const frm = (changes = {}) => ({
	doc: { name: "SAMPLE-PARENT", modified: "2026-10-04 12:34:56.123456", company: "自有样品公司", customer: "SAMPLE-CUSTOMER", docstatus: 1,
		items: [{ name: "native-row-a", sample_identity: "stable-uuid-a", item_code: "SAME-ITEM", stock_uom: "件", qty: "8", bom: "BOM-A" },
			{ name: "native-row-b", sample_identity: "stable-uuid-b", item_code: "SAME-ITEM", stock_uom: "件", qty: "12" }], ...changes },
	buttons: [], is_new: () => false, is_dirty: () => false,
	add_custom_button(label, action, group) { this.buttons.push({ label, action, group }); }
});
const quote = () => ({ customer: "EXPLICIT-CUSTOMER", currency: "CNY", valid_till: "2026-10-31", items: [
	{ include: 1, sample_item: "native-row-a", qty: "2.500", uom: "箱", rate: "0.00", make_flow: 1 },
	{ include: 1, sample_item: "native-row-b", qty: "6", uom: "件", rate: "12.30", make_flow: 0 }
] });
const found = (request, name) => ({ message: { status: "found", doctype: request.kind === "quotation" ? "Quotation" : "Work Order", name, request_id: request.requestId } });
const last = () => calls.at(-1);
const posts = () => calls.filter((call) => call.options.type === "POST").length;

async function main() {
	assert.equal(typeof registrations["Jifeng Sample Order"].refresh, "function");
	assert.equal(typeof registrations.Quotation.refresh, "function");
	const original = frm(), scope = ui.context(original);
	assert.equal(scope.company, original.doc.company); assert.equal(scope.version, original.doc.modified);
	for (const changes of [{ docstatus: 0 }, { docstatus: 2 }, { company: "" }, { modified: "" }, { items: [] },
		{ items: [{ name: "duplicate", item_code: "ITEM" }, { name: "duplicate", item_code: "ITEM" }] }])
		assert.throws(() => ui.context(frm(changes)));
	const dirty = frm(); dirty.is_dirty = () => true; assert.throws(() => ui.context(dirty));
	const fresh = frm(); fresh.is_new = () => true; assert.throws(() => ui.context(fresh));

	const payload = ui.quotationPayload(scope, quote()), rows = JSON.parse(payload.items);
	assert.deepEqual(rows.map((row) => row.sample_item), ["native-row-a", "native-row-b"], "two sample rows of the same Item stay separate");
	assert.deepEqual(rows[0], { sample_item: "native-row-a", qty: "2.500", uom: "箱", rate: "0.00", make_flow: 1 });
	assert.equal(rows[1].rate, "12.30"); assert.equal(rows[1].make_flow, 0);
	assert.equal(payload.sample_order, original.doc.name); assert.equal(payload.version, original.doc.modified);
	assert.equal("company" in payload, false, "company comes from the authorized native source, never an external argument");
	assert.equal("qty" in payload, false); assert.equal(rows[0].qty === original.doc.items[0].qty, false, "sample making quantity is not the commercial quantity");
	const zero = quote(); zero.items[0].rate = "0"; assert.equal(JSON.parse(ui.quotationPayload(scope, zero).items)[0].rate, "0");
	for (const field of ["customer", "currency", "valid_till"]) { const values = quote(); values[field] = ""; assert.throws(() => ui.quotationPayload(scope, values)); }
	for (const [field, value] of [["qty", ""], ["qty", "0"], ["qty", "0.000"], ["qty", "-1"], ["qty", "1e3"], ["qty", 2],
		["rate", ""], ["rate", "-1"], ["rate", " 0"], ["rate", 0], ["uom", ""]]) {
		const values = quote(); values.items[0][field] = value; assert.throws(() => ui.quotationPayload(scope, values));
	}
	for (const value of ["2026-02-30", "2026-13-01", "2026-1-2"]) { const values = quote(); values.valid_till = value; assert.throws(() => ui.quotationPayload(scope, values)); }
	const duplicate = quote(); duplicate.items[1].sample_item = "native-row-a"; assert.throws(() => ui.quotationPayload(scope, duplicate));
	const foreign = quote(); foreign.items[0].sample_item = "row-from-other-parent"; assert.throws(() => ui.quotationPayload(scope, foreign));
	const none = quote(); none.items.forEach((row) => { row.include = 0; }); assert.throws(() => ui.quotationPayload(scope, none));
	assert.deepEqual(clone(ui.workOrderPayload(scope, { sample_item: "native-row-b", bom: "BOM-EXPLICIT" })),
		{ sample_order: original.doc.name, sample_item: "native-row-b", version: original.doc.modified, bom: "BOM-EXPLICIT" });
	assert.equal(ui.workOrderPayload(scope, { sample_item: "native-row-a" }).bom, null, "optional BOM is not silently copied");
	assert.throws(() => ui.workOrderPayload(scope, { sample_item: "SAME-ITEM" }), "Item code cannot substitute for a child identity");

	const request = new ui.Request(original, "quotation", scope), posting = request.create(payload); await tick(); const post = last();
	assert.equal(request.phase, "sending"); assert.equal(post.options.type, "POST");
	assert.equal(post.options.method, "jifeng_erp.sample_quotations.make_sample_quotation");
	assert.equal(post.options.args.version, original.doc.modified); assert.equal("company" in post.options.args, false);
	assert.match(request.requestId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
	const pendingCount = calls.length; assert.equal(await request.create(payload), false); assert.equal(calls.length, pendingCount, "double click cannot send another creation");
	post.reject(new Error("response lost")); await posting;
	assert.equal(request.phase, "unknown"); const retainedID = request.requestId, retained = JSON.stringify(request.candidate);
	assert.equal(calls.length, pendingCount, "a failed POST does not auto-query or auto-repeat");
	assert.equal(await request.create(payload), false); assert.equal(calls.length, pendingCount);
	const firstRecovery = request.resolve(); assert.equal(last().options.type, "GET");
	assert.equal(last().options.method, "jifeng_erp.sample_quotations.resolve_sample_quotation");
	assert.deepEqual(clone(last().options.args), { request_id: retainedID });
	last().resolve({ message: { status: "absent", doctype: "Quotation", request_id: retainedID } }); await firstRecovery;
	assert.equal(request.phase, "unknown"); assert.equal(request.requestId, retainedID); assert.equal(JSON.stringify(request.candidate), retained);
	const postCount = posts(); const deniedRead = request.resolve(); last().reject({ status: 403, responseJSON: { exc_type: "PermissionError" } }); await deniedRead;
	assert.equal(request.phase, "unknown"); assert.equal(request.requestId, retainedID); assert.equal(routes.length, 0);
	const acceptedRead = request.resolve(); last().resolve(found(request, "QUOTATION-EXACT-NATIVE")); await acceptedRead;
	assert.equal(request.phase, "found"); assert.equal(posts(), postCount);
	assert.deepEqual(routes.at(-1), ["Form", "Quotation", "QUOTATION-EXACT-NATIVE"], "route uses the actual returned document, not a guessed source or Item name");
	const deleted = new ui.Request(frm({ name: "SAMPLE-DELETED-QUOTE" }), "quotation", ui.context(frm({ name: "SAMPLE-DELETED-QUOTE" })));
	const deletedWrite = deleted.create(ui.quotationPayload(deleted.scope, quote())); await tick(); last().reject(new Error("unknown write")); await deletedWrite;
	const beforeDeletedRead = posts(), deletedRead = deleted.resolve();
	last().resolve({ message: { status: "deleted", doctype: "Quotation", request_id: deleted.requestId } }); await deletedRead;
	assert.equal(deleted.phase, "deleted"); assert.equal(posts(), beforeDeletedRead, "known deleted draft never replays the original write");
	assert.equal(persisted.has(`jifeng-sample-request-v1:${JSON.stringify([frappe.session.user, deleted.scope.sample_order, "quotation"])}`), false);

	const malformed = new ui.Request(frm({ name: "SAMPLE-MALFORMED" }), "work_order", ui.context(frm({ name: "SAMPLE-MALFORMED" })));
	const badCreate = malformed.create(ui.workOrderPayload(malformed.scope, { sample_item: "native-row-a" }));
	await tick();
	last().resolve({ message: { status: "found", doctype: "Work Order", name: "UNTRUSTED-NAME", request_id: "wrong-request" } }); await badCreate;
	assert.equal(malformed.phase, "unknown"); assert.notEqual(malformed.requestId, null); assert.notEqual(routes.at(-1)[2], "UNTRUSTED-NAME");
	assert.equal(last().options.type, "POST", "a malformed success does not trigger an automatic recovery request");
	assert.equal(ui.definiteFailure({ status: 500, responseJSON: { exc_type: "ValidationError" } }), false);
	assert.equal(ui.definiteFailure({ status: 417, responseJSON: { exc_type: "ValidationError", outcome_requires_inspection: true } }), false);
	assert.equal(ui.definiteFailure({ responseJSON: { exc_type: "ValidationError" } }), false, "missing outcome evidence stays unknown");

	const editForm = frm({ name: "SAMPLE-EDITABLE" }); ui.show(editForm, "quotation");
	const editable = ui.stateFor(editForm, "quotation"); editable.dialog.values = quote();
	const beforeEdit = clone(editable.dialog.values), refused = editable.submit();
	await tick(); const actualRefusedID = editable.requestId;
	assert.equal(editable.dialog.properties["items.read_only"], 1); assert.equal(editable.dialog.disabled, true);
	last().reject({ status: 417, responseJSON: { exc_type: "ValidationError" } }); await refused;
	assert.equal(editable.phase, "editing"); assert.equal(editable.requestId, null);
	assert.equal(persisted.has(`jifeng-sample-request-v1:${JSON.stringify([frappe.session.user, editForm.doc.name, "quotation"])}`), false);
	assert.deepEqual(editable.dialog.values, beforeEdit, "definite rejection preserves the user's editable form inputs");
	assert.equal(editable.dialog.properties["items.read_only"], 0);
	editable.dialog.values.items[0].rate = "18.50";
	const editedPost = editable.submit(); await tick(); assert.notEqual(editable.requestId, actualRefusedID, "a deliberate edited attempt gets a fresh UUID");
	assert.equal(JSON.parse(last().options.args.items)[0].rate, "18.50");
	last().reject(new Error("transport result unknown")); await editedPost;
	const unknownValues = clone(editable.dialog.values), unknownID = editable.requestId;
	assert.equal(editable.dialog.primaryLabel, "核对生成结果"); assert.equal(editable.dialog.properties["items.read_only"], 1);
	const reopened = frm({ name: "SAMPLE-EDITABLE" }); assert.equal(ui.stateFor(reopened, "quotation"), editable);
	assert.deepEqual(editable.dialog.values, unknownValues); assert.equal(editable.requestId, unknownID, "reopening the Form does not replace an unknown request");
	const refreshingCount = calls.length; ui.refresh(reopened); assert.equal(calls.length, refreshingCount);
	assert.ok(reopened.buttons.some((button) => button.label === "核对报价生成结果"));
	let blockedUnload = false; const unloadEvent = { preventDefault: () => { blockedUnload = true; } }; events.beforeunload(unloadEvent);
	assert.equal(blockedUnload, true); assert.equal(unloadEvent.returnValue, "");

	const staleForm = frm(), stale = new ui.Request(staleForm, "quotation", ui.context(staleForm));
	for (const field of ["modified", "company"]) {
		const saved = staleForm.doc[field]; staleForm.doc[field] += "-CHANGED"; const count = calls.length;
		assert.equal(await stale.create(payload), false); assert.equal(calls.length, count); staleForm.doc[field] = saved;
	}
	staleForm.is_dirty = () => true; const count = calls.length; assert.equal(await stale.create(payload), false); assert.equal(calls.length, count);

	const multi = frm({ name: "SAMPLE-MULTIPLE" }); ui.show(multi, "work_order"); const first = ui.stateFor(multi, "work_order");
	first.dialog.values = { sample_item: "native-row-a", bom: "BOM-A" }; const firstWrite = first.submit();
	await tick();
	assert.equal(last().options.method, "jifeng_erp.sample_orders.make_sample_work_order");
	last().resolve(found(first, "WO-ROW-A")); await firstWrite;
	assert.deepEqual(routes.at(-1), ["Form", "Work Order", "WO-ROW-A"]);
	ui.show(multi, "work_order"); const second = ui.stateFor(multi, "work_order");
	assert.notEqual(second, first, "after a confirmed result, the user may explicitly choose another sample row");
	assert.equal(second.dialog.values.sample_item, ""); assert.equal(second.phase, "editing");
	const selector = second.dialog.options.fields.find((field) => field.fieldname === "sample_item");
	assert.equal(selector.options.includes("native-row-b"), true);
	assert.match(selector.description, /native-row-a \/ SAME-ITEM \/ 制作 8 件/);
	assert.match(selector.description, /native-row-b \/ SAME-ITEM \/ 制作 12 件/);
	const escapedForm = frm({ name: "SAMPLE-ESCAPED", items: [{ name: "safe-child", item_code: "<script>&Item", qty: "2", stock_uom: "件" }] });
	ui.show(escapedForm, "work_order");
	const description = ui.stateFor(escapedForm, "work_order").dialog.options.fields.find((field) => field.fieldname === "sample_item").description;
	assert.match(description, /&lt;script&gt;&amp;Item/); assert.equal(description.includes("<script>"), false);

	const defaults = frm({ name: "SAMPLE-NO-PRICE-DEFAULTS" }); ui.show(defaults, "quotation"); const defaultDialog = ui.stateFor(defaults, "quotation").dialog;
	assert.equal(defaultDialog.values.customer, defaults.doc.customer); assert.equal(defaultDialog.values.currency, ""); assert.equal(defaultDialog.values.valid_till, "");
	assert.equal(defaultDialog.values.items.length, 2);
	for (const row of defaultDialog.values.items) { assert.equal(row.qty, ""); assert.equal(row.uom, ""); assert.equal(row.rate, ""); assert.equal(row.include, 0); }
	const table = defaultDialog.options.fields.find((field) => field.fieldname === "items");
	assert.equal(table.fields.find((field) => field.fieldname === "rate").fieldtype, "Data", "blank and explicit zero are distinct");
	assert.equal(table.fields.find((field) => field.fieldname === "sample_item").read_only, 1);
	assert.equal(table.fields.find((field) => field.fieldname === "sample_qty").read_only, 1);
	const draft = frm({ docstatus: 0 }); ui.refresh(draft); assert.deepEqual(draft.buttons.map((button) => button.label), ["返回样品列表"]);
	draft.buttons[0].action(); assert.deepEqual(routes.at(-1), ["List", "Jifeng Sample Order"]);

	const noHeader = frm({ jf_sample_order: "", items: [{ jf_sample_order: "UNREADABLE-SOURCE", jf_sample_order_item: "secret-child", amount: 900 }] });
	const beforeSources = calls.length; source.refresh(noHeader); assert.equal(noHeader.buttons.length, 0); assert.equal(source.reference(noHeader.doc), null);
	const quotation = frm({ jf_sample_order: "READABLE-SOURCE", items: [
		{ name: "quote-row-a", jf_sample_order: "READABLE-SOURCE", jf_sample_order_item: "native-row-b", item_code: "SAME-ITEM", amount: 123 },
		{ name: "quote-row-b", jf_sample_order: "OTHER-SOURCE", jf_sample_order_item: "other-child", amount: 456 },
		{ name: "quote-row-c", item_code: "SAME-ITEM", amount: 789 }
	] });
	assert.deepEqual(clone(source.reference(quotation.doc)), { name: "READABLE-SOURCE", items: [{ quotation_item: "quote-row-a", sample_item: "native-row-b" }] });
	source.refresh(quotation); assert.equal(quotation.buttons.length, 1); quotation.buttons[0].action();
	assert.deepEqual(routes.at(-1), ["Form", "Jifeng Sample Order", "READABLE-SOURCE"]);
	assert.equal(calls.length, beforeSources, "source navigation reads only already-authorized local Form references, never queries hidden amounts or Item matches");

	function anotherWindow(user = frappe.session.user) {
		const target = { localStorage, navigator: { locks }, addEventListener() {} };
		const native = { ...frappe, session: { user } };
		return { ui: loadWindow(target, native), target, native };
	}
	const persistentForm = frm({ name: "SAMPLE-AB-RELOAD" }); ui.show(persistentForm, "quotation");
	const primary = ui.stateFor(persistentForm, "quotation"); primary.dialog.values = quote();
	const primaryWrite = primary.submit(); await tick(); const primaryPost = last(), persistentID = primary.requestId;
	const persistentKey = `jifeng-sample-request-v1:${JSON.stringify([frappe.session.user, persistentForm.doc.name, "quotation"])}`;
	const persistentIntent = persisted.get(persistentKey); assert.ok(persistentIntent);
	const variantB = anotherWindow(), bForm = frm({ name: persistentForm.doc.name }); variantB.ui.refresh(bForm);
	assert.ok(bForm.buttons.some((button) => button.label === "核对报价生成结果"), "the other A/B window discovers the persisted request without a POST");
	variantB.ui.show(bForm, "quotation"); const inB = variantB.ui.stateFor(bForm, "quotation");
	assert.equal(inB.phase, "unknown"); assert.equal(inB.requestId, persistentID);
	assert.deepEqual(clone(inB.candidate), clone(primary.candidate));
	assert.equal(inB.dialog.values.customer, "EXPLICIT-CUSTOMER"); assert.equal(inB.dialog.values.currency, "CNY");
	assert.equal(inB.dialog.values.items[0].qty, "2.500"); assert.equal(inB.dialog.values.items[0].rate, "0.00");
	assert.equal(inB.dialog.values.items[0].uom, "箱"); assert.equal(inB.dialog.values.items[0].make_flow, 1);
	assert.equal(inB.dialog.values.items[1].sample_item, "native-row-b");
	assert.equal(inB.dialog.properties["items.read_only"], 1);
	const beforeB = calls.length; assert.equal(await inB.create(ui.quotationPayload(inB.scope, quote())), false); assert.equal(calls.length, beforeB);
	primaryPost.reject(new Error("unknown after possible native commit")); await primaryWrite;
	const reloaded = anotherWindow(), reloadForm = frm({ name: persistentForm.doc.name }); reloaded.ui.show(reloadForm, "quotation");
	const afterReload = reloaded.ui.stateFor(reloadForm, "quotation");
	assert.equal(afterReload.requestId, persistentID); assert.equal(afterReload.phase, "unknown");
	assert.equal(JSON.stringify(afterReload.candidate), JSON.stringify(primary.candidate), "an entirely new JS realm retains UUID and candidate");
	const reloadRead = afterReload.resolve(); assert.equal(last().options.type, "GET");
	last().resolve({ message: { status: "absent", doctype: "Quotation", request_id: persistentID } }); await reloadRead;
	assert.equal(persisted.get(persistentKey), persistentIntent, "an absent lookup cannot discard an unknown write intent");
	const bRead = inB.resolve(); last().resolve(found(inB, "QUOTE-AB-CONFIRMED")); await bRead;
	assert.equal(persisted.has(persistentKey), false, "a confirmed result releases only its own intent under the safety lock");
	const newTask = anotherWindow(), explicitForm = frm({ name: persistentForm.doc.name }); newTask.ui.show(explicitForm, "quotation");
	const explicit = newTask.ui.stateFor(explicitForm, "quotation"); assert.equal(explicit.phase, "editing");
	assert.equal(explicit.dialog.values.items[0].rate, "", "a confirmed prior result does not silently copy commercial inputs into the new task");
	explicit.dialog.values = quote(); const explicitWrite = explicit.submit(); await tick(); const newPost = last();
	assert.notEqual(explicit.requestId, persistentID); const newIntent = persisted.get(persistentKey);
	const staleReceiptRead = afterReload.resolve(); last().resolve(found(afterReload, "QUOTE-AB-CONFIRMED")); await staleReceiptRead;
	assert.equal(persisted.get(persistentKey), newIntent, "a late receipt for the prior UUID cannot clear another window's new intent");
	newPost.reject(new Error("new request unknown")); await explicitWrite;
	const otherUser = anotherWindow("another-user@example.invalid"), otherForm = frm({ name: persistentForm.doc.name }); otherUser.ui.show(otherForm, "quotation");
	const separateUser = otherUser.ui.stateFor(otherForm, "quotation"); assert.equal(separateUser.phase, "editing");
	assert.equal(separateUser.requestId, null); assert.equal(separateUser.dialog.values.items[0].rate, "", "another account never loads the prior user's candidate inputs");
	const separateKind = ui.stateFor(frm({ name: persistentForm.doc.name }), "work_order"); assert.equal(separateKind.phase, "editing");
	assert.equal(separateKind.requestId, null, "a quotation intent does not occupy the distinct production request scope");

	const racing = anotherWindow(), raceForm = frm({ name: "SAMPLE-RACING" }); racing.ui.show(raceForm, "quotation");
	const raceA = racing.ui.stateFor(raceForm, "quotation"); raceA.dialog.values = quote();
	const peer = anotherWindow(), racePeerForm = frm({ name: raceForm.doc.name }); peer.ui.show(racePeerForm, "quotation");
	const raceB = peer.ui.stateFor(racePeerForm, "quotation"); raceB.dialog.values = quote();
	const beforeRacePosts = posts(), raceWrite = raceA.submit(), rejectedRace = raceB.submit(); await tick();
	assert.equal(posts(), beforeRacePosts + 1, "two windows prepared before either intent still dispatch only one POST");
	assert.equal(raceB.phase, "unknown"); assert.equal(raceB.requestId, raceA.requestId); await rejectedRace;
	last().reject(new Error("race result unknown")); await raceWrite;
	const raceKey = `jifeng-sample-request-v1:${JSON.stringify([frappe.session.user, raceForm.doc.name, "quotation"])}`;
	persisted.delete(raceKey); const missingIntentID = raceA.requestId, missingRead = raceA.resolve();
	assert.equal(last().options.type, "GET"); last().resolve({ message: { status: "absent", doctype: "Quotation", request_id: missingIntentID } }); await missingRead;
	assert.equal(raceA.phase, "unknown"); assert.equal(raceA.requestId, missingIntentID, "ordinary cache deletion is not proof that an uncertain write failed");

	const refusedWindow = anotherWindow(), refusedForm = frm({ name: "SAMPLE-AB-DEFINITE-REJECTION" }); refusedWindow.ui.show(refusedForm, "quotation");
	const refusedState = refusedWindow.ui.stateFor(refusedForm, "quotation"); refusedState.dialog.values = quote();
	const refusedWrite = refusedState.submit(); await tick(); const refusedPost = last(), rejectedID = refusedState.requestId;
	const waitingWindow = anotherWindow(), waitingForm = frm({ name: refusedForm.doc.name }); waitingWindow.ui.show(waitingForm, "quotation");
	const waitingState = waitingWindow.ui.stateFor(waitingForm, "quotation"); assert.equal(waitingState.requestId, rejectedID);
	const rejectedInput = clone(waitingState.dialog.values);
	refusedPost.reject({ status: 417, responseJSON: { exc_type: "ValidationError" } }); await refusedWrite;
	assert.equal(refusedState.phase, "editing"); const beforeRejectedReview = calls.length;
	assert.equal(await waitingState.resolve(), true); assert.equal(calls.length, beforeRejectedReview, "matching definite rejection receipt frees the waiting window without any new write");
	assert.equal(waitingState.phase, "editing"); assert.equal(waitingState.requestId, null); assert.deepEqual(waitingState.dialog.values, rejectedInput);
	const editedRetry = waitingState.submit(); await tick(); assert.notEqual(waitingState.requestId, rejectedID);
	last().reject(new Error("edited attempt unknown")); await editedRetry;

	const productionForm = frm({ name: "SAMPLE-WO-RELOAD" }); ui.show(productionForm, "work_order");
	const productionState = ui.stateFor(productionForm, "work_order"); productionState.dialog.values = { sample_item: "native-row-b", bom: "BOM-EXPLICIT-B" };
	const productionWrite = productionState.submit(); await tick(); const productionID = productionState.requestId;
	last().reject(new Error("production response unknown")); await productionWrite;
	const productionReload = anotherWindow(), productionCopy = frm({ name: productionForm.doc.name }); productionReload.ui.show(productionCopy, "work_order");
	const recoveredProduction = productionReload.ui.stateFor(productionCopy, "work_order");
	assert.equal(recoveredProduction.requestId, productionID); assert.equal(recoveredProduction.dialog.values.sample_item, "native-row-b");
	assert.equal(recoveredProduction.dialog.values.bom, "BOM-EXPLICIT-B"); assert.equal(recoveredProduction.scope.rows[1].qty, "12");
	assert.equal(recoveredProduction.scope.rows[1].stock_uom, "件", "recovering a production request cannot reinterpret its stock quantity/unit");
	const productionRead = recoveredProduction.resolve(); assert.equal(last().options.method, "jifeng_erp.sample_orders.get_sample_work_order_request");
	last().resolve(found(recoveredProduction, "WO-RECOVERED-B")); await productionRead;
	assert.deepEqual(routes.at(-1), ["Form", "Work Order", "WO-RECOVERED-B"]);

	const busyForm = frm({ name: "SAMPLE-BUSY-LOCK" }), busy = new ui.Request(busyForm, "quotation", ui.context(busyForm));
	const busyKey = `jifeng-sample-request-v1:${JSON.stringify([frappe.session.user, busyForm.doc.name, "quotation"])}`;
	acquiredLocks.add(busyKey); const beforeBusy = calls.length;
	assert.equal(await busy.create(ui.quotationPayload(busy.scope, quote())), false); assert.equal(calls.length, beforeBusy);
	assert.match(alerts.at(-1).message, /另一窗口.*未提交/); acquiredLocks.delete(busyKey);
	const noLocks = anotherWindow(), unlockedForm = frm({ name: "SAMPLE-NO-LOCKS" }); noLocks.target.navigator = {};
	const unlocked = new noLocks.ui.Request(unlockedForm, "quotation", noLocks.ui.context(unlockedForm));
	const beforeUnlocked = calls.length; assert.equal(await unlocked.create(ui.quotationPayload(unlocked.scope, quote())), false);
	assert.equal(calls.length, beforeUnlocked); assert.match(alerts.at(-1).message, /已阻止.*安全锁/);
	for (const fault of ["read", "write"]) {
		const failureForm = frm({ name: `SAMPLE-STORAGE-${fault}` }), failure = new ui.Request(failureForm, "quotation", ui.context(failureForm));
		storageFaults[fault] = true; const beforeFailure = calls.length;
		assert.equal(await failure.create(ui.quotationPayload(failure.scope, quote())), false);
		assert.equal(calls.length, beforeFailure); assert.equal(failure.phase, "editing"); assert.match(alerts.at(-1).message, /本地生成回执.*已阻止/);
		storageFaults[fault] = false;
	}
	const corruptForm = frm({ name: "SAMPLE-CORRUPT-INTENT" }), corruptKey = `jifeng-sample-request-v1:${JSON.stringify([frappe.session.user, corruptForm.doc.name, "quotation"])}`;
	persisted.set(corruptKey, "malformed receipt"); const corrupt = new ui.Request(corruptForm, "quotation", ui.context(corruptForm));
	const beforeCorrupt = calls.length; assert.equal(await corrupt.create(ui.quotationPayload(corrupt.scope, quote())), false);
	assert.equal(calls.length, beforeCorrupt); assert.equal(persisted.get(corruptKey), "malformed receipt", "unreadable intent is not erased to allow another write");

	const removalForm = frm({ name: "SAMPLE-STORAGE-REMOVE" }), removal = new ui.Request(removalForm, "quotation", ui.context(removalForm));
	const removalWrite = removal.create(ui.quotationPayload(removal.scope, quote())); await tick();
	const removalKey = `jifeng-sample-request-v1:${JSON.stringify([frappe.session.user, removalForm.doc.name, "quotation"])}`;
	storageFaults.remove = true; last().resolve(found(removal, "QUOTE-STORAGE-REMOVE")); await removalWrite;
	assert.equal(removal.phase, "unknown"); assert.ok(persisted.has(removalKey)); const removalPosts = posts();
	storageFaults.remove = false; const removalRead = removal.resolve(); last().resolve(found(removal, "QUOTE-STORAGE-REMOVE")); await removalRead;
	assert.equal(removal.phase, "found"); assert.equal(persisted.has(removalKey), false); assert.equal(posts(), removalPosts);
	console.log("Sample Form models passed: native source/Decimal/unit guards, exact routing, persistent before-POST intent, independent A/B/reload recovery, UUID-safe release, competing windows, storage/lock failures, deleted receipts and no repeat POST.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
