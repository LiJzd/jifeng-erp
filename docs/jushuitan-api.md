# 聚水潭 API 实现依据与配置

官方资料核对日期：2026-09-26。

## 协议

- [接口规则](https://openweb.jushuitan.com/doc?docId=30) / [签名](https://openweb.jushuitan.com/doc?docId=70)：POST 表单，biz 为完整 JSON 字符串；排除 sign 与空值后按参数名排序，拼接 key/value，前置 app_secret，UTF-8 MD5 小写。
- [订单](https://openweb.jushuitan.com/dev-doc?docId=22&docType=4)：`/open/orders/single/query`，page_index 固定1、page_size100、is_get_total=false、start_ts 为上页最大 ts。ts 是包含边界的整数游标，不能当浮点保存，也不自行加1。记录按 o_id、行按 oi_id 去重。
- [商品](https://openweb.jushuitan.com/dev-doc?docId=14&docType=2)：`/open/sku/query`；sku_ids 为逗号分隔字符串，最多20个；分页每页100，修改时间窗口不超过7天。实现采用6天窗口和2分钟重叠，分页进度与固定窗口一起保存。
- 正式域名 `https://openapi.jushuitan.com`，测试域名 `https://dev-api.jushuitan.com`。业务客户端仅开放上述两个查询路径；不支持任意主机或写订单接口。
- [自有商城授权/刷新](https://openweb.jushuitan.com/doc?docId=23)：正式 `/openWeb/auth/refreshToken`，测试 `/openWebIsv/auth/refreshToken`。仅适用于自有商城应用；服务商应用须重新授权。过期后不能刷新，授权15天内刷新无效；以实际响应 expires_in 保存有效期。刷新超时视为结果未知，不自动重试。

## 配置

安装后在“集沣生产协作 → 聚水潭设置”配置环境、应用类型、App Key、App Secret、Access Token、商家 co_id、公司、新商品默认分类与单位。Secret/Token 使用 Frappe Password 字段加密存储，只有 System Manager 能访问设置。

若服务器已有配置，可在该测试站点的 `site_config.json` 中用 `jushuitan` 对象承接字段名，再执行：

```bash
bench --site YOUR_SITE execute jifeng_erp.diagnostics.import_site_configuration
bench --site YOUR_SITE execute jifeng_erp.diagnostics.check
```

不要把凭据写进命令参数、仓库或日志。导入配置不会自动启用同步；先测试连接，再启用同步。服务器出口IP须已列入聚水潭白名单。

## 同步操作

1. 用“测试订单连接”验证令牌、订单权限和商家。空结果只能证明查询成功，尚不能证明商家数据覆盖。
2. 配置商品首次同步起点；如果不配置，从最近7天开始，历史订单需要的SKU会按编码补查。
3. 点击“立即同步”验证源数据。查看同步记录、商品映射和订单明细。
4. 确认允许生成流程单的源状态，再启用自动生成；定制/包装要求可以在来源订单填写，然后显式更新到流程单。
5. 配置图片精确域名后，新图片会排队归档；未配置时保留原图链接。只归档HTTPS图片，最大8MB、2500万像素，支持JPEG/PNG/WebP。
6. 确认后开启每15分钟同步。服务器须运行 Frappe scheduler、long queue worker 与 Redis。

每页本地写入成功后才推进游标。网络/限流对只读查询有有限重试；缺行号、商家不一致、响应缺分页标志等问题会停止推进。已提交页保留，失败页回滚。来源取消、拆合单和数量/商品变化会标记已有流程单，采购与试算数据不被远端直接改写。

## 适用边界

普通订单查询接口不覆盖淘系、拼多多的完整通道；如业务使用这些平台，须另行取得奇门/方舟等对应权限与文档并实测覆盖，不能把普通接口成功视为全平台订单都已接入。费用/套餐取决于实际商家授权与合同，公开资料不足以承诺免费。

图片归档需要管理员明确允许来源域名。原SKU已与人工维护 Item 同码时，新建带 JST 前缀的商品并留下映射，之后可人工调整映射；不会覆盖已有人工 Item。物料工艺模板需导入或由生产人员完善，API本身不提供录音中的全部工序报价。
