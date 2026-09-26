# 集沣 ERPNext 业务扩展

针对录音中的订单重复录入、商品档案与图片、物料工艺分析、定制/包装要求、分批回货计价。应用名 `jifeng_erp`，使用 ERPNext / Frappe v16。

## 工作流程

1. 在“集沣生产协作 → 聚水潭设置”填写商家配置，测试后同步订单与商品。
2. 同步创建商品映射、Item 和商品工艺档案。生产人员完善物料用量、工序顺序、加工商与报价；也可从标准 BOM 读取。
3. 在来源订单补充本单定制/包装要求及附件，生成各商品流程单。可按允许的订单状态自动生成。
4. 流程单保存本单物料/工序快照，查看库存，选择采购入库仓库后生成采购申请草稿，打印带图片的生产单。
5. 在原生采购申请、发料、委外收货明细关联“集沣流程单”；流程单可查看已提交委外收货费用汇总。
6. 从流程单创建成本试算，记录首次回货、返工回货、待检处理与追加费用。试算不会产生库存、会计或入库凭证。

商品编码和供应商货号分别保存；源订单变化会提示核对既有流程单。再次同步不会覆盖人工定制/包装要求或历史工艺快照。

## 安装与升级

运行条件：Linux/WSL，Frappe v16.21+ / ERPNext v16，Python 3.14，Node.js 24+，MariaDB、Redis、Bench scheduler 和 long queue worker。

```bash
bench get-app /path/to/jifeng_erp
bench --site YOUR_SITE install-app jifeng_erp
bench --site YOUR_SITE migrate
bench build --app jifeng_erp
```

已有安装升级时更新应用代码后执行 migrate；部署方式对应的进程管理器还需重启 worker/web。生产升级前按现有站点流程备份数据库和文件。

角色：System Manager 管理聚水潭授权和同步；Stock Manager / Manufacturing Manager 使用订单、商品工艺档案、流程单和试算单；ERPNext 原生凭证仍执行其原生权限。

## 试算口径

`单位预计成本 = (材料费 + 基础加工费 + 其他费用 + 追加加工费) / (计划数量 - 已确认损耗)`。

待返工、待检和未回货仍在预计可合格数量中。未结清前追加费用/损耗会改变此前批次的试算金额；它不是已入账单价。所有数量结清后按累计数量处理分位差额；全部损耗时费用保留未分摊。

例：100件，材料100元、基础加工50元；先50件合格、50件返工，再回48件合格、2件损耗，追加加工20元。最终98件合格，合计170元，材料只算一次。

## 配置与验收资料

- [API规则、凭据配置和同步操作](docs/jushuitan-api.md)
- [录音需求对应实现、业务口径与验收条件](docs/requirements.md)
- [验证记录与站点冒烟测试](docs/validation.md)

普通聚水潭订单接口有平台覆盖限制，不能代替淘系/拼多多专用通道。实连需要服务器主机、站点/配置路径及已授权应用；密钥不放进仓库或聊天正文。

## 本地验证

```bash
python -m unittest discover -s tests -v
python -m ruff check jifeng_erp tests
```

纯Python测试验证签名、请求、边界分页、检查点、数量状态和分摊。Frappe站点验证另见验证记录。
