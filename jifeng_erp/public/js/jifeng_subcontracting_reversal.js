(() => {
	const METHOD = "jifeng_erp.subcontracting_reversal.list_subcontracting_reversals";
	const today = () => frappe.datetime.get_today();
	const LABELS = {
		plating_order: ["审核状态", "单号", "日期", "档案分类", "状态", "电镀厂", "委外采购单", "数量", "操作"],
		outer_order: ["审核状态", "单号", "日期", "档案分类", "状态", "外发工厂", "委外采购单", "数量", "操作"],
		outer_receipt: ["审核状态", "单号", "日期", "档案分类", "业务类型", "状态", "外发工厂", "数量", "原收回单", "操作"],
		plating_inspection: ["审核状态", "品检单", "日期", "档案分类", "检查状态", "类型", "物料", "物料名称", "委外收回单", "操作"],
	};

	class JifengSubcontractingReversal {
		static mount(container, options = {}) { return new JifengSubcontractingReversal(container, options); }

		constructor(container, options = {}) {
			if (!(container instanceof HTMLElement)) throw new TypeError("委外反审需要页面容器");
			if (!Object.hasOwn(LABELS, options.kind)) throw new TypeError("委外反审类型无效");
			this.container = container;
			this.options = options;
			this.page = 1;
			this.pageSize = 50;
			this.requestId = 0;
			this.disposed = false;
			this.rows = [];
			this.root = document.createElement("section");
			this.root.className = "jifeng-subcontracting-reversal";
			this.container.replaceChildren(this.root);
			this.renderShell();
			this.onClick = event => this.handleClick(event);
			this.root.addEventListener("click", this.onClick);
			this.ready = this.refresh();
		}

		renderShell() {
			const title = document.createElement("h1");
			title.textContent = this.options.title || "委外单据反审";
			const explanation = document.createElement("p");
			explanation.textContent = "这里查询正式委外单据。请打开原单并使用系统提供的取消操作；后续收回、品检、库存或账务依赖由原生规则校验。取消后如需更正，请按原来源修订新单。";
			this.note = document.createElement("p");
			this.note.className = "jsr-note";
			const filters = document.createElement("div");
			filters.className = "jsr-filters";
			this.from = this.field("date", "from", this.monthStart());
			this.to = this.field("date", "to", today());
			this.search = this.field("search", "search", "", "单号、供应商、物料");
			this.status = this.field("select", "status", "");
			for (const [value, label] of [["submitted", "已提交待处理"], ["all", "全部状态"], ["cancelled", "已取消"]]) {
				const option = document.createElement("option"); option.value = value; option.textContent = label; this.status.append(option);
			}
			filters.append(this.label("开始日期", this.from), this.label("结束日期", this.to),
				this.label("状态", this.status), this.label("关键词", this.search), this.button("查询", "query", true));
			this.message = document.createElement("p");
			this.message.className = "jsr-message";
			this.message.setAttribute("aria-live", "polite");
			this.table = document.createElement("table"); this.table.className = "jsr-table";
			const thead = document.createElement("thead"); const tr = document.createElement("tr");
			for (const text of LABELS[this.options.kind]) { const th = document.createElement("th"); th.textContent = text; tr.append(th); }
			thead.append(tr); this.tbody = document.createElement("tbody"); this.table.append(thead, this.tbody);
			this.pager = document.createElement("footer"); this.pager.className = "jsr-pager";
			this.pageText = document.createElement("span");
			this.pager.append(this.button("上一页", "prev"), this.pageText, this.button("下一页", "next"));
			this.root.append(title, explanation, this.note, filters, this.message, this.table, this.pager);
		}

		monthStart() { return `${today().slice(0, 8)}01`; }
		field(type, key, value, placeholder = "") {
			const input = document.createElement(type === "select" ? "select" : "input");
			input.dataset.filter = key;
			if (type !== "select") { input.type = type; input.value = value; input.placeholder = placeholder; }
			return input;
		}
		label(text, control) { const label = document.createElement("label"); label.append(document.createTextNode(text), control); return label; }
		button(text, action, primary = false) {
			const button = document.createElement("button"); button.type = "button"; button.dataset.action = action;
			button.textContent = text; if (primary) button.className = "btn-primary"; return button;
		}
		value(name) { return this.root.querySelector(`[data-filter="${name}"]`)?.value || ""; }
		setBusy(busy) { this.root.querySelectorAll("button,input,select").forEach(node => { node.disabled = busy; }); }

		async refresh() {
			if (this.disposed) return;
			const request = ++this.requestId;
			const args = { kind: this.options.kind, company: this.options.company,
				from_date: this.value("from"), to_date: this.value("to"), status: this.value("status"),
				search: this.value("search").trim(), page: this.page, page_size: this.pageSize };
			if (!args.company || !args.from_date || !args.to_date || args.from_date > args.to_date) {
				this.show("请核对公司和查询日期。", "error"); return;
			}
			this.setBusy(true); this.show("正在读取正式单据…", "loading");
			try {
				const response = await frappe.call({ method: METHOD, type: "GET", args });
				if (this.disposed || request !== this.requestId) return;
				const result = response.message || {};
				this.rows = Array.isArray(result.rows) ? result.rows : [];
				this.hasMore = Boolean(result.has_more);
				this.note.textContent = result.source_note || "";
				this.actionNote = result.action_note || "";
				this.renderRows();
				this.pageText.textContent = `第 ${result.page || this.page} 页`;
				this.show(`${this.rows.length} 条可读取的正式单据。打开原单后由原生表单执行取消。`, "info");
			} catch (error) {
				if (this.disposed || request !== this.requestId) return;
				this.rows = []; this.renderRows();
				this.show(error?.message || "读取失败，请核对公司、日期和单据权限。", "error");
			} finally {
				if (!this.disposed && request === this.requestId) { this.setBusy(false); this.updatePager(); }
			}
		}

		show(text, kind) { this.message.textContent = text; this.message.dataset.kind = kind; }
		updatePager() {
			this.root.querySelector('[data-action="prev"]').disabled = this.page <= 1;
			this.root.querySelector('[data-action="next"]').disabled = !this.hasMore;
		}
		display(row, field) { const value = row[field]; return value === null || value === undefined ? "" : String(value); }
		cell(tr, value) { const td = document.createElement("td"); td.textContent = value == null ? "" : String(value); tr.append(td); }

		renderRows() {
			this.tbody.replaceChildren();
			for (const row of this.rows) {
				const tr = document.createElement("tr"); tr.dataset.name = row.name;
				if (["plating_order", "outer_order"].includes(this.options.kind)) {
					for (const field of ["docstatus_label", "name", "document_date", "source_classification", "status", "supplier", "purchase_order", "total_qty"]) this.cell(tr, row[field]);
				} else if (this.options.kind === "outer_receipt") {
					for (const field of ["docstatus_label", "name", "document_date", "source_classification", "business_type", "status", "supplier", "total_qty", "return_against"]) this.cell(tr, row[field]);
				} else {
					for (const field of ["docstatus_label", "name", "document_date", "source_classification", "status", "inspection_type", "item_code", "item_name", "reference_name"]) this.cell(tr, row[field]);
				}
				const td = document.createElement("td"); const open = this.button("打开原单", "open");
				open.dataset.doctype = row.source_doctype; open.dataset.name = row.name; td.append(open); tr.append(td);
				this.tbody.append(tr);
			}
		}

		async handleClick(event) {
			const button = event.target.closest("[data-action]");
			if (!button || !this.root.contains(button) || button.disabled) return;
			if (button.dataset.action === "query") { this.page = 1; await this.refresh(); }
			else if (button.dataset.action === "prev" && this.page > 1) { this.page -= 1; await this.refresh(); }
			else if (button.dataset.action === "next" && this.hasMore) { this.page += 1; await this.refresh(); }
			else if (button.dataset.action === "open" && button.dataset.doctype && button.dataset.name) {
				if (this.options.openForm) this.options.openForm(button.dataset.doctype, button.dataset.name);
				else frappe.set_route("Form", button.dataset.doctype, button.dataset.name);
			}
		}

		isDirty() { return false; }
		getCloseBlock() { return null; }
		setVisible(visible) { this.visible = Boolean(visible); }

		destroy() {
			if (this.disposed) return;
			this.disposed = true; this.requestId += 1;
			this.root.removeEventListener("click", this.onClick); this.root.remove();
		}
	}

	window.JifengSubcontractingReversal = JifengSubcontractingReversal;
})();
