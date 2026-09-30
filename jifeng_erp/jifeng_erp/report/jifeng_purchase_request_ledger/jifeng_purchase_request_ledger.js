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
        $("<div class='alert alert-info jifeng-purchase-request-note'>").text("按申请日期逐行查询已提交的采购申请。申请量、已订量与已收量均为原生库存单位数量。尚未订购量保留申请减已订的历史差额；待采购量为申请量减已订与已收中的较大值，最低为零，已停止的申请为零。直接收货也计入进度，已订与已收不重复相加；这些数量不代表付款。仓库组包含其可读明细仓库，不同商品或单位不合计。")
            .prependTo(report.page.main);
    },
};
