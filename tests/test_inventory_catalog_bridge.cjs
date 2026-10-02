// Real classic menu and bridge lifecycle; a browse page must stay read-only.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
	constructor() { this.children = []; this.style = {}; this.hidden = false; this.parentElement = null; }
	appendChild(child) { this.children.push(child); child.parentElement = this; }
	remove() { this.removed = true; }
	querySelector() { return null; }
}
const messages = [], instances = [];
const window = { JifengInventoryCatalog: class {
	constructor(host, kind) { this.host = host; this.kind = kind; this.filters = {}; this.ready = Promise.resolve(true); instances.push(this); }
	isDirty() { return false; }
	getCloseBlock() { return ""; }
	destroy() { this.destroyed = (this.destroyed || 0) + 1; }
} };
const document = { createElement: () => new Element() };
const frappe = { pages: { "jifeng-legacy": {} }, msgprint: message => messages.push(message) };
const context = { window, document, frappe, __: text => text };
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), context);
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_workspace_core.js", "utf8"), context);
window.Workspace = window.JifengERPWorkspace.Workspace; window.modules = window.JifengERPWorkspace.modules;

const workspace = Object.create(window.Workspace.prototype);
workspace.host = new Element(); workspace.tabs = [{ key: "home", title: "主页", kind: "home" }]; workspace.activeKey = "home";
const bridge = Object.create(window.JifengClassicBridge.prototype);
bridge.workspace = workspace; bridge.layer = new Element(); bridge.frames = new Map(); bridge.align = () => {};
workspace.formalBridge = bridge; workspace.render = () => bridge.sync();

for (const kind of ["物料", "材料"]) {
	const module = window.modules.find(item => item.name === kind);
	const name = module.groups.flatMap(group => Array.from(group.items)).find(item => item === "浏览");
	assert.equal(name, "浏览", "exercise the real legacy menu, not an invented prefixed alias");
	const moduleHtml = workspace.renderModule(kind);
	assert.match(moduleHtml, new RegExp('jfe-function-card is-live[^>]*data-page="浏览">[^]*?<em class="jfe-live-tag">正式</em>'),
		"the real browse menu must show the formal server-backed entry badge");
	workspace.openPage(name, kind);
	const key = `formal:${kind}:浏览`, entry = bridge.frames.get(key);
	assert.equal(entry.workbench.kind, kind); assert.equal(entry.iframe, undefined);
	assert.equal(workspace.activeTab().spec.kind, "catalog");
	entry.workbench.filters = { company: "Synthetic", search: "QA", page: 2, scroll: 123 };
	workspace.openModule(kind); workspace.switchTab("home"); workspace.openPage(name, kind);
	assert.equal(bridge.frames.get(key), entry, "parent and home navigation must keep the exact workbench");
	assert.deepEqual(entry.workbench.filters, { company: "Synthetic", search: "QA", page: 2, scroll: 123 });
	assert.equal(bridge.canClose(key), true); assert.equal(bridge.closeBlock(key), "");
	workspace.closeTab(key);
	assert.equal(entry.workbench.destroyed, 1); assert.equal(entry.container.removed, true);
	assert.equal(bridge.frames.has(key), false);
}
assert.equal(instances.length, 2, "each kind has one dedicated read-only cached workbench");
const saved = window.JifengInventoryCatalog;
delete window.JifengInventoryCatalog;
const before = workspace.tabs.length;
workspace.openPage("浏览", "材料");
assert.equal(workspace.tabs.length, before, "missing component cannot fall back to editable native List or local preview");
assert.equal(messages.at(-1), "浏览页面未能加载，请刷新后重试。");
window.JifengInventoryCatalog = saved; workspace.formalBridge = null;
workspace.openPage("浏览", "物料"); assert.equal(workspace.tabs.length, before);
console.log("Inventory browse actual menus, separate kinds, cached page state, exact disposal and no editable fallback passed (protocol models only).");
