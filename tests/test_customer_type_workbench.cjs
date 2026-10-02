// Shared native Dialog/transport models; no browser, database or production calls.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const { Element, Dialog, context, window, calls, messages, fieldMeta, pack, blank, listRow, tick } = require("./test_product_dictionary_workbench.cjs");
for (const file of ["jifeng_customer_type", "jifeng_workspace_core"]) vm.runInNewContext(fs.readFileSync(`jifeng_erp/public/js/${file}.js`, "utf8"), context);
const metadata = () => ({ ...fieldMeta(), abbreviation: { fieldtype: "Data", read_only: true } });
const customer = (extra = {}) => pack({ dictionary_type: "客户类型", field_meta: metadata(), ...extra });
const empty = (extra = {}) => blank({ dictionary_type: "客户类型", field_meta: metadata(), ...extra });
const row = (extra = {}) => listRow({ dictionary_type: "客户类型", ...extra });
const result = (extra = {}) => ({ company: "Company A", dictionary_type: "客户类型", search: "", include_inactive: 0,
	start: 0, page_length: 25, total: 0, rows: [], ...extra });
const page = (start, count) => Array.from({ length: count }, (_, i) => row({ name: "TYPE-" + (start + i), legacy_number: String(start + i).padStart(4, "0") }));

async function main() {
	const bench = new window.JifengCustomerType(new Element());
	assert.equal(calls.at(-1).options.method, "jifeng_erp.customer_type.init_dictionary");
	assert.equal(calls.at(-1).options.args.dictionary_type, "客户类型");
	calls.at(-1).resolve(empty({ company: null, number_preview: null })); await bench.ready;
	assert.equal(bench.host.descendants("th").map((e) => e.textContent).join("/"), "编号/名称/简拼/备注/停用/操作/日期/修改人/修改时间/修改/删除");
	assert.ok(bench.host.descendants("button").find((e) => e.textContent === "更新简拼").disabled);
	assert.ok(bench.host.descendants("input").find((e) => e.type === "checkbox" && e.checked && e.disabled));
	bench.companyControl.set_value("Company A"); calls.at(-1).resolve(result()); await tick();
	const first = bench.query(); calls.at(-1).resolve(result({ total: 103, rows: page(0, 25) })); await first;
	assert.equal(bench.total, 103); assert.equal(bench.rows.length, 25); assert.equal(bench.nextButton.disabled, false);
	const second = bench.query(25); assert.equal(calls.at(-1).options.args.start, 25);
	calls.at(-1).resolve(result({ total: 103, start: 25, rows: page(25, 25) })); await second;
	assert.equal(bench.start, 25); assert.equal(bench.previousButton.disabled, false);
	const last = bench.query(100); calls.at(-1).resolve(result({ total: 103, start: 100, rows: page(100, 3) })); await last;
	assert.equal(bench.rows.length, 3); assert.equal(bench.nextButton.disabled, true);
	const clamp = bench.query(100); calls.at(-1).resolve(result({ total: 51, start: 50, rows: page(50, 1) })); await clamp;
	assert.equal(bench.start, 50, "deleted or newly invisible last-page rows clamp to a complete valid page");
	const old = bench.query(), oldCall = calls.at(-1);
	bench.search.value = "新词"; bench.search.events.get("input")();
	const latest = bench.query(50); assert.equal(calls.at(-1).options.args.start, 0, "changed scope resets paging");
	const hostile = '<svg onload="alert(1)">原文</svg>';
	calls.at(-1).resolve(result({ search: "新词", total: 1, rows: [row({ value: hostile, remarks: hostile, owner: hostile })] })); await latest;
	oldCall.resolve(result({ total: 103, start: 50, rows: page(50, 25) })); await old;
	assert.equal(bench.rows[0].value, hostile); assert.match(bench.tbody.textContent, /<svg/);
	assert.equal(bench.tbody.descendants("td").length, 11);
	for (const bad of [result({ company: "Company B" }), result({ search: "新词", total: 1, rows: [row({ inactive: 1 })] }),
		result({ search: "新词", total: 1, rows: [] })]) {
		const query = bench.query(); calls.at(-1).resolve(bad); await query;
		assert.equal(bench.rows.length, 0); assert.match(bench.status.textContent, /查询失败/);
	}
	const open = bench.openEditor(); calls.at(-1).resolve(empty()); await open;
	const state = bench.editor, dialog = state.dialog;
	assert.equal(dialog.config.title, "客户类型新增");
	assert.equal(dialog.config.fields.filter((f) => !f.hidden).map((f) => f.label).join("/"), "编号/名称/备注/停用");
	assert.equal(dialog.fields_dict.legacy_number.df.read_only, true); assert.equal(dialog.fields_dict.remarks.df.fieldtype, "Data");
	await dialog.set_values({ value: " 客户类型原文 ", remarks: hostile, abbreviation: "禁止发送" });
	assert.equal("abbreviation" in bench.payload(state), false); assert.equal("legacy_number" in bench.payload(state), false);
	const save = bench.save(state), saveCall = calls.at(-1);
	assert.equal(saveCall.options.method, "jifeng_erp.customer_type.save_dictionary");
	assert.equal(saveCall.options.args.payload.value, " 客户类型原文 ");
	saveCall.reject(new Error("lost receipt")); await save;
	const callCount = calls.length; assert.equal(state.unresolved, true);
	await bench.save(state); bench.newDraft(state); dialog.hide(); assert.equal(calls.length, callCount);
	const resolve = bench.resolve(state); assert.equal(calls.at(-1).options.method, "jifeng_erp.customer_type.resolve_creation");
	calls.at(-1).resolve({ status: "found", archive: customer({ value: " 客户类型原文 ", remarks: hostile, inactive: 0 }) }); await resolve;
	assert.equal(state.unresolved, false); assert.equal(state.requestId, null);
	assert.equal(calls.at(-1).options.method, "jifeng_erp.customer_type.list_dictionary");
	calls.at(-1).resolve(result({ search: "新词" })); await tick();
	dialog.hide();
	const read = bench.openEditor("WORD-1"); calls.at(-1).resolve(customer({ can_write: false })); await read;
	assert.equal(bench.editor.dialog.fields_dict.abbreviation.df.hidden, true, "unverified old editor field is not invented");
	assert.equal(bench.editor.dialog.get_primary_btn().disabled, true);
	bench.editor.dialog.hide();
	const stale = bench.query(), staleCall = calls.at(-1); bench.destroy(); staleCall.resolve(result({ search: "新词" })); await stale;
	assert.equal(bench.disposed, true);
	const delayed = new window.JifengCustomerType(new Element());
	calls.at(-1).resolve(empty({ company: null, number_preview: null })); await delayed.ready;
	delayed.companyControl.set_value("Company A"); calls.at(-1).resolve(result()); await tick();
	const nativeSetValues = Dialog.prototype.set_values; let release;
	Dialog.prototype.set_values = async function(values) { await new Promise((yes) => { release = yes; }); return nativeSetValues.call(this, values); };
	const opening = delayed.openEditor(); calls.at(-1).resolve(empty()); await tick();
	const openingState = delayed.editor; assert.ok(openingState && release);
	delayed.companyControl.set_value("Company B"); calls.at(-1).resolve(result({ company: "Company B" }));
	release(); await opening; Dialog.prototype.set_values = nativeSetValues;
	assert.equal(delayed.editor, null); assert.equal(openingState.dialog.hidden, true);
	assert.equal(openingState.dialog.$wrapper.element.removed, true, "late field initialization cannot show an old-company dialog");
	delayed.destroy();
	for (const type of ["客户等级", "客户分类", "订单客户分类", "订单客户级别"]) {
		const order = type.startsWith("订单");
		const bridge = Object.create(window.JifengClassicBridge.prototype);
		const entry = bridge.mount(new Element(), { customerType: type, title: type });
		const typed = entry.workbench;
		assert.equal(calls.at(-1).options.args.dictionary_type, type, "bridge forwards the exact dictionary identity");
		calls.at(-1).resolve(empty({ dictionary_type: type, company: null, number_preview: null })); await entry.ready;
		assert.equal(typed.host.descendants("th").map((e) => e.textContent).join("/"), order ?
			"编号/名称/备注/简拼/停用/修改/删除" : "编号/名称/简拼/备注/停用/操作/日期/修改人/修改日期/修改/删除");
		typed.companyControl.set_value("Company A");
		calls.at(-1).resolve(result({ dictionary_type: type, total: 1, rows: [row({ dictionary_type: type })] })); await tick();
		assert.equal(typed.tbody.descendants("td").length, order ? 7 : 11);
		const wrong = typed.query(); calls.at(-1).resolve(result({ total: 1, rows: [row()] })); await wrong;
		assert.equal(typed.rows.length, 0, "a response for customer type cannot populate another dictionary");
		const opening = typed.openEditor();
		assert.equal(calls.at(-1).options.args.dictionary_type, type);
		calls.at(-1).resolve(empty({ dictionary_type: type })); await opening;
		const typedState = typed.editor;
		assert.equal(typedState.dialog.config.title, type + "新增");
		assert.equal(typedState.dialog.config.fields.filter((f) => !f.hidden).map((f) => f.label).join("/"), "编号/名称/备注/停用");
		await typedState.dialog.set_values({ value: type + "原文", remarks: "备注原文", inactive: 0 });
		const saving = typed.save(typedState);
		assert.equal(calls.at(-1).options.args.payload.dictionary_type, type);
		assert.equal("legacy_number" in calls.at(-1).options.args.payload, false);
		calls.at(-1).resolve(customer({ dictionary_type: type, value: type + "原文", remarks: "备注原文" })); await saving;
		assert.equal(typedState.unresolved, false);
		calls.at(-1).resolve(result({ dictionary_type: type })); await tick();
		typedState.dialog.hide(); entry.stop();
	}
	const workspace = Object.create(window.JifengERPWorkspace.Workspace.prototype), opened = [];
	workspace.formalBridge = { open: (spec) => opened.push(spec) }; workspace.addTab = () => { throw new Error("no local preview fallback"); };
	workspace.openPage("客户类型", "销售"); assert.equal(opened[0].customerType, "客户类型"); assert.equal(opened[0].availability, "partial");
	const component = window.JifengCustomerType; delete window.JifengCustomerType;
	workspace.openPage("客户类型", "销售"); assert.equal(opened.length, 1); assert.match(messages.at(-1), /客户类型页面未能加载/);
	window.JifengCustomerType = component;
	for (const [module, name] of [["销售", "客户等级"], ["销售", "客户分类"], ["订单", "订单客户分类"], ["订单", "订单客户级别"]]) {
		workspace.openPage(name, module);
		assert.equal(opened.at(-1).customerType, name); assert.equal(opened.at(-1).availability, "partial");
		delete window.JifengCustomerType; const count = opened.length;
		workspace.openPage(name, module); assert.equal(opened.length, count); assert.match(messages.at(-1), new RegExp(name + "页面未能加载"));
		window.JifengCustomerType = component;
	}
	console.log("Five customer dictionaries: exact bridge identities, seven/eleven observed columns, four-field saves, scoped paging, cross-type response rejection, unknown-result recovery and no preview fallback passed (models).");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
