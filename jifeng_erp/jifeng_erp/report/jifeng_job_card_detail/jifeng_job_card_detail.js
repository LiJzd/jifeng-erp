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
        { fieldname: "department", label: "生产部门", fieldtype: "Link", options: "Department",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "only_unassigned", label: "仅未指定部门", fieldtype: "Check" },
        { fieldname: "approval", label: "审核状态", fieldtype: "Select", options: "全部\n草稿\n已提交", default: "全部" },
        { fieldname: "only_overdue", label: "仅逾期未报完", fieldtype: "Check" },
        { fieldname: "only_loss", label: "仅有工序损耗", fieldtype: "Check" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-production-note").remove();
        $("<div class='alert alert-info jifeng-production-note'>")
            .text("按报工日期查看每张卡，草稿与已审核数量分列，已取消不计。原派工量包含转交余额，本卡任务量已扣除转交待派工；本卡逾期只看本卡未完成部分，部门未结清数量请看部门进度。部分报工请填写原生待完成数量，差额未说明时会计为损耗。正常工时与返工纠正工时分列；纠正卡不计部门生产数量。不同工序完成量不能相加作为成品量，工时不作为工资金额。")
            .prependTo(report.page.main);
    },
};
