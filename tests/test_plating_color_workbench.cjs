// Protocol/DOM models for the observed color fields; no browser or server writes.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const { Element, context, window, calls, messages, fieldMeta, pack, blank, listRow, tick } = require("./test_product_dictionary_workbench.cjs");
for (const file of ["jifeng_order_dictionary", "jifeng_plating_color", "jifeng_workspace_core"]) vm.runInNewContext(fs.readFileSync(`jifeng_erp/public/js/${file}.js`, "utf8"), context);
const metadata = () => ({ ...fieldMeta(), abbreviation: { fieldtype: "Data", read_only: true },
	code: { fieldtype: "Data", description: "代码生成条码时用" }, color_system: { fieldtype: "Data" } });
const color = (extra = {}) => pack({ dictionary_type: "镀色", code: " 001 ", color_system: " 暖色 ", field_meta: metadata(), ...extra });
const empty = (extra = {}) => blank({ dictionary_type: "镀色", code: null, color_system: null, field_meta: metadata(), ...extra });
const row = (extra = {}) => listRow({ dictionary_type: "镀色", code: "001", color_system: "暖色", ...extra });
const result = (extra = {}) => ({ company: "Company A", dictionary_type: "镀色", search: "", include_inactive: 0, start: 0, page_length: 25, total: 0, rows: [], ...extra });

async function main() {
	const bridge = Object.create(window.JifengClassicBridge.prototype);
	const entry = bridge.mount(new Element(), { platingColor: true });
	const bench = entry.workbench;
	assert.equal(calls.at(-1).options.method, "jifeng_erp.plating_color.init_dictionary");
	assert.equal(calls.at(-1).options.args.dictionary_type, "镀色");
	calls.at(-1).resolve(empty({ company: null, number_preview: null })); await entry.ready;
	assert.equal(bench.host.descendants("th").map((e) => e.textContent).join("/"), "代码/编号/名称/色系/备注/简拼/停用/操作/日期/修改/修改日期/修改/删除");
	assert.equal(bench.addButton.disabled, true, "plating color creation remains disabled");
	bench.companyControl.set_value("Company A"); calls.at(-1).resolve(result({ total: 1, rows: [row()] })); await tick();
	assert.equal(bench.tbody.descendants("td").length, 13);
	for (const extra of [{ dictionary_type: "订单类型" }, { company: "Other Company" }, { code: {} }, { color_system: [] }]) {
		const query = bench.query(); calls.at(-1).resolve(result({ total: 1, rows: [row(extra)] })); await query;
		assert.equal(bench.rows.length, 0, "wrong scope or nontext color fields are rejected before rendering");
	}
	const query = bench.query(); calls.at(-1).resolve(result()); await query;
	const blockedCalls = calls.length; await bench.openEditor(); assert.equal(calls.length, blockedCalls, "disabled plating color creation cannot be opened programmatically");
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
	console.log("Plating color protocol models: observed 13 columns/6 fields, disabled creation, existing-edit fields, scope and response guards, readonly/mask, unknown-result recovery and no POST retries passed.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
