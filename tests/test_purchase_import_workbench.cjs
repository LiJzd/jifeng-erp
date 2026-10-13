const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("jifeng_erp/public/js/jifeng_purchase_import.js", "utf8");
class Element {
    constructor() { this.children = []; this.style = {}; this.events = {}; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute() {}
    addEventListener(name, action) { this.events[name] = action; }
    set textContent(value) { this.text = String(value); }
    set innerHTML(_) { throw Error("Spreadsheet strings cannot execute as HTML"); }
}
function environment() {
    const calls = [], alerts = [], handlers = {};
    class Dialog {
        constructor(options) {
            this.fields_dict = Object.fromEntries(options.fields.map(field => [field.fieldname,
                { value: "", $wrapper: { get: () => new Element() }, $input: { on() {} } }]));
            this.primary = { prop() {} }; this.actions = {};
        }
        show() { this.shown = true; }
        hide() { this.shown = false; this.on_hide?.(); }
        get_primary_btn() { return this.primary; }
        set_primary_action(label, action) { this.label = label; this.action = action; }
        add_custom_action(label, action) { this.actions[label] = action; }
        get_value(key) { return this.fields_dict[key].value; }
        set_df_property() {}
    }
    const frappe = { ui: { Dialog, form: { on: (name, value) => handlers[name] = value } },
        msgprint: message => alerts.push(message), show_alert: message => alerts.push(message),
        call(options) { return new Promise((resolve, reject) => calls.push({ options, resolve: message => resolve({ message }), reject })); } };
    const window = {}, context = { frappe, window, document: { createElement: () => new Element() }, __: value => value,
        Uint8Array, btoa: value => Buffer.from(value, "binary").toString("base64") };
    vm.runInNewContext(source, context);
    const frm = { doc: { doctype: "Purchase Order", name: "new-po", docstatus: 0, company: "QA", supplier: "Supplier",
        transaction_date: "2026-10-13", items: [{ item_code: "EXISTING", qty: 9, rate: 3 }] },
        fields_dict: { items: { grid: { is_editable: () => true } } }, has_perm: () => true, is_new: () => true,
        add_custom_button(label, action) { this.button = action; },
        add_child(_field, row) { this.doc.items.push(row); }, refresh_field() {}, dirty() { this.dirtyCalled = true; },
        clear_table() { this.doc.items = []; },
        cscript: { calculate_taxes_and_totals: () => { frm.calculations = (frm.calculations || 0) + 1; } } };
    const importer = new window.JifengPurchaseImport(frm);
    importer.dialog.fields_dict.text.value = "商品编码/条码\t数量\t单价\n001\t2\t0\n002\t3\t8";
    return { importer, frm, calls, handlers, alerts };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
function preview(error = "") {
    return { company: "QA", supplier: "Supplier", row_count: 2, error_count: error ? 1 : 0, can_apply: !error,
        rows: [{ row_number: 2, code: "001", error: "", item: { item_code: "ITEM-001", item_name: "<img onerror=alert(1)>", qty: 2, rate: 0 } },
            { row_number: 3, code: "002", error, item: error ? null : { item_code: "ITEM-002", qty: 3, rate: 8 } }] };
}
async function prepared(env, result = preview()) {
    const running = env.importer.prepare(); await settle();
    assert.equal(env.calls.length, 1); env.calls[0].resolve(result); await running;
}
(async () => {
    {
        const env = environment(); await prepared(env);
        assert.equal(env.calls[0].options.type, "POST");
        env.importer.apply(); env.importer.apply();
        assert.equal(env.frm.doc.items.length, 3);
        assert.equal(env.frm.doc.items[0].item_code, "EXISTING");
        assert.equal(env.frm.doc.items[1].rate, 0);
        assert.equal(env.frm.calculations, 1); assert.ok(env.frm.dirtyCalled);
        assert.equal(env.calls.length, 1, "Applying drafts makes no save or submit request");
    }
    {
        const env = environment(); await prepared(env, preview("数量错误")); env.importer.apply();
        assert.equal(env.frm.doc.items.length, 1); assert.equal(env.importer.preview.can_apply, false);
    }
    {
        const env = environment(); const running = env.importer.prepare(); await settle();
        env.frm.doc.supplier = "Changed"; env.calls[0].resolve(preview()); await running; env.importer.apply();
        assert.equal(env.frm.doc.items.length, 1); assert.equal(env.importer.preview, null);
    }
    {
        const env = environment(); await prepared(env); env.frm.doc.items[0].qty = 10; env.importer.apply();
        assert.equal(env.frm.doc.items.length, 1);
    }
    {
        const env = environment(); const running = env.importer.prepare(); await settle();
        env.importer.dialog.hide(); env.calls[0].resolve(preview()); await running;
        assert.equal(env.importer.preview, null); assert.equal(env.frm.doc.items.length, 1);
    }
    {
        const env = environment(); await prepared(env); env.importer.invalidate(); env.importer.apply();
        assert.equal(env.frm.doc.items.length, 1); assert.equal(env.importer.preview, null);
    }
    {
        const env = environment(); env.importer.file = { name: "采购.xlsx", size: 100 }; await env.importer.prepare();
        assert.equal(env.calls.length, 0); assert.match(env.importer.status.text, /一种/);
    }
    {
        const env = environment(); env.frm.doc.is_subcontracted = 1;
        assert.equal(env.importer.constructor.canEdit(env.frm), false);
        env.frm.doc.is_subcontracted = 0; env.frm.doc.docstatus = 1;
        assert.equal(env.importer.constructor.canEdit(env.frm), false);
    }
    {
        const env = environment(); env.frm.doc.items = [{ __islocal: 1, qty: 0, rate: 0 }];
        env.importer.snapshot = JSON.stringify(env.frm.doc); await prepared(env); env.importer.apply();
        assert.equal(env.frm.doc.items.length, 2, "Remove the native empty starter row before appending");
    }
    console.log("9 purchase-import workbench scenarios passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
