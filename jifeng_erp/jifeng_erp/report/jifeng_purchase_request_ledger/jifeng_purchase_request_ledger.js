frappe.query_reports["Jifeng Purchase Request Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "申请开始日期", fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
        { fieldname: "to_date", label: "申请结束日期", fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
        { fieldname: "item_code", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "warehouse", label: "申请仓库或仓库组", fieldtype: "Link", options: "Warehouse",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "only_pending", label: "仅待采购", fieldtype: "Check", default: 0 },
    ],
    onload(report) {
        report.page.main.find(".jifeng-purchase-request-note").remove();
        $("<div class='alert alert-info jifeng-purchase-request-note'>").text("按申请日期逐行查询已提交的采购申请。申请量、已订量与净接收量均为库存单位数量，按精确申请明细核对所有已提交订单、收货和退货，不限来源凭证日期。待采购量扣除直接净接收及有效订单覆盖，订单收货不重复计算；已关闭订单只保留实际净接收覆盖，已停止申请待采购为零。尚未订购量保留申请减历史已订的差额，退拒收不减少净接收。数量不代表付款；仓库组包含可读明细仓库，不同商品或单位不合计。")
            .prependTo(report.page.main);
    },
};
