// Run the actual browse superclass and archive subclass with reordered RPCs.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this.attributes = {}; this.value = "";
		this.scrollTop = 0; this.scrollLeft = 0; this.classList = { add() {} }; this._text = ""; }
	get firstElementChild() { return this.children[0]; }
	append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
	prepend(...children) { for (const child of children) child.parent = this; this.children.unshift(...children); }
	after(child) { child.parent = this.parent; this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, child); }
	replaceChildren(...children) { this._text = ""; this.children = []; this.append(...children); }
	set textContent(text) { this._text = String(text); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("No source HTML execution"); }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, action) { this.events[name] = action; }
}
const calls = [], messages = [], edits = [];
const window = {}, document = { createElement: tag => new Element(tag) };
const frappe = { msgprint: text => messages.push(text), ui: { form: { make_control({ df }) {
	return { value: null, get_value() { return this.value; }, async set_value(value) { this.value = value; await df.onchange?.(); }, $input: { on() {} } };
} } }, call(options) {
	let resolve; const promise = new Promise(yes => { resolve = yes; }); calls.push({ options, resolve: message => resolve({ message }) }); return promise;
} };
const context = { window, document, frappe, __: value => value };
for (const file of ["jifeng_inventory_catalog.js", "jifeng_inventory_archive_list.js"])
	vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/" + file, "utf8"), context);
const bridge = { openInventory(...args) { edits.push(args); return Promise.resolve(); }, inventoryEditor: { active: new Map() } };
function row(name = "P1", kind = "物料", company = "JF") {
	const profile = { company, archive_kind: kind, item_code: "I" + name, inactive: 0, legacy_number: "0001", display_name: "<b>原文</b>" }, item = { item_code: "I" + name };
	const meta = values => Object.fromEntries(Object.keys(values).map(field => [field, { read_only: true }]));
	return { name, company, kind, item_code: item.item_code, profile_version: "2026-10-02T01:00:00.123456", item_version: "2026-10-02T01:00:00.123456",
		profile, item, field_meta: { profile: meta(profile), item: meta(item) }, readonly: true, can_write: false,
		audit: { owner: "native-user", modified: "2026-10-02T01:00:00.123456" },
		image: { source_doctype: "Item", source_name: item.item_code, source_field: "image", label: "关联业务商品图片", state: "未上传", url: "" } };
}
function reply(call, { total = 1, rows = [row()], page = call.options.args.page } = {}) {
	return { ...call.options.args, page, total, total_pages: Math.ceil(total / call.options.args.page_size), rows, readonly: true, can_write: false };
}
async function main() {
	const list = new window.JifengInventoryArchiveList(new Element(), "物料", bridge);
	assert.equal(list.fields.length, 26); assert.equal(list.statusFilter, "全部");
	await list.openEditor(); assert.equal(edits.length, 0); assert.equal(messages.at(-1), "请选择公司后新增或打开档案。");
	await list.companyControl.set_value("JF");
	const pending = list.query(); const call = calls.at(-1);
	assert.equal(call.options.method, "jifeng_erp.inventory_archive_list.query_archives");
	call.resolve(reply(call)); await pending;
	assert.match(list.tbody.textContent, /<b>原文<\/b>/); assert.match(list.tbody.textContent, /native-user/);
	assert.equal(list.rows[0].can_write, false, "list never turns its readonly DTO into write authority");
	await list.openEditor("P1");
	assert.equal(edits[0][0], "物料"); assert.equal(edits[0][1], "P1"); assert.equal(edits[0][2].company, "JF");
	assert.equal(edits[0][2].isCurrent(), true);
	const oldScope = edits[0][2]; await list.companyControl.set_value("OTHER");
	assert.equal(oldScope.isCurrent(), false, "a pending editor cannot open a changed company");
	assert.equal(list.rows.length, 0); await list.openEditor("P1"); assert.equal(edits.length, 1);
	await list.companyControl.set_value("JF");
	const old = list.query(); const oldCall = calls.at(-1);
	list.search.value = "changed"; list.search.events.input();
	oldCall.resolve(reply(oldCall)); await old; assert.equal(list.total, null, "stale query cannot recover cleared results");
	list.search.value = ""; const first = list.query(); const firstCall = calls.at(-1);
	firstCall.resolve(reply(firstCall)); await first;
	list.scroller.scrollTop = 32; list.scroller.scrollLeft = 240;
	const refresh = list.refreshSaved({ kind: "物料", profile: { company: "JF" } }); const fresh = calls.at(-1);
	fresh.resolve(reply(fresh)); await refresh;
	assert.equal(list.scroller.scrollTop, 32); assert.equal(list.scroller.scrollLeft, 240);
	const beforeSave = list.query(); const beforeSaveCall = calls.at(-1);
	const savedWhileQuerying = list.refreshSaved({ kind: "物料", profile: { company: "JF" } });
	const afterSaveCall = calls.at(-1);
	assert.notEqual(afterSaveCall, beforeSaveCall, "save during a pending query starts a fresh read");
	afterSaveCall.resolve(reply(afterSaveCall, { rows: [row("AFTER-SAVE")] })); await savedWhileQuerying;
	beforeSaveCall.resolve(reply(beforeSaveCall)); await beforeSave;
	assert.equal(list.rows[0].name, "AFTER-SAVE", "pre-save reply cannot replace the saved refresh");
	const before = calls.length; list.refreshSaved({ kind: "材料", profile: { company: "JF" } }); assert.equal(calls.length, before);
	const clamped = list.query(2); const clampCall = calls.at(-1); clampCall.resolve(reply(clampCall, { page: 1 })); await clamped;
	assert.equal(list.page, 1, "saved removal of the last result returns to a valid page");
	bridge.inventoryEditor.active.set("owned", { listOwner: list, closed: false });
	assert.equal(list.getCloseBlock(), "请先关闭档案录入窗口。"); bridge.inventoryEditor.active.clear();
	list.destroy(); assert.equal(oldScope.isCurrent(), false);
	const materials = new window.JifengInventoryArchiveList(new Element(), "材料", bridge);
	assert.equal(materials.fields.length, 25); assert.equal(materials.fields[3][0], "shape");
	console.log("Archive lists: exact kind columns, separate readonly authority, stale editor/query guards, save refresh and last-page recovery passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
