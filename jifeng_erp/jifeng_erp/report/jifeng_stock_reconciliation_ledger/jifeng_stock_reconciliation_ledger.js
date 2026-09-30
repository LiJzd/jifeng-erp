frappe.query_reports["Jifeng Stock Reconciliation Ledger"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "过账开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "过账结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "item_code", label: "物料", fieldtype: "Link", options: "Item" },
        { fieldname: "warehouse", label: "仓库", fieldtype: "Link", options: "Warehouse",
            get_query() { return { filters: { company: frappe.query_report.get_filter_value("company") } }; } },
        { fieldname: "only_difference", label: "仅显示有差异", fieldtype: "Check", default: 0 },
    ],
    onload(report) {
        report.page.main.find(".jifeng-inventory-note").remove();
        $("<div class='alert alert-info jifeng-inventory-note'>")
            .text("仅列已提交的原生库存盘点，盘后数量为盘点后的总余额。数量差异与价值差异分别列示，价值冲销不产生盘亏数量；差异处理通过原生盘点单提交，报表只查询。")
            .prependTo(report.page.main);
    },
};

