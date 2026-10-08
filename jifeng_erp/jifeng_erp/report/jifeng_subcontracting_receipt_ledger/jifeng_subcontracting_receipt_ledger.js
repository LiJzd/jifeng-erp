frappe.query_reports["Jifeng Subcontracting Receipt Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "收回/退回记账开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "收回/退回记账结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "supplier", label: "委外供应商", fieldtype: "Link", options: "Supplier" },
        { fieldname: "subcontracting_order", label: "委外订单", fieldtype: "Link", options: "Subcontracting Order",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company"), docstatus: 1 } }) },
        { fieldname: "item_code", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },

    ],
    onload(report) {
        report.page.main.find(".jifeng-subcontracting-note").remove();
        $("<div class='alert alert-info jifeng-subcontracting-note'>").text("按记账日期逐行查询已提交的主订单商品收回与实物退回，退回数量显示负数并保留原收回明细。接收与拒收按原收货仓库区分，原确认损耗保留；每行独立换算为库存单位，不同单位不合计。库存数量及库存价值只取对应收回明细的有效库存账，价值为公司本币变动，不代表委外加工售价或应付金额；库存重估未完成时不显示部分估价值。本表不拆分原料消耗金额。关联单据读取不完整或来源无法核对时，请先处理权限或来源。本表查询当前公司全部委外业务，可按供应商、商品或流程单筛选。")
            .prependTo(report.page.main);
    },
}; 
