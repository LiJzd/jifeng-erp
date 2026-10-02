frappe.pages["jifeng-modern"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("集沣业务工作台"), single_column: true });
	$(wrapper).addClass("jfe-modern-wrapper");
	$(wrapper).find(".page-head").hide();
	page.main.empty().addClass("jfe-modern-main");
	new window.JifengModernWorkspace(page.main.get(0));
};
