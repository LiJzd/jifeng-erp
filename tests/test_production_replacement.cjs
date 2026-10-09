const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const buttons = {}, handlers = {}, storage = new Map(), routes = [], messages = [];
let dialog, posts = 0, failPost = true;
const current = { flow_order: 'FLOW', version: 'v1', snapshot: 'confirmed-source', stock_uom: 'Nos',
  sources: [{ work_order: 'WO', produced_qty: 0, loss_qty: 5, available_qty: 5, draft_qty: 0 }] };
const frm = { doc: { name: 'FLOW', modified: 'v1', quantity_basis: 'stock_v1', stock_uom: 'Nos' },
  is_new: () => false, is_dirty: () => false, add_custom_button(label, action) {
    buttons[label] = action; return { prop() { return this; }, attr() { return this; } };
  } };
const frappe = { session: { user: 'ordinary@test' }, ui: { form: { on(name, callbacks) {
  if (name === 'Jifeng Flow Order') Object.assign(handlers, callbacks);
} }, Dialog: class {
  constructor(config) { this.config = config; this.values = {}; dialog = this;
    for (const field of config.fields) this.values[field.fieldname] = field.default;
    // Native controls may raise a change before construction returns.
    config.fields[0].onchange();
  }
  show() {} hide() {} get_value(key) { return this.values[key]; }
  set_value(key, value) { this.values[key] = value; }
  get_primary_btn() { return { prop() {} }; }
} }, async call(request) {
  if (request.type === 'GET') return { message: current };
  posts++;
  if (failPost) throw Error('Reply lost');
  return { message: { work_order: 'REPLACEMENT' } };
}, msgprint(message) { messages.push(message); }, set_route(...args) { routes.push(args); } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
  '../jifeng_erp/jifeng_erp/doctype/jifeng_flow_order/jifeng_flow_order.js'), 'utf8'), {
  frappe, localStorage: { getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  console, setTimeout, clearTimeout,
});
(async () => {
  handlers.refresh(frm);
  await buttons['生成补产工单草稿']();
  const confirm = dialog.config.primary_action;
  await Promise.all([confirm({ source: 'WO' }), confirm({ source: 'WO' })]);
  assert.equal(posts, 1, 'Double click must not create two orders');
  assert.equal(storage.size, 1, 'Unknown result remains pending');
  await buttons['生成补产工单草稿']();
  assert.equal(posts, 1, 'Reopening never repeats an unanswered POST');
  assert.equal(routes.length, 0);
  current.sources[0].draft = 'FOUND-REPLACEMENT'; current.sources[0].draft_qty = 5;
  await buttons['生成补产工单草稿']();
  assert.equal(posts, 1);
  assert.deepEqual(routes[0], ['Form', 'Work Order', 'FOUND-REPLACEMENT']);
  assert.equal(storage.size, 0);
  current.sources[0].draft = null; failPost = false;
  await buttons['生成补产工单草稿']();
  frm.doc.name = 'OTHER-FLOW';
  await dialog.config.primary_action({ source: 'WO' });
  assert.equal(posts, 1, 'A changed form cannot confirm the prior source');
  console.log('Replacement unknown result, double click, current draft readback and changed form checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
