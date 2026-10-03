// Native form integration models; no browser, requests or database.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const calls = [], messages = [], alerts = [], registrations = new Map();
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this._text = ""; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
    set innerHTML(_) { throw new Error("Customer text must not execute HTML"); }
    append(...nodes) { this.children.push(...nodes); } replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = value; } addEventListener(name, action) { this.events.set(name, action); }
    descendants(tag) { return this.children.flatMap((child) => [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Dialog {
    constructor(config) { this.config = config; this.values = {}; this.fields_dict = { choices: { $wrapper: { get: () => new Element("div") } } }; }
    get_value(key) { return this.values[key]; } show() { this.hidden = false; } hide() { this.hidden = true; this.on_hide?.(); }
    get_primary_btn() { return { prop: (key, value) => { this[key] = value; } }; }
}
const window = {}, document = { createElement: (tag) => new Element(tag) };
const frappe = { ui: { Dialog, form: { on: (doctype, actions) => registrations.set(doctype, actions) } },
    call(options) { let resolve, reject; const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
        calls.push({ options, resolve: (message) => resolve({ message }), reject }); return pending; },
    msgprint: (text) => messages.push(text), show_alert: (value) => alerts.push(value), set_route() {} };
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_sales_order.js", "utf8"), { window, document, frappe, __: (value) => value });
const tick = () => new Promise((resolve) => setImmediate(resolve));
const source = (overrides = {}) => ({ name: "PROFILE-A", company: "Company A", kind: "订单客户", profile_version: "2026-10-03T10:00:00",
    customer: "NATIVE-CUSTOMER-001", customer_version: "2026-10-03T09:00:00", legacy_number: "001", legacy_code: " 0007 ",
    display_name: '<img src=x onerror="alert(1)">同名客户', can_select: true, ...overrides });
const page = (rows = [source()], overrides = {}) => ({ scope: "order_customer", company: "Company A", search: "", page: 1,
    page_size: 25, total: rows.length, total_pages: rows.length ? 1 : 0, rows, ...overrides });
function form(overrides = {}) {
    const frm = { doc: { name: "new-sales-order-A", company: "Company A", customer: null, docstatus: 0, __islocal: 1 },
        fields_dict: { customer: { df: { read_only: 0 } } }, permissions: new Set(["create", "write"]), buttons: [], sets: [],
        is_new() { return !!this.doc.__islocal; }, has_perm(type) { return this.permissions.has(type); },
        add_custom_button(name, action, group) { this.buttons.push({ name, action, group }); },
        async set_value(field, value) { this.sets.push({ field, value }); this.doc[field] = value; }, ...overrides };
    return frm;
}
async function populated(frm = form(), rows = [source()]) {
    const picker = new window.JifengOrderCustomerPicker(frm), query = calls.at(-1);
    assert.equal(query.options.type, "GET"); query.resolve(page(rows)); await picker.ready; return { frm, picker };
}
async function main() {
    const refresh = registrations.get("Sales Order").refresh, draft = form(); refresh(draft);
    assert.equal(draft.buttons[0].name, "从订单客户档案选择");
    const locked = form({ read_only: true }); refresh(locked); assert.equal(locked.buttons.length, 0);
    const submitted = form(); submitted.doc.docstatus = 1; refresh(submitted); assert.deepEqual(submitted.buttons.map((button) => button.name), ["生成集沣生产流程单"]);
    const noPartyWrite = form(); noPartyWrite.fields_dict.customer.df.read_only = 1; refresh(noPartyWrite); assert.equal(noPartyWrite.buttons.length, 0);
    const { frm, picker } = await populated();
    assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1).options.args.context)), { company: "Company A", name: null, version: null });
    assert.equal(picker.body.descendants("td")[2].textContent, source().display_name);
    assert.equal(picker.body.descendants("td")[1].textContent, " 0007 ");
    const selecting = picker.choose(picker.rows[0].source), read = calls.at(-1);
    assert.equal(read.options.method, "jifeng_erp.order_customer_selection.choose_customer"); assert.equal(read.options.type, "GET");
    assert.equal(read.options.args.customer, "NATIVE-CUSTOMER-001"); assert.equal(picker.rows[0].button.disabled, true);
    read.resolve(source({ scope: "order_customer" })); await selecting;
    assert.deepEqual(frm.sets, [{ field: "customer", value: "NATIVE-CUSTOMER-001" }]); assert.equal(picker.closed, true);
    assert.match(alerts.at(-1).message, /保存订单/); assert.equal("save" in frm, false, "picker never saves native documents");
    const saved = form(); saved.doc = { name: "SO-A", company: "Company A", customer: "OLD", modified: "2026-10-03T08:00:00", docstatus: 0 };
    const current = await populated(saved); assert.equal(calls.at(-1).options.args.context.name, "SO-A");
    assert.equal(calls.at(-1).options.args.context.version, saved.doc.modified);
    const bad = current.picker.choose(current.picker.rows[0].source); calls.at(-1).resolve(source({ scope: "order_customer", customer: "OTHER-ID" })); await bad;
    assert.equal(saved.sets.length, 0, "same display name cannot substitute native customer ID");
    const stale = current.picker.choose(current.picker.rows[0].source); calls.at(-1).resolve(source({ scope: "order_customer", profile_version: "2026-10-03T11:00:00" })); await stale;
    assert.equal(saved.sets.length, 0, "changed archive version is not adopted");
    const changing = current.picker.choose(current.picker.rows[0].source); saved.doc.customer = "USER-CHANGED";
    calls.at(-1).resolve(source({ scope: "order_customer" })); await changing; assert.equal(saved.sets.length, 0, "late selection never replaces a user change");
    const pendingForm = form(), pending = new window.JifengOrderCustomerPicker(pendingForm); pendingForm.doc.company = "Company B";
    calls.at(-1).resolve(page()); await pending.ready; assert.equal(pending.rows.length, 0, "late query from previous company is not shown");
    const closing = await populated(), closedRead = closing.picker.choose(closing.picker.rows[0].source); closing.picker.dialog.hide();
    calls.at(-1).resolve(source({ scope: "order_customer" })); await closedRead; assert.equal(closing.frm.sets.length, 0, "closed picker ignores pending reads");
    const malformed = await populated(); const querying = malformed.picker.query(); calls.at(-1).resolve(page([source()], { total: 2 })); await querying;
    assert.equal(malformed.picker.rows.length, 1, "incomplete page preserves previous choices");
    const count = calls.length; await malformed.picker.choose(source()); assert.equal(calls.length, count, "only an actual displayed row can be selected");
    const denied = await populated(); const failedRead = denied.picker.choose(denied.picker.rows[0].source); calls.at(-1).reject(new Error("Permission changed")); await failedRead;
    assert.equal(denied.frm.sets.length, 0); assert.equal(denied.picker.busy, false);
    for (const lock of [
        (frm) => { frm.read_only = true; },
        (frm) => { frm.fields_dict.customer.df.read_only = 1; },
        (frm) => { frm.permissions.clear(); },
    ]) {
        let finishSetter; const deferred = await populated(form({
            set_value(field, value) {
                this.sets.push({ field, value }); this.doc[field] = value;
                return new Promise((resolve) => { finishSetter = resolve; });
            },
        }));
        const alertCount = alerts.length, applying = deferred.picker.choose(deferred.picker.rows[0].source);
        calls.at(-1).resolve(source({ scope: "order_customer" })); await tick();
        assert.equal(deferred.frm.sets.length, 1); lock(deferred.frm); finishSetter(); await applying;
        assert.equal(deferred.picker.closed, false); assert.equal(alerts.length, alertCount, "native permission change while party triggers run is not reported as success");
        assert.equal(deferred.frm.doc.customer, source().customer, "local native party change is reviewed, not silently undone");
        assert.match(deferred.picker.status.textContent, /核对原生订单/);
    }
    const partial = await populated(form({
        async set_value(field, value) { this.sets.push({ field, value }); this.doc[field] = value; throw new Error("Native party trigger failed"); },
    }));
    const alertCount = alerts.length, partialApply = partial.picker.choose(partial.picker.rows[0].source);
    calls.at(-1).resolve(source({ scope: "order_customer" })); await partialApply;
    assert.equal(partial.frm.sets.length, 1); assert.equal(partial.frm.doc.customer, source().customer);
    assert.equal(partial.picker.closed, false); assert.equal(alerts.length, alertCount);
    assert.match(partial.picker.status.textContent, /关闭选择窗口并核对原生订单/);
    console.log("Order customer choice models passed: native permissions, exact IDs/two versions, GET-only reads, safe text, full pages, native set_value without save, company/change/close guards.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
