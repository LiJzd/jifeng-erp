# 集沣 ERP 外网 A/B 测试站

## 2026-10-04 当前外网版本：81abd2e

- [A · 开源优化版](https://116.62.241.148/desk/jifeng-modern)
- [B · 旧界面版](https://116.62.241.148/desk/jifeng-legacy)

两套共用原测试数据库。当前实际源码为 `81abd2e716001ad482f1a37c1c489887dd4810c6`，五应用只读挂载 `/opt/jifeng-erp-test/releases/81abd2e-inventory-tracking-r17/app`。维护需保留基线、salary、tool-cost、classic-loading 和新库存覆盖文件五层；最后一层为 `/opt/jifeng-erp-test/releases/81abd2e-inventory-tracking-r17/compose.inventory-tracking.r17.yaml`。不能只用下方旧历史批次的覆盖文件重启。

五层配置按以下顺序保留：

1. `/opt/jifeng-erp-test/releases/8858743-r2/deploy/compose.yaml`
2. `/opt/jifeng-erp-test/releases/2ca4358-salary-r1/compose.salary.r1.yaml`
3. `/opt/jifeng-erp-test/releases/5b49bac-tool-cost-r1/compose.tool-cost.r1.yaml`
4. `/opt/jifeng-erp-test/releases/a75848f-classic-loading-r1/compose.classic-loading.r1.yaml`
5. `/opt/jifeng-erp-test/releases/81abd2e-inventory-tracking-r17/compose.inventory-tracking.r17.yaml`

fresh r17 五阶段各唯一派发完成；原生四部件备份、934 对象和权限／原配置保护、恢复公共入口与 worker 前全 SQL 核对、577 包文件（569 受 Git 跟踪＋8 项 Git 元数据）／35 资源及八服务通过。正常外网 HTTPS、密码登录／独立身份、A/B 两入口、两类查询及选项空结果、35 资源完整字节和 CSRF 退出／Guest 拒绝已通过；实际公网 GUI 同公司两版空查询、16／17 数据列及供应商弹窗取消／Escape通过并正常退出。没有 DDL、迁移、种子、角色或业务保存。此为测试环境，全旧模块正式可用尚未完成；账号在本地受限文件交接，证据及边界见[验证记录](../../docs/validation.md)。已消费阶段和 r8／r14 失败及恢复原件禁止重放；以下版本记录均为历史批次。

## 2026-10-04 历史外网版本：a75848f

- [A · 开源优化版](https://116.62.241.148/desk/jifeng-modern)
- [B · 旧界面版](https://116.62.241.148/desk/jifeng-legacy)

两套共用测试数据。当前实际界面源码为 `a75848f8f8237e94c5b834d6629e2cb9a1f9eda6`，业务保持 `5b49bac`，五个应用只读挂载 `/opt/jifeng-erp-test/releases/a75848f-classic-loading-r1/app`。启动配置包括基线、salary、tool-cost、classic-loading 四层；后续维护必须保留全部四层，不能按下文旧历史批次的命令启动。

该次发布只修复原生列表加载：四阶段、完整源码与33资源、八服务、配置和三个数据服务身份已验证；可信HTTPS登录、A/B页面、退出及实际订单菜单GUI已通过。没有DDL、迁移、种子或角色变更。详细证据见[验证记录](../../docs/validation.md)。库存跟踪与其他本地候选尚未发布；第一版全部旧模块正式可用的目标仍在推进。以下记录按当时的历史状态保留。

## 2026-10-04 当前工具费用测试发布：5b49bac

业务提交 `5b49bacde6fcc088e08b118103fee496524ec99f` 已切换五应用只读源码挂载至 `/opt/jifeng-erp-test/releases/5b49bac-tool-cost-r1/app`，固定镜像及三个数据服务身份保持。运行配置为基线 compose、既有 salary overlay 和新 `compose.tool-cost.r1.yaml` 三层叠加；禁止单独使用旧覆盖文件重启。实际 prepare→quiesce→before→apply→verify→cache→restore→start 全部通过，站点配置按原字节／属主／权限恢复；旧完整 932 对象保持、只新增两空表，原生备份留受限服务器目录。独立外网可信 HTTPS 登录、A/B 页面、33 资源、原生表单、CSRF 退出后 Guest 及前后八服务检查通过；自有测试账号单次轮换，受限 JSON 和中文交接 TXT 更新后独立核对一致。GUI 未验收，全部模块正式可用目标未完成。失败 r1／r2／r3 副本完整保留；详见[验证记录](../../docs/validation.md)。

## 2026-10-03 工具费用下一发布准备（尚未部署）

本轮真实并发已核同一费用 PRIMARY／X 行锁等待、唯一正式分摊和败方草稿，原账簿与源 932 对象保持。业务 Git 包 `5b49bac` 完整 572 文件／33 资源审计通过，尚未上传；新 native 助手 `4ba85a6` 的 Windows 32 模型门及 WSL 21 native 模型门、Ruff／编译通过。自有副本已实际执行 before／apply／verify／cache，root 独立重读完整备份及 934 对象／缓存均一致，源 932 和配置保持；宿主协调器验证继续。生产只读预检 r2 确认运行 `2ca4358`、八服务、旧源只读挂载及八个 native 依赖 SHA 匹配，实际 SSH 前中后 DIRECT；没有外网写入或切换。GUI 仍未通过，自动审批因不能可靠判断新窗口 URL 拒绝读取。下面“真实并发待验收”保留此前状态，当前以[验证记录](../../docs/validation.md)最新节为准。

业务候选 `5b49bacde6fcc088e08b118103fee496524ec99f` 增加工具费用来源、金额分摊和原生付款核对。32 后端模型、4 资产模型、共享组件协议、15 原生门及本机直接 HTTP 草稿/Submit/40＋80 付款回读/岗位拒绝/主管分摊撤销通过；已修复原生 Form Date 文本与 SQL date 的比较误拒绝。GUI 只到 A 预填，尚未保存；真实并发及全模块缺口保持，外网仍下节 `2ca4358`。

`tool_cost_release_audit.py` 仅纯检查，21 失败门通过，固定旧 932 对象、两个新空 DocType/表及完整元数据/权限保护，33 项资产（12 CSS/21 JS）和精准 hooks 增量。独立原生布局/元数据及历史安装证据离线重核通过；没有新发布包、备份、native/host 助手执行或上传。本次 GUI/HTTP 自有副本已关闭并仅移除自有库/用户/站点，源隔离站完整 932 对象与配置保持。下一实际发布需新操作目录和真实 collector，不能重放旧 salary helpers。详见[验证记录](../../docs/validation.md)。

## 2026-10-03 当前发布：2ca4358

- [A · 开源优化版](https://116.62.241.148/desk/jifeng-modern)
- [B · 旧界面版](https://116.62.241.148/desk/jifeng-legacy)

两套继续共用测试数据。实际业务提交为 `2ca4358398cf750da31584fc31d3afaef48b30b6`，新增历史工价引用、工资结算查询/明确分类和完整工资历史 XLSX 导出；工资模块及全模块的既有业务缺口仍按验证记录推进，菜单映射数量不作验收。

固定镜像保持，五应用只读挂载 `/opt/jifeng-erp-test/releases/2ca4358-salary-r1/app`，覆盖文件为该目录的 `compose.salary.r1.yaml`，基线仍为 `8858743-r2/deploy/compose.yaml`。独立 `salary-control-r1` 五阶段各执行一次通过；四部件原生备份在受限 `salary-private-r1`。完整服务器 931→932 保护、原权限/旧列/索引/旧行投影、空 Rule、四新增 NULL 列、Form/child/Page 缓存前后数据保持均通过。八服务 running、三数据服务完整身份、站点配置字节/属主/权限恢复、受管文件和证书 timer 已核实。不得重放此次阶段。

实际外网可信 HTTPS、31 资源逐字节、账号登录两个入口、4 个原生 Form 脚本、子表新增字段、工资 Page 下发、2099 年空日期查询及正常 HTTP 注销通过；未下载真实工资。忽略证据 `salary-release-control-20261003-r3-*` / `salary-release-readonly-status-*` / `salary-release-external-verify-20261003-r1.json`。上传 r1 因 SFTP 模式留下空文件；r2 已上传全部 8 个文件但解包写入模式失败，未写源码、停服或触碰站点。各次失败经独立只读核实，r3 单独完成解包和发布，没有重传、重放旧操作或自动回滚。

首次 CUA 连接失败，经重置后恢复；实际 GUI 已核 A/B 工资历史默认 21 列、15 条/页、2099-12-30..31 空查询、切换保留各自筛选状态、两套分类设置打开原生公司过滤列表及正常界面注销。独立“工价调用”入口仍标示本地预览，未验证实际历史工价读取；无工资行、导出、单据写入或过账。本轮空结果不能证明有数据时的一致性，reader GUI / 全模块 GUI 仍未验收。忽略报告 `salary-release-gui-20261003-r1/report.json`。

## 2026-10-03 工资增量发布准备：2ca4358

固定业务候选 `2ca4358398cf750da31584fc31d3afaef48b30b6`，553 个文件、20 JS / 11 CSS 共 31 主资源，继续使用既有镜像。新的 `salary_release_host.py` / `salary_release_native.py` / `salary_release_audit.py` 专用于当前 18e5835 的 931→932 增量，不能重放旧发布助手。

新建一张空 `Jifeng Salary History Rule` 表；既有 Wage Entry 和 Wage Allocation Employee 保留完整旧元数据身份与权限，仅精准插入四个只读 Data 字段、子表 Button，并调整相应 idx / 自有 header migration_hash。禁止整表 reload 既有 DocType；原生 updatedb 只处理这两张自有表。独立本地副本已实跑并通过旧行投影、原列定义和索引、全部旧权限及其余 931 对象保护；四新列全 NULL，新表为空，源 932 对象及配置完全保持，已删除成功副本。准备副本 r1 的 DDL 事务保护失败保留；独立只读核实未变化后，新 r2 使用原生 sql_ddl 完成副本准备，再实跑发布函数，未重放失败操作。

41 项纯审计 / 发布失败门测试通过；真实副本记录在忽略目录 `salary-release-native-clone-20261003-r2`，独立 Rule 结构参考在 `salary-release-native-reference-20261003-r1`。本节只记录发布准备，尚未上传、停服或切换；当前外网版本仍以下节 18e5835 为准。

## 2026-10-03 历史发布：18e5835

- [A · 开源优化版](https://116.62.241.148/desk/jifeng-modern)
- [B · 旧界面版](https://116.62.241.148/desk/jifeng-legacy)

两套共用测试数据，冻结业务源码为 `18e5835cfda4171ec691d5e701c8f42018b5448c`。继续使用原固定 `8858743-test-r4` 镜像，五应用只读挂载 `/opt/jifeng-erp-test/releases/18e5835-source-r1/app`。532文件候选、28工作台资源及独立Sales Order客户选择脚本均已核；只新增镀色/工序排序两张空表及自己元数据。原生四部件备份、完整929→931保护、表单缓存与配置精确恢复通过。

八服务运行、三个数据服务身份、可信HTTPS、28资源逐字节、实际测试账号登录A/B及原生表单脚本下发已独立复核，检查会话退出。全模块正式验收及真实GUI继续待完成；账号在本地受限文件交接，未提交账号或下载备份。下面较早“未发布/仍4d”记录保留历史状态，当前以本节和[验证记录](../../docs/validation.md)为准。

启动五应用同时使用以下基线及新覆盖文件：

```bash
docker compose --project-name jifeng-erp-test \
  --project-directory /opt/jifeng-erp-test --env-file /opt/jifeng-erp-test/.env \
  -f /opt/jifeng-erp-test/releases/8858743-r2/deploy/compose.yaml \
  -f /opt/jifeng-erp-test/releases/18e5835-source-r1/newcompose.source.r2.yaml \
  --profile tools up -d --no-deps --no-build --pull never \
  backend worker scheduler websocket frontend
```

发布助手独立审核提交 `fa560e8`，固定运行计划/公开SHA/三helperSHA/532文件/归档SHA和字节数；新控制位于 `incremental-control-r2`，备份位于 `private-backup-r2`。r1因完整工具清单漏算而在停服/SQL前拒绝，记录保留；r2五阶段各一次通过，禁止重新执行本轮阶段或自动回滚。之后的增量需重新定范围、冻结和创建操作目录，当前929→931助手不可复用于已经931的站点。证书续期仍依赖原8858743目录，继续保留。

## 2026-10-03 当前运行状态复核

本轮只读检查确认外网实际源码仍为`4d71c354d3f3f281d21d3d16b496bb6e5434e655`，八服务running、无OOM、RestartCount=0、数据库healthy，五应用release源码只读挂载匹配。可信HTTPS登录页和ping均200；HTTPS/SSH均在实际sourceIP/port连接前中后核DIRECT。未部署、登录/退出、重启、写配置、SQL或下载备份；不能把此运行检查当作本轮A/B业务GUI验收。

r1/r2遇远端旧Python不支持`subprocess.run(text=True)`的只读助手兼容错误，发生在Docker命令前，失败证据保留；r3独立采用已验证`universal_newlines=True`和兼容字符串处理后完成运行版本核验。没有重放部署或故障自动恢复。证据：忽略目录`order-customer-external-health-20261003-r1/proof.json`及r2/r3，完整说明见[验证记录](../../docs/validation.md)。本地客户选择/工序/镀色未发布，账户仍通过本地私有文件交接。

## 2026-10-03 尚未发布的订单客户选择

本地原生 Sales Order 脚本新增订单客户档案选择，客户读取及核对为GET-only接口，客户变更仍由原生表单和订单保存处理。32模型、客户端协议、15真实岗位门及931物理对象业务回滚独立核验通过；原生FormMeta确实加载并缓存新脚本，18JS/10CSS共28主资源保持。

此表单脚本经doctype_js/FormMeta下发，不在28主工作台资源中；下一次发布必须核对有效doctype_js、目标FormMeta缓存及实际原生表单加载，不能只核静态URL。新客户选择未在外网发布，外网继续4d71c35；累积候选还包含工序/镀色两个新DocType。既有505文件/25资源发布助手的审核范围不适用；新版本须单独冻结源码、备份、定向更新和全物理保护核验，DIRECT传输，不重放旧脚本或全migrate。当前未进行远程更新。

## 2026-10-03 尚未发布的共用订单工序

本地新增 `Jifeng Order Operation` 排序/创建回执表，订单工序使用全局原生 Operation；已有原生 Operation、BOM、Work Order、Job Card及工资表结构和权限未扩展。31模型、22隔离真实岗位门及931物理对象业务回滚独立核验通过，28静态资源字节和指纹通过，有效hooks为18JS/10CSS。实际浏览器工具故障，目标A/B GUI未验收；临时预览已经关闭、配置恢复，整段931对象全同。

外网仍为下述4d71c35和25资源。工序与前一批订单镀色未发布，下一次候选需要审查两个新增DocType、28资源和完整保护范围。已有发布助手仅批准此前505文件/两个客户与订单DocType，不能直接用于这两个本地新DocType；不得重放旧一次性脚本、全migrate、seed或改变原生制造权限。新发布继续使用独立操作目录、服务器备份、精确源码版本、实际DIRECT传输证明和fresh全物理核对。

## 2026-10-03 尚未发布的订单镀色

本地订单镀色通过32项后端模型、相关客户端协议、19项真实岗位门及回滚后930物理对象独立核验；只新增空镀色表与目标DocType元数据，26直引资源核对通过。此增量未上传、切换或更新外网数据库，当前外网仍为4d71c35和下述25资源。既有发布助手的505文件、两个目标DocType及SHA范围只适用于已发布批次，不能直接重跑来发布镀色。下一次发布须重新审查目标新表、全量保护、资源和源版本，并保留新备份及独立操作目录；不得用全migrate或重放seed替代定向更新。

本地A/B已实际核同公司空查询、13列和六字段空新增开关，预览已关闭且配置恢复。原生hooks文件字节相同仍可能实际加载旧Redis app_hooks；本次只失效该键并读取有效hooks，17JS/9CSS同源码，完整SQL保护核验通过。下一批资源核对须包含实际有效hooks和HTTP指纹，不能只导入hooks.py即认定已加载。

两套工作台共用四个应用、同一个站点、同一个数据库和同一套合成测试资料：

- A：`/desk/jifeng-modern`，优化后的开源 ERP 界面。
- B：`/desk/jifeng-legacy`，沿用旧系统菜单、列表和操作入口。

当前业务源码固定为 `4d71c354d3f3f281d21d3d16b496bb6e5434e655`。原运行镜像基线源码为 `8858743800efe1836ac53f5c3de6644dab4e7923`，Frappe 16.35.0、ERPNext 16.36.0、HRMS 16.20.0 及其已构建资源保持固定。共用业务工作台核心，界面不各自计算或存储业务数据。

## 2026-10-03 最新实际发布：客户档案和订单词典

`4d71c35` 的505个公开文件和25项资源已发布；五个应用使用只读源码挂载 `releases/4d71c35-source-r1/app`，当前覆盖文件为 `releases/4d71c35-source-r1/compose.source.r2.yaml`。启动时同时加载基线 Compose 和这个覆盖文件，固定镜像不变；以下较早批次的路径和状态按当时证据保留。

新单次协调器分 prepare、quiesce、metadata、verify、start 五阶段，52项模拟检查及Ruff通过。维护期间在服务器私有 `private-backup-r2/` 完成数据库、公开附件、私有附件及站点配置四部分原生备份，核gzip/tar、完整927个物理对象及SHA。独立连接对三张MyISAM表（包含 `__global_search`）持READ锁贯穿快照和SQL备份；物理清单同时包含六个InnoDB序列。

定向更新两行客户词典DocField的三项属性，只reload两个新增空DocType。独立后验及物理只读缓存阶段核927→929：仅新增空Customer Profile、Order Dictionary及明确元数据，既有业务表内容、结构、索引保持。完整before证明为 `89ef65d3909f68207bd88e082f19f3aab0ee0c051e4de4c6b069fd71958722b4`，apply、独立verify、cache证明均为 `c298a95077430ef3b34c68e4f9ab420ce049d7d3e043d240337ff1ab5064c063`。原站点配置精确恢复，数据库及两个Redis服务身份保持，八服务运行、后端Git为4d；没有全站migrate或seed。

原r1 prepare在停服务、配置修改及SQL之前因Docker inspect超时拒绝，证据保留。实际Docker 26.1.3 CLI与私有假socket复现看板JSON格式隐式请求size=1；采集器按SHA备份后加 `--size=false`，本地看板源亦修正。r5只恢复daemon，八任务ID/PID/启动tick及保护配置保持；尚未证明所有历史size查询的调用者或长期稳定性。

外部可信TLS登录页与ping通过，25项资源HTTP200且逐字节匹配，共560299字节，每项实际连接核DIRECT。受限测试账号登录、A/B认证页面及POST退出通过；Luna真实浏览器两套分别核五个客户/词典入口、同公司空查询和新增弹层开关，无业务保存。下方滚动字段、写入及完整操作未核，范围见[验证记录](../../docs/validation.md)。当前363入口为235正式映射、11部分可用、99业务预览、13导航、5工具；全模块正式可用尚未完成。账号仅在本地受限文件交接，未提交凭据或备份，未推送Git。

工程助手的单次日志、完整SQL证明和备份都保留在服务器；失败阶段先核实结果，禁止重复执行。`incremental-control-r1/` 和原 `private-backup/` 未覆盖，成功阶段位于 `incremental-control-r2/`。助手内版本与SHA是本次发布的审核范围，下一批必须重新审查并使用新操作目录。

## 2026-10-03 后续本地订单词典

订单类型/要求在本地隔离副本通过31模型及18真实岗位门；只新增空词典表，业务回滚后929表内容/结构/索引保持，25直引资源核对通过。本批没有上传、外网schema更新或切换，外网仍使用下述`5aa4b3b`及共享测试资料；新的本地两入口不能在当前外网链接验收。GUI、旧单据选择、简拼/删除/打印和并发继续待完成。

## 2026-10-03 再次外网响应恢复

收尾发现登录页响应超时，服务器dockerd RSS约747MiB/Swap约2368MiB，八个ERP任务仍运行。新r4只执行一次Docker管理进程恢复（已先确认live-restore和KillMode=process），PID328950→344058；八任务ID/PID/启动tick、Docker/内存/.env配置SHA保持，containerd及业务容器未重启，数据库无迁移或seed。独立外部检查HTTP301、可信TLS登录页200及ping200/pong；实际测试账号登录、两套认证页面和带CSRF的POST退出均200，各实际连接核DIRECT，无业务保存或凭据输出。

服务器单次intent/completed保存在私有`operations/daemon-recovery-20261003-r4/`；本地忽略目录driver/health/auth证据详见[验证记录](../../docs/validation.md)。管理API恢复不代表长期稳定，size查询调用者仍未知；本轮没有再次切换候选或发布本地新模块。以下较早失败与恢复经过按原证据保留。

## 2026-10-03 客户字典候选停止，恢复原测试版

后续本地销售/订单客户档案已完成targeted新表和真实岗位11门验证，未上传或切换外网。当前运行仍是下述`5aa4b3b`；本地928表及24资源检查属于隔离副本，不能当外网升级或界面交付证明。当地快照已实测将`__global_search`与两日志MyISAM一起持独立READ锁；外网实际锁探针尚未通过，不能从本地结果推定外网门槛通过。

当前仍运行 `5aa4b3b`，启动命令继续使用下节的 `5aa4b3b-source-r1` 覆盖文件。`0f84e22` 公开候选包上传、488文件校验及服务器本地复制完成，但没有切换运行。第一次 prepare 因主机没有 Git 而失败；改为固定镜像内核验后，独立 prepare 完成。quiesce 的完整 before 因非 InnoDB 表拒绝，发生在任何客户字典元数据写入前。本轮完整 before 和四部件备份没有完成，没有执行 DDL、全站 migrate、reload 或 seed。

回滚已实际完成：五个应用恢复旧源码只读挂载，站点配置原字节、所有权及权限恢复，三个数据服务保持。独立确认后端 Git 为 `5aa4b3b`、八个原生任务运行、maintenance=0。外部可信 HTTPS 登录页/ping 正常；受限账号接口登录及 A/B 认证后页面均200。这些检查不替代界面操作和全模块业务验收。

外网919张来源/Auth表中只有 `tabData Import Log`、`tabError Log` 为 MyISAM；不能将其读取称为 InnoDB 一致性事务快照。新的独立读锁草案已审阅，实际探针启动超时、结果未知，未计通过；后续查询未见探针数据库连接残留。历史发布的表读取/hash结果保留，但不额外承诺 MyISAM 全库原子快照。

本轮 Docker daemon 只重启一次，恢复时八个任务身份、PID/启动 tick 与保护配置保持；管理接口后来又超时，size 查询调用者仍未知。最新只读快照中 daemon RSS 约816 MiB、swap约987 MiB，`GOMEMLIMIT` 并非 RSS 硬上限，不能判定已稳定。本轮不继续新 r2 候选的 prepare/quiesce，所有失败和回滚证据保留。当前站点用于功能对照测试，尚未通过并发负载及全模块正式验收。

## 2026-10-03 源码增量发布

外网A/B已运行`5aa4b3b`：使用原`8858743-test-r4`镜像，backend、worker、scheduler、websocket、frontend五个服务只读挂载`releases/5aa4b3b-source-r1/app`。应用Python依赖未变；488个源码文件及22个内容指纹资源已在容器内核验。实际后端Git提交、五个只读挂载和八服务running/restart0/noOOM均复核，数据库healthy；外网可信TLS登录页/ping200，22个CSS/JS全部HTTP200且逐字节SHA与候选一致。真实浏览器验收另见验证记录，不把静态HTTP视为登录业务验收。

本次Luna真实浏览器已登录A、切换B，核双方中文工作台、客户类型的同公司0行以及流程单/成本/员工补充档案列表，B费用登记单亦可打开；未保存的空销售订单已放弃。没有进行本次业务保存或导入；console/network/pageerror和本地截图受工具能力限制未取得，不报零错误。测试账号仍通过本地受限文件交接。

升级前停止五个应用并在服务器私有目录完成原生数据库、公开/私有附件和配置备份；gzip/tar及SHA核验通过，不下载备份。四个自定义DocType按原生reload更新，11个既有流程Link仅原生set_value更新search_index并添加指定非唯一BTREE索引；生产/采购核心父表不整体同步。Stock Entry已有两处索引保持。每一步有私有started/done，DDL明确提交并用新连接核验；没有执行全站migrate、patch、install、初始化或seed。

两个独立原生只读快照核918→919张tab*/Auth表：仅新增空`Jifeng Customer Type`，914张既有非元数据表完整旧列投影/行数相同，账号、GL/SLE、历史数量保持；四种元数据表只允许本次四DocType和11CF的明确变化，原列与旧索引保持。before证明SHA为`be004b285103a093b9ae4001b155548e9f2884fd608afbe9e57afecc17ea9849`，最终after-r4为`01dcce2fb2f7de9c7f32aab98844ef696ad41852051f99d2cb8faefaddb9f75f`。缓存清理另用物理MariaDB只读事务，原站点配置字节/权限精确恢复；数据库/Redis容器身份和挂载保持。

候选检查的内存数字字符串、POSIX路径及挂载数组顺序误拒保留。Docker只读API后来超时，在核实已启用live-restore后恢复管理daemon，原八个容器身份/启动时间/重启数/镜像/挂载保持。升级首次预检查发现Purchase Order Item的其他原生结构漂移，在DDL前拒绝，随后改为指定索引更新；成功DDL只执行一次。后验工具保留HRMS Leave Control Panel重复逻辑键、异常帧行号及decimal零默认值格式的三轮失败，分别修正真实主键比较、准确异常帧、仅三列已批准scale6→9对应`0.000000→0.000000000`，没有改HRMS表、扩大允许业务差异或重跑DDL。

以后启动或更新应用同时加载基线与源码覆盖文件：

```bash
docker compose --project-name jifeng-erp-test \
  --project-directory /opt/jifeng-erp-test --env-file /opt/jifeng-erp-test/.env \
  -f /opt/jifeng-erp-test/releases/8858743-r2/deploy/compose.yaml \
  -f /opt/jifeng-erp-test/releases/5aa4b3b-source-r1/compose.source.yaml \
  up -d --no-deps --no-build --pull never backend worker scheduler websocket frontend
```

`.env`继续选择固定运行镜像；实际业务版本由只读源码挂载和本次manifest确认。证书续期仍依赖原`8858743-r2/deploy`目录，该目录继续保留。源包、私有备份、操作证明和失败日志均留在服务器独立发布目录；仓库不包含账号、数据库或备份。本发布仍是共享测试站，105个业务预览与全模块验收缺口继续推进。

## 2026-10-02 实际发布

- [A · 开源优化版](https://116.62.241.148/desk/jifeng-modern)
- [B · 旧界面版](https://116.62.241.148/desk/jifeng-legacy)

运行镜像为 `jifeng/erp-test:8858743-test-r4`，ID 为 `sha256:543ecd7e7e51f5088659718cadb950f542a80bd8a17a5a8da92a3067e9372cff`。业务包保持冻结，部署脚本单独记录版本。镜像后的通用启动参数修订未重新导出镜像：服务器 Compose 明确指定 Gunicorn 的 sites 工作目录，并将 `short,default,long` 作为 worker 的单个队列参数；实际后端和 worker 按这些参数运行。后续构建应使用当前 Containerfile 与 Compose。

新建站点 `jifeng-erp-test.localhost` 与数据库 `jifeng_erp_test`；独立原生连接已确认四个应用、设置向导、测试账号业务权限和全部合成资料。测试账号没有 System Manager 角色。种子包含 2 名员工、3 个商品、两项物料的商品工艺档案、电镀厂档案和销售订单草稿；初始化及种子验证时 GL Entry、Stock Ledger Entry 均为 0。

外网真实 Chrome 已通过登录、两套电镀厂查询、19 列展示、A 主页回航保留，以及合成档案备注 A→B、B→A 的两次保存和回读。验证记录保留驱动初期失败及限制，不把一条档案往返等同于所有模块验收。scheduler 原 128 MiB 限额出现历史 OOM，现按 `fcca100` 修订为 RAM 256 MiB、RAM+swap 512 MiB，仅重建调度容器，其他七个容器完整保持。

构建结束后残留的 daemon 内存占用曾引起主机 global OOM，Docker 自重启后已受控恢复现有服务，数据卷及已保存的 A/B 备注经独立连接核验保持。当前八服务 running、restart0，数据库 healthy；两套浏览器实时连接也已复验通过。该主机为 2 CPU、约 2 GB RAM、独立 4 GB swap 的小型测试机，未做并发负载验收。后续镜像在独立构建机生成并验证，按直连要求传输到此运行主机；保留此次失败、恢复与数据验证回执。

`318ce40` 配置本项目 Docker 的 Go 管理内存软限额 `GOMEMLIMIT=512MiB`，以及 `live-restore`。先重载并确认后者真实启用，再仅重启 daemon 使环境生效；独立复核八个容器 ID、PID、StartedAt、重启数保持，containerd 不变，数据库 healthy。运行快照中 daemon RSS 约 505 MiB、swap 约 9.4 MiB；软限额不能当作 RSS 硬上限或并发验收。

外部标准 CA 校验通过，HTTP 登录入口重定向到 HTTPS，HTTPS 登录页与 ping 均为 200。当前 IP 证书到期为 2026-10-09 00:38:58 UTC。自己的续期 timer 已 active/enabled：staging 续期演练退出 0，生产检查及 nginx 重载成功且未额外申请生产证书；尚未经历实际到期窗口续期。详细验收及限制见 [验证记录](../../docs/validation.md)。

## 服务器布局

部署在独立目录 `/opt/jifeng-erp-test`，Compose 项目名为 `jifeng-erp-test`。原服务器定时任务及其文件保持原样。只有前端公开 80、443；数据库、Redis、后端和实时服务均位于私有容器网络。

`releases/8858743-r2` 保存冻结的公开业务源码、资产及 SHA 清单。部署文件按后续工程提交修订，原文件与上传摘要保留在本项目 revision-history 和部署记录中；不能仅凭目录名把部署文件认定为初始字节。`secrets` 保存新生成的数据库、管理员、测试账号密码；不进入镜像、Git、环境变量或命令行参数。`acme` 保存证书及验证目录。测试站不导入本地站点配置、旧库、旧账户密码或登录会话。

## 构建与初始化

镜像原始来源为 Frappe 官方 GHCR 和 MariaDB 官方 GHCR，固定 amd64 manifest digest。官方直连大层连续 EOF 后，使用南京大学公开 GHCR 缓存，逐一验证 manifest digest 与原官方内容一致、实际直连及层 HEAD；禁止自动代理回退。下载前还须验证服务器无代理/TUN及实际出口，上传须核对当前 SSH 连接在 Mihomo 中的 DIRECT 链。

构建上下文只开放 `sources.tar.gz`、`assets.tar.gz`、明确的 banking lock 覆盖和部署源码。先构建 `tls-runtime` 可独立申请证书；正式运行镜像再加入四个应用和锁定 Redis 版本。构建使用原生 Bench 安装 Python/Node 依赖，部署已有完整资源；首次 sites volume 包含明确的相对 assets 链接。

源码/资源 tar 的元数据统一为 uid/gid 0，解包后须在新建的镜像目录中明确恢复 frappe 所有权，并实际读取四个本地 Git tag。Python 官方文件 CDN 直连多次中断后，使用已验证的阿里云 HTTPS PyPI 源；关键 wheel 的镜像 SHA 与官方发布元数据一致。没有配置额外索引或代理回退。

部署源码上传后在服务器为 root 0600；镜像内明确设目录 0755、公开部署源码 0644、入口脚本 0755，使 frappe 可以读取初始化与 seed 脚本。只修改镜像中的公开源码权限，secret 文件不复制进镜像。

`initialize.py` 仅创建不存在的新站；`seed_test_data.py` 使用官方设置向导及正常文档校验创建合成测试资料。失败时先检查初始化及 seed 的阶段记录、站点、数据库和容器退出状态，再决定后续动作，禁止直接 force 或重复未知结果的初始化。

IP HTTPS 证书使用 Certbot 5.4.0 和 Let’s Encrypt shortlived profile。本项目自己的 `jifeng-erp-test-cert-renew.timer` 每天 03、15 时检查，带最多 20 分钟随机延时和 Persistent 补跑；服务调用当前主机 Compose 中的 certbot，再重载 nginx。续期脚本依赖 `releases/8858743-r2/deploy`，保留此目录；更换发布目录时同步修订并核验服务。应用在可信 HTTPS 配置后公开。

## 验收范围

外网可访问和 A/B 数据互通是本次部署门槛。上线测试不代表旧系统全部业务已完成：当前菜单覆盖中仍有业务预览和部分实现，不能把它们作为正式可用功能交付。旧系统全部模块正式可用仍是总目标；模块差距和原生/GUI验收限制继续记录在 `docs/validation.md`。

工程提交不包含密码、私钥、站点文件、数据库、镜像或发布包。远程部署上传与 Git 远程推送是不同操作，本次未推送 Git。

## Docker 内存设置与维护

本机 `/etc/systemd/system/docker.service.d/jifeng-erp-test-memory.conf` 使用 `docker-memory.conf` 的公开设置；环境变量在新 daemon 进程中生效。`docker-daemon-settings.json` 是本机的 live-restore 设置，本次原 `/etc/docker/daemon.json` 不存在，按该文件新建；已有配置时合并该字段并保留其他键和原文件记录。

先验证 daemon 配置并 reload，再从实际 `docker info` 确认 LiveRestoreEnabled=true；需要重启 daemon 时保持版本、数据目录、存储驱动和网络参数，核 systemd 的 KillMode=process、Delegate=yes，保持 containerd。重启后比较容器 ID、PID、StartedAt、重启数及实际业务健康。Go 软内存限额的行为按 [Go 官方说明](https://go.dev/doc/gc-guide#Memory_limit)，live-restore 的条件按 [Docker 官方说明](https://docs.docker.com/engine/daemon/live-restore/)；发生异常先核本项目操作记录和当前状态。
