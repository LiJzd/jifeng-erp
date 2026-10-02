// Source/scope/unknown-write models; no browser, native DB or network.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const calls = [], dialogs = [], confirms = [], messages = [];
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this._text = ""; this.value = ""; this.scrollTop = this.scrollLeft = 0; this.classList = { add() {} }; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((child) => child.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("Business HTML must never execute"); }
	append(...nodes) { this.children.push(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, action) { this.events.set(name, action); }
	descendants(tag) { return this.children.flatMap((child) => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Wrapper {
	constructor(element = new Element()) { this.element = element; }
	get() { return this.element; }
	addClass() { return this; }
	modal() { return this; }
	on() { return this; }
}
class Dialog {
	constructor(config) { this.config = config; this.hidden = true; this.$wrapper = new Wrapper(); this.fields_dict = Object.fromEntries(config.fields.map((field) => [field.fieldname, { $wrapper: new Wrapper() }])); dialogs.push(this); }
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
const frappe = { ui: { Dialog, form: { make_control({ df }) {
	return { df, value: null, get_value() { return this.value; }, refresh() {}, async set_value(value) { this.value = value; await df.onchange?.(); } };
} } }, call(options) { let resolve, reject; const pending = new Promise((yes, no) => { resolve = yes; reject = no; }); calls.push({ options, resolve: (message) => resolve({ message }), reject }); return pending; },
	confirm(message, yes, no) { confirms.push({ message, yes, no }); }, msgprint(message) { messages.push(message); } };
const window = { addEventListener() {}, removeEventListener() {} }, document = { createElement: (tag) => new Element(tag) }, crypto = { randomUUID: () => "11111111-1111-4111-8111-111111111111" };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_partner_archive.js"), "utf8"), { window, document, frappe, crypto, structuredClone, __: (value) => value });
const css = fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/css/jifeng_partner_archive.css"), "utf8");
function fixedTail(value) {
	assert.equal(value.scroller.descendants("table")[0].className, "jfe-partner-table");
	const headers = value.host.descendants("th");
	assert.equal(headers.at(-2).className, "jfe-partner-action jfe-partner-modify");
	assert.equal(headers.at(-1).className, "jfe-partner-action jfe-partner-delete");
	for (const row of value.tbody.children) {
		assert.equal(row.children.at(-2).className, headers.at(-2).className);
		assert.equal(row.children.at(-1).className, headers.at(-1).className);
	}
}
const fields = ["legacy_number", "display_name", "owner_name", "mobile", "reception", "phone", "address", "notes", "bank_name", "account_name", "bank_account", "inactive", "company", "archive_kind", "supplier"];
function archive(name = "PROFILE-A", company = "Company A", writable = true, kind = "外发工厂") {
	return { name, kind, company, ...(kind === "电镀厂" ? { owner: "synthetic-creator@example.invalid" } : {}), supplier: name ? "SUP-A" : null, supplier_disabled: name ? false : null,
		profile_version: name ? "2026-10-02T00:00:00.123456" : null, supplier_version: name ? "2026-10-02T00:00:01.123456" : null,
		creation: name ? "2026-10-02T00:00:00.000001" : null, modified_by: name ? "synthetic-user@example.invalid" : null, can_write: writable,
		profile: { company, archive_kind: kind, supplier: name ? "SUP-A" : null, legacy_number: " 0007 ", display_name: '<b onclick="alert(1)">合成工厂</b>',
			owner_name: "合成负责人", mobile: "000000", reception: "合成接货人", phone: "0000", address: "合成地址\n原文", notes: "  合成备注  ",
			bank_name: "开户原文", account_name: "户名原文", bank_account: "0000123", inactive: 0 },
		can_create_supplier: !name && writable, can_link_supplier: !name && writable,
		supplier_defaults: { supplier_type: "Company", supplier_group: null, naming_series: "SUP-.YYYY.-" },
		supplier_field_meta: { supplier_type: { fieldtype: "Select", options: "Company\nIndividual\nPartnership", reqd: true, read_only: false },
			supplier_group: { fieldtype: "Link", options: "Supplier Group", reqd: false, read_only: false }, naming_series: { fieldtype: "Select", options: "SUP-.YYYY.-", reqd: false, read_only: false } },
		field_meta: Object.fromEntries(fields.map((field) => [field, { fieldtype: field === "inactive" ? "Check" : ["company", "supplier"].includes(field) ? "Link" : "Data",
			options: "", reqd: ["legacy_number", "display_name", "company", "supplier"].includes(field), read_only: !writable || ["company", "archive_kind"].includes(field) || field === "supplier" && name !== null }])) };
}
function page(call, rows) { return { ...call.options.args, rows, total: rows.length, total_pages: Math.ceil(rows.length / call.options.args.page_size), readonly: true }; }
async function bench(kind = "外发工厂") { const value = new window.JifengPartnerArchive(new Element(), kind); await value.ready; await value.companyControl.set_value("Company A"); return value; }
async function list(value, rows = [archive("PROFILE-A", "Company A", false, value.kind)]) { const pending = value.query(), call = calls.at(-1); call.resolve(page(call, rows)); await pending; }
async function existing(value, row = archive("PROFILE-A", "Company A", true, value.kind)) { await list(value); const pending = value.openExisting("PROFILE-A"); calls.at(-1).resolve(row); await pending; return value.editor; }
async function create(value) { const pending = value.openNew(); calls.at(-1).resolve(archive(null, "Company A", true, value.kind)); await pending; return value.editor; }
async function selectSupplier(value, state, name = "SUP-A", version = "2026-10-02T00:00:01.123456") {
	await state.modeControl.set_value("关联已有供应商");
	const pending = state.supplierControl.set_value(name); calls.at(-1).resolve({ name, version, disabled: false, company: "Company A", kind: value.kind }); await pending;
}
async function changed(state, field, value) { await state.controls[field].set_value(value); }
async function tick() { await new Promise(setImmediate); }

async function missedCompanyEvents() {
	const value = await bench();
	value.companyControl.value = "Company B";
	const queried = value.queryButton.events.get("click")(), queryCall = calls.at(-1);
	assert.equal(queryCall.options.args.company, "Company B");
	queryCall.resolve(page(queryCall, [archive("PROFILE-B", "Company B", false)])); await queried;
	assert.equal(value.rows[0].name, "PROFILE-B");
	const late = value.query(), lateCall = calls.at(-1);
	value.companyControl.value = "Company C";
	lateCall.resolve(page(lateCall, [archive("PROFILE-B", "Company B", false)])); await late;
	assert.equal(value.total, null);
	const count = calls.length; await value.openExisting("PROFILE-B"); assert.equal(calls.length, count);
	const opened = value.openNew(), openCall = calls.at(-1);
	assert.equal(openCall.options.args.company, "Company C");
	value.companyControl.value = "Company D";
	openCall.resolve(archive(null, "Company C")); await opened; assert.equal(value.editor, null);
	value.destroy();
}

function statusChange(value, desired) { value.statusSelect.value = desired; return value.statusSelect.events.get("change")({ target: value.statusSelect }); }
function frozenStatus(value, state = value.editor) {
	assert.equal(value.statusSelect.disabled, true);
	const snapshot = () => JSON.stringify({ status: value.statusFilter, company: value.companyScope, revision: value.revision, openRevision: value.openRevision,
		page: value.page, draft: state ? value.draft(state) : null, requestId: state?.requestId, candidate: state?.candidate,
		profileVersion: state?.archive.profile_version, supplierVersion: state?.supplierVersion });
	const before = snapshot(), count = calls.length; statusChange(value, "停用");
	assert.equal(value.statusSelect.value, value.statusFilter); assert.equal(snapshot(), before); assert.equal(calls.length, count);
}
async function statusFilterEvents(kind) {
	const value = await bench(kind), select = value.statusSelect;
	assert.equal(select.tag, "select"); assert.equal(select.attributes["aria-label"], "状态");
	assert.deepEqual(select.children.map((option) => [option.tag, option.value, option.textContent]),
		[["option", "在用", "在用"], ["option", "停用", "停用"], ["option", "全部", "全部"]]);
	assert.equal(select.value, "在用"); assert.equal(select.disabled, false);
	assert.equal(value.host.children[0].children.at(-2).children[0].textContent, "状态");
	const active = () => archive("ACTIVE", "Company A", false, kind), inactive = () => {
		const row = archive("INACTIVE", "Company A", false, kind); row.profile.inactive = 1; return row;
	};
	const secondPage = value.query(2), secondCall = calls.at(-1);
	secondCall.resolve({ ...page(secondCall, [active()]), total: 101, total_pages: 2 }); await secondPage;
	assert.equal(value.page, 2); value.search.value = "保留关键词"; value.scroller.scrollLeft = 440; value.scroller.scrollTop = 32;
	const beforeChange = calls.length, oldRevision = value.revision; statusChange(value, "停用");
	assert.equal(calls.length, beforeChange); assert.equal(value.revision, oldRevision + 1);
	assert.equal(value.statusFilter, "停用"); assert.equal(value.page, 1); assert.equal(value.goto.value, "1");
	assert.equal(value.rows.length, 0); assert.equal(value.tbody.children.length, 0); assert.equal(value.total, null);
	assert.equal(value.previous.disabled, true); assert.equal(value.next.disabled, true); assert.equal(value.count.textContent, "尚未查询");
	assert.equal(value.companyScope, "Company A"); assert.equal(value.search.value, "保留关键词");
	assert.equal(value.scroller.scrollLeft, 440); assert.equal(value.scroller.scrollTop, 32);
	assert.match(value.status.textContent, /请查询/);
	const stopped = value.queryButton.events.get("click")(), stoppedCall = calls.at(-1);
	assert.equal(stoppedCall.options.type, "GET"); assert.equal(stoppedCall.options.args.status, "停用"); assert.equal(stoppedCall.options.args.page, 1);
	stoppedCall.resolve(page(stoppedCall, [inactive()])); await stopped;
	assert.equal(value.rows[0].profile.inactive, 1); fixedTail(value);
	statusChange(value, "全部"); assert.equal(calls.length, beforeChange + 1);
	const all = value.queryButton.events.get("click")(), allCall = calls.at(-1);
	assert.equal(allCall.options.args.status, "全部"); allCall.resolve(page(allCall, [active(), inactive()])); await all;
	assert.deepEqual(Array.from(value.rows, (row) => row.profile.inactive), [0, 1]);
	const mismatch = value.query(), mismatchCall = calls.at(-1);
	mismatchCall.resolve({ ...page(mismatchCall, [active()]), status: "在用" }); await mismatch; assert.equal(value.total, null);
	statusChange(value, "停用"); const wrongRows = value.query(), wrongCall = calls.at(-1);
	wrongCall.resolve(page(wrongCall, [active()])); await wrongRows; assert.equal(value.total, null); assert.equal(value.rows.length, 0);
	// A -> B -> A without another query must still invalidate the original A response.
	statusChange(value, "在用"); const stale = value.query(), staleCall = calls.at(-1), count = calls.length;
	statusChange(value, "停用"); statusChange(value, "在用"); assert.equal(calls.length, count);
	staleCall.resolve(page(staleCall, [active()])); await stale; assert.equal(value.total, null); assert.equal(value.rows.length, 0);
	const invalidRevision = value.revision; statusChange(value, "坏状态"); assert.equal(select.value, "在用"); assert.equal(value.revision, invalidRevision);
	await value.companyControl.set_value("Company B"); assert.equal(value.statusFilter, "在用"); assert.equal(select.value, "在用");
	assert.equal(value.search.value, "保留关键词"); assert.equal(calls.length, count); await value.companyControl.set_value("Company A");
	const opening = value.openNew(), openCall = calls.at(-1); frozenStatus(value, null);
	openCall.resolve(archive(null, "Company A", true, kind)); await opening; const state = value.editor; frozenStatus(value);
	await value.companyControl.set_value("Company B"); assert.equal(value.companyScope, "Company A"); assert.equal(value.companyControl.get_value(), "Company A");
	await changed(state, "notes", "保持原始草稿"); frozenStatus(value);
	const confirmClose = state.dialog.hide(); assert.equal(state.confirming, true); frozenStatus(value); confirms.at(-1).no(); await confirmClose;
	const saving = value.save(state), saveCall = calls.at(-1); assert.equal(state.busy, true); frozenStatus(value);
	saveCall.reject(new Error("unknown transport")); await saving; assert.equal(state.unknown, true); frozenStatus(value);
	const writeCount = calls.filter((call) => call.options.type === "POST").length;
	await value.save(state); assert.equal(calls.filter((call) => call.options.type === "POST").length, writeCount);
	value.destroy(); frozenStatus(value, state);
	// Normal native hide and explicit close both restore the visible filter for a reader.
	const reader = await bench(kind), readState = await existing(reader, archive("PROFILE-A", "Company A", false, kind));
	frozenStatus(reader); readState.nativeHide(); assert.equal(reader.editor, null); assert.equal(reader.statusSelect.disabled, false);
	const nextReader = await existing(reader, archive("PROFILE-A", "Company A", false, kind)); frozenStatus(reader);
	reader.closeEditor(); assert.equal(nextReader.closed, true); assert.equal(reader.statusSelect.disabled, false); reader.destroy();
}

async function main() {
	await missedCompanyEvents();
	// Every tail style remains scoped to this table; both cells share the right offset width.
	const styles = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(([, selector]) => /jfe-partner-(?:table|action|modify|delete)/.test(selector));
	assert.equal(styles.length, 7);
	assert.ok(styles.every(([, selector]) => selector.trim().startsWith(".jfe-partner-scroll .jfe-partner-table")));
	const rule = (selector) => styles.find(([_, key]) => key.trim() === selector)?.[2] || "";
	const scope = ".jfe-partner-scroll .jfe-partner-table";
	assert.match(rule(scope), /--jfe-partner-action-width:\s*72px/);
	const actions = rule(scope + " .jfe-partner-action");
	assert.match(actions, /position:\s*sticky/); assert.match(actions, /box-sizing:\s*border-box/);
	for (const property of ["width", "min-width", "max-width"]) assert.match(actions, new RegExp("(?:^|;)\\s*" + property + ":\\s*var\\(--jfe-partner-action-width\\)"));
	assert.match(rule(scope + " .jfe-partner-delete"), /right:\s*0/);
	assert.match(rule(scope + " .jfe-partner-modify"), /right:\s*var\(--jfe-partner-action-width\)/);
	assert.match(rule(scope + " td.jfe-partner-action"), /z-index:\s*2;\s*background:\s*#fff/);
	assert.match(rule(scope + " th.jfe-partner-action"), /z-index:\s*3;\s*background:\s*#eee/);
	assert.match(rule(scope + " tbody tr:nth-child(even) td.jfe-partner-action"), /background:\s*#fafafa/);
	assert.throws(() => new window.JifengPartnerArchive(new Element(), "发镀"));
	const value = await bench();
	// Independent runtime evidence supersedes the earlier 16-column assumption.
	// Separate desktop Canvas header strip confirms every visible label.
	assert.equal(value.host.descendants("th").length, 13);
	assert.equal(value.host.descendants("th").map((node) => node.textContent).join("/"), "编号/名称/负责/手机/接货/电话/地址/备注/开户/户名/账号/修改/删除");
	assert.deepEqual(Array.from(value.listFields, ([field]) => field), fields.slice(0, 11));
	assert.match(value.scopeNotice.textContent, /部分可用/);
	assert.deepEqual(value.host.descendants("button").map((node) => node.textContent), ["新增", "导出", "查询", "上页", "下页", "前往"]);
	assert.equal(value.search.placeholder, "输入查询的关键字"); assert.equal(value.statusFilter, "在用");
	const beforeNoScope = calls.length, empty = new window.JifengPartnerArchive(new Element()); await empty.ready; await empty.query(); assert.equal(calls.length, beforeNoScope);
	await list(value);
	assert.equal(calls.at(-1).options.method, "jifeng_erp.partner_archive.query_archives"); assert.equal(calls.at(-1).options.type, "GET");
	assert.equal(value.tbody.children[0].children[1].textContent, '<b onclick="alert(1)">合成工厂</b>'); assert.equal(value.host.descendants("b").length, 0);
	assert.equal(value.tbody.children[0].children[10].textContent, "0000123");
	assert.equal(value.tbody.children[0].children.length, 13); fixedTail(value);
	assert.equal(value.tbody.children[0].children[11].children[0].textContent, "修改");
	const externalDelete = value.tbody.children[0].children[12].children[0];
	assert.equal(externalDelete.textContent, "删除"); assert.equal(externalDelete.disabled, true); assert.equal(externalDelete.events.size, 0);
	assert.equal(value.rows[0].profile.inactive, 0);
	assert.equal(value.rows[0].creation, "2026-10-02T00:00:00.000001");
	assert.equal(value.rows[0].modified_by, "synthetic-user@example.invalid");
	assert.equal(value.scroller.textContent.includes("synthetic-user@example.invalid"), false);
	const externalEdit = value.tbody.children[0].children[11].children[0].events.get("click")(), externalRead = calls.at(-1);
	assert.equal(externalRead.options.method, "jifeng_erp.partner_archive.read_archive");
	assert.equal(externalRead.options.args.kind, "外发工厂");
	externalRead.resolve(archive()); await externalEdit;
	assert.equal(value.editor.controls.inactive.get_value(), 0);
	assert.equal(value.editor.controls.bank_account.get_value(), "0000123");
	assert.equal(value.editor.archive.creation, "2026-10-02T00:00:00.000001");
	await value.editor.dialog.hide();
	const noBank = archive("PROFILE-A", "Company A", false); delete noBank.profile.bank_account; delete noBank.field_meta.bank_account;
	await list(value, [noBank]); assert.equal(value.tbody.children[0].children[10].textContent, "");
	value.scroller.scrollLeft = 440; value.scroller.scrollTop = 32; await list(value); assert.equal(value.scroller.scrollLeft, 440); assert.equal(value.scroller.scrollTop, 32);
	for (const invalid of [true, false, 0, 1.2, "01", " 1", "1 ", "+1", "1e0", "1001"]) { const previous = calls.length; await value.query(invalid); assert.equal(calls.length, previous); }
	const stale = value.query(), staleCall = calls.at(-1); value.search.value = "新词"; value.search.events.get("input")();
	const latest = value.query(), latestCall = calls.at(-1); latestCall.resolve(page(latestCall, [archive("LATEST", "Company A", false)])); await latest;
	staleCall.resolve(page(staleCall, [archive("STALE", "Company A", false)])); await stale; assert.equal(value.rows[0].name, "LATEST");
	value.search.value = "";
	const wrong = value.query(), wrongCall = calls.at(-1), wrongPage = page(wrongCall, [archive("PROFILE-A", "Company A", false)]); wrongPage.company = "Other";
	wrongCall.resolve(wrongPage); await wrong; assert.equal(value.total, null); assert.equal(value.rows.length, 0);

	let state = await existing(value);
	assert.deepEqual(Object.keys(state.controls), fields.slice(0, 12));
	assert.equal(state.controls.bank_name.df.label, "户行"); assert.equal(state.supplierControl.df.read_only, true);
	assert.equal(value.isDirty(), false); assert.equal(state.controls.bank_account.get_value(), "0000123");
	await changed(state, "notes", "合成新备注"); assert.equal(value.isDirty(), true);
	const hideNo = state.dialog.hide(); assert.equal(confirms.length, 1); confirms.at(-1).no(); await hideNo;
	assert.equal(state.closed, false); assert.equal(state.controls.notes.get_value(), "合成新备注");
	const hideYes = state.dialog.hide(); confirms.at(-1).yes(); await hideYes; assert.equal(state.closed, true); assert.equal(value.editor, null);
	assert.equal(value.companyScope, "Company A"); assert.equal(value.scroller.scrollLeft, 440);

	state = await create(value);
	assert.equal(state.sourceMode, "create"); assert.equal(state.supplierVersion, null); assert.equal(state.saveButton.disabled, false);
	assert.equal(state.supplierControl.df.hidden, true); assert.equal(state.supplierControls.supplier_type.get_value(), "Company");
	assert.equal(state.supplierControls.supplier_group.get_value(), "");
	const rawName = state.controls.display_name.get_value(); await selectSupplier(value, state); assert.equal(state.controls.display_name.get_value(), rawName);
	assert.equal(state.saveButton.disabled, false);
	await changed(state, "legacy_number", "WF-SYNTHETIC");
	const saving = value.save(state), saveCall = calls.at(-1); assert.equal(saveCall.options.type, "POST");
	assert.equal(saveCall.options.method, "jifeng_erp.partner_archive.save_archive"); assert.equal(saveCall.options.args.payload.request_id, crypto.randomUUID());
	assert.equal(saveCall.options.args.payload.profile.supplier, "SUP-A"); assert.equal(saveCall.options.args.payload.supplier_version, "2026-10-02T00:00:01.123456");
	assert.equal(state.saveButton.disabled, true); assert.match(value.getCloseBlock(), /正在/);
	const success = archive("CREATED"); success.profile.legacy_number = "WF-SYNTHETIC";
	saveCall.resolve(success); await tick(); assert.equal(calls.at(-1).options.method, "jifeng_erp.partner_archive.resolve_creation");
	assert.equal(calls.at(-1).options.args.request_id, crypto.randomUUID()); calls.at(-1).resolve({ status: "found", archive: success }); await saving;
	assert.equal(state.unknown, false); assert.equal(state.archive.name, "CREATED"); assert.equal(value.isDirty(), false); assert.equal(value.total, null);
	await state.dialog.hide();

	state = await existing(value);
	await changed(state, "bank_account", "0000999");
	const editSave = value.save(state), editCall = calls.at(-1); assert.deepEqual(Object.keys(editCall.options.args.payload.profile), ["bank_account"]);
	assert.equal(editCall.options.args.payload.name, "PROFILE-A"); assert.equal("request_id" in editCall.options.args.payload, false);
	assert.equal(editCall.options.args.payload.profile_version, state.archive.profile_version);
	editCall.reject(new Error("connection lost")); await editSave;
	assert.equal(state.unknown, true); assert.match(value.getCloseBlock(), /尚未确认/); assert.equal(state.controls.bank_account.get_value(), "0000999");
	const writesBefore = calls.filter((call) => call.options.type === "POST").length;
	await value.save(state); await state.dialog.hide(); assert.equal(calls.filter((call) => call.options.type === "POST").length, writesBefore); assert.equal(state.closed, false);
	const resolving = value.resolve(state), readCall = calls.at(-1); assert.equal(readCall.options.method, "jifeng_erp.partner_archive.read_archive"); assert.equal(readCall.options.type, "GET");
	const conflict = archive(); readCall.resolve(conflict); await resolving; assert.equal(state.unknown, true); assert.equal(state.conflict.name, "PROFILE-A");
	value.adopt(state); confirms.at(-1).no(); assert.equal(state.unknown, true);
	value.adopt(state); confirms.at(-1).yes(); await tick(); assert.equal(state.unknown, false); assert.equal(state.controls.bank_account.get_value(), "0000123");
	assert.equal(calls.filter((call) => call.options.type === "POST").length, writesBefore); await state.dialog.hide();

	state = await create(value); await selectSupplier(value, state);
	const unknownCreate = value.save(state), unknownWrite = calls.at(-1), originalPayload = JSON.stringify(unknownWrite.options.args.payload);
	unknownWrite.reject(new Error("unknown transport")); await unknownCreate;
	assert.equal(state.unknown, true);
	const resolveEmpty = value.resolve(state); calls.at(-1).resolve({ status: "not_found" }); await resolveEmpty;
	assert.equal(state.unknown, true); assert.equal(JSON.stringify(state.candidate), originalPayload);
	assert.match(state.message.textContent, /不会重复保存/); value.destroy(); assert.equal(state.closed, true); assert.equal(value.host.children.length, 0);
	assert.equal(calls.filter((call) => /delete|upload|submit|payment/.test(call.options.method)).length, 0);

	const reader = await bench(); const readOnly = await existing(reader, archive("PROFILE-A", "Company A", false));
	assert.equal(readOnly.saveButton.disabled, true); assert.equal(readOnly.controls.bank_account.df.read_only, true);
	const countBefore = calls.length; await reader.save(readOnly); assert.equal(calls.length, countBefore); await readOnly.dialog.hide();
	const pendingOpen = reader.openNew(), pendingCall = calls.at(-1); reader.destroy(); pendingCall.resolve(archive(null)); await pendingOpen; assert.equal(reader.editor, null);

	const validation = await bench(); const invalidScope = validation.openNew(); calls.at(-1).resolve(archive(null, "Other")); await invalidScope; assert.equal(validation.editor, null);
	const firstSupplier = await create(validation); await firstSupplier.modeControl.set_value("关联已有供应商");
	const supplierOld = firstSupplier.supplierControl.set_value("SUP-A"), supplierOldCall = calls.at(-1);
	const supplierNew = firstSupplier.supplierControl.set_value("SUP-B"), supplierNewCall = calls.at(-1);
	supplierNewCall.resolve({ name: "SUP-B", version: "new-version", disabled: false, company: "Company A", kind: "外发工厂" }); await supplierNew;
	supplierOldCall.resolve({ name: "SUP-A", version: "stale-version", disabled: false, company: "Company A", kind: "外发工厂" }); await supplierOld;
	assert.equal(firstSupplier.supplierVersion, "new-version"); assert.equal(firstSupplier.supplierControl.get_value(), "SUP-B");
	// The server converts this exact native duplicate-name rejection to HTTP 417.
	const duplicate = await bench(), duplicateState = await create(duplicate);
	await changed(duplicateState, "display_name", "合成已有供应商名称");
	const duplicateDraft = JSON.stringify(duplicate.values(duplicateState)), duplicateRequest = duplicateState.requestId;
	const duplicateWritesBefore = calls.filter((call) => call.options.type === "POST").length;
	const duplicateSave = duplicate.save(duplicateState), duplicateCall = calls.at(-1);
	assert.equal(duplicateCall.options.type, "POST");
	duplicateCall.reject({ status: 417, responseJSON: { exc_type: "ValidationError" } }); await duplicateSave;
	assert.equal(duplicateState.busy, false); assert.equal(duplicateState.unknown, false);
	assert.equal(JSON.stringify(duplicate.values(duplicateState)), duplicateDraft);
	assert.equal(duplicateState.requestId, duplicateRequest); assert.equal(duplicate.getCloseBlock(), "");
	assert.equal(duplicateState.controls.display_name.df.read_only, false); assert.equal(duplicateState.saveButton.disabled, false);
	assert.equal(calls.filter((call) => call.options.type === "POST").length, duplicateWritesBefore + 1);
	assert.match(duplicateState.message.textContent, /保存未完成/);
	const refusedDiscard = duplicateState.dialog.hide(); confirms.at(-1).no(); await refusedDiscard;
	assert.equal(duplicateState.closed, false); assert.equal(JSON.stringify(duplicate.values(duplicateState)), duplicateDraft);
	await changed(duplicateState, "display_name", "合成修正新供应商名称");
	assert.equal(calls.filter((call) => call.options.type === "POST").length, duplicateWritesBefore + 1);
	const correctedSave = duplicate.save(duplicateState), correctedCall = calls.at(-1);
	assert.equal(correctedCall.options.args.payload.request_id, duplicateRequest);
	assert.equal(correctedCall.options.args.payload.profile.display_name, "合成修正新供应商名称");
	assert.equal("supplier" in correctedCall.options.args.payload.profile, false);
	const correctedRow = archive("CORRECTED-CREATED");
	Object.assign(correctedRow.profile, correctedCall.options.args.payload.profile);
	correctedRow.supplier = correctedRow.profile.supplier = "NATIVE-CORRECTED-SUPPLIER";
	correctedCall.resolve(correctedRow); await tick();
	assert.equal(calls.at(-1).options.type, "GET");
	calls.at(-1).resolve({ status: "found", archive: correctedRow }); await correctedSave;
	assert.equal(duplicateState.archive.name, "CORRECTED-CREATED"); assert.equal(duplicateState.unknown, false);
	assert.equal(calls.filter((call) => call.options.type === "POST").length, duplicateWritesBefore + 2);
	await changed(duplicateState, "notes", "合成未保存备注");
	const confirmedDiscard = duplicateState.dialog.hide(); confirms.at(-1).yes(); await confirmedDiscard;
	assert.equal(duplicateState.closed, true); duplicate.destroy();
	const discardDuplicate = await bench(), discardDuplicateState = await create(discardDuplicate);
	await changed(discardDuplicateState, "display_name", "合成重名待放弃草稿");
	const discardWrites = calls.filter((call) => call.options.type === "POST").length;
	const discardedSave = discardDuplicate.save(discardDuplicateState);
	calls.at(-1).reject({ status: 417, responseJSON: { exc_type: "ValidationError" } }); await discardedSave;
	assert.equal(discardDuplicateState.unknown, false); assert.equal(discardDuplicate.getCloseBlock(), "");
	const dropRejectedDraft = discardDuplicateState.dialog.hide(); confirms.at(-1).yes(); await dropRejectedDraft;
	assert.equal(discardDuplicateState.closed, true);
	assert.equal(calls.filter((call) => call.options.type === "POST").length, discardWrites + 1);
	discardDuplicate.destroy();

	// A successful Save followed by a failed receipt is still an unknown result,
	// even when the receipt rejects with an ordinarily correctable error category.
	const uncertain = await bench(), uncertainState = await create(uncertain);
	const uncertainWritesBefore = calls.filter((call) => call.options.type === "POST").length;
	const receiptFailure = uncertain.save(uncertainState), uncertainWrite = calls.at(-1);
	const uncertainRow = archive("UNCERTAIN-CREATED");
	Object.assign(uncertainRow.profile, uncertainWrite.options.args.payload.profile);
	uncertainRow.supplier = uncertainRow.profile.supplier = "NATIVE-UNCERTAIN-SUPPLIER";
	uncertainWrite.resolve(uncertainRow); await tick();
	assert.equal(calls.at(-1).options.method, "jifeng_erp.partner_archive.resolve_creation");
	calls.at(-1).reject({ status: 417, responseJSON: { exc_type: "ValidationError" } }); await receiptFailure;
	assert.equal(uncertainState.busy, false); assert.equal(uncertainState.unknown, true);
	assert.equal(uncertainState.controls.display_name.df.read_only, true); assert.equal(uncertainState.saveButton.disabled, true);
	assert.match(uncertain.getCloseBlock(), /尚未确认/);
	await uncertain.save(uncertainState); await uncertainState.dialog.hide();
	assert.equal(uncertainState.closed, false);
	assert.equal(calls.filter((call) => call.options.type === "POST").length, uncertainWritesBefore + 1);
	uncertain.destroy();

	const joint = await bench(), jointState = await create(joint); await changed(jointState, "display_name", "合成联合工厂");
	const jointSave = joint.save(jointState), jointCall = calls.at(-1);
	assert.equal(jointCall.options.type, "POST"); assert.equal(jointCall.options.args.payload.supplier_create.supplier_type, "Company");
	assert.equal(jointCall.options.args.payload.supplier_create.supplier_group, null); assert.equal(jointCall.options.args.payload.supplier_create.naming_series, "SUP-.YYYY.-");
	assert.equal("supplier_version" in jointCall.options.args.payload, false); assert.equal("supplier" in jointCall.options.args.payload.profile, false);
	const jointRow = archive("JOINT-CREATED"); jointRow.profile.display_name = "合成联合工厂"; jointRow.supplier = jointRow.profile.supplier = "NATIVE-SUPPLIER-ID";
	jointCall.resolve(jointRow); await tick(); calls.at(-1).resolve({ status: "found", archive: jointRow }); await jointSave;
	assert.equal(jointState.archive.supplier, "NATIVE-SUPPLIER-ID"); assert.equal(jointState.unknown, false); assert.equal(jointState.sourceMode, "existing");
	assert.equal(joint.host.textContent.includes("UUID"), false); assert.equal(jointState.dialog.fields_dict.partner_editor.$wrapper.get().textContent.includes("UUID"), false);
	joint.destroy();
	validation.destroy(); reader.destroy(); empty.destroy();
	const plating = await bench("电镀厂");
	assert.deepEqual(plating.host.descendants("th").map((node) => node.textContent),
		["", "编号", "名称", "负责", "手机", "接货", "电话", "地址", "备注", "户行", "户名", "账号", "停用", "操作", "日期", "修改人", "修改日期", "修改", "删除"]);
	assert.match(plating.scopeNotice.textContent, /部分可用/); assert.match(plating.scopeNotice.textContent, /删除.*尚未开放/);
	await list(plating); assert.equal(calls.at(-1).options.args.kind, "电镀厂");
	assert.equal(plating.tbody.children[0].children.length, 19); fixedTail(plating);
	assert.equal(plating.tbody.children[0].children[0].textContent, "");
	assert.equal(plating.tbody.children[0].children[11].textContent, "0000123");
	assert.equal(plating.tbody.children[0].children[13].textContent, "synthetic-creator@example.invalid");
	assert.equal(plating.tbody.children[0].children[13].descendants("button").length, 0);
	assert.notEqual(plating.tbody.children[0].children[13].textContent, plating.rows[0].modified_by);
	const maskedCreator = archive("PROFILE-A", "Company A", false, "电镀厂"); maskedCreator.owner = null;
	await list(plating, [maskedCreator]);
	assert.equal(plating.tbody.children[0].children[13].textContent, "");
	await list(plating);
	const deleteButton = plating.tbody.children[0].children[18].children[0];
	assert.equal(deleteButton.textContent, "删除"); assert.equal(deleteButton.disabled, true);
	assert.equal(deleteButton.events.size, 0); assert.match(deleteButton.title, /尚未开放/);
	const platingNew = await create(plating);
	assert.equal(platingNew.controls.bank_name.df.label, "户行");
	assert.notEqual(platingNew.cells.bank_account.className, "jfe-partner-wide-field");
	assert.notEqual(platingNew.cells.inactive.className, "jfe-partner-wide-field");
	assert.equal(plating.payload(platingNew).kind, "电镀厂"); await platingNew.dialog.hide();
	const wrongKindOpen = plating.openNew(); calls.at(-1).resolve(archive(null)); await wrongKindOpen;
	assert.equal(plating.editor, null);
	const wrongKindQuery = plating.query(), wrongKindCall = calls.at(-1);
	wrongKindCall.resolve(page(wrongKindCall, [archive("PROFILE-A", "Company A", false)])); await wrongKindQuery;
	assert.equal(plating.total, null); assert.equal(plating.rows.length, 0);
	await list(plating);
	const modifyButton = plating.tbody.children[0].children[17].children[0];
	assert.equal(modifyButton.textContent, "修改");
	const rightModify = modifyButton.events.get("click")(), modifyCall = calls.at(-1);
	assert.equal(modifyCall.options.method, "jifeng_erp.partner_archive.read_archive");
	assert.equal(modifyCall.options.args.kind, "电镀厂");
	modifyCall.resolve(archive("PROFILE-A", "Company A", true, "电镀厂")); await rightModify;
	const platingEdit = plating.editor;
	assert.equal(platingEdit.newButton.textContent, "修改"); assert.equal(platingEdit.newButton.disabled, true);
	assert.match(platingEdit.newButton.title, /尚未开放.*保存/);
	const beforeUnknownFooter = calls.length; platingEdit.newButton.events.get("click")(); await tick();
	assert.equal(calls.length, beforeUnknownFooter); assert.equal(plating.editor, platingEdit);
	await changed(platingEdit, "notes", "电镀合成独立备注");
	const platingSave = plating.save(platingEdit), platingWrite = calls.at(-1);
	assert.equal(platingWrite.options.args.payload.kind, "电镀厂");
	assert.deepEqual(platingWrite.options.args.payload.profile, { notes: "电镀合成独立备注" });
	const savedPlating = archive("PROFILE-A", "Company A", true, "电镀厂");
	savedPlating.profile.notes = "电镀合成独立备注"; savedPlating.profile_version = "2026-10-02T00:00:09.123456";
	platingWrite.resolve(savedPlating); await platingSave;
	assert.equal(platingEdit.unknown, false); assert.equal(platingEdit.archive.kind, "电镀厂");
	await platingEdit.dialog.hide(); plating.destroy();
	assert.equal(calls.filter((call) => /delete|trash|unlink/.test(call.options.method)).length, 0);
	await statusFilterEvents("外发工厂"); await statusFilterEvents("电镀厂");

	console.log("partner archive client protocol models: PASS");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
