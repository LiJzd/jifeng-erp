frappe.pages["jifeng-modern"].on_page_show = function () {
	frappe.route_flags.replace_route = true;
	frappe.set_route("Workspaces", "集沣生产协作");
};
