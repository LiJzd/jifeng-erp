// Real picker code with deferred RPCs; no browser or database acceptance.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const tick = () => new Promise(setImmediate);
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this._text = ""; }
	append(...nodes) { this.children.push(...nodes); } replaceChildren(...nodes) { this.children = nodes; this._text = ""; }
	set textContent(value) { this._text = String(value); this.children = []; } get textContent() { return this._text + this.children.map(child => child.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("Category text must not execute HTML"); }
	addEventListener(event, action) { this.events[event] = action; }
	all(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.all(tag)]); }
}
function environment() {
	const env = { calls: [], dialogs: [], messages: [] }, window = {};
	class Dialog {
		constructor(options) { this.options = options; this.fields_dict = Object.fromEntries(options.fields.map(df => [df.fieldname, { df, value: "", host: new Element() }])); for (const control of Object.values(this.fields_dict)) control.$wrapper = { get: () => control.host }; env.dialogs.push(this); }
		get_value(name) { return this.fields_dict[name]?.value; } show() { this.shown = true; } hide() { this.shown = false; this.on_hide?.(); }
	}
	const frappe = { ui: { Dialog }, msgprint: message => env.messages.push(message), call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); env.calls.push({ options, resolve: message => resolve({ message }), reject }); return promise; } };
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_inventory_archive.js"), "utf8"), { window, frappe, document: { createElement: tag => new Element(tag) }, __: text => text });
	env.editor = new window.JifengInventoryArchive({});
	const fields = ["company", "major_category", "minor_category", "material", "display_uom", "conversion_uom", "conversion_value", "owner_name", "region"];
	const controls = Object.fromEntries(fields.map(field => ["profile_" + field, { value: field === "company" ? "JF" : "原草稿" + field, df: { read_only: false } }]));
	const button = { prop(_, value) { this.disabled = value; return this; } }, dialog = { fields_dict: controls, get_primary_btn: () => button,
		get_value(name) { return controls[name]?.value; }, set_df_property(name, field, value) { controls[name].df[field] = value; },
		async set_values(values) { for (const [name, value] of Object.entries(values)) controls[name].value = value; } };
	env.state = { kind: "物料", dialog, closed: false, saving: false, linking: false, unresolved: false, archive: { can_write: true,
		field_meta: { profile: Object.fromEntries(fields.map(field => [field, { read_only: false }])) } } };
	env.controls = controls; return env;
}
const category = { name: "C1", category: "M1", version: "2026-10-01T00:00:00.123456", category_version: "2026-10-01T00:00:00.654321", legacy_code: "0009",
	minor_category: "小类原文", major_category: "大类原文", material: "<svg onload=alert(1)>", display_uom: "原文单位", conversion_uom: "原文换算单位", conversion_value: "1.2500", owner_name: "负责原文", region: "区域原文", pinyin: "PY" };
const result = () => ({ kind: "物料", company: "JF", name: category.name, category: category.category, version: category.version,
	category_version: category.category_version, profile: Object.fromEntries(["major_category", "minor_category", "material", "display_uom", "conversion_uom", "conversion_value", "owner_name", "region"].map(field => [field, category[field]])) });
async function open(env) {
	const opening = env.editor.selectCategory(env.state); const dialog = env.dialogs.at(-1); assert.equal(dialog.options.title, "物料类别词典");
	assert.match(env.calls.at(-1).options.method, /query_selection$/); env.calls.at(-1).resolve({ kind: "物料", company: "JF", rows: [category], has_more: false }); await opening;
	return { dialog, host: dialog.fields_dict.results.host, line: dialog.fields_dict.results.host.all("tbody")[0].children[0] };
}
async function main() {
	let env = environment(), picker = await open(env);
	assert.equal(picker.host.all("thead")[0].textContent, "代码小类大类材质单位换算单位换算值负责区域简拼");
	assert.ok(picker.host.textContent.includes("<svg onload=alert(1)>"), "hostile source text is rendered literally");
	env.state.archive.field_meta.profile.region.read_only = true;
	let selecting = picker.line.events.click(); assert.equal(env.state.linking, true); assert.equal(env.controls.profile_company.df.read_only, true);
	const call = env.calls.at(-1); assert.match(call.options.method, /select_category$/); assert.equal(call.options.args.version, category.version); assert.equal(call.options.args.category_version, category.category_version);
	call.resolve(result()); await selecting; assert.equal(env.controls.profile_major_category.value, category.major_category); assert.equal(env.controls.profile_minor_category.value, category.minor_category);
	assert.equal(env.controls.profile_conversion_value.value, "1.2500"); assert.equal(env.controls.profile_region.value, "原草稿region", "readonly archive fields never receive category values");
	assert.equal(env.state.linking, false); assert.equal(env.controls.profile_company.df.read_only, false); assert.equal(env.state.categoryChooser, null);
	assert.equal(env.calls.filter(call => /save|insert|update/.test(call.options.method)).length, 0, "choosing never saves the archive or dictionary");
	for (const invalidation of ["company", "draft", "closed", "wrong-identity", "denied", "closed-picker"]) {
		env = environment(); picker = await open(env); const original = env.controls.profile_major_category.value; selecting = picker.line.events.click();
		if (invalidation === "company") env.controls.profile_company.value = "OTHER";
		if (invalidation === "draft") env.controls.profile_minor_category.value = "新输入";
		if (invalidation === "closed") env.state.closed = true;
		if (invalidation === "closed-picker") picker.dialog.hide();
		if (invalidation === "denied") env.calls.at(-1).reject(new Error("permission denied")); else env.calls.at(-1).resolve({ ...result(), ...(invalidation === "wrong-identity" ? { category: "M2" } : {}) });
		await selecting; assert.equal(env.controls.profile_major_category.value, original, invalidation + " cannot replace the draft"); assert.equal(env.state.linking, false);
	}
	env = environment(); const opening = env.editor.selectCategory(env.state), oldCall = env.calls.at(-1), dialog = env.dialogs.at(-1);
	dialog.fields_dict.search.value = "新关键词"; dialog.fields_dict.search.df.onchange(); const newCall = env.calls.at(-1); newCall.resolve({ kind: "物料", company: "JF", rows: [], has_more: false }); await tick();
	oldCall.resolve({ kind: "物料", company: "JF", rows: [category], has_more: false }); await opening;
	assert.equal(dialog.fields_dict.results.host.all("tbody")[0].children.length, 0, "late search results cannot replace the latest empty result");
	console.log("Inventory category picker: 10 original columns, exact child/parent versions, readonly fills, draft/company/close guards and read-only selection passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
