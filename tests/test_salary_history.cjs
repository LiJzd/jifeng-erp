// Real component and bridge in a DOM/protocol model, not GUI or HTTP evidence.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this._text = ""; this.value = ""; this.checked = false; this.classList = { add() {} }; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    set innerHTML(_) { throw new Error("Salary history must render source text without HTML"); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, action) { this.events.set(name, action); }
    descendants(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Dialog {
    constructor(config) { this.config = config; this.fields_dict = {}; this.actions = []; for (const field of config.fields) { const host = new Element("div"); this.fields_dict[field.fieldname] = { $wrapper: { get: () => host } }; } }
    show() { this.hidden = false; }
    hide() { this.hidden = true; this.on_hide?.(); }
    set_primary_action(_, action) { this.primary = action; }
    add_custom_action(_, action) { this.actions.push(action); }
}
const calls = [], messages = [], nativePages = [], formHandlers = new Map(), window = {};
const frappe = { ui: { Dialog, form: { on: (doctype, handlers) => formHandlers.set(doctype, handlers), make_control({ df }) {
    const control = { value: "", typed: "", events: new Map(), df, get_value() { return this.value; },
        async set_value(value) { this.value = value; this.typed = value; df.onchange(); } };
    control.$input = { val: () => control.typed, on: (name, action) => control.events.set(name, action), off: () => control.events.clear() }; return control;
} } }, defaults: { get_user_default: () => "C-1" },
datetime: { month_start: () => "2026-10-01", get_today: () => "2026-10-03" }, msgprint: value => messages.push(value),
call(options) { let resolve, reject; const pending = new Promise((yes, no) => { resolve = yes; reject = no; }); calls.push({ options, resolve: message => resolve({ message }), reject }); return pending; } };
const context = vm.createContext({ window, document: { createElement: tag => new Element(tag) }, frappe, __: value => value });
vm.runInContext(fs.readFileSync("jifeng_erp/public/js/jifeng_salary_history.js", "utf8"), context);
vm.runInContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), context);
vm.runInContext(fs.readFileSync("jifeng_erp/jifeng_erp/doctype/jifeng_salary_history_rule/jifeng_salary_history_rule.js", "utf8"), context);
const plain = value => JSON.parse(JSON.stringify(value));
const earningKeys = ["basic", "time", "piece", "housing", "full_attendance", "meal", "transport", "night_snack", "overtime", "introduction", "assessment", "other_earning"];
const deductionKeys = ["utilities", "social_insurance", "fine", "loan", "retained_pay", "accommodation", "other_deduction"];
const row = (extra = {}) => ({ name: "SS-1", version: "2026-10-03T08:00:00", company: "C-1", employee: "E-1", employee_name: '<img src=x onerror="bad()">', department: "D-1", currency: "CNY",
    start_date: "2026-10-01", end_date: "2026-10-03", posting_date: "2026-10-03", gross_pay: "9007199254740993.017", total_deduction: "0", net_pay: "9007199254740993.017", rounded_total: "9007199254740993",
    total_working_days: "3", payment_days: "3", description: null, type: null, actual_attendance: null, core_total: null, actual_paid: null, basic_default_amount: null,
    buckets: Object.fromEntries([...earningKeys, ...deductionKeys].map(key => [key, null])), classification_state: { earnings: "unclassified", deductions: "unclassified" }, unresolved: ["分类未完整核实"], ...extra });
const page = (view, rows = [row()], extra = {}) => ({ readonly: true, scope: "salary_history", context: plain(view.context()), page: 1, page_size: 15, total: rows.length, total_pages: rows.length ? 1 : 0, rows, ...extra });
const child = (extra = {}) => ({ name: "SD-1", salary_component: "SC-1", amount: "1.017", default_amount: "3", sources: [], classification: null, ...extra });
const detail = (view, value, extra = {}) => ({ readonly: true, scope: "salary_history", context: plain(view.context()), row: value, earnings: [child()], deductions: [], sources: [], ...extra });
async function make() {
    const host = new Element("div"), bridge = { open: spec => nativePages.push(plain(spec)) };
    const entry = window.JifengClassicBridge.prototype.mount.call(bridge, host, { salaryHistory: true });
    await entry.ready; return { view: entry.workbench, entry, host };
}
async function populate(view, rows = [row()], extra = {}) {
    const pending = view.query(1); assert.equal(calls.at(-1).options.type, "GET");
    calls.at(-1).resolve(page(view, rows, extra)); await pending;
}
async function main() {
    const ruleFields = Object.fromEntries(["parentfield", "bucket", "classification_mode", "extra_bucket"].map(name => [name, {}]));
    frappe.meta = { docfield_map: { "Jifeng Salary History Rule": ruleFields } }; frappe.utils = { escape_html: value => String(value).replaceAll("<", "&lt;") };
    const storedRule = { docstatus: 0, parentfield: "earnings", bucket: "other_earning", classification_mode: "wage_breakdown", extra_bucket: "housing" };
    const rule = { doctype: "Jifeng Salary History Rule", doc: storedRule, is_new: () => false, perm: [{ read: 1 }], options: {}, buttons: [],
        set_df_property(field, _, options) { this.options[field] = options; }, add_custom_button(label, action) { this.buttons.push({ label, action }); } };
    const beforeRule = JSON.stringify(storedRule); formHandlers.get(rule.doctype).refresh(rule);
    assert.equal(rule.buttons.length, 0, "read-only HR users never get the historical publish action");
    assert.equal(rule.options.bucket.find(option => option.value === "piece").label, "计件");
    assert.equal(rule.options.classification_mode.find(option => option.value === "wage_breakdown").label, "按计件计时来源");
    assert.equal(ruleFields.parentfield.formatter("earnings"), "收入"); assert.equal(ruleFields.bucket.formatter("time"), "计时");
    assert.equal(JSON.stringify(storedRule), beforeRule, "Chinese labels do not rewrite native stored identifiers or submit a rule");
    rule.perm = [{ read: 1, submit: 1 }]; formHandlers.get(rule.doctype).refresh(rule); assert.equal(rule.buttons.length, 1);
    storedRule.docstatus = 1; rule.buttons = []; formHandlers.get(rule.doctype).refresh(rule); assert.equal(rule.buttons.length, 0);
    const before = calls.length, model = await make(), view = model.view;
    assert.equal(calls.length, before, "mount, default company and native tab ready do not query payroll");
    assert.equal(view.from.value, "2026-10-01"); assert.equal(view.to.value, "2026-10-03"); assert.equal(view.useDate.checked, true);
    assert.deepEqual(plain(view.visibleColumns.map(column => column.label)), ["说明", "姓名", "部门", "底薪", "应出勤", "实出勤", "出勤工资", "计时", "计件", "合计", "房补", "全勤", "应发", "水电", "社保", "罚款", "借款", "实发", "开始", "结束", "类型"]);
    await populate(view);
    const cells = view.tbody.descendants("td"); assert.equal(cells[1].textContent, row().employee_name);
    assert.equal(cells[12].textContent, "9007199254740993.017", "money strings do not lose precision through Number");
    for (const index of [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 20]) assert.equal(cells[index].textContent, "—");
    assert.equal(view.isDirty(), false); assert.equal(view.getCloseBlock(), "");
    const own = view.rows[0], pendingDetail = view.openDetail(own); calls.at(-1).resolve(detail(view, own, { earnings: [child({ default_amount: null })] })); await pendingDetail;
    const dialog = view.detailDialog, text = dialog.fields_dict.detail.$wrapper.get(0).textContent;
    assert.match(text, /净应发（未证明付款）/); assert.match(text, /计薪天数/); assert.equal(text.includes("bank_account_no"), false);
    assert.equal(dialog.fields_dict.detail.$wrapper.get(0).descendants("td")[2].textContent, "—", "unknown saved default amount must not be rejected or fabricated as zero");
    dialog.actions[0](); assert.equal(view.detailDialog, null); assert.deepEqual(nativePages.at(-1).route, ["Form", "Salary Slip", own.name]);
    assert.equal(view.rows[0], own, "opening another mounted native tab preserves queried source and filters");
    const callsBeforeRules = calls.length; view.openRules(); assert.equal(calls.length, callsBeforeRules);
    assert.deepEqual(nativePages.at(-1).filters, { company: "C-1" });
    assert.equal(nativePages.at(-1).doctype, "Jifeng Salary History Rule", "settings open a native page, never publish or save automatically");
    view.configureColumns(); const columns = view.columnDialog, checkboxes = columns.fields_dict.columns.$wrapper.get(0).descendants("input");
    checkboxes.forEach(input => { input.checked = false; }); columns.primary(); assert.equal(view.visibleColumns.length, 21, "empty configuration cannot remove all columns");
    checkboxes[1].checked = true; checkboxes[24].checked = true; columns.primary(); assert.deepEqual(plain(view.visibleColumns.map(column => column.label)), ["姓名", "加班"]);
    assert.equal(calls.length, callsBeforeRules, "column changes are local view state");
    for (const control of ["from", "to", "search", "dateBasis", "useDate"]) {
        const fresh = (await make()).view, pending = fresh.query(), original = page(fresh);
        if (control === "useDate") fresh.useDate.checked = false; else fresh[control].value = control === "dateBasis" ? "posting_date" : control === "search" ? "变更" : "2026-10-02";
        fresh[control].events.get("change")(); calls.at(-1).resolve(original); await pending;
        assert.equal(fresh.rows.length, 0, `late query cannot cross ${control}`); assert.equal(fresh.loading, false);
    }
    const changedCompany = (await make()).view, companyPending = changedCompany.query(), companyResponse = page(changedCompany);
    changedCompany.companyControl.typed = "C-2"; [...changedCompany.companyControl.events.values()][0]();
    calls.at(-1).resolve(companyResponse); await companyPending; assert.equal(changedCompany.rows.length, 0);
    const staleCalls = calls.length; await changedCompany.query(); assert.equal(calls.length, staleCalls, "unsynced typed Company must not reuse the old link value");
    const newQuery = (await make()).view, first = newQuery.query(), firstCall = calls.at(-1), firstPage = page(newQuery);
    newQuery.invalidate(); const second = newQuery.query(), secondCall = calls.at(-1);
    secondCall.resolve(page(newQuery, [row({ name: "SS-2" })])); await second; firstCall.resolve(firstPage); await first;
    assert.equal(newQuery.rows[0].name, "SS-2", "out-of-order response cannot replace a newer query");
    for (const changed of [row({ company: "C-2" }), row({ actual_paid: "0" }), row({ gross_pay: 1 }), row({ buckets: {} }), row({ classification_state: { earnings: "verified", deductions: "unclassified" } })]) {
        const fresh = (await make()).view; await populate(fresh, [changed]); assert.equal(fresh.rows.length, 0, "incomplete or misleading source receipt is rejected");
    }
    const verified = (await make()).view, verifiedRow = row({ classification_state: { earnings: "verified", deductions: "verified" }, buckets: Object.fromEntries([...earningKeys, ...deductionKeys].map(key => [key, "0"])) });
    await populate(verified, [verifiedRow]); assert.equal(verified.tbody.descendants("td")[7].textContent, "0", "verified zero and unknown are different");
    const detailChange = (await make()).view; await populate(detailChange); const oldRow = detailChange.rows[0], delayed = detailChange.openDetail(oldRow), delayedCall = calls.at(-1), oldDetail = detail(detailChange, oldRow);
    detailChange.search.value = "变更"; detailChange.search.events.get("input")(); delayedCall.resolve(oldDetail); await delayed; assert.equal(detailChange.detailDialog, undefined);
    const badDetailView = (await make()).view; await populate(badDetailView); const bad = badDetailView.openDetail(badDetailView.rows[0]);
    calls.at(-1).resolve(detail(badDetailView, row({ version: "2026-10-03T09:00:00" }))); await bad; assert.equal(badDetailView.detailDialog, undefined);
    const hidden = (await make()).view, hiddenQuery = hidden.query(), hiddenResponse = page(hidden); hidden.destroy(); calls.at(-1).resolve(hiddenResponse); await hiddenQuery;
    assert.equal(hidden.rows.length, 0, "disposed tabs cannot receive payroll data");
    const rejected = (await make()).view; await populate(rejected); const retry = rejected.query(); calls.at(-1).reject(new Error("Denied")); await retry;
    assert.equal(rejected.rows.length, 0, "permission failures clear old displayed payroll");
    assert.ok(calls.every(call => call.options.type === "GET" && ["jifeng_erp.salary_history.query_salary_history", "jifeng_erp.salary_history.get_salary_history_detail"].includes(call.options.method)), "component never calculates, saves, exports or pays");
    model.entry.stop(); assert.equal(view.disposed, true);
    console.log("Salary history actual component/bridge: old 21-column defaults, source precision, unknown vs zero, read-only settings/details, source changes, late responses, company input and disposal gates passed (DOM/protocol model only).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
