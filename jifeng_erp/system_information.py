"""Read-only product version and operator guidance for the shared workspaces."""

import importlib

import frappe


APP_LABELS = {
    "frappe": "Frappe",
    "erpnext": "ERPNext",
    "hrms": "HRMS",
    "jifeng_erp": "集沣 ERP",
}

# These notes describe the current workbench routes and their verified scope.
# They intentionally distinguish live native workflows from partial coverage.
MODULE_GUIDANCE = [
    {"name": "物料", "path": "物料 → 采购申请、采购订单、采购收货；出库、调拨、盘点；库存、流水与退回",
     "verified": "普通账号采购、收货、发票、分次付款及逆序撤销已界面核对；单项库存功能有后台验证。",
     "pending": "普通岗位完整采购→领料链、旧配料语义、单位和历史映射未核实。"},
    {"name": "材料", "path": "材料 → 材料档案、采购申请/订单/收货、出库、调拨、盘点与库存报表",
     "verified": "部分档案和共用原生库存单据已验证。",
     "pending": "尚未从旧材料菜单逐项完成界面链；旧字段、形状/颜色映射、领退料和历史数据未齐。"},
    {"name": "产品", "path": "产品 → 产品档案、商品工艺、样品制作、报价单",
     "verified": "公网商品编码/供应商货号、图片、样品、报价、订单和流程来源回读已核对。",
     "pending": "平台图片自动建档、旧打印及部分旧字段规则未核实。"},
    {"name": "订单", "path": "订单 → 订单录入/查询、生产流程单、算料/算料汇总",
     "verified": "普通账号订单→流程单已界面保存、提交，定制与包装文本及来源数量回读准确。",
     "pending": "聚水潭真实接入、补单、作废回退和旧反审全链未核实。"},
    {"name": "生产", "path": "生产 → 生产计划单、生产工单、工序报工；完成后从入库单办理制造入库",
     "verified": "同单BOM、转料、两人报工、制造入库、库存成本和计件结算已界面核对。",
     "pending": "损耗、旧改排、返工及生产反审未全部验收。"},
    {"name": "人事", "path": "人事 → 员工档案、补充档案、部门/职位、晋升、调岗、离职",
     "verified": "测试员工晋升、调岗、离职办理与撤销及A/B历史回读已核对。",
     "pending": "历史导入、全部人员操作和完整岗位权限未验收。"},
    {"name": "工资", "path": "工资 → 工资录入、计薪记录、多人计件分配/审核、工资附加项、工资发放、工资单",
     "verified": "手工计件、报工分配和纯计时三类计提/付款/逆序撤销已核对；A/B同源。",
     "pending": "付款后重算工资成本、旧图表、税费/考勤和全部岗位权限未齐。"},
    {"name": "模具", "path": "模具 → 模具档案、模具录入、查询与实际费用",
     "verified": "档案图片/报废，以及赊账费用、费用分摊、付款和逆序撤销已核对。",
     "pending": "导出、独立建模费用及其他档案规则未全部验收。"},
    {"name": "版种", "path": "版种 → 版种档案、录入/建模、查询与实际费用",
     "verified": "图片/报废、费用分摊、付款/撤销和A/B来源回读已核对。",
     "pending": "独立建模费用、导出和其他档案操作未全部验收。"},
    {"name": "发镀", "path": "发镀 → 委外采购单、电镀委外单、电镀收货单、品检单",
     "verified": "两批收货、服务发票/付款、成本回读和逆序撤销已界面核对。",
     "pending": "旧品检扣款、返工和对账布局未核实。"},
    {"name": "外发", "path": "外发 → 委外采购单、委外订单、委外收货及未收查询",
     "verified": "发出/收回数量和费用已有后台验证，正式委外单据可查询。",
     "pending": "旧收回/工价规则及完整界面链未验收。"},
    {"name": "销售", "path": "销售 → 销售订单、发货单、销售发票、收款与退货来源单",
     "verified": "发货开票、分次收款、退货/红字及撤销已核对；A/B销售来源一致。",
     "pending": "零售、旧销售入库审核/标价和外发衔接未齐。"},
    {"name": "财务", "path": "财务 → 收付款单、总账凭证、费用登记及对应查询",
     "verified": "采购、工资、销售结算和撤销已界面核对；工具费用和电镀服务费也完成结清/撤销。",
     "pending": "电镀专用对账、全部财务报表及完整岗位权限未逐项核实。"},
    {"name": "总经理", "path": "总经理 → 经营总览；选择公司/日期，点击指标进入来源单据",
     "verified": "A/B普通账号总览报表按公司和日期打开、修改期间后重新查询已核对。",
     "pending": "完整指标口径、旧布局、现金余额配置和全部岗位范围未验收。"},
    {"name": "权限", "path": "权限 → 用户管理、角色管理、权限管理",
     "verified": "部分普通岗位的正例、拒绝和范围隔离已验证。",
     "pending": "全部角色在各模块关键动作尚未覆盖。"},
    {"name": "日志", "path": "日志 → 服务器操作日志、操作日志",
     "verified": "部分凭证的版本记录、删除回执和来源追溯已验证。",
     "pending": "普通岗位完整追溯界面未核实。"},
    {"name": "插件", "path": "插件 → 打印插件",
     "verified": "菜单提供插件说明入口。",
     "pending": "实际插件用途及所需范围未完成确认。"},
    {"name": "工具", "path": "工具 → 远程协助工具；业务页面另有导入、导出和打印入口",
     "verified": "部分共享导出功能已验证。",
     "pending": "旧打印格式、实际辅助工具及批量操作未全部验收。"},
    {"name": "关于", "path": "关于 → 关于本系统、用户须知、续费信息",
     "verified": "本页显示当前安装包版本并提供各模块业务导航和验收范围。",
     "pending": "续费合同、收费规则和续费联系人资料未提供，需向公司确认。"},
]


def _version(app):
    try:
        module = importlib.import_module(app)
    except ImportError:
        return "版本信息不可用"
    value = getattr(module, "__version__", None)
    return str(value) if value else "版本信息不可用"


@frappe.whitelist(methods=["GET"])
def get_information():
    """Return a safe version allowlist and Chinese operator guidance."""
    if not getattr(frappe.session, "user", None) or frappe.session.user == "Guest":
        frappe.throw("请登录后查看系统信息", frappe.PermissionError)

    installed = set(frappe.get_installed_apps())
    versions = [
        {"name": label, "version": _version(app)}
        for app, label in APP_LABELS.items()
        if app in installed
    ]
    return {
        "release": "公网业务测试版",
        "versions": versions,
        "shared_data_note": "A（开源优化版）与 B（旧界面复刻版）连接同一业务数据；切换界面不会复制或隔离单据。可见公司和操作仍由账号权限决定。",
        "workflow_note": "处理跨模块业务时沿来源单据继续：采购收货从采购订单生成；采购退货从已提交收货单使用 Create → Purchase Return；制造入库从已完成工单办理；工资结算按计薪审核、附加薪资、工资运行、工资条和付款的顺序核对。",
        "scope_note": "菜单上的“正式/部分可用”表示入口接入状态，不代表对应旧系统模块的全部字段、操作习惯、报表及权限已经验收。下表逐项列出已核范围与未完成项。",
        "modules": MODULE_GUIDANCE,
    }
