// Actual shared classes against delayed DTO/DOM models; no GUI/native ledger proof.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
	constructor(tag) { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this.dataset = {}; this._text = ""; this.value = ""; this.classList = { add() {}, remove() {} }; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
	set innerHTML(_) { throw new Error("Tracking source text cannot execute HTML"); }
	append(...nodes) { this.children.push(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, callback) { this.events.set(name, callback); }
	click() { if (!this.disabled) this.events.get("click")?.(); }
	descendants(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
const plain = value => JSON.parse(JSON.stringify(value));
function harness() {
	const calls = [], pages = [], controls = [], window = {};
	const frappe = { defaults: { get_user_default: () => "集沣" }, datetime: { get_today: () => "2026-10-04" }, ui: { form: { make_control({ df }) {
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
	const context = vm.createContext({ window, document: { createElement: tag => new Element(tag) }, frappe, __: value => value,
		localStorage: forbidden, sessionStorage: forbidden, indexedDB: forbidden });
	for (const file of ["jifeng_inventory_catalog.js", "jifeng_inventory_tracking.js"])
		vm.runInContext(fs.readFileSync("jifeng_erp/public/js/" + file, "utf8"), context);
	return { calls, controls, pages, window, bridge: { open: spec => pages.push(plain(spec)) } };
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
	return { warehouse: "", ...request.options.args, rows, currency: "CNY", basis, total: rows.length, total_pages: rows.length ? 1 : 0, readonly: true, can_write: false, ...extra };
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
	assert.equal(m.host.descendants("th").map(th => th.textContent).join("/"), "仓库/编号/库存/单价/金额/重量/总重量/大类/小类/材质/单位/名称/规格/库位/备注/来源");
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
		actions[1].click(); assert.deepEqual(m.pages[0].route, ["Form", "Item", "ITEM-0007"]); assert.equal(m.pages[0].module, kind);
		assert.equal(m.view.rows.length, 0); actions[0].click(); assert.equal(m.pages.length, 1);
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
(async () => { for (const [name, body] of tests) { try { await body(); } catch (error) { error.message = name + ": " + error.message; throw error; } }
	console.log(tests.length + " inventory tracking DTO/DOM models passed (no GUI/native/HTTP proof)");
})().catch(error => { console.error(error); process.exitCode = 1; });
