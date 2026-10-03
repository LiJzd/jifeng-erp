/* A/B share native sample Forms. Commercial quantities and prices are explicit. */
(() => {
	"use strict";
	const TYPE = "Jifeng Sample Order", held = new Map();
	const METHODS = {
		work_order: { doctype: "Work Order", create: "jifeng_erp.sample_orders.make_sample_work_order", resolve: "jifeng_erp.sample_orders.get_sample_work_order_request" },
		quotation: { doctype: "Quotation", create: "jifeng_erp.sample_quotations.make_sample_quotation", resolve: "jifeng_erp.sample_quotations.resolve_sample_quotation" }
	};
	const definiteTypes = new Set(["ValidationError", "PermissionError", "TimestampMismatchError", "MandatoryError", "LinkValidationError", "DataError", "DuplicateEntryError", "UniqueValidationError"]);
	const text = (value) => typeof value === "string" && !!value && value.trim() === value;
	const checked = (value) => value === true || value === 1 || value === "1";
	const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
	const recoveryError = () => new Error("本地生成回执当前无法安全保存或读取，已阻止重复提交。请允许本地存储，并使用支持安全锁的 HTTPS 浏览器窗口后再核对。");
	function storage() {
		try { if (window.localStorage) return window.localStorage; } catch {}
		throw recoveryError();
	}
	function storageKey(user, name, kind) { return "jifeng-sample-request-v1:" + JSON.stringify([user, name, kind]); }
	function inputValues(scope, kind, candidate, values) {
		if (kind === "work_order") return { sample_item: candidate.sample_item, bom: candidate.bom || "" };
		const selected = new Map(JSON.parse(candidate.items).map((row) => [row.sample_item, row]));
		const edited = new Map((values?.items || []).map((row) => [row.sample_item, row]));
		return { customer: candidate.customer, currency: candidate.currency, valid_till: candidate.valid_till,
			items: scope.rows.map((origin) => {
				const row = edited.get(origin.name) || selected.get(origin.name);
				return { include: selected.has(origin.name) ? 1 : 0, sample_item: origin.name,
					qty: row?.qty ?? "", uom: row?.uom ?? "", rate: row?.rate ?? "", make_flow: checked(row?.make_flow) ? 1 : 0 };
			}) };
	}
	function readRecord(user, name, kind) {
		try {
			const raw = storage().getItem(storageKey(user, name, kind)); if (raw === null) return null;
			const record = JSON.parse(raw), scope = record.scope;
			if (record.schema !== 1 || record.user !== user || record.kind !== kind || !scope || scope.sample_order !== name ||
				!text(scope.version) || !text(scope.company) || !Array.isArray(scope.rows) || !scope.rows.length ||
				scope.rows.some((row) => !text(row.name) || !text(row.item_code)) || new Set(scope.rows.map((row) => row.name)).size !== scope.rows.length ||
				!/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/.test(record.request_id || "") || record.candidate?.request_id !== record.request_id)
				throw recoveryError();
			const expected = kind === "quotation" ? quotationPayload(scope, record.inputs) : workOrderPayload(scope, record.inputs);
			if (Object.keys(record.candidate).length !== Object.keys(expected).length + 1 || Object.keys(expected).some((field) => expected[field] !== record.candidate[field])) throw recoveryError();
			return record;
		} catch { throw recoveryError(); }
	}
	async function locked(user, name, kind, action) {
		if (!text(user) || user === "Guest" || typeof window.navigator?.locks?.request !== "function") throw recoveryError();
		return window.navigator.locks.request(storageKey(user, name, kind), { mode: "exclusive", ifAvailable: true }, (lock) => {
			if (!lock) return { busy: true };
			return action(); // Only synchronous local receipt operations; never hold a lock across HTTP.
		});
	}
	function adopt(state, record) {
		state.dialog?.hide(); state.dialog = null; state.editFields = [];
		state.scope = record.scope; state.requestId = record.request_id; state.candidate = record.candidate;
		state.inputs = record.inputs; state.phase = "unknown";
	}
	function rejectionReceipt(state) {
		try {
			const raw = storage().getItem(storageKey(state.user, state.scope.sample_order, state.kind) + ":rejected:" + state.requestId);
			if (raw === null) return false;
			const rejected = JSON.parse(raw);
			if (rejected.schema !== 1 || rejected.status !== "rejected" || rejected.user !== state.user || rejected.kind !== state.kind ||
				rejected.sample_order !== state.scope.sample_order || rejected.version !== state.scope.version || rejected.company !== state.scope.company || rejected.request_id !== state.requestId)
				throw recoveryError();
			return true;
		} catch { throw recoveryError(); }
	}
	async function release(state, rejected = false) {
		const result = await locked(state.user, state.scope.sample_order, state.kind, () => {
			const current = readRecord(state.user, state.scope.sample_order, state.kind);
			if (rejected) {
				const key = storageKey(state.user, state.scope.sample_order, state.kind) + ":rejected:" + state.requestId;
				const proof = JSON.stringify({ schema: 1, status: "rejected", user: state.user, kind: state.kind,
					sample_order: state.scope.sample_order, version: state.scope.version, company: state.scope.company, request_id: state.requestId });
				storage().setItem(key, proof); if (storage().getItem(key) !== proof) throw recoveryError();
			}
			if (current?.request_id === state.requestId) {
				const key = storageKey(state.user, state.scope.sample_order, state.kind);
				storage().removeItem(key); if (storage().getItem(key) !== null) throw recoveryError();
			}
			return { released: true };
		});
		if (result.busy) throw recoveryError();
	}
	function uuid() {
		if (crypto.randomUUID) return crypto.randomUUID();
		const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
		bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
		const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
		return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
	}
	function context(frm) {
		if (frm.is_new() || frm.is_dirty() || frm.doc.docstatus !== 1 || !text(frm.doc.name) || !text(frm.doc.modified) || !text(frm.doc.company))
			throw new Error("请先保存并审核样品制作单，再生成业务单据。");
		const rows = frm.doc.items;
		if (!Array.isArray(rows) || !rows.length || rows.some((row) => !text(row.name) || !text(row.item_code)) || new Set(rows.map((row) => row.name)).size !== rows.length)
			throw new Error("样品行来源不完整，请重新打开已批准的样品制作单。");
		return { sample_order: frm.doc.name, version: frm.doc.modified, company: frm.doc.company, rows: rows.map((row) => ({ ...row })) };
	}
	function decimal(value, positive, label) {
		if (typeof value !== "string" || !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) || positive && /^[0.]+$/.test(value))
			throw new Error(label + (positive ? "须明确填写大于零的十进制数。" : "须明确填写不小于零的十进制数；零价请填写0。"));
		return value;
	}
	function date(value) {
		if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) throw new Error("请明确填写报价有效期。");
		const parsed = new Date(value + "T00:00:00Z");
		if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) throw new Error("报价有效期不是有效日期。");
		return value;
	}
	function quotationPayload(scope, values) {
		if (!text(values.customer) || !text(values.currency)) throw new Error("请明确选择报价客户和币种。");
		const items = [], seen = new Set(), rows = new Map(scope.rows.map((row) => [row.name, row]));
		for (const row of values.items || []) {
			if (!checked(row.include)) continue;
			if (!text(row.sample_item) || !rows.has(row.sample_item) || seen.has(row.sample_item)) throw new Error("每条报价须对应当前样品制作单的准确子行，同一子行不能重复选择。");
			seen.add(row.sample_item);
			if (!text(row.uom)) throw new Error("请为所选样品行明确填写报价单位。");
			items.push({ sample_item: row.sample_item, qty: decimal(row.qty, true, "报价数量"), uom: row.uom,
				rate: decimal(row.rate, false, "报价单价"), make_flow: checked(row.make_flow) ? 1 : 0 });
		}
		if (!items.length) throw new Error("请至少选择一条样品行，并填写其报价数量、单位和单价。");
		return { sample_order: scope.sample_order, version: scope.version, customer: values.customer,
			currency: values.currency, valid_till: date(values.valid_till), items: JSON.stringify(items) };
	}
	function workOrderPayload(scope, values) {
		if (!text(values.sample_item) || !scope.rows.some((row) => row.name === values.sample_item)) throw new Error("请明确选择当前样品制作单的准确子行。");
		if (values.bom && !text(values.bom)) throw new Error("请填写准确的BOM编号。");
		return { sample_order: scope.sample_order, sample_item: values.sample_item, version: scope.version, bom: values.bom || null };
	}
	function definiteFailure(error) {
		const status = Number(error?.status || error?.xhr?.status || error?.response?.status);
		return status >= 400 && status < 500 && definiteTypes.has(error?.responseJSON?.exc_type) && error?.responseJSON?.outcome_requires_inspection !== true;
	}
	function receipt(message, kind, requestId) {
		if (!message || message.doctype !== METHODS[kind].doctype || message.request_id !== requestId || !["found", "absent", ...(kind === "quotation" ? ["deleted"] : [])].includes(message.status)) throw new Error("生成回执不完整。");
		if (message.status === "found" && !text(message.name)) throw new Error("生成回执缺少准确对象编号。");
		if (message.status === "absent" && message.name != null) throw new Error("未命中回执不应带对象编号。");
		return message;
	}
	function key(frm, kind) { return String(frappe.session?.user || "") + "\u0000" + frm.doc.name + "\u0000" + kind; }
	function notify(message, indicator = "orange") { frappe.show_alert({ message: __(message), indicator }); }
	function renderState(state) {
		if (!state.dialog) return;
		const locked = state.phase === "preparing" || state.phase === "sending" || state.phase === "unknown" || state.phase === "resolving" || state.phase === "found";
		for (const name of state.editFields) state.dialog.set_df_property(name, "read_only", locked ? 1 : 0);
		state.dialog.set_primary_action(__(state.phase === "unknown" || state.phase === "resolving" ? "核对生成结果" : state.phase === "found" ? "打开已生成单据" : state.kind === "quotation" ? "生成报价草稿" : "生成样品工单"), () => {
			if (state.phase === "unknown") return state.resolve();
			if (state.phase === "found") return state.open();
			if (state.phase === "editing") return state.submit();
		});
		const primary = state.dialog.get_primary_btn?.();
		if (primary?.prop) primary.prop("disabled", state.phase === "preparing" || state.phase === "sending" || state.phase === "resolving");
	}
	class Request {
		constructor(frm, kind, scope) {
			this.frm = frm; this.kind = kind; this.scope = scope; this.phase = "editing";
			this.user = frappe.session?.user; this.requestId = null; this.candidate = null; this.inputs = null;
			this.result = null; this.dialog = null; this.editFields = [];
		}
		matchesCurrent() {
			return this.frm.doc.name === this.scope.sample_order && this.frm.doc.modified === this.scope.version && this.frm.doc.company === this.scope.company && this.frm.doc.docstatus === 1 && !this.frm.is_dirty();
		}
		async create(payload) {
			if (this.phase !== "editing") return false;
			if (!this.matchesCurrent()) { notify("样品版本或公司已变化，请重新打开已批准的制作单后核对。"); return false; }
			this.phase = "preparing"; renderState(this);
			try {
				const prepared = await locked(this.user, this.scope.sample_order, this.kind, () => {
					if (!this.matchesCurrent() || this.user !== frappe.session?.user) throw new Error("样品版本、公司或登录用户已变化，请重新打开后核对。");
					const previous = readRecord(this.user, this.scope.sample_order, this.kind); if (previous) return { record: previous };
					const requestId = uuid(), candidate = { ...payload, request_id: requestId };
					const scope = { ...this.scope, rows: this.scope.rows.map((row) => ({ name: row.name, item_code: row.item_code, qty: row.qty, stock_uom: row.stock_uom })) };
					const record = { schema: 1, user: this.user, kind: this.kind, scope, request_id: requestId, candidate,
						inputs: inputValues(scope, this.kind, candidate, this.dialog?.get_values()) };
					const key = storageKey(this.user, scope.sample_order, this.kind), serialized = JSON.stringify(record);
					storage().setItem(key, serialized); if (storage().getItem(key) !== serialized) throw recoveryError();
					return { candidate, requestId, inputs: record.inputs };
				});
				if (prepared.busy) { this.phase = "editing"; notify("另一窗口正在处理生成回执，当前未提交。请稍后重新打开生成窗口核对。"); renderState(this); return false; }
				if (prepared.record) { adopt(this, prepared.record); show(this.frm, this.kind); notify("已恢复同一账号的待核对请求，请核对原生成结果；不会新建或重复提交。"); return false; }
				this.requestId = prepared.requestId; this.candidate = prepared.candidate; this.inputs = prepared.inputs;
			} catch (error) {
				this.phase = "editing";
				notify(error.message === "样品版本、公司或登录用户已变化，请重新打开后核对。" ? error.message : recoveryError().message);
				renderState(this); return false;
			}
			this.phase = "sending"; renderState(this);
			try {
				const response = await frappe.call({ method: METHODS[this.kind].create, type: "POST", args: this.candidate });
				const result = receipt(response.message, this.kind, this.requestId);
				if (result.status !== "found") throw new Error("创建尚未确认。");
				await release(this); this.found(result); return true;
			} catch (error) {
				if (definiteFailure(error)) {
					try {
						await release(this, true); this.phase = "editing"; this.requestId = null; this.candidate = null; this.inputs = null;
						notify("服务器已明确拒绝生成，请核对并修改输入后再次生成。");
					} catch { this.phase = "unknown"; notify("服务器已明确拒绝，但本地回执尚未释放。请恢复本地存储后核对，不要重复生成。"); }
				} else {
					this.phase = "unknown";
					notify("生成结果尚未确认，已保留输入和请求编号。请点击“核对生成结果”，不要重复生成。");
				}
				return false;
			} finally { renderState(this); }
		}
		async resolve() {
			if (this.phase !== "unknown" || !this.requestId) return false;
			if (this.user !== frappe.session?.user) { notify("登录用户已变化，请使用原账号核对该生成请求。"); return false; }
			this.phase = "resolving"; renderState(this);
			try {
				if (rejectionReceipt(this)) {
					await release(this); this.phase = "editing"; this.requestId = null; this.candidate = null; this.inputs = null;
					notify("已同步另一窗口的服务器明确拒绝，原输入可修改后重新生成；当前未提交。"); return true;
				}
				const response = await frappe.call({ method: METHODS[this.kind].resolve, type: "GET", args: { request_id: this.requestId } });
				const result = receipt(response.message, this.kind, this.requestId);
				if (result.status === "found") { await release(this); this.found(result); return true; }
				if (result.status === "deleted") {
					await release(this); this.phase = "deleted"; this.dialog?.hide();
					notify("已确认该请求的报价草稿被删除。如需新建报价，请重新打开生成窗口。不会重复原请求。");
					return true;
				}
				notify("暂未查到已生成单据，原请求编号和输入继续保留；请稍后再核对，不要重复生成。");
			} catch { notify("当前无法核对生成结果，请核对查看权限后再查询；不要重复生成。"); }
			finally { if (this.phase === "resolving") this.phase = "unknown"; renderState(this); }
			return false;
		}
		found(result) {
			this.result = { doctype: result.doctype, name: result.name }; this.phase = "found";
			notify(this.kind === "quotation" ? "报价草稿已生成，请核对后保存、审核。" : "样品工单已生成，请核对后安排制作。", "green");
			this.open();
		}
		open() {
			if (!this.result || this.phase !== "found") return;
			this.dialog?.hide(); frappe.set_route("Form", this.result.doctype, this.result.name);
		}
	}
	function stateFor(frm, kind) {
		const id = key(frm, kind), existing = held.get(id);
		if (existing && ["preparing", "sending", "unknown", "resolving"].includes(existing.phase)) { existing.frm = frm; return existing; }
		const record = readRecord(frappe.session?.user, frm.doc.name, kind);
		if (record) { const state = existing || new Request(frm, kind, record.scope); state.frm = frm; adopt(state, record); held.set(id, state); return state; }
		if (existing?.phase === "editing" && existing.dialog && existing.matchesCurrent()) return existing;
		const state = new Request(frm, kind, context(frm)); held.set(id, state); return state;
	}
	function makeWorkOrderDialog(state) {
		const fields = [
			{ fieldname: "instructions", fieldtype: "HTML", options: "<p>" + __("选择样品明细；工单数量采用该行已审核的制作数量。BOM留空时采用该行已选BOM；该行尚未选择BOM时，请在此明确选择。") + "</p>" },
			{ fieldname: "sample_item", fieldtype: "Select", label: __("样品行编号"), options: ["", ...state.scope.rows.map((row) => row.name)], reqd: 1, default: state.inputs?.sample_item || "",
				description: state.scope.rows.map((row, index) => `${index + 1}. ${escapeHTML(row.name)} / ${escapeHTML(row.item_code)} / 制作 ${escapeHTML(row.qty)} ${escapeHTML(row.stock_uom)}`).join("<br>") },
			{ fieldname: "bom", fieldtype: "Link", options: "BOM", label: __("BOM（可选）"), default: state.inputs?.bom || "" }
		];
		state.dialog = new frappe.ui.Dialog({ title: __("生成样品工单"), fields }); state.editFields = ["sample_item", "bom"];
		state.submit = () => {
			try { const values = state.dialog.get_values(); if (values) return state.create(workOrderPayload(state.scope, values)); }
			catch (error) { notify(error.message); }
		};
		renderState(state);
	}
	function makeQuotationDialog(state) {
		const fields = [
			{ fieldname: "instructions", fieldtype: "HTML", options: "<p>" + __("制作数量与报价数量分别填写。勾选样品明细，填写报价数量、单位和单价；零价请填写0。生成后仍为报价草稿。") + "</p>" },
			{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: __("报价客户"), reqd: 1, default: state.inputs?.customer ?? state.frm.doc.customer ?? "" },
			{ fieldname: "currency", fieldtype: "Link", options: "Currency", label: __("报价币种"), reqd: 1, default: state.inputs?.currency || "" },
			{ fieldname: "valid_till", fieldtype: "Date", label: __("报价有效期"), reqd: 1, default: state.inputs?.valid_till || "" },
			{ fieldname: "items", fieldtype: "Table", label: __("报价明细"), cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true,
				fields: [
					{ fieldname: "include", fieldtype: "Check", label: __("纳入报价"), in_list_view: 1 },
					{ fieldname: "sample_item", fieldtype: "Data", label: __("样品行"), read_only: 1, in_list_view: 1 },
					{ fieldname: "item_code", fieldtype: "Data", label: __("款号"), read_only: 1, in_list_view: 1 },
					{ fieldname: "sample_qty", fieldtype: "Data", label: __("制作数量（参考）"), read_only: 1, in_list_view: 1 },
					{ fieldname: "sample_uom", fieldtype: "Data", label: __("制作单位"), read_only: 1 },
					{ fieldname: "qty", fieldtype: "Data", label: __("报价数量"), in_list_view: 1 },
					{ fieldname: "uom", fieldtype: "Link", options: "UOM", label: __("报价单位"), in_list_view: 1 },
					{ fieldname: "rate", fieldtype: "Data", label: __("报价单价"), in_list_view: 1 },
					{ fieldname: "make_flow", fieldtype: "Check", label: __("生成流程来源"), in_list_view: 1 }
				], data: state.scope.rows.map((row) => ({ include: 0, sample_item: row.name, item_code: row.item_code,
					sample_qty: String(row.qty ?? ""), sample_uom: row.stock_uom || "", qty: "", uom: "", rate: "", make_flow: 0,
					...(state.inputs?.items || []).find((input) => input.sample_item === row.name) })) }
		];
		state.dialog = new frappe.ui.Dialog({ title: __("生成报价草稿"), fields, size: "extra-large" }); state.editFields = ["customer", "currency", "valid_till", "items"];
		state.submit = () => {
			try { const values = state.dialog.get_values(); if (values) return state.create(quotationPayload(state.scope, values)); }
			catch (error) { notify(error.message); }
		};
		renderState(state);
	}
	function show(frm, kind) {
		try {
			const state = stateFor(frm, kind);
			if (state.phase === "found") return state.open();
			if (!state.dialog) (kind === "quotation" ? makeQuotationDialog : makeWorkOrderDialog)(state);
			state.dialog.show(); renderState(state);
		} catch (error) { notify(error.message); }
	}
	function refresh(frm) {
		frm.add_custom_button(__("返回样品列表"), () => frappe.set_route("List", TYPE));
		if (frm.doc.docstatus === 1 && !frm.is_new()) {
			frm.add_custom_button(__("生成样品工单"), () => show(frm, "work_order"), __("生成"));
			frm.add_custom_button(__("生成报价草稿"), () => show(frm, "quotation"), __("生成"));
		}
		for (const [kind, label] of [["work_order", "核对工单生成结果"], ["quotation", "核对报价生成结果"]]) {
			let state = held.get(key(frm, kind));
			if (!state) {
				try { const record = readRecord(frappe.session?.user, frm.doc.name, kind); if (record) { state = new Request(frm, kind, record.scope); adopt(state, record); held.set(key(frm, kind), state); } }
				catch { notify("本地生成回执暂不可读取，请恢复本地存储后再核对生成结果。"); }
			}
			if (state?.phase === "unknown") frm.add_custom_button(__(label), () => { state.frm = frm; return state.resolve(); }, __("生成结果"));
		}
	}
	window.JifengSampleOrderUI = { Request, context, quotationPayload, workOrderPayload, receipt, definiteFailure, show, refresh, stateFor };
	frappe.ui.form.on(TYPE, { refresh });
	window.addEventListener?.("beforeunload", (event) => {
		if ([...held.values()].some((state) => ["preparing", "sending", "unknown", "resolving"].includes(state.phase))) { event.preventDefault(); event.returnValue = ""; }
	});
})();
