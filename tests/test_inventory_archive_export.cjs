// Actual inventory browse + archive subclass: no browser, network, or live DB.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = {}; this.value = ""; this.attributes = {};
		this.scrollTop = 0; this.scrollLeft = 0; this.classList = { add() {} }; this._text = ""; }
	get firstElementChild() { return this.children[0]; }
	append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
	prepend(...children) { for (const child of children) child.parent = this; this.children.unshift(...children); }
	after(child) { child.parent = this.parent; this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, child); }
	replaceChildren(...children) { this.children = []; this._text = ""; this.append(...children); }
	set textContent(text) { this._text = String(text); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("No source HTML execution"); }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, fn) { this.events[name] = fn; }
	click() { downloads.push({ filename: this.download, href: this.href }); }
	remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
}
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const downloads = [], urls = [], revoked = [], requests = [], rpc = [], edits = [], messages = [];
const window = {}, document = { body: new Element("body"), createElement: tag => new Element(tag) };
const bridge = { inventoryEditor: { active: new Map(), loadingOwners: new Set() }, openInventory(...args) { edits.push(args); return Promise.resolve(); } };
const frappe = { msgprint: text => messages.push(text), call(options) { const pending = deferred(); rpc.push({ options, ...pending }); return pending.promise; },
	ui: { form: { make_control({ df }) { return { value: null, get_value() { return this.value; },
		async set_value(value) { this.value = value; await df.onchange?.(); }, $input: { on() {} } }; } } } };
const context = { window, document, frappe, __: value => value, AbortController, URLSearchParams,
	fetch(url, options) { const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise; },
	URL: { createObjectURL(blob) { const value = "blob:" + urls.length; urls.push({ blob, value }); return value; }, revokeObjectURL(url) { revoked.push(url); } },
	setTimeout(fn) { fn(); } };
for (const file of ["jifeng_inventory_catalog.js", "jifeng_inventory_archive_list.js"])
	vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/" + file, "utf8"), context);
const MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
function response(blob = Promise.resolve({ size: 100 }), contentType = MIME, ok = true) {
	return { ok, headers: { get() { return contentType; } }, blob() { return blob; } };
}
async function setup(kind = "物料") {
	const list = new window.JifengInventoryArchiveList(new Element(), kind, bridge);
	await list.companyControl.set_value("JF"); return list;
}
async function main() {
	const list = await setup(); list.search.value = "=literal"; list.statusChoice.value = "停用"; list.statusChoice.events.change();
	list.page = 7; list.scroller.scrollTop = 37; list.scroller.scrollLeft = 230;
	const beforeRpc = rpc.length, success = list.exportArchives(), request = requests.at(-1);
	assert.equal(request.url.split("?")[0], "/api/method/jifeng_erp.inventory_archive_export.export_archives");
	const params = new URLSearchParams(request.url.split("?")[1]);
	assert.deepEqual(Object.fromEntries(params), { kind: "物料", company: "JF", search: "=literal", status: "停用" });
	assert.equal(params.has("page"), false, "export always uses explicit filter scope, never page rows");
	assert.equal(request.options.method, "GET"); assert.equal(request.options.credentials, "same-origin");
	assert.equal(list.exportButton.disabled, true);
	await list.exportArchives(); assert.equal(requests.at(-1), request, "no duplicate export");
	request.resolve(response()); await success;
	assert.equal(downloads.at(-1).filename, "物料档案.xlsx"); assert.equal(revoked.at(-1), downloads.at(-1).href);
	assert.equal(list.page, 7); assert.equal(list.scroller.scrollTop, 37); assert.equal(list.scroller.scrollLeft, 230); assert.equal(rpc.length, beforeRpc);
	assert.equal(list.exportButton.disabled, false);
	for (const [name, action] of [
		["keyword", value => { value.search.value = "changed"; value.search.events.input(); }],
		["status", value => { value.statusChoice.value = "在用"; value.statusChoice.events.change(); }],
		["company", value => value.companyControl.set_value("OTHER")],
		["kind", value => { value.kind = "材料"; }],
		["disposed", value => value.destroy()],
		["editor opening", value => value.openEditor()],
		["active modal", value => bridge.inventoryEditor.active.set("export-owner", { listOwner: value, closed: false })],
		["pending copy", value => bridge.inventoryEditor.loadingOwners.add(value)],
	]) {
		const value = await setup(), running = value.exportArchives(), pending = requests.at(-1), count = downloads.length;
		await action(value); const status = value.status.textContent;
		pending.resolve(response()); await running;
		assert.equal(downloads.length, count, name + " rejects late response");
		assert.equal(value.status.textContent, status, name + " does not overwrite a new scope's status");
		bridge.inventoryEditor.active.clear(); bridge.inventoryEditor.loadingOwners.clear();
	}
	const lateBlob = await setup("材料"), blob = deferred(), blobRun = lateBlob.exportArchives(), blobReq = requests.at(-1), blobCount = downloads.length;
	blobReq.resolve(response(blob.promise)); await Promise.resolve(); await Promise.resolve();
	lateBlob.search.value = "new-scope"; lateBlob.search.events.input(); blob.resolve({ size: 123 }); await blobRun;
	assert.equal(blobReq.options.signal.aborted, true); assert.equal(downloads.length, blobCount, "late blob never downloads");
	const generation = await setup(), old = generation.exportArchives(), oldReq = requests.at(-1);
	generation.search.value = "new"; generation.search.events.input();
	const next = generation.exportArchives(), newReq = requests.at(-1);
	oldReq.resolve(response()); await old; assert.equal(generation.exportController.signal.aborted, false, "old finally cannot clear new owner");
	newReq.resolve(response()); await next; assert.equal(downloads.at(-1).filename, "物料档案.xlsx");
	for (const reply of [response(Promise.resolve({ size: 100 }), "application/json"), response(Promise.resolve({ size: 100 }), MIME, false), response(Promise.resolve({ size: 0 }))]) {
		const value = await setup(), run = value.exportArchives(), count = downloads.length;
		requests.at(-1).resolve(reply); await run;
		assert.equal(downloads.length, count); assert.match(value.status.textContent, /导出未完成/); assert.equal(value.exportButton.disabled, false);
	}
	const blocked = await setup(); bridge.inventoryEditor.active.set("owned", { listOwner: blocked, closed: false });
	const count = requests.length; await blocked.exportArchives(); assert.equal(requests.length, count); bridge.inventoryEditor.active.clear();
	const materials = await setup("材料"), materialRun = materials.exportArchives(); requests.at(-1).resolve(response()); await materialRun;
	assert.equal(downloads.at(-1).filename, "材料档案.xlsx");
	console.log("Inventory export: explicit saved scope, duplicate/abort/late response+blob, modal/copy lifecycle, no page mutation and safe download gates passed (models).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
