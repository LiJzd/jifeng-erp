frappe.query_reports["Jifeng Purchase Receipt Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "收货/退货记账开始日期", fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
        { fieldname: "to_date", label: "收货/退货记账结束日期", fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
        { fieldname: "supplier", label: "供应商", fieldtype: "Link", options: "Supplier" },
        { fieldname: "item_code", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "warehouse", label: "数量对应仓库或仓库组", fieldtype: "Link", options: "Warehouse",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "only_returns", label: "仅实物退回", fieldtype: "Check", default: 0 },
    ],
    onload(report) {
        report.page.main.find(".jifeng-purchase-receipt-note").remove();
        $("<div class='alert alert-info jifeng-purchase-receipt-note'>").text("按记账日期逐行查询已提交的采购收货与实物退回，数量以凭证库存单位显示，退回为负数并保留精确来源。选择仓库时数量只计该仓库或可读组内明细仓的接收/拒收数量，金额仍保留整张业务行货值，不分摊到仓库。行金额与行净额直接取原凭证；价内税和折扣可能使两者不同，价外税与抬头费用未分配到本表行金额。交易币与公司本币分列，不同币种或单位不合计；货值不代表库存账簿估价、当前库存余额或应付余额。")
            .prependTo(report.page.main);
    },
};
