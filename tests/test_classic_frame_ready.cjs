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
		timerCount() { return timers.size; },
		boot() { child.frappe.app = {}; child.frappe.container = {}; },
		setRoute(value) { route = value; },
		routeAction(callback) { onRoute = callback; }, refreshAction(callback) { onRefresh = callback; }, list,
		async tick() {
			const pending = [...timers.values()].map(callback => callback());
			for (let i = 0; i < 8; i++) await Promise.resolve();
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

	// Native router.set_route resolves through after_ajax before the jqXHR
	// .then / async parse continuation necessarily publishes current_route.
	for (const spec of [
		{ title: "Cold Page", kind: "page", route: ["synthetic-page"] },
		{ title: "Cold Report", kind: "page", route: ["query-report", "Synthetic Report"] },
		{ title: "Cold List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] },
		{ title: "Cold Filtered List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"], filters: { status: "Open" } },
	]) {
		const cold = fixture(spec);
		cold.boot(); cold.setRoute([""]); cold.child.cur_list = cold.list("Synthetic");
		cold.routeAction(() => {}); // Promise resolves while the old route still exists.
		cold.start(); const opening = await cold.tick(); await cold.tick();
		assert.equal(cold.calls.route, 1);
		assert.equal(cold.frames[0].classes.has("is-ready"), false, "route Promise is not target readiness");
		assert.equal(cold.calls.clear, 0, "a stale matching list cannot bypass target parsing");
		cold.setRoute(null); await cold.tick();
		assert.equal(cold.frames[0].classes.has("is-ready"), false, "null remains a pending route");
		cold.setRoute(spec.route);
		for (let i = 0; i < 4; i++) await cold.tick();
		await Promise.all(opening);
		assert.equal(await cold.entry.ready, true, spec.title);
		assert.equal(cold.calls.clear, spec.kind === "list" && !spec.filters ? 1 : 0);
		assert.equal(cold.timerCount(), 0);
	}

	const wrong = fixture({ title: "Synthetic List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	wrong.boot(); wrong.setRoute(["Workspaces"]); wrong.child.cur_list = wrong.list("Synthetic");
	wrong.routeAction(() => wrong.setRoute(["Workspaces"]));
	wrong.start(); const wrongOpening = await wrong.tick();
	for (let i = 0; i < 300; i++) await wrong.tick();
	await Promise.all(wrongOpening);
	assert.equal(await wrong.entry.ready, false, "a hidden stale cur_list cannot prove the target opened");
	assert.equal(wrong.calls.clear, 0); assert.equal(wrong.calls.refresh, 0);
	assert.equal(wrong.frames[0].classes.has("is-ready"), false);
	assert.equal(wrong.host.children[0].textContent, "页面未能打开，请关闭后重新打开。");
	assert.equal(wrong.timerCount(), 0, "target timeout clears its poll");

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

	const initializing = fixture({ title: "Initializing List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	initializing.boot(); initializing.setRoute([""]); initializing.child.cur_list = initializing.list("Synthetic");
	let finishInit;
	initializing.child.cur_list.init_promise = new Promise(resolve => { finishInit = resolve; });
	initializing.start(); const initOpening = await initializing.tick();
	await initializing.tick(); await initializing.tick();
	assert.equal(initializing.calls.clear, 0, "FilterArea alone does not prove native result/freeze controls exist");
	assert.equal(initializing.calls.refresh, 0);
	finishInit();
	await Promise.all(initOpening);
	assert.equal(await initializing.entry.ready, true);
	assert.equal(initializing.calls.clear, 1); assert.equal(initializing.calls.refresh, 1);

	const changedInit = fixture({ title: "Changed List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	changedInit.boot(); changedInit.setRoute([""]); changedInit.child.cur_list = changedInit.list("Synthetic");
	let finishChangedInit;
	changedInit.child.cur_list.init_promise = new Promise(resolve => { finishChangedInit = resolve; });
	changedInit.start(); const changedInitOpening = await changedInit.tick();
	await changedInit.tick(); await changedInit.tick();
	changedInit.child.cur_list = changedInit.list("Synthetic"); finishChangedInit();
	await Promise.all(changedInitOpening);
	assert.equal(await changedInit.entry.ready, false, "a replacement list cannot reuse an earlier initialization");
	assert.equal(changedInit.calls.clear, 0); assert.equal(changedInit.calls.refresh, 0);

	const rejectedInit = fixture({ title: "Rejected Initialization", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	rejectedInit.boot(); rejectedInit.setRoute([""]); rejectedInit.child.cur_list = rejectedInit.list("Synthetic");
	let rejectInit;
	rejectedInit.child.cur_list.init_promise = new Promise((_resolve, reject) => { rejectInit = reject; });
	rejectedInit.start(); const rejectedInitOpening = await rejectedInit.tick(); await rejectedInit.tick();
	rejectInit(new Error("Native initialization failed")); await Promise.all(rejectedInitOpening);
	assert.equal(await rejectedInit.entry.ready, false); assert.equal(rejectedInit.calls.refresh, 0);

	const closedInit = fixture({ title: "Closed Initialization", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	closedInit.boot(); closedInit.setRoute([""]); closedInit.child.cur_list = closedInit.list("Synthetic");
	let resumeClosedInit;
	closedInit.child.cur_list.init_promise = new Promise(resolve => { resumeClosedInit = resolve; });
	closedInit.start(); const closedInitOpening = await closedInit.tick(); await closedInit.tick();
	closedInit.entry.stop(); assert.equal(await closedInit.entry.ready, false);
	resumeClosedInit(); await Promise.all(closedInitOpening);
	assert.equal(closedInit.calls.clear, 0); assert.equal(closedInit.calls.refresh, 0);
	assert.equal(closedInit.frames[0].classes.has("is-ready"), false);

	const closedRoute = fixture({ title: "Closed Pending Route", kind: "page", route: ["synthetic-page"] });
	closedRoute.boot(); closedRoute.setRoute([""]); closedRoute.routeAction(() => {});
	closedRoute.start(); const pendingRoute = await closedRoute.tick(); await closedRoute.tick();
	closedRoute.entry.stop(); await Promise.all(pendingRoute);
	assert.equal(await closedRoute.entry.ready, false); assert.equal(closedRoute.timerCount(), 0);
	assert.equal(closedRoute.frames[0].classes.has("is-ready"), false);

	const closedList = fixture({ title: "Closed Pending List", kind: "list", doctype: "Synthetic", route: ["List", "Synthetic"] });
	closedList.boot(); closedList.setRoute([""]); closedList.child.cur_list = closedList.list("Other Synthetic");
	closedList.start(); const pendingList = await closedList.tick(); await closedList.tick();
	closedList.entry.stop(); await Promise.all(pendingList);
	assert.equal(await closedList.entry.ready, false); assert.equal(closedList.timerCount(), 0);
	assert.equal(closedList.calls.clear, 0); assert.equal(closedList.calls.refresh, 0);

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
	console.log("Classic frame startup: native route Promise/parse ordering, all route kinds, init barrier/rejection, stale/wrong/replaced lists, late redirects and cancellation passed (17 protocol cases; no real browser).");
}
const watchdog = setTimeout(() => {
	console.error("Classic frame protocol cases did not settle"); process.exitCode = 1;
}, 2000);
main().then(() => clearTimeout(watchdog), error => {
	clearTimeout(watchdog); console.error(error); process.exitCode = 1;
});
