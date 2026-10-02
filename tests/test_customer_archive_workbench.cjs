// Scoped reads, explicit native sources and unknown-write recovery; no browser/DB/network.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const calls = [], confirms = [], messages = [];
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this.value = ""; this._text = ""; this.scrollTop = this.scrollLeft = 0; this.classList = { add() {} }; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((node) => node.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("Business HTML must never execute"); }
	append(...nodes) { this.children.push(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, action) { this.events.set(name, action); }
	descendants(tag) { return this.children.flatMap((node) => [...(node.tag === tag ? [node] : []), ...node.descendants(tag)]); }
}
class Wrapper { constructor(element = new Element()) { this.element = element; } get() { return this.element; } addClass() { return this; } modal() { return this; } on() { return this; } }
class Dialog {
	constructor(config) { this.config = config; this.$wrapper = new Wrapper(); this.hidden = true; this.fields_dict = Object.fromEntries(config.fields.map((field) => [field.fieldname, { $wrapper: new Wrapper() }])); }
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
const frappe = { ui: { Dialog, form: { make_control({ df }) {
	return { df, value: null, refresh() {}, get_value() { return this.value; }, async set_value(value) {
		this.value = df.fieldtype === "Data" && typeof value === "string" ? value.replace(/[\r\n]/g, "") : value;
		await df.onchange?.();
	} };
} } }, call(options) { let resolve, reject; const pending = new Promise((yes, no) => { resolve = yes; reject = no; }); calls.push({ options, resolve: (message) => resolve({ message }), reject }); return pending; },
	confirm(message, yes, no) { confirms.push({ message, yes, no }); }, msgprint(message) { messages.push(message); } };
const window = { addEventListener() {}, removeEventListener() {} }, document = { createElement: (tag) => new Element(tag) };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_customer_archive.js"), "utf8"),
	{ window, document, frappe, structuredClone, crypto: { randomUUID: () => "11111111-1111-4111-8111-111111111111" }, __: (value) => value });
const VERSION = "2026-10-03T10:00:00.123456", AFTER = "2026-10-03T10:01:00.123456";
const commonFields = ["legacy_number", "legacy_code", "category", "grade", "display_name", "region", "province", "district", "salesperson", "contact_name",
	"tax_number", "company_text", "mobile", "legal_person", "address", "notes", "inactive", "company", "archive_kind", "customer"];
function archive({ name = "PROFILE-A", company = "Company A", kind = "销售客户", writable = true, create = true, link = true, dictionaries = true, version = VERSION } = {}) {
	const fields = [...commonFields, ...(kind === "销售客户" ? ["customer_kind", "discount"] : [])];
	const profile = Object.fromEntries(fields.map((field) => [field, ""]));
	Object.assign(profile, { company, archive_kind: kind, customer: name ? "CUSTOMER-A" : null, legacy_number: "001", legacy_code: " 0007 ",
		display_name: name ? '<img src=x onerror="alert(1)">合成客户' : "", notes: name ? "  原文\n备注  " : "", address: name ? "原文\n地址" : "", inactive: 0, company_text: "旧公司原文" });
	if (kind === "销售客户") profile.discount = " 9折 原文 ";
	const selected = name && dictionaries ? { category: "DICT-C", grade: "DICT-G", ...(kind === "销售客户" ? { customer_kind: "DICT-T" } : {}) } : {};
	Object.assign(profile, selected);
	return { name, kind, company, profile, profile_version: name ? version : null, customer: name ? "CUSTOMER-A" : null,
		customer_version: name ? VERSION : null, customer_disabled: name ? false : null, can_write: writable,
		owner: name ? "synthetic-creator@example.invalid" : null, creation: name ? VERSION : null, modified_by: name ? "synthetic-editor@example.invalid" : null,
		numbering_mode: "automatic_v1", number_reference_only: !name, can_create_customer: writable && create, can_link_customer: writable && link,
		dictionary_versions: Object.fromEntries(Object.keys(selected).map((field) => [field, VERSION])),
		dictionary_values: Object.fromEntries(Object.keys(selected).map((field) => [field, "词典原文 " + field])),
		customer_defaults: name ? {} : { customer_group: "Commercial", territory: "Synthetic Territory", customer_type: "Company", naming_series: "CUST-.YYYY.-" },
		customer_field_meta: name ? {} : {
			customer_group: { fieldtype: "Link", options: "Customer Group", reqd: false, read_only: !writable || !create },
			territory: { fieldtype: "Link", options: "Territory", reqd: false, read_only: !writable || !create },
			customer_type: { fieldtype: "Select", options: "Company\nIndividual\nPartnership", reqd: true, read_only: !writable || !create },
			naming_series: { fieldtype: "Select", options: "CUST-.YYYY.-", reqd: false, read_only: !writable || !create }
		}, field_meta: Object.fromEntries(fields.map((field) => [field, { fieldtype: field === "inactive" ? "Check" : ["category", "grade", "customer_kind", "customer", "company"].includes(field) ? "Link" : ["address", "notes"].includes(field) ? "Small Text" : "Data",
			options: ["category", "grade", "customer_kind"].includes(field) ? "Jifeng Customer Type" : field === "customer" ? "Customer" : field === "company" ? "Company" : "",
			reqd: ["display_name", "legacy_number", "company", "customer"].includes(field), read_only: !writable || ["legacy_number", "company", "archive_kind"].includes(field) || field === "customer" && !!name }])) };
}
const copy = (value) => JSON.parse(JSON.stringify(value));
function page(call, rows, total = rows.length) { return { ...call.options.args, rows, total, total_pages: Math.ceil(total / call.options.args.page_size), readonly: true }; }
async function tick() { await new Promise(setImmediate); }
async function bench(kind = "销售客户", capability = {}) {
	const value = new window.JifengCustomerArchive(new Element(), kind); await value.ready;
	const pending = value.companyControl.set_value("Company A");
	assert.match(calls.at(-1).options.method, /\.init_archive$/); calls.at(-1).resolve(archive({ name: null, kind, ...capability })); await pending;
	return value;
}
async function list(value, row = archive({ kind: value.kind, writable: false })) { const pending = value.query(), call = calls.at(-1); call.resolve(page(call, [row])); await pending; }
async function existing(value) { await list(value); const pending = value.openExisting("PROFILE-A"); calls.at(-1).resolve(archive({ kind: value.kind })); await pending; return value.editor; }
async function create(value, options = {}) { const pending = value.openNew(); calls.at(-1).resolve(archive({ name: null, kind: value.kind, ...options })); await pending; return value.editor; }
function saved(state, version = AFTER) {
	const row = archive({ kind: state.archive.kind, dictionaries: false, version }); Object.assign(row.profile, state.candidate.profile); row.profile.customer ||= "CUSTOMER-A";
	row.customer = row.profile.customer; row.dictionary_versions = copy(state.candidate.dictionary_versions);
	row.dictionary_values = Object.fromEntries(Object.keys(row.dictionary_versions).map((field) => [field, "已保存词典原文"])); return row;
}

async function observedFieldsAndSafeRows(kind) {
	const value = await bench(kind), sales = kind === "销售客户";
	assert.equal(value.host.descendants("th").map((node) => node.textContent).join("/"), sales ?
		"编号/分类/级别/类型/代码/名称/导购/折扣/联系/停用/操作/日期/修改人/修改日期/修改/删除" :
		"编号/区域/省份/地区/代码/名称/导购/联系/手机/停用/操作/日期/修改人/修改日期/修改/删除");
	await list(value); assert.equal(value.tbody.children[0].children.length, 16);
	assert.match(value.tbody.textContent, /<img src=x onerror=/); if (sales) { assert.match(value.tbody.textContent, /词典原文 category/); assert.doesNotMatch(value.tbody.textContent, /DICT-C/); }
	const state = await existing(value);
	assert.equal(value.fields.map(([, label]) => label).join("/"), sales ?
		"编号/代码/分类/级别/类型/名称/折扣/区域/省份/地区/导购/联系/税号/公司/手机/法人/地址/备注/停用" :
		"编号/代码/分类/级别/名称/区域/省份/地区/导购/联系/税号/公司/手机/法人/地址/备注/停用");
	assert.equal(value.fields.length, sales ? 19 : 17); assert.equal(state.controls.legacy_number.df.read_only, true);
	assert.equal(value.companyControl.df.label, "当前公司"); assert.equal(state.controls.company_text.df.label, "公司");
	assert.equal(state.customerControl.df.read_only, true); assert.equal(value.isDirty(), false);
	for (const [field, type] of Object.entries(value.dictionaryTypes)) assert.deepEqual(copy(state.controls[field].get_query()), { filters: { company: "Company A", dictionary_type: type, inactive: 0 } });
	const draft = value.payload(state); assert.equal("legacy_number" in draft.profile, false); assert.equal("customer_create" in draft, false);
	await state.controls.inactive.set_value(1); const changed = value.payload(state);
	assert.deepEqual(copy(changed.profile), { inactive: 1 }, "untouched single-line controls must not rewrite historical newlines");
	assert.equal(changed.customer_version, VERSION); assert.equal(changed.profile_version, VERSION);
	const closing = state.dialog.hide(); assert.equal(state.confirming, true); confirms.at(-1).no(); await closing; assert.equal(state.closed, false);
	const closeAgain = state.dialog.hide(); confirms.at(-1).yes(); await closeAgain; assert.equal(state.closed, true); assert.equal(value.statusSelect.disabled, false);
	const postCount = calls.filter((call) => call.options.type === "POST").length;
	for (const button of value.host.descendants("button").filter((node) => ["删除", "更新简拼", "导入", "打印"].includes(node.textContent))) assert.equal(button.disabled, true);
	assert.equal(calls.filter((call) => call.options.type === "POST").length, postCount); value.destroy();
}

async function capabilitiesAndExplicitSource() {
	const reader = await bench("销售客户", { writable: false }); assert.equal(reader.newButton.disabled, true); const before = calls.length;
	await reader.openNew(); assert.equal(calls.length, before); await list(reader); const opened = reader.openExisting("PROFILE-A");
	calls.at(-1).resolve(archive({ writable: false })); await opened;
	assert.equal(reader.editor.saveButton.disabled, true); assert.equal(reader.editor.controls.display_name.df.read_only, true); reader.destroy();
	const value = await bench("订单客户", { create: false }), state = await create(value, { create: false });
	assert.equal(state.sourceMode, "existing"); assert.equal(state.saveButton.disabled, true);
	await state.controls.display_name.set_value("明确关联的合成档案"); const source = state.customerControl.set_value("CUSTOMER-EXPLICIT"), call = calls.at(-1);
	assert.match(call.options.method, /\.read_customer$/); assert.equal(call.options.args.company, "Company A");
	call.resolve({ name: "CUSTOMER-EXPLICIT", kind: "订单客户", company: "Company A", version: VERSION, disabled: false }); await source;
	assert.equal(state.saveButton.disabled, false); const payload = value.payload(state);
	assert.equal(payload.profile.customer, "CUSTOMER-EXPLICIT"); assert.equal(payload.customer_version, VERSION);
	assert.equal("customer_create" in payload, false); assert.deepEqual(copy(payload.dictionary_versions), {});
	assert.equal("customer_kind" in payload.profile, false); assert.equal("discount" in payload.profile, false);
	value.destroy();
}

async function scopeAndDictionaryRaces() {
	const value = await bench(); const first = value.query(), firstCall = calls.at(-1);
	value.search.value = "new"; value.search.events.get("input")(); firstCall.resolve(page(firstCall, [archive({ writable: false })])); await first;
	assert.equal(value.total, null);
	value.companyControl.value = "Company B"; const pending = value.query(), permission = calls.at(-2), query = calls.at(-1);
	assert.equal(query.options.args.company, "Company B");
	query.resolve(page(query, [archive({ company: "Company B", writable: false })])); await pending;
	value.companyControl.value = "Company C"; permission.resolve(archive({ name: null, company: "Company B" })); await tick();
	assert.equal(value.readyArchive, null); assert.equal(value.newButton.disabled, true);
	const back = value.companyControl.set_value("Company A"); calls.at(-1).resolve(archive({ name: null })); await back;
	const state = await create(value); await state.controls.display_name.set_value("合成客户");
	for (const field of ["customer_group", "territory"]) assert.deepEqual(copy(state.customerControls[field].get_query()), { filters: { is_group: 0 } });
	const old = state.controls.category.set_value("DICT-OLD"), oldCall = calls.at(-1);
	const latest = state.controls.category.set_value("DICT-NEW"), newCall = calls.at(-1);
	assert.equal(state.saveButton.disabled, true);
	newCall.resolve({ name: "DICT-NEW", company: "Company A", dictionary_type: "客户分类", version: AFTER, value: "新分类", inactive: 0 }); await latest;
	oldCall.resolve({ name: "DICT-OLD", company: "Company A", dictionary_type: "客户分类", version: VERSION, value: "旧分类", inactive: 0 }); await old;
	assert.equal(state.dictionaryVersions.category, AFTER); assert.equal(state.controls.category.get_value(), "DICT-NEW");
	assert.deepEqual(copy(value.payload(state).dictionary_versions), { category: AFTER });
	const mismatch = state.controls.grade.set_value("DICT-WRONG"); calls.at(-1).resolve({ name: "DICT-WRONG", company: "Company A", dictionary_type: "订单客户级别", version: VERSION, value: "错误模块", inactive: 0 }); await mismatch;
	assert.equal(state.saveButton.disabled, true); assert.equal("grade" in state.dictionaryVersions, false);
	await state.controls.grade.set_value(""); assert.equal("grade" in value.payload(state).dictionary_versions, false);
	const before = value.companyScope; await value.companyControl.set_value("Company B"); assert.equal(value.companyScope, before); assert.equal(value.companyControl.get_value(), before);
	value.destroy();
}

async function sourceResponseRaceAndScopeRejection() {
	const value = await bench(), state = await create(value); await state.modeControl.set_value("关联已有客户");
	const first = state.customerControl.set_value("CUSTOMER-OLD"), oldCall = calls.at(-1);
	const latest = state.customerControl.set_value("CUSTOMER-NEW"), newCall = calls.at(-1);
	newCall.resolve({ name: "CUSTOMER-NEW", company: "Company A", kind: "销售客户", version: AFTER, disabled: false }); await latest;
	oldCall.resolve({ name: "CUSTOMER-OLD", company: "Company A", kind: "销售客户", version: VERSION, disabled: false }); await first;
	assert.equal(state.customerVersion, AFTER); await state.modeControl.set_value("新建业务客户"); assert.equal(state.customerVersion, null);
	value.destroy(); const order = await bench("订单客户"), wrong = order.query(), call = calls.at(-1);
	call.resolve(page(call, [archive({ kind: "销售客户", writable: false })])); await wrong; assert.equal(order.rows.length, 0);
	const wrongTitle = order.query(), titleCall = calls.at(-1), row = archive({ kind: "订单客户", writable: false }); delete row.dictionary_values.grade;
	titleCall.resolve(page(titleCall, [row])); await wrongTitle; assert.equal(order.total, null); order.destroy();
}

async function unknownCreationRecovery() {
	const value = await bench(), state = await create(value); await state.controls.display_name.set_value("合成新客户");
	await state.controls.company_text.set_value(" 旧公司原文 "); await state.controls.discount.set_value(" 原文折扣 ");
	const saving = value.save(state), post = calls.at(-1); assert.equal(post.options.type, "POST");
	const payload = copy(post.options.args.payload); assert.equal("legacy_number" in payload.profile, false); assert.equal("numbering_mode" in payload, false);
	assert.deepEqual(Object.keys(payload.customer_create).sort(), ["customer_group", "customer_type", "naming_series", "territory"]);
	post.reject(new Error("transport result unknown")); await saving; assert.equal(state.unknown, true); assert.equal(state.controls.display_name.df.read_only, true);
	const writes = calls.filter((call) => call.options.type === "POST").length; await value.save(state); assert.equal(calls.filter((call) => call.options.type === "POST").length, writes);
	await state.dialog.hide(); assert.equal(state.closed, false); assert.match(value.getCloseBlock(), /尚未确认/);
	const absent = value.resolve(state); calls.at(-1).resolve({ status: "not_found" }); await absent; assert.equal(state.unknown, true);
	const found = value.resolve(state), receipt = calls.at(-1); assert.equal(receipt.options.type, "GET"); assert.equal(receipt.options.args.request_id, payload.request_id);
	receipt.resolve({ status: "found", archive: saved(state) }); await found;
	assert.equal(state.unknown, false); assert.equal(state.archive.name, "PROFILE-A"); assert.equal(state.requestId, null); assert.equal(value.isDirty(), false);
	assert.equal(calls.filter((call) => call.options.type === "POST").length, writes); value.destroy();
}

async function creationReceiptAndEditedProfile() {
	const value = await bench(), state = await create(value); await state.controls.display_name.set_value("已保存合成客户");
	const saving = value.save(state), post = calls.at(-1); post.resolve(saved(state)); await tick();
	const receipt = calls.at(-1); assert.match(receipt.options.method, /\.resolve_creation$/); const row = saved(state);
	const wrong = copy(row); wrong.customer = wrong.profile.customer = "CUSTOMER-OTHER"; receipt.resolve({ status: "found", archive: wrong }); await saving;
	assert.equal(state.unknown, true); const recovered = value.resolve(state); calls.at(-1).resolve({ status: "found", archive: row }); await recovered;
	assert.equal(state.unknown, false); await state.controls.notes.set_value("修改后的备注");
	const editing = value.save(state), edit = calls.at(-1); assert.deepEqual(copy(edit.options.args.payload.profile), { notes: "修改后的备注" });
	assert.equal("customer_create" in edit.options.args.payload, false); assert.equal("customer" in edit.options.args.payload.profile, false);
	const changed = saved(state, "2026-10-03T10:02:00.123456"); edit.resolve(changed); await editing;
	assert.equal(state.archive.profile.notes, "修改后的备注"); assert.equal(state.unknown, false); value.destroy();
}

async function completePaginationAndClamp() {
	const value = await bench("订单客户"); value.scroller.scrollTop = 60; value.scroller.scrollLeft = 220;
	const oldLastPage = value.query(2), oldLast = calls.at(-1), oldRow = archive({ name: "OLD-LAST-ROW", kind: "订单客户", writable: false });
	oldLast.resolve(page(oldLast, [oldRow], 101)); await oldLastPage; assert.equal(value.page, 2);
	// The last row was stopped server-side: the same requested second page must return page 1.
	const shrunk = value.query(2), shrinkCall = calls.at(-1), remaining = Array.from({ length: 100 }, (_, index) => {
		const item = archive({ name: "REMAINING-" + index, kind: "订单客户", writable: false });
		item.customer = item.profile.customer = "SYNTHETIC-CUSTOMER-" + index; return item;
	});
	shrinkCall.resolve({ ...page(shrinkCall, remaining, 100), page: 1 }); await shrunk;
	assert.equal(value.total, 100); assert.equal(value.page, 1); assert.equal(value.goto.value, "1"); assert.equal(value.rows.length, 100);
	assert.equal(value.previous.disabled, true); assert.equal(value.next.disabled, true);
	const pending = value.query(200), call = calls.at(-1), row = archive({ kind: "订单客户", writable: false });
	call.resolve({ ...page(call, [row], 1501), page: 16 }); await pending;
	assert.equal(value.total, 1501); assert.equal(value.totalPages, 16); assert.equal(value.page, 16); assert.equal(value.goto.value, "16");
	assert.equal(value.scroller.scrollTop, 60); assert.equal(value.scroller.scrollLeft, 220);
	const incomplete = value.query(16), partial = calls.at(-1); partial.resolve({ ...page(partial, [], 1501), page: 16 }); await incomplete;
	assert.equal(value.total, null, "complete page length cannot silently truncate");
	const empty = value.query(4), final = calls.at(-1); final.resolve({ ...page(final, []), page: 1 }); await empty;
	assert.equal(value.total, 0); assert.equal(value.page, 1); assert.equal(value.pageLabel.textContent, "0/0"); value.destroy();
}

async function main() {
	await observedFieldsAndSafeRows("销售客户"); await observedFieldsAndSafeRows("订单客户");
	await capabilitiesAndExplicitSource(); await scopeAndDictionaryRaces(); await sourceResponseRaceAndScopeRejection();
	await unknownCreationRecovery(); await creationReceiptAndEditedProfile(); await completePaginationAndClamp();
	console.log("Customer archive models passed: observed 19/17 fields and 16 columns, safe dictionary text, native capabilities/explicit links, leaf pickers, Company/customer/dictionary races, profile-only edits, dirty close and no repeated POST after unknown results.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
