// Actual temporary-worker component, bridge and shared dispatch in DOM/protocol models.
// No browser, ERP runtime, database, HTTP or business writes are involved.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const root = path.join(__dirname, "../jifeng_erp/public/js");
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };

class Element {
    constructor(tag = "div") {
        this.tag = tag; this.children = []; this.events = new Map(); this.attributes = {}; this._text = "";
        this.value = ""; this.style = {}; this.dataset = {}; this.isConnected = true;
        this.classList = { add() {}, remove() {}, toggle() {} };
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => typeof child === "string" ? child : child.textContent || "").join(""); }
    set innerHTML(_) { throw new Error("Temporary-worker source text must never execute HTML"); }
    append(...nodes) { this.children.push(...nodes); for (const child of nodes) if (child && typeof child === "object") child.parentElement = this; }
    appendChild(node) { this.append(node); return node; }
    replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, action) { this.events.set(name, action); }
    removeEventListener(name) { this.events.delete(name); }
    remove() { this.isConnected = false; }
    descendants(tag) { return this.children.flatMap(child => typeof child === "string" ? [] : [...(child.tag === tag ? [child] : []), ...child.descendants(tag)]); }
}
class Wrapper {
    constructor(element = new Element()) { this.element = element; }
    get() { return this.element; }
    addClass() { return this; }
    on(names, action) { for (const name of names.split(" ")) this.element.events.set(name.split(".")[0], action); return this; }
    off() { this.element.events.clear(); return this; }
    val(value) { if (arguments.length) { this.element.value = value; return this; } return this.element.value; }
}
function fixture() {
    const calls = [], messages = [], personnel = [], opened = [], wageChoices = [], window = { addEventListener() {}, removeEventListener() {} };
    const frappe = {
        defaults: { get_user_default: () => "C-1" }, msgprint: text => messages.push(text),
        ui: { form: { make_control({ df }) {
            const input = new Element("input"), control = { df, value: "", $input: new Wrapper(input),
                get_value() { return this.value; }, refresh() {},
                async set_value(value) { this.value = value; input.value = value; await df.onchange?.(); } };
            return control;
        } } },
        call(options) { const task = deferred(); calls.push({ options, resolve: message => task.resolve({ message }), reject: task.reject }); return task.promise; },
    };
    const context = vm.createContext({ window, document: { createElement: tag => new Element(tag), addEventListener() {} }, frappe, __: value => value });
    for (const file of ["jifeng_temporary_workers.js", "jifeng_classic_bridge.js"])
        vm.runInContext(fs.readFileSync(path.join(root, file), "utf8"), context);
    const bridge = { frames: new Map(), openPersonnel: async (...args) => { personnel.push(args); return {}; },
        open: spec => { opened.push(plain(spec)); },
        selectTemporaryWorker: async (...args) => { wageChoices.push(args); return true; } };
    return { calls, messages, personnel, opened, wageChoices, window, frappe, context, bridge };
}
const row = (extra = {}) => ({ name: "E-1", company: "C-1", status: "Active", employee_name: '<img src=x onerror="bad()">原名',
    employee_number: "00017", department: null, employee_version: "a".repeat(64), supplement_version: "b".repeat(64), ...extra });
const reply = (extra = {}) => ({ readonly: true, scope: "current_temporary_workers", company: "C-1",
    classification_basis: "current_legacy_term_type", historical_qualification_known: false, rows: [row()], ...extra });
const selectionReply = (person = row(), extra = {}) => ({ readonly: true, scope: "current_temporary_workers", company: person.company,
    employee: person.name, eligible: true, employee_version: person.employee_version, supplement_version: person.supplement_version,
    classification_basis: "current_legacy_term_type", historical_qualification_known: false, ...extra });
async function make(mode = "browse") {
    const model = fixture(), host = new Element(), view = new model.window.JifengTemporaryWorkers(host, model.bridge, mode);
    await view.ready; await view.companyControl.set_value("C-1"); return { ...model, view, host };
}
async function populate(model, response = reply()) {
    const before = model.calls.length, pending = model.view.query();
    assert.equal(model.calls.length, before + 1, "one explicit query produces one native read");
    const call = model.calls.at(-1); call.resolve(response); await pending; return call;
}
function verifiedSelection(model, person = model.view.rows[0]) {
    const before = model.calls.length, pending = model.view.select(person), call = model.calls.at(-1);
    assert.equal(model.calls.length, before + 1, "non-wage actions recheck one exact current qualification");
    assert.equal(call.options.type, "GET"); assert.equal(call.options.method, "jifeng_erp.temporary_workers.select_temporary_worker");
    assert.deepEqual(plain(call.options.args), { company: person.company, employee: person.name,
        employee_version: person.employee_version, supplement_version: person.supplement_version });
    return { pending, call };
}
const input = (view, value) => { view.search.value = value; view.search.events.get("input")?.(); };
const typeCompany = (view, value) => {
    view.companyControl.$input.element.value = value;
    for (const action of new Set(view.companyControl.$input.element.events.values())) action();
};
const tests = [], test = (name, body) => tests.push([name, body]);

test("GET scope, masked optional fields and source text never imply historical eligibility", async () => {
    const model = await make(), call = await populate(model);
    assert.equal(call.options.type, "GET");
    assert.equal(call.options.method, "jifeng_erp.temporary_workers.list_temporary_workers");
    assert.deepEqual(plain(call.options.args), { company: "C-1", keyword: "" });
    assert.equal(model.view.rows.length, 1);
    assert.ok(model.view.body.textContent.includes(row().employee_name));
    assert.ok(model.view.body.textContent.includes("00017"), "employee number remains source text");
    assert.ok(model.view.body.textContent.includes("—"), "masked or absent optional fields do not become fabricated values");
    assert.equal(model.view.isDirty(), false); assert.equal(model.view.getCloseBlock(), "");
    const masked = row(); delete masked.employee_number; delete masked.department;
    await populate(model, reply({ rows: [masked] })); assert.equal(model.view.rows.length, 1);
    assert.ok(model.view.body.textContent.includes("—"));
    assert.ok(model.calls.every(item => item.options.type === "GET" && item.options.method.endsWith(".list_temporary_workers")),
        "listing never calls selection, save or payroll calculation");
});

test("wrong scope, classification, company and row versions clear old eligible rows", async () => {
    const invalid = [reply({ readonly: false }), reply({ scope: "all_workers" }), reply({ company: "C-2" }),
        reply({ classification_basis: "historical" }), reply({ historical_qualification_known: true }),
        reply({ rows: [row({ name: "" })] }), reply({ rows: [row({ company: "C-2" })] }),
        reply({ rows: [row({ status: "Left" })] }), reply({ rows: [row({ employee_name: null })] }),
        reply({ rows: [row({ employee_version: "" })] }), reply({ rows: [row({ employee_version: "short" })] }),
        reply({ rows: [row({ employee_version: "A".repeat(64) })] }), reply({ rows: [row({ supplement_version: "short" })] }),
        reply({ rows: [row({ supplement_version: "B".repeat(64) })] }), reply({ rows: [row(), row()] })];
    for (const value of invalid) {
        const model = await make(); await populate(model); await populate(model, value);
        assert.equal(model.view.rows.length, 0, "an invalid receipt must not preserve old actionable eligibility");
    }
    const denied = await make(); await populate(denied);
    const pending = denied.view.query(); denied.calls.at(-1).reject(new Error("native permission changed")); await pending;
    assert.equal(denied.view.rows.length, 0);
});

test("typed company, keywords, reordered reads and disposal invalidate delayed receipts", async () => {
    const changed = await make(), pending = changed.view.query(), old = changed.calls.at(-1);
    input(changed.view, "变更"); old.resolve(reply()); await pending; assert.equal(changed.view.rows.length, 0);
    const fresh = changed.view.query(); assert.equal(changed.calls.at(-1).options.args.keyword, "变更");
    changed.calls.at(-1).resolve(reply({ rows: [row({ name: "E-2" })] })); await fresh;
    const typed = await make(), typedPending = typed.view.query(), typedCall = typed.calls.at(-1);
    typeCompany(typed.view, "C-2"); typedCall.resolve(reply()); await typedPending;
    assert.equal(typed.view.rows.length, 0);
    const count = typed.calls.length; await typed.view.query(); assert.equal(typed.calls.length, count, "typed Company cannot reuse its old selected value");
    const company = await make(), companyPending = company.view.query(), companyRead = company.calls.at(-1);
    await company.view.companyControl.set_value("C-2"); companyRead.resolve(reply()); await companyPending; assert.equal(company.view.rows.length, 0);
    const reordered = await make(), first = reordered.view.query(), firstRead = reordered.calls.at(-1);
    reordered.view.invalidate(); const second = reordered.view.query(), secondRead = reordered.calls.at(-1);
    secondRead.resolve(reply({ rows: [row({ name: "E-NEW" })] })); await second;
    firstRead.resolve(reply()); await first; assert.equal(reordered.view.rows[0].name, "E-NEW");
    assert.equal(reordered.view.loading, false);
    const owner = await make(), oldPending = owner.view.query(), oldRead = owner.calls.at(-1);
    owner.view.invalidate(); const ownedPending = owner.view.query(), ownedRead = owner.calls.at(-1);
    oldRead.reject(new Error("old read denied")); await oldPending;
    assert.equal(owner.view.loading, true, "older finally cannot release a newer request's loading state");
    ownedRead.resolve(reply({ rows: [row({ name: "E-OWNER" })] })); await ownedPending;
    assert.equal(owner.view.rows[0].name, "E-OWNER"); assert.equal(owner.view.loading, false);
    const destroyed = await make(), last = destroyed.view.query(), lastRead = destroyed.calls.at(-1);
    destroyed.view.destroy(); lastRead.resolve(reply()); await last; assert.equal(destroyed.view.rows.length, 0);
});

test("four modes reuse exact native sources and only archive opens a new temporary draft", async () => {
    for (const mode of ["archive", "browse", "details", "wage"]) {
        const model = await make(mode); await populate(model);
        const shown = model.view.rows[0], callsBefore = model.calls.length;
        await model.view.select({ ...shown });
        assert.equal(model.personnel.length + model.opened.length + model.wageChoices.length, 0, "an unlisted row copy cannot become an action source");
        let action;
        if (mode === "wage") {
            action = model.view.select(shown); assert.equal(model.calls.length, callsBefore, "wage uses the bridge/workbench qualification read without a duplicate component RPC");
        } else {
            const check = verifiedSelection(model, shown); action = check.pending;
            assert.equal(model.personnel.length + model.opened.length, 0, "native handoff waits for the current qualification receipt");
            check.call.resolve(selectionReply(shown));
        }
        assert.equal(await action, true);
        if (["archive", "browse"].includes(mode)) {
            assert.equal(model.personnel[0][0], shown.name); assert.equal(typeof model.personnel[0][1].stillCurrent, "function");
            assert.deepEqual(Object.keys(model.personnel[0][1]), ["stillCurrent"], "existing native Employee opens with a guard and no copied personnel values");
        }
        if (mode === "details") {
            const spec = model.opened[0]; assert.equal(spec.doctype, "Jifeng Wage Entry"); assert.equal(spec.kind, "list");
            assert.deepEqual(spec.filters, { company: "C-1", employee: "E-1" }); assert.equal(spec.hideCreate, true);
            assert.ok(spec.title.includes("C-1") && spec.title.includes("E-1"), "native tab identity cannot reuse a different company's cached employee list");
        }
        if (mode === "wage") {
            const [company, person, current] = model.wageChoices[0]; assert.equal(company, "C-1");
            assert.deepEqual(plain(person), plain(shown)); assert.equal(typeof current, "function");
        }
        const beforeNew = model.personnel.length; await model.view.openNew();
        if (mode === "archive") {
            assert.deepEqual(plain(model.personnel.at(-1)), [null, { company: "C-1", term_type: "临时" }]);
            assert.equal(typeof model.personnel.at(-1)[1].stillCurrent, "function");
        }
        else assert.equal(model.personnel.length, beforeNew, "only the archive entry starts a native personnel draft");
    }
    const missing = await make("details"); await populate(missing);
    missing.view.rows[0].name = ""; await missing.view.select(missing.view.rows[0]);
    assert.equal(missing.opened.length, 0, "details cannot open an unscoped wage list without Employee");
    const unqueried = await make("archive"); await unqueried.view.openNew();
    assert.deepEqual(plain(unqueried.personnel[0]), [null, { company: "C-1", term_type: "临时" }]);
    assert.equal(typeof unqueried.personnel[0][1].stillCurrent, "function");
    assert.equal(unqueried.calls.length, 0, "a new temporary draft needs selected Company but no roster query");
    typeCompany(unqueried.view, "C-2"); await unqueried.view.openNew();
    assert.equal(unqueried.personnel.length, 1, "unconfirmed typed Company cannot leak the old company into a new draft");
});

test("non-wage native handoff rejects denied permission and changed exact qualification receipts", async () => {
    const mismatches = [{ readonly: false }, { scope: "all_workers" }, { company: "C-2" }, { employee: "E-OTHER" }, { eligible: false },
        { employee_version: "c".repeat(64) }, { supplement_version: "c".repeat(64) }, { employee_version: undefined },
        { classification_basis: "historical" }, { historical_qualification_known: true }];
    for (const mode of ["archive", "browse", "details"]) {
        for (const changes of [...mismatches, null]) {
            const model = await make(mode); await populate(model); const person = model.view.rows[0], check = verifiedSelection(model, person);
            if (changes === null) check.call.reject(new Error("current native qualification permission denied"));
            else check.call.resolve(selectionReply(person, changes));
            assert.equal(await check.pending, false);
            assert.equal(model.personnel.length + model.opened.length, 0, "an unconfirmed current qualification never opens Employee or Wage Entry");
            assert.equal(model.view.rows.length, 0, "denied or changed current eligibility cannot retain actionable list rows");
        }
    }
});

test("non-wage delayed qualification cannot cross changed context, employee versions or disposal", async () => {
    for (const mode of ["archive", "browse", "details"]) {
        for (const change of ["company", "typed", "keyword", "destroy", "version"]) {
            const model = await make(mode); await populate(model); const person = model.view.rows[0], exact = selectionReply(person);
            const check = verifiedSelection(model, person), count = model.calls.length;
            await model.view.select(person); assert.equal(model.calls.length, count, "one qualification read protects against parallel native handoffs");
            if (change === "company") await model.view.companyControl.set_value("C-2");
            if (change === "typed") typeCompany(model.view, "C-2");
            if (change === "keyword") input(model.view, "changed");
            if (change === "destroy") model.view.destroy();
            if (change === "version") person.supplement_version = "c".repeat(64);
            check.call.resolve(exact); assert.equal(await check.pending, false);
            assert.equal(model.personnel.length + model.opened.length, 0, `${change} invalidates a late non-wage native handoff`);
        }
    }
});

test("native personnel read and new draft retain a live guard until handoff finishes", async () => {
    for (const action of ["read", "new"]) {
        for (const change of ["company", "typed", "destroy"]) {
            const model = await make("archive"), finish = deferred(); let options, accepted = 0;
            if (action === "read") await populate(model);
            model.bridge.openPersonnel = async (_, passed) => { options = passed; await finish.promise; if (passed.stillCurrent()) accepted++; return passed.stillCurrent() ? {} : false; };
            let pending;
            if (action === "read") {
                const person = model.view.rows[0], check = verifiedSelection(model, person); pending = check.pending; check.call.resolve(selectionReply(person));
            } else pending = model.view.openNew();
            await flush(); assert.equal(typeof options.stillCurrent, "function"); assert.equal(options.stillCurrent(), true);
            if (action === "new") assert.deepEqual(plain(options), { company: "C-1", term_type: "临时" });
            else assert.deepEqual(plain(options), {});
            model.view.setVisible(false); assert.equal(options.stillCurrent(), true, "navigation hiding preserves the requested native handoff");
            if (change === "company") await model.view.companyControl.set_value("C-2");
            if (change === "typed") typeCompany(model.view, "C-2");
            if (change === "destroy") model.view.destroy();
            assert.equal(options.stillCurrent(), false, `${action} guard notices ${change} before delayed native form construction`);
            finish.resolve(); assert.equal(await pending, false); assert.equal(accepted, 0);
        }
    }
});

test("wage callback refusal or failure invalidates rows and retains visible failure text", async () => {
    for (const callback of [async () => false, async () => { throw new Error("synthetic dirty/unknown block"); }]) {
        const model = await make("wage"); await populate(model); model.bridge.selectTemporaryWorker = callback;
        await model.view.select(model.view.rows[0]);
        assert.equal(model.view.rows.length, 0); assert.ok(model.view.status.textContent, "failure is visible after eligibility is cleared");
        assert.equal(model.opened.length, 0, "bridge failure never falls back to a second wage page");
    }
});

test("source guards cancel delayed callbacks, but cached-page hiding caused by navigation stays valid", async () => {
    for (const change of ["company", "keyword", "destroy", "version"]) {
        const model = await make("wage"); await populate(model); const task = deferred(); let current, accepted = 0, callbacks = 0;
        model.bridge.selectTemporaryWorker = async (_, __, guard) => { callbacks++; current = guard; await task.promise; if (guard()) accepted++; return guard(); };
        const pending = model.view.select(model.view.rows[0]); await flush(); assert.equal(current(), true);
        await model.view.select(model.view.rows[0]); assert.equal(callbacks, 1, "one pending action blocks parallel callbacks");
        if (change === "company") await model.view.companyControl.set_value("C-2");
        if (change === "keyword") input(model.view, "changed");
        if (change === "destroy") model.view.destroy();
        if (change === "version") model.view.rows[0].supplement_version = "c".repeat(64);
        assert.equal(current(), false, `${change} cancels the exact action source`);
        task.resolve(); await pending; assert.equal(accepted, 0, "late callback cannot refill a changed or closed context");
    }
    const newer = await make("wage"); await populate(newer); const oldAction = deferred();
    newer.bridge.selectTemporaryWorker = () => oldAction.promise;
    const choosing = newer.view.select(newer.view.rows[0]); input(newer.view, "changed");
    await populate(newer, reply({ rows: [row({ name: "E-LATEST" })] }));
    oldAction.resolve(false); await choosing;
    assert.equal(newer.view.rows[0].name, "E-LATEST", "stale callback failure cannot clear a newer qualified result");
    const hidden = await make("wage"); await populate(hidden); let hiddenCurrent;
    hidden.bridge.selectTemporaryWorker = async (_, __, guard) => { hidden.view.setVisible(false); hiddenCurrent = guard(); return guard(); };
    await hidden.view.select(hidden.view.rows[0]); assert.equal(hiddenCurrent, true, "opening the wage tab may hide its cached source without cancelling the requested choice");
});

function bridgeModel() {
    const model = fixture(), bridge = Object.create(model.window.JifengClassicBridge.prototype), key = "formal:工资:工资录入";
    let dirty = false, block = "", sourceCurrent = true, routeTask;
    const chosen = [], routes = [], opened = [];
    const child = { frappe: { async set_route(...route) { routes.push(route); if (routeTask) await routeTask.promise; } },
        jifeng_wage_workbench: { is_dirty: () => dirty, get_close_block: () => block,
            async chooseTemporaryWorker(...args) { chosen.push(args); return args[2](); }, async choosePieceWorker() { return true; } } };
    const entry = { iframe: { contentWindow: child, isConnected: true }, ready: Promise.resolve(true) };
    bridge.frames = new Map([[key, entry]]);
    bridge.open = spec => { opened.push(plain(spec)); if (!bridge.frames.has(key)) bridge.frames.set(key, entry); };
    return { ...model, bridge, key, child, entry, chosen, routes, opened, current: () => sourceCurrent,
        dirty: value => { dirty = value; }, block: value => { block = value; },
        stale: () => { sourceCurrent = false; }, deferRoute: () => { routeTask = deferred(); return routeTask; } };
}

test("native wage bridge protects cached drafts, exact frame and source after every wait", async () => {
    for (const state of ["dirty", "unknown"]) {
        const model = bridgeModel(); if (state === "dirty") model.dirty(true); else model.block("保存结果尚未核实");
        assert.equal(await model.bridge.selectTemporaryWorker("C-1", row(), model.current), false);
        assert.equal(model.opened.length, 0); assert.equal(model.chosen.length, 0);
    }
    const success = bridgeModel(); assert.equal(await success.bridge.selectTemporaryWorker("C-1", row(), success.current), true);
    assert.equal(success.opened[0].title, "工资录入"); assert.deepEqual(success.routes[0], ["jifeng-wage-workbench"]);
    assert.deepEqual(plain(success.chosen[0].slice(0, 2)), ["C-1", row()]); assert.equal(success.chosen[0][2](), true);
    for (const change of ["dirty", "unknown", "closed", "disconnected", "source"]) {
        const model = bridgeModel(), ready = deferred(); model.entry.ready = ready.promise;
        const pending = model.bridge.selectTemporaryWorker("C-1", row(), model.current); await flush();
        assert.equal(await model.bridge.selectTemporaryWorker("C-1", row({ name: "E-2" }), model.current), false);
        assert.equal(await model.bridge.selectPieceWorker("C-1", "E-2"), false, "piece and temporary selections share one wage destination lock");
        if (change === "dirty") model.dirty(true); if (change === "unknown") model.block("unknown");
        if (change === "closed") model.bridge.frames.delete(model.key); if (change === "disconnected") model.entry.iframe.isConnected = false;
        if (change === "source") model.stale(); ready.resolve(true);
        assert.equal(await pending, false, `${change} during frame ready cannot enter the wage context`); assert.equal(model.chosen.length, 0);
    }
    for (const change of ["dirty", "unknown", "closed", "source"]) {
        const model = bridgeModel(), route = model.deferRoute(), pending = model.bridge.selectTemporaryWorker("C-1", row(), model.current);
        await flush(); assert.equal(model.routes.length, 1);
        if (change === "dirty") model.dirty(true); if (change === "unknown") model.block("unknown");
        if (change === "closed") model.bridge.frames.delete(model.key); if (change === "source") model.stale(); route.resolve();
        assert.equal(await pending, false, `${change} during native route must be rechecked`); assert.equal(model.chosen.length, 0);
    }
    const locked = bridgeModel(); locked.bridge.selectingPieceWorker = true;
    assert.equal(await locked.bridge.selectTemporaryWorker("C-1", row(), locked.current), false); assert.equal(locked.opened.length, 0);
    const failed = bridgeModel(); failed.entry.ready = Promise.resolve(false);
    assert.equal(await failed.bridge.selectTemporaryWorker("C-1", row(), failed.current), false); failed.entry.ready = Promise.resolve(true);
    assert.equal(await failed.bridge.selectTemporaryWorker("C-1", row(), failed.current), true, "failed ready releases its own selection lock");
    const applying = bridgeModel(), finish = deferred(); let destinationCurrent;
    applying.child.jifeng_wage_workbench.chooseTemporaryWorker = async (_, __, guard) => { destinationCurrent = guard; await finish.promise; return guard(); };
    const duringChoice = applying.bridge.selectTemporaryWorker("C-1", row(), applying.current); await flush();
    assert.equal(destinationCurrent(), true); applying.bridge.frames.delete(applying.key); assert.equal(destinationCurrent(), false);
    finish.resolve(); assert.equal(await duringChoice, false, "a removed wage frame invalidates the downstream qualification callback");
});

test("personnel bridge passes temporary defaults directly to the existing native editor", async () => {
    const model = fixture(), received = [], bridge = Object.create(model.window.JifengClassicBridge.prototype);
    model.window.JifengPersonnelArchive = class { open(...args) { received.push(args); return Promise.resolve(true); } };
    const defaults = { company: "C-1", term_type: "临时", stillCurrent: () => true }; await bridge.openPersonnel(undefined, defaults); await bridge.openPersonnel("E-1");
    assert.equal(received[0][0], undefined); assert.equal(received[0][1], defaults);
    assert.equal(received[0][1].stillCurrent, defaults.stillCurrent);
    assert.equal(received[1][0], "E-1");
});

test("all four shared workspace entries remain partial and mount the same component", async () => {
    const model = fixture(); vm.runInContext(fs.readFileSync(path.join(root, "jifeng_workspace_core.js"), "utf8"), model.context);
    const shared = model.window.JifengERPWorkspace;
    for (const [name, mode] of [["临工档案", "archive"], ["临工浏览", "browse"], ["临工", "wage"], ["临工明细", "details"]]) {
        assert.equal(shared.availability("工资", name), "partial");
        const opened = [], workspace = Object.create(shared.Workspace.prototype);
        workspace.formalBridge = { open: spec => opened.push(spec) }; workspace.addTab = () => { throw new Error("Temporary entry fell back to preview"); };
        workspace.openPage(name, "工资"); assert.equal(opened.length, 1); assert.equal(opened[0].temporaryWorkers, mode);
        assert.equal(opened[0].availability, "partial");
        const entry = model.window.JifengClassicBridge.prototype.mount.call(model.bridge, new Element(), opened[0]); await entry.ready;
        assert.ok(entry.workbench instanceof model.window.JifengTemporaryWorkers); assert.equal(entry.workbench.mode, mode);
        assert.equal(typeof entry.workbench.setVisible, "function"); entry.stop(); assert.equal(entry.workbench.disposed, true);
    }
});

(async () => {
    for (const [name, body] of tests) { try { await body(); } catch (error) { error.message = name + ": " + error.message; throw error; } }
    console.log(JSON.stringify({ ok: true, models: tests.length, browser_verified: false, network_used: false }));
})().catch(error => { console.error(error); process.exitCode = 1; });
