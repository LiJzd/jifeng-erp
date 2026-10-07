// Protocol/DOM models for the observed color fields; no browser or server writes.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const { Element, context, window, frappe, calls, messages, fieldMeta, pack, blank, listRow, tick } = require("./test_product_dictionary_workbench.cjs");
for (const file of ["jifeng_order_dictionary", "jifeng_plating_color", "jifeng_workspace_core"]) vm.runInNewContext(fs.readFileSync(`jifeng_erp/public/js/${file}.js`, "utf8"), context);
const metadata = () => ({ ...fieldMeta(), abbreviation: { fieldtype: "Data", read_only: true },
	code: { fieldtype: "Data", description: "代码生成条码时用" }, color_system: { fieldtype: "Data" } });
const color = (extra = {}) => pack({ dictionary_type: "镀色", code: " 001 ", color_system: " 暖色 ", field_meta: metadata(), ...extra });
const empty = (extra = {}) => blank({ dictionary_type: "镀色", code: null, color_system: null, field_meta: metadata(), ...extra });
const row = (extra = {}) => listRow({ dictionary_type: "镀色", code: "001", color_system: "暖色", can_delete: true, ...extra });
const result = (extra = {}) => ({ company: "Company A", dictionary_type: "镀色", search: "", include_inactive: 0, start: 0, page_length: 25, total: 0, rows: [], ...extra });

async function main() {
	const bridge = Object.create(window.JifengClassicBridge.prototype);
	const entry = bridge.mount(new Element(), { platingColor: true });
	const bench = entry.workbench;
	assert.equal(calls.at(-1).options.method, "jifeng_erp.plating_color.init_dictionary");
	assert.equal(calls.at(-1).options.args.dictionary_type, "镀色");
	calls.at(-1).resolve(empty({ company: null, number_preview: null })); await entry.ready;
	assert.equal(bench.host.descendants("th").map((e) => e.textContent).join("/"), "代码/编号/名称/色系/备注/简拼/停用/操作/日期/修改/修改日期/修改/删除");
	assert.equal(bench.addButton.disabled, true, "creation needs an authorized company");
	bench.companyControl.set_value("Company A"); calls.at(-1).resolve(result({ total: 1, rows: [row()] })); await tick();
	assert.equal(bench.addButton.disabled, false, "authorized plating color creation is available");
	assert.equal(bench.tbody.descendants("td").length, 13);
	for (const extra of [{ dictionary_type: "订单类型" }, { company: "Other Company" }, { code: {} }, { color_system: [] }]) {
		const query = bench.query(); calls.at(-1).resolve(result({ total: 1, rows: [row(extra)] })); await query;
		assert.equal(bench.rows.length, 0, "wrong scope or nontext color fields are rejected before rendering");
	}
	const query = bench.query(); calls.at(-1).resolve(result()); await query;
	await creationModels(bench);
	const opening = bench.openEditor("COLOR-1"); calls.at(-1).resolve(color({ name: "COLOR-1", code: "code-before", value: "value-before",
		color_system: "family-before", remarks: "remarks-before" })); await opening;
	const state = bench.editor, dialog = state.dialog;
	assert.equal(dialog.config.title, "产品镀色修改");
	assert.equal(dialog.config.fields.filter((f) => !f.hidden).map((f) => f.label).join("/"), "编号/代码/名称/色系/备注/停用");
	assert.equal(dialog.fields_dict.legacy_number.df.read_only, true);
	assert.equal(dialog.fields_dict.code.df.fieldtype, "Data");
	assert.equal(dialog.fields_dict.code.df.placeholder, "代码生成条码时用");
	assert.equal(dialog.fields_dict.color_system.df.fieldtype, "Data");
	await dialog.set_values({ code: "code-draft", value: "value-draft", color_system: "family-draft", remarks: "remarks-draft", inactive: 0 });
	const saving = bench.save(state), call = calls.at(-1);
	assert.equal(call.options.method, "jifeng_erp.plating_color.save_dictionary");
	assert.equal(call.options.args.payload.code, "code-draft"); assert.equal(call.options.args.payload.color_system, "family-draft");
	assert.equal("legacy_number" in call.options.args.payload, false); assert.equal("abbreviation" in call.options.args.payload, false);
	assert.equal(dialog.fields_dict.code.df.read_only, true, "code freezes during save");
	assert.equal(dialog.fields_dict.color_system.df.read_only, true, "color family freezes during save");
	call.reject(new Error("transport lost")); await saving;
	assert.equal(state.unresolved, true); assert.equal(dialog.fields_dict.code.df.read_only, true);
	const held = JSON.stringify(state.candidate), posts = calls.filter((c) => c.options.method.endsWith("save_dictionary")).length;
	dialog.hide(); assert.equal(state.closed, false, "unknown save cannot discard code or color family");
	const resolving = bench.resolve(state);
	assert.equal(calls.at(-1).options.method, "jifeng_erp.plating_color.read_dictionary");
	assert.equal(calls.at(-1).options.args.name, "COLOR-1");
	calls.at(-1).resolve(color({ name: "COLOR-1", version: "2026-10-07T00:00:03", code: "different", value: "value-draft", color_system: "family-draft", remarks: "remarks-draft" })); await resolving;
	assert.equal(state.unresolved, true, "a different returned code does not resolve the held save");
	assert.equal(JSON.stringify(state.candidate), held); assert.equal(state.conflict.code, "different");
	assert.ok(state.review.descendants("tr").some((tr) => tr.descendants("td").map((td) => td.textContent).join("/") === "代码/code-draft/different"), "code-only conflict is visible before adopting current values");
	const resolveFamily = bench.resolve(state);
	calls.at(-1).resolve(color({ name: "COLOR-1", version: "2026-10-07T00:00:04", code: "code-draft", value: "value-draft", color_system: "family-current", remarks: "remarks-draft" })); await resolveFamily;
	assert.equal(state.unresolved, true);
	assert.ok(state.review.descendants("tr").some((tr) => tr.descendants("td").map((td) => td.textContent).join("/") === "色系/family-draft/family-current"), "family-only conflict is visible before adopting current values");
	for (const field of ["code", "color_system"]) for (const omitted of ["value", "metadata"]) {
		const incomplete = color({ name: "COLOR-1", version: "2026-10-07T00:00:05", code: "code-draft", value: "value-draft", color_system: "family-draft", remarks: "remarks-draft" });
		if (omitted === "value") delete incomplete[field]; else delete incomplete.field_meta[field];
		const resolveMasked = bench.resolve(state);
		calls.at(-1).resolve(incomplete); await resolveMasked;
		assert.equal(state.unresolved, true); assert.equal(state.conflict, null, "incomplete visible receipt cannot authorize abandoning a held field");
		assert.equal(JSON.stringify(state.candidate), held);
		assert.equal(state.review.descendants("table").length, 0);
	}
	const resolveExact = bench.resolve(state);
	calls.at(-1).resolve(color({ name: "COLOR-1", version: "2026-10-07T00:00:06", code: "code-draft", value: "value-draft", color_system: "family-draft", remarks: "remarks-draft" })); await resolveExact;
	assert.equal(state.unresolved, false); assert.equal(dialog.get_value("code"), "code-draft");
	assert.equal(dialog.get_value("color_system"), "family-draft");
	assert.equal(calls.filter((c) => c.options.method.endsWith("save_dictionary")).length, posts, "receipt checks never retry POST");
	calls.at(-1).resolve(result()); await tick(); dialog.hide(); entry.stop();

	const masked = new window.JifengPlatingColor(new Element());
	calls.at(-1).resolve(empty({ company: null })); await masked.ready;
	masked.companyControl.set_value("Company A"); calls.at(-1).resolve(result()); await tick();
	const meta = metadata(); delete meta.code; meta.color_system.read_only = true;
	const read = masked.openEditor("WORD-1"); calls.at(-1).resolve(color({ field_meta: meta })); await read;
	const readonly = masked.editor;
	assert.equal(readonly.dialog.fields_dict.code, undefined, "masked code has no control");
	assert.equal(readonly.dialog.fields_dict.color_system.df.read_only, true);
	await readonly.dialog.set_values({ value: "另一个名称", color_system: "不可写" });
	assert.equal("code" in masked.payload(readonly), false); assert.equal("color_system" in masked.payload(readonly), false);
	masked.destroy();
	const workspace = Object.create(window.JifengERPWorkspace.Workspace.prototype), opened = [];
	workspace.formalBridge = { open: (spec) => opened.push(spec) }; workspace.addTab = () => { throw new Error("no preview fallback"); };
	for (const module of ["产品", "订单"]) {
		workspace.openPage("镀色", module);
		assert.equal(opened.at(-1).platingColor, true); assert.equal(opened.at(-1).availability, "partial");
		assert.equal(opened.at(-1).doctype, "Jifeng Plating Color"); assert.equal(opened.at(-1).module, module);
	}
	const component = window.JifengPlatingColor; delete window.JifengPlatingColor;
	for (const module of ["产品", "订单"]) {
		workspace.openPage("镀色", module); assert.equal(opened.length, 2); assert.match(messages.at(-1), /镀色页面未能加载/);
	}
	window.JifengPlatingColor = component;
	await deleteModels();
	console.log("Plating color protocol models passed: authorized creation, six fields, permanent deleted-request recovery/new UUID, confirmed deletion, exact identity, unknown-result GET recovery, mask/scope and no POST retries.");
}

async function creationModels(bench) {
	const opening = bench.openEditor(); calls.at(-1).resolve(empty()); await opening;
	const state = bench.editor;
	assert.equal(state.dialog.config.title, "产品镀色新增");
	assert.equal(state.dialog.config.fields.filter((f) => !f.hidden).map((f) => f.label).join("/"), "编号/代码/名称/色系/备注/停用");
	await state.dialog.set_values({ code: "new-code", value: "new-color", color_system: "new-family", remarks: "new-note", inactive: 0 });
	const saving = bench.save(state), request = calls.at(-1).options.args.payload;
	assert.ok(request.request_id); assert.equal(request.name, undefined);
	assert.equal("legacy_number" in request, false);
	calls.at(-1).reject(new Error("response lost")); await saving;
	const posts = calls.filter((call) => call.options.method.endsWith("save_dictionary")).length;
	const unresolved = bench.resolve(state);
	assert.equal(calls.at(-1).options.type, "GET");
	calls.at(-1).resolve({ status: "deleted", request_id: "wrong", company: "Company A", dictionary_type: "镀色", name: "old", creation: "2026-10-01T00:00:01", version: "2026-10-01T00:00:02" }); await unresolved;
	assert.equal(state.unresolved, true, "a mismatched receipt cannot release the draft");
	const resolved = bench.resolve(state);
	calls.at(-1).resolve({ status: "deleted", request_id: request.request_id, company: "Company A", dictionary_type: "镀色", name: "old", creation: "2026-10-01T00:00:01", version: "2026-10-01T00:00:02" }); await resolved;
	assert.equal(state.deleted, true); assert.equal(state.unresolved, false); assert.equal(state.dialog.button.disabled, true);
	await bench.save(state); assert.equal(calls.filter((call) => call.options.method.endsWith("save_dictionary")).length, posts);
	calls.at(-1).resolve(result()); await tick();
	bench.newDraft(state); calls.at(-1).resolve(empty()); await tick();
	assert.equal(state.deleted, false, "async shared reset must clear the deleted state after it completes");
	assert.notEqual(state.requestId, request.request_id); assert.equal(state.dialog.button.disabled, false);
	await state.dialog.set_values({ code: "another", value: "another-color", color_system: "another-family", remarks: "another-note", inactive: 0 });
	const newSave = bench.save(state);
	calls.at(-1).resolve(color({ name: "NEW-COLOR", code: "another", value: "another-color", color_system: "another-family", remarks: "another-note", inactive: 0 })); await newSave;
	assert.equal(state.archive.name, "NEW-COLOR"); assert.equal(state.deleted, false);
	calls.at(-1).resolve(result()); await tick(); state.dialog.hide();
}

async function deleteModels() {
	let confirmation;
	const originalConfirm = frappe.confirm;
	frappe.confirm = (message, yes, no) => { confirmation = { message, yes, no }; };
	try {
		const bench = new window.JifengPlatingColor(new Element());
		calls.at(-1).resolve(empty({ company: null })); await bench.ready;
		bench.companyControl.set_value("Company A");
		const exact = row({ name: "COLOR-A", creation: "2026-10-01 00:00:01.123456", version: "2026-10-01T00:00:02.654321" });
		calls.at(-1).resolve(result({ total: 1, rows: [exact] })); await tick();
		const before = calls.length;
		bench.confirmDelete({ ...exact, can_delete: false }); assert.equal(calls.length, before); assert.equal(confirmation, undefined);
		bench.confirmDelete(bench.rows[0]); assert.equal(calls.length, before, "no POST before explicit confirmation");
		confirmation.no(); assert.equal(bench.deletion, null);
		bench.confirmDelete(bench.rows[0]); const deleting = confirmation.yes();
		const held = calls.at(-1).options.args.payload;
		assert.equal(calls.at(-1).options.method, "jifeng_erp.plating_color.delete_dictionary"); assert.equal(calls.at(-1).options.type, "POST");
		assert.equal(held.creation, exact.creation); assert.equal(held.version, exact.version);
		calls.at(-1).reject(new Error("response lost")); await deleting;
		assert.ok(bench.getCloseBlock()); assert.equal(bench.deletion.unresolved, true);
		const posts = calls.filter((call) => call.options.method.endsWith("delete_dictionary")).length;
		bench.confirmDelete(bench.rows[0]); await bench.deleteColor(bench.deletion);
		assert.equal(calls.filter((call) => call.options.method.endsWith("delete_dictionary")).length, posts);
		const unknown = bench.resolveDelete(); assert.equal(calls.at(-1).options.type, "GET");
		calls.at(-1).resolve({ status: "unknown" }); await unknown; assert.ok(bench.deletion);
		const changedFormat = bench.resolveDelete();
		calls.at(-1).resolve({ status: "deleted", ...held, creation: held.creation.replace(" ", "T") }); await changedFormat;
		assert.ok(bench.deletion, "wire timestamps remain exact; backend must echo verified held strings without JS Date rounding");
		assert.equal(bench.deletion.payload.creation, "2026-10-01 00:00:01.123456");
		assert.equal(bench.deletion.payload.version, "2026-10-01T00:00:02.654321");
		const replaced = bench.resolveDelete();
		calls.at(-1).resolve({ status: "deleted", ...held, creation: "2026-10-02T00:00:00" }); await replaced;
		assert.ok(bench.deletion, "same name with another creation is not this deletion receipt");
		const resolved = bench.resolveDelete(); calls.at(-1).resolve({ status: "deleted", ...held }); await resolved;
		assert.equal(bench.deletion, null); assert.equal(bench.getCloseBlock(), "");
		const replacement = row({ name: exact.name, creation: "2026-10-02T00:00:00", version: "2026-10-02T00:00:01" });
		calls.at(-1).resolve(result({ total: 1, rows: [replacement] })); await tick();
		assert.equal(bench.rows[0].creation, replacement.creation, "completed old deletion never removes a same-name replacement");
		bench.confirmDelete(bench.rows[0]); const denied = confirmation.yes();
		calls.at(-1).reject({ responseJSON: { exc_type: "PermissionError" } }); await denied;
		assert.equal(bench.deletion, null); calls.at(-1).resolve(result({ total: 1, rows: [replacement] })); await tick();
		assert.equal(calls.filter((call) => call.options.method.endsWith("delete_dictionary")).length, posts + 1);
		bench.destroy();
	} finally { frappe.confirm = originalConfirm; }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
