/* A local picture stays in the draft until the archive's one atomic save. */
window.JifengInventoryArchiveImages = class JifengInventoryArchiveImages {
	constructor(editor, state) {
		this.editor = editor; this.state = state; this.draft = null; this.changed = false;
		this.requestId = null; this.previewURL = null; this.revision = 0;
		this.value = state.archive.profile?.image || null;
		const host = state.dialog.fields_dict.archive_image.$wrapper.get(0);
		host.classList.add("jfe-inventory-image-panel");
		this.choose = this.element("button"); this.choose.type = "button"; this.choose.className = "jfe-inventory-image-choose";
		this.picture = this.element("img"); this.picture.alt = __("档案图片");
		this.placeholder = this.element("span", "+"); this.placeholder.className = "jfe-inventory-image-placeholder";
		this.choose.append(this.picture, this.placeholder); this.choose.setAttribute("aria-label", __("选择档案图片"));
		this.input = this.element("input"); this.input.type = "file"; this.input.accept = ".png,.jpg,.jpeg,.gif,.webp";
		this.input.hidden = true; this.input.addEventListener("change", () => { const file = this.input.files?.[0]; this.input.value = ""; if (file) this.select(file); });
		this.choose.addEventListener("click", () => { if (this.editable("can_choose")) this.input.click(); });
		this.hint = this.element("p", __("图片名称不能含有中文")); this.hint.className = "jfe-inventory-image-hint";
		this.message = this.element("p"); this.message.className = "jfe-inventory-image-message"; this.message.setAttribute("aria-live", "polite");
		this.clear = this.element("button", __("清空图片")); this.clear.type = "button"; this.clear.className = "btn btn-default btn-sm";
		this.clear.addEventListener("click", () => this.clearImage());
		host.append(this.choose, this.input, this.hint, this.message, this.clear); this.refresh();
	}
	element(tag, text) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); return node; }
	editable(permission) {
		const s = this.state; return !s.closed && !s.saving && !s.linking && !s.unresolved && !s.confirming && !s.imageReading &&
			!!s.archive.can_write && !!s.archive.image_options?.[permission];
	}
	releasePreview() { if (this.previewURL) URL.revokeObjectURL(this.previewURL); this.previewURL = null; }
	safeURL(value) {
		if (typeof value !== "string" || !/^\/private\/files\/[^/?#\\\x00-\x1f]+\?fid=[^&#\\\x00-\x1f]+$/.test(value)) return "";
		try { const filename = decodeURIComponent(value.split("?")[0].slice("/private/files/".length));
			return filename && !/[\/\\%?#\x00-\x1f]/.test(filename) && filename !== "." && filename !== ".." ? value : "";
		} catch { return ""; }
	}
	refresh() {
		const image = this.state.archive.image;
		const src = this.previewURL || (!this.changed ? this.safeURL(image?.url) : "");
		if (src) this.picture.src = src; else this.picture.removeAttribute("src");
		this.picture.hidden = !src; this.placeholder.hidden = !!src;
		this.choose.disabled = !this.editable("can_choose"); this.clear.disabled = !this.editable("can_clear");
		this.message.textContent = this.draft ? __("已选择图片，保存后生效。") + " " + this.draft.file_name :
			this.changed ? __("图片已清空，保存后生效。") : __(image?.state || "未上传");
	}
	async select(file) {
		if (!this.editable("can_choose")) return false;
		const name = file.name, options = this.state.archive.image_options;
		if (typeof name !== "string" || !name || name.trim() !== name || name.length > 140 || /[\/\\%?#\x00-\x1f\x7f]/u.test(name)) {
			frappe.msgprint(__("请选择有效的图片文件名。")); return false;
		}
		if (/\p{Script=Han}/u.test(name)) { frappe.msgprint(__("图片名称不能含有中文")); return false; }
		const extension = "." + name.split(".").pop().toLowerCase();
		if (!options.extensions?.includes(extension)) { frappe.msgprint(__("请选择 PNG、JPG、GIF 或 WebP 图片。")); return false; }
		if (!Number.isSafeInteger(file.size) || file.size < 1 || file.size > options.maximum_bytes) {
			frappe.msgprint(__("图片内容为空或超过上传大小限制。")); return false;
		}
		const revision = ++this.revision, s = this.state; s.imageReading = true; this.refresh();
		try {
			const buffer = await file.arrayBuffer();
			if (s.closed || s.unresolved || s.saving || revision !== this.revision) return false;
			if (buffer.byteLength !== file.size) throw new Error("Image size changed");
			const bytes = new Uint8Array(buffer); let binary = "";
			for (let start = 0; start < bytes.length; start += 32768) binary += String.fromCharCode(...bytes.subarray(start, start + 32768));
			const draft = { file_name: name, content: btoa(binary) }, preview = URL.createObjectURL(file);
			this.releasePreview(); this.previewURL = preview; this.draft = draft; this.changed = true;
			this.requestId = this.editor.requestID(); return true;
		} catch { if (!s.closed && revision === this.revision) frappe.msgprint(__("图片读取失败，请重新选择。")); return false; }
		finally { if (revision === this.revision) { s.imageReading = false; this.refresh(); } }
	}
	clearImage() {
		if (!this.editable("can_clear")) return;
		this.revision++; this.releasePreview(); this.draft = null; this.value = null;
		this.changed = !!this.state.archive.profile?.image || this.state.archive.image?.state === "附件不可用";
		this.requestId = this.changed ? this.editor.requestID() : null; this.refresh();
	}
	writePayload(payload) {
		if (!this.changed) return;
		payload.image_change_request_id = this.requestId;
		if (this.draft) payload.image_upload = { ...this.draft }; else payload.profile.image = this.value;
	}
	accept() { this.revision++; this.releasePreview(); this.draft = null; this.changed = false; this.requestId = null;
		this.value = this.state.archive.profile?.image || null; this.refresh(); }
	destroy() { this.revision++; this.releasePreview(); this.draft = null; }
};
