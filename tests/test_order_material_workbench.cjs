// Client request-state checks only; native browser evidence is recorded separately.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const messages = [];
const frappe = { pages: { "jifeng-order-material-workbench": {} }, msgprint: text => messages.push(text),
    utils: { escape_html: text => text.replaceAll("<", "&lt;").replaceAll('"', "&quot;") } };
const context = vm.createContext({ frappe });
const source = fs.readFileSync(path.join(__dirname, "../jifeng_erp/jifeng_erp/page/jifeng_order_material_workbench/jifeng_order_material_workbench.js"), "utf8");
vm.runInContext(source + "\nglobalThis.Workbench=JifengOrderMaterialWorkbench;", context);
function workbench() {
    const object = Object.create(context.Workbench.prototype);
    const elements = {};
    object.root = { querySelector: selector => elements[selector] ||= { checked: true, textContent: "", innerHTML: "" } };
    const values = { company: "COMPANY", from_date: "2026-10-01", to_date: "2026-10-01", keyword: "", flow_order: "" };
    object.controls = Object.fromEntries(Object.keys(values).map(field => [field, { get_value: () => values[field] }]));
    Object.assign(object, { request: 0, mode: "analysis", data: null, resultKey: "", states: {} });
    object.render = () => {};
    return { object, values };
}
async function main() {
    let checks = 0;
    const check = (truth, label) => { assert.ok(truth, label); checks++; };
    const { object, values } = workbench();
    check(object.currentFilters().specified_date === 1, "analysis defaults to specified date");
    object.root.querySelector("[data-material-specified]").checked = false;
    check(object.currentFilters().specified_date === 0, "analysis explicitly disables date scope");
    object.mode = "summary";
    check(object.currentFilters().specified_date === 1, "summary always retains its visible date range");
    object.mode = "analysis";
    let resolve;
    frappe.call = () => new Promise(done => { resolve = done; });
    const pending = object.query();
    values.keyword = "changed while waiting";
    resolve({ message: { materials: [], summary: [], flows: [] } });
    await pending;
    check(object.data === null, "outdated query response never becomes current data");
    object.data = { materials: [], summary: [], flows: [] };
    object.resultKey = "stale";
    assert.throws(() => object.ensureCurrent(), /重新查询/); checks++;
    object.busy = true;
    check(object.is_dirty() && object.get_close_block().includes("稍后关闭"), "in-flight create cannot close");
    object.busy = false;
    const dialog = { get_value: () => "WH", disable_primary_action: () => {}, enable_primary_action: () => {},
        set_primary_action: () => {}, hide: () => {} };
    const actionButton = { disabled: false, textContent: "生成领料出库草稿", onclick: null };
    let writes = 0;
    frappe.call = async options => { if (options.type === "POST") writes++; throw new Error("synthetic lost response"); };
    await object.createIssue(dialog, { flow_order: "FLOW", flow_modified: "VERSION" }, "COMPANY", actionButton);
    check(writes === 1 && object.unknown && object.is_dirty(), "unknown native draft request freezes");
    await object.createIssue(dialog, { flow_order: "FLOW", flow_modified: "VERSION" }, "COMPANY", actionButton);
    check(writes === 1, "unknown request never blindly repeats POST");
    try { await object.checkIssue(dialog, "FLOW", "COMPANY"); } catch {}
    check(object.unknown && !object.busy, "failed status read retains unknown state");
    let opened = "";
    object.openNative = (_, name) => { opened = name; };
    frappe.call = async () => ({ message: { vouchers: [{ name: "EXISTING-DRAFT", docstatus: 0 }] } });
    await object.checkIssue(dialog, "FLOW", "COMPANY");
    check(!object.unknown && opened === "EXISTING-DRAFT" && writes === 1, "successful check opens original draft without new mutation");
    object.data = { materials: [{ material_code: "<script>" }], summary: [] };
    check(!object.table().includes("<script>"), "server strings are escaped in material grid");
    object.mode = "summary";
    object.data.summary = [{ material_code: "RAW", material_name: "RAW", quantity: 3,
        sources: [{ order: "SO1" }, { order: "SO2" }] }];
    check(object.cell(object.data.summary[0], "order").includes("2"), "multi-order summary does not masquerade as one source");
    console.log(JSON.stringify({ ok: true, checks, browser_verified: false }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
