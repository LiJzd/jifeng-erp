/* Order plating colors use the shared dictionary transport and dialog guards. */
window.JifengPlatingColor = class JifengPlatingColor extends window.JifengOrderDictionary {
	constructor(host) { super(host, "镀色", "产品镀色"); }

	build() {
		this.dictionaryApi = "jifeng_erp.plating_color";
		this.dictionaryOptions = { ...this.dictionaryOptions,
			editorFields: ["legacy_number", "code", "value", "color_system", "remarks", "inactive", "abbreviation"],
			editorLabels: { code: "代码", color_system: "色系" },
			editorFieldOptions: { code: { placeholder: "代码生成条码时用" } } };
		super.build(); this.host.classList.add("jfe-plating-color");
	}

	columnLabels() { return ["代码", "编号", "名称", "色系", "备注", "简拼", "停用", "操作", "日期", "修改", "修改日期", "修改", "删除"]; }

	validDictionaryRow(row) {
		return super.validDictionaryRow(row) && ["code", "color_system"].every((field) => row[field] == null || typeof row[field] === "string");
	}

	numberPolicy(archive) { return { read_only: true, value: archive.name ? archive.legacy_number ?? null : archive.number_preview ?? null,
		description: archive.name ? __("已保存的编号") : __("保存时自动生成；暂定编号仅供参考") }; }

	renderRow(row) {
		const tr = this.element("tr");
		for (const field of ["code", "legacy_number", "value", "color_system", "remarks", "abbreviation"]) tr.append(this.element("td", row[field] ?? ""));
		const inactive = this.element("td"), check = this.element("input"); check.type = "checkbox"; check.checked = !!Number(row.inactive); check.disabled = true;
		check.setAttribute("aria-label", __("停用")); inactive.append(check); tr.append(inactive);
		for (const field of ["owner", "creation", "modified_by", "version"]) tr.append(this.element("td", row[field] ?? ""));
		const operation = this.element("td"); operation.append(this.button(this.readyArchive?.can_write ? "修改" : "查看", () => this.openEditor(row.name))); tr.append(operation);
		const deletion = this.element("td"); deletion.append(this.button("删除", null, "删除尚未实现")); tr.append(deletion); this.tbody.append(tr);
	}
};
