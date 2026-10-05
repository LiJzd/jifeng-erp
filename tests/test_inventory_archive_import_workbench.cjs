// Real shared importer + list: no browser, network, file uploads or live DB.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), crypto = require("node:crypto");
const sources = ["jifeng_inventory_catalog.js", "jifeng_inventory_archive_import.js", "jifeng_inventory_archive_list.js"]
	.map(name => fs.readFileSync("jifeng_erp/public/js/" + name, "utf8"));
const settle = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) { for (let attempts = 0; attempts < 100 && !predicate(); attempts++) await settle(); assert.ok(predicate(), "expected async operation became observable"); }
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this.attributes = {}; this.style = {}; this.value = "";
		this.scrollTop = 0; this.scrollLeft = 0; this.classList = { add() {} }; this._text = ""; }
	get firstElementChild() { return this.children[0]; }
	append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
	prepend(...children) { for (const child of children) child.parent = this; this.children.unshift(...children); }
	after(child) { child.parent = this.parent; this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, child); }
	replaceChildren(...children) { this._text = ""; this.children = []; this.append(...children); }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw Error("Import source text must never execute as HTML"); }
	setAttribute(key, value) { this.attributes[key] = value; }
	addEventListener(key, action) { this.events[key] = action; }
	click() { this.clicked = true; }
	remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
}
function shared() {
	const values = new Map(), locks = new Map();
	return { values, storage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
		locks: { async request(key, _options, action) {
			const previous = locks.get(key) || Promise.resolve(), gate = deferred(); locks.set(key, gate.promise);
			await previous; try { return await action(); } finally { gate.resolve(); }
		} } };
}
function environment(common = shared(), user = "qa@example.invalid") {
	const env = { calls: [], dialogs: [], messages: [], listeners: new Map(), fetches: [], revoked: [] };
	const window = { addEventListener(type, fn) { if (!env.listeners.has(type)) env.listeners.set(type, new Set()); env.listeners.get(type).add(fn); },
		removeEventListener(type, fn) { env.listeners.get(type)?.delete(fn); } };
	class Dialog {
		constructor(options) {
			this.fields_dict = Object.fromEntries(options.fields.map(df => [df.fieldname,
				{ df: { ...df }, value: null, cached: null, input: null, get_value() { return this.value; },
					get_input_value() { return this.title_value_map?.[this.input] ?? this.input; }, $wrapper: { get: () => new Element() } }]));
			this.primary = { disabled: false, prop(_key, value) { this.disabled = value; return this; } };
			this.$wrapper = { addClass() {}, modal() {}, on() {} }; this.actions = new Map(); env.dialogs.push(this);
		}
		get_primary_btn() { return this.primary; }
		set_primary_action(label, fn) { this.primaryLabel = label; this.primaryAction = fn; }
		add_custom_action(label, fn) { this.actions.set(label, fn); }
		get_close_btn() { return { removeAttr() { return this; }, on() { return this; } }; }
		async set_values(values) { await Promise.resolve(); for (const [key, value] of Object.entries(values)) Object.assign(this.fields_dict[key], { value, cached: value, input: value }); }
		get_value(key) { return this.fields_dict[key]?.value; }
		get_values() {
			if (Object.values(this.fields_dict).some(control => control.df.reqd && !control.value)) return null;
			return Object.fromEntries(Object.entries(this.fields_dict).map(([key, control]) => [key, control.value]));
		}
		set_df_property(key, property, value) {
			const control = this.fields_dict[key]; control.df[property] = value;
			if (property === "read_only" && value) control.value = control.input = control.cached; // Native cached-value refresh.
		}
		show() { this.shown = true; }
		hide() { this.shown = false; this.on_hide?.(); }
	}
	const frappe = { session: { user }, msgprint: text => env.messages.push(text), ui: { Dialog, form: { make_control({ df }) {
		return { value: null, input: null, get_value() { return this.value; }, get_input_value() { return this.title_value_map?.[this.input] ?? this.input; },
			async set_value(value) { this.value = this.input = value; await df.onchange?.(); }, $input: { on() {} } };
	} } }, call(options) { const pending = deferred(); env.calls.push({ options, resolve: message => pending.resolve({ message }), reject: pending.reject }); return pending.promise; } };
	const context = { window, document: { body: new Element("body"), createElement: tag => new Element(tag) }, frappe, __: text => text,
		localStorage: common.storage, navigator: { locks: common.locks }, crypto: crypto.webcrypto, TextEncoder, Uint8Array, URLSearchParams, AbortController,
		btoa: value => Buffer.from(value, "binary").toString("base64"), setTimeout: fn => fn(),
		fetch(url, options) { const pending = deferred(); env.fetches.push({ url, options, ...pending }); return pending.promise; },
		URL: { createObjectURL: () => "blob:model", revokeObjectURL: value => env.revoked.push(value) } };
	for (const source of sources) vm.runInNewContext(source, context);
	const bridge = { inventoryEditor: { active: new Map(), loadingOwners: new Set() } };
	env.list = kind => new window.JifengInventoryArchiveList(new Element(), kind || "物料", bridge);
	Object.assign(env, { window, context, common }); return env;
}
const columns = [{ fieldname: "display_name", label: "名称" }, { fieldname: "display_uom", label: "单位" }, { fieldname: "notes", label: "备注" }];
function init(state, extra = {}) { return { company: state.company, kind: state.kind, columns, max_file_bytes: 1048576, max_rows: 100,
	can_create_item: true, can_select_item_group: true, can_select_stock_uom: true, ...extra }; }
function requestID(batch, index) {
	const bytes = crypto.createHash("sha256").update("jifeng-inventory-import:v1:" + batch + ":" + index).digest().subarray(0, 16);
	bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
	const hex = bytes.toString("hex"); return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}
function preview(state, count = 2, errors = []) {
	const metadata = { version: 1, kind: state.kind, company: state.company, batch_id: state.batchId, row_count: count,
		row_digests: Array.from({ length: count }, (_value, index) => crypto.createHash("sha256").update(String(index)).digest("hex")) };
	return { metadata: errors.length ? null : metadata, preview_digest: errors.length ? null : "d".repeat(64), row_count: count, errors, can_commit: !errors.length,
		rows: Array.from({ length: count }, (_value, index) => ({ row_index: index, sheet_row: index + 2, request_id: requestID(state.batchId, index), valid: !errors.some(error => error.row_index === index),
			profile: { display_name: "<img src=x onerror=alert(1)>" + index, display_uom: "档案单位原文", notes: "=literal" } })) };
}
function receipt(state, status = "found") {
	return { status, metadata: state.metadata, preview_digest: state.previewDigest, row_count: state.metadata.row_count,
		receipts: Array.from({ length: state.metadata.row_count }, (_value, index) => ({ row_index: index, request_id: requestID(state.metadata.batch_id, index),
			status: status === "found" ? "found" : "missing", name: status === "found" ? "P" + index : null, item_code: status === "found" ? "I" + index : null })) };
}
async function open(env, list = env.list(), file = { name: "本系统.xlsx", size: 3, arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer }) {
	await list.companyControl.set_value("JF"); list.openImport(); const importer = list.archiveImporter, picker = importer.picker;
	assert.equal(picker.type, "file"); assert.equal(picker.accept, ".xlsx"); assert.equal(picker.multiple, false); assert.equal(picker.clicked, true);
	picker.files = [file]; const running = picker.events.change(); await settle(); const state = importer.state;
	if (state) { const call = env.calls.at(-1); assert.equal(call.options.type, "GET"); assert.equal(call.options.method, "jifeng_erp.inventory_archive_import.init_import"); call.resolve(init(state)); }
	await running; return { list, importer, state };
}
async function prepared(env, info, count = 2, errors = []) {
	const { importer, state } = info;
	// Values typed after Frappe's cached baseline: implementation must hydrate them before freezing.
	state.dialog.fields_dict.item_group.value = state.dialog.fields_dict.item_group.input = "Materials";
	state.dialog.fields_dict.stock_uom.value = state.dialog.fields_dict.stock_uom.input = "Nos";
	const pending = importer.preview(state); await settle(); const call = env.calls.at(-1);
	assert.equal(call.options.method, "jifeng_erp.inventory_archive_import.preview_import"); assert.equal(call.options.type, "POST");
	assert.equal(call.options.args.item_group, "Materials"); assert.equal(call.options.args.stock_uom, "Nos");
	call.resolve(preview(state, count, errors)); await pending; return state;
}
async function confirmedBatchAndFilters() {
	const env = environment(), info = await open(env); await prepared(env, info, 100);
	const { state, importer, list } = info;
	assert.equal(state.grid.children[0].children[1].children.length, 100, "all 100 rows remain visible, not truncated");
	assert.match(state.grid.textContent, /<img src=x onerror=alert\(1\)>99/); assert.match(state.grid.textContent, /档案单位原文/);
	const stored = JSON.parse(env.common.values.get(importer.storageKey));
	assert.deepEqual(Object.keys(stored).sort(), ["metadata", "phase", "preview_digest"]);
	assert.deepEqual(Object.keys(stored.metadata).sort(), ["batch_id", "company", "kind", "row_count", "row_digests", "version"]);
	assert.doesNotMatch(JSON.stringify(stored), /content_base64|filename|Materials|Nos|onerror|=literal/);
	list.search.value = "保持关键词"; list.search.events.input(); list.statusChoice.value = "停用"; list.statusChoice.events.change();
	assert.equal(state.dialog.primaryLabel, "确认导入"); const running = importer.commit(state); await settle(); const call = env.calls.at(-1);
	assert.equal(call.options.method, "jifeng_erp.inventory_archive_import.commit_import"); assert.equal(call.options.type, "POST");
	assert.equal(JSON.parse(env.common.values.get(importer.storageKey)).phase, "dispatched");
	await importer.commit(state); assert.equal(env.calls.at(-1), call, "duplicate click creates no second commit POST");
	call.resolve(receipt(state)); await running;
	assert.equal(env.common.values.has(importer.storageKey), false); assert.equal(state.completed, true); assert.equal(state.dialog.primary.disabled, true);
	const query = env.calls.at(-1); assert.equal(query.options.method, "jifeng_erp.inventory_archive_list.query_archives");
	assert.equal(query.options.args.search, "保持关键词"); assert.equal(query.options.args.status, "停用");
	query.resolve({ ...query.options.args, readonly: true, can_write: false, rows: [], total: 0, total_pages: 0, page: 1 }); await settle();
	await state.dialog.hide(); assert.equal(importer.state, null); assert.equal(list.getCloseBlock(), ""); assert.equal(list.destroy(), true);
}
async function rowErrorsAndUnsentCancel() {
	const env = environment(), bad = await open(env); await prepared(env, bad, 2, [{ row_index: 1, sheet_row: 3, message: "第3行供应商不可读 <script>原文</script>" }]);
	assert.match(bad.state.grid.textContent, /第3行供应商不可读 <script>原文<\/script>/); assert.equal(bad.state.preview, null);
	const calls = env.calls.length; await bad.importer.commit(bad.state); assert.equal(env.calls.length, calls); assert.equal(env.common.values.size, 0);
	await bad.state.dialog.hide(); assert.equal(bad.importer.state, null);
	const clean = await open(env); await prepared(env, clean); assert.equal(env.common.values.size, 1);
	await clean.state.dialog.hide(); assert.equal(env.common.values.size, 0, "explicit cancellation only clears a prepared, never dispatched entry");
	assert.equal(env.calls.filter(call => call.options.method.endsWith(".commit_import")).length, 0);
}
async function unknownAcrossABAndCompany() {
	const common = shared(), a = environment(common), info = await open(a); await prepared(a, info);
	const run = info.importer.commit(info.state); await settle(); const commit = a.calls.at(-1); commit.reject(Error("response lost")); await run;
	assert.equal(info.state.unresolved, true); assert.match(info.list.getCloseBlock(), /尚未确认/); assert.equal(info.list.destroy(), false);
	assert.equal(info.list.disposed, false, "forced owner destruction cannot lose an unconfirmed commit");
	await info.state.dialog.hide(); assert.equal(info.state.closed, false); const before = a.calls.length; await info.importer.commit(info.state); assert.equal(a.calls.length, before);
	const b = environment(common), list = b.list(); await list.companyControl.set_value("OTHER"); const recovering = list.openImport(); await until(() => b.calls.length);
	const state = list.archiveImporter.state, query = b.calls.at(-1);
	assert.equal(list.archiveImporter.picker, null, "B first reads the shared pending batch instead of choosing another file");
	assert.equal(query.options.method, "jifeng_erp.inventory_archive_import.resolve_import"); assert.equal(query.options.type, "GET"); assert.equal(query.options.args.company, "JF");
	assert.equal(Object.hasOwn(query.options.args, "content_base64"), false); assert.equal(query.options.args.row_digests.length, 2);
	query.resolve(receipt(state, "not_found")); await recovering;
	assert.equal(state.unresolved, true, "not_found never permits another commit after an unknown result"); assert.equal(state.dialog.primary.disabled, true);
	await state.dialog.hide(); assert.equal(state.closed, false); assert.equal(common.values.size, 1);
	const resolution = list.archiveImporter.resolve(state); await settle(); b.calls.at(-1).resolve(receipt(state)); await resolution;
	assert.equal(common.values.size, 0); assert.equal(state.completed, true);
	assert.equal(b.calls.filter(call => call.options.method.endsWith(".query_archives")).length, 0, "old-company receipt cannot change the current company's list");
	assert.equal(a.calls.filter(call => call.options.method.endsWith(".commit_import")).length + b.calls.filter(call => call.options.method.endsWith(".commit_import")).length, 1);
	await state.dialog.hide(); const ownResolve = info.importer.resolve(info.state); await settle(); a.calls.at(-1).resolve(receipt(info.state)); await ownResolve;
	assert.equal(info.state.unresolved, false, "A separately confirms its own lost response through read-only recovery");
}
async function latePreparationAndPreview() {
	const env = environment(), list = env.list(); await list.companyControl.set_value("JF"); list.openImport(); const picker = list.archiveImporter.picker;
	picker.files = [{ name: "a.xlsx", size: 1, arrayBuffer: async () => new Uint8Array([1]).buffer }]; const preparing = picker.events.change(); await settle();
	const pending = env.calls.at(-1); await list.companyControl.set_value("OTHER"); const status = list.status.textContent;
	pending.resolve(init(list.archiveImporter.state)); await preparing;
	assert.equal(list.archiveImporter.state, null); assert.equal(list.status.textContent, status); assert.equal(env.common.values.size, 0);
	const normal = await open(env); const state = normal.state;
	await state.dialog.set_values({ item_group: "Materials", stock_uom: "Nos" }); const previewing = normal.importer.preview(state); await settle(); const call = env.calls.at(-1);
	await normal.list.companyControl.set_value("OTHER"); call.resolve(preview(state)); await previewing;
	assert.equal(state.preview, null); assert.equal(env.common.values.size, 0); assert.equal(state.dialog.primary.disabled, true);
	await state.dialog.hide(); assert.equal(normal.importer.state, null);
}
async function competingPreparedWindowsAndReceiptMismatch() {
	const common = shared(), a = environment(common), b = environment(common), first = await open(a), second = await open(b);
	await prepared(a, first); await prepared(b, second);
	assert.equal(second.state.preview, null, "another page cannot replace an existing prepared batch");
	assert.equal(JSON.parse(common.values.get(first.importer.storageKey)).metadata.batch_id, first.state.batchId);
	const run = first.importer.commit(first.state); await settle(); const call = a.calls.at(-1), wrong = receipt(first.state);
	wrong.receipts[0].request_id = crypto.randomUUID(); call.resolve(wrong); await run;
	assert.equal(first.state.unresolved, true); assert.equal(common.values.size, 1, "mismatched receipt cannot erase a dispatched batch");
	const checks = first.importer.resolve(first.state); await settle(); a.calls.at(-1).resolve({ ...receipt(first.state, "not_found"), status: "partial" }); await checks;
	assert.equal(first.state.unresolved, true); assert.match(first.state.status.textContent, /仅查到部分/);
}
async function invalidFilesAndTemplate() {
	for (const file of [{ name: "a.csv", size: 1 }, { name: "a.xlsx", size: 0 }, { name: "a.xlsx", size: 1048577 }]) {
		const env = environment(), info = await open(env, env.list(), file); assert.equal(info.state, null); assert.equal(env.calls.length, 0); assert.match(env.messages.at(-1), /1 MiB/);
	}
	const env = environment(), list = env.list("材料"); await list.companyControl.set_value("JF");
	const importer = list.importer(), pending = importer.downloadTemplate(); const request = env.fetches.at(-1);
	assert.equal(request.options.method, "GET"); assert.deepEqual(Object.fromEntries(new URLSearchParams(request.url.split("?")[1])), { company: "JF", kind: "材料" });
	request.resolve({ ok: true, headers: { get: () => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }, blob: async () => ({ size: 100 }) }); await pending;
	assert.equal(env.revoked.at(-1), "blob:model");
	const noLocks = environment(), info = await open(noLocks); delete noLocks.context.navigator.locks; await prepared(noLocks, info);
	assert.equal(info.state.preview, null); assert.equal(noLocks.common.values.size, 0); assert.equal(noLocks.calls.filter(call => call.options.method.endsWith(".commit_import")).length, 0);
}
async function exactNativeLinkScope() {
	const start = environment(), list = start.list(); await list.companyControl.set_value("JF"); list.companyControl.input = "OTHER-unconfirmed";
	list.openImport(); assert.equal(list.archiveImporter.picker, null); assert.equal(start.calls.length, 0); assert.equal(list.companyControl.input, "OTHER-unconfirmed");
	await list.archiveImporter.downloadTemplate(); assert.equal(start.fetches.length, 0, "typed company cannot download for the old selected company");
	for (const field of ["item_group", "stock_uom"]) {
		const env = environment(), info = await open(env); await info.state.dialog.set_values({ item_group: "Materials", stock_uom: "Nos" });
		info.state.dialog.fields_dict[field].input = "OTHER-unconfirmed"; const before = env.calls.length; await info.importer.preview(info.state);
		assert.equal(env.calls.length, before, "typed " + field + " cannot preview a previous selected ID");
		assert.equal(info.state.dialog.fields_dict[field].input, "OTHER-unconfirmed", "unconfirmed input remains untouched");
	}
	const previewEnv = environment(), previewInfo = await open(previewEnv); await previewInfo.state.dialog.set_values({ item_group: "Materials", stock_uom: "Nos" });
	previewInfo.list.companyControl.input = "OTHER-unconfirmed"; const beforePreview = previewEnv.calls.length; await previewInfo.importer.preview(previewInfo.state);
	assert.equal(previewEnv.calls.length, beforePreview); assert.equal(previewInfo.list.companyControl.input, "OTHER-unconfirmed");
	const commitEnv = environment(), commitInfo = await open(commitEnv); await prepared(commitEnv, commitInfo);
	commitInfo.list.companyControl.input = "OTHER-unconfirmed"; const beforeCommit = commitEnv.calls.length; await commitInfo.importer.commit(commitInfo.state);
	assert.equal(commitEnv.calls.length, beforeCommit, "typed company cannot commit the old selected company");
	const late = environment(), lateList = late.list(); await lateList.companyControl.set_value("JF"); lateList.openImport(); const input = lateList.archiveImporter.picker;
	input.files = [{ name: "a.xlsx", size: 1, arrayBuffer: async () => new Uint8Array([1]).buffer }]; const running = input.events.change(); await settle();
	lateList.companyControl.input = "OTHER-unconfirmed"; late.calls.at(-1).resolve(init(lateList.archiveImporter.state)); await running;
	assert.equal(lateList.archiveImporter.state, null, "silent unconfirmed company drops late init response");
	const titles = environment(), titleInfo = await open(titles);
	titleInfo.list.companyControl.title_value_map = { "公司标题": "JF" }; titleInfo.list.companyControl.input = "公司标题";
	for (const [field, value, title] of [["item_group", "Materials", "分组标题"], ["stock_uom", "Nos", "单位标题"]]) {
		const control = titleInfo.state.dialog.fields_dict[field]; control.value = value; control.input = title; control.title_value_map = { [title]: value };
	}
	const titlePreview = titleInfo.importer.preview(titleInfo.state); await settle(); const titleCall = titles.calls.at(-1);
	assert.equal(titleCall.options.args.company, "JF"); assert.equal(titleCall.options.args.item_group, "Materials"); assert.equal(titleCall.options.args.stock_uom, "Nos");
	titleCall.resolve(preview(titleInfo.state)); await titlePreview; assert.equal(titleInfo.state.preview.can_commit, true);
	const reordered = receipt(titleInfo.state); reordered.metadata = Object.fromEntries(Object.entries(reordered.metadata).reverse());
	assert.equal(titleInfo.importer.validReceipt(reordered, titleInfo.state), true, "JSON key order is not batch identity");
}
async function storageFailureCanCloseWithoutTouchingOtherBatch() {
	for (const failure of ["getItem", "setItem", "locks"]) {
		const common = shared(), env = environment(common), info = await open(env), other = environment(common), otherInfo = await open(other);
		if (failure !== "setItem") await prepared(other, otherInfo);
		let removals = 0; const originalRemove = common.storage.removeItem;
		const originalSet = common.storage.setItem;
		common.storage.removeItem = key => { removals++; return originalRemove(key); };
		if (failure === "locks") delete env.context.navigator.locks;
		else common.storage[failure] = () => { throw Error(failure === "setItem" ? "QuotaExceededError" : "SecurityError"); };
		await prepared(env, info);
		assert.ok(info.state.metadata, "a valid parsed preview precedes the reservation failure");
		assert.equal(info.state.preview, null); assert.equal(info.state.dispatched, false);
		if (failure === "setItem") {
			// This window already failed quota; another page later reserves its own batch.
			common.storage.setItem = originalSet; await prepared(other, otherInfo);
		}
		const otherEntry = common.values.get(info.importer.storageKey);
		const calls = env.calls.length; await info.importer.commit(info.state); assert.equal(env.calls.length, calls, "reservation failure cannot dispatch commit");
		await info.state.dialog.hide(); assert.equal(info.state.closed, true); assert.equal(info.importer.state, null);
		assert.equal(common.values.get(info.importer.storageKey), otherEntry, "closing failed reservation preserves the other page's batch");
		assert.equal(removals, 0, "failed preview close never deletes a global pending entry");
		assert.equal(info.list.destroy(), true);
	}
	const env = environment(), info = await open(env); env.common.storage.setItem = () => { throw Error("QuotaExceededError"); };
	await prepared(env, info); assert.equal(info.list.destroy(), true, "forced destroy may discard a never-reserved, never-dispatched preview");
	assert.equal(info.state.closed, true); assert.equal(info.list.disposed, true);
}
async function completedCloseIgnoresStorageAndOtherBatch() {
	const common = shared(), env = environment(common), info = await open(env); await prepared(env, info);
	const commit = info.importer.commit(info.state); await settle(); env.calls.at(-1).resolve(receipt(info.state)); await commit;
	assert.equal(info.state.completed, true); assert.equal(common.values.size, 0);
	const refresh = env.calls.at(-1); refresh.resolve({ ...refresh.options.args, readonly: true, can_write: false, rows: [], total: 0, total_pages: 0, page: 1 }); await settle();
	const other = environment(common), otherInfo = await open(other); await prepared(other, otherInfo);
	const otherEntry = common.values.get(info.importer.storageKey); let reads = 0, removals = 0;
	common.storage.getItem = () => { reads++; throw Error("SecurityError"); };
	common.storage.removeItem = () => { removals++; throw Error("Cannot remove another page's pending batch"); };
	await info.state.dialog.hide(); assert.equal(info.state.closed, true); assert.equal(info.importer.state, null);
	assert.equal(reads, 0, "closing confirmed success does not read batch storage"); assert.equal(removals, 0);
	assert.equal(common.values.get(info.importer.storageKey), otherEntry, "confirmed old window cannot affect another page's new batch");
	assert.equal(info.list.destroy(), true);
}
async function main() {
	await confirmedBatchAndFilters(); await rowErrorsAndUnsentCancel(); await unknownAcrossABAndCompany(); await latePreparationAndPreview();
	await competingPreparedWindowsAndReceiptMismatch(); await invalidFilesAndTemplate(); await exactNativeLinkScope(); await storageFailureCanCloseWithoutTouchingOtherBatch();
	await completedCloseIgnoresStorageAndOtherBatch();
	console.log("Inventory import: single XLSX chooser, 100-row text preview/errors, native hydration, metadata-only shared pending, one commit, lost/mismatched/partial recovery, current-filter refresh and lifecycle guards passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
