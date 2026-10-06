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

    const temporaryRow = { name: "TEMP-1", company: "C-2", status: "Active",
        employee_version: "employee-v1", supplement_version: "supplement-v1" };
    let temporaryCalls = [];
    frappe.call = async options => { temporaryCalls.push(options); return { message: { ...options.args, eligible: true } }; };
    const temporary = workbench();
    check(await temporary.chooseTemporaryWorker("C-2", temporaryRow) &&
        temporary.value("company") === "C-2" && temporary.value("employee") === "TEMP-1",
        "temporary selection uses its own current classification service, independent of piece-pay eligibility");
    check(temporaryCalls[0].type === "GET" && temporaryCalls[0].method.endsWith("select_temporary_worker") &&
        Object.keys(temporaryCalls[0].args).sort().join(",") === "company,employee,employee_version,supplement_version",
        "temporary eligibility is read-only and binds the exact two list versions");
    const dirtyTemporary = workbench();
    dirtyTemporary.rows = [{}];
    check(await dirtyTemporary.chooseTemporaryWorker("C-2", temporaryRow) === false && temporaryCalls.length === 1,
        "temporary selection cannot overwrite existing manual wage drafts");
    frappe.call = async options => ({ message: { ...options.args, eligible: true, supplement_version: "new-v2" } });
    const mismatched = workbench();
    check(await mismatched.chooseTemporaryWorker("C-2", temporaryRow) === false && mismatched.value("employee") === "E-1",
        "a changed classification/version reply cannot refill the wage page");
    for (const kind of ["closed", "list_changed", "person_changed", "route_changed"]) {
        const delayed = workbench();
        let finish, current = true;
        frappe.call = () => new Promise(resolve => { finish = resolve; });
        const pendingChoice = delayed.chooseTemporaryWorker("C-2", temporaryRow, () => current);
        if (kind === "closed") delayed.root.isConnected = false;
        if (kind === "list_changed") current = false;
        if (kind === "person_changed") delayed.controls.employee.set_value("MANUALLY-CHANGED");
        if (kind === "route_changed") frappe.get_route = () => ["List", "Jifeng Wage Entry"];
        finish({ message: { company: "C-2", employee: "TEMP-1", employee_version: "employee-v1",
            supplement_version: "supplement-v1", eligible: true } });
        check(await pendingChoice === false && delayed.value("company") === "C-1" &&
            delayed.value("employee") === (kind === "person_changed" ? "MANUALLY-CHANGED" : "E-1"),
            `late ${kind} selection does not replace a clean or changed wage page`);
        delete frappe.get_route;
    }
    const interrupted = workbench();
    let current = true;
    const setCompany = interrupted.controls.company.set_value;
    interrupted.controls.company.set_value = async value => { setCompany(value); if (value === "C-2") current = false; };
    frappe.call = async options => ({ message: { ...options.args, eligible: true } });
    check(await interrupted.chooseTemporaryWorker("C-2", temporaryRow, () => current) === false &&
        interrupted.value("company") === "C-1" && interrupted.value("employee") === "E-1" && !interrupted.selectionBusy,
        "changing selector during asynchronous company refill restores the original clean selection");

    for (const mode of ["piece", "temporary"]) {
        const initializing = workbench(); let finishCompany, reads = 0;
        initializing.companyReady = new Promise(resolve => { finishCompany = resolve; });
        frappe.call = async options => { reads++; return { message: { ...options.args, eligible: true } }; };
        const select = () => mode === "piece" ? initializing.choosePieceWorker("C-2", "E-2") :
            initializing.chooseTemporaryWorker("C-2", temporaryRow);
        const first = select(), second = select();
        check(reads === 0, `${mode} qualification waits for the native initial Company validation`);
        initializing.controls.company.set_value("C-DEFAULT"); finishCompany();
        const results = await Promise.all([first, second]);
        check(results.filter(Boolean).length === 1 && reads === 1 && initializing.value("company") === "C-2" &&
            initializing.value("employee") === (mode === "piece" ? "E-2" : "TEMP-1"),
            `${mode} snapshots stable initial values and serializes parallel choices`);
        const changedDraft = workbench(); let finishDraftCompany, changedReads = 0;
        changedDraft.companyReady = new Promise(resolve => { finishDraftCompany = resolve; });
        frappe.call = async options => { changedReads++; return { message: { ...options.args, eligible: true } }; };
        const late = mode === "piece" ? changedDraft.choosePieceWorker("C-2", "E-2") :
            changedDraft.chooseTemporaryWorker("C-2", temporaryRow);
        changedDraft.rows = [{}]; finishDraftCompany();
        check(await late === false && changedReads === 0 && changedDraft.value("employee") === "E-1",
            `${mode} cannot overwrite a draft created while initialization was pending`);
    }

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

    const history = workbench();
    const values = { work_date: "2026-10-01", operation: "O1", job_card: "J1", piece_rate: 7 };
    const status = { textContent: "" }, button = { addEventListener: (_, action) => { button.click = action; } };
    const host = { querySelector: selector => selector === "[data-call-rate]" ? button : status };
    const wrapper = { html: () => {}, get: () => host };
    let finishSetter = null;
    const dialog = { is_visible: true, fields_dict: { history_rate: { $wrapper: wrapper } },
        get_value: name => values[name], set_value: async (name, value) => {
            values[name] = value; await dialog.fields_dict[name].df.onchange?.();
            if (finishSetter) await new Promise(resolve => { finishSetter = resolve; });
        } };
    for (const name of ["piece_rate", "operation", "work_date"]) dialog.fields_dict[name] = { df: {} };
    let sourceProof = { job_card: "J1", operation: "O1", stock_uom: "Nos",
        source_modified: "2026-10-01 08:00:00", work_order_modified: "2026-10-01 09:00:00" };
    let picker;
    context.window = { JifengWageRatePicker: class { constructor(options) { picker = options; } } };
    const quote = history.bindHistoryRate(dialog, { company: "C-1", employee: "E-1" }, () => sourceProof);
    const selected = { name: "H1", version: "2026-09-01 08:00:00.000001", work_date: "2026-09-01", piece_rate: "0", stock_uom: "Nos" };
    await button.click();
    check(picker.stillCurrent(), "page picker begins only on the exact current person/source/price");
    check(await picker.onChoose(selected) === true && values.piece_rate === 0, "page adopts explicit zero price without saving a wage");
    check(quote.reference().piece_rate_reference === "H1" && quote.reference().piece_rate_reference_version === selected.version,
        "page keeps the original wage and exact version in payload fields");
    await dialog.set_value("piece_rate", 9);
    check(!quote.reference().piece_rate_reference && values.piece_rate === 9, "manual page price clears quote and retains manual value");
    await button.click();
    dialog.is_visible = false;
    check(await picker.onChoose(selected) === false && values.piece_rate === 9, "closed page dialog rejects a delayed historical choice");
    dialog.is_visible = true;
    await button.click();
    sourceProof = { ...sourceProof, source_modified: "2026-10-01 08:00:01" };
    check(await picker.onChoose(selected) === false, "changed source proof rejects choice before setter");
    await button.click();
    finishSetter = true;
    const pendingChoice = picker.onChoose(selected);
    for (let spin = 0; spin < 4 && typeof finishSetter !== "function"; spin++) await Promise.resolve();
    values.piece_rate = 12;
    finishSetter(); finishSetter = null;
    check(await pendingChoice === false && values.piece_rate === 12 && !quote.reference().piece_rate_reference,
        "manual change during native setter preserves current price and cannot attach a quote");
    history.rows = [{ piece_rate_reference: "H1", piece_rate_reference_version: selected.version, flow_order: "F1" }];
    check(history.payload().rows[0].piece_rate_reference === "H1" && !Object.hasOwn(history.payload().rows[0], "flow_order"),
        "page save keeps both reference fields and excludes display-only flow data");
    console.log(JSON.stringify({ ok: true, checks, browser_verified: false }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
