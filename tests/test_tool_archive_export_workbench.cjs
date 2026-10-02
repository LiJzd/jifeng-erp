// Real export guards with delayed HTTP/body models; no browser or network.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const requests = [], downloads = [], revoked = [], rpc = [];
const context = {
	window: { removeEventListener() {} }, __: (value) => value, AbortController, URLSearchParams,
	URL: { createObjectURL: () => "blob:tool-export", revokeObjectURL: (url) => revoked.push(url) }, setTimeout: (fn) => fn(),
	document: { body: { append() {} }, createElement: () => ({ click() { downloads.push({ href: this.href, filename: this.download }); }, remove() {} }) },
	frappe: { msgprint() {}, call(options) { rpc.push(options); return Promise.reject(new Error("modeled opening failure")); } },
	fetch(url, options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); requests.push({ url, options, resolve, reject }); return promise; },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_tool_archive.js"), "utf8"), context);
const Archive = context.window.JifengToolArchive;
function workbench(kind = "mold") {
	return Object.assign(Object.create(Archive.prototype), {
		recordType: kind, modeling: kind === "modeling", companyScope: "Company & A", search: { value: "原文=关键词" }, revision: 1,
		companyControl: { value: "Company & A", get_value() { return this.value; }, async set_value(value) { this.value = value; } },
		includeInactive: { checked: true }, disposed: false, opening: false, editor: null, changingCompany: false,
		exporting: false, exportController: null, exportButton: {}, status: { textContent: "" }, tbody: { replaceChildren() {} },
		rows: [{ name: "CLIENT-CACHE-FORBIDDEN", values: { legacy_number: "DRAFT-FORBIDDEN" } }], host: { replaceChildren() {} },
	});
}
function response(ok = true, mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet; charset=utf-8", size = 120) {
	return { ok, headers: { get: () => mime }, blob: async () => ({ size }) };
}
async function run() {
	for (const kind of ["mold", "plate", "modeling"]) {
		const bench = workbench(kind), beforeRows = bench.rows, pending = bench.exportArchives(), request = requests.at(-1);
		assert.equal(bench.exportButton.disabled, true);
		const url = new URL(request.url, "https://local.test");
		assert.equal(url.pathname, "/api/method/jifeng_erp.tool_archive_export.export_archives");
		assert.deepEqual(Object.fromEntries(url.searchParams), { record_type: kind, company: "Company & A", search: "原文=关键词", include_inactive: kind === "modeling" ? "0" : "1" });
		assert.equal(request.options.method, "GET"); assert.equal(request.options.credentials, "same-origin");
		const count = requests.length; await bench.exportArchives(); assert.equal(requests.length, count, "one export request at a time");
		request.resolve(response()); await pending;
		assert.equal(bench.rows, beforeRows, "export does not replace visible rows with server or draft data");
		assert.equal(downloads.at(-1).filename, (kind === "mold" ? "模具" : kind === "plate" ? "版种" : "版种建模") + "档案.xlsx");
		assert.equal(bench.exportButton.disabled, false); assert.equal(bench.exporting, false); assert.equal(bench.exportController, null);
	}
	const bench = workbench(), initial = downloads.length;
	for (const invalid of [response(false), response(true, "application/json"), response(true, null), response(true, undefined, 0)]) {
		const count = requests.length, pending = bench.exportArchives(); requests.at(-1).resolve(invalid); await pending;
		assert.equal(downloads.length, initial); assert.equal(requests.length, count + 1, "failed XLSX has no cache export or second request fallback");
		assert.match(bench.status.textContent, /导出未完成/); assert.equal(bench.exportButton.disabled, false);
	}
	for (const change of [(value) => { value.search.value = "changed"; value.revision++; value.cancelExport(); },
		(value) => { value.includeInactive.checked = false; }, (value) => { value.companyControl.value = "Company B"; },
		(value) => { value.editor = {}; }, (value) => { value.opening = true; }]) {
		const value = workbench(), pending = value.exportArchives(); change(value); requests.at(-1).resolve(response()); await pending;
		assert.equal(downloads.length, initial, "late HTTP response cannot publish changed scope or draft window");
	}
	// An already accepted HTTP response can still have a delayed body. Opening
	// then closing a dialog or starting another export must never accept it.
	let finishBlob, started;
	const blobStarted = new Promise((yes) => { started = yes; });
	const old = bench.exportArchives(), oldRequest = requests.at(-1);
	oldRequest.resolve({ ...response(), blob: () => new Promise((yes) => { finishBlob = yes; started(); }) });
	await blobStarted; bench.cancelExport(); bench.editor = {}; bench.editor = null;
	const newer = bench.exportArchives(), newController = bench.exportController, newRequest = requests.at(-1);
	finishBlob({ size: 120 }); await old;
	assert.equal(downloads.length, initial); assert.equal(bench.exportController, newController); assert.equal(bench.exporting, true);
	newRequest.resolve(response()); await newer; assert.equal(downloads.length, initial + 1);
	assert.equal(oldRequest.options.signal.aborted, true);
	const silent = workbench(); silent.companyControl.value = "Company B";
	const silentlyChanged = silent.exportArchives(); await Promise.resolve(); await Promise.resolve();
	assert.equal(silent.companyScope, "Company B"); assert.equal(new URL(requests.at(-1).url, "https://local.test").searchParams.get("company"), "Company B");
	requests.at(-1).resolve(response()); await silentlyChanged;
	const destroying = workbench(), destroyed = destroying.exportArchives(); destroying.destroy();
	assert.equal(requests.at(-1).options.signal.aborted, true); const downloadsBefore = downloads.length;
	requests.at(-1).resolve(response()); await destroyed; assert.equal(downloads.length, downloadsBefore);
	const opening = workbench(), pending = opening.exportArchives(), pendingRequest = requests.at(-1);
	await opening.openEditor(); assert.equal(pendingRequest.options.signal.aborted, true); assert.equal(rpc.at(-1).method, "jifeng_erp.tool_archive.init_record");
	pendingRequest.resolve(response()); await pending; assert.equal(downloads.length, downloadsBefore);
	for (const flag of ["editor", "opening", "changingCompany", "disposed"]) {
		const blocked = workbench(), count = requests.length; blocked[flag] = flag === "editor" ? {} : true; await blocked.exportArchives(); assert.equal(requests.length, count);
	}
	const noCompany = workbench(), count = requests.length; noCompany.companyScope = noCompany.companyControl.value = null; await noCompany.exportArchives(); assert.equal(requests.length, count);
	assert.equal(revoked.length, downloads.length); console.log("tool archive export workbench: passed");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
