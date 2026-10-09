// Client protocol only: no browser, HTTP or real native UI is opened.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../jifeng_erp/jifeng_erp/doctype/jifeng_wage_allocation/jifeng_wage_allocation.js"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
    const forms = {}, buttons = {}, dialogs = [], calls = [], messages = [], storage = new Map(), heldLocks = new Set();
    let route = ["Form", "Jifeng Wage Allocation", "ALLOC"], onCall;
    const window = {};
    const frappe = { session: { user: "SYNTHETIC-QA" }, user: { has_role: () => false },
        ui: { form: { on: (doctype, handlers) => { forms[doctype] = handlers; } } },
        utils: { escape_html: text => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;") },
        get_route: () => route, msgprint: value => messages.push(value),
        call: async options => {
            calls.push(options);
            if (options.method.endsWith("get_allocation_source")) return { message: {} };
            return onCall(options);
        } };
    frappe.ui.Dialog = class {
        constructor(options) { this.options = options; this.button = { prop: (_, value) => { this.disabled = value; } }; dialogs.push(this); }
        get_primary_btn() { return this.button; }
        show() { this.visible = true; }
        hide() { this.visible = false; this.on_hide?.(); }
    };
    const navigator = { locks: { request: async (key, options, action) => {
        if (heldLocks.has(key)) return action(null);
        heldLocks.add(key);
        try { return await action({ name: key }); } finally { heldLocks.delete(key); }
    } } };
    const context = vm.createContext({ window, frappe, navigator,
        localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value),
            removeItem: key => storage.delete(key) }, locals: {}, console });
    vm.runInContext(source, context);
    const frm = { doc: { name: "ALLOC", modified: "2026-10-09 08:00:00.123456", docstatus: 1, job_card: "CARD" },
        is_new: () => false, is_dirty: () => false, has_perm: () => true, set_intro: () => {},
        add_custom_button: (label, callback) => { buttons[label] = callback; }, reload_doc: async () => { frm.reloads++; }, reloads: 0 };
    window.cur_frm = frm;
    forms["Jifeng Wage Allocation"].refresh(frm);
    const view = { allocation: "ALLOC", version: frm.doc.modified, snapshot: "a".repeat(64),
        company: "C", currency: "CNY", source_completed_qty: "7", allocated_qty: "7", total_amount: "14.00",
        production_quantities: { for_quantity: "10", total_completed_qty: "7", process_loss_qty: "1", pending_qty: "2", stock_uom: "Nos" },
        can_process: true, blocked_reason: "", rows: [{ allocation_employee: "ROW", employee: "EMP", employee_name: "<b>员工</b>",
            quantity: "7", piece_rate: "2", piece_amount: "14.00", time_amount: "0", extra_amount: "0", total_amount: "14.00",
            wage_entry: "WAGE", wage_status: "草稿待审核", additional_salary: null, additional_salary_status: "尚未生成", cancelled_salaries: [] }] };
    onCall = async () => ({ message: structuredClone(view) });
    return { forms, buttons, dialogs, calls, messages, storage, heldLocks, frm, view,
        call: handler => { onCall = handler; }, route: next => { route = next; },
        open: () => buttons["核对并集中办理工资"](),
        posts: () => calls.filter(call => call.type === "POST"),
        inspections: () => calls.filter(call => call.method.endsWith("inspect_allocation_processing")) };
}

async function main() {
    const normal = fixture();
    const opened = normal.open(); await tick();
    const dialog = normal.dialogs[0];
    assert.equal(normal.posts().length, 0, "reading does not submit wages");
    assert.match(dialog.options.fields[0].options, /转交待派工量：2/);
    assert.match(dialog.options.fields[0].options, /工资合计：14.00/);
    assert.ok(dialog.options.fields[0].options.includes("&lt;b>员工&lt;/b>"), "employee HTML is escaped");
    normal.call(async options => {
        assert.equal(options.type, "POST");
        assert.equal(normal.storage.size, 1, "unknown marker persists before the mutation request");
        return { message: { status: "completed", allocation: "ALLOC", version: normal.view.version,
            submitted_wages: ["WAGE"], created_salaries: ["SALARY"], already_completed: false,
            current: { ...normal.view, rows: [{ ...normal.view.rows[0], wage_status: "已审核", additional_salary: "SALARY" }] } } };
    });
    await dialog.options.primary_action(); await opened;
    assert.equal(normal.posts().length, 1);
    assert.equal(normal.storage.size, 0);
    assert.equal(normal.frm.reloads, 1);

    const blocked = fixture(); blocked.view.can_process = false;
    blocked.view.blocked_reason = "取消历史，请逐单核对";
    blocked.view.rows[0].cancelled_salaries = ["OLD"];
    const blockedOpen = blocked.open(); await tick();
    assert.equal(blocked.dialogs[0].disabled, true);
    assert.match(blocked.dialogs[0].options.fields[0].options, /已取消附加项：OLD/);
    await blocked.dialogs[0].options.primary_action();
    assert.equal(blocked.posts().length, 0);
    blocked.dialogs[0].hide(); await blockedOpen;

    const unknown = fixture(); const unknownOpen = unknown.open(); await tick();
    unknown.call(async () => { throw new Error("response lost"); });
    await unknown.dialogs[0].options.primary_action();
    await unknown.dialogs[0].options.primary_action();
    assert.equal(unknown.posts().length, 1, "old confirmation cannot retry unknown writes");
    assert.equal(unknown.storage.size, 1);
    unknown.dialogs[0].hide(); await unknownOpen;
    unknown.view.rows[0].wage_status = "已审核"; unknown.view.rows[0].additional_salary = "SALARY";
    unknown.call(async () => ({ message: unknown.view }));
    const recheck = unknown.open(); await tick();
    assert.equal(unknown.inspections().length, 2, "reopen always reads actual state before deciding");
    assert.match(unknown.dialogs[1].options.fields[0].options, /未自动重试/);
    assert.equal(unknown.posts().length, 1);
    unknown.dialogs[1].hide(); await recheck;

    const delayed = fixture(); let finish;
    delayed.call(() => new Promise(resolve => { finish = resolve; }));
    const lateOpen = delayed.open(); await tick();
    delayed.route(["List", "Job Card"]);
    finish({ message: delayed.view }); await lateOpen;
    assert.equal(delayed.dialogs.length, 0, "late read cannot revive the wrong form");
    assert.equal(delayed.heldLocks.size, 0);

    const overlap = fixture(); const held = overlap.open(); await tick();
    await overlap.open();
    assert.equal(overlap.inspections().length, 1, "another action cannot read or post inside held processing scope");
    assert.equal(overlap.posts().length, 0); await held;

    const denied = fixture(); denied.frm.has_perm = permission => permission !== "submit";
    delete denied.buttons["核对并集中办理工资"];
    denied.forms["Jifeng Wage Allocation"].refresh(denied.frm);
    assert.equal(denied.buttons["核对并集中办理工资"], undefined, "native Form permission denial hides the processing action");
    console.log("Wage allocation client protocol passed; GUI not exercised.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
