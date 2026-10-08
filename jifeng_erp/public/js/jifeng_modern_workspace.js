/* A second presentation of the same permission-controlled business workspace. */
window.JifengModernWorkspace = class JifengModernWorkspace extends window.JifengERPWorkspace.Workspace {
	get pageName() { return "jifeng-modern"; }

	switchTab(key) {
		if (!this.tabs.some((tab) => tab.key === key)) return;
		super.switchTab(key);
		const scroller = this.host.closest?.(".main-section");
		if (scroller && scroller.scrollTop !== 0) scroller.scrollTop = 0;
		this.formalBridge?.align();
	}

	render() {
		const active = this.activeTab();
		this.disposeBusinessOperationLog();
		this.disposeSalaryAnalysis();
		this.host.innerHTML = '<div class="jfe-shell jfm-shell">' +
			'<aside class="jfm-sidebar"><button class="jfm-brand" data-action="classic-home"><span class="jfm-logo">集</span><span>集沣 ERP<small>业务协作工作台</small></span></button>' +
			'<nav aria-label="业务模块">' + this.renderSidebar() + '</nav><div class="jfm-sidebar-foot"><span class="jfm-version">A · 开源优化版</span><a href="/desk/jifeng-legacy" target="_blank" rel="noopener">打开 B · 旧界面复刻版 ↗</a></div></aside>' +
			'<section class="jfm-main"><header class="jfm-header"><div class="jfm-location">' + this.renderBreadcrumb(active) + '</div>' +
			'<div class="jfm-header-tools"><div class="jfm-search"><label class="jfm-search-field"><span aria-hidden="true">⌕</span><input type="search" data-modern-search aria-label="查找功能" aria-controls="jfm-search-results" aria-expanded="false" placeholder="查找功能，例如订单、工资" autocomplete="off"><kbd>/</kbd></label>' +
			'<div id="jfm-search-results" class="jfm-search-results" data-modern-results hidden></div></div>' +
			'<button class="jfm-header-button" data-action="fullscreen" title="全屏" aria-label="全屏">⛶</button><button class="jfm-header-button jfm-user" data-action="user" title="当前用户">' + this.esc(frappe.session.user_fullname || frappe.session.user || "当前用户") + '</button></div></header>' +
			this.renderTabs() + '<main class="jfe-content jfm-content">' + this.renderActive(active) + '</main>' +
			'<footer class="jfm-footer"><span>集沣业务协作</span><span data-clock></span></footer></section>' +
			'<div class="jfe-toast" data-toast></div><div class="jfe-modal-root" data-modal-root></div></div>';
		this.bindEvents();
		this.mountSystemInformation();
		this.mountBusinessOperationLog();
		this.mountSalaryAnalysis();
		this.formalBridge?.sync();
		this.updateClock();
		if (active.kind === "module" && active.module === "总经理") this.loadDashboard();
		clearInterval(this.clockTimer);
		this.clockTimer = setInterval(() => this.updateClock(), 30000);
	}

	renderSidebar() {
		const active = this.activeTab();
		const groups = [
			["业务", ["订单", "产品", "物料", "材料", "生产", "发镀", "外发", "销售"]],
			["人员与资产", ["人事", "工资", "模具", "版种"]],
			["经营与系统", ["财务", "总经理", "权限", "日志", "插件", "工具", "关于"]],
		];
		return '<button class="jfm-nav-item ' + (active.kind === "home" ? "is-active" : "") + '" data-action="classic-home"><span class="jfm-nav-icon">⌂</span>工作台</button>' +
			groups.map(([label, names]) => '<div class="jfm-nav-group"><h2>' + label + '</h2>' + names.map((name) =>
				'<button class="jfm-nav-item ' + (active.module === name ? "is-active" : "") + '" data-action="module" data-name="' + this.esc(name) + '"><span class="jfm-nav-icon">' + this.esc(name.slice(0, 1)) + '</span>' + this.esc(name) + '</button>'
			).join("") + '</div>').join("") +
			'<div class="jfm-screen-actions"><button data-action="fullscreen">全屏</button><button data-action="exit-fullscreen">退全屏</button></div>';
	}

	renderTabs() {
		return '<nav class="jfm-tabs" aria-label="已打开页面">' + this.tabs.map((tab) =>
			'<div class="jfm-tab ' + (tab.key === this.activeKey ? "is-active" : "") + '"><button data-action="tab" data-key="' + this.esc(tab.key) + '" ' + (tab.key === this.activeKey ? 'aria-current="page"' : '') + '>' + this.esc(tab.title) + '</button>' +
			(tab.key === "home" ? "" : '<button class="jfm-tab-close" data-action="close-tab" data-key="' + this.esc(tab.key) + '" aria-label="关闭' + this.esc(tab.title) + '">×</button>') + '</div>'
		).join("") + '</nav>';
	}

	statusLabel(entry) {
		return ({ formal: "正式", partial: "部分可用", preview: "交互预览", navigation: "模块入口", tool: "工具" })[entry.availability] || "交互预览";
	}

	entryButton(entry, extraClass = "") {
		return '<button class="jfm-entry ' + extraClass + '" data-action="open-page" data-module="' + this.esc(entry.module) + '" data-page="' + this.esc(entry.name) + '">' +
			'<span class="jfm-entry-icon">' + this.esc(entry.name.slice(0, 1)) + '</span><span class="jfm-entry-name"><b>' + this.esc(entry.name) + '</b><small>' + this.esc(entry.module + " / " + entry.group) + '</small></span>' +
			'<span class="jfm-status jfm-status-' + this.esc(entry.availability) + '">' + this.statusLabel(entry) + '</span></button>';
	}

	quickEntries() {
		const entries = window.JifengERPWorkspace.entries();
		return [["订单", "订单录入"], ["产品", "产品档案"], ["生产", "生产指令单"], ["物料", "浏览"], ["工资", "工资录入"], ["发镀", "电镀厂"]]
			.map(([module, name]) => entries.find((entry) => entry.module === module && entry.name === name)).filter(Boolean);
	}

	renderHome() {
		const modules = window.JifengERPWorkspace.modules.filter((item) => item.groups?.length && !["插件", "工具", "关于"].includes(item.name));
		return '<section class="jfm-home"><div class="jfm-welcome"><div><p class="jfm-eyebrow">工作台</p><h1>从一笔订单，开始今天的工作</h1><p>订单、生产、库存、人员与财务，集中在一个工作台。</p></div><div class="jfm-welcome-mark" aria-hidden="true">JF<span>集沣</span></div></div>' +
			'<section class="jfm-section"><div class="jfm-section-heading"><h2>常用操作</h2><span>直接进入业务页面</span></div><div class="jfm-quick-grid">' + this.quickEntries().map((entry) => this.entryButton(entry)).join("") + '</div></section>' +
			'<section class="jfm-section"><div class="jfm-section-heading"><h2>业务流程</h2><span>按业务顺序查找功能</span></div><div class="jfm-flow">' +
			[["01", "订单", "客户需求与订单"], ["02", "生产", "计划、派工与完成"], ["03", "销售", "交付、库存与销售"], ["04", "财务", "往来、收付与费用"]].map(([step, module, hint]) =>
				'<button data-action="module" data-name="' + module + '"><span>' + step + '</span><div><b>' + module + '</b><small>' + hint + '</small></div><i aria-hidden="true">→</i></button>'
			).join("") + '</div></section>' +
			'<section class="jfm-section"><div class="jfm-section-heading"><h2>全部业务模块</h2><span>沿用原系统的模块名称</span></div><div class="jfm-module-grid">' + modules.map((module) =>
				'<button class="jfm-module-card" data-action="module" data-name="' + this.esc(module.name) + '"><span class="jfm-module-icon">' + this.esc(module.name.slice(0, 1)) + '</span><div><b>' + this.esc(module.name) + '</b><small>' + this.esc(module.groups.slice(0, 2).map((group) => group.name).join(" · ")) + '</small></div><i aria-hidden="true">›</i></button>'
			).join("") + '</div></section></section>';
	}

	searchEntries(query) {
		const tokens = String(query || "").toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
		return tokens.length ? window.JifengERPWorkspace.entries().filter((entry) => {
			const text = [entry.module, entry.group, entry.name].join(" ").toLocaleLowerCase();
			return tokens.every((token) => text.includes(token));
		}) : this.quickEntries();
	}

	showSearch(query) {
		const results = this.host.querySelector("[data-modern-results]");
		const input = this.host.querySelector("[data-modern-search]");
		if (!results || !input) return;
		const entries = this.searchEntries(query);
		results.innerHTML = '<div class="jfm-search-heading">' + (query.trim() ? "找到 " + entries.length + " 项功能" : "常用操作") + '</div>' +
			(entries.length ? entries.slice(0, 20).map((entry) => this.entryButton(entry, "jfm-search-entry")).join("") : '<p class="jfm-search-empty">没有匹配的功能，请尝试模块名称。</p>') +
			(entries.length > 20 ? '<p class="jfm-search-empty">显示前 20 项，请补充关键词缩小范围。</p>' : "");
		results.hidden = false;
		input.setAttribute("aria-expanded", "true");
	}

	hideSearch() {
		const results = this.host.querySelector("[data-modern-results]");
		if (results) results.hidden = true;
		this.host.querySelector("[data-modern-search]")?.setAttribute("aria-expanded", "false");
	}

	bindEvents() {
		super.bindEvents();
		const input = this.host.querySelector("[data-modern-search]");
		input?.addEventListener("focus", () => this.showSearch(input.value));
		input?.addEventListener("input", () => this.showSearch(input.value));
		const nativeClick = this.host.onclick;
		this.host.onclick = (event) => {
			if (!event.target.closest(".jfm-search")) this.hideSearch();
			nativeClick(event);
		};
		const nativeKeydown = this.host.onkeydown;
		this.host.onkeydown = (event) => {
			nativeKeydown(event);
			if (event.key === "Escape") this.hideSearch();
			if (event.target === input && event.key === "ArrowDown") {
				event.preventDefault();
				this.host.querySelector("[data-modern-results] button")?.focus();
			}
			if (event.target === input && event.key === "Enter") {
				event.preventDefault();
				this.host.querySelector("[data-modern-results] button")?.click();
			}
			if (event.key === "/" && !event.ctrlKey && !event.altKey && !event.metaKey && !event.target.closest("input, textarea, select, [contenteditable]")) {
				event.preventDefault(); input?.focus();
			}
		};
	}
};
