// Actual client classes, simulated RPC/controls only; no browser or database.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const crypto = require("node:crypto"), tick = () => new Promise(setImmediate);
class Element {
	constructor() { this.children = []; this.value = ""; this.style = {}; this.checked = false; this.textContent = ""; }
	append(...values) { this.children.push(...values); }
	replaceChildren(...values) { this.children = values; }
	setAttribute() {}
	addEventListener() {}
}
const requests = [], messages = [], window = {}, document = { createElement: () => new Element() };
let dialogs = 0;
const frappe = {
	call(options) { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; });
		requests.push({ options, resolve: message => resolve({ message }), reject }); return promise; },
	msgprint: value => messages.push(value), show_alert() {}, confirm() {}, ui: { Dialog: class { constructor() { dialogs++; } } },
};
const context = { window, document, frappe, structuredClone, queueMicrotask, __: x => x, Uint8Array,
	crypto: { getRandomValues: bytes => crypto.randomFillSync(bytes) } };
for (const file of ["jifeng_product_dictionary", "jifeng_inventory_words"]) vm.runInNewContext(
	fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
const Words = window.JifengInventoryWords, plain = value => JSON.parse(JSON.stringify(value));
const uuid = "12345678-1234-4234-9234-123456789012", digest = "a".repeat(64);
const metadata = { legacy_number: { fieldtype: "Data", read_only: true }, value: { fieldtype: "Data" },
	remarks: { fieldtype: "Small Text" }, inactive: { fieldtype: "Check" } };
const record = (extra = {}) => ({ name: "SHAPE-1", version: "v1", company: "Company A", dictionary_type: "形状",
	numbering_mode: "automatic_v1", legacy_number: "017", value: "花朵/植物", remarks: "历史\n原文", inactive: 0,
	can_write: true, can_delete: true, field_meta: metadata, ...extra });
function bench() {
	const view = Object.create(Words.prototype);
	Object.assign(view, { type: "形状", dictionaryApi: "jifeng_erp.inventory_words", dictionaryOptions: { editorFields: ["legacy_number", "value", "remarks", "inactive"] },
		revision: 0, companyControl: { get_value: () => "Company A" }, search: new Element(), rows: [], tbody: new Element(),
		status: new Element(), count: new Element(), readyArchive: { can_write: true, can_pinyin: true, can_print: true },
		addButton: {}, pinyinButton: {}, queryButton: {}, printButton: {}, previous: {}, next: {}, resolveButton: {} });
	return view;
}
function state(view, archive = record()) {
	const fields = Object.fromEntries(Object.entries(metadata).map(([name, df]) => [name, {
		df: { ...df, fieldtype: name === "remarks" ? "Data" : df.fieldtype }, value: archive[name], get_value() { return this.value; },
	}]));
	fields.remarks.value = archive.remarks.replace(/[\r\n]/g, "");
	const model = { archive, company: archive.company, closed: false, busy: false, unresolved: false, confirming: false, review: new Element(),
		dialog: { fields_dict: fields, get_values: () => ({}), set_values: async values => { for (const [name, value] of Object.entries(values)) fields[name].value = value; } } };
	view.editor = model; model.original = JSON.stringify(view.payload(model)); return model;
}
async function main() {
	const view = bench();
	const old = view.query(0), first = requests.at(-1);
	view.search.value = "新查询"; view.revision++;
	const recent = view.query(0), second = requests.at(-1);
	second.resolve({ company: "Company A", dictionary_type: "形状", offset: 0, has_more: false, rows: [record()] }); await recent;
	first.resolve({ company: "Company A", dictionary_type: "形状", offset: 0, rows: [record({ value: "旧响应" })] }); await old;
	assert.equal(view.rows[0].value, "花朵/植物", "late query cannot replace current rows");
	const wrong = view.query(0); requests.at(-1).resolve({ company: "Other", dictionary_type: "形状", offset: 0, rows: [record()] }); await wrong;
	assert.equal(view.rows.length, 0, "wrong company envelope cannot populate shape rows");
	assert.equal(view.loadedScope, null);

	const edit = bench(), model = state(edit);
	assert.equal("remarks" in edit.payload(model), false, "unchanged historical multiline notes are omitted from a single-line control");
	model.dialog.fields_dict.inactive.value = 1;
	assert.equal(edit.payload(model).request_id, model.wordRequestId);
	const originalRequest = model.wordRequestId;
	edit.freeze = () => {}; let queries = 0; edit.query = () => { queries++; };
	const save = edit.save(model), preview = requests.at(-1);
	assert.equal(preview.options.type, "GET"); preview.resolve({ request_id: originalRequest, company: "Company A", dictionary_type: "形状", digest });
	await tick(); const dispatched = requests.at(-1);
	assert.equal(dispatched.options.method, "jifeng_erp.inventory_words.save_dictionary");
	assert.equal(dispatched.options.type, undefined); dispatched.reject(new Error("response lost")); await save;
	assert.equal(model.unresolved, true); assert.match(edit.getCloseBlock(), /保存结果/);
	const count = requests.length; await edit.save(model); assert.equal(requests.length, count, "an unknown save cannot POST again");
	const resolveWrong = edit.resolve(model), read = requests.at(-1);
	assert.equal(read.options.type, "GET"); assert.equal(read.options.args.request_id, originalRequest);
	read.resolve({ status: "applied", archives: [record({ version: "v2", inactive: 1, change_request_id: uuid })] }); await resolveWrong;
	assert.equal(model.unresolved, true, "equal values with another request do not prove this save");
	const resolveExact = edit.resolve(model);
	requests.at(-1).resolve({ status: "applied", archives: [record({ version: "v2", inactive: 1, change_request_id: originalRequest })] }); await resolveExact;
	assert.equal(model.unresolved, false); assert.equal(model.archive.version, "v2"); assert.equal(edit.isDirty(model), false);
	assert.equal(requests.filter(r => r.options.method.endsWith(".save_dictionary")).length, 1);
	assert.ok(queries > 0);

	const batch = bench(); let batchQueries = 0; batch.query = () => { batchQueries++; };
	const deletion = { company: "Company A", dictionary_type: "形状", request_id: uuid, operation: "delete", name: "SHAPE-1", version: "v1" };
	const removing = batch.operation(deletion);
	requests.at(-1).resolve({ company: "Company A", dictionary_type: "形状", request_id: uuid, digest }); await tick();
	requests.at(-1).reject(new Error("delete response lost")); await removing;
	assert.ok(batch.wordPending); const before = requests.length;
	await batch.operation(deletion); assert.equal(requests.length, before);
	const missing = batch.resolveOperation(); requests.at(-1).resolve({ status: "not_found", request_id: uuid }); await missing;
	assert.ok(batch.wordPending, "missing receipt keeps the delete frozen");
	const found = batch.resolveOperation(); assert.equal(requests.at(-1).options.type, "GET");
	requests.at(-1).resolve({ status: "applied", request_id: uuid, digest, company: "Company A", dictionary_type: "形状", deleted: ["SHAPE-1"] }); await found;
	assert.equal(batch.wordPending, null); assert.equal(batchQueries, 1);
	batch.wordPending = { payload: { ...deletion, operation: "pinyin", rows: [{ name: "SHAPE-1", abbreviation: "HD/ZW" }] }, digest };
	const result = { status: "applied", request_id: uuid, digest, company: "Company A", dictionary_type: "形状",
		archives: [record({ abbreviation: "HD/ZW", change_request_id: uuid })] };
	assert.equal(batch.validOperation(result), true);
	assert.equal(batch.validOperation({ ...result, archives: [record({ abbreviation: "HD/ZW" })] }), false, "later edits cannot acknowledge an old pinyin receipt");

	const close = bench(), closed = state(close), wrapper = { modal() {}, on() {}, removeAttr() { return this; } };
	closed.dialog.$wrapper = wrapper; closed.dialog.get_close_btn = () => wrapper;
	closed.dialog.hide = () => closed.dialog.on_hide(); close.guardClose(closed); close.updateControls();
	assert.ok(close.pinyinButton.disabled); closed.dialog.hide();
	assert.equal(close.editor, null); assert.equal(close.pinyinButton.disabled, false, "closing an unchanged editor re-enables pinyin");

	const printing = bench(); printing.rows = [record()]; printing.loadedScope = { company: "Company A", search: "" };
	const print = printing.print(), printCall = requests.at(-1); assert.equal(printCall.options.type, "GET"); printing.revision++;
	printCall.resolve({ company: "Company A", dictionary_type: "形状", row_count: 1, html: "safe preview" }); await print;
	assert.equal(dialogs, 0, "a late print response cannot open a stale preview");
	assert.equal(plain(model.candidate).request_id, originalRequest);
	console.log("Shape client late/company queries, multiline preservation, single-dispatch unknown save/delete, GET-only exact receipt resolution, pinyin receipt, close controls and stale print passed (protocol only).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
