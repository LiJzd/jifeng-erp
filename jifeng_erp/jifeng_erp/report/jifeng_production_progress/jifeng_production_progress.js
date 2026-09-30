frappe.query_reports["Jifeng Production Progress"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "工单建立开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "工单建立结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order" },
        { fieldname: "sales_order", label: "销售订单", fieldtype: "Link", options: "Sales Order" },
        { fieldname: "production_item", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "status", label: "工单状态", fieldtype: "Select",
            options: "\nDraft\nNot Started\nIn Process\nStock Reserved\nCompleted\nStopped\nClosed" },
        { fieldname: "only_overdue", label: "仅逾期未完工", fieldtype: "Check" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-production-note").remove();
        $("<div class='alert alert-info jifeng-production-note'>")
            .text("按工单建立日期查询；完工数量和损耗来自正式制造入库凭证。工序报工不计为成品入库。停止、关闭和草稿工单不判为逾期。")
            .prependTo(report.page.main);
    },
};
