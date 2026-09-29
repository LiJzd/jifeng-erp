frappe.query_reports["Jifeng Payment Analysis"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "开始日期", fieldtype: "Date",
            default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "结束日期", fieldtype: "Date",
            default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "payment_type", label: "收付类型", fieldtype: "Select",
            options: "Receive\nPay", default: "Receive", reqd: 1 },
        { fieldname: "party_type", label: "往来方类型", fieldtype: "Select",
            options: "\nCustomer\nSupplier\nEmployee" },
        { fieldname: "party", label: "往来方", fieldtype: "Dynamic Link", options: "party_type" },
        { fieldname: "account", label: "收付款账户", fieldtype: "Link", options: "Account" },
    ],
};
