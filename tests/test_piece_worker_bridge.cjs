// The two classic pages are separate cached frames; selection must reuse wage drafts.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const window = {}, messages = [];
vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), {
    window, frappe: { msgprint: (message) => messages.push(message) }, __: (text) => text,
});

async function main() {
    const bridge = Object.create(window.JifengClassicBridge.prototype);
    const key = "formal:工资:工资录入";
    const selected = [], routes = [], opened = [];
    let dirty = false, frozen = "", allowed = true;
    const child = { frappe: { set_route: async (...route) => routes.push(route) },
        jifeng_wage_workbench: { is_dirty: () => dirty, get_close_block: () => frozen,
            choosePieceWorker: async (...person) => { selected.push(person); return allowed; } } };
    const entry = { iframe: { contentWindow: child }, ready: Promise.resolve(true) };
    bridge.frames = new Map([[key, entry]]);
    bridge.open = (spec) => { opened.push(spec); bridge.frames.set(key, entry); };
    dirty = true;
    assert.equal(await bridge.selectPieceWorker("C", "E"), false);
    assert.equal(opened.length, 0, "a different workers frame cannot bypass a cached wage draft");
    dirty = false; frozen = "unknown save";
    assert.equal(await bridge.selectPieceWorker("C", "E"), false);
    assert.equal(selected.length, 0);
    frozen = "";
    assert.equal(await bridge.selectPieceWorker("C", "E"), true);
    assert.deepEqual(selected, [["C", "E"]]);
    assert.equal(opened[0].title, "工资录入");
    assert.equal(routes[0][0], "jifeng-wage-workbench");
    allowed = false;
    assert.equal(await bridge.selectPieceWorker("C", "DENIED"), false);
    assert.equal(bridge.selectingPieceWorker, false);

    bridge.frames.clear();
    let load;
    entry.ready = new Promise((resolve) => { load = resolve; });
    const pending = bridge.selectPieceWorker("C", "NEW");
    assert.equal(await bridge.selectPieceWorker("C", "SECOND"), false, "only one selection runs during frame loading");
    dirty = true;
    load(true);
    assert.equal(await pending, false, "a draft created during loading must also be protected");
    dirty = false; entry.ready = Promise.resolve(false);
    assert.equal(await bridge.selectPieceWorker("C", "FAILED"), false);
    assert.equal(bridge.selectingPieceWorker, false);
    entry.ready = new Promise((resolve) => { load = resolve; });
    const closing = bridge.selectPieceWorker("C", "CLOSED");
    bridge.frames.delete(key);
    load(true);
    assert.equal(await closing, false, "closing a loading tab cancels the cross-frame selection");
    console.log("Piece worker classic bridge checks passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
