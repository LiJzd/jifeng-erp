/* Read only source references already delivered by the native Quotation Form. */
(() => {
	"use strict";
	function reference(doc) {
		const name = doc?.jf_sample_order;
		if (typeof name !== "string" || !name || name.trim() !== name) return null;
		const items = (doc.items || []).filter((row) => row.jf_sample_order === name && typeof row.jf_sample_order_item === "string" && !!row.jf_sample_order_item)
			.map((row) => ({ quotation_item: row.name, sample_item: row.jf_sample_order_item }));
		return { name, items };
	}
	function refresh(frm) {
		const source = reference(frm.doc);
		if (!source) return;
		frm.add_custom_button(__("查看样品来源"), () => frappe.set_route("Form", "Jifeng Sample Order", source.name), __("来源"));
	}
	window.JifengQuotationSources = { reference, refresh };
	frappe.ui.form.on("Quotation", { refresh });
})();
