// Verify held saves and asynchronous selections without a browser or HR records.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const calls = [], dialogs = [], messages = [];
const window = { addEventListener() {}, removeEventListener() {} };
let confirmation;
const tick = () => new Promise(setImmediate);
class Wrapper {
	constructor() { this.buttons = []; this.content = ""; }
	empty() { this.buttons = []; this.content = ""; return this; }
	text(value) { this.content = value; return this; }
	find() { return { prop: (_, value) => { for (const button of this.buttons) button.disabled = value; } }; }
	modal() {}
	on() { return this; }
}
function $(markup) {
	assert.match(markup, /^<button/);
	return { disabled: false, text(value) { this.label = value; return this; },
		on(_, action) { this.click = action; return this; }, prop(_, value) { this.disabled = value; return this; },
		appendTo(host) { host.buttons.push(this); return this; } };
}
class Dialog {
	constructor(config) {
		this.config = config; this.$wrapper = new Wrapper(); this.hidden = false;
		this.button = { prop(_, value) { this.disabled = value; return this; }, text() {} };
		this.fields_dict = Object.fromEntries(config.fields.map((df) => [df.fieldname,
			{ df, value: df.default, $wrapper: new Wrapper(), get_value() { return this.value; } }]));
		dialogs.push(this);
	}
	get_value(field) { return this.fields_dict[field].value; }
	async set_value(field, value) { this.fields_dict[field].value = value; }
	async set_values(values) { for (const [field, value] of Object.entries(values)) await this.set_value(field, value); }
	get_values() { return {}; }
	set_df_property(field, property, value) { this.fields_dict[field].df[property] = value; }
	set_primary_action(_, action) { this.primary = action; }
	get_primary_btn() { return this.button; }
	get_close_btn() { return { removeAttr: () => ({ on() {} }) }; }
	add_custom_action() {}
	set_title() {}
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
const frappe = { ui: { Dialog },
	call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
		calls.push({ options, resolve: (message) => resolve({ message }), reject }); return promise; },
	msgprint(message) { messages.push(message); }, show_alert() {},
	confirm(_, yes, no) { confirmation = { yes, no }; }, pages: { "jifeng-legacy": {} } };
const context = { window, frappe, $, structuredClone, __: (text) => text, queueMicrotask };
for (const file of ["jifeng_hr_dictionary", "jifeng_personnel_archive", "jifeng_classic_bridge"]) {
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
}
const editor = new window.JifengHRDictionary({ frames: new Map() });
const pack = (extra = {}) => ({ name: "DICT-1", version: "2026-10-01 00:00:01", company: "Company A",
	dictionary_type: "专长", value: "工艺原文", inactive: 0, can_write: true,
	field_meta: { company: { fieldtype: "Link", reqd: true }, dictionary_type: { fieldtype: "Select" },
		value: { fieldtype: "Data", reqd: true }, inactive: { fieldtype: "Check" } }, ...extra });

async function main() {
	const loading = editor.open("专长");
	assert.equal(calls.at(-1).options.method, "jifeng_erp.hr_dictionary.init_dictionary");
	calls.at(-1).resolve(pack({ name: null, company: null, value: null, version: null }));
	const dialog = await loading;
	await dialog.set_values({ company: "Company A", value: "  工艺原文  " });
	const state = editor.active.get("专长:new:");
	assert.ok(editor.isDirty(state));
	const saving = editor.save(state);
	const saveCall = calls.at(-1);
	assert.equal(saveCall.options.args.payload.value, "  工艺原文  ", "UI retains text until native normalization");
	assert.equal(dialog.fields_dict.company.df.read_only, true, "scope is held while POST is running");
	saveCall.reject(new Error("response lost")); await saving;
	assert.equal(state.unresolved, true); assert.equal(dialog.button.disabled, true);
	const count = calls.length; await editor.save(state);
	assert.equal(calls.length, count, "unknown save never repeats a POST");
	dialog.hide(); assert.equal(dialog.hidden, false, "unknown save cannot be discarded by X/footer");
	const missing = editor.resolve(state);
	assert.equal(calls.at(-1).options.method, "jifeng_erp.hr_dictionary.resolve_creation");
	calls.at(-1).resolve({ status: "not_found" }); await missing;
	assert.equal(state.unresolved, true, "scope-local absence does not authorize another save");
	const resolving = editor.resolve(state);
	calls.at(-1).resolve({ status: "found", archive: pack() }); await resolving;
	assert.equal(state.unresolved, false); assert.equal(state.archive.name, "DICT-1");
	assert.equal(dialog.get_value("value"), "工艺原文");
	assert.equal(dialog.fields_dict.value.df.read_only, true, "saved dictionary text is immutable");
	assert.equal(dialog.fields_dict.inactive.df.read_only, false);
	assert.equal(editor.isDirty(state), false);
	await dialog.set_value("inactive", 1);
	dialog.hide(); assert.ok(confirmation); confirmation.no(); assert.equal(dialog.hidden, false);
	const disabling = editor.save(state);
	calls.at(-1).resolve({ name: "DICT-1" }); await disabling;
	assert.equal(state.unresolved, true, "partial success cannot replace the held draft");
	const changed = editor.resolve(state);
	assert.equal(calls.at(-1).options.method, "jifeng_erp.hr_dictionary.read_dictionary");
	calls.at(-1).resolve(pack({ inactive: 0 })); await changed;
	assert.equal(state.unresolved, true, "different saved inactive value is not this save's result");
	const confirmed = editor.resolve(state);
	calls.at(-1).resolve(pack({ inactive: 1, can_write: false })); await confirmed;
	assert.equal(state.unresolved, false); assert.equal(dialog.fields_dict.inactive.df.read_only, true);
	assert.equal(dialog.button.disabled, true, "new permissions apply after read recovery");

	let current = true, text = "手写原文";
	const chooser = editor.choose({ type: "民族", company: "Company A", current: () => current,
		getValue: () => text, apply: async (value) => { text = value; return true; } });
	const oldQuery = calls.at(-1);
	await chooser.set_value("search", "新关键字"); chooser.fields_dict.search.df.onchange();
	const newQuery = calls.at(-1);
	newQuery.resolve([{ name: "NEW", value: "<script>原文</script>" }]); await tick();
	oldQuery.resolve([{ name: "OLD", value: "过期结果" }]); await tick();
	const host = chooser.fields_dict.results.$wrapper;
	assert.equal(host.buttons.length, 1); assert.equal(host.buttons[0].label, "<script>原文</script>");
	const choice = host.buttons[0].click();
	assert.equal(calls.at(-1).options.method, "jifeng_erp.hr_dictionary.read_dictionary");
	calls.at(-1).resolve(pack({ dictionary_type: "民族", inactive: 1 })); await choice;
	assert.equal(text, "手写原文", "inactive result cannot replace legacy text");
	const typing = host.buttons[0].click(); text = "等待期间的输入";
	calls.at(-1).resolve(pack({ dictionary_type: "民族" })); await typing;
	assert.equal(text, "等待期间的输入", "recheck cannot overwrite concurrent typing");
	const changingCompany = host.buttons[0].click(); current = false;
	calls.at(-1).resolve(pack({ dictionary_type: "民族" })); await changingCompany;
	assert.equal(text, "等待期间的输入", "changed parent scope rejects delayed selection");
	current = true;
	const closing = host.buttons[0].click(); chooser.hide();
	calls.at(-1).resolve(pack({ dictionary_type: "民族" })); await closing;
	assert.equal(text, "等待期间的输入", "closed selector ignores delayed reads");

	const parent = new window.JifengPersonnelArchive({ frames: new Map(), hrDictionaryEditor: editor });
	const personnel = { archive: { can_write: true, field_meta: { supplement: { ethnicity: {} } } },
		dialog: new Dialog({ fields: [{ fieldname: "employee_company", default: "Company A" },
			{ fieldname: "supplement_ethnicity", default: "旧民族原文" }] }),
		closed: false, saving: false, confirming: false };
	parent.installDictionaries(personnel);
	assert.equal(parent.canSelectDictionary(personnel, "ethnicity"), true);
	const nested = parent.selectDictionary(personnel, "ethnicity", "民族");
	assert.equal(personnel.dialog.get_value("supplement_ethnicity"), "旧民族原文", "opening keeps raw text");
	calls.at(-1).resolve([{ name: "HR-1", value: "新民族原文" }]); await tick();
	const use = nested.fields_dict.results.$wrapper.buttons[0].click();
	personnel.archive = { ...personnel.archive, can_write: false };
	calls.at(-1).resolve(pack({ dictionary_type: "民族", value: "新民族原文" })); await use;
	assert.equal(personnel.dialog.get_value("supplement_ethnicity"), "旧民族原文", "new archive permission invalidates picker");
	parent.installDictionaries(personnel);
	assert.equal(personnel.dictionaryButtons.get("ethnicity").disabled, true);
	assert.equal(parent.selectDictionary(personnel, "ethnicity", "民族"), undefined);
	nested.hide(); assert.equal(personnel.dictionaryDialogs.size, 0);

	vm.runInNewContext(fs.readFileSync(path.join(__dirname,
		"../jifeng_erp/public/js/jifeng_workspace_core.js"), "utf8"), context);
	window.Workspace = window.JifengERPWorkspace.Workspace;
	const workspace = Object.create(window.Workspace.prototype);
	const opened = []; workspace.formalBridge = { open(spec) { opened.push(spec); } };
	for (const type of ["专长", "文化", "民族"]) {
		workspace.openPage("人事" + type, "人事");
		assert.equal(opened.at(-1).hrDictionary, type);
		assert.equal(opened.at(-1).filters.dictionary_type, type);
		assert.equal(opened.at(-1).doctype, "Jifeng HR Dictionary");
	}
	console.log("HR dictionary: held saves, scoped menu routes, stale/disabled/private selections and raw text retention passed.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
