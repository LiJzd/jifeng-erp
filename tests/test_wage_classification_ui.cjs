const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const observers = [], calls = [], messages = [], dialogs = [];
class Node {
    constructor(tag, parent = null) { this.tagName = tag; this.nodeType = 1; this.parentElement = parent; this.isConnected = true; this.visible = true; }
    getClientRects() { return this.visible && this.isConnected ? [{}] : []; }
    mutate(attribute) { for (const observer of [...observers]) if (observer.active && observer.nodes.some(row => row.node === this && row.options.attributes && row.options.attributeFilter.includes(attribute))) observer.callback([{ type: 'attributes', target: this }]); }
    removeAndReattach(child) { for (const observer of [...observers]) if (observer.active && observer.nodes.some(row => row.node === this && row.options.childList)) observer.callback([{ type: 'childList', removedNodes: [child] }]); }
}
const body = new Node('BODY'), page = new Node('DIV', body), wrapper = new Node('DIV', page);
const host = new Node('DIV', body), frame = new Node('IFRAME', host);
class Observer {
    constructor(callback) { this.callback = callback; this.nodes = []; this.active = true; observers.push(this); }
    observe(node, options) { this.nodes.push({ node, options }); }
    disconnect() { this.active = false; }
}
class Dialog {
    constructor(config) { this.config = config; this.shown = false; this.hideCount = 0; dialogs.push(this); }
    set_values(values) { this.values = { ...values }; }
    show() { this.shown = true; body.mutate('style'); }
    hide() { this.shown = false; this.hideCount++; this.on_hide?.(); }
}
let actions, route = [];
const window = { frameElement: frame };
const frappe = { ui: { Dialog, form: { on: (doctype, handlers) => { assert.equal(doctype, 'Jifeng Wage Entry'); actions = handlers; } } },
    get_route: () => route, msgprint: text => messages.push(text),
    call(options) { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
        calls.push({ options, resolve: message => resolve({ message }), reject }); return promise; },
};
vm.runInNewContext(fs.readFileSync('jifeng_erp/jifeng_erp/doctype/jifeng_wage_entry/jifeng_wage_entry.js', 'utf8'), { window, frappe, MutationObserver: Observer });
function form(status = 1) {
    const frm = { doc: { name: 'WAGE-1', modified: '2026-10-07 10:00:00.000001', company: 'C-1', employee: 'E-1', docstatus: status },
        wrapper: [wrapper], dirty: false, buttons: [], fields_dict: {},
        is_new: () => false, is_dirty() { return this.dirty; }, has_perm: () => false,
        set_intro() {}, set_df_property() {}, add_custom_button(label, action) { this.buttons.push({ label, action }); } };
    window.cur_frm = frm; route = ['Form', 'Jifeng Wage Entry', frm.doc.name]; wrapper.visible = frame.visible = true;
    actions.refresh(frm); return frm;
}
function button(frm) { return frm.buttons.find(row => row.label === '查看审核时人员类型')?.action; }
function dto(frm, extra = {}) { return { readonly: true, scope: 'wage_personnel_classification', name: frm.doc.name,
    version: frm.doc.modified.replace(' ', 'T'), docstatus: frm.doc.docstatus, classification_basis: 'first_native_submission',
    status: '已核实', personnel_type: '临时', recorded_at: '2026-10-07T10:01:00.000001', ...extra }; }
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    const initial = form(), original = JSON.stringify(initial.doc), pending = button(initial)();
    assert.equal(calls.at(-1).options.type, 'GET'); assert.equal(calls.at(-1).options.silent, true);
    assert.equal(calls.at(-1).options.args.name, initial.doc.name); assert.equal(calls.at(-1).options.args.version, initial.doc.modified);
    calls.at(-1).resolve(dto(initial)); await pending;
    const shown = dialogs.at(-1); assert.equal(shown.shown, true); assert.equal(shown.values.personnel_type, '临时');
    assert.equal(JSON.stringify(initial.doc), original);
    assert.ok(shown.config.fields.every(field => field.read_only === 1 && ['Data', 'Datetime'].includes(field.fieldtype)));
    assert.ok(observers.filter(observer => observer.active).every(observer => observer.nodes.every(row => !['BODY', 'HTML'].includes(row.node.tagName) || !row.options.attributes)));
    host.mutate('hidden'); assert.equal(shown.shown, false); assert.equal(initial._jifeng_classification_owner, null);

    const draft = form(0), count = calls.length; assert.equal(button(draft), undefined); assert.equal(calls.length, count);
    const legacy = form(2), legacyPending = button(legacy)(); calls.at(-1).resolve(dto(legacy, { status: '历史未记录', personnel_type: null, recorded_at: null }));
    await legacyPending; assert.equal(dialogs.at(-1).values.personnel_type, '未记录'); assert.equal(dialogs.at(-1).values.recorded_at, null);
    dialogs.at(-1).hide(); assert.equal(legacy._jifeng_classification_owner, null);

    const aba = form(), abaCount = dialogs.length, old = button(aba)(); const oldCall = calls.at(-1);
    host.mutate('hidden'); host.mutate('hidden'); oldCall.resolve(dto(aba)); await old; assert.equal(dialogs.length, abaCount);

    const reattached = form(), reattachCount = dialogs.length, removedRequest = button(reattached)();
    body.removeAndReattach(page); calls.at(-1).resolve(dto(reattached)); await removedRequest;
    assert.equal(dialogs.length, reattachCount);
    const removedDialog = form(), dialogRequest = button(removedDialog)(); calls.at(-1).resolve(dto(removedDialog)); await dialogRequest;
    const beforeRemoval = dialogs.at(-1); body.removeAndReattach(host); assert.equal(beforeRemoval.shown, false);

    const owned = form(), first = button(owned)(); const firstCall = calls.at(-1); actions.refresh(owned);
    const second = button(owned)(); const secondCall = calls.at(-1), heldOwner = owned._jifeng_classification_owner;
    firstCall.reject(new Error('old failure')); await first;
    assert.equal(owned._jifeng_classification_owner, heldOwner);
    secondCall.resolve(dto(owned, { personnel_type: '长期' })); await second; assert.equal(dialogs.at(-1).values.personnel_type, '长期');
    dialogs.at(-1).hide();

    for (const change of [frm => { frm.dirty = true; }, frm => { frm.doc = { ...frm.doc }; },
        frm => { frm.doc.company = 'C-2'; }, () => { route = ['Form', 'Jifeng Wage Entry', 'WAGE-2']; },
        () => { frame.visible = false; }, () => { wrapper.visible = false; }]) {
        const frm = form(), before = dialogs.length, request = button(frm)(), call = calls.at(-1), value = dto(frm);
        change(frm); call.resolve(value); await request; assert.equal(dialogs.length, before);
    }
    for (const invalid of [{ name: 'WAGE-2' }, { version: '2026-10-07T10:00:00.000002' }, { docstatus: 2 },
        { classification_basis: 'current_draft' }, { personnel_type: '<img src=x onerror=bad()>' },
        { recorded_at: null }, { scope: 'other' }]) {
        const frm = form(), before = dialogs.length, request = button(frm)(); calls.at(-1).resolve(dto(frm, invalid));
        await request; assert.equal(dialogs.length, before); assert.equal(frm._jifeng_classification_owner, null);
    }
    const invisible = form(); wrapper.visible = false; const invisibleCount = calls.length;
    await button(invisible)(); assert.equal(calls.length, invisibleCount);
    await flush(); assert.ok(observers.every(observer => !observer.active));
    console.log('wage classification UI: exact immutable projection, legacy missing evidence, late ownership, visibility, microseconds and safe controls passed (model only)');
})().catch(error => { console.error(error); process.exitCode = 1; });
