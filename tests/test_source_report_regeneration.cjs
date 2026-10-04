// Real optional asset, native failure-Promise/late jqXHR shape; no browser/RPC.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("jifeng_erp/public/js/jifeng_source_reports.js", "utf8");
const drain = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function fixture(name = "Open Work Orders", options = {}) {
	const calls = [], timers = new Map(), buttons = [];
	let route = ["query-report", name], target = { eligible: true, name: "OLD" };
	let onRegenerate, onTarget, sequence = 0;
	class Request {
		always(fn) { this.listener = fn; if (this.completed) fn(...this.completed); return this; }
		complete(data = {}, status = "error") { this.completed = [data, status]; this.listener?.(data, status); }
	}
	class Report {
		constructor() {
			this.report_name = name; this.prepared_report_name = options.dn || null;
			this.report_doc = { report_type: options.custom ? "Custom Report" : "Query Report",
				reference_report: options.custom ? "INTERMEDIATE CUSTOM" : null };
			this.report_settings = {};
			this.filters = [{ default: "JF", value: "JF" }]; this.values = { company: "JF" };
			this.page = { set_primary_action: (label, click) => {
				const button = { label, click, disabled: false, prop: (_name, value) => { button.disabled = value; } };
				buttons.push(button); return button;
			} };
			this.loading = true;
		}
		get_filter_values() { return this.values; }
		refresh() {
			this.last_ajax = new Request();
			// Frappe resolves only callback on HTTP success. Failure stays pending.
			this.nativePromise = new Promise(() => {}); return this.nativePromise;
		}
		hide_loading_screen() { this.loading = false; }
	}
	const report = new Report();
	if (options.existingOwnRefresh) report.refresh = Report.prototype.refresh.bind(report);
	const frappe = { views: options.lazy ? {} : { QueryReport: Report }, query_reports: {}, query_report: report,
		get_route: () => route, hide_progress: () => calls.push(["hide_progress"]),
		xcall: (method, args) => {
			calls.push([method, args]);
			if (method.endsWith("get_regeneration_target")) return onTarget ? onTarget(args) : Promise.resolve(target);
			return onRegenerate ? onRegenerate(args) : Promise.resolve({ name: "NEW" });
		},
		set_route: (...next) => { calls.push(["route", next]); route = next; },
		show_alert: value => calls.push(["alert", value]),
		utils: { throttle: (fn, milliseconds) => { assert.equal(milliseconds, 300); return fn; } },
		router: { on: (_name, fn) => { frappe.routeListener = fn; } },
	};
	if (options.custom) frappe.query_reports["Open Work Orders"] = report.report_settings;
	const window = {};
	vm.runInNewContext(source, { window, frappe, crypto: { randomUUID: () => "123e4567-e89b-42d3-a456-426614174000" },
		Uint8Array, clearInterval: () => {}, setTimeout: fn => { timers.set(++sequence, fn); return sequence; },
		clearTimeout: id => timers.delete(id), });
	return { report, frappe, window, calls, buttons, timers, Report,
		setRoute: value => { route = value; },
		setTarget: value => { target = value; },
		setOnTarget: fn => { onTarget = fn; }, setOnRegenerate: fn => { onRegenerate = fn; },
		targetCalls: () => calls.filter(call => call[0].endsWith("get_regeneration_target")),
		regenCalls: () => calls.filter(call => call[0].endsWith("regenerate_prepared_report")),
	};
}
(async () => {
	{
		const f = fixture("Other Report"); const promise = f.report.refresh();
		assert.equal(promise, f.report.nativePromise); f.report.last_ajax.complete(); await drain();
		assert.equal(f.calls.length, 0); assert.equal(f.report.loading, true);
	}
	{
		const f = fixture("Open Work Orders", { dn: "OLD" });
		const promise = f.report.refresh(); f.report.last_ajax.complete({ exception: "permission" }); await drain();
		assert.equal(promise, f.report.nativePromise); assert.equal(f.report.loading, false);
		assert.equal(f.targetCalls().length, 1); assert.equal(f.targetCalls()[0][1].prepared_report_name, "OLD");
		assert.equal(f.buttons.length, 1); assert.equal(f.buttons[0].label, "按原筛选重新生成");
		await f.buttons[0].click();
		assert.equal(f.regenCalls().length, 1); assert.equal(f.report.prepared_report_doc_name, "NEW");
		assert.equal(JSON.stringify(f.calls.find(call => call[0] === "route")[1]), '["Form","Prepared Report","NEW"]');
		assert.equal(f.regenCalls()[0][1].prepared_report_name, "OLD");
	}
	{
		const f = fixture(); f.report.refresh(); f.report.last_ajax.complete(); await drain();
		assert.equal(f.targetCalls()[0][1].prepared_report_name, null);
		assert.equal(f.targetCalls()[0][1].filters, '{"company":"JF"}');
		assert.equal(f.targetCalls()[0][1].are_default_filters, true);
	}
	{
		const f = fixture("Custom WO", { custom: true }); f.report.refresh(); f.report.last_ajax.complete(); await drain();
		assert.equal(f.targetCalls().length, 1); assert.equal(f.targetCalls()[0][1].report_name, "Custom WO");
	}
	{
		const f = fixture("Unrelated Custom", { custom: true }); f.frappe.query_reports = {};
		f.report.refresh(); f.report.last_ajax.complete(); await drain(); assert.equal(f.targetCalls().length, 0);
	}
	{
		const f = fixture(); f.setTarget({ eligible: false });
		f.report.refresh(); f.report.last_ajax.complete(); await drain(); assert.equal(f.buttons.length, 0);
	}
	{
		const f = fixture(); f.report.refresh(); f.report.last_ajax.complete({ message: { result: [[1]] } }, "success");
		await drain(); assert.equal(f.targetCalls().length, 0);
		f.report.refresh(); f.report.last_ajax.complete({}, "abort"); await drain(); assert.equal(f.targetCalls().length, 0);
	}
	{
		const f = fixture(); let resolve; f.setOnTarget(() => new Promise(done => { resolve = done; }));
		f.report.refresh(); f.report.last_ajax.complete(); f.setRoute(["query-report", "Other Report"]);
		resolve({ eligible: true, name: "OLD" }); await drain(); assert.equal(f.buttons.length, 0);
	}
	{
		const f = fixture(); f.report.refresh(); const old = f.report.last_ajax;
		f.report.refresh(); old.complete(); await drain(); assert.equal(f.targetCalls().length, 0);
		f.report.last_ajax.complete(); await drain(); assert.equal(f.buttons.length, 1);
		f.report.values = { company: "OTHER" }; await f.buttons[0].click(); assert.equal(f.regenCalls().length, 0);
	}
	{
		const f = fixture(); f.report.refresh(); f.report.last_ajax.complete(); await drain();
		let finish; f.setOnRegenerate(() => new Promise(resolve => { finish = resolve; }));
		const first = f.buttons[0].click(); await f.buttons[0].click(); assert.equal(f.regenCalls().length, 1);
		f.setRoute(["query-report", "Other Report"]); finish({ name: "NEW" }); await first;
		assert.equal(f.calls.filter(call => call[0] === "route").length, 0);
	}
	{
		const f = fixture(); f.report.refresh(); f.report.last_ajax.complete(); await drain();
		f.setOnRegenerate(() => Promise.reject(new Error("response lost"))); await f.buttons[0].click();
		assert.equal(f.buttons[0].disabled, false);
		f.setOnRegenerate(() => Promise.resolve({ name: "RECOVERED" })); await f.buttons[0].click();
		assert.equal(f.regenCalls().length, 2);
		assert.equal(f.regenCalls()[0][1].regeneration_request_id, f.regenCalls()[1][1].regeneration_request_id);
		assert.equal(f.report.prepared_report_doc_name, "RECOVERED");
	}
	{
		const f = fixture("Open Work Orders", { existingOwnRefresh: true });
		assert.equal(f.window.JifengSourceReports.install(), true);
		f.report.refresh(); f.report.last_ajax.complete(); await drain(); assert.equal(f.targetCalls().length, 1);
	}
	{
		const f = fixture("Open Work Orders", { lazy: true }); assert.equal(f.timers.size, 1);
		f.frappe.views.QueryReport = f.Report; [...f.timers.values()][0]();
		f.report.refresh(); f.report.last_ajax.complete(); await drain(); assert.equal(f.buttons.length, 1);
	}
	console.log("13 source regeneration models passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
