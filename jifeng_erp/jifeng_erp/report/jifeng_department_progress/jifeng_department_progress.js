frappe.query_reports["Jifeng Department Progress"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "报工开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "报工结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "department", label: "生产部门", fieldtype: "Link", options: "Department",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "operation", label: "工序", fieldtype: "Link", options: "Operation" },
        { fieldname: "flow_order", label: "流程单", fieldtype: "Link", options: "Jifeng Flow Order" },
        { fieldname: "work_order", label: "生产工单", fieldtype: "Link", options: "Work Order" },
        { fieldname: "production_item", label: "商品", fieldtype: "Link", options: "Item" },
        { fieldname: "approval", label: "审核状态", fieldtype: "Select", options: "全部\n草稿\n已提交", default: "全部" },
        { fieldname: "only_overdue", label: "仅逾期未报完", fieldtype: "Check" },
        { fieldname: "only_unassigned", label: "仅未指定部门", fieldtype: "Check" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-production-note").remove();
        $("<div class='alert alert-info jifeng-production-note'>")
            .text("按派工时的部门、工序和单位分组；未指定部门单列。不同工序及单位的数量不相加作为成品数量。待审核与已审核分列；记录工时包含草稿，不作为工资金额。")
            .prependTo(report.page.main);
    },
};
