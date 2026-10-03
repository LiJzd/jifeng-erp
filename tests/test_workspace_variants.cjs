// A/B use the actual shared dispatch and bridge; only presentation differs.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
class Element {
	constructor() { this.children = []; this.style = {}; this.hidden = false; this.html = ""; this.classList = { add() {}, remove() {}, toggle() {} }; }
	appendChild(child) { this.children.push(child); child.parentElement = this; }
	remove() { this.removed = true; }
	getClientRects() { return [{}]; }
	getBoundingClientRect() { return { top: 20, left: 30, width: 500, height: 450 }; }
	querySelectorAll() { return []; }
}
let route = "jifeng-legacy";
const messages = [], confirmations = [];
const window = { addEventListener() {}, removeEventListener() {}, JifengSalaryHistory: class {}, JifengInventoryCatalog: class {}, JifengPartnerArchive: class {}, JifengCustomerType: class {}, JifengCustomerArchive: class {}, JifengOrderDictionary: class {}, JifengPlatingColor: class {}, JifengOrderOperations: class {} };
window.JifengInventoryArchiveList = class extends window.JifengInventoryCatalog {};
const document = { createElement: () => new Element(), addEventListener() {} };
const frappe = { pages: { "jifeng-legacy": {}, "jifeng-modern": {} }, router: { on() {} }, get_route: () => [route],
	session: { user: "Synthetic", user_fullname: "合成测试用户" },
	datetime: { get_today: () => "2026-10-02", month_start: () => "2026-10-01" },
	msgprint: message => messages.push(message), confirm: (_, yes, no) => confirmations.push({ yes, no }) };
const context = { window, document, frappe, __: text => text, ResizeObserver: class { observe() {} },
	setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout };
for (const file of ["jifeng_classic_bridge", "jifeng_workspace_core", "jifeng_modern_workspace"]) {
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
}
const shared = window.JifengERPWorkspace, Classic = shared.Workspace, Modern = window.JifengModernWorkspace;
assert.equal(Object.getPrototypeOf(Modern.prototype), Classic.prototype);
for (const name of ["openPage", "openModule", "addTab", "switchTab", "closeTab", "defaultViewState", "saveActiveTabState"]) {
	assert.equal(Object.hasOwn(Modern.prototype, name), false, `A cannot replace shared ${name}`);
	assert.equal(Modern.prototype[name], Classic.prototype[name]);
}
assert.equal(Modern.prototype.pageName, "jifeng-modern");
const plain = value => JSON.parse(JSON.stringify(value));
const entries = plain(shared.entries());
assert.deepEqual(entries, plain(shared.modules.flatMap(module => module.groups.flatMap(group =>
	group.items.map(name => ({ module: module.name, group: group.name, name, availability: shared.availability(module.name, name) }))))));
assert.ok(entries.length > 300, "both views retain the complete existing menu");
assert.equal(shared.availability("外发", "外发工厂"), "partial");
assert.equal(shared.availability("发镀", "电镀厂"), "partial");
assert.equal(shared.availability("物料", "浏览"), "formal");
assert.equal(shared.availability("销售", "客户类型"), "partial");
assert.equal(shared.availability("模具", "压模人"), "preview");
assert.equal(shared.availability("总经理", "物料"), "navigation");
assert.equal(shared.availability("插件", "打印插件"), "tool");
const changed = shared.entries(); changed[0].name = "不得改写菜单";
assert.notEqual(shared.entries()[0].name, changed[0].name);

function dispatch(Workspace, entry) {
	const calls = [], workspace = Object.create(Workspace.prototype);
	workspace.formalBridge = { open: spec => calls.push({ action: "open", spec }), openCreate: spec => calls.push({ action: "create", spec }) };
	workspace.addTab = tab => calls.push({ action: "tab", tab });
	workspace.openModule = module => calls.push({ action: "module", module });
	workspace.openPage(entry.name, entry.module);
	return plain(calls);
}
for (const entry of entries) {
	const before = messages.length, classic = dispatch(Classic, entry), modern = dispatch(Modern, entry);
	assert.deepEqual(modern, classic, `${entry.module}/${entry.name}: identical source, mode, filters and create values`);
	assert.equal(classic.length, 1, `${entry.module}/${entry.name}: one original dispatch`);
	assert.equal(messages.length, before, "available components do not fall back or reject dispatch");
	const expected = entry.availability === "navigation" ? "module" : ["preview", "tool"].includes(entry.availability) ? "tab" : null;
	if (expected) assert.equal(classic[0].action, expected);
}
const find = (module, name) => entries.find(entry => entry.module === module && entry.name === name);
assert.deepEqual(dispatch(Modern, find("物料", "采购订单"))[0].spec.filters, { is_subcontracted: 0 });
assert.deepEqual(dispatch(Modern, find("外发", "委外采购单"))[0].spec.filters, { is_subcontracted: 1 });
assert.deepEqual(dispatch(Modern, find("订单", "算料"))[0].spec.route, ["jifeng-order-material-workbench", "analysis"]);
assert.deepEqual(dispatch(Modern, find("订单", "算料汇总"))[0].spec.route, ["jifeng-order-material-workbench", "summary"]);
assert.equal(dispatch(Modern, find("人事", "人事浏览"))[0].spec.hideCreate, true);
assert.equal(dispatch(Modern, find("外发", "外发工厂"))[0].spec.partnerArchive, "外发工厂");
assert.equal(dispatch(Modern, find("发镀", "电镀厂"))[0].spec.partnerArchive, "电镀厂");
assert.equal(dispatch(Modern, find("销售", "客户类型"))[0].spec.customerType, "客户类型");
assert.equal(dispatch(Modern, find("销售", "客户类型"))[0].spec.doctype, "Jifeng Customer Type");
for (const [module, name] of [["销售", "客户等级"], ["销售", "客户分类"], ["订单", "订单客户分类"], ["订单", "订单客户级别"]]) {
	assert.equal(shared.availability(module, name), "partial");
	assert.equal(dispatch(Modern, find(module, name))[0].spec.customerType, name);
}
for (const [module, name, kind] of [["销售", "客户档案", "销售客户"], ["订单", "客户", "订单客户"]]) {
	assert.equal(shared.availability(module, name), "partial");
	const spec = dispatch(Modern, find(module, name))[0].spec;
	assert.equal(spec.customerArchive, kind);
	assert.equal(spec.doctype, "Jifeng Customer Profile");
}
assert.equal(dispatch(Modern, find("销售", "日报表"))[0].spec.filters.from_date, "2026-10-02");
for (const name of ["类型", "要求"]) {
	assert.equal(shared.availability("订单", name), "partial");
	const spec = dispatch(Modern, find("订单", name))[0].spec;
	assert.equal(spec.orderDictionary, "订单" + name);
	assert.equal(spec.doctype, "Jifeng Order Dictionary");
}
assert.equal(shared.availability("订单", "镀色"), "partial");
assert.equal(dispatch(Modern, find("订单", "镀色"))[0].spec.platingColor, true);
assert.equal(dispatch(Modern, find("订单", "镀色"))[0].spec.doctype, "Jifeng Plating Color");
assert.equal(shared.availability("订单", "工序"), "partial");
assert.equal(dispatch(Modern, find("订单", "工序"))[0].spec.orderOperations, true);
assert.equal(dispatch(Modern, find("订单", "工序"))[0].spec.doctype, "Jifeng Order Operation");
assert.equal(shared.availability("工资", "历史"), "partial");
assert.equal(dispatch(Modern, find("工资", "历史"))[0].spec.salaryHistory, true);
assert.equal(shared.availability("工资", "历史分析"), "preview", "old departmental/group/operation charts cannot be the wage report");
assert.equal(dispatch(Modern, find("工资", "历史分析"))[0].action, "tab");

const navigation = Workspace => {
	const workspace = Object.create(Workspace.prototype); workspace.activeTab = () => ({ kind: "home" });
	return [...workspace.renderSidebar().matchAll(/<button\b[^>]*data-action="([^"]+)"[^>]*>/g)].flatMap(([tag, action]) => {
		if (!["module", "fullscreen", "exit-fullscreen"].includes(action)) return [];
		return [{ action, name: action === "module" ? tag.match(/data-name="([^"]+)"/)[1] : "" }];
	});
};
const classicNavigation = navigation(Classic), modernNavigation = navigation(Modern);
const sortNavigation = list => list.map(item => item.action + ":" + item.name).sort();
assert.equal(classicNavigation.length, 21); assert.equal(modernNavigation.length, 21);
assert.deepEqual(sortNavigation(modernNavigation), sortNavigation(classicNavigation), "all 19 modules and both screen actions remain visible");
for (const item of classicNavigation) {
	const clicks = Workspace => {
		const calls = [], workspace = Object.create(Workspace.prototype);
		workspace.host = { querySelector: () => null, contains: () => true };
		workspace.openModule = name => calls.push({ action: "module", name });
		workspace.addTab = tab => calls.push({ action: "tab", tab });
		workspace.fullscreen = () => calls.push({ action: "fullscreen" });
		workspace.exitFullscreen = () => calls.push({ action: "exit-fullscreen" });
		workspace.bindEvents();
		const button = { dataset: item, matches: () => false, closest: selector => selector === "[data-action]" ? button : null };
		workspace.host.onclick({ target: button, preventDefault() {}, stopPropagation() {} });
		return plain(calls);
	};
	assert.deepEqual(clicks(Modern), clicks(Classic), `${item.action}/${item.name}: actual sidebar event parity`);
}
const searchWorkspace = Object.create(Modern.prototype), inputHandlers = new Map(), searchInput = {
	value: "外发 基础 工厂", attributes: {}, setAttribute(name, value) { this.attributes[name] = value; },
	addEventListener(name, handler) { inputHandlers.set(name, handler); },
};
const results = new Element(), searchCalls = [];
searchWorkspace.host = { contains: () => true, querySelector: selector =>
	selector === "[data-modern-results]" ? results : selector === "[data-modern-search]" ? searchInput : null };
searchWorkspace.formalBridge = { open: spec => searchCalls.push(plain(spec)) };
searchWorkspace.bindEvents(); inputHandlers.get("input")();
assert.equal(searchCalls.length, 0, "search only filters navigation and never opens or queries business data");
assert.equal(results.hidden, false); assert.equal(searchInput.attributes["aria-expanded"], "true");
assert.deepEqual(plain(searchWorkspace.searchEntries(searchInput.value)), entries.filter(entry =>
	["外发", "基础", "工厂"].every(token => [entry.module, entry.group, entry.name].join(" ").includes(token))));
assert.match(results.innerHTML, /data-module="外发" data-page="外发工厂"/);
const resultButton = { dataset: { action: "open-page", module: "外发", page: "外发工厂" }, matches: () => false,
	closest: selector => selector === "[data-action]" ? resultButton : null };
searchWorkspace.host.onclick({ target: resultButton, preventDefault() {}, stopPropagation() {} });
assert.deepEqual(searchCalls, [dispatch(Classic, find("外发", "外发工厂"))[0].spec]);
assert.equal(results.hidden, true, "selecting the search result hides navigation without replacing dispatch");

function makeWorkspace(Workspace) {
	const host = new Element(), anchor = new Element(), filters = new Map();
	host.parentElement = new Element(); host.contains = () => true;
	Object.defineProperty(host, "innerHTML", { get() { return this.html; }, set(value) { this.html = value; } });
	host.querySelector = selector => selector === ".jfe-formal-anchor" ? (host.html.includes("jfe-formal-anchor") ? anchor : null) : filters.get(selector) || null;
	const workspace = new Workspace(host);
	workspace.renderSidebar = () => ""; workspace.renderHome = () => "<section>home</section>";
	workspace.renderModule = () => "<section>module</section>"; workspace.updateClock = () => {};
	const bridge = workspace.formalBridge;
	let stopped = 0, mounted = 0;
	const state = { dirty: true, block: "", value: "同一草稿", requestId: "保留的请求身份" };
	bridge.mount = container => {
		mounted++;
		return { container, workbench: { isDirty: () => state.dirty, getCloseBlock: () => state.block }, stop: () => stopped++ };
	};
	const click = (action, values = {}) => {
		const button = { dataset: { action, ...values }, matches: () => false, closest: () => button };
		host.onclick({ target: button, preventDefault() {}, stopPropagation() {} });
	};
	workspace.render();
	return { workspace, bridge, state, filters, click, counts: () => ({ stopped, mounted }) };
}
const b = makeWorkspace(Classic), a = makeWorkspace(Modern);
for (const model of [b, a]) {
	model.click("open-page", { module: "人事", page: "人事档案" });
	assert.equal(model.workspace.activeTab().spec.personnelArchive, true, "real delegated UI action uses original dispatch");
}
const key = "formal:人事:人事档案", bEntry = b.bridge.frames.get(key), aEntry = a.bridge.frames.get(key);
for (const [currentRoute, visible, hidden] of [["jifeng-legacy", b, a], ["jifeng-modern", a, b]]) {
	route = currentRoute; b.bridge.align(); a.bridge.align();
	assert.equal(visible.bridge.layer.hidden, false); assert.equal(visible.bridge.frames.get(key).container.hidden, false);
	assert.equal(hidden.bridge.layer.hidden, true); assert.equal(hidden.bridge.frames.get(key).container.hidden, true);
}
route = "query-report"; b.bridge.align(); a.bridge.align();
assert.equal(b.bridge.layer.hidden, true); assert.equal(a.bridge.layer.hidden, true);
for (const [model, currentRoute] of [[b, "jifeng-legacy"], [a, "jifeng-modern"]]) {
	route = currentRoute;
	const entry = model.bridge.frames.get(key), before = confirmations.length;
	model.filters.set("[data-search]", { value: "未查询筛选" });
	model.workspace.switchTab("home"); assert.equal(entry.container.hidden, true);
	assert.equal(model.bridge.frames.get(key), entry); assert.equal(model.counts().stopped, 0);
	model.filters.clear(); model.click("tab", { key });
	assert.equal(model.workspace.search, "未查询筛选"); assert.equal(model.bridge.frames.get(key), entry);
	assert.equal(model.counts().mounted, 1); assert.equal(entry.container.hidden, false);
	for (const block of ["正在保存", "保存结果未知", "上传结果未知"]) {
		model.state.block = block; model.workspace.closeTab(key, true);
		assert.equal(model.bridge.frames.get(key), entry); assert.equal(model.counts().stopped, 0);
	}
	model.state.block = ""; model.click("close-tab", { key });
	assert.equal(confirmations.length, before + 1); assert.equal(model.bridge.frames.get(key), entry);
	confirmations.at(-1).no?.(); assert.equal(model.bridge.frames.get(key), entry);
	model.click("close-tab", { key }); model.state.block = "保存结果未知"; confirmations.at(-1).yes();
	assert.equal(model.bridge.frames.get(key), entry, "unknown appearing during confirmation prevents disposal");
	model.state.block = ""; model.click("close-tab", { key }); confirmations.at(-1).yes();
	assert.equal(model.bridge.frames.has(key), false); assert.equal(entry.container.removed, true);
	assert.deepEqual(model.counts(), { stopped: 1, mounted: 1 });
	assert.equal(model.state.value, "同一草稿"); assert.equal(model.state.requestId, "保留的请求身份");
}
assert.notEqual(aEntry, bEntry, "presentation instances keep independent mounted view state");
console.log(`A/B ${entries.length} existing menu dispatches, exact filters, all 21 sidebar actions, search input/click, real constructors, route visibility and dirty/unknown close parity passed (DOM/protocol models only).`);
