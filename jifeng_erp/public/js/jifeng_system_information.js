/* Shared A/B system information and operator guide. */
window.JifengSystemInformation = class JifengSystemInformation {
	static escape(value) {
		return String(value ?? "").replace(/[&<>"']/g, (char) => ({
			"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
		})[char]);
	}

	static tabs() {
		return [["about", "关于本系统"], ["notice", "用户须知"], ["renewal", "续费信息"]];
	}

	static navigation(section) {
		return '<nav class="jfi-tabs" aria-label="系统信息">' + this.tabs().map(([key, label]) =>
			'<button type="button" data-jfi-section="' + key + '" ' + (section === key ? 'aria-current="page"' : "") + ">" + label + "</button>"
		).join("") + "</nav>";
	}

	static aboutContent(data) {
		const esc = this.escape;
		const versions = (data.versions || []).map((row) =>
			'<div class="jfi-version-row"><span>' + esc(row.name) + '</span><b>' + esc(row.version) + '</b></div>'
		).join("");
		return '<section class="jfi-card"><h2>安装版本</h2><div class="jfi-release">' + esc(data.release) +
			'</div><div class="jfi-versions">' + versions + '</div></section>' +
			'<section class="jfi-card"><h2>业务数据</h2><p>' + esc(data.shared_data_note) + '</p></section>' +
			'<section class="jfi-card"><h2>操作范围</h2><p>' + esc(data.scope_note) + '</p></section>';
	}

	static noticeContent(data) {
		const esc = this.escape;
		const modules = (data.modules || []).map((row) =>
			'<article class="jfi-module"><h3>' + esc(row.name) + '</h3><p class="jfi-path">' + esc(row.path) +
			'</p><p><b>已核实：</b>' + esc(row.verified) + '</p><p><b>待完成：</b>' + esc(row.pending) + '</p></article>'
		).join("");
		return '<section class="jfi-card"><h2>业务操作顺序</h2><p>' + esc(data.workflow_note) +
			'</p><p>请在当前模块按业务来源进入单据，并在提交前核对公司、日期、来源单和明细。A、B 页面共享同一数据，切换页面后应查询原单确认结果。</p></section>' +
			'<section class="jfi-card"><h2>19 个模块的已核范围与待完成项</h2><div class="jfi-modules">' + modules + '</div></section>';
	}

	static renewalContent(data) {
		const esc = this.escape;
		const item = (data.modules || []).find((row) => row.name === "关于");
		return '<section class="jfi-card"><h2>续费信息</h2><p>当前没有可核实的续费合同、收费规则或续费联系人资料。请向公司确认后再办理续费。</p>' +
			'<p class="jfi-muted">' + esc(item?.pending || "") + '</p></section>';
	}

	static render(container, data, section = "about", onSectionChange = () => {}) {
		const selected = this.tabs().some(([key]) => key === section) ? section : "about";
		const title = this.tabs().find(([key]) => key === selected)[1];
		const content = selected === "about" ? this.aboutContent(data) :
			selected === "notice" ? this.noticeContent(data) : this.renewalContent(data);
		container.dataset.section = selected;
		container.innerHTML = '<section class="jfi-page" aria-labelledby="jfi-title"><header class="jfi-heading"><div><p class="jfi-eyebrow">系统信息与操作帮助</p>' +
			'<h1 id="jfi-title">' + title + '</h1></div></header>' + this.navigation(selected) + content + '</section>';
		container.onclick = (event) => {
			const button = event.target.closest("[data-jfi-section]");
			if (!button || !container.contains(button)) return;
			onSectionChange(button.dataset.jfiSection);
			this.render(container, data, button.dataset.jfiSection, onSectionChange);
		};
	}

	static async mount(container, section = null, onSectionChange = () => {}) {
		if (!container) return;
		const selected = section || container.dataset.section || "about";
		const requestId = String(Number(container.dataset.requestId || 0) + 1);
		container.dataset.requestId = requestId;
		container.innerHTML = '<section class="jfi-page" aria-live="polite">' + this.navigation(selected) +
			'<p class="jfi-loading">正在读取版本与操作帮助…</p></section>';
		container.onclick = (event) => {
			const button = event.target.closest("[data-jfi-section]");
			if (!button || !container.contains(button)) return;
			container.dataset.section = button.dataset.jfiSection;
			onSectionChange(button.dataset.jfiSection);
			if (this.data) this.render(container, this.data, button.dataset.jfiSection, onSectionChange);
		};
		try {
			const response = await frappe.call({
				method: "jifeng_erp.system_information.get_information",
				type: "GET",
				silent: true,
			});
			if (!response.message || !Array.isArray(response.message.versions) || !Array.isArray(response.message.modules)) {
				throw new Error("Invalid system information response");
			}
			if (!container.isConnected || container.dataset.requestId !== requestId) return;
			this.data = response.message;
			this.render(container, this.data, container.dataset.section || selected, onSectionChange);
		} catch (_error) {
			if (!container.isConnected || container.dataset.requestId !== requestId) return;
			container.innerHTML = '<section class="jfi-page"><div class="jfi-error" role="alert">系统信息暂时无法读取，请刷新页面后重试。</div></section>';
		}
	}
};
