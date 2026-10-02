// Load the real list, bridge and editor; reorder RPC and native field hydration.
// These are protocol models. They do not run a browser or touch a database.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const crypto = require("node:crypto");
const sources = ["jifeng_inventory_catalog.js", "jifeng_inventory_archive_list.js", "jifeng_inventory_archive.js", "jifeng_classic_bridge.js"]
	.map(file => fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js", file), "utf8"));

class Element {
	constructor(tag = "div") {
		this.tag = tag; this.children = []; this.events = {}; this.attributes = {}; this.value = "";
		this.scrollTop = 0; this.scrollLeft = 0; this.classList = { add() {} }; this._text = "";
	}
	get firstElementChild() { return this.children[0]; }
	append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
	prepend(...children) { for (const child of children) child.parent = this; this.children.unshift(...children); }
	after(child) { child.parent = this.parent; this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, child); }
	replaceChildren(...children) { this._text = ""; this.children = []; this.append(...children); }
	set textContent(text) { this._text = String(text); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Archive source HTML must not execute"); }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, action) { this.events[name] = action; }
}

function archive(name = "P1", company = "JF", kind = "物料") {
	return { name, kind, item_code: name ? "I-" + name : null, item_name: "业务来源名称",
		profile_version: name ? "2026-10-02T01:00:00.123456" : null,
		item_version: name ? "2026-10-02T01:00:00.123456" : null,
		profile: { company, display_name: "档案原文", list_price: 0 },
		item: { item_group: "Materials", stock_uom: "Nos" },
		field_meta: { profile: { company: { fieldtype: "Link", read_only: !!name },
			display_name: { fieldtype: "Data" }, list_price: { fieldtype: "Currency" } },
			item: { item_group: { fieldtype: "Link", read_only: !!name }, stock_uom: { fieldtype: "Link", read_only: !!name } } },
		can_write: true, can_create_item: !name, can_link_item: !name, existing_item: null };
}

function environment() {
	const env = { calls: [], dialogs: [], messages: [], confirmations: [], listeners: new Set(), holdNextValues: false };
	const window = { addEventListener(type, handler) { if (type === "beforeunload") env.listeners.add(handler); },
		removeEventListener(type, handler) { if (type === "beforeunload") env.listeners.delete(handler); } };
	class Dialog {
		constructor({ fields }) {
			this.fields_dict = Object.fromEntries(fields.filter(df => df.fieldname).map(df => [df.fieldname,
				{ df: { ...df }, value: df.default ?? null, get_value() { return this.value; } }]));
			this.shown = false; this.hidden = false;
			this.$wrapper = { removed: false, addClass() {}, modal() {}, on() {}, remove() { this.removed = true; } };
			this.primary = { disabled: false, prop(_, value) { this.disabled = value; return this; }, text() {} };
			env.dialogs.push(this);
		}
		set_primary_action() {} add_custom_action() {} set_title() {}
		get_primary_btn() { return this.primary; }
		get_close_btn() { return { removeAttr() { return this; }, on() { return this; } }; }
		async set_values(values) {
			for (const [name, value] of Object.entries(values)) this.fields_dict[name].value = value;
			if (env.holdNextValues) { env.holdNextValues = false; await new Promise(resolve => { this.releaseValues = resolve; }); }
		}
		async set_value(name, value) { this.fields_dict[name].value = value; }
		get_value(name) { return this.fields_dict[name]?.value; }
		get_values() { return Object.fromEntries(Object.entries(this.fields_dict).map(([name, control]) => [name, control.value])); }
		set_df_property(name, property, value) { this.fields_dict[name].df[property] = value; }
		show() { this.shown = true; }
		hide() { this.shown = false; this.hidden = true; this.on_hide?.(); }
	}
	const frappe = { ui: { Dialog, form: { make_control({ df }) {
		return { value: null, get_value() { return this.value; }, async set_value(value) { this.value = value; await df.onchange?.(); }, $input: { on() {} } };
	} } }, msgprint: message => env.messages.push(message), show_alert() {},
		confirm: (_, yes, no) => env.confirmations.push({ yes, no }), call(options) {
			let resolve, reject; const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
			env.calls.push({ options, resolve: message => resolve({ message }), reject }); return pending;
		} };
	const context = { window, document: { createElement: tag => new Element(tag) }, frappe, __: value => value,
		crypto, structuredClone, queueMicrotask };
	for (const source of sources) vm.runInNewContext(source, context);
	const bridge = Object.create(window.JifengClassicBridge.prototype); bridge.frames = new Map();
	bridge.inventoryEditor = new window.JifengInventoryArchive(bridge);
	Object.assign(env, { window, bridge, editor: bridge.inventoryEditor });
	env.list = (kind = "物料") => new window.JifengInventoryArchiveList(new Element(), kind, bridge);
	return env;
}
const settle = () => new Promise(resolve => setImmediate(resolve));

async function openSaved(env, list, name = "P1") {
	list.rows = [{ name }]; const pending = list.openEditor(name), call = env.calls.at(-1);
	assert.equal(call.options.method, "jifeng_erp.inventory_archive.read_archive");
	assert.equal(call.options.args.company, list.companyScope);
	call.resolve(archive(name, list.companyScope, list.kind)); await pending;
	return [...env.editor.active.values()].at(-1);
}

async function staleRpcScopes() {
	for (const invalidate of ["company", "disposed", "wrong-company"]) {
		const env = environment(), list = env.list(); await list.companyControl.set_value("JF");
		list.rows = [{ name: "P1" }]; const pending = list.openEditor("P1"), call = env.calls.at(-1);
		assert.equal(list.getCloseBlock(), "正在读取档案，请稍候。");
		if (invalidate === "company") await list.companyControl.set_value("OTHER");
		if (invalidate === "disposed") list.destroy();
		call.resolve(archive("P1", invalidate === "wrong-company" ? "OTHER" : "JF")); await pending;
		assert.equal(env.dialogs.length, 0, invalidate + " cannot open a late read response");
		assert.equal(env.editor.active.size, 0); assert.equal(env.editor.opening.size, 0);
		assert.equal(env.editor.loadingOwners.size, 0); assert.equal(list.openingEditor, false);
	}
}

async function scopeChangesDuringHydration() {
	for (const invalidate of ["company", "disposed"]) {
		const env = environment(), list = env.list(); await list.companyControl.set_value("JF");
		list.rows = [{ name: "P1" }]; env.holdNextValues = true;
		const pending = list.openEditor("P1"); env.calls.at(-1).resolve(archive()); await settle();
		const state = [...env.editor.active.values()][0], dialog = state.dialog;
		assert.equal(typeof dialog.releaseValues, "function"); assert.equal(dialog.shown, false);
		assert.equal(env.editor.loadingOwners.get(list), 1);
		if (invalidate === "company") await list.companyControl.set_value("OTHER"); else list.destroy();
		dialog.releaseValues(); await pending;
		assert.equal(dialog.shown, false, "set_values completion cannot show an invalidated editor");
		assert.equal(dialog.$wrapper.removed, true); assert.equal(state.closed, true);
		assert.equal(env.editor.active.size, 0); assert.equal(env.editor.loadingOwners.size, 0);
		assert.equal(env.listeners.size, 0, "an editor discarded before show cannot register beforeunload");
	}
}

async function companyAndKindKeys() {
	const env = environment(); const opened = [];
	for (const [company, kind] of [["JF", "物料"], ["OTHER", "物料"], ["JF", "材料"]]) {
		const list = env.list(kind); await list.companyControl.set_value(company);
		const pending = list.openEditor(); const call = env.calls.at(-1);
		assert.equal(call.options.args.company, company); assert.equal(call.options.args.kind, kind);
		call.resolve(archive(null, company, kind)); await pending;
		opened.push({ list, state: env.editor.active.get(kind + ":" + company + ":new") });
	}
	assert.equal(env.editor.active.size, 3); assert.equal(new Set(opened.map(row => row.state.dialog)).size, 3);
	opened[0].state.dialog.hide(); assert.equal(env.editor.active.size, 2);
	assert.equal(opened[0].list.getCloseBlock(), "");
	for (const { list, state } of opened.slice(1)) {
		assert.equal(state.dialog.shown, true); assert.equal(list.getCloseBlock(), "请先关闭档案录入窗口。");
		state.dialog.hide();
	}
	assert.equal(env.listeners.size, 0);
}

async function copyLifecycle() {
	for (const outcome of ["company", "disposed", "success", "rpc-error"]) {
		const env = environment(), list = env.list(); await list.companyControl.set_value("JF");
		const original = await openSaved(env, list); assert.equal(env.editor.isDirty(original), false);
		const pending = env.editor.copy(original); await settle(); const call = env.calls.at(-1);
		assert.equal(call.options.method, "jifeng_erp.inventory_archive.init_archive");
		assert.equal(call.options.args.copy_from, "P1"); assert.equal(call.options.args.company, "JF");
		assert.equal(original.linking, true); assert.equal(original.dialog.primary.disabled, true);
		assert.equal(env.editor.loadingOwners.get(list), 1); assert.equal(list.getCloseBlock(), "正在读取档案，请稍候。");
		original.dialog.hide(); assert.equal(original.closed, false, "copy in progress blocks closing the source modal");
		assert.equal(original.dialog.shown, true); assert.equal(env.confirmations.length, 0);
		assert.equal(env.messages.at(-1), "正在处理，请稍候。");
		if (outcome === "company") await list.companyControl.set_value("OTHER");
		if (outcome === "disposed") list.destroy(); // Force disposal to exercise the late response guard as well.
		if (outcome === "rpc-error") call.reject(new Error("native RPC failed")); else call.resolve(archive(null));
		await pending;
		assert.equal(original.linking, false); assert.equal(original.dialog.primary.disabled, false);
		assert.equal(env.editor.loadingOwners.size, 0); assert.equal(env.editor.opening.size, 0);
		if (outcome === "success") {
			assert.equal(env.editor.active.size, 2);
			const copy = env.editor.active.get("物料:JF:copy:P1");
			assert.ok(copy); assert.equal(copy.listOwner, list); assert.equal(copy.dialog.shown, true);
			assert.equal(copy.archive.name, null); assert.notEqual(copy.requestId, original.requestId);
			copy.dialog.hide();
		} else {
			assert.equal(env.editor.active.size, 1, outcome + " cannot leave an orphan copy editor");
			assert.equal(env.dialogs.length, 1);
		}
		original.dialog.hide(); assert.equal(original.closed, true); assert.equal(env.editor.active.size, 0);
		assert.equal(env.listeners.size, 0); assert.equal(list.getCloseBlock(), "");
	}
}

async function main() {
	await staleRpcScopes(); await scopeChangesDuringHydration(); await companyAndKindKeys(); await copyLifecycle();
	console.log("Inventory archive lifecycle: real editor/bridge, scope keys, stale RPC/hydration, pending-copy close guards and cleanup passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
