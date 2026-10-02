const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let respond, requests = [], downloads = [], revoked = [];
const context = {
	window: {}, __: (value) => value, AbortController, URLSearchParams,
	URL: { createObjectURL: () => "blob:archive-export", revokeObjectURL: (url) => revoked.push(url) },
	setTimeout: (callback) => callback(),
	document: { body: { append() {} }, createElement: () => ({
		click() { downloads.push({ href: this.href, filename: this.download }); }, remove() {},
	}) },
	fetch: (url, options) => { requests.push({ url, options }); return new Promise((resolve) => { respond = resolve; }); },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_partner_archive.js"), "utf8"), context);
const Archive = context.window.JifengPartnerArchive;
function workbench() {
	return Object.assign(Object.create(Archive.prototype), {
		kind: "电镀厂", companyScope: "Company & A", search: { value: "原文=关键字" }, statusFilter: "全部", revision: 1,
		disposed: false, opening: false, editor: null, exporting: false, exportController: null,
		exportButton: {}, statusSelect: {}, status: { textContent: "" }, host: { replaceChildren() {} },
	});
}
function response(ok = true, mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet; charset=utf-8", size = 120) {
	return { ok, headers: { get: () => mime }, blob: async () => ({ size }) };
}
async function run() {
	const value = workbench();
	const completed = value.exportArchives();
	assert.equal(value.exportButton.disabled, true);
	assert.equal(requests.length, 1);
	const request = requests[0], query = new URL(request.url, "https://local.test");
	assert.equal(query.pathname, "/api/method/jifeng_erp.partner_archive_export.export_archives");
	assert.deepEqual(Object.fromEntries(query.searchParams), { kind: "电镀厂", company: "Company & A", search: "原文=关键字", status: "全部" });
	assert.equal(request.options.method, "GET"); assert.equal(request.options.credentials, "same-origin");
	respond(response()); await completed;
	assert.deepEqual(downloads, [{ href: "blob:archive-export", filename: "电镀厂档案.xlsx" }]);
	assert.deepEqual(revoked, ["blob:archive-export"]); assert.equal(value.exportButton.disabled, false);
	assert.equal(value.exporting, false); assert.equal(value.exportController, null);

	for (const invalid of [response(false), response(true, "application/json"), response(true, undefined, 0)]) {
		const count = downloads.length, calls = requests.length, rejected = value.exportArchives();
		respond(invalid); await rejected;
		assert.equal(downloads.length, count); assert.equal(requests.length, calls + 1);
		assert.match(value.status.textContent, /导出未完成/); assert.equal(value.exportButton.disabled, false);
	}
	const cancelled = value.exportArchives(), pending = requests.at(-1);
	value.statusFilter = "停用"; value.revision++; value.clearResults();
	assert.equal(pending.options.signal.aborted, true); respond(response()); await cancelled;
	assert.equal(downloads.length, 1); assert.equal(value.exporting, false);
	// Even a resolved HTTP response must not download after cancel + modal close.
	let finishBlob, startBlob;
	const blobStarted = new Promise((resolve) => { startBlob = resolve; });
	const late = value.exportArchives();
	respond({ ...response(), blob: () => new Promise((resolve) => { finishBlob = resolve; startBlob(); }) });
	await blobStarted;
	assert.equal(typeof finishBlob, "function");
	value.cancelExport(); value.editor = {}; value.editor = null;
	finishBlob({ size: 120 }); await late; assert.equal(downloads.length, 1);

	const destroyed = workbench(), destroyedRequest = destroyed.exportArchives();
	destroyed.destroy(); assert.equal(requests.at(-1).options.signal.aborted, true);
	respond(response()); await destroyedRequest; assert.equal(downloads.length, 1);
	const before = requests.length;
	value.editor = {}; await value.exportArchives(); assert.equal(requests.length, before);
	console.log("partner archive export workbench: passed");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
