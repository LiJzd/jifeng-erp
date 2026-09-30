// Verify transport uncertainty cannot create a second Item or discard a draft.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const nodeCrypto = require("node:crypto");
const calls = [], messages = [], window = {};
let reply, confirmation;
const frappe = {
	call: async (options) => { calls.push(options); if (reply instanceof Error) throw reply; return { message: reply }; },
	msgprint: (message) => messages.push(message), show_alert() {},
	confirm: (_, yes, no) => { confirmation = { yes, no }; },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_inventory_archive.js"), "utf8"),
	{ window, frappe, structuredClone, __: (text) => text, queueMicrotask,
		crypto: { getRandomValues: (bytes) => nodeCrypto.randomFillSync(bytes) } });
const editor = new window.JifengInventoryArchive({ frames: new Map() });

function fixture(name = null) {
	const archive = { name, kind: "材料", can_write: true, profile_version: name ? "v1" : null,
		item_version: name ? "i1" : null, item_code: name ? "ITEM-1" : null,
		profile: { company: "Company", display_name: "材料A", list_price: 0 },
		item: { item_group: "Group", stock_uom: "Nos" },
		field_meta: { profile: { company: { read_only: !!name }, display_name: {}, list_price: {} },
			item: { item_group: { read_only: !!name }, stock_uom: { read_only: !!name } } } };
	const bindings = [], fields = {};
	for (const group of ["profile", "item"]) for (const fieldname of Object.keys(archive[group])) {
		const controlName = group + "_" + fieldname;
		fields[controlName] = { value: archive[group][fieldname], df: { ...archive.field_meta[group][fieldname] },
			get_value() { return this.value; } };
		bindings.push({ group, fieldname, controlName });
	}
	fields.copy_archive = { value: 0, df: {} };
	fields.business_item = { value: "", df: {} }; fields.business_name = { value: "", df: {} };
	let hidden = false;
	const button = { disabled: false, prop(_, value) { this.disabled = value; return this; }, text() {} };
	const dialog = { fields_dict: fields, hide() { hidden = true; },
		$wrapper: { modal() {}, on() {} }, get_close_btn: () => ({ removeAttr: () => ({ on() {} }) }),
		get_values: () => ({}), get_primary_btn: () => button, set_title() {},
		async set_values(values) { for (const [key, value] of Object.entries(values)) fields[key].value = value; },
		async set_value(key, value) { fields[key].value = value; },
		get_value: (key) => fields[key].value,
		set_df_property(key, property, value) { fields[key].df[property] = value; } };
	const state = { archive, kind: "材料", requestId: "646e6cc9-d1a0-46de-a0cf-ae25f3b5d92", dialog, bindings,
		saving: false, unresolved: false, confirming: false, closed: false };
	state.original = JSON.stringify(editor.payload(state));
	return { state, fields, button, get hidden() { return hidden; } };
}

async function main() {
	assert.match(editor.requestID(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
		"UUID4 also works when randomUUID is unavailable on an HTTP LAN origin");
	const f = fixture();
	assert.equal(editor.payload(f.state).profile.list_price, 0, "zero reference price is retained");
	assert.equal(editor.payload(f.state).profile.company, "Company");
	f.fields.profile_display_name.value = "用户草稿";
	const validation = new Error("atomic server validation"); validation.responseJSON = { exc: "ValidationError" };
	reply = validation;
	await editor.save(f.state);
	assert.equal(f.state.unresolved, false);
	assert.equal(f.fields.profile_display_name.value, "用户草稿");
	assert.equal(f.button.disabled, false);
	assert.equal(editor.isDirty(f.state), true);

	reply = new Error("connection lost");
	await editor.save(f.state);
	const unknownCalls = calls.length;
	assert.equal(f.state.unresolved, true);
	assert.equal(f.fields.profile_display_name.df.read_only, true);
	assert.equal(f.button.disabled, true);
	assert.equal(f.state.candidate.profile.display_name, "用户草稿");
	await editor.save(f.state);
	assert.equal(calls.length, unknownCalls, "unknown result cannot repeat a POST");
	editor.guardClose(f.state); f.state.dialog.hide();
	assert.equal(f.hidden, false, "unknown result cannot discard the receipt identity");

	reply = { status: "not_found" };
	await editor.resolve(f.state);
	assert.equal(calls.at(-1).method, "jifeng_erp.inventory_archive.resolve_creation");
	assert.equal(f.state.unresolved, true, "not found is not permission to retry creation");
	assert.equal(calls.filter((call) => call.method.endsWith("save_archive")).length, 2);
	const saved = { ...f.state.archive, name: "PROFILE-1", item_code: "ITEM-1", item_name: "用户草稿",
		profile_version: "v2", item_version: "i2", profile: { ...f.state.candidate.profile },
		field_meta: { profile: { ...f.state.archive.field_meta.profile, company: { read_only: true } },
			item: { item_group: { read_only: true }, stock_uom: { read_only: true } } } };
	reply = { status: "found", archive: saved };
	await editor.resolve(f.state);
	assert.equal(f.state.unresolved, false); assert.equal(f.button.disabled, false);
	assert.equal(f.state.archive.name, "PROFILE-1");
	assert.equal(editor.payload(f.state).profile_version, "v2");
	assert.equal(editor.payload(f.state).request_id, null, "updates do not forge or replace the creation receipt");
	assert.equal(Object.keys(editor.payload(f.state).item).length, 0, "existing shared Item fields are absent");
	assert.ok(!("company" in editor.payload(f.state).profile), "saved Company is immutable");
	assert.equal(editor.isDirty(f.state), false);

	f.fields.profile_display_name.value = "另一个草稿";
	f.state.dialog.hide(); assert.ok(confirmation); confirmation.no();
	assert.equal(f.hidden, false); assert.equal(f.state.confirming, false);
	assert.equal(f.fields.profile_display_name.value, "另一个草稿");
	let copies = 0; editor.open = async () => { copies++; };
	await editor.copy(f.state); assert.equal(copies, 0, "copy must not silently omit unsaved source changes");

	const existing = fixture("PROFILE-2");
	existing.fields.profile_display_name.value = "新的名称";
	reply = new Error("update response lost"); await editor.save(existing.state);
	reply = { ...existing.state.archive, profile: { ...existing.state.archive.profile, display_name: "其他人的变更" } };
	await editor.resolve(existing.state);
	assert.equal(calls.at(-1).method, "jifeng_erp.inventory_archive.read_archive");
	assert.equal(existing.state.unresolved, true, "a different saved value cannot overwrite the held draft");
	assert.equal(existing.state.candidate.profile.display_name, "新的名称");

	const malformed = fixture(); reply = {};
	await editor.save(malformed.state); assert.equal(malformed.state.unresolved, true);
	assert.equal(malformed.button.disabled, true, "malformed success still requires read verification");
	console.log("Inventory archive protocol checks passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
