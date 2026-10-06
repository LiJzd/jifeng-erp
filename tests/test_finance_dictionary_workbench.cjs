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
  legacy_number: "001", value: "费用名称", remarks: "<script>text</script>", inactive: 0, account: null, can_write: true, can_create: true, can_map_account: true };
function state(existing = false) {
  const fields = { legacy_number: existing ? "001" : "", value: row.value, remarks: row.remarks, inactive: 0 };
  const locks = {};
  return { entry: existing ? { ...row } : { company: row.company, dictionary_type: row.dictionary_type }, company: row.company,
    writable: true, request_id: "c9a22b22-4e5d-46f2-a3c3-1cb6fb7f2c1e", saving: false, unresolved: false, closed: false,
    dialog: { get_value: name => fields[name], get_values: () => fields,
      actions: [], add_custom_action(label, action) { this.actions.push({ label, action }); },
      set_values: async values => Object.assign(fields, values),
      set_value: async (name, value) => { fields[name] = value; }, set_df_property: (name, property, value) => { locks[name] = value; },
      get_primary_btn: () => ({ prop: (name, value) => { locks.primary = value; } }) }, fields, locks };
}
// Frappe hydrates defaults asynchronously and read-only refresh renders cached model values.
class AsyncDialog {
  constructor(options) {
    this.options = options; this.actions = [];
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
  set_primary_action(label, action) { this.primary = { label, action }; }
  add_custom_action(label, action) { this.actions.push({ label, action }); }
  show() { this.shown = true; }
  hide() { this.hidden++; }
}
sandbox.frappe.ui = { Dialog: AsyncDialog };
(async () => {
  const w = workbench();
  assert.equal(w.valid(row), true);
  for (const bad of [{ company: "Other" }, { dictionary_type: "收款银行" }, { inactive: 2 }, { can_write: undefined }, { can_map_account: undefined }, { account: 3 }]) assert.equal(w.valid({ ...row, ...bad }), false);
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
  assert.deepEqual(Array.from(untouchedState.dialog.options.fields, field => field.fieldname), ["legacy_number", "value", "remarks", "inactive"], "observed four-field editor stays in the same order");
  assert.equal(untouchedState.dialog.actions.some(action => action.label === "记账科目关联"), true, "saved labels have a separate mapping action");
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
  assert.equal(fresh.dialog.actions.some(action => action.label === "记账科目关联"), true, "a confirmed newly saved label gets the same independent setting action");
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

  const expenseFilters = { company: row.company, account_currency: "CNY", is_group: 0, disabled: 0, root_type: "Expense" };
  const context = (entry = row, filters = expenseFilters) => ({ entry: { ...entry }, account_filters: { ...filters } });
  const mappingBench = workbench();
  assert.equal(mappingBench.accountContext(context(), row.company, row.name), true);
  for (const bad of [{ company: "Other" }, { account_currency: "" }, { disabled: 1 }, { is_group: 1 }, { root_type: "Asset" }, { account_type: "Bank" }]) {
    assert.equal(mappingBench.accountContext(context(row, { ...expenseFilters, ...bad }), row.company, row.name), false, "reject inaccurate or unexpected account filters");
  }
  const bankBench = workbench(); bankBench.type = "付款银行";
  const bankRow = { ...row, dictionary_type: "付款银行" };
  const bankFilters = { ...expenseFilters, root_type: "Asset", account_type: "Bank" };
  assert.equal(bankBench.accountContext(context(bankRow, bankFilters), row.company, row.name), true);
  assert.equal(bankBench.accountContext(context(bankRow, { ...bankFilters, account_type: "Cash" }), row.company, row.name), false);
  assert.equal(mappingBench.accountContext(context({ ...row, can_map_account: false }), row.company, row.name), false);
  mappingBench.api = async (method, args, http) => {
    if (method === "read_dictionary") return { ...row };
    assert.equal(method, "read_dictionary_account"); assert.equal(http || "GET", "GET");
    assert.deepEqual(Object.keys(args).sort(), ["company", "dictionary_type", "name"]);
    return context();
  };
  await mappingBench.open(row.name); const ordinaryState = [...mappingBench.editors][0];
  await mappingBench.openAccount(ordinaryState); const mapping = [...mappingBench.editors][0];
  assert.equal(ordinaryState.closed, true, "replace only the clean ordinary editor");
  assert.equal(mapping.mode, "account"); assert.equal(mapping.entry.name, row.name);
  assert.equal(mappingBench.isDirty(), false, "mapping hydration is clean");
  assert.deepEqual(Array.from(mapping.dialog.options.fields, field => field.fieldname), ["company", "dictionary_type", "legacy_number", "value", "account"]);
  assert.equal(mapping.dialog.inputs.company, row.company); assert.equal(mapping.dialog.inputs.dictionary_type, row.dictionary_type);
  assert.equal(mapping.dialog.inputs.legacy_number, row.legacy_number); assert.equal(mapping.dialog.inputs.value, row.value);
  const accountField = mapping.dialog.options.fields.find(field => field.fieldname === "account");
  assert.equal(accountField.options, "Account");
  assert.deepEqual(JSON.parse(JSON.stringify(accountField.get_query())), { filters: expenseFilters }, "native Link gets exact server filters");
  const mutableFilters = accountField.get_query(); mutableFilters.filters.company = "tampered";
  assert.equal(accountField.get_query().filters.company, row.company, "one query cannot mutate later filters");
  await mappingBench.open(row.name);
  const separateOrdinary = [...mappingBench.editors].find(editor => editor.mode !== "account");
  assert.ok(separateOrdinary, "list Modify opens an ordinary four-field editor even when mapping is open");
  assert.deepEqual(Array.from(separateOrdinary.dialog.options.fields, field => field.fieldname), ["legacy_number", "value", "remarks", "inactive"]);
  const editorCount = mappingBench.editors.size;
  mappingBench.api = async () => { throw Error("existing mapping focus should not requery"); };
  await mappingBench.openAccount(separateOrdinary);
  assert.equal(mappingBench.editors.size, editorCount, "repeated mapping action focuses the same mapping draft");
  separateOrdinary.dialog.hide(); assert.equal(separateOrdinary.closed, true);
  mapping.dialog.inputs.account = "Expense Account";
  let mappingWrites = 0;
  mappingBench.api = async (method, args, http) => {
    assert.equal(method, "set_dictionary_account"); assert.equal(http, "POST"); mappingWrites++;
    assert.deepEqual(Object.keys(args).sort(), ["account", "company", "dictionary_type", "modified", "name"]);
    assert.equal(args.modified, row.modified); assert.equal(args.account, "Expense Account");
    return { ...row, account: args.account, modified: "2026-10-06 02:00:00.000001" };
  };
  await mappingBench.save(mapping);
  assert.equal(mappingWrites, 1); assert.equal(mappingBench.isDirty(), false);
  assert.equal(mapping.dialog.inputs.account, "Expense Account", "freeze preserves the explicit Link input");
  assert.equal(mapping.entry.modified, "2026-10-06 02:00:00.000001");
  mapping.dialog.inputs.account = "";
  mappingBench.api = async (method, args, http) => {
    if (method === "set_dictionary_account") { assert.equal(http, "POST"); assert.equal(args.account, null); mappingWrites++; throw { status: 0 }; }
    assert.equal(method, "read_dictionary"); assert.equal(http || "GET", "GET");
    return { ...row, modified: "2026-10-06 02:00:01.000001", can_map_account: false };
  };
  await mappingBench.save(mapping); assert.equal(mapping.unresolved, true); assert.equal(mapping.dialog.readonly.account, true);
  const writesAfterUnknown = mappingWrites;
  await mappingBench.save(mapping); assert.equal(mappingWrites, writesAfterUnknown, "unknown mapping write is not repeated");
  mapping.dialog.hide(); assert.equal(mapping.closed, false, "unknown result protects the editor from closing");
  assert.notEqual(mappingBench.getCloseBlock(), "");
  await mappingBench.resolve(mapping);
  assert.equal(mapping.unresolved, false); assert.equal(mapping.writable, false, "permission change is adopted from current readback");
  assert.equal(mapping.dialog.readonly.account, true); assert.equal(mappingBench.isDirty(), false);
  mapping.dialog.hide(); assert.equal(mapping.closed, true);

  const readonly = workbench(); readonly.api = async () => ({ ...row, can_write: false });
  await readonly.open(row.name); const readonlyState = [...readonly.editors][0];
  assert.equal(readonlyState.writable, false);
  assert.equal(readonlyState.dialog.actions.some(action => action.label === "记账科目关联"), true, "mapping permission is independent from ordinary field editing");
  const noMapping = workbench(); noMapping.api = async () => ({ ...row, can_map_account: false });
  await noMapping.open(row.name);
  assert.equal([...noMapping.editors][0].dialog.actions.some(action => action.label === "记账科目关联"), false);
  const unsaved = workbench(); unsaved.api = async () => ({ ...row, name: null, can_map_account: false });
  await unsaved.open(); assert.equal([...unsaved.editors][0].dialog.actions.some(action => action.label === "记账科目关联"), false);

  const dirtySource = workbench(); dirtySource.api = async () => ({ ...row });
  await dirtySource.open(row.name); const dirtySourceState = [...dirtySource.editors][0];
  dirtySourceState.dialog.inputs.remarks = "unsaved remark";
  let dirtySourceReads = 0;
  dirtySource.api = async () => { dirtySourceReads++; throw Error("dirty source must not query mapping"); };
  await dirtySource.openAccount(dirtySourceState); assert.equal(dirtySourceReads, 0); assert.equal(dirtySourceState.closed, false); assert.equal(dirtySourceState.dialog.inputs.remarks, "unsaved remark");
  const lateMapping = workbench(); lateMapping.api = async () => ({ ...row });
  await lateMapping.open(row.name); const lateState = [...lateMapping.editors][0]; let finishMapping;
  lateMapping.api = () => new Promise(resolve => { finishMapping = resolve; });
  const latePending = lateMapping.openAccount(lateState); assert.equal(lateState.accountOpening, true);
  lateState.dialog.hide(); assert.equal(lateState.closed, false, "context read cannot close the source editor mid-flight");
  lateMapping.companyName = () => "Company B"; finishMapping(context()); await latePending;
  assert.equal(lateState.accountOpening, false); assert.equal(lateState.closed, false);
  assert.equal([...lateMapping.editors].some(editor => editor.mode === "account"), false, "late different-company context creates no editor");

  const roundTrip = workbench(); roundTrip.api = async () => ({ ...row });
  await roundTrip.open(row.name); const roundTripState = [...roundTrip.editors][0];
  let roundTripCompany = row.company, finishRoundTrip;
  roundTrip.companyName = () => roundTripCompany; roundTrip.query = P.query;
  roundTrip.api = async (method, args) => {
    if (method === "read_dictionary_account") return new Promise(resolve => { finishRoundTrip = resolve; });
    assert.equal(method, "list_dictionary");
    return { company: args.company, dictionary_type: roundTrip.type, search: "", include_inactive: 0,
      start: 0, page_length: 25, total: 0, rows: [], can_create: false };
  };
  const roundTripPending = roundTrip.openAccount(roundTripState), sourceSequence = roundTrip.sequence;
  // The normal company onchange increments sequence and calls the real query,
  // which increments again. Returning to the same text cannot revive old reads.
  roundTripCompany = "Company B"; roundTrip.sequence++; await roundTrip.query(0);
  roundTripCompany = row.company; roundTrip.sequence++; await roundTrip.query(0);
  assert.ok(roundTrip.sequence > sourceSequence); assert.equal(roundTrip.companyName(), row.company);
  finishRoundTrip(context()); await roundTripPending;
  assert.equal(roundTripState.accountOpening, false); assert.equal(roundTripState.closed, false);
  assert.equal([...roundTrip.editors].some(editor => editor.mode === "account"), false, "A to B to A invalidates the old mapping context despite matching company text");

  const staleMapping = state(true); staleMapping.mode = "account"; staleMapping.fields.account = "New Account";
  staleMapping.original = JSON.stringify({ account: "" }); let staleMappingWrites = 0;
  const staleMappingBench = workbench(); staleMappingBench.api = async () => { staleMappingWrites++; throw { status: 417 }; };
  await staleMappingBench.save(staleMapping); assert.equal(staleMappingWrites, 1); assert.equal(staleMapping.unresolved, false);
  assert.equal(staleMapping.locks.account, false, "definite validation rejection leaves the draft editable");
  const versioned = state(true); versioned.mode = "account"; versioned.attempt = { account: "EXP" };
  assert.equal(!!w.matches(versioned, { ...row, account: "EXP" }), false, "same source version cannot confirm a mapping write");
  assert.equal(!!w.matches(versioned, { ...row, account: "EXP", modified: row.modified.replace(" ", "T") }), false, "ISO separator does not create a newer version");
  assert.equal(!!w.matches(versioned, { ...row, account: "EXP", modified: "2026-10-05 11:59:59.999999" }), false);
  assert.equal(!!w.matches(versioned, { ...row, account: "EXP", modified: "2026-10-05 12:00:00.000002" }), true, "one microsecond native advance is preserved");
  for (const modified of ["not-a-version", "2026-02-30 12:00:00.000001", "2026-10-05 12:00:00.0000001", "2026-10-05T12:00:00Z"]) {
    assert.equal(!!w.matches(versioned, { ...row, account: "EXP", modified }), false);
  }
  assert.equal(!!w.matches(versioned, { ...row, account: "wrong", modified: "2026-10-05 12:00:00.000002" }), false);
  assert.equal(!!w.matches(versioned, { ...row, value: "another edit", account: "EXP", modified: "2026-10-05 12:00:00.000002" }), false, "concurrent ordinary edits are not silently accepted as this mapping result");
  console.log("finance dictionary frontend: scope, identity, save uncertainty, permission and paging checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
