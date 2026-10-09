// Shared native Stock Entry actions: source display is read only and escaped.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
function harness(doc, permissions = true) {
	const buttons = [], calls = [], messages = [], routes = [];
	let handlers;
	const escape = value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
	const frappe = { ui: { form: { on: (type, actions) => { assert.equal(type, "Stock Entry"); handlers = actions; } } },
		utils: { escape_html: escape }, msgprint: message => messages.push(message),
		set_route: (...args) => routes.push(args), call: options => {
			let resolve; const promise = new Promise(yes => { resolve = yes; }); calls.push({ options, resolve }); return promise;
		} };
	vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_material_returns.js", "utf8"), { frappe, __: value => value });
	const frm = { doc, is_new: () => false, has_perm: () => permissions,
		add_custom_button: (label, action, group) => buttons.push({ label, action, group }) };
	handlers.refresh(frm);
	return { buttons, calls, messages, routes, frm };
}
(async () => {
	const issued = harness({ name: "ISSUE", docstatus: 1, stock_entry_type: "Material Issue" });
	assert.deepEqual(issued.buttons.map(button => button.label), ["领料退库"]);
	const action = issued.buttons[0].action();
	await issued.buttons[0].action();
	assert.equal(issued.calls.length, 1, "double click must not create two drafts");
	assert.equal(issued.calls[0].options.type, "POST");
	assert.equal(issued.calls[0].options.args.source_name, "ISSUE");
	issued.calls[0].resolve({ message: { name: "RETURN", docstatus: 0, reused: true } }); await action;
	assert.deepEqual(issued.routes, [["Form", "Stock Entry", "RETURN"]]);
	const doc = { name: "RETURN", docstatus: 0, stock_entry_type: "Material Receipt", jf_material_return_against: "ISSUE" };
	const returning = harness(doc);
	assert.deepEqual(returning.buttons.map(button => button.label), ["原领料批次/序列"]);
	const original = JSON.stringify(doc), display = returning.buttons[0].action();
	assert.equal(returning.calls[0].options.type, "GET");
	assert.equal(returning.calls[0].options.method, "jifeng_erp.material_returns.get_material_return_sources");
	returning.calls[0].resolve({ message: { source_name: "ISSUE", rows: [{ source_idx: 2, item_code: '<img src=x onerror="bad()">',
		original_bundles: ["BUNDLE-2"], stock_uom: "Nos", warehouse: "原仓库", lots: [{ batch_no: "<script>bad()</script>",
			serial_no: 'S"HIGH', original_qty: "2", returned_qty: "1", remaining_qty: "1" }] }] } }); await display;
	assert.equal(JSON.stringify(doc), original, "source display must never fill or allocate native stock fields");
	assert.equal(returning.routes.length, 0);
	assert.equal(returning.calls.length, 1);
	assert.match(returning.messages[0].message, /&lt;script&gt;/);
	assert.match(returning.messages[0].message, /S&quot;HIGH/);
	assert.doesNotMatch(returning.messages[0].message, /<img|<script/);
	const stale = returning.buttons[0].action();
	returning.frm.doc.jf_material_return_against = "OTHER";
	returning.calls[1].resolve({ message: { source_name: "ISSUE", rows: [] } }); await stale;
	assert.equal(returning.messages.length, 1, "changed source cannot display previous issue's response");
	assert.equal(harness(doc, false).buttons.length, 0);
	console.log("Material return source display and native draft actions passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
