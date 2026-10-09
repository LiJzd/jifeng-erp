// Loss batch client protocol; no real browser, HTTP or business writes.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,
    "../jifeng_erp/jifeng_erp/doctype/jifeng_cost_preview/jifeng_cost_preview.js"), "utf8");
function fixture() {
    let handlers, respond;
    const buttons = {}, messages = [], requests = [];
    const frappe = { ui: { form: { on: (_, value) => { handlers = value; } } },
        utils: { escape_html: value => String(value).replaceAll("<", "&lt;") },
        msgprint: message => messages.push(message), call: options => {
            requests.push(options); return respond(options);
        } };
    vm.runInNewContext(source, { frappe, format_currency: value => String(value) });
    const frm = { doc: { name: "PREVIEW", flow_order: "FLOW", planned_qty: 5, batches: [] },
        is_new: () => false, set_df_property: () => {}, set_intro: () => {},
        add_custom_button: (label, callback) => { buttons[label] = callback; },
        dirty: () => { frm.changed = true; }, refresh_field: () => {},
        add_child: (_, row) => { frm.doc.batches.push(row); frm.changed = true; return row; } };
    handlers.refresh(frm);
    const actual = [{ source_kind: "Production Loss", source_name: "LOSS", work_order: "WO", job_card: "CARD",
        company: "JF", item_code: "FG", stock_uom: "Nos", qty: 5, inventory_qty: 0, lost_qty: 5 }];
    respond = async () => ({ message: actual });
    return { frm, actual, messages, requests, read: () => buttons["读取已提交制造损耗出库批次"](),
        respond: callback => { respond = callback; } };
}
async function main() {
    const initial = fixture();
    await initial.read();
    assert.equal(initial.requests[0].type, "GET");
    assert.deepEqual(JSON.parse(JSON.stringify(initial.frm.doc.batches)), [{ receipt_type: "首次回货", reference_no: "LOSS",
        source_loss_issue: "LOSS", received_qty: 5, lost_qty: 5, good_qty: 0, rework_qty: 0, pending_qty: 0, extra_processing_amount: 0 }]);
    assert.match(initial.messages[0], /没有成品入库/);
    assert.match(initial.messages[0], /未分配成本/);
    assert.equal(initial.frm.doc.batches[0].source_manufacture, undefined, "loss is not a Manufacture source");
    await initial.read();
    assert.equal(initial.frm.doc.batches.length, 1, "same loss source is not duplicated");
    initial.frm.doc.batches[0].good_qty = 1;
    await initial.read();
    assert.equal(initial.frm.doc.batches[0].good_qty, 1, "invalid manual classification is reported, not overwritten");
    assert.match(initial.messages.at(-1), /未覆盖分类/);

    const exactManual = fixture(); exactManual.frm.doc.batches = [{ receipt_type: "首次回货", reference_no: "LOSS",
        received_qty: 5, lost_qty: 5, good_qty: 0, rework_qty: 0, pending_qty: 0 }];
    await exactManual.read();
    assert.equal(exactManual.frm.doc.batches[0].source_loss_issue, "LOSS");
    assert.equal(exactManual.frm.doc.batches.length, 1);
    const wrongManual = fixture(); wrongManual.frm.doc.batches = [{ receipt_type: "首次回货", reference_no: "LOSS",
        received_qty: 5, lost_qty: 4, good_qty: 1 }];
    await wrongManual.read();
    assert.equal(wrongManual.frm.doc.batches[0].source_loss_issue, undefined);

    const delayed = fixture(); let finish;
    delayed.respond(() => new Promise(resolve => { finish = resolve; }));
    const waiting = delayed.read(); delayed.frm.doc.batches.push({ reference_no: "UNSAVED", good_qty: 3 });
    finish({ message: delayed.actual }); await waiting;
    assert.equal(delayed.frm.doc.batches.length, 1, "changes made during read are preserved");
    assert.match(delayed.messages[0], /读取期间批次已修改/);

    const moved = fixture(); let late;
    moved.respond(() => new Promise(resolve => { late = resolve; }));
    const moving = moved.read(); moved.frm.doc = { ...moved.frm.doc, flow_order: "OTHER" };
    late({ message: moved.actual }); await moving;
    assert.equal(moved.frm.doc.batches.length, 0, "late source is not added to another Flow");
    console.log("Production loss batch protocol passed; GUI not exercised.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
