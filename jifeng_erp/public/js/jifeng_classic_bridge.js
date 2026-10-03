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
	}

	open(spec) {
		const key = "formal:" + spec.module + ":" + spec.title;
		this.workspace.addTab({ key, title: spec.title, module: spec.module, kind: "formal", spec });
	}

	content(tab) {
		const escape = (value) => this.workspace.esc(value);
		return '<section class="jfe-formal-page">' +
			'<div class="jfe-formal-anchor" aria-label="' + escape(tab.title) + '"></div></section>';
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
			entry.container.hidden = !(visible && key === active);
		}
		this.layer.hidden = !visible;
		if (!visible) return;
		const rect = anchor.getBoundingClientRect();
		Object.assign(this.layer.style, { top: rect.top + "px", left: rect.left + "px",
			width: rect.width + "px", height: rect.height + "px" });
	}

	canClose(key) {
		const entry = this.frames.get(key);
		return entry?.workbench ? !entry.workbench.isDirty() : !this.isDirty(entry?.iframe);
	}

	closeBlock(key) {
		const entry = this.frames.get(key);
		return entry?.workbench ? entry.workbench.getCloseBlock() : this.frameCloseBlock(entry?.iframe);
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
	}

	openPersonnel(name) {
		this.personnelEditor ||= new window.JifengPersonnelArchive(this);
		// The native RPC displays permission or validation errors.
		return this.personnelEditor.open(name).catch(() => {});
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
		if (this.selectingPieceWorker) return false;
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

	mount(host, spec) {
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
		let stopped = false;
		let settleReady;
		const ready = new Promise((resolve) => { settleReady = resolve; });
		const stop = () => { stopped = true; clearInterval(timer); settleReady(false); };
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
					style.textContent = ".navbar,.layout-side-section{display:none!important}.layout-main-section-wrapper{margin:0!important}.page-container{padding-top:0!important}.page-head{top:0!important}" +
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
					}
					else {
						child.frappe.route_options = { ...(spec.filters || {}) };
						await child.frappe.set_route(...spec.route);
						if (stopped) return;
						const isTargetRoute = () => spec.route.every((part, index) => child.frappe.get_route()[index] === part);
						if (!isTargetRoute()) throw new Error("Native target route not ready");
						if (spec.kind === "list" && !Object.keys(spec.filters || {}).length) {
							// Native empty route_options keeps saved filters. Classic
							// entry defaults must start clear, once per mounted tab.
							const list = await new Promise((resolve, reject) => {
								let tries = 0;
								timer = setInterval(() => {
										const view = child.cur_list;
									if (stopped) { clearInterval(timer); resolve(null); }
									else if (isTargetRoute() && view?.doctype === spec.doctype && view.filter_area) { clearInterval(timer); resolve(view); }
									else if (++tries >= 300) { clearInterval(timer); reject(new Error("List not ready")); }
								}, 50);
							});
							if (!list || stopped) return;
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
