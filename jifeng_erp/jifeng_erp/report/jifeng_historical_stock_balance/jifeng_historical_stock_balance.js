frappe.query_reports["Jifeng Historical Stock Balance"] = {
    filters: [
        { fieldname: "company", label: "公司", fieldtype: "Link", options: "Company",
            default: frappe.defaults.get_user_default("Company"), reqd: 1 },
        { fieldname: "from_date", label: "过账开始日期", fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
        { fieldname: "to_date", label: "过账结束日期", fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "item_code", label: "物料", fieldtype: "Link", options: "Item" },
        { fieldname: "warehouse", label: "仓库", fieldtype: "Link", options: "Warehouse",
            get_query() { return { filters: { company: frappe.query_report.get_filter_value("company") } }; } },
        { fieldname: "include_zero", label: "显示有历史流水的零余额", fieldtype: "Check", default: 0 },
    ],
    onload(report) {
        report.page.main.find(".jifeng-inventory-note").remove();
        $("<div class='alert alert-info jifeng-inventory-note'>")
            .text("数量按物料库存单位列示；按已过账流水计算期初、期间变动和期末余额，包含盘点的绝对余额调整。价值为公司本币库存估价，不是采购价或应付余额。不同物料和单位不混加；期初库存凭证列入期初。")
            .prependTo(report.page.main);
    },
};

