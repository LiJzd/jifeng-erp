frappe.query_reports["Jifeng Personnel Roster"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company", reqd: 1,
            default: frappe.defaults.get_user_default("Company") },
        { fieldname: "department", label: "部门", fieldtype: "Link", options: "Department",
            get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
        { fieldname: "designation", label: "职位", fieldtype: "Link", options: "Designation" },
        { fieldname: "status", label: "原生员工状态", fieldtype: "Select",
            options: "\nActive\nInactive\nSuspended\nLeft" },
    ],
    onload(report) {
        report.page.main.find(".jifeng-personnel-roster-note").remove();
        $("<div class='alert alert-info jifeng-personnel-roster-note'>")
            .text("显示当前可读取的员工档案；状态和入职、离职日期沿用原生员工记录。人员类型沿用旧档案长期/临时分类，工资方式为档案分类；补充列及其值依读取权限显示。")
            .prependTo(report.page.main);
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "status" && value) {
            return frappe.utils.escape_html(__(String(value)));
        }
        if (column.fieldtype === "Data") {
            return frappe.utils.escape_html(String(value ?? ""));
        }
        return default_formatter(value, row, column, data);
    },
};
