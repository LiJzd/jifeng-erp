// DOM/protocol models only: no browser, database, files uploaded or network.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), nodeCrypto = require("node:crypto");
const calls = [], dialogs = [], uploads = [], messages = [], listeners = new Map(); let confirmation;
const tick = () => new Promise(setImmediate);
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this._value = ""; this.checked = false;
		this.classList = { add() {} }; this.attributes = {}; this._text = ""; this.scrollTop = 0; this.scrollLeft = 0; }
	set value(value) { this._value = String(value ?? ""); if (this.tag === "input" && this.type !== "checkbox") this._value = this._value.replace(/[\r\n]/g, ""); }
	get value() { return this._value; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((child) => child.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("untrusted HTML rendering forbidden"); }
	append(...nodes) { this.children.push(...nodes); }
	appendChild(node) { this.append(node); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	remove() { this.removed = true; }
	setAttribute(key, value) { this.attributes[key] = value; }
	addEventListener(event, action) { this.events.set(event, action); }
	descendants(tag) { return this.children.flatMap((child) => [ ...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Wrapper {
	constructor(element = new Element()) { this.element = element; this.events = new Map(); }
	get() { return this.element; }
	addClass() { return this; }
	removeAttr() { return this; }
	modal() { return this; }
	on(events, action) { for (const event of events.split(" ")) this.events.set(event.split(".")[0], action); return this; }
	prop(field, value) { this[field] = value; return this; }
}
class Dialog {
	constructor(config = {}) { this.config = config; this.hidden = false; this.$wrapper = new Wrapper(); this.button = new Wrapper(); this.close = new Wrapper();
		this.fields_dict = Object.fromEntries((config.fields || []).map((df) => [df.fieldname, { df: { ...df }, value: df.default, $wrapper: new Wrapper(), get_value() { return this.value; } }]));
		this.fields_dict.__section_1 = { df: { fieldtype: "Section Break" } }; dialogs.push(this); }
	get_value(field) { return this.fields_dict[field].value; }
	get_values() { return Object.fromEntries((this.config.fields || []).filter((df) => df.fieldtype !== "HTML").map((df) => [df.fieldname, this.get_value(df.fieldname)])); }
	get_primary_btn() { return this.button; }
	get_close_btn() { return this.close; }
	set_title(title) { this.title = title; }
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
class FileUploader {
	constructor(options) { this.options = options; this.dialog = new Dialog(); this.uploader = { files: [{ private: true, file_obj: {} }] }; this.nativeWrites = 0; this.nativeHidden = 0;
		this.dialog.on_hide = () => { this.nativeHidden++; }; uploads.push(this); }
	upload_files() { this.nativeWrites++; return Promise.resolve(); } // Native resolves before its HTTP response callback.
}
const window = { addEventListener(name, action) { listeners.set(action, name); }, removeEventListener(_, action) { listeners.delete(action); } };
const document = { createElement: (tag) => new Element(tag) };
const frappe = { pages: { "jifeng-legacy": {} }, ui: { Dialog, FileUploader, form: { make_control({ df }) {
	return { df: { ...df }, $input: new Wrapper(), value: null, get_value() { return this.value; }, async set_value(value) { await Promise.resolve(); this.value = value; await df.onchange?.(); }, refresh() {} };
} } }, call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); calls.push({ options, resolve: (message) => resolve({ message: options.method.endsWith(".query_archives") && Array.isArray(message) ? pageResult(options.args, message) : message }), reject }); return promise; },
	msgprint(message) { messages.push(message); }, show_alert() {}, confirm(_, yes, no) { confirmation = { yes, no }; } };
const context = { window, document, frappe, structuredClone, __: (text) => text, Uint8Array,
	crypto: { getRandomValues: (bytes) => nodeCrypto.randomFillSync(bytes) } };
for (const file of ["jifeng_product_archive", "jifeng_classic_bridge"]) vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
const originalFields = ["legacy_number", "legacy_code", "major_category", "minor_category", "old_number", "brand", "material", "shared_version", "patent_number", "display_name", "source", "designer", "pattern_maker", "modeler", "style", "owner_name", "region", "display_uom", "list_price", "blank_price", "weight", "silver_weight", "stone_weight", "inactive", "legacy_remarks", "notes", "profile_image", "patent_image"];
const extraFields = ["supplier_product_code", "supplier", "specification"], floatFields = ["list_price", "blank_price", "weight", "silver_weight", "stone_weight"];
function meta(saved = false) {
	return { profile: { company: { fieldtype: "Link", options: "Company", read_only: saved }, item_code: { fieldtype: "Link", read_only: true }, category: { fieldtype: "Link" }, category_detail: { fieldtype: "Link" },
		...Object.fromEntries([...originalFields, ...extraFields].map((field) => [field, { fieldtype: field === "inactive" ? "Check" : floatFields.includes(field) ? "Float" : ["notes", "legacy_remarks", "specification"].includes(field) ? "Small Text" : "Data",
			read_only: ["legacy_code", "major_category", "minor_category"].includes(field) || !saved && ["profile_image", "patent_image"].includes(field) }])) },
		item: Object.fromEntries(["item_code", "item_name", "item_group", "stock_uom"].map((field) => [field, { fieldtype: field === "item_name" ? "Data" : "Link", read_only: saved || field === "item_code" }])) };
}
function blank(extra = {}) {
	return { name: null, item_code: null, existing_item: null, profile_version: null, item_version: null, creation_mode: null,
		profile: { company: "Company A", item_code: null, category: null, category_detail: null, ...Object.fromEntries([...originalFields, ...extraFields].map((field) => [field, field === "inactive" ? 0 : null])) },
		item: { item_code: null, item_name: null, item_group: null, stock_uom: null }, field_meta: meta(), image_states: { profile_image: { state: "未上传", url: "" }, patent_image: { state: "未上传", url: "" } },
		can_write: true, can_create_item: true, can_link_item: true, ...extra };
}
function pack(extra = {}) {
	const base = blank(); return { ...base, name: "PROFILE-1", item_code: "ITEM-1", profile_version: "2026-10-01T00:00:02", item_version: "2026-10-01T00:00:01", creation_mode: "new_item",
		profile: { ...base.profile, item_code: "ITEM-1", display_name: " 档案名称 ", display_uom: " 档案单位 ", ...Object.fromEntries(floatFields.map((field) => [field, 0])) },
		item: { item_code: "ITEM-1", item_name: " 业务名称 ", item_group: "Leaf Group", stock_uom: "Nos" }, field_meta: meta(true), can_create_item: false, can_link_item: false, ...extra };
}
const category = { category: "CAT-1", category_detail: "DETAIL-1", category_code: "类别代码原文", major_name: "大类原文", minor_name: "小类原文", material: "不能自动带入的材质", owner_name: "不能自动带入的负责", region: "区域原文", abbreviation: "" };
const attachment = (extra = {}) => ({ name: "FILE-1", file_name: "图片.png", file_url: "/private/files/image.png", unique_url: "/private/files/image.png?fid=FILE-1", creation: "2026-10-01T00:00:03", ...extra });
function pageResult(args, rows, total = rows.length, changes = {}) {
	const pages = Math.ceil(total / args.page_size);
	return { company: args.company, search: args.search, include_inactive: args.include_inactive,
		requested_page: args.page, page: Math.min(args.page, Math.max(1, pages)), page_size: args.page_size,
		total, total_pages: pages, rows, ...changes };
}
async function opening(bench, archive = blank()) {
	const pending = bench.openEditor(archive.name); assert.equal(calls.at(-1).options.method, "jifeng_erp.product_archive." + (archive.name ? "read_archive" : "init_archive"));
	calls.at(-1).resolve(archive); const dialog = await pending;
	if (!archive.name) { assert.equal(calls.at(-1).options.method, "jifeng_erp.product_archive.list_category_choices"); calls.at(-1).resolve([category]); await tick(); }
	return dialog;
}
async function companyScopeEvents() {
	const bench = new window.JifengProductArchive(new Element()), initial = calls.length;
	assert.equal(bench.companyControl.get_value(), null); assert.equal(calls.length, initial, "mount does not choose a company or query");
	bench.companyControl.value = "Company A"; const first = bench.query(); await tick();
	assert.equal(calls.length, initial + 1); assert.equal(calls.at(-1).options.args.company, "Company A"); calls.at(-1).resolve([pack()]); await first;
	const input = (value) => { bench.companyControl.value = value; return bench.companyControl.$input.events.get("input")(); };
	const idleSetter = bench.companyControl.set_value.bind(bench.companyControl); let idleSetterCalls = 0;
	bench.companyControl.set_value = () => { idleSetterCalls++; throw new Error("idle company input must not call set_value"); };
	const count = calls.length, changed = input("Company B"); assert.equal(bench.rows.length, 0, "raw input clears old rows before native onchange");
	await changed; assert.equal(bench.companyScope, "Company B"); assert.equal(calls.length, count, "company input never automatically queries");
	assert.equal(idleSetterCalls, 0, "idle typing never resets or validates the native Link"); assert.equal(bench.companyControl.get_value(), "Company B"); bench.companyControl.set_value = idleSetter;
	const late = bench.query(), lateCall = calls.at(-1); bench.companyControl.value = "Company A";
	lateCall.resolve([pack({ profile: { ...pack().profile, company: "Company B" } })]); await late;
	assert.equal(bench.companyScope, "Company A"); assert.equal(bench.rows.length, 0, "silent changed company rejects a late query");
	const beforeOpen = calls.length; bench.companyControl.value = "Company B"; await bench.openEditor("PROFILE-1");
	assert.equal(calls.length, beforeOpen, "an old row cannot open after a silent company change"); assert.equal(bench.companyScope, "Company B");
	bench.companyControl.value = null; await bench.companyControl.$input.events.get("change")(); await bench.query(); assert.equal(calls.length, beforeOpen, "clearing company does not query");
	await bench.companyControl.set_value("Company A"); assert.equal(calls.length, beforeOpen, "native onchange does not query either");
	const openingRead = bench.openEditor(), openingCall = calls.at(-1), nativeSet = bench.companyControl.set_value.bind(bench.companyControl); let restoreCompany;
	bench.companyControl.set_value = (value) => new Promise((resolve) => { restoreCompany = async () => { await nativeSet(value); resolve(); }; });
	const reverting = input("Company B"); assert.equal(bench.changingCompany, true); assert.equal(bench.companyControl.get_value(), "Company B");
	openingCall.resolve(blank()); await openingRead;
	assert.equal(bench.editor.company, "Company A", "pending native company reversion preserves the original opening"); assert.equal(bench.companyScope, "Company A");
	await restoreCompany(); await reverting; bench.companyControl.set_value = nativeSet; assert.equal(bench.companyControl.get_value(), "Company A");
	calls.at(-1).resolve([]); await tick(); bench.closeEditor();
	const failedOpen = bench.openEditor(), failedCall = calls.at(-1); bench.companyControl.value = "Company B"; failedCall.reject(new Error("read failed")); await failedOpen;
	assert.equal(bench.companyControl.get_value(), "Company A"); assert.equal(bench.editor, null, "failed opening also restores a silently changed company");
	await opening(bench); const state = bench.editor; bench.closePicker(state); const reads = calls.length;
	for (const flag of ["busy", "unresolved"]) { state[flag] = true; const revision = bench.revision; await input("Company B");
		assert.equal(bench.editor, state); assert.equal(bench.revision, revision); assert.equal(bench.companyScope, "Company A"); assert.equal(bench.companyControl.get_value(), "Company A"); state[flag] = false; }
	state.controls.display_name.value = "dirty"; const decline = input("Company B"); await tick(); confirmation.no(); await decline;
	assert.equal(bench.editor, state); assert.equal(state.controls.display_name.value, "dirty"); assert.equal(bench.companyScope, "Company A");
	const accept = input("Company B"); await tick(); const duringConfirm = bench.query(); calls.at(-1).resolve([pack()]); await duringConfirm;
	assert.equal(bench.rows.length, 1); confirmation.yes(); await accept;
	assert.equal(bench.editor, null); assert.equal(bench.companyScope, "Company B"); assert.equal(bench.rows.length, 0, "accepted discard clears rows rebuilt during confirmation");
	assert.equal(calls.length, reads + 1, "scope acceptance adds no query beyond the explicit query during confirmation");
	bench.destroy();
}
function pageRow(index, extra = {}) {
	const name = "PAGED-" + String(index).padStart(4, "0"), source = pack();
	return pack({ name, item_code: name, profile: { ...source.profile, item_code: name }, item: { ...source.item, item_code: name }, ...extra });
}
async function paginationEvents() {
	const bench = new window.JifengProductArchive(new Element()); await bench.companyControl.set_value("Company A");
	assert.ok(bench.previous.disabled && bench.next.disabled); assert.equal(bench.pageSizeControl.value, "100");
	const second = bench.query(2), secondCall = calls.at(-1), lastRow = pageRow(100);
	assert.equal(secondCall.options.method, "jifeng_erp.product_archive.query_archives"); assert.equal(secondCall.options.args.page, 2);
	secondCall.resolve(pageResult(secondCall.options.args, [lastRow], 101)); await second;
	assert.equal(bench.page, 2); assert.equal(bench.total, 101); assert.equal(bench.totalPages, 2); assert.equal(bench.previous.disabled, false); assert.ok(bench.next.disabled);
	bench.scroller.scrollTop = 170; bench.scroller.scrollLeft = 61;
	await opening(bench, lastRow); const state = bench.editor, readsBeforeClose = calls.length; await state.dialog.hide();
	assert.equal(bench.page, 2); assert.equal(calls.length, readsBeforeClose, "closing the editor keeps the same page without requerying");
	assert.equal(bench.scroller.scrollTop, 170); assert.equal(bench.scroller.scrollLeft, 61);
	await opening(bench, lastRow); const editing = bench.editor; editing.controls.inactive.checked = true;
	const save = bench.save(editing); await tick(); const saveCall = calls.at(-1);
	saveCall.resolve(pageRow(100, { profile_version: "2026-10-01T00:00:09", profile: { ...lastRow.profile, inactive: 1 } })); await save;
	const refreshed = calls.at(-1); assert.equal(refreshed.options.method, "jifeng_erp.product_archive.query_archives");
	assert.equal(refreshed.options.args.page, 2, "save refresh requests the page that was being edited");
	const firstRows = Array.from({ length: 100 }, (_, index) => pageRow(index));
	refreshed.resolve(pageResult(refreshed.options.args, firstRows, 100)); await tick();
	assert.equal(bench.page, 1, "saving the last active row returns to the new last valid page"); assert.equal(bench.total, 100);
	assert.equal(bench.scroller.scrollTop, 170); assert.equal(bench.scroller.scrollLeft, 61); assert.ok(!editing.unresolved);
	bench.closeEditor();
	const older = bench.query(1), olderCall = calls.at(-1), newer = bench.query(2), newerCall = calls.at(-1);
	newerCall.resolve(pageResult(newerCall.options.args, [lastRow], 101)); await newer;
	olderCall.resolve(pageResult(olderCall.options.args, firstRows, 101)); await older;
	assert.equal(bench.page, 2); assert.equal(bench.rows[0].name, lastRow.name, "late first-page replies cannot replace a newer page");
	const duplicate = bench.query(1), duplicateCall = calls.at(-1);
	duplicateCall.resolve(pageResult(duplicateCall.options.args, [lastRow, lastRow], 2)); await duplicate;
	assert.equal(bench.page, 2); assert.equal(bench.total, 101, "malformed totals or duplicate source identities do not replace the last valid page");
	assert.match(bench.status.textContent, /查询失败/);
	for (const page of [true, 0, -1, "01", " 1", "1.2", "1e2", 2147483648]) {
		const count = calls.length; await bench.query(page); assert.equal(calls.length, count, "invalid page does not send a query");
	}
	bench.pageSizeControl.value = "25"; const resized = bench.pageSizeControl.events.get("change")(), resizedCall = calls.at(-1);
	assert.equal(resizedCall.options.args.page, 1); assert.equal(resizedCall.options.args.page_size, 25);
	assert.equal(bench.total, null, "page size invalidates old counts before querying");
	resizedCall.resolve(pageResult(resizedCall.options.args, firstRows.slice(0, 25), 101)); await resized;
	assert.equal(bench.page, 1); assert.equal(bench.totalPages, 5); assert.equal(bench.pageSize, 25);
	const late = bench.query(2), lateCall = calls.at(-1); bench.search.value = "new filter"; bench.search.events.get("input")();
	lateCall.resolve(pageResult(lateCall.options.args, firstRows.slice(25, 50), 101)); await late;
	assert.equal(bench.total, null); assert.equal(bench.rows.length, 0, "changed filter rejects both late rows and count");
	bench.search.value = ""; bench.search.events.get("input")();
	await opening(bench, lastRow); const accepted = bench.editor, originalApply = bench.applyArchive; let finishApply;
	const gate = new Promise((resolve) => { finishApply = resolve; });
	bench.applyArchive = async (...args) => { await gate; return originalApply.apply(bench, args); };
	const accepting = bench.accept(accepted, lastRow, "accepted"); bench.search.value = "later filter"; bench.search.events.get("input")();
	const countBeforeFinish = calls.length; finishApply(); await accepting;
	assert.equal(calls.length, countBeforeFinish, "save completion cannot start a stale refresh after newer filter input");
	bench.applyArchive = originalApply; bench.closeEditor(); bench.destroy();
}
async function main() {
	await companyScopeEvents();
	await paginationEvents();
	const openedRecipes = [], bench = new window.JifengProductArchive(new Element(), { open: (spec) => openedRecipes.push(spec) });
	bench.companyScope = "Company A"; await bench.companyControl.set_value("Company A");
	const query = bench.query(); assert.equal(calls.at(-1).options.args.include_inactive, 0); calls.at(-1).resolve([]); await query;
	bench.includeInactive.checked = true; const inactive = bench.query(); assert.equal(calls.at(-1).options.args.include_inactive, 1); calls.at(-1).resolve([]); await inactive;
	for (const label of ["导出", "导入", "打印", "批量传图"]) assert.ok(bench.host.descendants("button").find((node) => node.textContent === label).disabled);
	let dialog = await opening(bench), state = bench.editor;
	assert.equal(state.form.descendants("label").map((node) => node.textContent).join("/"), "编号/代码/大类/小类/旧号/品牌/材质/共版/专利号/名称/来源/设计/打版/建模/风格/负责/区域/单位/单价/毛胚价/重量/银重/石重/停用/复制/备注/注意事项/产品图片/专利图片");
	assert.equal(state.controls.legacy_remarks.tag, "input"); assert.equal(state.controls.legacy_remarks.type, "text"); assert.equal(state.controls.notes.tag, "textarea");
	assert.ok(state.controls.legacy_code.disabled); assert.ok(state.controls.major_category.disabled); assert.ok(state.controls.minor_category.disabled); assert.ok(state.controls.copy.disabled);
	assert.equal(state.controls.legacy_number.disabled, false); assert.equal(state.controls.weight.type, "text");
	assert.ok(state.images.profile_image.uploadButton.disabled); assert.ok(state.images.patent_image.uploadButton.disabled);
	assert.ok(state.resolveButton.hidden); assert.ok(state.images.profile_image.resolveButton.hidden); assert.equal(bench.isDirty(), false, "async native control initialization finishes before the draft baseline");
	assert.ok(dialog.fields_dict.__section_1); assert.equal(dialog.fields_dict.__section_1.get_value, undefined, "native section objects are present but never traversed as business controls");
	assert.equal(state.extraControls.supplier.df.options, "Supplier"); assert.equal(state.extraControls.specification.df.fieldtype, "Small Text");
	let picker = state.picker;
	assert.equal(picker.body.children[0].children.length, 7); assert.equal(picker.dialog.fields_dict.category_layout.$wrapper.element.descendants("th").map((node) => node.textContent).join("/"), "代码/小类/大类/材质/负责/区域/简拼");
	picker.body.children[0].events.get("click")(); assert.equal(state.selection.category, null, "single click selects a candidate without changing the archive");
	picker.body.children[0].events.get("dblclick")(); assert.equal(state.selection.category, "CAT-1"); assert.equal(state.selection.category_detail, "DETAIL-1");
	assert.equal(picker.closed, false, "double-click adoption preserves the old floating chooser habit"); assert.equal(state.controls.major_category.value, "大类原文");
	for (const field of ["legacy_number", "legacy_code", "old_number", "material", "display_uom", "owner_name", "region", ...floatFields]) assert.equal(state.controls[field].value, "", "category choice never guesses other archive values");
	bench.closePicker(state);
	state.controls.display_name.value = " 原文名称 "; state.controls.legacy_remarks.value = '<svg onload="alert(1)">原文</svg>';
	state.controls.notes.value = "注意\n事项"; state.controls.weight.value = "";
	await state.extraControls.supplier_product_code.set_value("供应商独立货号"); await state.extraControls.supplier.set_value("Supplier A"); await state.extraControls.specification.set_value("规格\n原文");
	await state.itemControls.item_name.set_value(" 业务名称 "); await state.itemControls.item_group.set_value("Leaf Group"); await state.itemControls.stock_uom.set_value("Nos");
	const payload = bench.payload(state); assert.equal(payload.profile.supplier_product_code, "供应商独立货号"); assert.equal(payload.item.item_name, " 业务名称 ");
	assert.equal("legacy_code" in payload.profile, false); assert.equal("major_category" in payload.profile, false); assert.equal("copy" in payload.profile, false); assert.equal(payload.profile.category, "CAT-1");
	dialog.hide(); await tick(); confirmation.no(); await tick(); assert.equal(dialog.hidden, false); assert.ok(bench.isDirty());
	const saving = bench.save(state); await tick(); const saveCall = calls.at(-1); assert.equal(saveCall.options.method, "jifeng_erp.product_archive.save_archive");
	assert.match(saveCall.options.args.payload.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/); assert.ok(state.saveButton.disabled);
	saveCall.reject(new Error("transport lost")); await saving; assert.ok(state.unresolved); assert.equal(state.resolveButton.hidden, false);
	const heldCount = calls.length; await bench.save(state); await bench.newDraft(state); dialog.hide(); await tick(); assert.equal(calls.length, heldCount); assert.equal(dialog.hidden, false);
	const missing = bench.resolve(state); assert.equal(calls.at(-1).options.method, "jifeng_erp.product_archive.resolve_creation"); calls.at(-1).resolve({ status: "not_found" }); await missing; assert.ok(state.unresolved);
	const partial = bench.resolve(state); calls.at(-1).resolve({ status: "found", archive: { name: "PROFILE-1" } }); await partial; assert.ok(state.unresolved);
	const expectedProfile = { ...pack().profile, ...saveCall.options.args.payload.profile, item_code: "ITEM-1", major_category: "大类原文", minor_category: "小类原文", ...Object.fromEntries(floatFields.map((field) => [field, 0])) };
	const exact = bench.resolve(state); calls.at(-1).resolve({ status: "found", archive: pack({ profile: expectedProfile }) }); await exact;
	assert.equal(state.unresolved, false); assert.equal(state.archive.name, "PROFILE-1"); assert.equal(bench.isDirty(), false); assert.equal(state.controls.weight.value, "0", "native empty numeric persistence as zero is a normal saved result");
	assert.ok(state.business.hidden); assert.equal(state.recipeButton.hidden, false); assert.equal(state.images.profile_image.uploadButton.disabled, false);
	assert.equal(state.resolveButton.hidden, true); assert.equal(dialog.hidden, false, "save continues in the same popup");
	// Complete the optional background list refresh started by accept without changing its query state.
	assert.equal(calls.at(-1).options.method, "jifeng_erp.product_archive.query_archives"); calls.at(-1).resolve([pack({ profile: expectedProfile })]); await tick();
	assert.equal(bench.includeInactive.checked, true);

	state.controls.display_name.value = " 本次改名 "; const failed = bench.save(state); await tick(); calls.at(-1).reject({ responseJSON: { exc_type: "TimestampMismatchError" } }); await failed;
	assert.equal(state.unresolved, false); assert.equal(state.controls.display_name.value, " 本次改名 ");
	const unknown = bench.save(state); await tick(); calls.at(-1).reject({ responseJSON: { exc_type: "RuntimeError" } }); await unknown; assert.ok(state.unresolved);
	const conflict = bench.resolve(state); calls.at(-1).resolve(pack({ profile_version: "2026-10-01T00:00:04", profile: { ...expectedProfile, display_name: " 另一人改名 " } })); await conflict;
	assert.match(state.review.textContent, /本次改名/); assert.match(state.review.textContent, /另一人改名/); assert.ok(state.adoptButton.hidden === false);
	const posts = calls.filter((call) => call.options.method.endsWith("save_archive")).length;
	bench.acceptCurrent(state); await confirmation.yes(); assert.equal(state.controls.display_name.value, " 另一人改名 "); assert.equal(bench.isDirty(), false);
	assert.equal(calls.filter((call) => call.options.method.endsWith("save_archive")).length, posts); calls.at(-1).resolve([]); await tick();

	// Bootstrap X hides the uploader directly, bypassing the overridden hide method.
	const cancelling = bench.uploadImage(state, "profile_image"); calls.at(-1).resolve([attachment({ name: "OLDER" })]); await cancelling;
	const cancelledUploader = uploads.at(-1); cancelledUploader.uploader.files = [];
	const imagesBeforeCancel = JSON.stringify(state.imageValues);
	cancelledUploader.dialog.hidden = true; cancelledUploader.dialog.on_hide();
	assert.equal(cancelledUploader.nativeHidden, 1, "native hidden callback still runs");
	assert.equal(cancelledUploader.nativeWrites, 0); assert.equal(bench.getCloseBlock(), "");
	assert.equal(state.uploadUnresolved, false); assert.equal(state.saveButton.disabled, false);
	assert.equal(JSON.stringify(state.imageValues), imagesBeforeCancel, "cancel without upload preserves both image values");

	// Native upload promise resolves early: only exact File callback or explicit read-only adoption unlocks it.
	const uploading = bench.uploadImage(state, "profile_image"); assert.equal(calls.at(-1).options.method, "jifeng_erp.product_archive.list_image_attachments");
	calls.at(-1).resolve([attachment({ name: "OLDER" })]); await uploading; const uploader = uploads.at(-1);
	assert.equal(uploader.options.make_attachments_public, 0); assert.equal(uploader.options.allow_toggle_private, false); assert.equal(uploader.options.disable_file_browser, true); assert.equal(uploader.options.allow_web_link, false);
	assert.equal(uploader.options.docname, "PROFILE-1"); assert.equal(uploader.options.fieldname, "profile_image"); assert.equal(uploader.options.doctype, "Jifeng Product Profile");
	await uploader.upload_files(); assert.match(bench.getCloseBlock(), /正在读取、保存或上传/); assert.equal(state.imageValues.profile_image, null, "early resolution is not an upload result");
	uploader.dialog.hidden = true; uploader.dialog.on_hide(); assert.equal(state.uploadUnresolved, true); assert.equal(state.images.profile_image.resolveButton.hidden, false);
	const unknownUploadCount = uploader.nativeWrites; await uploader.upload_files(); assert.equal(uploader.nativeWrites, unknownUploadCount, "native upload cannot be retried after any POST attempt");
	const none = bench.resolveUpload(state); calls.at(-1).resolve([attachment({ name: "OLDER" })]); await none; assert.ok(state.uploadUnresolved);
	const candidates = bench.resolveUpload(state); calls.at(-1).resolve([attachment({ name: "OLDER" }), attachment()]); await candidates;
	assert.equal(state.upload.candidates.length, 1); const adoptImage = state.images.profile_image.candidates.descendants("button")[0]; adoptImage.events.get("click")(); confirmation.no(); assert.ok(state.uploadUnresolved);
	adoptImage.events.get("click")(); confirmation.yes(); assert.equal(state.uploadUnresolved, false); assert.equal(state.imageValues.profile_image, "/private/files/image.png");
	assert.equal(bench.payload(state).profile.profile_image, "/private/files/image.png"); assert.ok(!bench.payload(state).profile.profile_image.includes("fid="), "display unique_url never becomes the stored source URL");
	assert.equal(state.images.profile_image.preview.descendants("img").length, 0, "new image preview waits for the server-authorized saved field");
	const imageSave = bench.save(state); await tick(); calls.at(-1).resolve(pack({ profile_version: "2026-10-01T00:00:05", profile: { ...expectedProfile, display_name: " 另一人改名 ", profile_image: "/private/files/image.png" },
		image_states: { profile_image: { state: "已上传", url: "/private/files/image.png?fid=FILE-1" }, patent_image: { state: "未上传", url: "" } } })); await imageSave;
	assert.equal(state.unresolved, false); assert.equal(state.images.profile_image.preview.descendants("img")[0].src, "/private/files/image.png?fid=FILE-1"); calls.at(-1).resolve([]); await tick();
	const patent = bench.uploadImage(state, "patent_image"); calls.at(-1).resolve([]); await patent; const patentUploader = uploads.at(-1); await patentUploader.upload_files();
	patentUploader.options.on_success({ name: "WRONG", doctype: "File", is_private: 1, attached_to_doctype: "Jifeng Product Profile", attached_to_name: "PROFILE-1", attached_to_field: "profile_image", file_url: "/private/files/wrong.png" });
	assert.equal(state.imageValues.patent_image, null, "other field's exact binding cannot authorize the patent image");
	patentUploader.options.on_success({ name: "PATENT", doctype: "File", is_private: 1, attached_to_doctype: "Jifeng Product Profile", attached_to_name: "PROFILE-1", attached_to_field: "patent_image", file_url: "/private/files/patent.png" });
	patentUploader.dialog.hide(); assert.equal(state.uploadUnresolved, false); assert.equal(state.imageValues.patent_image, "/private/files/patent.png");
	assert.equal(state.images.patent_image.resolveButton.hidden, true);
	const recipe = bench.openRecipe(state); await tick(); confirmation.no(); await recipe; assert.equal(openedRecipes.length, 0, "native recipe navigation guards the pending image change");
	const recipeYes = bench.openRecipe(state); await tick(); confirmation.yes(); await recipeYes;
	assert.equal(openedRecipes[0].route.join("/"), "Form/Jifeng Product Profile/PROFILE-1"); assert.equal(dialog.hidden, true); assert.equal(bench.editor, null);

	// A multiline historic remark loses newlines in its single-line display only; no untouched value is posted.
	dialog = await opening(bench, pack({ profile: { ...pack().profile, legacy_remarks: "原\n备注" } })); state = bench.editor;
	assert.equal(state.controls.legacy_remarks.value, "原备注"); assert.equal("legacy_remarks" in bench.payload(state).profile, false); assert.equal(bench.isDirty(), false);
	state.controls.display_name.value = "修改名称"; assert.equal("legacy_remarks" in bench.payload(state).profile, false); assert.equal("company" in bench.payload(state).profile, false); assert.equal(Object.keys(bench.payload(state).item).length, 0);
	const close = dialog.hide(); await tick(); confirmation.yes(); await close; assert.equal(dialog.hidden, true);
	// Explicit source association reads the actual candidate version and never edits the shared Item.
	dialog = await opening(bench); state = bench.editor; bench.closePicker(state);
	state.controls.display_name.value = "原文仍保留"; await state.extraControls.supplier_product_code.set_value("独立货号2");
	await state.itemControls.item_name.set_value("先前输入的新商品名称"); await state.itemControls.item_group.set_value("Leaf Group"); await state.itemControls.stock_uom.set_value("Nos");
	state.modeControl.value = "existing_item"; await bench.changeSource(state);
	state.existingControl.value = "EXISTING-ITEM"; const source = bench.selectExisting(state); await tick();
	assert.equal(calls.at(-1).options.method, "jifeng_erp.product_archive.init_archive"); assert.equal(calls.at(-1).options.args.existing_item, "EXISTING-ITEM");
	const linkedMeta = meta(); for (const df of Object.values(linkedMeta.item)) df.read_only = true;
	const linked = blank({ existing_item: "EXISTING-ITEM", item_code: "EXISTING-ITEM", item_version: "2026-10-01T00:00:07",
		profile: { ...blank().profile, item_code: "EXISTING-ITEM" }, item: { item_code: "EXISTING-ITEM", item_name: "已有关联名", item_group: "Existing Group", stock_uom: "g" }, field_meta: linkedMeta });
	calls.at(-1).resolve(linked); await source;
	assert.equal(state.archive.existing_item, "EXISTING-ITEM"); assert.equal(state.controls.display_name.value, "原文仍保留"); assert.equal(state.extraControls.supplier_product_code.get_value(), "独立货号2");
	assert.equal(Object.keys(bench.payload(state).item).length, 0); assert.equal(bench.payload(state).existing_item, "EXISTING-ITEM"); assert.equal(bench.payload(state).item_version, "2026-10-01T00:00:07");
	assert.ok(state.itemControls.item_name.df.read_only); assert.ok(bench.isDirty(), "choosing an unsaved source is a meaningful draft change");
	state.modeControl.value = "new_item"; const restoreNew = bench.changeSource(state); calls.at(-1).resolve(blank()); await restoreNew;
	assert.equal(state.itemControls.item_name.get_value(), "先前输入的新商品名称", "switching source back preserves the user's explicit new Item input");
	assert.equal(state.controls.display_name.value, "原文仍保留");
	// Company switching requires explicit discard and clears category/source identities for the new scope.
	state.companyControl.value = "Company B"; const cancelCompany = bench.changeEditorCompany(state); await tick(); confirmation.no(); await cancelCompany;
	assert.equal(state.company, "Company A"); assert.equal(state.companyControl.get_value(), "Company A"); assert.equal(state.controls.display_name.value, "原文仍保留");
	state.companyControl.value = "Company B"; const switchCompany = bench.changeEditorCompany(state); await tick(); confirmation.yes(); await tick();
	calls.at(-1).resolve(blank({ profile: { ...blank().profile, company: "Company B" } })); await switchCompany;
	assert.equal(state.company, "Company B"); assert.equal(state.controls.display_name.value, ""); assert.equal(state.archive.existing_item, null); assert.equal(state.selection.category, null);
	calls.at(-1).resolve([]); await tick(); bench.closePicker(state); await dialog.hide(); assert.equal(dialog.hidden, true);
	// A read-only permission refresh omits protected values; the prior DTO is not merged into the current editor.
	const hiddenMeta = meta(true); delete hiddenMeta.profile.legacy_remarks; delete hiddenMeta.profile.supplier_product_code;
	const hiddenProfile = { ...pack().profile }; delete hiddenProfile.legacy_remarks; delete hiddenProfile.supplier_product_code;
	dialog = await opening(bench, pack({ can_write: false, field_meta: hiddenMeta, profile: hiddenProfile })); state = bench.editor;
	assert.ok(state.cells.legacy_remarks.hidden); assert.ok(state.extraControls.supplier_product_code.df.hidden); assert.equal("legacy_remarks" in bench.payload(state).profile, false);
	assert.ok(state.saveButton.disabled); assert.equal(state.controls.legacy_remarks.value, ""); await dialog.hide();

	// Classic visible routes and native recipe route remain separate.
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_workspace_core.js"), "utf8"), context);
	window.Workspace = window.JifengERPWorkspace.Workspace;
	const workspace = Object.create(window.Workspace.prototype), opened = []; workspace.formalBridge = { open: (spec) => opened.push(spec), openCreate: (spec) => opened.push(spec) };
	workspace.addTab = () => { throw new Error("old product entry fell back to a preview"); };
	workspace.openPage("产品档案", "产品"); assert.equal(opened.at(-1).productArchive, true);
	workspace.openPage("资料录入", "产品"); assert.equal(opened.at(-1).productArchive, true);
	workspace.openPage("商品工艺档案", "产品"); assert.equal(opened.at(-1).productArchive, false);
	const createBridge = Object.create(window.JifengClassicBridge.prototype); createBridge.frames = new Map();
	let readyResolve, editorOpens = 0, createdSpec; const createReady = new Promise((resolve) => { readyResolve = resolve; });
	createBridge.open = (spec) => { createdSpec = spec; createBridge.frames.set("formal:产品:产品档案", { ready: createReady, workbench: { openEditor: () => { editorOpens++; return "PRODUCT-DIALOG"; } } }); };
	const created = createBridge.openCreate(opened[1]); assert.equal(editorOpens, 0, "资料录入 waits for the mounted product workbench");
	assert.equal(createdSpec.productArchive, true); assert.equal(createdSpec.kind, "list"); readyResolve();
	assert.equal(await created, "PRODUCT-DIALOG"); assert.equal(editorOpens, 1, "资料录入 opens one product draft after readiness");
	const bridge = Object.create(window.JifengClassicBridge.prototype), mounted = bridge.mount(new Element(), opened[0]); assert.ok(mounted.workbench instanceof window.JifengProductArchive); assert.equal(mounted.iframe, undefined);
	await mounted.ready; bridge.frames = new Map([["PRODUCT", mounted]]); mounted.stop(); assert.equal(mounted.workbench.disposed, true);
	assert.equal(listeners.has(mounted.workbench.beforeUnload), false); bench.destroy(); assert.equal(listeners.has(bench.beforeUnload), false);
	console.log("Product archive: 29 original controls, seven-column choices, explicit Item provenance, changed-only remarks/supplier values, saved private image sources, early-upload/unknown recovery guards and real classic routes passed (DOM/protocol models only).");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
