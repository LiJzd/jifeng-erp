frappe.pages["jifeng-legacy"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("经典业务工作台"),
		single_column: true,
	});
	$(wrapper).addClass("jfe-legacy-wrapper");
	$(wrapper).find(".page-head").hide();
	page.main.empty().addClass("jfe-legacy-main");
	new window.JifengERPWorkspace.Workspace(page.main.get(0));
};
