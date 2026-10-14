// Compare menu names, groups and ordering against live read-only observations.
// This does not establish form, business rule or runtime acceptance.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
	"../jifeng_erp/public/js/jifeng_workspace_core.js"), "utf8"), { window });
const shared = window.JifengERPWorkspace;
const evidence = JSON.parse(fs.readFileSync(path.join(__dirname,
	"fixtures/legacy_core_menus_20261015.json"), "utf8"));
for (const [moduleName, expected] of Object.entries(evidence.modules)) {
	const module = shared.modules.find(module => module.name === moduleName);
	assert.ok(module, moduleName);
	const actual = Object.fromEntries(module.groups.filter(group => group.name !== "正式业务")
		.map(group => [group.name, Array.from(group.items, name => shared.title(moduleName, name))]));
	assert.deepEqual(Object.keys(actual), Object.keys(expected), `${moduleName}: original group order`);
	assert.deepEqual(actual, expected, `${moduleName}: original groups, labels and item order`);
	const workspace = Object.create(shared.Workspace.prototype);
	const html = workspace.renderModule(moduleName);
	for (const titles of Object.values(expected)) {
		for (const title of titles) assert.ok(html.includes(`<span>${title}</span>`), `${moduleName}/${title} is visible`);
	}
	assert.doesNotMatch(html, /<b>正式业务<\/b>/, "additional native entries do not reorder the original groups");
	for (const group of module.groups.filter(group => group.name === "正式业务")) {
		assert.ok(html.includes("<summary>更多业务</summary>"));
		for (const name of group.items) assert.ok(html.includes(`data-page="${name}"`), `${moduleName}/${name} stays reachable`);
	}
}
assert.equal(shared.title("物料", "采购订单"), "采购订单", "native extension identifiers keep their labels");
assert.equal(shared.title("销售", "客户档案"), "客户档案", "unobserved modules do not acquire guessed labels");
console.log(`Original ERP observed menus: ${Object.keys(evidence.modules).length} modules matched.`);
