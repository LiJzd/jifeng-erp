const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const window = {};
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), { window });
const bridge = Object.create(window.JifengClassicBridge.prototype);
let requestedFilters = null;
class QueryReport {
	constructor() {
		this.report_name = "Stock Ledger";
		this.filters = { company: "QA", from_date: "2026-10-08", to_date: "2026-10-08" };
		this.reads = 0;
		this.steps = [];
	}
	setup_progress_bar() { this.steps.push("progress"); }
	refresh(...args) { this.steps.push("refresh"); this.refreshArgs = args; this.reads++; requestedFilters = { ...this.filters }; }
}
const report = new QueryReport();
const child = { frappe: { views: { QueryReport }, query_report: report,
	get_route: () => ["query-report", "Stock Ledger"] } };
const iframe = { isConnected: true, classList: { contains: name => name === "is-ready" }, contentWindow: child };
const reportTab = { key: "formal:inventory:stock-ledger", spec: { kind: "report", route: ["query-report", "Stock Ledger"] } };
bridge.workspace = { tabs: [reportTab], activeKey: reportTab.key, esc: value => String(value), host: { contains: () => true } };
bridge.frames = new Map([[reportTab.key, { iframe }]]);

const html = bridge.content({ ...reportTab, title: "库存明细" });
assert.match(html, /data-jfe-report-refresh="formal:inventory:stock-ledger"/);
assert.match(html, />刷新报表<\/button>/);

let prevented = false, stopped = false;
bridge.reportRefreshClick({
	target: { closest: selector => selector === "[data-jfe-report-refresh]" ? { dataset: { jfeReportRefresh: reportTab.key } } : null },
	preventDefault: () => { prevented = true; },
	stopPropagation: () => { stopped = true; },
});
assert.equal(prevented, true);
assert.equal(stopped, true);
assert.equal(report.reads, 1, "a click starts a new native report read");
assert.deepEqual(report.steps, ["progress", "refresh"], "the bridge follows Frappe's native Reload Report action");
assert.deepEqual(report.refreshArgs, [], "the native action passes no flags that change prepared-report/filter semantics");
assert.deepEqual(requestedFilters, { company: "QA", from_date: "2026-10-08", to_date: "2026-10-08" },
	"refresh retains the filters already visible in the native report");

assert.doesNotMatch(bridge.content({ ...reportTab, spec: { kind: "form", route: ["Form", "Stock Entry", "STE-1"] } }),
	/data-jfe-report-refresh/, "forms do not get the report refresh control");
const formTab = { key: "form:stock-entry", spec: { kind: "form", route: ["Form", "Stock Entry", "STE-1"] } };
bridge.workspace.tabs = [formTab]; bridge.workspace.activeKey = formTab.key;
bridge.frames.set(formTab.key, { iframe: { isConnected: true, classList: { contains: () => true },
	contentWindow: { frappe: { cur_frm: { refresh: () => { throw new Error("form must not refresh"); } } } } } });
assert.equal(bridge.refreshReport(formTab.key), false);
assert.equal(report.reads, 1, "switching to a form cannot refresh or reload it");

bridge.workspace.tabs = [reportTab]; bridge.workspace.activeKey = reportTab.key;
iframe.classList.contains = () => false;
assert.equal(bridge.refreshReport(reportTab.key), false, "a report that is not ready cannot be refreshed");
iframe.classList.contains = name => name === "is-ready";
child.frappe.get_route = () => ["query-report", "Another Report"];
assert.equal(bridge.refreshReport(reportTab.key), false, "a stale report route is not refreshed");
assert.equal(report.reads, 1);

console.log("Classic report refresh: native instance, live route, filter retention, and form safety passed.");
