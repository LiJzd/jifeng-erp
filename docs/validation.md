# 验证记录

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
