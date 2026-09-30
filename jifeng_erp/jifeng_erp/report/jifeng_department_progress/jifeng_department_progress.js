frappe.query_reports["Jifeng Department Progress"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "from_date", label: "工单建立开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "工单建立结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
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
            .text("按工单建立日期选批次，包含该批次所有可读报工。按派工部门、源工序和单位分组，每道工单工序的计划量计一次；转交余额和续卡不重复加计划量。未指定部门及历史未关联源工序单列。草稿与已审核完成、损耗分列；正常工时与返工纠正工时分列，纠正卡不计生产数量。逾期按尚未结清的源工序计，停止或关闭工单不计逾期。工序数量不作为成品数量，工时不作为工资金额。")
            .prependTo(report.page.main);
    },
};
