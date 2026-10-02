// Protocol models for the explicit plan commitment. No browser or database.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("jifeng_erp/jifeng_erp/doctype/jifeng_flow_order/jifeng_flow_order.js", "utf8");
const version = "2026-10-03 10:20:30.123456";

function model() {
    const calls = [], dialogs = [], messages = [], buttons = new Map();
    let refresh;
    class Dialog {
        constructor(config) { this.config = config; dialogs.push(this); }
        show() { this.shown = true; }
        hide() { this.hidden = true; }
        get_primary_btn() { return { prop: (_key, value) => { this.disabled = value; } }; }
    }
    const frappe = {
        ui: { form: { on: (_name, events) => { refresh = events.refresh; } }, Dialog },
        msgprint: (value) => messages.push(value),
    };
    vm.runInNewContext(source, { frappe });
    const frm = {
        doc: { name: "FLOW", modified: version, quantity_basis: "pending", stock_uom: "", planned_qty: 1 },
        dirty: false, reloads: 0,
        is_new: () => false, is_dirty() { return this.dirty; },
        set_intro() {}, add_custom_button: (label, handler) => buttons.set(label, handler),
        call(method, args) {
            return new Promise((resolve, reject) => calls.push({ method, args, resolve, reject }));
        },
        async reload_doc() { this.reloads++; if (this.reloadError) throw this.reloadError; },
    };
    refresh(frm);
    return { frm, calls, dialogs, messages, buttons, refresh };
}
const context = (extra = {}) => ({ name: "FLOW", version, stock_uom: "Nos", current_planned_qty: 1,
    quantity_basis: "pending", ...extra });
async function open(m, data = context()) {
    const pending = m.buttons.get("确认计划库存单位")();
    m.calls.at(-1).resolve({ message: data }); await pending;
    return m.dialogs.at(-1);
}

async function main() {
    {
        const m = model(); m.frm.dirty = true;
        await m.buttons.get("确认计划库存单位")();
        assert.equal(m.calls.length, 0, "an unrelated dirty form is never auto-saved");
        assert.equal(m.dialogs.length, 0);
    }
    for (const bad of [context({ name: "OTHER" }), context({ version: version.slice(0, -1) }), context({ stock_uom: "" }), null]) {
        const m = model(); await open(m, bad);
        assert.equal(m.dialogs.length, 0, "invalid or stale context cannot authorize a write");
    }
    {
        const m = model(); const pending = m.buttons.get("确认计划库存单位")();
        m.frm.dirty = true; m.calls[0].resolve({ message: context() }); await pending;
        assert.equal(m.dialogs.length, 0, "edits during a context read prevent opening");
    }
    {
        const m = model(), dialog = await open(m);
        assert.equal(dialog.config.fields.find((field) => field.fieldname === "stock_uom").read_only, 1);
        assert.equal("default" in dialog.config.fields.find((field) => field.fieldname === "planned_qty"), false,
            "unknown source quantity is never silently reused as stock quantity");
        const pending = dialog.config.primary_action({ planned_qty: 12, note: "1箱=12件" });
        const write = m.calls.at(-1);
        assert.equal(write.method, "confirm_stock_plan");
        assert.equal(write.args.expected_version, version);
        assert.equal(write.args.stock_uom, "Nos");
        assert.equal(write.args.planned_qty, 12);
        await dialog.config.primary_action({ planned_qty: 144, note: "重复点击" });
        assert.equal(m.calls.length, 2, "two clicks produce only one commitment request");
        write.resolve({ message: { name: "FLOW", version: "new", planned_qty: 12, stock_uom: "Nos" } }); await pending;
        assert.equal(dialog.hidden, true); assert.equal(m.frm.reloads, 1);
    }
    for (const change of [(m) => { m.frm.dirty = true; }, (m) => { m.frm.doc.name = "OTHER"; },
        (m) => { m.frm.doc.modified = "2026-10-03 10:20:31"; }]) {
        const m = model(), dialog = await open(m); change(m);
        await dialog.config.primary_action({ planned_qty: 12, note: "核对" });
        assert.equal(m.calls.length, 1, "changed scope prevents committing the old dialog");
        assert.equal(dialog.hidden, true);
    }
    for (const reloadFails of [false, true]) {
        const m = model(), dialog = await open(m);
        if (reloadFails) m.frm.reloadError = new Error("offline");
        const pending = dialog.config.primary_action({ planned_qty: 12, note: "核对" });
        m.calls.at(-1).reject(new Error("response lost after server commit")); await pending;
        assert.equal(m.calls.length, 2, "unknown write result never causes an automatic retry");
        assert.equal(dialog.hidden, true); assert.equal(dialog.disabled, true);
        assert.match(m.messages.at(-1), /核对/);
        if (reloadFails) assert.match(m.messages.at(-1), /请勿重复提交/);
    }
    {
        const m = model(); m.frm.doc.quantity_basis = "stock_v1"; m.frm.doc.stock_uom = "Nos";
        m.buttons.clear(); m.refresh(m.frm);
        assert.equal(m.buttons.has("确认计划库存单位"), false);
    }
    console.log("Flow plan confirmation protocol: 13 scenarios passed");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
