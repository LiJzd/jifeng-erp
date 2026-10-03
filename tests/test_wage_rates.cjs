// Historical wage choice and native form models. No browser, HTTP or database.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const calls = [], messages = [], alerts = [], registrations = new Map(), locals = {};
const childForms = new Map(); let allocationNumber = 0;
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this._text = ""; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    set innerHTML(_) { throw new Error("Historical wage text must not execute HTML"); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, action) { this.events.set(name, action); }
    descendants(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Dialog {
    constructor(config) { this.config = config; this.fields_dict = { choices: { $wrapper: { get: () => new Element("div") } } }; }
    show() { this.hidden = false; }
    hide() { this.hidden = true; this.on_hide?.(); }
    get_primary_btn() { return { prop: (key, value) => { this[key] = value; } }; }
}
const window = {}, document = { createElement: tag => new Element(tag) };
const frappe = { ui: { Dialog, form: { on: (doctype, actions) => registrations.set(doctype, actions) } },
    call(options) {
        let resolve, reject; const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
        calls.push({ options, resolve: message => resolve({ message }), reject }); return pending;
    },
    msgprint: value => messages.push(value), show_alert: value => alerts.push(value), require: async () => {},
    user: { has_role: () => true }, set_route() {}, model: {}, db: {},
};
const sandbox = vm.createContext({ window, document, frappe, locals, __: value => value });
for (const file of ["jifeng_erp/public/js/jifeng_wage_rates.js",
    "jifeng_erp/jifeng_erp/doctype/jifeng_wage_entry/jifeng_wage_entry.js",
    "jifeng_erp/jifeng_erp/doctype/jifeng_wage_allocation/jifeng_wage_allocation.js"]) vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);
const tick = () => new Promise(resolve => setImmediate(resolve));
const context = (extra = {}) => ({ company: "C-1", employee: "E-1", operation: "OP-1", work_date: "2026-10-03", stock_uom: "个",
    job_card: "JC-1", source_modified: "2026-10-03 08:00:00", work_order_modified: "2026-10-02 08:00:00", ...extra });
const source = (extra = {}) => ({ name: '<img src=x onerror="bad()">WAGE-001', version: "2026-10-02 10:00:00", work_date: "2026-10-01",
    stock_uom: "个", piece_rate: "0.017", company: "C-1", employee: "E-1", operation: "OP-1", ...extra });
const page = (rows = [source()], extra = {}) => ({ scope: "wage_piece_rates", readonly: true, page: 1, page_size: 25,
    total: rows.length, total_pages: rows.length ? 1 : 0, rows, ...extra });
const choice = value => ({ scope: "wage_piece_rates", readonly: true, ...value });
const jobSource = (extra = {}) => ({ job_card: "JC-1", source_modified: context().source_modified,
    work_order_modified: context().work_order_modified, operation: "OP-1", stock_uom: "个", work_order: "WO-1", flow_order: "FLOW-1", ...extra });
function state(extra = {}) {
    const value = { context: context(), editable: true, price: "2", baseline: "2", sets: [], ...extra };
    value.create = () => new window.JifengWageRatePicker({ getContext: () => value.context,
        stillCurrent: () => value.editable && value.price === value.baseline, onChoose: async chosen => {
            value.sets.push(chosen); value.price = chosen.piece_rate; value.baseline = chosen.piece_rate; return true;
        } });
    return value;
}
async function populated(value = state(), rows = [source()]) {
    const picker = value.create(); assert.equal(calls.at(-1).options.type, "GET");
    calls.at(-1).resolve(page(rows)); await picker.ready; return { value, picker };
}
function form(extra = {}) {
    const frm = { doc: { name: "WAGE-DRAFT", modified: "2026-10-03 07:00:00", docstatus: 0, company: "C-1", employee: "E-1",
        operation: "OP-1", work_date: "2026-10-03", stock_uom: "个", job_card: "JC-1", piece_rate: 2,
        piece_rate_reference: "", piece_rate_reference_version: "" },
        fields_dict: { piece_rate: { df: { read_only: 0 } }, employees: { df: { read_only: 0 }, grid: { get_field: () => ({ read_only: 0 }) } } },
        permissions: new Set(["create", "write"]), buttons: [], sets: [],
        is_new() { return !!this.doc.__islocal; }, has_perm(type) { return this.permissions.has(type); },
        set_intro() {}, set_df_property(field, key, value) { if (this.fields_dict[field]) this.fields_dict[field].df[key] = value; },
        add_custom_button(name, action) { this.buttons.push({ name, action }); },
        async set_value(values) {
            this.sets.push({ ...values }); Object.assign(this.doc, values);
            for (const field of Object.keys(values)) await registrations.get("Jifeng Wage Entry")[field]?.(this);
        }, ...extra };
    return frm;
}
function allocation(extra = {}) {
    const frm = form(extra); delete frm.doc.employee;
    frm.doc.doctype = "Jifeng Wage Allocation";
    const row = { doctype: "Jifeng Wage Allocation Employee", name: "ROW-" + (++allocationNumber), employee: "E-1", quantity: 3, piece_rate: 2,
        include_time_pay: 1, wage_hours: 2, hourly_rate: 4, piece_rate_reference: "", piece_rate_reference_version: "" };
    frm.doc.employees = [row]; locals[row.doctype] ||= {}; locals[row.doctype][row.name] = row; childForms.set(row.name, frm);
    frappe.model.set_value = async (doctype, name, values) => {
        const target = locals[doctype][name], parent = childForms.get(name); parent.sets.push({ child: name, ...values }); Object.assign(target, values);
        for (const field of Object.keys(values)) await registrations.get(doctype)[field]?.(parent, doctype, name);
    };
    return { frm, row };
}
async function nativePicker(frm, row = frm.doc, rows = [source()]) {
    const pending = window.JifengWageRateTools.open(frm, row), read = calls.at(-1);
    assert.equal(read.options.method, "jifeng_erp.wage_workbench.get_entry_source"); assert.equal(read.options.type, "GET");
    read.resolve(jobSource()); const picker = await pending;
    assert.ok(picker); calls.at(-1).resolve(page(rows)); await picker.ready; return picker;
}
async function select(picker, result = choice(picker.rows[0].source)) {
    const pending = picker.choose(picker.rows[0].source); const read = calls.at(-1);
    assert.equal(read.options.method, "jifeng_erp.wage_rates.choose_piece_rate"); assert.equal(read.options.type, "GET");
    read.resolve(result); await pending;
}
async function main() {
    const beforeMissing = calls.length;
    assert.throws(() => state({ context: context({ job_card: "" }) }).create(), /已提交/);
    assert.equal(calls.length, beforeMissing);
    const normal = await populated();
    assert.equal(normal.value.sets.length, 0, "display order never auto-adopts the latest wage");
    assert.equal(normal.picker.body.descendants("td")[0].textContent, source().name, "source IDs are rendered as text");
    assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1).options.args.context)), context());
    await select(normal.picker); assert.equal(normal.value.sets.length, 1); assert.equal(normal.picker.closed, true);
    assert.match(alerts.at(-1).message, /核对.*保存/);
    const zero = await populated(state(), [source({ piece_rate: "0" })]);
    await select(zero.picker); assert.equal(zero.value.price, "0", "a real zero piece rate is selectable");
    for (const field of Object.keys(context())) {
        const value = state(), picker = value.create(); value.context = context({ [field]: field === "work_date" ? "2026-10-04" : "CHANGED" });
        calls.at(-1).resolve(page()); await picker.ready;
        assert.equal(picker.rows.length, 0, `late page cannot cross ${field}`);
    }
    const changedPrice = await populated(); const priceChoice = changedPrice.picker.choose(changedPrice.picker.rows[0].source);
    changedPrice.value.price = "5"; calls.at(-1).resolve(choice(source())); await priceChoice;
    assert.equal(changedPrice.value.sets.length, 0, "pending choice never overwrites a manual price");
    const permission = await populated(); const lockedChoice = permission.picker.choose(permission.picker.rows[0].source);
    permission.value.editable = false; calls.at(-1).resolve(choice(source())); await lockedChoice; assert.equal(permission.value.sets.length, 0);
    const closing = await populated(); const closedChoice = closing.picker.choose(closing.picker.rows[0].source);
    closing.picker.dialog.hide(); calls.at(-1).resolve(choice(source())); await closedChoice; assert.equal(closing.value.sets.length, 0);
    for (const changed of [source({ version: "NEW-VERSION" }), source({ piece_rate: "7" }), source({ employee: "E-2" })]) {
        const check = await populated(); await select(check.picker, choice(changed)); assert.equal(check.value.sets.length, 0);
    }
    const incomplete = await populated(); const incompleteQuery = incomplete.picker.query();
    calls.at(-1).resolve(page([source()], { total: 2 })); await incompleteQuery; assert.equal(incomplete.picker.rows.length, 1);
    const count = calls.length; await incomplete.picker.choose(source()); assert.equal(calls.length, count, "only a displayed row can be chosen");
    const future = await populated(state(), [source({ work_date: "2026-10-04" })]); assert.equal(future.picker.rows.length, 0);
    const partial = state(); partial.create = () => new window.JifengWageRatePicker({ getContext: () => partial.context,
        stillCurrent: () => true, onChoose: async chosen => { partial.price = chosen.piece_rate; throw new Error("Setter failed"); } });
    const failed = await populated(partial), alertCount = alerts.length; await select(failed.picker);
    assert.equal(partial.price, source().piece_rate); assert.equal(failed.picker.closed, false); assert.equal(alerts.length, alertCount);
    assert.match(failed.picker.status.textContent, /核对当前工价/);
    const entryActions = registrations.get("Jifeng Wage Entry"), entry = form(); entryActions.refresh(entry);
    assert.equal(entry.buttons[0].name, "调用历史工价");
    for (const modify of [frm => { frm.doc.docstatus = 1; }, frm => { frm.doc.wage_allocation = "ALLOC-1"; },
        frm => { frm.read_only = true; }, frm => { frm.permissions.clear(); }, frm => { frm.fields_dict.piece_rate.df.read_only = 1; }]) {
        const locked = form(); modify(locked); entryActions.refresh(locked);
        assert.ok(!locked.buttons.some(button => button.name === "调用历史工价"));
        const start = calls.length; assert.equal(await window.JifengWageRateTools.open(locked), null); assert.equal(calls.length, start);
    }
    const entryPicker = await nativePicker(entry); await select(entryPicker);
    assert.equal(entry.doc.piece_rate, "0.017"); assert.equal(entry.doc.piece_rate_reference, source().name);
    assert.equal(entry.doc.piece_rate_reference_version, source().version); assert.equal(entryPicker.closed, true);
    assert.equal("save" in entry, false, "native setter does not save the wage");
    const amended = form(); amended.doc.__islocal = 1; amended.doc.amended_from = "WAGE-CANCELLED";
    amended.doc.piece_rate_reference = "COPIED-HISTORY"; amended.doc.piece_rate_reference_version = "COPIED-VERSION";
    entryActions.refresh(amended); assert.ok(amended.buttons.some(button => button.name === "调用历史工价"), "the visible amendment button can explain the first-save requirement");
    const beforeAmendment = calls.length; assert.equal(await window.JifengWageRateTools.open(amended), null);
    assert.equal(calls.length, beforeAmendment); assert.match(messages.at(-1), /先保存修改草稿/);
    assert.equal(amended.doc.piece_rate, 2); assert.equal(amended.doc.piece_rate_reference, "COPIED-HISTORY", "picker does not modify or save an unsaved amendment");
    amended.doc.__islocal = 0; amended.doc.name = "WAGE-AMENDED-DRAFT";
    amended.doc.piece_rate_reference = ""; amended.doc.piece_rate_reference_version = "";
    const amendmentPicker = await nativePicker(amended); await select(amendmentPicker);
    assert.equal(amended.doc.piece_rate_reference, source().name, "a saved amendment can explicitly select history");
    entry.doc.piece_rate = 9; await entryActions.piece_rate(entry);
    assert.equal(entry.doc.piece_rate, 9); assert.equal(entry.doc.piece_rate_reference, ""); assert.equal(entry.doc.piece_rate_reference_version, "");
    for (const field of ["employee", "company", "operation", "stock_uom", "work_date"]) {
        entry.doc.piece_rate_reference = "OLD"; entry.doc.piece_rate_reference_version = "V";
        await entryActions[field](entry); assert.equal(entry.doc.piece_rate_reference, "", `${field} clears history reference`);
    }
    entry.doc.piece_rate_reference = "OLD"; entry.doc.piece_rate_reference_version = "V"; entry.doc.job_card = "";
    await entryActions.job_card(entry); assert.equal(entry.doc.piece_rate_reference, "", "clearing job card clears the quote without changing price");
    const staleSource = form(), sourceRead = window.JifengWageRateTools.open(staleSource); staleSource.doc.employee = "E-2";
    calls.at(-1).resolve(jobSource()); assert.equal(await sourceRead, null, "late native source read cannot cross employee");
    const removed = allocation(), removedOpen = window.JifengWageRateTools.open(removed.frm, removed.row);
    removed.frm.doc.employees = []; calls.at(-1).resolve(jobSource()); assert.equal(await removedOpen, null);
    const split = allocation(), childPicker = await nativePicker(split.frm, split.row); await select(childPicker);
    assert.equal(split.row.piece_rate, "0.017"); assert.equal(split.row.piece_rate_reference, source().name);
    assert.equal(split.row.quantity, 3); assert.equal(split.row.include_time_pay, 1); assert.equal(split.row.wage_hours, 2); assert.equal(split.row.hourly_rate, 4);
    const amendedSplit = allocation(); amendedSplit.frm.doc.__islocal = 1; amendedSplit.frm.doc.amended_from = "ALLOC-CANCELLED";
    const beforeSplitAmendment = calls.length;
    await registrations.get(amendedSplit.row.doctype).call_history_rate(amendedSplit.frm, amendedSplit.row.doctype, amendedSplit.row.name);
    assert.equal(calls.length, beforeSplitAmendment); assert.match(messages.at(-1), /先保存修改草稿/);
    assert.equal(amendedSplit.row.piece_rate, 2); assert.equal(amendedSplit.row.quantity, 3);
    amendedSplit.frm.doc.__islocal = 0; amendedSplit.frm.doc.name = "ALLOC-AMENDED-DRAFT";
    const amendedSplitPicker = await nativePicker(amendedSplit.frm, amendedSplit.row); await select(amendedSplitPicker);
    assert.equal(amendedSplit.row.piece_rate_reference, source().name); assert.equal(amendedSplit.row.quantity, 3);
    split.row.piece_rate = 11; await registrations.get(split.row.doctype).piece_rate(split.frm, split.row.doctype, split.row.name);
    assert.equal(split.row.piece_rate, 11); assert.equal(split.row.piece_rate_reference, "");
    split.row.piece_rate_reference = "OLD"; split.row.piece_rate_reference_version = "V"; split.row.employee = "E-2";
    await registrations.get(split.row.doctype).employee(split.frm, split.row.doctype, split.row.name); assert.equal(split.row.piece_rate_reference, "");
    split.row.piece_rate_reference = "OLD"; split.row.piece_rate_reference_version = "V";
    await registrations.get("Jifeng Wage Allocation").work_date(split.frm); assert.equal(split.row.piece_rate_reference, "");
    const button = allocation(), buttonRead = registrations.get(button.row.doctype).call_history_rate(button.frm, button.row.doctype, button.row.name);
    await tick();
    assert.equal(calls.at(-1).options.args.employee, button.row.employee); calls.at(-1).resolve(jobSource()); await buttonRead;
    assert.equal(calls.at(-1).options.method, "jifeng_erp.wage_rates.query_piece_rates"); calls.at(-1).resolve(page()); await tick();
    for (const modify of [frm => { frm.doc.docstatus = 1; }, frm => { frm.read_only = true; },
        frm => { frm.fields_dict.employees.df.read_only = 1; }, frm => { frm.fields_dict.employees.grid.get_field = () => ({ read_only: 1 }); }]) {
        const locked = allocation(); modify(locked.frm); const start = calls.length;
        assert.equal(await window.JifengWageRateTools.open(locked.frm, locked.row), null); assert.equal(calls.length, start);
    }
    const partialEntry = form({ async set_value(values) { this.sets.push(values); Object.assign(this.doc, values); throw new Error("Native trigger failed"); } });
    const uncertain = await nativePicker(partialEntry), alertsBefore = alerts.length; await select(uncertain);
    assert.equal(uncertain.closed, false); assert.equal(alerts.length, alertsBefore); assert.match(uncertain.status.textContent, /核对/);
    const during = form(); let finishSetter;
    during.set_value = function(values) { Object.assign(this.doc, values); return new Promise(resolve => { finishSetter = resolve; }); };
    const duringPicker = await nativePicker(during), applying = duringPicker.choose(duringPicker.rows[0].source);
    calls.at(-1).resolve(choice(source())); await tick(); during.read_only = true; finishSetter(); await applying;
    assert.equal(duringPicker.closed, false); assert.match(duringPicker.status.textContent, /带入期间/);
    const manualDuring = form(); let finishManual;
    manualDuring.set_value = function(values) {
        Object.assign(this.doc, values);
        if ("piece_rate" in values) return new Promise(resolve => { finishManual = resolve; });
        return Promise.resolve();
    };
    const manualPicker = await nativePicker(manualDuring), manualApply = manualPicker.choose(manualPicker.rows[0].source);
    calls.at(-1).resolve(choice(source())); await tick(); manualDuring.doc.piece_rate = 19; finishManual(); await manualApply;
    assert.equal(manualDuring.doc.piece_rate, 19); assert.equal(manualDuring.doc.piece_rate_reference, ""); assert.equal(manualPicker.closed, false);
    const closeDuring = form(); let finishClosing;
    closeDuring.set_value = function(values) { Object.assign(this.doc, values); return new Promise(resolve => { finishClosing = resolve; }); };
    const closeDuringPicker = await nativePicker(closeDuring), closeApply = closeDuringPicker.choose(closeDuringPicker.rows[0].source);
    const closeAlerts = alerts.length; calls.at(-1).resolve(choice(source())); await tick(); closeDuringPicker.dialog.hide(); finishClosing(); await closeApply;
    assert.equal(alerts.length, closeAlerts, "closing during native setters is never reported as success");
    const changed = form(), changingPicker = await nativePicker(changed), pendingChoice = changingPicker.choose(changingPicker.rows[0].source);
    changed.doc.stock_uom = "克"; calls.at(-1).resolve(choice(source())); await pendingChoice; assert.equal(changed.sets.length, 0);
    assert.ok(calls.every(call => call.options.type === "GET"), "all selection requests are read only GETs");
    console.log("Historical wage rate models passed: explicit 0-price selection, exact scope/source/version, safe text and full pages, closed/late/readonly/manual guards, Entry and Allocation native setters without saves or share/time changes.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
