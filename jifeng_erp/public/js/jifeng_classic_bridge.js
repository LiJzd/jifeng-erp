/* Keep native, permission-controlled pages inside the classic workspace. */
window.JifengClassicBridge = class JifengClassicBridge {
	constructor(workspace) {
		this.workspace = workspace;
		this.frames = new Map();
		this.layer = document.createElement("div");
		this.layer.className = "jfe-formal-layer";
		// This sibling stays mounted while the classic shell rerenders. Moving an
		// iframe between containers would reload it and discard unsaved input.
		workspace.host.parentElement.appendChild(this.layer);
		this.align = this.align.bind(this);
		window.addEventListener("resize", this.align);
		document.addEventListener("fullscreenchange", this.align);
		window.addEventListener("scroll", this.align, true);
		frappe.router.on("change", this.align);
		this.observer = new ResizeObserver(this.align);
		this.observer.observe(workspace.host);
		this.reportRefreshClick = this.reportRefreshClick.bind(this);
		workspace.host.addEventListener("click", this.reportRefreshClick);
	}

	open(spec) {
		const key = "formal:" + spec.module + ":" + spec.title;
		const existing = this.workspace.tabs?.find(tab => tab.key === key);
		if (existing && spec.module === "总经理" && spec.kind === "report" &&
			JSON.stringify(existing.spec?.filters) !== JSON.stringify(spec.filters)) {
			// A changed dashboard scope replaces this read-only report's iframe;
			// reusing the old tab otherwise keeps its previous company/period.
			const entry = this.frames.get(key);
			entry?.stop?.(); entry?.container.remove();
			this.frames.delete(key);
			existing.spec = spec;
		}
		this.workspace.addTab({ key, title: spec.title, module: spec.module, kind: "formal", spec });
	}

	content(tab) {
		const escape = (value) => this.workspace.esc(value);
		const reportRefresh = tab.spec?.kind === "report" && tab.spec.route?.[0] === "query-report"
			? '<div style="display:flex;justify-content:flex-end;padding:4px 0 8px"><button type="button" class="jfe-button small" data-jfe-report-refresh="' +
				escape(tab.key) + '">刷新报表</button></div>' : "";
		return '<section class="jfe-formal-page">' + reportRefresh +
			'<div class="jfe-formal-anchor" aria-label="' + escape(tab.title) + '"></div></section>';
	}

	reportRefreshClick(event) {
		const button = event.target.closest?.("[data-jfe-report-refresh]");
		if (!button || !this.workspace.host.contains(button)) return;
		event.preventDefault();
		event.stopPropagation();
		this.refreshReport(button.dataset.jfeReportRefresh);
	}

	refreshReport(key) {
		const tab = this.workspace.tabs.find(item => item.key === key);
		if (!tab || this.workspace.activeKey !== key || tab.spec?.kind !== "report" ||
			tab.spec.route?.[0] !== "query-report") return false;
		const entry = this.frames.get(key), iframe = entry?.iframe;
		if (!iframe || iframe.isConnected === false || !iframe.classList?.contains("is-ready")) return false;
		const child = iframe.contentWindow, route = child?.frappe?.get_route?.();
		const report = child?.frappe?.query_report, QueryReport = child?.frappe?.views?.QueryReport;
		if (!QueryReport || !(report instanceof QueryReport) ||
			report.report_name !== tab.spec.route[1] || route?.[0] !== "query-report" ||
			route?.[1] !== tab.spec.route[1] || typeof report.setup_progress_bar !== "function" ||
			typeof report.refresh !== "function") return false;
		// Match Frappe's native Reload Report action: QueryReport.refresh() reruns
		// with the visible filter values. refresh_report() would reset route filters.
		report.setup_progress_bar();
		report.refresh();
		return true;
	}

	sync() {
		const keys = new Set(this.workspace.tabs.map((tab) => tab.key));
		for (const [key, entry] of this.frames) {
			if (!keys.has(key)) {
				entry.stop?.();
				entry.container.remove();
				this.frames.delete(key);
			}
		}
		const tab = this.workspace.tabs.find((item) => item.key === this.workspace.activeKey);
		if (tab?.kind === "formal" && !this.frames.has(tab.key)) {
			const container = document.createElement("div");
			container.className = "jfe-formal-frame";
			this.layer.appendChild(container);
			const entry = this.mount(container, tab.spec);
			this.frames.set(tab.key, { ...entry, container });
		}
		this.align();
	}

	align() {
		const active = this.workspace.activeKey;
		const anchor = this.workspace.host.querySelector(".jfe-formal-anchor");
		const visible = anchor && anchor.getClientRects().length && frappe.get_route()[0] === (this.workspace.pageName || "jifeng-legacy");
		for (const [key, entry] of this.frames) {
			const frameVisible = !!(visible && key === active);
			entry.container.hidden = !frameVisible;
			entry.workbench?.setVisible?.(frameVisible);
		}
		this.layer.hidden = !visible;
		if (!visible) return;
		const rect = anchor.getBoundingClientRect();
		Object.assign(this.layer.style, { top: rect.top + "px", left: rect.left + "px",
			width: rect.width + "px", height: rect.height + "px" });
	}

	canClose(key) {
		const entry = this.frames.get(key);
		if (this.supplierPaymentCloseBlock(entry)) return false;
		if (entry?.readOnly) return true;
		return entry?.workbench ? !entry.workbench.isDirty() : !this.isDirty(entry?.iframe);
	}

	closeBlock(key) {
		const entry = this.frames.get(key);
		const paymentBlock = this.supplierPaymentCloseBlock(entry);
		if (paymentBlock) return paymentBlock;
		if (entry?.readOnly) return "";
		return entry?.workbench ? entry.workbench.getCloseBlock() : this.frameCloseBlock(entry?.iframe);
	}

	supplierPaymentCloseBlock(entry) {
		const state = entry?.supplierPayment;
		if (!state) return "";
		// A late native route may finish after our wait expired. Only its exact
		// draft form releases the pending guard; native dirty checks then apply.
		if (state.phase === "unresolved" && state.target && this.nativeForm(entry, state.key, state.target)) {
			state.release?.(); entry.supplierPayment = null; return "";
		}
		return state.phase === "unresolved" ? "原生收付款结果尚未确认，请先在此窗口核对结果。" : "正在读取原生发票或准备收付款草稿，请稍候。";
	}

	nativeForm(entry, key, target) {
		if (this.frames.get(key) !== entry || entry.iframe?.isConnected === false) return null;
		const child = entry.iframe?.contentWindow, route = child?.frappe?.get_route?.(), frm = child?.cur_frm;
		return Array.isArray(route) && route.length === 3 && route[0] === "Form" && route[1] === target.doctype && route[2] === target.name &&
			frm?.doctype === target.doctype && frm.docname === target.name && frm.doc?.doctype === target.doctype && frm.doc.name === target.name &&
			(!target.company || frm.doc.company === target.company) &&
			frm.cscript?.is_onload === false ? frm : null;
	}

	nativeDraft(frame, spec) {
		if (frame?.isConnected === false) return null;
		const child = frame?.contentWindow, route = child?.frappe?.get_route?.(), frm = child?.cur_frm;
		return Array.isArray(route) && route.length === 3 && route[0] === "Form" && route[1] === spec.doctype &&
			route[2] === frm?.docname && frm?.doctype === spec.doctype && frm.doc?.doctype === spec.doctype &&
			frm.doc.name === frm.docname && frm.doc.__islocal && frm.doc.docstatus === 0 &&
			frm.doc.company === spec.values.company && frm.cscript?.is_onload === false ? frm : null;
	}

	waitNativeForm(entry, key, target) {
		return new Promise(resolve => {
			let attempts = 0, timer;
			const finish = value => { clearInterval(timer); resolve(value); };
			const check = () => {
				const frm = this.nativeForm(entry, key, target);
				if (frm) finish(frm);
				else if (this.frames.get(key) !== entry || entry.iframe?.isConnected === false || ++attempts >= 300) finish(null);
			};
			timer = setInterval(check, 50); check();
		});
	}

	watchNativeFormLoad(entry, key, accepts) {
		const child = entry.iframe.contentWindow, hooks = child.frappe.route_hooks;
		if (!hooks) throw new Error("Native form hooks unavailable");
		const previous = hooks.after_load;
		let timer, settle, done = false;
		const promise = new Promise(resolve => { settle = resolve; });
		const finish = frm => {
			if (done) return; done = true; clearTimeout(timer);
			if (hooks.after_load === handler) {
				if (previous) hooks.after_load = previous; else delete hooks.after_load;
			}
			settle(frm);
		};
		const handler = frm => {
			let priorCompleted = false;
			try { previous?.(frm); priorCompleted = true; }
			finally { finish(priorCompleted && this.frames.get(key) === entry && entry.iframe.isConnected !== false && accepts(frm) ? frm : null); }
		};
		hooks.after_load = handler;
		timer = setTimeout(() => finish(null), 15000);
		return { promise, cancel: () => finish(null) };
	}

	supplierPaymentAllowed(entry, key, identity, frm) {
		const child = entry.iframe.contentWindow, doc = frm?.doc;
		return !!frm && this.nativeForm(entry, key, { doctype: "Purchase Invoice", name: identity.name }) === frm &&
			doc.company === identity.company && doc.supplier === identity.supplier && !doc.__islocal && doc.docstatus === 1 && !doc.on_hold &&
			typeof doc.outstanding_amount === "number" && Number.isFinite(doc.outstanding_amount) && doc.outstanding_amount !== 0 &&
			!this.isDirty(entry.iframe) && !!frm.get_docinfo?.()?.permissions?.read &&
			!!child.frappe.model.can_read("Purchase Invoice") && !!child.frappe.model.can_create("Payment Entry") &&
			(!doc.__onload?.make_payment_via_journal_entry || !!child.frappe.model.can_create("Journal Entry")) &&
			frm.cscript?.frm === frm && typeof frm.cscript.make_payment_entry === "function" && typeof frm.cscript.make_mapped_payment_entry === "function";
	}

	async openSupplierPayment(identity) {
		const validName = value => typeof value === "string" && !!value && value === value.trim() &&
			[...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value);
		if (!identity || Object.keys(identity).length !== 3 || !["company", "supplier", "name"].every(field => validName(identity[field]))) return false;
		const spec = { title: "原生收付款：" + identity.name, module: "财务", kind: "form", doctype: "Purchase Invoice",
			route: ["Form", "Purchase Invoice", identity.name] };
		const key = "formal:财务:" + spec.title;
		let entry = this.frames.get(key);
		this.open(spec);
		entry ||= this.frames.get(key);
		if (!entry || this.frames.get(key) !== entry) return false;
		if (entry.supplierPayment || this.frameCloseBlock(entry.iframe) || this.isDirty(entry.iframe)) {
			frappe.msgprint(__("请先核对当前原生窗口中的收付款或未保存内容。")); return false;
		}
		const state = { phase: "opening", key }; entry.supplierPayment = state;
		try {
			if (await entry.ready === false) return false;
			const target = { doctype: "Purchase Invoice", name: identity.name };
			let frm = await this.waitNativeForm(entry, key, target);
			if (!frm || this.isDirty(entry.iframe)) return false;
			const oldDoc = frm.doc;
			const fresh = this.watchNativeFormLoad(entry, key, loaded => loaded === frm && loaded.doc !== oldDoc &&
				loaded.doc?.doctype === target.doctype && loaded.doc.name === target.name);
			try {
				// Native with_doc can leave its Promise pending on a failed GET.
				// The after_load wait owns the timeout and proves actual readiness.
				Promise.resolve(frm.reload_doc()).catch(() => fresh.cancel());
				if (!await fresh.promise) return false;
			} finally { fresh.cancel(); }
			frm = await this.waitNativeForm(entry, key, target);
			if (!this.supplierPaymentAllowed(entry, key, identity, frm)) {
				frappe.msgprint(__("当前发票不符合原生收付款条件，请核对公司、供应商、权限、状态、暂停付款和当前余额。")); return false;
			}
			return await this.makeNativeSupplierPayment(entry, key, identity, frm, state);
		} catch {
			frappe.msgprint(__("原生收付款入口未完成，请在原生窗口核对发票和结果；不会自动重试。")); return false;
		} finally { if (entry.supplierPayment === state && state.phase !== "unresolved") entry.supplierPayment = null; }
	}

	makeNativeSupplierPayment(entry, key, identity, frm, state) {
		const child = entry.iframe.contentWindow, script = frm.cscript, original = script.make_mapped_payment_entry;
		const descriptor = Object.getOwnPropertyDescriptor(script, "make_mapped_payment_entry");
		const nativeEntry = script.make_payment_entry, entryDescriptor = Object.getOwnPropertyDescriptor(script, "make_payment_entry");
		const expectedDoctype = frm.doc.__onload?.make_payment_via_journal_entry ? "Journal Entry" : "Payment Entry";
		return new Promise(resolve => {
			let started = false, done = false, prompt, promptTimer, mappingTimer, mappingLoad;
			const release = () => {
				clearInterval(promptTimer); clearTimeout(mappingTimer); mappingLoad?.cancel();
				if (script.make_mapped_payment_entry === mapped) {
					if (descriptor) Object.defineProperty(script, "make_mapped_payment_entry", descriptor);
					else delete script.make_mapped_payment_entry;
				}
				if (script.make_payment_entry === blockedEntry) {
					if (entryDescriptor) Object.defineProperty(script, "make_payment_entry", entryDescriptor);
					else delete script.make_payment_entry;
				}
				if (state.release === release) delete state.release;
			};
			state.release = release;
			const finish = value => {
				if (done) return; done = true; clearInterval(promptTimer); clearTimeout(mappingTimer); mappingLoad?.cancel();
				// Only this PI controller remains guarded for an unknown result.
				// Do not let its Payment button dispatch another mapping meanwhile.
				if (state.phase !== "unresolved") release();
				resolve(value);
			};
			const knownFailure = () => {
				if (!done) finish(false);
				else if (state.phase === "unresolved") { release(); if (entry.supplierPayment === state) entry.supplierPayment = null; }
			};
			const blockedEntry = () => false;
			const mapped = (...args) => {
				if (started || done) return;
				if (!this.supplierPaymentAllowed(entry, key, identity, frm)) { finish(false); return; }
				started = true; state.phase = "mapping";
				let request, responseDoc;
				const load = mappingLoad = this.watchNativeFormLoad(entry, key, loaded => loaded.doc?.doctype === expectedDoctype &&
					loaded.doc.company === identity.company && loaded.doc.docstatus === 0 && !!loaded.doc.__islocal);
				// The request itself can remain pending even after after_load expires.
				// Bound the caller independently, retaining only the local PI guard.
				mappingTimer = setTimeout(() => { state.phase = "unresolved"; finish(false); }, 15000);
				try {
					request = original.apply(script, args);
					if (!request?.then) throw new Error("Native mapper did not return its request");
					Promise.all([Promise.resolve(request).then(response => {
						const value = response?.message; responseDoc = Array.isArray(value) ? value[0] : value;
						if (response?.exc || responseDoc?.doctype !== expectedDoctype || !responseDoc.name || responseDoc.company !== identity.company)
							throw new Error("Native payment mapping did not return its draft");
						state.target = { doctype: responseDoc.doctype, name: responseDoc.name };
					}), load.promise]).then(async ([, loaded]) => {
						if (done) {
							if (state.phase === "unresolved" && this.nativeForm(entry, key, state.target)) {
								release(); if (entry.supplierPayment === state) entry.supplierPayment = null;
							}
							return;
						}
						const current = loaded && await this.waitNativeForm(entry, key, state.target);
						if (done) return;
						if (!current) state.phase = "unresolved";
						finish(!!current);
					}).catch(() => { load.cancel(); knownFailure(); });
				} catch { load.cancel(); knownFailure(); }
				return request;
			};
			script.make_mapped_payment_entry = mapped;
			// Keep the native Payment button from opening another date prompt or
			// mapping while this exact native invocation is still outstanding.
			script.make_payment_entry = blockedEntry;
			// The native controller discards frappe.prompt's Dialog return value.
			// Capture only this synchronous invocation, retaining native validation,
			// reference-date callback and either Payment Entry / Journal Entry branch.
			const nativePrompt = child.frappe.prompt;
			child.frappe.prompt = (...args) => { prompt = nativePrompt.apply(child.frappe, args); return prompt; };
			try { nativeEntry.call(script); }
			catch { if (started) state.phase = "unresolved"; finish(false); }
			finally { child.frappe.prompt = nativePrompt; }
			if (!started && !done) {
				if (!prompt) { finish(false); return; }
				state.phase = "prompt";
				promptTimer = setInterval(() => {
					if (!started && (!prompt.is_visible || this.frames.get(key) !== entry || entry.iframe.isConnected === false)) finish(false);
				}, 50);
			}
		});
	}

	frameCloseBlock(frame) {
		try { return frame?.contentWindow?.jifeng_wage_workbench?.get_close_block() ||
			frame?.contentWindow?.jifeng_order_material_workbench?.get_close_block() || ""; }
		catch { return ""; }
	}

	isDirty(frame) {
		try {
			const child = frame?.contentWindow;
			const quick = child?.frappe?.quick_entry?.dialog;
			return !!(child?.cur_frm?.is_dirty() || child?.jifeng_wage_workbench?.is_dirty() ||
				child?.jifeng_order_material_workbench?.is_dirty() ||
				quick?.is_visible || quick?.display);
		} catch { return true; }
	}

	openCreate(spec) {
		if (spec.productArchive && window.JifengProductArchive) {
			const key = "formal:产品:产品档案";
			this.open({ title: "产品档案", module: "产品", kind: "list", doctype: "Jifeng Product Profile", productArchive: true });
			const entry = this.frames.get(key);
			return Promise.resolve(entry?.ready).then(() => entry?.workbench?.openEditor());
		}
		if (spec.hrDictionary && window.JifengHRDictionary) {
			return this.openHRDictionary(spec.hrDictionary, null, spec.values?.company);
		}
		if (spec.inventoryArchive && window.JifengInventoryArchive) {
			return this.openInventory(spec.inventoryArchive);
		}
		if (spec.personnelArchive && window.JifengPersonnelArchive) {
			return this.openPersonnel();
		}
		const dialog = new frappe.ui.Dialog({ title: spec.title, size: "extra-large",
			fields: [{ fieldname: "native_form", fieldtype: "HTML" }] });
		const host = dialog.fields_dict.native_form.$wrapper.get(0);
		host.classList.add("jfe-formal-dialog");
		const entry = this.mount(host, spec);
		const hide = dialog.hide.bind(dialog);
		let disposed = false;
		let confirming = false;
		const dispose = () => {
			if (disposed) return;
			disposed = true;
			entry.stop();
			entry.iframe.remove();
			for (const cached of this.frames.values()) {
				try { cached.iframe?.contentWindow?.cur_list?.refresh(); } catch { /* page may still be loading */ }
				if (cached.refreshOnCreate) cached.workbench.refresh();
			}
		};
		const close = () => { dispose(); hide(); };
		dialog.hide = () => {
			if (disposed || confirming) return;
			const block = this.frameCloseBlock(entry.iframe);
			if (block) { frappe.msgprint(__(block)); return; }
			const dirty = this.isDirty(entry.iframe);
			if (dirty) {
				confirming = true;
				frappe.confirm(__("尚有未保存的修改，关闭并放弃修改？"),
					() => { confirming = false; queueMicrotask(close); },
					() => { confirming = false; });
			}
			else close();
		};
		// Bootstrap's X/backdrop/Escape do not call Dialog.hide. Keep the
		// backdrop static and route explicit close buttons through the guard.
		dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		dialog.get_close_btn().removeAttr("data-dismiss").on("click.classic", () => dialog.hide());
		dialog.on_hide = dispose;
		dialog.$wrapper.on("keydown.classic", (event) => {
			if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); dialog.hide(); }
		});
		dialog.add_custom_action(__("关闭"), () => dialog.hide());
		dialog.show();
		return entry.ready;
	}

	openPersonnel(name, options = {}) {
		this.personnelEditor ||= new window.JifengPersonnelArchive(this);
		// The native RPC displays permission or validation errors.
		return this.personnelEditor.open(name, options).catch(() => false);
	}

	openInventory(kind, name, options = {}) {
		this.inventoryEditor ||= new window.JifengInventoryArchive(this);
		return this.inventoryEditor.open(kind, name, null, options).catch(() => {});
	}

	openHRDictionary(type, name, company) {
		this.hrDictionaryEditor ||= new window.JifengHRDictionary(this);
		return this.hrDictionaryEditor.open(type, name, company).catch(() => {});
	}

	async selectPieceWorker(company, employee) {
		if (this.selectingPieceWorker || this.selectingTemporaryWorker) return false;
		this.selectingPieceWorker = true;
		try {
			const key = "formal:工资:工资录入";
			let entry = this.frames.get(key);
			const blocked = () => entry && (this.frameCloseBlock(entry.iframe) || this.isDirty(entry.iframe));
			if (blocked()) {
				frappe.msgprint(__("请先保存或核对工资录入中的草稿，再选择计件人员。")); return false;
			}
			// Reuse the actual classic wage tab; a workers iframe must not create a second workbench.
			this.open({ title: "工资录入", module: "工资", kind: "page", route: ["jifeng-wage-workbench"] });
			entry = this.frames.get(key);
			if (!entry || await entry.ready === false) return false;
			if (this.frames.get(key) !== entry || entry.iframe.isConnected === false) return false;
			if (blocked()) { frappe.msgprint(__("工资录入已有未保存的内容，请先处理。")); return false; }
			const child = entry.iframe.contentWindow;
			await child.frappe.set_route("jifeng-wage-workbench");
			if (this.frames.get(key) !== entry || entry.iframe.isConnected === false) return false;
			return !!await child.jifeng_wage_workbench?.choosePieceWorker(company, employee);
		} finally { this.selectingPieceWorker = false; }
	}

	async selectTemporaryWorker(company, row, stillCurrent = () => true) {
		const validName = value => typeof value === "string" && !!value && value === value.trim() && [...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value);
		if (this.selectingTemporaryWorker || this.selectingPieceWorker || typeof stillCurrent !== "function" || !stillCurrent() ||
			!validName(company) || !row || !validName(row.name) || row.company !== company || row.status !== "Active" ||
			!["employee_version", "supplement_version"].every(field => typeof row[field] === "string" && /^[0-9a-f]{64}$/.test(row[field]))) return false;
		const selected = Object.freeze({ ...row }); this.selectingTemporaryWorker = true;
		try {
			const key = "formal:工资:工资录入";
			let entry = this.frames.get(key);
			const blocked = () => entry && (this.frameCloseBlock(entry.iframe) || this.isDirty(entry.iframe));
			if (blocked()) { frappe.msgprint(__("请先保存或核对工资录入中的草稿，再选择临工。")); return false; }
			this.open({ title: "工资录入", module: "工资", kind: "page", route: ["jifeng-wage-workbench"] });
			entry = this.frames.get(key);
			if (!entry || await entry.ready === false || !stillCurrent()) return false;
			const connected = () => this.frames.get(key) === entry && entry.iframe?.isConnected !== false;
			if (!connected() || blocked()) return false;
			const child = entry.iframe.contentWindow;
			await child.frappe.set_route("jifeng-wage-workbench");
			if (!connected() || !stillCurrent() || blocked() || typeof child.jifeng_wage_workbench?.chooseTemporaryWorker !== "function") return false;
			return await child.jifeng_wage_workbench.chooseTemporaryWorker(company, selected, () => connected() && stillCurrent()) === true;
		} catch { frappe.msgprint(__("临工选择未完成，请核对工资草稿、来源或权限后重试。")); return false; }
		finally { this.selectingTemporaryWorker = false; }
	}

	guardFinancialReportInitialization(child, spec) {
		const reports = ["Profit and Loss Statement", "Cash Flow", "Balance Sheet"];
		if (spec.kind !== "report" || spec.route?.[0] !== "query-report" ||
			!reports.includes(spec.route[1]) || spec.filters?.filter_based_on !== "Date Range") return;
		const prototype = child.frappe.views?.QueryReport?.prototype;
		if (!prototype?.setup_filters) throw new Error("Native report filters unavailable");
		const setup = prototype.setup_filters;
		// This prototype belongs to this tab's isolated Desk iframe. Native v16's
		// financial mode callback refreshes even during set_route_filters, before
		// the two required dates have been filled. Preserve its display changes,
		// but respect the report's own initialization guard for that callback.
		prototype.setup_filters = function (...args) {
			const result = setup.apply(this, args);
			if (this.report_name !== spec.route[1]) return result;
			const mode = this.get_filter("filter_based_on", false);
			const change = mode?.on_change;
			if (typeof change !== "function") return result;
			const report = this;
			mode.on_change = function (...values) {
				if (!report._no_refresh) return change.apply(this, values);
				const refresh = report.refresh;
				report.refresh = () => Promise.resolve();
				try { return change.apply(this, values); }
				finally { report.refresh = refresh; }
			};
			return result;
		};
	}

	async openCompanySource(spec, company, doctype, name) {
		this.open({ title: name, module: spec.module, kind: "form", doctype,
			route: ["Form", doctype, name], company });
		const key = "formal:" + spec.module + ":" + name, entry = this.frames.get(key);
		if (!entry || await entry.ready === false) return false;
		const frm = await this.waitNativeForm(entry, key, { doctype, name, company });
		if (!frm) frappe.msgprint(__("原单尚未准确加载，请核对单号、公司和读取权限。"));
		return !!frm;
	}

	mountCompanyWorkbench(host, spec, Component) {
		const shell = document.createElement("section"), companyHost = document.createElement("div"), body = document.createElement("div");
		Object.assign(shell.style, { height: "100%", overflow: "auto", padding: "12px" });
		companyHost.style.maxWidth = "360px";
		shell.append(companyHost, body); host.appendChild(shell);
		let disposed = false, visible = true, current = null, company = "", control;
		const validCompany = () => {
			const value = control?.get_value?.(), typed = control?.get_input_value?.() ?? control?.$input?.val?.();
			return typeof value === "string" && value && value === value.trim() &&
				(typeof typed !== "string" || typed === value) ? value : "";
		};
		const clear = () => { current?.destroy(); current = null; company = ""; body.replaceChildren(); };
		const update = () => {
			if (disposed) return;
			const selected = validCompany();
			if (current && selected === company) return;
			clear();
			if (!selected) { body.textContent = __("请选择单一公司后读取正式单据。"); return; }
			if (!visible) return;
			company = selected;
			let mounted;
			const isCurrent = () => !disposed && visible && current === mounted && validCompany() === selected;
			mounted = Component.mount(body, { company: selected, title: spec.title, kind: spec.subcontractingReversal, bridge: this,
				openForm: (doctype, name) => isCurrent() ? this.openCompanySource(spec, selected, doctype, name) : Promise.resolve(false),
				newForm: (doctype, defaults = {}) => isCurrent() ? this.openCreate({ title: "新增" + spec.title,
					module: spec.module, kind: "create", doctype, values: { ...defaults, company: selected } }) : Promise.resolve(false) });
			current = mounted;
			current.setVisible?.(visible);
		};
		control = frappe.ui.form.make_control({ parent: companyHost, render_input: true,
			df: { fieldname: "company", fieldtype: "Link", label: __("公司"), options: "Company", reqd: 1, onchange: update } });
		control.$input?.on?.("input.jifeng-formal-company change.jifeng-formal-company", update);
		const workbench = {
			isDirty: () => false, getCloseBlock: () => "",
			refresh: () => { if (!disposed && visible && current && validCompany() === company) return current.refresh(); },
			setVisible: value => {
				const returning = !visible && !!value; visible = !!value; current?.setVisible?.(visible);
				if (visible && !current) update();
				else if (returning) workbench.refresh();
			},
			destroy: () => {
				if (disposed) return; disposed = true;
				control.$input?.off?.(".jifeng-formal-company"); clear(); shell.remove();
			},
		};
		const ready = (async () => {
			const initial = frappe.defaults?.get_user_default?.("Company");
			if (typeof initial === "string" && initial && initial === initial.trim()) await control.set_value(initial);
			update();
			await current?.ready;
			return !disposed;
		})();
		return { workbench, ready, readOnly: true, refreshOnCreate: true, stop: workbench.destroy };
	}

	mount(host, spec) {
		if (spec.productionInstructionList || spec.subcontractingReversal) {
			const Component = spec.productionInstructionList ? window.JifengProductionInstructionList : window.JifengSubcontractingReversal;
			return this.mountCompanyWorkbench(host, spec, Component);
		}
		if (spec.subcontractingReconciliation) {
			const workbench = new window.JifengSubcontractingReconciliation(host, this, spec.subcontractingReconciliation);
			return { workbench, ready: workbench.ready, readOnly: true, stop: () => workbench.destroy() };
		}
		if (spec.temporaryWorkers) {
			const workbench = new window.JifengTemporaryWorkers(host, this, spec.temporaryWorkers);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.salesFulfillment) {
			const workbench = new window.JifengSalesFulfillment(host, this, spec.module || "销售");
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.financeDictionary) {
			const workbench = new window.JifengFinanceDictionary(host, spec.financeDictionary, this);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.inventoryTracking) {
			const workbench = new window.JifengInventoryTracking(host, spec.module, this);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.procurementFulfillment || spec.supplierPayables) {
			const Component = spec.procurementFulfillment ? window.JifengProcurementFulfillment : window.JifengSupplierPayables;
			const workbench = new Component(host, this, spec.module);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.salaryHistory) {
			const workbench = new window.JifengSalaryHistory(host, this);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.inventoryArchive) {
			const workbench = new window.JifengInventoryArchiveList(host, spec.inventoryArchive, this);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.partnerArchive) {
			const workbench = new window.JifengPartnerArchive(host, spec.partnerArchive);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.customerArchive) {
			const workbench = new window.JifengCustomerArchive(host, spec.customerArchive);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.inventoryCatalog) {
			const workbench = new window.JifengInventoryCatalog(host, spec.inventoryCatalog);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.toolArchive) {
			const workbench = new window.JifengToolArchive(host, spec.toolArchive, this);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.productArchive) {
			const workbench = new window.JifengProductArchive(host, this);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.productCategory) {
			const workbench = new window.JifengProductCategory(host);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.productDictionary) {
			const workbench = new window.JifengProductDictionary(host, spec.productDictionary);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.customerType) {
			const workbench = new window.JifengCustomerType(host,
				spec.customerType === true ? "客户类型" : spec.customerType, spec.title);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.orderDictionary) {
			const workbench = new window.JifengOrderDictionary(host, spec.orderDictionary);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.platingColor) {
			const workbench = new window.JifengPlatingColor(host);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		if (spec.orderOperations) {
			const workbench = new window.JifengOrderOperations(host);
			return { workbench, ready: workbench.ready, stop: () => workbench.destroy() };
		}
		const iframe = document.createElement("iframe");
		iframe.className = "jfe-formal-iframe";
		iframe.title = spec.title;
		iframe.src = "/desk";
		const loading = document.createElement("div");
		loading.className = "jfe-formal-loading";
		loading.textContent = __("正在加载…");
		host.append(loading, iframe);
		let timer;
		let cancelWait;
		let stopped = false;
		let settleReady;
		const ready = new Promise((resolve) => { settleReady = resolve; });
		const stop = () => { stopped = true; clearInterval(timer); cancelWait?.(); settleReady(false); };
		iframe.addEventListener("load", () => {
			if (stopped) return;
			clearInterval(timer);
			let attempts = 0;
			timer = setInterval(async () => {
				if (stopped) return;
				const child = iframe.contentWindow;
				// These functions exist before Application.startup has routed Desk.
				// Native v16 Home is the completed route [""], whereas startup's
				// current_route is null. Wait for parsing, not a named first page.
				const initialRoute = child?.frappe?.get_route?.();
				if (!child?.frappe?.boot?.user || !child.frappe.app || !child.frappe.container ||
					!child.frappe.router || !child.frappe.new_doc || !Array.isArray(initialRoute) || !initialRoute.length) {
					if (++attempts >= 300) {
						stop();
						loading.textContent = __("页面未能加载，请关闭后重新打开。");
					}
					return;
				}
				clearInterval(timer);
				try {
					const style = child.document.createElement("style");
					style.textContent = ".navbar,.layout-side-section,.body-sidebar{display:none!important}.layout-main-section-wrapper{margin:0!important}.page-container{padding-top:0!important}.page-head{top:0!important}" +
						(spec.hideCreate ? ".page-head .primary-action{display:none!important}" : "");
					child.document.head.appendChild(style);
					if (spec.kind === "page" && spec.route?.[0] === "jifeng-piece-workers") {
						child.jifeng_classic_select_piece_worker = (company, employee) => this.selectPieceWorker(company, employee);
					}
					if (spec.kind === "list") {
						if (spec.inventoryArchive && window.JifengInventoryArchive) {
							child.document.addEventListener("click", (event) => {
								const link = event.target.closest?.(".list-row a[data-name]");
								if (!link || child.frappe.get_route()[0] !== "List" ||
									child.cur_list?.doctype !== "Jifeng Inventory Profile" ||
									!child.cur_list.data?.some((row) => row.name === link.dataset.name)) return;
								event.preventDefault(); event.stopImmediatePropagation();
								this.openInventory(spec.inventoryArchive, link.dataset.name);
							}, true);
						}
						if (spec.hrDictionary && window.JifengHRDictionary) {
							child.document.addEventListener("click", (event) => {
								const link = event.target.closest?.(".list-row a[data-name]");
								if (!link || child.frappe.get_route()[0] !== "List" ||
									child.cur_list?.doctype !== "Jifeng HR Dictionary" ||
									!child.cur_list.data?.some((row) => row.name === link.dataset.name)) return;
								event.preventDefault(); event.stopImmediatePropagation();
								this.openHRDictionary(spec.hrDictionary, link.dataset.name);
							}, true);
							}
						if (spec.personnelArchive && window.JifengPersonnelArchive) {
							child.document.addEventListener("click", (event) => {
								const link = event.target.closest?.(".list-row a[data-name]");
								if (!link || child.frappe.get_route()[0] !== "List" ||
									child.cur_list?.doctype !== "Employee" ||
									!child.cur_list.data?.some((row) => row.name === link.dataset.name)) return;
								event.preventDefault();
								event.stopImmediatePropagation();
								this.openPersonnel(link.dataset.name);
							}, true);
						}
						const nativeNew = child.frappe.new_doc;
						child.frappe.new_doc = (doctype, opts, callback) => {
							if (child.frappe.get_route()[0] === "List" && doctype === spec.doctype && !callback) {
								if (spec.hideCreate) return Promise.resolve();
								this.openCreate({ ...spec, title: "新增" + spec.title, kind: "create",
									values: spec.dictionaryArchive || spec.hrDictionary ? { ...opts, ...spec.filters } : opts || {} });
								return Promise.resolve();
							}
							return nativeNew.call(child.frappe, doctype, opts, callback);
						};
					}
					if (spec.kind === "create") {
						await new Promise((resolve) => child.frappe.model.with_doctype(spec.doctype, resolve));
						if (stopped) return;
						if (!child.frappe.model.can_create(spec.doctype)) {
							loading.textContent = __("没有新建此单据的权限。");
							settleReady(false);
							return;
						}
						await child.frappe.new_doc(spec.doctype, { ...spec.values });
						if (spec.values?.company) {
							const draftReady = await new Promise((resolve, reject) => {
								let tries = 0;
								const finish = value => { clearInterval(timer); cancelWait = null; resolve(value); };
								cancelWait = () => finish(false);
								const check = () => {
									if (stopped) finish(false);
									else if (this.nativeDraft(iframe, spec)) finish(true);
									else if (++tries >= 300) { clearInterval(timer); cancelWait = null; reject(new Error("Native company draft not ready")); }
								};
								timer = setInterval(check, 50); check();
							});
							if (!draftReady || stopped) return;
						}
					}
					else {
						this.guardFinancialReportInitialization(child, spec);
						child.frappe.route_options = { ...(spec.filters || {}) };
						await child.frappe.set_route(...spec.route);
						if (stopped) return;
						const isTargetRoute = () => {
							const route = child.frappe.get_route();
							return Array.isArray(route) && spec.route.every((part, index) => route[index] === part);
						};
						// Native set_route resolves after AJAX, before its async parse
						// necessarily updates current_route. Wait for the exact target.
						const routeReady = await new Promise((resolve, reject) => {
							let tries = 0;
							const finish = (value) => { clearInterval(timer); cancelWait = null; resolve(value); };
							cancelWait = () => finish(false);
							const check = () => {
								if (stopped) finish(false);
								else if (isTargetRoute()) finish(true);
								else if (++tries >= 300) { clearInterval(timer); cancelWait = null; reject(new Error("Native target route not ready")); }
							};
							timer = setInterval(check, 50);
							check();
						});
						if (!routeReady || stopped) return;
						if (spec.kind === "list" && !Object.keys(spec.filters || {}).length) {
							// Native empty route_options keeps saved filters. Classic
							// entry defaults must start clear, once per mounted tab.
							const list = await new Promise((resolve, reject) => {
								let tries = 0;
								const finish = (value) => { clearInterval(timer); cancelWait = null; resolve(value); };
								cancelWait = () => finish(null);
								timer = setInterval(() => {
										const view = child.cur_list;
										if (stopped) finish(null);
										else if (isTargetRoute() && view?.doctype === spec.doctype && view.filter_area) finish(view);
										else if (++tries >= 300) { clearInterval(timer); cancelWait = null; reject(new Error("List not ready")); }
								}, 50);
							});
							if (!list || stopped) return;
							// FilterArea exists before native result/freeze controls.
							if (list.init_promise) await list.init_promise;
							if (stopped) return;
							if (!isTargetRoute() || child.cur_list !== list) throw new Error("Native list changed during initialization");
							child.frappe.route_options = null;
							await list.filter_area.clear(false);
							await list.refresh();
						}
						if (!isTargetRoute()) throw new Error("Native target route changed during loading");
					}
					if (stopped) return;
					loading.remove();
					iframe.classList.add("is-ready");
					settleReady(true);
				} catch {
					loading.textContent = __("页面未能打开，请关闭后重新打开。");
					settleReady(false);
				}
			}, 100);
		});
		return { iframe, stop, ready };
	}
};
