frappe.pages["jifeng-legacy"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("经典业务工作台"),
		single_column: true,
	});
	$(wrapper).addClass("jfe-legacy-wrapper");
	$(wrapper).find(".page-head").hide();
	page.main.empty().addClass("jfe-legacy-main");
	new JifengLegacyWorkspace(page.main.get(0));
};

const JIFENG_LEGACY_MODULES = [
	{
		name: "物料",
		groups: [
			{ name: "基础资料", items: ["物料类别", "物料档案", "仓库", "采购员", "出库方式", "领用人", "浏览"] },
			{ name: "正式业务", items: ["物料主数据", "采购申请", "采购订单", "采购收货", "发料凭证"] },
			{ name: "操作功能", items: ["申购", "入库", "退回", "出库", "调拨", "反审", "跟踪"] },
			{ name: "仓库管理", items: ["盘点", "差异处理", "库存", "盘点明细", "PDA数据", "历史库存", "库存明细"] },
			{ name: "报表查询", items: ["申购报表", "申购明细", "入库报表", "入库明细", "退回报表", "退回明细", "出库报表", "出库明细", "调拨报表", "调拨明细"] },
		],
	},
	{
		name: "材料",
		groups: [
			{ name: "基础资料", items: ["材料类别", "形状", "颜色", "材料档案", "仓库", "供应商", "采购员", "出库方式", "领用人", "浏览"] },
			{ name: "正式业务", items: ["物料主数据", "采购申请", "采购订单", "采购收货", "发料凭证"] },
			{ name: "操作功能", items: ["申购", "入库", "退回", "出库", "调拨", "反审", "跟踪"] },
			{ name: "仓库管理", items: ["盘点", "差异处理", "库存", "盘点明细", "PDA数据", "历史库存", "库存明细"] },
			{ name: "报表查询", items: ["申购报表", "申购明细", "入库报表", "入库明细", "退回报表", "退回明细", "出库报表", "出库明细", "调拨报表", "调拨明细"] },
		],
	},
	{
		name: "产品",
		groups: [
			{ name: "基础资料", items: ["类别", "风格", "来源", "品牌", "流程", "镀色", "报价参数", "设计员", "打版员", "建模员", "材料类别", "产品档案", "资料录入"] },
			{ name: "正式业务", items: ["商品映射", "商品工艺档案", "产品报价单"] },
			{ name: "产品功能", items: ["产品浏览", "共用材料", "新款", "打印", "产品明细", "样品制作单", "样品单反审", "报价单"] },
			{ name: "报表查询", items: ["品牌分析", "类别分析", "材质分析", "来源分析", "设计分析", "打版分析", "建模分析", "风格分析", "产品材料明细", "报价查询", "新款价"] },
		],
	},
	{
		name: "订单",
		groups: [
			{ name: "基础资料", items: ["客户分类", "客户级别", "客户", "类型", "要求", "部门", "镀色", "工序"] },
			{ name: "正式业务", items: ["聚水潭订单", "生产流程单"] },
			{ name: "订单操作", items: ["订单录入", "订单查询", "算料", "配料", "消单", "反审", "算料汇总", "按单开单", "补数", "按单反审"] },
			{ name: "报表分析", items: ["订单分析", "订单排行", "新款分析", "补数分析", "补数查询"] },
		],
	},
	{
		name: "生产",
		groups: [
			{ name: "正式业务", items: ["生产计划单", "生产工单", "工序报工单"] },
			{ name: "基础资料", items: ["刷卡工序", "工序查询"] },
			{ name: "生产操作", items: ["刷单", "生产指令单", "计划", "完成", "反审", "在线订单金额", "计划图片", "入库单", "部门订单", "置顶", "订单逾期", "部门逾期", "工序逾期"] },
			{ name: "报表分析", items: ["刷卡明细", "刷卡报表", "未投产分析", "计划分析", "完成分析", "前后分析", "生产汇总", "工序损耗", "损耗汇总"] },
		],
	},
	{
		name: "人事",
		groups: [
			{ name: "正式业务", items: ["员工档案", "员工补充档案", "部门档案", "职位档案", "晋升记录", "晋升办理", "调岗记录", "调岗办理", "离职流程"] },
			{ name: "基础资料", items: ["部门", "专长", "文化", "民族"] },
			{ name: "人员管理", items: ["人事档案", "人员浏览"] },
			{ name: "报表查询", items: ["人事报表"] },
		],
	},
	{
		name: "工资",
		groups: [
			{ name: "正式业务", items: ["计薪记录", "工资附加项", "工资单", "工资发放", "调薪记录", "调薪录入"] },
			{ name: "基础资料", items: ["工序", "计件人员", "工价调用", "工序查询", "人员查询", "临工档案"] },
			{ name: "工资操作", items: ["工资录入", "临工", "工资汇总", "审核", "计算", "临工打印"] },
			{ name: "报表分析", items: ["工资明细", "临工明细", "工资分析", "临工分析", "临工浏览", "历史", "当月分析", "历史分析"] },
		],
	},
	{
		name: "模具",
		groups: [
			{ name: "正式业务", items: ["模具档案"] },
			{ name: "基础资料", items: ["压模人"] },
			{ name: "模具管理", items: ["模具录入"] },
			{ name: "报表查询", items: ["模具查询", "模具报表"] },
		],
	},
	{
		name: "版种",
		groups: [
			{ name: "正式业务", items: ["版种档案"] },
			{ name: "基础资料", items: ["制版人"] },
			{ name: "版种管理", items: ["版种录入", "建模"] },
			{ name: "报表查询", items: ["版种查询", "版种报表", "建模查询"] },
		],
	},
	{
		name: "发镀",
		groups: [
			{ name: "正式业务", items: ["新建委外采购", "委外采购单", "电镀委外单", "电镀收货单", "品检单"] },
			{ name: "基础资料", items: ["电镀厂", "电镀类型"] },
			{ name: "业务操作", items: ["电镀单", "电镀品检", "电镀对账", "电镀反审", "电镀跟踪", "品检反审"] },
			{ name: "报表查询", items: ["电镀明细", "电镀单查询", "电镀报表"] },
		],
	},
	{
		name: "外发",
		groups: [
			{ name: "基础资料", items: ["外发工厂", "外发工序"] },
			{ name: "正式业务", items: ["新建委外采购", "委外采购单", "委外订单", "委外收货"] },
			{ name: "业务操作", items: ["外发加工", "收回", "对账", "反审", "未收", "收回反审"] },
			{ name: "报表查询", items: ["外发明细", "收回明细", "外发查询", "收回查询", "外发报表", "收回报表"] },
		],
	},
	{
		name: "销售",
		groups: [
			{ name: "基础资料", items: ["客户类型", "客户等级", "客户分类", "客户档案", "入库部门", "入库类型", "商品信息", "条码打印", "销售员", "条码标题", "收款方式", "快递公司", "销售页脚", "零售倍率", "库位模式"] },
			{ name: "正式业务", items: ["销售订单", "零售开单", "发货单", "销售发票", "分批回货成本试算"] },
			{ name: "销售操作", items: ["销售入库", "前台开单", "销售调拨", "库位下架", "销售反审", "销售跟踪", "成品出库", "客户返修"] },
			{ name: "仓库管理", items: ["销售盘点", "差异处理", "库存", "盘点明细", "PDA数据", "库存明细", "库位库存", "库位修改", "库存排行", "历史库存", "库存预警"] },
			{ name: "报表分析", items: ["销售单查询", "日报表", "销售报表", "销售分析", "销售排行", "入库排行", "进销存", "入库明细", "明细查询", "调拨明细", "下架明细", "销售比", "新款分析", "明细成本", "同比", "部门同比", "导购同比", "客户同比", "品牌同比", "类别同比"] },
		],
	},
	{
		name: "财务",
		groups: [
			{ name: "正式业务", items: ["收付款单", "总账凭证", "费用登记单"] },
			{ name: "基础资料", items: ["收款银行", "付款银行", "费用科目"] },
			{ name: "凭证录入", items: ["收款凭证", "付款凭证", "费用登记"] },
			{ name: "报表查询", items: ["收款凭证查询", "付款凭证查询", "费用登记查询", "物料账", "材料账", "委外账", "电镀账", "销售账", "费用账", "付款报表", "收款报表", "总报表"] },
		],
	},
	{
		name: "总经理",
		groups: [
			{ name: "经营总览", items: ["物料", "材料", "产品", "订单", "生产", "人事", "工资", "模具", "版种", "发镀", "外发", "销售", "财务"] },
		],
	},
		{ name: "权限", groups: [{ name: "正式业务", items: ["用户管理", "角色管理"] }, { name: "旧版页面", items: ["权限管理"] }] },
		{ name: "日志", groups: [{ name: "正式业务", items: ["服务器操作日志"] }, { name: "旧版页面", items: ["操作日志"] }] },
	{ name: "插件", groups: [{ name: "安装说明", items: ["打印插件"] }] },
	{ name: "工具", groups: [{ name: "桌面工具", items: ["远程协助工具"] }] },
	{ name: "关于", groups: [{ name: "系统信息", items: ["关于本系统", "用户须知", "续费信息"] }] },
];

const JIFENG_LIVE_ROUTES = {
	物料: { 物料档案: "Item", 仓库: "Warehouse", 物料主数据: "Item", 采购申请: "Material Request", 采购订单: "Purchase Order", 采购收货: "Purchase Receipt", 发料凭证: "Stock Entry" },
	材料: { 材料档案: "Item", 仓库: "Warehouse", 供应商: "Supplier", 物料主数据: "Item", 采购申请: "Material Request", 采购订单: "Purchase Order", 采购收货: "Purchase Receipt", 发料凭证: "Stock Entry" },
	产品: { 产品档案: "Jifeng Product Profile", 商品映射: "Jifeng SKU Mapping", 商品工艺档案: "Jifeng Product Profile", 产品报价单: "Quotation" },
	订单: { 客户: "Customer", 订单查询: "Sales Order", 聚水潭订单: "Jifeng External Order", 生产流程单: "Jifeng Flow Order" },
	生产: { 生产计划单: "Production Plan", 生产工单: "Work Order", 工序报工单: "Job Card" },
	人事: { 人事档案: "Employee", 人员浏览: "Employee", 部门: "Department", 员工档案: "Employee", 员工补充档案: "Jifeng Employee Supplement", 部门档案: "Department", 职位档案: "Designation", 晋升记录: "Employee Promotion", 调岗记录: "Employee Transfer", 离职流程: "Employee Separation" },
	工资: { 计薪记录: "Jifeng Wage Entry", 工资录入: "Jifeng Wage Entry", 工资明细: "Jifeng Wage Entry", 审核: "Jifeng Wage Entry", 工资附加项: "Additional Salary", 工资单: "Salary Slip", 工资汇总: "Salary Slip", 工资发放: "Payroll Entry", 调薪记录: "Salary Structure Assignment" },
	模具: { 模具录入: "Jifeng Mold", 模具查询: "Jifeng Mold", 模具档案: "Jifeng Mold" },
	版种: { 版种档案: "Jifeng Plate", 版种录入: "Jifeng Plate", 版种查询: "Jifeng Plate" },
	发镀: { 委外采购单: "Purchase Order", 电镀委外单: "Subcontracting Order", 电镀收货单: "Subcontracting Receipt", 品检单: "Quality Inspection", 电镀单查询: "Subcontracting Order" },
	外发: { 委外采购单: "Purchase Order", 委外订单: "Subcontracting Order", 委外收货: "Subcontracting Receipt", 外发查询: "Subcontracting Order", 收回查询: "Subcontracting Receipt" },
	销售: { 客户档案: "Customer", 销售订单: "Sales Order", 零售开单: "POS Invoice", 发货单: "Delivery Note", 销售发票: "Sales Invoice", 分批回货成本试算: "Jifeng Cost Preview" },
	财务: { 收付款单: "Payment Entry", 总账凭证: "Journal Entry", 费用登记单: "Jifeng Expense Voucher", 收款凭证查询: "Payment Entry", 付款凭证查询: "Payment Entry", 费用登记查询: "Jifeng Expense Voucher" },
	权限: { 用户管理: "User", 角色管理: "Role" },
	日志: { 服务器操作日志: "Activity Log" },
};

const JIFENG_LIVE_CREATE = {
	物料: {
		申购: { doctype: "Material Request", values: { material_request_type: "Purchase" } },
		入库: { doctype: "Purchase Receipt", values: {} },
		出库: { doctype: "Stock Entry", values: { stock_entry_type: "Material Issue" } },
		调拨: { doctype: "Stock Entry", values: { stock_entry_type: "Material Transfer" } },
		盘点: { doctype: "Stock Reconciliation", values: {} },
	},
	材料: {
		申购: { doctype: "Material Request", values: { material_request_type: "Purchase" } },
		入库: { doctype: "Purchase Receipt", values: {} },
		出库: { doctype: "Stock Entry", values: { stock_entry_type: "Material Issue" } },
		调拨: { doctype: "Stock Entry", values: { stock_entry_type: "Material Transfer" } },
		盘点: { doctype: "Stock Reconciliation", values: {} },
	},
	订单: { 订单录入: { doctype: "Sales Order", values: {} } },
	人事: {
		晋升办理: { doctype: "Employee Promotion", values: {} },
		调岗办理: { doctype: "Employee Transfer", values: {} },
	},
	工资: { 调薪录入: { doctype: "Salary Structure Assignment", values: {} } },
	发镀: {
		新建委外采购: { doctype: "Purchase Order", values: { is_subcontracted: 1 } },
		电镀单: { doctype: "Subcontracting Order", values: {} },
		电镀品检: { doctype: "Quality Inspection", values: { inspection_type: "Incoming", reference_type: "Subcontracting Receipt" } },
	},
	外发: {
		新建委外采购: { doctype: "Purchase Order", values: { is_subcontracted: 1 } },
		外发加工: { doctype: "Subcontracting Order", values: {} },
		收回: { doctype: "Subcontracting Receipt", values: {} },
	},
	销售: {
		销售入库: { doctype: "Stock Entry", values: { stock_entry_type: "Material Receipt" } },
		成品出库: { doctype: "Stock Entry", values: { stock_entry_type: "Material Issue" } },
		销售调拨: { doctype: "Stock Entry", values: { stock_entry_type: "Material Transfer" } },
		销售盘点: { doctype: "Stock Reconciliation", values: {} },
		前台开单: { doctype: "POS Invoice", values: {} },
	},
	财务: {
		收款凭证: { doctype: "Payment Entry", values: { payment_type: "Receive" } },
		付款凭证: { doctype: "Payment Entry", values: { payment_type: "Pay" } },
		费用登记: { doctype: "Jifeng Expense Voucher", values: {} },
	},
};

const JIFENG_LIVE_LIST_FILTERS = {
	物料: { 采购订单: { is_subcontracted: 0 } },
	材料: { 采购订单: { is_subcontracted: 0 } },
	发镀: { 委外采购单: { is_subcontracted: 1 } },
	外发: { 委外采购单: { is_subcontracted: 1 } },
	财务: { 收款凭证查询: { payment_type: "Receive" }, 付款凭证查询: { payment_type: "Pay" } },
};

const JIFENG_LIVE_REPORTS = {
	物料: { 库存: "Stock Balance", 库存明细: "Stock Ledger" },
	材料: { 库存: "Stock Balance", 库存明细: "Stock Ledger" },
	生产: { 计划分析: "Production Plan Summary", 生产汇总: "Work Order Summary" },
	工资: { 工资分析: "Salary Register" },
	销售: {
		库存: "Stock Balance", 库存明细: "Stock Ledger", 进销存: "Stock Balance",
		销售报表: "Sales Register", 销售分析: "Sales Analytics", 明细成本: "Gross Profit",
	},
	财务: { 销售账: "Accounts Receivable", 总报表: "General Ledger" },
};

class JifengLegacyWorkspace {
	constructor(host) {
		this.host = host;
		this.tabs = [{ key: "home", title: "主页", kind: "home" }];
		this.activeKey = "home";
		this.search = "";
		this.dateFrom = "";
		this.dateTo = "";
		this.onlyDate = false;
		this.approvalFilter = "未审";
		this.planStatus = "生产中";
		this.deliveryFilter = false;
		this.moldView = "列表模式";
		this.moldPreview = true;
		this.dashboard = null;
		this.dashboardRequest = 0;
		this.pageNo = 1;
		this.pageSize = 15;
		this.editing = null;
		this.render();
	}

	module(name) {
		return JIFENG_LEGACY_MODULES.find((item) => item.name === name);
	}

	today() {
		const now = new Date();
		return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-");
	}

	openModule(name) {
		if (name === "总经理") {
			this.addTab({ key: "module:总经理", title: "总经理", kind: "module", module: "总经理" });
			return;
		}
		this.addTab({ key: "module:" + name, title: name, kind: "module", module: name });
	}

	openPage(name, moduleName) {
		const report = JIFENG_LIVE_REPORTS[moduleName]?.[name];
		if (report) {
			frappe.set_route("query-report", report);
			return;
		}
		const create = JIFENG_LIVE_CREATE[moduleName]?.[name];
		if (create) {
			frappe.new_doc(create.doctype, create.values);
			return;
		}
		const liveDoctype = JIFENG_LIVE_ROUTES[moduleName]?.[name];
		if (liveDoctype) {
			const filters = JIFENG_LIVE_LIST_FILTERS[moduleName]?.[name];
			// Frappe List views can restore saved user filters when no route filter is supplied.
			frappe.route_options = filters || {};
			frappe.set_route("List", liveDoctype);
			return;
		}
		if (moduleName === "总经理" && this.module(name)) {
			this.openModule(name);
			return;
		}
		this.search = "";
		this.dateFrom = "";
		this.dateTo = "";
		this.onlyDate = false;
		this.approvalFilter = "未审";
		this.pageNo = 1;
		if (["操作日志", "销售单查询", "收款凭证", "付款凭证", "费用登记"].includes(name)) {
			const today = this.today();
			this.dateFrom = name === "操作日志" ? today + "T00:00" : today;
			this.dateTo = name === "操作日志" ? today + "T23:59" : today;
		}
		if (name === "计划图片") { this.planStatus = "生产中"; this.deliveryFilter = false; }
		if (name === "模具录入") { this.moldView = "列表模式"; this.moldPreview = true; }
		this.addTab({
			key: "page:" + moduleName + ":" + name,
			title: name,
			kind: "list",
			module: moduleName,
			page: name,
		});
	}

	addTab(tab) {
		const existing = this.tabs.find((item) => item.key === tab.key);
		if (existing) {
			this.activeKey = tab.key;
		} else {
			this.tabs.push(tab);
			this.activeKey = tab.key;
		}
		this.render();
	}

	activeTab() {
		return this.tabs.find((item) => item.key === this.activeKey) || this.tabs[0];
	}

	closeTab(key) {
		if (key === "home") return;
		const index = this.tabs.findIndex((item) => item.key === key);
		if (index < 0) return;
		this.tabs.splice(index, 1);
		if (this.activeKey === key) this.activeKey = this.tabs[Math.max(0, index - 1)].key;
		this.render();
	}

	render() {
		const active = this.activeTab();
		this.host.innerHTML =
			'<div class="jfe-shell">' +
			'<header class="jfe-topbar"><div class="jfe-brand">集沣贸易企业ERP管理系统</div>' +
			'<div class="jfe-top-tools"><span class="jfe-clock" data-clock></span><button class="jfe-icon-btn" data-action="fullscreen" title="全屏">⛶</button>' +
			'<button class="jfe-icon-btn" data-action="history" title="最近访问">◷</button><button class="jfe-user" data-action="user">◉ <span>当前用户</span>⌄</button></div></header>' +
			'<div class="jfe-body"><nav class="jfe-sidebar">' + this.renderSidebar() + '</nav>' +
			'<section class="jfe-work"><div class="jfe-tabs">' + this.renderTabs() + '<button class="jfe-tab-menu" data-action="tab-menu" title="标签列表">☷</button></div>' +
			'<main class="jfe-content">' + this.renderActive(active) + '</main></section></div>' +
			'<div class="jfe-toast" data-toast></div><div class="jfe-modal-root" data-modal-root></div></div>';
		this.bindEvents();
		this.updateClock();
		if (active.kind === "module" && active.module === "总经理") this.loadDashboard();
		clearInterval(this.clockTimer);
		this.clockTimer = setInterval(() => this.updateClock(), 30000);
	}

	renderSidebar() {
		const items = [
			"物料", "材料", "产品", "订单", "生产", "人事", "工资", "模具", "版种", "发镀",
			"外发", "销售", "财务", "总经理", "权限", "日志", "全屏", "退全屏", "插件", "工具", "关于",
		];
		const active = this.activeTab();
		return items
			.map((name) => {
				const action = name === "全屏" ? "fullscreen" : name === "退全屏" ? "exit-fullscreen" : "module";
				return '<button class="jfe-side-link ' + (active.module === name ? "is-active" : "") + '" data-action="' + action + '" data-name="' + this.esc(name) + '">' +
					'<span>' + this.esc(name) + "</span></button>";
			})
			.join("");
	}

	renderTabs() {
		return this.tabs
			.map((tab) => {
				return '<button class="jfe-tab ' + (tab.key === this.activeKey ? "is-active" : "") + '" data-action="tab" data-key="' + this.esc(tab.key) + '">' +
					this.esc(tab.title) +
					(tab.key === "home" ? "" : '<span class="jfe-tab-close" data-action="close-tab" data-key="' + this.esc(tab.key) + '">×</span>') +
					"</button>";
			})
			.join("");
	}

	renderActive(tab) {
		if (tab.kind === "home") return this.renderHome();
		if (tab.kind === "module") return this.renderModule(tab.module);
		if (tab.kind === "list" && tab.module === "工资" && tab.page === "工资录入") return this.renderPayrollEntry();
		if (tab.kind === "list") return this.renderList(tab.module, tab.page);
		return this.renderHome();
	}

	renderPayrollEntry() {
		const columns = ["日期", "姓名", "工序", "流程单", "小类", "编号", "订单数", "数量", "工价", "钻数", "附加费", "计件金额", "小时", "单价", "计时金额", "备注", "完成数", "超数", "前次价", "删除"];
		const today = this.today();
		return '<section class="jfe-list jfe-payroll"><div class="jfe-list-heading"><div><p>工资 / 工资操作</p><h1>工资录入</h1></div><span class="jfe-local-badge">本地交互预览</span></div>' +
			'<div class="jfe-payroll-controls"><label>日期<input type="date" data-payroll="date" value="' + today + '"></label><label>姓名<input type="text" data-payroll="employee" data-payroll-name placeholder="选择或录入姓名"></label><label>工序<input type="text" data-payroll="operation" data-payroll-operation placeholder="选择或录入工序"></label>' +
			'<label><input type="checkbox" data-payroll="multi"> 多人操作</label><label><input type="checkbox" data-payroll="multi-operation"> 多工序</label><label><input type="checkbox" data-payroll="default"> 默认</label></div>' +
			'<div class="jfe-payroll-hint">双击姓名为计时；“多人操作”时双击姓名为一个工序多人操作；“多工序”时双击工序为多工序一个人操作。</div>' +
			'<div class="jfe-payroll-shortcuts"><label>流程单<input data-payroll="flow" placeholder="流程单"></label><label>数量<input type="number" data-payroll="quantity"></label><label>工价<input type="number" data-payroll="price"></label>' +
			'<label>格式<select data-payroll="format"><option>格式1</option></select></label><button class="jfe-button" data-action="print-preview">打印</button><button class="jfe-button primary" data-action="payroll-save">保存</button></div>' +
			'<div class="jfe-payroll-panels"><section><header><b>员工</b></header><div class="jfe-table-wrap"><table class="jfe-table"><thead><tr><th>编号</th><th>姓名</th><th>工序</th></tr></thead><tbody><tr><td colspan="3" class="jfe-empty">暂无员工数据</td></tr></tbody></table></div></section>' +
			'<section><header><b>工资明细</b><button class="jfe-button small" data-action="payroll-add">新增行</button></header><div class="jfe-table-wrap"><table class="jfe-table jfe-wage-table"><thead><tr>' + columns.map((column) => "<th>" + this.esc(column) + "</th>").join("") +
			'</tr></thead><tbody>' + this.renderPayrollRow(columns) + "</tbody></table></div></section></div>" +
			'<div class="jfe-payroll-total"><b>合计数量：0</b><b>计件金额：0.00</b><b>计时金额：0.00</b></div>' +
			'<div class="jfe-list-footnote">页面输入与保存内容只写入当前浏览器本地草稿，不会生成工资凭证。</div></section>';
	}

	renderPayrollRow(columns) {
		return '<tr data-wage-row>' + columns.map((column) => {
			if (column === "删除") return '<td><button type="button" data-action="remove-wage-row">删除</button></td>';
			const type = ["数量", "工价", "钻数", "附加费", "计件金额", "小时", "单价", "计时金额", "完成数", "超数", "前次价", "订单数"].includes(column) ? "number" : "text";
			return '<td><input type="' + type + '" data-wage-column="' + this.esc(column) + '" ' + (column === "姓名" ? 'data-payroll-name' : column === "工序" ? 'data-payroll-operation' : "") + '></td>';
		}).join("") + "</tr>";
	}

	renderHome() {
		const quick = [
			["📷", "拍照 / 上传图", "upload-image"],
			["⌕", "找相似款", "find-similar"],
			["▣", "查库存", "quick-inventory"],
			["▤", "查订单", "quick-order"],
			["◆", "查库位", "quick-location"],
		];
		return '<section class="jfe-home"><div class="jfe-hero"><span>以图搜图 + ERP</span></div>' +
			'<div class="jfe-quick-row">' + quick.map((item) =>
				'<button class="jfe-quick-card" data-action="' + item[2] + '"><span class="jfe-quick-icon">' + item[0] + "</span><b>" + item[1] + "</b></button>"
			).join("") + "</div>" +
			'<section class="jfe-process"><div class="jfe-process-half"><h2>传统流程（效率低、易出错）</h2><div class="jfe-process-steps">' +
			'<span>找图片</span><i>➜</i><span>翻资料</span><i>➜</i><span>电话确认</span><i>➜</i><span>重复录单</span><i>➜</i><span>仓库查找</span></div>' +
			'<b>流程繁琐，客户等到没耐心</b></div><div class="jfe-process-half jfe-process-new"><h2>我们的系统（高效、准确、省心）</h2>' +
			'<div class="jfe-new-flow"><span class="jfe-gem">◈</span><i>⟶</i><span class="jfe-search-result">一图定位<br>订单 · 库存 · 库位</span></div><b>一张图，快速找到所需信息</b></div>' +
			'<button class="jfe-contact" data-action="contact">咨询详情</button></section>' +
			'<section class="jfe-benefits"><strong>为您带来的价值</strong><div><b>🚀 提升效率</b><span>效率提升<br>5–10倍</span></div><div><b>♟ 降低成本</b><span>减少人力<br>降低运营成本</span></div><div><b>▤ 提升业绩</b><span>快速响应客户<br>提升成交率</span></div><div><b>◇ 规范管理</b><span>流程标准化<br>数据更准确</span></div></section>' +
			'<footer class="jfe-footer"><b>请广大用户妥善保管好账号和密码！！！</b><span>集沣 ERP · 业务工作台</span></footer></section>';
	}

	renderModule(moduleName) {
		if (moduleName === "总经理") return this.renderDashboard();
		if (moduleName === "插件" || moduleName === "工具") {
			const label = moduleName === "插件" ? "打印插件" : "远程协助工具";
			return '<section class="jfe-notice"><div class="jfe-notice-icon">↗</div><h2>' + label + '</h2><p>原系统会跳转到外部安装包。此工作台仅保留入口说明。</p>' +
				'<button class="jfe-button primary" data-action="external-info">查看说明</button></section>';
		}
		if (moduleName === "关于") {
			return '<section class="jfe-about"><h1>集沣贸易企业 ERP 管理系统</h1><p>集沣 ERP · 经典业务工作台</p><div class="jfe-about-actions">' +
				'<button data-action="notice">用户须知</button><button data-action="renewal">续费信息</button></div><small>工作台交互预览 · 页面操作保存在当前浏览器</small></section>';
		}
		const module = this.module(moduleName);
		if (!module) return this.renderHome();
		return '<section class="jfe-module"><div class="jfe-module-title"><span class="jfe-module-icon">' + this.esc(moduleName.slice(0, 1)) + '</span><div><h1>' + this.esc(moduleName) + '</h1><p>“正式业务”进入服务器单据；其余页面为本地交互预览</p></div></div>' +
			module.groups.map((group) =>
				'<section class="jfe-group"><header><b>' + this.esc(group.name) + '</b><span>' + group.items.length + " 项</span></header><div class=\"jfe-function-grid\">" +
				group.items.map((name) => '<button class="jfe-function-card' + (JIFENG_LIVE_ROUTES[moduleName]?.[name] || JIFENG_LIVE_CREATE[moduleName]?.[name] || JIFENG_LIVE_REPORTS[moduleName]?.[name] ? ' is-live' : '') + '" data-action="open-page" data-module="' + this.esc(moduleName) + '" data-page="' + this.esc(name) + '">' +
					'<span class="jfe-function-mark">' + this.esc(name.slice(0, 1)) + "</span><span>" + this.esc(name) + "</span>" +
					(JIFENG_LIVE_ROUTES[moduleName]?.[name] || JIFENG_LIVE_CREATE[moduleName]?.[name] || JIFENG_LIVE_REPORTS[moduleName]?.[name] ? '<em class="jfe-live-tag">正式</em>' : "") + '<i>›</i></button>').join("") +
				"</div></section>"
			).join("") + "</section>";
	}

	renderDashboard() {
		const state = this.dashboard;
		const companies = state?.companies || [];
		const company = state?.company || "";
		const from = state?.from_date || this.today().slice(0, 7) + "-01";
		const to = state?.to_date || this.today();
		const controls = '<div class="jfe-dashboard-filters"><label>公司<select data-dashboard-company>' +
			companies.map((row) => '<option value="' + this.esc(row.name) + '"' + (row.name === company ? ' selected' : '') + '>' + this.esc(row.name) + '</option>').join("") +
			'</select></label><label>开始日期<input type="date" data-dashboard-from value="' + this.esc(from) + '"></label>' +
			'<label>结束日期<input type="date" data-dashboard-to value="' + this.esc(to) + '"></label>' +
			'<button class="jfe-button primary" data-action="dashboard-refresh">查询</button></div>';
		let body = '<p class="jfe-dashboard-status">正在读取正式单据…</p>';
		if (state?.error) body = '<p class="jfe-dashboard-status">' + this.esc(state.error) + '</p>';
		else if (state && !companies.length) body = '<p class="jfe-dashboard-status">当前账号没有可查看的公司。</p>';
		else if (state) {
			const items = [
				["sales", "销售净额", "已提交销售发票的本币未税净额，含退货冲减", "Sales Invoice", true],
				["purchases", "采购净额", "已提交采购发票的本币未税净额，含退货冲减", "Purchase Invoice", true],
				["expenses", "费用支出", "已提交集沣费用登记单金额", "Jifeng Expense Voucher", true],
				["open_work_orders", "未完工工单", "当前已提交且未完成、停止或关闭的工单", "Work Order", false],
			];
			body = '<div class="jfe-dashboard-grid">' + items.map(([key, label, hint, doctype, isMoney]) => {
				const value = state.metrics[key];
				if (value === null || value === undefined) return '';
				const display = isMoney ? format_currency(value, state.currency) : String(value);
				return '<button class="jfe-dashboard-card" data-action="dashboard-list" data-doctype="' + doctype + '" data-metric="' + key + '"><span>' + label + '</span><strong>' + this.esc(display) + '</strong><small>' + hint + '</small></button>';
			}).join("") + '</div><p class="jfe-dashboard-note">金额按所选日期统计；未完工工单是当前状态。点击指标查看正式单据。</p>';
		}
		return '<section class="jfe-dashboard"><div class="jfe-module-title"><span class="jfe-module-icon">总</span><div><h1>经营总览</h1><p>来自正式单据，按当前账号权限显示</p></div></div>' + controls + body + '</section>';
	}

	async loadDashboard(filters = {}) {
		const request = ++this.dashboardRequest;
		try {
			const response = await frappe.call({ method: "jifeng_erp.dashboard.overview", args: filters });
			if (request !== this.dashboardRequest) return;
			this.dashboard = response.message || { companies: [], metrics: {} };
		} catch (error) {
			if (request !== this.dashboardRequest) return;
			this.dashboard = { ...this.dashboard, error: error.message || "经营数据读取失败", metrics: {} };
		}
		if (this.activeTab().module === "总经理") {
			const node = this.host.querySelector(".jfe-dashboard");
			if (node) node.outerHTML = this.renderDashboard();
		}
	}

	renderList(moduleName, pageName) {
		const rows = this.getRows(pageName, moduleName);
		const keyword = this.search.trim().toLowerCase();
		const isVoucher = ["收款凭证", "付款凭证", "费用登记"].includes(pageName);
		const isLog = pageName === "操作日志";
		const isPlanImage = pageName === "计划图片";
		const isMold = pageName === "模具录入";
		const filtered = rows.filter((row) => {
			const textMatch = !keyword || Object.values(row).join(" ").toLowerCase().includes(keyword);
			let date = String(row.timestamp || row.date || row.time || "").replace(" ", "T");
			if (isLog && date.length === 10) date += "T12:00";
			const rangeMatch = (!this.dateFrom || !date || date >= this.dateFrom) && (!this.dateTo || !date || date <= this.dateTo);
			const exactMatch = !this.onlyDate || !this.dateFrom || date === this.dateFrom;
			const status = ["草稿", "未审核", "未审"].includes(row._status) ? "未审" : row._status === "已审核" || row._status === "已审" ? "已审" : row._status;
			const statusMatch = !isVoucher || this.approvalFilter === "全部" || !status || status === this.approvalFilter;
			const planState = row.plan_status || row.production_status || row.status || "";
			const planStatus = !isPlanImage || this.planStatus === "全部" || !planState || planState === this.planStatus;
			const deliveryMatch = !isPlanImage || !this.deliveryFilter || Boolean(row.delivery_date || row.delivery || row.deadline);
			return textMatch && rangeMatch && exactMatch && statusMatch && planStatus && deliveryMatch;
		});
		const start = (this.pageNo - 1) * this.pageSize;
		const pageRows = filtered.slice(start, start + this.pageSize);
		const columns = this.columnsFor(moduleName, pageName);
		if (pageName.includes("反审") && !columns.includes("审核选择框")) columns.unshift("审核选择框");
		const toolbar = this.toolbarFor(moduleName, pageName);
		const dateType = isLog ? "datetime-local" : "date";
		let filters = '<div class="jfe-filter-row">';
		if (!isMold) filters += '<label>' + (isLog ? "开始时间" : "开始日期") + '<input type="' + dateType + '" data-filter="from" value="' + this.esc(this.dateFrom) + '"></label><label>' + (isLog ? "结束时间" : "结束日期") + '<input type="' + dateType + '" data-filter="to" value="' + this.esc(this.dateTo) + '"></label>';
		filters += '<label class="jfe-keyword">关键词<input type="search" data-search value="' + this.esc(this.search) + '" placeholder="单号、名称、客户、备注"></label>';
		if (isPlanImage) filters += '<label class="jfe-check"><input type="checkbox" data-delivery-filter ' + (this.deliveryFilter ? "checked" : "") + '> 交期</label>';
		else if (!isLog && !isMold) filters += '<label class="jfe-check"><input type="checkbox" data-only-date ' + (this.onlyDate ? "checked" : "") + '> 指定日期</label>';
		filters += '<button class="jfe-button primary" data-action="query">查询</button>';
		if (!isLog && !isPlanImage && !isMold) filters += '<button class="jfe-button" data-action="format">格式1</button><button class="jfe-button" data-action="print-preview">打印</button><button class="jfe-icon-btn small" data-action="more" title="更多">⚙</button>';
		filters += '</div>';
		const statusFilters = isVoucher ? '<div class="jfe-status-filter"><label><input type="radio" name="approval-filter" data-approval-filter value="全部" ' + (this.approvalFilter === "全部" ? "checked" : "") + '> 全部</label><label><input type="radio" name="approval-filter" data-approval-filter value="已审" ' + (this.approvalFilter === "已审" ? "checked" : "") + '> 已审</label><label><input type="radio" name="approval-filter" data-approval-filter value="未审" ' + (this.approvalFilter === "未审" ? "checked" : "") + '> 未审</label></div>' : "";
		const planStatusFilters = isPlanImage ? '<div class="jfe-status-filter jfe-plan-status">' + ["未投产", "生产中", "已完成"].map((item) => '<label><input type="radio" name="plan-status" data-plan-status value="' + item + '" ' + (this.planStatus === item ? "checked" : "") + '> ' + item + '</label>').join("") + '<label><input type="radio" name="plan-status" data-plan-status value="全部" ' + (this.planStatus === "全部" ? "checked" : "") + '> 所有</label></div>' : "";
		const moldModes = isMold ? '<div class="jfe-mold-tabs">' + ["列表模式", "图片模式", "列表图文"].map((item) => '<button type="button" class="jfe-button ' + (this.moldView === item ? "primary" : "") + '" data-action="mold-view" data-view="' + item + '">' + item + '</button>').join("") + '<label><input type="checkbox" data-mold-preview ' + (this.moldPreview ? "checked" : "") + '> 预览</label></div>' : "";
		const extra = isLog ? '<div class="jfe-log-tip">可按操作时间、操作人、模块或操作内容筛选；旧系统日志数据未复制，本地预览未连接生产日志服务。</div>' : "";
		const moldGallery = isMold && (this.moldView !== "列表模式" || this.moldPreview) ? this.renderMoldGallery(pageRows) : "";
		const showMoldTable = !isMold || this.moldView !== "图片模式";
		const tableHtml = '<div class="jfe-table-wrap"><table class="jfe-table"><thead><tr>' + columns.map((column) => "<th>" + this.esc(column) + "</th>").join("") +
			'</tr></thead><tbody>' + (pageRows.length ? pageRows.map((row) => this.renderRow(row, columns, pageName)).join("") : '<tr><td colspan="' + columns.length + '" class="jfe-empty">暂无数据</td></tr>') + '</tbody></table></div>';
		const pagerHtml = '<div class="jfe-list-bottom"><span>共 ' + filtered.length + ' 条</span><div class="jfe-pager"><button data-action="prev-page" ' + (this.pageNo <= 1 ? "disabled" : "") + '>‹</button><span>' + this.pageNo + '</span><button data-action="next-page" ' + (start + this.pageSize >= filtered.length ? "disabled" : "") + '>›</button><select><option value="15" ' + (this.pageSize === 15 ? "selected" : "") + '>15</option><option value="30" ' + (this.pageSize === 30 ? "selected" : "") + '>30</option><option value="50" ' + (this.pageSize === 50 ? "selected" : "") + '>50</option></select></div></div>';
		return '<section class="jfe-list"><div class="jfe-list-heading"><div><p>' + this.esc(moduleName) + " / 业务页面</p><h1>" + this.esc(pageName) + '</h1></div>' +
			'<span class="jfe-local-badge">本地交互预览</span></div><div class="jfe-toolbar">' + toolbar + "</div>" +
			filters + statusFilters + planStatusFilters + moldModes + extra + moldGallery +
			(showMoldTable ? tableHtml + pagerHtml : moldGallery ? '<div class="jfe-list-bottom">共 ' + filtered.length + ' 条</div>' : "") +
			(this.isSalesReport(pageName) ? this.renderSalesTotals() : "") +
			'<div class="jfe-list-footnote">页面交互在当前浏览器本地保存，不会创建或修改 ERPNext 业务凭证。</div></section>';
	}

	renderMoldGallery(rows) {
		return '<div class="jfe-mold-gallery">' + (rows.length ? rows.map((row) => '<article class="jfe-mold-card"><div class="jfe-mold-thumb">' + (this.moldPreview && row.image ? '<span>' + this.esc(row.image) + '</span>' : '<span>图片预览</span>') + '</div><b>' + this.esc(row.mold_number || row.number || row.code || "未填写模具号") + '</b><span>产品号：' + this.esc(row.product_number || row.product || "") + '</span><span>库位：' + this.esc(row.location || "") + '</span><button type="button" class="jfe-button small" data-action="edit-row" data-id="' + this.esc(row._id) + '">查看 / 修改</button></article>').join("") : '<div class="jfe-empty">暂无模具图片</div>') + '</div>';
	}

	toolbarFor(moduleName, pageName) {
		if (pageName === "操作日志") return '<button class="jfe-button" data-action="refresh">更新日志</button><button class="jfe-button" data-action="export">导出</button>';
		if (pageName === "计划图片") return '<button class="jfe-button" data-action="all-plans">所有</button><button class="jfe-button" data-action="format">格式1</button><button class="jfe-button" data-action="print-preview">打总单</button><button class="jfe-button" data-action="print-preview">打流程</button><button class="jfe-button" data-action="print-preview">打明细</button><button class="jfe-button" data-action="export">导出</button>';
		if (pageName === "模具录入") return '<button class="jfe-button primary" data-action="new">新增</button><button class="jfe-button" data-action="export">导出</button><button class="jfe-button" data-action="import">导入</button><button class="jfe-button" data-action="format">格式1</button><button class="jfe-button" data-action="print-preview">打印</button>';
		if (pageName === "报价单") return '<button class="jfe-button primary" data-action="new">新增</button><button class="jfe-button" data-action="export">导出</button>';
		if (pageName === "物料类别") return '<button class="jfe-button" data-action="export">导出</button><button class="jfe-button" data-action="import">导入</button><button class="jfe-button primary" data-action="save-grid">保存</button><button class="jfe-button" data-action="pinyin">更新简拼</button>';
		if (["收款凭证", "付款凭证", "费用登记"].includes(pageName)) return '<button class="jfe-button primary" data-action="new">新增</button><button class="jfe-button" data-action="review">审核</button><button class="jfe-button" data-action="export">导出</button>';
		if (pageName === "生产指令单" || pageName === "前台开单") return '<button class="jfe-button primary" data-action="new">新增</button><button class="jfe-button" data-action="export">导出</button>';
		const report = this.isReport(pageName);
		let html = "";
		if (!report) html += '<button class="jfe-button primary" data-action="new">新增</button>';
		if (pageName.includes("反审")) html += '<button class="jfe-button danger" data-action="unreview">反审</button>';
		if (pageName.includes("跟踪")) html += '<button class="jfe-button" data-action="track">跟踪</button>';
		if (pageName.includes("入库")) html += '<button class="jfe-button" data-action="stock-in">入库</button>';
		if (pageName.includes("调拨")) html += '<button class="jfe-button" data-action="transfer">调拨</button>';
		if (pageName.includes("工资")) html += '<button class="jfe-button" data-action="calculate">计算</button>';
		if (pageName.includes("计算") || pageName.includes("算料")) html += '<button class="jfe-button" data-action="calculate">计算</button>';
		html += '<button class="jfe-button" data-action="export">导出</button>';
		if (!report) html += '<button class="jfe-button" data-action="import">导入</button>';
		if (pageName === "前台开单") html += '<button class="jfe-button" data-action="customer">客户档案</button><button class="jfe-button" data-action="select">调入历史</button>';
		if (pageName === "生产指令单") html += '<button class="jfe-button" data-action="schedule">调计划</button>';
		return html;
	}

	renderRow(row, columns, pageName) {
		const keyMap = this.rowKeyMap(row);
		return '<tr data-action="edit-row" data-id="' + this.esc(row._id) + '">' +
			columns.map((column) => {
				if (column === "审核选择框") return '<td><input type="checkbox" data-row-select="' + this.esc(row._id) + '"></td>';
				if (column === "分享") return '<td><button data-action="share-row" data-id="' + this.esc(row._id) + '">分享</button></td>';
				if (column === "表格") return '<td><button data-action="edit-row" data-id="' + this.esc(row._id) + '">表格</button></td>';
				if (column === "带图片") return '<td><button data-action="image-row" data-id="' + this.esc(row._id) + '">带图片</button></td>';
				if (column === "下载") return '<td><button data-action="download-row" data-id="' + this.esc(row._id) + '">下载</button></td>';
				if (column === "操作") return '<td class="jfe-row-actions"><button data-action="edit-row" data-id="' + this.esc(row._id) + '">查看</button><button data-action="delete-row" data-id="' + this.esc(row._id) + '">删除</button></td>';
				if (column === "修改") return '<td><button data-action="edit-row" data-id="' + this.esc(row._id) + '">修改</button></td>';
				if (column === "删除") return '<td><button data-action="delete-row" data-id="' + this.esc(row._id) + '">删除</button></td>';
				if (column === "审核") return '<td><button data-action="review-one" data-id="' + this.esc(row._id) + '">' + this.esc(row._status || "未审核") + "</button></td>";
				return "<td>" + this.esc(keyMap[column] || row[column] || "") + "</td>";
			}).join("") + "</tr>";
	}

	renderSalesTotals() {
		const labels = ["部门", "导购", "客户", "操作"];
		return '<div class="jfe-totals">' + labels.map((label) =>
			'<div><b>' + label + '</b><span>数量：0</span><span>金额：0.00</span></div>'
		).join("") + "</div>";
	}

	renderEditor(record) {
		const active = this.activeTab();
		const pageName = active.page;
		const fields = this.fieldsFor(active.module, pageName);
		const detailCols = this.detailColumns(pageName);
		const detailHasDeleteColumn = detailCols.includes("删除");
		const detailRows = record && record._details && record._details.length ? record._details : [this.emptyDetail()];
		const fieldsHtml = fields.map((field) => this.renderField(field, record)).join("");
		const detailHtml = detailCols.length ? '<section class="jfe-detail"><header><b>明细</b><button type="button" class="jfe-button small" data-action="add-detail">新增明细</button></header><div class="jfe-table-wrap"><table class="jfe-table jfe-detail-table"><thead><tr>' +
			detailCols.map((col) => "<th>" + this.esc(col) + "</th>").join("") + (detailHasDeleteColumn ? "" : "<th>操作</th>") + "</tr></thead><tbody>" +
			detailRows.map((detail) => '<tr>' + detailCols.map((col) => col === "删除" ? '<td><button type="button" data-action="remove-detail">删除</button></td>' : '<td><input data-detail="' + this.esc(col) + '" value="' + this.esc(detail[col] || "") + '" placeholder="' + this.esc(col) + '"></td>').join("") +
				(detailHasDeleteColumn ? "" : '<td><button type="button" data-action="remove-detail">删除</button></td>') + "</tr>").join("") + "</tbody></table></div></section>" : "";
		const salesActions = pageName === "前台开单" ?
			'<section class="jfe-sales-entry"><div class="jfe-scan-row"><label>扫码录入<input name="scan_code" placeholder="条码 / 数量"></label><button type="button" class="jfe-button primary" data-action="sales-add">新增</button><button type="button" class="jfe-button" data-action="sales-return">退货</button><button type="button" class="jfe-button" data-action="discount">折扣</button><button type="button" class="jfe-button" data-action="collect">收款</button></div><div class="jfe-sales-options"><label><input type="checkbox"> 默认</label><label><input type="checkbox"> 累加</label><label><input type="checkbox"> 退货</label><label><input type="checkbox"> 零售</label><label><input type="checkbox"> 跟踪价</label><label><input type="checkbox"> 按单</label><label><input type="checkbox"> 预览</label><button type="button" class="jfe-button" data-action="hold">暂存</button><button type="button" class="jfe-button" data-action="restore">恢复</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打印</button><button type="button" class="jfe-button primary" data-action="review-form">审核</button></div></section>' : "";
		const editorExtra = this.editorExtra(active.module, pageName);
		const extraAboveDetails = pageName === "前台开单" || (active.module === "销售" && pageName === "销售入库") || (active.module === "外发" && pageName === "外发加工");
		const editorExtraTop = extraAboveDetails ? editorExtra : "";
		const editorExtraBottom = extraAboveDetails ? "" : editorExtra;
		const editorActions = pageName === "前台开单" ?
			'<button type="button" class="jfe-button" data-action="customer">客户档案</button><button type="button" class="jfe-button danger" data-action="delete-current">删除单据</button><button type="button" class="jfe-button" data-action="change-customer">修改客户</button><button type="button" class="jfe-button" data-action="stock-in">调入库</button><button type="button" class="jfe-button" data-action="load-transfer">调调拨</button><button type="button" class="jfe-button" data-action="load-history">调历史</button><button type="button" class="jfe-button" data-action="more">⚙</button>' :
			active.module === "模具" && pageName === "模具录入" ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="close-modal">关闭</button>' :
			active.module === "产品" && pageName === "报价单" ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button danger" data-action="delete-current">删除</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="review-form">审核</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打印</button>' :
			pageName === "订单录入" ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button danger" data-action="delete-current">删除</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="load-history">调历史</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打总单</button><button type="button" class="jfe-button" data-action="print-preview">打流程单</button><button type="button" class="jfe-button" data-action="review-form">审核</button><button type="button" class="jfe-button" data-action="more">⚙</button><button type="button" class="jfe-button" disabled>行高</button>' :
			["物料", "材料"].includes(active.module) && pageName === "入库" ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button danger" data-action="delete-current">删除</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="review-form">审核</button><button type="button" class="jfe-button" data-action="calculate">调算料</button><button type="button" class="jfe-button" data-action="purchase">调申购</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打印</button><button type="button" class="jfe-button" data-action="more">⚙</button>' :
			["物料", "材料"].includes(active.module) && pageName === "申购" ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button danger" data-action="delete-current">删除</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="review-form">审核</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打印</button>' :
			active.module === "销售" && pageName === "销售入库" ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button danger" data-action="delete-current">删除</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="stockin-add">条码 / 输入</button><button type="button" class="jfe-button" data-action="review-form">审核</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打印</button><button type="button" class="jfe-button" data-action="load-pos">调前台</button><button type="button" class="jfe-button" data-action="load-transfer">调调拨</button><button type="button" class="jfe-button" data-action="more">⚙</button>' :
			active.module === "外发" && pageName === "外发加工" ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button danger" data-action="delete-current">删除</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="load-order">调订单</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打印</button><button type="button" class="jfe-button" data-action="review-form">审核</button>' :
			["收款凭证", "付款凭证", "费用登记"].includes(pageName) ? '<button type="button" class="jfe-button" data-action="new">新增</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="close-modal">关闭</button>' :
			'<button type="button" class="jfe-button primary" data-action="save-form">保存</button><button type="button" class="jfe-button" data-action="schedule">调计划</button><button type="button" class="jfe-button" data-action="review-form">审核</button><button type="button" class="jfe-button" data-action="format">格式1</button><button type="button" class="jfe-button" data-action="print-preview">打印</button>';
		return '<div class="jfe-modal-backdrop" data-action="backdrop"><section class="jfe-editor" role="dialog" aria-modal="true"><header class="jfe-editor-head"><div><p>' + this.esc(active.module) + "</p><h2>" +
			(record ? "修改" : "新增") + " · " + this.esc(pageName) + '</h2></div><button class="jfe-icon-btn" data-action="close-modal">×</button></header>' +
			'<div class="jfe-editor-tools">' + editorActions + "</div>" +
			'<form class="jfe-editor-form" data-record-id="' + this.esc(record ? record._id : "") + '"><div class="jfe-form-grid">' + fieldsHtml + "</div>" + editorExtraTop + detailHtml + editorExtraBottom + salesActions + "</form>" +
			'<footer class="jfe-editor-foot"><span>草稿只保存在当前浏览器。</span><div><button type="button" class="jfe-button" data-action="close-modal">关闭</button><button type="button" class="jfe-button primary" data-action="save-form">保存</button></div></footer></section></div>';
	}

	fieldsFor(moduleName, pageName) {
		if (moduleName === "模具" && pageName === "模具录入") return [
			{ name: "mold_number", label: "模具号" }, { name: "product_number", label: "产品号" },
			{ name: "quantity", label: "数量", type: "number", default: "1" },
			{ name: "molder", label: "压模", type: "select", options: ["", "本人", "其他"] },
			{ name: "date", label: "日期", type: "date", default: this.today() }, { name: "amount", label: "金额", type: "number" },
			{ name: "remark", label: "说明", type: "textarea", full: true }, { name: "location", label: "库位" },
			{ name: "scrapped", label: "报废", type: "checkbox" }, { name: "copy", label: "复制", type: "checkbox" },
			{ name: "image", label: "图片", type: "file" },
		];
		if (moduleName === "产品" && pageName === "报价单") return [
			{ name: "number", label: "单号" }, { name: "customer", label: "客户", lookup: "客户查找" },
			{ name: "remark", label: "备注", type: "textarea", full: true },
			{ name: "method", label: "方式", type: "select", options: ["", "现金", "银行转账", "其他"] },
			{ name: "discount_rate", label: "折扣", type: "number" }, { name: "quantity", label: "数量", type: "number" },
			{ name: "weight", label: "重量", type: "number" }, { name: "amount", label: "金额", type: "number" },
			{ name: "payment", label: "付款", type: "number" }, { name: "discount", label: "优惠", type: "number" },
			{ name: "arrears", label: "欠款", type: "number" },
		];
		if (["物料", "材料"].includes(moduleName) && pageName === "申购") return [
			{ name: "number", label: "单号" }, { name: "warehouse", label: "仓库", lookup: "仓库词典" },
			{ name: "brand", label: "品牌" }, { name: "company", label: "公司" }, { name: "contact", label: "联系" },
			{ name: "buyer", label: "采购" }, { name: "phone", label: "手机" }, { name: "address", label: "地址" },
			{ name: "quantity", label: "数量", type: "number" }, { name: "weight", label: "重量", type: "number" },
			{ name: "amount", label: "金额", type: "number" }, { name: "remark", label: "备注", type: "textarea", full: true },
		];
		if (moduleName === "销售" && pageName === "销售入库") return [
			{ name: "number", label: "单号" }, { name: "department", label: "部门" },
			{ name: "type", label: "类型" }, { name: "customer", label: "客户" },
			{ name: "status", label: "状态" }, { name: "remark", label: "说明", type: "textarea", full: true },
			{ name: "method", label: "方式" }, { name: "quantity", label: "数量", type: "number" },
			{ name: "styles", label: "款数", type: "number" }, { name: "amount", label: "金额", type: "number" },
			{ name: "payment", label: "付款", type: "number" }, { name: "discount", label: "优惠", type: "number" },
			{ name: "arrears", label: "欠款", type: "number" },
		];
		if (["物料", "材料"].includes(moduleName) && pageName === "入库") return [
			{ name: "number", label: "单号" }, { name: "warehouse", label: "仓库", lookup: "仓库词典", placeholder: "请点新增" },
			{ name: "brand", label: "品牌" }, { name: "buyer", label: "采购" }, { name: "delivery_note", label: "货单" },
			{ name: "delivery_amount", label: "货单金额", type: "number" }, { name: "method", label: "方式" },
			{ name: "quantity", label: "数量", type: "number" }, { name: "weight", label: "重量", type: "number" },
			{ name: "amount", label: "金额", type: "number" }, { name: "payment", label: "付款", type: "number" },
			{ name: "discount", label: "优惠", type: "number" }, { name: "arrears", label: "欠款", type: "number" },
			{ name: "remark", label: "备注", type: "textarea", full: true },
		];
		if (moduleName === "订单" && pageName === "订单录入") return [
			{ name: "number", label: "单号" }, { name: "customer", label: "客户", lookup: "客户查找", placeholder: "请点新增" },
			{ name: "self_number", label: "自编" }, { name: "date", label: "日期", type: "date" },
			{ name: "preproduction_date", label: "前制日期", type: "date" }, { name: "delivery_date", label: "交期", type: "date" },
			{ name: "department", label: "部门" }, { name: "type", label: "类型" }, { name: "requirement", label: "要求" },
			{ name: "contract", label: "合同" }, { name: "business", label: "业务" }, { name: "phone", label: "电话" },
			{ name: "quantity", label: "数量", type: "number" }, { name: "amount", label: "金额", type: "number" },
			{ name: "remark", label: "备注", type: "textarea", full: true },
		];
		if (moduleName === "产品" && pageName === "产品档案") return [
			{ name: "number", label: "编号" }, { name: "code", label: "代码" }, { name: "major_category", label: "大类", lookup: "产品类别词典" },
			{ name: "minor_category", label: "小类" }, { name: "old_number", label: "旧号" }, { name: "brand", label: "品牌" },
			{ name: "material", label: "材质" }, { name: "shared_version", label: "共版" }, { name: "patent_number", label: "专利号" },
			{ name: "name", label: "名称" }, { name: "source", label: "来源" }, { name: "designer", label: "设计" },
			{ name: "pattern_maker", label: "打版" }, { name: "modeler", label: "建模" }, { name: "style", label: "风格" },
			{ name: "owner", label: "负责" }, { name: "region", label: "区域" }, { name: "unit", label: "单位" },
			{ name: "price", label: "单价", type: "number" }, { name: "blank_price", label: "毛胚价", type: "number" },
			{ name: "weight", label: "重量", type: "number" }, { name: "silver_weight", label: "银重", type: "number" },
			{ name: "stone_weight", label: "石重", type: "number" }, { name: "inactive", label: "停用", type: "checkbox" },
			{ name: "copy", label: "复制", type: "checkbox" }, { name: "remark", label: "备注", type: "textarea", full: true },
			{ name: "notes", label: "注意事项", type: "textarea", full: true },
			{ name: "product_image", label: "产品图片", type: "file" }, { name: "patent_image", label: "专利图片", type: "file" },
		];
		if (moduleName === "人事" && pageName === "人事档案") return [
			{ name: "department", label: "部门", lookup: "人事部门词典" }, { name: "title", label: "职称" },
			{ name: "education", label: "文化" }, { name: "time_based", label: "计时", type: "checkbox" },
			{ name: "bank", label: "银行" }, { name: "account", label: "账号" }, { name: "identity_number", label: "身份证号码" },
			{ name: "hire_date", label: "入职日", type: "date" }, { name: "province", label: "省份" },
			{ name: "address", label: "地址" }, { name: "number", label: "编号" }, { name: "gender", label: "性别", type: "radios", options: ["男", "女"] },
			{ name: "method", label: "方式", type: "select", options: ["计时", "计件"] }, { name: "phone", label: "手机" },
			{ name: "account_name", label: "户名" }, { name: "ethnicity", label: "民族" }, { name: "specialty", label: "专长" },
			{ name: "dormitory", label: "宿舍" }, { name: "birthday", label: "生日", type: "date" },
			{ name: "introduction", label: "介绍" }, { name: "seniority", label: "工龄" },
			{ name: "employee_type", label: "类型", type: "select", options: ["长期", "临时"] },
			{ name: "marital_status", label: "婚否", type: "radios", options: ["已婚", "未婚"] },
			{ name: "copy", label: "复制", type: "checkbox" }, { name: "resigned", label: "离职", type: "checkbox" },
			{ name: "photo", label: "照片", type: "file" }, { name: "identity_image", label: "身份证图片", type: "file" },
			{ name: "remark", label: "备注", type: "textarea", full: true },
			{ name: "resignation_reason", label: "离职原因", type: "textarea", full: true },
		];
		if (moduleName === "财务" && ["收款凭证", "付款凭证", "费用登记"].includes(pageName)) return [
			{ name: "arrears", label: "欠款", type: "number" }, { name: "store", label: "店面", lookup: "销售部门词典" },
			{ name: "number", label: "编号" }, { name: "name", label: "名称" }, { name: "method", label: "方式", type: "select", options: ["现金", "银行转账", "其他"] },
			{ name: "amount", label: "金额", type: "number" }, { name: "summary", label: "摘要", type: "textarea", full: true },
		];
		if (pageName === "生产指令单") return [
			{ name: "number", label: "单号" }, { name: "date", label: "日期", type: "date" },
			{ name: "instruction_date", label: "指令日期", type: "date" }, { name: "quantity", label: "数量", type: "number" },
			{ name: "weight", label: "重量", type: "number" }, { name: "amount", label: "金额", type: "number" },
			{ name: "remark", label: "备注", type: "textarea", full: true },
		];
		if (moduleName === "外发" && pageName === "外发加工") return [
			{ name: "number", label: "单号" }, { name: "name", label: "名称" }, { name: "delivery_date", label: "交期", type: "date" },
			{ name: "phone", label: "手机" }, { name: "receiver", label: "接货" }, { name: "telephone", label: "电话" },
			{ name: "phone2", label: "手机" }, { name: "quantity", label: "数量", type: "number" },
			{ name: "styles", label: "款数", type: "number" }, { name: "amount", label: "金额", type: "number" },
		];
		if (pageName === "前台开单") return [
			{ name: "number", label: "单号" }, { name: "date", label: "日期", type: "date" },
			{ name: "customer", label: "客户", placeholder: "请点新增后选择客户" }, { name: "department", label: "部门" },
			{ name: "salesperson", label: "导购" }, { name: "status", label: "状态" },
			{ name: "remark", label: "备注", type: "textarea", full: true },
		];
		if (moduleName === "订单" || pageName.includes("订单")) return [
			{ name: "number", label: "单号" }, { name: "date", label: "日期", type: "date" },
			{ name: "customer", label: "客户" }, { name: "type", label: "类型" },
			{ name: "department", label: "部门" }, { name: "amount", label: "金额", type: "number" },
			{ name: "delivery_date", label: "交期", type: "date" }, { name: "remark", label: "要求 / 备注", type: "textarea", full: true },
		];
		if (moduleName === "人事" || pageName.includes("人员") || pageName.includes("人事")) return [
			{ name: "employee_no", label: "员工编号" }, { name: "name", label: "姓名" },
			{ name: "department", label: "部门" }, { name: "position", label: "岗位 / 专长" },
			{ name: "phone", label: "联系电话" }, { name: "status", label: "状态" },
			{ name: "remark", label: "备注", type: "textarea", full: true },
		];
		if (moduleName === "财务" || pageName.includes("凭证") || pageName.includes("费用")) return [
			{ name: "number", label: "单号" }, { name: "date", label: "日期", type: "date" },
			{ name: "account", label: "银行 / 科目" }, { name: "counterparty", label: "往来单位" },
			{ name: "amount", label: "金额", type: "number" }, { name: "method", label: "收付款方式" },
			{ name: "remark", label: "摘要 / 备注", type: "textarea", full: true },
		];
		return [
			{ name: "code", label: "编号" }, { name: "name", label: "名称" },
			{ name: "category", label: "类别" }, { name: "specification", label: "规格" },
			{ name: "unit", label: "单位" }, { name: "quantity", label: "数量", type: "number" },
			{ name: "date", label: "日期", type: "date" }, { name: "remark", label: "备注", type: "textarea", full: true },
		];
	}

	editorExtra(moduleName, pageName) {
		if (pageName === "前台开单") {
			return '<div class="jfe-pos-summary"><span>数量<b data-pos-total="quantity">0</b></span><span>款数<b data-pos-total="styles">0</b></span><span>折前<b data-pos-total="gross">0.00</b></span><span>金额<b data-pos-total="amount">0.00</b></span><label>运费<input name="freight" type="number" step="0.01" value="0"></label><label>欠款<input name="arrears" type="number" step="0.01" value="0"></label></div>';
		}
		if (moduleName === "销售" && pageName === "销售入库") {
			return '<div class="jfe-inbound-tools"><label>条码 / 输入<input name="barcode_input" placeholder="扫描条码或输入编号"></label><label>数量<input name="quantity_input" type="number" placeholder="数量"></label><label class="jfe-check"><input type="checkbox" name="by_order" value="是"> 按单</label><button type="button" class="jfe-button" data-action="stockin-add">输入</button><span class="jfe-no-image">图片预览</span></div><div class="jfe-stockin-summary"><span>数量<b data-stockin-total="quantity">0</b></span><span>款数<b data-stockin-total="styles">0</b></span><span>金额<b data-stockin-total="amount">0.00</b></span><span>付款<b data-stockin-total="payment">0.00</b></span><span>优惠<b data-stockin-total="discount">0.00</b></span><span>欠款<b data-stockin-total="arrears">0.00</b></span></div>';
		}
		if (moduleName === "外发" && pageName === "外发加工") return '<div class="jfe-inbound-tools jfe-outsource-tools"><label>流程单输入<input name="flow_input" placeholder="流程单"></label><label>数量输入<input name="quantity_input" type="number" placeholder="数量"></label><label>工序<select name="operation_input"><option value="">选择工序</option><option>加工</option><option>镶石</option><option>抛光</option></select></label><button type="button" class="jfe-button" data-action="external-add">新增工序</button><span class="jfe-no-image">图片区域</span></div><div class="jfe-stockin-summary"><span>数量<b data-external-total="quantity">0</b></span><span>款数<b data-external-total="styles">0</b></span><span>金额<b data-external-total="amount">0.00</b></span></div>';
		if (moduleName === "产品" && pageName === "报价单") return '<div class="jfe-stockin-summary"><span>数量<b data-quote-total="quantity">0</b></span><span>重量<b data-quote-total="weight">0</b></span><span>金额<b data-quote-total="amount">0.00</b></span><span>付款<b data-quote-total="payment">0.00</b></span><span>优惠<b data-quote-total="discount">0.00</b></span><span>欠款<b data-quote-total="arrears">0.00</b></span></div>';
		if (["物料", "材料"].includes(moduleName) && pageName === "入库") {
			return '<div class="jfe-inbound-tools"><label>明细输入<input name="detail_input" placeholder="请输入"></label><label>数量<input name="quantity_input" type="number" placeholder="数量"></label><button type="button" class="jfe-button" data-action="inbound-add">扫码 / 增加明细</button><span class="jfe-no-image">暂无图片</span></div>';
		}
		if (moduleName === "订单" && pageName === "订单录入") {
			return '<div class="jfe-order-summary"><span>在线</span><span>次数：0</span><span>库存：0</span><b>数量：0</b><b>金额：0.00</b></div>';
		}
		if (moduleName === "产品" && pageName === "产品档案") {
			return '<div class="jfe-image-note">图片名称不能含有中文。产品图片、专利图片可在上方选择文件；清空按钮只清除当前预览。</div><div class="jfe-image-actions"><button type="button" class="jfe-button" data-action="clear-product-image">清空产品图片</button><button type="button" class="jfe-button" data-action="clear-patent-image">清空专利图片</button></div>';
		}
		if (moduleName === "人事" && pageName === "人事档案") {
			return this.employeeRecordsHtml() + '<div class="jfe-image-actions"><button type="button" class="jfe-button" data-action="clear-file" data-field="photo">清空照片</button><button type="button" class="jfe-button" data-action="clear-file" data-field="identity_image">清空身份证图片</button></div>';
		}
		return "";
	}

	employeeColumns(name) {
		const columns = {
			"工作经历": ["开始", "结束", "公司", "职务", "项目 / 工作内容", "离职原因"],
			"教育经历": ["开始", "结束", "学校", "专业", "学历", "备注"],
			"调薪记录": ["日期", "原工资", "调整后工资", "原因", "备注"],
			"升职记录": ["日期", "原职务", "新职务", "原因", "备注"],
		};
		return columns[name] || columns["工作经历"];
	}

	employeeRecordsHtml() {
		const tabs = ["工作经历", "教育经历", "调薪记录", "升职记录"];
		const active = this.activeEmployeeTab || "工作经历";
		return '<section class="jfe-employee-records"><div class="jfe-employee-tabs">' + tabs.map((name) =>
			'<button type="button" class="jfe-button ' + (name === active ? "primary" : "") + '" data-action="employee-subtab" data-name="' + this.esc(name) + '">' + name + "</button>"
		).join("") + '<button type="button" class="jfe-button small" data-action="employee-add">新增</button></div>' +
			this.renderEmployeeTable(active) + "</section>";
	}

	renderEmployeeTable(name) {
		const columns = this.employeeColumns(name);
		const rows = this.employeeDrafts && this.employeeDrafts[name] ? this.employeeDrafts[name] : [];
		return '<div class="jfe-table-wrap jfe-employee-table"><table class="jfe-table"><thead><tr>' + columns.map((column) => "<th>" + this.esc(column) + "</th>").join("") +
			"<th>操作</th></tr></thead><tbody>" + (rows.length ? rows.map((row, index) => '<tr data-employee-row data-index="' + index + '">' +
			columns.map((column) => '<td><input data-employee-field="' + this.esc(column) + '" value="' + this.esc(row[column] || "") + '"></td>').join("") +
			'<td><button type="button" data-action="employee-remove" data-index="' + index + '">删除</button></td></tr>').join("") :
			'<tr><td colspan="' + (columns.length + 1) + '" class="jfe-empty">暂无记录</td></tr>') + "</tbody></table></div>";
	}

	captureEmployeeRows() {
		const name = this.activeEmployeeTab || "工作经历";
		const table = this.host.querySelector(".jfe-employee-table tbody");
		if (!table) return;
		this.employeeDrafts = this.employeeDrafts || {};
		this.employeeDrafts[name] = Array.from(table.querySelectorAll("[data-employee-row]")).map((tr) => {
			const row = {};
			tr.querySelectorAll("[data-employee-field]").forEach((input) => row[input.dataset.employeeField] = input.value);
			return row;
		});
	}

	setEmployeeSubtab(name) {
		this.captureEmployeeRows();
		this.activeEmployeeTab = name;
		const tabs = this.host.querySelector(".jfe-employee-records");
		if (tabs) tabs.outerHTML = this.employeeRecordsHtml();
	}

	addEmployeeRow() {
		this.captureEmployeeRows();
		const name = this.activeEmployeeTab || "工作经历";
		this.employeeDrafts = this.employeeDrafts || {};
		this.employeeDrafts[name].push({});
		const section = this.host.querySelector(".jfe-employee-records");
		if (section) section.outerHTML = this.employeeRecordsHtml();
	}

	removeEmployeeRow(index) {
		this.captureEmployeeRows();
		const name = this.activeEmployeeTab || "工作经历";
		this.employeeDrafts[name].splice(Number(index), 1);
		const section = this.host.querySelector(".jfe-employee-records");
		if (section) section.outerHTML = this.employeeRecordsHtml();
	}

	readFormValues(form) {
		const values = Object.fromEntries(new FormData(form).entries());
		form.querySelectorAll('input[type="checkbox"][name]').forEach((input) => {
			values[input.name] = input.checked ? input.value : "否";
		});
		form.querySelectorAll('input[type="file"][name]').forEach((input) => {
			const file = input.files && input.files[0];
			if (file) values[input.name] = file.name;
			else if (this.clearedFiles && this.clearedFiles.has(input.name)) values[input.name] = "";
			else values[input.name] = this.editing && this.editing[input.name] ? this.editing[input.name] : "";
		});
		delete values.scan_code;
		delete values.detail_input;
		delete values.quantity_input;
		delete values.barcode_input;
		delete values.flow_input;
		delete values.operation_input;
		return values;
	}

	editorSnapshot(form) {
		const values = this.readFormValues(form);
		values._id = form.dataset.recordId || "";
		values._details = Array.from(form.querySelectorAll(".jfe-detail-table tbody tr")).map((tr) => {
			const row = {};
			tr.querySelectorAll("[data-detail]").forEach((input) => row[input.dataset.detail] = input.value);
			return row;
		});
		this.captureEmployeeRows();
		values._employee_records = this.employeeDrafts || {};
		return values;
	}

	openLookup(title, field) {
		const form = this.host.querySelector(".jfe-editor-form");
		if (!form) return;
		const values = this.editorSnapshot(form);
		this.lookupContext = { title: title, field: field, values: values };
		const definitions = {
			"仓库词典": ["编号", "名称", "备注", "简拼"],
			"客户查找": ["编号", "名称", "电话", "等级", "简拼"],
			"产品类别词典": ["代码", "小类", "大类", "材质", "负责", "区域", "简拼"],
			"人事部门词典": ["代码", "职称", "部门", "负责", "备注", "简拼"],
			"销售部门词典": ["编号", "名称", "备注", "简拼"],
		};
		const columns = definitions[title] || ["编号", "名称", "备注"];
		this.host.querySelector("[data-modal-root]").innerHTML = '<div class="jfe-modal-backdrop" data-action="backdrop"><section class="jfe-lookup" role="dialog" aria-modal="true"><header><h2>' + this.esc(title) + '</h2><button type="button" class="jfe-icon-btn" data-action="lookup-cancel">×</button></header><div class="jfe-lookup-filter"><input data-dictionary-search placeholder="关键词"><button type="button" class="jfe-button primary" data-action="pick-lookup">使用关键词</button></div><div class="jfe-table-wrap"><table class="jfe-table"><thead><tr>' + columns.map((column) => "<th>" + this.esc(column) + "</th>").join("") + '</tr></thead><tbody><tr><td class="jfe-empty" colspan="' + columns.length + '">暂无本地词典数据</td></tr></tbody></table></div><footer><button type="button" class="jfe-button" data-action="lookup-cancel">关闭</button></footer></section></div>';
	}

	pickLookup() {
		if (!this.lookupContext) return;
		const search = this.host.querySelector("[data-dictionary-search]");
		const value = search ? search.value.trim() : "";
		if (!value) {
			this.toast("请输入关键词后使用。");
			return;
		}
		const context = this.lookupContext;
		context.values[context.field] = value;
		this.lookupContext = null;
		this.openEditor(context.values);
	}

	cancelLookup() {
		const context = this.lookupContext;
		this.lookupContext = null;
		if (context) this.openEditor(context.values);
	}

	renderField(field, record) {
		const value = record ? record[field.name] || "" : field.default || (field.name === "hire_date" ? this.today() : "");
		let control = "";
		if (field.type === "textarea") {
			control = '<textarea name="' + this.esc(field.name) + '" rows="2">' + this.esc(value) + "</textarea>";
		} else if (field.type === "select") {
			control = '<select name="' + this.esc(field.name) + '">' + (field.options || []).map((option) =>
				'<option value="' + this.esc(option) + '" ' + (value === option ? "selected" : "") + ">" + this.esc(option) + "</option>"
			).join("") + "</select>";
		} else if (field.type === "radios") {
			control = '<div class="jfe-radio-row">' + (field.options || []).map((option) =>
				'<label><input type="radio" name="' + this.esc(field.name) + '" value="' + this.esc(option) + '" ' + (value === option ? "checked" : "") + "> " + this.esc(option) + "</label>"
			).join("") + "</div>";
		} else if (field.type === "checkbox") {
			control = '<span class="jfe-check-field"><input type="checkbox" name="' + this.esc(field.name) + '" value="是" ' + (value === "是" || value === true ? "checked" : "") + "> 是</span>";
		} else if (field.type === "file") {
			control = '<input name="' + this.esc(field.name) + '" type="file" accept="image/*"><small class="jfe-file-name" data-file-name="' + this.esc(field.name) + '">' + this.esc(value || "未选择文件") + "</small>";
		} else {
			const type = field.type === "link" ? "text" : field.type || "text";
			control = '<div class="jfe-field-control"><input name="' + this.esc(field.name) + '" type="' + this.esc(type) + '" value="' + this.esc(value) + '" ' + (field.placeholder ? 'placeholder="' + this.esc(field.placeholder) + '"' : "") + ">";
			if (field.lookup) control += '<button type="button" class="jfe-button small" data-action="lookup" data-lookup="' + this.esc(field.lookup) + '" data-field="' + this.esc(field.name) + '">查找</button>';
			control += "</div>";
		}
		return '<div class="jfe-field ' + (field.full ? "jfe-field-full" : "") + '"><span>' + this.esc(field.label) + "</span>" + control + "</div>";
	}

	detailColumns(pageName) {
		if (pageName === "前台开单") return ["条码", "编号", "小类", "方式", "单位", "数量", "单价", "金额", "折扣", "折后", "折后价", "前次价", "库存", "原价", "新旧", "备注", "删除"];
		if (pageName === "生产指令单") return ["流程单", "客户", "编号", "工序", "日期", "交期", "离交期", "订单", "完成", "次数", "合计", "差数", "天数", "停审", "删除"];
		if (pageName === "订单录入") return ["编号", "图片", "客编号", "单位", "数量1", "单价1", "镀色1", "数量2", "单价2", "镀色2", "数量3", "单价3", "镀色3", "数量4", "单价4", "镀色4", "要求", "包装", "删除"];
		if (["物料", "材料"].includes(this.activeTab().module) && pageName === "申购") return ["编号", "名称", "规格", "单位", "数量", "单价", "金额", "备注", "删除"];
		if (pageName === "入库" && ["物料", "材料"].includes(this.activeTab().module)) return ["编号", "名称", "规格", "小类", "流程单", "单位", "数量", "单价", "金额", "重量", "总重量", "备注", "库存", "换算值", "扫码", "删除"];
		if (pageName === "销售入库" && this.activeTab().module === "销售") return ["条码", "流程单", "编号", "订单数", "已入", "小类", "单位", "数量", "标价", "金额", "折扣", "折后", "库存", "库位", "备注", "删除"];
		if (pageName === "外发加工" && this.activeTab().module === "外发") return ["流程单", "编号", "名称GX", "类别", "订单数", "单位", "数量", "工价", "钻数", "金额", "说明", "完成", "超数", "删除"];
		if (pageName === "报价单" && this.activeTab().module === "产品") return ["编号", "名称", "单位", "数量", "重量", "单价", "折前", "折扣", "金额", "备注", "删除"];
		if (["订单录入", "入库", "出库", "申购", "销售入库", "外发加工", "收回", "电镀单", "工资录入"].includes(pageName)) {
			return ["编号", "名称", "规格", "单位", "数量", "单价", "金额", "备注", "删除"];
		}
		return [];
	}

	emptyDetail() {
		return {};
	}

	columnsFor(moduleName, pageName) {
		if (moduleName === "模具" && pageName === "模具录入") return ["模具", "产品", "压模", "数量", "日期", "金额", "说明", "库位", "报废", "操作", "日期", "修改人", "修改日期", "修改", "删除"];
		if (moduleName === "产品" && pageName === "报价单") return ["审核", "单号", "客户", "日期", "数量", "折前", "金额", "实收", "优惠", "欠款", "备注", "时间", "修改人", "修改日期", "操作", "修改", "删除"];
		if (moduleName === "销售" && pageName === "销售入库") return ["审核", "单号", "部门", "类型", "客户", "状态", "说明", "方式", "数量", "款数", "金额", "付款", "优惠", "欠款", "操作", "修改", "删除"];
		if (moduleName === "外发" && pageName === "外发加工") return ["审核", "单号", "名称", "交期", "手机", "接货", "电话", "数量", "款数", "金额", "操作", "修改", "删除"];
		if (moduleName === "物料" || moduleName === "材料") {
			if (pageName === "申购") return ["审核", "单号", "仓库", "品牌", "公司", "联系", "采购", "手机", "地址", "数量", "重量", "金额", "操作", "修改", "删除"];
		}
		if (pageName === "计划图片") return ["流程单", "客户", "编号", "名称", "工序", "数量", "完成", "交期", "状态", "操作"];
		if (pageName === "生产指令单") return ["审核", "单号", "日期", "数量", "金额", "指令日期", "款数", "备注", "时间", "修改人", "修改日期", "操作", "修改", "删除"];
		if (pageName === "前台开单") return ["日期", "单号", "客户", "部门", "导购", "数量", "金额", "实收", "欠款", "状态", "操作"];
		if (pageName === "销售单查询") return ["审核", "日期", "客户", "部门", "单号", "导购", "数量", "折前", "金额", "方式", "实收", "优惠", "欠款", "备注", "操作", "时间", "修改时间", "分享", "表格", "带图片", "下载", "修改"];
		if (pageName === "操作日志") return ["日期", "操作人", "模块", "操作内容", "单号", "备注"];
		if (pageName === "收款凭证") return ["审核选择框", "审核", "单号", "店面", "账号", "名称", "方式", "金额", "摘要", "时间", "修改人", "修改日期", "操作", "修改", "删除"];
		if (pageName === "付款凭证" || pageName === "费用登记") return ["审核选择框", "审核", "单号", "店面", "账号", "名称", "方式", "金额", "摘要", "时间", "修改人", "修改日期", "操作", "修改", "删除"];
		if (pageName === "物料类别" || pageName === "材料类别") return [pageName.replace("类别", "类别编号"), "类别名称", "简拼", "备注", "修改"];
		if (pageName.includes("库存") || pageName.includes("盘点")) return ["编号", "名称", "规格", "仓库", "库位", "单位", "库存数量", "盘点数量", "差异", "备注", "操作"];
		if (moduleName === "人事") return ["员工编号", "姓名", "部门", "岗位", "联系电话", "状态", "操作"];
		if (moduleName === "财务") return ["审核", "日期", "单号", "科目", "往来单位", "摘要", "金额", "经办人", "操作"];
		if (pageName.includes("报表") || pageName.includes("分析") || pageName.includes("排行") || pageName.includes("同比")) return ["日期", "编号", "名称", "类别", "数量", "金额", "备注"];
		if (moduleName === "订单") return ["审核", "日期", "单号", "客户", "类型", "款数", "数量", "金额", "交期", "备注", "操作"];
		if (moduleName === "生产") return ["审核", "日期", "单号", "部门", "工序", "数量", "完成", "交期", "状态", "操作"];
		if (moduleName === "工资") return ["月份", "员工编号", "姓名", "工序", "数量", "工价", "金额", "状态", "操作"];
		if (moduleName === "发镀" || moduleName === "外发") return ["审核", "日期", "单号", "工厂", "工序", "款数", "数量", "金额", "状态", "操作"];
		if (moduleName === "销售") return ["日期", "编号", "名称", "规格", "数量", "单价", "金额", "库存", "状态", "操作"];
		return ["编号", "名称", "类别", "规格", "单位", "仓库", "库存数量", "状态", "备注", "操作"];
	}

	isReport(pageName) {
		return ["查询", "报表", "明细", "分析", "排行", "汇总", "浏览", "库存", "历史", "跟踪", "同比", "对账", "预警"].some((word) => pageName.includes(word));
	}

	isSalesReport(pageName) {
		return pageName === "销售单查询";
	}

	getRows(pageName, moduleName) {
		try {
			const parsed = JSON.parse(localStorage.getItem(this.storageKey(pageName, moduleName)) || "[]");
			return Array.isArray(parsed) ? parsed : [];
		} catch (error) {
			return [];
		}
	}

	saveRows(pageName, rows, moduleName) {
		try {
			localStorage.setItem(this.storageKey(pageName, moduleName), JSON.stringify(rows));
			return true;
		} catch (error) {
			this.toast("浏览器本地存储不可用。");
			return false;
		}
	}

	storageKey(pageName, moduleName) {
		return "jifeng-legacy:" + (moduleName || this.activeTab().module || "") + ":" + pageName;
	}

	rowKeyMap(row) {
		return {
			"编号": row.code || row.number || row.employee_no || "",
			"类别": row.category || row.type || "",
			"类别名称": row.name || "",
			"名称": row.name || row.customer || "",
			"规格": row.specification || "",
			"单位": row.unit || "",
			"仓库": row.warehouse || "",
			"库位": row.location || "",
			"库存数量": row.quantity || "",
			"状态": row._status || row.status || "",
			"日期": row.date || "",
			"单号": row.number || "",
			"客户": row.customer || "",
			"部门": row.department || "",
			"导购": row.salesperson || "",
			"金额": row.amount || "",
			"交期": row.delivery_date || "",
			"备注": row.remark || "",
			"员工编号": row.employee_no || "",
			"姓名": row.name || "",
			"岗位": row.position || "",
			"联系电话": row.phone || "",
			"操作人": row.operator || "",
			"操作内容": row.action || "",
			"模具": row.mold_number || row.number || row.code || "",
			"产品": row.product_number || row.product || "",
			"压模": row.molder || "",
			"说明": row.remark || "",
			"公司": row.company || "",
			"联系": row.contact || "",
			"采购": row.buyer || "",
			"地址": row.address || "",
			"手机": row.phone || "",
			"接货": row.receiver || "",
			"交期": row.delivery_date || "",
			"款数": row.styles || "",
			"折前": row.gross || "",
			"实收": row.payment || "",
			"优惠": row.discount || "",
			"欠款": row.arrears || "",
			"重量": row.weight || "",
			"流程单": row.flow || "",
			"订单数": row.order_quantity || "",
			"工序": row.operation || row.process || "",
			"工价": row.unit_price || "",
			"创建日期": row.date || "",
			"修改日期": row.modified || "",
			"修改人": row.modified_by || row.owner || "",
			"报废": row.scrapped || "",
		};
	}

	bindEvents() {
		this.host.onclick = (event) => {
			if (event.target.matches("[data-row-select]")) {
				event.stopPropagation();
				return;
			}
			const button = event.target.closest("[data-action]");
			if (!button || !this.host.contains(button)) return;
			if (button.dataset.action === "backdrop" && event.target !== button) return;
			event.preventDefault();
			const action = button.dataset.action;
			if (action === "module") {
				if (button.dataset.name === "权限") {
					this.addTab({ key: "module:权限", title: "权限", kind: "module", module: "权限" });
				} else {
					this.openModule(button.dataset.name);
				}
			} else if (action === "open-page") this.openPage(button.dataset.page, button.dataset.module);
			else if (action === "dashboard-refresh") {
				this.loadDashboard({
					company: this.host.querySelector("[data-dashboard-company]")?.value,
					from_date: this.host.querySelector("[data-dashboard-from]")?.value,
					to_date: this.host.querySelector("[data-dashboard-to]")?.value,
				});
			}
			else if (action === "dashboard-list") {
				const state = this.dashboard;
				const filters = { company: state.company, docstatus: 1 };
				if (button.dataset.metric === "open_work_orders") {
					filters.status = ["not in", ["Completed", "Stopped", "Closed"]];
				} else {
					filters.posting_date = ["between", [state.from_date, state.to_date]];
				}
				frappe.route_options = filters;
				frappe.set_route("List", button.dataset.doctype);
			}
			else if (action === "tab") { this.activeKey = button.dataset.key; this.render(); }
			else if (action === "close-tab") { event.stopPropagation(); this.closeTab(button.dataset.key); }
			else if (action === "fullscreen") this.fullscreen();
			else if (action === "exit-fullscreen") this.exitFullscreen();
			else if (action === "user") this.toast("经典工作台的编辑内容保存在当前浏览器。");
			else if (action === "history") this.toast("最近访问：" + this.tabs.slice(-4).map((tab) => tab.title).join("、"));
			else if (action === "tab-menu") this.toast("已打开标签：" + this.tabs.map((tab) => tab.title).join("、"));
			else if (action === "upload-image" || action === "find-similar") this.pickImage(action);
			else if (action === "quick-inventory") this.openPage("库存", "销售");
			else if (action === "quick-order") this.openPage("订单查询", "订单");
			else if (action === "quick-location") this.openPage("库位库存", "销售");
			else if (action === "contact") this.showInfo("咨询详情", "请联系集沣系统管理员。");
			else if (action === "new") this.openEditor(null);
			else if (action === "lookup") this.openLookup(button.dataset.lookup, button.dataset.field);
			else if (action === "pick-lookup") this.pickLookup();
			else if (action === "lookup-cancel") this.cancelLookup();
			else if (action === "pick-draft") this.pickDraft(button.dataset.id);
			else if (action === "cancel-draft-picker") this.finishInfoModal();
			else if (action === "employee-subtab") this.setEmployeeSubtab(button.dataset.name);
			else if (action === "employee-add") this.addEmployeeRow();
			else if (action === "employee-remove") this.removeEmployeeRow(button.dataset.index);
			else if (action === "payroll-add") this.host.querySelector(".jfe-wage-table tbody").insertAdjacentHTML("beforeend", this.renderPayrollRow(["日期", "姓名", "工序", "流程单", "小类", "编号", "订单数", "数量", "工价", "钻数", "附加费", "计件金额", "小时", "单价", "计时金额", "备注", "完成数", "超数", "前次价", "删除"]));
			else if (action === "remove-wage-row") button.closest("tr").remove();
			else if (action === "payroll-save") this.savePayrollEntry();
			else if (action === "clear-product-image" || action === "clear-patent-image" || action === "clear-file") this.clearImage(action, button.dataset.field);
			else if (action === "edit-row") { event.stopPropagation(); this.openEditor(this.getRows(this.activeTab().page).find((row) => row._id === button.dataset.id)); }
			else if (action === "review-one") { event.stopPropagation(); this.changeRowStatus(button.dataset.id); }
			else if (action === "share-row") this.showInfo("分享", "此条记录仅保存在当前浏览器，没有服务器链接可分享。");
			else if (action === "image-row") this.showInfo("图文预览", "当前记录没有关联图片。");
			else if (action === "download-row") this.exportRows();
			else if (action === "delete-row") { event.stopPropagation(); this.deleteRecord(button.dataset.id); }
			else if (action === "delete-current") this.deleteCurrent();
			else if (action === "save-form") this.saveForm();
			else if (action === "review-form") this.saveForm("已审核");
			else if (action === "close-modal" || action === "backdrop") this.closeModal(event);
			else if (action === "add-detail") this.addDetailRow();
			else if (action === "inbound-add") this.addInboundDetail();
			else if (action === "stockin-add") this.addSalesInboundDetail();
			else if (action === "external-add") this.addExternalDetail();
			else if (action === "remove-detail") { button.closest("tr").remove(); this.updateSalesTotals(); }
			else if (action === "query") {
				this.search = this.host.querySelector("[data-search]")?.value || "";
				this.dateFrom = this.host.querySelector('[data-filter="from"]')?.value || "";
				this.dateTo = this.host.querySelector('[data-filter="to"]')?.value || "";
				this.onlyDate = this.host.querySelector("[data-only-date]")?.checked || false;
				this.deliveryFilter = this.host.querySelector("[data-delivery-filter]")?.checked || false;
				const status = this.host.querySelector("[data-approval-filter]:checked");
				if (status) this.approvalFilter = status.value;
				this.pageNo = 1;
				this.render();
			}
			else if (action === "prev-page") { this.pageNo = Math.max(1, this.pageNo - 1); this.render(); }
			else if (action === "next-page") { this.pageNo += 1; this.render(); }
			else if (action === "export") this.exportRows();
			else if (action === "import") this.importRows();
			else if (action === "save-grid") this.toast("表格内容已保存到当前浏览器草稿。");
			else if (action === "pinyin") this.toast("已更新当前浏览器草稿中的简拼字段。");
			else if (action === "refresh") this.toast("日志已更新。");
			else if (action === "format") this.toast("已切换至格式1。");
			else if (action === "print-preview") this.printPreview();
			else if (action === "mold-view") { this.moldView = button.dataset.view; this.pageNo = 1; this.render(); }
			else if (action === "all-plans") { this.planStatus = "全部"; this.pageNo = 1; this.render(); }
			else if (action === "review" || action === "unreview") this.changeSelectedStatus(action === "review" ? "已审核" : "未审核");
			else if (action === "track") this.toast("跟踪记录将在有本地草稿时显示。");
			else if (action === "change-customer" || action === "load-transfer" || action === "load-history" || action === "load-pos" || action === "load-order") this.toolbarAction(action);
			else if (action === "sales-add") this.addSalesDetail();
			else if (action === "sales-return" || action === "discount" || action === "collect" || action === "hold" || action === "restore") {
				if (action === "hold") this.saveForm("暂存");
				else if (action === "restore") this.restoreDraft();
				else this.toolbarAction(action);
			}
			else if (action === "stock-in" || action === "transfer" || action === "calculate" || action === "schedule" || action === "customer" || action === "select" || action === "more" || action === "purchase") this.toolbarAction(action);
			else if (action === "external-info") this.showInfo("外部工具", "原系统的插件与远程协助入口会跳转到外部安装包。本工作台不下载或安装程序。");
			else if (action === "notice") this.showInfo("用户须知", "请妥善保管账号和密码；业务操作前确认日期、单号及明细。");
			else if (action === "renewal") this.showInfo("续费信息", "请联系系统管理员查询授权与续费状态。");
		};
		this.host.onsubmit = (event) => event.preventDefault();
		this.host.onkeydown = (event) => {
			if (event.key === "Enter" && event.target.matches('[name="scan_code"]')) {
				event.preventDefault();
				this.addSalesDetail();
			}
			if (event.key === "Enter" && event.target.matches('[name="barcode_input"]')) { event.preventDefault(); this.addSalesInboundDetail(); }
			if (event.key === "Enter" && event.target.matches('[name="flow_input"]')) { event.preventDefault(); this.addExternalDetail(); }
		};
		this.host.oninput = (event) => {
			if (event.target.matches("[data-search]")) this.search = event.target.value;
			if (event.target.matches("[data-wage-column]")) this.updatePayrollTotals();
			if (event.target.matches("[data-detail], [name=freight], [name=arrears], [name=quantity], [name=amount], [name=payment], [name=discount]")) this.updateSalesTotals();
		};
		this.host.onchange = (event) => {
			if (event.target.matches('input[type="file"][name]')) {
				const label = this.host.querySelector('[data-file-name="' + event.target.name + '"]');
				if (label) label.textContent = event.target.files && event.target.files[0] ? event.target.files[0].name : "未选择文件";
				if (this.clearedFiles) this.clearedFiles.delete(event.target.name);
			}
			if (event.target.matches("[data-approval-filter]")) {
				this.approvalFilter = event.target.value;
				this.render();
			}
			if (event.target.matches("[data-plan-status]")) { this.planStatus = event.target.value; this.pageNo = 1; this.render(); }
			if (event.target.matches("[data-mold-preview]")) { this.moldPreview = event.target.checked; this.render(); }
			if (event.target.matches(".jfe-pager select")) {
				this.pageSize = Number(event.target.value) || 15;
				this.pageNo = 1;
				this.render();
			}
		};
		this.host.ondblclick = (event) => {
			if (event.target.matches("[data-payroll-name]")) {
				const multi = this.host.querySelector('[data-payroll="multi"]')?.checked;
				this.toast(multi ? "多人操作：已选择一个工序多人操作。" : "双击姓名：计时操作。");
			}
			if (event.target.matches("[data-payroll-operation]")) {
				const multi = this.host.querySelector('[data-payroll="multi-operation"]')?.checked;
				this.toast(multi ? "多工序操作：已选择多工序一个人操作。" : "双击工序：打开工序操作。");
			}
		};
	}

	updatePayrollTotals() {
		const rows = Array.from(this.host.querySelectorAll("[data-wage-row]"));
		const sum = (field) => rows.reduce((total, row) => total + (Number(row.querySelector('[data-wage-column="' + field + '"]')?.value) || 0), 0);
		const target = this.host.querySelector(".jfe-payroll-total");
		if (target) target.innerHTML = "<b>合计数量：" + sum("数量") + "</b><b>计件金额：" + sum("计件金额").toFixed(2) + "</b><b>计时金额：" + sum("计时金额").toFixed(2) + "</b>";
	}

	savePayrollEntry() {
		const records = Array.from(this.host.querySelectorAll("[data-wage-row]")).map((tr) => {
			const row = {};
			tr.querySelectorAll("[data-wage-column]").forEach((input) => row[input.dataset.wageColumn] = input.value);
			return row;
		}).filter((row) => Object.values(row).some(Boolean));
		if (!records.length) {
			this.toast("请先录入工资明细。");
			return;
		}
		const get = (name) => this.host.querySelector('[data-payroll="' + name + '"]')?.value || "";
		const header = {
			_id: "local-" + Date.now(),
			date: get("date"),
			name: get("employee"),
			operation: get("operation"),
			flow: get("flow"),
			quantity: records.reduce((sum, row) => sum + (Number(row["数量"]) || 0), 0),
			amount: records.reduce((sum, row) => sum + (Number(row["计件金额"]) || 0) + (Number(row["计时金额"]) || 0), 0),
			_status: "草稿",
			_details: records,
		};
		const saved = this.getRows("工资录入");
		saved.unshift(header);
		if (!this.saveRows("工资录入", saved)) return;
		this.toast("工资草稿已保存在当前浏览器。");
	}

	clearImage(action, fieldName) {
		const fileName = fieldName || (action === "clear-product-image" ? "product_image" : "patent_image");
		const input = this.host.querySelector('[name="' + fileName + '"]');
		if (input) input.value = "";
		this.clearedFiles = this.clearedFiles || new Set();
		this.clearedFiles.add(fileName);
		const label = this.host.querySelector('[data-file-name="' + fileName + '"]');
		if (label) label.textContent = "未选择文件";
		this.toast("已清空当前图片选择。");
	}

	refreshContent() {
		const active = this.activeTab();
		const content = this.host.querySelector(".jfe-content");
		if (content) content.innerHTML = this.renderActive(active);
	}

	openEditor(record) {
		this.editing = record || null;
		this.infoFormContext = null;
		this.clearedFiles = new Set();
		this.employeeDrafts = record && record._employee_records ? JSON.parse(JSON.stringify(record._employee_records)) : {};
		this.activeEmployeeTab = "工作经历";
		const root = this.host.querySelector("[data-modal-root]");
		root.innerHTML = this.renderEditor(record);
		this.updateSalesTotals();
	}

	closeModal(event) {
		if (event.target.classList.contains("jfe-modal-backdrop")) {
			if (this.lookupContext) {
				this.cancelLookup();
				return;
			}
			this.finishInfoModal();
			return;
		}
		if (event.target.dataset.action === "close-modal") this.finishInfoModal();
	}

	finishInfoModal() {
		const root = this.host.querySelector("[data-modal-root]");
		root.innerHTML = "";
		this.pickerRows = null;
		this.pickerContext = null;
		this.pickerSource = null;
		if (this.infoFormContext) {
			const context = this.infoFormContext;
			this.infoFormContext = null;
			this.openEditor(context);
		}
	}

	addDetailRow() {
		const body = this.host.querySelector(".jfe-detail-table tbody");
		if (!body) return;
		const pageName = this.activeTab().page;
		const columns = this.detailColumns(pageName);
		const hasDelete = columns.includes("删除");
		body.insertAdjacentHTML("beforeend", "<tr>" + columns.map((col) => col === "删除" ? '<td><button type="button" data-action="remove-detail">删除</button></td>' :
			'<td><input data-detail="' + this.esc(col) + '" placeholder="' + this.esc(col) + '"></td>'
		).join("") + (hasDelete ? "" : '<td><button type="button" data-action="remove-detail">删除</button></td>') + "</tr>");
		this.updateSalesTotals();
	}

	addSalesDetail() {
		const scan = this.host.querySelector('[name="scan_code"]');
		const code = scan ? scan.value.trim() : "";
		this.addDetailRow();
		const row = this.host.querySelector(".jfe-detail-table tbody tr:last-child");
		const barcode = row && row.querySelector('[data-detail="条码"]');
		if (barcode && code) barcode.value = code;
		if (scan) scan.value = "";
		this.updateSalesTotals();
	}

	addInboundDetail() {
		const codeInput = this.host.querySelector('[name="detail_input"]');
		const quantityInput = this.host.querySelector('[name="quantity_input"]');
		const code = codeInput ? codeInput.value.trim() : "";
		const quantity = quantityInput ? quantityInput.value : "";
		this.addDetailRow();
		const row = this.host.querySelector(".jfe-detail-table tbody tr:last-child");
		if (row && code) {
			const number = row.querySelector('[data-detail="编号"]');
			if (number) number.value = code;
		}
		if (row && quantity) {
			const amount = row.querySelector('[data-detail="数量"]');
			if (amount) amount.value = quantity;
		}
		if (codeInput) codeInput.value = "";
		if (quantityInput) quantityInput.value = "";
	}

	addSalesInboundDetail() {
		const codeInput = this.host.querySelector('[name="barcode_input"]');
		const quantityInput = this.host.querySelector('[name="quantity_input"]');
		const code = codeInput ? codeInput.value.trim() : "";
		const quantity = quantityInput ? quantityInput.value : "";
		this.addDetailRow();
		const row = this.host.querySelector(".jfe-detail-table tbody tr:last-child");
		if (row && code) {
			const barcode = row.querySelector('[data-detail="条码"]');
			const number = row.querySelector('[data-detail="编号"]');
			if (barcode) barcode.value = code;
			if (number) number.value = code;
		}
		if (row && quantity) {
			const amount = row.querySelector('[data-detail="数量"]');
			if (amount) amount.value = quantity;
		}
		if (codeInput) codeInput.value = "";
		if (quantityInput) quantityInput.value = "";
		this.updateSalesTotals();
	}

	addExternalDetail() {
		const flow = this.host.querySelector('[name="flow_input"]');
		const quantity = this.host.querySelector('[name="quantity_input"]');
		const operation = this.host.querySelector('[name="operation_input"]');
		const flowValue = flow ? flow.value.trim() : "";
		const quantityValue = quantity ? quantity.value : "";
		const operationValue = operation ? operation.value : "";
		this.addDetailRow();
		const row = this.host.querySelector(".jfe-detail-table tbody tr:last-child");
		const setValue = (column, value) => { const input = row && row.querySelector('[data-detail="' + column + '"]'); if (input) input.value = value; };
		setValue("流程单", flowValue);
		setValue("数量", quantityValue);
		setValue("名称GX", operationValue);
		if (flow) flow.value = "";
		if (quantity) quantity.value = "";
		this.updateSalesTotals();
	}

	updateSalesTotals() {
		const rows = Array.from(this.host.querySelectorAll(".jfe-detail-table tbody tr"));
		const number = (row, column) => Number(row.querySelector('[data-detail="' + column + '"]')?.value) || 0;
		const hasDetails = rows.some((row) => Array.from(row.querySelectorAll("[data-detail]")).some((input) => input.value));
		const total = (column) => rows.reduce((sum, row) => sum + number(row, column), 0);
		const quantity = hasDetails ? total("数量") : Number(this.host.querySelector('[name="quantity"]')?.value) || 0;
		const gross = hasDetails ? rows.reduce((sum, row) => sum + number(row, "数量") * number(row, "单价"), 0) : Number(this.host.querySelector('[name="gross"]')?.value) || Number(this.host.querySelector('[name="amount"]')?.value) || 0;
		const amount = hasDetails ? rows.reduce((sum, row) => sum + (number(row, "折后") || number(row, "金额")), 0) : Number(this.host.querySelector('[name="amount"]')?.value) || 0;
		const styles = hasDetails ? rows.filter((row) => row.querySelector('[data-detail="编号"]')?.value || row.querySelector('[data-detail="条码"]')?.value).length : Number(this.host.querySelector('[name="styles"]')?.value) || 0;
		const values = { quantity: quantity, styles: styles, gross: gross.toFixed(2), amount: amount.toFixed(2) };
		this.host.querySelectorAll("[data-pos-total]").forEach((node) => node.textContent = values[node.dataset.posTotal]);
		const weight = hasDetails ? total("重量") : Number(this.host.querySelector('[name="weight"]')?.value) || 0;
		const quoteTotals = { quantity: quantity, weight: weight, amount: amount, payment: Number(this.host.querySelector('[name="payment"]')?.value) || 0, discount: Number(this.host.querySelector('[name="discount"]')?.value) || 0, arrears: Number(this.host.querySelector('[name="arrears"]')?.value) || 0 };
		this.host.querySelectorAll("[data-quote-total]").forEach((node) => node.textContent = Number(quoteTotals[node.dataset.quoteTotal]).toFixed(2));
		const payment = Number(this.host.querySelector('[name="payment"]')?.value) || 0;
		const discount = Number(this.host.querySelector('[name="discount"]')?.value) || 0;
		const arrears = Number(this.host.querySelector('[name="arrears"]')?.value) || 0;
		const stockTotals = { quantity: quantity, styles: styles, amount: amount, payment: payment, discount: discount, arrears: arrears };
		this.host.querySelectorAll("[data-stockin-total]").forEach((node) => node.textContent = ["quantity", "styles"].includes(node.dataset.stockinTotal) ? stockTotals[node.dataset.stockinTotal] : stockTotals[node.dataset.stockinTotal].toFixed(2));
		const externalTotals = { quantity: quantity, styles: styles, amount: amount.toFixed(2) };
		this.host.querySelectorAll("[data-external-total]").forEach((node) => node.textContent = externalTotals[node.dataset.externalTotal]);
	}

	saveForm(status) {
		const form = this.host.querySelector(".jfe-editor-form");
		if (!form) return;
		const active = this.activeTab();
		const data = this.readFormValues(form);
		const detailRows = Array.from(form.querySelectorAll(".jfe-detail-table tbody tr")).map((tr) => {
			const row = {};
			tr.querySelectorAll("[data-detail]").forEach((input) => row[input.dataset.detail] = input.value);
			return row;
		}).filter((row) => Object.values(row).some(Boolean));
		const current = this.getRows(active.page);
		const id = form.dataset.recordId || ("local-" + Date.now());
		const existing = current.findIndex((row) => row._id === id);
		const row = Object.assign({}, existing >= 0 ? current[existing] : {}, data, {
			_id: id,
			_status: status || (existing >= 0 ? current[existing]._status : "草稿"),
			_details: detailRows,
		});
		this.captureEmployeeRows();
		row._employee_records = this.employeeDrafts || {};
		if (existing >= 0) current[existing] = row;
		else current.unshift(row);
		if (!this.saveRows(active.page, current)) return;
		this.host.querySelector("[data-modal-root]").innerHTML = "";
		this.toast(status ? "已在本地草稿中标记为“" + status + "”。" : "已保存到当前浏览器草稿。");
		this.render();
	}

	deleteRecord(id) {
		if (!window.confirm("删除当前浏览器中的这条工作台草稿？")) return;
		const active = this.activeTab();
		this.saveRows(active.page, this.getRows(active.page).filter((row) => row._id !== id));
		this.toast("草稿已删除。");
		this.render();
	}

	deleteCurrent() {
		const form = this.host.querySelector(".jfe-editor-form");
		if (!form || !form.dataset.recordId) {
			this.toast("新建草稿尚未保存。");
			return;
		}
		this.deleteRecord(form.dataset.recordId);
		this.host.querySelector("[data-modal-root]").innerHTML = "";
	}

	changeSelectedStatus(status) {
		const rows = this.getRows(this.activeTab().page);
		if (!rows.length) {
			this.toast("当前没有可操作的本地草稿。");
			return;
		}
		const selected = new Set(Array.from(this.host.querySelectorAll("[data-row-select]:checked"), (input) => input.dataset.rowSelect));
		if (!selected.size) {
			this.toast("请先勾选要处理的单据。");
			return;
		}
		if (!window.confirm("将选中的 " + selected.size + " 条草稿标记为“" + status + "”？此操作只影响本地预览。")) return;
		if (!this.saveRows(this.activeTab().page, rows.map((row) => selected.has(row._id) ? Object.assign({}, row, { _status: status }) : row))) return;
		this.toast("已更新本地草稿状态。");
		this.render();
	}

	changeRowStatus(id) {
		const rows = this.getRows(this.activeTab().page);
		const current = rows.find((row) => row._id === id);
		if (!current) return;
		const status = current._status === "已审核" || current._status === "已审" ? "未审核" : "已审核";
		if (!window.confirm("将此单据标记为“" + status + "”？此操作只影响本地预览。")) return;
		if (!this.saveRows(this.activeTab().page, rows.map((row) => row._id === id ? Object.assign({}, row, { _status: status }) : row))) return;
		this.toast("已更新本地草稿状态。");
		this.render();
	}

	toolbarAction(action) {
		if (action === "customer" && this.activeTab().page === "前台开单") {
			this.openLookup("客户查找", "customer");
			return;
		}
		if (action === "schedule") return this.showDraftPicker("选择计划", "计划");
		if (action === "load-transfer") return this.showDraftPicker("选择调拨单", "销售调拨");
		if (action === "load-pos") return this.showDraftPicker("选择前台单据", "前台开单", "销售");
		if (action === "load-order") return this.showDraftPicker("选择订单", "订单录入", "订单");
		if (action === "select" || action === "load-history") return this.showDraftPicker("选择历史单据", this.activeTab().page);
		const messages = {
			"stock-in": "已打开入库操作预览。",
			transfer: "已打开调拨操作预览。",
			calculate: "已完成本地预览计算。",
			schedule: "请选择本地草稿中的计划明细。",
			customer: "客户档案入口已打开。",
			select: "可从当前浏览器本地草稿选择历史记录。",
			more: "更多页面设置。",
			"change-customer": "请在保存前确认客户信息。",
			"load-transfer": "已打开调拨单选择预览。",
			"load-history": "已打开历史单据选择预览。",
			"sales-return": "已切换至退货操作预览。",
			discount: "已打开折扣操作预览。",
			collect: "已打开收款操作预览。",
		};
		this.showInfo("操作", messages[action] || "操作已打开。");
	}

	showDraftPicker(title, sourcePage, sourceModule) {
		const rows = this.getRows(sourcePage, sourceModule);
		if (!rows.length) {
			this.showInfo(title, "当前浏览器中没有可选的本地草稿。");
			return;
		}
		const form = this.host.querySelector(".jfe-editor-form");
		this.pickerContext = form ? this.editorSnapshot(form) : null;
		this.infoFormContext = this.pickerContext;
		this.pickerSource = { page: sourcePage, module: sourceModule || this.activeTab().module };
		this.pickerRows = rows;
		const columns = ["单号", "日期", "客户", "名称", "数量", "金额"];
		this.host.querySelector("[data-modal-root]").innerHTML = '<div class="jfe-modal-backdrop" data-action="backdrop"><section class="jfe-lookup" role="dialog" aria-modal="true"><header><h2>' + this.esc(title) + '</h2><button type="button" class="jfe-icon-btn" data-action="cancel-draft-picker">×</button></header><div class="jfe-table-wrap"><table class="jfe-table"><thead><tr>' + columns.map((column) => "<th>" + this.esc(column) + "</th>").join("") + '<th>操作</th></tr></thead><tbody>' + rows.map((row) => {
			const values = this.rowKeyMap(row);
			return '<tr>' + columns.map((column) => "<td>" + this.esc(values[column] || row[column] || "") + "</td>").join("") + '<td><button type="button" data-action="pick-draft" data-id="' + this.esc(row._id) + '">带入</button></td></tr>';
		}).join("") + '</tbody></table></div><footer><button type="button" class="jfe-button" data-action="cancel-draft-picker">关闭</button></footer></section></div>';
	}

	pickDraft(id) {
		const selected = (this.pickerRows || []).find((row) => row._id === id);
		if (!selected) return;
		const current = this.pickerContext;
		const source = this.pickerSource || {};
		this.pickerRows = null;
		this.pickerContext = null;
		this.pickerSource = null;
		this.infoFormContext = null;
		if (!current) {
			this.openEditor(selected);
			return;
		}
		const samePage = source.page === this.activeTab().page && source.module === this.activeTab().module;
		const importedDetails = (selected._details || []).map((detail) => source.page === "订单录入" && this.activeTab().page === "外发加工" ? {
			"流程单": selected.number || detail["流程单"] || "", "编号": detail["编号"] || "", "名称GX": detail["名称GX"] || detail["名称"] || "",
			"类别": detail["类别"] || "", "订单数": detail["数量1"] || detail["数量"] || "", "单位": detail["单位"] || "",
			"数量": detail["数量1"] || detail["数量"] || "", "工价": detail["单价1"] || "", "钻数": detail["钻数"] || "",
			"金额": detail["金额"] || "", "说明": detail["要求"] || "", "完成": "", "超数": "",
		} : Object.assign({}, detail));
		const record = samePage ? Object.assign({}, current, selected, {
			_id: current._id || "",
			_details: selected._details || [],
			_employee_records: selected._employee_records || current._employee_records || {},
		}) : Object.assign({}, current, {
			_id: current._id || "",
			_details: importedDetails,
		});
		this.openEditor(record);
	}

	restoreDraft() {
		const rows = this.getRows(this.activeTab().page);
		if (!rows.length) {
			this.toast("当前没有可恢复的本地草稿。");
			return;
		}
		this.openEditor(rows[0]);
	}

	pickImage(action) {
		const input = document.createElement("input");
		input.type = "file";
		input.accept = "image/*";
		input.onchange = () => {
			if (input.files && input.files.length) this.toast((action === "find-similar" ? "已选择图片，可用于相似款检索：" : "已选择图片：") + input.files[0].name);
		};
		input.click();
	}

	importRows() {
		const input = document.createElement("input");
		input.type = "file";
		input.accept = ".csv,text/csv";
		input.onchange = () => {
			const file = input.files && input.files[0];
			if (!file) return;
			const reader = new FileReader();
			reader.onload = () => {
				const table = this.parseCsv(reader.result || "");
				const header = (table.shift() || []).map((item) => item.trim());
				const imported = table.filter((values) => values.some((value) => value.trim())).map((values) => {
					const row = { _id: "local-" + Date.now() + "-" + Math.random().toString(16).slice(2), _status: "草稿" };
					header.forEach((key, index) => {
						const field = this.csvField(key);
						if (field) row[field] = (values[index] || "").trim();
					});
					return row;
				});
				this.saveRows(this.activeTab().page, this.getRows(this.activeTab().page).concat(imported));
				this.toast("导入 " + imported.length + " 条到当前浏览器草稿。");
				this.render();
			};
			reader.readAsText(file, "utf-8");
		};
		input.click();
	}

	parseCsv(source) {
		const text = String(source).replace(/^\uFEFF/, "");
		const rows = [];
		let row = [];
		let cell = "";
		let quoted = false;
		for (let index = 0; index < text.length; index += 1) {
			const char = text[index];
			if (char === '"') {
				if (quoted && text[index + 1] === '"') {
					cell += '"';
					index += 1;
				} else quoted = !quoted;
			} else if (char === "," && !quoted) {
				row.push(cell);
				cell = "";
			} else if ((char === "\n" || char === "\r") && !quoted) {
				if (char === "\r" && text[index + 1] === "\n") index += 1;
				row.push(cell);
				if (row.some((value) => value.length)) rows.push(row);
				row = [];
				cell = "";
			} else cell += char;
		}
		if (cell.length || row.length) {
			row.push(cell);
			rows.push(row);
		}
		return rows;
	}

	csvField(column) {
		const fields = {
			"单号": "number", "日期": "date", "指令日期": "instruction_date", "客户": "customer",
			"部门": "department", "导购": "salesperson", "数量": "quantity", "金额": "amount",
			"重量": "weight", "备注": "remark", "名称": "name", "规格": "specification",
			"单位": "unit", "仓库": "warehouse", "库位": "location", "员工编号": "employee_no",
			"姓名": "name", "岗位": "position", "联系电话": "phone", "类别": "category",
			"类别名称": "name", "状态": "status", "交期": "delivery_date", "工序": "operation",
			"审核": "_status", "摘要": "summary", "店面": "store", "方式": "method",
			"模具": "mold_number", "产品": "product_number", "压模": "molder", "说明": "remark",
			"公司": "company", "联系": "contact", "采购": "buyer", "地址": "address", "手机": "phone",
			"接货": "receiver", "款数": "styles", "折前": "gross", "实收": "payment", "优惠": "discount",
			"欠款": "arrears", "重量": "weight", "修改人": "modified_by", "修改日期": "modified",
		};
		if (column === "审核选择框" || ["操作", "修改", "删除", "分享", "表格", "带图片", "下载"].includes(column)) return "";
		if (column === "编号") return ["物料", "材料", "产品"].includes(this.activeTab().module) ? "code" : "number";
		return fields[column] || column;
	}

	exportRows() {
		const rows = this.getRows(this.activeTab().page);
		const columns = this.columnsFor(this.activeTab().module, this.activeTab().page).filter((column) => !["审核选择框", "操作", "修改", "删除", "分享", "表格", "带图片", "下载"].includes(column));
		const escapeCell = (value) => '"' + String(value == null ? "" : value).replace(/"/g, '""') + '"';
		const content = [columns.map(escapeCell).join(","), ...rows.map((row) => {
			const map = this.rowKeyMap(row);
			return columns.map((column) => escapeCell(map[column] || row[column] || "")).join(",");
		})].join("\r\n");
		const blob = new Blob(["\ufeff" + content], { type: "text/csv;charset=utf-8" });
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = url;
		link.download = this.activeTab().page + ".csv";
		link.click();
		URL.revokeObjectURL(url);
		this.toast("已导出当前列表。");
	}

	printPreview() {
		const active = this.activeTab();
		this.showInfo("打印预览", "页面：" + active.page + "。数据列表和表单可使用浏览器打印功能。");
	}

	showInfo(title, message) {
		const form = this.host.querySelector(".jfe-editor-form");
		if (form) {
			this.infoFormContext = this.editorSnapshot(form);
		} else {
			this.infoFormContext = null;
		}
		const root = this.host.querySelector("[data-modal-root]");
		root.innerHTML = '<div class="jfe-modal-backdrop" data-action="backdrop"><section class="jfe-info-modal" role="dialog" aria-modal="true"><header><h2>' +
			this.esc(title) + '</h2><button class="jfe-icon-btn" data-action="close-modal">×</button></header><p>' + this.esc(message) +
			'</p><footer><button class="jfe-button primary" data-action="close-modal">知道了</button></footer></section></div>';
	}

	toast(message) {
		const node = this.host.querySelector("[data-toast]");
		if (!node) return;
		node.textContent = message;
		node.classList.add("is-visible");
		clearTimeout(this.toastTimer);
		this.toastTimer = setTimeout(() => node.classList.remove("is-visible"), 2600);
	}

	fullscreen() {
		const shell = this.host.querySelector(".jfe-shell");
		if (shell && shell.requestFullscreen) shell.requestFullscreen().catch(() => this.toast("浏览器未允许全屏。"));
	}

	exitFullscreen() {
		if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
		else this.toast("当前页面未处于全屏状态。");
	}

	updateClock() {
		const node = this.host.querySelector("[data-clock]");
		if (!node) return;
		node.textContent = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date());
	}

	esc(value) {
		return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
			"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
		})[char]);
	}
}
