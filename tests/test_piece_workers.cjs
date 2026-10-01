// Pure client protocol only: no browser, HTTP or database writes.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const calls = [], routes = [], messages = [];
const window = {};
const frappe = { pages: { "jifeng-piece-workers": {} }, msgprint: text => messages.push(text),
    call: async options => { calls.push(options); return { message: { ...options.args, eligible: true } }; },
    set_route: async route => { routes.push(route); } };
const context = vm.createContext({ window, frappe });
vm.runInContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/jifeng_erp/page/jifeng_piece_workers/jifeng_piece_workers.js"), "utf8") +
    "\nglobalThis.Workers = JifengPieceWorkers;", context);

function page() {
    const object = Object.create(context.Workers.prototype);
    let company = "C-1";
    Object.assign(object, { root: { isConnected: true }, queryNumber: 1,
        controls: { company: { get_value: () => company, set_value: value => { company = value; } } } });
    return object;
}

async function main() {
    let checks = 0;
    const check = (value, description) => { assert.ok(value, description); checks += 1; };
    const standalone = page();
    await standalone.select("C-1", "E-1", {});
    check(routes.join(",") === "jifeng-wage-workbench", "standalone uses the dedicated wage page");
    check(frappe.route_options.piece_worker_company === "C-1" && frappe.route_options.piece_worker_employee === "E-1",
        "standalone transfers the verified employee and company");
    check(calls[0].method === "jifeng_erp.piece_workers.select_piece_worker", "selection rechecks native server eligibility");

    let bridgeCalls = 0;
    window.jifeng_classic_select_piece_worker = async () => { bridgeCalls += 1; return false; };
    routes.length = 0;
    await page().select("C-1", "E-1", {});
    check(bridgeCalls === 1 && routes.length === 0, "classic bridge refusal never falls back to a second workbench");
    window.jifeng_classic_select_piece_worker = async () => { throw new Error("synthetic unknown/dirty block"); };
    await page().select("C-1", "E-1", {});
    check(routes.length === 0, "classic bridge failure never opens another wage iframe");
    delete window.jifeng_classic_select_piece_worker;

    const changed = page();
    let finish;
    frappe.call = () => new Promise(resolve => { finish = resolve; });
    const selection = changed.select("C-1", "E-1", {});
    let parallelCalls = 0;
    const second = changed.select("C-1", "E-2", { set disabled(value) { parallelCalls += 1; } });
    await second;
    check(parallelCalls === 0, "one pending selection prevents parallel buttons");
    changed.controls.company.set_value("C-2");
    finish({ message: { company: "C-1", employee: "E-1", eligible: true } });
    await selection;
    check(routes.length === 0 && !changed.selecting, "changed company discards delayed server selection");

    const closed = page();
    const closing = closed.select("C-1", "E-1", {});
    closed.root.isConnected = false;
    finish({ message: { company: "C-1", employee: "E-1", eligible: true } });
    await closing;
    check(routes.length === 0, "closed page cannot navigate after delayed selection RPC");

    window.jifeng_wage_workbench = { is_dirty: () => true };
    const beforeCalls = calls.length;
    await page().select("C-1", "E-1", {});
    check(calls.length === beforeCalls && routes.length === 0, "standalone refuses dirty wage context before qualification request");
    console.log(JSON.stringify({ ok: true, checks, browser_verified: false }));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
