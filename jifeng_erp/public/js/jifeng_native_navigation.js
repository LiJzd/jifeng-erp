// Both historical links now enter the original ERP's module navigation.
(() => {
	delete frappe.re_route["jifeng-legacy"];
	frappe.re_route["jifeng-modern"] = "/desk/jifeng-legacy";
	for (const name of ["jifeng-modern", "jifeng-legacy"]) {
		try {
			localStorage.removeItem("_page:" + name);
		} catch (_) {
			// Browser storage is optional; server Page scripts remain authoritative.
		}
	}
})();
