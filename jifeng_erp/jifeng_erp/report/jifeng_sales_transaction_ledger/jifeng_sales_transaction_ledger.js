frappe.query_reports["Jifeng Sales Transaction Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "凭证记账开始日期", fieldtype: "Date", reqd: 1,
            default: frappe.datetime.month_start() },
        { fieldname: "to_date", label: "凭证记账结束日期", fieldtype: "Date", reqd: 1,
            default: frappe.datetime.get_today() },
        { fieldname: "customer", label: "凭证客户", fieldtype: "Link", options: "Customer" },
        { fieldname: "pos_profile", label: "收银配置", fieldtype: "Link", options: "POS Profile",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "cashier", label: "凭证创建人账号", fieldtype: "Data" },
        { fieldname: "source_type", label: "来源类型", fieldtype: "Select", options: "\n销售发票\n零售凭证" },
        { fieldname: "only_returns", label: "仅退货或贷项", fieldtype: "Check", default: 0 },
    ],
    onload(report) {
        report.page.main.find(".jifeng-sales-ledger-note").remove();
        $("<div class='alert alert-info jifeng-sales-ledger-note'>").text("按原始销售凭证的记账日期，每张可读的已提交凭证一行。普通销售发票与原始零售凭证均计入；已有完整原零售来源的合并销售发票或退货镜像不再重复计入。零售结班前后原始交易保留一次；本表营业交易可能尚未完成总账和库存过账。 退货按原凭证负金额保留，原币与公司本币分列，不同币种不合计。凭证收款净额为存储支付明细合计减找零一次；凭证已付字段、核销与积分抵扣单列，不代表总账现金、当前应收余额或所有收款。后续收付款单不在本表支付明细中。每张凭证金额完整保留，不按商品或仓库筛选和分摊；收银配置与创建人沿用原生字段。关联来源须可完整核对；可选过账或结班链接只显示可读凭证。")
            .prependTo(report.page.main);
    },
};

