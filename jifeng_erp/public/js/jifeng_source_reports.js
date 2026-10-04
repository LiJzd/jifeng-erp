/* Source report regeneration uses server authority; it never reads legacy bytes. */
(() => {
	if (window.JifengSourceReports) return;
	const known = new Set([
		"Open Work Orders", "Completed Work Orders", "Work Orders in Progress",
		"Issued Items Against Work Order", "Work Order Summary", "Production Analytics",
		"Sales Order Analysis", "Lost Quotations", "Quotation Trends", "Sales Order Trends",
	]);
	const targetMethod = "jifeng_erp.sample_source_prepared_reports.get_regeneration_target";
	const regenerateMethod = "jifeng_erp.sample_source_prepared_reports.regenerate_prepared_report";
	let timer = null;
	const route = () => JSON.stringify(frappe.get_route?.() || []);
	const scoped = report => known.has(report.report_name) || (
		report.report_doc?.report_type === "Custom Report" && (
			known.has(report.report_doc.reference_report) || [...known].some(name =>
				frappe.query_reports?.[name] && frappe.query_reports[name] === report.report_settings)
		)
	);
	const visibleFilters = report => JSON.stringify(report.get_filter_values(false) || {});
	function current(state) {
		return frappe.query_report === state.report && state.report.report_name === state.name &&
			route() === state.route && state.report.last_ajax === state.request &&
			state.report.__jf_source_refresh === state && visibleFilters(state.report) === state.filters;
	}
	function failed(data, status) {
		if (status === "abort") return false;
		return ["error", "timeout", "parsererror"].includes(status) ||
			Boolean(data?.exc || data?.exception || data?.responseJSON?.exc || data?.responseJSON?.exception);
	}
	function nonce() {
		if (crypto.randomUUID) return crypto.randomUUID();
		const bytes = crypto.getRandomValues(new Uint8Array(16));
		bytes[6] = (bytes[6] & 15) | 64;
		bytes[8] = (bytes[8] & 63) | 128;
		const value = [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
		return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
	}
	function finishFailure(state) {
		if (!current(state)) return;
		const report = state.report;
		report.hide_loading_screen?.();
		frappe.hide_progress?.();
		clearInterval(report.interval);
		clearInterval(report.stale_report_interval);
		report.primary_button?.prop("disabled", false);
		frappe.xcall(targetMethod, {
			report_name: state.name, prepared_report_name: state.oldName || null,
			filters: state.filters, are_default_filters: state.defaults,
		}).then(target => {
			if (!current(state) || !target?.eligible || typeof target.name !== "string") return;
			state.target = target.name;
			let button;
			const generate = async () => {
				if (!current(state) || state.pending) return;
				state.pending = true;
				button?.prop("disabled", true);
				try {
					// Keep this nonce after a lost response. The server rechecks the
					// exact derived row, rather than starting another queue job.
					state.nonce ||= nonce();
					const result = await frappe.xcall(regenerateMethod, {
						prepared_report_name: state.target, regeneration_request_id: state.nonce,
					});
					if (!current(state) || typeof result?.name !== "string") return;
					report.prepared_report_doc_name = result.name;
					// Native report_generated handles this exact name; the status
					// link also works before the background task finishes.
					frappe.show_alert({ message: "报表已进入生成队列", indicator: "orange" }, 10);
					frappe.set_route("Form", "Prepared Report", result.name);
				} catch (_error) {
					// Native RPC shows its error. A retry keeps the same identity;
					// no error text/HTTP status confers a permission or a new nonce.
				} finally {
					state.pending = false;
					if (current(state)) button?.prop("disabled", false);
				}
			};
			button = report.page.set_primary_action("按原筛选重新生成", generate);
			report.primary_button = button;
		}).catch(() => {});
	}
	function observe(report, request, args) {
		if (!request?.always || !scoped(report)) return;
		const state = {
			report, request, name: report.report_name, route: route(),
			oldName: report.prepared_report_name || null, filters: visibleFilters(report),
			defaults: (report.filters || []).every(filter => !args[0] && (
				filter.default === filter.value || (!filter.default && !filter.value))),
		};
		report.__jf_source_refresh = state;
		request.always((data, status) => {
			if (failed(data, status)) finishFailure(state);
		});
	}
	function install() {
		const prototype = frappe.views?.QueryReport?.prototype;
		if (!prototype || typeof prototype.refresh !== "function") return false;
		if (prototype.__jf_source_regeneration_installed) return true;
		const nativeRefresh = prototype.refresh;
		prototype.refresh = function (...args) {
			const previous = this.last_ajax;
			const result = nativeRefresh.apply(this, args);
			if (this.last_ajax !== previous) observe(this, this.last_ajax, args);
			return result;
		};
		prototype.__jf_source_regeneration_installed = true;
		// init creates a throttled own refresh before this optional asset may
		// load. Rebuild that exact native 300 ms throttle for an existing view.
		const existing = frappe.query_report;
		if (existing instanceof frappe.views.QueryReport && Object.hasOwn(existing, "refresh")) {
			existing.refresh = frappe.utils.throttle(prototype.refresh.bind(existing), 300);
		}
		if (existing && scoped(existing)) observe(existing, existing.last_ajax, []);
		return true;
	}
	function schedule(attempt = 0) {
		if (install() || attempt >= 30 || frappe.get_route?.()[0] !== "query-report") return;
		if (timer !== null) clearTimeout(timer);
		timer = setTimeout(() => { timer = null; schedule(attempt + 1); }, Math.min(1000, 25 * 2 ** attempt));
	}
	window.JifengSourceReports = { install };
	frappe.router?.on("change", () => schedule());
	schedule();
})();
