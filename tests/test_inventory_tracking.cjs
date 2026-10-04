// Actual shared classes against delayed DTO/DOM models; no GUI/native ledger proof.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
	constructor(tag) { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this.dataset = {}; this._text = ""; this.value = ""; this.classList = { add() {}, remove() {} }; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Tracking source text cannot execute HTML"); }
	append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
	replaceChildren(...nodes) { this._text = ""; for (const node of this.children) node.parent = null; this.children = []; this.append(...nodes); }
	remove() { if (this.parent) { this.parent.children = this.parent.children.filter(node => node !== this); this.parent = null; } }
	focus() { this.focused = true; }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, callback) { this.events.set(name, callback); }
	removeEventListener(name, callback) { if (this.events.get(name) === callback) this.events.delete(name); }
	click() { if (!this.disabled) this.events.get("click")?.(); }
	descendants(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
const plain = value => JSON.parse(JSON.stringify(value));
function harness() {
	const calls = [], pages = [], controls = [], window = {}, exports = [], downloads = [], revoked = [];
	const frappe = { csrf_token: "synthetic-csrf", defaults: { get_user_default: () => "集沣" }, datetime: { get_today: () => "2026-10-04" }, ui: { form: { make_control({ df }) {
		const control = { df, value: "", typed: "", handlers: new Map(), titleMap: {}, get_value() { return df.fieldtype === "Date" ? (this.dateMap?.[this.typed] || this.typed) : this.value; },
			get_input_value() { return this.titleMap[this.typed] || this.typed; }, async set_value(value) { this.value = value; this.typed = value; df.onchange(); },
			input(value) { this.typed = value; for (const action of this.handlers.values()) action(); } };
		control.$input = { on: (name, action) => control.handlers.set(name, action), off: () => control.handlers.clear(), val: () => control.typed };
		control.clearHandlers = new Map();
		control.$link_clear = { on: (name, action) => control.clearHandlers.set(name, action), off: () => control.clearHandlers.clear() };
		control.clear = () => { control.value = control.typed = ""; for (const action of control.clearHandlers.values()) action(); };
		controls.push(control); return control;
	} } }, call(options) {
		let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
		const request = { options: plain(options), resolve: message => resolve({ message }), reject, aborted: false };
		promise.abort = () => { request.aborted = true; }; calls.push(request); return promise;
	} };
	const forbidden = new Proxy({}, { get() { throw new Error("Stock DTO cannot enter browser storage"); } });
	const document = { body: new Element("body"), createElement(tag) {
		const node = new Element(tag); if (tag === "a") node.click = () => downloads.push({ href: node.href, filename: node.download }); return node;
	} };
	const fetch = (url, options) => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
		exports.push({ url, options, args: Object.fromEntries(options.body), resolve, reject }); return promise;
	};
	const context = vm.createContext({ window, document, frappe, fetch, AbortController, URLSearchParams, __: value => value,
		URL: { createObjectURL: () => "blob:synthetic-tracking", revokeObjectURL: url => revoked.push(url) }, setTimeout: fn => fn(),
		localStorage: forbidden, sessionStorage: forbidden, indexedDB: forbidden });
	for (const file of ["jifeng_inventory_catalog.js", "jifeng_inventory_tracking.js"])
		vm.runInContext(fs.readFileSync("jifeng_erp/public/js/" + file, "utf8"), context);
	return { calls, controls, pages, window, exports, downloads, revoked, frappe, bridge: { open: spec => pages.push(plain(spec)) } };
}
const basis = Object.fromEntries(["stock_qty", "valuation_rate", "stock_value", "stock_uom", "profile_weight", "profile_display_uom", "total_weight"].map(key => [key, "来源 " + key]));
function row(extra = {}) {
	return { profile_name: "PROFILE-0007", profile_version: "2026-10-04T00:00:00", item_code: "ITEM-0007", item_version: "2026-10-04T00:00:01",
		warehouse: "仓库一", warehouse_version: "2026-10-04T00:00:02", stock_uom: "Nos", stock_qty: "9007199254740993.01",
		valuation_rate: "-0E-20", stock_value: "1E+28", total_weight: null,
		profile: { legacy_number: "0007", display_name: '<img src=x onerror="bad()">', weight: "0.2300", display_uom: "对", notes: "原文\n保留" },
		image: { source_doctype: "Item", source_name: "ITEM-0007", source_field: "image", label: "关联业务商品图片", state: "未上传", url: "" }, ...extra };
}
function response(request, rows = [row()], extra = {}) {
	const args = request.options.args, extended = Object.hasOwn(args, "column_filters") ? {
		filter_protocol: 1, column_filters: JSON.parse(args.column_filters), sort: JSON.parse(args.sort) } : {};
	return { warehouse: "", ...args, ...extended, rows, currency: "CNY", basis, total: rows.length, total_pages: rows.length ? 1 : 0, readonly: true, can_write: false, ...extra };
}
async function make(kind = "物料", h = harness()) {
	const host = new Element("div"), view = new h.window.JifengInventoryTracking(host, kind, h.bridge); await view.ready; return { ...h, host, view };
}
async function populate(model, rows = [row()], extra = {}, page = 1) {
	const pending = model.view.query(page), request = model.calls.at(-1); request.resolve(response(request, rows, extra)); await pending;
}
const tests = [];
const test = (name, body) => tests.push([name, body]);
test("old column order and explicit native scope; blank warehouse is omitted", async () => {
	const m = await make(); assert.equal(m.calls.length, 0);
	assert.equal(m.host.descendants("th").map(th => th.textContent).join("/"), "仓库/编号/库存/单价/金额/重量/总重量/大类/小类/材质/单位/名称/规格/库位/备注/供应商/来源");
	await populate(m); assert.deepEqual(m.calls[0].options, { method: "jifeng_erp.inventory_tracking.query_tracking", type: "GET",
		args: { kind: "物料", company: "集沣", search: "", as_of: "2026-10-04", page: 1, page_size: 100 } });
	assert.deepEqual(plain(m.view.warehouseControl.get_query()), { filters: { company: "集沣", is_group: 0 } });
});
test("native precision and stock unit survive; no fabricated total weight or masked fields", async () => {
	const m = await make(); await populate(m); const cells = m.view.tbody.descendants("td");
	assert.deepEqual([2, 3, 4, 5, 6, 10].map(i => cells[i].textContent), ["9007199254740993.01", "-0E-20", "1E+28", "0.2300", "—", "Nos"]);
	assert.equal(cells[7].textContent, "—"); assert.match(cells[10].title, /档案单位：对/); assert.equal(m.host.descendants("img").length, 0);
	assert.equal(cells[11].textContent, '<img src=x onerror="bad()">'); assert.equal(cells[14].textContent, "原文\n保留");
});
test("material tracking has its own old fields and picture cards only repeat stock identity", async () => {
	const m = await make("材料");
	assert.equal(m.host.descendants("th").map(th => th.textContent).join("/"), "仓库/编号/库存/单价/金额/重量/总重量/小类/规格/形状/颜色/名称/库位/旧编号/负责/区域/供应商/来源");
	const source = row({ profile: { shape: "形状原文", color: "颜色原文", old_number: "旧0007", owner_name: "档案负责人" } });
	await populate(m, [source]);
	assert.deepEqual([9, 10, 13, 14].map(i => m.view.tbody.descendants("td")[i].textContent), ["形状原文", "颜色原文", "旧0007", "档案负责人"]);
	m.view.setMode("图片"); await populate(m, [source]);
	assert.equal(m.view.cards.descendants("dt").map(d => d.textContent).join("/"), "仓库/编号/库存");
});
test("same item in two warehouses stays two distinct native balances", async () => {
	const m = await make(); await populate(m, [row(), row({ warehouse: "仓库二", stock_qty: "0", stock_value: "0" })]);
	assert.equal(m.view.tbody.descendants("tr").length, 2); assert.match(m.view.count.textContent, /2/);
	await populate(m, [row(), row()]); assert.equal(m.view.rows.length, 0, "duplicate exact native pair invalidates the whole response");
});
test("whole page rejects foreign scope, native units, money and unexpected fields", async () => {
	for (const extra of [{ company: "OTHER" }, { kind: "材料" }, { as_of: "2026-10-03" }, { search: "OTHER" }, { warehouse: "OTHER" }, { currency: "" }, { can_write: true }, { extra_source: "secret" }]) {
		const m = await make(); await populate(m); await populate(m, [row()], extra); assert.equal(m.view.rows.length, 0);
	}
	for (const bad of [row({ stock_uom: "" }), row({ stock_qty: 3 }), row({ valuation_rate: "NaN" }), row({ stock_value: "Infinity" }), row({ total_weight: "3" }),
		row({ profile: { weight: 0 } }), row({ profile: { hidden_field: "hidden" } }), row({ voucher_no: "private" })]) {
		const m = await make(); await populate(m, [row(), bad]); assert.equal(m.view.tbody.descendants("td").length, 0);
	}
});
test("private image binding is exact; unsafe URLs cannot become image requests", async () => {
	const m = await make(); m.view.setMode("图片");
	const image = { ...row().image, state: "已上传", url: "/private/files/a.png?fid=FILE-1" };
	await populate(m, [row({ image })]); assert.equal(m.view.cards.descendants("img")[0].src, image.url);
	for (const value of [{ ...image, url: "https://other/image.png" }, { ...image, source_name: "OTHER" }, { ...image, url: "/private/files/%2e%2e%2fa.png?fid=f" }]) {
		await populate(m, [row({ image: value })]); assert.equal(m.host.descendants("img").length, 0);
	}
});
test("mode switch clears old quantities and images; query returns same scope in picture cards", async () => {
	const m = await make(); await populate(m); const args = m.calls[0].options.args;
	m.view.setMode("图片"); assert.equal(m.view.rows.length, 0); assert.equal(m.calls.length, 1); assert.equal(m.view.scroller.hidden, true);
	await populate(m); assert.deepEqual(m.calls[1].options.args, args); assert.equal(m.view.cards.descendants("article").length, 1);
	assert.ok(m.view.cards.textContent.includes("9007199254740993.01")); m.view.setMode("列表"); assert.equal(m.view.cards.textContent, "");
});
test("real pagination preserves total and rejects truncated or unsafe counts", async () => {
	const m = await make(); const rows = Array.from({ length: 100 }, (_, i) => row({ profile_name: "P-" + i, item_code: "I-" + i,
		image: { ...row().image, source_name: "I-" + i } }));
	await populate(m, rows, { total: 101, total_pages: 2 }); assert.equal(m.view.next.disabled, false);
	await populate(m, [row()], { total: 101, total_pages: 2 }, 2); assert.equal(m.view.page, 2); assert.match(m.view.count.textContent, /101/);
	await populate(m, [row()], { total: 101, total_pages: 2 }, 1); assert.equal(m.view.rows.length, 0);
	await populate(m, [row()], { total: Number.MAX_SAFE_INTEGER + 1, total_pages: 1 }); assert.equal(m.view.rows.length, 0);
	const calls = m.calls.length; await m.view.query("1e2"); assert.equal(m.calls.length, calls);
});
test("all filters drop data immediately and late responses cannot re-populate", async () => {
	for (const field of ["companyControl", "warehouseControl", "dateControl", "search"]) {
		const m = await make(); await populate(m); const pending = m.view.query(), request = m.calls.at(-1);
		if (field === "search") { m.view.search.value = "changed"; m.view.search.events.get("input")(); }
		else m.view[field].input(field === "dateControl" ? "2026-10-03" : "changed");
		assert.equal(request.aborted, true); request.resolve(response(request)); await pending; assert.equal(m.view.rows.length, 0);
	}
});
test("localized native controls resolve stored values and incomplete input never widens scope", async () => {
	const m = await make(); m.view.companyControl.titleMap["公司标题"] = "集沣"; m.view.companyControl.typed = "公司标题";
	m.view.dateControl.dateMap = { "04/10/2026": "2026-10-04" }; m.view.dateControl.typed = "04/10/2026";
	await m.view.warehouseControl.set_value("仓库一"); await populate(m); assert.equal(m.calls[0].options.args.warehouse, "仓库一");
	m.view.warehouseControl.typed = ""; await m.view.query(); assert.equal(m.calls.length, 1);
	await m.view.dateControl.set_value("2026-02-30"); await m.view.query(); assert.equal(m.calls.length, 1);
});
test("native Link clear without input/change drops stock immediately and invalidates pending replies", async () => {
	for (const field of ["companyControl", "warehouseControl"]) {
		const m = await make(); await m.view.warehouseControl.set_value("仓库一"); await populate(m);
		m.view[field].clear(); assert.equal(m.view.rows.length, 0); assert.equal(m.view.tbody.children.length, 0);
		assert.match(m.view.status.textContent, /重新查询/); assert.equal(m.calls.length, 1);
		await m.view.companyControl.set_value("集沣"); await m.view.warehouseControl.set_value("仓库一");
		const pending = m.view.query(), request = m.calls.at(-1); m.view[field].clear();
		assert.equal(request.aborted, true); request.resolve(response(request)); await pending; assert.equal(m.view.rows.length, 0);
		m.view.destroy(); assert.ok(m.controls.every(c => c.clearHandlers.size === 0));
	}
});
test("source opens exact native Form in original module and clears all stock output", async () => {
	for (const kind of ["物料", "材料"]) {
		const m = await make(kind); await populate(m); const actions = m.view.tbody.descendants("button");
		actions.find(button => button.textContent === "商品").click(); assert.deepEqual(m.pages[0].route, ["Form", "Item", "ITEM-0007"]); assert.equal(m.pages[0].module, kind);
		assert.equal(m.view.rows.length, 0); actions.find(button => button.textContent === "档案").click(); assert.equal(m.pages.length, 1);
		assert.equal(m.view.companyControl.get_value(), "集沣"); assert.equal(m.view.dateControl.get_value(), "2026-10-04");
	}
});
test("tab exit and disposal clear all data, disable reads and discard late replies", async () => {
	const m = await make(); await populate(m); const pending = m.view.query(), request = m.calls.at(-1);
	m.view.setVisible(false); assert.equal(request.aborted, true); assert.equal(m.view.rows.length, 0); await m.view.query(); assert.equal(m.calls.length, 2);
	m.view.setVisible(true); assert.equal(m.calls.length, 2); request.resolve(response(request)); await pending; assert.equal(m.view.rows.length, 0);
	await populate(m); m.view.destroy(); m.view.destroy(); assert.equal(m.host.textContent, ""); assert.ok(m.controls.every(c => c.handlers.size === 0));
	await m.view.query(); assert.equal(m.calls.length, 3); assert.equal(m.view.isDirty(), false); assert.equal(m.view.getCloseBlock(), "");
});
test("denied requests do not leak diagnostics or look like an empty balance", async () => {
	const m = await make(); await populate(m); const pending = m.view.query(); m.calls.at(-1).reject(new Error("private SQL source")); await pending;
	assert.equal(m.view.rows.length, 0); assert.match(m.view.status.textContent, /查询未完成/); assert.ok(!m.host.textContent.includes("private SQL source"));
	await populate(m, []); assert.match(m.view.status.textContent, /没有可见的库存历史/);
});
function archive(source = row(), kind = "物料", profileExtra = {}) {
	const profile = { company: "集沣", archive_kind: kind, item_code: source.item_code, inactive: 0, ...source.profile, ...profileExtra };
	const item = { item_code: source.item_code, stock_uom: source.stock_uom, is_stock_item: 1 };
	const metadata = values => Object.fromEntries(Object.keys(values).map(key => [key, {
		label: "字段 " + key, fieldtype: "Data", options: "", description: "", read_only: true, reqd: false }]));
	return { name: source.profile_name, kind, company: "集沣", item_code: source.item_code, profile_version: source.profile_version,
		item_version: source.item_version, profile, item, field_meta: { profile: metadata(profile), item: metadata(item) },
		image: source.image, readonly: true, can_write: false };
}
function archiveResponse(request, source = row(), extra = {}) {
	const args = request.options.args;
	return { kind: args.kind, company: args.company, search: args.search, warehouse: args.warehouse, as_of: args.as_of,
		currency: "CNY", row: source, archive: archive(source, args.kind), balances: [source], readonly: true, can_write: false, ...extra };
}
function ledgerRow(extra = {}) {
	return { name: "SLE-1", modified: "2026-10-04T01:01:00", posting_datetime: "2026-10-04 01:00:00", voucher_type: "Stock Reconciliation",
		voucher_no: "REC-0001", actual_qty: "-9007199254740993.01", qty_after_transaction: "0", valuation_rate: "-0E-20",
		stock_value: "0", stock_value_difference: "-1E+28", stock_uom: "Nos", is_adjustment_entry: 0, company: "集沣", ...extra };
}
function recordsResponse(request, source = row(), records = [ledgerRow()], extra = {}) {
	const args = request.options.args;
	return { kind: args.kind, company: args.company, search: args.search, warehouse: args.warehouse, as_of: args.as_of, currency: "CNY", row: source,
		page: args.page, page_size: args.page_size, total: records.length, total_pages: records.length ? 1 : 0, rows: records, readonly: true, can_write: false, ...extra };
}
function openDetail(model, mode = "archive") {
	const trigger = model.view.tbody.descendants("td")[mode === "archive" ? 1 : 2].descendants("button")[0];
	const pending = model.view.readTrackingDetail(model.view.rows[0], mode, trigger);
	return { pending, request: model.calls.at(-1), state: model.view.detail, trigger };
}
test("list and picture identity links request exact read-only detail without native Form navigation", async () => {
	for (const kind of ["物料", "材料"]) {
		const m = await make(kind); await populate(m);
		const button = m.view.tbody.descendants("td")[1].descendants("button")[0]; button.click(); const request = m.calls.at(-1);
		assert.deepEqual(request.options, { method: "jifeng_erp.inventory_tracking.read_tracking_archive", type: "GET", args: {
			kind, company: "集沣", warehouse: "仓库一", as_of: "2026-10-04", search: "", profile_name: "PROFILE-0007", item_code: "ITEM-0007",
			profile_version: row().profile_version, item_version: row().item_version, warehouse_version: row().warehouse_version } });
		request.resolve(archiveResponse(request)); await new Promise(setImmediate); assert.equal(m.pages.length, 0);
		m.view.closeDetail(); m.view.setMode("图片"); await populate(m);
		const stock = m.view.cards.descendants("dd")[2].descendants("button")[0]; stock.click(); const records = m.calls.at(-1);
		assert.equal(records.options.method, "jifeng_erp.inventory_tracking.query_tracking_records"); assert.equal(records.options.type, "GET");
		assert.equal(records.options.args.page_size, 100); records.resolve(recordsResponse(records)); await new Promise(setImmediate);
		assert.equal(m.view.detail.layer.attributes["aria-label"], kind + "跟踪记录"); assert.equal(m.pages.length, 0);
	}
});
test("drawer close and Escape restore focus and preserve parent rows, filters and page without another query", async () => {
	const m = await make(); m.view.search.value = "0007"; await populate(m, [row()], { total: 101, total_pages: 2 }, 2);
	const rows = m.view.rows, table = m.view.tbody.children[0], detail = openDetail(m);
	assert.ok(m.host.children.slice(0, -1).every(node => node.inert === true)); assert.equal(detail.state.close.focused, true);
	const otherWarehouse = row({ warehouse: "仓库二", warehouse_version: "2026-10-04T00:00:03", stock_qty: "0", stock_value: "0" });
	detail.request.resolve(archiveResponse(detail.request, row(), { balances: [row(), otherWarehouse] })); await detail.pending;
	assert.equal(detail.state.body.descendants("tr").length, 3); assert.ok(detail.state.body.textContent.includes("1E+28"));
	detail.state.layer.events.get("keydown")({ key: "Escape", preventDefault() {}, stopPropagation() {} });
	assert.equal(m.view.detail, null); assert.equal(detail.trigger.focused, true); assert.ok(m.host.children.every(node => node.inert !== true));
	assert.equal(m.view.rows, rows); assert.equal(m.view.tbody.children[0], table); assert.equal(m.view.page, 2); assert.equal(m.view.total, 101);
	assert.equal(m.view.search.value, "0007"); assert.equal(m.calls.length, 2);
});
test("masked archive fields and empty labels render only readable keys; private image stays exactly bound", async () => {
	const image = { ...row().image, state: "已上传", url: "/private/files/detail.png?fid=FILE-2" };
	const source = row({ profile: { display_name: "已读取名称" }, image }); const m = await make(); await populate(m, [source]);
	assert.equal(m.view.tbody.descendants("td")[1].descendants("button").length, 0, "masked number cannot become a guessed identity button");
	const detail = openDetail(m, "records"); detail.request.resolve(recordsResponse(detail.request, source)); await detail.pending; m.view.closeDetail();
	const pending = m.view.readTrackingDetail(m.view.rows[0], "archive"); const request = m.calls.at(-1), state = m.view.detail;
	const data = archiveResponse(request, source); data.archive.field_meta.profile.display_name.label = null;
	data.archive.field_meta.profile.hidden_field = { label: "不可见字段标签", read_only: true };
	request.resolve(data); await pending;
	assert.ok(state.body.textContent.includes("display_name")); assert.ok(!state.body.textContent.includes("不可见字段标签"));
	assert.ok(!state.body.textContent.includes("字段 weight")); assert.equal(state.body.descendants("img")[0].src, image.url);
});
test("archive detail rejects foreign identity, versions, currency, duplicate or missing balances and unknown keys", async () => {
	const changes = [data => { data.company = "OTHER"; }, data => { data.currency = "USD"; }, data => { data.warehouse = "OTHER"; },
		data => { data.row.item_version += "changed"; }, data => { data.archive.profile_version += "changed"; },
		data => { data.archive.name = "OTHER"; }, data => { data.archive.image.source_name = "OTHER"; },
		data => { data.balances = [row(), row()]; }, data => { data.balances = []; }, data => { data.balances[0].stock_qty = "3"; },
		data => { data.can_write = true; }, data => { data.secret = "private"; }];
	for (const change of changes) {
		const m = await make(); await populate(m); const detail = openDetail(m), data = plain(archiveResponse(detail.request)); change(data);
		detail.request.resolve(data); await detail.pending; assert.match(detail.state.body.textContent, /详情未能读取/); assert.equal(detail.state.body.descendants("td").length, 0);
		assert.equal(m.view.rows.length, 1, "denied detail must not fabricate or discard its parent query");
	}
});
test("ledger renders native change and after balance separately and paginates true total without changing parent", async () => {
	const m = await make(); await populate(m); const detail = openDetail(m, "records"), parent = m.view.rows;
	const rows = Array.from({ length: 100 }, (_, i) => ledgerRow({ name: "SLE-" + i }));
	detail.request.resolve(recordsResponse(detail.request, row(), rows, { total: 101, total_pages: 2 })); await detail.pending;
	assert.ok(detail.state.body.textContent.includes("-9007199254740993.01")); assert.ok(detail.state.body.textContent.includes("本次余数"));
	assert.ok(detail.state.body.textContent.includes("仅价值调整")); assert.ok(!detail.state.body.textContent.includes("盘点标记"));
	assert.ok(detail.state.body.textContent.includes("旧去向、退回等列规则待确认"));
	const buttons = detail.state.body.descendants("button"); assert.equal(buttons[0].disabled, true); assert.equal(buttons[1].disabled, false);
	buttons[1].click(); const page = m.calls.at(-1); assert.equal(page.options.args.page, 2); assert.equal(page.options.args.warehouse, "仓库一");
	page.resolve(recordsResponse(page, row(), [ledgerRow({ name: "SLE-100" })], { total: 101, total_pages: 2 })); await new Promise(setImmediate);
	assert.ok(detail.state.body.textContent.includes("第 2 页 · 共 101 条")); assert.equal(m.view.rows, parent); assert.equal(m.view.page, 1);
	assert.equal(detail.state.body.descendants("tr").length, 2); m.view.closeDetail(); assert.equal(m.calls.length, 3);
});
test("ledger rejects scope, unsafe counts, truncated pages and numeric or foreign source rows as a whole", async () => {
	const changes = [data => { data.search = "OTHER"; }, data => { data.row.warehouse_version = "changed"; }, data => { data.total = 101; data.total_pages = 2; },
		data => { data.total = Number.MAX_SAFE_INTEGER + 1; }, data => { data.rows[0].actual_qty = 3; }, data => { data.rows[0].stock_value = "NaN"; },
		data => { data.rows[0].company = "OTHER"; }, data => { data.rows[0].stock_uom = "Kg"; }, data => { data.rows[0].is_adjustment_entry = 2; },
		data => { data.rows[0].private_source = "secret"; }, data => { data.rows.push(ledgerRow()); data.total = 2; }];
	for (const change of changes) {
		const m = await make(); await populate(m); const detail = openDetail(m, "records"), data = plain(recordsResponse(detail.request)); change(data);
		detail.request.resolve(data); await detail.pending; assert.match(detail.state.body.textContent, /详情未能读取/); assert.equal(detail.state.body.descendants("td").length, 0);
	}
});
test("closing details aborts pending reads and late completion cannot restore a drawer or ledger", async () => {
	for (const mode of ["archive", "records"]) {
		const m = await make(); await populate(m); const detail = openDetail(m, mode), parent = m.view.rows;
		m.view.closeDetail(); assert.equal(detail.request.aborted, true); detail.request.resolve(mode === "archive" ? archiveResponse(detail.request) : recordsResponse(detail.request)); await detail.pending;
		assert.equal(m.view.detail, null); assert.equal(detail.state.body.textContent, ""); assert.equal(m.view.rows, parent); assert.equal(m.calls.length, 2);
	}
});
test("Escape while focus is on the parent closes a nonmodal ledger; destroy removes its handler", async () => {
	const m = await make(); await populate(m); const detail = openDetail(m, "records");
	m.host.events.get("keydown")({ key: "Escape", preventDefault() {}, stopPropagation() {} });
	assert.equal(m.view.detail, null); assert.equal(detail.request.aborted, true); assert.equal(detail.trigger.focused, true); assert.equal(m.view.rows.length, 1);
	detail.request.resolve(recordsResponse(detail.request)); await detail.pending; m.view.destroy(); assert.equal(m.host.events.has("keydown"), false);
});
test("parent filter, mode, page, leave and destroy close details and permanently discard their late returns", async () => {
	const actions = [m => { m.view.search.value = "changed"; m.view.search.events.get("input")(); },
		m => m.view.companyControl.clear(), m => m.view.dateControl.input("2026-10-03"), m => m.view.warehouseControl.clear(),
		m => { m.view.goto.value = "2"; m.view.goto.events.get("input")(); },
		m => m.view.setMode("图片"), m => m.view.query(1), m => m.view.setVisible(false), m => m.view.destroy()];
	for (const action of actions) {
		const m = await make(); await populate(m); const detail = openDetail(m, "records"); action(m);
		assert.equal(detail.request.aborted, true); assert.equal(m.view.detail, null);
		detail.request.resolve(recordsResponse(detail.request)); await detail.pending; assert.equal(detail.state.body.textContent, "");
		assert.ok(!m.host.textContent.includes("REC-0001"));
	}
});
test("replaced detail page aborts prior page and denied diagnostics remain generic", async () => {
	const m = await make(); await populate(m); const detail = openDetail(m, "records");
	const next = m.view.queryTrackingDetail(detail.state, 2), request = m.calls.at(-1); assert.equal(detail.request.aborted, true);
	detail.request.resolve(recordsResponse(detail.request)); await detail.pending; assert.match(detail.state.body.textContent, /正在读取/);
	request.reject(new Error("private SQL password")); await next; assert.match(detail.state.body.textContent, /详情未能读取/);
	assert.ok(!m.host.textContent.includes("private SQL password")); assert.equal(m.view.rows.length, 1);
});
function valuesResponse(request, values = [null, "—"], extra = {}) {
	const args = request.options.args;
	return { ...args, warehouse: args.warehouse || "", column_filters: JSON.parse(args.column_filters), sort: JSON.parse(args.sort),
		filter_protocol: 1, currency: "CNY", full_scope: true, source_total: values.length, matched_total: values.length,
		values, total: values.length, total_pages: values.length ? 1 : 0, readonly: true, can_write: false, ...extra };
}
test("column menu and text cancellation preserve rows, page, search and conditions; Escape restores focus", async () => {
	const m = await make(); m.view.search.value = "0007"; await populate(m, [row()], { total: 101, total_pages: 2 }, 2);
	const rows = m.view.rows, trigger = m.view.columnHeaders.get("display_name").button;
	m.view.openColumnMenu("display_name", trigger); const menu = m.view.columnPopup;
	assert.equal(menu.layer.descendants("button").map(button => button.textContent).join("/"), "升序排序/降序排序/清空筛选/文本筛选/设置筛选");
	menu.controls[3].click(); menu.layer.descendants("button").find(button => button.textContent === "包含").click(); const draft = m.view.columnPopup;
	assert.equal(draft.operator.value, "contains"); assert.equal(draft.operator.descendants("option").length, 10); draft.input.value = "nomatch";
	draft.cancel.click(); assert.equal(m.view.columnPopup, null); assert.equal(trigger.focused, true);
	assert.equal(m.view.rows, rows); assert.equal(m.view.page, 2); assert.equal(m.view.search.value, "0007"); assert.equal(m.calls.length, 1); assert.deepEqual(plain(m.view.columnFilters), []);
	const next = m.view.openTextFilter("display_name", "contains", trigger); next.input.value = "other";
	next.layer.events.get("keydown")({ key: "Escape", preventDefault() {}, stopPropagation() {} });
	assert.equal(m.view.columnPopup, null); assert.equal(trigger.focused, true); assert.equal(m.view.rows, rows); assert.equal(m.calls.length, 1);
});
test("column changes abort old rows/details, use POST protocol 1 and restart page 1 while keeping page search", async () => {
	const m = await make(); m.view.search.value = "0007"; await populate(m, [row()], { total: 101, total_pages: 2 }, 2);
	const detail = openDetail(m), condition = { field: "display_name", operator: "contains", value: "nomatch" };
	const pending = m.view.applyColumnCondition("display_name", condition), request = m.calls.at(-1);
	assert.equal(detail.request.aborted, true); assert.equal(m.view.detail, null); assert.equal(m.view.rows.length, 0); assert.equal(m.view.page, 1);
	assert.equal(request.options.type, "POST"); assert.equal(request.options.args.page, 1); assert.equal(request.options.args.search, "0007");
	assert.deepEqual(JSON.parse(request.options.args.column_filters), [condition]); assert.equal(JSON.parse(request.options.args.sort), null);
	assert.equal(m.view.columnHeaders.get("display_name").button.dataset.filtered, "true");
	request.resolve(response(request, [])); await pending; detail.request.resolve(archiveResponse(detail.request)); await detail.pending;
	assert.equal(m.view.total, 0); assert.equal(m.view.detail, null); assert.equal(m.view.search.value, "0007");
	const clear = m.view.applyColumnCondition("display_name", null), old = m.calls.at(-1); assert.equal(old.options.type, "GET");
	assert.equal(Object.hasOwn(old.options.args, "column_filters"), false); assert.equal(old.options.args.search, "0007");
	old.resolve(response(old, [])); await clear; assert.equal(m.view.columnHeaders.get("display_name").button.dataset.filtered, "false");
});
test("filter changes discard old responses even when requests ignore abort", async () => {
	const m = await make(); await populate(m); const previous = m.view.query(), old = m.calls.at(-1);
	const pending = m.view.applyColumnCondition("notes", { operator: "eq", value: "原文\n保留" }), request = m.calls.at(-1);
	assert.equal(old.aborted, true); assert.notEqual(old, request); old.resolve(response(old)); await previous; assert.equal(m.view.rows.length, 0);
	request.resolve(response(request)); await pending; assert.equal(m.view.rows.length, 1);
	const third = m.view.query(), stale = m.calls.at(-1); m.view.search.value = "new"; m.view.search.events.get("input")();
	stale.resolve(response(stale)); await third; assert.equal(m.view.rows.length, 0);
});
test("extended response binds exact filters, sort and protocol; sorting leaves decimal text untouched", async () => {
	for (const change of [data => { data.filter_protocol = 2; }, data => { data.column_filters = []; }, data => { data.sort.direction = "asc"; }, data => { data.unexpected = true; }]) {
		const m = await make(); m.view.columnFilters = [{ field: "notes", operator: "eq", value: "原文\n保留" }];
		const pending = m.view.setColumnSort("stock_qty", "desc"), request = m.calls.at(-1), data = response(request); change(data); request.resolve(data); await pending;
		assert.equal(m.view.rows.length, 0); assert.match(m.view.status.textContent, /查询未完成/);
	}
	const m = await make(), pending = m.view.setColumnSort("stock_qty", "desc"), request = m.calls.at(-1);
	assert.equal(request.options.type, "POST"); assert.equal(m.view.columnHeaders.get("stock_qty").th.attributes["aria-sort"], "descending");
	request.resolve(response(request)); await pending; assert.equal(m.view.tbody.descendants("td")[2].textContent, "9007199254740993.01");
});
test("value pages cover the full authorized scope, separate null/dash and preserve cross-page exclusions", async () => {
	const m = await make(); await populate(m); const state = m.view.openValueFilter("notes"), first = m.calls.at(-1);
	assert.equal(first.options.method, "jifeng_erp.inventory_tracking.query_tracking_values"); assert.equal(first.options.type, "POST"); assert.equal(state.confirm.disabled, true);
	const values = [null, "—", ...Array.from({ length: 98 }, (_, index) => "value-" + index)];
	first.resolve(valuesResponse(first, values, { source_total: 150, matched_total: 150, total: 101, total_pages: 2 })); await state.ready;
	assert.equal(state.list.descendants("label")[0].textContent, "（空值）"); assert.equal(state.list.descendants("label")[1].textContent, "—"); assert.equal(state.next.disabled, false);
	const empty = state.list.descendants("input")[0]; empty.checked = false; empty.events.get("change")(); assert.equal(state.selected.has("null"), true); assert.equal(state.selected.has('"—"'), false);
	const next = m.view.queryValueOptions(state, 2), second = m.calls.at(-1); second.resolve(valuesResponse(second, ["outside-first-100"], { source_total: 150, matched_total: 150, total: 101, total_pages: 2 })); await next;
	assert.equal(state.selected.has("null"), true); state.confirm.click(); const request = m.calls.at(-1);
	assert.deepEqual(JSON.parse(request.options.args.column_filters), [{ field: "notes", operator: "not_in", value: [null] }]); assert.equal(request.options.args.page, 1);
	request.resolve(response(request)); await new Promise(setImmediate); assert.equal(m.view.rows.length, 1);
});
test("cancelled or stale value pages cannot apply drafts or render late option values", async () => {
	const m = await make(); await populate(m); const rows = m.view.rows, state = m.view.openValueFilter("notes"), first = m.calls.at(-1);
	first.resolve(valuesResponse(first)); await state.ready; const box = state.list.descendants("input")[0]; box.checked = false; box.events.get("change")(); state.cancel.click();
	assert.equal(m.view.rows, rows); assert.deepEqual(plain(m.view.columnFilters), []); assert.equal(m.calls.length, 2);
	const stale = m.view.openValueFilter("notes"), request = m.calls.at(-1); m.view.search.value = "changed"; m.view.search.events.get("input")();
	assert.equal(request.aborted, true); request.resolve(valuesResponse(request, ["do-not-render"])); await stale.ready;
	assert.equal(m.view.columnPopup, null); assert.ok(!m.host.textContent.includes("do-not-render")); assert.equal(stale.list.textContent, "");
});
test("incomplete or denied options clear older data and cannot masquerade as a valid empty set", async () => {
	for (const change of [data => { data.full_scope = false; }, data => { data.source_total = 0; }, data => { data.column_filters = [{ field: "notes", operator: "eq", value: "other" }]; },
		data => { data.values = [null, null]; }, data => { data.field = "display_name"; }, data => { data.currency = ""; }]) {
		const m = await make(); await populate(m); const state = m.view.openValueFilter("notes"), request = m.calls.at(-1), data = valuesResponse(request); change(data); request.resolve(data); await state.ready;
		assert.equal(state.confirm.disabled, true); assert.equal(state.list.textContent, ""); assert.equal(m.view.rows.length, 0); assert.match(state.error.textContent, /未能完整读取/);
	}
	const m = await make(); await populate(m); const state = m.view.openValueFilter("notes"); m.calls.at(-1).reject(new Error("private SQL")); await state.ready;
	assert.equal(m.view.rows.length, 0); assert.ok(!state.layer.textContent.includes("private SQL"));
});
test("filtered details retain old exact scope and closing preserves conditions, count and focus", async () => {
	const m = await make(); m.view.search.value = "0007"; m.view.columnFilters = [{ field: "notes", operator: "contains", value: "原文" }]; m.view.columnSort = { field: "stock_qty", direction: "asc" };
	await populate(m, [row()], { total: 101, total_pages: 2 }, 2); const conditions = plain(m.view.columnFilters), order = plain(m.view.columnSort), detail = openDetail(m);
	assert.equal(Object.hasOwn(detail.request.options.args, "column_filters"), false); assert.equal(Object.hasOwn(detail.request.options.args, "sort"), false);
	detail.request.resolve(archiveResponse(detail.request)); await detail.pending; m.view.closeDetail(true);
	assert.equal(m.view.page, 2); assert.equal(m.view.total, 101); assert.equal(detail.trigger.focused, true); assert.equal(m.view.search.value, "0007");
	assert.deepEqual(plain(m.view.columnFilters), conditions); assert.deepEqual(plain(m.view.columnSort), order); assert.equal(m.calls.length, 2);
});
test("empty complete options disable confirm and cancellation does not apply a filter", async () => {
	const m = await make(); const state = m.view.openValueFilter("notes"), request = m.calls.at(-1); request.resolve(valuesResponse(request, [])); await state.ready;
	assert.equal(state.confirm.disabled, true); assert.equal(state.all.disabled, true); assert.equal(m.calls.length, 1); state.confirm.click(); assert.equal(m.calls.length, 1);
	state.cancel.click(); assert.deepEqual(plain(m.view.columnFilters), []);
});
const exportResponse = (options = {}) => ({ ok: true, headers: { get: () => "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }, blob: async () => ({ size: 12 }), ...options });
test("export uses current full conditions and CSRF POST body without page or long URL", async () => {
	const m = await make("材料"); m.view.search.value = "0007";
	m.view.columnFilters = ["warehouse", "legacy_number", "minor_category", "display_name", "specification", "location"].map(field => ({ field, operator: "contains", value: "中".repeat(2000) }));
	m.view.columnSort = { field: "stock_qty", direction: "desc" };
	const pending = m.view.exportTracking(), request = m.exports.at(-1);
	assert.equal(request.url, "/api/method/jifeng_erp.inventory_tracking_export.export_tracking");
	assert.equal(request.options.method, "POST"); assert.equal(request.options.credentials, "same-origin");
	assert.equal(request.options.headers["X-Frappe-CSRF-Token"], "synthetic-csrf");
	assert.ok(request.options.body.toString().length > 8000);
	assert.deepEqual(Object.keys(request.args).sort(), ["as_of", "column_filters", "company", "kind", "search", "sort"]);
	assert.deepEqual(JSON.parse(request.args.column_filters), plain(m.view.columnFilters));
	assert.deepEqual(JSON.parse(request.args.sort), { field: "stock_qty", direction: "desc" });
	assert.equal(m.view.exportButton.disabled, true); await m.view.exportTracking(); assert.equal(m.exports.length, 1);
	request.resolve(exportResponse()); await pending;
	assert.deepEqual(m.downloads, [{ href: "blob:synthetic-tracking", filename: "材料跟踪.xlsx" }]);
	assert.deepEqual(m.revoked, ["blob:synthetic-tracking"]); assert.equal(m.calls.length, 0);
	assert.equal(m.view.exporting, false); assert.equal(m.view.exportButton.disabled, false);
});
test("invalid context or missing CSRF refuses an export before network", async () => {
	for (const action of [m => m.view.companyControl.input("unfinished"), m => m.frappe.csrf_token = "", m => m.view.columnSort = { field: "item_code", direction: "asc" }]) {
		const m = await make(); action(m); await m.view.exportTracking(); assert.equal(m.exports.length, 0); assert.equal(m.downloads.length, 0);
	}
});
test("all input changes and leave, mode, dispose cancel a late export", async () => {
	const actions = [m => { m.view.search.value = "changed"; m.view.search.events.get("input")(); },
		m => m.view.companyControl.clear(), m => m.view.warehouseControl.input("changed"), m => m.view.dateControl.input("2026-10-03"),
		m => m.view.setVisible(false), m => m.view.setMode("图片"), m => m.view.dispose()];
	for (const action of actions) {
		const m = await make(); const pending = m.view.exportTracking(), request = m.exports.at(-1); action(m);
		assert.equal(request.options.signal.aborted, true); request.resolve(exportResponse()); await pending;
		assert.equal(m.downloads.length, 0); assert.equal(m.view.exporting, false);
	}
});
test("fresh query aborts pending export and rejected files preserve readable results", async () => {
	const m = await make(); await populate(m);
	const pending = m.view.exportTracking(), request = m.exports.at(-1), query = m.view.query();
	assert.equal(request.options.signal.aborted, true); const call = m.calls.at(-1); call.resolve(response(call)); await query;
	request.resolve(exportResponse()); await pending; assert.equal(m.downloads.length, 0);
	for (const bad of [exportResponse({ ok: false }), exportResponse({ headers: { get: () => "application/json" } }), exportResponse({ blob: async () => ({ size: 0 }) })]) {
		const failed = m.view.exportTracking(); m.exports.at(-1).resolve(bad); await failed;
		assert.equal(m.downloads.length, 0); assert.equal(m.view.total, 1); assert.equal(m.view.rows.length, 1);
		assert.match(m.view.status.textContent, /导出未完成/);
	}
});
test("old export completion cannot reset a new export or download changed filters", async () => {
	const m = await make(); const old = m.view.exportTracking(), first = m.exports.at(-1);
	m.view.search.value = "new"; m.view.search.events.get("input")(); const fresh = m.view.exportTracking(), second = m.exports.at(-1);
	first.resolve(exportResponse()); await old; assert.equal(m.view.exporting, true); assert.equal(m.downloads.length, 0);
	second.resolve(exportResponse()); await fresh; assert.equal(m.downloads.length, 1); assert.equal(m.view.exporting, false);
	const next = m.view.exportTracking(), third = m.exports.at(-1);
	m.view.columnFilters = [{ field: "supplier", operator: "in", value: ["changed"] }];
	third.resolve(exportResponse()); await next; assert.equal(m.downloads.length, 1, "unreported native/control change still discards stale bytes");
});
(async () => { for (const [name, body] of tests) { try { await body(); } catch (error) { error.message = name + ": " + error.message; throw error; } }
	console.log(tests.length + " inventory tracking DTO/DOM models passed (no GUI/native/HTTP proof)");
})().catch(error => { console.error(error); process.exitCode = 1; });
