// Exercise the real mount through cold Desk startup, stale pages and disposal.
const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");

class Element {
	constructor() { this.children = []; this.events = {}; this.classes = new Set(); this.classList = { add: name => this.classes.add(name) }; }
	append(...children) { this.children.push(...children); }
	appendChild(child) { this.append(child); }
	addEventListener(name, callback) { this.events[name] = callback; }
	remove() { this.removed = true; }
}

function fixture(spec) {
	const timers = new Map(), calls = { route: 0, clear: 0, refresh: 0, create: 0 };
	let sequence = 0, route = null, onRoute, onRefresh;
	const child = { document: { createElement: () => new Element(), head: new Element() },
		frappe: { boot: { user: { name: "synthetic" } }, router: {}, app: null, container: null,
			get_route: () => route, new_doc: async () => { calls.create++; },
			set_route: async (...target) => { calls.route++; if (onRoute) await onRoute(target); else route = target; },
			model: { can_create: () => true, with_doctype: (_name, callback) => callback() } } };
	const window = {}, frames = [];
	const document = { createElement: name => {
		const node = new Element();
		if (name === "iframe") { node.contentWindow = child; frames.push(node); }
		return node;
	} };
	vm.runInNewContext(fs.readFileSync("jifeng_erp/public/js/jifeng_classic_bridge.js", "utf8"), {
		window, document, __: text => text,
		setInterval: callback => { const id = ++sequence; timers.set(id, callback); return id; },
		clearInterval: id => timers.delete(id),
	});
	const bridge = Object.create(window.JifengClassicBridge.prototype);
	const host = new Element(), entry = bridge.mount(host, spec);
	const list = doctype => ({ doctype,
		filter_area: { clear: async value => { assert.equal(value, false); calls.clear++; } },
		refresh: async () => { calls.refresh++; if (onRefresh) onRefresh(); } });
	return { child, host, entry, calls, frames,
		start() { frames[0].events.load(); },
		boot() { child.frappe.app = {}; child.frappe.container = {}; },
		setRoute(value) { route = value; },
		routeAction(callback) { onRoute = callback; }, refreshAction(callback) { onRefresh = callback; }, list,
		async tick() {
			const pending = [...timers.values()].map(callback => callback());
			await Promise.resolve(); await Promise.resolve();
			return pending;
		} };
}

async function main() {
	const page = fixture({ title: "Synthetic Page", kind: "page", route: ["synthetic-page"] });
	page.start(); await page.tick();
	assert.equal(page.calls.route, 0, "loaded functions are not completed Application startup");
	page.boot(); await page.tick();
	assert.equal(page.calls.route, 0, "native null current_route means initial parsing is still pending");
	page.setRoute(["Workspaces", "private", "synthetic"]);
	await Promise.all(await page.tick());
	assert.equal(await page.entry.ready, true); assert.equal(page.calls.route, 1);
	assert.equal(page.frames[0].classes.has("is-ready"), true);

	const home = fixture({ title: "Synthetic List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	home.boot(); home.setRoute([""]); home.child.cur_list = home.list("Synthetic");
	home.start(); const fromHome = await home.tick(); await Promise.all(await home.tick());
	await Promise.all(fromHome);
	assert.equal(await home.entry.ready, true, "native v16 Home's completed [''] route must allow the target list");
	assert.equal(home.calls.route, 1); assert.equal(home.calls.clear, 1); assert.equal(home.calls.refresh, 1);

	const wrong = fixture({ title: "Synthetic List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	wrong.boot(); wrong.setRoute(["Workspaces"]); wrong.child.cur_list = wrong.list("Synthetic");
	wrong.routeAction(() => wrong.setRoute(["Workspaces"]));
	wrong.start(); await Promise.all(await wrong.tick());
	assert.equal(await wrong.entry.ready, false, "a hidden stale cur_list cannot prove the target opened");
	assert.equal(wrong.calls.clear, 0); assert.equal(wrong.calls.refresh, 0);
	assert.equal(wrong.frames[0].classes.has("is-ready"), false);
	assert.equal(wrong.host.children[0].textContent, "页面未能打开，请关闭后重新打开。");

	const changed = fixture({ title: "Synthetic List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	changed.boot(); changed.setRoute(["Workspaces"]); changed.child.cur_list = changed.list("Synthetic");
	changed.refreshAction(() => changed.setRoute(["Workspaces"]));
	changed.start(); const pending = await changed.tick();
	await Promise.all(await changed.tick()); await Promise.all(pending);
	assert.equal(await changed.entry.ready, false, "a late initial redirect must not publish a wrong page as ready");
	assert.equal(changed.calls.clear, 1); assert.equal(changed.calls.refresh, 1);
	assert.equal(changed.frames[0].classes.has("is-ready"), false);

	const list = fixture({ title: "Synthetic List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	list.boot(); list.setRoute(["Workspaces"]); list.child.cur_list = list.list("Other Synthetic");
	list.start(); const loading = await list.tick(); await Promise.all(await list.tick());
	assert.equal(list.calls.clear, 0, "wait for this route's list, not an old hidden list");
	list.child.cur_list = list.list("Synthetic");
	await Promise.all(await list.tick()); await Promise.all(loading);
	assert.equal(await list.entry.ready, true); assert.equal(list.calls.route, 1);
	assert.equal(list.calls.clear, 1); assert.equal(list.calls.refresh, 1);

	const closed = fixture({ title: "Synthetic Page", kind: "page", route: ["synthetic-page"] });
	closed.boot(); closed.setRoute(["Workspaces"]);
	let finish; closed.routeAction(() => new Promise(resolve => { finish = resolve; }));
	closed.start(); const routing = await closed.tick();
	closed.entry.stop(); finish(); await Promise.all(routing);
	assert.equal(await closed.entry.ready, false); assert.equal(closed.frames[0].classes.has("is-ready"), false);

	const create = fixture({ title: "Synthetic Create", kind: "create", doctype: "Synthetic" });
	create.boot(); create.setRoute(["Workspaces"]);
	let metadata; create.child.frappe.model.with_doctype = (_name, callback) => { metadata = callback; };
	create.start(); const creating = await create.tick(); create.entry.stop(); metadata(); await Promise.all(creating);
	assert.equal(await create.entry.ready, false); assert.equal(create.calls.create, 0);
	console.log("Classic frame startup: null startup, native empty-name Home, stale/wrong lists, late redirects and close during route/metadata passed (7 protocol cases; no real browser).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
