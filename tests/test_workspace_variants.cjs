// A/B use the actual shared dispatch and bridge; only presentation differs.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
class Element {
	constructor() { this.children = []; this.style = {}; this.hidden = false; this.html = ""; this.classList = { add() {}, remove() {}, toggle() {} }; }
	appendChild(child) { this.children.push(child); child.parentElement = this; }
	append(...children) { children.forEach(child => this.appendChild(child)); }
	replaceChildren(...children) { this.children = []; this.append(...children); }
	remove() { this.removed = true; }
	getClientRects() { return [{}]; }
	getBoundingClientRect() { return { top: 20, left: 30, width: 500, height: 450 }; }
	querySelectorAll() { return []; }
	addEventListener() {}
}
let route = "jifeng-legacy";
const messages = [], confirmations = [];
const window = { addEventListener() {}, removeEventListener() {}, JifengSalaryHistory: class {}, JifengSalaryAnalysis: class {}, JifengInventoryCatalog: class {}, JifengPartnerArchive: class {}, JifengCustomerType: class {}, JifengCustomerArchive: class {}, JifengOrderDictionary: class {}, JifengPlatingColor: class {}, JifengOrderOperations: class {} };
window.JifengProcurementFulfillment = class {};
window.JifengSalesFulfillment = class {};
window.JifengSupplierPayables = class {};
window.JifengInventoryTracking = class {};
window.JifengFinanceDictionary = class {};
window.JifengTemporaryWorkers = class {};
window.JifengBusinessOperationLog = class {};
window.JifengProductionInstructionList = class {};
window.JifengSubcontractingReconciliation = class {};
window.JifengSubcontractingReversal = class {};
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
for (const name of ["openPage", "openModule", "addTab", "closeTab", "defaultViewState", "saveActiveTabState"]) {
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
	workspace.openFinishedIntakeSources = () => calls.push({ action: "finished-intake-dialog" });
	workspace.openPage(entry.name, entry.module);
	return plain(calls);
}
for (const entry of entries) {
	// POS resolves its native doctype asynchronously; verify it below after the settings response.
	if (entry.module === "销售" && entry.name === "零售单记录") continue;
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
const colorSpecs = [];
for (const module of ["产品", "订单"]) {
	assert.equal(shared.availability(module, "镀色"), "partial");
	const call = dispatch(Modern, find(module, "镀色"))[0];
	assert.equal(call.action, "open", "both color entries use the existing server editor instead of local preview");
	assert.equal(call.spec.platingColor, true);
	assert.equal(call.spec.doctype, "Jifeng Plating Color");
	assert.equal(call.spec.availability, "partial");
	assert.equal(call.spec.module, module);
	const { module: presentationModule, ...directorySpec } = call.spec;
	colorSpecs.push(directorySpec);
}
assert.deepEqual(colorSpecs[0], colorSpecs[1], "product and order reuse one color directory and editor without new scope or numbering rules");
assert.equal(shared.availability("订单", "工序"), "partial");
assert.equal(dispatch(Modern, find("订单", "工序"))[0].spec.orderOperations, true);
assert.equal(dispatch(Modern, find("订单", "工序"))[0].spec.doctype, "Jifeng Order Operation");
assert.equal(shared.availability("工资", "历史"), "partial");
for (const [name, mode] of [["临工档案", "archive"], ["临工浏览", "browse"], ["临工", "wage"], ["临工明细", "details"]]) {
	assert.equal(shared.availability("工资", name), "partial");
	const modern = dispatch(Modern, find("工资", name))[0].spec;
	assert.equal(modern.temporaryWorkers, mode);
	assert.equal(modern.availability, "partial");
}
assert.equal(dispatch(Modern, find("工资", "历史"))[0].spec.salaryHistory, true);
assert.equal(shared.availability("工资", "历史分析"), "partial", "salary analysis uses saved payroll sources, not the wage-entry report");
assert.ok(entries.some(entry => entry.module === "工资" && entry.name === "工资发放" && entry.group === "工资操作"),
	"monthly payroll remains visible in the existing wage operations group");
assert.deepEqual(dispatch(Modern, find("工资", "工资发放"))[0].spec.route, ["List", "Payroll Entry"], "monthly payroll opens the native Payroll Entry list");
assert.deepEqual(dispatch(Classic, find("工资", "工资发放"))[0].spec.route, ["List", "Payroll Entry"]);
for (const [module, name, flag] of [["物料", "采购履行核对", "procurementFulfillment"], ["材料", "采购履行核对", "procurementFulfillment"], ["销售", "销售履行核对", "salesFulfillment"], ["财务", "供应商应付核对", "supplierPayables"], ["物料", "跟踪", "inventoryTracking"], ["材料", "跟踪", "inventoryTracking"]]) {
	assert.equal(shared.availability(module, name), "partial");
	assert.equal(dispatch(Modern, find(module, name))[0].spec[flag], true);
}
assert.equal(shared.availability("销售", "销售跟踪"), "preview");
const connectedEntries = [["生产", "生产指令单", "productionInstructionList", true],
	["发镀", "电镀对账", "subcontractingReconciliation", "plating"],
	["外发", "对账", "subcontractingReconciliation", "outsourcing"],
	["财务", "电镀账", "subcontractingReconciliation", "plating"],
	["财务", "委外账", "subcontractingReconciliation", "outsourcing"],
	["发镀", "电镀反审", "subcontractingReversal", "plating_order"],
	["发镀", "品检反审", "subcontractingReversal", "plating_inspection"],
	["外发", "反审", "subcontractingReversal", "outer_order"],
	["外发", "收回反审", "subcontractingReversal", "outer_receipt"]];
for (const [module, name, flag, value] of connectedEntries) {
	assert.equal(shared.availability(module, name), "partial", "unverified legacy rules remain explicitly limited");
	const call = dispatch(Modern, find(module, name))[0];
	assert.equal(call.action, "open", "formal component opens before native create or local preview dispatch");
	assert.equal(call.spec[flag], value);
	assert.equal(call.spec.availability, "partial");
	assert.deepEqual(call, dispatch(Classic, find(module, name))[0]);
}
for (const [flag, component] of [["productionInstructionList", "JifengProductionInstructionList"],
	["subcontractingReconciliation", "JifengSubcontractingReconciliation"], ["subcontractingReversal", "JifengSubcontractingReversal"]]) {
	const original = window[component]; delete window[component];
	for (const [module, name, expectedFlag] of connectedEntries.filter(entry => entry[2] === flag)) {
		assert.equal(expectedFlag, flag);
		const before = messages.length;
		assert.deepEqual(dispatch(Modern, find(module, name)), []);
		assert.equal(messages.length, before + 1, "missing formal component cannot fall back to local rows or native creation");
	}
	window[component] = original;
}
for (const name of ["收款银行", "付款银行", "费用科目"]) {
	assert.equal(shared.availability("财务", name), "partial");
	assert.equal(dispatch(Modern, find("财务", name))[0].spec.financeDictionary, name);
	assert.deepEqual(dispatch(Modern, find("财务", name)), dispatch(Classic, find("财务", name)));
}
for (const name of ["样品制作单", "样品单反审"]) {
	assert.equal(shared.availability("产品", name), "partial");
	assert.equal(dispatch(Modern, find("产品", name))[0].spec.doctype, "Jifeng Sample Order");
}
assert.deepEqual(dispatch(Modern, find("产品", "样品单反审"))[0].spec.filters, { docstatus: 1 });
assert.equal(dispatch(Modern, find("工资", "历史分析"))[0].action, "tab");
assert.deepEqual(dispatch(Modern, find("工资", "历史分析"))[0].tab, { key: "salary-analysis", title: "历史分析", kind: "salary-analysis", module: "工资" });

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
assert.ok(searchWorkspace.searchEntries("工资发放").some(entry => entry.module === "工资" && entry.group === "工资操作" && entry.name === "工资发放"),
	"global navigation search indexes the visible monthly payroll entry");
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
	const state = { dirty: true, block: "", visible: false, value: "同一草稿", requestId: "保留的请求身份" };
	bridge.mount = container => {
		mounted++;
		return { container, workbench: { isDirty: () => state.dirty, getCloseBlock: () => state.block,
			setVisible: value => { state.visible = value; } }, stop: () => stopped++ };
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
// Returning from a scrolled module must reveal the cached formal toolbar.
// Its rectangle follows the real outer scroller, while draft/query state stays mounted.
route = "jifeng-modern";
let mainTop = 223, scrollWrites = 0;
const mainSection = {
	get scrollTop() { return mainTop; },
	set scrollTop(value) { mainTop = value; scrollWrites++; },
};
a.workspace.host.closest = selector => selector === ".main-section" ? mainSection : null;
const formalAnchor = a.workspace.host.querySelector(".jfe-formal-anchor");
formalAnchor.getBoundingClientRect = () => ({ top: 100 - mainTop, left: 30, width: 500, height: 450 });
a.bridge.align();
assert.ok(Number.parseFloat(a.bridge.layer.style.top) < 0, "the prior module scroll can put the formal toolbar above the viewport");
a.state.page = 5; a.state.query = "同一查询";
const cachedState = plain(a.state), mountedBefore = a.counts().mounted;
a.workspace.switchTab("home"); mainTop = 223;
a.click("tab", { key });
assert.equal(mainTop, 0); assert.equal(a.bridge.layer.style.top, "100px", "toolbar is aligned after the outer scroll resets");
assert.equal(a.bridge.frames.get(key), aEntry); assert.equal(a.counts().mounted, mountedBefore); assert.equal(a.counts().stopped, 0);
assert.deepEqual(plain(a.state), cachedState, "reveal preserves dirty input, query/page and request identity");
mainTop = 223; a.click("open-page", { module: "人事", page: "人事档案" });
assert.equal(mainTop, 0, "opening the already active page also reveals its toolbar");
assert.equal(a.bridge.frames.get(key), aEntry); assert.equal(a.counts().mounted, mountedBefore);
const writesAtTop = scrollWrites; a.workspace.switchTab(key);
assert.equal(scrollWrites, writesAtTop, "an already visible page does not write its scroll position");
mainTop = 223; const positionBeforeInvalid = a.bridge.layer.style.top;
a.workspace.switchTab("invalid:no-tab");
assert.equal(mainTop, 223); assert.equal(a.workspace.activeKey, key); assert.equal(a.bridge.layer.style.top, positionBeforeInvalid);
assert.equal(scrollWrites, writesAtTop, "invalid navigation cannot scroll the current page");
a.workspace.host.closest = () => null;
assert.doesNotThrow(() => a.workspace.switchTab(key), "a workspace without an outer scroller keeps normal navigation");
assert.equal(mainTop, 223); assert.equal(a.bridge.frames.get(key), aEntry); assert.equal(a.counts().mounted, mountedBefore);
route = "jifeng-legacy";
b.workspace.host.closest = () => mainSection; b.workspace.switchTab(key);
assert.equal(mainTop, 223, "B retains its existing scroll behavior");
for (const [currentRoute, visible, hidden] of [["jifeng-legacy", b, a], ["jifeng-modern", a, b]]) {
	route = currentRoute; b.bridge.align(); a.bridge.align();
	assert.equal(visible.bridge.layer.hidden, false); assert.equal(visible.bridge.frames.get(key).container.hidden, false);
	assert.equal(hidden.bridge.layer.hidden, true); assert.equal(hidden.bridge.frames.get(key).container.hidden, true);
	assert.equal(visible.state.visible, true); assert.equal(hidden.state.visible, false);
}
route = "query-report"; b.bridge.align(); a.bridge.align();
assert.equal(b.bridge.layer.hidden, true); assert.equal(a.bridge.layer.hidden, true);
assert.equal(b.state.visible, false); assert.equal(a.state.visible, false);
for (const [model, currentRoute] of [[b, "jifeng-legacy"], [a, "jifeng-modern"]]) {
	route = currentRoute;
	const entry = model.bridge.frames.get(key), before = confirmations.length;
	model.filters.set("[data-search]", { value: "未查询筛选" });
	model.workspace.switchTab("home"); assert.equal(entry.container.hidden, true);
	assert.equal(model.state.visible, false);
	assert.equal(model.bridge.frames.get(key), entry); assert.equal(model.counts().stopped, 0);
	model.filters.clear(); model.click("tab", { key });
	assert.equal(model.workspace.search, "未查询筛选"); assert.equal(model.bridge.frames.get(key), entry);
	assert.equal(model.counts().mounted, 1); assert.equal(entry.container.hidden, false);
	assert.equal(model.state.visible, true);
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

async function dashboardReportScope() {
	for (const Workspace of [Classic, Modern]) {
		const workspace = Object.create(Workspace.prototype), opened = [], requests = [];
		workspace.activeTab = () => ({ module: "销售" });
		workspace.dashboardRequest = 1;
		workspace.dashboard = { company: "VISIBLE", from_date: "2026-10-03", to_date: "2026-10-08",
			financial_reports: [{ name: "General Ledger", label: "总账" }] };
		frappe.call = async args => {
			requests.push(plain(args));
			return { message: { ...workspace.dashboard, ...args.args } };
		};
		await workspace.loadDashboard({ company: "VISIBLE", from_date: "2026-10-01", to_date: "2026-10-31" });
		await workspace.loadDashboard(); // Return to the dashboard from a report.
		assert.deepEqual(requests[1].args, { company: "VISIBLE", from_date: "2026-10-01", to_date: "2026-10-31" });
		assert.equal(workspace.dashboard.to_date, "2026-10-31");
		workspace.dashboard.from_date = "2026-10-03"; workspace.dashboard.to_date = "2026-10-08";
		requests.length = 0;
		workspace.formalBridge = { open: value => opened.push(plain(value)) };
		const result = { route: ["query-report", "General Ledger"], filters: { company: "VISIBLE",
			from_date: "2026-10-03", to_date: "2026-10-08", show_cancelled_entries: 0 } };
		frappe.call = async args => { requests.push(plain(args)); return { message: result }; };
		await workspace.openDashboardReport("General Ledger");
		assert.deepEqual(requests[0], { method: "jifeng_erp.dashboard.financial_report", type: "GET",
			args: { name: "General Ledger", company: "VISIBLE", from_date: "2026-10-03", to_date: "2026-10-08" } });
		assert.deepEqual(opened[0], { title: "总账", module: "总经理", kind: "report", ...result });
		await workspace.openDashboardReport("Hidden Report");
		assert.equal(requests.length, 1, "a report absent from the permitted result cannot be opened");

		let finish;
		frappe.call = () => new Promise(resolve => { finish = resolve; });
		const pending = workspace.openDashboardReport("General Ledger");
		workspace.dashboardRequest++;
		workspace.dashboard = { ...workspace.dashboard, company: "OTHER" };
		finish({ message: result }); await pending;
		assert.equal(opened.length, 1, "a late response cannot open the previous company's report");

		workspace.dashboard.company = "VISIBLE";
		const before = messages.length;
		frappe.call = async () => { throw new Error("permission revoked"); };
		await workspace.openDashboardReport("General Ledger");
		assert.equal(opened.length, 1, "native permission failure must not open a cached report");
		assert.equal(messages.length, before + 1);
	}
	console.log("A/B dashboard report company/date scope, stale response and permission refusal passed (protocol only).");
}
async function companyWorkbenchLifecycle() {
	const retailCalls = [];
	for (const Workspace of [Classic, Modern]) {
		const workspace = Object.create(Workspace.prototype), calls = [];
		workspace.formalBridge = { open: spec => calls.push(plain(spec)) };
		frappe.db = { get_single_value: async (doctype, field) => {
			assert.equal(doctype, "POS Settings"); assert.equal(field, "invoice_type"); return "POS Invoice";
		} };
		workspace.openPage("零售单记录", "销售"); await new Promise(resolve => setTimeout(resolve, 0));
		assert.equal(calls.length, 1); assert.deepEqual(calls[0].route, ["List", "POS Invoice"]);
		retailCalls.push(calls);
	}
	assert.deepEqual(retailCalls[0], retailCalls[1]);
	const components = [];
	class CompanyComponent {
		static mount(host, options) {
			const instance = { host, options, ready: Promise.resolve(true), destroyed: 0, refreshed: 0,
				destroy() { this.destroyed++; }, refresh() { this.refreshed++; }, setVisible(value) { this.visible = value; } };
			components.push(instance); return instance;
		}
	}
	window.JifengProductionInstructionList = CompanyComponent;
	window.JifengSubcontractingReversal = CompanyComponent;
	let control;
	frappe.defaults = { get_user_default: () => "DEFAULT" };
	frappe.ui = { form: { make_control({ df }) {
		const listeners = new Map();
		control = { value: "", typed: "", df, removed: false,
			get_value() { return this.value; }, get_input_value() { return this.typed; },
			async set_value(value) { this.value = value; this.typed = value; df.onchange(); },
			$input: { on(events, callback) { listeners.set(events, callback); }, off() { control.removed = true; } },
			type(value, accept = false) { this.typed = value; if (accept) this.value = value;
				for (const callback of listeners.values()) callback(); },
		};
		return control;
	} } };
	for (const Workspace of [Classic, Modern]) {
		for (const [module, name, flag, value] of connectedEntries.filter(entry => entry[2] !== "subcontractingReconciliation")) {
			const bridge = Object.create(window.JifengClassicBridge.prototype), opened = [], created = [];
			bridge.openCompanySource = async (...values) => { opened.push(values); return true; };
			bridge.openCreate = async spec => { created.push(spec); return true; };
			const spec = dispatch(Workspace, find(module, name))[0].spec;
			const entry = bridge.mount(new Element(), spec); await entry.ready;
			const initial = components.at(-1), originalOptions = initial.options, field = control;
			assert.equal(initial.options.company, "DEFAULT"); assert.equal(initial.options.kind, flag === "subcontractingReversal" ? value : undefined);
			assert.equal(entry.workbench.isDirty(), false); assert.equal(entry.workbench.getCloseBlock(), "");
			field.type("OTHER");
			assert.equal(initial.destroyed, 1, "typing a new company disposes the former company's rows and pending response identity");
			assert.equal(components.at(-1), initial, "unaccepted Link input cannot start another query");
			assert.equal(await originalOptions.openForm("Production Plan", "OLD"), false);
			assert.equal(await originalOptions.newForm("Production Plan"), false);
			assert.equal(opened.length, 0); assert.equal(created.length, 0);
			field.type("OTHER", true);
			const current = components.at(-1);
			assert.notEqual(current, initial); assert.equal(current.options.company, "OTHER");
			await current.options.openForm("Production Plan", "CURRENT");
			assert.equal(opened[0][1], "OTHER"); assert.equal(opened[0][2], "Production Plan"); assert.equal(opened[0][3], "CURRENT");
			await current.options.newForm("Production Plan", { company: "WRONG", remark: "保留默认字段" });
			assert.deepEqual(plain(created[0].values), { company: "OTHER", remark: "保留默认字段" });
			entry.workbench.setVisible(false); assert.equal(current.visible, false);
			assert.equal(await current.options.openForm("Production Plan", "HIDDEN"), false);
			entry.workbench.setVisible(true); entry.workbench.setVisible(true);
			assert.equal(current.refreshed, 1, "returning from a native source refreshes once without resetting filters");
			entry.workbench.refresh(); assert.equal(current.refreshed, 2);
			entry.stop(); entry.stop(); assert.equal(current.destroyed, 1); assert.equal(field.removed, true);
			assert.equal(await current.options.newForm("Production Plan"), false, "destroyed views cannot open a native draft");
		}
	}
	window.JifengSubcontractingReconciliation = class {
		constructor(host, bridge, kind) { this.host = host; this.bridge = bridge; this.kind = kind; this.ready = Promise.resolve(true); }
		destroy() { this.destroyed = true; }
	};
	const bridge = Object.create(window.JifengClassicBridge.prototype);
	for (const [module, name, flag, kind] of connectedEntries.filter(entry => entry[2] === "subcontractingReconciliation")) {
		assert.equal(flag, "subcontractingReconciliation");
		const entry = bridge.mount(new Element(), dispatch(Modern, find(module, name))[0].spec); await entry.ready;
		assert.equal(entry.workbench.kind, kind); assert.equal(entry.workbench.bridge, bridge);
		bridge.frames = new Map([[name, entry]]);
		assert.equal(bridge.canClose(name), true); assert.equal(bridge.closeBlock(name), "");
		entry.stop(); assert.equal(entry.workbench.destroyed, true);
	}
	const frm = { doctype: "Quality Inspection", docname: "QI", doc: { doctype: "Quality Inspection", name: "QI", company: "OTHER" }, cscript: { is_onload: false } };
	const native = { iframe: { contentWindow: { frappe: { get_route: () => ["Form", "Quality Inspection", "QI"] }, cur_frm: frm } } };
	bridge.frames = new Map([["source", native]]);
	assert.equal(bridge.nativeForm(native, "source", { doctype: "Quality Inspection", name: "QI", company: "DEFAULT" }), null);
	assert.equal(bridge.nativeForm(native, "source", { doctype: "Quality Inspection", name: "QI", company: "OTHER" }), frm);
	const sourceSpec = { title: "生产指令单", module: "生产" }, sourceEntry = { ready: Promise.resolve(true) }, routes = [];
	bridge.open = spec => routes.push(plain(spec));
	bridge.frames = new Map([["formal:生产:PLAN", sourceEntry]]);
	bridge.waitNativeForm = async (entry, key, target) => {
		assert.equal(entry, sourceEntry); assert.equal(key, "formal:生产:PLAN");
		assert.deepEqual(plain(target), { doctype: "Production Plan", name: "PLAN", company: "OTHER" }); return frm;
	};
	assert.equal(await bridge.openCompanySource(sourceSpec, "OTHER", "Production Plan", "PLAN"), true);
	assert.deepEqual(routes, [{ title: "PLAN", module: "生产", kind: "form", doctype: "Production Plan", route: ["Form", "Production Plan", "PLAN"], company: "OTHER" }]);
	const draft = { doctype: "Production Plan", docname: "new-plan", doc: { doctype: "Production Plan", name: "new-plan",
		company: "OTHER", docstatus: 0, __islocal: 1 }, cscript: { is_onload: false } };
	const draftFrame = { contentWindow: { frappe: { get_route: () => ["Form", "Production Plan", "new-plan"] }, cur_frm: draft } };
	assert.equal(bridge.nativeDraft(draftFrame, { doctype: "Production Plan", values: { company: "OTHER" } }), draft);
	assert.equal(bridge.nativeDraft(draftFrame, { doctype: "Production Plan", values: { company: "DEFAULT" } }), null);
	draft.cscript.is_onload = true;
	assert.equal(bridge.nativeDraft(draftFrame, { doctype: "Production Plan", values: { company: "OTHER" } }), null);
	draft.cscript.is_onload = false; delete draft.doc.__islocal;
	assert.equal(bridge.nativeDraft(draftFrame, { doctype: "Production Plan", values: { company: "OTHER" } }), null);
	console.log("A/B production/reversal company mounting, native callbacks, remount/refresh/disposal and reconciliation/source company guards passed (protocol only).");
}
companyWorkbenchLifecycle().then(dashboardReportScope).catch(error => { console.error(error); process.exitCode = 1; });
