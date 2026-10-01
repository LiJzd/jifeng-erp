// Pure client protocol tests: no browser, HTTP or native UI is opened.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const crypto = require("node:crypto");

const messages = [];
const storage = new Map();
const sessionStorage = {
    getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
};
const frappe = { pages: { "jifeng-wage-workbench": {} }, session: { user: "SYNTHETIC-USER" },
    msgprint: message => messages.push(message),
    utils: { escape_html: text => String(text).replaceAll("<", "&lt;"), get_form_link: (_, name) => "/desk/wage/" + name } };
const context = vm.createContext({ frappe, crypto, sessionStorage, queueMicrotask });
const source = fs.readFileSync(path.join(__dirname, "../jifeng_erp/jifeng_erp/page/jifeng_wage_workbench/jifeng_wage_workbench.js"), "utf8");
vm.runInContext(source + "\nglobalThis.Workbench = JifengWageWorkbench;", context);

function workbench() {
    storage.clear();
    const object = Object.create(context.Workbench.prototype);
    Object.assign(object, { rows: [], busy: false, frozen: false,
        requestId: crypto.randomUUID(), storageKey: "SYNTHETIC-PENDING", batchPerson: { company: "C-1", employee: "E-1" } });
    const elements = {};
    object.root = { querySelector: selector => elements[selector] ||= { checked: false, innerHTML: "", textContent: "" } };
    const values = { company: "C-1", employee: "E-1", payroll_date: "2026-10-01", salary_component: "PAY" };
    object.controls = Object.fromEntries(Object.entries(values).map(([field, value]) => [field, {
        get_value: () => values[field], set_value: next => { values[field] = next; }, df: {}, refresh: () => {} }]));
    object.lock = frozen => { object.frozen = frozen; };
    object.renderRows = () => {};
    return object;
}

async function main() {
    let checks = 0;
    const check = (condition, message) => { assert.ok(condition, message); checks++; };
    let randomCalls = 0;
    context.crypto = { getRandomValues: bytes => { randomCalls += 1; bytes.fill(255); return bytes; } };
    check(workbench().requestID() === "ffffffff-ffff-4fff-bfff-ffffffffffff" && randomCalls === 1,
        "LAN HTTP fallback uses real secure bytes and sets UUID4 version and variant");
    context.crypto = crypto;
    const dirty = workbench();
    check(!dirty.is_dirty(), "empty page is clean");
    dirty.rows = [{}]; check(dirty.is_dirty(), "unsaved rows are dirty");
    check(!dirty.get_close_block(), "ordinary rows use discard confirmation");
    dirty.rows = []; dirty.busy = true;
    check(dirty.is_dirty() && dirty.get_close_block().includes("正在保存"), "in-flight save blocks closing");
    dirty.busy = false; dirty.frozen = true;
    check(dirty.is_dirty() && dirty.get_close_block().includes("尚未核实"), "unknown result blocks closing");
    dirty.frozen = false; dirty.activeDialog = { is_visible: true };
    check(dirty.is_dirty(), "open unsaved dialog is dirty");

    const gestures = workbench();
    let action = "";
    gestures.entryDialog = timed => { action = timed ? "timing" : "piece"; };
    gestures.allocationDialog = () => { action = "allocation"; };
    gestures.nameDoubleClick(); check(action === "timing", "ordinary name double-click enters explicit timing");
    gestures.root.querySelector("[data-wage-multi]").checked = true;
    gestures.root.querySelector("[data-wage-multi-operation]").checked = true;
    gestures.nameDoubleClick(); check(action === "allocation", "name selects allocation even with both independent modes enabled");

    const selection = workbench();
    let finishSelection;
    frappe.call = () => new Promise(resolve => { finishSelection = resolve; });
    const choosing = selection.choosePieceWorker("C-2", "E-2");
    check(selection.selectionBusy && selection.get_close_block().includes("正在核对"), "pending qualification blocks closing");
    let entryCalls = 0;
    await selection.guard(() => { entryCalls += 1; });
    check(entryCalls === 0, "pending qualification blocks add and double-click business actions");
    await assert.rejects(selection.save(), /正在核对/);
    checks += 1;
    selection.controls.employee.set_value("MANUALLY-CHANGED");
    finishSelection({ message: { company: "C-2", employee: "E-2", eligible: true } });
    check(await choosing === false && selection.value("employee") === "MANUALLY-CHANGED" && selection.value("company") === "C-1",
        "changing employee during delayed RPC prevents overwrite or save");
    check(!selection.selectionBusy, "failed selection releases its pending guard");
    frappe.call = async () => ({ message: { company: "C-2", employee: "E-2", eligible: true } });
    check(await selection.choosePieceWorker("C-2", "E-2") && selection.value("company") === "C-2" && selection.value("employee") === "E-2",
        "verified clean selection pre-fills exact company and employee");

    const unknown = workbench();
    unknown.rows = [{ quantity: 0, hours: 1, hourly_rate: 10, flow_order: "", job_card: "" }];
    const calls = [];
    frappe.call = async options => { calls.push(options.method); throw new Error("synthetic lost connection"); };
    await unknown.save();
    check(unknown.frozen && unknown.rows.length === 1, "failed status retains unknown rows and freezes");
    check(calls.filter(method => method.endsWith("save_drafts")).length === 1, "unknown save sent once");
    const pending = JSON.parse(storage.get(unknown.storageKey));
    check(Object.keys(pending).sort().join(",") === "company,employee,request_id", "session marker contains no payroll details or credentials");
    await unknown.save();
    check(calls.filter(method => method.endsWith("save_drafts")).length === 1, "frozen save button only checks status; never resends");

    const pendingText = storage.get(unknown.storageKey);
    const recovered = workbench();
    storage.set(recovered.storageKey, pendingText);
    recovered.restorePending();
    check(recovered.frozen && recovered.requestId === pending.request_id, "reload restores same request and blocks new batch");
    frappe.call = async options => { calls.push(options.method); return { message: { wages: [] } }; };
    await recovered.checkResult();
    check(!recovered.frozen && !storage.has(recovered.storageKey), "verified absent request unlocks and clears marker");
    check(recovered.requestId !== pending.request_id, "new ID only after verified absent result");

    const saved = workbench();
    saved.rows = [{ quantity: 0, hours: 1, hourly_rate: 10, flow_order: "", job_card: "" }];
    frappe.call = async () => ({ message: { wages: [{ name: "SYNTHETIC-WAGE", docstatus: 0 }] } });
    await saved.save();
    check(saved.rows.length === 0 && !saved.frozen && !saved.busy && !saved.is_dirty(), "confirmed save clears only saved rows and dirty state");
    check(!storage.has(saved.storageKey), "confirmed save clears session marker");

    console.log(JSON.stringify({ ok: true, checks, browser_verified: false }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
