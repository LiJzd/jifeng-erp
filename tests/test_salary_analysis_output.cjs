// Actual shared component in a DOM/HTTP protocol model; no GUI claim.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const calls = [], requests = [], downloads = [], prints = [], messages = [], timers = new Map();
let timerId = 0;
class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this.style = {};
        this._text = ""; this.value = ""; this.classList = { add() {} };
        if (tag === "iframe") this.contentWindow = { focus() {}, print: () => prints.push(this.srcdoc),
            addEventListener: (name, action) => this.events.set(name, action) };
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
    append(...nodes) { this.children.push(...nodes); for (const node of nodes) if (node.tag === "iframe") Promise.resolve().then(() => node.onload?.()); }
    replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, action) { this.events.set(name, action); }
    click() { if (this.tag === "a") downloads.push({ filename: this.download, href: this.href }); }
    remove() { this.removed = true; }
}
const frappe = { defaults: { get_user_default: () => "C-1" },
    datetime: { month_start: () => "2026-10-01", get_today: () => "2026-10-09" },
    msgprint: text => messages.push(text), ui: { form: { make_control() {
        const control = { value: "", typed: "", get_value() { return this.value; },
            async set_value(value) { this.value = this.typed = value; } };
        control.$input = { val: () => control.typed, on() {}, off() {}, trigger() {} }; return control;
    } } }, call(options) {
        let resolve; const promise = new Promise(done => { resolve = done; }); calls.push({ options, resolve: message => resolve({ message }) }); return promise;
    } };
const window = {}, context = vm.createContext({ window, frappe, document: { createElement: tag => new Element(tag), body: new Element("body") },
    __: value => value, AbortController, URLSearchParams,
    setTimeout: action => { timers.set(++timerId, action); return timerId; }, clearTimeout: id => timers.delete(id),
    URL: { createObjectURL: () => "blob:analysis", revokeObjectURL() {} },
    fetch(url, options) { let resolve; const promise = new Promise(done => { resolve = done; }); requests.push({ url, options, resolve }); return promise; } });
vm.runInContext(fs.readFileSync("jifeng_erp/public/js/jifeng_salary_analysis.js", "utf8"), context);
const plain = value => JSON.parse(JSON.stringify(value));
const money = "9007199254740993.017";
function response(view, changes = {}) {
    return { readonly: true, scope: "salary_analysis", context: plain(view.context()), currency: "CNY", salary_slips: 1,
        result_version: "a".repeat(64), settlements: { gross_pay: money, total_deduction: "0", net_pay: money, rounded_total: "9007199254740993" },
        department: [{ name: "装配", amount: money, share_percent: "100.00" }],
        group: [{ name: "未归类", amount: money, share_percent: "100.00" }], operation: [{ name: "未归类", amount: money, share_percent: "100.00" }],
        slips: [{ name: "SLIP", version: "2026-10-09T10:00:00", employee: "E-1", employee_name: "员工一", department: "装配", currency: "CNY",
            start_date: "2026-10-01", end_date: "2026-10-31", gross_pay: money, total_deduction: "0", net_pay: money, rounded_total: "9007199254740993" }],
        sources: [], notes: ["净应发不代表付款。"], ...changes };
}
async function make() { const view = new window.JifengSalaryAnalysis(new Element("div")); await Promise.resolve(); view.basis.value = "period_end"; return view; }
async function populate(view, changes) {
    const pending = view.query(); calls.at(-1).resolve(response(view, changes)); await pending;
}
function file(type, extra = {}) { return { ok: true, headers: { get: () => type }, blob: async () => ({ size: 50 }), text: async () => "<!doctype html><html><body>工资结算</body></html>", ...extra }; }
async function main() {
    const view = await make();
    assert.equal(view.exportButton.disabled, true); assert.equal(view.printButton.disabled, true);
    await view.output("export"); assert.equal(requests.length, 0, "query must precede business output");
    await populate(view);
    assert.equal(view.exportButton.disabled, false); assert.match(view.totals.textContent, /9007199254740993\.017/);
    const exported = view.output("export"), request = requests.at(-1), url = new URL(request.url, "https://test.invalid");
    assert.equal(request.options.method, "GET"); assert.equal(request.options.credentials, "same-origin");
    assert.equal(url.pathname, "/api/method/jifeng_erp.salary_analysis_output.export_salary_analysis");
    assert.deepEqual(JSON.parse(url.searchParams.get("context")), plain(view.context()));
    assert.equal(url.searchParams.get("result_version"), "a".repeat(64));
    request.resolve(file("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")); await exported;
    assert.equal(downloads.at(-1).filename, "工资历史分析.xlsx");
    assert.equal(view.result.salary_slips, 1, "successful output keeps the queried business screen");

    const print = view.output("print"), printRequest = requests.at(-1);
    assert.match(printRequest.url, /print_salary_analysis/); printRequest.resolve(file("text/html")); await print;
    assert.equal(prints.length, 1); assert.equal(view.printFrame.attributes.sandbox, "allow-same-origin allow-modals");
    const frame = view.printFrame; frame.events.get("afterprint")(); assert.equal(frame.removed, true); assert.equal(view.printFrame, null);

    const before = downloads.length, stale = view.output("export"), staleRequest = requests.at(-1);
    view.search.value = "变更"; view.invalidate();
    assert.equal(staleRequest.options.signal.aborted, true); staleRequest.resolve(file("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"));
    await stale; assert.equal(downloads.length, before); assert.equal(view.exportButton.disabled, true);

    await populate(view); const late = view.output("export"), lateRequest = requests.at(-1);
    let resolveBlob; lateRequest.resolve(file("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", { blob: () => new Promise(done => { resolveBlob = done; }) }));
    await new Promise(done => setImmediate(done)); view.invalidate(); resolveBlob({ size: 20 }); await late;
    assert.equal(downloads.length, before, "late bytes cannot cross a filter change");

    await populate(view); const denied = view.output("export"); requests.at(-1).resolve(file("application/json", { ok: false })); await denied;
    assert.equal(downloads.length, before); assert.equal(view.result, null); assert.equal(view.printButton.disabled, true);
    assert.match(view.status.textContent, /重新查询/);
    await populate(view, { result_version: "invalid" }); assert.equal(view.result, null, "invalid snapshot does not enable output");
    await populate(view, { requires_currency: true, currencies: ["CNY", "USD"], currency: undefined });
    assert.equal(view.result, null); assert.equal(view.exportButton.disabled, true, "mixed currencies must be selected before output");

    await populate(view); const closing = view.output("print"), closingRequest = requests.at(-1);
    view.destroy(); closingRequest.resolve(file("text/html")); await closing;
    assert.equal(prints.length, 1); assert.equal(closingRequest.options.signal.aborted, true, "closing a tab cancels payroll output");
    console.log("Salary analysis query → exact-context export/print, permission errors and late-response checks passed (DOM/protocol model only).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
