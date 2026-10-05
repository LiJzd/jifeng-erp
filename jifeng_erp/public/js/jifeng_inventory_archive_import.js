/* Shared A/B stock-archive import. Files and business rows stay in memory. */
window.JifengInventoryArchiveImport = class JifengInventoryArchiveImport {
	constructor(owner) {
		this.owner = owner; this.state = null; this.picker = null; this.disposed = false;
		this.storageKey = "jifeng-inventory-import-v1:" + encodeURIComponent(frappe.session?.user || "Guest");
		this.storageChanged = event => { if (event.key === this.storageKey) this.pendingChanged(); };
		this.beforeUnload = event => { if (this.state?.saving || this.state?.unresolved) { event.preventDefault(); event.returnValue = ""; } };
		window.addEventListener("storage", this.storageChanged); window.addEventListener("beforeunload", this.beforeUnload);
	}
	element(tag, text, className) { return this.owner.element(tag, text, className); }
	linkValue(control) {
		const value = control?.get_value?.() || "";
		// Native Link resolves a displayed title to the selected document ID.
		const input = control?.get_input_value?.() ?? control?.$input?.val?.();
		if (typeof input === "string" && input !== value) return null;
		return typeof value === "string" && !!value && value === value.trim() && [...value].length <= 140 && !/[\x00-\x1f\x7f]/.test(value) ? value : null;
	}
	companyValue() { return this.linkValue(this.owner.companyControl); }
	api(method, args, type = "GET") {
		return frappe.call({ method: "jifeng_erp.inventory_archive_import." + method, args, type }).then(response => response.message);
	}
	uuid(value) { return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value); }
	digest(value) { return typeof value === "string" && /^[0-9a-f]{64}$/.test(value); }
	async requestIDs(metadata) {
		return Promise.all(metadata.row_digests.map(async (_digest, index) => {
			const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode("jifeng-inventory-import:v1:" + metadata.batch_id + ":" + index))).slice(0, 16);
			bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
			const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
			return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
		}));
	}
	validMetadata(metadata) {
		return metadata && Object.keys(metadata).sort().join(",") === "batch_id,company,kind,row_count,row_digests,version" && metadata.version === 1 &&
			["物料", "材料"].includes(metadata.kind) && typeof metadata.company === "string" && !!metadata.company && this.uuid(metadata.batch_id) &&
			Number.isSafeInteger(metadata.row_count) && metadata.row_count > 0 && metadata.row_count <= 100 &&
			Array.isArray(metadata.row_digests) && metadata.row_digests.length === metadata.row_count && metadata.row_digests.every(value => this.digest(value));
	}
	sameMetadata(left, right) {
		return this.validMetadata(left) && this.validMetadata(right) && ["version", "kind", "company", "batch_id", "row_count"].every(field => left[field] === right[field]) &&
			left.row_digests.every((digest, index) => digest === right.row_digests[index]);
	}
	readPending() {
		const raw = localStorage.getItem(this.storageKey); if (!raw) return null;
		const entry = JSON.parse(raw);
		if (!entry || Object.keys(entry).sort().join(",") !== "metadata,phase,preview_digest" ||
			!["prepared", "dispatched"].includes(entry.phase) || !this.validMetadata(entry.metadata) || !this.digest(entry.preview_digest)) throw Error("Invalid pending import");
		return entry;
	}
	samePending(entry, state) {
		return !!entry && entry.preview_digest === state.previewDigest && this.sameMetadata(entry.metadata, state.metadata);
	}
	withLock(action) {
		if (!navigator.locks?.request) return Promise.reject(Error("Web Locks unavailable"));
		return navigator.locks.request(this.storageKey, { mode: "exclusive" }, action);
	}
	current(state) {
		return !this.disposed && this.state === state && !state.closed && !this.owner.disposed && this.owner.kind === state.kind &&
			this.owner.companyScope === state.company && this.companyValue() === state.company;
	}
	status(state, text) { if (this.state === state && !state.closed) state.status.textContent = __(text); }
	getCloseBlock() {
		const state = this.state;
		if (state?.saving || state?.unresolved) return __("导入结果尚未确认，请先核对导入结果。");
		if (state?.preparing || this.picker) return __("正在准备档案导入，请稍候。");
		return state && !state.closed ? __("请先关闭档案导入窗口。") : "";
	}
	open() {
		if (this.disposed || this.owner.disposed) return;
		if (this.state && !this.state.closed) { this.state.dialog.show(); return; }
		if (this.picker || this.owner.getCloseBlock()) return;
		this.owner.changeScope(); const company = this.companyValue(), kind = this.owner.kind;
		if (!company) { frappe.msgprint(__("请选择公司后导入。")); return; }
		let pending;
		try { pending = this.readPending(); } catch { frappe.msgprint(__("本机导入批次记录无法读取，暂不能开始新导入。")); return; }
		if (pending) { return this.openPending(pending); }
		// Preserve the observed old habit: clicking 导入 opens a single .xlsx chooser.
		const input = this.element("input"); input.type = "file"; input.accept = ".xlsx"; input.multiple = false; input.hidden = true;
		const revision = this.owner.revision; this.picker = input; document.body.append(input); this.owner.freezeExport();
		const cleanup = () => { input.remove(); if (this.picker === input) { this.picker = null; this.owner.freezeExport(); } };
		input.addEventListener("cancel", cleanup);
		input.addEventListener("change", () => {
			const files = [...(input.files || [])]; cleanup();
			if (files.length !== 1 || this.disposed || this.owner.disposed || this.owner.revision !== revision || this.owner.kind !== kind ||
				this.owner.companyScope !== company || this.companyValue() !== company) return;
			return this.openFile(files[0], company, kind, revision);
		});
		input.click();
	}
	makeDialog(company, kind, filename, recovering = false) {
		const dialog = new frappe.ui.Dialog({ title: __(kind + "档案导入"), size: "extra-large", no_submit_on_enter: true,
			fields: [{ fieldname: "company", fieldtype: "Data", label: __("公司"), read_only: 1 },
				{ fieldname: "kind", fieldtype: "Data", label: __("档案种类"), read_only: 1 },
				{ fieldname: "filename", fieldtype: "Data", label: __("文件"), read_only: 1 },
				{ fieldname: "item_group", fieldtype: "Link", options: "Item Group", label: __("新业务物料的默认商品分组"), reqd: !recovering, read_only: recovering },
				{ fieldname: "stock_uom", fieldtype: "Link", options: "UOM", label: __("新业务物料的默认库存单位"), reqd: !recovering, read_only: recovering },
				{ fieldname: "preview", fieldtype: "HTML" }] });
		dialog.$wrapper.addClass("jfe-inventory-catalog-detail jfe-inventory-archive-import");
		const body = dialog.fields_dict.preview.$wrapper.get(0), status = this.element("p", "正在准备导入…"); status.setAttribute("role", "status");
		const grid = this.element("div", undefined, "jfe-inventory-catalog-scroll"); grid.style.maxHeight = "45vh";
		body.replaceChildren(this.element("p", __("请按导入模板的列名准备文件；导入会新增档案和业务物料，不覆盖已存记录。档案单位与业务库存单位分别保存。")), status, grid);
		const state = { company, kind, filename, dialog, status, grid, closed: false, preparing: true, saving: false, unresolved: false,
			recovering, preview: null, metadata: null, previewDigest: null, dispatched: false };
		this.state = state; this.guard(state);
		dialog.add_custom_action(__("下载导入模板"), () => this.downloadTemplate(state.company, state.kind, state));
		dialog.add_custom_action(__("核对导入结果"), () => this.resolve(state));
		dialog.add_custom_action(__("关闭"), () => dialog.hide());
		dialog.set_primary_action(__("预览"), () => this.preview(state)); this.freeze(state); this.owner.freezeExport();
		return state;
	}
	async hydrate(state) {
		await state.dialog.set_values({ company: state.company, kind: state.kind, filename: state.filename });
	}
	validInit(result, state) {
		return result && result.kind === state.kind && result.company === state.company && result.max_file_bytes === 1048576 && result.max_rows === 100 &&
			["can_create_item", "can_select_item_group", "can_select_stock_uom"].every(field => typeof result[field] === "boolean") &&
			Array.isArray(result.columns) && result.columns.length > 0 && result.columns.length <= 30 &&
			new Set(result.columns.map(column => column?.fieldname)).size === result.columns.length &&
			result.columns.every(column => column && typeof column.fieldname === "string" && /^[a-z][a-z0-9_]*$/.test(column.fieldname) && typeof column.label === "string");
	}
	async openFile(file, company, kind, revision) {
		if (!/\.xlsx$/i.test(file.name) || !file.size || file.size > 1048576) { frappe.msgprint(__("请选择非空 .xlsx 文件，文件大小最多 1 MiB。")); return; }
		const state = this.makeDialog(company, kind, file.name);
		try {
			await this.hydrate(state);
			if (!this.current(state) || this.owner.revision !== revision) { this.discard(state); return; }
			state.dialog.show();
			const result = await this.api("init_import", { company, kind });
			if (!this.current(state)) { this.discard(state); return; }
			if (!this.validInit(result, state)) throw Error("Invalid import init");
			state.init = result;
			if (!result.can_create_item) { this.status(state, "没有新增业务物料的权限，不能导入。"); return; }
			const bytes = new Uint8Array(await file.arrayBuffer());
			if (!this.current(state)) { this.discard(state); return; }
			if (bytes.length !== file.size || bytes.length > result.max_file_bytes) throw Error("Invalid import file size");
			let binary = ""; for (let start = 0; start < bytes.length; start += 16384) binary += String.fromCharCode(...bytes.subarray(start, start + 16384));
			state.content = btoa(binary); state.batchId = crypto.randomUUID();
			if (!this.uuid(state.batchId)) throw Error("Invalid import batch id");
			this.status(state, "请选择新业务物料的默认商品分组和库存单位，再预览全部行。预览不保存档案。");
		} catch { if (this.current(state)) this.status(state, "文件准备失败，请关闭后重新选择文件，并核对导入权限。"); }
		finally { state.preparing = false; this.freeze(state); this.owner.freezeExport(); }
	}
	async openPending(entry) {
		const state = this.makeDialog(entry.metadata.company, entry.metadata.kind, "待核对批次 · " + entry.metadata.batch_id, true);
		state.metadata = entry.metadata; state.previewDigest = entry.preview_digest; state.dispatched = entry.phase === "dispatched"; state.unresolved = state.dispatched;
		try { await this.hydrate(state); state.requestIDs = await this.requestIDs(state.metadata); if (state.closed || this.disposed) return; state.dialog.show(); }
		finally { state.preparing = false; this.freeze(state); this.owner.freezeExport(); }
		await this.resolve(state);
	}
	freeze(state) {
		if (state.closed) return;
		const locked = state.preparing || state.saving || state.unresolved || !!state.preview || state.recovering || !this.current(state);
		for (const field of ["item_group", "stock_uom"]) state.dialog.set_df_property(field, "read_only", locked || !state.init?.[field === "item_group" ? "can_select_item_group" : "can_select_stock_uom"]);
		const ready = !locked && !state.completed && state.init?.can_create_item && !!state.content;
		const confirmable = !!state.preview && state.preview.can_commit === true && !state.completed && !state.preparing && !state.saving && !state.unresolved && !state.recovering && this.current(state);
		state.dialog.get_primary_btn().prop("disabled", !(ready || confirmable));
	}
	payload(state) {
		return { kind: state.kind, company: state.company, filename: state.filename, content_base64: state.content,
			item_group: this.linkValue(state.dialog.fields_dict.item_group), stock_uom: this.linkValue(state.dialog.fields_dict.stock_uom), batch_id: state.batchId };
	}
	validPreview(result, state) {
		if (!result || !Array.isArray(result.rows) || !Array.isArray(result.errors) || typeof result.can_commit !== "boolean" ||
			!Number.isSafeInteger(result.row_count) || result.row_count < 1 || result.row_count > 100 || result.rows.length !== result.row_count ||
			result.rows.some((row, index) => !row || row.row_index !== index || !Number.isSafeInteger(row.sheet_row) || row.sheet_row < 2 || !this.uuid(row.request_id) || typeof row.valid !== "boolean" ||
				!row.profile || Object.entries(row.profile).some(([key, value]) => !state.init.columns.some(column => column.fieldname === key) ||
					value !== null && !["string", "number", "boolean"].includes(typeof value) || typeof value === "number" && !Number.isFinite(value))) ||
			result.errors.some(error => !error || !Number.isSafeInteger(error.row_index) || error.row_index < 0 || error.row_index >= result.row_count ||
				error.sheet_row !== result.rows[error.row_index].sheet_row || typeof error.message !== "string")) return false;
		if (!result.can_commit) return result.errors.length > 0 && !result.preview_digest;
		return result.errors.length === 0 && result.rows.every(row => row.valid) && this.validMetadata(result.metadata) && result.metadata.company === state.company && result.metadata.kind === state.kind &&
			result.metadata.batch_id === state.batchId && result.metadata.row_count === result.row_count && this.digest(result.preview_digest);
	}
	renderPreview(state, result) {
		const table = this.element("table", undefined, "jfe-inventory-catalog-table"), head = this.element("thead"), headings = this.element("tr");
		for (const label of ["文件行", ...state.init.columns.map(column => column.label), "行错误"]) headings.append(this.element("th", label));
		head.append(headings); const body = this.element("tbody");
		for (const row of result.rows) {
			const tr = this.element("tr"); tr.append(this.element("td", row.sheet_row));
			for (const column of state.init.columns) tr.append(this.element("td", row.profile[column.fieldname] ?? ""));
			tr.append(this.element("td", result.errors.filter(error => error.row_index === row.row_index).map(error => error.message).join("；"))); body.append(tr);
		}
		table.append(head, body); state.grid.replaceChildren(table);
	}
	async preview(state) {
		if (!this.current(state) || state.preparing || state.saving || state.unresolved || state.preview || !state.content || !state.dialog.get_values()) return;
		const payload = this.payload(state);
		if (!payload.item_group || !payload.stock_uom) { this.status(state, "请选择新业务物料的默认商品分组和库存单位。"); return; }
		state.preparing = true; this.owner.freezeExport(); this.status(state, "正在预览全部行，尚未保存档案…");
		try {
			// Finish native control hydration before locking its cached values.
			await state.dialog.set_values({ item_group: payload.item_group, stock_uom: payload.stock_uom });
			if (!this.current(state)) return;
			this.freeze(state);
			const result = await this.api("preview_import", payload, "POST");
			if (!this.current(state)) return;
			if (!this.validPreview(result, state)) throw Error("Invalid import preview");
			this.renderPreview(state, result);
			if (!result.can_commit) { this.status(state, "预览发现行错误，未保存任何档案。请修正文件后重新选择导入。"); return; }
			state.metadata = result.metadata; state.previewDigest = result.preview_digest;
			state.requestIDs = await this.requestIDs(state.metadata);
			if (result.rows.some((row, index) => row.request_id !== state.requestIDs[index])) throw Error("Invalid import row identity");
			await this.withLock(() => {
				if (!this.current(state)) return;
				const pending = this.readPending(); if (pending && !this.samePending(pending, state)) throw Error("Another import is pending");
				if (pending?.phase === "dispatched") throw Error("Import already dispatched");
				localStorage.setItem(this.storageKey, JSON.stringify({ metadata: state.metadata, preview_digest: state.previewDigest, phase: "prepared" }));
				state.preview = result; state.attempt = payload;
			});
			if (!this.current(state) || !state.preview) return;
			state.dialog.set_primary_action(__("确认导入"), () => this.commit(state));
			this.status(state, "已预览全部 " + result.row_count + " 行。确认导入后将一次新增本批档案及业务物料；关闭会取消未发送批次。");
		} catch { if (this.current(state)) this.status(state, "预览未完成，未发送确认导入。请核对文件、权限及其他页面的待核对批次后重试。"); }
		finally { state.preparing = false; this.freeze(state); this.owner.freezeExport(); }
	}
	validReceipt(result, state) {
		return result && result.status === "found" && result.row_count === state.metadata.row_count && result.preview_digest === state.previewDigest &&
			this.sameMetadata(result.metadata, state.metadata) &&
			Array.isArray(result.receipts) && result.receipts.length === state.metadata.row_count && result.receipts.every((row, index) => row && row.row_index === index &&
				row.status === "found" && this.uuid(row.request_id) && row.request_id === state.requestIDs?.[index] &&
				typeof row.name === "string" && !!row.name && (row.item_code === null || typeof row.item_code === "string" && !!row.item_code)) &&
			new Set(result.receipts.map(row => row.name)).size === result.receipts.length;
	}
	async accepted(state, result) {
		if (!this.validReceipt(result, state)) throw Error("Unconfirmed import receipts");
		const pending = this.readPending();
		if (pending && !this.samePending(pending, state)) throw Error("Pending import identity changed");
		if (pending) localStorage.removeItem(this.storageKey);
		state.unresolved = false; state.dispatched = false; state.completed = true; state.content = null; state.attempt = null;
		state.dialog.set_primary_action(__("已导入"), () => {});
		this.status(state, "导入已确认，共新增 " + result.row_count + " 条档案。可以关闭窗口。");
		// Query the owner's current filters; a late receipt never restores an old company or filter.
		if (this.current(state)) this.owner.query(this.owner.page);
	}
	async commit(state) {
		if (!this.current(state) || state.preparing || state.saving || state.unresolved || state.completed || !state.preview?.can_commit) return;
		if (["item_group", "stock_uom"].some(field => this.linkValue(state.dialog.fields_dict[field]) !== state.attempt[field])) {
			this.status(state, "默认商品分组或库存单位已改变，本批未发送确认导入。请关闭后重新预览。"); return;
		}
		state.saving = true; this.freeze(state); this.owner.freezeExport();
		try {
			await this.withLock(async () => {
				if (!this.current(state)) return;
				const pending = this.readPending();
				if (this.samePending(pending, state) && pending.phase === "dispatched") { state.dispatched = true; state.unresolved = true; }
				if (!this.samePending(pending, state) || pending.phase !== "prepared") throw Error("Import cannot be dispatched again");
				// The durable marker precedes the sole commit POST. Unknown results never resend.
				localStorage.setItem(this.storageKey, JSON.stringify({ ...pending, phase: "dispatched" }));
				state.dispatched = true; state.unresolved = true; this.status(state, "正在确认导入，请勿关闭或重复提交…");
				const result = await this.api("commit_import", { ...state.attempt, preview_digest: state.previewDigest }, "POST");
				await this.accepted(state, result);
			});
		} catch { this.status(state, state.dispatched ? "导入结果尚未确认。请使用“核对导入结果”；不会自动重复提交。" : "本批未发送确认导入。请核对其他页面的批次记录，关闭本窗口后再继续。"); }
		finally { state.saving = false; this.freeze(state); this.owner.freezeExport(); }
	}
	async resolve(state) {
		if (state.closed || state.saving || state.preparing || !this.validMetadata(state.metadata) || !this.digest(state.previewDigest) || state.completed) return;
		state.saving = true; this.freeze(state); this.owner.freezeExport(); this.status(state, "正在只读核对导入批次…");
		try {
			await this.withLock(async () => {
				const result = await this.api("resolve_import", { kind: state.kind, company: state.company, batch_id: state.metadata.batch_id,
					row_count: state.metadata.row_count, preview_digest: state.previewDigest, row_digests: state.metadata.row_digests });
				if (state.closed || this.state !== state) return;
				if (this.validReceipt(result, state)) { await this.accepted(state, result); return; }
				if (result?.status === "partial" || result?.status === "found") { state.dispatched = true; state.unresolved = true; }
				this.status(state, result?.status === "partial" ? "仅查到部分档案，整批结果尚未确认，请保留批次并联系管理员核对。" :
					state.dispatched ? "尚未查到本批完整档案，结果仍未确认。请稍后再次核对；不能重复确认导入。" : "本批尚未发送确认导入。关闭窗口可取消该批次，再重新选择文件。");
			});
		} catch { this.status(state, "导入结果核对未完成，请保留本批记录并稍后再次核对。"); }
		finally { state.saving = false; this.freeze(state); this.owner.freezeExport(); }
	}
	guard(state) {
		const hide = state.dialog.hide.bind(state.dialog);
		state.dialog.hide = () => {
			if (state.closed) return;
			if (state.preparing || state.saving || state.unresolved) { frappe.msgprint(__(this.getCloseBlock())); return; }
			// Unreserved previews and confirmed imports need no storage access to close.
			// Closing them must not touch another page's batch.
			if (state.completed || !state.preview && !state.recovering && !state.dispatched) { this.discard(state, hide); return; }
			return this.withLock(() => {
				if (state.closed || state.saving || state.unresolved) return;
				const pending = this.readPending();
				if (this.samePending(pending, state)) {
					if (pending.phase === "dispatched") { state.dispatched = true; state.unresolved = true; this.freeze(state); this.status(state, "该批次已发送，请先核对导入结果。"); return; }
					localStorage.removeItem(this.storageKey);
				}
				this.discard(state, hide);
			}).catch(() => this.status(state, "批次记录无法核对，窗口暂不能关闭。请稍后重试。"));
		};
		state.dialog.$wrapper.modal({ backdrop: "static", keyboard: false, show: false });
		state.dialog.get_close_btn().removeAttr("data-dismiss").on("click.inventory-import", () => state.dialog.hide());
		state.dialog.$wrapper.on("keydown.inventory-import", event => { if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); state.dialog.hide(); } });
		state.dialog.on_hide = () => { if (!state.closed) { state.dialog.show(); state.dialog.hide(); } };
		state.hide = hide;
	}
	discard(state, hide = state.hide) {
		if (state.closed) return;
		state.closed = true; state.content = null; state.attempt = null; state.preview = null; state.grid.replaceChildren(); hide();
		if (this.state === state) this.state = null; this.owner.freezeExport();
	}
	pendingChanged() {
		const state = this.state; if (!state || state.closed || state.completed || !state.metadata) return;
		try {
			const entry = this.readPending();
			if (this.samePending(entry, state) && entry.phase === "dispatched") { state.dispatched = true; state.unresolved = true; }
			else if (!this.samePending(entry, state) && !state.dispatched) { state.preview = null; this.status(state, "批次记录已在其他页面改变。本窗口不能确认导入，请关闭后重新核对。"); }
		} catch { this.status(state, "批次记录无法读取，请保留窗口并核对导入结果。"); }
		this.freeze(state); this.owner.freezeExport();
	}
	scopeChanged() {
		const state = this.state;
		if (!state || state.closed || this.current(state)) return;
		this.freeze(state); this.status(state, state.dispatched ? "公司或页面已改变，仍须核对原批次结果；本窗口不会更新其他公司的列表。" : "公司或页面已改变。本批不能确认导入，请关闭后在目标公司重新选择文件。");
	}
	async downloadTemplate(company = this.companyValue(), kind = this.owner.kind, state = null) {
		if (!company || this.companyValue() !== company || this.disposed || state?.saving || state?.unresolved) return;
		const revision = this.owner.revision; let url;
		const current = () => !this.disposed && this.companyValue() === company && (state ? this.state === state && !state.closed : !this.owner.disposed && revision === this.owner.revision &&
			company === this.owner.companyScope && kind === this.owner.kind);
		try {
			const response = await fetch("/api/method/jifeng_erp.inventory_archive_import.download_template?" + new URLSearchParams({ company, kind }),
				{ method: "GET", credentials: "same-origin", headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" } });
			if (!current()) return;
			if (!response.ok || response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") throw Error("Template download failed");
			const blob = await response.blob(); if (!current() || !blob.size) return;
			url = URL.createObjectURL(blob); const link = this.element("a"); link.href = url; link.download = kind + "档案-导入模板.xlsx";
			document.body.append(link); link.click(); link.remove();
		} catch { if (current()) { if (state) this.status(state, "导入模板下载失败，请核对权限后重试。"); else frappe.msgprint(__("导入模板下载失败，请核对权限后重试。")); } }
		finally { if (url) setTimeout(() => URL.revokeObjectURL(url), 1000); }
	}
	destroy() {
		if (this.state?.saving || this.state?.unresolved || this.state?.preparing) return false;
		if (this.state && !this.state.closed && !this.state.completed && (this.state.preview || this.state.recovering || this.state.dispatched)) return false;
		this.disposed = true; if (this.picker) { this.picker.remove(); this.picker = null; }
		if (this.state) this.discard(this.state);
		window.removeEventListener("storage", this.storageChanged); window.removeEventListener("beforeunload", this.beforeUnload); return true;
	}
};
