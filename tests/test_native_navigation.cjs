const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "..", "jifeng_erp");
const script = fs.readFileSync(path.join(root, "public/js/jifeng_native_navigation.js"), "utf8");

// The old native redirect must not swallow the original ERP module Page.
for (const storageAvailable of [true, false]) {
	const removed = [];
	const frappe = { re_route: { "existing-route": "existing-target", "jifeng-legacy": "/desk/集沣生产协作" } };
	vm.runInNewContext(script, { frappe, localStorage: {
		removeItem(name) {
			if (!storageAvailable) throw new Error("storage blocked");
			removed.push(name);
		},
	} });
	assert.equal(frappe.re_route["existing-route"], "existing-target");
	assert.equal(frappe.re_route["jifeng-modern"], "/desk/jifeng-legacy");
	assert.equal(Object.hasOwn(frappe.re_route, "jifeng-legacy"), false);
	for (const name of ["jifeng-modern", "jifeng-legacy"]) {
		if (storageAvailable) assert(removed.includes("_page:" + name));
	}
}

// Both old links use a single module menu; the Page creates it once and retains
// its bridge on subsequent visits so existing drafts and queries survive.
const calls = [], mounted = [], aligned = [], wrapper = {}, host = {};
const main = { empty() { return this; }, addClass() { return this; }, get() { return host; } };
const page = { main };
const frappe = {
	pages: { "jifeng-modern": {}, "jifeng-legacy": {} }, route_flags: {},
	set_route: (...args) => calls.push(args),
	ui: { make_app_page: options => { mounted.push(options); return page; } },
};
const context = { frappe, __: text => text, $: () => ({ addClass() {} }),
	window: { JifengERPWorkspace: { Workspace: class {
		constructor(target) { assert.equal(target, host); this.formalBridge = { align: () => aligned.push(true) }; }
	} } },
};
for (const name of ["jifeng-modern", "jifeng-legacy"]) {
	const folder = name.replaceAll("-", "_");
	vm.runInNewContext(fs.readFileSync(path.join(root, "jifeng_erp/page", folder, folder + ".js"), "utf8"), context);
}
frappe.pages["jifeng-modern"].on_page_show();
assert.deepEqual(calls, [["jifeng-legacy"]]);
assert.equal(frappe.route_flags.replace_route, true);
frappe.pages["jifeng-legacy"].on_page_load(wrapper);
assert.equal(mounted.length, 1);
assert.equal(mounted[0].single_column, true);
assert.equal(page.hide_sidebar, true);
const workspace = wrapper.jifeng_workspace;
frappe.pages["jifeng-legacy"].on_page_show(wrapper);
frappe.pages["jifeng-legacy"].on_page_show(wrapper);
assert.equal(wrapper.jifeng_workspace, workspace);
assert.equal(mounted.length, 1);
assert.equal(aligned.length, 2);
console.log("Original ERP navigation: aliases, cache eviction, blocked storage and retained Page passed.");
