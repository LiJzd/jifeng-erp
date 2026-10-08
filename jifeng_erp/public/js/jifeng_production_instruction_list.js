(() => {
	const METHOD = "jifeng_erp.production_instruction_list.list_production_instructions";
	const DOCTYPE = "Production Plan";
	const today = () => frappe.datetime.get_today();

	class JifengProductionInstructionList {
		static mount(container, options = {}) {
			return new JifengProductionInstructionList(container, options);
		}

		constructor(container, options = {}) {
			if (!(container instanceof HTMLElement)) throw new TypeError("生产指令需要页面容器");
			this.container = container;
			this.options = options;
			this.page = 1;
			this.pageSize = 50;
			this.requestId = 0;
			this.disposed = false;
			this.rows = [];
			this.root = document.createElement("section");
			this.root.className = "jifeng-production-instruction-list";
			this.container.replaceChildren(this.root);
			this.renderShell();
			this.onClick = (event) => this.handleClick(event);
			this.root.addEventListener("click", this.onClick);
			this.ready = this.refresh();
		}

		renderShell() {
			const heading = document.createElement("h1");
			heading.textContent = "生产指令单";
			const explanation = document.createElement("p");
			explanation.textContent = "列表读取正式生产计划；数量按库存单位分别汇总。参考金额是指令记录，不代表库存估值或实际成本。款数按不同商品编号计数，旧系统口径尚未核实。";

			const filters = document.createElement("div");
			filters.className = "jpil-filters";
			this.from = this.input("date", "from", this.monthStart());
			this.to = this.input("date", "to", today());
			this.exact = this.input("date", "exact", "");
			this.search = this.input("search", "search", "", "单号或备注");
			filters.append(
				this.label("开始日期", this.from), this.label("结束日期", this.to),
				this.label("指定日期", this.exact), this.label("关键词", this.search),
				this.button("查询", "query", true), this.button("新增", "new"),
				this.button("导出", "export"), this.button("格式1", "format"), this.button("打印", "print")
			);

			this.status = document.createElement("p");
			this.status.className = "jpil-status";		this.status.setAttribute("aria-live", "polite");
			const table = document.createElement("table");
			table.className = "jpil-table";
			const head = document.createElement("thead");
			const header = document.createElement("tr");
			for (const label of ["审核", "单号", "日期", "数量", "参考金额（原金额口径未核实）", "指令日期", "款数*", "备注", "操作", "修改", "删除"]) {
				const cell = document.createElement("th"); cell.textContent = label; header.append(cell);
			}
			head.append(header);
			this.body = document.createElement("tbody");
			table.append(head, this.body);
			this.pager = document.createElement("footer");
			this.pager.className = "jpil-pager";
			this.pageText = document.createElement("span");
			this.pager.append(this.button("上一页", "prev"), this.pageText, this.button("下一页", "next"));
			this.root.append(heading, explanation, filters, this.status, table, this.pager);
		}

		monthStart() { return today().slice(0, 8) + "01"; }
		input(type, name, value, placeholder = "") {
			const input = document.createElement("input"); input.type = type; input.dataset.filter = name;
			input.value = value; input.placeholder = placeholder; return input;
		}
		label(caption, input) {
			const label = document.createElement("label"); label.append(document.createTextNode(caption), input); return label;
		}
		button(text, action, primary = false) {
			const button = document.createElement("button"); button.type = "button";
			button.dataset.action = action; button.textContent = text;
			if (primary) button.className = "btn-primary";
			return button;
		}
		value(name) { return this.root.querySelector(`[data-filter="${name}"]`)?.value || ""; }

		async refresh() {
			if (this.disposed) return;
			const request = ++this.requestId;
			const args = {
				company: this.options.company,
				from_date: this.value("from"), to_date: this.value("to"),
				exact_date: this.value("exact") || undefined, search: this.value("search").trim(),
				page: this.page, page_size: this.pageSize,
			};
			if (!args.company) { this.message("请先选择公司。", "error"); return; }
			if (!args.exact_date && (!args.from_date || !args.to_date || args.from_date > args.to_date)) {
				this.message("请核对开始日期和结束日期。", "error"); return;
			}
			this.setBusy(true);
			this.message("正在读取正式生产计划…", "loading");
			try {
				const response = await frappe.call({ method: METHOD, type: "GET", args });
				if (this.disposed || request !== this.requestId) return;
				const result = response.message || {};
				this.rows = Array.isArray(result.rows) ? result.rows : [];
				this.hasMore = Boolean(result.has_more);
				this.renderRows();
				this.pageText.textContent = `第 ${result.page || this.page} 页`;
				this.message(`${this.rows.length} 条正式生产计划。*按不同商品编号计数；旧系统“款数”口径未核实。`, "info");
			} catch (error) {
				if (this.disposed || request !== this.requestId) return;
				this.rows = []; this.renderRows();
				this.message(error?.message || "生产指令读取失败，请核对日期、公司和读取权限。", "error");
			} finally {
				if (!this.disposed && request === this.requestId) {
					this.setBusy(false); this.updatePager();
				}
			}
		}

		message(text, kind) { this.status.textContent = text; this.status.dataset.kind = kind; }
		setBusy(busy) { this.root.querySelectorAll("button, input").forEach((node) => { node.disabled = busy; }); }
		updatePager() {
			this.root.querySelector('[data-action="prev"]').disabled = this.page <= 1;
			this.root.querySelector('[data-action="next"]').disabled = !this.hasMore;
		}
		formatQuantity(groups) {
			return (groups || []).map((entry) => `${frappe.format(entry.qty, { fieldtype: "Float" })} ${entry.stock_uom}`).join("；") || "—";
		}
		formatAmount(row) {
			if (row.reference_amount === null || row.reference_amount === undefined || row.reference_amount === "") return "未填写";
			if (!row.currency) return `${row.reference_amount}（币种未配置）`;
			return frappe.format(row.reference_amount, { fieldtype: "Currency", options: row.currency });
		}
		statusLabel(docstatus) { return ({ 0: "草稿", 1: "已审核", 2: "已取消" })[docstatus] || "未知"; }

		renderRows() {
			this.body.replaceChildren();
			for (const row of this.rows) {
				const tr = document.createElement("tr"); tr.dataset.name = row.name;
				const values = [this.statusLabel(row.docstatus), row.instruction_no || row.name,
					row.posting_date, this.formatQuantity(row.quantity_groups), this.formatAmount(row),
					row.instruction_date, row.style_count, row.remark];
				for (const value of values) { const td = document.createElement("td"); td.textContent = value == null ? "" : String(value); tr.append(td); }
				for (const [text, action] of [["打开", "open"], ["修改", "edit"], ["删除", "delete"]]) {
					const td = document.createElement("td"); const button = this.button(text, action); button.dataset.name = row.name;
					if (action === "delete" && row.docstatus !== 0) { button.disabled = true; button.title = "已审核/取消单据请在正式单据中按原生流程处理"; }
					td.append(button); tr.append(td);
				}
				this.body.append(tr);
			}
		}

		async handleClick(event) {
			const button = event.target.closest("[data-action]");
			if (!button || !this.root.contains(button) || button.disabled) return;
			const action = button.dataset.action;
			if (action === "query") { this.page = 1; await this.refresh(); return; }
			if (action === "prev" && this.page > 1) { this.page -= 1; await this.refresh(); return; }
			if (action === "next" && this.hasMore) { this.page += 1; await this.refresh(); return; }
			if (action === "new") {
				if (!this.options.company) { this.message("请先选择公司。", "error"); return; }
				if (this.options.newForm) this.options.newForm(DOCTYPE, { company: this.options.company });
				else frappe.new_doc(DOCTYPE, { company: this.options.company });
				return;
			}
			if (["open", "edit", "delete"].includes(action)) {
				if (this.options.openForm) this.options.openForm(DOCTYPE, button.dataset.name);
				else frappe.set_route("Form", DOCTYPE, button.dataset.name);
				return;
			}
			if (action === "format") {
				if (this.rows.length !== 1) { frappe.msgprint(__("请将查询范围缩小到一张生产指令后使用格式1。")); return; }
				const row = this.rows[0];
				const url = `/printview?doctype=${encodeURIComponent(DOCTYPE)}&name=${encodeURIComponent(row.name)}&format=${encodeURIComponent("Jifeng Production Instruction")}&no_letterhead=1`;
				window.open(url, "_blank", "noopener"); return;
			}
			if (action === "print") { window.print(); return; }
			if (action === "export") { this.exportRows(); }
		}

		exportRows() {
			const columns = ["审核", "单号", "日期", "数量", "参考金额（原金额口径未核实）", "指令日期", "款数*", "备注"];
			const quote = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
			const lines = [columns.map(quote).join(","), ...this.rows.map((row) => [
				this.statusLabel(row.docstatus), row.instruction_no || row.name, row.posting_date,
				this.formatQuantity(row.quantity_groups), this.formatAmount(row), row.instruction_date,
				row.style_count, row.remark,
			].map(quote).join(","))];
			const url = URL.createObjectURL(new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" }));
			const link = document.createElement("a"); link.href = url; link.download = "生产指令单.csv"; link.click(); URL.revokeObjectURL(url);
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

	window.JifengProductionInstructionList = JifengProductionInstructionList;
})();
