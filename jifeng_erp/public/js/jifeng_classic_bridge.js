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
		const visible = anchor && anchor.getClientRects().length && frappe.get_route()[0] === "jifeng-legacy";
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
		const frame = this.frames.get(key)?.iframe;
		return !this.isDirty(frame);
	}

	isDirty(frame) {
		try {
			const child = frame?.contentWindow;
			const quick = child?.frappe?.quick_entry?.dialog;
			return !!(child?.cur_frm?.is_dirty() || quick?.is_visible || quick?.display);
		} catch { return true; }
	}

	openCreate(spec) {
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
				try { cached.iframe.contentWindow?.cur_list?.refresh(); } catch { /* page may still be loading */ }
			}
		};
		const close = () => { dispose(); hide(); };
		dialog.hide = () => {
			if (disposed || confirming) return;
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

	mount(host, spec) {
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
		const stop = () => { stopped = true; clearInterval(timer); };
		iframe.addEventListener("load", () => {
			if (stopped) return;
			clearInterval(timer);
			let attempts = 0;
			timer = setInterval(async () => {
				if (stopped) return;
				const child = iframe.contentWindow;
				if (!child?.frappe?.boot?.user || !child.frappe.router || !child.frappe.new_doc) {
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
					if (spec.kind === "list") {
						const nativeNew = child.frappe.new_doc;
						child.frappe.new_doc = (doctype, opts, callback) => {
							if (child.frappe.get_route()[0] === "List" && doctype === spec.doctype && !callback) {
								if (spec.hideCreate) return Promise.resolve();
								this.openCreate({ ...spec, title: "新增" + spec.title, kind: "create", values: opts || {} });
								return Promise.resolve();
							}
							return nativeNew.call(child.frappe, doctype, opts, callback);
						};
					}
					if (spec.kind === "create") {
						await new Promise((resolve) => child.frappe.model.with_doctype(spec.doctype, resolve));
						if (!child.frappe.model.can_create(spec.doctype)) {
							loading.textContent = __("没有新建此单据的权限。");
							return;
						}
						await child.frappe.new_doc(spec.doctype, { ...spec.values });
					}
					else {
						child.frappe.route_options = { ...(spec.filters || {}) };
						await child.frappe.set_route(...spec.route);
						if (spec.kind === "list" && !Object.keys(spec.filters || {}).length) {
							// Native empty route_options keeps saved filters. Classic
							// entry defaults must start clear, once per mounted tab.
							const list = await new Promise((resolve, reject) => {
								let tries = 0;
								timer = setInterval(() => {
										const view = child.cur_list;
									if (stopped) { clearInterval(timer); resolve(null); }
									else if (view?.doctype === spec.doctype && view.filter_area) { clearInterval(timer); resolve(view); }
									else if (++tries >= 300) { clearInterval(timer); reject(new Error("List not ready")); }
								}, 50);
							});
							if (!list || stopped) return;
							child.frappe.route_options = null;
							await list.filter_area.clear(false);
							await list.refresh();
						}
					}
					loading.remove();
					iframe.classList.add("is-ready");
				} catch {
					loading.textContent = __("页面未能打开，请关闭后重新打开。");
				}
			}, 100);
		});
		return { iframe, stop };
	}
};
