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
    {"name": "物料", "path": "物料 → 采购申请/采购订单/采购收货；出库、调拨、盘点；库存与流水",
     "scope": "正式单据与报表；采购履行核对、跟踪为部分可用。退货从已提交收货单的 Create → Purchase Return。"},
    {"name": "材料", "path": "材料 → 材料档案/采购申请/采购订单/采购收货；出库、调拨、盘点；库存与流水",
     "scope": "正式单据与报表；采购履行核对、跟踪为部分可用。退货从已提交收货单的 Create → Purchase Return。"},
    {"name": "产品", "path": "产品 → 产品档案、商品工艺、报价与样品",
     "scope": "商品主数据与报价连接原生单据；镀色、样品制作与反审入口标为部分可用。"},
    {"name": "订单", "path": "订单 → 销售订单、生产流程单；算料/算料汇总查看材料需求",
     "scope": "订单与流程连接正式单据；字典及部分分析入口的覆盖范围以菜单标签为准。"},
    {"name": "生产", "path": "生产 → 生产计划单 → 生产工单 → 工序报工；完成后办理入库",
     "scope": "计划、工单、报工及对应正式报表；数量与库存以已提交单据为准。"},
    {"name": "人事", "path": "人事 → 员工档案、部门/职位、补充档案、晋升、调岗、离职",
     "scope": "正式人事单据使用原生权限与审核；菜单权限受当前账号角色限制。"},
    {"name": "工资", "path": "工资 → 工资录入/计薪记录 → 分配审核 → 工资附加项 → 工资发放/工资单",
     "scope": "计薪、审核、附加项、工资单与发放走正式薪资单据；工资历史和临工相关入口有部分可用范围。"},
    {"name": "模具", "path": "模具 → 模具档案、模具录入、模具查询",
     "scope": "档案与查询连接原生单据；旧系统字段和打印习惯仍以实际页面为准。"},
    {"name": "版种", "path": "版种 → 版种档案、录入/建模、查询",
     "scope": "档案与查询连接原生单据；旧系统字段和建模习惯仍以实际页面为准。"},
    {"name": "发镀", "path": "发镀 → 委外采购单 → 电镀委外单 → 电镀收货/品检",
     "scope": "委外订单、收货与质检连接原生单据；电镀厂档案入口标为部分可用。"},
    {"name": "外发", "path": "外发 → 委外采购单 → 委外订单 → 委外收货",
     "scope": "委外订单与收货连接原生单据；外发工厂档案入口标为部分可用。"},
    {"name": "销售", "path": "销售 → 销售订单 → 发货单/销售发票；零售开单需按站点模式操作",
     "scope": "销售单据与零售入口连接原生单据；履行核对、来源核对和部分客户档案入口为部分可用。"},
    {"name": "财务", "path": "财务 → 收付款单、总账凭证、费用登记；从相应查询入口核对凭证",
     "scope": "收付、总账与费用使用正式单据；供应商应付核对和部分基础科目入口标为部分可用。"},
    {"name": "总经理", "path": "总经理 → 经营总览，选择公司和日期范围；点击指标查看来源单据",
     "scope": "汇总按指标说明取数；库存与未完工工单反映当前状态，不随日期范围回溯。"},
    {"name": "权限", "path": "权限 → 用户管理、角色管理、权限管理",
     "scope": "权限取决于系统角色与原生权限设置；本页面不提供权限绕过。"},
    {"name": "日志", "path": "日志 → 服务器操作日志/操作日志",
     "scope": "按账号权限查看原生操作记录。"},
    {"name": "插件", "path": "插件 → 打印插件",
     "scope": "提供说明入口；不会在工作台自动下载或安装外部程序。"},
    {"name": "工具", "path": "工具 → 远程协助工具",
     "scope": "提供说明入口；不会在工作台自动下载或启动外部程序。"},
    {"name": "关于", "path": "关于 → 关于本系统/用户须知/续费信息",
     "scope": "显示当前安装包版本和本页操作帮助；不展示服务器路径、配置或账户信息。"},
]


def _version(app):
    try:
        module = importlib.import_module(app)
    except ImportError:
        return "未安装"
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
        "workflow_note": "正式单据使用页面提供的保存、提交、取消等原生操作；请先核对公司、日期、仓库、来源单和明细。结果未知时先查询原单，不要重复创建。",
        "scope_note": "菜单上的“正式/部分可用”表示当前入口接入范围，不等于全部旧系统操作、字段、打印或报表已验收。未列为正式的功能请按页面实际提示判断。",
        "modules": MODULE_GUIDANCE,
    }
