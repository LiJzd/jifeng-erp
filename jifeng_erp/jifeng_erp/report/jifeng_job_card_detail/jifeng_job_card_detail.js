frappe.query_reports["Jifeng Job Card Detail"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "报工开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "报工结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order" },
        { fieldname: "work_order", label: "生产工单", fieldtype: "Link", options: "Work Order" },
        { fieldname: "production_item", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "operation", label: "工序", fieldtype: "Link", options: "Operation" },
        { fieldname: "workstation", label: "工作站", fieldtype: "Link", options: "Workstation" },
        { fieldname: "approval", label: "审核状态", fieldtype: "Select", options: "全部\n草稿\n已提交", default: "全部" },
        { fieldname: "only_overdue", label: "仅逾期未报完", fieldtype: "Check" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-production-note").remove();
        $("<div class='alert alert-info jifeng-production-note'>")
            .text("每行一道工序报工；草稿和已提交数量分列，已取消不计。不同工序完成数量不能相加作为成品数量；记录工时包含草稿，不作为工资金额。")
            .prependTo(report.page.main);
    },
};
