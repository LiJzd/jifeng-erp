// Payroll Entry completion action contract; no browser or business write.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");

function fixture(doc = { name: "PAY-1", docstatus: 1, status: "Submitted", salary_slips_submitted: 1 }) {
	const handlers = {}, buttons = [], confirmations = [], calls = [], alerts = [], messages = [], routes = [];
	let reloads = 0, response = { message: { status: "completed", payroll_entry: "PAY-1", journal_entry: "ACC-JV-1",
		salary_slips: ["SAL-1", "SAL-2"], already_completed: false } };
	const frappe = {
		ui: { form: { on(doctype, events) { assert.equal(doctype, "Payroll Entry"); Object.assign(handlers, events); } } },
		confirm(message, yes, no) { confirmations.push({ message, yes, no }); },
		async call(options) { calls.push(options); if (response instanceof Error) throw response; return response; },
		msgprint(message) { messages.push(message); },
		show_alert(value) { alerts.push(value); },
		set_route(...args) { routes.push(args); },
	};
	const context = { frappe, __: value => value, WeakSet };
	vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_payroll_entry.js", "utf8"), context);
	const frm = {
		doc,
		add_custom_button(label, action, group) {
			const button = { label, action, group, classes: [], disabled: false,
				addClass(value) { this.classes.push(value); return this; },
				prop(name, value) { if (name === "disabled") this.disabled = value; return this; } };
			buttons.push(button); return button;
		},
		async reload_doc() { reloads++; },
	};
	const refresh = () => handlers.refresh(frm);
	return { frappe, frm, handlers, buttons, confirmations, calls, alerts, messages, routes, refresh,
		get reloads() { return reloads; }, setResponse(value) { response = value; } };
}

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

async function main() {
	for (const doc of [{ name: "PAY-DRAFT", docstatus: 0, status: "Draft" },
		{ name: "PAY-QUEUE", docstatus: 1, status: "Queued" }]) {
		const model = fixture(doc); model.refresh(); assert.equal(model.buttons.length, 0);
	}

	const model = fixture(); model.refresh();
	assert.equal(model.buttons[0].label, "提交工资条并计提", "submitted payroll remains actionable even if one slip was already submitted");
	assert.equal(model.buttons[0].classes.includes("btn-primary"), true);
	model.buttons[0].action(); model.buttons[0].action();
	assert.equal(model.confirmations.length, 1, "duplicate confirmation prompts are suppressed");
	assert.match(model.confirmations[0].message, /尚未提交.*月度工资计提凭证/);
	await model.confirmations[0].yes(); await flush();
	assert.equal(model.calls.length, 1);
	assert.equal(model.calls[0].method, "jifeng_erp.payroll_completion.complete_payroll_accrual");
	assert.equal(model.calls[0].type, "POST");
	assert.deepEqual(JSON.parse(JSON.stringify(model.calls[0].args)), { payroll_entry: "PAY-1" });
	assert.equal(model.reloads, 1);
	assert.equal(model.alerts.length, 1);
	assert.match(model.alerts[0].message, /计提已生成/);
	assert.doesNotMatch(model.alerts[0].message, /已支付|已付款/);
	const link = model.buttons.find(button => button.label === "查看月度计提凭证");
	assert.ok(link, "verified journal entry is exposed as a normal source link"); link.action();
	assert.deepEqual(JSON.parse(JSON.stringify(model.routes[0])), ["Form", "Journal Entry", "ACC-JV-1"]);

	const invalid = fixture(); invalid.setResponse({ message: { status: "completed", payroll_entry: "PAY-1" } });
	invalid.refresh(); invalid.buttons[0].action(); await invalid.confirmations[0].yes();
	assert.equal(invalid.reloads, 1, "malformed outcome triggers a read refresh only");
	assert.equal(invalid.buttons.some(button => button.label === "查看月度计提凭证"), false);
	assert.equal(invalid.alerts.length, 0);
	assert.match(invalid.messages[0].message, /未返回可核验/);
	assert.equal(invalid.calls.length, 1, "malformed response is never automatically retried");

	const rejected = fixture(); rejected.setResponse(new Error("native permission denial"));
	rejected.refresh(); rejected.buttons[0].action();
	await assert.rejects(rejected.confirmations[0].yes(), /native permission denial/);
	assert.equal(rejected.calls.length, 1, "native permission errors are not retried");
	console.log("Payroll Entry completion UI contract passed");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
