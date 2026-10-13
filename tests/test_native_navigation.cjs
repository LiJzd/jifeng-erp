const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "..", "jifeng_erp");
const script = fs.readFileSync(path.join(root, "public/js/jifeng_native_navigation.js"), "utf8");

// A returning browser must bypass the old cached Page before rendering it.
for (const storageAvailable of [true, false]) {
	const removed = [];
	const frappe = { re_route: { "existing-route": "existing-target" } };
	vm.runInNewContext(script, { frappe, localStorage: {
		removeItem(name) {
			if (!storageAvailable) throw new Error("storage blocked");
			removed.push(name);
		},
	} });
	assert.equal(frappe.re_route["existing-route"], "existing-target");
	for (const name of ["jifeng-modern", "jifeng-legacy"]) {
		assert.equal(frappe.re_route[name], "/desk/集沣生产协作");
		if (storageAvailable) assert(removed.includes("_page:" + name));
	}
}

// Direct Page loading remains a native redirect, without constructing a shell.
for (const name of ["jifeng-modern", "jifeng-legacy"]) {
	const folder = name.replaceAll("-", "_");
	const calls = [];
	const frappe = { pages: { [name]: {} }, route_flags: {}, set_route: (...args) => calls.push(args) };
	vm.runInNewContext(fs.readFileSync(path.join(root, "jifeng_erp/page", folder, folder + ".js"), "utf8"), { frappe });
	frappe.pages[name].on_page_show();
	assert.deepEqual(calls, [["Workspaces", "集沣生产协作"]]);
	assert.equal(frappe.route_flags.replace_route, true);
}
console.log("Native navigation: cached pages, blocked storage and both aliases passed.");
