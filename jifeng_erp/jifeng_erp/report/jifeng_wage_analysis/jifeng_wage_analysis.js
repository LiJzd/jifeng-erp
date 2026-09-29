frappe.query_reports["Jifeng Wage Analysis"] = {
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
