// Reproduce the native financial-mode callback querying before required dates.
const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const window = {};
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), { window });
const bridge = Object.create(window.JifengClassicBridge.prototype);

for (const name of ["Profit and Loss Statement", "Cash Flow", "Balance Sheet"]) {
	const calls = [], displays = [], values = {};
	class Report {
		setup_filters() {
			this.mode = { on_change: () => {
				displays.push(values.filter_based_on);
				this.refresh(); // Native on_change ignores _no_refresh.
			} };
		}
		get_filter(name) { assert.equal(name, "filter_based_on"); return this.mode; }
		refresh() {
			assert.ok(values.period_start_date && values.period_end_date, "query must have both dates");
			calls.push({ ...values });
		}
	}
	const child = { frappe: { views: { QueryReport: Report } } };
	const nativeSetup = Report.prototype.setup_filters;
	bridge.guardFinancialReportInitialization(child, { kind: "report", route: ["query-report", name],
		filters: { filter_based_on: "Date Range", period_start_date: "2026-10-01", period_end_date: "2026-10-31" } });
	assert.notEqual(Report.prototype.setup_filters, nativeSetup);
	const report = new Report(); report.report_name = name; report.setup_filters();
	const refresh = report.refresh;
	report._no_refresh = true;
	values.filter_based_on = "Date Range";
	report.mode.on_change();
	assert.deepEqual(displays, ["Date Range"], "native display changes still run");
	assert.equal(calls.length, 0, "initial filter mode must not send an incomplete query");
	assert.equal(report.refresh, refresh);
	values.period_start_date = "2026-10-01"; values.period_end_date = "2026-10-31";
	report._no_refresh = false; report.refresh();
	assert.equal(calls.length, 1); assert.equal(calls[0].period_end_date, "2026-10-31");
	values.period_end_date = "2026-11-30"; report.mode.on_change();
	assert.equal(calls.length, 2, "later user changes continue native refresh");
	assert.equal(calls[1].period_end_date, "2026-11-30");
	// A failed display callback must not leave refresh suppressed.
	report.setup_filters = Report.prototype.setup_filters;
	report.mode.on_change = undefined;
	class ThrowingReport extends Report {
		setup_filters() { this.mode = { on_change() { throw new Error("display failed"); } }; }
	}
	const failingChild = { frappe: { views: { QueryReport: ThrowingReport } } };
	bridge.guardFinancialReportInitialization(failingChild, { kind: "report", route: ["query-report", name], filters: { filter_based_on: "Date Range" } });
	const failing = new ThrowingReport(); failing.report_name = name; failing.setup_filters(); failing._no_refresh = true;
	const failingRefresh = failing.refresh;
	assert.throws(() => failing.mode.on_change(), /display failed/); assert.equal(failing.refresh, failingRefresh);
}

class OtherReport { setup_filters() {} }
const native = OtherReport.prototype.setup_filters;
const other = { frappe: { views: { QueryReport: OtherReport } } };
for (const spec of [
	{ kind: "report", route: ["query-report", "General Ledger"], filters: {} },
	{ kind: "list", route: ["List", "Employee"] },
	{ kind: "report", route: ["query-report", "Profit and Loss Statement"], filters: { filter_based_on: "Fiscal Year" } },
]) bridge.guardFinancialReportInitialization(other, spec);
assert.equal(OtherReport.prototype.setup_filters, native, "other routes and fiscal-year reports are untouched");
console.log("financial report initialization: passed");
