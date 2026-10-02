// Read-only DOM and response-order models; no browser, native DB or network.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const calls = [], dialogs = [];
class Element {
	constructor(tag = "div") {
		this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this._text = "";
		this.value = ""; this.scrollTop = 0; this.scrollLeft = 0; this.classList = { add() {} };
	}
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((child) => child.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("Business source HTML must never execute"); }
	append(...nodes) { this.children.push(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, action) { this.events.set(name, action); }
	descendants(tag) { return this.children.flatMap((child) => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Wrapper {
	constructor(element = new Element()) { this.element = element; }
	get() { return this.element; }
	addClass() { return this; }
	on(names, action) { for (const name of names.split(" ")) this.element.events.set(name.split(".")[0], action); return this; }
}
class Dialog {
	constructor(config) {
		this.config = config; this.hidden = false; this.$wrapper = new Wrapper(); this.actions = [];
		this.fields_dict = Object.fromEntries(config.fields.map((field) => [field.fieldname, { $wrapper: new Wrapper() }])); dialogs.push(this);
	}
	add_custom_action(label, action) { this.actions.push({ label, action }); }
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
const frappe = { ui: { Dialog, form: { make_control({ df }) {
	return { df, $input: new Wrapper(new Element("input")), value: null, get_value() { return this.value; }, async set_value(value) { this.value = value; await df.onchange?.(); } };
} } }, call(options) {
	let resolve, reject; const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
	calls.push({ options, resolve: (message) => resolve({ message }), reject }); return pending;
} };
const window = {}, document = { createElement: (tag) => new Element(tag) };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_inventory_catalog.js"), "utf8"), { window, document, frappe, __: (value) => value });
const itemFields = ["item_code", "item_name", "stock_uom"], profileFields = ["company", "archive_kind", "item_code", "inactive", "legacy_number", "old_number", "major_category", "minor_category", "display_name", "specification", "material", "shape", "color", "location", "display_uom", "list_price", "processing_price", "weight", "upper_limit", "lower_limit", "conversion_uom", "conversion_value", "supplier", "notes"];
function archive(kind = "物料", company = "Company A", name = "PROFILE-A") {
	const item = "ITEM-" + name;
	return { name, kind, company, item_code: item, profile_version: "2026-10-02T00:00:00.123456", item_version: "2026-10-02T00:00:01.123456",
		profile: { company, archive_kind: kind, item_code: item, inactive: 0, legacy_number: " 0007 ", old_number: "旧号", major_category: "大类", minor_category: "小类",
			display_name: '<svg onload="alert(1)">原文</svg>', specification: "规格\n原文", material: "材质", shape: "形状", color: "颜色", location: "库位",
			display_uom: "对（档案原文）", list_price: 0, processing_price: "0.00", weight: "0.2300", upper_limit: "99.50", lower_limit: 0,
			conversion_uom: "Kg", conversion_value: "9.00", supplier: "SUP", notes: "备注\n原文" },
		item: { item_code: item, item_name: "不能回填的业务名称", stock_uom: "不能回填的业务单位" },
		field_meta: { profile: Object.fromEntries(profileFields.map((field) => [field, { read_only: true }])), item: Object.fromEntries(itemFields.map((field) => [field, { read_only: true }])) },
		image: { source_doctype: "Item", source_name: item, source_field: "image", label: "关联业务商品图片", state: "未上传", url: "" }, readonly: true, can_write: false };
}
function pageReply(call, rows, total = rows.length) {
	const args = call.options.args;
	return { ...args, rows, total, total_pages: Math.ceil(total / args.page_size), readonly: true, can_write: false };
}
async function make(kind = "物料") {
	const bench = new window.JifengInventoryCatalog(new Element(), kind);
	await bench.ready; await bench.companyControl.set_value("Company A"); return bench;
}
async function respond(bench, rows = [archive(bench.kind, bench.companyScope)], total = rows.length, target = 1) {
	const pending = bench.query(target), call = calls.at(-1); call.resolve(pageReply(call, rows, total)); await pending; return call;
}
const itemLabels = "编号/旧编号/大类/小类/名称/规格/材质/库位/单位/单价/加工价/重量/上限/下限/换算单位/换算值/供应商/备注/停用/图片";
const materialLabels = "编号/名称/规格/形状/颜色/小类/单位/单价/重量/加工价/库位/上限/下限/换算单位/换算值/供应商/备注/停用/图片";
async function main() {
	assert.throws(() => new window.JifengInventoryCatalog(new Element(), "产品"));
	const bench = await make(), materials = await make("材料");
	assert.equal(bench.host.descendants("th").map((node) => node.textContent).join("/"), itemLabels);
	assert.equal(materials.host.descendants("th").map((node) => node.textContent).join("/"), materialLabels);
	assert.equal(bench.search.placeholder, "输入查询的关键字"); assert.equal(bench.statusFilter, "在用");
	assert.deepEqual(bench.host.descendants("button").map((node) => node.textContent), ["查询", "上页", "下页", "前往"]);
	assert.equal(bench.isDirty(), false); assert.equal(bench.getCloseBlock(), "");
	const noScope = new window.JifengInventoryCatalog(new Element(), "物料"), count = calls.length;
	await noScope.query(); assert.equal(calls.length, count); assert.match(noScope.status.textContent, /请选择公司/);
	const silent = new window.JifengInventoryCatalog(new Element(), "物料");
	silent.companyControl.value = "Company A"; // A real Link selection may not notify df.onchange.
	await respond(silent, [archive()]); assert.equal(silent.companyScope, "Company A"); assert.equal(calls.at(-1).options.args.company, "Company A");
	const inputCalls = calls.length; silent.companyControl.value = "Company B";
	silent.companyControl.$input.element.events.get("input")();
	assert.equal(silent.companyScope, "Company B"); assert.equal(silent.total, null); assert.equal(silent.rows.length, 0);
	assert.equal(silent.tbody.children.length, 0); assert.equal(silent.page, 1); assert.equal(calls.length, inputCalls, "Company input never queries automatically");
	await respond(silent, [archive("物料", "Company B")]);
	const silentQuery = silent.query(), silentQueryCall = calls.at(-1);
	silent.companyControl.value = "Company A"; silentQueryCall.resolve(pageReply(silentQueryCall, [archive("物料", "Company B")], 1)); await silentQuery;
	assert.equal(silent.companyScope, "Company A"); assert.equal(silent.rows.length, 0); assert.equal(silent.total, null);
	await respond(silent); const silentReadCalls = calls.length;
	silent.companyControl.value = "Company B"; await silent.readDetail("PROFILE-A");
	assert.equal(calls.length, silentReadCalls, "Opening an old row synchronizes and clears a silently changed scope"); assert.equal(silent.detail, null);
	await respond(silent, [archive("物料", "Company B")]);
	const silentDetail = silent.readDetail("PROFILE-A"), silentDetailCall = calls.at(-1), silentDialog = dialogs.at(-1);
	silent.companyControl.value = "Company A"; silentDetailCall.resolve(archive("物料", "Company B")); await silentDetail;
	assert.equal(silentDialog.hidden, true); assert.equal(silent.detail, null); assert.equal(silent.rows.length, 0);
	assert.equal(silentDialog.fields_dict.inventory_detail.$wrapper.element.children.length, 0);
	await respond(silent); const noScopeInputCalls = calls.length; silent.companyControl.value = null;
	silent.companyControl.$input.element.events.get("change")();
	assert.equal(silent.companyScope, null); assert.equal(silent.total, null); assert.equal(calls.length, noScopeInputCalls);
	const first = await respond(bench);
	assert.equal(first.options.method, "jifeng_erp.inventory_catalog.query_archives"); assert.equal(first.options.args.page_size, 100);
	assert.equal(bench.count.textContent, "共 1 条"); assert.equal(bench.previous.disabled, true); assert.equal(bench.next.disabled, true);
	const cells = bench.tbody.children[0].children;
	assert.equal(cells[0].textContent, " 0007 "); assert.equal(cells[4].textContent, '<svg onload="alert(1)">原文</svg>');
	assert.equal(cells[8].textContent, "对（档案原文）"); assert.equal(cells[9].textContent, "0"); assert.equal(cells[10].textContent, "0.00");
	assert.equal(cells[11].textContent, "0.2300"); assert.equal(cells[15].textContent, "9.00");
	assert.equal(bench.host.descendants("svg").length, 0); assert.equal(bench.host.descendants("img").length, 0);
	const omitted = archive(); delete omitted.profile.display_name; delete omitted.field_meta.profile.display_name;
	delete omitted.profile.display_uom; delete omitted.field_meta.profile.display_uom;
	await respond(bench, [omitted]); assert.equal(bench.tbody.children[0].children[4].textContent, ""); assert.equal(bench.tbody.children[0].children[8].textContent, "");
	assert.equal(bench.host.textContent.includes("不能回填"), false);

	bench.pageSize = 1; bench.scroller.scrollTop = 71; bench.scroller.scrollLeft = 430;
	await respond(bench, [archive()], 2); assert.equal(bench.next.disabled, false); assert.equal(bench.scroller.scrollLeft, 430);
	await respond(bench, [archive("物料", "Company A", "PROFILE-B")], 2, 2);
	assert.equal(bench.pageLabel.textContent, "2"); assert.equal(bench.previous.disabled, false); assert.equal(bench.next.disabled, true); assert.equal(bench.goto.value, "2");
	assert.equal(bench.scroller.scrollTop, 71); assert.equal(bench.scroller.scrollLeft, 430);
	bench.goto.value = "3"; const outside = bench.goToPage(); calls.at(-1).reject(new Error("page outside current visible set")); await outside;
	assert.equal(bench.rows.length, 0); assert.equal(bench.total, null); assert.equal(bench.count.textContent, "尚未查询"); assert.equal(bench.pageLabel.textContent, "—");
	assert.match(bench.status.textContent, /合法页码/); assert.equal(bench.previous.disabled, true); assert.equal(bench.next.disabled, true);
	for (const invalid of [true, false, 0, -1, 1.5, "01", " 1", "1 ", "+1", "1e0", "1001", ""]) {
		const before = calls.length; await bench.query(invalid); assert.equal(calls.length, before); assert.equal(bench.total, null);
	}
	await respond(bench, [], 0); assert.equal(bench.count.textContent, "共 0 条"); assert.equal(bench.pageLabel.textContent, "1");

	const old = bench.query(), oldCall = calls.at(-1); bench.search.value = "新词";
	bench.search.events.get("input")(); const newer = bench.query(), newCall = calls.at(-1);
	newCall.resolve(pageReply(newCall, [archive()], 1)); await newer;
	oldCall.resolve(pageReply(oldCall, [archive("物料", "Company A", "STALE")], 1)); await old;
	assert.equal(bench.rows[0].name, "PROFILE-A"); assert.equal(bench.search.value, "新词");
	const beforeSwitch = bench.query(), switchedCall = calls.at(-1); await bench.companyControl.set_value("Company B");
	assert.equal(bench.total, null); assert.equal(bench.tbody.children.length, 0);
	switchedCall.resolve(pageReply(switchedCall, [archive()], 1)); await beforeSwitch; assert.equal(bench.rows.length, 0);
	await respond(bench, [archive("物料", "Company B")]); await bench.companyControl.set_value("Company A"); bench.search.value = "";
	await respond(bench);
	const enterBefore = calls.length; bench.search.events.get("keydown")({ key: "Enter", preventDefault() {} });
	assert.equal(calls.length, enterBefore + 1); assert.equal(calls.at(-1).options.args.page, 1);
	calls.at(-1).resolve(pageReply(calls.at(-1), [archive()], 1)); await new Promise(setImmediate);

	for (const change of [
		(result) => result.total++, (result) => result.kind = "材料", (result) => result.company = "Other",
		(result) => result.rows[0].profile.company = "Other", (result) => result.rows[0].profile.inactive = 1,
		(result) => result.rows[0].field_meta.profile.list_price.read_only = false, (result) => delete result.rows[0].field_meta.profile.company,
		(result) => result.rows[0].item.item_code = "Other", (result) => result.rows[0].can_write = true,
		(result) => result.rows[0].profile.list_price = { unsafe: true }, (result) => result.rows[0].profile_version = null,
		(result) => result.rows[0].image = { ...result.rows[0].image, state: "已上传", url: "https://example.invalid/image.png" },
	]) {
		const pending = bench.query(), call = calls.at(-1), malformed = pageReply(call, [archive()], 1); change(malformed); call.resolve(malformed); await pending;
		assert.equal(bench.total, null); assert.equal(bench.rows.length, 0); assert.equal(bench.tbody.children.length, 0);
	}
	const image = archive(); image.image.state = "已上传"; image.image.url = "/private/files/item.png?fid=EXACT";
	await respond(bench, [image]); const preview = bench.host.descendants("img")[0]; assert.equal(preview.src, image.image.url);
	assert.equal(preview.alt, "关联业务商品图片"); assert.match(bench.tbody.children[0].children.at(-1).title, /Item \/ ITEM-PROFILE-A \/ image/);
	for (const bad of ["//example.invalid/x.png", "/files/x.png", "/private/files/a/b.png?fid=X", "/private/files/%2e%2e?fid=X", "/private/files/%252fescape.png?fid=X", "/private/files/a.png?fid=X#x"]) assert.equal(bench.safePreview(bad), false, bad);

	const reading = bench.readDetail("PROFILE-A"), readCall = calls.at(-1), detail = dialogs.at(-1);
	assert.equal(readCall.options.method, "jifeng_erp.inventory_catalog.read_archive"); assert.equal(readCall.options.args.company, "Company A");
	assert.deepEqual(detail.actions.map((action) => action.label), ["关闭"]); assert.equal(detail.config.primary_action, undefined);
	readCall.resolve(image); await reading; assert.equal(detail.fields_dict.inventory_detail.$wrapper.element.descendants("dt").map((node) => node.textContent).join("/"), itemLabels);
	assert.equal(detail.fields_dict.inventory_detail.$wrapper.element.descendants("input").length, 0);
	assert.equal(detail.fields_dict.inventory_detail.$wrapper.element.descendants("svg").length, 0);
	detail.hide(); assert.equal(bench.detail, null); assert.equal(detail.fields_dict.inventory_detail.$wrapper.element.children.length, 0);
	const reopening = bench.readDetail("PROFILE-A"), reopenedCall = calls.at(-1);
	assert.notEqual(reopenedCall, readCall, "every reopening gets a fresh source RPC"); reopenedCall.resolve(archive()); await reopening;
	const firstDetail = bench.readDetail("PROFILE-A"), firstRead = calls.at(-1), firstDialog = dialogs.at(-1);
	const secondDetail = bench.readDetail("PROFILE-A"), secondRead = calls.at(-1), secondDialog = dialogs.at(-1);
	assert.equal(firstDialog.hidden, true); secondRead.resolve(archive()); await secondDetail;
	firstRead.resolve(image); await firstDetail; assert.equal(firstDialog.fields_dict.inventory_detail.$wrapper.element.children.length, 0);
	assert.equal(secondDialog.fields_dict.inventory_detail.$wrapper.element.descendants("img").length, 0);
	const closing = bench.readDetail("PROFILE-A"), closedRead = calls.at(-1), closedDialog = dialogs.at(-1); closedDialog.hide(); closedRead.resolve(image); await closing;
	assert.equal(closedDialog.fields_dict.inventory_detail.$wrapper.element.children.length, 0);
	const scopeDetail = bench.readDetail("PROFILE-A"), scopeRead = calls.at(-1), scopeDialog = dialogs.at(-1);
	await bench.companyControl.set_value("Company B"); scopeRead.resolve(image); await scopeDetail;
	assert.equal(scopeDialog.hidden, true); assert.equal(bench.detail, null); assert.equal(scopeDialog.fields_dict.inventory_detail.$wrapper.element.children.length, 0);
	await bench.companyControl.set_value("Company A"); await respond(bench);
	const failDetail = bench.readDetail("PROFILE-A"), failRead = calls.at(-1), failDialog = dialogs.at(-1); failRead.reject(new Error("permission changed")); await failDetail;
	assert.match(failDialog.fields_dict.inventory_detail.$wrapper.element.textContent, /关闭后重试/); assert.equal(failDialog.fields_dict.inventory_detail.$wrapper.element.descendants("img").length, 0);
	const afterDestroy = bench.query(), destroyedCall = calls.at(-1); bench.destroy(); destroyedCall.resolve(pageReply(destroyedCall, [image], 1)); await afterDestroy;
	assert.equal(bench.host.children.length, 0); assert.equal(bench.rows.length, 0); assert.equal(bench.detail, null); bench.destroy();
	const detailBench = await make(); await respond(detailBench);
	const destroyedDetail = detailBench.readDetail("PROFILE-A"), destroyedRead = calls.at(-1), destroyedDialog = dialogs.at(-1);
	detailBench.destroy(); destroyedRead.resolve(image); await destroyedDetail;
	assert.equal(destroyedDialog.hidden, true); assert.equal(destroyedDialog.fields_dict.inventory_detail.$wrapper.element.children.length, 0);
	assert.equal(detailBench.isDirty(), false); assert.equal(detailBench.getCloseBlock(), "");
	assert.ok(calls.every((call) => ["jifeng_erp.inventory_catalog.query_archives", "jifeng_erp.inventory_catalog.read_archive"].includes(call.options.method)), "no write RPC, Item fallback, import, export or print calls");
	console.log("Inventory browse pagination, permissions and response-order models passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
