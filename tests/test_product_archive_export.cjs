// Actual product frontend under DOM/protocol models. No browser, HTTP or native DB.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this.value = ""; this.checked = false;
		this.classList = { add() {} }; this.attributes = {}; this.scrollTop = 0; this.scrollLeft = 0; this._text = ""; }
	append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
	replaceChildren(...children) { this._text = ""; this.children = []; this.append(...children); }
	set textContent(text) { this._text = String(text); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Source HTML execution forbidden"); }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, action) { this.events[name] = action; }
	click() { downloads.push({ filename: this.download, href: this.href }); }
	remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const requests = [], rpc = [], downloads = [], urls = [], revoked = [], messages = [], listeners = new Map();
const window = { addEventListener(name, action) { listeners.set(action, name); }, removeEventListener(_, action) { listeners.delete(action); } };
const document = { body: new Element("body"), createElement: tag => new Element(tag) };
const frappe = { msgprint: message => messages.push(message),
	call(options) { const pending = deferred(); rpc.push({ options, ...pending }); return pending.promise; },
	ui: { form: { make_control({ df }) { return { df, value: null, get_value() { return this.value; },
		async set_value(value) { this.value = value; await df.onchange?.(); },
		$input: { events: {}, on(events, action) { for (const event of events.split(" ")) this.events[event.split(".")[0]] = action; } } }; } } } };
const context = { window, document, frappe, __: value => value, AbortController, URLSearchParams,
	fetch(url, options) { const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise; },
	URL: { createObjectURL(blob) { const url = "blob:" + urls.length; urls.push({ blob, url }); return url; }, revokeObjectURL(url) { revoked.push(url); } },
	setTimeout(action) { action(); } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_product_archive.js"), "utf8"), context);
const MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
function response(blob = Promise.resolve({ size: 100 }), type = MIME, ok = true) {
	return { ok, headers: { get() { return type; } }, blob() { return blob; } };
}
async function setup() {
	const list = new window.JifengProductArchive(new Element());
	assert.ok(list.exportButton.disabled, "constructor does not invent a company"); assert.ok(list.cancelExportButton.hidden);
	await list.companyControl.set_value("JF"); assert.equal(list.exportButton.disabled, false); return list;
}
async function main() {
	const list = await setup(); list.search.value = "=literal"; list.includeInactive.checked = true;
	list.page = 7; list.rows = [{ saved: true }]; list.total = 700; list.totalPages = 7;
	list.scroller.scrollTop = 123; list.scroller.scrollLeft = 234;
	const rows = list.rows, beforeRpc = rpc.length, sequence = list.revision, success = list.exportArchives(), request = requests.at(-1);
	assert.equal(request.url.split("?")[0], "/api/method/jifeng_erp.product_archive_export.export_archives");
	const params = new URLSearchParams(request.url.split("?")[1]);
	assert.deepEqual(Object.fromEntries(params), { company: "JF", search: "=literal", include_inactive: "1" });
	assert.equal(params.has("page"), false); assert.equal(params.has("page_size"), false);
	assert.equal(request.options.method, "GET"); assert.equal(request.options.credentials, "same-origin");
	assert.ok(list.exportButton.disabled); assert.equal(list.cancelExportButton.hidden, false);
	await list.exportArchives(); assert.equal(requests.at(-1), request, "duplicate export never sends another GET");
	request.resolve(response()); await success;
	assert.equal(downloads.at(-1).filename, "产品档案.xlsx"); assert.equal(revoked.at(-1), downloads.at(-1).href);
	assert.equal(document.body.children.length, 0); assert.equal(list.exportButton.disabled, false); assert.ok(list.cancelExportButton.hidden);
	assert.equal(list.page, 7); assert.equal(list.rows, rows); assert.equal(list.total, 700); assert.equal(list.revision, sequence);
	assert.equal(list.scroller.scrollTop, 123); assert.equal(list.scroller.scrollLeft, 234); assert.equal(rpc.length, beforeRpc);
	assert.match(list.status.textContent, /全部已保存/); assert.match(list.exportButton.title, /旧导出模板尚未核实/);
	for (const [name, action] of [
		["keyword", value => { value.search.value = "changed"; value.search.events.input(); }],
		["raw keyword", value => { value.search.value = "uncommitted"; }],
		["inactive filter", value => { value.includeInactive.checked = true; return value.includeInactive.events.change(); }],
		["raw company", value => { value.companyControl.value = "OTHER"; }],
		["company ABA", async value => { await value.companyControl.set_value("OTHER"); await value.companyControl.set_value("JF"); }],
		["page query", value => value.query(2)],
		["page size", value => { value.pageSizeControl.value = "25"; return value.pageSizeControl.events.change(); }],
		["destroy", value => value.destroy()],
		["opening", value => { value.opening = true; }],
		["owned modal", value => { value.editor = { closed: false }; }],
		["leave and return", value => { value.setVisible(false); value.setVisible(true); }],
		["cancel", value => value.cancelExportButton.events.click()],
	]) {
		const value = await setup(), running = value.exportArchives(), pending = requests.at(-1), count = downloads.length;
		// Scope/filter handlers may deliberately launch a list query. Resolve only that modeled read.
		const before = rpc.length, actionRun = action(value); await Promise.resolve();
		if (rpc.length > before) { const read = rpc.at(-1); read.resolve({ message: {
			company: read.options.args.company, search: read.options.args.search, include_inactive: read.options.args.include_inactive,
			requested_page: read.options.args.page, page: 1, page_size: read.options.args.page_size, total: 0, total_pages: 0, rows: [] } }); }
		await actionRun; const status = value.status.textContent;
		pending.resolve(response()); await running;
		assert.equal(downloads.length, count, name + " refuses stale download");
		assert.equal(value.status.textContent, status, name + " preserves current scope status");
	}
	for (const action of [value => value.cancelExport(), value => value.setVisible(false), value => { value.search.value = "new"; value.search.events.input(); }]) {
		const value = await setup(), blob = deferred(), run = value.exportArchives(), pending = requests.at(-1), count = downloads.length;
		pending.resolve(response(blob.promise)); await Promise.resolve(); await Promise.resolve();
		action(value); const status = value.status.textContent; blob.resolve({ size: 200 }); await run;
		assert.ok(pending.options.signal.aborted); assert.equal(downloads.length, count); assert.equal(value.status.textContent, status);
	}
	for (const lateFailure of [false, true]) {
		const value = await setup(), old = value.exportArchives(), older = requests.at(-1);
		value.setVisible(false); value.setVisible(true); const next = value.exportArchives(), newer = requests.at(-1), owner = value.exportRequest;
		if (lateFailure) older.reject(new Error("late synthetic failure")); else older.resolve(response()); await old;
		assert.equal(value.exportRequest, owner, "old finally preserves new export owner"); assert.equal(newer.options.signal.aborted, false);
		newer.resolve(response()); await next; assert.equal(value.exportRequest, null);
	}
	for (const reply of [response(Promise.resolve({ size: 100 }), "application/json"), response(Promise.resolve({ size: 100 }), MIME, false), response(Promise.resolve({ size: 0 }))]) {
		const value = await setup(), run = value.exportArchives(), count = downloads.length; requests.at(-1).resolve(reply); await run;
		assert.equal(downloads.length, count); assert.match(value.status.textContent, /导出未完成/); assert.equal(value.exportButton.disabled, false);
	}
	for (const state of [{}, { busy: true }, { unresolved: true }, { uploadUnresolved: true }, { upload: { open: true } }, { confirming: true }]) {
		const value = await setup(), candidate = { immutable: "synthetic" }, editor = { ...state, closed: false, candidate };
		value.editor = editor; const count = requests.length, beforeCalls = rpc.length;
		value.freezeExport(); assert.ok(value.exportButton.disabled); await value.exportArchives(); assert.equal(requests.length, count);
		const block = value.getCloseBlock(); value.setVisible(false); value.setVisible(true);
		assert.equal(value.editor, editor); assert.equal(editor.candidate, candidate); assert.equal(value.getCloseBlock(), block);
		assert.equal(rpc.length, beforeCalls, "visibility never repeats a save, upload or resolve RPC");
	}
	const opening = await setup(), pending = opening.exportArchives(), exportCall = requests.at(-1), count = downloads.length;
	const open = opening.openEditor("EXACT-PROFILE"); assert.ok(exportCall.options.signal.aborted);
	assert.equal(rpc.at(-1).options.method, "jifeng_erp.product_archive.read_archive");
	assert.equal(rpc.at(-1).options.args.name, "EXACT-PROFILE"); assert.ok(opening.exportButton.disabled);
	rpc.at(-1).reject(new Error("synthetic read failure")); await open;
	exportCall.resolve(response()); await pending; assert.equal(downloads.length, count);
	const noCompany = new window.JifengProductArchive(new Element()), noCompanyCount = requests.length;
	await noCompany.exportArchives(); assert.equal(requests.length, noCompanyCount); assert.match(messages.at(-1), /请选择公司/);
	console.log("Product export models passed: whole saved filter GET, literal template notice, no page/list mutation, visibility/company ABA/filter/owner/late blob cancellation and editor unknown preservation.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
