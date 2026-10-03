/* Shared native Operation maintenance. Table ordering is independent of BOM routing. */
window.JifengOrderOperations = class JifengOrderOperations {
	constructor(host) {
		this.host = host; this.disposed = false; this.states = []; this.start = 0; this.total = 0; this.revision = 0; this.loading = false;
		this.build(); this.beforeUnload = (event) => { if (this.isDirty() || this.getCloseBlock()) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener("beforeunload", this.beforeUnload); this.ready = this.initialize();
	}
	element(tag, text, className) {
		const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text ?? "");
		if (className) node.className = className; return node;
	}
	button(label, action, reason) {
		const node = this.element("button", __(label), "btn btn-default btn-xs"); node.type = "button";
		if (reason) { node.disabled = true; node.title = __(reason); } else node.addEventListener("click", action);
		return node;
	}
	call(method, args = {}) { return frappe.call({ method: "jifeng_erp.order_operations." + method, args }); }
	build() {
		this.host.classList.add("jfe-order-operations"); this.host.replaceChildren();
		this.status = this.element("p", __("正在读取工序…")); this.status.setAttribute("role", "status");
		const scope = this.element("p", __("共用工序；此页顺序用于排列，生产路线按各商品的 BOM 执行。"), "jfe-order-operations-scope");
		this.scroller = this.element("div", undefined, "jfe-order-operations-scroll");
		const table = this.element("table", undefined, "jfe-order-operations-table"); table.setAttribute("aria-label", __("订单工序"));
		const headings = this.element("tr"), thead = this.element("thead");
		for (const label of ["顺序", "工序", "备注", "操作", "日期", "修改人", "修改日期", "删除"]) headings.append(this.element("th", __(label)));
		thead.append(headings); this.tbody = this.element("tbody"); table.append(thead, this.tbody); this.scroller.append(table);
		this.previous = this.button("上页", () => this.query(Math.max(0, this.start - 25)));
		this.next = this.button("下页", () => this.query(this.start + 25)); this.count = this.element("span", "—");
		const footer = this.element("div", undefined, "jfe-order-operations-footer");
		footer.append(this.previous, this.count, this.next, this.button("刷新", () => this.query(this.start)));
		this.review = this.element("div", undefined, "jfe-order-operations-review");
		this.host.append(scope, this.status, this.scroller, footer, this.review); this.updatePager();
	}
	validArchive(archive, blank = false) {
		if (!archive || archive.scope !== "shared_operation" || !archive.field_meta || typeof archive.can_write !== "boolean") return false;
		if (blank ? archive.name != null || archive.version != null : typeof archive.name !== "string" || !archive.name || typeof archive.version !== "string" || !archive.version) return false;
		if ((archive.ordering_name == null) !== (archive.ordering_version == null)) return false;
		if (archive.ordering_name != null && (typeof archive.ordering_name !== "string" || !archive.ordering_name || typeof archive.ordering_version !== "string" || !archive.ordering_version)) return false;
		if (archive.sequence != null && (!Number.isInteger(archive.sequence) || archive.sequence < 0 || archive.sequence > 2147483647)) return false;
		if (archive.remarks != null && typeof archive.remarks !== "string") return false;
		return true;
	}
	async initialize() {
		try {
			const response = await this.call("init_operations");
			if (this.disposed) return false;
			if (!this.validArchive(response.message, true) || typeof response.message.can_create !== "boolean") throw new Error("Incomplete operation capabilities");
			this.blank = response.message; await this.query(0); return true;
		} catch { if (!this.disposed) this.status.textContent = __("无法读取工序，请核对查看权限后刷新。"); return false; }
	}
	updatePager() {
		this.count.textContent = __("第") + (this.total ? Math.floor(this.start / 25) + 1 : 0) + "/" + Math.ceil(this.total / 25) + __("页 · ") + this.total + __("条");
		this.previous.disabled = this.loading || this.start === 0; this.next.disabled = this.loading || this.start + 25 >= this.total;
	}
	invalidateQuery() {
		if (this.loading) { this.revision++; this.loading = false; this.updatePager(); }
	}
	async query(start = this.start) {
		if (this.disposed || !this.blank || this.loading) return;
		if (this.getCloseBlock() || this.isDirty()) { frappe.msgprint(__("请先保存或取消当前修改，再刷新工序列表。")); return; }
		const revision = ++this.revision; this.loading = true; this.updatePager();
		try {
			const response = await this.call("list_operations", { start, page_length: 25 }), result = response.message;
			if (this.disposed || revision !== this.revision) return;
			const expectedStart = result?.total ? Math.min(start, Math.floor((result.total - 1) / 25) * 25) : 0;
			if (result?.scope !== "shared_operation" || !Number.isSafeInteger(result.total) || result.total < 0 || result.start !== expectedStart || result.page_length !== 25 ||
				!Array.isArray(result.rows) || result.rows.length !== Math.min(25, Math.max(0, result.total - result.start)) ||
				new Set(result.rows.map((row) => row?.name)).size !== result.rows.length || result.rows.some((row) => !this.validArchive(row))) throw new Error("Incomplete operation list");
			if (this.isDirty() || this.getCloseBlock()) { this.status.textContent = __("查询期间有新的修改，已保留当前草稿，请保存或取消后刷新。"); return; }
			this.states = []; this.tbody.replaceChildren(); this.review.replaceChildren(); this.start = result.start; this.total = result.total;
			for (const archive of result.rows) this.appendRow(archive);
			if (this.blank.can_create) this.appendRow(structuredClone(this.blank));
			this.status.textContent = __("单击行内字段编辑；保存前可取消。空备注将采用工序名，已保存工序名只读。");
		} catch { if (!this.disposed && revision === this.revision) this.status.textContent = __("工序来源或权限已变化，未替换当前列表，请刷新后核对。"); }
		finally { if (!this.disposed && revision === this.revision) { this.loading = false; this.updatePager(); } }
	}
	appendRow(archive) {
		const state = { archive, controls: {}, busy: false, unresolved: false, knownConflict: false, confirming: false, candidate: null, conflict: null };
		state.review = this.element("div"); this.review.append(state.review);
		const row = this.element("tr"); state.row = row;
		for (const field of ["sequence", "operation", "remarks"]) {
			const cell = this.element("td"), df = archive.field_meta[field];
			if (df) {
				const input = this.element("input"); input.type = "text";
				input.setAttribute("aria-label", __({ sequence: "顺序", operation: "工序", remarks: "备注" }[field]));
				if (field === "sequence") { input.inputMode = "numeric"; input.maxLength = 16; }
				if (field === "operation") input.maxLength = 140;
				if (field === "remarks") { input.maxLength = 65535; input.placeholder = __("留空采用工序名"); }
				input.addEventListener("input", () => { this.freeze(state); });
				input.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); this.cancel(state); } });
				state.controls[field] = input; cell.append(input);
			} else if (field === "operation") cell.textContent = archive.name || "";
			row.append(cell);
		}
		state.actions = this.element("td"); state.owner = this.element("span", archive.owner || ""); state.actions.append(state.owner);
		state.saveButton = this.button("保存", () => this.save(state)); state.cancelButton = this.button("取消", () => this.cancel(state));
		state.resolveButton = this.button("核对保存结果", () => this.resolve(state)); state.adoptButton = this.button("采用已核对当前值", () => this.adopt(state));
		state.actions.append(state.saveButton, state.cancelButton, state.resolveButton, state.adoptButton); row.append(state.actions);
		state.creation = this.element("td"); state.modifier = this.element("td"); state.modified = this.element("td"); row.append(state.creation, state.modifier, state.modified);
		const deletion = this.element("td"); deletion.append(this.button("删除", null, "删除暂不可用，请先核对关联工艺与生产记录")); row.append(deletion);
		this.states.push(state); this.tbody.append(row); this.setValues(state); this.freeze(state); return state;
	}
	setValues(state) {
		for (const [field, control] of Object.entries(state.controls)) control.value = String(field === "operation" ? state.archive.name || "" : state.archive[field] ?? "");
		state.owner.textContent = state.archive.owner || ""; state.creation.textContent = state.archive.creation || "";
		const orderingNewer = !!(state.archive.ordering_version && state.archive.ordering_version > state.archive.version);
		state.modifier.textContent = (orderingNewer ? state.archive.ordering_modified_by : state.archive.modified_by) || "";
		state.modified.textContent = (orderingNewer ? state.archive.ordering_version : state.archive.version) || "";
	}
	dirty(state) {
		return ["sequence", "operation", "remarks"].some((field) => {
			const control = state.controls[field]; if (!control || control.readOnly || field === "operation" && state.archive.name) return false;
			const original = field === "operation" ? state.archive.name || "" : state.archive[field] ?? "";
			return control.value !== String(original).replace(/[\r\n]/g, "");
		});
	}
	isDirty() { return this.states.some((state) => state.unresolved || state.candidate && state.busy || this.dirty(state)); }
	getCloseBlock() {
		if (this.states.some((state) => state.busy || state.confirming)) return "正在保存或核对工序，请稍候。";
		if (this.states.some((state) => state.unresolved && !state.knownConflict)) return "工序保存结果尚未确认，请先核对保存结果。";
		return this.states.some((state) => state.unresolved) ? "工序版本已变化，请先核对当前值。" : "";
	}
	freeze(state) {
		const blocked = state.busy || state.unresolved || state.confirming;
		for (const [field, control] of Object.entries(state.controls)) control.readOnly = !!(blocked || !state.archive.can_write || state.archive.field_meta[field]?.read_only || field === "operation" && state.archive.name);
		const dirty = this.dirty(state);
		state.saveButton.hidden = state.cancelButton.hidden = !(dirty || state.busy); state.saveButton.disabled = blocked || !dirty;
		state.cancelButton.disabled = blocked; state.resolveButton.hidden = !state.unresolved; state.resolveButton.disabled = state.busy || state.confirming;
		state.adoptButton.hidden = !state.conflict; state.adoptButton.disabled = state.busy || state.confirming || !state.conflict;
	}
	requestID() {
		const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
		const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join(""); return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
	}
	payload(state) {
		const fresh = !state.archive.name, payload = { operation: fresh ? state.controls.operation?.value || "" : state.archive.name };
		if (fresh) payload.request_id = state.requestId || (state.requestId = this.requestID());
		else Object.assign(payload, { version: state.archive.version, ordering_name: state.archive.ordering_name, ordering_version: state.archive.ordering_version });
		for (const field of ["sequence", "remarks"]) {
			const control = state.controls[field], df = state.archive.field_meta[field];
			if (!control || !df || df.read_only || !state.archive.can_write) continue;
			const original = state.archive[field] ?? "";
			if (fresh || control.value !== String(original).replace(/[\r\n]/g, "")) {
				if (field === "sequence") {
					if (!/^\d{1,16}$/.test(control.value || "0") || Number(control.value || 0) > 2147483647) throw new Error("顺序须为零至2147483647的整数");
					payload.sequence = Number(control.value || 0);
				} else payload.remarks = control.value || payload.operation;
			}
		}
		if (!payload.operation || payload.operation.trim() !== payload.operation || payload.operation.length > 140) throw new Error("请填写准确的工序名，前后不要留空格");
		return payload;
	}
	matches(state, archive) {
		const held = state.candidate; if (!held || !this.validArchive(archive) || archive.name !== held.operation) return false;
		if (held.request_id ? archive.request_id !== held.request_id : archive.version === held.version && archive.ordering_version === held.ordering_version) return false;
		return ["sequence", "remarks"].every((field) => !(field in held) || archive.field_meta[field] && field in archive && archive[field] === held[field]);
	}
	definiteFailure(error) { return new Set(["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError", "UniqueValidationError", "DuplicateEntryError"]).has(error?.responseJSON?.exc_type); }
	async save(state) {
		if (this.disposed || state.busy || state.unresolved || state.confirming || !state.archive.can_write || !this.dirty(state)) return;
		try { state.candidate = structuredClone(this.payload(state)); } catch (error) { frappe.msgprint(__(error.message)); return; }
		this.invalidateQuery(); state.busy = true; state.conflict = null; state.knownConflict = false; state.review.replaceChildren(); this.freeze(state);
		try {
			const response = await this.call("save_operation", { payload: state.candidate });
			if (this.disposed || !this.states.includes(state)) return;
			if (this.matches(state, response.message)) this.accept(state, response.message); else state.unresolved = true;
		} catch (error) {
			if (!this.disposed) { state.knownConflict = error?.responseJSON?.exc_type === "TimestampMismatchError"; state.unresolved = state.knownConflict || !this.definiteFailure(error); }
		}
		finally {
			state.busy = false; this.freeze(state);
			if (state.unresolved) frappe.msgprint(__(state.knownConflict ? "工序版本已变化，本次保存未完成；草稿已保留，请核对当前值。" : "保存结果尚未确认，草稿已保留；请核对保存结果，不会重复保存。"));
			else if (!this.disposed && !this.isDirty()) this.query(this.start);
		}
	}
	accept(state, archive) {
		const message = state.knownConflict ? "已采用核对后的当前工序" : "工序已核对保存";
		state.archive = archive; state.candidate = null; state.unresolved = false; state.knownConflict = false; state.conflict = null; state.requestId = null;
		state.review.replaceChildren(); this.setValues(state); frappe.show_alert({ message: __(message), indicator: "green" });
	}
	async resolve(state) {
		if (this.disposed || state.busy || state.confirming || !state.unresolved || !state.candidate) return;
		state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try {
			const fresh = !!state.candidate.request_id;
			const response = await this.call(fresh ? "resolve_creation" : "read_operation", fresh ? { payload: state.candidate } : { operation: state.candidate.operation });
			if (this.disposed || !this.states.includes(state)) return;
			const reply = response.message;
			if (fresh && (reply?.scope !== "shared_operation" || reply.request_id !== state.candidate.request_id || !["found", "changed"].includes(reply.status))) return;
			const archive = fresh ? reply.operation : reply;
			if (this.matches(state, archive) && !state.knownConflict) { this.accept(state, archive); return; }
			if (!this.validArchive(archive) || !fresh && archive.name !== state.candidate.operation ||
				["sequence", "remarks"].some((field) => field in state.candidate && (!archive.field_meta[field] || !(field in archive)))) return;
			state.conflict = archive;
			state.review.append(this.element("p", state.candidate.operation + "：" + __(state.knownConflict ? "本次保存因版本变化被拒绝；核对当前值后可明确采用。" : "服务器当前值与保留草稿不同，核对后可明确采用当前值。")));
			const table = this.element("table"), header = this.element("tr");
			for (const label of ["字段", "保留草稿", "服务器当前值"]) header.append(this.element("th", __(label))); table.append(header);
			for (const [field, label] of [["operation", "工序"], ["sequence", "顺序"], ["remarks", "备注"]]) {
				if (field !== "operation" && !(field in state.candidate)) continue;
				const row = this.element("tr"); row.append(this.element("td", __(label)), this.element("td", state.candidate[field] ?? ""), this.element("td", field === "operation" ? archive.name : archive[field] ?? "")); table.append(row);
			}
			state.review.append(table);
		} catch { /* Read failure retains the held draft, with no write fallback. */ }
		finally { state.busy = false; this.freeze(state); if (!this.disposed && !this.isDirty()) this.query(this.start); }
	}
	adopt(state) {
		if (this.disposed || state.busy || state.confirming || !state.unresolved || !state.conflict) return;
		const archive = state.conflict; state.confirming = true; this.freeze(state);
		frappe.confirm(__("放弃保留草稿并采用已核对的当前工序？不会再次保存。"), () => {
			state.confirming = false; if (this.disposed || state.conflict !== archive) return;
			this.accept(state, archive); this.freeze(state); if (!this.isDirty()) this.query(this.start);
		}, () => { state.confirming = false; this.freeze(state); });
	}
	cancel(state) {
		if (state.busy || state.unresolved || state.confirming || this.disposed) return;
		this.setValues(state); state.candidate = null; state.conflict = null; state.knownConflict = false; state.requestId = null; state.review.replaceChildren(); this.freeze(state);
	}
	destroy() { this.disposed = true; this.revision++; window.removeEventListener("beforeunload", this.beforeUnload); }
};
