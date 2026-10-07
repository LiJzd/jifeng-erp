const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const handlers = {};
const buttons = {};
const source = { company: 'QA', party_type: 'Supplier', party: 'SUP', paid_to: 'PAY',
    paid_to_account_currency: 'CNY', reference_doctype: 'Journal Entry', reference_name: 'JE-30',
    total_amount: 30, outstanding_amount: 30 };
let promptAction, payment, contextCalls = 0, created = 0;
let routed;
const frappe = { ui: { form: { on: (name, events) => { handlers[name] = events; } } },
    model: { can_create: () => true, with_doctype: (_name, callback) => callback(),
        get_new_doc: doctype => {
            assert.equal(doctype, 'Payment Entry'); created++;
            payment = { doctype, name: 'new-payment-test', __islocal: 1, __run_link_triggers: 1,
                references: [{ reference_doctype: 'Purchase Invoice' }, {}] };
            return payment;
        },
        clear_table: (doc, field) => { doc[field] = []; },
        add_child: (doc, doctype, field) => {
        const row = { doctype }; doc[field].push(row); return row;
    } },
    set_route: (...route) => {
        assert.equal(route.join('/'), 'Form/Payment Entry/new-payment-test');
        // A form can begin loading and copying its table before the route
        // promise resolves. All source fields must exist at this boundary.
        routed = JSON.parse(JSON.stringify(payment));
        if (payment.__run_link_triggers) { payment.party = ''; payment.references = []; }
    },
    prompt: (_fields, action) => { promptAction = action; return { show() {} }; },
    new_doc: () => { throw new Error('routing before source initialization loses references'); } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../jifeng_erp/jifeng_erp/doctype/jifeng_expense_voucher/jifeng_expense_voucher.js'), 'utf8'), { frappe });
const frm = { doc: { name: 'EXP-30', docstatus: 1, payment_method: '赊账', journal_entry: 'JE-30' },
    add_custom_button: (label, action) => { buttons[label] = action; },
    call: async method => { assert.equal(method, 'credit_payment_context'); contextCalls++; return { message: source }; } };
(async () => {
    handlers['Jifeng Expense Voucher'].refresh(frm);
    await buttons['办理付款']();
    assert.equal(contextCalls, 1);
    assert.equal(created, 0, 'reading a balance must not create or save a payment');
    await promptAction({ paid_from: 'CASH', amount: 30 });
    assert.equal(created, 1);
    assert.equal(payment.party, 'SUP');
    assert.equal(payment.company, 'QA');
    assert.equal(payment.paid_from, 'CASH');
    assert.equal(payment.paid_to, 'PAY');
    assert.equal(payment.paid_to_account_currency, 'CNY');
    assert.equal(payment.paid_amount, 30);
    assert.equal(payment.received_amount, 30);
    assert.equal(payment.references.length, 1);
    assert.equal(payment.references[0].reference_doctype, 'Journal Entry');
    assert.equal(payment.references[0].reference_name, 'JE-30');
    assert.equal(payment.references[0].allocated_amount, 30);
    assert.equal(payment.references[0].outstanding_amount, 30);
    assert.equal(payment.__islocal, 1, 'the prepared document remains an unsaved native draft');
    assert.equal(routed.party, 'SUP');
    assert.equal(routed.references.length, 1, 'no default empty rows survive');
    assert.equal(routed.references[0].reference_name, 'JE-30', 'exact source exists before form rendering starts');
    await buttons['办理付款']();
    assert.equal(contextCalls, 1, 'an already open payment dialog is reused');
    frm._jf_credit_dialog.on_hide();
    assert.equal(frm._jf_credit_dialog, null);
    console.log('expense payment source initialization passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
