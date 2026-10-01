// DOM/protocol models only; exercise the real workspace and bridge lifecycle.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const messages = [], confirmations = [], removedListeners = [];
class Element {
	constructor() { this.children = []; this.style = {}; this.hidden = false; this.html = ""; }
	appendChild(child) { this.children.push(child); }
	remove() { this.removed = true; }
	getClientRects() { return [{}]; }
	getBoundingClientRect() { return { top: 20, left: 30, width: 500, height: 450 }; }
}
const anchor = new Element(), content = new Element(), filters = new Map();
const host = new Element(); host.parentElement = new Element();
Object.defineProperty(host, "innerHTML", { get() { return this.html; }, set(value) { this.html = value; } });
host.querySelector = (selector) => selector === ".jfe-formal-anchor" ? (host.html.includes("jfe-formal-anchor") ? anchor : null) : selector === ".jfe-content" ? content : filters.get(selector) || null;
host.contains = () => true;
const window = { addEventListener() {}, removeEventListener(name, action) { removedListeners.push({ name, action }); } };
const document = { createElement: () => new Element(), addEventListener() {} };
const frappe = { pages: { "jifeng-legacy": {} }, router: { on() {} }, get_route: () => ["jifeng-legacy"],
	msgprint: (message) => messages.push(message), confirm: (_, yes, no) => confirmations.push({ yes, no }) };
const context = { window, document, frappe, __: (text) => text, ResizeObserver: class { observe() {} },
	setInterval: () => 1, clearInterval() {} };
for (const name of ["jifeng_classic_bridge", "jifeng_tool_archive"]) vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${name}.js`), "utf8"), context);
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/jifeng_erp/page/jifeng_legacy/jifeng_legacy.js"), "utf8") + "\nwindow.Workspace = JifengLegacyWorkspace;", context);

const workspace = Object.create(window.Workspace.prototype);
workspace.host = host; workspace.tabs = [{ key: "home", title: "主页", kind: "home" }]; workspace.activeKey = "home";
Object.assign(workspace, workspace.defaultViewState());
workspace.renderSidebar = () => ""; workspace.renderHome = () => "<section>home-content</section>";
workspace.renderModule = (name) => `<section>${workspace.esc(name)}-content</section>`; workspace.updateClock = () => {};
const bridge = new window.JifengClassicBridge(workspace); workspace.formalBridge = bridge;
const mounts = [], stopCounts = new Map();
bridge.mount = (container, spec) => {
	const iframe = new Element(); iframe.contentWindow = { cur_frm: { is_dirty: () => true, doc: { first_name: "保留的草稿姓名" } }, cur_list: { filters: ["retained"], scrollTop: 91 } };
	container.appendChild(iframe);
	const entry = { iframe, ready: Promise.resolve(true), stop: () => stopCounts.set(spec.title, (stopCounts.get(spec.title) || 0) + 1) };
	mounts.push(entry); return entry;
};
const click = (action, extra = {}) => {
	const button = { dataset: { action, ...extra }, matches: () => false, closest: () => button };
	host.onclick({ target: button, preventDefault() {}, stopPropagation() {} });
};
const assertSinglePath = (expected) => {
	const breadcrumb = host.html.match(/<nav class="jfe-formal-breadcrumb".*?<\/nav>/)?.[0];
	assert.ok(breadcrumb); assert.equal((host.html.match(/aria-label="页面位置"/g) || []).length, 1);
	assert.deepEqual([...breadcrumb.matchAll(/>([^<>]+)<\/(?:button|b)>/g)].map((item) => item[1]), expected);
	assert.equal((breadcrumb.match(/aria-current="page"/g) || []).length, 1);
	assert.doesNotMatch(host.html, /jfe-tabs|jfe-tab-close|data-action="(?:tab|close-tab|tab-menu)"/);
};

workspace.render(); assertSinglePath(["主页"]);
workspace.openModule("人事"); assertSinglePath(["主页", "人事"]);
workspace.openPage("人事档案", "人事"); assertSinglePath(["主页", "人事", "人事档案"]);
const personnelKey = workspace.activeKey, personnelEntry = bridge.frames.get(personnelKey), personnelFrame = personnelEntry.iframe;
assert.equal(mounts.length, 1); assert.equal(personnelEntry.container.hidden, false);
// These values have not been queried yet; the breadcrumb must retain them too.
filters.set("[data-search]", { value: "未提交查询" }); filters.set('[data-filter="from"]', { value: "2026-09-15" });
click("classic-module", { module: "人事" }); assertSinglePath(["主页", "人事"]);
assert.equal(bridge.frames.get(personnelKey), personnelEntry); assert.equal(personnelEntry.container.hidden, true);
assert.equal(personnelFrame.contentWindow.cur_frm.is_dirty(), true); assert.equal(stopCounts.size, 0);
filters.clear();
click("classic-home"); assertSinglePath(["主页"]);
workspace.openPage("人事档案", "人事"); assertSinglePath(["主页", "人事", "人事档案"]);
assert.equal(bridge.frames.get(personnelKey).iframe, personnelFrame); assert.equal(mounts.length, 1);
assert.equal(personnelEntry.container.children[0], personnelFrame); assert.equal(host.parentElement.children[0], bridge.layer);
assert.equal(personnelFrame.contentWindow.cur_frm.doc.first_name, "保留的草稿姓名");
assert.equal(workspace.search, "未提交查询"); assert.equal(workspace.dateFrom, "2026-09-15");
assert.deepEqual(personnelFrame.contentWindow.cur_list.filters, ["retained"]); assert.equal(personnelFrame.contentWindow.cur_list.scrollTop, 91);
workspace.refreshContent(); assert.doesNotMatch(content.innerHTML, /页面位置/); assert.equal(mounts.length, 1);

// The old cached page remains alive while another module is opened.
workspace.openPage("客户", "订单"); assertSinglePath(["主页", "订单", "客户"]);
assert.equal(personnelEntry.container.hidden, true); assert.equal(bridge.frames.get(personnelKey), personnelEntry);
assert.equal(mounts.length, 2); assert.doesNotMatch(host.html.match(/<nav class="jfe-formal-breadcrumb".*?<\/nav>/)[0], /人事档案/);

// Tool save/upload flags use the actual workbench close-block implementation.
const tool = Object.create(window.JifengToolArchive.prototype), draft = { remark: "未保存内容" };
tool.revision = 0; tool.beforeUnload = () => {}; tool.draft = (state) => state.values;
let editorHides = 0, uploaderHides = 0;
const state = { closed: false, values: draft, original: JSON.stringify({ remark: "原值" }), requestId: "retained-request",
	nativeHide: () => editorHides++, upload: { open: false, uploader: { dialog: { hide: () => uploaderHides++ } } } };
tool.editor = state;
const toolKey = "formal:模具:模具录入", toolContainer = new Element();
workspace.tabs.push({ key: toolKey, title: "模具录入", module: "模具", kind: "formal", spec: { toolArchive: "mold" } });
bridge.frames.set(toolKey, { workbench: tool, container: toolContainer, stop: () => tool.destroy() });
workspace.switchTab(toolKey); assertSinglePath(["主页", "模具", "模具录入"]);
for (const [flag, value] of [["busy", true], ["unresolved", true], ["uploadUnresolved", true], ["upload", { ...state.upload, open: true }]]) {
	const previous = state[flag]; state[flag] = value;
	click("classic-module", { module: "模具" });
	assert.equal(toolContainer.hidden, true); assert.equal(bridge.frames.get(toolKey).workbench, tool);
	assert.equal(state.values, draft); assert.equal(state.requestId, "retained-request"); assert.equal(editorHides, 0); assert.equal(uploaderHides, 0);
	workspace.closeTab(toolKey, true); assert.equal(bridge.frames.get(toolKey).workbench, tool, "discard cannot bypass a save/upload result block");
	state[flag] = previous; workspace.switchTab(toolKey);
}
assert.equal(tool.disposed, undefined); assert.equal(confirmations.length, 0, "hiding cached drafts is not destructive");
workspace.closeTab(toolKey); assert.equal(confirmations.length, 1); assert.ok(bridge.frames.has(toolKey));
// Native Cancel closes its confirmation without invoking the optional yes callback.
confirmations[0].no?.(); assert.ok(bridge.frames.has(toolKey)); assert.equal(editorHides, 0);
workspace.closeTab(toolKey); const accepted = confirmations[1];
state.unresolved = true; accepted.yes();
assert.ok(bridge.frames.has(toolKey), "confirmation must recheck a newly unknown result before destroying");
state.unresolved = false; workspace.closeTab(toolKey); confirmations[2].yes();
assert.equal(bridge.frames.has(toolKey), false); assert.equal(tool.disposed, true); assert.equal(toolContainer.removed, true);
assert.equal(editorHides, 1); assert.equal(uploaderHides, 1); assert.equal(removedListeners.some(({ action }) => action === tool.beforeUnload), true);
assert.equal(bridge.frames.get(personnelKey), personnelEntry, "disposing one cached page preserves other native contexts");

const escaped = workspace.renderBreadcrumb({ kind: "formal", module: '<模块"', title: "<页面&>" });
assert.match(escaped, /data-module="&lt;模块&quot;"/); assert.match(escaped, /&lt;页面&amp;&gt;/); assert.doesNotMatch(escaped, /<模块|<页面/);
console.log("Classic navigation: single cumulative breadcrumb, real parent click routes, cached drafts/filters/native frames, save/upload uncertainty and confirmed disposal passed (DOM/protocol models only).");
