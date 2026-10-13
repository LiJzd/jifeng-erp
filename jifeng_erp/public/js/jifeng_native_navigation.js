// Route before Page rendering, including browsers with cached old Page scripts.
(() => {
	for (const name of ["jifeng-modern", "jifeng-legacy"]) {
		frappe.re_route[name] = "/desk/集沣生产协作";
		try {
			localStorage.removeItem("_page:" + name);
		} catch (_) {
			// Routing also works when browser storage is unavailable.
		}
	}
})();
