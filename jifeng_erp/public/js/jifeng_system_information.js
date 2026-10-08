/* Shared A/B system information and Chinese operator guide. */
window.JifengSystemInformation = class JifengSystemInformation {
	static escape(value) {
		return String(value ?? "").replace(/[&<>"']/g, (char) => ({
			"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
		})[char]);
	}

	static render(data) {
		const esc = this.escape;
		const versions = (data.versions || []).map((row) =>
			'<div class="jfi-version-row"><span>' + esc(row.name) + '</span><b>' + esc(row.version) + '</b></div>'
		).join("");
		const modules = (data.modules || []).map((row) =>
			'<article class="jfi-module"><h3>' + esc(row.name) + '</h3><p class="jfi-path">' + esc(row.path) +
			'</p><p>' + esc(row.scope) + '</p></article>'
		).join("");
		return '<section class="jfi-page" aria-labelledby="jfi-title"><header class="jfi-heading"><div><p class="jfi-eyebrow">系统信息与帮助</p>' +
			'<h1 id="jfi-title">集沣 ERP</h1><p>当前环境：' + esc(data.release) + '</p></div><span class="jfi-badge">只读信息</span></header>' +
			'<section class="jfi-card"><h2>安装版本</h2><div class="jfi-versions">' + versions + '</div><p class="jfi-muted">版本取自当前安装的应用包；未提供准确部署提交号，因此不显示 Git 版本。</p></section>' +
			'<section class="jfi-card"><h2>开始操作前</h2><ul><li>' + esc(data.shared_data_note) + '</li><li>' + esc(data.workflow_note) + '</li><li>' + esc(data.scope_note) + '</li></ul></section>' +
			'<section class="jfi-card"><h2>中文业务导航</h2><div class="jfi-modules">' + modules + '</div></section>' +
			'<footer class="jfi-footer">本帮助说明当前工作台导航与已知接入范围。各业务单据的完整字段和审批规则以打开的正式业务页面为准。</footer></section>';
	}

	static async mount(container) {
		if (!container) return;
		container.innerHTML = '<section class="jfi-page" aria-live="polite"><p class="jfi-loading">正在读取版本与操作帮助…</p></section>';
		try {
			const response = await frappe.call({
				method: "jifeng_erp.system_information.get_information",
				type: "GET",
				silent: true,
			});
			if (!response.message || !Array.isArray(response.message.versions) || !Array.isArray(response.message.modules)) {
				throw new Error("Invalid system information response");
			}
			container.innerHTML = this.render(response.message);
		} catch (_error) {
			container.innerHTML = '<section class="jfi-page"><div class="jfi-error" role="alert">系统信息暂时无法读取，请刷新页面后重试。</div></section>';
		}
	}
};
