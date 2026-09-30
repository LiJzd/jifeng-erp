frappe.query_reports["Jifeng Posted Sales Ledger"] = {
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
        { fieldname: "source_type", label: "来源类型", fieldtype: "Select", options: "\n销售发票" },
        { fieldname: "only_returns", label: "仅退货或贷项", fieldtype: "Check", default: 0 },
    ],
    onload(report) {
        report.page.main.find(".jifeng-sales-ledger-note").remove();
        $("<div class='alert alert-info jifeng-sales-ledger-note'>").text("按销售发票记账日期，每张可读的已提交销售发票一行，包含零售合并销售发票与退货贷项。尚未合并为销售发票的零售凭证不计入本表。合并发票日期可能与原零售业务日期不同；营业交易台账用于查询原始交易。 退货按原凭证负金额保留，原币与公司本币分列，不同币种不合计。凭证收款净额为存储支付明细合计减找零一次；凭证已付字段、核销与积分抵扣单列，不代表总账现金、当前应收余额或所有收款。后续收付款单不在本表支付明细中。每张凭证金额完整保留，不按商品或仓库筛选和分摊；收银配置与创建人沿用原生字段。关联来源须可完整核对；可选过账或结班链接只显示可读凭证。")
            .prependTo(report.page.main);
    },
};

