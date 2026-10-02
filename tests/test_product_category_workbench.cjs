// DOM/protocol models only. No browser, database, or production network calls.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), crypto = require("node:crypto");
const calls = [], messages = [], listeners = new Map(); let confirmation;
const tick = () => new Promise(setImmediate);
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.value = ""; this.checked = false;
		this.classes = new Set(); this.classList = { add: (name) => this.classes.add(name) }; this.attributes = {}; this._text = ""; this.scrollTop = 0; this.scrollLeft = 0; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((child) => child.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("untrusted HTML rendering is forbidden"); }
	append(...nodes) { this.children.push(...nodes); }
	appendChild(node) { this.append(node); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	remove() { this.removed = true; }
	setAttribute(key, value) { this.attributes[key] = value; }
	addEventListener(event, action) { this.events.set(event, action); }
	descendants(tag) { return this.children.flatMap((child) => [ ...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
const window = { addEventListener(name, handler) { listeners.set(handler, name); }, removeEventListener(_, handler) { listeners.delete(handler); } };
const document = { createElement: (tag) => new Element(tag) };
const frappe = { pages: { "jifeng-legacy": {} }, ui: { form: { make_control({ df }) {
	return { df, value: null, get_value() { return this.value; }, async set_value(value) { this.value = value; await df.onchange(); } };
} } }, call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
	calls.push({ options, resolve: (message) => resolve({ message }), reject }); return promise; },
	msgprint(message) { messages.push(message); }, show_alert() {}, confirm(_, yes, no) { confirmation = { yes, no }; } };
	const context = { window, document, frappe, structuredClone, __: (text) => text, Uint8Array,
	crypto: { getRandomValues: (bytes) => crypto.randomFillSync(bytes) } };
for (const file of ["jifeng_product_category", "jifeng_classic_bridge"]) vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
const fields = { category: ["legacy_code", "major_name", "owner_name", "region", "inactive"],
	detail: ["minor_name", "material", "display_uom", "conversion_uom_text", "conversion_value", "inactive"] };
function meta(kind, extra = {}) {
	return { company: { fieldtype: "Link", options: "Company", read_only: true }, ...(kind === "detail" ? { category: { fieldtype: "Link", read_only: true } } : {}),
		...Object.fromEntries(fields[kind].map((field) => [field, { fieldtype: field === "inactive" ? "Check" : "Data", reqd: field.endsWith("_name") && !["owner_name"].includes(field), read_only: false }])), ...extra };
}
function pack(kind = "category", extra = {}) {
	return { record_type: kind, name: kind === "category" ? "CAT-1" : "DET-1", version: "2026-10-01T00:00:02", company: "Company A",
		...(kind === "category" ? { legacy_code: " 原始代码 ", major_name: " 大类甲 ", owner_name: " 负责人 ", region: null } :
			{ category: "CAT-1", minor_name: " 小类甲 ", material: null, display_uom: " 原文单位 ", conversion_uom_text: null, conversion_value: null }),
		inactive: 0, can_write: true, can_select: true, field_meta: meta(kind), ...extra };
}
function blank(kind = "category", extra = {}) {
	return pack(kind, { name: null, version: null, ...Object.fromEntries(fields[kind].map((field) => [field, field === "inactive" ? 0 : null])), can_select: false, ...extra });
}
function listRow(kind = "category", extra = {}) { const row = pack(kind, extra); delete row.field_meta; delete row.can_write; return row; }
async function initialize() {
	const bench = new window.JifengProductCategory(new Element());
	assert.equal(calls.at(-1).options.method, "jifeng_erp.product_category.init_record"); assert.equal(calls.at(-1).options.args.record_type, "category");
	calls.at(-1).resolve(blank("category", { company: null })); await bench.ready; return bench;
}
async function company(bench, name = "Company A", rows = []) {
	bench.companyControl.value = name; const changing = bench.changeCompany(); await tick();
	assert.equal(calls.at(-1).options.method, "jifeng_erp.product_category.list_records");
	assert.equal(calls.at(-1).options.args.include_inactive, 0, "Frappe form POST gets 0, not the string false");
	calls.at(-1).resolve(rows); await changing;
}
async function edit(bench, kind = "category", archive = blank(kind)) {
	const opening = bench.openEditor(kind, archive.name); await tick();
	assert.equal(calls.at(-1).options.method, "jifeng_erp.product_category." + (archive.name ? "read_record" : "init_record"));
	calls.at(-1).resolve(archive); await opening; return bench.editor;
}
async function main() {
	const bench = await initialize();
	assert.equal(bench.sameNumberText("", null), true);
	assert.equal(bench.sameNumberText("", "0"), false, "an omitted number is distinct from zero");
	assert.equal(bench.sameNumberText("-0", "0"), false, "signed zero remains distinct");
	assert.equal(bench.sameNumberText("-0.000", "-0"), true);
	assert.equal(bench.sameNumberText(".5", "0.5"), true); assert.equal(bench.sameNumberText("01.00", "1.00"), true);
	assert.equal(bench.sameNumberText("1e3", "1E+3"), true); assert.equal(bench.sameNumberText(" 1_00.00 ", "100.00"), true);
	assert.equal(bench.sameNumberText("1e999999999999999999999999999999", "10e999999999999999999999999999998"), true, "exponents are BigInt tokens, never float or expanded powers");
	assert.equal(bench.sameNumberText("0.1234567890123456789012345678901234567890", "0.1234567890123456789012345678901234567891"), false, "long decimal differences do not round away");
	assert.equal(bench.panels.category.panel.descendants("th").map((node) => node.textContent).join("/"), "代码/大类/负责/区域/停用/删除");
	assert.equal(bench.panels.detail.panel.descendants("th").map((node) => node.textContent).join("/"), "小类/材质/单位/换算单位/换算值/停用/操作/删除");
	for (const label of ["导出", "导入", "更新简拼"]) assert.ok(bench.host.descendants("button").find((node) => node.textContent === label).disabled);
	assert.match(bench.limits.textContent, /仅保存当前编辑行/); assert.match(bench.limits.textContent, /尚未接入库存换算/);
	assert.ok(bench.panels.category.add.disabled); assert.ok(bench.panels.detail.add.disabled);
	await company(bench, "Company A", [listRow(), listRow("category", { name: "CAT-2", major_name: " 大类乙 " })]);
	assert.equal(bench.panels.category.add.disabled, false); assert.equal(bench.panels.category.tbody.descendants("td").length, 12);
	assert.equal(bench.panels.category.tbody.descendants("button").every((button) => button.disabled), true, "all deletion cells disabled");
	bench.includeInactive.checked = true; const query = bench.query(); await tick();
	assert.equal(calls.at(-1).options.args.include_inactive, 1, "Frappe form POST gets 1, not the string true");
	calls.at(-1).resolve([listRow(), listRow("category", { name: "CAT-2", major_name: " 大类乙 " })]); await query;
	const parent = bench.selectCategory(bench.rows.category[0]); await tick();
	assert.equal(calls.at(-1).options.args.record_type, "detail"); assert.equal(calls.at(-1).options.args.category, "CAT-1");
	assert.equal(calls.at(-1).options.args.include_inactive, 1);
	const hostile = '<svg onload="alert(1)">原文</svg>';
	calls.at(-1).resolve([listRow("detail", { material: hostile })]); await parent;
	assert.match(bench.panels.detail.tbody.textContent, /<svg/); assert.equal(bench.panels.detail.tbody.descendants("td").length, 8);
	assert.equal(bench.panels.detail.add.disabled, false); assert.match(bench.panels.detail.hint.textContent, /大类甲/);
	const oldDetails = bench.fetchDetails(), oldCall = calls.at(-1);
	const newParent = bench.selectCategory(bench.rows.category[1]); await tick(); const newCall = calls.at(-1);
	newCall.resolve([listRow("detail", { name: "DET-2", category: "CAT-2", minor_name: "乙小类" })]); await newParent;
	oldCall.resolve([listRow("detail")]); await oldDetails; assert.equal(bench.rows.detail[0].name, "DET-2", "late results from the old parent are ignored");

	let state = await edit(bench, "detail", blank("detail", { category: "CAT-2" }));
	assert.equal(state.controls.conversion_value.value, "", "empty value is not defaulted to one or zero");
	assert.equal(state.controls.conversion_value.type, "text");
	for (const text of ["", "0", "-0", "-12.3456789012345678901234567890123456789", "100000000000000000000000000000000000000.0000"]) {
		state.controls.conversion_value.value = text;
		assert.equal(bench.payload(state).conversion_value, text, "conversion values remain exact text with no Number conversion");
	}
	state.controls.minor_name.value = " 小类新 "; state.controls.material.value = hostile; state.controls.conversion_value.value = "-0";
	assert.equal(bench.payload(state).category, "CAT-2"); assert.equal(bench.payload(state).company, "Company A");
	assert.match(bench.payload(state).request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
	assert.ok(bench.isDirty());
	const queryCount = calls.length, deniedQuery = bench.query(); await tick(); assert.ok(confirmation); confirmation.no(); await deniedQuery;
	assert.equal(calls.length, queryCount); assert.equal(bench.editor, state); assert.equal(state.controls.material.value, hostile);
	bench.companyControl.value = "Company B"; const deniedCompany = bench.changeCompany(); await tick();
	assert.equal(bench.companyControl.get_value(), "Company A", "company control is restored while the draft decision is pending");
	confirmation.no(); await deniedCompany; assert.equal(bench.scopeCompany, "Company A"); assert.equal(bench.editor, state);
	const deniedParent = bench.selectCategory(bench.rows.category[0]); await tick(); confirmation.no(); await deniedParent;
	assert.equal(bench.selected.name, "CAT-2"); assert.equal(bench.editor, state, "parent switch cannot discard a detail draft without confirmation");

	const saving = bench.save(), saveCall = calls.at(-1);
	assert.equal(saveCall.options.method, "jifeng_erp.product_category.save_record"); assert.equal(saveCall.options.args.payload.conversion_value, "-0");
	assert.ok(bench.saveButton.disabled); assert.ok(state.controls.minor_name.disabled); assert.match(bench.getCloseBlock(), /正在读取或保存/);
	saveCall.reject(new Error("transport lost")); await saving;
	assert.equal(state.unresolved, true); assert.equal(bench.resolveButton.disabled, false); assert.ok(bench.saveButton.disabled); assert.ok(bench.cancelButton.disabled);
	const heldCount = calls.length; await bench.save(); await bench.query(); await bench.openEditor("category");
	assert.equal(calls.length, heldCount, "unknown save blocks destructive scope actions and never repeats the POST");
	const missing = bench.resolve(); assert.equal(calls.at(-1).options.method, "jifeng_erp.product_category.resolve_creation");
	assert.equal(calls.at(-1).options.args.category, "CAT-2"); assert.equal(calls.at(-1).options.args.record_type, "detail");
	assert.equal(calls.at(-1).options.args.request_id, state.candidate.request_id); calls.at(-1).resolve({ status: "not_found" }); await missing;
	assert.equal(state.unresolved, true);
	const partial = bench.resolve(); calls.at(-1).resolve({ status: "found", archive: { name: "DET-3" } }); await partial; assert.equal(state.unresolved, true);
	const wrongScope = bench.resolve(); calls.at(-1).resolve({ status: "found", archive: pack("detail", { category: "CAT-1" }) }); await wrongScope;
	assert.equal(state.conflict, null); assert.ok(bench.adoptButton.disabled);
	const exact = bench.resolve(); calls.at(-1).resolve({ status: "found", archive: pack("detail", {
		name: "DET-3", category: "CAT-2", minor_name: " 小类新 ", material: hostile, display_uom: null, conversion_uom_text: null, conversion_value: "-0" }) }); await exact;
	state = bench.editor; assert.equal(state.archive.name, "DET-3"); assert.equal(state.unresolved, false); assert.equal(bench.isDirty(), false);
	assert.equal(state.controls.conversion_value.value, "-0"); assert.ok(bench.saveButton.disabled, "unchanged saved rows do not emit no-op writes");
	assert.equal(bench.payload(state).company, undefined); assert.equal(bench.payload(state).category, undefined); assert.equal(bench.payload(state).request_id, undefined);
	state.controls.minor_name.value = " 本次改单 "; state.controls.minor_name.events.get("input")(); assert.equal(bench.saveButton.disabled, false);
	const versionFail = bench.save(); calls.at(-1).reject({ responseJSON: { exc_type: "TimestampMismatchError" } }); await versionFail;
	assert.equal(state.unresolved, false); assert.equal(state.controls.minor_name.value, " 本次改单 "); assert.ok(bench.isDirty());
	const unknown = bench.save(); calls.at(-1).reject({ responseJSON: { exc_type: "RuntimeError" } }); await unknown;
	assert.equal(state.unresolved, true);
	const conflict = bench.resolve(); assert.equal(calls.at(-1).options.method, "jifeng_erp.product_category.read_record");
	calls.at(-1).resolve(pack("detail", { name: "DET-3", category: "CAT-2", version: "2026-10-01T00:00:04", minor_name: " 另一人改单 ", material: hostile, conversion_value: "-0" })); await conflict;
	assert.match(bench.review.textContent, /本次改单/); assert.match(bench.review.textContent, /另一人改单/); assert.equal(bench.adoptButton.disabled, false);
	const postsBeforeAdopt = calls.filter((call) => call.options.method.endsWith("save_record")).length;
	bench.acceptCurrent(); confirmation.no(); assert.equal(bench.editor, state); assert.ok(state.unresolved);
	bench.acceptCurrent(); confirmation.yes(); state = bench.editor;
	assert.equal(state.controls.minor_name.value, " 另一人改单 "); assert.equal(bench.isDirty(), false);
	assert.equal(calls.filter((call) => call.options.method.endsWith("save_record")).length, postsBeforeAdopt, "explicit adoption is local, never a repeated write");

	// A save/recovery response refreshes permissions; omitted prior fields must not leak into the new editor.
	state.controls.minor_name.value = " 新可见名 "; state.controls.minor_name.events.get("input")();
	const permissionSave = bench.save(), newMeta = meta("detail"); delete newMeta.material;
	const reduced = pack("detail", { name: "DET-3", category: "CAT-2", version: "2026-10-01T00:00:05", minor_name: " 新可见名 ", field_meta: newMeta, can_write: false }); delete reduced.material;
	calls.at(-1).resolve(reduced); await permissionSave;
	state = bench.editor; assert.equal(state.controls.material, undefined); assert.equal(bench.payload(state).material, undefined); assert.ok(bench.saveButton.disabled);
	bench.clearEditor(); bench.selected.can_select = false; bench.freeze(); assert.ok(bench.panels.detail.add.disabled, "inactive/unselectable parent forbids new choices");
	const noChoose = await edit(bench, "detail", pack("detail", { name: "DET-2", category: "CAT-2", can_select: false }));
	assert.equal(noChoose.controls.minor_name.disabled, false, "independent historical text remains editable if the server permits it"); bench.clearEditor();
	for (const [typed, canonical] of [[".5", "0.5"], ["01.00", "1.00"], ["1e3", "1E+3"]]) {
		state = await edit(bench, "detail", blank("detail", { category: "CAT-2" }));
		state.controls.minor_name.value = "规范数值小类"; state.controls.conversion_value.value = typed;
		const canonicalSave = bench.save();
		assert.equal(calls.at(-1).options.args.payload.conversion_value, typed, "the POST keeps the exact entered text");
		calls.at(-1).resolve(pack("detail", { name: "NORMAL-" + typed, category: "CAT-2", minor_name: "规范数值小类", display_uom: null,
			material: null, conversion_uom_text: null, conversion_value: canonical })); await canonicalSave;
		assert.equal(bench.editor.unresolved, false, "equivalent Decimal normalization on a 200 response completes normally");
		assert.equal(bench.editor.controls.conversion_value.value, canonical); assert.equal(bench.isDirty(), false); bench.clearEditor();
	}

	// New company results are independently scoped; a malformed/late list cannot replace the current data.
	const bad = bench.query(); await tick(); calls.at(-1).resolve([listRow("category", { company: "Other Company" })]); await bad;
	assert.match(bench.status.textContent, /查询失败/); assert.equal(bench.rows.category[0].company, "Company A");
	bench.selected = null;
	const late = bench.fetchCategories(), lateCall = calls.at(-1); bench.search.value = "不同关键词"; bench.search.events.get("input")();
	const current = bench.fetchCategories(), currentCall = calls.at(-1); currentCall.resolve([listRow("category", { name: "CAT-4", major_name: hostile })]); await current;
	lateCall.resolve([listRow()]); await late; assert.equal(bench.rows.category[0].name, "CAT-4"); assert.match(bench.panels.category.tbody.textContent, /<svg/);
	assert.equal(bench.panels.category.tbody.descendants("td").length, 6);

	// The real visible menu mounts this workbench, never the previous local preview or an iframe list.
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_workspace_core.js"), "utf8"), context);
	window.Workspace = window.JifengERPWorkspace.Workspace; window.MenuModules = window.JifengERPWorkspace.modules;
	const workspace = Object.create(window.Workspace.prototype), opened = []; workspace.formalBridge = { open(spec) { opened.push(spec); } };
	workspace.addTab = () => { throw new Error("visible category menu fell back to a local preview"); };
	assert.ok(window.MenuModules.find((module) => module.name === "产品").groups.flatMap((group) => group.items).includes("类别"));
	workspace.openPage("类别", "产品"); assert.equal(opened.at(-1).productCategory, true); assert.equal(opened.at(-1).doctype, "Jifeng Product Category");
	const bridge = Object.create(window.JifengClassicBridge.prototype), mounted = bridge.mount(new Element(), opened[0]);
	assert.ok(mounted.workbench instanceof window.JifengProductCategory); assert.equal(mounted.iframe, undefined);
	calls.at(-1).resolve(blank("category", { company: null })); await mounted.ready;
	bridge.frames = new Map([["CATEGORY", mounted]]); mounted.workbench.scopeCompany = "Company A";
	const held = mounted.workbench.setEditor(blank(), "category"); held.controls.major_name.value = "未保存大类";
	assert.equal(bridge.canClose("CATEGORY"), false); held.unresolved = true; assert.match(bridge.closeBlock("CATEGORY"), /保存结果尚未确认/);
	mounted.stop(); assert.equal(mounted.workbench.disposed, true); assert.equal(listeners.has(mounted.workbench.beforeUnload), false);
	const delayed = bench.fetchCategories(), delayedCall = calls.at(-1); bench.destroy(); delayedCall.resolve([]); await delayed;
	assert.equal(listeners.has(bench.beforeUnload), false); assert.equal(bench.rows.category[0].name, "CAT-4", "disposed workbench ignores late data");
	console.log("Product category: original two grids, exact numeric texts, single-row writes, native scopes and permissions, dirty/unknown guards, read-only recovery and real menu routes passed (DOM/protocol models only).");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
