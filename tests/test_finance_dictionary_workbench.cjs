const assert = require("node:assert/strict");
const fs = require("node:fs"); const vm = require("node:vm"); const path = require("node:path");
const alerts = [], confirmations = [];
const sandbox = { window: {}, crypto: require("node:crypto"), __: x => x,
  frappe: { msgprint: x => alerts.push(x), show_alert: x => alerts.push(x.message), confirm: x => confirmations.push(x) } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_finance_dictionary.js"), "utf8"), sandbox);
const P = sandbox.window.JifengFinanceDictionary.prototype;
function workbench() {
  const object = Object.create(P);
  Object.assign(object, { type: "费用科目", sequence: 0, disposed: false, start: 0, editors: new Set(),
    companyName: () => "Company A", search: { value: "" }, inactive: { checked: false },
    body: { replaceChildren() {}, append() {} }, add: {}, status: {}, clear() { this.loadedScope = null; this.rows = []; },
    pager() {}, query() {} }); return object;
}
const row = { name: "JF-FIN-c9a22b22-4e5d-46f2-a3c3-1cb6fb7f2c1e", modified: "2026-10-05 12:00:00.000001", company: "Company A", dictionary_type: "费用科目",
  legacy_number: "001", value: "费用名称", remarks: "<script>text</script>", inactive: 0, can_write: true, can_create: true };
function state(existing = false) {
  const fields = { legacy_number: existing ? "001" : "", value: row.value, remarks: row.remarks, inactive: 0 };
  const locks = {};
  return { entry: existing ? { ...row } : { company: row.company, dictionary_type: row.dictionary_type }, company: row.company,
    writable: true, request_id: "c9a22b22-4e5d-46f2-a3c3-1cb6fb7f2c1e", saving: false, unresolved: false, closed: false,
    dialog: { get_value: name => fields[name], get_values: () => fields,
      set_values: async values => Object.assign(fields, values),
      set_value: async (name, value) => { fields[name] = value; }, set_df_property: (name, property, value) => { locks[name] = value; },
      get_primary_btn: () => ({ prop: (name, value) => { locks.primary = value; } }) }, fields, locks };
}
// Frappe hydrates defaults asynchronously and read-only refresh renders cached model values.
class AsyncDialog {
  constructor(options) {
    this.inputs = {}; this.model = {}; this.readonly = {}; this.hidden = 0;
    this.$wrapper = { addClass() {}, modal() {}, on() {} };
    for (const field of options.fields) this.readonly[field.fieldname] = !!field.read_only;
    this.defaults = this.set_values(Object.fromEntries(options.fields.filter(f => f.default !== undefined).map(f => [f.fieldname, f.default])));
  }
  async set_values(values) { await Promise.resolve(); Object.assign(this.model, values); Object.assign(this.inputs, values); }
  get_value(name) { return this.readonly[name] ? this.model[name] : this.inputs[name]; }
  get_values() { return this.inputs; }
  set_df_property(name, property, value) { this.readonly[name] = !!value; this.inputs[name] = this.model[name]; }
  get_primary_btn() { return { prop() {} }; }
  get_close_btn() { return { removeAttr() { return this; }, on() {} }; }
  set_primary_action() {}
  add_custom_action() {}
  show() { this.shown = true; }
  hide() { this.hidden++; }
}
sandbox.frappe.ui = { Dialog: AsyncDialog };
(async () => {
  const w = workbench();
  assert.equal(w.valid(row), true);
  for (const bad of [{ company: "Other" }, { dictionary_type: "收款银行" }, { inactive: 2 }, { can_write: undefined }]) assert.equal(w.valid({ ...row, ...bad }), false);
  const fresh = state(); fresh.attempt = w.values(fresh);
  assert.equal(!!w.matches(fresh, row), true, "new automatic display number is accepted");
  assert.equal(!!w.matches(fresh, { ...row, name: "another-id" }), false, "creation confirmation must identify this exact request");
  const old = state(true); old.attempt = w.values(old);
  assert.equal(!!w.matches(old, { ...row, legacy_number: "002" }), false, "existing display identity cannot silently change");
  assert.equal(!!w.matches(old, { ...row, value: "another user's value" }), false);
  assert.equal(Object.hasOwn(w.payload(fresh), "account"), false, "ordinary editor cannot change ledger mapping");
  assert.equal(w.payload(fresh).request_id, fresh.request_id);
  assert.equal(Object.hasOwn(w.payload(fresh), "name"), false, "create contract excludes saved-record keys");
  assert.equal(Object.hasOwn(w.payload(fresh), "modified"), false);
  assert.equal(Object.hasOwn(w.payload(old), "request_id"), false);
  const untouched = workbench(); untouched.api = async () => ({ ...row });
  await untouched.open(row.name);
  const untouchedState = [...untouched.editors][0];
  await untouchedState.dialog.defaults;
  assert.equal(untouched.isDirty(), false, "asynchronous hydration cannot mark an untouched editor dirty");
  untouchedState.dialog.hide();
  assert.equal(untouchedState.dialog.hidden, 1); assert.equal(confirmations.length, 0);
  const edited = workbench(); edited.api = async () => ({ ...row });
  await edited.open(row.name); const editedState = [...edited.editors][0];
  editedState.dialog.inputs.remarks = "  exact new remark  "; editedState.dialog.inputs.inactive = 1;
  assert.equal(edited.isDirty(), true, "actual input changes remain protected");
  editedState.dialog.hide(); assert.equal(confirmations.length, 1); assert.equal(editedState.closed, false);
  edited.api = async (method, args) => {
    assert.equal(method, "save_dictionary");
    assert.equal(args.payload.remarks, "  exact new remark  ", "read-only refresh must not replace current inputs with cached values");
    assert.equal(args.payload.inactive, 1);
    return { ...row, remarks: args.payload.remarks, inactive: 1, modified: "2026-10-06 01:00:00.000001" };
  };
  await edited.save(editedState);
  assert.equal(edited.isDirty(), false, "confirmed save must restore a clean baseline after unlocking");
  assert.equal(editedState.dialog.inputs.remarks, "  exact new remark  ");
  editedState.dialog.hide(); assert.equal(editedState.dialog.hidden, 1); assert.equal(confirmations.length, 1);
  let writes = 0;
  w.api = async method => { if (method === "save_dictionary") { writes++; throw { status: 0 }; } return { ...row }; };
  await w.save(fresh); assert.equal(fresh.unresolved, true); assert.equal(fresh.locks.primary, true);
  await w.save(fresh); assert.equal(writes, 1, "unknown save is never automatically or blindly repeated");
  await w.resolve(fresh); assert.equal(fresh.unresolved, false); assert.equal(fresh.entry.name, row.name); assert.equal(fresh.fields.legacy_number, "001");
  const validation = state(); w.api = async () => { writes++; throw { status: 417 }; };
  await w.save(validation); assert.equal(validation.unresolved, false); assert.equal(validation.locks.primary, false);
  const wrongScope = state(); w.api = async () => ({ ...row, company: "Other" });
  await w.save(wrongScope); assert.equal(wrongScope.unresolved, true);
  let resolveQuery; const stale = workbench(); stale.query = P.query;
  stale.api = () => new Promise(resolve => { resolveQuery = resolve; });
  const pending = stale.query(0); stale.companyName = () => "Company B";
  resolveQuery({ company: "Company A", dictionary_type: stale.type, search: "", include_inactive: 0, start: 0, page_length: 25, total: 1, rows: [row], can_create: true });
  await pending; assert.equal(stale.loadedScope, null); assert.equal(stale.rows.length, 0, "late other-company data is discarded");
  const malformed = workbench(); malformed.query = P.query;
  malformed.api = async () => ({ company: "Company A", dictionary_type: malformed.type, search: "", include_inactive: 0, start: 0, page_length: 25, total: 2, rows: [row], can_create: true });
  await malformed.query(0); assert.equal(malformed.loadedScope, null, "partial page is not presented as complete");
  console.log("finance dictionary frontend: scope, identity, save uncertainty, permission and paging checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
