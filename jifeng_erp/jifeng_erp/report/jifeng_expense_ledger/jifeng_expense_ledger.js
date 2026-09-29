frappe.query_reports["Jifeng Expense Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "开始日期", fieldtype: "Date",
            default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "结束日期", fieldtype: "Date",
            default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "expense_account", label: "费用科目", fieldtype: "Link", options: "Account" },
        { fieldname: "cost_center", label: "成本中心", fieldtype: "Link", options: "Cost Center" },
        { fieldname: "store_name", label: "店面", fieldtype: "Data" },
    ],
};
