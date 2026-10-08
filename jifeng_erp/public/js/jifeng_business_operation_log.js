(() => {
	const MODULES = ["物料", "材料", "产品", "订单", "生产", "人事", "工资", "模具", "版种", "发镀", "外发", "销售", "财务", "总经理"];
	const METHOD = "jifeng_erp.business_operation_log.query_operation_log";
	const today = () => frappe.datetime.get_today();

	class JifengBusinessOperationLog {
		static mount(container, options = {}) {
			return new JifengBusinessOperationLog(container, options);
		}

		constructor(container, options) {
			if (!(container instanceof HTMLElement)) throw new TypeError("操作日志需要一个页面容器");
			this.container = container;
			this.options = options;
			this.page = 1;
			this.pageSize = 25;
			this.requestId = 0;
			this.destroyed = false;
			this.root = document.createElement("section");
			this.root.className = "jifeng-business-operation-log";
			this.root.dataset.jifengBusinessOperationLog = "";
			this.container.replaceChildren(this.root);
			this.renderShell();
			this.onClick = (event) => this.handleClick(event);
			this.root.addEventListener("click", this.onClick);
			if (options.module && MODULES.includes(options.module)) {
				this.root.querySelector('[data-jol-filter="module"]').value = options.module;
			}
			this.refresh();
		}

		renderShell() {
			const header = document.createElement("header");
			header.className = "jol-header";
			const title = document.createElement("h1");
			title.textContent = this.options.title || "操作日志";
			const note = document.createElement("p");
			note.textContent = "仅显示可读取业务单据对应的原生创建与版本记录；未保留的历史不会补造。";
			header.append(title, note);

			const filters = document.createElement("div");
			filters.className = "jol-filters";
			filters.append(
				this.label("开始时间", this.input("datetime-local", "from-date", `${today()}T00:00`)),
				this.label("结束时间", this.input("datetime-local", "to-date", `${today()}T23:59`)),
				this.label("业务模块", this.moduleSelect()),
				this.label("操作人", this.input("search", "operator", "", "用户账号")),
				this.label("操作内容", this.actionSelect()),
				this.label("单号", this.input("search", "document-name", "", "单据编号")),
				this.button("查询", "query", true),
				this.button("更新日志", "refresh")
			);

			const status = document.createElement("div");
			status.className = "jol-status";
			status.setAttribute("aria-live", "polite");
			status.dataset.jolStatus = "";
			const tableWrap = document.createElement("div");
			tableWrap.className = "jol-table-wrap";
			const table = document.createElement("table");
			table.className = "jol-table";
			const thead = document.createElement("thead");
			const headRow = document.createElement("tr");
			for (const label of ["日期", "操作人", "模块", "操作内容", "单号", "备注"]) {
				const th = document.createElement("th");
				th.textContent = label;
				headRow.append(th);
			}
			thead.append(headRow);
			this.tbody = document.createElement("tbody");
			table.append(thead, this.tbody);
			tableWrap.append(table);

			const pager = document.createElement("footer");
			pager.className = "jol-pager";
			this.pageLabel = document.createElement("span");
			this.pageLabel.dataset.jolPage = "";
			pager.append(this.button("上一页", "prev"), this.pageLabel, this.button("下一页", "next"));
			this.root.append(header, filters, status, tableWrap, pager);
			this.status = status;
		}

		label(text, control) {
			const wrap = document.createElement("label");
			wrap.className = "jol-field";
			const caption = document.createElement("span");
			caption.textContent = text;
			wrap.append(caption, control);
			return wrap;
		}

		input(type, name, value, placeholder = "") {
			const input = document.createElement("input");
			input.type = type;
			input.dataset.jolFilter = name;
			input.value = value;
			input.placeholder = placeholder;
			return input;
		}

		moduleSelect() {
			const select = document.createElement("select");
			select.dataset.jolFilter = "module";
			const blank = document.createElement("option");
			blank.value = "";
			blank.textContent = "请选择模块";
			select.append(blank);
			for (const module of MODULES) {
				const option = document.createElement("option");
				option.value = module;
				option.textContent = module;
				select.append(option);
			}
			return select;
		}

		actionSelect() {
			const select = document.createElement("select");
			select.dataset.jolFilter = "action";
			for (const [value, label] of [["", "全部操作"], ["创建", "创建"], ["修改", "修改"], ["提交", "提交"], ["取消", "取消"]]) {
				const option = document.createElement("option");
				option.value = value;
				option.textContent = label;
				select.append(option);
			}
			return select;
		}

		button(text, action, primary = false) {
			const button = document.createElement("button");
			button.type = "button";
			button.dataset.jolAction = action;
			button.textContent = text;
			if (primary) button.className = "primary";
			return button;
		}

		async refresh() {
			if (this.destroyed) return;
			const requestId = ++this.requestId;
			const args = {
				from_date: this.value("from-date"),
				to_date: this.value("to-date"),
				module: this.value("module"),
				document_name: this.value("document-name").trim(),
				operator: this.value("operator").trim(),
				action: this.value("action"),
				company: this.options.company || undefined,
				page: this.page,
				page_size: this.pageSize,
			};
			if (!args.module) {
				this.showMessage("请选择业务模块后查询。", "info");
				this.renderRows([]);
				this.hasMore = false;
				this.updatePagerState();
				return;
			}
			this.showMessage("正在读取可访问的业务记录…", "loading");
			this.setControlsDisabled(true);
			try {
				const response = await frappe.call({ method: METHOD, type: "GET", args });
				if (this.destroyed || requestId !== this.requestId) return;
				const result = response.message || {};
				this.renderRows(Array.isArray(result.rows) ? result.rows : []);
				this.hasMore = Boolean(result.has_more);
				this.truncated = Boolean(result.truncated);
				this.pageLabel.textContent = `第 ${result.page || this.page} 页`;
				const historyNote = result.history_available === false ? "部分版本历史不可读取；当前只显示可证明的原生记录。" : "";
				const truncationNote = this.truncated ? "达到单来源读取上限，结果可能不完整；请缩短时间范围或填写单号。" : "";
				const rowNote = result.rows?.length ? `共显示 ${result.rows.length} 条可读取记录。` : "所选范围内没有可读取的原生业务操作记录。";
				this.showMessage([rowNote, historyNote, truncationNote].filter(Boolean).join(" "), this.truncated ? "warning" : "info");
			} catch (error) {
				if (this.destroyed || requestId !== this.requestId) return;
				this.renderRows([]);
				this.hasMore = false;
				this.truncated = false;
				this.showMessage(error?.message || "日志读取失败，请核对日期、模块和业务权限。", "error");
			} finally {
				if (!this.destroyed && requestId === this.requestId) {
					this.setControlsDisabled(false);
					this.updatePagerState();
				}
			}
		}

		value(name) {
			return this.root.querySelector(`[data-jol-filter="${name}"]`)?.value || "";
		}

		showMessage(text, kind) {
			this.status.textContent = text;
			this.status.dataset.kind = kind;
		}

		setControlsDisabled(disabled) {
			this.root.querySelectorAll("button, input, select").forEach((control) => { control.disabled = disabled; });
		}

		updatePagerState() {
			const prev = this.root.querySelector('[data-jol-action="prev"]');
			const next = this.root.querySelector('[data-jol-action="next"]');
			if (prev) prev.disabled = this.page <= 1;
			if (next) next.disabled = !this.hasMore;
		}

		renderRows(rows) {
			this.tbody.replaceChildren();
			for (const row of rows) {
				const tr = document.createElement("tr");
				tr.dataset.jolEventId = row.event_id || "";
				for (const key of ["happened_at", "actor", "module", "action"]) {
					const td = document.createElement("td");
					td.textContent = row[key] || "";
					tr.append(td);
				}
				const linkCell = document.createElement("td");
				const link = document.createElement("a");
				link.href = "#";
				link.dataset.jolSourceLink = "";
				link.dataset.doctype = row.source_doctype || "";
				link.dataset.name = row.source_name || "";
				link.textContent = row.source_name || "";
				linkCell.append(link);
				tr.append(linkCell);
				const note = document.createElement("td");
				note.textContent = row.note || "";
				tr.append(note);
				this.tbody.append(tr);
			}
		}

		handleClick(event) {
			const link = event.target.closest("[data-jol-source-link]");
			if (link && this.root.contains(link)) {
				event.preventDefault();
				if (link.dataset.doctype && link.dataset.name) {
					frappe.set_route("Form", link.dataset.doctype, link.dataset.name);
				}
				return;
			}
			const button = event.target.closest("[data-jol-action]");
			if (!button || !this.root.contains(button)) return;
			const action = button.dataset.jolAction;
			if (action === "query" || action === "refresh") {
				this.page = 1;
				this.refresh();
			} else if (action === "prev" && this.page > 1) {
				this.page -= 1;
				this.refresh();
			} else if (action === "next" && this.hasMore) {
				this.page += 1;
				this.refresh();
			}
		}

		destroy() {
			if (this.destroyed) return;
			this.destroyed = true;
			this.root.removeEventListener("click", this.onClick);
			this.root.remove();
		}
	}

	window.JifengBusinessOperationLog = JifengBusinessOperationLog;
})();
