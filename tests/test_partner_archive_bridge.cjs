// Exercise the actual old factory menu through the formal bridge lifecycle.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
	constructor() { this.children = []; this.style = {}; this.hidden = false; }
	appendChild(child) { this.children.push(child); child.parentElement = this; }
	remove() { this.removed = true; }
	querySelector() { return null; }
}
const messages = [], instances = [];
const window = { JifengPartnerArchive: class {
	constructor(host, kind) { this.host = host; this.kind = kind; this.ready = Promise.resolve(true); instances.push(this); }
	isDirty() { return false; }
	getCloseBlock() { return this.block || ""; }
	destroy() { this.destroyed = (this.destroyed || 0) + 1; }
} };
const context = { window, document: { createElement: () => new Element() },
	frappe: { pages: { "jifeng-legacy": {} }, msgprint: message => messages.push(message) }, __: text => text };
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), context);
vm.runInNewContext(fs.readFileSync("jifeng_erp/jifeng_erp/page/jifeng_legacy/jifeng_legacy.js", "utf8") +
	"\nwindow.Workspace=JifengLegacyWorkspace;window.modules=JIFENG_LEGACY_MODULES;", context);
const workspace = Object.create(window.Workspace.prototype);
workspace.host = new Element(); workspace.tabs = [{ key: "home", title: "主页", kind: "home" }]; workspace.activeKey = "home";
const bridge = Object.create(window.JifengClassicBridge.prototype);
bridge.workspace = workspace; bridge.layer = new Element(); bridge.frames = new Map(); bridge.align = () => {};
workspace.formalBridge = bridge; workspace.render = () => bridge.sync();
const name = window.modules.find(module => module.name === "外发").groups.flatMap(group => Array.from(group.items))
	.find(item => item === "外发工厂");
assert.equal(name, "外发工厂");
assert.match(workspace.renderModule("外发"), /jfe-function-card is-live[^>]*data-page="外发工厂">[^]*?<em class="jfe-live-tag">正式<\/em>/);
workspace.openPage(name, "外发");
const key = "formal:外发:外发工厂", entry = bridge.frames.get(key);
assert.equal(entry.workbench.kind, name); assert.equal(entry.iframe, undefined);
assert.equal(workspace.activeTab().spec.doctype, "Jifeng Partner Profile");
entry.workbench.filters = { company: "Synthetic", search: "QA", page: 2 };
workspace.openModule("外发"); workspace.switchTab("home"); workspace.openPage(name, "外发");
assert.equal(bridge.frames.get(key), entry); assert.deepEqual(entry.workbench.filters, { company: "Synthetic", search: "QA", page: 2 });
entry.workbench.block = "正在保存";
workspace.closeTab(key); assert.equal(bridge.frames.has(key), true); assert.equal(messages.at(-1), "正在保存");
entry.workbench.block = "";
workspace.closeTab(key); assert.equal(entry.workbench.destroyed, 1); assert.equal(entry.container.removed, true);
assert.equal(instances.length, 1);
delete window.JifengPartnerArchive;
const count = workspace.tabs.length;
workspace.openPage(name, "外发");
assert.equal(workspace.tabs.length, count); assert.equal(messages.at(-1), "工厂档案页面未能加载，请刷新后重试。");
console.log("Actual factory menu, formal badge, cached inline workbench, save close blocker and exact disposal passed (protocol models only).");
