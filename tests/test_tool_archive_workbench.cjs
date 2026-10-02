// DOM/protocol models only; no browser, network, database or uploaded files.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), nodeCrypto = require("node:crypto");
const calls = [], uploads = [], messages = [], listeners = new Map(); let confirmation;
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
	descendants(tag) { return this.children.flatMap((child) => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
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
		this.fields_dict = Object.fromEntries((config.fields || []).map((df) => [df.fieldname, { df: { ...df }, $wrapper: new Wrapper() }]));
		this.fields_dict.__section_1 = { df: { fieldtype: "Section Break" } }; }
	get_primary_btn() { return this.button; }
	get_close_btn() { return this.close; }
	add_custom_action() {} // Real Frappe returns undefined.
	set_title(title) { this.title = title; }
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
class FileUploader {
	constructor(options) { this.options = options; this.dialog = new Dialog(); this.uploader = { files: [{ private: true, optimize: true, file_obj: { size: 201 * 1024 } }] }; this.nativeWrites = 0; this.nativeHidden = 0;
		this.dialog.on_hide = () => { this.nativeHidden++; }; uploads.push(this); }
	upload_files() { this.nativeWrites++; this.nativeOptimizeFlags = this.uploader.files.map((file) => file.optimize); return Promise.resolve(); } // Native resolves before HTTP on_success.
}
const window = { addEventListener(name, action) { listeners.set(action, name); }, removeEventListener(_, action) { listeners.delete(action); } };
const document = { createElement: (tag) => new Element(tag) };
const frappe = { pages: { "jifeng-legacy": {} }, ui: { Dialog, FileUploader, form: { make_control({ df }) {
	return { df: { ...df }, $input: new Wrapper(), value: null, get_value() { return this.value; }, async set_value(value) { await Promise.resolve(); this.value = value; await df.onchange?.(); }, refresh() {} };
} } }, call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); calls.push({ options, resolve: (message) => resolve({ message }), reject }); return promise; },
	msgprint(message) { messages.push(message); }, show_alert() {}, confirm(_, yes, no) { confirmation = { yes, no }; } };
const context = { window, document, frappe, structuredClone, __: (text) => text, Uint8Array, crypto: { getRandomValues: (bytes) => nodeCrypto.randomFillSync(bytes) } };
for (const file of ["jifeng_tool_archive", "jifeng_classic_bridge"]) vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
const fields = {
	mold: ["legacy_number", "mold_no", "product_code", "quantity", "maker", "mold_date", "amount", "remark", "location", "scrapped", "legacy_copy", "copy_from", "item_code", "image", "scrap_date", "scrap_reason"],
	plate: ["legacy_number", "plate_type", "product_code", "assessment_score", "maker", "quantity", "notes", "location", "retired", "legacy_copy", "copy_from", "plate_no", "item_code", "image", "plate_date", "modeler", "reference_cost", "retired_date", "retired_reason"],
};
function blank(kind, changes = {}) {
	const check = kind === "mold" ? "scrapped" : "retired", identity = kind === "mold" ? "mold_no" : "plate_no", float = kind === "mold" ? ["quantity", "amount"] : ["reference_cost"];
	return { record_type: kind, name: null, version: null, values: { company: "Company A", ...Object.fromEntries(fields[kind].map((field) => [field, null])),
		[check]: 0, legacy_copy: 0, [kind === "mold" ? "mold_date" : "plate_date"]: "2026-10-01", ...(kind === "mold" ? { quantity: 1, amount: 0 } : { assessment_score: "1", reference_cost: 0, plate_no: "JF-P-12345678-1234-4234-8234-123456789abc" }) },
		field_meta: { company: { fieldtype: "Link", options: "Company" }, ...Object.fromEntries(fields[kind].map((field) => [field, {
			fieldtype: float.includes(field) ? "Float" : [check, "legacy_copy"].includes(field) ? "Check" : field.endsWith("_date") ? "Date" : ["maker", "modeler", "item_code", "copy_from"].includes(field) ? "Link" : ["notes", "remark"].includes(field) || field.endsWith("_reason") ? "Small Text" : "Data",
			reqd: field === identity || kind === "mold" && field === "product_code", read_only: field === "image" }])) },
		image_states: { image: { state: "未上传", url: "" } }, can_write: true, can_select: false, ...changes };
}
function pack(kind, changes = {}) { const base = blank(kind), id = kind === "mold" ? "mold_no" : "plate_no"; return { ...base, name: kind.toUpperCase() + "-1", version: "2026-10-01T00:00:01",
	values: { ...base.values, [id]: "业务1", product_code: "产品1", legacy_number: "旧001" }, field_meta: { ...base.field_meta, company: { fieldtype: "Link", read_only: true }, image: { fieldtype: "Attach Image" } }, can_select: true, request_id: "native-receipt", ...changes }; }
const attachment = (changes = {}) => ({ name: "FILE-1", file_name: "档案.png", file_url: "/private/files/design.png", unique_url: "/private/files/design.png?fid=FILE-1", creation: "2026-10-01T00:00:02", ...changes });
function flatModel(saved = false, changes = {}) {
	const schema = JSON.parse(fs.readFileSync(path.join(__dirname, "../jifeng_erp/jifeng_erp/doctype/jifeng_plate_modeling/jifeng_plate_modeling.json"), "utf8"));
	const names = ["company", "legacy_number", "model_number", "product_code", "reference_unit_price", "modeler_text", "model_date", "remarks", "legacy_copy", "design_image", "item_code", "plate", "expense_voucher"];
	return { name: saved ? "MODEL-1" : null, version: saved ? "2026-10-01T00:00:01" : null, ...Object.fromEntries(names.map((field) => [field, null])),
		company: "Company A", model_date: "2026-10-01", legacy_copy: 0, field_meta: Object.fromEntries(names.map((field) => { const df = schema.fields.find((row) => row.fieldname === field); return [field, { fieldtype: df.fieldtype, options: df.options, reqd: !!df.reqd, read_only: field === "company" && saved || field === "design_image" && !saved }]; })),
		can_write: true, image_state: { state: "未上传", url: "" }, item_code_state: "未关联", plate_state: "未关联", expense_voucher_state: "未关联", ...changes };
}
async function opening(bench, archive = blank(bench.recordType)) {
	const pending = bench.openEditor(archive.name); const call = calls.at(-1); assert.equal(call.options.method, "jifeng_erp.tool_archive." + (archive.name ? "read_record" : "init_record"));
	assert.equal(call.options.args.record_type, bench.recordType); call.resolve(archive); return pending;
}
async function companyScopeEvents() {
	const bench = new window.JifengToolArchive(new Element(), "mold"), initial = calls.length;
	assert.equal(bench.companyControl.get_value(), null); assert.equal(calls.length, initial, "mount does not choose a company or query");
	bench.companyControl.value = "Company A"; const first = bench.query(); await tick();
	assert.equal(calls.length, initial + 1); assert.equal(calls.at(-1).options.args.company, "Company A"); calls.at(-1).resolve([pack("mold")]); await first;
	const input = (value) => { bench.companyControl.value = value; return bench.companyControl.$input.events.get("input")(); };
	const idleSetter = bench.companyControl.set_value.bind(bench.companyControl); let idleSetterCalls = 0;
	bench.companyControl.set_value = () => { idleSetterCalls++; throw new Error("idle company input must not call set_value"); };
	const count = calls.length, changed = input("Company B"); assert.equal(bench.rows.length, 0, "raw input clears old rows before native onchange");
	await changed; assert.equal(bench.companyScope, "Company B"); assert.equal(calls.length, count, "company input never automatically queries");
	assert.equal(idleSetterCalls, 0, "idle typing never resets or validates the native Link"); assert.equal(bench.companyControl.get_value(), "Company B"); bench.companyControl.set_value = idleSetter;
	const late = bench.query(), lateCall = calls.at(-1); bench.companyControl.value = "Company A";
	lateCall.resolve([pack("mold", { values: { ...pack("mold").values, company: "Company B" } })]); await late;
	assert.equal(bench.companyScope, "Company A"); assert.equal(bench.rows.length, 0, "silent changed company rejects a late query");
	const beforeOpen = calls.length; bench.companyControl.value = "Company B"; await bench.openEditor("MOLD-1");
	assert.equal(calls.length, beforeOpen, "an old row cannot open after a silent company change"); assert.equal(bench.companyScope, "Company B");
	bench.companyControl.value = null; await bench.companyControl.$input.events.get("change")(); await bench.query(); assert.equal(calls.length, beforeOpen, "clearing company does not query");
	await bench.companyControl.set_value("Company A"); assert.equal(calls.length, beforeOpen, "native onchange does not query either");
	const openingRead = bench.openEditor(), openingCall = calls.at(-1), nativeSet = bench.companyControl.set_value.bind(bench.companyControl); let restoreCompany;
	bench.companyControl.set_value = (value) => new Promise((resolve) => { restoreCompany = async () => { await nativeSet(value); resolve(); }; });
	const reverting = input("Company B"); assert.equal(bench.changingCompany, true); assert.equal(bench.companyControl.get_value(), "Company B");
	openingCall.resolve(blank("mold")); await openingRead;
	assert.equal(bench.editor.company, "Company A", "pending native company reversion preserves the original opening"); assert.equal(bench.companyScope, "Company A");
	await restoreCompany(); await reverting; bench.companyControl.set_value = nativeSet; assert.equal(bench.companyControl.get_value(), "Company A"); bench.closeEditor();
	const failedOpen = bench.openEditor(), failedCall = calls.at(-1); bench.companyControl.value = "Company B"; failedCall.reject(new Error("read failed")); await failedOpen;
	assert.equal(bench.companyControl.get_value(), "Company A"); assert.equal(bench.editor, null, "failed opening also restores a silently changed company");
	await opening(bench); const state = bench.editor, reads = calls.length;
	for (const flag of ["busy", "unresolved"]) { state[flag] = true; const revision = bench.revision; await input("Company B");
		assert.equal(bench.editor, state); assert.equal(bench.revision, revision); assert.equal(bench.companyScope, "Company A"); assert.equal(bench.companyControl.get_value(), "Company A"); state[flag] = false; }
	state.controls.mold_no.value = "dirty"; const decline = input("Company B"); await tick(); confirmation.no(); await decline;
	assert.equal(bench.editor, state); assert.equal(state.controls.mold_no.value, "dirty"); assert.equal(bench.companyScope, "Company A");
	const accept = input("Company B"); await tick(); const duringConfirm = bench.query(); calls.at(-1).resolve([pack("mold")]); await duringConfirm;
	assert.equal(bench.rows.length, 1); confirmation.yes(); await accept;
	assert.equal(bench.editor, null); assert.equal(bench.companyScope, "Company B"); assert.equal(bench.rows.length, 0, "accepted discard clears rows rebuilt during confirmation");
	assert.equal(calls.length, reads + 1, "scope acceptance adds no query beyond the explicit query during confirmation");
	bench.destroy();
}
async function main() {
	await companyScopeEvents();
	const opened = [], bridge = { open: (spec) => opened.push(spec) }, mold = new window.JifengToolArchive(new Element(), "mold", bridge), plate = new window.JifengToolArchive(new Element(), "plate", bridge);
	for (const bench of [mold, plate]) { bench.companyScope = "Company A"; await bench.companyControl.set_value("Company A"); }
	let query = mold.query(); assert.equal(calls.at(-1).options.args.include_inactive, 0); calls.at(-1).resolve([]); await query;
	mold.includeInactive.checked = true; query = mold.query(); assert.equal(calls.at(-1).options.args.include_inactive, 1); calls.at(-1).resolve([]); await query;
	const oldQuery = mold.query(), oldCall = calls.at(-1); mold.search.value = "new"; const latest = mold.query(); calls.at(-1).resolve([pack("mold")]); await latest;
	oldCall.resolve([pack("mold", { name: "OLD" })]); await oldQuery; assert.equal(mold.rows[0].name, "MOLD-1", "stale query does not replace newer rows");
	for (const bench of [mold, plate]) for (const label of ["导出", "导入", "打印"]) assert.ok(bench.host.descendants("button").find((node) => node.textContent === label).disabled);
	let dialog = await opening(mold), state = mold.editor;
	assert.equal(state.form.descendants("label").map((node) => node.textContent).join("/"), "编号/模具/产品/数量/压模/日期/金额/说明/库位/报废/复制");
	assert.ok(state.controls.legacy_number.readOnly); assert.ok(state.controls.mold_date.readOnly); assert.equal(state.controls.remark.tag, "input"); assert.equal(state.controls.remark.type, "text");
	assert.equal(state.controls.legacy_copy.disabled, false, "raw checkbox state is editable, no cloning operation"); assert.equal(state.nativeControls.maker.df.options, "Employee");
	assert.equal(state.nativeControls.maker.get_query().filters.company, "Company A"); assert.equal(state.nativeControls.copy_from.get_query().filters.company, "Company A");
	assert.equal(state.nativeControls.item_code.df.options, "Item"); assert.ok(state.image.uploadButton.disabled); assert.ok(state.image.resolveButton.hidden); assert.ok(state.resolveButton.hidden);
	assert.equal(mold.isDirty(), false); assert.equal(mold.payload(state).values.quantity, "1"); assert.equal("mold_date" in mold.payload(state).values, false);
	state.controls.mold_no.value = " 业务1 "; state.controls.product_code.value = " 产品1 "; state.controls.amount.value = ""; state.controls.legacy_copy.checked = true;
	await state.nativeControls.legacy_number.set_value(" 原编号 "); assert.equal(state.controls.legacy_number.value, " 原编号 ");
	const createPayload = mold.payload(state); assert.match(createPayload.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
	const saving = mold.save(state), saveCall = calls.at(-1); assert.equal(saveCall.options.type, "POST"); assert.equal(Object.keys(saveCall.options.args).join("/"), "payload", "save has no extra record_type keyword");
	assert.equal(saveCall.options.args.payload.record_type, "mold");
	const created = pack("mold", { request_id: createPayload.request_id, values: { ...blank("mold").values, ...createPayload.values, mold_no: "业务1", product_code: "产品1", quantity: 1, amount: 0, legacy_copy: 1 } });
	saveCall.resolve(created); await saving; assert.equal(state.unresolved, false); assert.equal(mold.isDirty(), false); calls.at(-1).resolve([]); await tick();
	assert.equal(state.controls.amount.value, "0"); assert.equal(state.controls.mold_no.value, "业务1"); assert.equal(state.image.uploadButton.disabled, false);
	assert.equal(state.dialog.hidden, false, "successful save stays in the editor"); assert.equal("request_id" in mold.payload(state), false); assert.equal(Object.keys(mold.payload(state).values).length, 0);
	const imageOpening = mold.uploadImage(state); calls.at(-1).resolve([attachment({ name: "OLDER" })]); await imageOpening; const uploader = uploads.at(-1);
	assert.equal(uploader.options.doctype, "Jifeng Mold"); assert.equal(uploader.options.docname, "MOLD-1"); assert.equal(uploader.options.fieldname, "image");
	assert.equal(uploader.options.make_attachments_public, 0); assert.equal(uploader.options.allow_toggle_private, false);
	assert.equal(uploader.options.allow_toggle_optimize, false); uploader.uploader.files.push({ private: true, optimize: true, file_obj: { size: 301 * 1024 } });
	await uploader.upload_files(); assert.ok(state.upload.open); assert.equal(state.imageValue, null, "early Promise resolution is not confirmed upload");
	assert.deepEqual(uploader.nativeOptimizeFlags, [false, false], "native upload receives every original image without optimization"); assert.equal(uploader.nativeWrites, 1);
	uploader.dialog.hidden = true; uploader.dialog.on_hide(); assert.equal(uploader.nativeHidden, 1); assert.equal(state.upload.open, false); assert.equal(state.uploadUnresolved, true);
	assert.ok(!state.image.resolveButton.hidden); const writes = uploader.nativeWrites; await uploader.upload_files(); assert.equal(uploader.nativeWrites, writes, "unknown upload cannot repeat POST");
	const none = mold.resolveUpload(state); calls.at(-1).resolve([attachment({ name: "OLDER" })]); await none; assert.ok(state.uploadUnresolved);
	const recovery = mold.resolveUpload(state); calls.at(-1).resolve([attachment({ name: "OLDER" }), attachment()]); await recovery;
	assert.equal(state.upload.candidates.length, 1); const choose = state.image.candidates.descendants("button")[0]; choose.events.get("click")(); confirmation.no(); assert.ok(state.uploadUnresolved);
	choose.events.get("click")(); confirmation.yes(); assert.equal(state.imageValue, "/private/files/design.png"); assert.equal(state.uploadUnresolved, false); assert.equal(mold.payload(state).values.image, "/private/files/design.png");
	assert.equal(state.image.preview.descendants("img").length, 0, "preview waits for saved authorized field");
	const imageSaving = mold.save(state); calls.at(-1).resolve({ ...created, version: "2026-10-01T00:00:02", values: { ...created.values, image: "/private/files/design.png" }, image_states: { image: { state: "已上传", url: "/private/files/design.png?fid=FILE-1" } } }); await imageSaving;
	assert.equal(state.image.preview.descendants("img")[0].src, "/private/files/design.png?fid=FILE-1"); calls.at(-1).resolve([]); await tick();
	const cancelOpening = mold.uploadImage(state); calls.at(-1).resolve([attachment()]); await cancelOpening; const cancelled = uploads.at(-1); cancelled.dialog.hidden = true; cancelled.dialog.on_hide();
	assert.equal(state.uploadUnresolved, false); assert.equal(state.upload.open, false); assert.equal(mold.getCloseBlock(), "", "X before any upload does not lock editor");
	const wrongOpening = mold.uploadImage(state); calls.at(-1).resolve([attachment()]); await wrongOpening; const wrong = uploads.at(-1); await wrong.upload_files();
	wrong.options.on_success({ name: "WRONG", doctype: "File", is_private: 1, attached_to_doctype: "Jifeng Plate", attached_to_name: "MOLD-1", attached_to_field: "image", file_url: "/private/files/wrong.png" });
	wrong.dialog.hide(); assert.equal(state.uploadUnresolved, true, "other record type cannot authorize image");
	wrong.options.on_success({ name: "LATE", doctype: "File", is_private: 1, attached_to_doctype: "Jifeng Mold", attached_to_name: "MOLD-1", attached_to_field: "image", file_url: "/private/files/late.png" });
	assert.equal(state.uploadUnresolved, false, "late exact success callback confirms result without another write");
	const pendingNative = mold.openNative(state); await tick(); confirmation.no(); await pendingNative; assert.equal(opened.length, 0);
	const nativeYes = mold.openNative(state); await tick(); confirmation.yes(); await nativeYes; assert.equal(opened[0].route.join("/"), "Form/Jifeng Mold/MOLD-1"); assert.equal(mold.editor, null);

	dialog = await opening(mold, pack("mold", { values: { ...pack("mold").values, remark: "说明\n原文", image: undefined } })); state = mold.editor;
	assert.equal(state.controls.remark.value, "说明原文"); assert.equal(mold.isDirty(), false); state.controls.location.value = "库位2";
	assert.equal("remark" in mold.payload(state).values, false); assert.equal("image" in mold.payload(state).values, false, "omitted inaccessible image is preserved");
	const update = mold.save(state), updateCall = calls.at(-1); updateCall.reject(new Error("connection lost")); await update;
	assert.ok(state.unresolved); assert.ok(mold.getCloseBlock()); const unknownWrites = calls.length; await mold.save(state); assert.equal(calls.length, unknownWrites);
	const noResult = mold.resolve(state); assert.equal(calls.at(-1).options.method, "jifeng_erp.tool_archive.read_record"); calls.at(-1).resolve(pack("plate")); await noResult; assert.ok(state.unresolved); assert.equal(state.conflict, null);
	const changed = mold.resolve(state); calls.at(-1).resolve(pack("mold", { version: "2026-10-01T00:00:03", values: { ...pack("mold").values, location: "另一人库位" } })); await changed;
	assert.ok(state.conflict); const postsBeforeAdopt = calls.filter((call) => call.options.type === "POST").length; mold.acceptCurrent(state); confirmation.no(); assert.ok(state.unresolved);
	mold.acceptCurrent(state); confirmation.yes(); await tick(); assert.equal(state.unresolved, false); assert.equal(state.controls.location.value, "另一人库位"); assert.equal(calls.filter((call) => call.options.type === "POST").length, postsBeforeAdopt);
	calls.at(-1).resolve([]); await tick();
	state.controls.location.value = "dirty"; const closeNo = state.dialog.hide(); await tick(); confirmation.no(); await closeNo; assert.equal(dialog.hidden, false);
	const scopeNo = mold.companyControl.set_value("Company B"); await tick(); confirmation.no(); await scopeNo; assert.equal(mold.companyScope, "Company A"); assert.equal(mold.companyControl.get_value(), "Company A");
	const closeYes = state.dialog.hide(); await tick(); confirmation.yes(); await closeYes; assert.equal(dialog.hidden, true);

	dialog = await opening(plate); state = plate.editor;
	assert.equal(state.nativeControls.plate_no.get_value(), blank("plate").values.plate_no); assert.equal(plate.payload(state).values.plate_no, blank("plate").values.plate_no);
	assert.equal(plate.isDirty(), false, "server supplied number is the clean new draft default");
	assert.equal(state.form.descendants("label").map((node) => node.textContent).join("/"), "编号/类型/产品/考核分/制版/数量/说明/库位/报废/复制");
	assert.ok(state.form.descendants("button").find((node) => node.textContent === "选择类型").disabled); assert.equal(state.controls.notes.tag, "input"); assert.equal(state.nativeControls.plate_no.df.reqd, true);
	for (const value of ["", "0", "-0", "0.123456789012345678901234567890", "01.0"]) { state.controls.quantity.value = value; state.controls.assessment_score.value = value;
		assert.equal(plate.payload(state).values.quantity, value); assert.equal(plate.payload(state).values.assessment_score, value); }
	state.controls.quantity.value = "-0"; state.controls.assessment_score.value = "0.12345678901234567890123456789"; state.controls.product_code.value = " 原产品 "; await state.nativeControls.plate_no.set_value(" 业务版1 ");
	const candidate = plate.payload(state), failed = plate.save(state); calls.at(-1).reject(new Error("unknown create")); await failed; assert.ok(state.unresolved);
	const absent = plate.resolve(state); assert.equal(calls.at(-1).options.method, "jifeng_erp.tool_archive.resolve_creation"); assert.equal(calls.at(-1).options.args.request_id, candidate.request_id);
	calls.at(-1).resolve({ status: "not_found" }); await absent; assert.ok(state.unresolved); assert.equal(state.conflict, null); const createWrites = calls.filter((call) => call.options.type === "POST").length; await plate.save(state); assert.equal(calls.filter((call) => call.options.type === "POST").length, createWrites);
	const foreignUUID = plate.resolve(state); calls.at(-1).resolve({ status: "found", archive: pack("plate", { request_id: "wrong", values: { ...blank("plate").values, ...candidate.values, plate_no: "业务版1" } }) }); await foreignUUID;
	assert.ok(state.unresolved); assert.equal(state.conflict, null);
	const found = plate.resolve(state); const plateSaved = pack("plate", { request_id: candidate.request_id, values: { ...blank("plate").values, ...candidate.values, plate_no: "业务版1" } }); calls.at(-1).resolve({ status: "found", archive: plateSaved }); await found;
	assert.equal(state.unresolved, false); assert.equal(state.controls.quantity.value, "-0"); assert.equal(state.controls.assessment_score.value, "0.12345678901234567890123456789"); calls.at(-1).resolve([]); await tick();
	const nextNumber = "JF-P-87654321-4321-4321-8321-cba987654321", freshArchive = blank("plate"); freshArchive.values.plate_no = nextNumber;
	const fresh = plate.newDraft(state); calls.at(-1).resolve(freshArchive); await fresh; assert.notEqual(state.requestId, candidate.request_id); assert.equal(state.archive.name, null); assert.equal(plate.isDirty(), false);
	const automaticCandidate = plate.payload(state); assert.equal(automaticCandidate.values.plate_no, nextNumber);
	const automaticSave = plate.save(state); calls.at(-1).reject(new Error("automatic number save unknown")); await automaticSave;
	const beforeFreshRetry = calls.length; await plate.newDraft(state); assert.equal(calls.length, beforeFreshRetry, "unknown save never allocates a replacement draft number");
	assert.equal(state.requestId, automaticCandidate.request_id); assert.equal(state.nativeControls.plate_no.get_value(), nextNumber);
	const automaticResolve = plate.resolve(state); calls.at(-1).resolve({ status: "found", archive: pack("plate", { request_id: automaticCandidate.request_id,
		values: { ...automaticCandidate.values } }) }); await automaticResolve; assert.equal(state.unresolved, false);
	assert.equal(state.nativeControls.plate_no.get_value(), nextNumber); calls.at(-1).resolve([]); await tick();
	state.controls.retired.checked = true; plate.freeze(state); assert.equal(state.nativeControls.retired_date.df.hidden, false); assert.equal(state.nativeControls.retired_date.df.reqd, true);
	state.controls.retired.checked = false; plate.freeze(state); assert.equal(state.nativeControls.retired_date.df.hidden, true); assert.equal("retired_date" in plate.payload(state).values, false);
	const close = dialog.hide(); await tick(); if (confirmation) confirmation.yes(); await close;

	// Native permission omission/readonly never becomes an editable or posted field.
	const readOnly = pack("plate"); readOnly.field_meta.plate_type.read_only = true; delete readOnly.field_meta.quantity; delete readOnly.values.quantity; readOnly.can_write = false;
	dialog = await opening(plate, readOnly); state = plate.editor; assert.ok(state.controls.plate_type.disabled); assert.ok(state.cells.quantity.hidden); assert.ok(state.saveButton.disabled); assert.ok(state.image.uploadButton.disabled); assert.equal("quantity" in plate.payload(state).values, false);
	await dialog.hide();
	const broken = plate.openEditor(); calls.at(-1).resolve(blank("mold")); await broken; assert.match(plate.status.textContent, /未能打开/); assert.equal(plate.editor, null);

	// Modeling reuses the same guard, but its real flat API has no record_type or values envelope.
	const modeling = new window.JifengToolArchive(new Element(), "modeling", bridge); modeling.companyScope = "Company A"; await modeling.companyControl.set_value("Company A");
	const modelList = modeling.query(); assert.equal(calls.at(-1).options.method, "jifeng_erp.plate_modeling.list_records"); assert.equal("record_type" in calls.at(-1).options.args, false); assert.equal("include_inactive" in calls.at(-1).options.args, false);
	const listOnly = flatModel(true); delete listOnly.field_meta; delete listOnly.can_write; calls.at(-1).resolve([listOnly]); await modelList; assert.equal(modeling.rows.length, 1); assert.equal(modeling.rows[0].can_write, false);
	const modelOpen = modeling.openEditor(); assert.equal(calls.at(-1).options.method, "jifeng_erp.plate_modeling.init_record"); assert.equal("record_type" in calls.at(-1).options.args, false); calls.at(-1).resolve(flatModel()); dialog = await modelOpen; state = modeling.editor;
	assert.equal(state.form.descendants("label").map((node) => node.textContent).join("/"), "编号/建模号/产品编号/单价/建模/日期/备注/复制");
	assert.ok(state.controls.model_date.readOnly); assert.equal(state.controls.modeler_text.type, "text"); assert.equal(state.controls.remarks.tag, "input"); assert.equal("maker" in state.nativeControls, false);
	assert.equal(state.nativeControls.plate.df.options, "Jifeng Plate"); assert.equal(state.nativeControls.expense_voucher.df.options, "Jifeng Expense Voucher"); assert.equal(state.nativeControls.expense_voucher.get_query().filters.docstatus, 1);
	assert.equal(state.imageCell.descendants("label")[0].textContent, "设计原图");
	state.controls.model_number.value = " 模号原文 "; state.controls.product_code.value = " 原产品 "; state.controls.reference_unit_price.value = "01.00000000000000000000001"; state.controls.modeler_text.value = " 外部建模人 ";
	await state.nativeControls.expense_voucher.set_value("EXP-1"); assert.match(state.linkStatus.textContent, /变更待保存/);
	const modelCandidate = modeling.payload(state), modelSave = modeling.save(state), modelWrite = calls.at(-1); assert.equal(modelWrite.options.method, "jifeng_erp.plate_modeling.save_record"); assert.equal(modelWrite.options.args.payload.model_number, " 模号原文 ");
	assert.equal(modelWrite.options.args.payload.reference_unit_price, "01.00000000000000000000001"); assert.equal("record_type" in modelWrite.options.args.payload, false); assert.equal("values" in modelWrite.options.args.payload, false);
	const modelSaved = flatModel(true, { ...modelCandidate.values, expense_voucher_state: "已提交" }); modelWrite.resolve(modelSaved); await tick();
	assert.equal(calls.at(-1).options.method, "jifeng_erp.plate_modeling.resolve_creation", "new response lacks UUID; exact receipt GET is required"); assert.equal(calls.at(-1).options.args.request_id, modelCandidate.request_id);
	calls.at(-1).resolve({ status: "found", archive: modelSaved }); await modelSave; assert.equal(state.unresolved, false); assert.match(state.linkStatus.textContent, /已提交/); calls.at(-1).resolve([]); await tick();
	assert.equal(state.controls.model_number.value, " 模号原文 ", "modeling Data is not trimmed"); assert.equal(state.controls.reference_unit_price.value, "01.00000000000000000000001");
	const modelImage = modeling.uploadImage(state); assert.equal("fieldname" in calls.at(-1).options.args, false); calls.at(-1).resolve([]); await modelImage; const modelUploader = uploads.at(-1);
	assert.equal(modelUploader.options.doctype, "Jifeng Plate Modeling"); assert.equal(modelUploader.options.fieldname, "design_image"); await modelUploader.upload_files();
	assert.deepEqual(modelUploader.nativeOptimizeFlags, [false], "design original uses the shared upload guard"); await modelUploader.upload_files(); assert.equal(modelUploader.nativeWrites, 1, "retry cannot upload the design original again");
	modelUploader.options.on_success({ name: "MODELFILE", doctype: "File", is_private: 1, attached_to_doctype: "Jifeng Plate Modeling", attached_to_name: "MODEL-1", attached_to_field: "design_image", file_url: "/private/files/model.png" }); modelUploader.dialog.hide();
	assert.equal(state.imageValue, "/private/files/model.png"); const modelImageSave = modeling.save(state); assert.equal(calls.at(-1).options.args.payload.design_image, "/private/files/model.png"); assert.equal("image" in calls.at(-1).options.args.payload, false);
	calls.at(-1).resolve(flatModel(true, { ...modelSaved, version: "2026-10-01T00:00:02", design_image: "/private/files/model.png", image_state: { state: "已上传", url: "/private/files/model.png?_file=MODELFILE" }, expense_voucher_state: "已取消" })); await modelImageSave;
	assert.equal(state.image.preview.descendants("img")[0].src, "/private/files/model.png?_file=MODELFILE"); assert.match(state.linkStatus.textContent, /已取消/); calls.at(-1).resolve([]); await tick(); await dialog.hide();
	const secondModelOpen = modeling.openEditor(); calls.at(-1).resolve(flatModel()); dialog = await secondModelOpen; state = modeling.editor;
	state.controls.model_number.value = "回执读取失败保留"; const secondCandidate = modeling.payload(state), secondSave = modeling.save(state), secondSaved = flatModel(true, { ...secondCandidate.values });
	calls.at(-1).resolve(secondSaved); await tick(); calls.at(-1).reject({ responseJSON: { exc_type: "PermissionError" } }); await secondSave;
	assert.equal(state.unresolved, true, "permission failure in follow-up GET cannot declare an answered POST failed"); assert.equal(state.candidate.request_id, secondCandidate.request_id);
	const secondResolve = modeling.resolve(state); calls.at(-1).resolve({ status: "found", archive: secondSaved }); await secondResolve; assert.equal(state.unresolved, false); calls.at(-1).resolve([]); await tick(); await dialog.hide();
	const bridgePrototype = window.JifengClassicBridge.prototype;
	const frames = new Map([ ["mold", { workbench: mold }], ["plate", { workbench: plate }] ]);
	assert.equal(bridgePrototype.canClose.call({ frames }, "plate"), true); assert.equal(bridgePrototype.closeBlock.call({ frames }, "plate"), "");
	const mounted = bridgePrototype.mount.call(bridge, new Element(), { toolArchive: "mold" }); assert.equal(mounted.workbench.recordType, "mold"); mounted.stop();
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_workspace_core.js"), "utf8"), context);
	window.ToolWorkspace = window.JifengERPWorkspace.Workspace;
	const dispatched = [], workspace = { formalBridge: { open: (spec) => dispatched.push(spec) } };
	context.window.ToolWorkspace.prototype.openPage.call(workspace, "模具录入", "模具"); context.window.ToolWorkspace.prototype.openPage.call(workspace, "版种录入", "版种");
	assert.equal(dispatched[0].toolArchive, "mold"); assert.equal(dispatched[1].toolArchive, "plate"); assert.equal(dispatched[0].doctype, "Jifeng Mold"); assert.equal(dispatched[1].doctype, "Jifeng Plate");
	context.window.ToolWorkspace.prototype.openPage.call(workspace, "版种建模", "版种"); context.window.ToolWorkspace.prototype.openPage.call(workspace, "建模", "版种");
	assert.equal(dispatched[2].toolArchive, "modeling"); assert.equal(dispatched[3].toolArchive, "modeling");
	context.window.ToolWorkspace.prototype.openPage.call(workspace, "版种建模查询", "版种"); context.window.ToolWorkspace.prototype.openPage.call(workspace, "建模查询", "版种");
	assert.equal(dispatched[4].route.join("/"), "query-report/Jifeng Plate Modeling Catalog"); assert.equal(dispatched[5].route.join("/"), "query-report/Jifeng Plate Modeling Catalog");
	mold.destroy(); plate.destroy(); modeling.destroy(); assert.equal(listeners.size, 0);
	console.log("Tool/modeling DOM/protocol PASS: actual menus+aliases, old11/10/8 fields, changed-only raw text, type+UUID/version and flat receipt adapter, unknown read-only adoption, native X upload lifecycle, original-image flags and one-upload guard, exact private attachment.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
