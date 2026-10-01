// DOM/protocol models only. No browser, database, or production network calls.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const nodeCrypto = require("node:crypto");
const calls = [], dialogs = [], messages = [], alerts = [], listeners = new Map();
let confirmation;
const tick = () => new Promise(setImmediate);
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.value = ""; this.checked = false;
		this.classList = { add() {} }; this.scrollTop = 0; this.scrollLeft = 0; this.attributes = {}; this._text = ""; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((child) => child.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("untrusted HTML rendering is forbidden"); }
	append(...nodes) { this.children.push(...nodes); }
	appendChild(node) { this.append(node); }
	remove() { this.removed = true; }
	prepend(...nodes) { this.children.unshift(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(key, value) { this.attributes[key] = value; }
	addEventListener(event, action) { this.events.set(event, action); }
	descendants(tag) { return this.children.flatMap((child) => [ ...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Wrapper {
	constructor(element = new Element()) { this.element = element; this.events = new Map(); }
	append(node) { this.element.append(node); return this; }
	prepend(node) { this.element.prepend(node); return this; }
	modal() { return this; }
	on(event, action) { this.events.set(event, action); return this; }
	removeAttr() { return this; }
	prop(name, value) { this[name] = value; return this; }
	find(selector) { return this.buttons.get(selector); }
	get() { return this.element; }
}
class Dialog {
	constructor(config) {
		this.config = config; this.$wrapper = new Wrapper(); this.$body = new Wrapper(); this.hidden = false;
		this.button = new Wrapper(); this.close = new Wrapper(); this.actions = new Map();
		this.custom_actions = new Wrapper(); this.custom_actions.buttons = new Map();
		this.fields_dict = Object.fromEntries(config.fields.map((df) => [df.fieldname,
			{ df: { ...df }, value: df.default, $wrapper: new Wrapper(), get_value() { return this.value; } }])); dialogs.push(this);
	}
	get_value(field) { return this.fields_dict[field].value; }
	async set_values(values) { for (const [field, value] of Object.entries(values)) this.fields_dict[field].value = value; }
	get_values() { return {}; }
	set_df_property(field, property, value) { this.fields_dict[field].df[property] = value; }
	set_primary_action(_, action) { this.primary = action; }
	get_primary_btn() { return this.button; }
	get_close_btn() { return this.close; }
	add_custom_action(label, action, cssClass) {
		const button = new Wrapper(); button.action = action; this.actions.set(label, button);
		if (cssClass) this.custom_actions.buttons.set("." + cssClass, button);
		// Match native Frappe: add_custom_action has no return value.
	}
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
const window = { addEventListener(name, handler) { listeners.set(handler, name); }, removeEventListener(_, handler) { listeners.delete(handler); } };
const document = { createElement: (tag) => new Element(tag) };
const frappe = { pages: { "jifeng-legacy": {} }, ui: { Dialog, form: { make_control({ df }) {
	return { df, value: null, get_value() { return this.value; }, set_value(value) { this.value = value; df.onchange(); } }; } } },
	call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
		calls.push({ options, resolve: (message) => resolve({ message }), reject }); return promise; },
	msgprint(message) { messages.push(message); }, show_alert(message) { alerts.push(message); },
	confirm(_, yes, no) { confirmation = { yes, no }; } };
const context = { window, document, frappe, structuredClone, __: (text) => text, queueMicrotask, Uint8Array,
	crypto: { getRandomValues: (bytes) => nodeCrypto.randomFillSync(bytes) } };
for (const file of ["jifeng_product_dictionary", "jifeng_classic_bridge"]) vm.runInNewContext(
	fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
const fieldMeta = () => ({ company: { fieldtype: "Link", read_only: true }, dictionary_type: { fieldtype: "Select", read_only: true },
	legacy_number: { fieldtype: "Data", read_only: true }, value: { fieldtype: "Data", reqd: true }, remarks: { fieldtype: "Small Text" },
	inactive: { fieldtype: "Check" }, abbreviation: { fieldtype: "Data" } });
const pack = (extra = {}) => ({ name: "WORD-1", version: "2026-10-01T00:00:02", company: "Company A", dictionary_type: "风格",
	legacy_number: "001", value: " 典雅 ", remarks: " 原始备注 ", abbreviation: null, inactive: 0,
	numbering_mode: "automatic_v1", number_preview: null, number_preview_is_provisional: false, can_write: true, field_meta: fieldMeta(), ...extra });
const blank = (extra = {}) => pack({ name: null, version: null, legacy_number: null, value: null, remarks: null,
	number_preview: "007", number_preview_is_provisional: true, ...extra });
const listRow = (extra = {}) => ({ name: "WORD-1", company: "Company A", dictionary_type: "风格", value: " 典雅 ",
	legacy_number: "001", remarks: "备注", abbreviation: "DY", inactive: 0, creation: "2026-10-01T00:00:01", owner: "录入人",
	modified_by: "修改人", version: "2026-10-01T00:00:02", ...extra });
async function initialize(type = "风格") {
	const host = new Element(), bench = new window.JifengProductDictionary(host, type);
	assert.equal(calls.at(-1).options.method, "jifeng_erp.product_dictionary.init_dictionary");
	assert.equal(calls.at(-1).options.args.numbering_mode, "automatic_v1");
	calls.at(-1).resolve(blank({ dictionary_type: type, company: null, number_preview: null })); await bench.ready;
	return bench;
}
async function main() {
	const bench = await initialize();
	assert.equal(bench.host.descendants("th").map((node) => node.textContent).join("/"), "编号/名称/备注/简拼/停用/操作/日期/修改/删除");
	for (const label of ["更新简拼", "格式1", "打印", "预览"]) assert.ok(bench.host.descendants("button").find((node) => node.textContent === label).disabled);
	assert.match(bench.limits.textContent, /本系统新规则/); assert.match(bench.limits.textContent, /暂定号不占号/);
	assert.equal(bench.addButton.disabled, true, "new needs an explicit native Company selection");
	bench.companyControl.set_value("Company A"); calls.at(-1).resolve([]); await tick();
	assert.equal(bench.addButton.disabled, false);
	assert.match(bench.requestID(), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/, "LAN UUID fallback");

	const oldQuery = bench.query(), oldCall = calls.at(-1);
	bench.search.value = "新关键词"; bench.search.events.get("input")();
	const newQuery = bench.query(), newCall = calls.at(-1);
	const hostile = '<svg onload="alert(1)">原文</svg>';
	newCall.resolve([listRow({ value: hostile, remarks: hostile, owner: hostile, modified_by: hostile })]); await newQuery;
	oldCall.resolve([listRow({ value: "过期结果" })]); await oldQuery;
	assert.equal(bench.rows[0].value, hostile); assert.match(bench.tbody.textContent, /<svg/);
	assert.equal(bench.tbody.descendants("td").length, 9, "every real row preserves nine columns");
	const deletion = bench.tbody.descendants("button").find((node) => node.textContent === "删除"); assert.ok(deletion.disabled);
	const wrong = bench.query(); calls.at(-1).resolve([listRow({ company: "Other Company" })]); await wrong;
	assert.equal(bench.rows.length, 0); assert.match(bench.status.textContent, /查询失败/);

	const opening = bench.openEditor(); calls.at(-1).resolve(blank()); const dialog = await opening;
	const state = bench.editor;
	assert.equal(dialog.config.fields.filter((df) => !df.hidden).map((df) => df.label).join("/"), "编号/名称/备注/停用");
	assert.equal(dialog.get_value("legacy_number"), "007", "provisional readonly number is displayed");
	assert.ok(dialog.fields_dict.legacy_number.df.read_only); assert.match(dialog.fields_dict.legacy_number.df.description, /暂定号不占号/);
	assert.equal(dialog.fields_dict.abbreviation.df.hidden, true);
	await dialog.set_values({ value: " 保留原文 ", remarks: hostile });
	assert.equal(bench.payload(state).numbering_mode, "automatic_v1");
	assert.equal("legacy_number" in bench.payload(state), false, "preview and legacy number never enter the POST");
	assert.equal("abbreviation" in bench.payload(state), false, "blank legacy add form has no abbreviation input");
	dialog.hide(); assert.ok(confirmation);
	assert.equal(dialog.actions.get("新增").disabled, true, "native custom-action button is disabled during confirmation");
	confirmation.no(); assert.equal(dialog.hidden, false); assert.ok(bench.isDirty());
	const escape = { key: "Escape", preventDefault() {}, stopImmediatePropagation() {} };
	dialog.$wrapper.events.get("keydown.product-dictionary")(escape); confirmation.no(); assert.equal(dialog.hidden, false);
	dialog.close.events.get("click.product-dictionary")(); confirmation.no(); assert.equal(dialog.hidden, false, "X follows the same guard");

	const saving = bench.save(state), saveCall = calls.at(-1);
	assert.equal(saveCall.options.method, "jifeng_erp.product_dictionary.save_dictionary");
	assert.ok(dialog.button.disabled); assert.equal(bench.getCloseBlock(), "正在读取或保存词条，请稍候。");
	for (const label of ["新增", "核对保存结果", "采用已核对当前值"]) assert.ok(dialog.actions.get(label).disabled);
	saveCall.reject(new Error("transport lost")); await saving;
	assert.equal(state.unresolved, true); const saveCount = calls.length;
	assert.equal(dialog.actions.get("新增").disabled, true); assert.equal(dialog.actions.get("核对保存结果").disabled, false);
	assert.equal(dialog.actions.get("采用已核对当前值").disabled, true);
	await bench.save(state); bench.newDraft(state); dialog.hide(); assert.equal(calls.length, saveCount); assert.equal(dialog.hidden, false);
	const missing = bench.resolve(state); assert.equal(calls.at(-1).options.method, "jifeng_erp.product_dictionary.resolve_creation");
	assert.equal(calls.at(-1).options.args.request_id, state.candidate.request_id);
	calls.at(-1).resolve({ status: "not_found" }); await missing; assert.equal(state.unresolved, true);
	const partial = bench.resolve(state); calls.at(-1).resolve({ status: "found", archive: { name: "WORD-1" } }); await partial;
	assert.equal(state.unresolved, true, "partial reads cannot unlock or discard held text");
	const resolved = bench.resolve(state); calls.at(-1).resolve({ status: "found", archive: pack({ value: " 保留原文 ", remarks: hostile, legacy_number: "008" }) }); await resolved;
	assert.equal(state.unresolved, false); assert.equal(dialog.get_value("legacy_number"), "008", "final server allocation replaces preview");
	assert.equal(state.archive.name, "WORD-1"); assert.equal(bench.isDirty(), false); assert.equal(dialog.hidden, false, "save keeps the popup open");
	assert.equal(dialog.fields_dict.abbreviation.df.hidden, false, "saved raw abbreviation may be maintained");
	assert.equal(dialog.fields_dict.value.df.read_only, false, "mutable word differs from HR immutable vocabulary");

	await dialog.set_values({ value: " 本次改单 " });
	const failed = bench.save(state), denied = new Error("native stale version"); denied.responseJSON = { exc_type: "TimestampMismatchError", exc: "trace" };
	calls.at(-1).reject(denied); await failed; assert.equal(state.unresolved, false); assert.equal(dialog.get_value("value"), " 本次改单 ");
	assert.ok(bench.isDirty()); assert.equal(dialog.button.disabled, false);
	const unknown = bench.save(state); calls.at(-1).reject(Object.assign(new Error("unexpected server error"), { responseJSON: { exc_type: "RuntimeError", exc: "trace" } })); await unknown;
	assert.equal(state.unresolved, true, "an unexpected server exception is not proof of atomic rollback");
	const wrongMode = bench.resolve(state);
	calls.at(-1).resolve(pack({ version: "2026-10-01T00:00:04", value: "另一人已改名", numbering_mode: null })); await wrongMode;
	assert.equal(state.conflict, null); assert.equal(state.unresolved, true);
	assert.equal(dialog.actions.get("采用已核对当前值").disabled, true, "numbering-mode mismatch cannot be adopted as a normal rename conflict");
	const conflict = bench.resolve(state); assert.equal(calls.at(-1).options.method, "jifeng_erp.product_dictionary.read_dictionary");
	calls.at(-1).resolve(pack({ version: "2026-10-01T00:00:04", value: "另一人已改名", remarks: hostile })); await conflict;
	assert.equal(state.unresolved, true); assert.match(state.review.textContent, /本次改单/); assert.match(state.review.textContent, /另一人已改名/);
	assert.match(state.review.textContent, /编号（只读）/); assert.match(state.review.textContent, /008/);
	assert.match(state.review.textContent, /系统自动/); assert.doesNotMatch(state.review.textContent, /automatic_v1/);
	assert.equal(dialog.actions.get("采用已核对当前值").disabled, false);
	const beforeAdopt = calls.filter((call) => call.options.method.endsWith("save_dictionary")).length;
	bench.acceptCurrent(state); await confirmation.yes();
	assert.equal(state.unresolved, false); assert.equal(dialog.get_value("value"), "另一人已改名"); assert.equal(bench.isDirty(), false);
	assert.equal(calls.filter((call) => call.options.method.endsWith("save_dictionary")).length, beforeAdopt, "explicit local adoption never resends the POST");

	const resetting = bench.newDraft(state), resetCall = calls.at(-1); resetCall.resolve(blank({ number_preview: "009" })); await resetting;
	assert.equal(state.archive.name, null); assert.equal(dialog.get_value("legacy_number"), "009"); assert.equal(dialog.fields_dict.abbreviation.df.hidden, true);
	assert.notEqual(state.requestId, saveCall.options.args.payload.request_id); assert.equal(bench.isDirty(), false);
	await dialog.set_values({ value: "下一条" });
	const next = bench.save(state); const nextCall = calls.at(-1);
	nextCall.resolve(pack({ name: "WORD-2", value: "下一条", remarks: null, legacy_number: "010", version: "2026-10-01T00:00:05" })); await next;
	assert.equal(dialog.hidden, false); assert.equal(state.archive.name, "WORD-2"); assert.equal(dialog.get_value("legacy_number"), "010");
	dialog.hide(); assert.equal(dialog.hidden, true); assert.equal(bench.editor, null);

	const readOpening = bench.openEditor("WORD-1"); calls.at(-1).resolve(pack({ field_meta: { ...fieldMeta(), remarks: { fieldtype: "Small Text", read_only: true } } })); await readOpening;
	const readState = bench.editor;
	assert.equal("remarks" in bench.payload(readState), false, "readonly fields are not posted back as blind writes");
	await readState.dialog.set_values({ value: "更新可见名" });
	const permissionChange = bench.save(readState);
	const newMeta = fieldMeta(); delete newMeta.remarks;
	calls.at(-1).resolve(pack({ value: "更新可见名", version: "2026-10-01T00:00:06", field_meta: newMeta, remarks: undefined, can_write: false })); await permissionChange;
	assert.equal(readState.dialog.get_value("remarks"), null); assert.equal(readState.dialog.fields_dict.remarks.df.hidden, true);
	assert.equal(readState.dialog.button.disabled, true, "current server permissions govern after save/recovery");
	readState.dialog.hide();

	const delayedOpen = bench.openEditor("WORD-1"), delayedCall = calls.at(-1);
	bench.companyControl.set_value("Company B"); delayedCall.resolve(pack()); await delayedOpen;
	assert.equal(bench.editor, null, "scope change ignores delayed editor reads");
	const delayedQuery = bench.query(), delayedQueryCall = calls.at(-1); bench.destroy(); delayedQueryCall.resolve([listRow({ company: "Company B" })]); await delayedQuery;
	assert.equal(bench.disposed, true); assert.equal(listeners.has(bench.beforeUnload), false);

	vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/jifeng_erp/page/jifeng_legacy/jifeng_legacy.js"), "utf8") + "\nwindow.Workspace = JifengLegacyWorkspace;", context);
	const workspace = Object.create(window.Workspace.prototype), opened = []; workspace.formalBridge = { open(spec) { opened.push(spec); } };
	for (const type of ["风格", "来源", "品牌"]) {
		workspace.openPage("产品" + type, "产品"); assert.equal(opened.at(-1).productDictionary, type); assert.equal(opened.at(-1).doctype, "Jifeng Product Dictionary");
	}
	assert.equal(opened.length, 3); workspace.addTab = (tab) => opened.push(tab); workspace.openPage("产品类别", "产品"); assert.equal(opened.at(-1).productDictionary, undefined);
	const bridge = Object.create(window.JifengClassicBridge.prototype);
	bridge.frames = new Map([["WORD", { workbench: { isDirty: () => true, getCloseBlock: () => "保存结果未知" } }]]);
	assert.equal(bridge.canClose("WORD"), false); assert.equal(bridge.closeBlock("WORD"), "保存结果未知");
	const frame = { contentWindow: { cur_frm: { is_dirty: () => false } } }; bridge.frames.set("NATIVE", { iframe: frame });
	assert.equal(bridge.canClose("NATIVE"), true); assert.equal(bridge.closeBlock("NATIVE"), "");

	// Exercise the real classic closeTab -> bridge.sync -> workbench.destroy lifecycle.
	const mountedWorkspace = Object.create(window.Workspace.prototype), mountedBridge = Object.create(window.JifengClassicBridge.prototype);
	mountedWorkspace.tabs = [{ key: "home" }, { key: "formal:产品:产品风格", kind: "formal", spec: { productDictionary: "风格" } }];
	mountedWorkspace.activeKey = "formal:产品:产品风格"; mountedWorkspace.formalBridge = mountedBridge;
	mountedWorkspace.saveActiveTabState = () => {}; mountedWorkspace.defaultViewState = () => ({});
	mountedWorkspace.render = () => mountedBridge.sync();
	mountedBridge.workspace = mountedWorkspace; mountedBridge.frames = new Map(); mountedBridge.layer = new Element(); mountedBridge.align = () => {};
	mountedBridge.sync(); const entry = mountedBridge.frames.get(mountedWorkspace.activeKey);
	assert.ok(entry.workbench); assert.equal(entry.iframe, undefined, "dictionary tabs do not create a native-list iframe");
	calls.at(-1).resolve(blank({ company: null, number_preview: null })); await entry.ready;
	entry.workbench.companyControl.set_value("Company A"); calls.at(-1).resolve([]); await tick();
	const mountedOpening = entry.workbench.openEditor(); calls.at(-1).resolve(blank()); await mountedOpening;
	const mountedState = entry.workbench.editor; await mountedState.dialog.set_values({ value: "未保存词条" });
	const mountedKey = mountedWorkspace.activeKey;
	mountedState.busy = true; mountedWorkspace.closeTab(mountedKey, true);
	assert.equal(mountedWorkspace.tabs.length, 2, "discard cannot bypass a busy close block");
	mountedState.busy = false; mountedState.unresolved = true; mountedWorkspace.closeTab(mountedKey, true);
	assert.equal(mountedWorkspace.tabs.length, 2, "discard cannot bypass an unknown result");
	mountedState.unresolved = false; mountedWorkspace.closeTab(mountedKey);
	assert.equal(mountedWorkspace.tabs.length, 2, "dirty close awaits explicit confirmation");
	assert.equal(mountedState.dialog.hidden, false); assert.ok(listeners.has(entry.workbench.beforeUnload));
	confirmation.yes();
	assert.equal(mountedWorkspace.tabs.length, 1); assert.equal(mountedBridge.frames.has(mountedKey), false);
	assert.equal(entry.container.removed, true); assert.equal(entry.workbench.disposed, true);
	assert.equal(mountedState.dialog.hidden, true, "confirmed tab close hides the still-mounted native overlay");
	assert.equal(listeners.has(entry.workbench.beforeUnload), false, "confirmed close removes this workbench's unload listener");

	// Closing an ordinary native create dialog also visits cached non-iframe entries.
	let refreshes = 0, stops = 0; const createdFrame = new Element("iframe");
	createdFrame.contentWindow = { cur_frm: { is_dirty: () => false } };
	mountedBridge.frames.set("WORD", { workbench: {} });
	mountedBridge.frames.set("LIST", { iframe: { contentWindow: { cur_list: { refresh: () => refreshes++ } } } });
	mountedBridge.mount = () => ({ iframe: createdFrame, stop: () => stops++ });
	mountedBridge.openCreate({ title: "原生新增", kind: "create" }); dialogs.at(-1).hide();
	assert.equal(stops, 1); assert.equal(createdFrame.removed, true); assert.equal(refreshes, 1);
	console.log("Product dictionaries: nine columns, readonly provisional numbering, native scopes, mutable words, dirty/unknown close guards, read-only recovery and three classic routes passed (DOM/protocol models only).");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
