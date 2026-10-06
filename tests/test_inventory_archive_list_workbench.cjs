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
	closest(tag) { return this.tag === tag ? this : this.parent?.closest(tag); }
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
function descendants(node, predicate) {
	return node.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
function records(list) { return list.mode === "列表模式" ? list.tbody.children : list.views.get(list.mode).children; }
const settle = () => new Promise(resolve => setImmediate(resolve));
async function modeModels(kind) {
	const opened = [], modeBridge = { inventoryEditor: { active: new Map(), loadingOwners: new Set() },
		openInventory(...args) { opened.push(args); return Promise.resolve(); } };
	const list = new window.JifengInventoryArchiveList(new Element(), kind, modeBridge);
	assert.deepEqual(Array.from(list.modeButtons.keys()), ["列表模式", "图片模式", "列表图文"]);
	assert.equal(list.mode, "列表模式");
	await list.companyControl.set_value("JF"); list.search.value = "同条件";
	list.statusChoice.value = "停用"; list.statusChoice.events.change();
	const data = [row("EXACT-A", kind), row("EXACT-B", kind)];
	for (const item of data) item.profile.inactive = 1;
	data[0].image = { ...data[0].image, state: "已上传", url: "/private/files/authorized.png?fid=FILE-A" };
	data[1].image = { ...data[1].image, state: "无字段读取权限" };
	const query = list.query(2), call = calls.at(-1); call.resolve(reply(call, { total: 102, rows: data })); await query;
	const count = calls.length, revision = list.revision, selectedRows = list.rows;
	const expectedScope = { company: "JF", search: "同条件", status: "停用", page: 2, goto: "2" };
	const scope = () => ({ company: list.companyScope, search: list.search.value, status: list.statusFilter, page: list.page, goto: list.goto.value });
	const position = () => [list.scroller.scrollTop, list.scroller.scrollLeft];
	list.scroller.scrollTop = 32; list.scroller.scrollLeft = 240;
	for (const mode of ["图片模式", "列表图文", "列表模式"]) {
		list.modeButtons.get(mode).events.click();
		assert.equal(calls.length, count, "mode switches must not query, load a detail or write");
		assert.equal(list.revision, revision, "mode switches must keep the query/editor owner valid");
		assert.equal(list.rows, selectedRows, "all modes use the exact validated DTO");
		assert.deepEqual(scope(), expectedScope);
		for (const [name, view] of list.views) assert.equal(view.hidden, name !== mode);
		for (const [name, button] of list.modeButtons) assert.equal(button.attributes["aria-pressed"], String(name === mode));
		const displayed = records(list);
		assert.deepEqual(displayed.map(record => record.attributes["data-archive-name"]), ["EXACT-A", "EXACT-B"]);
		assert.match(displayed[0].textContent, /<b>原文<\/b>/, "DTO strings remain literal in every view");
		assert.match(displayed[0].textContent, /native-user/); assert.match(displayed[1].textContent, /无字段读取权限/);
		const images = descendants(list.scroller, node => node.tag === "img");
		assert.equal(images.length, 1, "render only the active mode and never create an image for masked fields");
		assert.equal(images[0].src, data[0].image.url); assert.equal(images[0].alt, "关联业务商品图片");
		assert.equal(images[0].parent.title, "关联业务商品图片 · Item / IEXACT-A / image");
		for (const record of displayed) {
			const name = record.attributes["data-archive-name"];
			const edit = descendants(record, node => node.tag === "button" && node.textContent === "修改")[0];
			await edit.events.click(); assert.equal(opened.at(-1)[1], name);
			assert.equal(opened.at(-1)[0], kind); assert.equal(opened.at(-1)[2].company, "JF");
			assert.equal(opened.at(-1)[2].listOwner, list); assert.equal(opened.at(-1)[2].isCurrent(), true);
			const before = opened.length;
			record.events.dblclick({ target: edit }); record.events.keydown({ key: "Enter", target: edit, preventDefault() { throw new Error("Do not consume a button Enter"); } });
			assert.equal(opened.length, before, "bubbled edit events cannot open twice");
			record.events.dblclick({ target: record }); await settle(); assert.equal(opened.at(-1)[1], name);
			let prevented = false;
			record.events.keydown({ key: "Enter", target: record, preventDefault() { prevented = true; } }); await settle();
			assert.equal(prevented, true); assert.equal(opened.at(-1)[1], name);
			assert.equal(descendants(record, node => node.tag === "button" && node.textContent === "删除")[0].disabled, true);
		}
		if (mode === "图片模式") { assert.deepEqual(position(), [0, 0]); list.scroller.scrollTop = 70; }
		if (mode === "列表图文") { assert.deepEqual(position(), [0, 0]); list.scroller.scrollTop = 125; list.scroller.scrollLeft = 12; }
	}
	assert.deepEqual(position(), [32, 240]);
	list.setMode("图片模式"); assert.deepEqual(position(), [70, 0]);
	list.setMode("列表图文"); assert.deepEqual(position(), [125, 12]);
	list.setMode("invalid"); assert.equal(list.mode, "列表图文");
	// A completed editor returns to the same mode, page, filters and position through the existing refresh path.
	const returnScope = opened.at(-1)[2];
	const saved = list.refreshSaved({ kind, profile: { company: "JF" } }), savedCall = calls.at(-1);
	assert.deepEqual({ ...savedCall.options.args }, { kind, company: "JF", search: "同条件", status: "停用", page: 2, page_size: 100 });
	savedCall.resolve(reply(savedCall, { total: 102, rows: data })); await saved;
	assert.equal(list.mode, "列表图文"); assert.deepEqual(position(), [125, 12]); assert.deepEqual(scope(), expectedScope);
	assert.equal(returnScope.isCurrent(), false, "saved refresh still invalidates an obsolete opening owner");
	list.setMode("列表模式"); const switched = list.query(2), switchedCall = calls.at(-1), switchingCount = calls.length;
	list.scroller.scrollTop = 0; list.scroller.scrollLeft = 0; list.scroller.events.scroll(); list.setMode("图片模式");
	list.scroller.scrollTop = 0; list.scroller.events.scroll();
	assert.equal(calls.length, switchingCount, "switching a pending query does not start another read");
	switchedCall.resolve(reply(switchedCall, { total: 102, rows: data })); await switched;
	assert.equal(list.mode, "图片模式"); assert.deepEqual(position(), [70, 0], "query return restores the current mode, not its start mode");
	// Switching while results are empty cannot erase either mode's position or reuse the start mode's scroll on return.
	list.setMode("列表模式"); const pending = list.query(2), pendingCall = calls.at(-1);
	list.scroller.scrollTop = 0; list.scroller.scrollLeft = 0; list.scroller.events.scroll();
	list.setMode("图片模式"); list.scroller.scrollTop = 0; list.scroller.events.scroll();
	const fresh = list.refreshSaved({ kind, profile: { company: "JF" } }), freshCall = calls.at(-1);
	freshCall.resolve(reply(freshCall, { total: 102, rows: data })); await fresh;
	assert.deepEqual(position(), [70, 0]);
	list.scroller.scrollTop = 88; list.scroller.events.scroll();
	list.setMode("列表图文"); assert.deepEqual(position(), [125, 12]);
	pendingCall.resolve(reply(pendingCall, { total: 102, rows: data })); await pending;
	assert.deepEqual(position(), [125, 12], "stale query completion cannot reset another mode's position");
	list.setMode("图片模式"); assert.deepEqual(position(), [88, 0]);
	list.setMode("列表模式"); assert.deepEqual(position(), [32, 240]);
	// Presentation changes preserve busy/unknown draft and import ownership and never abort an export.
	const draft = { listOwner: list, closed: false, dirty: true, unknown: true };
	modeBridge.inventoryEditor.active.set("draft", draft);
	const before = calls.length, oldImporter = { getCloseBlock: () => "导入结果待确认", scopeChanged() { throw new Error("Mode change cannot invalidate an import"); } };
	list.archiveImporter = oldImporter;
	let aborted = false; const exporting = { abort() { aborted = true; } };
	list.exportController = exporting; list.exporting = true;
	list.setMode("图片模式"); list.setMode("列表图文");
	assert.equal(calls.length, before); assert.equal(list.getCloseBlock(), "导入结果待确认");
	assert.equal(modeBridge.inventoryEditor.active.get("draft"), draft); assert.equal(list.archiveImporter, oldImporter);
	assert.equal(list.exportController, exporting); assert.equal(list.exporting, true); assert.equal(aborted, false);
	list.archiveImporter = null; assert.equal(list.getCloseBlock(), "请先关闭档案录入窗口。");
	modeBridge.inventoryEditor.active.clear(); modeBridge.inventoryEditor.loadingOwners.add(list);
	assert.equal(list.getCloseBlock(), "正在读取档案，请稍候。"); list.setMode("列表模式");
	assert.equal(list.getCloseBlock(), "正在读取档案，请稍候。"); modeBridge.inventoryEditor.loadingOwners.clear();
	list.exportController = null; list.exporting = false;
	// Attachment states reuse imageCell, and an unsafe DTO fails before any mode renders it.
	for (const state of ["未上传", "附件不可用"]) {
		const result = list.query(2), request = calls.at(-1);
		const changed = data.map(item => ({ ...item, image: { ...item.image, state, url: "" } }));
		request.resolve(reply(request, { total: 102, rows: changed })); await result;
		for (const mode of list.modeButtons.keys()) {
			list.setMode(mode); assert.equal(descendants(list.scroller, node => node.tag === "img").length, 0);
			assert.match(list.views.get(mode).textContent, new RegExp(state));
		}
	}
	const unsafe = list.query(2), unsafeCall = calls.at(-1);
	unsafeCall.resolve(reply(unsafeCall, { total: 102, rows: [
		{ ...data[0], image: { ...data[0].image, url: "https://untrusted.invalid/image.png" } }, data[1]] })); await unsafe;
	assert.equal(list.total, null); assert.equal(list.rows.length, 0);
	for (const mode of list.modeButtons.keys()) { list.setMode(mode); assert.equal(records(list).length, 0); }
	await list.companyControl.set_value("OTHER");
	assert.deepEqual(Array.from(list.modeScroll.values()).map(scroll => [scroll.top, scroll.left]), [[0, 0], [0, 0], [0, 0]]);
	const last = list.mode; list.destroy(); list.setMode("图片模式"); assert.equal(list.mode, last);
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
	for (const kind of ["物料", "材料"]) await modeModels(kind);
	const browse = new window.JifengInventoryCatalog(new Element(), "物料");
	assert.equal(browse.fields.length, 20); assert.equal(browse.modeButtons, undefined); assert.equal(browse.scroller.children.length, 1);
	console.log("Archive lists: three DTO-only modes, exact-ID editor return, shared filters/pages, independent scroll, image permission states, stale replies and unchanged draft/import/export ownership passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
