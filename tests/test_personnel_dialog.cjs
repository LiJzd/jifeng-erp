// Reproduce the native Autocomplete's repeated onchange while typing a Link.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../jifeng_erp/public/js/jifeng_personnel_change.js"), "utf8");

function fixture(doctype = "Employee Promotion") {
	const calls = [], handlers = {}, button = { off() { return this; }, on(_, fn) { this.open = fn; return this; } };
	let dialog;
	const primary = { disabled: true, attr(_, value) { this.disabled = value; } };
	class Dialog {
		constructor(config) {
			this.config = config;
			this.values = {};
			this.fields_dict = Object.fromEntries(config.fields.map((df) => [df.fieldname, { df }]));
			this.replacements = 0;
			dialog = this;
		}
		get_primary_btn() { return primary; }
		get_value(name) { return this.values[name]; }
		set_value(name, value) { this.values[name] = value; }
		replace_field(name, df) {
			this.fields_dict[name] = { df };
			this.values[name] = df.default;
			this.replacements++;
		}
		show() {}
	}
	const table = doctype === "Employee Promotion" ? "promotion_details" : "transfer_details";
	const frm = {
		doctype, doc: { employee: "EMP-A", docstatus: 0 }, events: {},
		fields_dict: { [table]: { grid: { add_custom_button() { return button; } } } },
		set_df_property() {},
	};
	const frappe = { ui: { Dialog, form: { on(name, events) { handlers[name] = events; } } },
		call(call) { calls.push(call); }, msgprint() {} };
	vm.runInNewContext(source, { frappe, cur_frm: frm, __: (value) => value });
	handlers[doctype].refresh(frm);
	button.open();
	calls.shift().callback({ message: [
		{ value: "designation", label: "职位" }, { value: "department", label: "部门" },
	] });
	return { frm, calls, primary, dialog, select(fieldname) {
		dialog.values.property = fieldname;
		dialog.fields_dict.property.df.onchange();
	} };
}

function reply(call, value = "原职位") {
	call.callback({ message: { label: call.args.fieldname, value, datatype: "Link", options: "Designation" } });
}

for (const doctype of ["Employee Promotion", "Employee Transfer"]) {
	const f = fixture(doctype);
	f.select("designation");
	f.select("designation");
	assert.equal(f.calls.length, 1, `${doctype}: duplicate pending request`);
	reply(f.calls[0]);
	const control = f.dialog.fields_dict.new_value;
	f.dialog.values.new_value = "JF-HRUI-partial";
	f.select("designation");
	assert.equal(f.calls.length, 1, `${doctype}: duplicate resolved request`);
	assert.equal(f.dialog.fields_dict.new_value, control, "blur must keep the input node");
	assert.equal(f.dialog.values.new_value, "JF-HRUI-partial", "blur must keep typed text");
	assert.equal(f.dialog.replacements, 1);
	assert.equal(f.primary.disabled, false);
}

{
	const f = fixture();
	f.select("designation");
	f.select("department");
	reply(f.calls[1], "当前部门");
	f.dialog.values.new_value = "新部门";
	reply(f.calls[0]);
	assert.equal(f.dialog.values.current, "当前部门", "stale response must not change current");
	assert.equal(f.dialog.values.new_value, "新部门", "stale response must not clear new");
	assert.equal(f.dialog.replacements, 1);
}

{
	const f = fixture();
	f.select("designation");
	f.calls[0].error();
	f.select("designation");
	assert.equal(f.calls.length, 2, "failed request can be retried");
	f.calls[1].callback({});
	f.select("designation");
	assert.equal(f.calls.length, 3, "empty result can be retried");
	reply(f.calls[2]);
	assert.equal(f.primary.disabled, false);
}

{
	const f = fixture();
	f.select("designation");
	f.frm.doc.employee = "EMP-B";
	f.select("designation");
	assert.equal(f.calls.length, 2, "different employee needs a new request");
	assert.equal(f.calls[0].args.employee, "EMP-A");
	assert.equal(f.calls[1].args.employee, "EMP-B");
	reply(f.calls[1], "员工B职位");
	reply(f.calls[0], "员工A职位");
	assert.equal(f.dialog.values.current, "员工B职位");
}

console.log("Personnel dialog: duplicate requests, typed value, stale replies and retries passed.");
