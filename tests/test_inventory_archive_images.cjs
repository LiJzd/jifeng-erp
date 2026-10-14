// Actual editor and picture component: draft/receipt models, no browser upload.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), crypto = require("node:crypto");
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this.attributes = {}; this.classList = { add() {} }; }
	append(...nodes) { this.children.push(...nodes); }
	setAttribute(key, value) { this.attributes[key] = value; }
	removeAttribute(key) { delete this.attributes[key]; if (key === "src") delete this.src; }
	addEventListener(key, handler) { this.events[key] = handler; }
	set innerHTML(_) { throw new Error("Picture names must remain text"); }
	click() { this.events.click?.(); }
}
function environment(name = "PROFILE") {
	const env = { calls: [], messages: [], created: [], revoked: [], confirmations: [] }, window = {};
	const frappe = { msgprint: text => env.messages.push(text), show_alert() {}, confirm(_, yes, no) { env.confirmations.push({ yes, no }); },
		call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
			env.calls.push({ options, resolve: message => resolve({ message }), reject }); return promise; } };
	const context = { window, document: { createElement: tag => new Element(tag) }, frappe, crypto, structuredClone, queueMicrotask,
		Uint8Array, btoa: binary => Buffer.from(binary, "binary").toString("base64"), __: text => text,
		URL: { createObjectURL(file) { const url = "blob:local-" + env.created.length; env.created.push({ file, url }); return url; }, revokeObjectURL: url => env.revoked.push(url) } };
	for (const file of ["jifeng_inventory_archive", "jifeng_inventory_archive_images"]) vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/" + file + ".js"), "utf8"), context);
	const editor = new window.JifengInventoryArchive({ frames: new Map() });
	const archive = { name, kind: "物料", profile_version: name ? "v1" : null, item_version: name ? "i1" : null, item_code: name ? "ITEM" : null,
		profile: { company: "JF", display_name: "档案原文", image: name ? "/private/files/old.png" : null },
		item: { item_group: "Group", stock_uom: "Nos" }, can_write: true, can_create_item: !name, can_link_item: !name,
		field_meta: { profile: { company: { read_only: !!name }, display_name: {}, image: {} }, item: { item_group: { read_only: !!name }, stock_uom: { read_only: !!name } } },
		image: { state: name ? "已上传" : "未上传", url: name ? "/private/files/old.png?fid=OLD" : "" },
		image_options: { can_choose: true, can_clear: true, maximum_bytes: 1024, extensions: [".png", ".jpg", ".jpeg", ".gif", ".webp"] } };
	const bindings = [], controls = {};
	for (const group of ["profile", "item"]) for (const field of Object.keys(archive[group])) {
		if (field === "image") continue;
		const key = group + "_" + field; controls[key] = { value: archive[group][field], df: { ...archive.field_meta[group][field] }, get_value() { return this.value; } };
		bindings.push({ group, fieldname: field, controlName: key });
	}
	controls.copy_archive = { value: 0, df: {} }; controls.business_item = { df: {} }; controls.business_name = { df: {} };
	const host = new Element(); controls.archive_image = { $wrapper: { get: () => host } };
	const button = { prop(_, value) { this.disabled = value; return this; }, text() {} };
	const dialog = { fields_dict: controls, $wrapper: { modal() {}, on() {} }, get_close_btn: () => ({ removeAttr() { return this; }, on() {} }),
		get_primary_btn: () => button, get_values: () => ({}), get_value: key => controls[key]?.value, set_title() {},
		set_df_property(key, property, value) { controls[key].df[property] = value; },
		async set_values(values) { for (const [key, value] of Object.entries(values)) controls[key].value = value; },
		async set_value(key, value) { controls[key].value = value; }, hide() { env.hidden = true; state.images.destroy(); } };
	const state = { archive, dialog, bindings, requestId: editor.requestID(), kind: "物料", closed: false, saving: false, linking: false, unresolved: false, confirming: false };
	state.images = new window.JifengInventoryArchiveImages(editor, state); state.original = JSON.stringify(editor.payload(state));
	Object.assign(env, { state, editor, image: state.images, button, host }); return env;
}
const picture = (name = "picture.png", bytes = [0, 128, 255, 65]) => ({ name, size: bytes.length, async arrayBuffer() { return Uint8Array.from(bytes).buffer; } });
function saved(env, requestId) {
	return { ...env.state.archive, name: env.state.archive.name || "CREATED", profile_version: "v2", item_version: "i2", item_code: "ITEM", image_change_request_id: requestId,
		profile: { ...env.state.archive.profile, image: "/private/files/new.png" }, image: { state: "已上传", url: "/private/files/new.png?fid=NEW" } };
}
async function main() {
	let env = environment(null);
	assert.equal(env.image.picture.hidden, true); assert.equal(env.image.choose.disabled, false);
	assert.equal(await env.image.select(picture()), true); assert.equal(env.calls.length, 0, "choosing does not create a server attachment");
	let payload = env.editor.payload(env.state);
	assert.equal(payload.image_upload.content, "AID/QQ=="); assert.equal(payload.image_upload.file_name, "picture.png");
	assert.ok(payload.image_change_request_id); assert.equal(env.editor.isDirty(env.state), true); assert.match(env.image.picture.src, /^blob:/);
	env.image.clearImage(); assert.equal(env.editor.isDirty(env.state), false, "clearing a new pending picture returns to the original empty draft");
	assert.equal(env.created.length, 1); assert.equal(env.revoked.length, 1); assert.equal(env.calls.length, 0);
	for (const bad of ["中文.png", "\u3007.png", "\u{20000}.png", "../x.png", "x.svg", "x.png?data", " x.png", "x\0.png"]) {
		assert.equal(await env.image.select(picture(bad)), false, bad);
	}
	assert.equal(await env.image.select({ ...picture(), size: 1025 }), false); assert.equal(env.calls.length, 0);
	assert.equal(await env.image.select(picture("<test>.png")), true); assert.ok(env.image.message.textContent.includes("<test>.png"));
	for (const flag of ["saving", "linking", "unresolved", "confirming", "imageReading", "closed"]) {
		env.state[flag] = true; const held = JSON.stringify(env.editor.payload(env.state)); env.image.clearImage();
		assert.equal(await env.image.select(picture("blocked.png")), false); assert.equal(JSON.stringify(env.editor.payload(env.state)), held, flag);
		env.state[flag] = false;
	}
	env.state.archive.image_options.can_choose = false;
	assert.equal(await env.image.select(picture()), false); env.image.refresh(); assert.equal(env.image.choose.disabled, true);
	env.state.archive.image_options.can_choose = true;
	const saving = env.editor.save(env.state), uploadCall = env.calls.at(-1), receipt = uploadCall.options.args.payload.image_change_request_id;
	assert.ok(uploadCall.options.args.payload.image_upload); uploadCall.reject(new Error("response lost")); await saving;
	assert.equal(env.state.unresolved, true); assert.equal(env.image.clear.disabled, true); assert.ok(env.image.draft);
	await env.editor.save(env.state); assert.equal(env.calls.length, 1, "an uncertain upload cannot repeat the save POST");
	let resolving = env.editor.resolve(env.state); env.calls.at(-1).resolve({ status: "found", archive: saved(env, "older-picture-request") }); await resolving;
	assert.equal(env.state.unresolved, true, "the same archive text with a different picture receipt is not success");
	resolving = env.editor.resolve(env.state); env.calls.at(-1).resolve({ status: "found", archive: saved(env, receipt) }); await resolving;
	assert.equal(env.state.unresolved, false); assert.equal(env.image.draft, null); assert.equal(env.editor.isDirty(env.state), false);
	assert.equal(env.image.picture.src, "/private/files/new.png?fid=NEW"); assert.equal(env.image.choose.disabled, false);
	assert.deepEqual(env.created.map(row => row.url), env.revoked, "all local object URLs are released after accepting a save");
	assert.equal(env.calls.filter(call => call.options.method.endsWith("save_archive")).length, 1);
	env = environment(null); await env.image.select(picture());
	const linking = env.editor.setSource(env.state, "EXISTING");
	env.calls.at(-1).resolve({ ...env.state.archive, existing_item: "EXISTING", item_code: "EXISTING", item_version: "source-v1",
		can_create_item: false, item: { item_group: "Original", stock_uom: "Kg" } });
	assert.equal(await linking, true); assert.equal(env.image.choose.disabled, false, "source hydration restores picture controls after its busy state");
	assert.ok(env.editor.payload(env.state).image_upload, "the local picture remains an unsaved draft when changing the business source");
	env = environment(); env.image.clearImage(); payload = env.editor.payload(env.state);
	assert.equal(payload.profile.image, null); assert.ok(payload.image_change_request_id); assert.equal(env.image.picture.hidden, true);
	let updating = env.editor.save(env.state); const clearCall = env.calls.at(-1);
	const validation = new Error("server rollback"); validation.responseJSON = { exc: "ValidationError" }; clearCall.reject(validation); await updating;
	assert.equal(env.state.unresolved, false); assert.equal(env.editor.isDirty(env.state), true); assert.equal(env.image.clear.disabled, false);
	updating = env.editor.save(env.state); env.calls.at(-1).resolve(saved(env, "wrong-clear-receipt")); await updating;
	assert.equal(env.state.unresolved, true, "malformed picture success also requires read-only resolution");
	const read = env.editor.resolve(env.state); assert.match(env.calls.at(-1).options.method, /read_archive$/);
	env.calls.at(-1).resolve({ ...saved(env, clearCall.options.args.payload.image_change_request_id), profile: { ...env.state.archive.profile, image: null }, image: { state: "未上传", url: "" } }); await read;
	assert.equal(env.state.unresolved, false); assert.equal(env.editor.isDirty(env.state), false);
	env = environment(); env.state.archive.image.url = "https://untrusted.invalid/private.png"; env.image.refresh();
	assert.equal(env.image.picture.hidden, true); assert.equal(env.image.picture.src, undefined);
	for (const bad of ["/files/a.png?fid=X", "/private/files/%2fescape.png?fid=X", "/private/files/%252fescape.png?fid=X", "/private/files/x.png?fid=X#fragment"]) assert.equal(env.image.safeURL(bad), "");
	env = environment(null); let finish;
	const pending = env.image.select({ ...picture(), arrayBuffer: () => new Promise(resolve => { finish = resolve; }) });
	assert.equal(env.state.imageReading, true); await env.editor.save(env.state); assert.equal(env.calls.length, 0);
	env.editor.guardClose(env.state); env.state.dialog.hide(); assert.equal(env.hidden, undefined, "a read in progress cannot lose its draft owner");
	env.state.closed = true; finish(Uint8Array.from([0, 128, 255, 65]).buffer); assert.equal(await pending, false);
	assert.equal(env.created.length, 0); assert.equal(env.image.draft, null);
	console.log("Inventory pictures: local drafts, names/limits, permissions, stale reads, private previews and uncertain-save receipts passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
