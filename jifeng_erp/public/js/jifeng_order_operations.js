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
	call(method, args = {}, type = "POST") { return frappe.call({ method: "jifeng_erp.order_operations." + method, args, type }); }
	build() {
		this.host.classList.add("jfe-order-operations"); this.host.replaceChildren();
		this.status = this.element("p", __("正在读取工序…")); this.status.setAttribute("role", "status");
		const scope = this.element("p", __("共用工序；此页顺序用于排列，生产路线按各商品的 BOM 执行。已有 BOM 或报工引用的工序暂不支持改名。"), "jfe-order-operations-scope");
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
			this.status.textContent = __("单击行内字段编辑；备注和顺序保存前可取消，改名须单独确认。空备注将采用工序名。");
		} catch { if (!this.disposed && revision === this.revision) this.status.textContent = __("工序来源或权限已变化，未替换当前列表，请刷新后核对。"); }
		finally { if (!this.disposed && revision === this.revision) { this.loading = false; this.updatePager(); } }
	}
	appendRow(archive) {
		const state = { archive, controls: {}, busy: false, unresolved: false, knownConflict: false, confirming: false, candidate: null, renameCandidate: null, deleteCandidate: null, conflict: null };
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
		state.renameButton = this.button("确认改名", () => this.confirmRename(state));
		state.resolveButton = this.button("核对保存结果", () => this.resolve(state)); state.adoptButton = this.button("采用已核对当前值", () => this.adopt(state));
		state.actions.append(state.saveButton, state.renameButton, state.cancelButton, state.resolveButton, state.adoptButton); row.append(state.actions);
		state.creation = this.element("td"); state.modifier = this.element("td"); state.modified = this.element("td"); row.append(state.creation, state.modifier, state.modified);
		const deletion = this.element("td"); state.deleteButton = this.button("删除", () => this.confirmDelete(state)); deletion.append(state.deleteButton); row.append(deletion);
		this.states.push(state); this.tbody.append(row); this.setValues(state); this.freeze(state); return state;
	}
	setValues(state) {
		for (const [field, control] of Object.entries(state.controls)) control.value = String(field === "operation" ? state.archive.name || "" : state.archive[field] ?? "");
		state.owner.textContent = state.archive.owner || ""; state.creation.textContent = state.archive.creation || "";
		const orderingNewer = !!(state.archive.ordering_version && state.archive.ordering_version > state.archive.version);
		state.modifier.textContent = (orderingNewer ? state.archive.ordering_modified_by : state.archive.modified_by) || "";
		state.modified.textContent = (orderingNewer ? state.archive.ordering_version : state.archive.version) || "";
	}
	nameDirty(state) { return !!(state.archive.name && state.archive.can_rename && state.controls.operation?.value !== state.archive.name); }
	otherDirty(state) {
		return ["sequence", "remarks"].some((field) => {
			const control = state.controls[field]; if (!control || state.archive.field_meta[field]?.read_only || !state.archive.can_write) return false;
			return control.value !== String(state.archive[field] ?? "").replace(/[\r\n]/g, "");
		});
	}
	dirty(state) {
		return this.nameDirty(state) || this.otherDirty(state) || !!(!state.archive.name && state.archive.can_write &&
			!state.archive.field_meta.operation?.read_only && state.controls.operation?.value);
	}
	isDirty() { return this.states.some((state) => state.unresolved || state.candidate && state.busy || this.dirty(state)); }
	getCloseBlock() {
		if (this.states.some((state) => state.busy || state.confirming)) return "正在保存或核对工序，请稍候。";
		if (this.states.some((state) => state.unresolved && !state.knownConflict)) return "工序保存结果尚未确认，请先核对保存结果。";
		return this.states.some((state) => state.unresolved) ? "工序版本已变化，请先核对当前值。" : "";
	}
	freeze(state) {
		const blocked = state.busy || state.unresolved || state.confirming;
		const renaming = this.nameDirty(state), otherDirty = this.otherDirty(state);
		for (const [field, control] of Object.entries(state.controls)) control.readOnly = !!(blocked || state.archive.field_meta[field]?.read_only ||
			(field === "operation" && state.archive.name ? !state.archive.can_rename || otherDirty : !state.archive.can_write || renaming));
		const dirty = this.dirty(state);
		state.saveButton.hidden = !(dirty || state.busy) || renaming; state.saveButton.disabled = blocked || !dirty || renaming;
		state.renameButton.hidden = !renaming; state.renameButton.disabled = blocked || otherDirty;
		state.cancelButton.hidden = !(dirty || state.busy);
		state.cancelButton.disabled = blocked; state.resolveButton.hidden = !state.unresolved; state.resolveButton.disabled = state.busy || state.confirming;
		state.adoptButton.hidden = !state.conflict; state.adoptButton.disabled = state.busy || state.confirming || !state.conflict;
		state.deleteButton.disabled = blocked || dirty || state.archive.can_delete !== true || !state.archive.name || !state.archive.creation;
		state.deleteButton.title = state.archive.can_delete === true ? __("仅无业务引用的工序可删除；新建回执与删除记录永久保留。") : __("没有删除工序及保存删除记录所需的权限或创建身份。");
		state.resolveButton.textContent = __(state.deleteCandidate ? "核对删除结果" : "核对保存结果");
	}
	requestID() {
		const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
		const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join(""); return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
	}
	payload(state) {
		if (this.nameDirty(state)) throw new Error("请单独确认改名，不与备注或顺序一起保存");
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
		const message = state.renameCandidate ? "已核对工序当前名称" : state.knownConflict ? "已采用核对后的当前工序" : "工序已核对保存";
		state.archive = archive; state.candidate = null; state.renameCandidate = null; state.unresolved = false; state.knownConflict = false; state.conflict = null; state.requestId = null;
		state.review.replaceChildren(); this.setValues(state); frappe.show_alert({ message: __(message), indicator: "green" });
	}
	async resolve(state) {
		if (state.deleteCandidate) return this.resolveDelete(state);
		if (state.renameCandidate) return this.resolveRename(state);
		if (this.disposed || state.busy || state.confirming || !state.unresolved || !state.candidate) return;
		state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try {
			const fresh = !!state.candidate.request_id;
			const response = await this.call(fresh ? "resolve_creation" : "read_operation", fresh ? { payload: state.candidate } : { operation: state.candidate.operation });
			if (this.disposed || !this.states.includes(state)) return;
			const reply = response.message;
			if (fresh && reply?.scope === "shared_operation" && reply.request_id === state.candidate.request_id && reply.status === "deleted" && reply.operation === null) {
				state.review.append(this.element("p", __("该新建请求对应的工序已删除；旧请求不会再次保存或认领同名新工序。")));
				if (reply.current_state === "restored" || reply.current_state === "replaced") state.review.append(this.element("p", __(reply.current_state === "restored" ? "同一工序现已通过原生流程恢复，请刷新后核对。" : "当前同名工序是另一创建身份，请刷新后核对。")));
				state.review.append(this.button("放弃已删除工序的草稿", () => this.discardDeletedCreation(state)));
				return;
			}
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
		this.setValues(state); state.candidate = null; state.renameCandidate = null; state.conflict = null; state.knownConflict = false; state.requestId = null; state.review.replaceChildren(); this.freeze(state);
	}
	confirmRename(state) {
		if (this.disposed || state.busy || state.unresolved || state.confirming || !this.nameDirty(state) || this.otherDirty(state)) return;
		const newName = state.controls.operation.value;
		if (!newName || newName.trim() !== newName || newName.length > 140) { frappe.msgprint(__("请填写准确的工序名，前后不要留空格")); return; }
		const held = { operation: state.archive.name, new_name: newName, version: state.archive.version,
			ordering_name: state.archive.ordering_name, ordering_version: state.archive.ordering_version };
		const escape = (value) => value.replace(/[&<>"']/g, (letter) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[letter]));
		state.confirming = true; this.freeze(state);
		frappe.confirm(__("确认将工序") + " “" + escape(held.operation) + "” " + __("改为") + " “" + escape(newName) + "”？", () => {
			state.confirming = false;
			if (this.disposed || !this.states.includes(state)) return;
			return this.rename(state, held);
		}, () => { state.confirming = false; this.freeze(state); });
	}
	async rename(state, held) {
		if (this.disposed || state.busy || state.unresolved || !state.archive.can_rename || !this.nameDirty(state) || this.otherDirty(state) ||
			state.archive.name !== held.operation || state.archive.version !== held.version || state.archive.ordering_name !== held.ordering_name ||
			state.archive.ordering_version !== held.ordering_version || state.controls.operation.value !== held.new_name) { this.freeze(state); return; }
		this.invalidateQuery(); state.renameCandidate = structuredClone(held); state.busy = true; state.conflict = null; state.knownConflict = false; this.freeze(state);
		try {
			const response = await this.call("rename_operation", { payload: state.renameCandidate }), reply = response.message, archive = reply?.operation;
			if (this.disposed || !this.states.includes(state)) return;
			if (reply?.scope === "shared_operation" && reply.old_name === held.operation && reply.new_name === held.new_name && this.validArchive(archive) &&
				archive.name === held.new_name && archive.ordering_name === held.ordering_name && archive.version === held.version && archive.ordering_version === held.ordering_version) this.accept(state, archive);
			else state.unresolved = true;
		} catch (error) {
			state.knownConflict = error?.responseJSON?.exc_type === "TimestampMismatchError";
			state.unresolved = state.knownConflict || !this.definiteFailure(error);
			if (!state.unresolved) state.renameCandidate = null;
		} finally {
			state.busy = false; this.freeze(state);
			if (state.unresolved) frappe.msgprint(__("改名结果尚未确认，请核对当前工序；本次改名不会重复提交。"));
			else if (!this.disposed && !this.isDirty()) this.query(this.start);
		}
	}
	async resolveRename(state) {
		if (this.disposed || state.busy || state.confirming || !state.unresolved || !state.renameCandidate) return;
		const held = state.renameCandidate; state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try {
			const response = await this.call("resolve_rename", { payload: held }), reply = response.message, archive = reply?.operation;
			if (this.disposed || !this.states.includes(state) || reply?.scope !== "shared_operation" || reply.old_name !== held.operation || reply.new_name !== held.new_name) return;
			if (held.ordering_name && reply.status === "current" && this.validArchive(archive) && archive.ordering_name === held.ordering_name) {
				if (archive.name === held.new_name) { this.accept(state, archive); return; }
				if (state.knownConflict) { state.conflict = archive; state.review.append(this.element("p", __("版本已变化，请核对当前工序名称：") + archive.name)); return; }
			}
			state.review.append(this.element("p", __(held.ordering_name ? "当前结果不足以确认本次改名，请人工核对；不会重复提交。" : "原工序没有稳定排序身份，无法确认本次改名，请人工核对；不会按目标名称猜测或重复提交。")));
		} catch { /* Keep the original rename request; there is no POST fallback. */ }
		finally { state.busy = false; this.freeze(state); if (!this.disposed && !this.isDirty()) this.query(this.start); }
	}
	discardDeletedCreation(state) {
		if (this.disposed || state.busy || state.confirming || !state.unresolved || !state.candidate?.request_id) return;
		const held = state.candidate; state.confirming = true; this.freeze(state);
		frappe.confirm(__("确认放弃已核对删除记录的工序草稿？不会再次保存。"), () => {
			state.confirming = false;
			if (this.disposed || !this.states.includes(state) || state.candidate !== held) return;
			state.archive = structuredClone(this.blank); state.candidate = null; state.requestId = null; state.unresolved = false;
			state.review.replaceChildren(); this.setValues(state); this.freeze(state); if (!this.isDirty()) this.query(this.start);
		}, () => { state.confirming = false; this.freeze(state); });
	}
	confirmDelete(state) {
		if (this.disposed || !this.states.includes(state) || state.busy || state.unresolved || state.confirming || this.dirty(state) || state.archive.can_delete !== true || !state.archive.name || !state.archive.creation) return;
		const held = { operation: state.archive.name, creation: state.archive.creation, version: state.archive.version,
			ordering_name: state.archive.ordering_name, ordering_version: state.archive.ordering_version, request_id: this.requestID() };
		const escape = (value) => value.replace(/[&<>"']/g, (letter) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[letter]));
		state.confirming = true; this.freeze(state);
		frappe.confirm(__("确认删除工序") + " “" + escape(held.operation) + "”？" + __("已有业务引用时会拒绝删除；新建回执与删除记录将永久保留。"), () => {
			state.confirming = false; return this.deleteOperation(state, held);
		}, () => { state.confirming = false; this.freeze(state); });
	}
	deletionMatches(reply, held) {
		return reply?.scope === "shared_operation" && reply.status === "deleted" && reply.operation === held.operation && reply.creation === held.creation &&
			reply.version === held.version && reply.request_id === held.request_id && typeof reply.ordering_name === "string" && !!reply.ordering_name &&
			(held.ordering_name === null || reply.ordering_name === held.ordering_name);
	}
	removeDeleted(state, reply) {
		const held = state.deleteCandidate;
		if (!held || state.archive.name !== held.operation || state.archive.creation !== held.creation || state.archive.version !== held.version ||
			state.archive.ordering_name !== held.ordering_name || state.archive.ordering_version !== held.ordering_version) {
			// The receipt proves this past request, never ownership of a newer row.
			state.deleteCandidate = null; state.unresolved = false;
			state.review.append(this.element("p", __("已核对原删除请求，当前行身份已变化，已保留当前工序，请刷新核对。")));
			return;
		}
		this.states = this.states.filter((row) => row !== state); state.row.remove(); state.review.remove();
		this.total = Math.max(0, this.total - 1); this.updatePager();
		frappe.show_alert({ message: __(reply?.current_state === "restored" ? "已核对删除记录；同一工序现已恢复，请核对刷新结果" : reply?.current_state === "replaced" ? "已核对删除记录；当前同名工序是另一创建身份" : "已核对工序删除，回执与删除记录已保留"), indicator: "green" });
	}
	async deleteOperation(state, held) {
		if (this.disposed || !this.states.includes(state) || state.busy || state.unresolved || this.dirty(state) || state.archive.can_delete !== true ||
			state.archive.name !== held.operation || state.archive.creation !== held.creation || state.archive.version !== held.version ||
			state.archive.ordering_name !== held.ordering_name || state.archive.ordering_version !== held.ordering_version) { this.freeze(state); return; }
		this.invalidateQuery(); state.deleteCandidate = structuredClone(held); state.busy = true; state.conflict = null; state.knownConflict = false; this.freeze(state);
		try {
			const response = await this.call("delete_operation", { payload: state.deleteCandidate });
			if (this.disposed || !this.states.includes(state)) return;
			if (this.deletionMatches(response.message, held)) this.removeDeleted(state, response.message); else state.unresolved = true;
		} catch (error) {
			if (!this.disposed && this.states.includes(state)) {
				state.unresolved = !this.definiteFailure(error); if (!state.unresolved) state.deleteCandidate = null;
			}
		} finally {
			state.busy = false; this.freeze(state);
			if (!this.disposed && state.unresolved) frappe.msgprint(__("删除结果尚未确认，请只读核对删除记录；不会重复删除。"));
			else if (!this.disposed && !this.isDirty()) this.query(this.start);
		}
	}
	async resolveDelete(state) {
		if (this.disposed || !this.states.includes(state) || state.busy || state.confirming || !state.unresolved || !state.deleteCandidate) return;
		const held = state.deleteCandidate; state.busy = true; state.conflict = null; state.review.replaceChildren(); this.freeze(state);
		try {
			const response = await this.call("resolve_delete", { payload: held }, "GET"), reply = response.message;
			if (this.disposed || !this.states.includes(state)) return;
			if (this.deletionMatches(reply, held)) { this.removeDeleted(state, reply); return; }
			state.review.append(this.element("p", __("未取得匹配的删除记录，当前名称存在或缺失均不能确认本次删除；请人工核对，不会重复提交。")));
		} catch { /* Retain the exact request; absence or read denial never permits retry. */ }
		finally { state.busy = false; this.freeze(state); if (!this.disposed && !this.isDirty()) this.query(this.start); }
	}
	destroy() { this.disposed = true; this.revision++; window.removeEventListener("beforeunload", this.beforeUnload); }
};
