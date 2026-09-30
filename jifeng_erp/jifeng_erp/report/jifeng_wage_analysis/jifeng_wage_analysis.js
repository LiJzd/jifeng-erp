frappe.query_reports["Jifeng Wage Analysis"] = {
    onload(report) {
        report.page.set_title(__("Jifeng Wage Analysis"));
        report.page.main.find(".jifeng-wage-analysis-note").remove();
        $("<div class='alert alert-info jifeng-wage-analysis-note'>")
            .text("按工作日期查询可读的已审核计薪记录，按员工、工序、工资项目和计件单位分组。历史未记录单位的计件单逐笔展示，不混加数量；计时小时为已确认计薪工时。金额不代表工资已计提或已发放，请核对工资单、工资发放及付款凭证。")
            .prependTo(report.page.main);
    },
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "开始日期", fieldtype: "Date",
            default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "结束日期", fieldtype: "Date",
            default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "employee", label: "员工", fieldtype: "Link", options: "Employee" },
        { fieldname: "operation", label: "工序", fieldtype: "Data" },
        { fieldname: "salary_component", label: "工资项目", fieldtype: "Link",
            options: "Salary Component" },
    ],
};
