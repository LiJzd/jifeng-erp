// Native Dialog/RPC protocol models only. No browser, server, database or network.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const { Element, context, window, frappe, calls, messages, fieldMeta, blank, listRow, tick } = require("./test_product_dictionary_workbench.cjs");
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_customer_type.js", "utf8"), context);
let confirmation;
frappe.confirm = (message, yes, no) => { confirmation = { message, yes, no }; };
const creation = "2026-10-01 00:00:01.123456", version = "2026-10-01T00:00:02.654321";
const empty = (type) => blank({ dictionary_type: type, company: "Company A", field_meta: fieldMeta() });
const row = (type, extra = {}) => listRow({ dictionary_type: type, creation, version, can_delete: true, ...extra });
const result = (type, rows = [], company = "Company A") => ({ company, dictionary_type: type, search: "", include_inactive: 0,
	start: 0, page_length: 25, total: rows.length, rows });
async function initialize(type) {
	const bench = new window.JifengCustomerType(new Element(), type);
	calls.at(-1).resolve(empty(type)); await bench.ready;
	bench.companyControl.set_value("Company A"); calls.at(-1).resolve(result(type, [row(type)])); await tick();
	return bench;
}
function reply(state, status = "deleted", extra = {}) { return { status, ...state.payload, ...extra }; }
async function flushQuery(type) {
	assert.ok(calls.at(-1).options.method.endsWith("list_dictionary")); calls.at(-1).resolve(result(type)); await tick();
}

async function main() {
	for (const type of ["客户类型", "客户等级", "客户分类", "订单客户分类", "订单客户级别"]) {
		const bench = await initialize(type), original = bench.rows[0];
		assert.match(bench.limits.textContent, /操作记录永久保留/);
		assert.equal(bench.tbody.descendants("td").length, type.startsWith("订单") ? 7 : 11);
		assert.equal(bench.tbody.descendants("button").find((button) => button.textContent === "删除").disabled, false);
		const beforeConfirm = calls.length;
		bench.confirmDelete(original);
		assert.equal(calls.length, beforeConfirm, "confirmation never sends the delete POST");
		assert.match(confirmation.message, /业务引用/); assert.match(bench.getCloseBlock(), /确认/);
		assert.equal(bench.addButton.disabled, true);
		confirmation.no(); assert.equal(bench.deletion, null);
		bench.confirmDelete(original); const state = bench.deletion;
		const deleting = confirmation.yes(), post = calls.at(-1);
		assert.equal(post.options.method, "jifeng_erp.customer_type.delete_dictionary");
		assert.equal(post.options.type, "POST");
		assert.equal(state.payload.creation, creation, "do not lose native space spelling or microseconds");
		assert.equal(state.payload.version, version);
		assert.equal(state.payload.dictionary_type, type);
		assert.deepEqual(Object.keys(state.payload).sort(), ["company", "creation", "dictionary_type", "name", "request_id", "version"]);
		post.reject(new Error("HTTP result lost")); await deleting;
		assert.equal(state.unresolved, true); assert.match(bench.getCloseBlock(), /核对删除记录/);
		const count = calls.length;
		await bench.deleteEntry(state); await bench.openEditor(); bench.confirmDelete(original);
		assert.equal(calls.length, count, "unknown delete cannot be retried or open another editor");
		assert.equal(bench.deletion, state);
		const wrong = bench.resolveDelete(), wrongCall = calls.at(-1);
		assert.equal(wrongCall.options.type, "GET");
		assert.deepEqual(wrongCall.options.args.payload, post.options.args.payload);
		wrongCall.resolve(reply(state, "deleted", { creation: creation.replace(" ", "T") })); await wrong;
		assert.equal(bench.deletion, state, "even an equivalent timestamp spelling must be echoed exactly by the backend");
		const wrongScope = bench.resolveDelete(); calls.at(-1).resolve(reply(state, "deleted", { dictionary_type: "wrong" })); await wrongScope;
		assert.equal(bench.deletion, state);
		const missing = bench.resolveDelete(); calls.at(-1).resolve({ status: "unknown" }); await missing;
		assert.equal(bench.deletion, state);
		const resolved = bench.resolveDelete(); calls.at(-1).resolve(reply(state)); await resolved;
		assert.equal(bench.deletion, null); await flushQuery(type);
		assert.equal(calls.filter((call) => call.options.method.endsWith("delete_dictionary") && call.options.args.payload.request_id === state.payload.request_id).length, 1);
		bench.destroy();
	}

	const acknowledged = await initialize("客户等级"), selected = acknowledged.rows[0];
	acknowledged.confirmDelete(selected); const acknowledgedState = acknowledged.deletion, normalDelete = confirmation.yes();
	calls.at(-1).resolve(reply(acknowledgedState)); await normalDelete;
	assert.equal(acknowledged.deletion, null, "a complete exact POST reply finishes without a recovery GET");
	await flushQuery("客户等级");
	const readonlyQuery = acknowledged.query();
	calls.at(-1).resolve(result("客户等级", [row("客户等级", { can_delete: false })])); await readonlyQuery;
	assert.equal(acknowledged.tbody.descendants("button").find((button) => button.textContent === "删除").disabled, true);
	const readonlyCalls = calls.length; acknowledged.confirmDelete(acknowledged.rows[0]);
	assert.equal(acknowledged.deletion, null); assert.equal(calls.length, readonlyCalls);
	acknowledged.destroy();

	const bench = await initialize("客户类型");
	const original = bench.rows[0];
	const editing = bench.openEditor(); calls.at(-1).resolve(empty("客户类型")); await editing;
	await bench.editor.dialog.set_values({ value: "未保存原文" });
	const count = calls.length; bench.confirmDelete(original);
	assert.equal(bench.deletion, undefined, "open editor blocks deletion without losing its draft"); assert.equal(calls.length, count);
	const saving = bench.save(bench.editor); calls.at(-1).reject(new Error("save response lost")); await saving;
	const heldEditor = bench.editor, candidate = heldEditor.candidate;
	const unknown = bench.resolve(heldEditor); calls.at(-1).resolve({ status: "deleted", request_id: "wrong",
		company: candidate.company, dictionary_type: "客户类型", name: "WORD-1", creation, version }); await unknown;
	assert.equal(heldEditor.unresolved, true, "wrong creation request does not unlock the unknown save");
	const resolvedCreation = bench.resolve(heldEditor); calls.at(-1).resolve({ status: "deleted", request_id: candidate.request_id,
		company: candidate.company, dictionary_type: "客户类型", name: "WORD-1", creation, version }); await resolvedCreation;
	assert.equal(heldEditor.deleted, true); assert.equal(heldEditor.unresolved, false);
	assert.equal(heldEditor.dialog.get_value("value"), "未保存原文");
	assert.equal(heldEditor.dialog.get_primary_btn().disabled, true); assert.match(heldEditor.review.textContent, /已删除/);
	await flushQuery("客户类型");
	const beforeSave = calls.length; await bench.save(heldEditor); assert.equal(calls.length, beforeSave);
	const newDraft = bench.newDraft(heldEditor); calls.at(-1).resolve(empty("客户类型")); await newDraft;
	assert.equal(heldEditor.deleted, false); assert.notEqual(heldEditor.requestId, candidate.request_id);
	heldEditor.dialog.hide();
	const freshQuery = bench.query(); calls.at(-1).resolve(result("客户类型", [original])); await freshQuery;
	bench.confirmDelete(original); const scoped = bench.deletion;
	const lost = confirmation.yes(); calls.at(-1).reject(new Error("delete unknown")); await lost;
	bench.companyControl.set_value("Company B"); calls.at(-1).resolve(result("客户类型", [], "Company B")); await tick();
	const scopeResolve = bench.resolveDelete(); assert.equal(calls.at(-1).options.args.payload.company, "Company A");
	calls.at(-1).resolve(reply(scoped, "not_deleted")); await scopeResolve;
	assert.equal(bench.deletion, null); assert.equal(bench.company(), "Company B");
	assert.equal(calls.at(-1).options.method, "jifeng_erp.customer_type.resolve_delete", "original scope acknowledgement does not query or replace the new company");
	bench.destroy();

	const late = await initialize("订单客户级别"), lateRow = late.rows[0];
	late.confirmDelete(lateRow); const lateState = late.deletion, pending = confirmation.yes();
	const pendingCall = calls.at(-1), lastMessages = messages.length;
	late.destroy(); pendingCall.resolve(reply(lateState)); await pending;
	assert.equal(late.deletion, lateState, "late RPC cannot mutate a destroyed workbench's held state");
	assert.equal(messages.length, lastMessages);
	console.log("Five customer dictionary deletions: explicit confirmation, native wire timestamps, held scopes/UUIDs, unknown GET-only recovery, deleted creation drafts and late replies passed (protocol models).");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
