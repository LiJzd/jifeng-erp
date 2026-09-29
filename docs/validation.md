# 验证记录

2026-09-30：旧订单录入/查询入口接 ERPNext Sales Order；前台开单入口接 POS Invoice。路由静态检查和 Node VM 调用检查通过。手工 Sales Order 的选中明细 → 流程单逻辑已有纯 Python 测试，但当前站点不可用，尚未验证 Desk 新建、销售订单提交、POS Profile 设置、POS 库存和收款过账。

2026-09-30：旧财务“收款凭证/付款凭证”入口改为新建 ERPNext Payment Entry，并分别预设 Receive/Pay；查询入口按对应类型筛选原生 Payment Entry 列表。已对照 ERPNext Payment Entry 的正式 Select 选项、Frappe `new_doc` 传参及 List View 的 `route_options` 筛选实现做源码核对。此改动仅验证静态路由与 JS 语法；未在站点用会计角色提交凭证、核对 GL Entry 或实际点击查询过滤。店面、欠款、费用登记和旧财务报表仍未完成。

2026-09-30：计薪记录现可关联 ERPNext Job Card，核对公司、工序、生产工单和流程单；审核时要求 Job Card 已提交且员工在报工时间行。选择报工单时可带出公司、工序和来源单；工资角色在站点上需有对应 Job Card/Work Order 的读取权限。已审核计薪记录会阻止源 Job Card 被取消。42 项纯 Python 测试、Ruff、编译和字段顺序检查通过；相关站点冒烟函数可选传入已有已提交 Job Card。当前仍未在真实 Frappe/HRMS 站点验证 Job Card 权限、提交/取消钩子及工资单联动。多人计件数量如何分摊尚待业务口径确认，因此数量仍由每位员工的计薪单显式填入。

2026-09-30：新增版种正式档案，支持公司、编号、关联商品、图片、制版/建模人、参考费用、存放位置和停用记录；旧“版种录入/查询”路由连到服务器档案。旧页面原始字段缺失，当前字段为待现场核对的最小档案；费用不入账，建模业务单、版种报表和真实角色验收未完成。

2026-09-30：新增一人一道工序的正式计薪记录，可计算计件、计时与附加费并由 HR Manager 审核。已审核记录可幂等生成 HRMS Additional Salary 草稿；后者直接编辑时校验来源、金额、公司、员工、日期、收入项目和唯一性，提交后才可能进入工资单。38 项纯 Python 测试、Ruff、编译、相关 JS 语法、DocType JSON 和工作区入口检查通过。HRMS 已加入应用依赖，安装或迁移前须先在同站点安装 HRMS v16；当前无运行中的测试站点，尚未执行工资结构分配、附加项提交、工资单计算及权限验收。多人操作分摊规则仍待业务确认；当前每条仅表示一人一道工序，工资币种暂要求与公司默认币种一致。

同日：旧工资录入、明细、审核和汇总菜单已分别路由到服务器计薪记录与 HRMS Salary Slip；静态 JS 语法通过，尚未做 Desk 点击和角色验收。

2026-09-30：员工补充档案一对一关联 ERPNext Employee，仅 HR Manager/System Manager 有单据权限；身份证图片只能引用当前档案的私有 File。34 项纯 Python 测试通过，新增用例覆盖私有图片、公开地址、跨档案引用和原生员工信息变更；Ruff、Python 编译、JS 语法、16 个 JSON 解析及工作区快捷入口检查通过。0.2.0 wheel 已确认包含新增 DocType。站点冒烟脚本已准备；当前没有运行中的 Frappe 站点，尚未验证 DocType migrate、上传权限和真实角色查询；身份证号码在数据库中按受限字段保存，未实现字段级加密或脱敏展示。

2026-09-30：流程单增加生产 BOM 与 Work Order 关联，可从有效 BOM 生成正式工单草稿；所生成工单反向记录流程单，并校验公司、商品、BOM 与销售来源一致。单元测试覆盖重复生成复用和跨公司 BOM 拒绝；测试站点冒烟命令已增加，但因本机没有运行中的 Frappe 站点，BOM/仓库设置、原生工单提交与实际报工尚未验收。

2026-09-30：新增已提交 Sales Order 明细生成集沣流程单、手工订单来源快照与取消/变更提示。27 项纯 Python 测试通过，覆盖重复生成、同商品不同明细分开、未提交拒绝、源数量/要求更新不覆盖计划、取消标记；Ruff、Python 编译、JS 语法、15 个 JSON 解析及 DocType 字段顺序检查通过。0.2.0 wheel 构建成功并确认包含销售订单按钮脚本、流程单结构和打印格式。查 Frappe 源码发现文档通知会调用数据库 commit；冒烟脚本现于测试期间禁止事务提交并在 finally 回滚保存点，避免测试数据意外留存。Frappe 站点冒烟脚本已准备，但当前本机没有运行中的站点，尚未执行 migrate、真实提交/取消及按钮/角色验收。

2026-09-29：商品工艺档案补齐已从旧产品档案页面核对的分类、设计/打版/建模、专利、参考价、重量等字段；金额和重量拒绝负数及非有限数字。静态验证通过：DocType 41 个字段与顺序一致、Python 编译、Ruff、23 项既有测试及 `git diff --check`。这些新字段尚未在 Frappe 站点执行 migrate，也未验证导入、图片权限或实际业务录入；参考价只作档案信息。

同日：经典工作台将已确认能对应正式档案的旧入口接到 Item、Warehouse、Supplier、Customer、Employee、Jifeng Product Profile 和 Jifeng Mold。仅完成静态语法和路由映射核对，尚未在 Desk 用实际角色逐项点击验证；其余旧业务页仍是本地预览。

2026-09-29：新增在成本试算单中显式带入已提交委外收货费用，保存最近一次来源凭证和带入时间；从流程单重新读取预计费用时清除实际费用来源。经典工作台增加正式单据入口，与本地预览页面明确区分。Windows 本地纯 Python 测试 23 项、Ruff、相关 JS 语法、DocType JSON 解析及 `git diff --check` 均通过。站点冒烟测试新增“无已提交收货不得改费用”检查，但当前环境仅列出 `docker-desktop` WSL 发行版，尚未在 Frappe 测试站点运行本次变更；有真实收货/退货明细的带入和新入口布局仍需现场验收。

同日需求范围更新：用户要求第一版旧系统所有模块正式可用。已增加生产、人事、发镀、外发、销售、财务的原生单据入口并建立全模块交付清单；这些入口尚未逐个做站点/角色验收，旧页面动作仍不得视为正式业务功能。Frappe HR v16.20.0 源码已单独取得，尚未安装到测试或生产站点。

新增模具正式档案后，静态验证通过：DocType 字段/顺序及工作区入口一致、两个工作区 JSON 层级可解析、Python 编译、Ruff、经典工作台 JS 语法和 23 项既有测试。模具创建、图片权限、报废状态和复制链需待 Frappe 测试站点实际验证；参考金额不产生库存或财务凭证。

2026-09-26：纯Python测试23项通过，包含官方公开签名向量、SKU参数类型、64位游标精度、限流重试、错误信息脱敏、令牌刷新不盲重试、同SKU不同行号、游标包含边界、失败页不推进、商家校验、商品窗口续传、图片地址检查，以及返工/待检/损耗计价。Ruff、Python编译、13个JSON解析和5个JS语法检查通过。0.2.0 wheel构建成功，已检查包含DocType、脚本、工作区、打印格式和中文翻译。

WSL Ubuntu独立测试环境已安装：Python3.14、Node24、MariaDB11.8（端口13316）、Redis、Frappe/ERPNext version-16。测试站点为 `jifeng.localhost`；未修改ERPNext上游业务源码。二开应用安装、`bench migrate`、前端完整构建均通过。

真实数据库冒烟检查12项通过：流程单重复生成复用、采购草稿复用、成本试算、返工损耗、实际费用查询、库存查询、商品更新、订单更新、同SKU不同明细行、来源变更保护、打印HTML渲染、不产生总账或库存台账。测试业务数据在保存点回滚。测试发现采购申请需要入库仓库，已增加字段与公司/启用状态校验。

浏览器登录页可加载；随后浏览器工具因无法可靠确认当前URL而拒绝继续操作，已停止该项验证。业务页面布局、中文标签和按钮的浏览器验收尚未完成。

聚水潭真实凭据联调尚待完成：服务器凭据据用户说明已存在，尚缺主机和配置路径，未读取任何真实密钥，也未执行远端部署。普通订单接口的渠道覆盖、真实图片下载、生产角色权限以及实际委外凭证费用/退货需现场验收；空库费用查询通过不等于这些业务验收通过。

## 已安装测试站点上的冒烟测试

完成 app 安装/migrate，并准备好测试公司的科目与基础设置后，替换下面参数：

```bash
bench --site YOUR_SITE execute jifeng_erp.diagnostics.check
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_test --kwargs '{"company":"测试公司","item_group":"Products","stock_uom":"Nos"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_sales_order_flows --kwargs '{"company":"测试公司","customer":"测试客户","item_group":"Products","stock_uom":"Nos"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_work_order --kwargs '{"flow_order":"尚未生成工单且已关联有效BOM的测试流程单"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_employee_supplement --kwargs '{"employee":"尚未有补充档案的测试员工编号"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_wage_entry --kwargs '{"employee":"已分配工资结构的在职测试员工编号","salary_component":"已启用的收入类工资项目"}'
```

冒烟测试实际创建测试商品、档案、来源订单、流程单、采购草稿和分批试算，检查重复操作复用原单、同步变化及台账数量不变；退出时回滚这些测试数据，不提交库存/财务凭证。测试公司须有启用的明细仓库和当前会计年度。请在测试站点运行，不能把单元测试通过等同于生产验收。

## 实连验收

在服务器端载入现有配置，用只读查询验证授权/白名单/商家及平台覆盖；选取少量已知订单核对商品、行号、数量和图片。验证重复同步、源单改量、取消、分页故障恢复。之后由生产人员用实际单据核对单位、库存、流程单费用与试算结果。
