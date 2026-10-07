// Exercise payload boundaries and repeat saves without a browser or business data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const window = {};
const calls = [];
const messages = [];
let reply;
const frappe = {
	call: async (options) => { calls.push(options); if (reply instanceof Error) throw reply; return { message: reply }; },
	show_alert() {},
	msgprint(text) { messages.push(text); },
	utils: { escape_html: (value) => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;") },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_personnel_archive.js"), "utf8"),
	{ window, frappe, structuredClone, __: (text) => text, queueMicrotask });
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_classic_bridge.js"), "utf8"),
	{ window });
const editor = new window.JifengPersonnelArchive({ frames: new Map() });
const control = (value, read_only = false) => ({ df: { read_only }, get_value() { return this.value; }, value,
	refresh() {} });

function fixture() {
	const archive = { name: "EMP-1", supplement_name: "OLD-ID", employee_modified: "2026-10-01 00:00:01",
		supplement_modified: "2026-10-01 00:00:02", can_write: true,
		employee: { employee_name: "王 小 明", first_name: "王", middle_name: "小", last_name: "明" },
		supplement: { legacy_base_salary: 0, legacy_account: "00123" }, histories: {},
		field_meta: { employee: { image: {} }, supplement: { identity_image: {}, legacy_base_salary: {}, legacy_account: {} } } };
	const fields = {
		archive_name: control("王 小 明"),
		employee_company: control("Original Company", true),
		employee_status: control("Left", true),
		supplement_legacy_base_salary: control(0),
		supplement_legacy_account: control("00123"),
		salary_history: control([{ name: "CHILD-1", project: "旧项目", wage: 0, title: "旧职務",
			protected_column: "retained only on server" },
			{ name: "dialog-random-row", __islocal: true, idx: 2, project: "新项目", wage: 20 }]),
		education_history: control([{ name: "dialog-random-education", __islocal: true,
			project: "学习", study_content: "内容", school: "学校" }]),
	};
	fields.salary_history.df.fields = [
		{ fieldname: "project" }, { fieldname: "wage" }, { fieldname: "title", read_only: true },
	];
	let refreshes = 0;
	fields.salary_history.grid = { refresh() { fields.salary_history.value = fields.salary_history.df.data; refreshes++; } };
	fields.education_history.df.fields = ["project", "study_content", "from_date", "to_date", "school", "title"]
		.map((fieldname) => ({ fieldname }));
	fields.education_history.grid = { refresh() { fields.education_history.value = fields.education_history.df.data; } };
	const button = { disabled: false, prop(_, value) { this.disabled = value; return this; }, text() {} };
	const dialog = { fields_dict: fields, get_values: () => ({}), get_primary_btn: () => button,
		async set_values(values) { for (const [key, value] of Object.entries(values)) fields[key].value = value; },
		set_df_property(key, property, value) { fields[key].df[property] = value; }, set_title() {} };
	const bindings = [
		{ group: "employee", fieldname: "company", controlName: "employee_company", locked: true },
		{ group: "employee", fieldname: "status", controlName: "employee_status", locked: true },
		{ group: "supplement", fieldname: "legacy_base_salary", controlName: "supplement_legacy_base_salary" },
		{ group: "supplement", fieldname: "legacy_account", controlName: "supplement_legacy_account" },
	];
	return { archive, dialog, bindings, tables: { salary_history: {}, education_history: {} }, saving: false,
		button, get refreshes() { return refreshes; } };
}

async function main() {
	const cache = new window.JifengPersonnelArchive({ frames: new Map() });
	const loaded = [];
	cache.load = async (name, options) => { loaded.push({ name, ...options }); return { name, ...options }; };
	const longDraft = { title: "existing long-term draft" };
	cache.active.set("new", { dialog: longDraft });
	assert.equal(await cache.open(), longDraft);
	await cache.open(undefined, { term_type: "临时", company: "C-1" });
	await cache.open(undefined, { term_type: "临时", company: "C-2" });
	assert.equal(loaded.length, 2, "temporary/company drafts do not replace the existing default draft");
	assert.equal(loaded[0].term_type, "临时");
	assert.notEqual(cache.archiveKey(null, loaded[0]), cache.archiveKey(null, loaded[1]));
	assert.equal(cache.archiveKey("EMP-1", { term_type: "临时" }), "EMP-1",
		"opening an existing archive uses its identity, never a requested classification");
	const delayedEditor = new window.JifengPersonnelArchive({ frames: new Map() });
	const originalCall = frappe.call;
	let finishRead, current = true, readArgs;
	frappe.call = (options) => { readArgs = options.args; return new Promise(resolve => { finishRead = resolve; }); };
	const delayedOpen = delayedEditor.open("EMP-1", { stillCurrent: () => current });
	current = false;
	finishRead({ message: { name: "EMP-1" } });
	assert.equal(await delayedOpen, undefined, "closed or changed selector cannot open a late archive response");
	assert.equal(delayedEditor.active.size, 0);
	assert.deepEqual(Object.keys(readArgs), ["name"], "current callback never enters the archive RPC");
	frappe.call = originalCall;
	const state = fixture();
	let payload = editor.payload(state);
	assert.equal(payload.supplement.legacy_base_salary, 0, "zero is a value");
	assert.equal(payload.supplement.legacy_account, "00123", "raw account stays text");
	assert.deepEqual(Object.keys(payload.employee), [], "unchanged split name and locked native state are absent");
	assert.equal(payload.histories.salary_history[0].name, "CHILD-1", "row identity is retained");
	assert.ok(!("name" in payload.histories.salary_history[1]), "new dialog row random name is not a server identity");
	assert.ok(!("name" in payload.histories.education_history[0]), "new education rows use native insert");
	assert.ok(!("__islocal" in payload.histories.education_history[0]), "client grid flags are not server input");
	assert.ok(!("title" in payload.histories.salary_history[0]), "read-only child columns are absent");
	assert.ok(!("protected_column" in payload.histories.salary_history[0]), "unknown child columns are absent");
	state.dialog.fields_dict.archive_name.value = "新名字";
	payload = editor.payload(state);
	assert.deepEqual(Object.keys(payload.employee).sort(), ["first_name", "last_name", "middle_name"],
		"only an explicit name edit replaces name parts");
	assert.equal(payload.employee.first_name, "新名字");
	assert.equal(editor.uploadOptions(state.archive, "supplement", "identity_image").options.docname, "OLD-ID",
		"renamed employee must use the true Supplement name");
	state.dialog.fields_dict.archive_name.value = "王 小 明";
	reply = { ...state.archive, employee_modified: "new-employee-version", supplement_modified: "new-supplement-version",
		histories: { salary_history: [{ name: "CHILD-1", project: "旧项目", wage: 0 },
			{ name: "NEW-CHILD-ID", project: "新项目", wage: 20 }],
			education_history: [{ name: "EDUCATION-ROW", project: "学习", study_content: "内容", school: "学校" }] } };
	await editor.save(state);
	payload = editor.payload(state);
	assert.equal(payload.employee_modified, "new-employee-version");
	assert.equal(payload.supplement_modified, "new-supplement-version");
	assert.equal(payload.histories.salary_history[1].name, "NEW-CHILD-ID", "repeat save uses server child IDs");
	assert.equal(payload.histories.education_history[0].name, "EDUCATION-ROW", "education repeat save uses server child ID");
	assert.equal(state.refreshes, 1);
	assert.equal(state.original, JSON.stringify(payload));
	assert.equal(state.button.disabled, false);
	state.dialog.fields_dict.supplement_legacy_account.value = "changed draft";
	reply = new Error("second-document save rejected");
	await editor.save(state);
	assert.equal(state.dialog.fields_dict.supplement_legacy_account.value, "changed draft", "failed save keeps typed input");
	assert.notEqual(state.original, JSON.stringify(editor.payload(state)), "failed save remains dirty");
	assert.equal(state.archive.employee_modified, "new-employee-version", "failure does not advance version");
	assert.equal(state.button.disabled, false);
	assert.equal(state.saving, false);
	assert.equal(calls.length, 2, "each save makes one atomic API request");
	const formalState = fixture();
	formalState.archive.formal_records = [
		{ doctype: "Employee Transfer", name: "TRANSFER-1", effective_date: "2026-09-01", docstatus: 1 },
		{ doctype: "Employee Promotion", name: "<PROMOTION>", docstatus: 2 },
		{ doctype: "Employee Separation", name: "EXIT-1", docstatus: 1, exit_status: "已办理" },
		{ doctype: "Employee Separation", name: "EXIT-DRAFT", docstatus: 0, exit_status: "待办理" },
	];
	let html, handler, hides = 0;
	const wrapper = { html(value) { html = value; return this; }, off() { return this; },
		on(_event, _selector, callback) { handler = callback; return this; } };
	formalState.dialog.fields_dict.formal_records = { $wrapper: wrapper };
	formalState.dialog.hide = () => { hides++; };
	formalState.original = JSON.stringify(editor.payload(formalState));
	const opened = [];
	editor.bridge.open = (spec) => opened.push(spec);
	editor.renderFormalRecords(formalState);
	assert.ok(html.includes("已提交") && html.includes("已取消") && html.includes("已办理") && html.includes("草稿"),
		"formal state is distinguished from exit processing phase and legacy archive labels");
	assert.ok(html.includes("&lt;PROMOTION&gt;") && !html.includes("<PROMOTION>"), "native identities are escaped in HTML");
	assert.ok(!Object.hasOwn(editor.payload(formalState), "formal_records"), "formal history never enters archive save payload");
	handler({ currentTarget: { dataset: { formalRecord: "1" } } });
	assert.equal(JSON.stringify(opened[0].route), JSON.stringify(["Form", "Employee Promotion", "<PROMOTION>"]),
		"opening uses the exact native doctype/name, not a legacy row or employee identifier");
	assert.equal(hides, 1);
	assert.equal(calls.length, 2, "opening a source never autosaves or submits it");
	formalState.dialog.fields_dict.supplement_legacy_account.value = "unsaved archive input";
	editor.openFormalRecord(formalState, 0);
	assert.equal(opened.length, 1, "dirty archive must remain open instead of losing draft values");
	assert.equal(hides, 1);
	assert.equal(formalState.dialog.fields_dict.supplement_legacy_account.value, "unsaved archive input");
	assert.ok(messages.at(-1).includes("先保存"));
	formalState.dialog.fields_dict.supplement_legacy_account.value = "00123";
	formalState.saving = true;
	editor.openFormalRecord(formalState, 0);
	formalState.saving = false;
	for (const index of [-1, 0.5, 99]) editor.openFormalRecord(formalState, index);
	assert.equal(opened.length, 1, "pending save and invalid references cannot navigate");
	formalState.archive.can_write = false;
	editor.openFormalRecord(formalState, 2);
	assert.equal(JSON.stringify(opened[1].route), JSON.stringify(["Form", "Employee Separation", "EXIT-1"]),
		"a read-only archive can open a readable source without write permission");
	formalState.archive.formal_records = [];
	editor.renderFormalRecords(formalState);
	assert.ok(html.includes("暂无可读取"), "an empty permission-filtered timeline does not claim no underlying records exist");
	const bridge = Object.create(window.JifengClassicBridge.prototype);
	let dirty = true, block = "保存结果尚未核实";
	const frame = { contentWindow: { cur_frm: { is_dirty: () => false },
		jifeng_wage_workbench: { is_dirty: () => dirty, get_close_block: () => block } } };
	bridge.frames = new Map([["wages", { iframe: frame }]]);
	assert.equal(bridge.canClose("wages"), false, "custom page drafts must participate in native close guard");
	assert.equal(bridge.closeBlock("wages"), block, "unknown batch result blocks destructive tab disposal");
	dirty = false;
	block = "";
	assert.equal(bridge.canClose("wages"), true);
	assert.equal(bridge.closeBlock("wages"), "");
	console.log("Personnel archive payload: permissions, split names, zero/text, child IDs, versions and failure retention passed.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
