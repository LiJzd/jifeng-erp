// Execute the production component with a DOM and deferred Frappe protocol.
// No browser, network, database or accounting writes.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), nodeCrypto = require("node:crypto");
const calls = [], messages = [], opened = [], dialogs = [], watchers = []; let confirmation;
const nextCall = (suffix) => new Promise((resolve) => watchers.push({ suffix, resolve }));
const tick = () => new Promise(setImmediate);
class Element {
	constructor(tag = "div") { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this.classList = { add() {} }; this.value = ""; this._text = ""; this.scrollTop = 0; this.scrollLeft = 0; }
	set textContent(value) { this._text = String(value); this.children = []; }
	get textContent() { return this._text + this.children.map((node) => node.textContent || "").join(""); }
	set innerHTML(_) { throw new Error("No source HTML is rendered"); }
	append(...nodes) { this.children.push(...nodes); }
	replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
	setAttribute(name, value) { this.attributes[name] = value; }
	addEventListener(name, callback) { this.events.set(name, callback); }
	descendants(tag) { return this.children.flatMap((node) => [...(node.tag === tag ? [node] : []), ...node.descendants(tag)]); }
}
class Wrapper {
	constructor(node = new Element()) { this.node = node; this.events = new Map(); }
	get() { return this.node; }
	addClass() { return this; }
	modal() { return this; }
	removeAttr() { return this; }
	on(events, callback) { for (const event of events.split(" ")) this.events.set(event.split(".")[0], callback); return this; }
}
class Dialog {
	constructor(options) { this.options = options; this.$wrapper = new Wrapper(); this.close = new Wrapper(); this.fields_dict = Object.fromEntries(options.fields.map((field) => [field.fieldname, { $wrapper: new Wrapper() }])); dialogs.push(this); }
	get_close_btn() { return this.close; }
	show() { this.hidden = false; }
	hide() { this.hidden = true; this.on_hide?.(); }
}
const window = { addEventListener() {}, removeEventListener() {} }, document = { createElement: (tag) => new Element(tag) };
const frappe = { ui: { Dialog, form: { make_control({ df }) { return { df: { ...df }, $input: new Wrapper(), value: null,
	get_value() { return this.value; }, set_input(value) { this.value = value; }, async set_value(value) { this.value = value; await df.onchange?.(); }, refresh() {} }; } } },
	call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); const call = { options, resolve: (message) => resolve({ message }), reject }; calls.push(call); for (const watcher of [...watchers]) if (options.method.endsWith(watcher.suffix)) { watchers.splice(watchers.indexOf(watcher), 1); watcher.resolve(call); } return promise; },
	msgprint(message) { messages.push(message); }, confirm(message, yes, no) { confirmation = { message, yes, no }; } };
const context = vm.createContext({ window, document, frappe, __: (value) => value, BigInt, crypto: nodeCrypto.webcrypto, Uint8Array, TextEncoder });
for (const file of ["jifeng_tool_costs", "jifeng_tool_archive"]) vm.runInContext(fs.readFileSync(path.join(__dirname, `../jifeng_erp/public/js/${file}.js`), "utf8"), context);
const Costs = window.JifengToolCosts, scope = { company: "Company A", record_type: "mold", name: "MOLD-1" }, stamp = "2026-10-03T01:02:03.000004", bridge = { open: (spec) => opened.push(spec) };
const source = (changes = {}) => ({ company: "Company A", expense_voucher: "EXP-1", expense_version: stamp, journal_entry: "JE-1", journal_version: stamp, currency: "CNY", posting_date: "2026-10-03", historical_amount: "120", incurred_amount: "120", paid_amount: "40", unpaid_amount: "80", source_state: "submitted", payment_basis: "native_payment_entry_gl_ple", payments: [{ name: "PE-1", version: stamp, posting_date: "2026-10-03", docstatus: 1, allocated_amount: "40", actual_paid: "40" }], ...changes });
const item = (changes = {}) => ({ record_type: "mold", tool_doctype: "Jifeng Mold", tool_name: "MOLD-1", tool_version: stamp, tool_label: "业务模具 1", historical_amount: "70", incurred_amount: "70", paid_amount: null, unpaid_amount: null, payment_state: "partial_payment_tool_unknown", payment_reason: "原生付款没有逐工具分配，部分付款不能拆到工具", ...changes });
const allocation = (changes = {}) => ({ name: "COST-1", version: stamp, company: "Company A", docstatus: 1, currency: "CNY", reason: "手工归集", cancel_reason: null, can_cancel: true, source: source(), items: [item(), item({ record_type: "plate", tool_doctype: "Jifeng Plate", tool_name: "PLATE-1", tool_label: "业务版种 1", historical_amount: "50", incurred_amount: "50" })], ...changes });
const summary = (changes = {}) => ({ ...scope, currency: "CNY", can_create: true, incurred_amount: "70", paid_amount: null, unpaid_amount: null, allocations: [allocation()], ...changes });
const tool = (kind = "mold", changes = {}) => ({ record_type: kind, name: kind === "mold" ? "MOLD-1" : "PLATE-1", version: stamp, values: { company: "Company A", mold_no: "业务模具 1", plate_no: "业务版种 1", amount: "999", reference_cost: "888" }, field_meta: { company: { fieldtype: "Link", options: "Company" } }, ...changes });
async function make(options = {}, value = summary()) { const costs = Costs.open(scope, bridge, options); const call = calls.at(-1); assert.equal(call.options.type, "GET"); assert.equal(call.options.method, "jifeng_erp.tool_cost_sources.get_tool_costs"); call.resolve(value); await costs.ready; return costs; }
async function readSource(costs, value = source()) { await costs.expenseControl.set_value(value.expense_voucher); const pending = costs.readSource(); const call = calls.at(-1); assert.equal(call.options.type, "GET"); call.resolve(value); await pending; }
async function choose(costs, kind = "mold", value = tool(kind)) { const pending = costs.selectTool(kind, value.name); const call = calls.at(-1); assert.equal(call.options.type, "GET"); assert.equal(call.options.method, "jifeng_erp.tool_archive.read_record"); call.resolve(value); await pending; }
async function prepare(costs) { await readSource(costs); await choose(costs); await choose(costs, "plate"); assert.deepEqual(Array.from(costs.items, (row) => row.amount), ["", ""], "reference amounts never populate allocations"); for (const [index, amount] of ["70", "50"].entries()) { costs.items[index].input.value = amount; costs.items[index].input.events.get("input")(); } costs.reason.value = "依据费用单分摊"; costs.reason.events.get("input")(); }
async function readDetail(costs, value = allocation()) { const pending = costs.readAllocation(value.name, value.version); assert.equal(calls.at(-1).options.type, "GET"); calls.at(-1).resolve(value); await pending; }
async function main() {
	const typing = await make();
	const control = typing.expenseControl;
	let text = "", refreshes = 0;
	control.value = "";
	control.get_value = () => control.df.read_only ? control.value : text;
	control.set_input = (value) => { control.value = value; text = value; };
	control.refresh = () => { refreshes++; control.set_input(control.value); };
	for (const value of ["E", "EXP-1"]) {
		text = value;
		control.$input.events.get("input")();
		assert.equal(text, value, "native standalone Link typing keeps input before blur");
	}
	assert.equal(refreshes, 0, "typing must not restore the previous model value");
	typing.busy = true; typing.freeze();
	assert.equal(control.get_value(), "EXP-1", "locking preserves the entered source");
	typing.busy = false; typing.freeze();
	assert.equal(control.get_value(), "EXP-1", "unlocking preserves the entered source");
	await readSource(typing);
	assert.equal(typing.source.expense_voucher, "EXP-1");
	typing.destroy();
	assert.equal(Costs.money("1e2"), null); assert.equal(Costs.money("-1"), null); assert.equal(Costs.money("1.0000000001"), null); assert.equal(Costs.money("0", true), null); assert.equal(Costs.money("1000000000000000000000"), null);
	const twoHundredOne = Array.from({ length: 201 }, () => Costs.money("0.01").units).reduce((sum, value) => sum + value, 0n); assert.equal(Costs.amount(twoHundredOne), "2.01"); assert.equal(Costs.money("0001.010000000").text, "1.01");
	let costs = await make(); assert.match(costs.summaryHost.textContent, /已发生：70 CNY/); assert.match(costs.summaryHost.textContent, /已付：未核实/); assert.doesNotMatch(costs.summaryHost.textContent, /23\.33|46\.66/);
	await readDetail(costs); assert.match(costs.details.textContent, /没有逐工具分配/); assert.equal(costs.cancelPanel.hidden, false); assert.match(costs.details.textContent, /未核实/);
	const failed = costs.query(); assert.equal(costs.summaryHost.textContent, ""); calls.at(-1).reject(new Error("PermissionError")); await failed; assert.equal(costs.summary, null); assert.equal(costs.detail, null); assert.equal(costs.details.textContent, ""); assert.match(costs.status.textContent, /来源未核实/);
	const restored = costs.query(); calls.at(-1).resolve(summary()); await restored; await readSource(costs); assert.match(costs.sourceHost.textContent, /实际付款：40 CNY/);
	const overlapping = costs.readSource(), overlappingCall = calls.at(-1), deniedQuery = costs.query(), deniedQueryCall = calls.at(-1); deniedQueryCall.reject(new Error("source no longer visible")); await deniedQuery; overlappingCall.resolve(source()); await overlapping; assert.equal(costs.source, null, "permission failure invalidates other in-flight source responses"); assert.equal(costs.sourceHost.textContent, "");
	const latestSource = costs.readSource(), oldSource = calls.at(-1); costs.expenseControl.value = "EXP-2"; costs.expenseControl.$input.events.get("input")(); oldSource.resolve(source()); await latestSource; assert.equal(costs.source, null); assert.equal(costs.sourceHost.textContent, "");
	await readSource(costs); const deniedSource = costs.readSource(); calls.at(-1).reject(new Error("Version changed")); await deniedSource; assert.equal(costs.summaryHost.textContent, ""); assert.equal(costs.sourceHost.textContent, ""); costs.destroy();
	costs = await make(); const old = costs.query(), oldCall = calls.at(-1), recent = costs.query(), recentCall = calls.at(-1); recentCall.resolve(summary({ incurred_amount: "71" })); await recent; oldCall.resolve(summary({ incurred_amount: "999" })); await old; assert.match(costs.summaryHost.textContent, /已发生：71/);
	let permitted = true; const contextual = await make({ stillCurrent: () => permitted }); const delayed = contextual.query(); const delayedCall = calls.at(-1); permitted = false; delayedCall.resolve(summary()); await delayed; assert.equal(contextual.summaryHost.textContent, ""); assert.match(contextual.status.textContent, /公司或工具已改变/); contextual.destroy();
	const disposed = costs.query(), disposedCall = calls.at(-1); costs.destroy(); disposedCall.resolve(summary()); await disposed; assert.equal(costs.summary, null); assert.equal(costs.host.textContent, "");
	costs = await make(); costs.kind.value = "plate"; costs.search.value = "业务"; const search = costs.lookup(); assert.equal(calls.at(-1).options.type, "GET"); calls.at(-1).resolve([tool("plate")]); await search;
	const selection = costs.selectTool("plate", "PLATE-1", stamp); assert.equal(calls.at(-1).options.type, "GET"); calls.at(-1).resolve(tool("plate", { version: stamp + "1" })); await selection; assert.equal(costs.items.length, 0); assert.equal(costs.summary, null); assert.match(costs.status.textContent, /版本未核实/);
	const refresh = costs.query(); calls.at(-1).resolve(summary()); await refresh; await prepare(costs); assert.equal(costs.createButton.disabled, false); costs.items[1].input.value = "49.99"; costs.items[1].input.events.get("input")(); assert.equal(costs.createButton.disabled, true); const noWrite = calls.length; await costs.create(); assert.equal(calls.length, noWrite);
	costs.items[1].input.value = "50"; costs.items[1].input.events.get("input")(); const arrived = nextCall("create_tool_cost_allocation"), creating = costs.create(), write = await arrived; assert.equal(write.options.type, "POST"); assert.equal(write.options.method, "jifeng_erp.tool_cost_sources.create_tool_cost_allocation"); assert.equal(write.options.args.context.expense_version, stamp); assert.equal(write.options.args.items[0].version, stamp); assert.equal(write.options.args.items[0].amount, "70"); const request = write.options.args.request_id, digest = costs.pending.request_digest; assert.match(request, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/); assert.match(digest, /^[a-f0-9]{64}$/);
	await costs.create(); assert.equal(calls.at(-1), write, "double click cannot issue a second POST"); write.reject(new Error("response lost")); await creating; assert.equal(costs.pending.request_id, request); assert.equal(costs.source, null); assert.equal(costs.summaryHost.textContent, ""); assert.equal(costs.items.length, 0); assert.equal(costs.resolveButton.hidden, false);
	assert.equal(digest, "6d59f565fce4160c15572ee2d96722551d97ad7c97240b91dd3c835c649ce053", "matches Python native sorted UTF-8 JSON digest for the Unicode draft fixture");
	await costs.dialog.hide(); assert.equal(costs.disposed, false); assert.match(messages.at(-1), /结果尚未确认/); const beforeResolve = calls.filter((call) => call.options.type === "POST").length; const empty = costs.resolve(); assert.equal(calls.at(-1).options.type, "GET"); assert.equal(calls.at(-1).options.args.request_id, request); calls.at(-1).resolve(null); await empty; assert.equal(costs.pending.request_id, request); await costs.create(); assert.equal(calls.filter((call) => call.options.type === "POST").length, beforeResolve);
	const wrongReceipt = costs.resolve(); calls.at(-1).resolve({ name: "COST-2", version: stamp, company: "Company B", docstatus: 0, request_id: request, request_digest: digest }); await wrongReceipt; assert.equal(costs.pending.request_id, request);
	const wrongContent = costs.resolve(); calls.at(-1).resolve({ name: "COST-2", version: stamp, company: "Company A", docstatus: 0, request_id: request, request_digest: "a".repeat(64) }); await wrongContent; assert.equal(costs.pending.request_id, request, "same UUID with another content digest is not this draft receipt");
	const confirmed = costs.resolve(); calls.at(-1).resolve({ name: "COST-2", version: stamp, company: "Company A", docstatus: 0, request_id: request, request_digest: digest }); await confirmed; await tick(); assert.equal(costs.disposed, true); assert.deepEqual(Array.from(opened.at(-1).route), ["Form", "Jifeng Tool Cost Allocation", "COST-2"]); assert.equal(calls.filter((call) => call.options.type === "POST").length, beforeResolve);
	for (const [status, exc_type] of [[403, "PermissionError"], [417, "ValidationError"]]) {
		costs = await make(); await prepare(costs); const arrival = nextCall("create_tool_cost_allocation"), saving = costs.createButton.events.get("click")(), rejectedWrite = await arrival;
		rejectedWrite.reject({ status, readyState: 4, responseJSON: { exc_type } }); await saving; assert.equal(costs.pending, null, "an answered native rejection releases pending"); assert.equal(costs.getCloseBlock(), ""); assert.equal(costs.summaryHost.textContent, ""); assert.equal(costs.sourceHost.textContent, ""); assert.equal(costs.details.textContent, ""); assert.equal(costs.items.length, 0); assert.match(costs.status.textContent, /保存已被拒绝/); assert.equal(costs.refreshButton.disabled, false);
		const recovery = costs.refreshButton.events.get("click")(); assert.equal(calls.at(-1).options.type, "GET"); calls.at(-1).resolve(summary()); await recovery; assert.ok(costs.summary); await prepare(costs); assert.equal(costs.createButton.disabled, false, "fresh authorized reads allow refilling after a definite rejection"); costs.items = []; costs.reason.value = ""; const close = costs.dialog.get_close_btn().events.get("click")(); await close; assert.equal(costs.disposed, true, "native close button works after confirmed refusal");
	}
	for (const rejection of [{ status: 417, responseJSON: { exc_type: "ValidationError", cleanup_unknown: true } }, { status: 403, responseJSON: { exc_type: "PermissionError", exc: "离职局部回滚清理失败（database_rollback:RuntimeError），调用方须完整回滚并核对" } }, { status: 500, responseJSON: { exc_type: "ToolCostWriteUncertain" } }, { status: 0, responseJSON: { exc_type: "PermissionError" } }]) {
		costs = await make(); await prepare(costs); const arrival = nextCall("create_tool_cost_allocation"), saving = costs.createButton.events.get("click")(), failedWrite = await arrival; const unknownIdentity = failedWrite.options.args.request_id; failedWrite.reject(rejection); await saving;
		assert.equal(costs.pending.request_id, unknownIdentity, "unknown rollback/cleanup never becomes a definite refusal"); const count = calls.filter((call) => call.options.type === "POST").length; await costs.createButton.events.get("click")(); assert.equal(calls.filter((call) => call.options.type === "POST").length, count);
		const lookupReceipt = costs.resolveButton.events.get("click")(); assert.equal(calls.at(-1).options.type, "GET"); calls.at(-1).resolve(null); await lookupReceipt; assert.equal(costs.pending.request_id, unknownIdentity); await costs.dialog.get_close_btn().events.get("click")(); assert.equal(costs.disposed, false); costs.destroy();
	}
	const reader = await make({}, summary({ can_create: false })); await prepare(reader); assert.equal(reader.createButton.hidden, true); const readerCalls = calls.length; await reader.create(); assert.equal(calls.length, readerCalls); await readDetail(reader, allocation({ can_cancel: false })); assert.equal(reader.cancelPanel.hidden, true); const readerCancel = calls.length; await reader.cancel(); assert.equal(calls.length, readerCancel); reader.destroy();
	for (const [status, exc_type] of [[403, "PermissionError"], [417, "ValidationError"]]) {
		costs = await make(); await readDetail(costs); costs.cancelReason.value = "原工具归属需核对"; const cancelling = costs.cancelButton.events.get("click")(); confirmation.yes(); await tick(); const rejectedCancel = calls.at(-1); assert.equal(rejectedCancel.options.type, "POST"); rejectedCancel.reject({ status, readyState: 4, responseJSON: { exc_type } }); await cancelling;
		assert.equal(costs.pending, null); assert.equal(costs.getCloseBlock(), ""); assert.equal(costs.summaryHost.textContent, ""); assert.equal(costs.details.textContent, ""); assert.match(costs.status.textContent, /撤销已被拒绝/); const recovery = costs.refreshButton.events.get("click")(); assert.equal(calls.at(-1).options.type, "GET"); calls.at(-1).resolve(summary()); await recovery; await costs.dialog.get_close_btn().events.get("click")(); assert.equal(costs.disposed, true);
	}
	costs = await make(); await readDetail(costs); costs.cancelReason.value = "原分摊工具填错"; const denied = costs.cancel(); confirmation.no(); await denied; const accepted = costs.cancel(); confirmation.yes(); await tick(); const cancelWrite = calls.at(-1); assert.equal(cancelWrite.options.type, "POST"); assert.equal(cancelWrite.options.method, "jifeng_erp.tool_cost_sources.cancel_tool_cost_allocation"); assert.equal(cancelWrite.options.args.version, stamp); await costs.cancel(); assert.equal(calls.at(-1), cancelWrite); cancelWrite.reject(new Error("unknown cancel")); await accepted; assert.equal(costs.pending.operation, "cancel"); assert.equal(costs.details.textContent, "");
	const unchanged = costs.resolve(); assert.equal(calls.at(-1).options.type, "GET"); assert.deepEqual(Object.keys(calls.at(-1).options.args), ["name"], "unknown cancel reads current state without replaying a versioned POST"); calls.at(-1).resolve(allocation()); await unchanged; assert.ok(costs.pending);
	const cancellation = costs.resolve(); calls.at(-1).resolve(allocation({ version: stamp + "1", docstatus: 2, can_cancel: false, cancel_reason: "原分摊工具填错", items: [item({ incurred_amount: "0", payment_state: "not_active" }), item({ record_type: "plate", tool_doctype: "Jifeng Plate", tool_name: "PLATE-1", historical_amount: "50", incurred_amount: "0", payment_state: "not_active" })] })); await cancellation; assert.equal(costs.pending, null); assert.match(costs.details.textContent, /已撤销/); costs.destroy();
	let savedFilters; costs = await make({ onClose: (filters) => { savedFilters = filters; } }); costs.search.value = "原查询"; costs.kind.value = "modeling"; await costs.expenseControl.set_value("EXP-1"); await costs.dialog.hide(); assert.equal(costs.disposed, true); assert.deepEqual(Object.keys(savedFilters).sort(), ["expense_voucher", "kind", "search"]); assert.equal(savedFilters.search, "原查询"); assert.equal("source" in savedFilters, false);
	const returned = await make({ filters: savedFilters }); await tick(); assert.equal(returned.expenseControl.get_value(), "EXP-1"); assert.equal(returned.source, null, "return does not reuse previously authorized source amounts"); assert.equal(returned.kind.value, "modeling"); assert.equal(returned.search.value, "原查询"); returned.destroy();
	const sourceCancelled = await make({}, summary({ incurred_amount: "0", paid_amount: "0", unpaid_amount: "0", allocations: [allocation({ can_cancel: false, docstatus: 2, source: source({ source_state: "cancelled", incurred_amount: "0", paid_amount: "0", unpaid_amount: "0", payments: [] }), items: [item({ incurred_amount: "0", paid_amount: "0", unpaid_amount: "0", payment_state: "source_cancelled" })] })] })); assert.match(sourceCancelled.summaryHost.textContent, /已发生：0 CNY/); sourceCancelled.destroy();
	for (const variant of ["A", "B"]) {
		const archive = new window.JifengToolArchive(new Element(), "mold", bridge); archive.companyScope = "Company A"; archive.companyControl.value = "Company A"; archive.search.value = variant + " 原筛选";
		archive.rows = [tool("mold", { can_write: true, image_states: { image: {} } })]; archive.renderRows(); const button = archive.tbody.descendants("button").find((node) => node.textContent === "实际费用"); assert.ok(button && !button.disabled);
		button.events.get("click")(); await tick(); calls.at(-1).resolve(summary()); await archive.costs.ready; assert.equal(archive.costs.scope.name, "MOLD-1"); assert.equal(archive.costs.scope.company, "Company A");
		archive.costs.search.value = "分摊工具关键词"; await archive.costs.dialog.hide(); assert.equal(archive.costs, null); assert.equal(archive.search.value, variant + " 原筛选", "native return keeps the mounted archive query");
		button.events.get("click")(); await tick(); calls.at(-1).resolve(summary()); await archive.costs.ready; assert.equal(archive.costs.search.value, "分摊工具关键词"); assert.equal(archive.costs.source, null);
		const reopened = archive.costs; reopened.pending = { operation: "create", request_id: "12345678-1234-4234-8234-123456789abc" }; archive.companyControl.value = "Company B"; await archive.changeScope(); assert.equal(archive.companyScope, "Company A"); assert.equal(archive.companyControl.get_value(), "Company A"); assert.equal(archive.costs, reopened, "company change cannot abandon an unknown write"); reopened.pending = null;
		await prepare(reopened); const arrived = nextCall("create_tool_cost_allocation"), rejectedSaving = reopened.createButton.events.get("click")(), rejectedWrite = await arrived; rejectedWrite.reject({ status: 403, readyState: 4, responseJSON: { exc_type: "PermissionError" } }); await rejectedSaving; assert.equal(reopened.pending, null);
		archive.companyControl.value = "Company B"; await archive.changeScope(); assert.equal(archive.companyScope, "Company B", "company switch resumes after confirmed native refusal"); assert.equal(archive.costs, null); archive.destroy(); assert.equal(reopened.disposed, true); assert.equal(reopened.dialog.hidden, true);
	}
	assert.ok(calls.filter((call) => call.options.type === "POST").every((call) => ["create_tool_cost_allocation", "cancel_tool_cost_allocation"].some((name) => call.options.method.endsWith(name))));
	console.log("tool cost component: native-source display, exact amount entry, source/version/permission guards, late response, draft receipt, one-write recovery, definite native refusal recovery, cancel proof and fresh return filters passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
