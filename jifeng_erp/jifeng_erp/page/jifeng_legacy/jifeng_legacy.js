frappe.pages["jifeng-legacy"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("集沣 ERP"),
		single_column: true,
	});
	page.hide_sidebar = true;
	$(wrapper).addClass("jfe-legacy-wrapper");
	page.main.empty().addClass("jfe-legacy-main");
	wrapper.jifeng_workspace = new window.JifengERPWorkspace.Workspace(page.main.get(0));
};

frappe.pages["jifeng-legacy"].on_page_show = function (wrapper) {
	wrapper.jifeng_workspace?.formalBridge?.align();
};
