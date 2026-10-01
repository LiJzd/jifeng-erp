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
const fields = ["legacy_number", "display_name", "owner_name", "mobile", "reception", "phone", "address", "notes", "bank_name", "account_name", "bank_account", "inactive", "company", "archive_kind", "supplier"];
function archive(name = "PROFILE-A", company = "Company A", writable = true) {
	return { name, kind: "外发工厂", company, supplier: name ? "SUP-A" : null, supplier_disabled: name ? false : null,
		profile_version: name ? "2026-10-02T00:00:00.123456" : null, supplier_version: name ? "2026-10-02T00:00:01.123456" : null,
		creation: name ? "2026-10-02T00:00:00.000001" : null, modified_by: name ? "synthetic-user@example.invalid" : null, can_write: writable,
		profile: { company, archive_kind: "外发工厂", supplier: name ? "SUP-A" : null, legacy_number: " 0007 ", display_name: '<b onclick="alert(1)">合成工厂</b>',
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
async function bench() { const value = new window.JifengPartnerArchive(new Element()); await value.ready; await value.companyControl.set_value("Company A"); return value; }
async function list(value, rows = [archive("PROFILE-A", "Company A", false)]) { const pending = value.query(), call = calls.at(-1); call.resolve(page(call, rows)); await pending; }
async function existing(value, row = archive()) { await list(value); const pending = value.openExisting("PROFILE-A"); calls.at(-1).resolve(row); await pending; return value.editor; }
async function create(value) { const pending = value.openNew(); calls.at(-1).resolve(archive(null)); await pending; return value.editor; }
async function selectSupplier(value, state, name = "SUP-A", version = "2026-10-02T00:00:01.123456") {
	await state.modeControl.set_value("关联已有供应商");
	const pending = state.supplierControl.set_value(name); calls.at(-1).resolve({ name, version, disabled: false, company: "Company A", kind: "外发工厂" }); await pending;
}
async function changed(state, field, value) { await state.controls[field].set_value(value); }
async function tick() { await new Promise(setImmediate); }

async function main() {
	assert.throws(() => new window.JifengPartnerArchive(new Element(), "电镀厂"));
	const value = await bench();
	assert.equal(value.host.descendants("th").map((node) => node.textContent).join("/"), "编号/名称/负责/手机/接货/电话/地址/备注/开户/户名/账号/停用/操作/日期/修改人/修改日期");
	assert.deepEqual(value.host.descendants("button").map((node) => node.textContent), ["新增", "查询", "上页", "下页", "前往"]);
	assert.equal(value.search.placeholder, "输入查询的关键字"); assert.equal(value.statusFilter, "在用");
	const beforeNoScope = calls.length, empty = new window.JifengPartnerArchive(new Element()); await empty.ready; await empty.query(); assert.equal(calls.length, beforeNoScope);
	await list(value);
	assert.equal(calls.at(-1).options.method, "jifeng_erp.partner_archive.query_archives"); assert.equal(calls.at(-1).options.type, "GET");
	assert.equal(value.tbody.children[0].children[1].textContent, '<b onclick="alert(1)">合成工厂</b>'); assert.equal(value.host.descendants("b").length, 0);
	assert.equal(value.tbody.children[0].children[10].textContent, "0000123"); assert.equal(value.tbody.children[0].children[11].textContent, "0");
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
	console.log("partner archive client protocol models: PASS");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
