// DOM/RPC models: batch edits, source versions, stale responses, and unknown saves.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), crypto = require("node:crypto");
const tick = () => new Promise(setImmediate);
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this.dataset = {}; this.attributes = {}; this.value = ""; this._text = ""; this.classes = new Set(); this.classList = { add: (...names) => names.forEach(name => this.classes.add(name)), remove: name => this.classes.delete(name) }; }
	append(...nodes) { this.children.push(...nodes); }
	appendChild(node) { this.append(node); }
	replaceChildren(...nodes) { this.children = nodes; this._text = ""; }
	set textContent(text) { this._text = String(text); this.children = []; }
	get textContent() { return this._text + this.children.map(node => node.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("Source HTML must not execute"); }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, action) { (this.events[name] ||= []).push(action); }
	remove() { this.removed = true; }
	all(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.all(tag)]); }
	querySelectorAll(selector) { const tag = selector.split("[")[0]; return this.all(tag).filter(node => !selector.includes("[") || (tag === "input" ? node.dataset.rowKey : node.dataset.deleteKey)); }
}
const fields = { major: ["legacy_code", "value", "owner_name", "region", "pinyin", "inactive"], minor: ["value", "material", "display_uom", "conversion_uom_text", "conversion_value", "inactive", "notes", "pinyin"] };
const meta = type => Object.fromEntries(fields[type].map(field => [field, { fieldtype: field === "inactive" ? "Check" : "Data", read_only: false }]));
const config = (kind = "物料", company = null) => ({ kind, company, can_create: true, can_export: true, major_meta: meta("major"), minor_meta: meta("minor") });
const row = (name, type = "major", kind = "物料", parent = null) => ({ name, version: "2026-10-01T00:00:00", archive_kind: kind, company: "JF", dictionary_type: type === "major" ? "大类" : "小类", parent_dictionary: parent, value: name + "原文", inactive: 0, can_write: true, can_delete: true, field_meta: meta(type), conversion_value: type === "minor" ? "1.2500" : null });
const query = (type, rows, category = null, kind = "物料", more = false) => ({ kind, company: "JF", record_type: type, category, category_version: category ? "2026-10-01T00:00:00" : null, rows, has_more: more, offset: 0 });
function environment() {
	const env = { calls: [], messages: [], listeners: new Set(), dialogs: [], confirmations: [] };
	const window = { addEventListener(_, handler) { env.listeners.add(handler); }, removeEventListener(_, handler) { env.listeners.delete(handler); } };
	class Dialog {
		constructor(options) { this.options = options; this.fields_dict = Object.fromEntries(options.fields.map(df => [df.fieldname, { df, value: df.default, $wrapper: { get: () => new Element() } }])); this.button = { prop() { return this; } }; env.dialogs.push(this); }
		get_values() { return Object.fromEntries(Object.entries(this.fields_dict).map(([key, control]) => [key, control.value])); }
		get_primary_btn() { return this.button; } show() {} hide() { this.on_hide?.(); }
	}
	const frappe = { ui: { Dialog, form: { make_control({ df }) { return { value: null, get_value() { return this.value; }, async set_value(value) { this.value = value; await df.onchange?.(); } }; } } },
		call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); env.calls.push({ options, resolve: message => resolve({ message }), reject }); return promise; },
		msgprint: text => env.messages.push(text), show_alert() {}, confirm(_, yes, no) { env.confirmations.push({ yes, no }); } };
	const context = { window, document: { createElement: tag => new Element(tag) }, crypto: { getRandomValues: bytes => crypto.randomFillSync(bytes) }, frappe, structuredClone, __: text => text, Uint8Array };
	for (const file of ["jifeng_inventory_category", "jifeng_inventory_category_io"]) vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/" + file + ".js"), "utf8"), context);
	Object.assign(env, { window, frappe }); return env;
}
async function create(env, kind = "物料", parents = [row("M1"), row("M2")]) {
	const bench = new env.window.JifengInventoryCategory(new Element(), kind); env.calls.at(-1).resolve(config(kind)); await bench.ready;
	bench.companyControl.value = "JF"; const changing = bench.changeCompany(); env.calls.at(-1).resolve(config(kind, "JF")); await tick();
	assert.match(env.calls.at(-1).options.method, /query_grid$/); env.calls.at(-1).resolve(query("major", parents, null, kind)); await tick();
	if (parents.length) env.calls.at(-1).resolve(query("minor", [row("C1", "minor", kind, parents[0].name)], parents[0].name, kind));
	await changing; return bench;
}
function edit(bench, key, field, value) {
	const input = bench.panels[bench.records.get(key).type].body.all("input").find(node => node.dataset.rowKey === key && node.dataset.field === field);
	assert.ok(input, key + "/" + field); if (field === "inactive") input.checked = !!value; else input.value = value;
	for (const action of input.events[field === "inactive" ? "change" : "input"]) action();
}
function result(bench, changes = {}) {
	return { status: "applied", request_id: bench.candidate.payload.request_id, digest: bench.candidate.digest, kind: bench.kind, company: bench.company,
		rows: bench.candidate.payload.rows.map((entry, index) => ({ row_id: entry.row_id, operation: entry.operation, name: entry.name || "SAVED-" + index, version: "2026-10-01T00:00:01" })), ...changes };
}
async function startSave(env, bench) {
	const saving = bench.save(); assert.match(env.calls.at(-1).options.method, /preview_batch$/);
	const payload = env.calls.at(-1).options.args.payload; env.calls.at(-1).resolve({ kind: bench.kind, company: bench.company, request_id: payload.request_id, row_count: payload.rows.length, digest: "a".repeat(64) });
	await tick(); assert.match(env.calls.at(-1).options.method, /save_batch$/); return { saving, post: env.calls.at(-1) };
}
async function main() {
	const env = environment(), bench = await create(env);
	assert.equal(bench.panels.major.head.textContent, "代码大类负责区域停用删除");
	assert.equal(bench.panels.minor.head.textContent, "小类材质单位换算单位换算值停用操作日期修改修改时间删除");
	assert.equal(bench.selected, "M1"); assert.equal(bench.isDirty(), false);
	edit(bench, "M1", "owner_name", "新负责"); edit(bench, "C1", "conversion_value", "0");
	bench.select("M2"); assert.equal(bench.panels.minor.body.all("input").some(input => input.dataset.rowKey === "C1"), false, "old parent's children disappear immediately");
	env.calls.at(-1).resolve(query("minor", [row("C2", "minor", "物料", "M2")], "M2")); await tick(); edit(bench, "C2", "material", "<svg onload=alert(1)>");
	const payload = bench.collectPayload(); assert.equal(payload.rows.length, 3); assert.equal(payload.rows.find(row => row.name === "C1").values.conversion_value, "0");
	assert.equal(payload.rows.find(row => row.name === "C1").category, "M1"); assert.equal(payload.rows.find(row => row.name === "C2").category, "M2");
	assert.equal(payload.rows.find(row => row.name === "M1").values.owner_name, "新负责");
	bench.companyControl.value = "OTHER"; await bench.changeCompany(); assert.equal(bench.company, "JF"); assert.equal(bench.companyControl.get_value(), "JF");
	const unknown = await startSave(env, bench); unknown.post.reject(new Error("response lost")); await unknown.saving;
	assert.equal(bench.unresolved, true); assert.equal(bench.saveButton.disabled, true); assert.equal(bench.discardButton.disabled, true);
	const writes = env.calls.length; await bench.save(); bench.discard(); await bench.changeCompany(); assert.equal(env.calls.length, writes, "unknown save cannot repeat POST or discard");
	let resolving = bench.resolve(); assert.match(env.calls.at(-1).options.method, /resolve_batch$/); env.calls.at(-1).resolve({ status: "not_found" }); await resolving; assert.equal(bench.unresolved, true);
	resolving = bench.resolve(); env.calls.at(-1).resolve(result(bench, { digest: "b".repeat(64) })); await resolving; assert.equal(bench.unresolved, true, "wrong receipt cannot clear drafts");
	bench.query = async () => {}; resolving = bench.resolve(); env.calls.at(-1).resolve(result(bench)); await resolving;
	assert.equal(bench.unresolved, false); assert.equal(bench.isDirty(), false); assert.equal(bench.records.get("C1").source.version, "2026-10-01T00:00:01");
	assert.equal(env.calls.filter(call => call.options.method.endsWith("save_batch")).length, 1);

	const freshEnv = environment(), fresh = await create(freshEnv, "物料", []);
	fresh.addRow("major"); const major = fresh.records.get(fresh.selected); edit(fresh, major.key, "value", "新大类"); fresh.addRow("minor");
	const minor = [...fresh.records.values()].find(record => record.type === "minor" && !record.blank); edit(fresh, minor.key, "value", "新小类"); edit(fresh, minor.key, "conversion_value", "1.2500");
	const newPayload = fresh.collectPayload(); assert.equal(newPayload.rows.length, 2); assert.equal(newPayload.rows[1].parent_row_id, newPayload.rows[0].row_id);
	const saving = await startSave(freshEnv, fresh); const validation = new Error("atomic validation"); validation.responseJSON = { exc: "ValidationError" }; saving.post.reject(validation); await saving.saving;
	assert.equal(fresh.unresolved, false); assert.equal(fresh.isDirty(), true); assert.equal(minor.values.conversion_value, "1.2500");
	fresh.query = async () => {}; const accepted = await startSave(freshEnv, fresh); accepted.post.resolve(result(fresh)); await accepted.saving;
	assert.equal(fresh.isDirty(), false); const child = [...fresh.records.values()].find(record => record.type === "minor"); assert.equal(child.parentKey, fresh.selected); assert.equal(child.parentVersion, "2026-10-01T00:00:01");

	const raceEnv = environment(), race = await create(raceEnv); const old = race.load("minor", 0), oldCall = raceEnv.calls.at(-1);
	race.select("M2"); const current = raceEnv.calls.at(-1); current.resolve(query("minor", [row("C2", "minor", "物料", "M2")], "M2")); await tick();
	oldCall.resolve(query("minor", [row("WRONG", "minor", "物料", "M1")], "M1")); await old; assert.deepEqual(Array.from(race.rows.minor), ["C2"]);
	edit(race, "C2", "material", "用户草稿"); const update = race.load("minor", 0); raceEnv.calls.at(-1).resolve(query("minor", [{ ...row("C2", "minor", "物料", "M2"), version: "2026-10-01T00:00:05", material: "他人新值" }], "M2")); await update;
	assert.equal(race.records.get("C2").values.material, "用户草稿"); assert.equal(race.records.get("C2").source.version, "2026-10-01T00:00:00"); assert.equal(race.records.get("C2").conflict, true);

	const readonlyEnv = environment(), readonly = await create(readonlyEnv); readonly.records.get("C1").meta.material.read_only = true; readonly.freeze(); edit(readonly, "C1", "material", "拒绝"); assert.equal(readonly.isDirty(), false);
	readonly.records.get("M1").canDelete = false; readonly.toggleDelete(readonly.records.get("M1")); assert.equal(readonly.isDirty(), false);
	const materialEnv = environment(), material = await create(materialEnv, "材料", [row("M1", "major", "材料")]); assert.equal(material.panels.major.head.textContent, "代码大类负责简拼停用删除");
	assert.equal(material.panels.minor.head.textContent, "小类材质单位换算删除", "material category uses only the default columns supported by its screenshot");
	const importedEnv = environment(), imported = await create(importedEnv); const parentID = importedEnv.window.JifengInventoryCategory.uuid(), childID = importedEnv.window.JifengInventoryCategory.uuid();
	importedEnv.window.JifengInventoryCategoryIO.stage(imported, { kind: "物料", company: "JF", sources: {}, rows: [
		{ row_id: parentID, record_type: "major", operation: "upsert", values: { value: "文件大类", legacy_code: "0008" } },
		{ row_id: childID, record_type: "minor", operation: "upsert", parent_row_id: parentID, values: { value: "文件小类", conversion_value: "0" } }] });
	const importedPayload = imported.collectPayload(); assert.equal(importedPayload.rows.length, 2); assert.equal(importedPayload.rows[0].values.legacy_code, "0008"); assert.equal(importedPayload.rows[1].parent_row_id, parentID); assert.equal(importedEnv.calls.filter(call => call.options.method.endsWith("save_batch")).length, 0, "file staging cannot save");
	for (const [e, b] of [[env, bench], [freshEnv, fresh], [raceEnv, race], [readonlyEnv, readonly], [materialEnv, material], [importedEnv, imported]]) { b.destroy(); assert.equal(e.listeners.size, 0); }
	console.log("Inventory related grids: field order, cross-parent drafts, original versions, atomic save receipt/unknown guards, stale queries, role masks and XLSX staging passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
