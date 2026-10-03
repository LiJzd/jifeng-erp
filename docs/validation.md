# 验证记录

## 2026-10-03 本地工具费用归集与结清验收（未发布）

模具、版种和建模档案共用 `tool_cost_sources` 及“实际费用”组件。整笔已提交费用须明确分摊、准确核平；金额使用十进制文本，客户端精确计算，参考价不参与记账。UUID 收据只创建草稿，原生 Form 审核后生效；未知写入结果保留 UUID 并只 GET 核对，不重放。明确原生权限/校验 4xx 拒绝解除核对状态，清空旧金额和来源版本后允许重新查询、填写和关闭。查询或权限失效会清旧结果，迟到 GET 不恢复旧金额。

实际结清从费用的 JE、GL、PLE 及原生付款来源完整核对；核销不是现金付款。部分付款仅显示费用整体已付/未付，未明确分配付款到各工具时不按比例或先进先出推算。全部付清才显示逐工具已付金额。分摊反审仅取消本分摊的生效绑定和保留回执；来源费用及原生付款的取消仍受原会计规则保护。父子表权限、Company User Permission、字段屏蔽和实际隐藏付款来源均核；DocShare 新建在普通、管理员及原生忽略共享权限路径全部拒绝，原会计权限未扩大。

31 项后端模型、4 项资源模型、实际组件 CJS 及 Ruff 通过。新建本机自有副本 `jf_tc_qa_20261003`，定向 reload 两个新 DocType 后 932→934；旧对象的全部行、结构、索引、序列、权限和非本元数据保持。原生 r1..r5 的真实接口失败均先完成独立全对象回滚核对再修复，没有重放旧写入脚本。r6 实际通过 15 门：120 费用的原生 JE/GL/PLE、70/50 审核、真实 share 拒绝、参考价变更不入账、40 部分付款与逐工具未知、隐藏付款不可读且响应不泄露、再付 80 完全结清、金额/重复拒绝、岗位及字段权限、付款后的费用取消保护、分摊反审不改财务、80→40→费用完整反审并保留历史及真实现金/银行费用。取消来源 JE 后，ERPNext 原生可能清掉已取消 PE 的 GL 对方引用；仅已取消且可读的确切 Reference JE、完整反转账簿核平才接受此状态。

业务试验全部回滚，独立新连接复核副本完整 934 对象、原隔离站完整 932 对象及双方配置精确一致。忽略证据 `tool-cost-native-smoke-20261003-r6/`，源基线 `salary-export-cleanup-20261003-r1/source-before.json` SHA256 `27e2cd036effd68c9e2af6ff8ed1d0a8175f77b9a9adeed099b9a11dee6d2d25`。33 项本应用资产（12 CSS/21 JS）及加载顺序已同步本机副本，未写外网。

随后只在自有 GUI 副本提交合成费用 120、模具参考价 9999、版种参考价 8888、两名限公司测试岗位，未扩原生权限；原站仍维护且调度关闭。Luna 在 A 的“模具→模具录入”实际查看费用来源 120 CNY、已付 0/未付 120，填入模具 70/版种 50 和说明，保存按钮启用；保存前 CUA 桥接断开，未点击。不能认定 A/B 保存、付款回读、完整 HTTP、真实并发或旧系统全模块通过。外网仍 `2ca4358`；本轮只读服务器核对八服务和实际提交，真实 SSH socket 前、中、后均 DIRECT，无上传/部署/业务修改。

## 2026-10-03 最新外网工资增量发布：2ca4358

实际业务提交 `2ca4358398cf750da31584fc31d3afaef48b30b6` 已更新两套共用测试库的外网入口。工程助手提交 `120c1b8` 单独定向处理 18e5835 的 931→932：先整体验证两个既有自有 DocType 的元数据没有漂移，再精准插入四个 Data 字段及子表 Button，仅调整旧 DocField.idx 和自有 DocType 的 migration_hash/modified/by；原生 updatedb 只同步两自有表，新空 Rule 才 reload。禁止重建既有 DocType 的字段与权限。41 项模型/失败门和独立原生副本实跑通过，副本已清理，源隔离站完整 932 对象与配置保持。

DIRECT 在系统/env 代理、Clash/TUN 运行状态及真实目标 SSH socket 前中后复核；553 文件 / 3,362,045 字节的冻结归档 SHA 为 `0aff14d96887ccd8c7bc70c140d466b90b89a4433f7de91b95bc760b4f094fbe`。上传 r1 的 SFTP 排他模式失败，只留下空文件；r2 上传 8 文件的 SHA/字节均吻合，但解包 Python 写模式失败，未写源码或触碰测试站。各次经新只读检查后，r3 单独完成解包和五阶段，未重传/盲重放。服务器三 MyISAM READ 锁贯穿完整四部件原生备份及验证；全部原 931 对象、6 序列值、业务/账簿/Auth/权限、旧列和索引/旧行投影保持，新 Rule 空且四新列均 NULL。Form4（含 Sales Order）、child 元数据和工资 Page 的实际新源/缓存核验后再次完整快照保持。只切换五应用源码挂载，不重建镜像或三个数据服务；配置按字节和属主/权限精确恢复，证书 timer/受管文件保持。

独立外网 HTTP 验收实际通过：可信 HTTPS、31 工作台静态资源逐字节/版本一致，正常测试账号登录 A/B 入口，原生 Sales Order/工资单/分摊/分类四个 Form 和子表字段、工资 Page 新源下发，2099 年空日期工资查询 0 行及正常带 CSRF 注销。没有下载实际工资、导入本地 202 张工资夹具、创建分类或过账。八服务 running、三数据服务身份不变。忽略证据 `salary-release-native-reference-20261003-r1/`、`salary-release-native-clone-20261003-r1/` 和 r2、`salary-release-stage-inspection-20261003-r1.json` 和 r2、`salary-release-control-20261003-r3-*`、`salary-release-external-verify-20261003-r1.json`；数据库/站点备份留服务器受限目录，不提交/下载。旧“未发布”章节保留历史状态。

首次 CUA GUI 桥接不可用，经一次 reset 后恢复 Chrome；Luna 实际界面验收完成：A/B 默认 QA 公司、日期 2026-10-01..03、21 个相同中文列、15 条/页；分别查 2099-12-30..31 均为 0 条，A→B→A→B 切换保留各自公司、筛选、页容量和空结果；两站分类设置打开原生 Rule 列表并按 QA 公司过滤为无匹配，未清筛选。A 的独立“工价调用”仍显示本地预览/0 条，不能当作原生工资表单历史工价 API 的实际验收或正式模块。正常界面用户菜单注销、确认后回到登录页，未使用人工接口退出。未查看工资行、导出、创建/修改分类或业务单、提交/过账；空查询不证明有数据时共享一致，reader GUI、实际工价读取及全部模块仍待验证。报告 `salary-release-gui-20261003-r1/report.json`，root 已读取报告核对验证边界。

## 2026-10-03 最新本地工资历史导出验收（未发布）

`salary_history_export.export_salary_history(context, columns)`为GET只读下载，context沿用公司、开始/结束、严格bool指定日期、四种日期口径、关键词；columns为31个固定中文列名的非空、唯一、有序JSON数组。复用工资历史完整200分批两遍读取；生成后用独立、非root、当前primary的只读事务重读完整来源及权限，比较成功且rollback/close完成才发布XLSX，不拼查询页或旧缓存。工资历史sheet只含所选旧21默认列/10可选列，另有口径说明及币种/分类批注；全部单元格显式文本，保留合法零和准确十进制字符串，公式样文本不执行，Excel行数及UTF-16单元格上限整份拒绝，不截断。

保留固定Frappe原生Exporter的`can_export(Salary Slip)`与逐单read/get_list、父子字段/mask守门，包括System Manager原生can_export语义；Company/Employee/Currency/SalaryComponent/Rule保持read/select、字段和版本核验，不要求配置或联系人整表export。仅实际输出的非空分类金额所用Additional Salary/Jifeng Wage Entry另核can_export和准确可读来源。分类或来源未知时保留工资单及原生总收入、整侧分类留空，不补零、不假称实付；银行值与内部来源编号不进入文件。classification_mode为空按既有component语义处理。

root实际65项工资历史模型、共享组件CJS、A/B363派发和31项资产检查通过。本机独立clone prepare r2通过，仅经Unix socket复制到新隔离副本，原站932全物理对象及配置保持；该副本有202张经HRMS真实insert/submit提交的合成工资，未在原站或外网提交。导出原生r3的9门实际通过：完整202单与31列文字/未知/零/无银行及内部ID、所选列顺序与全集、日期/关键词/空结果、read/select reader与manager拒绝导出、System Manager原生导出能力、公司UP拒绝、SalaryonlyExport岗位可导出原生总收入但不可导出无源export的分类金额、非法/重复/空/私密列拒绝。独立fresh932对象、配置、源文件pins及调用者状态保持；这是串行原生验收，不证明GUI或真实并发通过。

本批发现临时CustomDocPerm未显式设置的unused rights会沿原生默认成为1，已将自建reader夹具全部unused rights明确为0；9门按明确read/select夹具及SalaryonlyExport合成岗位核验，没有给予任何业务角色新的权限。旧查询30门保留为当时记录，不据其临时reader夹具推定裸HR岗位或默认导出权。修订后的自建fixture validator已受控复制并保留备份，未重跑整套旧查询30门。忽略证据 `salary-export-clone-20261003-r2/`、`salary-export-fixtures-20261003-r1/`、`salary-export-native-suite-20261003-r3/`、`salary-export-financial-actor-20261003-r3/`、`salary-export-validator-copy-20261003-r1/result.json`；含私密夹具，不能提交、公开或重放一次性助手。

GUI r1的CUA `Page.navigate`与标签绑定调用超时，标签停在about:blank；未输入凭据、登录、查询或触发下载，没有页面截图或A/B操作通过证据，见忽略报告 `salary-export-gui-20261003-r1/report.md`，保留失败历史。修复serve故障后的GUI r2已在A实际查询202条、每页15条共14页，验证列隐藏/恢复、结束日期改到7月20日后201条及回航保留。单次导出显示成功反馈和Chrome下载气泡；CDP下载事件等待超时。root独立只读核最新本机文件为44,330字节、202行21列，七项未知旧口径列为空白，公式样字符串为字面文本、无超链接和银行内容，见 `salary-export-gui-20261003-r2/root-file-verification.json`。通过Frappe左上应用菜单正常注销、确认后回到登录页，没有改用人工接口退出。B只核默认公司、日期及21列，未做同范围202条查询；reader GUI因安全凭据输入能力限制未执行。以上证明A已实际导出文件落地和正常界面退出，不是完整A/B/岗位GUI通过。

临时副本清理r1通过：关闭自有服务进程，先按字节恢复克隆配置，再仅移除自有QA库、用户及站点；原隔离站完整932对象的before、fresh after及clone prepare基线一致，源码配置保持。Bench保留本批三个导出相关生产源文件及上述受控validator修订；清理证明不作为修订后30门重跑。忽略证据 `salary-export-cleanup-20261003-r1/result.json`。旧分析仅观察到三张空数据图，金额来源未知；格式1–7已观察但未复刻。旧打印、实际付款/考勤/合计口径、HRMS工资提交与规则发布/取消并发、其余GUI和全模块验收继续。

实际HTTP r1在发网络请求前因WSL loopback路由断言未满足而停止，保留失败历史。修正后threaded克隆服务r3已真实密码登录并核已登录身份；GET查询返回15条、total=202、14页，GET导出默认21列/202行、44,333字节，均HTTP200。全TCP peer/source为本机`10.255.255.254:18006`，连接前/中/后路由均type local、dev lo、table local；requests不用环境代理，没有重定向或代理回退。证据 `salary-export-http-20261003-r3/result.json`。该r3总体passed=false：driver以GET退出被原生框架403拒绝；r4改POST后仍400，原身份仍在，driver退出未通过，原因未确定。登录/查询/导出已验证，不能称整套HTTP driver通过；其失败不覆盖上述独立GUI正常界面退出结果。

本地363入口仍233formal/14partial/98preview/13navigation/5tool，工资历史保持partial；正式映射不代表模块完整。外网18e5835不含a13d824、c6d2cbd或当前导出，未部署本批。最新外网只读预检通过，八服务运行、三数据服务身份及受管配置/保护文件保持，SSH实际连接前/中/后均DIRECT；无上传、切换或SQL写入，见忽略证据 `salary-history-release-preflight-20261003-r1.json`。剩余完整包的只读审计见 `remaining-first-release-priorities-20261003-c6d2cbd.md/json`；下一包工具费用来源、准确金额分摊与实际结清尚未实现。下节保留导出前查询验收历史。

## 2026-10-03 本地工资历史结算查询与旧页纠错

gpt-6-luna重新只读取旧站，未查询真实工资、保存/删除、打印或导出。工资录入的姓名/工序双击和多人/多工序提示已确认，现有正式操作台已经保留对应触发；工价调用可见“未勾选默认调用历史工价”、调BOM/超数可保存/备注/操作/日期列和保存入口，默认价范围仍未知。工资历史默认本月首日至今天、指定日期勾选、空关键词，默认21列为说明/姓名/部门/底薪/应出勤/实出勤/出勤工资/计时/计件/合计/房补/全勤/应发/水电/社保/罚款/借款/实发/开始/结束/类型。历史分析为另一URL的部门占比/分组占比/工序金额图。因此移除两处计薪工序分析错误映射，历史接新partial查询，历史分析恢复preview；不是缩减第一版交付要求。

同轮产品镀色和订单镀色可见9行同一数据集、相同13数据列和六字段新增窗，订单页新增标题仍为产品镀色新增。支持共用界面/数据集的推断，未证明同一物理底表；本批没有据此新增产品镀色正式映射。忽略证据 `legacy-wage-ui-evidence-20261003-r1/`、`legacy-erp-ui-round2-20261003-r1/`、`remaining-business-audit-20261003-a13d824.md/json`。

新历史服务只读已提交 Salary Slip及保存明细，按公司、四种显式日期口径和关键词完整200分批前后复核，再分页15/25/50/100；Native公司/员工/read/select、父子字段/mask与来源版本均核。DTO不含银行信息，不调用原生全量无逐单权限的Salary Register查询。旧底薪/应出勤/实出勤/合计/说明/类型/实发口径未确认则留空，详情中的未折算默认额/计薪天数/净应发明确区分；应发列明确标原生总收入。查询阶段尚无新导出API，旧打印/导出按钮当时不可用；本轮导出增量与未扩HRMS权限的边界见上节。

工资项目归列由新自有 Jifeng Salary History Rule 明确公司/准确项目/收入或扣款/生效日期及归类依据。HR User只读，HR Manager/System Manager可保存及发布；已发布配置不能修改、改名或删除，被使用的规则不能取消。首次对既有历史归类须独立明确发布并写依据、发布人、时间和版本；后续追加未来版本，按已保存工资期结束日选择。混合计件/计时/附加费只沿准确 Salary Detail→Additional Salary→已批准 Wage 单、同公司/员工/币种/日期/金额闭合拆分；同单重复AS或多个AS重复同Wage整侧未知。收入或扣款一侧有缺分类、准确来源不可读或核不平，该侧金额全部null，已保存原生总额保持。核心工资项目后来合法改型时也保持总额、整侧未核实，不伪称分类完全脱离可变核心来源。

root实际42项服务/规则模型、实际组件及bridge/原生Rule表单中文label与readonly的CJS、A/B363派发、2资源模型通过。固定Frappe原生保存先锁已存Rule行，已修显式发布同样Rule→Company锁序；DocStatus为int子类的原生兼容门通过，bool仍拒绝。页面默认21列、日期、本地列设置、未确认与合法零、decimal字符串精度、native详情/分类tab、迟到结果、未同步Company输入、筛选变化和dispose均有协议门；模型不计GUI/HTTP通过。

隔离站一次定向reload新自有Rule，完整931→932，仅新增空表与本DocType元数据；原931对象业务/Auth/权限/序列/账簿/结构索引和配置保持，未full migrate或reload核心HRMS。资源r1发现有效app_hooks旧缓存后拒绝，fresh932保持；r2仅重复制两项锁序/DocStatus修复并定向删除该站app_hooks键，20JS/11CSS共31项字节/指纹、原生Rule getdoctype以及client_cache/Redis value缓存JS一致，fresh932/配置保持。不是GUI或HTTP证明。忽略证据 `salary-history-native-schema-20261003-r1/`、`salary-history-native-assets-20261003-r1/`、r2；所有一次性助手不得重放。

原生业务r1真实工资结构、分配、节假日、已批准混合计件单→Additional Salary→已提交工资条通过，首次查询被原生字段函数不列出Table拒绝；已修为核父单原生读权限、Table元数据/权限级别/字段mask及Salary Detail值字段权限，模型同时排除Table投影，不扩大业务岗位权限。r2通过27项功能门后，202张分页夹具因2025财年未配置被HRMS拒绝；另暴露回滚后的Administrator角色缓存仍含临时Role。两次失败分别独立fresh932/配置/源码保持，未重放助手。

新r3只复制冻结validator，先核两公司既有财年，使用2026-01-01至07-21的202连续日期，经真实HRMS逐单insert/submit；不新增财年或SQL伪造工资。开头发现旧角色缓存与持久Role不一致，定向失效原用户roles并由原生get_roles重建核验；finally仅失效原用户/Administrator角色键，七类调用方状态逐项严格比较及哈希诊断。30项实际门全部通过：保存总额/分类混合来源、无银行DTO、公司/员工/币种/项目User Permission、父子permlevel/mask/元数据拒绝、规则发布Version/唯一/不可变及真实savedocs Cancel、源原生变更与权限变化、重复来源守卫、202单完整200+2扫描和100/100/2分页、显式日期及过期详情、全量回滚。临时岗位是在HR User/HR Manager上追加自有read/select reader Role，不证明裸HR User已具备全部来源读取权；规则及业务岗位原权限没有扩大。前后独立fresh文件SHA均为 `27e2cd036effd68c9e2af6ff8ed1d0a8175f77b9a9adeed099b9a11dee6d2d25`，全部932对象、Auth/权限/账簿/序列/结构索引、配置、源码及callbacks/local/user/in_test/roles/事务控制/权限响应保持。忽略证据 `salary-history-native-suite-20261003-r1/`、r2、r3；一次性助手不得重放。

该证明是串行原生函数和权限实验，GUI、HTTP传输、HRMS工资提交与规则发布/取消并发、旧实际付款/考勤/合计口径、占比图和全模块正式验收仍待完成。原生Salary Slip提交没有本批共享Company锁，不能以模型锁序声称并发通过。本地363入口实际VM计数为233formal/14partial/98preview/13navigation/5tool。此批和a13d824历史工价未发布，外网仍18e5835。

## 2026-10-03 本地历史计件工价与持久引用

工资操作台单道/多道录入、原生 Wage Entry 草稿及 Wage Allocation 员工行共用显式历史选价。GET 使用原生权限列表按公司、员工、工序、单位、日期及已审核/正数计件数量筛选，完整200分批复核后分页，合法0工价可选；DTO不含计时价、工资项目或备注。选择同时核准确ID、微秒版本和工价，目标及原生来源权限/字段/mask保持。保存两项只读Data引用（不建立旧工资取消依赖），首次保存/审核在当前Job Card锁之后复核并单次历史Wage NOWAIT；无自动重试或保存。批准后保留准确工价、引用和来源绑定，后来取消旧工资不重算。取消路径独立守卫；分配逐行引用完整传播到工资。修改单先保存草稿，再选价。

163 项工资相关Python模型（工资操作台/历史工价/引用/分配/来源权限/分析/原生浏览校验/计薪/计件人员调用方）通过，共享选价CJS及操作台32项协议检查通过；Ruff py313、Node语法及diff检查通过。字段安装只reload两个既有自有DocType；两表新增两Data列及分配Button元数据，完整931对象独立审计确认原数据/索引/权限/Auth/账簿/序列保持，未full migrate或改核心生产/HRMS结构。

实际原生r1夹具单位错误；r2仍用了不存在的planned_uom，真实控制器字段stock_uom修正后r3通过。三次分别保留证据，失败后各独立fresh931全同。r3实际HR Manager/HR User/company及JobCard UP、202合法历史工资完整分页和0价、无写查询、准确scope/version/rate、native草稿Version/提交、人工改价带旧引用拒绝、20行重复来源前置拒绝、双人批准分配、取消篡改拒绝、旧来源取消后重新生成/提交工资、native amendment清引用及字段readonly/父子mask共13功能门；finally全物理回滚门合计14。前后fresh文件SHA同为 `6b5a7efad34bbb93023c5c52ff0ffa755e445bdcef3ce4f3f527f798b1ef23d5`。

r4 加入真实白名单 `frappe.desk.form.save.savedocs` 的 JSON Cancel 入口直调；有/无历史引用的工资和分配逐项金额/日期/工资项目/来源及员工行增删替换拒绝，正常取消独立完整父子 SQL 列（只排状态和修改时间/人）保持。14门与fresh931全同通过，既有配置没有 Pulse key，真实 is_enabled 为 false，未改遥测或配置。此为真实原生函数入口，HTTP传输为 false。进一步复核发现回执/修改来源及只读姓名需要纳入取消快照，随后在r5补验通过；此前r3/r4的20行均携带同一个JobCard，实际只在prepared/insert前被重复来源拒绝，不能据此宣称前19条插入后回滚。

r5 的14门全部通过。取消快照新增只读姓名、批量请求key/fingerprint与修改来源；真实正式Workbench单行先保存并提交，清空/替换回执拒绝，合法取消后batch_status保持原name/key/fingerprint及docstatus2。两个真实修改单submit后修改amended_from拒绝，普通取消保持。真实 apply_fieldlevel_read_permissions→as_dict 的父/子权限裁剪JSON取消通过，已有非空隐藏资格摘要恢复后完整SQL列不变，没有应用侧跳过或特判。20行改为19合法纯计时与最后1条明确来源错误引用；实际异常栈证明前19个Document已insert完成、正在处理第20个且进入引用校验，最后仍new/in_insert；错误后请求键与全SQL回滚。最终全部931对象fresh/config和14份复制源码逐SHA保持，前后fresh文件SHA均为 `6b5a7efad34bbb93023c5c52ff0ffa755e445bdcef3ce4f3f527f798b1ef23d5`。

双连接r1因站点账号无PROCESS拒绝，未进入锁协议，fresh931保持。单次现有root无密码Unix socket只读握手证明同一实例与PROCESS有效后，经独立审查的新r2通过：准确A持JobCard、B持Wage并等待A，INNODB实际等待证明；A单次NOWAIT立即拒绝，rollback后B完成退出；新的独立连接释放后对照可取锁。没有更改权限、SQL业务写、Redis或用户；所有连接/线程退出，fresh931/config同。这是准确原生SQL争用诊断，不是完整应用并发负载验收。

资源r1实际响应源正确，缓存读取误用了hash API而TypeError；无SQL变更且fresh931全同。按实际Frappe FormMeta代码改为client_cache/Redis value键后r2通过：有效hooks 19JS/10CSS、29项源码字节/指纹/静态路径、Wage Entry和Allocation原生getdoctype响应与两个实际doctype_form_meta缓存JS一致；fresh931保持。此为本地源/缓存检查，不是HTTP或GUI验证。

忽略证据目录：`wage-rate-native-schema-20261003-r1/`、`wage-rate-native-suite-20261003-r1/`、r2、r3、r4、r5、`wage-rate-native-lock-20261003-r1/`、r2、`wage-rate-native-root-handshake-20261003-r1/`、`wage-rate-native-assets-20261003-r1/`、r2。本轮gpt-6-luna取旧工价页面任务创建被 `agent thread limit reached` 拒绝，未进行浏览器操作/预览。默认价范围已询问未回复，未推定旧默认保存规则；工价独立菜单及363入口状态保持。此增量未发布，外网仍为 `18e5835`；GUI、完整旧操作习惯与全模块正式验收继续待完成。

## 2026-10-03 客户选择、工序与镀色实际外网发布

测试入口为 [A · 开源优化版](https://116.62.241.148/desk/jifeng-modern) 和 [B · 旧界面版](https://116.62.241.148/desk/jifeng-legacy)，使用同一 Frappe 站点及测试数据库。测试账号仅在本地私有 `output/ERP外网测试账号.private.txt` 交接，不进入公开仓库。这里证明认证页面与资源访问，未做两套业务写入比对或 GUI 验收；下方旧版本和“当时最新”的批次记录已被本节取代。

外网 A/B 已由 `4d71c35` 切换为冻结业务源码 `18e5835cfda4171ec691d5e701c8f42018b5448c`。公开候选含 532 个文件，归档 3,099,538 字节，SHA256 `64474ad8769f76a899eb8df32eb6b3f44646af60460c71d8b49105ee82ede236`。本地已有客户选择15、工序22、镀色19原生岗位门；本轮发布助手共89模型通过。默认 Windows pytest 临时目录权限拒绝后使用工作区新独立目录通过，未修改业务实现绕过测试。

独立本地只读参考采集两张已安装空表结构/索引与元数据，931物理对象 fresh 校验保持；真实归档逐字节、28 hooks、Sales Order 专用脚本及零既有 DocField 修改的纯审计通过。初次本地打包对 CRLF/LF 的 raw SHA 比较拒绝，未产生或上传归档；后续分别核实际原生 raw 源及根仓库/冻结 Git 的规范化源一致，生成独立 r2 归档。归档和两轮助手批量传输均核环境/系统/WinHTTP、实际 Clash/TUN，并对每个准确 live SSH sourceIP/port/目标22连接前、回调及后确认唯一 DIRECT，无代理回退。

prepare r1 因 `compose_service_set_changed` 拒绝，发生在停服和SQL前。独立只读诊断确认 `tools` 配置实际含14服务：八个运行服务、四个原有初始化/证书工具及两个发布工具。修正严格清单并逐项保护四原工具配置；保留 r1 全部记录，使用全新 `incoming-r2`、`incremental-control-r2`、`private-backup-r2` 和覆盖文件。计划文件所有权已核1000:1000/0644，公开内容SHA保持；助手审核提交为 `fa560e8`，与冻结业务源码独立。

新的 prepare/quiesce/metadata/verify/start 均一次通过。quiesce 保存原站点配置，进入维护并停五应用；三个数据服务保持完整身份。原生数据库、配置、公开/私有附件四部件备份仅保留服务器，gzip/tar、完整929物理对象覆盖及SHA通过；三个MyISAM独立READ锁贯穿完整dump，六个sequence状态纳入证明。数据库备份前后及fresh一致。未执行全站migrate、seed、核心Operation/BOM/Work Order/Job Card reload、镜像构建或拉取。

仅新增空 `Jifeng Plating Color`、`Jifeng Order Operation` 及自己的元数据，完整929→931校验通过。before SHA `744614d8d834f3160c2cc9dbd069cffb8804f2c94afda8976408019c1e826772`，after-apply/verify/cache 均为 `bce45abaf3645607ee4ada211fe0e2df55522c7583e9f0405f49aa64fbf192fa`。有效hooks为18JS/10CSS；生产缓存实际核 `app_hooks` 和 `doctype_form_meta::Sales Order`，专用客户脚本SHA `fb5f17e51a63c42a21addf34f5ce6f13ac5941ab68466d2a7e69ee855543f4c4`，完整FormMeta JS SHA `0952a700b1a899de60289bb3816558b202a56604fa1324db44ec55e61ec98c00`，fresh SQL证明保持。

应用切换后独立SSH确认八服务running/restart0/noOOM、db healthy、五个新源码只读挂载和实际后端Git18e；三个数据服务的完整身份及保护配置保持，原site_config字节/uid/gid/mode恢复，维护解除，证书续期timer active。可信TLS登录页/ping、28个CSS/JS逐字节、实际测试账号登录/A/B认证页面、Sales Order getdoctype专用脚本及两新DocType元数据HTTP下发均通过，检查会话带CSRF退出。实际HTTPS连接前、发出、响应头及正文块均核DIRECT。接口检查未进行业务保存，账号/会话仅RAM，不输出或提交。

本轮Luna子任务新建及恢复被 `agent thread limit reached` 拒绝，未进行真实浏览器操作；HTTP/FormMeta不计GUI通过。旧窗口习惯、效果1～4生产规则、97个预览及全模块正式验收继续待完成，目标保持active。当前363入口为235正式映射、13部分可用、97预览、13导航、5工具，映射不等于业务验收。以下“未发布”属于当时状态。

证据均在仓库忽略 `output/`：`order-release-native-reference-20261003-r1/`、`order-release-audit-model-20261003-r1/result.json`、`erp-source-18e5835-r2/`、`order-release-preflight-20261003-r1.json`、`order-release-upload-20261003-r1.json`、两轮 `order-release-helper-pins/helper-upload`、`order-release-compose-diagnostic-20261003-r1.json`、各 `order-release-*-dispatch/poll-20261003-r2*.json`、`order-release-external-verify-20261003-r1.json`。服务器完整SQL证明、备份和失败日志位于 `/opt/jifeng-erp-test/releases/18e5835-source-r1/` 私有子目录，未下载；未知结果先读回执，禁止重放。

## 2026-10-03 外网测试站只读交接复核

本轮可信TLS HEAD `/login` 与GET `/api/method/ping`均200/pong。检查环境/系统/WinHTTP及实际Clash/TUN状态，并在每个实际连接的准确sourceIP/port、目标IP/port前中后匹配唯一DIRECT链，没有代理回退。未输入账号、登录/退出、业务保存、下载备份或再次发布。

SSH运行检查r1远端固定断言失败，r2定位旧系统Python不接受`subprocess.run(text=True)`，Docker命令尚未运行。失败独立保留，r3只读采用既有助手的`universal_newlines=True`及兼容字符串处理，`docker ps --size=false`/限定inspect/源Git读取通过。实际八服务running、noOOM、RestartCount=0、数据库healthy，五应用只读挂载匹配预期release；源码仍`4d71c354d3f3f281d21d3d16b496bb6e5434e655`。该结果不证明长期负载稳定或完整模块业务验收，没有重启daemon/业务服务或写远端配置/SQL。

证据均在仓库忽略目录：`order-customer-external-health-20261003-r1/proof.json`为实际HTTPS与失败SSH，r2为兼容诊断，r3为成功运行版本核验。下面客户选择属于已提交本地`071cecb`，它及工序/镀色未发布，外网保持此前4d。真实A/B业务GUI仍沿用此前对应版本的已说明范围，本轮不新增GUI通过认定。

## 2026-10-03 最新本地增量：订单客户选择

新增只读 `order_customer_selection`：当前公司、可写原生 Sales Order 草稿、在用订单客户 Profile 及关联 Customer，精确原生ID及两来源版本。无档案创建权限的销售人员也可读取其有权选择的档案；不复用要求create权的档案维护接口。原生get_list投影、字段mask、User Permission与docstatus/版本守门，查询只统计实际可选择行，首尾重新核对完整候选分类；没有1000条截断。GET-only选择不save/insert、不匹配同名，不复制地址、手机或折扣。

32 后端模型、客户端CJS、JS语法和Python3.13 AST/Ruff通过。客户端只调用一次原生`frm.set_value(customer,准确ID)`，不自动保存；窗口关闭、公司/客户/订单/版本变化拒采用迟到GET。独立复核发现setter等待期间表单权限变化仍可能误报成功，现post-set再次检查canEdit与新旧单状态，补三种延迟锁和先赋值后失败回归；不回写旧客户，失败明确关闭窗口后核对原生草稿。模型不是实际GUI。

| 步骤 | 实际结果 | 忽略目录证据 |
| --- | --- | --- |
| 源镜像前置r1 | 错列不存在的customer_dictionary.py依赖，在创建intent或写源前失败；未连SQL/网络，未重放。 | `order-customer-native-source-preflight-20261003-r1.json`、原r1 helper |
| 源码r2冻结 | 改为实际customer_type.py依赖，新helper仅镜像API/验收器/订单JS三文件，39原依赖保持；原JS字节备份。pins SHA `29d24b856fd8c4788b5188f7babbd4edfd6a5419d2a52d679d5dcdbd33c22149`。 | `order-customer-native-source-20261003-r2.json`及同名目录 |
| suite r1 | 15项真实原生岗位门通过，所有合成业务/权限/元数据/审计夹具回滚；独立fresh931对象内容、结构、索引全同，没有reload/DDL/commit。 | `order-customer-native-suite-20261003-r1/`，`passed=true`、`fresh_unchanged=true` |
| 关联来源静默过滤 | 原生Customer-only User Permission下两个Profile实际get_list可见，选择结果仅一个；成功查询message_log、flags.error_message及真实permission_debug_log未变化。没有扩大角色或吞掉带私有名称提示的权限错误。 | suite `evidence.quiet_linked_source_filter` |
| 实际原生表单源 | 仅失效隔离站`doctype_form_meta::Sales Order`键，有效doctype_js及原生FormMeta完整新JS/再次缓存读取均通过；旧缓存不含新脚本已明确记录。28主资源SHA及有效18JS/10CSS保持，fresh931全同。 | `order-customer-form-meta-20261003-r1/`；不是HTTP或GUI证明 |
| GUI连接r1/r2 | gpt-6-luna首次visible:true在子线程不支持，文档visible:false修正后明确IAB不可用。没有登录或业务操作，无目标页面证明；此前本轮Chrome库存fetch失败。不绕过原生审批，不启动无可用连接的8000预览。 | `order-customer-gui-connection-20261003-r1/report.md/json`及r2 |

真实15门包含纯Sales User/Manager读选、精确同名客户、关键词/分页/停用/公司/种类、两来源版本、档案/原生客户记录权限、Sales User新单保存及已有草稿换客户的原生Version、订单只读岗位/记录权限/旧版本拒绝、客户readonly与三个真实mask、可选自编屏蔽后DTO和搜索均省略、Guest/严格上下文。Sales User + Stock Manager提交后生成一张准确Sales Order Item来源流程单，库存数量/要求/包装保持，重复生成没有额外写入；源要求更新只标识来源变化，不覆盖人工快照，订单取消后标Cancelled。

不能据此称完整生产或工资过账、原生客户选择GUI、旧效果1～4生产规则、档案来源历史持久化、全模块或并发通过。订单只保存准确nativeCustomer，档案来源版本尚未存入订单。完整931物理保护含六InnoDB序列和独立三个MyISAM READ锁，原生文件、Auth、角色、账簿、原业务内容保持。没有本轮schema新增、worker/scheduler或外网更新。本地363入口状态仍235正式映射/13部分/97预览/13导航/5工具，外网继续4d71c35；本批及工序/镀色未发布。旧四组效果关系的用户问题仍待答。

## 2026-10-03 最新本地增量：共用订单工序

原站依据为 `output/legacy-order-operations-20261003-r1/report.md/json`：8列顺序/工序/备注/操作/日期/修改人/修改日期/删除，六行后有空白行，前三格单击编辑；没有标准新增/保存工具栏。没有输入或提交，旧保存触发未知。新组件保留结构，明确行内保存/取消，已有原生工序名只读。使用全局 ERPNext Operation，独立 `Jifeng Order Operation` 保存页面顺序和永久创建回执；不改原生工序权限、表结构或BOM/生产行序。原生 description 空值采用名称是当前原生规则。

31 后端模型、2 资源模型、工序/A-B/订单词典/镀色四份客户端协议及JS语法检查通过；Python3.13 AST/Ruff通过。新增回归覆盖查询在飞时保存成功不被旧响应覆盖、各行差异独立、TimestampMismatch明确拒绝后保留草稿并能只读核对；确定版本冲突即使当前字段已相同，也必须明确采用，不误报本次保存成功。模型不能代替实际GUI。

| 步骤 | 实际结果 | 忽略目录证据 |
| --- | --- | --- |
| 原站再取证 | 真实授权登录下仅观察行内控件；没有新增、删除或保存，退出再读仍六行，不推定自动保存。 | `legacy-order-operations-20261003-r1/report.md/json` |
| 源码冻结及镜像 | 10运行文件、16原依赖，旧字节保留；本地文件镜像，无网络。r1与每次修正独立保留，r4最终pins SHA `6f3e8c64483e5c6da449ece03810254ffb7d9fdc40572967e656897e456c7b85`。 | `order-operations-native-source-20261003-r1.json`及r2/r3/r4；同名源备份目录 |
| schema r1 | 一次定向reload+commit，930→931，仅新增空排序/回执表与本DocType元数据；六元数据表目标外及所有旧业务/Auth/账簿内容、结构、索引全同。 | `order-operations-native-schema-20261003-r1/` |
| suite r1拒绝及独立诊断 | 原生新建把total_operation_time从None规范化为float，保护拒绝；诊断只返回变化字段名和类型，真实创建后回滚，两次独立fresh931全同。修正仅新Doc/空子工序/该值None时预置原生0.0，再冻结保护；旧工序保护保持。 | `order-operations-native-suite-20261003-r1/`、`order-operations-native-creation-diag-20261003-r1/`、`order-operations-native-source-20261003-r2/` |
| suite r2/r3验收器修正 | r2通过两制造岗位与公司共享后，未保存Link文档缺原生_action使验收器报错；r3通过11门后，原生关联User Permission过滤回执为missing与原期望抛错不符。分别只修验收器合法save action及严格missing/null/输入UUID契约，每次fresh931全同。 | `order-operations-native-suite-20261003-r2/`、r3及对应r3/r4源码备份；不是把业务失败直接当通过 |
| suite r4 | 22真实原生门通过，全部业务、审计、临时权限/元数据夹具回滚，独立新连接931物理对象内容/结构/索引全同。 | `order-operations-native-suite-20261003-r4/`，`passed=true`、`fresh_unchanged=true` |
| 资源及有效hooks | 18JS/10CSS共28资源，镜像与本地逐字节/SHA及URL指纹相同；预览中仅失效app_hooks键，实际有效hooks逐URL同源码，缓存操作fresh931全同。没有HTTP资源或浏览器加载证明。 | `order-operations-native-assets-20261003-r1.json`、`order-operations-hooks-cache-20261003-r1/` |
| GUI r1/r2未验收 | gpt-6-luna用允许的CUA读取Chrome库存，返回Unable to load browser request-header policy；一次文档支持的IAB创建返回Browser is not available: iab。未打开ERP、登录或进行草稿编辑，不能计A/B可见列、取消、共享保存、截图或console/network通过。 | `order-operations-gui-20261003-r1/report.md/json`、r2；r1父目录原件保留、逐SHA复制，见`order-operations-gui-report-copy-20261003-r1.json` |
| 关闭及独立后验 | 按本轮PID/tick关闭预览，8000关闭，配置原字节/owner/mode恢复；整段预览与缓存后至关闭两个比较均931全同，业务/元数据/Auth/角色/账簿内容、结构、索引保持，无worker/scheduler或外网操作。 | WSL私有`order-operations-preview-20261003-r1/stopped.json`、`order-operations-gui-closure-audit-20261003-r1/` |

r4实际涵盖纯Manufacturing User/Manager创建、描述和排序的原生Version、不同Company User Permission共读全局工序、原生Link验证与实际search_link、顺序单独修改保留原生父子全行及原生Version、备注修改保留其它属性、两个版本失效原子拒绝、UUID原始摘要/重复POST拒绝/只读回查、原生改名传播、排序原生表单禁止伪造/改绑/删除、103条同序分页、Operation及映射User Permission、真实mask/readonly、先保存原生后排序Select/mandatory失败完整回滚、陈旧总工时的非请求变化拒绝、Sales User/Guest拒绝及严格payload。

生产/BOM/Job Card/商品工艺和工资父子表、元数据、GL/SLE保持，原生文件SHA保持。Link验证使用未持久化引用文档及真实选择查询；工资operation实际是Data，不能称完整工资或生产链路通过。改名使用原生支持的rebuild_search=False，没有搜索重建验收。物理核验包含六InnoDB序列，READ ONLY/RR一致快照与独立连接的三个MyISAM READ锁；未全migrate/seed、扩原生权限或外网写入。这里只是串行实验，没有多事务并发或负载证明。

当前本地363入口为235正式映射、13部分可用、97业务预览、13导航、5工具，28资源。工序保持部分可用，旧自动保存、此页改名/删除、历史迁移及完整GUI业务验收继续；外网仍4d71c35的11部分/99预览，本批与镀色未发布。下方较早批次保留当时数字，以本节为当前本地状态。

## 2026-10-03 最新本地增量：订单镀色

原站结构依据为`output/legacy-plating-color-20261003-r2/report.md/json`：订单/产品两个URL外观一致，但没有业务查询或共用底层证明；13列和“产品镀色新增”六字段已观察。编号灰色且AX不可编辑，色系是普通文本，代码占位为“代码生成条码时用”；旧必填/保存/编号/简拼算法未验证。本批只绑定订单入口，产品入口仍预览。当前规则要求名称非空，编号采用公司/类型独立服务器计数，简拼只读；代码/色系保留原文，不改Customer、Item或Sales Order。

32后端模型、2资源模型、镀色/订单词典/商品词典/客户词典/客户档案/A-B六份CJS及Python3.13 AST/Ruff通过。客户端检查13列/六字段、readonly编号、代码/色系原文、scope/类型/分页、mask/readonly payload、保存冻结和UUID回查，无重复POST。额外只读审查实测发现resolve差异表和完整性检查仍硬编码旧四字段，可能在代码屏蔽时允许采用未完整核对的值；现两处与editorFields/editorLabels同源，补仅代码/仅色系冲突展示、两字段分别缺DTO或metadata拒采用的回归，六份CJS再次通过。这些是DOM/协议模型，不算真实浏览器。

| 隔离步骤 | 实际结果 | 忽略目录证据 |
| --- | --- | --- |
| 前置及源码冻结 | 11运行文件、10原不变依赖，原字节保留；无网络。 | `plating-color-native-preflight-20261003-r1.json`、`plating-color-native-source-20261003-r1.json`（SHA `21610ded9f98f2ddd6c810b47029a92de1589794f5f9bf9cf6f847b64cb2d3b8`）及同名源码备份目录 |
| schema r1 | 一次targeted reload+commit，929→930，仅空Plating Color表与本DocType元数据；六元数据表目标外内容/结构/索引全同，全部旧业务/Auth/账簿保持。 | `plating-color-native-schema-20261003-r1/` |
| suite r1 | 19实际岗位门通过，全部业务与审计回滚；独立新连接930个物理对象内容/结构/索引与suite前一致。实际主管创建、原文/Version、原生表单身份、Sales User拒写、公司/记录权限、103分页、停用、真实mask及readonly、必填失败完整回滚、UUID原始摘要与重复回查、stale、Guest及原生删除拒绝均通过。 | `plating-color-native-suite-20261003-r1/`，`fresh_unchanged=true`；native Customer/Item/Sales Order及GL/SLE保持 |
| frontend r2 | 只复制上述两行resolve修正后的共享前端文件；旧字节与新pins独立保留，r1后端冻结文件保持，不覆盖原native证明。 | `plating-color-frontend-source-20261003-r2/`；无SQL/网络 |
| 资源 r1/r2 | native实际hooks和26资源字节/内容指纹与各时本地一致；r2覆盖最终前端修正。 | `plating-color-native-assets-20261003-r1.json`、`plating-color-native-assets-20261003-r2.json`；无DB/浏览器HTTP |
| GUI r1失配 | 两套实际显示旧11列“本地交互预览”，新增为8字段“新增·镀色”；未保存，不能计目标界面通过。 | `plating-color-gui-20261003-r1/report.md/json`；已有QA会话，目标公司筛选器未出现 |
| hooks cache r1 | 查明Redis仍缓存14JS，仅失效隔离站app_hooks这一键并重读为17JS/9CSS，逐URL同当前源码；独立fresh 930对象与操作前全同。 | `plating-color-hooks-cache-20261003-r1/`；无full clear-cache、SQL写入或外网操作 |
| GUI r2 | 缓存修正后A/B重新加载、重新菜单进入；同Validation公司空关键词查询均0条，13列、六字段/标题、代码占位、色系文本框、编号不可编辑可见。名称有星号，两空窗打开后关闭，未保存。 | `plating-color-gui-20261003-r2/report.md/json`；已有会话，无输入凭据；截图仅工具内观察未落盘，未采console/network，不计保存互通或零错误证明 |
| 预览关闭及独立后验 | 精确本轮PID/tick关闭，8000不监听，原站点配置字节/owner/mode恢复，调度/邮件仍停；新连接930对象结构/索引全同、业务/元数据/Auth凭据/角色/账簿全同。整段预览仅Sessions、User两表内容变动，行数均不变，不声称全部认证表内容保持；缓存修正后至r2关闭则930对象全同。 | WSL私有`plating-color-preview-20261003-r1/stopped.json`，`plating-color-gui-closure-audit-20261003-r1/`；无worker/scheduler、SQL写入或外网操作 |

物理核验使用READ ONLY/RR一致快照，独立连接持全部三个MyISAM READ锁并复核连接ID；含六个InnoDB序列。仅允许明确隔离site/db和两既有Validation公司，未全migrate、扩原生权限、导入历史或外网写入。当前只做串行实验，无双事务/负载证明。GUI报告由子智能体写在父工作目录的output，四文件已逐SHA复制到仓库忽略的output，原件保留，见`plating-color-gui-report-copy-20261003-r1.json`。旧编号、简拼、条码、删除/打印、订单选择联动和全业务GUI验收继续。本地363入口为235正式映射、12部分可用、98预览、13导航、5工具，17JS/9CSS共26资源；外网继续4d71c35、11部分/99预览，本批未发布。

## 2026-10-03 此前外网发布：4d71c35（已被18e5835取代）

此批次A/B共用源码从5aa升级至4d71c35；下文“未发布”“外网仍5aa”等描述是当时记录。当前外网版本以顶部18e5835发布节为准。此批次公开候选505文件逐SHA、25资源指纹、固定镜像和只读挂载核验通过。没有下载服务器备份，上传及每项资源读取实际连接均核DIRECT；凭据只在RAM使用。

| 核验 | 实际结果 | 忽略目录证据 |
| --- | --- | --- |
| 本地发布助手 | 52项纯审计/原生清理/主机阶段模拟通过，Ruff通过；不是外网业务验收。 | `tests/test_incremental*.py` |
| 看板查询 | 实际Docker26.1.3 CLI对假Unixsocket：原JSON格式请求size=1，显式size=false不请求；未连接真实daemon。按SHA备份并修正飞哥采集器，本地看板同步修正。 | `erp_docker_json_size_model_20261003_r1.json`、`erp_dashboard_size_fix_20261003_r1.json` |
| 管理恢复 | r5仅恢复daemon，PID344058→353776；8任务ID/PID/启动tick及保护配置相同，containerd/容器未重启。RSS恢复时86228KiB，长期稳定性及历史请求具体来源未证明。 | `erp_daemon_recover_driver_20261003_r5.json` |
| r1 prepare拒绝 | inspect超时，未停服务、改站点配置或连接SQL；原失败不重跑，r2采用独立协调/备份/覆盖目录。 | `erp-incremental-prepare-poll-20261003-r2.json` |
| r2 prepare | 505文件/25hooks/原生备份实现SHA固定，无SQL；8服务基线和旧source完整核验。 | `erp-incremental-prepare-poll-20261003-r4.json` |
| r2 quiesce | 停五应用，三个数据服务保持；全927物理对象快照、三MyISAM独立READ锁贯穿完整SQLdump，四部分原生备份gzip/tar/SHA及完整对象范围通过。 | `erp-incremental-quiesce-poll-20261003-r3.json` |
| r2 apply/verify/cache | 只更新两DocField的三属性及两个新增空DocType；927→929，元数据逐列/来源MD5、两个新表批准结构与唯一索引核验。所有既有非目标业务表内容、结构及索引保持；三个阶段fresh证明相同。 | `erp-incremental-metadata-poll-20261003-r2.json`、`erp-incremental-verify-poll-20261003-r1.json`、`erp-incremental-start-poll-20261003-r2.json` |
| r2 start | 精确恢复原配置，五应用只读挂4d候选，8运行/noOOM，数据服务ID/启动/挂载保持，后端Git准确4d。无全migrate、seed、镜像build/pull或自动SQL重试。 | `erp-incremental-start-poll-20261003-r2.json` |
| 外网公开连接 | HTTP301、可信TLS login200、ping200；25资源均200且逐字节SHA同冻结候选，总560299字节，各实际源IP/端口及目标核DIRECT。 | `erp_external_https_health_20261003_r5.json`、`erp_incremental_public_assets_20261003_r1.json` |
| 外网认证连接 | 实际受限测试账号登录、get_logged_user、A/B认证页面、带CSRF的POST退出均200；未写业务或输出凭据，不当作浏览器操作证明。 | `erp-authenticated-https-check-20261003-r4.json` |
| 发布后OS只读 | dockerd PID353776，RSS38288KiB/Swap12836KiB；8个ERP task全RUNNING，原db及两个Redis的ID/PID/starttick保持。当前远程看板目标使用fixed_collector=true，飞哥采集器修正覆盖该调用方式。无Docker API、数据库读取或备份下载；此单次观察不计长期负载验收。 | `erp-post-release-os-readonly-20261003-r1.json` |
| 本轮真实浏览器 | gpt-6-luna在已有认证会话中刷新A/B，两套分别打开销售客户档案、销售客户类型、订单客户、订单类型、订单要求；同QA公司查询均0条，10个新增弹层开后直接关闭，无业务保存。核客户档案两套16列、词典11/7列及当前可见字段；下方滚动区域、保存、删除/打印/导入、console/network/pageerror和截图证据未取得，不报全功能或零错误。 | `customer-order-release-gui-20261003-r3/report.md/json` |

服务器私有路径为 `/opt/jifeng-erp-test/releases/4d71c35-source-r1/private-backup-r2/`，成功协调证明位于 `incremental-control-r2/`，原r1失败与空备份目录保留。全927范围含六个序列和`__global_search`；较早919张tab*/Auth范围不能等同本次完整物理范围。实际登录会写会话/活动表，恢复访问后的HTTP/GUI检查不作为全数据库内容保持证明。

本轮外网363入口为235正式映射、11部分可用、99业务预览、13导航、5工具；“正式”映射数量不等于旧系统所有操作已正式验收。并发、历史迁移及全模块正式目标继续。

## 2026-10-03 订单类型与要求

原站只读结构依据为`output/legacy-order-dictionaries-20261003-r1/report.md/json`；记录类型11列、要求7列及四字段空新增，未保存业务。A/B专用订单词典共用服务器API和组件；公司/类型、只读编号与简拼、UUID/摘要、版本及字段权限独立，不改原生Customer、SalesOrder或明细。旧必填未知，本系统名称非空为明确当前规则。简拼保持空值或历史原文，不引入猜测转换算法；旧简拼更新、删除、打印、旧编号和单据选择联动继续待完成。

31后端模型、2资源模型、订单词典/客户词典/客户档案/A-B四份CJS及Ruff通过；后端新文件最终使用兼容Python3.13的except元组，纠正Ruff按项目3.14目标自动去括号造成的本机模型解析失败，format后已重跑31项。客户端核11/7列、备注在简拼前、四字段新增、明确API/type、分页及末页缩小、公司/类型迟到响应拒绝、保存结果未知不重复POST；这些为DOM/协议模型，不算浏览器验收。

| 隔离步骤 | 实际结果 | 留存证据（忽略的output目录） |
|---|---|---|
| 来源前置 | 第一次在任何复制前拒绝：本地catalog_validation.py为CRLF，bench仍等于原冻结SHA；独立核规范行尾后内容相同，无DB或文件复制。其余依赖不变。 | `order-dictionary-native-sync-preflight-20261003-r1.json` |
| 冻结与镜像 | 9运行文件、10原不变依赖；3既有bench文件与HEAD内容相同，6新文件原先不存在，旧字节已备份。只复制本地源码，无网络。 | `order-dictionary-native-source-20261003-r1.json`，SHA `61b70ec94bf4781ac0d1a5f251e27a91ec95f0bf2de4655570fed0c4b8b4023e`；同名目录保存intent/旧源码。 |
| schema r1 | 一次targeted reload+commit，928→929，只新增空Order Dictionary；Module/角色存在、目标物理表不存在前置通过。 | `order-dictionary-native-schema-20261003-r1/`；六元数据表只允许新parent，其他行/结构/索引全同，所有既有非元数据表/Auth/账簿全同。 |
| suite r1 | 18实际岗位门通过，全部业务及审计回滚；独立新连接929表完整内容、结构、索引与suite前相同。 | `order-dictionary-native-suite-20261003-r1/`；主管创建/修改/原生Version、原生表单编号、只读/公司/记录范围、103条分页、停用/版本冲突、两类型计数/同名独立、跨类型UUID冲突/atomic等通过。每门及末次核Customer/SalesOrder递归实际子表内容、完整父子元数据及覆盖配置保持。 |
| 资源 | native实际hooks导入及25资源字节/内容指纹与本地一致；无DB/浏览器HTTP。 | `order-dictionary-native-assets-20261003-r1.json` |

schema及fresh audit沿用物理InnoDB READ ONLY/RR一致快照，另一连接持三个MyISAM表（Data Import Log、Error Log、__global_search）READ锁并复核同连接ID；有效本机DB/site及维护/停调度状态严格限定。未执行全migrate、原站或外网写入。只做串行实验，无并发/负载证明；新页面GUI尚未完成，浏览器继续任务因子智能体thread上限拒绝，未自行操作屏幕或改走被拒通道。旧选择窗未观察到，订单四效果业务含义仍等待用户确认。

外网收尾r3发现可信TLS连接后的登录页响应超时；只读检查八个任务仍运行，Docker管理API超时，dockerd RSS764712KiB/Swap2425280KiB。来源诊断r2因32KB输出截断无法parse；r3缩小函数摘要后成功，无业务写入。当前任务/配置检查确认live-restore=true、KillMode=process；只执行一次新r4管理进程恢复，PID328950→344058，八个任务ID/PID/启动tick及保护配置SHA全同，containerd及业务容器未重启。恢复即刻RSS86684KiB/Swap1088KiB，管理ping恢复；造成size查询的调用者仍未找到，不能据此证明长期稳定或根因已修。

恢复后的public health r4为HTTP301、可信TLS登录页200、ping200/pong；认证r3实际测试账号登录、get_logged_user、A/B两页及携带当前session CSRF的POST退出均200，账号/cookie/CSRF仅RAM使用，未输出。每条HTTP/SSH连接均按精确源IP/端口及目标核DIRECT。认证仅HTTP，未做新GUI/业务保存或外网DB快照，不将登录活动计为全表不变。证据：`erp_external_https_health_20261003_r3.json`（失败）、`erp_external_server_health_readonly_20261003_r1.json`、`erp_daemon_origin_and_runtime_20261003_r2/r3.json`、`erp_daemon_recover_driver_20261003_r4.json`、`erp_external_https_health_20261003_r4.json`及`erp-authenticated-https-check-20261003-r3.json`。服务器私有单次恢复receipt为`operations/daemon-recovery-20261003-r4/`，不是候选发布或数据迁移。

当前本地registry实际363＝235正式映射＋11部分可用＋99业务预览＋13导航＋5工具；两个新增入口保持部分可用。外网继续`5aa4b3b`的237正式/3部分/105业务预览，本批未发布。旧系统全部模块正式可用及旧操作习惯的完整验收目标尚未完成。

## 2026-10-03 销售与订单客户档案

销售“客户档案”、订单“客户”接同一服务器Profile API及界面组件，保留实际旧页19/17字段、各16列和编号只读；依据为`output/legacy-customer-archive-20261003-r1/report.md`。五种词典使用公司、模块、字段类型和实际版本，不把原生Customer主体类型当旧客户类型。姓名、手机、联系、税号、公司、折扣等按档案原文保存；已有档案编辑/停用只改Profile。当前公司与旧“公司”原文分开。新建使用原生Customer命名及实际创建权，也可显式关联已有客户；未自动合并同名资料，未给旧角色增加Customer写权。

29项Python模型、2项资源模型通过；客户档案、客户词典及A/B363入口三份CJS、Ruff、三份JS语法和diff检查通过。独立复核修复末页减少的客户端页码clamp不兼容，并测试101→100条/第2→1页、1501条末页、缺行拒绝及空集合；这些仍是模型，不能作为真实浏览器或数据库大集合负载证明。控制器去未用import、仅新测试格式修正后29项再次通过。

| 隔离批次 | 实际结果 | 保护与证据（本地忽略的output） |
|---|---|---|
| schema r1 | 引擎前置拒绝，无reload/DDL。 | 本地927表另有`__global_search` MyISAM，不能沿用外网两日志表的清单。独立物理只读检查确认目标DocType/table均不存在；`customer-archive-native-schema-20261003-r1/`及`customer-archive-native-schema-engine-inspect-20261003-r1.json`保留。 |
| schema r2 | 实际驱动不支持`ping(reconnect=False)`，快照尾部失败；仍未尝试reload/DDL。 | 源码只verify，未重拷；三MyISAM读锁释放，保留`customer-archive-native-schema-20261003-r2/`。 |
| schema r3 | 一次原生targeted reload并commit通过；927→928，只新增空`tabJifeng Customer Profile`。 | 有效本机DB/site/维护状态、目标物理表不存在、Module Def/角色已存在前置；全InnoDB物理READ ONLY/RR快照，独立连接持有全部三MyISAM READ LOCK并复核连接ID。现有非元数据表/Auth/账簿内容、列、索引全同；六元数据表只允许新parent，其他行及结构/索引相同。安装字段/序/权限逐声明核对；`customer-archive-native-schema-20261003-r3/`。 |
| suite r3 | 真实角色11门通过，业务及审计完整回滚；另一个新连接928表内容、结构和索引全部一致。 | 主管只关联/改Profile、原生可创建岗位联合创建、五类型/公司/version、停用历史、Sales User静默只读、权限范围、UUID/摘要和双版本、新建第二文档失败及两层mask均验；`customer-archive-native-suite-20261003-r3/`。无外网、业务commit、权限扩张或Customer元数据改动。 |

源码冻结清单为`customer-archive-native-source-20261003-r1.json`，SHA `72acbd22169f648240ca84f38887eff937fd3b87a1cbbde818e6e99bcc41ccb2`：10运行文件及10不变依赖。首次镜像前核三个既有bench文件等于HEAD，其余新文件不存在，旧字节已备份；后两schema批次不重拷。实际native源码导入核24项资源（15JS/9CSS）内容指纹，无DB连接、不算浏览器HTTP；见`customer-archive-native-assets-20261003-r1.json`。

gpt-6-luna本次仅静态准备；专用CUA的Chrome inventory返回fetch失败，IAB没有可用标签/登录会话。未输入或输出凭据、未尝试被拒的原生屏幕/CDP通道，未启动预览、未浏览器保存。新A/B界面、只读控件、dirty-close、console/network及跨界面回读仍待GUI证明；11门为串行本地验证，双连接并发、旧编号/底层关系、历史导入、简拼/删除/打印、单据客户选择均未验收。

本地registry VM实际363入口：235正式、9部分可用、101业务预览、13导航、5工具；两个通用原生Customer入口改为专用部分可用，未减少业务预览。外网保持`5aa4b3b`、237正式/3部分/105预览，本批未发布。收尾重新检查外网HTTP301、可信TLS登录页200及ping200/pong，每条实际连接按源IP/端口和目标核DIRECT，未登录或写业务；证据为`output/erp_external_https_health_20261003_r2.json`，不替代本次浏览器验收。第一版全模块正式可用目标仍未完成。

## 2026-10-03 客户字典发布停止与外网恢复

当前外网交付仍为 `5aa4b3b`；本地 `0f84e22` 未发布。公开包及服务器本地候选文件不构成发布成功。未继续新 r2 候选的 prepare/quiesce，冻结助手仅有静态复审和模拟证据。

| 核验 | 实际结果 | 忽略目录证据 |
|---|---|---|
| 候选 | 488公开文件逐SHA解包通过；新r2目录仅服务器本地复制公开文件，运行未切换，未复制私有备份。 | `erp-customer-0f84-extract-20261003-r2.json`、`erp-customer-release-stage-20261003-r2.json` |
| before失败 | 原生检查因 `non_snapshot_table` 拒绝，元数据未提交、无DDL；本轮完整before及四部分原生备份未完成，没有进入metadata阶段。 | `erp-customer-quiesce-diagnostic-20261003-r1.json` |
| 引擎 | 919张来源/Auth表中仅Data Import Log、Error Log为MyISAM，数据库计划事件0；不改表引擎或删除日志。旧全表读取/hash证据不等于MyISAM获得事务快照。 | `erp-customer-engine-readonly-20261003-r1.json` |
| 回滚 | 实际恢复五应用的5aa只读挂载、精确原配置；数据服务保持，物理只读缓存恢复通过，未执行元数据回滚SQL。 | `erp-customer-release-rollback-poll-20261003-r2.json` |
| 管理与锁探针 | 单次daemon恢复后管理API再次超时；READ锁探针stdin启动超时、结果未知。后续native维护0，仅检查自身Query，无探针连接残留。不得计读锁通过或长期稳定。 | `erp-daemon-recovery-inspect-20261003-r3.json`、`erp-unknown-probe-inspect-20261003-r1.json` |
| 恢复后运行 | 实际后端Git=5aa、八个原生任务RUNNING；daemon仍active但RSS836032 KiB/swap1011092 KiB。另有一个历史STOPPED工具任务，未当作业务服务。 | `erp-restored-runtime-readonly-20261003-r1.json` |
| 外部HTTPS | HTTP301、可信TLS login200、ping200/pong；每个连接按准确源IP/端口及目的443/80核DIRECT。 | `erp_external_https_health_20261003_r1.json` |
| 认证HTTP | 测试账号登录及get_logged_user正确。r1读取分块响应未读尽，下一请求报ResponseNotReady；r2完整读取后A/B认证页面均200且含集沣资源。GET退出403使脚本整体失败，退出未确认；不删除或重置其他会话。凭据及cookie仅RAM使用，未输出。 | `erp-authenticated-https-check-20261003-r1.json`、`erp-authenticated-https-check-20261003-r2.json` |
| 本次浏览器 | gpt-6-luna发现旧A标签先500，刷新到登录页，无有效会话。受支持CUA缺少本地文件读取，无法在不把口令写入调用源码的前提下重登，未尝试未文档化桥接。未验本次A/B切换、保存、console/network或截图；既有成功浏览器证据按原范围保留。 | `customer-release-external-gui-20261003-r1/report.md`、`report.json` |

上述文件均位于本地忽略目录 `output/`，服务器私有逐项证明仍保留于原发布目录。认证检查只产生登录活动/会话，不作全表内容保持证明。当前外网363入口仍为237正式映射、3部分可用、105业务预览、13导航、5工具；本地四个新词典入口及全模块正式验收继续待完成。

## 2026-10-03 本地客户字典增量

销售客户等级/分类、订单客户分类/级别从业务预览接到同一客户词典API；各类型独立记录、计数及创建回执，原客户类型键、默认值、权限和Customer元数据保持。实际registry VM为363入口：237正式映射、7部分可用、101预览、13导航、5工具；四入口尚缺客户/单据联动、旧简拼/删除/打印，未计为全业务可用。外网仍运行`5aa4b3b`的3部分/105预览，本增量未部署。

29项词典Python模型（原21项及8项新增风险）、2项资源指纹模型通过；客户词典、产品词典共用组件和A/B363入口三份CJS通过。覆盖5类型×2公司计数/同名资料隔离、跨类型UUID冲突回滚、类型不可改、mask及来源漂移、只读权限、明确客户端类型、7/11列和错误类型响应拒绝。Ruff、JS语法及diff检查通过。初次Ruff发现本测试文件既有lambda与未用变量两项，最小修正后通过。

| 本地原生批次 | 实际结果 | 保护与限制 |
|---|---|---|
| r1 定向元数据 | 原生`db.set_value`一次提交两DocField：dictionary_type选项/标签、value说明；后验比较误把Frappe自动补入的时间戳字符串和SQL datetime相等比较，业务suite未运行。 | 独立只读确认三项值正确，仅DocField行hash变化，其余926表内容/结构/索引均相同；无DDL、migrate、角色或业务记录写入。原失败证据保持。 |
| r2 原生业务 | 只读后验认证通过；原11项业务门通过，新增订单分类因计数键101字符超过原生Series.name varchar(100)而失败。 | 完整回滚后927表内容/结构/索引与r2前一致；未重复元数据写入，无新类型持久记录。 |
| r3 修正后 | 只镜像缩短订单内部计数后缀的Python文件，18项真实角色门全部通过。五类型计数键长91/92/95/92/89，最大95；原三类键保持。 | 包含4新类型主管创建/备注修改/Version/停用/stale、十公司类型计数与同名资料隔离、错类型读/跨UUID原子冲突、Sales User静默只读与写拒、公司越界拒。完整回滚及fresh连接927张表含Auth/GL/SLE的内容、列结构和索引全部相同；Customer原生元数据/分组/主体类型保持。仅串行验收，无并发/大集合负载证明。 |

忽略目录凭证：`output/customer-dictionaries-native-20261003-r1/`、`r2/`、`r3/`；各实验独立日志与before/after保留，无原站或外网服务器业务操作。旧站界面证据见`output/legacy-customer-dictionaries-20261003-r1/report.md`，没有本地截图或DOM属性证明。

本地GUI由gpt-6-luna执行：A的实际Sales Manager会话对四入口按公司查询为空，销售11列/订单7列；四个新增窗均实际打开及关闭，编号不可编辑、名称标星、备注及停用可见，未填保存。B本轮只确认订单目录中两入口存在。之后`mcp__node_repl__js`/`@oai/sky`的`get_window_state`被自动审批停止，理由为无法充分确认Windows浏览器当前URL；没有重试原生屏幕动作或绕过限制。B四页面、Sales User界面、新类型GUI保存/回读、console/pageerror/network及本地截图仍未验，不宣称完整A/B验收。专用CUA仅补读精确本地tab URL。证据为`output/customer-dictionaries-gui-20261003-r1/report.md/json`。

预览仅本地loopback，无worker；22项native hooks资源指纹在物理DB只读下校验通过，不当作浏览器资源请求证据。验收后准确停止预览进程，站点配置原字节恢复，Linux8000无监听且实际连接拒绝；Windows关闭探针先因命令换行语法失败，后因WSL转发超时返回10035，未冒充10061拒绝，单独保留结果。新连接物理只读后验确认927张表列结构及索引保持；客户词典、计数、Auth、客户、分组、角色/权限、元数据及GL/SLE保持。仅Activity Log、Sessions、User三表内容hash因本轮登录/界面会话变化，不声称GUI后全表内容均相同；没有数据清理或密码/权限更改。

## 2026-10-03 外网源码增量发布

实际业务版本`5aa4b3b`，五个应用复用固定`8858743-test-r4`镜像并只读挂载新集沣应用；没有在2GB运行机重新构建。公开包2,643,099字节、488个regular文件，容器内逐SHA/所有权/只读挂载、Git及22个直引资源核验通过。上传前后核系统代理、空代理环境变量、运行中的Mihomo规则/TUN及当前SSH准确源端口，全部DIRECT；公开包/工具上传均远端SHA一致，无备份或数据库下载。

| 阶段 | 实际结果 | 证据 |
| --- | --- | --- |
| 候选/管理服务恢复 | 候选原生只读488文件/22资源通过且未连接站点。Compose内存字节为字符串、Windows/POSIX模板路径和Mounts数组顺序的误拒保留。之后Docker inspect60秒超时；确认已加载live-restore后恢复daemon，原八容器ID/StartedAt/restart/image/mount保持，候选再按真实状态认证。 | `output/source-mount-candidate-certified-20261003-r1.json`、`source-mount-daemon-recovery-poll-20261003-r1.json`，原r1–r4候选失败留在服务器独立目录。 |
| 原生备份/before | 五个应用退出、三个数据服务继续运行；服务器私有数据库/文件/配置备份经gzip/tar/SHA验证。918张tab*/Auth表完整before，待补丁0；证明SHA `be004b285103a093b9ae4001b155548e9f2884fd608afbe9e57afecc17ea9849`。 | 服务器`releases/5aa4b3b-source-r1/backup-before-r1`，备份未离开服务器。 |
| 定向结构升级 | r1只读预检查拒绝Purchase Order Item非目标结构漂移，尚无DDL；r2四DocType原生reload、11CF仅search_index及指定索引，实际commit和新连接核成功。无full migrate/patch/install/seed/核心父表updatedb，原Stock Entry索引保持。 | 私有`targeted-schema-r1/result.json`失败、`targeted-schema-r2/result.json`成功；各项600 started/done及fresh-schema-proof。成功DDL未重跑。 |
| after完整核对 | 首轮比较键漏掉HRMS Leave Control Panel的第二条同逻辑键DocField，改为非目标真实name主键；r2硬钉原异常帧行号误差，r3修正后仅三列decimal默认零字符串格式误拒。r4只接受三列已批准21,6→21,9附带`0.000000→0.000000000`，最终全部保护、结构、元数据和历史数量门通过。 | 918→919，仅新增空客户类型表；914既有非元数据表旧列完整行hash/计数及Auth/GL/SLE相同。原失败proof完整保留；最终after-r4 SHA `01dcce2fb2f7de9c7f32aab98844ef696ad41852051f99d2cb8faefaddb9f75f`。 |
| 启动/外部HTTP | 清缓存用物理MariaDB只读事务，原config字节/权限恢复；只重建五个应用且真实RO源挂载，数据库/Redis实例不变。独立实际backend Git=5aa4b3b，八服务running/restart0/noOOM、DB healthy、原续期timer active/enabled。可信TLS登录200/ping200、HTTP301；全部22 CSS/JS外网HTTP200且完整SHA匹配候选。 | `output/source-mount-start-poll-20261003-r1.json`、`source-mount-post-deploy-runtime-20261003-r1.json`、`source-mount-health-after-20261003-r1.json`。HTTP健康不替代浏览器登录及业务验收。 |

A/B两套仍使用同站点、同数据库、同业务代码及权限。363入口仍为237正式映射、3部分可用、105业务预览、13导航、5工具；本发布没有将业务预览升级为正式模块。新计划单位弹层真实GUI、双事务竞争、复杂成本分摊、数据库大集合负载和全模块正式验收继续待完成。浏览器外网验收结果另记如下。

### 本次外网真实浏览器

gpt-6-luna使用computer-use真实测试账号登录，A中文现代工作台、链接切换B中文旧工作台均成功。A/B客户类型显式选择测试公司后同为0条；生产流程单、成本试算及员工补充档案列表均正常加载为空，B费用登记单亦为空。B财务/人事中文菜单及A订单/销售/生产导航可见。订单录入打开原生未保存销售订单表单，未填字段、未保存，放弃并关闭；没有点击保存、提交、导入、删除或数量确认。空表和只读导航不替代真实业务闭环或原数据写入互通验收。

CUA只能提供AX和会话内截图；另一个CDP工具出现MCP transport失败，未取得console/network/pageerror日志或安全导出的本地截图，不能声称这些错误计数为0。本次GUI依据保留的工具状态及子智能体脱敏报告，外部HTTP/22资源SHA由root独立验证。旧站只读资料和部署服务器权限没有因GUI扩大。

## 2026-10-03 最新批次：计划库存单位与正式成本来源

本批已以`5aa4b3b`更新外网；下列业务测试在本地隔离副本完成，外网发布核对见上节。新服务与原有四个成本接口共用业务代码；没有增加菜单正式入口，363入口的237正式/3部分/105预览/13导航/5工具保持。计划使用准确商品库存单位，销售来源保留原生数量、单位、换算系数、库存量及精度；外部/历史未知单位要求保存后的明确核对，不自动猜换算。核对记录是历史单位确认，单位已明确后的正常计划调整仍保留；销售凭证不改变原生数量/价格/估价。

当前源码定向Python模型164项通过：数量与确认33、生产计划锁序5、费用来源23、回货批次16、销售来源13、配料7、库存锁序14、库存凭证11、销售交付34、试算8。Flow确认弹层CJS13场景通过，包含保存后读取、精确版本、空白数量输入、迟到/脏表单拒绝及结果未知时不重复写入。Ruff、两份JS语法与差异检查通过。Python模型运行时有既有pytest-asyncio默认fixture范围弃用提示，不影响结果，未为此改动无关配置。模型不替代实际双事务或新弹层GUI验收。

| 原生批次 | 实际结果 | 凭证与限制 |
| --- | --- | --- |
| r1，定向元数据/索引变更 | 隔离副本新增10个Flow字段，重载Flow/Preview定义并增加准确12个缺失的单列BTREE索引。DDL已明确提交；完整旧列投影、稳定索引、CustomField/PropertySetter和账簿核验通过。随后业务夹具因遗漏必填目标仓库失败，全部回滚。 | `output/flow-quantity-cost-native-20261003-r1/result.private.json`保留passed=false、schema_reload_committed=true及fresh_business_unchanged=true；未执行全站migrate/install，不把DDL写成已回滚。每条DDL开始及实际索引差异独立记录。 |
| r2，原生数量8门 | 纯业务岗位真实SO销售1箱/库存12件；Flow12、采购申请24及工单12；只读核对上下文不产生确认；未知单位拒绝；明确确认保存actor/time/Version；旧版本重放拒绝；有效Preview阻止重新解释为144。 | 同目录`-r2/result.private.json`数量8门通过；下一费用用例发现SCR临时映射属性不是持久DocField，整批失败记录保留。919张来源/Auth表、账簿及稳定索引由新连接核完整回滚。只有本次合成Flow通过已记录DBset_value构造旧单位缺失情形，未改原业务。 |
| r3，原生成本8门 | 原生入库/委外发料/SCR真实过账：70接收+30损耗、材料100/加工200；精确原明细实物退10后材料90/加工180；批次净90、损耗30；另领料10与收货90合并材料100。隐藏退货或SLE整次拒绝；四处原生字段/表屏蔽整次拒绝，纯Manufacturing/Purchase/Stock User无System Manager。 | `output/flow-quantity-cost-native-20261003-r3/result.private.json` passed=true、成本8门和fresh_business_unchanged=true。复用r2数量8门的路径/SHA，不声称重跑；无DDL、业务提交或外网操作。所有夹具及原生审计完整回滚，919表、Auth、索引、Customer元数据与GL/SLE独立保持。未执行全损耗/全拒收真实用例、双事务撤权/重估竞争或复杂分摊。 |

SCR的`return_qty_from_rejected_warehouse`仅为映射临时属性，修复后不要求该属性为数据库字段，不按客户端瞬态标志重新分类。退货核精确原明细、持久仓库/数量及库存账；接受与拒收共用仓库且无法明确分割时整次拒绝。费用核全部关联已提交收货/退货、原供料参考和SLE；待处理/失败的重估或复杂文件状态无法证明时拒绝。当前BOM不能证明历史分摊；混合币种、副产品/LCV和不支持的分摊未作可用承诺。请求内两次来源/授权复核可发现串行变化，不能宣称原子并发快照或此后即时撤权。

数量确认锁当前Flow，读取Item及精确源单，核版本和有效下游。各下游保存共同锁Flow，但原生父/子锁可能在它之前；Production Plan工厂先锁并重读计划父/子，再排序锁全部Flow、读取WO额度。逆向来源/下游探测使用NOWAIT，错误父类型/字段或孤立关联不能借用取消父单放行。真实双事务尚未执行，不能从模型或索引存在推断无竞争/死锁。

源码同步到既有私有WSL副本之前核其13个目标文件与HEAD旧版本一致；逐文件旧字节受限备份并记录SHA。r2仅删unused Decimal import，r3仅修持久成本来源；最终`output/flow-quantity-source-sync-20261003-r3.json`与原生运行对应。预览端口未启动，副本maintenance/邮件/调度静默配置保持；未操作原站数据库、外网服务器或下载依赖。

### 原站库存导入按钮只读取证

gpt-6-luna正常授权登录，在物料档案空列表（0条）点击“导入”，实际收到filechooser事件；INPUT的accept为`.xlsx`、multiple=false。Escape关闭，未选择文件、上传或导入。工具栏和列头已记；选文件前无网页映射/预览/错误弹层，不能据此认定文件选择后的规则不存在。仅允许精确只读布局请求，没有业务POST授权。`output/legacy-inventory-import-20261003-r1/gui-import-observation.json`及受限`report.md`保存证明，自己的Chrome已关闭。此前“No event”来自采集器对同步布尔值错误调用.catch，失败历史保留并更正，不算原站缺少选择器。

## 2026-10-02 最新批次：客户类型、库存全集导出与分页

本批仍在本地，未部署到外网。直接执行当前 `jifeng_workspace_core.js` 的 registry VM，363入口实际分布为237正式、3部分可用、105业务预览、13导航、5工具；客户类型新增为部分可用，两工厂部分可用保持。A/B共用派发模型对363项入口通过。hooks实际读取14 JS/8 CSS，共22项内容指纹URL；新增客户类型脚本位于共用词典之后，库存专用列表仍位于浏览组件之后。历史的13 JS/21项官方资源证明仅对应上一批。

本批最终定向Python模型122项通过：客户类型21、产品词典43、库存浏览41、专用列表7、库存导出8、资源指纹2。初批78项通过后，GUI能力探测修复新增1项客户模型并纳入43项产品词典回归，按唯一测试计数122。客户类型、产品词典、库存浏览/列表/导出和A/B363入口相关CJS通过，Ruff、6份JS语法及差异检查通过。库存浏览模型实际建立1501条候选，核完整批次、总数和最后页；导出模型建立1001条保存来源，核全集不受分页或旧1000上限截断。这些是无数据库模型，不是数据库大集合或请求耗时/内存容量验收。

| 原生批次 | 已通过范围 | 凭证与限制 |
| --- | --- | --- |
| 库存导出r1，10门 | 两种档案的在用/停用/全部全集；既有Stock Manager的原生read/export及必要来源权限；Guest拒绝；完整来源、计数、账簿及调用上下文保持 | `output/inventory-export-native-20261002-r1.json`：passed、fresh_unchanged为真。该批仅核官方HTTP response builder；真实HTTP、只读岗位及GUI见10-03续验，并发撤权及负载容量未验。 |
| 库存分页r2，15门 | 实际Stock User/Manager、两种档案及停用历史、公司/Item/Profile User Permission、两行分页与HTTP整数文本、专用列表原生审计字段、主管保存备注后准确Version/修改人、只读浏览与非法参数拒绝 | `output/inventory-paging-native-20261002-r2.json`：passed、fresh_unchanged为真，large_database_dataset_verified为假。新服务完整分批扫描和复扫是请求内来源守门，不是原子快照；未建立数据库1501条负载、跨库存页或末页收缩的新增GUI证明。 |
| 客户类型r2，14门 | 实际Sales/System Manager创建修改与Version、公司/类型独立服务器分号、原生表单分号、新建请求核对、过期更新/不可变身份拒绝、Sales User只读、公司/词条User Permission、103行真实分页与空结果回退、停用历史、Admin删除及Guest拒绝、完整回滚 | `output/customer-type-native-20261002-r2.json`：passed、fresh_unchanged为真。r1失败保留；r2按站点实际Customer元数据修正验收预期，原生 `Company/Individual/Partnership` 保持，未改客户类型字段或授予Sales User写权。仅串行回滚实验；GUI见10-03续验，并发与负载未验。 |

库存XLSX按显式公司、种类、关键词、状态导出已保存可读全集，物料20/材料19数据列，不含审计、操作、内部Item/Supplier ID及附件URL；Supplier只取授权原生 `supplier_name` 投影，空供方不补ID，图片仅已授权DTO状态，不嵌图。逐来源read/export、字段和File下载权限复核后，在独立只读新快照中再次核完整来源与集合；新快照只保证覆盖其首次一致性读取之前的提交，不承诺此后即时撤权。前导零、公式样文本和HTML保留字符串，无公式/外部链接；非法XML、非有限值、超Excel单元格或1048575数据行上限均整次拒绝、不截断。旧导出模板与嵌图规则未知，不能认定旧模板已复刻。前端参数快照、abort及迟到response/blob模型通过，实际下载范围见下方。

客户类型旧证据来自受限 `output/legacy-customer-type-r3-20261002/report.md`：编号只读预填、名称/备注单行文本、停用默认关；第九列完整标签、修改弹层、旧编号及更新简拼算法仍未知。r4未放行未知Refresh初始化请求，没有新增这些语义证据。本系统编号为独立公司/类型规则，名称、备注、停用可持久保存；简拼仅保留原文，删除、更新简拼、打印及客户/销售凭证分类联动仍缺，入口保持部分可用。

客户GUI夹具r1/r2的审计账目声明分别漏计正常原生Comment/Permission Log，提交前完整风险守门正确拒绝，均停止并独立确认回滚，不是业务保存失败；回执保留于 `output/customer-type-qa-fixture-20261002-r1/failed.json`、`-r2/failed.json`。r2另经独立新连接核919张已记录tab/Auth表完整行及计数、Customer元数据与GL/SLE保持。r3补精确5条Shared Comment和6条Permission Log的正常原生审计声明后，一次提交和独立fresh核验通过：104条客户词条（103条分页及1条CRUD）、3个纯岗位测试User、显示名称区别于内部ID的供方及精确只读分享；不在文档公开账号值。GUI前已冻结919表快照，临时预览仅监听loopback。

### 2026-10-03 真实GUI、能力探测修复与独立后验

实际Chrome由Luna执行，A/B客户类型103条分页及同一新增词条各一行回读通过。新建最初未采到客户端保存响应及POST载荷，未盲目重复：root从独立原生来源确认唯一正确的新记录、实际actor/类型/公司/备注和未停用状态、新记录Version为0，自己的服务器日志单次POST200与登记时间吻合，恢复证明后才继续一次已授权的CRUD备注修改。该次客户端200/响应体/actor已捕获，B→A各一行回读新备注；实际POST载荷仍未捕获，最终审计的重建载荷明确为派生证据，不能写成原始线上请求。早期未完成/结果未知及r4误报记录保留。

Sales User只读详情曾触发不应出现的写权限不足消息，查明是 `Document.has_permission` 能力探测默认print_logs=True。`customer_type.py`、`product_dictionary.py`仅将能力探测改为文档范围的静默 `frappe.has_permission(..., throw=False)`，实际read/write/create守门、schema及岗位权限不变。客户21/产品词典43模型通过；原生静默r2实际核Sales User四调用无消息/can_write=False、真正写入/新建仍拒绝，Stock Manager两类产品词典init仍允许，来源/元数据/账簿/Series保持。首轮静默helper因回执结构KeyError在三检查后失败，原失败保留，修正helper的r2通过。修复后GUI r5 A/B详情read200、无误报、四个业务控件只读、Add/Save禁用，关闭后modal/backdrop均归零。r5的公司getByLabel探针因原控件无aria-label失败并回落空串，不能据此认定公司清空或缓存/筛选保持，非零滚动恢复也未证明。

Stock Manager在A/B物料/材料四次真实HTTP200并保存XLSX；root直接独立解析下载的ZIP/XML，20/19表头及各1条已保存数据行、所有单元格字符串、无公式/超链接/media/外部关系、无内部商品/供方ID及附件URL通过；供方为区别于内部ID的授权显示名称。A/B同类表头及单元格值相同，ZIP SHA可能因打包时间而不同，不以整包SHA不同判断业务不一致。Stock User仅在A物料/B材料两路实际导出403、无下载；按钮仍enabled，拒绝来自服务器。证据 `output/customer-inventory-gui-qa-r3-20261002/report.sanitized.json`、同目录 `xlsx-independent-20261003.json`。未扩大为四路只读拒绝、真实大集合/并发负载或旧嵌图模板验收。

root最终运行独立只读后验通过：准确两次业务写入（新增1条、备注修改1条），103条分页资料、原生Customer元数据、GL/SLE及全部敏感来源保持；仅六表允许已登记变化：QA登录的User/Activity Log/Sessions，以及Jifeng Customer Type/Series/Version。受限凭证 `output/customer-type-qa-fixture-20261002-r3/gui-native-after-validated.private.json` 保留响应缺采及载荷派生标识，不在公共文档公开账号或行值。preview r2 PID10207已精确停止、8000端口关闭，原配置字节/SHA、权限及mute/scheduler/maintenance恢复；同目录 `preview-closure-20261003-r2.json`。这次GUI和后验不等于全模块完成，也没有外网发布。

外网冻结 `8858743` 仍为237正式、2部分可用、106业务预览。最新只读健康摘要 `output/erp_external_https_health_customer_inventory_20261002.json` 确认实际DIRECT、HTTP登录跳转301、可信IP TLS下登录200及ping200/pong；没有切换镜像、部署或迁移本批。全模块正式可用目标继续，以下保留此前验收和失败记录。

## 2026-10-02：外网 A/B 测试站实际部署

在 `116.62.241.148` 的独立目录 `/opt/jifeng-erp-test` 部署 Compose 项目 `jifeng-erp-test`。两套入口为 `https://116.62.241.148/desk/jifeng-modern` 和 `https://116.62.241.148/desk/jifeng-legacy`，共用新建站点 `jifeng-erp-test.localhost` 与数据库 `jifeng_erp_test`。业务源码固定 `8858743800efe1836ac53f5c3de6644dab4e7923`；Frappe 16.35.0、ERPNext 16.36.0、HRMS 16.20.0 及完整生产资源由发布 manifest 固定。没有导入旧库、已有配置、登录会话或业务凭据，工程只做本地 Git 提交，未远程推送。

源码/资源包经实际 SSH 连接的 Mihomo DIRECT 链验证后上传并逐项核 SHA。官方 GHCR 直连大层中断后改用南京大学公开缓存，amd64 manifest digest 与官方一致；Python CDN 中断后改用阿里云 HTTPS PyPI，关键 wheel SHA 与官方一致。服务器实际出口、构建依赖与镜像传输直连核验保留；没有代理回退。当前镜像 `jifeng/erp-test:8858743-test-r4`，ID `sha256:543ecd7e7e51f5088659718cadb950f542a80bd8a17a5a8da92a3067e9372cff`。

保留真实失败与修正：公开部署脚本初始权限阻止 frappe 读取；官方新建站点调用需要先 `frappe.init(..., new_site=True)`；种子预检的工作区名称应为“集沣生产协作”；Gunicorn 需要 sites 工作目录，worker 的逗号队列须为单个参数。前两次初始化及第一次种子未完成，均先独立核状态后再受控修复。初始化 r3、种子 r2 实际退出 0，独立原生连接确认设置完成、四个应用、测试账号 11 项业务角色且没有 System Manager、全部 10 个业务文档可读、2 名员工、3 个商品、两项工艺物料、订单草稿和两个 Page；GL Entry、Stock Ledger Entry 均为 0。依据为 `output/erp_shared_seed_native_proof_20261002.result.json`。

最后一次镜像导出因小内存主机持续换页而受控停止；独立检查确认该子进程终止、构建退出 1/context canceled、未生成新镜像，保留日志。复用此前完整构建并验证的 r4 镜像，服务器 Compose 的 `bd0d25b` 修订明确覆盖 Gunicorn 参数与已修正的 worker 队列，不将未完成的导出认定为发布。部署文件逐版上传，旧文件与 SHA 保留；业务包未变化。

外部标准 CA/IP SAN 校验通过，HTTP `/login` 为 301 指向 HTTPS，HTTPS `/login` 为 200、`/api/method/ping` 为 200/pong，精确连接实际 DIRECT。生产 IP 证书到期为 2026-10-09 00:38:58 UTC。独立自己的续期服务 staging dry-run 实际退出 0；生产未到期检查、nginx 重载 Result=success/ExecMainStatus=0，timer active/enabled，每天检查两次。没有额外生产签发，尚未经历实际到期窗口续期。依据为 `output/erp_external_https_health_20261002.json`、`output/erp_renew_job_poll_20261002.result.json`。原 `/etc/fstab`、`jf-sheet-preview.timer`、对应 service 的 SHA 保持，原 timer active/enabled。

真实 Chrome 1366×900、新 profile、标准 TLS 校验、目标连接实际 DIRECT 后登录通过。A/B Page 均 HTTP 200，电镀厂各 19 列；通过原生 Company 候选选择测试公司，精确关键词与“全部”状态查询均 API/UI 一行。A 主页往返保持公司、关键词、状态和结果，没有额外查询。仅对唯一合成电镀厂的备注操作：A 一次保存 200 后 B 回读，B 一次保存 200 后 A 回读，均由正常页面完成，没有修改供应商或财务库存凭证。证据 `output/erp-qa-gui-20261002/report.md`、`evidence.jsonl` 及三张首页/回航截图。测试驱动早期关键词空格、控件歧义和 form payload 守卫失败按历史保留，未送达服务器的请求不算产品保存失败；没有重复未知保存。第一次实时 observer 仅见 WSS 打开，后续恢复后的只读复验在两套均确认 `frappe.realtime.socket.connected=true`、Engine.IO=open 和客户端 connect 事件，证明 namespace 已连接；没有单独记录原始 CONNECT ACK 帧，也不记录帧内容或 SID。只读复验最初等待登录跳转超时，没有采集该次 POST 状态，无法回溯；一次受控新会话明确登录成功并读取最终 B 备注，没有再次保存。

独立 Docker 历史事件证明原 128 MiB scheduler 曾 OOM→exit137→重启，当前 OOMKilled=false 不能覆盖该历史。`fcca100` 将其 RAM 限额改为 256 MiB、RAM+swap 改为 512 MiB，只执行该服务 `up --no-deps`。实际退出 0，独立结果核 scheduler 新容器 running、restart0、同一镜像；其他七个容器 ID、状态、限额及重启元数据完整保持，未重新初始化或 seed。依据 `output/erp_scheduler_restart_readonly_20261002.result.json`、`output/erp_scheduler_fix_poll_20261002.result.json`。原一次性 `jf-sheet-preview.service` 为 inactive/static，上次执行 success/exit0，原 timer active/enabled；Docker 与自己的 swap unit 均 active/enabled。

后续发现构建结束后 dockerd 仍占 RSS 644 MB、swap 3.59 GB。内核记录 global OOM 杀死旧 daemon PID235116，systemd 自动重启 Docker，八个服务均退出，数据库 exit0。受控仅执行已有八服务 `up --no-build`，没有初始化、种子或镜像构建；原恢复回执因要求所有容器 ID 完全相同而标 needs_inspection，保留该状态和第一轮断言失败。独立 r2 复核证明七个 ID 保持、仅 scheduler 再创建、全部镜像和按目标路径核对的数据卷保留，DB healthy、八服务 running/restart0，当前调度限额正确。随后新的原生连接确认最终 A/B 备注仍在，仅两个 Version 且均只改 notes、供应商关联保持、订单仍草稿、GL/SLE0、rollback/destroy 返回；外网 HTTPS 和只读新浏览器登录/实时连接再次通过。依据 `output/erp_daemon_state_readonly_20261002.result.json`、`output/erp_runtime_recover_verified_readonly_r2_20261002.result.json`、`output/erp_post_recovery_data_native_20261002.result.json`、`output/erp_final_service_states_readonly_r2_20261002.result.json`。backend 独立 cgroup failcnt/oom_kill 均为 0，峰值 284 MB 低于 512 MiB 限额，不把登录等待超时归因为内存上限。

恢复后 daemon RSS/swap 继续增长，一次本机 Go GC 请求返回 200 且八个容器 ID 不变，但不能据此认定内存已归还。只读堆采样达到 2 MiB 上限，保留数字/函数名而不保存完整 profile，未取得尾部 MemStats，单次采样不证明泄漏。`318ce40` 增加 `GOMEMLIMIT=512MiB` 公开 systemd drop-in；原 daemon JSON 不存在，创建 live-restore 设置，经配置验证、reload 和实际 Docker info=true 后才仅重启 daemon。初始助手后置断言失败，原 needs_inspection 保留并未再次重启；独立新连接逐项确认 Go 环境、live-restore、containerd PID 保持及全部八个容器 ID/PID/StartedAt/重启数/运行状态一致。随后 fresh confirm 通过、DB healthy，daemon RSS 529346560 B、swap 9875456 B、主机 MemAvailable 369463296 B、SwapFree 4188303360 B。Go 设置是软限额，不能当作 RSS 硬限额或长时间稳定性保证。依据 `output/erp_docker_memory_tuning_verify_readonly_20261002.result.json`、`output/erp_docker_memory_tuning_confirm_readonly_20261002.result.json`；新配置及单位的原不存在状态、SHA、阶段和实际重启记录保留在服务器本项目目录。

本次部署不能计为旧系统全模块完成：363 个入口中仍有 106 个业务预览，239 个直接映射中的 2 个工厂档案仍为部分可用。所有模块正式可用仍是总目标；本轮验证的合成档案往返不能代替全部模块业务验收，尚未做并发负载验证。

## 2026-10-02：A/B 工作台共用核心

新增 A 开源优化工作台 `jifeng-modern`；B 仍为 `jifeng-legacy`。从原 Page 第 13 行起的常量及完整 Workspace 原样迁入公共核心，原体 138711 字节逐字核对一致。现代界面继承同一业务 class，只调整首页、导航、搜索与页面标签；桥接正式面板按所属 Page 路由判断可见性。两 Page 的 14 项访问角色相同。工作区提供两个入口，共用同一站点和数据。

根实际执行 `test_workspace_variants.cjs` exit0：363 个菜单各一次原派发、完整 spec/default filters/create values 等价，21 个侧栏动作齐全；实际两类构造器、搜索输入与点击路径、正式面板两路由显隐，以及脏关闭确认期间转为结果未知后的禁止处置均通过。该检查为 DOM/协议模型，不能替代真实浏览器验收。8 个旧 CJS 消费者只迁移共用核心加载路径，保留原业务断言，各项执行 exit0。此时构建、两套实际浏览器与外网部署尚待完成；全模块正式交付目标未完成。

## 2026-10-02 共用档案组件公司范围同步

实际工厂Company Link漏通知后，短源码审计发现库存浏览、模具/版种/建模共用Tool组件、产品档案也只依赖缓存公司。三个组件现接实际输入/变更，并在查询、详情或打开前后核实际控件；公司改变清旧结果而不自动查询，迟到原公司数据不接纳。Tool/Product无草稿的普通键入直接同步范围，不调用set_value复位控件或启动原生Link验证；已有草稿仍沿原确认/回退协议，opening/busy/unknown不改变范围和版本。

独立复审补了两处异步边界：冻结状态下延期控件回退不能取消合法原公司打开；脏确认接受新公司时仍须再次清结果/增revision，防止确认期间旧公司查询重建列表。工厂也保留settingCompany回退期间原合法打开，CJS核原公司草稿不变。正常input的setter抛错反例、漏通知、迟到查询/详情/打开、脏确认和延期回退均通过。root定向执行七组CJS（库存浏览/bridge、Tool、Product、工厂/导出/bridge）全部exit0；未改后端、权限或schema。新三组件本轮尚无真实GUI证据，缓存构建及旧压模人页面补证继续，不计新增入口或全模块交付。

## 2026-10-02 工厂公司选择与导出最终快照修复

真实GUI r3已进入电镀厂19列表格，但Company Link候选选中后，控件值与缓存companyScope不同，原生onchange未通知组件，查询提前返回、零档案请求和零下载。保留 `output/factory-export-gui-r3-20261002/report.md`，不把JS直接调用处理函数当成界面通过。组件现监听输入/变更，且查询、导出、打开前同步当前控件值；迟到查询、打开和下载再比较实际公司，原公司结果不会覆盖新条件。未增加默认公司或自动查询。

安装源码及本地MariaDB系统变量确认请求连接采用REPEATABLE-READ，原58d6b19末尾普通SELECT可能仍读到原快照，不能据原SDK四项声称能发现其他事务的新提交。导出末尾改用当前站点相同配置的独立只读事务，完整权限、来源、可见集合和字段复核集中在新快照；无论开始、复核、回滚或关闭失败都恢复原请求连接，清理失败不发附件。只保证发现最终快照首次一致性读取之前已提交的变化，不承诺此后即时撤权；原请求事务和隔离等级不变。

公司漏通知及迟到响应反例的三组CJS已通过。18项导出模型、Ruff、编译与独立复审通过，包含原连接旧视图仍不变但fresh发现此前提交、最终复核全走fresh、开始/读取/回滚/关闭失败均恢复原连接并不发附件。缓存构建、独立副本SDK及真实HTTP/GUI续验继续；前次失败和58d6b19原SDK证据按历史保留，全模块目标及两工厂部分可用标记保持。

修复提交 `6f1083c7a8292de25038c7f72083a9c28c10f2a4` 已本地同步WSL，缓存单app生产构建actual exit0，未安装/下载依赖。复用既有私有副本的新native r2实际exit0，四项原生检查和rollback/destroy返回均通过；Admin两个导出额外核原请求DB对象恢复，真实六参数匹配并成功建立只读事务。工具SHA256 `89753f0b6c8228bc038d3b5ab2b58ebece144c91c484b965ee93585e882545ae`，安全摘要 `output/factory-export-native-r2-20261002/actual-native4-proof.json` SHA256 `84b461914f99cd3305a1014ba0cead5bb7a6eecca69760fcf7795ca38a209f61`。这四项不是实际跨事务并发撤权证明，该场景此次只有模型覆盖。

旧r2预览PID19781已精确停止且clone配置哈希恢复；新r3预览PID20305仅监听127.0.0.1:8020、threaded=True，无worker/scheduler，继续由Luna复用9235真实页面验证查询/下载及返回条件。原站web仍停止，未改变原站配置或连接原站数据库。

真实GUI r4已证明公司选择修复有效，精确前缀查询唯一GET200、一行和19UI列。仅一次导出GET200但没有下载，安全响应头为application/octet-stream、声明长度5291、attachment/xlsx，无重定向；保留 `output/factory-export-gui-r4-20261002/report.md` 和合成行截图。安装官方 `frappe/utils/response.py` 的as_binary固定octet-stream而忽略content_type，故前端严格XLSX检查正确拒绝。现改官方download/as_raw，保留完整最终守门和严格MIME检查；18模型和Ruff再通过，实际响应builder/SDK与GUI复验待。没有放宽前端或重放r4请求。

`c0c5fea0094ef4971f54cc14c1f345ba0f3b10a6` 修download并已同步WSL。独立native r3实际exit0、五项及rollback/destroy通过，新增真实官方build_response证明正确XLSX MIME、attachment和内容字节一致；原SDK四项和原请求DB恢复仍通过。工具SHA256 `38fa0e164948750042a002ffb2530c37aa53d12cd6d943d023fbbd7a9e292619`，安全摘要 `output/factory-export-native-r3-20261002/actual-native5-proof.json` SHA256 `0c6309b452f2419753f1b63f2fc569dd18c684af9c93b88add1486b1abf45e85`。

真实GUI r5仅再点一次导出，GET200/正确XLSX MIME及组件成功；Playwright未接收download事件，但Chrome已实际保存到Windows默认Downloads。精确文件创建/修改时间对应唯一响应结束、5291字节与声明及浏览器encoded/decoded body-size一致；未重发请求、改下载策略或运行探针。按原字节不覆盖复制 `output/factory-export-gui-r5-20261002/plating-filtered.xlsx`，SHA256 `ec0bc7b41f2a84f67d9917b488bec617ec00c3a6f487dc5a40ca2a2ebafa77a6`。Luna及root独立openpyxl核1数据行/16列、合成前缀、字符串账号前导0、字面HTML、无公式/超链接通过，只记录布尔与摘要。CLI事件缺失的具体原因没有独立证明，不把它当应用下载失败。

实际主页→发镀→电镀厂往返保持同archive DOM、公司/关键词/状态、一行及19UI列，回航无新query_archives请求；报告 `output/factory-export-gui-r5-20261002/report.md`。root已精确停preview r4 PID20572并恢复clone配置哈希，原站未连接或修改。r3/r4/r5初始故障按历史保留，source18模型、缓存构建、SDK和真实HTTP/GUI这条导出链已完成；导入、删除、简拼、打印、迁移、未知旧条件及106业务预览仍未完成，两工厂部分标记保持。

## 2026-10-02 工厂档案全集导出（源码、构建与真实SDK）

按旧工厂工具栏补“导出”，GET读取当前公司/kind/关键词/状态的完整可见结果，不只导出当前100条页面。共用原查询的加载和来源复核，分页仍100、完整候选上限仍1000；逐档案前后核原生export，未扩角色或schema。外发11业务列；电镀12业务列加创建人/日期/修改人/修改日期，不包含选择空列和修改/删除操作。所有值只取既有授权DTO，masked字段留空，无Supplier银行回退、内部ID或请求身份。末尾再核来源、原始权限输入和省略档案是否变得可读，变化整次拒绝。

内存XLSX使用已安装openpyxl，全部单元格按原文字串存储，不做数值/HTML转换，不生成公式、超链接、File或业务记录；超32767字符或非法XML字符整次拒绝，避免静默截断。前端只接纳成功XLSX，改变条件、打开弹层或销毁时取消；两个await后检查controller身份/取消状态，弹层开闭或新请求不能接纳旧body。导出保留当前列表，不修改账簿或权限。

13项真实XLSX包/权限模型、原工厂58模型、应付16模型及Ruff/编译通过；现组件、bridge、新导出三组CJS通过，新增已获得HTTP后的blob迟到/弹层开闭反例。独立短审指出的取消代次和可见集合扩张均已修。58d6b19已同步本地WSL，缓存Node执行单app生产构建实际exit0，没有安装或下载依赖。

root在既有私有副本唯一一次执行真实SDK工具，实际exit0、四项检查及rollback/destroy返回通过：Admin按合成前缀导出电镀16列/一行，账号前导0、字面HTML和审计列保持，XLSX没有公式/超链接；同前缀外发只返回11列表头，无跨类型行；完整Profile/Supplier状态前后相同；真正Accounts User能够原生查询该行，但export权限拒绝且未发布附件。没有mock生产函数、改变权限或新建业务档案；临时User/User Permission只在副本回滚夹具内创建。工具SHA256 `ec958d089a1c70491bf0491af0ecfecebd756dc4587f11712337983c956d8924`，安全摘要 `output/factory-export-native-r1-20261002/actual-native4-proof.json` SHA256 `7fd413602e134f70627015dfe91919f9fc141fe330757719d3202273e75f9d07`。未连接原站，未声称副本全SQL/Redis恢复。

预览仅监听127.0.0.1:8020；副本maintenance_mode临时关闭有私有配置哈希日志，邮件/搜索/scheduler隔离及私有Redis保留，原站web保持停止。WSL与Windows无代理本地GET /login均HTTP200。Luna在现有9229及独立context尝试访问时，导航GET在其page路由前报ERR_BLOCKED_BY_CLIENT，未到达应用或执行导出；失败报告 `output/factory-export-gui-r1-20261002/report.md` 保留，浏览器环境诊断继续。此时真实下载HTTP/GUI尚未通过，不计全部工厂交付；导入、删除、简拼、打印、迁移和未知旧条件仍待。菜单覆盖和两部分标记保持。

## 2026-10-02 电镀应付独立副本原生八门

生产适配仍为589c900的相同字节；运行来源冻结于5d9d177。root通过已核实的本地MariaDB Unix socket建立单独数据库/账号和离线site，使用现有本地工具完整复制926表；复制后SQL、全部结构/索引及23项自身元数据与原站before相同，原站前后整对象相等。新账号只授副本库，密码仅在受限文件/内存；新目录0700、配置/结果0600，独立Redis仅开私有Unix socket。副本使用官方邮件、搜索和scheduler隔离配置，已有telemetry关闭；没有改变原站配置、引擎或队列，没有网络传输、HTTP、worker或scheduler。

首次隔离r1实际exit1：child继承Windows仓库工作目录，Frappe Database构造先初始化相对日志路径而报FileNotFoundError，未连接数据库、开始事务或运行业务suite；保留原intent/result/真实退出收据。独立fresh核副本926表/结构/元数据/两个队列及全部来源，整对象与初始副本相同；原站完整fresh也相同。r2仅新增独立执行目录并固定child的原生sites工作目录，复用现有副本，不重建、不重复dump，不覆盖或重发失败r1。

r2实际进程退出0，八门及rollback/destroy返回后才由root核验：Admin与原AP响应完全一致；真实Accounts User满足Company select=true/read=false并读取真实已提交PI/PLE来源；同Supplier两档案类型及另一个真实存在且Admin可select的Company仍整次隔离；停用历史、原银行文本和只读投影保持；银行mask省略字段而AP响应不变；Profile.supplier、Supplier.disabled、PLE.amount分别失权时整次拒绝并可恢复读取。夹具仅在副本创建1非库存Item、1已提交PI、1同供应商外发档案及临时岗位/权限；0新Supplier、0Payment Entry，没有重跑既有AP19/native17或改变生产函数。

最后新的原站连接实际exit0，完整926表所有行（没有SID或业务排除）、结构/索引、23元数据、完整有序Error Log/global_search队列和来源文件与context.original_before整对象相等。root逐项绑定context、r2自SHA、实际exit receipt、八门、清理返回及前次失败/副本fresh依据后发布 `output/plating-payables-isolated-native-r2-20261002/actual-native8-proof.json`（SHA256 `ec3bd9226534dc3d6207af7472bdabaffcfc15f9ad2942abf91319604aa387b6`）。私有副本、Redis和原始证据离线保留；没有声称副本全部SQL/Redis恢复，Windows只复制无数据值的摘要proof，未复制数据库或私有context。

此批证明新增应付适配的原生集成，不证明HTTP/GUI或旧电镀欠款公式。旧电镀账仍预览，覆盖数保持239直接映射、106业务预览、237正式徽标及2部分徽标。销售源码续审确认Product Profile已有品牌/小类/参考价/档案图片，原生Item也有多条码及UOM；缺旧XS_SPXX条码到产品的显式关联和销售部门库存粒度，不能重复分配Item库存或把参考价当旧标价。已异步询问条码粒度，金额和电镀欠款口径仍待；旧产品档案显示模式本次CLI超时未取证，不能推断该页无模式。全模块目标继续。

## 2026-10-02 旧账与销售跟踪绑定、应付原生 r1 诊断

旧站在真实可见 iframe 中取得固定定义：财务电镀账的欠款直接绑定 `CW_DDQK.欠款`，超链接到电镀账明细，传值 `=D6 → PP`。缓存仅引用 `CW_DDQK`/`CW_DDQKDY`，没有公开 SQL、欠款公式或数据库关联键；不把供应商全部应付当成旧电镀欠款。已异步询问期初、品检扣款、付款、退货及其他采购的计入范围，其他工作继续。证据 `output/legacy-plating-ledger-binding-20261002/report.md`；没有手动查询或读取业务行，正常导航自动初始化请求及后续守卫收窄已如实记录。

销售跟踪三模式均读取 `XS_JSKC`。部门/条码/库存直绑；标价/编号/小类/备注通过条码关联 `XS_SPXX`，品牌再由编号关联 `CP_DA.品牌`，两图片模式同链读取 `CP_DA.图片`。金额列未暴露绑定或公式，不能推算为库存乘售价。库存超链接静态配置含未执行的服务端命令“销售_跟踪”和销售跟踪记录弹窗（`=H6 → BH`、`=C6 → CK`）；未执行该命令、读取业务行或验证图片 HTTP。证据 `output/legacy-sales-tracking-binding-20261002/report.md`、`output/legacy-sales-tracking-modes-20261002/report.md`；这些是固定定义证据，不是销售跟踪完成验收。

589c900 的应付适配原生 r1：新工具 24 项文件/mock 检查、Ruff、编译和实际 WSL 文件预检通过。root 实际 capture exit0；一次 launcher/child exit1，停在事务/业务夹具开始前的全表 InnoDB 断言，没有运行八项业务门。站点已有 `tabData Import Log`、`tabError Log`、`__global_search` 三张 MyISAM 表。独立 fresh verify 保留失败收据，`database_restored_diagnostic` 确认 926 表完整 SQL、23 元数据、所有结构/索引、原身份与停用夹具、Series 及完整 Error Log 队列（0）一致，rollback/destroy 和来源文件连续性完成；`native_gate_pass_established=false`。证据 `output/plating-payables-native-r1/`，不覆盖、不重发 r1，未读取过程日志正文。

有限安装源码审计发现：普通 Document 保存同步调用 global search，可能当场写 Redis 搜索队列，连接错误还可能直接写 MyISAM 搜索表；`in_test=False` 和事务 callback 回滚不能隔离它。没有仅跳过搜索的 doc flag，只有站点配置开关。原生报表异常日志也可能直接写 MyISAM Error Log，不能概括为必然延迟队列。原站引擎/配置/队列未修改；不通过放宽断言、清队列或关闭日志来制造通过。下一步采用独立测试站隔离这些副作用后再验证；当前不计原生八门、HTTP/GUI或旧电镀账完成，未声称整个 Redis 恢复。当前 239 直接服务器映射、106 业务预览、237 正式徽标和 2 部分徽标保持，全模块目标未完成。

## 2026-10-02 电镀档案关联应付读取适配（源码和模型）

新增单档案GET `plating_payables.read_payables(company,name,report_date)`：kind固定电镀厂，Supplier只由显式档案取得；返回既有只读档案投影和原生完整截止应付响应，不重算、汇总、分页或按电镀分类金额。公司只需select，原文联系/银行字段按既有read/mask投影，缺字段不补取；停用历史可读。查询前后比较Profile/Supplier完整状态和原始DocPerm/字段/角色/User Permission/Share输入，末尾再守门，来源或权限变化拒绝完整响应。原生异常原样传播。scope文字明确包含该供应商的全部业务，不将共享Supplier余额称为电镀专属欠款。

root审阅应用两文件候选，仅把scope展示改为完整中文；16项实际旧守卫/权限函数模型及Ruff通过。没有修改原AP算法、JSON、权限、schema、菜单或账簿，尚未运行新适配的原生集成或HTTP/GUI。旧电镀账的欠款字段/计算来源正通过实际内容iframe继续只读核对；该旧菜单仍为预览，当前适配不能计作电镀账迁移完成。既有AP原生r6的19门保留，不替代新增集成验收。

## 2026-10-02 工厂列序对照与电镀档案同步

旧站只读续查确认：外发工厂 metadata 虽有19项，当前桌面实际只显示13列（编号、名称、负责、手机、接货、电话、地址、备注、开户、户名、账号、修改、删除）；停用及四项审计列被原布局隐藏。此前本地16列不能算已符合当前原页，旧观察按历史保留。电镀厂实际19列，含左侧空列、12个档案字段、操作/日期/修改人/修改日期和修改/删除；“操作”绑定创建人，不是编辑按钮。电镀空新增与已有记录修改弹层的字段、按钮只读证据已取得；底部“修改”的另一条件及存储过程语义尚不明。证据 `output/legacy-external-factory-evidence-20261002/`、`output/legacy-plating-factory-evidence-20261002/`，未在旧站保存或删除。

`eadcb2d` 增加独立电镀档案类型，外发默认13列、电镀19列及双列弹层按上述证据调整；电镀创建人取 Profile.owner，与修改人分开。两个档案共用Supplier时仍按公司和类型严格隔离，编辑档案不改共享Supplier。删除仅显示禁用按钮，底部未知“修改”未开放，正常保存、修改与停用保留原生审计。两入口标为“部分可用”；导入、导出、简拼、打印、历史迁移及未知旧按钮语义尚未完成。

定向同步只执行一次原生 reload：archive_kind.options 增加“电镀厂”，父DocType和22条子元数据按原生导入重建，未改变业务表结构、索引或权限定义。独立连接 `output/partner-kind-schema-r2/fresh-synced-proof.json` 确认同步完成，逐项核对完整23行及新旧身份映射，其他全站SQL、原有合成夹具、账号角色、Series、GL/SLE和完整Error Log队列保持。r1只读prepare因WSL工作区Git信任配置缺失而失败，未登记reload或写入；保留失败收据，r2使用既有专用Git配置。同步通过不代表真实电镀业务或界面验收完成。

该提交58项工厂模型、全部1308项Python模型和18组CJS协议通过；单app构建退出0，未下载或执行整站迁移。实际原生17门一次运行通过，进程完成退出0后才记录执行收据；独立连接 `output/partner-native17-r1-full-sql/actual-native17-proof.json` 核对所有SQL、完整23元数据、保留夹具、结构/indexes和Error Log队列保持。包含真实采购主管/主数据岗位的电镀联合新建、公司/类型/UUID隔离、共享Supplier保持及创建人与修改人分开；整批业务回滚。结果完整归档 `output/classic-partner_archive-native-result-r3.json`。当前363入口中239直接服务器映射（7 Page/92 Report/34 Create/104 List/2 Catalog）、106业务预览、13总经理导航和5工具/说明；其中2个工厂入口标部分可用，其余237个正式徽标也不能代替逐模块验收。

同一Administrator实际从“主页→发镀→电镀厂”进入：19列、六行双列弹层、36px可调文本框、账号/停用同排已核。一次真实新建和一次仅备注修改均先暂停原POST、核对来源/版本后放行一次，HTTP200及页面成功接纳、正常关闭modal=0。新建原生分组通过中文“分销商”候选选择，底层仍为Distributor；银行账号前导0和备注字面HTML保持。合成前缀只查到一行，水平滚动实测0/900/1960，末端修改/删除列固定且hit-test命中；回主页重入保留同document/table/scroller、筛选和滚动。未证明旧空列选择功能，不将未登记的frame对象算作iframe身份。GUI证据 `output/plating-gui-r1/gui-report.md`。

每次GUI保存后停止本任务web并由新连接核验：`output/plating-gui-fixture-r1/created-proof.json`、`edited-proof.json`、`retired-proof.json`均通过。备注修改只增加一条精确Version，Supplier完整行保持；最后正常停用Profile并禁用本次Supplier，保留1 Profile/1 Supplier/0子行/3 Version及Series+1，原有全SQL、元数据、物理结构/indexes、GL/SLE和完整Error Log队列保持。没有删除来源或倒拨编号。实际GUI两项审计人均为Admin，创建人与其他修改人分离由原生17门的采购岗位验证。

`725ba8f` 随后增加可见“状态”选择（在用/停用/全部，默认在用），用于查询正常停用的档案；后台已有筛选，不改API/schema/权限。切换清空结果并回第1页，保留公司和关键词，等用户查询；打开/编辑/只读弹层及未知保存期间禁用选择，迟到响应不能覆盖切换后结果。实际组件和菜单bridge两组CJS及cached单app构建通过。真实GUI三种状态各手动查询两次，六次GET均200、结果各为0/1/1；只切状态的事件窗口零API请求。备注/账号原文、19列、Admin现有可编辑弹层期间状态禁用与关闭恢复、主页重入保留状态/公司/前缀/单行且无新查询均通过；外发空网格实见13列及默认在用，没有全范围查询。证据 `output/partner-status-gui-r1/report.md`。旧站状态控件位置和查询时机尚无证据，不宣称该新增入口已复刻未知旧行为。

首次hard reload的两个原生启动POST被只读守卫阻止；查本地源码后仅放行会话默认值读取、jifeng-legacy页面资源及标准Company候选查询，再正常重刷，业务保存始终禁止。GUI结束后精确停本任务web；新工具登记ead→725的六路径和原链字节，不改旧proof/guard。实际执行退出0，`output/partner-status-gui-readonly-r1/actual-status-gui-proof.json`确认926表完整canonical、23元数据、全部保留来源/Version/Series、Auth/Role、GL/SLE、结构/indexes与native Error Log队列不变，仅原Admin/SID窄时间刷新。rollback/destroy真正返回后才原子发布proof；没有重跑native17或数据写入。CLI新快照的清理被工具策略阻止，文件未读取并保留；既有追加console也保留，没有删除此前证据。

`14568c4` 重名纠错续验已完成：真实原生15门及独立完整SQL回滚通过，实际GUI只放行一个原POST，收到HTTP417和中文ValidationError，草稿仍可修改；关闭否/是守护通过，没有自动重发。停web后的新连接 `output/partner-duplicate-http-r1-full-sql/actual-http417-proof.json` 确认完整SQL、保留夹具、Series、GL/SLE和Error Log队列不变，原UUID只读resolve为not_found。未关闭或压制日志配置。

## 2026-10-02 外发工厂同步及原生13项验收

`64133b9`、`1290195`、`77e96e9` 将“外发→外发工厂”接入专用服务器档案和经典弹层；按旧空新增弹层保留双列字段顺序，默认一次保存新建原生 Supplier 与独立公司工厂档案，也可显式关联已有 Supplier。不会按名称复用供应商；档案编辑不改共享供应商、联系人或银行账户。已有档案受双来源版本保护，新建 UUID 可只读核对，未知结果不自动重发；停用保留历史，档案不能删除。旧编号算法仍未知，当前本站编号可编辑，不能宣称旧编号已迁移。

首次定向 schema reload 已执行一次，但核验器误判当前 DocField Meta 中已退役的物理 tinyint 列 `show_preview_popup` 的 SQL 默认值“0”与整数0，原生导入末尾的 `migration_hash` 更新随后被连接回滚撤销。原 `unknown` manifest、失败 fresh proof 与原工具均保留。独立只读恢复核验精确证明新增1 DocType/17 DocField/5 DocPerm、新空业务表与索引、原有全部 SQL/表结构/账簿保持；唯一未持久化字段是新 DocType 的导入哈希。另一次定向动作沿原生导入末尾 QB 实现只补这一字段，未再次 reload/DDL；新连接 `complete-fresh-r1.json` 确认同步完成、只此字段变化、原数据/所有其他新增元数据和所有计数/结构不变。证据 `output/partner-profile-schema-r1/`、`output/partner-schema-hash-finalize-r1/`。同步成功不代表业务验收完成。

`45cec29` 后直接运行工厂原生 helper，13项通过：原文银行账号/地址/字面HTML、显式关联与共享Supplier不变、同UUID幂等及不同内容拒绝、只读核对、编辑/停用/Version、两来源旧版本和固定身份、必填失败回滚、真实 Purchase Manager/Profile 创建编辑、真实 Purchase User 只读、Company/Supplier/Profile User Permission、两页精确来源和停用查询、非法分页、联合创建及第二份档案失败后全回滚。原生 Purchase Manager 没有 Supplier.create，已实际验证拒绝；联合新建使用 Purchase Manager + Purchase Master Manager，未授予 System Manager 或改权限。独立连接核对全部表计数、完整供应商/子表/档案/账号角色/Series/Error Log 和 GL/SLE 恢复；再按同步完成 proof 的整站 SQL 哈希核对所有旧行与新增元数据保持。证据 `output/classic-partner_archive-native-result-r1.json`、`output/factory-gui-origin-after-native-r1.json`。

`77e96e9` 完整前端构建退出0。后续单 app 构建发现本项目缺少 Frappe app discovery 必需的 `patches.txt`；`9f263ba` 加入标准空 patch 分节，未运行迁移。`da69b45` 调整弹窗宽度，实际原生 Small Text 控件的 inline 150px 高度仍覆盖 CSS，失败截图保留；`708863f` 在控件构造后设置初始36px，实际账号/停用均可见，用户仍可拖动文本框。该版单 app 构建及实际页面加载通过。中文 Supplier Group 是原生 translated doctype：英文 Distributor 搜索返回空，中文“分销商”候选选中后真实控件值为 Distributor；未创建或直接设置分组。

同一 Administrator 登录在隔离站真实执行新增一次、备注修改一次，每次点击可见保存并暂停原 POST；root 在发送前核对完整载荷、UUID/版本、Supplier/Profile 命名及 Series，确认后放行同一请求一次。新增 HTTP200，组件只读 resolve found 后接纳；真实列表前缀查询恰好一行，再点“查看 / 修改”仅修改备注，HTTP200后干净关闭。字面HTML、银行账号前导0和其余字段保持；没有直接 API 代替界面保存。停 web 后独立 created/edited proof 逐项核对新完整行、0子表、精确备注 Version、原有全部SQL/GL/SLE。最后正常保存本次 Profile.inactive=1、Supplier.disabled=1，fresh retired proof 通过：保留1 Profile/1 Supplier/0子行/3 Version及原生Series+1，不删除或回退计数器。证据 `output/factory-gui-r1/empty-gui-report.md`、`output/factory-gui-fixture-r1/created-proof.json`、`edited-proof.json`、`retired-proof.json`。材料/物料浏览与外发工厂实际正式徽标已核；缓存旧文档的失败观察及随后同SID刷新证据分别保留。

`14568c4` 精确把当前 Supplier 的原生重名拒绝转为中文 ValidationError，整笔事务回滚，不自动关联已有同名供应商；其他文档重复和未知结果仍保持原处理。50项工厂模型、实际组件纠错/未知结果协议及Ruff通过，原生15门和真实HTTP417正在续验。旧编号算法、迁移、导入/导出/打印和实际采购岗位GUI仍未完成。当前363入口中238正式映射（7 Page/92 Report/34 Create/103 List/2 Catalog）、107业务预览、13总经理导航和5工具/说明；入口映射仍不是全模块交付。原生13项、全模型1298项与18组客户端协议为各自已执行范围，不替代其余旧流程验收。

## 2026-10-02 库存浏览原生13门与真实双行GUI

root 在已构建的4e12a6c隔离站直接运行库存浏览helper，原生13门通过：真实Stock User/Manager、Profile和Item User Permission、公司隔离、停用历史、原文/零值、可见总数与两页精确来源、HTTP整数文本、非法和越界分页；独立fresh连接确认完整来源/子表/角色账号/Series、所有表计数和GL/SLE回滚恢复。证据 `output/classic-inventory_catalog-native-result-r3.json`，原r1失败、r2九门与其同SHA副本均保留。

GUI使用同一预登记Administrator会话，从实际“物料→浏览”“材料→浏览”进入专用只读网格，各查询两条合成资料，实际20/19列、0值、单位原文、Nos/2、字面`<b>合成</b>`不作为HTML渲染、当前1页/共2条和上/下页禁用均通过。两类双击打开只读详情并关闭，实际query/read RPC各200；材料经主页/模块重入后公司、关键词和两条结果保持。仅证明状态保留，未采集节点身份；两条无跨页/纵向滚动证据，水平滚动未量化，无File不声明图片HTTP。双击是本站新增交互，旧站习惯仍未知。root核安全截图；最终modal=0，停web后独立全SQL/计数核验通过，仅准确Admin/SID活动字段变化。证据 `output/inventory-catalog-gui-r1/`。

原生库存档案禁止删除，本次GUI夹具采用正常联合新建、验证、只读GUI、正常停用、独立验证。结束保留4条停用Profile、4个禁用Item、4条原生UOM子行、8条仅inactive/disabled的0→1 Version；既有空key Series准确递增4并保留。全部原有SQL资料与GL/SLE内容不变，新增身份和每一Version完整行已精确核对；没有File、业务SQL写、角色修改或编号回退，不能宣称资料已删除或计数全部恢复。证据 `output/inventory-catalog-gui-fixture-r1/prepared-proof.json`、`retired-proof.json`。worker/scheduler保持停止；原生Item搜索队列的后续处理尚未验。源码另补两个浏览卡片遗漏的正式徽标，客户端菜单/生命周期协议通过，徽标实际新构建GUI待验。

旧站追加只读证据确认外发工厂新增空弹层的双列顺序：编号/名称、负责/手机、接货/电话、地址/备注、户行/户名、账号，另有停用。编号自动预置，单个样例形态不证明算法；停用控制类型/默认值未知，没有Supplier控件。实际只开一次空弹层并关闭，精确域名非GET默认拒绝、仅已证实读取端点允许，无未知POST，未保存。销售跟踪一次普通查询显示4640条/47页，未读取行值；商品显示/标价/金额等列仍不能用销售履行替代。财务电镀账是电镀厂/欠款/联系人银行列，无可见日期/公司筛选，不等同日期区间电镀对账。外发工厂专属后端已补，仍待迁移、原生及实际UI验收；全模块目标未完成。

## 2026-10-02 原生首页路由复验、采购闭环与库存浏览分页

经典导航 r3 从“集沣生产协作”实际桌面图标进入成功，但两种正式档案 iframe 都停在合法原生首页 `['']`，等待超时。安装的 Frappe v16 源码证明空首项是首页，启动前 current_route 才是 null；84d9c0e 改为等待已解析的非空路由数组，保留精确目标 List 和关闭守门。7项客户端协议、完整 build 退出0。r4 同一原生会话禁缓存重载后，实际模具/版种正式菜单分别进入 `/desk/jifeng-mold`、`/desk/jifeng-plate`，可见 List 与实际 route/doctype 一致。模具原生公司与合成关键词筛选后，经主页/模块重入，同 iframe/document/List 和筛选保留；空表没有滚动范围。root 已核安全截图。r3/r4 各停 web 后独立完整 SQL/计数核验通过，仅预登记会话刷新和管理员 last_active 改变；r3失败保留。

采购 r3 取证没有字段类型差异，真实失败门是 Supplier 范围校验。纯模型模拟原生查询忽略空 IN，精确复现尚未取得明细时误发现其他供应商 PR/PI；a70be48 只在明细 ID 非空时查询返回来源，父单 return_against、完整子表授权和最终重核保留。59 API 模型通过；root 原生 r4 共19门通过，含同商品两 PO 明细/不同 Flow、三批 PR/PI 与直接 PO 发票、退货/贷项、已过账 PE/JE、其他供应商隔离、隐藏来源/字段/坏明细拒绝、服务与成品不同的委外半批转换、取消及真实逆向库存。独立 fresh 全部计数、完整来源/子表/权限/Series、员工/账号/补充档案与 GL/SLE 一致。证据 `classic-procurement_fulfillment-native-result-r4.json`；旧采购 UI、多币种、混合多订单、副产品/拒收/损耗/退货、先发票后收货、worker 和双连接仍待验。

Luna 旧站一次真实登录成功后补得2026-10-02报告与五张空网格，root 核了物料/材料浏览及外发对账截图。浏览是关键词/分页，档案另有列表/图片/图文和编辑动作。销售跟踪实列为部门/条码/库存/标价/金额/编号/小类/品牌/备注，不能用销售履行 API 代表旧页面；外发与电镀对账金额列不同，不能用通用供应商余额冒充品检或扣款。财务电镀账、两配料子入口取得空网格；一次普通配物料日期查询显示1134条/12页，行内操作含义仍未知。工资工价调用有默认历史工价提示和保存按钮，计算未执行。未保存/审核/出库/导出；没有原站完整 SQL 审计，不声明自动缓存 POST 无副作用。

09688c9 保持旧库存数组接口，新增真实权限分页：最多1000候选，全量核 Profile/Item、字段和末尾来源后计算可见 total，再返回每页最多100条；超限要求缩小关键词，非法/越界页拒绝。只读工作台按旧20/19列序与关键词/查询、上页/页码/下页/前往/总条数布局；原文、零值、缺权限空白和关联 Item 图片来源分开，不推算库存、金额或换算。详情每次重读、只读关闭，双击查看是新交互，旧站尚未知。root 接实际“物料/材料→浏览”，缺组件不落预览或编辑List，返回上级保留同实例。38专项模型、root全1250模型、全16组客户端协议及静态检查通过。helper扩至13门，本批尚未运行；新GUI/HTTP图片待验。363入口中237正式映射（7Page/92Report/34Create/102List/2Catalog）、108业务预览、13总经理、5工具，不是模块交付比例。

## 2026-10-02 经典导航实际回归、入口修复与采购来源续验

经典导航 GUI r2 使用同一个明确登记的原生管理员会话。旧“模具录入”实际经过主页、模块、菜单打开；公司与合成关键词查询为空，返回主页后再进入，host、scroller、tbody 均保持同一 DOM 节点，筛选保留。实际未保存草稿关闭选择“否”保留、“是”丢弃，弹窗关闭后 modal=0；顶栏点击被可见弹窗遮罩拦截。只见2次列表读取和1次草稿初始化，保存/上传/提交/删除/打印均0。空表没有滚动范围，不能宣称已测滚动保留。截图及运行方法证据保存在忽略的 `output/classic-navigation-gui-r2/`。

本批同时发现两个真实缺陷：Desk 的模块图标首项仍指向已不存在的 `Jifeng ERP` Workspace；经典“模具档案”原生 iframe 外壳标题正确，但实际内容回到默认工作区，不能以隐藏旧 cur_list 证明打开成功。`def477c` 修复精确旧入口并等待完整 Desk 初始路由、检查目标路由与对应列表、阻止关闭过程中继续创建表单。6项新加载协议及全14组CJS通过；完整 bench build 已退出0，**修复后的实际 iframe GUI仍待重新核验**。

严格 GUI 全SQL初审失败，保留原失败及只读证明：原生图表读取更新了 `Produced Quantity.last_synced_on`，通知请求把 Administrator 的 `seen` 设为1。分别只替换该字段为历史标准值/0后，82张图表及4条通知设置的完整原表SHA与不可变基线精确相符；没有忽略整张表或重设基线。两字段按锁定值一次恢复，保留提交回执，再由独立连接核对全部SQL、全部表计数恢复，仅允许已登记原生会话及管理员活动时间变化。复原工具首个只读诊断/哈希守门错误没有写入，也保留了原因。

入口配置先原生保存、幂等和完整回滚验收，r2通过；r1因验收脚本将字典 `items` 误读为方法失败，独立全SQL回滚通过。随后只应用一次正式修复：保留所有侧栏子行身份及其余配置，原生增加1条说明审计；仅 Workspace Sidebar、其子表与 Comment 三表变化。独立新连接精确匹配提交回执全部SQL，重复调用不再保存，不改变角色、权限或业务数据。

销售履行原生 r5 共25门通过，新增实际无WO单独制造、同商品两精确订单行共用制造凭证、逐成品子行SLE及取消历史。原生入库后成品31/原料4，取消单独制造后成品30/原料5；共用原料没有虚构逐Flow分摊，正常原生手填基本价保留BOM原价及进出价值一致。原有两WO/三制造批次、分批DN/SI、退货、收款与来源隔离仍通过。整批回滚，独立新连接确认所有表计数、完整所列来源/子表/账号权限/Series及GL/SLE一致。证据 `output/classic-sales_fulfillment-native-result-r5.json`；并发、外币、真实重过账worker及旧跟踪UI仍未覆盖。

采购 `5fe7938` 新增独立只读 PO→PR/SCO/SCR→PI、原行退货、整凭证付款来源 API，58模型通过；保留原生单位、币种、整单当前余额，不向采购行分摊。`3b4eb88`/`5e3c60b` 原生验收helper已有23模型；Single设置完整读取tabSingles，实体子表和缺表/虚拟来源拒绝守门保留。首次bootstrap读取错误没有进入业务写入，独立完整SQL证明无变化；原生r2已实际建立同物料两个PO子行，但批次收货开票后的完整读取拒绝在父来源值比较，原错误保留且完整来源、权限、账簿和计数独立回滚通过。正在诊断，**不能宣称采购新API原生验收或旧对账页面正式可用**。

root全已跟踪Python模型1219通过（当时不含后来新增的采购helper），另独立helper23通过；原生与GUI结果按上述范围记录。旧站已在2026-10-02用授权账号仅一次实际登录，明确原生RSA/Login各200并跳转首页，未记录凭据、请求体或会话；继续只读补列头与操作证据。用户不清楚抓取目录，不再以缺目录阻塞开发。全模块第一版正式可用目标仍未完成。

## 2026-10-01 三类工装真实写入、完整清理及独立制造来源

实际 classic GUI r3 已完成版种、模具、建模各一次新建、一次备注修改、一次图片引用保存，共9次成功 Save；三次原生私有图片上传与三个实际图片预览 HTTP200。每次请求先精确登记原载荷/UUID，再放行同一请求一次；服务器重读完整来源行、Version、File字段绑定、图片字节/hash/磁盘路径及全部受保护SQL、Series均通过。图片为本地3×2合成PNG，仅证明本批字节与预览，不扩大为大图上传或实际用户档案验收。模具首次因夹具缺少原生必填产品号收到确定417；原拒绝证据、无写证明、原载荷及明确补值修订保留，另用新UUID完成创建，没有减弱原生校验。

清理预检的Single真实`name`字段曾与工具身份参数冲突，发生在删除登记之前；修正为独立`row_identity`后，新连接确认已知来源/File/审计/Series/保护SQL全未变。首张File实际删除随后因工具错误预期Deleted feed而停止，未commit或删磁盘；installed File.no_feed_on_delete为真。新连接完整SQL、Series、三个文件及alias证明整批回滚，原失败manifest字节保留。恢复入口只登记新receipt并再次核完整快照，不重基线；新清理使用原生权限/hooks、删除前冻结原生完整JSON、提交后核alias再删精确磁盘文件。三条来源及三个File已移除，原Series恢复，保留6条Deleted Document与3条Deleted Comment共9条原生审计；独立fresh连接再次确认全部SQL与最终receipt一致，源基线除精确审计及既有Admin/session活动外恢复，三个私有文件不存在。36项ignored夹具模型由root独立通过。证据`output/tool_gui_mutation_34d031243547_r3/cleanup-preflight-error-fresh.json`、`cleanup-first-delete-rollback-fresh.json`、`manifest-cleanup-recovery-27158e5300f4.json`、`cleanup-independent-fresh.json`；不声称所有表计数变化为零。

`d558657`修复销售履行漏查合法无工单Manufacture：保留工单树，另返回准确`independent_manufacture_entries`成品行。Stock Entry及Flow材料完整原生子行、其他关联Flow/订单行/客户/工单进入权限、字段及末尾raw membership重核，坏父子身份或来源变化整体拒绝；原料、拆解、重新包装与委外发退料不冒充成品。56项销售API模型加原13项native-helper模型通过，root全1152模型通过。独立制造实际原生提交及新版导航GUI尚待，不计旧销售跟踪或整模块验收。原生sales r1/r2因BOM夹具缺100%成本分配及明确启用失败；r3已通过两SO行、Flow、两WO、三个Manufacture与实际成品/原料库存核对，后在验收脚本nested callback恢复检查失败。`bd981dd`复用既有caller恢复顺序，清缓存后在finally恢复精确callback队列/上下文，16项helper及14项Catalog模型通过；r4整套21门原生通过，包括完整制造库存、发货/库存发票、精确退货、收款、无PLE草稿反向来源、跨客户隔离、隐藏来源、取消及持久重过账状态。四轮独立连接均确认选定完整来源/子表/权限/Series及员工、账号、账簿、全表计数恢复，失败r1–r3保留且不计通过。证据`classic-sales_fulfillment-native-result-r1.json`至`-r4.json`；重过账仅状态协议，不是worker运行，尚缺多币种、字段高等级、双连接及实际销售GUI。

`00f919f`令工装和建模上传器在原生上传前逐文件设置optimize=false；仅隐藏优化按钮不足以关闭原生大图默认压缩。201/301KB客户端协议已验证，实际大图GUI未验。`9b43df6`顶部已按旧站证据改为当前“主页→模块→页面”面包屑，内部缓存及显式关闭守护模型通过；完整build退出0、清缓存并重启至bd981dd，实际GUI续验中。用户暂不清楚原抓取保存目录，已有历史读取记录未发现HTML/HAR完整导出；旧站本轮登录响应仍未知，不能以后台或新界面代替旧操作证据。

## 2026-10-01 版种默认编号r3及销售履行后台

Luna离线CLI独立session完成r3：实际classic版种录入，两次init HTTP200、两个canonical UUID4默认业务号不同且响应均匹配DOM，上方编号只读、编号原文可编辑；最终modal0、公司/关键词/空列表保留。无保存或上传。独立r3 fresh audit保护业务/权限/员工/账簿hash全一致，仅Admin.last_active/last_login及登录Sessions/Activity各+1。socket.io:9000有4次连接拒绝；初始导航未单独监听pageerror，不能称所有console/页面错误为0。v2 GUI夹具prepare先因运行cwd、后因SQL TIME驱动timedelta类型失败，均未创建业务或manifest；以bench/sites运行并精确保存带符号微秒后prepare成功，仅本地3张小PNG与manifest，19无DB夹具模型通过。实际Save/Upload及原生清理仍在执行，不计通过。

8ab7574新增独立销售履行query/read来源树：同公司订单的native明细/Flow/工单制造入库、精确DN/SI交叉行与退货原明细、原生发票状态及付款账簿凭证闭包。无跨凭证发货净数/工序成品/币种余额重算。独立审查修复无Flow工单商品错行、Flow/工单parent与row双向漏查、无PLE付款和JE反向来源/重过账，以及后期钩子更改来源而未更新modified的返回缺口；跨客户批次现代码本来已有context重置，增隔离回归没有因此改生产。40模型、root全1123模型、Ruff与差异检查通过，本地提交，无菜单/GUI/native通过。当前仅工单制造链，独立无WO制造入库、真实制造/多币种、请求前已陈旧的权限缓存与双连接仍待验证；PLE.amount为公司本币、amount_in_account_currency为明确科目币，UI必须保留两者口径，不能混作同币种。

本地隔离route探针已由Luna实测：实际按钮一次，匹配POST暂停时服务器计数0，root明确放行同一个route后计数1，原正文与UUID一致；任务浏览器/服务器精确关闭。只证Playwright CLI跨调用持有原请求，不是ERP保存通过。新版ignored GUI夹具v2的18无DB模型已由root独立复核；冻结默认日期/期望、原生Version格式及严格nullable元数据、精确actor/session活动与实际请求登记，仍待真实GUI/nativecleanup。r3准备阶段误用r2前缀覆盖旧before文件，已将新基线移为唯一r3_before并令before路径存在时拒绝覆盖；原r2_after的已完成审计及内容hash保留，原r2_before无法复原，不能重新作r2对照。

## 2026-10-01 物料与材料只读来源及末尾撤权守门

56c8acc新增物料/材料浏览API（list/read），要求明确公司、种类、关键词及档案在用/停用；原生Profile→Item的真实投影，旧单位/参考价/换算原文分别保存，不计算库存或自行换算。浏览始终只读，包括具有写权限的岗位。Item.disabled仅可读展示，不能代替Profile.inactive控制列表。图片只用标明来源的关联Item/image精确私有File，并核read/mask/download及唯一URL；没有独立档案图片或新增UI。

独立审查复现File加载时对Profile价格撤销mask仍带出价格，已改为先解析图片、整批source重载后统一核对已输出字段权限/标量/version及准确File，任何末尾变化整体拒绝，不补隐藏字段或返回部分DTO。31项库存模型通过，root独立全1083模型、Ruff及差异检查通过。

原生r1因验收误认为Stock User没有Company.read失败；实际标准角色有read，2d15ab2修正证据预期，未改业务权限。r2共9门通过：真实Stock User/Manager只读、相同旧代码不同业务商品、两种档案和停用历史、已disabled商品仍按档案状态可查、公司/Item/Profile User Permission、Guest/非法筛选以及全批回滚。独立连接确认指定档案/商品/所有子表/价格/File/Version/权限/Auth/Series/ErrorLog完整SQL内容、GL/SLE及所有表行数恢复；这不等于每张数据库表的内容hash或双连接隔离。证据classic-inventory_catalog-native-result-r1.json、-r2.json。旧浏览操作/菜单及实际图片HTTP、Company仅select真实岗位、原生字段read/mask撤权、双连接并发仍待验。旧站本轮登录结果未知，不能把后端或模型当成旧页面操作通过。

## 2026-10-01 档案报告整套复验及应付查询守门

`a1baf4a` 为每次成功原生操作核对完整 Error Log，防止附件钩子吞下错误；预期报告权限拒绝仅清理本次调用新增、标题/角色/异常/完整行哈希均匹配的一条原生日志，使用原生删除并记录精确回执，既有日志不动。整套 catalog_full r2 在初始产品图片别名断言处失败，独立全表/账簿审计无变化。已证原生 File.insert 会把该别名尝试去重为 Item 附件，`3c998c9` 用原生 create_attachment_copy 补齐验收数据，不放松业务角色或 File 权限。r3 整套64门通过，8条本次预期报告日志精确清理，成功保存未增加附图错误；独立连接确认完整档案/子表/附件/Version/Series/Auth、员工、账簿及所有表计数恢复。r1 的9条旧日志仍保留。证据 `classic-catalog_full-native-result-r2.json`、`-r3.json`；未扩大为实际浏览器保存/上传、并发或全模块通过。

默认号 GUI 首批见到旧文案、号未刷新，但该批仅核对另取资源 SHA，无法证明执行的类属于最新版本；下方“编号原文”可编辑本来就是原文维护设计，上方“编号”只读。fresh r2 已确认实际 buildEditor 含新文案、不含旧文案，字段区别正确、空查询及首次 init HTTP200。第二次新增等待遇工具30秒超时、句柄重置，未重复点击；响应/DOM号码匹配、请求 UUID、clean 与最终弹层状态未知，不能计通过或判业务缺陷。两批独立审计保护业务全内容及账簿，首批计数无变化、Admin.last_active变；r2 仅Admin.last_active/last_login与登录Sessions/Activity各+1。证据 `tool-archive-default-number-gui-20261001.md`、两个 number GUI fresh 审计。

`9468187` 新增只读 query_payables：明确公司/供应商/截止日，调用真实 Accounts Payable，以公司本币返回原生金额；完整核对科目、PLE、原凭证/对方凭证、费用来源、字段与记录权限、重过账状态，隐藏付款不提供部分余额。报告前后完整内容与权限输入变化即拒绝，未修改事务隔离或锁定 writer；准确处理原生 Date 首列合计尾行。root独立全1048模型、改动Ruff/编译通过。后续6b45af9补独立mask权限守门，37项应付模型通过。d1bce6d原生r6共19门通过：120元采购发票、40+30元分批付款、20元退货冲抵与未来10元付款的截止余额30元；80元赊账费用沿真实JE，另25元来源用于隔离负例；外币原生本币投影、隐藏付款/PLE/科目/对方发票/费用反向来源拒绝、三项字段read撤权及金额mask、持久重过账Queued/Failed/Completed状态、已安装取消账本语义均已验证。r1至r5为验收数据角色、历史记账标志和退货草稿旧总额问题，不计通过；原生validate先核旧总额再重算，r6改为数量调整后调用原生金额重算，没有修改应付生产算法或授予DocPerm。每轮独立新连接确认指定来源/子表、完整权限与Auth/Series、GL/SLE及所有表计数恢复。证据classic-supplier_reconciliation-native-result-r1.json至-r6.json。root当前全1077模型通过（含尚待原生验收的库存浏览25项），不是模块完成率。Company仅select真实角色、双连接并发、实际重过账worker、岗位GUI和旧对账口径仍待验证，未接旧对账菜单。

## 2026-10-01 模具、版种原生及单列弹窗续验

`bd2e109` 将模具/版种报表图片收紧到当前已保存档案及 image 字段的精确私有 File.read/download，其他父记录的同 URL alias 不能授权。原验收夹具改用五张本地小 PNG，并保留原权限、来源、原文、复制、状态与报告入口门禁。root 独立全1003模型、Ruff及Tool客户端协议通过。

Tool r2 的纯 Manufacturing Manager 夹具带了其无权读取的 Item，原生拒绝正确；r3 保留这项负例，正例使用可选关联为空的原生录入，13门通过。`5bc32b2` 的新 plate init 仅给可写字段提供本系统 UUID4 业务编号，不读写 Series；旧编号原文、显式业务号、原生主键各自独立。root全1006模型与客户端协议通过，r4 原生15门通过：追加重复 init 全SQL/计数/队列/上下文无写、默认号保存、显式改号及旧请求回执不覆盖当前值。两批回滚后的独立连接均确认完整Tool及原生子表、权限/账户、员工、Series/Version、账簿恢复，全表计数无变化。证据 `classic-tool_archive-native-result-r3.json`、`-r4.json`；默认号实际GUI续验待，原生表单仍要求业务号，不能将经典页默认号扩大为所有录入渠道已验收。

单列CSS `ad9ec15` 已build及实际经典菜单复验。模具/版种/建模维持旧11/10/8主字段顺序、左标签右输入、右图片栏及底部新增/保存/关闭；稳定态普通输入可编辑，编号与日期只读。版种类型草稿 X→否保留→X→是弃置，建模单价 X→否保留→Escape→是弃置，最终 editor/modal=0；无浏览器保存、上传或打印。此前两列阶段建模X未闭合且CLI会话消失的事实保留，不计关闭通过。r2首次本地500查证Redis未运行，恢复已有服务后一次刷新成功；未收集全量console日志。独立 `tool_archive_vertical_gui_fresh_after.json` 保护全内容及账簿一致，仅Admin.last_active/last_login及原生登录Sessions/Activity各+1，不声称所有计数为0。报告 `tool-archive-empty-gui-20261001.md`。

整套 catalog_full r1 通过前61个业务/权限检查，最终因ErrorLog变化失败：业务、账户、员工、完整档案及账簿独立审计一致，Error Log 69→78。已核原生该表为MyISAM，不能随事务回滚；8条来自预期跨公司 report PermissionError，另1条为合成Product Profile的原生附图 PermissionError，仍在只读归因。原日志保留，尚未修复或复跑，不能计为整套档案报告续验通过。证据 `classic-catalog_full-native-result-r1.json` 与只读日志归因记录。

旧站本批授权登录一次仍停在登录表单，没有可见错误或业务菜单，也没有该请求响应证据。任务tab已确认关闭；未重复登录或操作业务。两类对账、财务四账、销售跟踪及库存浏览的新增旧页证据未取得，继续保留缺口。

## 2026-10-01 产品档案已保存界面复验及版种建模后台

续验：产品来源切换R3在异步完成后，新建模式三个Item控件实际可见、enabled、df.read_only=false；切已有模式但不选Item时隐藏并禁用，切回后恢复可键入。逐控件合成文本输入后即清空，Link搜索/校验200、freeze_count=0，最终弹层0且公司/关键词保持。R2的即时disabled观察不能计为稳定态缺陷，也未据此修改后台权限。`product_archive_source_gui_fresh_after.json` 人员、账号权限、补充档案、词典原生内容及账簿保护通过、全表计数变化为空，仅管理员last_active变化；本次源切换审计没有另含产品全行集合，产品全行及磁盘恢复证据仍为前述已保存夹具的独立审计。

本批已在隔离站点迁移、完整build退出0并清缓存。赊账原生首次在基线读取遇到MariaDB TIME的timedelta序列化遗漏，尚未调用smoke；c276c54修全行哈希为精确days/seconds/microseconds结构，并以正负值、超过24小时及大值相差一微秒验证，未作浮点转换。随后r1费用helper误用既有Version检查函数三参数，整批回滚和独立新连接全内容/计数一致；cd5d513只修helper调用，不改业务守门。r2赊账原生17门通过：实际Accounts Manager/User及外公司权限、120元应付费用审核、原生供应商JE、只读余额/未保存付款上下文、实际40+80付款、源JE修改/直接取消守门、已核销来源取消拒绝、错供应商引用触发PE/JE锁门、逆序取消付款及来源、现金路径保持。全费用/JE/PE/GL/PLE父子、供应商及Auth/权限/Series/Version、科目/公司/成本中心、人员及全表计数恢复，独立审计全部一致。证据 `classic-credit_expense-native-result-r1.json` 与 `-r2.json`；真实GUI、高字段等级撤销和双连接竞争仍待验。

版种建模r1前11门通过，准确File不可读而另一父记录同URL alias可读时，DTO仍隐藏原图、候选为空；负例错把未改变的已存图片URL当成新绑定，合法历史保留因此不应拒绝，整批及独立审计恢复。e13c943只改helper为真实API普通备注保留不可读原图→清空→用新版本尝试重绑并拒绝→授权角色读最新版本恢复，不放松API或用SQL伪造历史。r2原生16门通过，含纯Stock/Manufacturing角色、普通原文及Version、UUID自然唯一冲突/回执、显式商品/版种关联、真实1元费用草稿拒绝/提交关联/取消后历史状态与新绑定拒绝、无财务读权隐藏费用、两个本地PNG/准确字段/File权限/同URL alias、新建追溯及重命名/过期/公司商品权限、只读报告。每阶段建模保持费用/JE/PE/GL/PLE/SLE全行不变；最终两PNG由原生File.on_rollback准确清除，工资、财务、账户及全表内容/计数恢复，独立新连接全部一致。报告调用实际execute和原生查询，未宣称HTTP报告调用、图片下载、实际GUI、字段权限撤销或双连接验收；证据 `classic-plate_modeling-native-result-r1.json`、`-r2.json`。

产品档案已在经典“产品→产品档案”用准确合成夹具复验：29字段、备注单行与注意事项多行、公司及业务商品固定、供应商/规格区和正常滚动可到达的底部新增/保存/关闭均实见。两个精确私有图板的真实同源下载200，图片实际为96×96；类别单击选中可采用，当前夹具只有同一类别，不能计为改变类别快照的界面验收。清图仅修改草稿，关闭“否”保持、“是”丢弃。没有浏览器保存、上传、打印、导入或导出。

21bee7a 为原生 FileUploader 增加所有关闭路径共用的幂等 on_hide 收尾，保留原生回调；无文件取消解除父编辑器锁，已开始而未确认成功的上传仍保持结果未知。协议检查通过。R1 没有准确区分所点击的 X 所属弹层；R2 起初误点父层 X 出现正确的忙碌守护，不能当成上传器关闭失败。R2 再定位到含唯一 file input 的上传器自身 X，产品及专利两次取消后都实际解锁。整页重载HTTP200资产哈希与构建一致。证据 `product-archive-saved-gui-20261001-r2.md` 保留这一纠正，不改写R1原始观察。

本轮准确清理五类合成档案、子表、已知审计及两个私有 File；先核每行完整哈希、全部入向引用与文件URL/内容别名，再走原生删除和提交后的精确磁盘删除，没有重复清理。清理完成，独立 `product_archive_saved_gui_fresh_after.json` 所有保护内容一致、全表计数变化为空，仅 Administrator.last_active 变化。只读真实init返回确认新建及恢复的三项Item字段可写，关联时只读，两种来源能力均可用；稳定界面控件的续验R3见上，不将未重新选择已有候选的空切换验收扩大为实际关联保存。

7f85b8d 增加版种建模普通档案和明细报表，保留旧八字段及独立“设计原图”，可明确关联同公司可读的商品、版种及已提交费用来源。单价按有限非负十进制原文保存；原编号、建模号、产品编号互不替代，文本建模人员不猜成Employee。公司、名称及服务器UUID追溯身份固定；历史取消费用保留引用并显示当前状态，建模不产生财务、库存或工资。保存需要版本和准确服务器上下文，私有图片必须绑定该已保存记录的准确字段并可下载；普通及force重命名拒绝。代码冻结时root独立36专项模型及Ruff通过，随后2006af9连接前端和真实菜单，迁移及原生16门续验见上；不能计为建模模块全部业务已交付。

## 2026-10-01 赊账费用与旧模具、版种字段续批

12bff52 增加明确的赊账费用方式、应付供应商和本币应付科目。费用审核通过原生 Journal Entry 借记费用、贷记供应商应付；实际付款另外在 Payment Entry 保存、审核，费用页面只读取当前原生余额并打开未保存付款单。旧欠款参考继续独立保存。现金、银行转账、其他方式保持原有规则；费用账显示明确供应商、应付科目及来源凭证。

关联 Journal Entry 的修改和取消仅在服务器精确文档上下文授权，客户端 flag 不能放行；update_after_submit 单独接来源守门。付款及普通 Journal Entry 引用受管赊账来源时按来源→总账凭证顺序锁定并重核公司、供应商、科目及提交状态，不改原生核销计算。取消闭包使用原生 QueryBuilder FOR UPDATE 当前读核查已提交付款/凭证明细和 active PLE，避免旧 MVCC 快照遗漏刚提交来源；已取消凭证仅其实际正反 PLE 净额为零才允许继续。33专项模型、实际隔离 Node 的重复点击/付款草稿构造、Python/Ruff/JSON/JS及差异检查通过；root 独立全仓939模型通过。原生17门、迁移、真实GUI、字段高等级撤销和双连接竞争仍待验证，不将代码锁门视为已完成并发验收。

6f9472a 按旧空窗补独立原编号和复制勾选原文；版种另补类型、产品编号、考核分、数量及明确复制来源。既有 mold_no/plate_no 身份不改，不从 Item 或主键回填原文，不按勾选执行未知复制。考核分只在新插入未提供时设为“1”；无 JSON/SQL 默认，历史空值不回填，显式空、负零、长精度和文本保留。版种数量和考核分在报表按 Data 原文展示，不用于库存、工资或金额计算。复制来源按同公司可读及无环校验，报表读取先核所有字段权限；既有 Item、Employee.select、币种和图片校验保持。81项相关模型通过，尚未迁移或执行本批原生、GUI。

Luna 旧站版种建模空窗实见图区“设计原图”与字段“编号”分别独立，不能把可访问树拼接作为字段名；另有建模号、产品编号、单价、建模文本、只读当天日期、备注、复制勾选，底部新增/保存/关闭。普通建模文本未见人员选择按钮；第二图片用途、自动号占用副作用和列表审核仍未知。仅打开一次并关闭，没有保存；安全截图遮罩编号值。证据为忽略的 `legacy-plate-modeling-empty-gui-20261001.md`。

## 2026-10-01 产品档案联合建档与类别选择后台

9e6cf20 增加产品档案 init/read/list/save/resolve API。新建明确选择关联已有 Item 或填写真实业务名称、明细分组、库存单位后联合创建 Item/Profile；新业务商品编号使用本系统 UUID 规则，与旧编号、代码、旧号各自独立，没有推定旧自动编号。关联已有商品仅核其读取权限及版本，不写 Item；既有档案仅更新 Profile，company/item_code/请求追溯身份固定，普通及 force 重命名均拒绝。材料、工序、要求和包装不由此接口改写。供应商货号、供应商和规格保留独立字段，供应商选择执行原生权限。

类别选择增加稳定父子 Link，不回填历史档案；明确改选时只快照大/小类原文，不带入材质、单位、负责、区域或价格。平表查询从原生权限过滤的父子集合返回准确代码/小类/大类/材质/负责/区域，简拼留空；父子关键词独立匹配，超过100条提示缩小查询。保存关联先锁 Item，再锁类别父项和子项，修正新建已有商品与既有档案编辑相反锁顺序形成的死锁风险；未执行实际双事务竞争测试。

产品/专利图片均要求已保存档案、精确私有 File/字段绑定、当前 File.read 与可下载；不借同 URL 的其他附件授权。历史未改专利地址可原样保留，DTO 不返回不可用链接；既有产品图片的读取要求增强为同时可下载，失去该权限会阻止保存。新增只读附件核对接口，返回准确授权 File 身份，供上传结果未知时人工选择；不重复上传。数值字段沿用原生 Float/Currency 空输入落零，响应重读实际持久值，不能声称可空数值或把参考值用于单位换算。

45项产品档案模型及53项既有 Profile 模型通过，root 独立全仓897项通过，相关 Ruff、Python/JSON、差异及冻结文件哈希核对通过。410d0ff 增加旧29字段大弹层、右侧双私有图板、七列类别平表及单击选中/双击采用且不自动关闭；供应商货号/供应商/规格另列，已保存档案可经脏草稿守护进入工艺材料原生表单。明确已有 Item 或新 Item 业务来源，未改历史换行备注只留显示基线，不回写。上传前记录当前字段的准确 File 身份，只有成功回调能确认，原生 uploader 早 resolve 不解锁；结果未知只读查新附件，由用户明确采用后再保存图片引用。相关六个客户端协议和三份 JS 语法、hooks Ruff 通过，root 专项协议及三种共享 bridge 调用方复核通过。复制、导入/导出、打印和批量传图仍不可用。

原生 r1 因夹具仅给 System Manager 而缺 Item.create 失败；d191e8a 改用实际 Stock Manager+Item Manager 联合建档，明确不含 System Manager，纯 Stock Manager 关联已有商品仍通过。r2 负例收到正确 PermissionError 而旧 oracle 预期 ValidationError，0544f0d 修正预期。r3 的 Item 范围夹具允许产品但未允许其材料子行，原生完整文档读取正确拒绝；83275cb 为同一合成档案准确允许产品和材料，其他商品仍隐藏。三次失败各已整批回滚且新连接全计数、员工/账号权限、补充档案、商品/所有子表、Supplier/子表、File/Version/Series、GL/SLE 全部恢复，不计通过，没有修改业务守门或 DocPerm。

r4 原生15门通过：两种真实业务角色建档、商品与原文独立、供应商货号/规格及原配方/要求保持、UUID回执/自然唯一冲突发生在 Item.save 后的联合回滚、平表父子关键词、明确类别改选与历史快照、两个精确私有字段和 File 权限/同 URL alias 拒绝、只读上传候选、原生公司与完整产品/材料 User Permission、过期及固定身份/强制重命名拒绝、所有只读入口无写。历史专利外链只做未持久化投影和模型保持检查，没有伪造历史 SQL；HTTP 下载、实际浏览器上传、并发与高字段等级撤销尚未实测。两张本地产生的微型 PNG 无外部传输，原生回滚钩子确认准确磁盘路径消失，完整内容/计数/回调与上下文恢复，独立新连接审计全部通过。忽略证据 `classic-product_archive-native-result-r1.json` 至 `-r4.json`。

本批 Product 字段/控制器及前端已迁移，完整 build 退出0，清缓存并重启至83275cb。只读运行检查确认创建身份/稳定类别 Link、保存后公司/商品只读、两张图片新建只读、JS/CSS hook 与四份 HTTP200 资产和磁盘哈希一致，证据 `product_archive_runtime_read.json`。实际弹层布局、上传/保存及岗位 GUI 待验，不能据模型或原生15门计为产品模块完整交付。

续验空表 GUI 已从真实“产品→产品档案”进入，明确测试公司查询200且零行；只开一次新增，自动七列类别窗、X关闭保留父窗、29字段顺序、代码/大类/小类只读、备注单行/注意事项多行、两图未保存禁用、供应商与规格区及明确业务商品来源均实见。合成名称草稿关闭“否”保留、“是”丢弃，最终弹层0、列表公司/关键词/停用状态保留，无保存或上传。类别和已有商品无可选行，双击选择未证；底部实际滚动和已保存图板继续下一阶段。root 已查看安全截图；停服务后独立 `product_archive_empty_gui_fresh_after.json` 全部保护内容一致、全表计数变化为空，仅 Administrator.last_active 变化。证据 `product-archive-empty-gui-20261001.md` 及两张安全空表截图。

## 2026-10-01 产品类别双表、原生持久化与本轮界面验收

924a5c3 增加普通 DocType `Jifeng Product Category` 和 `Jifeng Product Category Detail`，分别保存旧类别维护左表的代码/大类/负责/区域/停用和右表的小类/材质/单位/换算单位/换算值/停用。父公司固定，子公司从准确父项取得，父子稳定身份禁止普通及 force 重命名；可改原文但不能改绑来源。新建请求 UUID 按记录类型和公司核对，原生 Form 也由服务器生成追溯身份。没有改 Item Group、UOM、BOM、商品档案或历史数据。

换算值使用可空 Data 保存有限 Decimal 原文，区别空值、零、负零及长精度；不是 Float，不默认补一，也不据此计算库存换算。39 项类别模型、Ruff、Python/JSON 及差异检查通过。原生 r1 共18门通过，覆盖真实管理角色、Company/父项 User Permission、类型与 UUID 范围、可改原文与固定身份、长精度和空值、停用历史读取与新选择拒绝、过期版本、原生新建及普通/force 重命名、删除和越权拒绝。整批回滚后独立新连接核对完整父子档案、Item/Profile/子表、File/Version/Series、人员、账号权限、补充档案、GL/SLE、全部表计数及回调/上下文恢复。证据为忽略的 `classic-product_category-native-result-r1.json`。

9bc5bf1 连接实际产品菜单“类别”，左右网格分别保留六列和八列，左行选择加载其明细，允许当前行编辑与保存。保存只提交当前行，不能算旧表的整表批量保存；导入、导出、删除和更新简拼仍不可用。原生 Company 选择、关键词、含停用查询、脏草稿确认、旧响应拒绝及结果未知只读核对已接入；含停用参数按原生表单编码传0/1。换算值在客户端按十进制比较，不经 JS 浮点舍入。类别和三词典客户端协议及相关 JS 语法通过。

14c39b7 所含两类新表已在隔离 WSL 站点迁移，完整 build 退出0，清缓存并重启服务。当前363入口中233直接路由（7Page/91Report/34Create/101List），仍有112业务预览、13总经理导航和5工具/说明。Luna 用两条父项及三条子项合成夹具完成实际 GUI：父子联动、含停用过滤、空换算值/负零/26字符长精度、空新增父子行、查询/切父项/切公司否保留与是丢弃、跨词典标签的关键词/公司/停用保留通过，没有浏览器保存。初版强制表宽导致默认只见左3/6和右5/8列，两个滚动容器均正确裁切，并未相互绘制覆盖；a14575d 调整近对等网格并取消强制表宽，复验默认左右6/8列完整，clientWidth=scrollWidth为427/469，无横纵向溢出。长文本可换行完整显示，行内窄输入框仍可水平滚动访问尾部，字段内容不变。

复验期间 pageerror、控制台错误/警告及 HTTP失败均0；单个 `/desk/undefined` GET200 的来源仍未定位，不称已修复。忽略证据 `gui-product-category-browser-20261001.md` 及其安全合成截图。停 web 后准确检查五条夹具行/已知子项/Version，再精确清理，Series未消耗，所有业务内容及全表计数恢复；独立 `product_category_gui_fresh_after.json` 全部保护项通过，计数变化为空，仅 Administrator.last_active 原生字段变化。夹具收据审计保存在本地忽略目录，类别 GUI 和清理已完成；整表批量保存、导入导出等未实现项继续保留。

## 2026-10-01 三产品词典实际 GUI 与原生控件修正

Luna 已从实际短菜单风格、来源、品牌分别进入新九列工作台，确认公司筛选、空结果查询和三个空新增弹层。实际浏览器发现布尔参数被原生表单编码为字符串 `false` 后遭417拒绝，0aa31fe 改传0/1；禁缓存整页重载后核验新函数和两个筛选状态。随后发现 Frappe 自动加入的 Section 控件没有 `set_value`，9fa812e 限定只初始化和冻结业务字段，失败时清理半初始化弹层并显示通用失败状态。协议模型包含这一原生 Section 契约，修复后三个新增均实际显示。

14c39b7 将备注显示为与旧站一致的单行文本，底层仍为 SmallText；备注控件长度按其存储能力设置65535，名称为140。已有记录仅提交实际改动字段，未修改的历史多行备注不会被单行显示改写。最终浏览器空窗实际为两个可编辑文本框、零 textarea，编号只读、停用未勾选；名称及备注临时输入后，X 选择否保留，底部关闭选择是丢弃并回列表，所有弹层清空。筛选和页签上下文保留，但空列表没有可滚动内容，未声称实际滚动保持。没有词条数据，也没有浏览器保存、自动简拼、删除或打印验收。

修复后的检查未见 pageerror、控制台错误/警告或非 Socket HTTP 失败，实时连接为已连接。root 已查看安全空表截图，当前实际 JS 与磁盘哈希一致。忽略证据 `gui-product-dictionary-browser-20261001.md`、`gui-product-style-empty-form-singleline-20261001.png`；fresh after 的员工、补充档案、账号权限、词典/商品/子表/File/Version/Series 和 GL/SLE 内容一致，表计数变化为空，仅 Administrator.last_active 原生登录字段变化。

验收早期本地浏览器 CLI 凭据输入曾回显隔离测试站管理员密码，已通知用户并仅轮换该站管理员，原生核验新凭据有效、旧凭据无效，私有配置权限600且原子更新。旧站凭据未改，仓库和报告不保存任何密码。原 GUI 基线因轮换失效，重新建立轮换后的基线再做验收；以上 fresh 结论仅对应新的 `product_dictionary_gui_fresh_before.json` 和 `product_dictionary_gui_fresh_after.json`。

## 2026-10-01 旧产品类别选择器与编号证据边界

Luna 在已授权旧站临时草稿核实：类别维护是双表，而产品档案打开的类别选择器是代码/小类/大类/材质/负责/区域/简拼七列平表；浮层实际标题却为“产品品牌词典”，保留这个旧界面不一致事实。单击只选行，Enter未采用，双击后编号、代码、大类、小类、旧号由空变为非空，浮层没有自动关闭。代码及大类/小类只读，编号和旧号可编辑；材质、单位、负责、区域和参考价格仍空。关闭浮层后档案草稿直接返回列表，没有确认框。

观察到 `GetAutoGeneratedID` POST200，没有读取编号或响应结构，也没有观察到业务保存请求；其是否占号或有其他副作用未知。编号与旧号、代码与编号的相等性以及代码格式尚未核验，已关闭草稿不再重开触发。没有保留业务行值或业务行截图。证据为忽略的 `legacy-product-category-selection-browser-20261001.md`。产品新式入口将采用准确父子来源，仅快照大/小类；编号规则、旧复制语义和旧代码返回键仍不推定。

## 2026-10-01 三产品词典旧式工作台与事务编号

110871d 新增三词典九列工作台和专用弹层，实际产品菜单风格、来源、品牌在 27fd7cb 接入；保留带产品前缀的旧别名，类别仍是独立未完成入口。根任务统计发现初版仅接了前缀别名、实际短菜单仍进入本地预览，现已修正并把协议测试改为从真实菜单数组取名、禁止回退预览。当前363菜单中232直接路由（7Page/91Report/34Create/100List），113业务预览、13总经理导航和5工具/说明；这是映射统计，不是模块交付比例。

九列按旧页编号/名称/备注/简拼/停用/操作/日期/修改/删除排列；空新增可见编号/名称/备注/停用，编号只读，公司范围另行明确。查询、保存继续、空白新增、脏关闭确认、标签草稿保留及结果未知只读核对已实现。日期和操作人是本系统审计信息，未声称旧列计算语义已还原；自动简拼、删除、打印、预览仍明确不可用。核对失败或字段/编号模式不同保留草稿，不自动重发；当前值与原草稿不同须明确采用。实际 Frappe add_custom_action 没有返回值，已按原生按钮集合修正禁用状态，模型同样采用无返回契约；关闭标签模型验证实际隐藏弹层并移除离页提醒。五项相关客户端协议、三份界面 JS 与 hooks 检查通过，实际 GUI 尚待。

db7a8d6 按新系统规则为每公司/词典类型分别使用原生 Series，在保存事务中锁定分配三位起始编号，位数增长不截断；预填号只读且不占号，实际以保存回执为准。该规则未宣称与旧分号或旧并发一致，不从历史编号初始化、不为历史原文增加唯一约束。automatic_v1 的 UUID 初始摘要排除服务器定号，原 mode 空的历史摘要保持兼容；改名后同初始请求只读返回当前条目，不重分号/还原名称。新原生表单也由服务器生成编号，禁止客户端指定自动号/模式/追溯字段，普通复制清编号及创建身份后重新分号；历史原文不回填。

原生 r3 在复制夹具失败，Python copy_doc 默认 ignore_no_copy=True 与普通 Desk 的 no_copy 语义不同；独立新连接完整内容、Series 和计数均恢复，失败不计通过。0010fdb 明确使用 ignore_no_copy=False 后，r4 原生20门通过（此前15门加预填无写、公司/类型分号、改名后回执不重分号、固定身份/注入拒绝和失败回滚）。普通复制仅是明确参数的服务器夹具，不冒称 Desk 复制按钮 GUI。旧 helper 把 Series 当 DocType 过滤，r1/r2 仅核到表计数；本批修为独立读取完整 tabSeries 物理行及 DECIMAL 精度，r4 与 fresh connection 内容复原。所有词条、档案、Item/子表、File/Version、员工、账号权限、补充档案及 GL/SLE 一致。首次同 Series key 的双事务竞争仍未实测。

全仓811项 Python 模型通过，相关 Ruff/JS/差异检查通过；WSL 迁移、完整 build 均退出0，27fd7cb 页面改动之后清缓存并重启服务。只读运行核验确认编号/创建元数据、Profile 已保存身份只读规则、JS/CSS hook 及三份 HTTP200 资产与磁盘哈希一致。忽略证据为 `classic-product_dictionary-native-result-r3.json`、`-r4.json`、`product_dictionary_runtime_read.json`。旧产品档案空窗29字段与控件属性另由 Luna 核验并由 root 查看安全截图：编号可编辑，代码/大类/小类只读，新开自动弹类别词典；备注实际单行，注意事项多行，图板在右侧。类别选择返回字段尚未核实，不猜代码或换算生成。证据 `legacy-product-archive-browser-20261001.md` 和所列空窗截图。完整产品原式录入、类别双表及岗位保存等缺口继续保留。

## 2026-10-01 已保存产品档案身份与独立图片显示

26642c0 为已保存 Product Profile 冻结 company/item_code，服务器要求原生保存前快照一致；原生界面两项保存后只读。新建仍通过原生来源/公司权限校验，sync.py 的新建路径不变；不迁移或改绑历史档案，不改变 Item 全局唯一对应一个档案的规则。51 项档案模型通过，新增四项覆盖两个改绑、缺快照拒绝及新建/界面规则。原生 delta r3 19门通过（保留之前16门，追加 Admin 改公司/Item 两次拒绝及全部 Profile 父子行/Version 不变核对），批末原生回滚和独立新连接全表计数、完整商品/子表、档案/子表、File/Version/Series、员工、账号、补充档案和 GL/SLE 均相同。忽略证据 `classic-catalog_delta-native-result-r3.json`。该元数据已随 db7a8d6 合并批次迁移并只读核对，实际保存后只读 GUI 尚待。

975c5a2 为产品目录的独立档案图片增加缩略图格式化，与业务商品图片使用不同中文说明，复用服务器已守护的精确附件 URL 和 HTML 转义。JS 语法通过；实际浏览器图片字节/上传/显示仍未验收。

## 2026-10-01 产品词典后端与原文目录权限

655035c 新增公司范围的产品风格、来源、品牌词典，独立保存编号、名称、备注、简拼、停用；名称可修改，编号和名称未假定唯一。公司及类型固定，创建回执使用 UUID 和初始载荷摘要，改名后只读核对仍返回当前记录，不自动还原旧名；原生 Version、显式版本锁、Company/词条 User Permission 和字段权限继续生效。System Manager、Stock Manager、Manufacturing Manager 管理，不改员工、Item 或 Product Profile 历史原文。删除、编号分配、自动简拼、旧式界面及打印尚未完成，产品类别不套用此单值模型。

词典 r1 第五门的原文不变判据失败：插入后 Python 文档中的日期为字符串，SQL 重读为 datetime，原 as_dict 摘要比较混用了两种表示。e12e778 改用准确父行及所有已安装子表的完整 SQL 快照，保留 creation/modified 微秒、DECIMAL 精度和全部物理字段，不忽略业务或审计列。r2 原生15门通过，真实三种管理角色、三个类型、重名/重号、原生表单回执、元数据注入拒绝、可变名称与原档案/Item 父子行不变、停用、未知结果只读核对、过期版本、公司/词条范围、非法输入、删除拒绝及普通角色拒绝均已检查。r1 与 r2 的独立新连接全表计数、完整词典/档案/Item/File/Version、人员和子表、账号权限、补充档案及 GL/SLE 均恢复，Series 内容核验缺口由上方r4补齐；r1 不计通过。

产品目录增加五个独立原文列及独立私有档案图片，空原文或图片不从 Item 回填。所有实际返回字段要求原生读取字段权限；图片必须属于当前档案的准确字段，且 File.read 与 is_downloadable 同时通过。原文目录 r1 在同父多字段图片 alias 夹具的独立 File 假设处失败；未记录实际值，不能宣称已经证明原生去重细节。e12e778 采用官方 File.create_attachment_copy，默认保留权限验证，将同一私有 blob 复制到另一可读档案。r2 原生8门通过：真实 Stock Manager 调原生 QueryReport 取得准确业务/原文列和独立图片 URL，空原文不回填；准确源 File 被 File 专属 User Permission 拒绝时，即使另一档案同 URL alias 可读也不返回源图片。正例用原生 QueryReport.run，拒绝例用同报告核心，未冒称 HTTP 或报告外层拒绝验收；同父异字段和字段等级撤销仅模型覆盖。两张本地合成 PNG 共243字节，没有远程传输；原生回滚后准确磁盘路径消失、完整内容和全表计数恢复，独立新连接复核通过。

本批全仓798项 Python 模型、相关 Ruff 和 diff 检查通过。忽略证据 `classic-product_dictionary-native-result-r1.json`、`-r2.json` 及 `classic-catalog_original-native-result-r1.json`、`-r2.json` 保留失败和成功记录。当前后台迁移成功，最新前端构建/重启及旧式三词典实际界面仍待；此阶段未减少116个业务预览，也不代表产品模块交付。

## 2026-10-01 产品档案独立原文与私有图片

f2717a9 为 `Jifeng Product Profile` 增加可选的独立编号、代码、名称、单位、备注和产品档案图片字段。前五项保留原文，编号不合并 item_code 或旧号，备注不合并注意事项/默认要求；图片独立于共享 Item.image。没有迁移回填、改 Item 或增授角色，一件业务商品仍全局唯一对应一个 Product Profile。原生表单明确展示业务名称、库存单位和业务图片，先保存档案再上传私有档案图片；保存图片要求当前档案/字段的精确 File 绑定及原生 File.read，拒绝公开、远程、无 File、其他档案和其他字段的图片引用。

47 个专项、全仓759个纯 Python 模型通过，Ruff、JSON 字段顺序、Python/JS 语法及 diff 检查通过；上传配置的四类权限状态仅为客户端模型检查。原有57门 catalog smoke 文本保持一致，没有重复扩大整套旧功能验收。WSL 测试站迁移和完整构建均退出0，服务重启到本批代码。

新增 `catalog_validation.delta_smoke(company, item_group, stock_uom, other_company)`。r1 在最终回调审计失败：实际 Frappe clear_document_cache 会再次登记 after_commit/after_rollback，原 helper 恢复队列后又清缓存；该次独立新连接的全表计数、所有商品及子表、Profile/File/Version/Series、员工、账户权限、补充档案和 GL/SLE 均恢复，仍未计为通过。ae2dd41 调整为完成缓存清理后最后恢复原队列及上下文，逐项清理审计保留原业务异常及失败记录，不改业务权限。

r2 原生16门通过：真实 Stock Manager 和 Manufacturing Manager/Manufacturing User 分别保存原文及 Version，原注意事项/旧号/要求/价格重量保持；每个角色实际插入自己的私有 File 并保存引用。拒绝远程/公开/无登记/异档案/未保存档案/同档案异字段图片，Company 和 Item User Permission 继续生效，同 Item 异公司重复档案仍拒绝。实际4张随机内容本地 PNG 共473字节，没有外部传输，均调用原生 File 回滚清理，精确私有/公开磁盘路径全部消失；全表计数、完整商品、GL/SLE、回调和保存/实时上下文恢复。独立新连接再核对所有 Item 子表、档案子表、File/Version/Series、人员子表、账户及权限、补充子表、GL/SLE 与批前一致。忽略证据为 `classic-catalog_delta-native-result-r1.json`、`-r2.json`。

Luna 在更新后的原生 Product Profile 空列表打开新增表单，实际确认编号/代码/名称/单位/备注原文五个文本框可编辑，未保存并回到空列表。当前新建表单的图片控件未出现在实际快照中，不能算浏览器上传禁用或图片显示验收；新页控制台零错误、一条 Moment 日期解析警告，栈可见 convert_to_user_tz，但根因未定位。fresh before/after 核对完整商品及子表、Profile 及子表、File/Version/Series、人员/账号权限和 GL/SLE 相同、全表计数无变化，仅 Administrator.last_active 原生登录元数据变化。忽略证据 `gui-product-profile-browser-20261001.md`、`product_raw_gui_fresh_before.json`、`product_raw_gui_fresh_after.json`。此次检查使用原生表单，并未验证旧产品菜单中的专用新增弹层。

本批仍未完成旧产品29字段顺序的专用录入弹层、产品词典、复制及列表工具栏；HTTP 图片下载字节、实际浏览器上传/保存及岗位验收未通过。旧页面已经确认的原文可持久保存不代表产品模块完整交付，也没有减少116个业务预览缺口。

## 2026-10-01 旧产品四个词典的画布表头与空新增

Luna 通过可见菜单打开并核对选中状态及实际 iframe 路径，取得产品类别、风格、来源、品牌的脱敏表头截图，root 已查看截图复核。SpreadJS 在 AX 文本中显示 Canvas fallback，但画面实际绘出了表头，不能据 fallback 文案把列头当作未知。来源和品牌的截图哈希相同，对应分别确认的 `/jfmy/产品来源` 和 `/jfmy/产品品牌`，不是只凭相同文件名推断页面。

产品类别实际为两张并列表：左侧代码、大类、负责、区域、停用、删除；右侧小类、材质、单位、换算单位、换算值、停用、操作、删除。顶栏导出、导入、保存、更新简拼、关键词及查询。没有看到树控件；后续单击第二个可见父行时左右画布哈希均变化，观察到 GetTableDataWithOffset 和 GetTableRowData 请求，但未采集请求体、准确返回行数或选择返回字段，只能证明有限联动。焦点随后落到可见网格输入控件，未输入或保存，停止进一步操作。不能压成一个单值词典或擅套 Item Group，也不能把换算值直接写业务单位换算。

风格/来源/品牌实际共用九列：编号、名称、备注、简拼、停用、操作、日期、修改、删除；顶栏新增、更新简拼、关键词、查询、格式1、打印、预览。三种空新增弹层均依次显示编号（预填序列）、名称、备注、停用，底部新增/保存/关闭；简拼不在空新增输入项中。后续真实 DOM 属性核验三个编号 text 控件均 readonly=true、disabled=false，名称和备注可编辑，停用未勾选；各窗逐一关闭。空窗观察的序列为001/004/006，只说明该次预填，不证明编号分配/并发/唯一性规则。均未保存，只关闭返回原页。类别合成无匹配查询后已恢复空关键词；未做导入/导出、简拼更新、行编辑或删除。旧站导航有未归因控制台错误，不当作本项目错误或某词典操作的确定缺陷。忽略证据 `output/legacy-product-dictionary-browser-20261001.md` 和所列 header-only/blank-dialog 安全截图。

## 2026-10-01 人事词典实际界面与本机实时连接

本轮 Luna 子智能体已实际操作隔离站经典工作台，三个词典入口分别打开固定类型的新增弹层，公司、名称、停用及底部新增/核对/关闭可见，关闭后原词典列表和类型过滤保留。人事档案明确选择测试公司，在民族、文化、专长填入三个不同合成原文；各自打开关键词选择弹层，空结果关闭后原文完整保留。父层 X 的放弃确认选择“否”仍保留草稿，底部关闭选择“是”后返回员工列表，最终对话框为零、添加及过滤按钮仍在。未创建词条、未保存人员，没有实际词条行可供选择，不能算岗位浏览器保存或有结果选择验收。脱敏证据为忽略的 `output/gui-hr-dictionary-browser-20261001.md`。

原生只读复核确认员工及所有子表、补充档案及所有子表、词典/Version/Series、Auth/角色/User Permission 和 GL/SLE 内容未变。较早原生基线到首次登录之间，User 父表摘要有变化，Activity Log/Sessions 各增加一行，不能从旧摘要反推具体 User 字段。随后重新建立逐字段哈希基线，只允许 Administrator 的原生 last_login/last_active/last_ip 变化，再重复三项原文与关闭守护检查；fresh after 全部保护内容相同，User 字段和全表计数均无差异。对应证据 `hr_dictionary_gui_protected.json`（旧基线不通过）和 `hr_dictionary_gui_fresh_before.json`、`hr_dictionary_gui_fresh_after.json`（新基线通过），均仅存忽略目录，不含明文账户内容。

实时连接曾报 Socket.IO Unauthorized/fetch failed。已核对实际 Node 认证代码和 DNS：WSL 将 `jifeng.localhost` 解析到 TUN 虚拟地址，Node 访问本地后端失败。仅在 WSL `/etc/hosts` 增加本机 127.0.0.1 对该站名的映射，原文件备份在 WSL root 私有目录，前后哈希记于本地 `local-site-hosts-audit.json`；没有改上游代码、代理或 TUN 规则。新解析为 127.0.0.1，Node 后端 HTTP200；浏览器重新加载后 socket.connected=true，四次轮询 HTTP200、控制台零错误/警告。WSL 自动生成 hosts，重启后需重新核对映射。先前 `/undefined` 资源404在这次重载未复现，来源仍未定位，不能称为已修复。

## 2026-10-01 人事专长、文化和民族持久词典

a2c0056 新增公司范围的 `Jifeng HR Dictionary`，经典人事三个原菜单进入按类型筛选的正式列表，新增/修改使用弹层，底部新增/保存/关闭及 X 走草稿保护。人事档案在原民族、文化、专长位置增加关键词选择弹窗，仍允许保存既有及手填 Data 原文。没有改动人员字段顺序，未将民族映射到 Religion，也未推定旧词典编号或 Canvas 列。词条公司、类型、原文创建后固定；修正采用停用并新增，禁止删除，原生 Version 记录启停。这是本次明确设计，尚未核实旧词典内部操作是否一致。

HR Manager/System Manager 管理，HR User 只读/选择；没有扩大私密 Supplement 读取。API 使用原生角色、Company.select、具体词条权限、字段白名单及 get_list/逐文档检查；公司或词条 User Permission 限制继续生效。唯一碰撞转为不含文档编号/SQL 的通用校验消息，不提升查询权限或自动重发保存。d3ff20f 进一步替换原生唯一约束处理器已排入 UI 队列的消息，保留此前独立消息，并核验异常链不含原生重复错误。

客户端异步查询防过期响应；选择前重读词条，停用、权限收紧、父档案保存/关闭、改公司或等待期间继续输入均不会把旧选择写入当前草稿。保存响应丢失或不完整时冻结原草稿，只读核对原记录或同公司/类型/原文，未找到或内容不同不自动再次保存。关闭人事档案同时关闭其选择弹窗；保存后按最新字段权限恢复按钮。新增 HR 客户端协议、既有人事/库存/计件 bridge 协议与涉及 JS 语法通过；25 人事词典模型、全仓743模型、Ruff通过。

隔离站迁移、完整 bench build 均退出0。原生 r1/r2 各11门通过，r2增加消息队列和异常链检查：真实 HR Manager 新建三类词条、原生直接保存、同值跨公司、启停 Version、重复与过期版本拒绝、API/原生身份不可改、非法类型/Check拒绝、HR User 无 Supplement.read 仍可读取但无词典创建/写权、Company/词条 User Permission、不可读重复身份不泄露、只读核对、Admin 对引用及未引用词条删除均拒绝。引用和未登记的三项人员原文均保持；每批整批回滚后用独立新连接核对全表计数无差异、全词典/Version/Series、员工和全部子表、补充档案及全部子表、User/Role/UP/Auth、GL/SLE完整内容一致。忽略证据 `classic-hr_dictionary-native-result-r1.json`、`-r2.json`。

服务已重启。独立只读运行检查确认元数据唯一/固定身份/track_changes、Desk 脚本 hook 及三份实际 HTTP200 JS 与磁盘 SHA256 一致，证据 `hr_dictionary_runtime_read.json`。实际浏览器操作、旧词典内部列/管理习惯、岗位浏览器保存、打印/迁移导入仍未验收，Luna 启动仍受线程上限阻止，主智能体没有代做 GUI。当前363入口中229直接路由（7Page/91Report/34Create/97List），仍有116业务预览、13总经理导航、5工具/说明；全模块正式可用目标仍未完成。

## 2026-10-01 计件人员入口、当前资格和真实工资角色

ac81c0d新增 `jifeng-piece-workers` 并接旧工资基础资料“计件人员”。保留已核实的关键字/查询与人员档案计件提示；旧Canvas列仍未取得，新表的编号/姓名/部门及进入工资录入操作为本次明确设计，不能当作已复刻旧列。人员候选先经过原生Employee get_list及逐文档读取、公司/员工User Permission、具体Wage.create，随后内部只读取Supplement的工资方式及审计来源；HR User不需要完整私密Supplement读取。接口只返回必要资格与已有许可Employee字段，来源编号和版本为服务器摘要，Wage/分配子行permlevel1仅System Manager读取。实际HR User原生client.get返回这两个字段为空，没有摘要内容。

新增/改单/提交的计件工资核当前资格；数量及工价均0的纯计时不受计件资格限制。分配审核快照保留，但不能替代新生成工资或工资草稿提交的当前资格。已审核工资保留当时快照；早期已审核记录缺快照时不推定当时工资方式，取消仍走原生规则。默认HR User补六种来源的read/select（Job Card、Work Order、Item、Flow读取，Operation/UOM仅选择），无生产写/审核/取消/导出权。已有明确站点HR规则包括deny保持；实际迁移新增6条DocType审计注释，字段permlevel核对通过，忽略的 `piece-permission-migration-audit.json` 留存。

原生r1发现业务Meta不含标准name，查询未取员工标识；2023c0c按Frappe标准字段契约补从权限过滤列表取name。r2发现旧Workbench错误要求Company.read，ec30b0d改为选择公司权限，员工公司与具体工资文档权限继续校验。r3原生字段值已被清除，但as_dict按schema输出null键，修正只要求不可读字段无值；r4模拟旧记录时错误将NOT NULL Check设null，改用旧迁移默认0。后两次为oracle/夹具修正，没有扩大读取权限。各次失败全批回滚并独立新连接核对全表计数、人员/账户/权限/补充档案、完整工资/分配子表/附加薪资、制造来源及全GL/SLE一致，失败未计通过。

r5专用原生8项通过：真实纯HR User无Supplement.read但按已审核报工保存计件草稿、原生Company/Employee范围与Guest/财务角色拒绝、纯计时、重复请求只读复用、方式变化后的新建/改草稿/提交拒绝、已审核快照取消、分配快照不能绕过新工资资格、早期无快照工资取消及整批回滚。既有工资Workbench r6原生21项、多人分配至工资条/财务付款原生32项回归通过。每批独立新连接完整内容哈希及全表计数一致。证据为忽略的 `classic-pieces-native-result-r1.json`至`-r5.json`、`classic-wage-native-result-r6.json`、`classic-allocation-native-result-r1.json`。

全仓718项模型、Workbench客户端24项、人员选择9项、classic跨iframe复用/脏草稿/未知结果/关闭取消、库存及人员既有协议通过，改动Python Ruff/JS语法通过。classic复用原工资标签而不在人员iframe另建工资台；选择期间保护控件与业务动作，资格异步响应后改公司/姓名或关闭的结果不覆盖新输入。迁移及完整bench build退出0，之后仅Page JS选择控件保护调整，清缓存和重启加载。实际新界面/旧表格、真实角色浏览器保存、打印/导出与并发资格变更仍待验收，Luna线程上限未解除。363个入口中226直接路由（7Page/91Report/34Create/94List），剩余119业务预览、13总经理导航、5工具/说明；目标仍未完成。

## 2026-10-01 已有业务物料关联旧式库存档案

12ec86d补齐明确选择已有Item并新增Profile的入口，不按旧编号/代码猜测匹配。关联先检查Profile具体create、Company.select、Item具体read及编号字段权限，保存锁定当前Item并核对原生版本，再检查已有绑定。纯Stock Manager无需Item.create/write即可关联已有物料；新建Item仍要求原生Item.create。业务Item从不save，Item名称/单位/子表/默认值、价格表和账簿保持；旧式档案的编号、代码、名称等独立保存。一个Item仍只能绑定一个Profile，已有隐藏公司的绑定拒绝不透露其档案编号或公司。UUID摘要加入所选来源；既有新建Item收据摘要保持兼容，重复关联核对原收据，改来源拒绝。已有档案不能重新绑定。

库存模型24项、客户端协议及JS语法/Ruff通过。r4原生14门通过，新增纯Stock Manager关联成功、缺/过期来源版本拒绝、伪造Item改写及异公司/非法档案拒绝、重复收据与改来源拒绝、隐藏重复绑定不泄露身份；另一个真实Stock Manager带原生Item User Permission，只能关联允许的物料。未增加DocPerm或给用户越权。测试整批回滚，独立新连接全表计数无变化，完整Item与所有安装子表、ItemPrice、两类档案、Version、员工/账户/补充档案及GL/SLE哈希相同。忽略证据 `output/classic-inventory-native-result-r4.json`。

客户端在原23/24字段顺序后提供“关联已有业务物料”和有权限时的“新建业务物料”操作。切换来源保留已填写档案值，所选业务物料的单位/分组只读，未知保存结果不能换来源。实际新界面、旧库存词典内部列、换算方向、导入/打印与两连接关联竞争仍未验收；本轮Luna启动受线程上限阻止，没有由主智能体代做GUI。

## 2026-10-01 两类库存档案、词典与保存响应版本

95505c6新增Inventory Profile/Dictionary及联合录入API，旧物料/材料字段顺序分开，编号/代码/旧号独立；创建原生Item明确opening_stock=0、standard_rate=0。既有档案只改Profile，真实Item名称、单位、uoms/defaults/估值等保持；参考价格、换算值及停用不产生价格表、库存或财务动作。分类按公司/种类/词典类型持久保存，材料形状颜色不能写入物料；公司/绑定/请求身份不可改，原生Form也守门，档案不能删除请求身份。复制当前为清身份的新草稿，不能当作已复刻旧复制语义。创建有UUID4及载荷摘要，重复同请求读原档案，不同载荷拒绝；并发唯一冲突仍整体回滚，未做两连接建档实测。网络结果未知冻结当前草稿，只读核对后恢复，不自动重试创建；跨刷新草稿恢复未实现。本批界面结构与行为只由客户端协议检查，实际GUI及岗位浏览器保存没有通过。

首次两次直接Python启动在Frappe日志初始化失败（工作目录先继承Windows、再为bench根），均尚未建立数据库连接或进入业务套件；修正为bench/sites，记录 `output/inventory-native-runner-setup-failures.json`。r1实际创建后紧接更新发现TimestampMismatchError：原生save返回modified空格字符串，数据库重读datetime经序列化变ISO T。失败整批回滚，独立新连接tab计数无变化、员工/账户/补充档案及GL/SLE哈希不变；未将失败算通过。f64fc98修复两个档案API共用的版本比较，保留精确微秒及过期守门，拒绝无效/超精度/时区输入，不改变原生Document时间戳校验。新增4个针对真实格式的模型，全部690项通过；库存客户端未知结果/核对/复制源脏数据/已有绑定及HTTP环境UUID协议、既有人事协议、JS语法及改动Python Ruff通过。

r2原生库存11项通过，另一个新连接只读核对完整Item及所有已安装子表、ItemPrice、Profile、Dictionary、Version哈希均与批前相同。f08364b补验真实正常建档角色后r3原生12项通过：两类联合新增、Item身份独立、全Item字段/子表/价格保持、实际Version、重复UUID与改载荷拒绝、两版本过期与复制清身份、第二文档失败导致Item/回调/上下文联合回滚、原生伪造新建/身份修改/删除/跨类字段拒绝、词典隔离与原生改绑拒绝、纯Stock Manager创建缺Item.create正确拒绝及可更新自身公司档案、隐藏异公司结果拒绝、Stock Manager+Item Manager无System Manager实际联合新建、Stock User只读与Guest拒绝。未更改DocPerm或真实用户角色。所有合成记录/Version/价格表/回调整批回滚；独立新连接计数变化为空，完整物料/所有子表/价格/两档案/Version、员工/账户/人员补充档案及全GL/SLE一致。原生未做并发、导入或浏览器保存。

同根因的人员档案r4原生14项通过，新增直接使用上一保存响应employee_modified/supplement_modified再次保存，不重新查询掩盖问题；旧资格/经历、关联账号及权限依旧保持。整批回滚后的新连接全表计数及员工/账户/补充档案/账簿哈希不变。私密证据为忽略的 `classic-inventory-native-result-r1.json`至`-r3.json`、`inventory-native-r2-fresh-content-audit.json`、`classic-personnel-native-result-r4.json`。

两个新增DocType的migrate与完整bench build退出0，版本修复只有Python无需重复build，最后清缓存并重启8000，原生Socket.IO9000持续监听。新旧界面Luna再启动均被agent thread limit reached阻止，没有绕过限制由主智能体操作GUI。当前363个菜单包含225个直接路由（6Page/91Report/34Create/94List），仍有120个业务预览和各项语义/岗位/界面验收缺口；全部旧模块正式可用的目标仍未完成。

## 2026-10-01 教育新表实际界面及提交数据

Luna 在127.0.0.1真实登录后实测六列表头顺序、新增两条未保存记录、修改第一条学校与学习内容、切换到调薪再返回保留数据、删除第二条。X和底部关闭均显示“否/是”放弃确认，选是后原列表唯一筛选仍匹配1项，非零scrollTop=90保留。没有发送save_archive，不能将其算作浏览器保存。未捕获pageerror；console有19次404及15次Socket.IO轮询错误，故不能记为无console错误。原始证据 `output/playwright/education-table-review.json` 与截图保留。

源码复核实际Frappe Grid：非Form的Dialog新行标记__islocal后会自动补随机name，旧前端把该name提交为已保存身份，服务器正确拒绝“明细不属于当前档案”。02518f4修复仅发送已保存行的身份；所有四种历史表统一处理。人员前端协议检查采用真实临时name+__islocal行形状通过，涵盖新调薪/教育行省略name与客户端标记、保存响应用服务器ID重复编辑、权限字段过滤、失败保留草稿。原有后端拒绝外来ID不放宽。7fc8c60另删除旧教育维护按钮保存后强制启用逻辑，表格按钮继续遵循权限。两项前端修复已完整build，服务重启。

日志确认轮询请求错误发到8000/socket.io，原隔离站仅启动bench serve且缺bench start所设DEV_SERVER环境和实时服务。现本地启动原生9000 Socket.IO，web按标准开发环境DEV_SERVER=true重启，两端口监听正常；没有禁用异步功能或改全局数据库配置。后续Luna真实浏览器核验9000轮询均200，两次WebSocket建立、无断开或错误且connected=true；没有Socket.IO 404、其他请求失败或pageerror。仍有一条/undefined图片404，parser发起文档为/desk；另两条/desk/undefined图片请求200。请求前后DOM及文档标签扫描均未找到对应控件，不能声称已定位或修复该资源问题；本批未复现favicon404。

同一实际浏览器Dialog中，新教育行确有随机name及__islocal=true；使用已加载的人员编辑器payload方法对实际cur_dialog序列化，name和__islocal均省略，合成学校与学习内容保留。随后实际X→是关闭并丢弃，save_archive请求数为0；这是实际表格载荷验证，尚不等同浏览器保存或真实岗位验收。脱敏证据及截图为忽略的 `output/playwright/education-payload-runtime-review.json`、`.png`。

## 2026-10-01 领料并发与来源保护补验

3b138d8 修复原生 Flow.reload 保留非锁 flags 时仍读旧 RR 快照的问题：工厂显式锁后 reload 使用 for_update，保存校验直接加载锁定的当前 Flow。新增 Stock Entry mixin 在原生 check_if_latest 之前按排序锁当前及原有 Flow；保留原生权限、时间戳和状态流转，锁后的实际旧来源若不在预锁集合就整体拒绝，不在已持库存锁时补取 Flow 锁。已有凭证的领料头来源不能清空、替换或从空重新绑定，关闭仅靠前端 read_only 可绕过领料上限的路径。隐藏关联库存凭证的拒绝提示不含其编号。移除分析行后仍列出该物料已提交出库，需用0、已发6、差额−6；新领料仍要求有效分析。

两个 Flow 共享手工出库凭证时，详情原先可能先持各自子行再等待同一父单。当前来源发现及父单预锁使用 NOWAIT，已提交数量扫描也使用 NOWAIT；占用时整体拒绝，不跳过、不给部分余额、不内部重试。dc80c4e 为 Stock Entry.jf_material_issue_flow 和 Stock Entry Detail.jf_flow_order 添加索引；测试站 migrate退出0，并用 SHOW INDEX 确认两列索引存在，避免无索引扫描造成无关库存行锁。

668项 Python 模型通过，其中14项新增早期锁模型，以及忙碌凭证拒绝的模型。原生材料 r5 在恢复已删除分析子行的测试夹具失败，原有已删除 name 不能用 DB update 重新插入；213b498 改为原生新增子行，r6原生15项通过。新增头来源三种绕过拒绝、隐藏编号不泄露、删除分析仍可核对已发，且原有分6/9提交、超量拒绝和取消净账继续通过。失败与成功夹具均完整回滚；独立新连接全tab计数无变化、员工/账户/补充档案及全GL/SLE哈希不变。

另以两个真实连接做8项检查，在专属新Item/仓库/两Flow/两Stock Entry草稿上运行，无任何提交、取消或库存财务过账：

- 本站实际 REPEATABLE-READ + innodb_snapshot_isolation=1 时，旧快照遇已变Flow以原生 ER_CHECKREAD(1020)拒绝，需结束事务重新查询。
- 只在测试子连接临时关闭 snapshot_isolation，证明标准RR锁定当前读采用新需用7，允许6、拒绝8；连接销毁恢复默认，未改全局/站点配置。
- A持Flow时，B原生Stock Entry.save先等待Flow；A可获取库存父单及全部子行锁，释放后B完成保存并回滚。
- 多Flow共享凭证被另一连接占用，详情收到原生QueryTimeoutError后泛化拒绝；失败连接回滚后不遗留子行锁。

添加索引后的最终8项全部通过，独立新连接确认7个专属主/草稿夹具均不存在，wholeGL/SLE、员工、账户、补充档案哈希不变。保留原生审计新增3个Version、7个Deleted Document与7个Deleted Comment，逐条确认只指向本批专属夹具。原生Deleted Comment将身份存于subject而不是reference_name，初次审计分类失败及只读补验保留；最终新批映射正确全部通过，未为通过而删审计或重复库存过账。前序缺参考估价及站点快照保护导致的失败批次也保留，没有冒充通过。

证据位于忽略的 `classic-materials-native-result-r5.json`、`-r6.json` 和 `material-concurrency-JF-CONCUR-*.json`。本应用主动锁库存单的两个调用方已核对先持Flow；第三方预先锁库存再保存不受早期mixin兜底，须遵守该顺序。本次没有做并发原生审核/取消、完整大数据压测或真实岗位界面保存，不能将8项外推为所有库存并发均已验收。

## 2026-10-01 教育经历子表

d53f0b3 已同步隔离站，migrate、完整 build 和服务重启成功。653项 Python 模型通过。独立 direct Python 人员 r3 原生13项通过，新增教育经历插入、更新学校而保留行标识及学习内容，非法日期与员工修改联合回滚。整个夹具回滚后另一个新连接确认全 tab 表计数无变化，wholeEmployee/children、User/Auth/Role/Profile/UP、Supplement/children 和 wholeGL/SLE 内容哈希不变。旧 Employee.education 没有被新旧教育字段互相覆盖。新增教育表格的浏览器保存尚未验收，之前“教育经历”维护按钮的界面记录不算本次新表验收。

Luna 旧站只读取得教育6字段与物料/材料档案完整顺序、独立类别词典入口；未保存、上传或更改旧业务记录。空 Canvas 的词典内部列和项目控件绑定仍未知，不能据此声称完整数据语义迁移。证据为忽略的 `classic-personnel-native-result-r3.json` 和 `playwright/legacy-catalog-education-review.json`。

## 2026-10-01 算料/汇总与本批实际界面

1602b41–5fcdd0b 已本地提交并同步隔离站。新增Page的站点migrate及完整bench build均退出0，最后保护修复同步后清缓存并重启8000。652项Python模型通过，新增12项算料请求状态协议通过：过期查询不显示、条件变化需重查、未知生成结果冻结、核对失败仍冻结、成功只打开原草稿不再POST、HTML数据转义。协议检查不是浏览器。

`order_material_workbench_validation.smoke` direct Python r1在真正Stock Manager的Company完整读权限处失败，改用与库存报表一致的原生Company选择范围，仅派生本币；没有授予Company资料读取。r2发现纯Stock Manager没有Warehouse读权限，保留拒绝守门，夹具先证明拒绝，再加入原生Stock User作为正常仓库岗位组合。r3及最终5fcdd0b r4共13项通过：精确物料分析行/预算、相同物料不同单位分别汇总、关键字和日期/指定日期、真实2倍Item单位换算、旧版本/异公司仓库拒绝、草稿不记账/重复复用/不同仓库不默改、分6及9两次原生提交、超量10拒绝、领完不额外生成、取消恢复余量、错流程明细/源变化拒绝、Stock Manager+Stock User真实查询/详情/草稿与Company权限、隐藏相关出库凭证时拒绝给不完整余额及生成、Manufacturing User不能写、取消后全SLE净量等于原生收货30。库存累计扫描和唯一草稿查询采用锁后当前读，validate在Flow锁后reload；双事务竞争仍未另做验收。

每次失败与成功均整个保存点回滚，另一个独立新连接核对 `count_changes={}`，wholeEmployee/child、User/Auth/Role/Profile/UP、Supplement/child以及wholeGL/SLE哈希不变。合成UOM、Item、Warehouse、Flow、Stock Entry与角色夹具无永久残留。私密证据 `output/classic-materials-native-result-r1.json` 至 `-r4.json`，失败没有按通过记录。

Luna旧站只读确认算料/汇总前18/13列及工具栏，配料父页分两入口，空Canvas未核实行内行为。新站Admin真实菜单进入、默认日期/指定日期/格式1、日期查询返回3行/API200、双击详情及原生流程只读打开关闭、筛选保留均通过；监听期间没有console/page错误，没有出库生成、审核、撤销、打印或导出。滚动为空不算算料滚动测试。源字段未知的旧分类、形状、颜色、库存等显示未提供；保留列头不等于数据语义已迁移。查询当前最多一千个可读流程，超量要求指定流程，不做部分汇总。

同批补人员/工资实际界面：127.0.0.1真实登录成功（此前jifeng.localhost一次输入被拒，原生只读哈希确认测试secret匹配，改直接Node内部fill后HTTP200；未修改账户或打印凭据）。新增人事四标签、字段顺序、未保存关闭取消/放弃及原列表筛选/50px滚动保留；工资Page、当天日期、人员/工序选择与Escape、双击姓名计时弹窗已通过，无保存。仅勾多人/多工序没有自动加字段，这是触发方式检查，不能计为两种来源录入验收；工资默认含义、教育列、真实岗位保存仍待。证据为忽略的 `output/playwright/classic-personnel-wage-review.json` 和 `order-material-workbench-review.json`。菜单已核算363/221，124业务预览未落地，整目标继续进行。

## 2026-10-01 人员与工资专用录入

050c27d–3c17f0d 已在隔离站点同步。050c27d站点迁移与完整资源build均退出0；3c17f0d工资改单schema迁移退出0，服务器清缓存重启。当前全仓651项Python模型通过；17项工资客户端协议、人员载荷/身份/重复保存/脏关闭协议检查通过，41份JS语法/50份JSON解析/159份Python AST的先前检查仅对应首次录入实现，新改单改动另做编译与schema检查。命令曾用不存在的 tests 包模块路径导致ImportError，改用实际 unittest discover -s tests 后651项通过，没有将错误命令算通过。

人员 direct Python 原生r1在 Employee教育/外部/内部经历的整行比较失败：真实Frappe普通保存更新每个子行 modified/modified_by，旧项目、备注、CTC已通过，并非业务列被清空。修正oracle仅排除这两字段且要求等于新父，其他全部列/身份/顺序仍精确；同时修复显式公司/部门赋值前doccreate检查默认链接可能误拒合法选项的问题，无角色授权变更。cae13cb r2共12门通过：实际纯HR Manager/HR User、字段元数据、三旧历史子表与稳定ID、原生Version具体改动、后半补充档案天然日期错误引发联合保存回滚、无权/异公司/异部门、伪造生命周期/历史ID/私有图片、双版本过期、原生新建必填与账号不创建。关联停用User未启用或改资料，原生教育/经历业务列及其他员工全部保留。12门包含两门整批回滚检查；业务夹具没有提交。证据 `output/classic-personnel-native-result-r1.json`、`-r2.json`。

工资失败证据完整保留：r1为测试第二独立工单先开第二工序，原生正确报OperationSequenceError，改为两份合法单工序BOM；r2因原生允许值字段列表排除Table，time_logs及operations错误按物理列检查，现分别核对父Table的schema/permlevel与child.parenttype字段，未给生产写权；r3为测试copy取消单未置草稿；r4实际发现两自有工资单据虽然amend=1却缺amended_from列。3c17f0d补齐两SelfLink，核对实际Desk from_amend会保留no_copy字段，因此服务器在原生amend权限、源read、取消状态后清新改单复制的请求/生成引用，旧历史保留；真正原生恢复仍先核对删除快照与服务器token。每次失败后整批回滚，独立新连接全tab计数变化均为空，员工/全部child、User/Auth/Role/Profile/UP、Supplement/child及wholeGL/SLE均相同。

工资r5实际21项通过：纯HR Manager只有生产来源read/select，两个真实不同工序来源与金额、同UUID/同载荷复用、改载荷拒绝、明确手工计时、删除草稿后的原请求保护、System Manager原生恢复并核对请求、HR User有create无amend的两类拒绝、原生单人改单释放复制请求而保留旧收据、两个普通计薪草稿NULL唯一键、最后一条失败全批回滚、源版本过期、同工序多人必须主管分配、手填1/4=5的分配改单产生新父/新行精确工资且重复生成不重复、工资先撤销再撤销分配、员工非来源成员、伪造/替换请求、异公司与Accounts User拒绝、GL/SLE不变。`rollback_verified=true`；原生分配/工资/附加薪资及所有合成商品、BOM、工单、报工、角色数据全批回滚。证据 `output/classic-wage-native-result-r1.json` 至 `-r5.json`。删除保护依赖站点原生删除日志保留期，框架默认180天，未宣称永久凭据或双事务竞争通过。

两个成功批次均由另一个独立新连接只读核对：`count_changes={}`，wholeEmployee/child、User/Auth/Role/Profile/UP、Supplement/child与wholeGL/SLE hash不变。本批未进行浏览器保存、登录或业务变更；新Luna启动返回agent thread limit reached，故新人员大弹层、4标签、工资双击与关闭流程均未算实际浏览器通过。576553e基础桥接Admin UI证据仅覆盖旧框架。教育原列/账号控件/词典/复制、工资默认含义及计件入口、真实上传/导入/格式/打印和真实岗位界面仍待验收。入口统计仍363/219，126业务预览仍待落地，全模块交付条件未满足。

2026-09-30：新增正式委外收回进度、逐笔收回台账与原料发退台账。进度以订单日期选批次、来源订单明细为键，读取该批次全部已提交收货及实物退货；净接受、净拒收、确认损耗、累计实物退回、尚未覆盖和当前待收回分列，Closed 不计待收回和逾期但保留剩余量。台账以每张凭证记账日期，按行保留订单/流程/原收货及原明细，退货数量显示负数。不能使用原生 returned_qty 缓存代替实物：拒收仓退回持久化后 qty 为负、临时拒收旗标消失，报表按原接收/拒收仓库归池；原仓库相同而两池均有数量时报无法区分，不猜测。收货和订单数量各乘自身 conversion_factor 转库存单位，副产品不增加主订单收回；原料发退取 transfer_qty，退料为负净变动。不同单位不合计。

`subcontract_validation.smoke` 30 项真实原生检查通过：服务采购单→委外单→发料，两批接收5/拒收2/损耗1，接受与拒收各退1后净4/1/1、实物退2、已覆盖6、未收4；退回取消恢复数量，第二批取消后尚未覆盖8，关闭/重开及订单日期批次/收回记账日期正确。损坏原收货订单明细、退货原明细和两订单链接会被拒，隐藏相关退货时报读取不完整；业务角色经原生 QueryReport.run 可读，订单权限隔离和Guest拒绝通过。`subcontract_material_validation.smoke` 20 项通过：原料发出10/退回3/净7、准确原料来源明细与流程继承、草稿与取消不计、不同供应商筛选、日期/成品/原料/流程条件、借用和缺失来源拒绝、可读库存凭证却不可读源订单时拒绝、真实报表接口及权限。全部合成业务和台账保存点回滚，每次报告查询前后GL/SLE数量守卫不变。114项Python测试、改动文件Ruff、JS语法、站点迁移和完整资源构建通过；不同换算系数由纯模型测试，当前原生合成凭证均系数1，未宣称非1原生库存验收。

旧外发未收、外发明细/报表、收回明细/报表和电镀明细/报表/跟踪已连接上述正式报表；两模块新增原料发退台账，采购及库存角色加入工作台入口。两个模块当前按公司、供应商、商品和流程筛选共用委外数据，未推断电镀专属分类、工序或对账；数量报表不代表应付余额或外币成本。准确原生服务成本源码未显示 supplier_currency 金额显式换算，外币成本仍待单独验收。

同批有数据的管理员浏览器只读验收通过：经典工作台未收进入中文“委外收回进度”且仅待收回=1，计划10/净接收4/净拒收1/损耗1/实物退回2/已覆盖6/尚未覆盖与待收回4/60%/逾期2；外发明细和报表清除仅待收回，电镀报表与跟踪清除逾期和待收回。收回明细/报表及电镀明细进入中文台账，4行正收回和负退回，拒收仓退回正确为接收0/拒收-1/覆盖-1。两处原料发退台账显示实际夹具发出10/退回0/净10，来源凭证、订单、供应商、流程、商品、原料、仓库和单位链接存在；浏览器未验收原料退回行，退3/净7来自上述后台原生检查。页面级异常0，单进程开发站点Socket.IO 404单列。截图与结构化证据在 `output/playwright/subcontract-*.png/json`。曾以子串匹配“收回”误点更长标签而得到台账，旧 `subcontract-receipt-entry` 证据已标为 invalid_substring_match；补正后字面“外发→收回”精确点击打开未保存 Subcontracting Receipt“委外入库”新建页，未编辑或保存，见 `subcontract-receipt-correction.json` 与 `subcontract-receipt-literal.png`。旧收回反审仍未正式映射。

持久浏览器夹具 `JF-SUB-BROWSER-d08ff596729a` 已按精确记录原生逆序取消：所有已提交QA凭证数为0，SLE均已取消，各商品/仓库库存及估值、采购待订、委外预留和预计库存归零，按账户GL净额为0，三份正式报表均返回0行。原生不可变账簿要求保留凭证及来源主档；删除按原生接口受历史链接限制，没有强制删除或手删台账。三个Warehouse抛出的特定历史SLE阻止删除已用准确源码和全仓精确QA历史归属验证归正常留存，其他错误仍单列。3商品、1供应商和3仓库已原生停用，记录原值、停用前后GL/SLE内容完全一致；流程单无停用/业务取消字段，保留QA来源，BOM及采购/委外/库存/收回凭证均已取消。私有记录 `site/private/jifeng_qa/subcontract-browser-d08ff596729a.json` 最终阶段 cancelled_history_retained、清理异常空、设置恢复=true、库存/总账恢复=true、主档停用=true；`cleaned=false`明确表示历史未删。没有生产站点数据改动。

2026-09-30：新增工作站/工序默认部门、工单工序派工快照及报工部门继承；部门订单查正式报工，部门逾期和部门进度查正式源工序汇总。工单显式部门优先，其次工作站和工序默认值，已派工历史不随默认值改变。部门汇总按工单建立批次读取全部可读报工，以实际工单工序行去重计划量；原生待完成数量属于转交其他卡，明细本卡数量扣除转交余额。真实续卡首卡计划5/完成1/转交4、续卡4/完成4后，源工序仍计划5/完成5/剩余0/100%，无假逾期。原生纠正卡保留原工序来源，以纠正工序保存提交，正常数量不增加，正常工时1小时、纠正工时0.25小时分列；错工序、错来源和隐藏来源被拒。`department_validation.smoke` 32 项通过，合成业务和台账保存点回滚。生产角色新安装规则明确 read=0/select=1，保留已有角色规则；报工范围的部门权限只返回本部门，全局部门权限导致整张跨部门工单不可读时，报表排除该工单。既有生产提交及制造入库 15 项回归通过。

2026-09-30：计划图片接正式工单进度，优先可读流程单，回退可读商品；站内附件用原生 File 下载权限，私有图使用原生文件标识，拒绝把无权地址放入返回值。`production_picture_validation.smoke` 13 项真实检查通过，包含 Manufacturing User 的来源/附件权限、无权图隐藏、商品图回退、缺图、Guest、报表接口及数量单位。唯一合成32×24蓝橙PNG已通过原生File删除并核对实物；业务数据回滚，无库存或财务记账。91项Python测试、改动文件Ruff、JS语法与完整资源构建通过。部门和图片有数据的浏览器核验已完成，结果见下一条；不据此判定旧系统全模块可交付。

2026-09-30：部门/图片真实浏览器核验通过。经典工作台部门订单显示两张正式合成卡：原派工5/5、转交0/4、本卡任务5/1、已审核5/1、本卡未报完0/0、各0.5小时。部门逾期进入部门汇总并预设仅逾期，只返回部门2（计划5、完成1、剩余4、20%、逾期工序1）；返回部门进度后逾期条件清零，两部门为100%/20%、计划5/5、损耗0。图片报表中文状态“生产中”返回计划5、入库0、剩余5、0%、逾期4；带原生文件标识的私有合成图真实加载，尺寸32×24。左右滚动截图保存于 `output/playwright/department-*.png`、`production-picture-image.png` 及 `production-picture.png`，明细为 `department-picture-results.json`；标题已显示部门生产进度/图片生产计划。页面级异常为零，404仅为开发站点Socket.IO轮询；浏览器使用管理员，生产用户/用户权限由前述真实后台测试核对，未进行浏览器保存或提交。

本轮持久合成夹具已清理：两商品、两部门、两工作站、两工序、BOM、流程、工单、两报工及5个原生图片附件引用均为零，唯一图片实物删除、生产设置恢复。原生系统会把商品图片引用附到商品、流程、BOM和工单；清理现按精确QA文件路径、唯一PNG字节和夹具父单/子表身份追踪全部引用，保留独立QA哈希与原生哈希。前三次夹具创建断言失败均已回滚，遗留唯一QA实物在验证无File引用、无业务对象且字节匹配后用原生文件删除恢复，并在私有夹具JSON保留恢复记录；未删除业务上传。最终夹具日志为 `site/private/jifeng_qa/department-browser-909e32dc69ac.json`（阶段cleaned）。此验收覆盖普通工序任务，未验证半成品实际产出比例：本地v16派工和制造入库直接沿用任务数值，无自动比例换算，不能只调整报表比例。

2026-09-30：生产指令本轮最终验收：`instruction_validation.smoke` 27 项通过，包含 Manufacturing User 实际调入流程、插入和提交计划、调用原生工单生成及打印。实际发现该角色可选择公司但不能读取完整公司档案，调入接口现与生产报表一致使用权限过滤的公司名称查询，不授予额外公司读取权限。79 项测试、改动文件 Ruff、JS 语法和差异检查通过；站点迁移、完整资源构建及既有生产提交链路 15 项复核通过。浏览器由实际选择器将 5/3 数量及对应商品/BOM/流程带入未保存计划，统一改为 2026-10-07 08:00；已保存合成草稿的真实打印预览为 7 列横排、两款完整、8 Nos 合计、参考金额仅一次币种、日期无微秒，并说明审核工单量不含草稿。未进行浏览器保存、提交或生成工单，未发现 pageerror；开发 Socket.IO 404 单独记录。证据位于 `output/playwright/instruction-*.png` 和相关 JSON。浏览器合成指令、流程、BOM 和商品已按确切标识验证后清理；后台保存点测试全部回滚，指令测试商品及用户残留数均为零。打印验证为页面和原生 HTML 渲染，未验证 PDF 导出、实体打印或分页。真实角色浏览器、生产损耗后的补排现场规则与旧指令其余字段仍需验收。

2026-09-30：生产指令扩展到原生 Production Plan，保存多款商品与流程单明细，记录原编号、指令日期、重量及显式单位、公司本币参考金额和备注；启用原生 Version 变更记录。新增调入流程单与统一排期按钮，打印按库存单位分别汇总数量。原生批量生成工单时跳过 validate，已把流程继承与数量守卫放到 before_validate；针对关联流程单的计划明细，生成量另计已有草稿，重复点击不增加工单。数量校验覆盖同流程分批、每条计划明细及其他有效工单；已提交工单损耗按原生净承诺量处理，取消释放安排数量。锁定来源及使用当前锁定读取避免沿用旧数量快照。旧编号不替代原生主键，参考金额不会过账。原生提交检查、打印文字转义、版本记录、来源变更、BOM 变更、超量、Guest 权限及无库存/总账记账已通过；脚本 `jifeng_erp.instruction_validation.smoke` 使用保存点回滚合成数据。既有生产报工→制造入库的 15 项检查复核通过。原“调计划”窗口的实际语义、指令工序/工期等旧列、真实角色与现场单据仍需核对，不将当前按钮解释为旧流程已完整复刻。

2026-09-30：生产模块浏览器只读复核通过。经典工作台“生产汇总”进入正式进度报表，“未投产分析”预设 `Not Started`；“工序损耗”预设已提交且有损耗，切到“工序逾期”后审核状态恢复全部、损耗条件清零、只保留逾期条件，回汇总时状态条件也清空，避免 Frappe 保留上次筛选。中文口径说明均可见。测试站点真实查询返回空数据，未渲染结果行及列标题；实际数据与列定义由前述后台提交链路核对，浏览器实际结果行布局仍待验证。“完成”进入 `docstatus=1/status=Completed` 工单列表，“入库单”进入 `stock_entry_type=Manufacture` 库存凭证列表，“生产指令单”打开新建 Production Plan，显示选物料/销售订单/物料需求等中文来源和待生产成品计划明细，未保存；此路由核验不等同旧指令字段完整映射。报工流程单 Link 元数据存在。页面级 JS 异常为零，开发单进程 Socket.IO 轮询 404 单独记录。截图及实测记录位于本地 `output/playwright/production-*.png` 和 `production-results.json`。

2026-09-30：新增正式生产进度与工序报工明细报表，先核对公司可读权限，再用权限过滤的 Work Order / Job Card 查询；报工明细同时限制可读工单范围。工单按建立日期查询，完成与损耗读取制造入库更新值；报工按业务日期查询，草稿和已提交完成/损耗分列，取消不计，不同工序不汇总成成品数量。实际测试发现流程单生成工单未加载 BOM 工序，现已调用原生物料/工序加载方法并保留半成品模式；工单提交自动生成报工单且继承流程单，冲突及公司/商品不符被拒。Manufacturing User 新增流程单只读权限，并可进入工作台；在隔离站点保存点内以该角色实际保存并提交两道工序，首道完成 5，末道完成 4/损耗 1，各记录 0.5 小时，报表草稿/审核分列正确，工序损耗入口只返回末道。报工提交后工单入库数仍为零；实际制造入库成品 4/损耗 1 后工单 Completed、剩余 0、结清进度 100%、逾期归零，入库库存明细保留流程单且台账成品 +4。限制该用户只读一个工单时另一草稿工单不出现在报表，标准报表接口及经典工作台权限通过，Guest 被拒。可重复 `jifeng_erp.production_validation.smoke` 15 项检查通过，合成商品、工序、工作站、用户和台账已回滚。79 项单元测试、Ruff、相关 JS 语法、差异检查、站点迁移及完整构建通过。角色浏览器、旧指令字段/打印、多部门与多人报工/计薪仍待进一步验收。

2026-09-30：退货净回货读取的浏览器核验通过。目标 `JF-COST-2026-00002` 初始无批次；真实 API 返回空数组并保持无改动。仅浏览器拦截的合成候选净回货 3/损耗 1/已退回 2 新增一行，表格为回货 3、待检 2、损耗 1，并处于未保存状态。随后合成净数量改为 2，界面提示需核对且保留原分类；改为 0 时提示删除已有批次，另一个全退回来源不新增行。刷新后批次归零、未修改状态恢复，全程未保存合成响应。页面级 JS 异常为零，另有资源 404 和单进程开发环境的 Socket.IO 轮询错误，未计为业务验证通过的内容。截图保存在本地 `output/playwright/receipt-return-*.png`。浏览器真实已提交来源的分类仍待现场验收，后端真实提交链路结果见下一条。

2026-09-30：回货批次现按已提交原收货明细关联的退货计算净数量，保留原工艺损耗；不使用包含损耗的原生 `returned_qty` 状态字段作为实物退货量。读取权限无法覆盖全部关联退货时拒绝汇总，旧分类与净数量不一致时禁止保存，全额实物退回后仍有损耗的来源继续保留。隔离站点可重复执行 `jifeng_erp.diagnostics.smoke_receipt_batches`：实际创建并提交原料收货、委外采购订单、委外订单、向供应商发料和委外收货，接收 3 + 拒收 1 + 损耗 1 = 5；退回 2 后净回货 3、损耗 1，旧 5 件分类保存被拒，调整后可保存；撤销退货恢复净回货 5；接收 3 和拒收 1 全部退回后只剩损耗 1。另发现原生退货草稿复制原收货损耗，新增关联流程单退货损耗必须为零的保存保护，原生草稿插入被正确拦截。10 项站点检查通过，测试业务行及台账均在保存点回滚，复查委外收货、合成商品/供应商无残留。78 项单元测试、Ruff、JS 语法、差异检查及完整 `bench build` 通过。真实员工角色及现场分批、品检、返工分类仍待验收。

2026-09-30：试算单新增“读取已提交委外回货批次”，只读取有权限的本公司、本流程单及商品明细，填入未保存表格供人工分类。核对 ERPNext 源码后使用接收量 + 拒收量 + 工艺损耗作为回货数量，保留来源单和明细行，工艺损耗直接带入、其余先待检。重复来源不能保存；源数量变更、取消及来源已退货均要求人工核对，试算损耗不能低于凭证已确认的工艺损耗；请求期间切换单据或流程单不会将旧响应填入新表格。隔离站点完成字段迁移；保存点内用直接插入的合成查询夹具（不是经过库存/总账提交的业务凭证）验证接收 2 + 拒收 1 + 损耗 1 = 4，待检 3、损耗 1，来源数量不符、重复明细、取消、退货及 Guest 拦截，夹具全部回滚。浏览器真实 API 返回空列表及无来源提示，表格不变；另用浏览器拦截的合成响应验证新增 1 行、回货 4/待检 3/损耗 1、来源字段正确，重复点击仍 1 行，表单为未保存状态，刷新后测试行消失，页面级 JS 异常为零。新增 9 项来源保护单元测试，合计 72 项通过，Ruff、JS 语法及完整 `bench build` 通过；真实已过账委外回货的现场分类和退货净批次自动处理仍待验收。

2026-09-30：财务旧“费用账”接入 Jifeng Expense Ledger 正式脚本报表，只统计已提交的集沣费用登记单，显示费用单及其已提交总账凭证链接，并可按公司、日期、费用科目、成本中心、店面筛选；它不涵盖其他来源总账费用。隔离站点迁移后确认报表注册，保存点内提交 19 元合成费用单并创建 31 元草稿，报表只返回 19 元且关联已提交 Journal Entry；费用科目和店面筛选正确，Guest 被拒。Accounts User 经 Frappe 报表接口可打开；合成费用单、草稿、总账分录和临时用户均已回滚，站点无残留。浏览器从经典工作台“财务→费用账”可打开报表，公司、日期、费用科目等筛选加载正常。63 项单元测试、Ruff、JS 语法及完整 `bench build` 通过。浏览器控制台仍有开发 Web 单进程环境的 Socket.IO 连接错误及 `/socket.io/`、`/undefined` 404；报表无界面错误，页面级 JS 异常为零。

2026-09-30：财务旧“收款报表”和“付款报表”接入正式 Jifeng Payment Analysis 脚本报表，以已提交 Payment Entry 的公司本币 `base_received_amount` / `base_paid_amount` 为收付金额，提供公司、日期、方向、往来方和收付款账户筛选，并显示可回溯凭证号。隔离站点首次迁移发现专用 Redis 服务未启动，启动 `redis_cache`/`redis_queue` 后重新迁移成功，报表已注册。保存点内提交合成客户收款 23 元与供应商付款 17 元，分别只出现在正确方向，付款账户筛选命中；另以合成收款 29 元验证 Accounts User 经 Frappe 报表接口可读，Guest 被拒。所有本次合成凭证、供应商、临时用户和总账写入均已回滚，站点确认无持久 Payment Entry。浏览器从经典工作台“财务”进入两份报表，路由分别带 `payment_type=Receive` 与 `payment_type=Pay`，公司及日期筛选正确，空结果符合无持久凭证的站点状态；页面级 JS 异常为零。63 项单元测试、Ruff、JS 语法及完整 `bench build` 通过。

2026-09-30：旧“工资分析”入口改为正式 Jifeng Wage Analysis 脚本报表，按公司、工作日期、员工、工序和工资项目筛选，仅汇总已审核的计薪记录，输出审核笔数、计件数量/金额、计时小时/金额、附加费与合计。隔离站点 `bench migrate` 后确认 Report 已安装且绑定计薪单；保存点内创建 1 名合成员工、2 张已提交和 1 张草稿计薪记录，报表只汇总已提交的 2 张：数量 5、计件 50 元、计时 5 元、附加费 1 元、合计 56 元，测试写入随后回滚；Guest 调用被拒。随后保留合成员工 `HR-EMP-00001` 和两张已提交计薪单 `JF-WAGE-2026-00003`、`JF-WAGE-2026-00004` 供浏览器验收，均标记为隔离站点合成凭证，未生成工资附加项或工资条；浏览器显示 2 笔、数量 5、计件 50 元、计时 5 元、附加费 1 元、合计 56 元，筛选和总计行正常。真实 HR User 角色测试暴露其对 Company 仅有 select 权限，改用可选公司校验后，其直接查询及 Frappe 报表接口均可读取 56 元汇总；限于另一公司的 HR User 无法读取本公司报表。临时用户及授权均已回滚。新增报表名中文翻译后，浏览器整页重载可显示标题“工资分析”。63 项单元测试、Ruff 和完整 `bench build` 通过；真实 HR 角色的浏览器界面仍需核验。

2026-09-30：经理看板真实角色边界补验：隔离站点在保存点内创建仅有 Stock User 角色、并仅获“成品 - JUI”仓库 User Permission 的合成用户；看板只返回这一个仓库和 0 元库存价值。用户与授权记录均已回滚，站点确认无残留。

2026-09-30：总经理看板新增当前库存价值，使用当前账号可读的本公司非组仓库及 Bin 结存价值汇总，并将同一仓库范围带入明细列表。隔离站点管理员显示 4 个仓库、当前结存价值 0 元；保存点内将合成 Bin 的价值暂设为 123 元时看板显示 123 元，模拟仅可读另一空仓库时显示 0 元，写入随后回滚。浏览器只读验收确认卡片显示 CNY 0.00，点击后进入正式 Bin 列表，页面公司筛选与 4 个可读仓库筛选正确，测试物料结存 0。63 项单元测试、Ruff、JS 语法检查和完整 `bench build` 通过；真实限制仓库的业务角色仍需核验。

2026-09-30：成本试算新增手动“带入已过账实际费用”，从有权限读取的正式 Material Issue 库存台账取负向库存价值作为材料费，并与委外收货明细的材料、加工、其他费用分列汇总；带入时记录来源凭证和时间，不自动记账。隔离站点先在保存点内提交合成领料 `MAT-STE-2026-00002`，确认台账材料费 50 元、组合费用材料费 50 元且无委外收货，再带入试算，材料费 50 元、加工费 0、来源号正确，所有这次测试写入已回滚。为浏览器验收，隔离站点随后正式提交此前保留的合成领料 `MAT-STE-2026-00002`，并保留合成试算 `JF-COST-2026-00002`；浏览器确认文案分别列出物料出库 1 条/50 元、委外收货 0 条/0 元，点击后试算材料费 50 元、加工费 0、来源号正确，未新增库存或财务凭证。保存点内又撤销唯一来源领料并重新带入，确认材料费从 50 元清零、来源号清空；此前从未带入实际费用的试算无来源时继续保留估算值，两组测试写入均已回滚。混合直接领料与委外收货的同一流程单仍需实单核验。完整 `bench build`、63 项单元测试和 Ruff 通过。验收时发现测试站点系统时区为 `Asia/Shanghai`、管理员个人时区却为 `Asia/Kolkata`；已将该隔离站点管理员时区改为 `Asia/Shanghai`，刷新后浏览器显示 `2026-09-30 06:51:23 Asia/Shanghai`，与先前 `04:21:23 Asia/Kolkata` 为同一时刻。

2026-09-30：修正按单配料的单位换算边界。ERPNext 的通用转换在缺少配置时可退回系数 1；现从商品本身、变体模板或全局换算表明确查找，缺失即拒绝生成。隔离站点保存点内使用商品自定义 `1 Box = 144 Gram`、流程单计划 2 Box，草稿建议 288 Gram；另一件未配置 Box→Gram 的商品被拒绝。63 项单元测试和 Ruff 通过；所有新增合成行已回滚。

2026-09-30：流程单新增“生成按单配料出库草稿”，选择本公司来源仓库后按物料分析建议 Stock Entry Material Issue，库存单位换算后扣除已有已提交出库量；重复点击复用未提交草稿。Stock Entry 明细必须来自同一流程单，累计数量超过分析量会拒绝保存/提交，只有人工提交正式库存凭证才扣库存。隔离站点 `bench migrate` 后在保存点内验证：收货 5 件，先领 2 件，再生成剩余 3 件并提交；尝试改为 4 件被拒，库存台账三条记录数量及价值净为 0。另以每件 0.2 Kg、计划 5 件、库存单位 Gram 的物料生成 1000 Gram 出库并提交，库存净为 0；这些合成行均已回滚。62 项单元测试、Ruff、完整 `bench build` 通过。为浏览器验收在隔离站点保留合成流程单 `JF-FLOW-2026-00001`、原料/成品 `JF-UI-ISSUE-RAW-b4dd1931`/`JF-UI-ISSUE-FG-b4dd1931`、已提交收货 `MAT-STE-2026-00001`（5 件、50 元）和未提交领料草稿 `MAT-STE-2026-00002`；未接生产凭据。浏览器确认按钮和仓库筛选，草稿为 Material Issue，原料 5 件、来源仓库 `仓库 - JUI`、单价 10 元、总发出 50 元；再次生成返回同一草稿，未提交。真实仓库角色、与工单/委外发料同时使用及额外损耗处理仍待验收。

2026-09-30：产品模块旧“报价单”改为新建 ERPNext Quotation（预设报价对象为客户），“报价查询”进入 Quotation 正式列表。源码已确认 `quotation_to` 字段和测试站点 Quotation 单据存在，JS 路由调用通过；隔离站点保存点内提交合成客户 2 件、单价 15 元的 Quotation，总金额 30 元，测试数据回滚。浏览器只读核验新建页显示“报价对象=客户”，报价查询进入正常加载的 Quotation 列表，未保存单据。旧页面的付款、欠款等字段不属于标准报价，报价转销售订单与旧价格规则仍需逐项核对。

2026-09-30：隔离站点内采购收货 5 件、单价 10 元后部分退回 2 件；退回明细自动继承原流程单，库存台账两条记录净结存 3 件、净价值 30 元，测试数据回滚。经典工作台旧“权限管理”接 Frappe Permission Manager，旧“操作日志”接 Activity Log；测试站点已确认目标 Page/DocType 存在，JS 语法和路由调用通过。浏览器只读核验：前者进入 `/desk/permission-manager` 并显示“角色权限管理”，后者进入 `/desk/activity-log` 并显示“用户操作日志”列表；未做权限编辑或单据保存。真实角色权限尚待验收。

2026-09-30：旧“物料→退回”经浏览器核验进入 `docstatus=1&is_return=0` 的采购收货单列表，筛选面板显示“已提交”和“退货=否”；“物料→退回报表”进入 `is_return=1` 列表，显示“退货=是”，均未保存单据。此前只清除站点缓存时，旧版开发服务器又写回了缺少采购钩子的缓存；停止该服务、使用当前代码重启并清缓存后，连续三个独立进程均加载到采购和委外事件钩子。随后在保存点内重跑流程单→采购申请→采购订单→采购收货→采购发票，四级明细保留同一流程单，收货 5 件、发票金额 50 元，业务测试行已回滚。诊断现返回各流程单钩子的生效状态；代码发布后须重启服务并核对此项。

2026-09-30：隔离站点发现直接录入并关联流程单的 Purchase Receipt 生成退货时，`no_copy` 使退货明细丢失流程单号。新增从原收货明细继承的来源校验，并覆盖 Purchase Invoice、Subcontracting Receipt 的退回明细；单元测试 58 项和 Ruff 通过。首次实连未通过，原因是站点缓存的 `doc_events` 缺少较新采购钩子；`bench --site jifeng.localhost clear-cache` 后一度确认四项采购/委外钩子已装载，后续复测发现旧服务又写回了旧缓存。再测直接采购收货 5 件、50 元及全额退货，退货明细保留流程单，库存台账 +5/+50 和 -5/-50 净为零；直接采购发票退货保留流程单且金额 -50 元；委外发料、收货、退货的流程单及成本净额回归通过。测试行均在保存点回滚。物料/材料旧“退回”入口现进入已提交非退回收货单列表，原生单据有 Create→Purchase Return；“退回报表”入口可查询退回收货单。部分退回及真实角色尚待验收。

2026-09-30：新增入口完成浏览器只读复核。“外发→新建委外采购”打开 Purchase Order 新建页且“委外”已勾选；“外发→委外采购单”打开 `is_subcontracted=1` 的列表，“物料→采购订单”打开 `is_subcontracted=0` 的列表，筛选面板分别显示“委外=是/否”。首次复测发现 Frappe 会从用户列表设置恢复上次的委外筛选，即使直接访问无参数 URL 也是如此；两种业务入口改用显式筛选后最终复测通过。JS 语法、路由调用检查和完整 `bench build` 通过。所有浏览器操作未保存业务单据。

2026-09-30：浏览器只读核验经典工作台的“外发加工”“收回”“电镀品检”，分别打开 ERPNext 新建委外订单、委外收货、质检单；质检单预设入站和委外收货来源。新委外订单必须先选已存在的委外采购订单；测试站点没有可选采购单，因而浏览器未展开商品明细，不能据此确认新“集沣流程单”字段的可见性。为补齐前置路径，物料/材料增加正式采购订单列表，发镀/外发增加预设 `is_subcontracted=1` 的新建委外采购和按该字段筛选的委外采购单列表。三个浏览器表单均未保存。

2026-09-30：继续在隔离站点保存点内验证委外发料与退回。ERPNext 根据委外订单生成的“发给委外商”库存凭证，其原料行通过 `sco_rm_detail` 找到委外订单商品行并自动带入流程单号，5 件发料提交成功。已提交的委外收货又生成全额退回单，退回行继承流程单、数量为 -5；流程单实际材料费由 50 元净为 0、加工费由 100 元净为 0。合成单据退出时回滚。单元测试 57 项及 Ruff 通过；部分退回、委外原料退回和多商品混单仍需验收。

2026-09-30：同类合成委外收货提交后，原生 Quality Inspection 以 Incoming 类型关联收货单和成品，提交 Accepted 后，收货明细反向记录品检单号。该验证没有配置检测参数或读数，只证明单据关联；实测品检标准、拒收、返工和角色审批仍待完成。

2026-09-30：隔离站点迁移后，在保存点内完成真实委外链路：原料收货、采购订单、带流程单的委外订单、向供应商发料、委外收货。委外订单明细的流程单号自动传到收货明细；5 件成品的收货实际材料费 50 元、加工费 100 元，流程单汇总与成本试算单显式带入结果均一致。回滚后合成商品、供应商和已提交委外收货单计数均为零。此项尚未验证分批收回、退货、品检、返工、额外费用分摊及真实角色权限。

2026-09-30：检查 ERP 工作目录及其上级“集沣”项目目录的文件清单，未发现旧系统爬取页面的 HTML、HAR、MHTML 或快照包；当前页面证据仍以先前浏览器勘察和经典工作台记录为准。若要逐字段完成所有旧模块等价验收，需要原始抓取包或可访问的旧系统测试环境。

2026-09-30：隔离站点 `bench migrate` 后 `diagnostics.check` 确认 Material Request、Purchase Order、Purchase Receipt、Purchase Invoice 四级明细均有流程单字段。首次站点测试发现 Frappe 的 `no_copy` 规则使映射出的新单草稿字段为空，随后改为保存时从来源明细补齐并拒绝来源冲突。保存点内提交合成流程单采购申请、采购订单、采购收货和采购发票后，四级明细均为同一流程单；收货原料 5 件，Stock Ledger Entry 增加 5 件、库存价值 50 元。测试业务行已回滚，采购订单/收货/发票记录数复查均为零。新映射草稿在首次保存前不会显示自动追溯字段，需在界面提示；采购退货、发票冲红与真实供应商对账仍待验收。

2026-09-30：按 ERPNext/HRMS v16 仓库中的 Report JSON 核对正式名称和来源单据，且隔离站点均存在这些 Report 记录；将旧工作台的生产计划分析/生产汇总、工资分析、销售报表/销售分析/明细成本、财务销售账/总报表接到 Production Plan Summary、Work Order Summary、Salary Register、Sales Register、Sales Analytics、Gross Profit、Accounts Receivable、General Ledger。仅为可确认的报表入口映射；旧系统筛选维度和统计口径尚未逐项对比。

2026-09-30：隔离站点保存点内创建合成原料、成品、已提交 BOM 和流程单，生成并提交关联流程单的 Work Order；原料收货 5 件后，通过 ERPNext `make_stock_entry` 生成并提交 Manufacture 凭证。制造凭证两条明细均自动带流程单号，原料结存 0、成品结存 5，库存台账显示原料 -50 元、成品 +50 元，全部业务测试数据回滚。此项验证的是不转移在制仓的基本制造路径；Job Card、工序加工费、返工与分批完工估价仍待验收。

2026-09-30：经典工作台销售入库、成品出库、销售调拨、销售盘点及库存/进销存入口改接 ERPNext 正式库存凭证或报表。成本试算仍不产生库存凭证；成品入库的估价需在正式 Stock Entry 中核对填写。路由静态检查、JS 语法通过；浏览器点击“销售入库”打开未保存的 Stock Entry 新建表单，移动类型为“其他入库”，关联类型记录为 Material Receipt。未在该入口实际提交库存凭证。

2026-09-30：经营总览已接正式单据的权限过滤汇总。隔离站点完整 `bench build` 通过；管理员可读取公司、销售/采购发票净额、费用和未完工工单。合成财务角色仅获一家公司的 User Permission 时，返回的公司列表只有该公司，查询另一家公司被拒绝，未授予的工单指标不返回；Guest 被拒绝，反向/超长日期区间被拒绝。保存点内提交 12.34 元费用单后看板返回 12.34，回滚后费用单记录数为零。浏览器可见四项指标、公司切换和日期查询，四项均可跳转正式单据列表；当前无已提交业务数据，显示为零。费用指标跳转后的列表 URL 带所选公司、已提交状态及查询日期范围，筛选器数为 3。当前开发站点只启动 Web 服务，浏览器仍记录 Socket.IO 404/XHR 错误；另有一次 Moment 对 undefined 日期的弃用警告，需在完整服务栈复查来源。

2026-09-30：另在保存点内验证销售发票 100 元→客户收款核销、采购发票 80 元→供应商付款核销；两类发票的未结金额均从原金额变为零，测试单据已回滚。此项使用合成公司、客户、供应商和科目，不等于真实客户历史账、汇率或银行对账已验收。

2026-09-30：新增收付款站点冒烟，在保存点内提交一张合成客户收款 Payment Entry（100 元）和一张合成供应商付款 Payment Entry（80 元），两张凭证各产生 2 条借贷平衡的 GL Entry，3 项通过后回滚。此检查只覆盖未核销发票的收付款，实际应收应付核销、银行对账、角色权限和历史报表仍需验收。

2026-09-30：工资站点冒烟增加可选 `salary_structure` 参数。合成员工已有已提交工资结构分配和公司假日表分配时，18 元计薪记录生成并提交 Additional Salary；同月 Salary Slip 把 1000 元基础工资和 18 元附加项分别列示，工资条总额 1018 元，可提交，明细保留 Additional Salary 来源。完整链路 8 项、原有草稿模式 7 项通过，保存点回滚后无工资条/附加项/计薪单残留。未验证 Payroll Entry 发放、工资总账过账和多人报工分摊。

2026-09-30：新增可重复的库存台账冒烟；隔离站点保存点内创建合成商品和流程单，提交关联流程单的 Material Receipt（5 件）与 Material Issue（2 件），核对 2 条 Stock Ledger Entry 和 Bin 结存 3 件，4 项通过后回滚。工单自动生成的发料/完工入库、采购收货、退货和盘点尚未验证。

2026-09-30：浏览器完成隔离站点首次向导，语言中文、国家中国、币种 CNY、时区 Asia/Shanghai，使用合成公司与管理员资料、未启用演示数据。经典工作台、费用登记列表及 HRMS 费用、Payroll、HR Setup、员工列表均可打开；发现原集沣工作区英文记录名与中文路由不一致，改为中文记录名后迁移并从桌面菜单再次点击，可进入经典工作台且侧栏费用入口可用。直接打开中文工作区可见经典工作台、计薪和费用快捷卡片；补齐翻译并重新构建后，费用登记列表标题显示中文。完整 `bench build` 通过；单独 `bench build --app jifeng_erp` 因该应用没有独立前端打包入口而失败，安装文档已更正。浏览器仍见 Socket.IO 与部分资源 404，当前只运行了开发 Web 服务，未启动实时服务；需在完整服务栈复查。未创建业务凭证。

2026-09-30：在保存点内创建合成原料、成品、已提交 BOM 和流程单；生成 ERPNext Work Order 草稿，核对流程单/BOM 关联及重复操作复用，均通过，所有测试数据已回滚。工单提交、报工与生产库存过账仍未验证。

2026-09-30：新建隔离的 WSL Ubuntu-24.04 测试站点 `jifeng.localhost`，使用 Python 3.14、Node 24、MariaDB 11.8、Redis 和 Frappe/ERPNext v16、HRMS v16.20.0。三应用同站点安装、`bench migrate`、`bench build` 通过；`diagnostics.check` 确认订单、工单、委外收货、员工补充档案及计薪扩展字段已同步。此站点只含合成基础资料，聚水潭凭据未配置。为了测试创建了公司、科目/仓库/成本中心、商品组、单位、会计年度、客户、销售价格表、员工、工资结构及分配；真实业务测试行均在保存点回滚。

2026-09-30：同站点重跑流程单冒烟 13 项、销售订单→流程单冒烟 10 项、员工补充档案 3 项和计薪→补充薪资草稿 6 项，均通过。销售订单冒烟补齐了原生单据必需的仓库和同币种销售价格表，否则无法在全新站点保存；计薪冒烟要求测试员工已有已提交的工资结构分配。

2026-09-30：新增可重复的费用登记站点冒烟，在保存点内提交现金费用单，核对 Journal Entry 的两条平衡 GL Entry、直接取消凭证被拦截、从费用单取消凭证，4 项通过且业务数据回滚。仍未验证真实财务角色、银行付款、收付款凭证、会计期间限制及历史对账。

2026-09-30：人事模块增加 HRMS 晋升、调岗、离职正式入口，工资模块增加工资结构分配的调薪录入与查询；对照本地 HRMS v16.20.0 源码确认 DocType 名称和晋升/调岗提交行为。仅完成静态路由与 JS 语法检查，尚未在同站点安装 HRMS，也未验证真实角色、员工历史或工资基数生效。

2026-09-30：新增正式费用登记单，保留旧页面可见的店面、编号、名称、方式、金额、欠款参考和摘要；审核时校验公司、本币、明细费用科目、现金/银行付款科目及成本中心，并在同一请求内生成提交一借一贷的 ERPNext Journal Entry。取消费用单会取消关联总账凭证；直接修改或取消该总账凭证被钩子拒绝。旧费用登记和查询入口已改为服务器单据。53 项纯 Python 测试、Ruff、相关 JS 语法、DocType JSON 字段顺序及 wheel 内新增文件检查通过，ERPNext v16 源码已核对字段及 Accounts Manager 权限。未在真实站点验证迁移、总账过账、会计期间、取消/修订和角色权限；当前只覆盖现金/银行本币直接费用，赊账及其他付款方式的科目映射仍需业务确认。

2026-09-30：旧外发加工、收回、电镀单、电镀品检入口现分别打开正式委外订单、委外收货和品检单；电镀品检预设 Incoming 与 Subcontracting Receipt 来源。旧外发/收回查询及电镀单查询打开对应服务器单据列表。ERPNext v16 源码确认 Quality Inspection 允许此来源及检查类型；JS 语法检查通过，尚未在站点验证供应商、品检、流程单、费用和返工闭环，电镀专属字段仍需建模。

2026-09-30：本机 Docker Desktop 引擎启动时因 `dockerInference` / `engine.sock` 本地套接字路径错误反复崩溃。两处旧运行目录已保留在用户目录的带时间戳备份中；尝试关闭 Docker AI 后仍无法启动，设置已恢复原值，错误窗口和相关进程已关闭。未删除 VHDX、镜像、容器或卷；因引擎不可用，尚不能确认是否存在可复用测试站点，以下新增功能均缺真实站点验收。

2026-09-30：流程单生成采购申请时，已有有效申请仍复用；原申请取消后可重新生成，并更新流程单关联。复用前核对公司、申请类型及每条明细的流程单来源，避免返回被改作其他用途的单据。新增 2 项纯 Python 用例，合计 48 项通过；Ruff 与 `git diff --check` 通过，真实站点取消/重建流程待验收。

2026-09-30：生产工单关联流程单后，其生成的 Stock Entry 会在校验时把流程单写入已列入本单物料分析的物料行与成品行；不同流程单混挂或公司/商品不符会拒绝保存。未列入分析的副产品/废料保持空关联，需通过工单追溯。新增 4 项纯 Python 校验用例，合计 46 项通过；Ruff 和 `git diff --check` 通过。尚未在真实站点验证工单发料、生产入库及原生 Stock Entry 自动生成路径。

2026-09-30：物料/材料旧菜单中申购、入库、出库、调拨、盘点现分别进入 Material Request、Purchase Receipt、Stock Entry（预设 Material Issue/Material Transfer）、Stock Reconciliation；库存及库存明细进入 ERPNext Stock Balance/Stock Ledger 报表。源码已核对单据选项和原生报表路由，Node VM 路由调用与 JS 语法通过。尚未在测试站点核对单据提交、库存台账、退回和流程单发料。

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
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_stock_flow --kwargs '{"company":"测试公司","item_group":"Products","stock_uom":"Nos"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_payments --kwargs '{"company":"测试公司","customer":"测试客户"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_expense_voucher --kwargs '{"company":"测试公司"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_work_order --kwargs '{"flow_order":"尚未生成工单且已关联有效BOM的测试流程单"}'
bench --site YOUR_SITE execute jifeng_erp.instruction_validation.smoke --kwargs '{"company":"测试公司","item_group":"Products","stock_uom":"Nos"}'
bench --site YOUR_SITE execute jifeng_erp.production_validation.smoke --kwargs '{"company":"测试公司","item_group":"Products","stock_uom":"Nos"}'
bench --site YOUR_SITE execute jifeng_erp.department_validation.smoke --kwargs '{"company":"测试公司","item_group":"Products","stock_uom":"Nos"}'
bench --site YOUR_SITE execute jifeng_erp.production_picture_validation.smoke --kwargs '{"company":"测试公司","item_group":"Products","stock_uom":"Nos"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_employee_supplement --kwargs '{"employee":"尚未有补充档案的测试员工编号"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_wage_entry --kwargs '{"employee":"已分配工资结构的在职测试员工编号","salary_component":"已启用的收入类工资项目"}'
bench --site YOUR_SITE execute jifeng_erp.diagnostics.smoke_wage_entry --kwargs '{"employee":"已分配工资结构的在职测试员工编号","salary_component":"已启用的收入类工资项目","salary_structure":"员工当前已提交的工资结构"}'
```

冒烟测试实际创建测试商品、档案、来源订单、流程单、采购草稿和分批试算，检查重复操作复用原单、同步变化及台账数量不变；退出时回滚这些测试数据。费用冒烟会临时提交并取消财务凭证，再回滚。测试公司须有启用的明细仓库、当前会计年度、同币种销售价格表、本币费用与现金明细科目以及明细成本中心；收付款冒烟还需客户、应收/应付明细科目和明细供应商分组。计薪测试员工须有已提交的工资结构分配；工资条模式还需当前月份适用的公司或员工假日表分配。请只在测试站点运行，不能把冒烟测试通过等同于生产验收。

## 实连验收

在服务器端载入现有配置，用只读查询验证授权/白名单/商家及平台覆盖；选取少量已知订单核对商品、行号、数量和图片。验证重复同步、源单改量、取消、分页故障恢复。之后由生产人员用实际单据核对单位、库存、流程单费用与试算结果。


2026-09-30：五个新采购/库存台账完成实现与测试站点迁移：采购申请、采购收货退回、库存出入调拨、历史库存余额、库存盘点差异。182项Python测试通过，新增采购32项、库存交易23项、盘点与完整SLE权限13项；相关Ruff及工作台JS语法通过。隔离站点 `inventory_validation.smoke` 原生33项通过且 report_api_verified=true，全部夹具回滚。采购申请库存已订10；分批收货净接受6/拒收1/实物7；15个交易单位转30库存单位；普通调拨3、在途4+4每条业务明细一次且公司数量/价值净0。原生盘点真实27→30→25→仅估价10改12→0→8，最终数量8、价值96；普通盘点SLE.actual_qty为0仍按原生余额状态机得到正确变动。盘前/盘后、草稿和取消排除、历史日期、零余额、停用主档、Stock User完整SLE可读余额、Stock Manager盘点API、隐藏部分SLE拒绝、公司/物料/仓库用户权限和Guest拒绝全部通过；每次报表读前后比较SLE/GL全部内容hash，不新增、编辑或删行。原生3个本次fixture重算同步Completed后再查询。

补充清理核验：前批委外浏览器夹具取消后留下13个原生Queued库存估价重算。`finish_retained_reposts` 仅处理私有journal拥有的精确来源和独立物料/仓库，通过原生repost完成13项，并再次确认各仓库存、预留/订购及GL净额恢复零。原journal记录前后任务状态；保留取消凭证及不可变账簿历史，没有直接改台账或删除重算记录。这是新增核验，修正之前仅确认凭证取消/净余额而未完成后台重算的清理边界。

新增入口按模块+菜单去重：357总入口，190直接正式路由，167其他（13总经理模块导航、154预览或说明）；不是完成百分比。五个报表的浏览器实际行、采购额外边界以及同源两池退货已在下面的追加验收完成。基本库存33项夹具的接受与拒收退货来自不同源单，同源两池依据独立18项正式扩展检查。


2026-09-30：采购申请改为按精确申请明细核对已提交来源，直接净接受收货与有效订单分别覆盖，不把订单收货再加一次；关闭订单仅保留已收到的净接受量，停止申请待采购为零，拒收品退回不减少接受量。正式 WSL 源码 `procurement_validation.smoke` 默认调用26项通过：直接收5+订购5申请10待采购0、订单分批收退不重复、关闭/重开、原生缓存被拒收退回减少但本台账净接受仍正确、同商品不同来源、来源跨日期、仓库数量池与整行凭证货值、公司本币/交易币、隐藏原收货但可读退单拒绝、业务角色原生QueryReport接口及Guest拒绝。全部业务保存点回滚，查询前后全部GL/SLE内容一致。46项采购纯模型测试通过。

采购收货正式类扩展修复原生累计退货接受/拒收混池。按持久化子明细标志、精确原收货行及仓库分别限额，累计同单其他行；源单事务锁和已提交退货 current-read 防止读取旧额度。永久dispatcher仅匹配本扩展Purchase Receipt退货类，使用ContextVar隔离请求，复制并规范数量输入后仍调用原生validator及完整super.validate链，其他单据保持原生路径；没有修改ERPNext文件或跳过原生校验。正式安装hook入口 `purchase_return_validation.smoke` 18项通过：两种退货顺序、混合/剩余、真实超量、同单多行绕限额拒绝、换算2、同商品精确源行、取消释放、映射旧qty、某池全退后仅退另一池、非PR原生路径。业务全回滚，2项本夹具重算Completed。20项相关纯模型测试通过。尚未做双事务同时提交，不宣称并发验收通过。更新后默认库存33项再次通过（report_api_verified=true，本夹具3项重算Completed），全套216项Python测试通过。

管理员浏览器五个报表和经典菜单实际只读验收通过：采购收退4行净接受6/拒收1/实物7，普通调拨3及在途两段4+4各业务行净0，接收精确指向发出单及明细；历史A仓期初27/270、增11/176、减30/350、期末8/96、单价12；盘点5行包含仅价值增加50。经典退回筛选仅退=1，再入库清零；出库issue、调拨transfer、销售收货receipt并清空旧商品/仓库/流程条件；历史/盘点进入对应新台账，差异处理打开未保存原生Stock Reconciliation。服务重启后独立采购申请复验：MAT-MR-2026-00001申请10/已订10/已收6/尚未订0/待采购0。原服务缓存已收5证据不计为新口径通过；另有误把进程session编号作为端口造成的连接失败，独立保留后使用8000成功复验。页面JS异常0；Socket.IO轮询404为单进程开发环境记录，另有Desk parser发起/desk/undefined再转/undefined的404，未阻断页面，尚未定位具体节点，未宣称无HTTP异常。证据在output/playwright/inventory-*.json/png；管理员浏览器不能代替真实角色报表权限验收，后者由上述原生检查覆盖。

持久库存浏览器夹具JF-INV-BROWSER-7f5aefdbf0aa已精确逆序原生取消、完成23项相关估价重算并停用8个独立主档（2商品、1供应商、4仓库、1专用单位）。最初取消事务因错误地要求取消历史SLE估值差额合计0而失败，状态核对确认整体回滚；只读源码及事务内调试证明ERPNext16.36.0普通取消流水估值差额可以为0、原流水已标记取消且保留价值字段，该历史合计不是当前余额。修正清理判据后成功：无有效SLE，普通数量净0，所有Bin数量/价值/订购/需求/预留/预计余额0，GL按账户及每来源凭证+账户净0，所有正式QA凭证不再提交，5个报表都0行，停用与报表读取未改变账簿内容。历史诊断合计仍写私有journal；未手改账簿或强删历史。最终phase=cancelled_history_retained、cleanup_complete=true、cleaned=false，设置恢复/库存恢复/总账恢复/主档停用=true。源文件、索引与HEAD核验确认上述取消逻辑属于安装版本基线，ERPNext仅已有banking/yarn.lock差异，与本次业务代码无关。

2026-09-30追加销售与多人计件批次：销售默认原生 `sales_validation.smoke` 在正式提交72a4952通过30项检查，业务夹具全部回滚。普通SI、原生SI收银、POS开/结班及精确重复商品行部分退货均通过；结班合并前后营业交易行数/金额保持一次，合并SI仅在已提交后计入过账台账，结班取消反转SI并解除原POS回指，客户组合并保留原交易客户。外币支付明细重载核对原币/本币并只扣找零一次；隐藏合并/退货来源拒绝，可选无权SI链接不泄露。Accounts User/Manager真实Report API成功，纯Sales User/Manager无原生发票读取/报表权限故明确拒绝；SalesManager共享精确QA票后仅用POS Profile.select可以筛选，不冒充完整Profile.read或报表授权。看板已过账销售净额与同日期可读SI本币有符号净额相等，报告读前后全GL/SLE内容hash不变。浏览器及至少10单真实后台结班队列仍未验收。

同批本地全套353项测试通过；多人分配30项、原工资8项、三档案31项、档案报表7项及默认权限识别8项已包含其中。工资与档案新增代码、表结构、报表已迁移，安装诊断各字段/Hook/报表均true。原生工资首轮在AS主管反审权限断言停下且完全回滚：HRMS默认Employee Self Service用户类型从SalaryDetail的AS链接自动生成CustomDocPerm克隆，不能把所有克隆都视为手工定制。本扩展改为逐权限位核对原生克隆和已验证的ESS选择规则，只给默认HRManager补cancel/delete；存在其他角色/权限差异的站点保持原样，且记录DocType评论。安装后真实HRManager AS取消/删除均1。验收脚本将续卡来源数量的六位字符串转为Decimal比较后，正式8c160c1默认工资检查32项通过（payroll_chain=true、business_data=rolled_back）。HRManager对正常完成5的两人报工按份额2/3、各人明确计时选择生成工资16/12，提交两笔AS；原生PayrollEntry生成两张保留AS来源的工资条，测试基础工资2000加本批28，仅生成一张有效计提JE。AccountsUser实际提交现金/银行付款JE2028；主管原生反审PayrollEntry联动撤销工资条/计提/付款，再取消AS→Wage→Allocation，源JobCard恢复可取消。分配/工资本身不写GL/SLE、HRManager无生产write、公司隔离/隐藏来源/Guest拒绝、草稿删除与重复/超量均通过。后置工资验收员工/公司/商品/用户/薪资结构零残留，整个SLE/GL内容hash与运行前一致。双事务竞争、实际工资浏览器尚未验收；档案原生默认验收见下面的独立记录。

同批前端完整 `bench build` 在8c160c1退出0；33份JS语法、43份JSON解析通过。测试站点验证不等于生产部署。

2026-09-30：正式7be0b706默认 `catalog_validation.smoke` 原生57项通过（29种检查标签，含循环覆盖），report_api_verified=true。Stock Manager、Manufacturing Manager+Manufacturing User实际保存和修改三档案，核对原生Version、复制来源/BOM有效性、停用与报废、Company/Item/Employee User Permission、员工仅select无HR.read及四报表API。重复材料保留各行Nos/Kg单位且不混合汇总；隐藏材料在当前原生User Permission语义下使整个父档案不可读，材料报表严格零行，不泄露配方或成本。图片验收跟踪10条原生File元数据别名并调用原生下载权限；未写入、请求或下载图片字节，不能代表实际图片浏览器/上传已验收。全部来源、Version和File元数据在保存点回滚；独立只读审计十类夹具全零，完整GL/SLE hash与运行前一致。三档案及图片实际浏览器仍待完成。

2026-09-30追加零售浏览器准备：`sales_browser_validation.prepare` 在正式5fc7df92成功提交隔离夹具JF-SALES-BROWSER-43cb85c867c1，后置独立verify通过：6条原始业务交易本币净80.96、4张已过账SI本币净60.72、14条GL、0条SLE，原生自动ItemPrice1条；差额20.24为未合并POS净交易。POS/Selling4项设置恢复准备前精确值，verify前后整表GL/SLE hash相同。首轮时间早于销售、动态非schema属性误入快照、原生User权限审计及自动ItemPrice支持记录在准备过程中逐次修复；每轮失败及诊断均独立确认完整回滚，不盲目重复未知提交。持久操作使用bench env独立Python进程单次调用，避免bench execute异常后eval再次调用同一目标。价格历史和权限日志按精确原生来源保留，不关闭全局自动价格，也不手改账簿。当前夹具仍为已提交QA业务，须浏览器结束后精确撤销。

同批工作台首次管理员浏览器返回500，安全trace定位为配置Redis cache13000未运行；按现有配置恢复cache13000/queue11000并独立PING后页面恢复。复验经典零售开单/前台开单均进入原生point-of-sale，POS设置发票模式为Sales Invoice，未更改设置或创建班次；开/结班、零售单和合并日志及两报表路线已只读打开。Admin路线不是业务角色UI验收；首次渲染/undefined的404仍保留，后续路线没有HTTP错误、pageerror=0。实际交易行和工资UI尚待完成。

追加实际销售浏览器结果：管理员只读核对营业台账全部6行及本币净80.96、已过账台账全部4行及净60.72。两份报表逐行来源、日期、方向、中文状态、退款来源、合并/过账标记、支付/找零与原币/本币字段匹配；原始POS和合并SI各在自己的台账只出现一次，未合并POS净20.24是两报表差额。贷项SI的持久时间与query_report原始数据均为17:47:49.527159，原生Time网格显示17:47:49，未改业务时间。经典日报实际默认当天5行，销售单查询默认当月6行，已过账台账默认当月4行，均清空旧客户/收银配置/人员/来源类型/仅退货筛选。最终成功核验pageerror=0、HTTP错误0；先前一次带筛选URL直达500未取得安全异常栈，再用同一URL与UI筛选都成功，保留该历史故障而不宣称已定位。证据为output/playwright/retail-sales-ui-summary-r2.json、retail-ledger-ui-verification-r2.json与retail-direct-query-diagnostic-r3-summary.json；不代替收银角色操作验收。

销售夹具JF-SALES-BROWSER-43cb85c867c1已按原生依赖精确取消、停用6个独立主档。独立新连接verify通过：phase/durable_state均cancelled_history_retained，两报表0行，总账原14条及反向14条按每凭证/科目净额均0，无有效GL与SLE，0条库存记录，开班不再Open，原POS/Selling设置一致；原生ItemPrice、Contact、权限审计及取消历史保留。未手改账簿或强删历史。随后持久工资浏览器来源夹具JF-WAGE-BROWSER-c2570f10e0db创建成功，独立inspect来源错误/外部引用/未登记UI均为空、whole GL/SLE hash不变；当前仅准备正常完成5件的两人来源卡，实际UI分阶段验收与原生撤销尚待完成。

新增正式发货/发票来源字段与before_validate钩子，按精确SO/DN/SI子行继承流程单，退货按原始明细，不按商品猜测。1370490迁移后诊断字段/Hook均true。原生首轮发现Frappe新建单据会把单一User Permission作为只读Link默认值，no_copy不阻止该默认值；首轮保存点和全部辅助记录/GL/SLE均回滚。f48f732保留真实默认值输入并优先核对不可读来源流程权限，增加回归测试；原生完整链路复验尚待完成。

新增产品八维度分析和人事花名册，经典九个入口及六份档案报表文本转义通过隔离Node检查；当前363入口中214有直接正式路由，149其他（13总经理导航/136预览说明），这不是模块验收比例。产品类别保留两键、空值不与真实“未填写”混合，展开只读来源不重复计数；花名册只展示当前可读Employee，补充字段依权限显示，长期/临时称旧人员类型，未冒称合同分类。b30c29e全套435单测通过，原生角色/API及实际浏览器尚待验收。

正式bbca324默认 `fulfillment_validation.smoke` 原生57项（43种标签，取消循环重复标签）通过：2张SO、7张DN、8张SI、3张Flow、1张StockEntry全部保存点回滚。重复商品两订单行各自Flow、分批2+2及3、DN→SI不重复库存、SI精确原行部分退款、DN实物退回及其生成无SI.return_against的贷项发票、晚建Flow不追改历史空Flow、错公司/商品/子行/冲突/歧义和隐藏来源拒绝、原生上游依赖及逆序取消均通过；每单原生价格和数量保持，取消后源订单已发/已开金额归零、来源变化提示仍保留。原生新建Link默认值真实保留并测试隐藏源，未改全局默认或放宽权限。Mapper得到的空控制器随后改变is_return时不重建DN原生status_updater，验收现与Desk savedocs一致，从最终字典重新构造控制器后保存，SO退回数量1及开票金额34都正确；此前断言失败也全部回滚，未误报原生业务故障。

同批实际角色：追溯链Sales User+Stock Manager+Accounts Manager；纯Sales User可提交无Flow SO/DN，纯Accounts User可开无Flow SI；Accounts Manager+Purchase User+Projects User+Delivery User有SO.select而无SO.read，仍能从历史无Flow DN开SI。Manager不自动含User角色，AccountsUser本身拥有SO.read，故不冒充选择-only来源验收；最后组合依本地原生权限确认，未修改DocPerm。核验8项本夹具原生库存重算、所有主档/子行/辅助表计数及whole GL/SLE hash完全恢复。此项尚未实际发货浏览器，不等同旧成品出库MaterialIssue语义已确认。

2a966e2花名册修复字段级权限边界：Frappe get_list会删除不可读字段，full get_doc仅用于来源与完整read核对，输出保留原生许可行字段，不从全文档补回隐藏值；21项专项模型测试通过。两新报表与字段完成隔离站点迁移，完整 `bench build` 退出0，35份JS语法和45份JSON解析通过。工资浏览器首阶段Admin实际选择来源卡后自动带入公司、工单、Flow、工序、商品、完成量5和两人工时各1；个人份额1/3工价3/4保存草稿JF-WALLOC-2026-00010合计4/总15，审核原生拒绝“各人计件份额合计必须等于来源报工单的已完成数量”，无本次Wage。私有journal已登记under_total_rejected，独立inspect全部来源/账簿/无外部引用通过。提交拒绝HTTP417符合预期；首次保存空白第三员工行被必填校验拦截后清掉空行保存成功，另/undefined404留存，pageerror=0。纠正/审核及工资提交仍待下一阶段。

工资实际浏览器后续完成：主管表单把首人份额改为2，合计5/金额18，审核生成两笔草稿 JF-WAGE-2026-00011（2×3=6）与00012（3×4=12），精确员工/分配子行/源卡匹配；再次点击生成/查看仍为同两笔。两笔在 UI 审核后工资分析显示2行、审核笔数2、数量5、计件金额/总额18，计时及附加费0。报表首次暴露 Page 没有 set_intro 的实际错误；bed2bdf 修复工资与产品分析提示 DOM，重新通过经典入口加载和精确筛选，提示可见、pageerror=0、非 Socket.IO HTTP 错误0。未生成 Additional Salary 或 Payroll；原生全薪资链已有独立32项回滚验收，此处是 Admin UI 操作，不替代实际主管岗位 UI。

工资夹具已精确逆序原生取消工资2、分配1、报工1、工单1、BOM1，员工2停用/商品2与工资项目1禁用；唯一工序、流程来源和无支持停用字段的原生主档保留可追溯历史。私有journal phase=cancelled_history_retained、cleanup_complete=true。独立新连接只读审计6凭证docstatus=2、5主档停用、本项目 Additional Salary/Salary Detail=0、工资分析0行、Bin数量/价值0；整个GL/SLE hash与准备前一致。保存证据 wage-analysis-r2-review.json/png、wage-cleanup-independent-verify.json；不手改账簿或强删历史。

新增销售商品排行及经典入口，20项模型覆盖持久净额/库存单位数量、退货负数、同商品精确子行、汇总与来源值分列、权限和稳定排序；隔离Node检查10个经典入口重置、7份报表Data转义、3份报告onload原生Page接口通过。当前363入口中215正式路由、148其他（13导航/135预览说明），不是业务验收比例。产品/人员原生验收helper已准备，销售排行原生helper开发中；新排行迁移、实际角色数据与浏览器仍待验收。

发货/发票来源默认修复补9项回归：只归一化新父单新子行恰等于原生new_doc实际Flow默认的值，再按精确来源继承；保存历史和同一服务器对象已继承来源不清除，隐藏来源仍拒。33项专项及全套466模型通过；多Flow实际默认值的原生补充门尚待完成，此前bbca324的57门不能替代本次差异验收。

正式dff0f8d产品/人员默认原生验收15门通过：Stock Manager、Manufacturing Manager+User实际QueryReport八维度树及唯一商品档案、状态/空值/真实“未填写”/类别两键、公司与Item范围；HR User/Manager实际花名册四状态、空编号/部门/职位、原生可读字段、补充档案独立权限与隐藏/缺失一致、Employee/Company范围、选择权限和Guest拒绝。原生报告列所需字段当前均permlevel0，未改元数据或故意抬高字段等级；不冒充已验证现场自定义字段等级。所有主档/子行/Version/File元数据及事务表计数恢复，whole GL/SLE hash相同；独立新连接六类前缀对象均零。图片只为元数据，未验实际字节/上传。

最初两轮的末尾计数断言仅 Error Log +8，业务门全部走完；原生 query_report.run 对八次预期权限拒绝记录日志，已核验实际Error Log表engine=MyISAM，保存点不能回滚。修正验收仅允许逐拒绝登记且验证标题/异常类型/精确ID的原生日志，保留先前及本轮记录，不删日志、不屏蔽logger、不改权限；其他全表差异继续拒绝。最终仍明确 Error Log 54→62，业务数据回滚，不宣称全站无任何新日志。新销售排行报告在268b6fb迁移与完整build退出0，36份JS/46份JSON解析通过。

正式b3ef2f7 `fulfillment_validation.delta_smoke` 原生17门通过：两精确同商品SO行Flow A/B与显式UserPermission默认B，新DN/SI空来源默认清除，SO A映射DN/直SI、DN→SI、SI部分贷项、DN实物退回及其贷项全部从实际默认B继承精确A；Desk最终字典重建后保持原生库存/财务数量和价格。保存历史B/blank不清除、晚建隐藏SO Flow不回填历史空DN、非默认伪造/保存后改来源/隐藏Flow及DN祖先均拒。所有跟踪对象和原生tab*计数恢复，whole GL/SLE hash不变，未新增错误日志；原57门源码hash保持，未用重复跑旧门替代默认值差异检查。

正式e7cc771商品排行默认原生12门通过：两真实不同库存单位Item、重复商品精确行、多张已提交SI、行/整单折扣后存储净额、精确原行负数贷项、草稿/取消/跨记账日排除；未合并POS不计、明确小批原生结班的合并SI只计一次。Accounts User/Manager+Purchase User实际QueryReport各4来源发票/6子行，完整Item读取与公司隔离；共享可读发票但隐藏Item拒绝部分排行，Manager无Item.read及Guest拒绝。汇总与子行数量/金额分列、来源行号/单位/方向/是否更新库存匹配。初轮夹具显式rate与行折扣冲突被原生按rate清除折扣，未改业务report；让原生计算折后rate后通过。两次失败及最终业务均完全回滚，Settings与whole GL/SLE相同；只保留7条精确预期PermissionError MyISAM日志（62→69），旧日志hash不变。全部其他tab*计数恢复，独立新连接两批13个来源ID均不存在、ledger一致。未构造同Item冲突库存单位，未覆盖至少10单后台结班队列；实际排行有数据的浏览器仍待完成。

实际Admin只读UI追加：产品品牌/类别分析提示可见、维度切换清空商品/状态，当前无档案数据，只验页面与空态；人事报表本公司3名合成员工，00001激活、00002/00003失效且原生Employee链接正确，没有身份证/银行/手机列；销售排行默认本月/商品空、提示可见，清理后0行。三类页面pageerror/非Socket.IO HTTP错误0；空排行不替代真实数值UI验收。证据 product-analysis-r1.json、hr-report-r2.json、sales-ranking-r1.json及截图。

新增工资当月分析、计薪历史/历史分析和人员查询4个正式路由，原工资分析也显式清空旧条件/重置本月；15个经典入口、8份报表Data转义、3份onload接口隔离Node检查通过。发现站点实际Report.add_total_row=1，尽管版本JSON=0，旧工资UI会显示跨未知单位的数量总计；修复为原生ScriptReport第六返回值skip_total_row=True，同时转义工资姓名/工序等Data。9项工资报表模型通过，全部调用方仅读取execute[1]或使用原生ReportAPI，保留兼容。当前363/219正式路由，其余144（13导航/131预览说明）。该自动总计修复的原生API及浏览器复核待执行。

后续人事闭环审计已核验上游实际源码：晋升/调岗反审历史删除缺employee parent，晋升未传日期、跨公司调岗取消未恢复入职日期；反审直接采用来源单current而未核对后续员工变更。现有正式菜单不代表这些闭环已验收。正在开发只按本员工本来源精确快照恢复的扩展；create_new_employee_id及旧无来源快照单据的安全迁移仍需明确处理。原生Employee Separation只是交接任务项目，完成不自动变Left，离职办理尚未收口。

34107d2工资总计修复追加原生/实际UI：同一个旧Report保存add_total_row=1保持不变，实际QueryReport响应add_total_row=false，仅两条基线来源且无第三总计数组。浏览器当月/历史/历史分析均显示各自2/3数量、21/35金额、空单位与单笔历史说明，来源00003/00004链接正确，无总计行及跨未知单位数量5footer；UI改起始日期08-31后切入口恢复09-01至09-30且员工/工序/项目空，人员查询3条员工正确。最终成功导航pageerror/非Socket.IO HTTP0；另首次经典入口HTTP500仍未取得异常栈，同路由重试和硬刷新成功，保留该历史故障。未修改或清理前批已有基线工资，仅只读查询；证据wage-history-menu-review-r2.json及截图。

人事晋升/调岗安全扩展已集成：服务器锁定并读取实际员工，纠正客户端 current/current_ctc，保留原生提交、未来日期检查和 Employee.save；取消只删除本员工本次新增历史 ID，恢复本次字段和旧历史结束日期，有后续变更则拒绝。两个服务器快照字段限制注入及修改，源单读取/打印/导出和列表同时核验当前 Employee 完整读取、主/子表字段权限。六岗位属性、晋升 CTC 和同编号跨公司调动在当前安全链内；创建新员工编号、其他属性及旧无可靠快照单据的迁移尚未验收。原生属性 RPC 增加字段读取权限，表单仅列出可读可写的支持岗位属性。32 人事专项、6 属性 API 专项、全套 505 模型检查、Ruff、37 JS 语法/46 JSON 解析通过；隔离站点迁移、真实原生动作及实际新对话框 UI 尚待验收，不计为完整人事模块交付。

706efe4 已完成隔离站点迁移，完整 build 退出0；独立元数据核验两个只读快照全部属性、MRO原生控制器继承、读取/列表守门及属性RPC override生效。22f3e96 原生人事首轮在首门失败，尚未执行晋升/调岗动作；全tab*计数、whole Employee及全部child hash、whole GL/SLE完全恢复，fresh connection四类QA前缀均0。实际字段权限均permlevel0且HR Manager read/write1，但get_permitted_fields及get_permitted_fieldnames都排除Table；模型此前错误地把Table列入结果。修正为物理列仍走原生列接口，父Table实际permlevel对照原生get_permlevel_access及原生read分享规则，子表仍带parenttype核验；没有添加角色授权。38人事专项+7属性API专项及全套512模型通过。属性对话框还提前核对整条办理需要的追溯字段权限。修正后原生重跑仍待完成，首轮失败证据保留。

d53281d 默认原生人事验收20门通过：实际 HR Manager、Company/Employee User Permission，晋升纠正伪造 current 和零 current_ctc；调岗后拒绝先取消晋升，逆序取消精确恢复原字段/历史结束日期；同编号跨公司转移及原入职日期恢复；后续岗位/历史更改、未来日期、新编号分支、快照/来源篡改拒绝。含另一个员工的相同岗位/日期碰撞历史，及无历史员工首办生成两个ID并精确移除；真实提交/取消均有来源与Employee Version，源单read/client.get/get_list(name,snapshot)和覆盖属性RPC遵守实际员工/公司范围。所有8源单（含拒绝后保留草稿）、3员工、主档/权限/历史/20 Versions及所有tab*计数回滚，整个Employee+全部child/GL/SLE hash恢复；Error Log保持69，Email Queue/Notification Log为0。fresh新连接四类QA前缀0、5精确提交来源ID均不存在，独立全表和whole hashes复核通过。未更改元数据制造字段撤权，实际表/列正向权限已验、动态撤权由模型覆盖；Employee无user_id，不宣称已验证关联账号副作用。旧无快照仅未持久controller拒绝探针，未验真实旧单反审；属性对话框UI仍待实际核验。

后续离职源码审计确认：原生Employee Separation提交只建Project/Task，交接百分比不代表Employee已Left，也不代表工资/财务结清；开始交接日不是最后工作日。原生cancel会force删除项目下全部Task，不能直接用作已办理离职的恢复；默认HR角色没有该单submit/cancel权限。Employee.save关联User会同步账号及资料，需要独立验证并避免在离职动作中误停用/启用全局账号；工资仍可按原生规则办理与离职日期重叠的期间，集沣未审核计薪则应先处理。当前只完成源码事实核验，尚未新增办理或撤销动作，不计为离职闭环。

ca6f8fd 人事属性对话框实际UI复验完成：r2在晋升首次输入时捕获同一员工/属性两次POST，第二响应替换Link输入节点并丢掉已输入前缀；保留失败及重输成功证据。修复同一员工/属性的重复onchange请求，失败或空响应可重试，切换属性/员工的旧响应不覆盖新控件。Node回归覆盖pending/已响应重复、前缀与节点保持、旧响应和重试。r4晋升/调岗在首次Link出现后实际键盘输入JF-HRUI，原节点保持connected/前缀不丢、各1次getter200，原生候选选中后添加1条未保存明细，current空/new为精确测试职位，离开丢弃；不是保存/提交业务验收。原生cleanup仅删除本次owned Designation，fresh连接确认不存在、Employee及所有child内容和whole GL/SLE保持；保留精确Deleted Document及原生Deleted Comment各1，UI登录Activity Log/Sessions各+20，其他全表计数不变，Error Log保持69。证据employee-change-ui-r2-summary.json、employee-promotion-r4-precise.json、employee-transfer-r4.json、personnel-ui-cleanup-independent-verify.json。

此前经典工作台首次文档500已有实际异常帧：r4同错误页显示QueryDeadlockError/1020，栈为desk.get_context→get_csrf_token→generate_csrf_token→Session.update，冲突tabSessions；站点实际MariaDB11.8.9、InnoDB Sessions、REPEATABLE-READ、innodb_snapshot_isolation=1。[MariaDB官方11.8变更记录](https://github.com/mariadb-corporation/mariadb-docs/blob/main/release-notes/community-server/11.8/what-is-mariadb-118.md)说明该快照隔离默认开启。eac4f75初版on_session_creation太早初始化token，r5真实登录POST400/Invalid Request，尽管原生已建Session，后续200不算登录成功；该失败保留。6870e58改为HTTPRequest完成原生CSRF校验后的before_request，仅新已认证login_manager.resume=False会话调用原生token函数；无Guest、旧会话、CSRF校验或数据库隔离放宽，5项模型通过。r6两个全新context登录POST200/已登录菜单正确，各两tab并行首次Desk/classic文档全部200，pageerror/500为0，原生属性POST200且未保存。它证明本次新登录首次打开路径，未宣称所有会话续期并发已覆盖。/undefined图片404仍留存；r7 HTML img及初始DOM没有literal undefined节点，parser发起行117无栈，具体源节点未定位，不宣称已修复。证据employee-change-ui-r3.json、employee-change-ui-r4.json、employee-session-r5.json、employee-session-r6.json、employee-undefined-image-r7.json。

2026-10-01，离职办理闭环原生验收完成：来源填写实际最后工作日和原因，精确交接项目及每项任务实际完成后，由服务器通过原生 Employee.save 登记 Left；只保存状态、离职日期、原因的本次变化，保留员工历史，关联账号的启用状态、资料、角色、密码及 User Permission 不变。撤销核对员工、人事历史、交接图和四类工资父子表完整内容摘要，有后续变更则拒绝；已办理先撤销才能原生取消，取消通过原生 Task/Project.save 标记 Cancelled，保留来源链接，不删除交接证据。快照、状态禁止客户端注入；来源读取/列表和动作同时核对员工及主子表字段权限。动作不代表工资或财务已结清。

697d6d6 补齐默认 HR Manager 的 Employee Separation submit/cancel（原生更新已提交单也需要 submit）；只在无 Custom DocPerm 或完整原生克隆时安装，保留所有其他角色、字段权限及实质自定义差异，不增加 delete。实际迁移后逐字段核对只有这两个权限变化、DocType Info 评论1条，Employee、User/Auth/Profile/Role/UP 及 whole GL/SLE 内容不变。局部回滚保留原有四组 CallbackManager、realtime 消息和 currently_saving 键的存在性/内容，丢弃本次失败上下文。bf047ed 适配真实副本：Desk 修订会复制 no_copy 字段，普通复制仍带原生 project；仅核验可读、同员工/公司的精确原来源后清空旧项目、任务、状态和快照，创建新的原生交接图，修订还要求原来源已取消。已提交旧活动不能删除、清空或调换任务。复核实际调用顺序后确认 update_after_submit 只调用 before_update_after_submit；未以重写 validate 或绕过原生保存支持该路径。

da9aa97 的33项原生检查通过（31业务+2回滚），实际纯 HR Manager 完成提交、办理、撤销、取消，HR User 无提交/取消权限且不能办理。覆盖启用/停用关联账号、真实密码行与 Role Profile、四类后续工资变更、未审核计薪、手动完成项目/取消任务不能冒充实际交接、日期、下属及重复活跃账号约束、原生 Version、安全取消、同员工再次建单及完整复制修订链。修订测试使用原生 frappe.copy_doc(ignore_no_copy=True) 保留全部旧服务器字段，再由服务器归一化，旧来源和交接图全内容不变；这证明显式复制新来源路径，未冒称实际 Desk Amend 按钮验收。失败上下文原生检查为自然 Employee.validate_status 拒绝，Employee SQL 成功后 Source.save 失败的分支仅模型验证。首轮 helper 复用权限拒绝后未刷新 Python 文档触发 TimestampMismatch；第二轮错误假设 Manual 100 自动变 Completed，在 RPC 前断言失败，均保留失败及独立回滚证据。第三轮与 fresh connection 核对全 tab* 计数、Employee/全部子表、User/Auth/Profile/Role/UP、whole GL/SLE 全部相同，QA前缀零，新增 Error Log 为0。证据 personnel-separation-native-result-r1/r2/r3.json 和各轮 cleanup-independent-verify。

离职浏览器夹具采用私有 journal，一次原生准备独立员工、假日表及员工专属分配、来源和精确两项交接任务。前两次准备分别在无 DocType 的物理 Series 表元数据检查、原生插入字符串时间与 DB datetime 表示差异处失败；提交前已回滚，fresh connection 核对原33门基线全部计数/内容相同。修复后 d37a8d5 准备成功，使用精确微秒时间身份核对。594项全套模型、38 JS 语法、46 JSON 解析通过；完整 bench build 退出0。

Admin 实际离职 UI 三阶段完成：点“办理离职”并确认，唯一 finalize 请求200，来源已办理、员工 Left；点“撤销离职办理”并确认，唯一 reverse 请求200，来源已撤销、员工 Active，在原生“退出”页签核对日期和原因均空；点来源顶部原生“取消”并确认，唯一 native cancel 请求200，来源 docstatus=2/办理状态仍已撤销，Project/两 Task 均 Cancelled，精确链接和活动保留，员工仍 Active。每阶段原生 record_ui 独立读库核对状态、图和保护内容。证据 separation-ui-r1/r2/r3.json 及截图；pageerror=0，后两阶段仍有 GET /undefined 404，Socket.IO 404另列，未宣称这两个缺口已修复。此处是 Admin UI，实际 HR Manager 只覆盖33项原生链，未冒称岗位 UI 或 Desk Amend 按钮已验。

随后一次原生 cleanup 取消本员工 Holiday List Assignment 并将合成员工设 Inactive，来源/Project/Task 不重复取消，保留7个精确 owned 对象和7条原生 Version。fresh connection inspect 无验证错误；独立只读 verifier 核对7对象的创建身份、三阶段顺序、来源及任务链接、无有效假日分配、员工日期/原因空，其他 Employee/全部子表、User/Auth/Profile/Role/UP 和 whole GL/SLE hash均与准备前相同；所有 tab* 计数等于已登记清理后值，Error Log仍69。Activity Log/Sessions各+12、Series+3、来源活动2条及取消历史保留，未手改账簿或强删记录。证据 separation-browser-cleanup-independent-verify.json。

2026-10-01 开始按用户补充要求重新核对旧站操作习惯。系统代理开启且 TUN 生效，命令行 no-proxy 不能单独证明直连；只读查询当前实际 Mihomo controller 的连接，a.ywxyrj.cn:443 HTTPS/Tun均 chains=[DIRECT]、DomainSuffix cn，未修改全局代理/TUN/规则。授权真实登录仅用于读取菜单、空表单、按钮及页面结构，不主动保存真实业务行、凭据或会话。登录工具输出发生密码回显，已告知用户在勘察结束后轮换；凭据没有写入代码或 Git，本次37个自动 DOM/console 文件已按精确绝对路径清理并核对不存在。旧站人事已观察到默认列表/图片/图文视图、新增覆盖列表的弹层、在职/离职和四类经历记录。全模块第一版仍未达到交付条件。

新增经典正式页面桥接，菜单名称与人事分组采用本轮真实旧站名称，保留旧代码别名；预览查询/日期/状态/分页改为各标签独立。219个正式映射的四种原生路由保留在工作台内（91 List/91 Report/34新增/3 Page），新增在大弹层，iframe 常驻保留未保存输入。第一次空筛选列表明确清除原生 saved filters，创建预检查原生 can_create，实际保存继续完整原生权限和验证；未修改任何服务端角色。读源码发现并修复 Bootstrap ×/遮罩/Escape绕过 Dialog.hide、QuickEntry 不属于 cur_frm、全屏层级及 native cur_list 实例名。源码/隔离 Node 核对标签状态、关闭、挂载和原生目标，39 JS语法/46 JSON解析通过；这些不替代实际浏览器。

Admin实际首轮 classic-bridge-qa-r1（457e5ae）证据：classic→人事档案父路由始终 jifeng-legacy，面包屑、iframe ready/Employee List、空筛选/无loading；新增大原生表单填合成值后×→否保留/是关闭；已有合成员工未保存输入切主页再返回仍保留，关闭标签否保留/是移除iframe；客户 QuickEntry 脏关闭受确认。两正式标签的iframe节点、子context及条件各自保留，上级面包屑正常，全屏/退全屏后 iframe 可见。pageerror0、业务保存/审核/取消POST0；仍有图片 /undefined 404及另列Socket.IO404。首轮指出人事浏览误有新增，576553e已隐藏/阻止该入口；原生 confirm先回调后隐藏，关闭下层延至microtask并防重复确认。1a4d08d将全屏目标升到documentElement以覆盖body中弹层。

最终 classic-bridge-review（576553e hardreload）通过：人事浏览无新增、人事档案仍可新增，×→否保留、底部关闭→是丢弃后父 modal 数0、三个窗口 open_dialogs均0，新表单iframe卸载而原列表仍ready；已有合成员工未保存输入跨正式标签返回仍保留、确认关标签才移除iframe；客户 QuickEntry 精确弹层/原生标题及脏确认正确。documentElement全屏中表单/确认框均可见，关闭后列表正常，退全屏成功。业务 mutation POST0、pageerror0，图片404与Socket.IO404完整分列。完整576553e bench build退出0。独立 fresh connection 只读保护核验 whole Employee/全部子表、User/Auth/Profile/Role/UP、whole GL/SLE内容均与浏览器前相同，其他全部tab*计数一致；仅 Activity Log/Sessions各+49，未删登录记录或修改账簿。证据 classic-bridge-protected-after.json。旧字段顺序、图文视图、字典弹层及工资双击路径仍须独立适配，全模块交付未达成。


## 外网交付后档案批次（2026-10-02，本地集成）

外网继续运行冻结业务版本8858743；以下验证使用本地已有隔离站 `jifeng-plating-native-isolated.localhost`，不修改116测试服务器的镜像、业务或账号。文件同步先核bench干净88587438及每个旧文件SHA，再逐文件复制；补丁后的第二次同步核对上次收据SHA。所有私有工具和证据位于忽略的output，新增忽略Playwright自动生成的会话/截图目录，不提交凭据。

实现范围：物料/材料档案由原生List改为独立列表，保留旧20/19数据列及操作/日期/修改人/修改日期/修改/删除列位置。默认全部档案，显式公司、关键词、状态、分页及滚动；新增/修改使用已有档案弹层，公司明确预填及既有公司只读，独立编辑权限来自原生服务。审计列读取当前ERP元数据而非推断旧审计用户，来源权限及原生get_list字段投影省略均保留；浏览组件的readonly/can_write=false合同不变。图片/图文、导入/导出/简拼/删除/打印等未完成入口仍禁用，列表保存提示用户打开档案保存。候选查询仍有1000上限，超过时需关键词缩小范围，不能称全量迁移容量已验。

产品档案去除100条硬拒绝，新增25/50/100分页、可见总数、上/下页和跳页，保存后在原页刷新，最后有效页减少时回到合法页。总数来自完整授权Profile/Item集合及两次完整来源/字段权限复核，原list_archives数组合同保留。全集合复核成本随档案增长；没有并发或大规模性能验收，不能称原子数据库快照。

模具/版种/建模新增当前公司、类型、筛选的已保存全集XLSX下载，分别11/10/8列，逐条使用既有授权DTO和原生export；生成文字单元格保留前导零、公式和HTML字面值，新独立只读MariaDB快照复核范围、来源、授权及字段，异常不返回附件。建模JSON只给既有System/Stock/Manufacturing Manager补export=1，与模具/版种一致；没有给只读角色或真实用户增加角色、删除权或CustomDocPerm。独立站仅reload该DocType并核语义变化只为三个export位，记录 `archive-schema-permissions-20261002.json`。旧XLSX模板未知，因此采用当前已核实字段列序，不声称旧模板完全一致；快照只保证首次一致性读取前的已提交状态。

97项定向Python模型（产品52、库存档案25、库存列表4、工具导出16）及相关8组CJS通过。独立复审复现并关闭3个异步问题：保存时挂起查询的迟到旧结果、复制挂起后关页的孤儿窗口、初值set_values完成后失效条件仍开窗；真实组件生命周期回归覆盖scope/种类key、RPC/初值迟到、copy关闭阻挡及清理。产品原生16门包含103个rollback-only档案；库存15门包含真实Stock User/Stock Manager审计、公司范围、原生备注修改后列表值与修改账号。两组均actual exit0、全夹具回滚，fresh连接计数/账户/原有文档/GL/SLE哈希不变。收据 `archive-paging-native-20261002/classic-product_archive-native-result.json` 与 `classic-inventory_catalog-native-result.json`。

工具原生r1仅验证空表5门，不计非空或岗位验收。为真实下载，本地隔离站注册 `JF-ARCHIVE-QA-20261002`，只新增本轮3工具、103产品、2库存和三个纯主管测试账号；合成材料没有库存/财务凭证，GL/SLE保持。夹具r1给Employee Link写普通姓名失败，r2把版种原文字量当数值失败；两次在总提交前失败并fresh核命名空间/User零残留，保留失败工具及收据。r3修夹具后一次原生业务commit成功，记录intent、身份、表计数变化和受限账号文件；没有修改原业务人员、角色或外网数据。工具r2真实9门通过：三个类型各1已保存记录、三个纯主管逐类导出、Stock User原生读取分享仍无export而整次拒绝、官方HTTP response builder与全部内容/权限/账簿恢复。只读角色临时夹具回滚；尚未验证真实请求中的并发撤权或超过100条已保存工具导出，后者模型106条通过。证据 `tool-export-native-20261002-r1.json`、`-r2.json` 和 `archive-qa-fixture-r3-20261002/receipt.json`。

缓存单app构建r1的最后shell行CR导致tail工具退出1，实际build日志已完成；LF脚本r2完整exit0。构建提示默认redis_cache无法更新assets_json，隔离站私有Redis连接与clear-cache成功，静态文件已链接；不把警告删去。GUI预览最初nohup未保持进程；benchserve降权至jifengtest无法读root600隔离配置，文档404；改同一官方frappe.app.serve使用绝对sites_path，首次启动import先于cwd失败，修正后进程存活。副本原maintenance_mode=1导致503，仅此隔离配置改0并记SHA，保持scheduler disabled和mute_emails，HTTP login200/ping200后才开始GUI。

纯Stock Manager实际A→B→A物料备注两次save_archive POST200：A写“QA A 列表保存验证”，B读到后写“QA B 列表保存验证”，A再读到B值。两套均26列、既有公司只读及未保存放弃回原备注通过。root独立原生after核仅目标Profile的notes/modified/modified_by变化、两个Version都只有notes；完整Item/Product/三工具/Employee/DocPerm/CustomDocPerm/UP/HasRole、其他库存及Auth共12组SHA与完整GL/SLE保持。证据 `archive-gui-native-before-20261002.json`、`archive-gui-native-after-20261002.json`。

同一岗位A/B产品实际GUI均103条：每页100时100→3→100；改25后跳第5页3条。查看后关闭保持第5页、公司/关键词及3行，不额外查询或保存。A/B材料实际25列、公司/namespace唯一行、只读开窗关闭及模块上级/主页返回均保持条件与行，不重新查询；物料/材料滚轮后window.scrollY仍0，没有可用的非零纵向位置，因此不认定非零滚动恢复已验。菜单“模具录入”“版种录入”“版种建模”两版各一次实际GET导出，共六次HTTP200和真实下载。GUI只访问loopback，两条授权备注外没有其他业务保存。报告 `archive-batch-gui-20261002/report.md` 和 `sanitized.json` 保留最初端口故障及后续成功；局部截图只有页头，不作为列/行证据。

root独立逐文件读回六个XLSX，分别11/10/8列和一行已保存合成资料，表头及全部值与夹具相同，A/B业务值一致；非空数据格均文字及格式@，保留前导零、=1+2、HTML原文和高精度单价，没有公式、超链接或外部关系。初次独立verifier误把复制Check的默认0当空，按实际字段声明修正预期后通过，未修改产品或下载文件。收据 `archive-downloads-independent-20261002.json` 含六文件SHA。GUI结束后精确停止本批PID2456，8000关闭；仅恢复副本maintenance_mode=1，其余配置语义与0600保持，JSON字节与原配置不同，未声称整文件字节恢复。受登记QA资料保留在副本，外网未变；收据 `archive-gui-preview-closed-20261002.json`。外网另一次只读实际DIRECT核验HTTP301、可信HTTPS登录200和ping200/pong，证书仍到2026-10-09UTC，`erp_external_https_health_archive_batch_20261002.json`；没有登录或切版本。

增量发布只读复审确认：8858743到本批前HEAD没有业务目录差异，本批唯一schema为三个建模export位；未来候选由最终提交冻结、独立构建，只切五个应用容器并保留数据库/Redis/卷/原证书目录。原发布打包工具硬编码旧提交与目录，不能直接复跑；须新清单、DIRECT、站点备份和权限快照，targeted reload及clear-cache。ecs-release-deploy所列Postgres/Vite工程参考不属于本ERP，按本仓库部署说明执行。106预览不阻塞此批增量发布，但仍是全模块交付缺口。

本批已本地提交19c33af；独立新namespace `output/erp-archive-19c33af-r1` 仅取此提交形成一应用depth-1 clean checkout和2,458,695字节app.tar.gz，468个regular成员逐SHA读回，去除remote/FETCH_HEAD/reflog/本机源路径，21个hooks静态路径实际存在；16个已验bench同步文件核对当前SHA和Git规范换行后内容相同。继承镜像固定8858743-test-r4及其实际ID、其他三应用/依赖不变，pyproject未变；候选Containerfile仅替换集沣应用并离线编译/导入，未执行build。源码/清单未含site配置、output、日志、凭据或数据库；没有传输。Docker Desktop CLI后台启动返回成功并出现pipe，但两次有限info仍超时，WSL docker-desktop仍Stopped；daemon可用和缓存镜像未建立。不在2GB运行机重建，新镜像/上传/切换未发生。保持当前外网基线及测试数据。

本轮启动的Docker Desktop尝试一次正常后台stop，CLI返回Failed to stop且30秒未结束，随后只中断该CLI；没有全局kill、WSL shutdown或停止Ubuntu，未创建/删除镜像和容器。未声称Desktop进程已停止。运行检查收据 `archive-build-runtime-check-20261002.json` 保留失败状态；这个构建限制不代表外网服务失败，也不暂停其他模块开发。

冻结候选独立复审实际核出缓存缺陷：Frappe Desk/include_script/include_style/bundled_asset对直引路径不加版本，而本站nginx assets缓存7天；站点clear-cache不能使已有浏览器重新取同URL，可能新列表组件与旧core/bridge等混用。现hooks为全部13 JS/8 CSS加入实际文件SHA前16位查询参数，保持顺序/路径；内容变化自动改URL，无需手工发布号。新增两项定向模型（包含同长度内容变化仅对应URL变化）及五项原登录钩子回归通过；本地官方Frappe _load_app_hooks和HTML formatter实际21文件校验，查询参数/指纹均保持，没有site/数据库连接，证据 `archive-asset-hooks-native-20261002.json`。没有重开GUI或冒称真实已缓存浏览器普通刷新已实测。旧19c33af源码候选因尚无此修复不能发布，后续重新冻结；另一个发布门是构建入口需实际inspect固定base ID并禁止pull及验证候选RootFS继承，manifest记录本身不等于执行了门。

缓存修复已提交ff541a0并在独立 `output/erp-archive-ff541a0-r3` 重冻：469个regular成员、2,464,602字节app.tar.gz、SHA256为5b3e415b134a276098844dbd7eb4f5e7062dbe0d85e0bc2c015be16bbee7bbc6。加载冻结hooks核21个实际query指纹/文件，避免旧AST collector遇到函数赋值失败；16个同期已验文件匹配最新hooks独立收据及原同步SHA。r2首次加载产生pycache，在归档边界被拒绝，partial/failed.json保留；r3仅加载时禁止写bytecode后通过，没有重写旧候选。新build_checked入口包含仅本地context、固定base实际ID/平台、候选tag必须不存在、禁止pull/network、前后base ID及候选RootFS基底前缀/继承Config守门，已有attempt禁止重复；脚本仅语法校验，未执行构建。新镜像、外网增量、真实缓存浏览器及真实构建守门仍未验，现有外网8858743和共享测试数据保持。

旧销售词典首轮只到四类分组，拦截自动读取对卡片空白的影响未排除，不能当作旧系统无入口。r2实际进入销售→基础资料→客户类型、打开新增后关闭返回；已见新增/更新简拼/查询/打印、空关键词、两个disabled页按钮、录入三个文本框（首框只读）及停用/保存/关闭。没有保存/查询列表行；表头及文本框可靠业务标签、默认状态/编号规则仍未知，未据控件ID猜映射。GetRowCount/GetTableDataWithOffset/GetTableRowData成功，未知Refresh POST及外域仍阻断，对完整性影响未排除。实际旧站DIRECT链/Chrome exe匹配，但controller无PID，不声称单连接与精确PID绑定。r2报告在受限 `legacy-sales-dictionary-r2-20261002/report.md`。首轮临时旧站profile/截图删除遭自动审查拒绝，未换工具重试删除；目录限制当前用户/SYSTEM/Admin访问，保留受限证据，不纳入Git。没有据未知字段实现新词典。

本批补现有正式映射的操作，不消除106业务预览，也不将239映射认定为模块全部通过。363入口、237正式、2工厂部分、106业务预览、13导航和5工具说明保持；第一版全模块目标继续。
